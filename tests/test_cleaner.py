import io
import json
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch

from cleaner import DEFAULT_OPTIONS, parse_csv, run_agent, validate_options
from gemini import GeminiPlanner, PlannerError


class FakePlanner:
    """Test double only. The app always uses GeminiPlanner."""
    model = "test-double"
    def __init__(self):
        self.observations = []
    def choose(self, profile, offered, history):
        self.observations.append((profile, offered))
        return {"tool": offered[0]["tool"] if offered else "finish", "reason": "Test decision from observed tool candidates."}


class CleaningTests(unittest.TestCase):
    def test_empty_and_sparse_unnamed_columns_are_archived(self):
        rows = ['name,, ,measure'] + [f'item_{i},,{"side value" if i == 42 else ""},{i}' for i in range(200)]
        data = parse_csv(('\n'.join(rows) + '\n').encode(), ',')
        cleaned, output, report = run_agent(data, {}, FakePlanner())
        self.assertEqual(cleaned.headers, ['name', 'measure'])
        self.assertEqual(cleaned.column_ids, [1, 4])
        self.assertEqual(len(cleaned.rows), 200)
        self.assertEqual(report['after']['missing_cells'], 0)
        self.assertEqual(report['quarantine'], [{'record': 44, 'original_column': 3, 'header': 'column_3', 'value': 'side value'}])
        self.assertEqual(parse_csv(output.encode()).rows, cleaned.rows)
        self.assertEqual(len(data.headers), 4)

    def test_named_sparse_and_populated_unnamed_columns_stay(self):
        rows = ['name,optional,'] + [f'item_{i},{"rare" if i == 42 else ""},{"x" if i < 3 else ""}' for i in range(200)]
        cleaned, _, report = run_agent(parse_csv(('\n'.join(rows)+'\n').encode(), ','), {}, FakePlanner())
        self.assertEqual(len(cleaned.headers), 3)
        self.assertEqual(report['quarantine'], [])

    def test_column_removal_keeps_original_audit_coordinates(self):
        rows = ['a,,b'] + [f'{i},, {i} ' for i in range(3)]
        cleaned, _, report = run_agent(parse_csv(('\n'.join(rows)+'\n').encode(), ','), {}, FakePlanner())
        self.assertEqual(cleaned.column_ids, [1, 3])
        edits = [c for c in report['changes'] if c['tool'] == 'trim_whitespace']
        self.assertTrue(all(c['column'] == 3 for c in edits))

    def test_column_tools_can_be_disabled(self):
        data = parse_csv(b'a,\nx,\n')
        cleaned, _, _ = run_agent(data, {'remove_empty_columns': False, 'quarantine_sparse_columns': False}, FakePlanner())
        self.assertEqual(len(cleaned.headers), 2)

    def test_sample_end_to_end_and_reinspection(self):
        data = parse_csv((Path(__file__).parents[1] / 'samples/messy_lab_data.csv').read_bytes())
        original = [r[:] for r in data.rows]
        planner = FakePlanner()
        cleaned, output, report = run_agent(data, {"normalize_numbers": True, "remove_duplicates": True, "missing_markers": ["N/A"]}, planner)
        self.assertEqual(len(cleaned.rows), 10)
        self.assertEqual(cleaned.rows[0][0], '001')
        self.assertEqual(cleaned.rows[0][2], '1200')
        self.assertEqual(cleaned.rows[4][2], '')
        self.assertEqual(data.rows, original)
        self.assertEqual(parse_csv(output.encode()).rows, cleaned.rows)
        self.assertTrue(report['verification']['csv_round_trip'])
        self.assertEqual(report['after']['duplicate_rows'], 0)
        self.assertTrue(any(i['type'] == 'outlier' and 9 in i['records'] for i in report['review']))
        self.assertTrue(any(i['type'] == 'ambiguous_date' for i in report['review']))
        self.assertGreater(len(planner.observations), 1)
        self.assertEqual(planner.observations[-1][0]['whitespace_cells'], 0)
        self.assertTrue(any(c['record'] == 12 and c['tool'] == 'remove_duplicates' for c in report['changes']))

    def test_safe_defaults_preserve_repeat_and_na(self):
        data = parse_csv(b'country,value\nNA,001\nNA,001\n')
        cleaned, _, report = run_agent(data, {}, FakePlanner())
        self.assertEqual(cleaned.rows, data.rows)
        self.assertEqual(report['changes'], [])
        self.assertEqual(report['after']['duplicate_rows'], 1)

    def test_dedup_after_normalization(self):
        data = parse_csv(b'x,y\n a ,N/A\na,\n')
        cleaned, _, report = run_agent(data, {"remove_duplicates": True, "remove_empty_columns": False, "missing_markers": ["N/A"]}, FakePlanner())
        self.assertEqual(cleaned.rows, [['a', '']])
        self.assertEqual(report['actions'][-1]['tool'], 'remove_duplicates')

    def test_unique_headers_handle_existing_suffixes(self):
        data = parse_csv(b' x ,x,x_2,\na,b,c,d\n')
        cleaned, _, _ = run_agent(data, {}, FakePlanner())
        self.assertEqual(len(set(cleaned.headers)), 4)
        self.assertEqual(cleaned.headers, ['x', 'x_2', 'x_2_2', 'column_4'])

    def test_numeric_id_precision_and_ambiguous_comma(self):
        data = parse_csv(b'id,amount,decimal_comma,precision\n001,"1,200","1,20",2.1000\n002,"2,500","2,30",3.2000\n')
        cleaned, _, _ = run_agent(data, {"normalize_numbers": True}, FakePlanner())
        self.assertEqual(cleaned.rows[0], ['001', '1200', '1,20', '2.1000'])

    def test_exact_missing_markers(self):
        data = parse_csv(b'x\nNULL\nnull\nNA\n')
        cleaned, _, _ = run_agent(data, {"missing_markers": ['NULL'], "remove_blank_rows": False}, FakePlanner())
        self.assertEqual(cleaned.rows, [[''], ['null'], ['NA']])

    def test_malformed_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'record 3'):
            parse_csv(b'a,b\n1,2\n3,4,5\n', ',')
        with self.assertRaisesRegex(ValueError, 'quoting'):
            parse_csv(b'a,b\n"unfinished,2\n', ',')

    def test_utf16_semicolon_multiline_and_quotes(self):
        raw = 'name;note\nArun;"two\nlines"\n'.encode('utf-16')
        data = parse_csv(raw, ';')
        cleaned, output, _ = run_agent(data, {}, FakePlanner())
        self.assertEqual(cleaned.rows[0][1], 'two\nlines')
        self.assertEqual(parse_csv(output.encode(), ';').rows, data.rows)

    def test_empty_header_only_and_blank_single_column(self):
        for raw in (b'x\n', b'x\n""\n'):
            cleaned, output, report = run_agent(parse_csv(raw), {}, FakePlanner())
            self.assertEqual(cleaned.rows, [])
            self.assertTrue(report['verification']['csv_round_trip'])
        with self.assertRaises(ValueError):
            parse_csv(b'')

    def test_model_cannot_use_unapproved_tools_or_stop_early(self):
        class BadPlanner(FakePlanner):
            def choose(self, *args):
                return {'tool': 'remove_duplicates', 'reason': 'Not permitted'}
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            run_agent(parse_csv(b'x\na\na\n'), {}, BadPlanner())
        class EarlyPlanner(FakePlanner):
            def choose(self, *args):
                return {'tool': 'finish', 'reason': 'Stopped early'}
        with self.assertRaisesRegex(ValueError, 'before'):
            run_agent(parse_csv(b'x\n a \n'), {}, EarlyPlanner())

    def test_disabled_tools_do_not_run(self):
        options = {name: False for name in DEFAULT_OPTIONS if name != 'missing_markers'}
        data = parse_csv(b' x ,b\n a ,N/A\n a ,N/A\n', ',')
        cleaned, _, _ = run_agent(data, options, FakePlanner())
        self.assertEqual(cleaned.headers, data.headers)
        self.assertEqual(cleaned.rows, data.rows)

    def test_option_validation(self):
        for options in ({'missing_markers': 'NULL'}, {'remove_duplicates': 'true'}, [], {'missing_markers': [None]}):
            with self.assertRaises(ValueError):
                validate_options(options)


