# Dataset Cleaning Agent

A useful, explainable AI project: upload a CSV, let Gemini choose bounded cleaning tools, review the results, and download the cleaned data and an audit report.

## How it works — brief

Python inspects your CSV and identifies eligible fixes. Gemini chooses one permitted cleaning tool, Python applies it and checks the result, and the cycle repeats until the enabled fixes are complete. Download the cleaned CSV, an explanation of the actions, and a detailed audit; values from quarantined columns are available separately.

**Flow:** Upload → Inspect → Gemini decides → Python cleans → Verify → Repeat → Download.

See the [detailed app schema](docs/APP_SCHEMA.md) for architecture diagrams, data structures, API contracts, all eight tools, and verification rules.

## Run it

You need Python 3.10+, Node.js 20.19+ (or 22.12+), and a Gemini API key.

1. Get an API key from [Google AI Studio](https://aistudio.google.com/apikey).
2. Copy `.env.example` to `.env` and put your key after `GEMINI_API_KEY=`. Keep this file private. The `.gitignore` excludes it.
3. Install and build the interface:

   ```sh
   npm install
   npm run build
   ```

4. Start the app:

   ```sh
   python3 server.py
   ```

5. Open **http://127.0.0.1:8000**. Click **Try sample**, allow profile sharing, and click **Run cleaning agent**.

The server rereads `.env` for each request. After adding a key, click **Check connection**. That button checks local configuration; the first cleaning request verifies API access.

The default model is `gemini-3.5-flash-lite`, taken from the current [Gemini model documentation](https://ai.google.dev/gemini-api/docs/models). Set `GEMINI_MODEL` to another model available to your key if needed. It must support the [structured-output API](https://ai.google.dev/gemini-api/docs/generate-content/structured-output). Quotas and account access depend on your Google account; a configured key does not prove the API is reachable.

For interface development, start `python3 server.py` and `npm run dev` in separate terminals; open the Vite URL. Its `/api` requests are proxied to port 8000.

## What makes it an agent?

The loop is **observe → decide → act → verify → observe again**:

1. Python inspects row counts, missing cells, repeated rows, headers, and column-type hints.
2. Gemini receives that profile and the currently eligible tools. It chooses one action and supplies a short explanation.
3. Python validates the choice against the user's permissions and applies the tool.
4. Python inspects the updated dataset and sends the new observations and action results back to Gemini.
5. The loop stops when Gemini chooses `finish`. The server only permits finishing when every enabled fix is complete. The CSV export is parsed again to verify it matches the cleaned data.

This is a constrained LLM agent, not an unrestricted autonomous data scientist. Gemini chooses the next tool from eligible actions; deterministic functions implement the edits. It never generates executable code. There is one production planner, Gemini, with no offline fallback. Test doubles exist only in the automated tests.

### Tools

| Tool | Action | Default |
|---|---|---|
| `trim_whitespace` | Trim the edges of cell values | On |
| `normalize_headers` | Trim headers, name blanks, resolve duplicates | On |
| `normalize_missing` | Replace the user's exact, case-sensitive markers with empty cells | On; no markers configured |
| `normalize_numbers` | Remove thousands commas in wholly numeric, non-identifier columns | Off |
| `remove_empty_columns` | Remove fully empty columns; keep at least one column | On |
| `quarantine_sparse_columns` | Archive populated cells in originally unnamed columns with under 1% non-empty values, then remove those columns from the main table | On |
| `remove_blank_rows` | Remove records with no remaining values | On |
| `remove_duplicates` | Keep the first exact row and remove repeated copies | Off |

Blank-row removal and duplicate removal are offered after enabled normalization work has completed. Reinspection catches duplicates that become identical after spaces or missing markers are normalized.

`NA` and `NULL` are never assumed to be missing without the user's markers. Leading zeros and numeric precision are preserved. Identifier inference is a conservative heuristic; review columns before enabling number cleanup. Missing values are not imputed. Suspected outliers (1.5 × IQR), ambiguous day/month dates, empty columns, and mostly numeric columns with text are flagged for review, not silently corrected.

## Outputs

- **Cleaned CSV:** Original separator, UTF-8 output; the download adds a UTF-8 BOM for spreadsheet compatibility.
- **Markdown report:** Before/after counts, agent decisions, review findings, and verification results.
- **JSON audit:** Every changed cell and removed record, options, full profiles, surviving original record numbers, and file hashes.
- **Extra values CSV:** Original record/column coordinates and values moved out of unnamed sparse columns. Named sparse columns are preserved. The 1% rule is a heuristic for accidental worksheet spillover; disable it when unnamed sparse columns are intentional.

Record numbers refer to parsed CSV records, including header record 1. They are not physical line numbers when fields contain embedded newlines. Original files are never overwritten. Uploads and results remain in server/browser memory for the request; the app does not write datasets to disk.

The output SHA-256 is calculated over the cleaned UTF-8 text before the browser adds its BOM. For comparison with the downloaded file, remove the UTF-8 BOM before hashing.

## Privacy and limits

Raw rows are kept local. Gemini receives column names, aggregate statistics, eligible tool descriptions, and previous action summaries. Column names can still contain sensitive information, so the UI asks you to acknowledge this transfer. The API key stays on the Python server and is never returned to the browser. Model explanations appear in the UI and audit; they are short decision summaries, not internal reasoning traces.

This local prototype supports CSV/TSV with a header row, UTF-8 or BOM-marked UTF-16, comma/semicolon/tab/pipe separators, up to 5 MB, 25,000 data rows, and 100 columns. It rejects inconsistent record widths and invalid quoting rather than guessing how to repair them. A delimiter override is available if auto-detection gets it wrong. It does not read Excel workbooks or perform domain-specific validation.

The server listens only on `127.0.0.1`; it is intended for local demos, not public hosting. The model gets a maximum of 12 decision steps and each API request has a 45-second timeout. Failed runs offer no downloadable result. API failures do not fall back to a different planner.

## Present the project in one minute

“My project is a dataset cleaning agent. The user uploads a CSV and sets the edits they permit. Gemini sees a quality profile and selects a cleaning tool. Python runs the tool, checks the result, and feeds the updated profile back to Gemini. That repeats until the enabled fixes are complete. The user gets a cleaned CSV, an explanation of the actions, and a record of every edit. Uncertain values are left for human review.”

### Demo

Use `samples/messy_lab_data.csv`. The sample button enables numeric-separator cleanup and duplicate removal and supplies `N/A` as a missing marker. Its expected result is **12 → 10 data rows**, IDs such as `001` preserved, `1,200 → 1200`, `N/A` changed to an empty cell, a repeated record and blank row removed, and the unusually large load and ambiguous date flagged. Every actual action is still selected by Gemini.

## Verify

```sh
python3 -m unittest discover -s tests -v
npm run build
```

The tests cover tool behavior, reinspection, identifiers, precision, missing-marker semantics, malformed CSV rejection, UTF-16/multiline handling, model-choice validation, and mocked Gemini responses. They do not prove a live API key works; do a sample cleaning run after configuring your key.

## Files

- `cleaner.py`: Dataset inspection, deterministic tools, review checks, and agent loop.
- `gemini.py`: Gemini API request, structured decision schema, and response validation.
- `server.py`: Local API, streamed activity events, and built frontend serving.
- `src/main.jsx`: Upload, permissions, preview, activity log, and downloads.
- `tests/test_cleaner.py`: Core and API-contract tests.

Architecture: **React interface → local Python server → Gemini decision → validated Python tool → updated profile**.
