"""One line rule for text and append-only JSON Lines logs. Python standard library."""
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_lines import (LINE_BREAK, committed_byte_lines, committed_length, committed_lines, committed_records,
                        byte_lines, line_at, line_number, split_lines, stream_lines)

SEPARATORS = 'a\u2028b\u2029c\x85d'


class SplitLinesTests(unittest.TestCase):
    def test_only_lf_and_crlf_end_a_line(self):
        self.assertEqual(split_lines('a\nb\r\nc'), ['a', 'b', 'c'])
        self.assertEqual(split_lines(SEPARATORS + '\n' + SEPARATORS), [SEPARATORS, SEPARATORS])

    def test_final_lf_adds_no_line_and_blank_lines_stay(self):
        self.assertEqual(split_lines(''), [])
        self.assertEqual(split_lines('\n'), [''])
        self.assertEqual(split_lines('a\n\nb\n'), ['a', '', 'b'])

    def test_line_break_parts_rebuild_the_text(self):
        for text in ('', '\n', 'a', 'a\n\n', SEPARATORS + '\n' + SEPARATORS + '\n'):
            with self.subTest(text=text):
                self.assertEqual('\n'.join(LINE_BREAK.split(text)), text)
        self.assertEqual(LINE_BREAK.split('a\r\nb\n'), ['a', 'b', ''])


class CommittedLogTests(unittest.TestCase):
    def test_unfinished_last_record_is_not_read(self):
        self.assertEqual(committed_lines('{"a": 1}\n{"b": '), {1: '{"a": 1}'})
        self.assertEqual(committed_lines('{"b": '), {})
        self.assertEqual(committed_lines(''), {})
        self.assertEqual((committed_length('{"a": 1}\n{"b": '), committed_length(b'no break')), (9, 0))

    def test_bytes_cut_inside_a_character_decode_only_complete_records(self):
        raw = ('{"a": 1}\n' + json.dumps({'b': '가'}, ensure_ascii=False) + '\n').encode()
        self.assertEqual(committed_lines(raw[:-4]), {1: '{"a": 1}'})
        self.assertEqual(committed_records(raw)[2], {'b': '가'})

    def test_json_string_line_separators_stay_inside_their_record(self):
        record = json.dumps({'text': SEPARATORS}, ensure_ascii=False)
        self.assertIn('\u2028', record)
        self.assertEqual(committed_lines(record + '\n' + record + '\n'), {1: record, 2: record})
        self.assertEqual(committed_records((record + '\n').encode()), {1: {'text': SEPARATORS}})

    def test_numbers_are_physical_lines_and_crlf_is_removed(self):
        self.assertEqual(committed_lines('{"a": 1}\n\n{"b": 2}\r\n'), {1: '{"a": 1}', 3: '{"b": 2}'})

    def test_a_complete_line_that_is_not_json_is_an_error(self):
        with self.assertRaises(ValueError):
            committed_records('{"a": 1}\nnot json\n')

    def test_byte_lines_carry_offsets_and_stop_at_the_unfinished_record(self):
        raw = b'{"a":1}\n{"b":2}\r\n{"c":'
        self.assertEqual(list(committed_byte_lines(raw)), [(0, 8, b'{"a":1}\n'), (8, 17, b'{"b":2}\r\n')])
        self.assertEqual(list(committed_byte_lines(raw, 8)), [(8, 17, b'{"b":2}\r\n')])
        self.assertEqual([line_number(raw, offset) for offset in (0, 8, 17)], [1, 2, 3])

    def test_line_at_reads_and_decodes_only_through_the_selected_line(self):
        raw = b'first\r\n\nthird\n' + '가'.encode()[:2]
        self.assertEqual([line_at(io.BytesIO(raw), number) for number in (1, 2, 3)], ['first', '', 'third'])
        self.assertIsNone(line_at(io.BytesIO(raw), 5))
        self.assertIsNone(line_at(io.BytesIO(raw), 0))
        with self.assertRaises(UnicodeDecodeError):
            line_at(io.BytesIO(raw), 4)
        stream = io.BytesIO(raw)
        line_at(stream, 1)
        self.assertEqual(stream.tell(), len(b'first\r\n'))
        self.assertEqual(line_at(io.BytesIO(b'a\nlast'), 2), 'last')

    def test_stream_lines_number_each_line_and_keep_separators_inside(self):
        stream = io.BytesIO(('a\r\n' + SEPARATORS + '\nlast').encode())
        self.assertEqual(list(stream_lines(stream)), [(1, 'a'), (2, SEPARATORS), (3, 'last')])
        self.assertEqual(byte_lines(b'a\r\nb\nc'), [b'a\r\n', b'b\n', b'c'])


if __name__ == '__main__':
    unittest.main()