class GeminiTests(unittest.TestCase):
    def test_missing_key_has_clear_error(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaisesRegex(PlannerError, 'GEMINI_API_KEY'):
            GeminiPlanner()

    def test_structured_payload_and_no_raw_rows(self):
        class Response:
            def __enter__(self): return io.StringIO(json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"tool":"finish","reason":"No fixes remain."}'}]}}]}))
            def __exit__(self, *args): pass
        with patch('urllib.request.urlopen', return_value=Response()) as request:
            planner = GeminiPlanner(key='test-key')
            result = planner.choose({'rows': 5, 'column_profiles': []}, [], [])
            self.assertEqual(result['tool'], 'finish')
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload['generationConfig']['responseFormat']['text']['mimeType'], 'APPLICATION_JSON')
            self.assertEqual(payload['generationConfig']['responseFormat']['text']['schema']['properties']['tool']['enum'], ['finish'])
            self.assertNotIn('raw_rows', payload['contents'][0]['parts'][0]['text'])

    def test_provider_error_detail_redacts_key(self):
        key = 'private-test-key'
        error = urllib.error.HTTPError('https://example.invalid', 400, 'Bad request', {},
            io.BytesIO(json.dumps({'error': {'message': f'Invalid mimeType; key={key}'}}).encode()))
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(PlannerError) as caught:
                GeminiPlanner(key=key).choose({}, [], [])
            self.assertIn('Invalid mimeType', str(caught.exception))
            self.assertNotIn(key, str(caught.exception))

    def test_invalid_model_reply_fails(self):
        class Response:
            def __enter__(self): return io.StringIO('{"candidates": []}')
            def __exit__(self, *args): pass
        with patch('urllib.request.urlopen', return_value=Response()):
            with self.assertRaisesRegex(PlannerError, 'valid cleaning decision'):
                GeminiPlanner(key='test-key').choose({}, [], [])


if __name__ == '__main__':
    unittest.main()
