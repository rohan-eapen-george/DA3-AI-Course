"""Deterministic cleaning tools and the observe -> decide -> act -> verify loop.

Gemini is the only production planner. Tools never execute model-generated code.
All cell values remain strings; identifiers and numeric precision are preserved.
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import io
import re

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 25_000
MAX_COLUMNS = 100
TOOLS = {
    "trim_whitespace": "Remove leading and trailing whitespace from cells.",
    "normalize_headers": "Trim headers; give empty and duplicate headers unique names.",
    "normalize_missing": "Replace only the user's exact missing-value markers with empty cells.",
    "normalize_numbers": "Remove valid thousands commas in numeric columns; preserve IDs and precision.",
    "remove_empty_columns": "Remove columns with no non-empty values; preserve at least one column.",
    "quarantine_sparse_columns": "Move originally unnamed columns with under 1% populated cells into the review archive, then remove them from the main table.",
    "remove_blank_rows": "Remove rows whose cells are all empty.",
    "remove_duplicates": "Keep the first exact row and remove subsequent identical rows.",
}
DEFAULT_OPTIONS = {
    "trim_whitespace": True,
    "normalize_headers": True,
    "normalize_missing": True,
    "normalize_numbers": False,
    "remove_empty_columns": True,
    "quarantine_sparse_columns": True,
    "remove_blank_rows": True,
    "remove_duplicates": False,
    "missing_markers": [],
}


@dataclass
class Dataset:
    headers: list[str]
    rows: list[list[str]]
    row_ids: list[int]
    delimiter: str = ","
    encoding: str = "utf-8"
    column_ids: list[int] | None = None
    original_headers: list[str] | None = None

    def __post_init__(self):
        if self.column_ids is None:
            self.column_ids = list(range(1, len(self.headers) + 1))
        if self.original_headers is None:
            self.original_headers = self.headers[:]

    def copy(self):
        return Dataset(self.headers[:], [r[:] for r in self.rows], self.row_ids[:],
                       self.delimiter, self.encoding, self.column_ids[:], self.original_headers[:])


def parse_csv(raw: bytes, delimiter="auto") -> Dataset:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Choose a non-empty CSV file of at most 5 MB.")
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        text = raw.decode(encoding)
    except UnicodeError as exc:
        raise ValueError("Save the file as UTF-8 CSV or UTF-16 CSV and try again.") from exc
    if "\x00" in text:
        raise ValueError("This file contains binary data. Please use a text CSV file.")
    if delimiter == "auto":
        try:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    if delimiter not in (",", ";", "\t", "|"):
        raise ValueError("Choose a supported delimiter: comma, semicolon, tab, or pipe.")
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise ValueError(f"The CSV has invalid quoting: {exc}.") from exc
    if not records or not records[0]:
        raise ValueError("The first CSV record must contain column headers.")
    headers, rows = records[0], records[1:]
    if len(headers) > MAX_COLUMNS or len(rows) > MAX_ROWS:
        raise ValueError("This prototype supports up to 100 columns and 25,000 data rows.")
    for index, row in enumerate(rows, 2):
        if not row:
            rows[index - 2] = [""] * len(headers)
        elif len(row) != len(headers):
            raise ValueError(f"CSV record {index} has {len(row)} cells; expected {len(headers)}. "
                             "Check the delimiter or repair this row first. No data was changed.")
    return Dataset(headers, rows, list(range(2, len(rows) + 2)), delimiter, encoding)


def validate_options(options):
    if not isinstance(options, dict):
        raise ValueError("Cleaning options must be an object.")
    result = DEFAULT_OPTIONS.copy()
    for name in TOOLS:
        if name in options:
            if not isinstance(options[name], bool):
                raise ValueError(f"{name} must be enabled or disabled.")
            result[name] = options[name]
    markers = options.get("missing_markers", [])
    if not isinstance(markers, list) or len(markers) > 20 or any(
            not isinstance(m, str) or len(m) > 100 for m in markers):
        raise ValueError("Provide up to 20 text missing-value markers.")
    # Exact and case-sensitive; 'NA' is never treated as missing by default.
    result["missing_markers"] = list(dict.fromkeys(m for m in markers if m))
    return result


NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
GROUPED = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
LEADING_ZERO = re.compile(r"^[+-]?0\d")
IDENTIFIER = re.compile(r"(?:^|[\s_-])(id|code|zip|postal|phone|mobile|account|serial)(?:$|[\s_-])", re.I)


def numeric(value):
    v = value.strip()
    if not (NUMBER.fullmatch(v) or GROUPED.fullmatch(v)) or len(v) > 100:
        return None
    try:
        number = Decimal(v.replace(",", ""))
        # Keep extreme exponents out of arithmetic / browser numeric conversion.
        if number.is_finite() and -100 <= number.adjusted() <= 100:
            return number
    except InvalidOperation:
        pass
    return None


def is_identifier(header, values):
    return bool(IDENTIFIER.search(header.strip())) or any(LEADING_ZERO.match(v.strip()) for v in values)


def target_headers(headers):
    result, used = [], set()
    for index, header in enumerate(headers, 1):
        base = header.strip() or f"column_{index}"
        name, suffix = base, 2
        while name in used:
            name, suffix = f"{base}_{suffix}", suffix + 1
        result.append(name)
        used.add(name)
    return result


def number_columns(data):
    eligible = []
    for index, header in enumerate(data.headers):
        values = [r[index] for r in data.rows if r[index].strip()]
        if values and not is_identifier(header, values) and all(numeric(v) is not None for v in values):
            eligible.append(index)
    return eligible


def inspect(data):
    counts = Counter(tuple(r) for r in data.rows)
    profile = {
        "rows": len(data.rows), "columns": len(data.headers),
        "missing_cells": sum(v.strip() == "" for r in data.rows for v in r),
        "whitespace_cells": sum(v != v.strip() for r in data.rows for v in r),
        "duplicate_rows": sum(n - 1 for n in counts.values()),
        "blank_rows": sum(all(not v.strip() for v in r) for r in data.rows),
        "header_changes_needed": sum(a != b for a, b in zip(data.headers, target_headers(data.headers))),
        "column_profiles": [],
    }
    for index, header in enumerate(data.headers):
        values = [r[index] for r in data.rows]
        present = [v for v in values if v.strip()]
        parsed = [numeric(v) for v in present]
        n_numeric = sum(v is not None for v in parsed)
        identifier = is_identifier(header, present)
        kind = "identifier" if identifier else "numeric" if present and n_numeric == len(present) else "text"
        profile["column_profiles"].append({
            "index": index, "name": header, "kind": kind,
            "original_column": data.column_ids[index],
            "originally_unnamed": not data.original_headers[data.column_ids[index] - 1].strip(),
            "missing": len(values) - len(present), "unique": len(set(present)),
            "numeric_values": n_numeric, "present": len(present),
        })
    return profile


def removable_columns(data, sparse=False):
    if not data.rows:
        return []
    result = []
    for index, cid in enumerate(data.column_ids):
        present = sum(bool(r[index].strip()) for r in data.rows)
        if sparse:
            match = not data.original_headers[cid - 1].strip() and 0 < present / len(data.rows) < .01
        else:
            match = present == 0
        if match:
            result.append(index)
    # Never produce a zero-column CSV. Entirely empty datasets need row review instead.
    return result if len(result) < len(data.headers) else []


def candidates(data, options):
    """Offer only permitted tools with actual work; destructive steps run last."""
    profile = inspect(data)
    eligible = number_columns(data)
    counts = {
        "trim_whitespace": profile["whitespace_cells"],
        "normalize_headers": profile["header_changes_needed"],
        "normalize_missing": sum(v in options["missing_markers"] for r in data.rows for v in r),
        "normalize_numbers": sum(bool(GROUPED.fullmatch(r[i])) for r in data.rows for i in eligible),
        "remove_empty_columns": len(removable_columns(data)),
        "quarantine_sparse_columns": len(removable_columns(data, sparse=True)),
        "remove_blank_rows": sum(all(v == "" for v in r) for r in data.rows),
        "remove_duplicates": profile["duplicate_rows"],
    }
    offered = [name for name in TOOLS if options[name] and counts[name]]
    # Canonicalization can create new duplicates; finish it before deleting records.
    if any(name in offered for name in ("trim_whitespace", "normalize_headers", "normalize_missing", "normalize_numbers")):
        offered = [n for n in offered if n not in ("remove_blank_rows", "remove_duplicates", "remove_empty_columns", "quarantine_sparse_columns")]
    if any(n in offered for n in ("remove_empty_columns", "quarantine_sparse_columns")):
        offered = [n for n in offered if n not in ("remove_blank_rows", "remove_duplicates")]
    return [{"tool": name, "description": TOOLS[name], "affected": counts[name]} for name in offered]


def apply_tool(data, tool, options):
    if tool not in {c["tool"] for c in candidates(data, options)}:
        raise ValueError("The planner selected a tool that is not currently allowed.")
    changes = []
    if tool == "normalize_headers":
        new = target_headers(data.headers)
        for index, (before, after) in enumerate(zip(data.headers, new)):
            if before != after:
                changes.append({"record": 1, "column": data.column_ids[index], "before": before, "after": after, "tool": tool})
        data.headers = new
    elif tool in ("remove_empty_columns", "quarantine_sparse_columns"):
        removed = set(removable_columns(data, sparse=tool == "quarantine_sparse_columns"))
        for index in sorted(removed):
            changes.append({"record": 1, "column": data.column_ids[index], "before": data.headers[index],
                            "after": None, "tool": tool,
                            "archived_values": [{"record": rid, "value": row[index]} for rid, row in zip(data.row_ids, data.rows) if row[index] != ""]})
        kept = [i for i in range(len(data.headers)) if i not in removed]
        data.headers = [data.headers[i] for i in kept]
        data.column_ids = [data.column_ids[i] for i in kept]
        data.rows = [[row[i] for i in kept] for row in data.rows]
    elif tool in ("remove_blank_rows", "remove_duplicates"):
        kept, ids, seen = [], [], set()
        for record, row in zip(data.row_ids, data.rows):
            remove = all(v == "" for v in row) if tool == "remove_blank_rows" else tuple(row) in seen
            if remove:
                changes.append({"record": record, "before": row[:], "after": None, "tool": tool})
            else:
                kept.append(row)
                ids.append(record)
                seen.add(tuple(row))
        data.rows, data.row_ids = kept, ids
    else:
        eligible = set(number_columns(data))
        for record, row in zip(data.row_ids, data.rows):
            for index, before in enumerate(row):
                after = before
                if tool == "trim_whitespace":
                    after = before.strip()
                elif tool == "normalize_missing" and before in options["missing_markers"]:
                    after = ""
                elif tool == "normalize_numbers" and index in eligible and GROUPED.fullmatch(before):
                    after = before.replace(",", "")
                if before != after:
                    row[index] = after
                    changes.append({"record": record, "column": data.column_ids[index], "before": before, "after": after, "tool": tool})
    return changes


def review_issues(data):
    issues = []
    profile = inspect(data)
    for key, label in (("missing_cells", "Empty cells remain; values were not invented."),
                       ("duplicate_rows", "Identical rows remain. They may be legitimate repeat observations."),
                       ("header_changes_needed", "Some headers are empty, repeated, or padded with whitespace.")):
        if profile[key]:
            issues.append({"type": key, "count": profile[key], "message": label})
    for col in profile["column_profiles"]:
        index, name = col["index"], col["name"]
        present = [(rid, row[index]) for rid, row in zip(data.row_ids, data.rows) if row[index].strip()]
        if not present:
            issues.append({"type": "empty_column", "column": name, "message": "This column has no non-empty values."})
        if col["kind"] != "identifier" and 0 < col["numeric_values"] < col["present"] and col["numeric_values"] / col["present"] >= .8:
            records = [rid for rid, v in present if numeric(v) is None]
            issues.append({"type": "mixed_numeric", "column": name, "records": records,
                           "message": "Numeric values include text markers. These may be intentional suppression or status codes; values were preserved."})
        if col["kind"] == "numeric" and len(present) >= 4:
            numbers = sorted(numeric(v) for _, v in present)
            def quantile(fraction):
                position = Decimal(len(numbers) - 1) * fraction
                low = int(position)
                return numbers[low] + (numbers[min(low + 1, len(numbers) - 1)] - numbers[low]) * (position - low)
            q1, q3 = quantile(Decimal(".25")), quantile(Decimal(".75"))
            iqr = q3 - q1
            records = [rid for rid, value in present if numeric(value) < q1 - Decimal("1.5") * iqr or numeric(value) > q3 + Decimal("1.5") * iqr]
            if records:
                issues.append({"type": "outlier", "column": name, "records": records,
                               "message": "Outside the 1.5 × IQR range. Flagged for review, not removed."})
        ambiguous = []
        for rid, value in present:
            match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})", value.strip())
            if match and 1 <= int(match[1]) <= 12 and 1 <= int(match[2]) <= 12 and match[1] != match[2]:
                ambiguous.append(rid)
        if ambiguous:
            issues.append({"type": "ambiguous_date", "column": name, "records": ambiguous,
                           "message": "Day/month order is ambiguous. Dates were preserved."})
    return issues


def serialize_csv(data):
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=data.delimiter, lineterminator="\n")
    writer.writerow(data.headers)
    writer.writerows(data.rows)
    return output.getvalue()


def run_agent(original, options, planner, emit=lambda event: None):
    """Re-observe after each action; abort on invalid decisions or no progress."""
    options = validate_options(options)
    data = original.copy()
    before = inspect(data)
    history, all_changes = [], []
    emit({"phase": "inspect", "message": f"Inspected {before['rows']} rows and {before['columns']} columns.", "profile": before})
    for step in range(1, 13):
        offered = candidates(data, options)
        emit({"phase": "decide", "message": "Gemini is choosing the next action.", "step": step})
        decision = planner.choose(inspect(data), offered, history)
        if not isinstance(decision, dict) or not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise ValueError("Gemini returned an invalid decision. The original file is unchanged.")
        tool = decision.get("tool")
        if tool == "finish":
            if offered:
                raise ValueError("Gemini stopped before the enabled fixes were completed. Try again.")
            emit({"phase": "finish", "message": decision["reason"], "step": step})
            break
        if tool not in {c["tool"] for c in offered}:
            raise ValueError("Gemini selected an unavailable cleaning tool. No result was exported.")
        emit({"phase": "act", "tool": tool, "message": decision["reason"], "step": step})
        changes = apply_tool(data, tool, options)
        if not changes:
            raise ValueError("The tool made no progress. The agent stopped to avoid a loop.")
        all_changes.extend(changes)
        after = inspect(data)
        item = {"step": step, "tool": tool, "reason": decision["reason"], "changes": len(changes),
                "columns_after": after["columns"],
                "rows_after": after["rows"], "missing_after": after["missing_cells"]}
        history.append(item)
        emit({"phase": "verify", "message": f"Verified {len(changes)} changes; {after['rows']} rows and {after['columns']} columns remain.", **item})
    else:
        raise ValueError("The agent reached its 12-step limit. No result was exported.")
    # A round-trip is an independent check of rectangular shape and quoted content.
    output = serialize_csv(data)
    check = parse_csv(output.encode("utf-8"), data.delimiter)
    if check.headers != data.headers or check.rows != data.rows:
        raise ValueError("Export verification failed. No result was exported.")
    report = {
        "planner": "Gemini", "model": planner.model, "before": before, "after": inspect(data),
        "options": options, "actions": history, "changes": all_changes, "review": review_issues(data),
        "verification": {"csv_round_trip": True, "enabled_fixes_completed": not candidates(data, options)},
        "original_record_numbers": data.row_ids,
        "original_column_numbers": data.column_ids,
        "quarantine": [{"record": v["record"], "original_column": c["column"], "header": c["before"], "value": v["value"]}
                       for c in all_changes if c["tool"] in ("remove_empty_columns", "quarantine_sparse_columns") for v in c["archived_values"]],
        "export": {"encoding": "utf-8", "delimiter": data.delimiter},
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "notes": ["Record numbers refer to the original CSV records, including header record 1.",
                  "Missing values, ambiguous dates, and suspected outliers are preserved for review.",
                  "Column types are inferred hints; CSV cells remain strings."],
    }
    if report["quarantine"]:
        report["review"].insert(0, {"type": "quarantined_values", "count": len(report["quarantine"]),
                                   "message": "Values from unnamed sparse columns were moved to the extra-values archive. Review that file; the original upload is unchanged."})
    return data, output, report
