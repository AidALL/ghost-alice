"""timestamps: the one UTC time text, its formatter and its parser."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest import mock

import timestamps


class FormatTest(unittest.TestCase):
    def test_utc_now_is_iso_8601_to_the_second_ending_in_z(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        text = timestamps.utc_now()
        parsed = datetime.fromisoformat(text)
        self.assertTrue(text.endswith('Z'))
        self.assertNotIn('.', text)
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertLessEqual(before, parsed)
        self.assertLessEqual(parsed, datetime.now(timezone.utc))

    def test_utc_now_with_microseconds_keeps_the_fraction_and_z(self):
        moment = datetime(2026, 11, 7, 0, 30, 15, 123456, tzinfo=timezone.utc)
        with mock.patch.object(timestamps, 'datetime') as clock:
            clock.now.return_value = moment
            text = timestamps.utc_now(microseconds=True)
        self.assertEqual(text, '2026-11-07T00:30:15.123456Z')

    def test_format_timestamp_converts_to_utc_and_drops_the_fraction_unless_asked(self):
        moment = datetime(2026, 11, 7, 9, 30, 15, 123456, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(timestamps.format_timestamp(moment), '2026-11-07T00:30:15Z')
        self.assertEqual(timestamps.format_timestamp(moment, microseconds=True), '2026-11-07T00:30:15.123456Z')
        whole = moment.replace(microsecond=0)
        self.assertEqual(timestamps.format_timestamp(whole, microseconds=True), '2026-11-07T00:30:15Z')


class ParseTest(unittest.TestCase):
    def test_z_and_offset_forms_parse_to_aware_datetimes(self):
        self.assertEqual(timestamps.parse_timestamp('2026-11-07T00:30:15Z'),
                         datetime(2026, 11, 7, 0, 30, 15, tzinfo=timezone.utc))
        self.assertEqual(timestamps.parse_timestamp('2026-11-07T00:30:15.123456+00:00'),
                         datetime(2026, 11, 7, 0, 30, 15, 123456, tzinfo=timezone.utc))
        parsed = timestamps.parse_timestamp('2026-11-07T09:30:15+09:00')
        self.assertEqual(parsed.utcoffset(), timedelta(hours=9))

    def test_naive_empty_invalid_and_non_text_values_parse_to_none(self):
        for value in ('2026-11-07T00:30:15', '', 'invalid', None, 20261107, ['2026-11-07T00:30:15Z']):
            with self.subTest(value=value):
                self.assertIsNone(timestamps.parse_timestamp(value))


if __name__ == '__main__':
    unittest.main()
