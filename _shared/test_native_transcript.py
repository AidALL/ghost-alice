"""Complete records of append-only host JSONL logs. Python standard library."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_transcript import committed_lines, stop_response_text


class CommittedLinesTests(unittest.TestCase):
    def test_unfinished_last_record_is_not_read(self):
        self.assertEqual(committed_lines('{"a": 1}\n{"b": '), {1: '{"a": 1}'})
        self.assertEqual(committed_lines('{"b": '), {})
        self.assertEqual(committed_lines(''), {})

    def test_bytes_cut_inside_a_character_decode_only_complete_records(self):
        raw = ('{"a": 1}\n' + json.dumps({'b': '가'}, ensure_ascii=False) + '\n').encode()
        self.assertEqual(committed_lines(raw[:-4]), {1: '{"a": 1}'})
        self.assertEqual(json.loads(committed_lines(raw)[2]), {'b': '가'})

    def test_json_string_line_separators_stay_inside_their_record(self):
        record = json.dumps({'text': 'a b c\x85d'}, ensure_ascii=False)
        self.assertIn(' ', record)
        lines = committed_lines(record + '\n' + record + '\n')
        self.assertEqual(lines, {1: record, 2: record})
        self.assertEqual(json.loads(lines[2]), {'text': 'a b c\x85d'})
        self.assertEqual(committed_lines((record + '\n').encode()), {1: record})

    def test_numbers_are_physical_lines(self):
        lines = committed_lines('{"a": 1}\n\n{"b": 2}\r\n')
        self.assertEqual({n: json.loads(line) for n, line in lines.items()}, {1: {'a': 1}, 3: {'b': 2}})


class StopResponseTextTests(unittest.TestCase):
    def test_transcript_fallback_keeps_a_record_with_a_line_separator(self):
        text = 'first second'
        rows = [dict(type='session_meta', payload=dict(id='current')),
                dict(type='turn_context', payload=dict(turn_id='turn')),
                dict(type='response_item', payload=dict(type='message', role='assistant',
                     internal_chat_message_metadata_passthrough=dict(turn_id='turn'),
                     content=[dict(type='output_text', text=text)]))]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rollout.jsonl'
            path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
            native_input = dict(session_id='current', turn_id='turn', transcript_path=str(path))
            self.assertEqual(stop_response_text(native_input), text)


if __name__ == '__main__':
    unittest.main()
