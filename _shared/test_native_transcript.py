"""Stop answer text read from a native transcript. Python standard library."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_transcript import stop_response_text


class StopResponseTextTests(unittest.TestCase):
    def test_transcript_fallback_keeps_a_record_with_a_line_separator(self):
        text = 'first\u2028second'
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
