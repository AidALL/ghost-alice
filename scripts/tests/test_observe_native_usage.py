"""Server counters, not prompt sizes, define these observation expectations."""
import json
import importlib.util
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "observe_native_usage.py"


def counter(input_tokens, cached, output, total=None):
    return {"input_tokens": input_tokens, "cached_input_tokens": cached,
            "cache_write_input_tokens": 0, "output_tokens": output,
            "reasoning_output_tokens": 1,
            "total_tokens": input_tokens + output if total is None else total}


def snapshot(total, last=None):
    return {"type": "event_msg", "timestamp": "2026-10-12T20:00:00Z",
            "payload": {"type": "token_count", "info": {
                "total_token_usage": total, "last_token_usage": last or total}}}


def assistant(mid, usage):
    return {"type": "assistant", "timestamp": "2026-10-12T20:00:00Z",
            "message": {"id": mid, "model": "claude-sonnet-5-5", "usage": usage}}


class NativeUsageTests(unittest.TestCase):
    def test_chronology_equal_timestamp_prefix_cannot_replace_final_snapshot(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=1))
        late = assistant('msg_a', dict(output_tokens=5))
        early['timestamp'] = late['timestamp'] = '2026-10-12T20:00:00.000Z'
        for sources in ([[early, late], [early]], [[early], [early, late]]):
            with self.subTest(sources=sources):
                report = self.observe_sources(sources)
                self.assertEqual(report['totals']['output_tokens'], 5)
                self.assertEqual(report['totals']['server_input_tokens'], 22)
                self.assertEqual(len(report['observations']), 1)
                self.assertEqual(len(report['observations'][0]['usage_snapshot_locators']), 3)
                self.assertFalse(report['gaps'])

    def test_chronology_compares_instants_with_fractional_precision_and_offsets(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=1))
        late = assistant('msg_a', dict(output_tokens=5))
        for timestamp in ('2026-10-12T20:00:00.100Z', '2026-10-12T21:00:00.100+01:00'):
            with self.subTest(timestamp=timestamp):
                late['timestamp'] = timestamp
                report = self.observe_sources([[late], [early]])
                self.assertEqual(report['totals']['output_tokens'], 5)
                self.assertEqual(report['observations'][0]['timestamp'], timestamp)

    def test_chronology_preserves_precision_beyond_microseconds(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=5))
        late = assistant('msg_a', dict(output_tokens=1))
        early['timestamp'] = '2026-10-12T20:00:00.0000001Z'
        late['timestamp'] = '2026-10-12T20:00:00.0000002Z'
        report = self.observe_sources([[late], [early]])
        self.assertEqual(report['totals']['output_tokens'], 1)
        self.assertFalse(report['gaps'])

    def test_chronology_unordered_conflict_is_unknown_but_agreed_fields_survive(self):
        a = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=1))
        b = assistant('msg_a', dict(output_tokens=5))
        for sources in ([[a], [b]], [[b], [a]]):
            with self.subTest(sources=sources):
                report = self.observe_sources(sources)
                self.assertIsNone(report['totals']['output_tokens'])
                self.assertEqual(report['totals']['server_input_tokens'], 22)
                self.assertTrue(any('chronology' in g['reason'].lower() for g in report['gaps']))

    def test_chronology_common_anchor_does_not_choose_between_unordered_final_updates(self):
        anchor = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=1))
        a, b = assistant('msg_a', dict(output_tokens=5)), assistant('msg_a', dict(output_tokens=3))
        report = self.observe_sources([[anchor, a], [anchor, b]])
        self.assertIsNone(report['totals']['output_tokens'])
        self.assertEqual(report['totals']['server_input_tokens'], 22)

    def test_chronology_repeated_value_keeps_occurrences_and_explicit_decrease(self):
        a = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=1))
        b = assistant('msg_a', dict(output_tokens=5))
        for sources in ([[a, b, a], [a]], [[a], [a, b, a]]):
            with self.subTest(sources=sources):
                report = self.observe_sources(sources)
                self.assertEqual(report['totals']['output_tokens'], 1)
                self.assertEqual(len(report['observations'][0]['usage_snapshot_locators']), 4)
                self.assertFalse(report['gaps'])

    def test_chronology_missing_timestamp_needs_source_order_for_conflicting_values(self):
        a, b = assistant('msg_a', dict(output_tokens=1)), assistant('msg_a', dict(output_tokens=5))
        a.pop('timestamp'); b.pop('timestamp')
        report = self.observe_sources([[a], [b]])
        self.assertIsNone(report['totals']['output_tokens'])
        report = self.observe_sources([[a, b]])
        self.assertEqual(report['totals']['output_tokens'], 5)

    def test_chronology_contradictory_time_and_source_order_cannot_claim_total(self):
        a = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=1))
        b = assistant('msg_a', dict(output_tokens=5))
        a['timestamp'] = '2026-10-12T20:00:01Z'
        report = self.observe_sources([[a, b]])
        self.assertIsNone(report['totals']['output_tokens'])
        self.assertIsNone(report['totals']['server_input_tokens'])
        self.assertTrue(any('chronology' in g['reason'].lower() for g in report['gaps']))

    def test_chronology_nested_erasure_is_not_an_omitted_field(self):
        a = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=10,
            cache_read_input_tokens=20, output_tokens=5,
            cache_creation={'ephemeral_5m_input_tokens': 4, 'ephemeral_1h_input_tokens': 6}))
        erased = assistant('msg_a', dict(cache_creation=None))
        restored = assistant('msg_a', dict(cache_creation={'ephemeral_5m_input_tokens': 10}))
        report = self.observe_sources([[a, erased, restored]])
        self.assertEqual(report['totals']['cache_write_5m_tokens'], 10)
        self.assertIsNone(report['totals']['cache_write_1h_tokens'])

    def test_chronology_unordered_compaction_arrays_cannot_fall_back_to_reply_zero(self):
        a = assistant('msg_summary', dict(input_tokens=0, cache_creation_input_tokens=0,
            cache_read_input_tokens=0, output_tokens=0,
            iterations=[dict(type='compaction', input_tokens=37,
                cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=12)]))
        b = assistant('msg_summary', dict(input_tokens=0, cache_creation_input_tokens=0,
            cache_read_input_tokens=0, output_tokens=0,
            iterations=[dict(type='compaction', input_tokens=45,
                cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=16)]))
        for sources in ([[a], [b]], [[b], [a]]):
            with self.subTest(sources=sources):
                report = self.observe_sources(sources)
                self.assertIsNone(report['totals']['server_input_tokens'])
                self.assertIsNone(report['totals']['output_tokens'])
                self.assertEqual(len(report['observations']), 1)
                self.assertTrue(report['gaps'])

    def test_chronology_distinct_native_record_ids_cannot_be_content_anchors(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=1))
        late = assistant('msg_a', dict(output_tokens=5))
        other = assistant('msg_a', dict(early['message']['usage']))
        early['uuid'], late['uuid'], other['uuid'] = 'record-a', 'record-late', 'record-b'
        for sources in ([[early, late], [other]], [[other], [early, late]]):
            with self.subTest(sources=sources):
                report = self.observe_sources(sources)
                self.assertIsNone(report['totals']['output_tokens'])
                self.assertEqual(report['totals']['server_input_tokens'], 22)
                self.assertEqual(len(report['observations']), 1)
                self.assertEqual({s['native_record_id'] for s in report['observations'][0]['usage_snapshots']},
                                 {'record-a', 'record-late', 'record-b'})

    def test_chronology_same_native_record_id_preserves_prefix_alignment(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=1))
        late = assistant('msg_a', dict(output_tokens=5))
        early['uuid'], late['uuid'] = 'record-a', 'record-late'
        report = self.observe_sources([[early, late], [early]])
        self.assertEqual(report['totals']['output_tokens'], 5)
        self.assertIn('native_record_id', report['observations'][0]['usage_snapshots'][0])
        self.assertEqual([s['native_record_id'] for s in report['observations'][0]['usage_snapshots']],
                         ['record-a', 'record-late', 'record-a'])
        self.assertFalse(report['gaps'])

    def test_chronology_missing_native_record_id_cannot_borrow_known_occurrence(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=1))
        late = assistant('msg_a', dict(output_tokens=5))
        other = assistant('msg_a', dict(early['message']['usage']))
        early['uuid'], late['uuid'] = 'record-a', 'record-late'
        report = self.observe_sources([[early, late], [other]])
        self.assertIsNone(report['totals']['output_tokens'])

    def observe_sources(self, sources, provider='anthropic'):
        with tempfile.TemporaryDirectory() as directory:
            args = [sys.executable, str(SCRIPT), '--provider', provider]
            for index, rows in enumerate(sources):
                path = Path(directory) / f'native-{index}.jsonl'
                path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                args += ['--source', str(path)]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_sparse_later_source_preserves_same_response_input_and_nested_details(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=10,
            cache_read_input_tokens=20, output_tokens=5,
            cache_creation={'ephemeral_5m_input_tokens': 4, 'ephemeral_1h_input_tokens': 6},
            output_tokens_details={'thinking_tokens': 3}))
        late = assistant('msg_a', dict(output_tokens=8,
            cache_creation={'ephemeral_5m_input_tokens': 4}))
        late['timestamp'] = '2026-10-12T20:00:01Z'
        report = self.observe_sources([[early], [late]])
        self.assertEqual(report['totals']['server_input_tokens'], 32)
        self.assertEqual(report['totals']['output_tokens'], 8)
        self.assertEqual(report['totals']['cache_write_1h_tokens'], 6)
        self.assertEqual(report['totals']['reasoning_output_tokens'], 3)
        self.assertEqual(len(report['observations']), 1)
        self.assertEqual(len(report['observations'][0]['usage_snapshot_locators']), 2)
        self.assertFalse(report['gaps'])

    def test_source_order_does_not_override_later_response_counters(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=5))
        late = assistant('msg_a', dict(output_tokens=8))
        late['timestamp'] = '2026-10-12T20:00:01Z'
        report = self.observe_sources([[late], [early]])
        self.assertEqual(report['totals']['server_input_tokens'], 22)
        self.assertEqual(report['totals']['output_tokens'], 8)
        self.assertEqual(len(report['observations']), 1)
        self.assertFalse(report['gaps'])

    def test_nested_streaming_snapshot_keeps_prior_ttl_and_thinking_fields(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=10,
            cache_read_input_tokens=20, output_tokens=5,
            cache_creation={'ephemeral_5m_input_tokens': 4, 'ephemeral_1h_input_tokens': 6},
            output_tokens_details={'thinking_tokens': 3, 'text_tokens': 2}))
        late = assistant('msg_a', dict(output_tokens=8,
            cache_creation={'ephemeral_5m_input_tokens': 4},
            output_tokens_details={'text_tokens': 5}))
        late['timestamp'] = '2026-10-12T20:00:01Z'
        report = self.observe('anthropic', [early, late])
        self.assertEqual(report['totals']['cache_write_1h_tokens'], 6)
        self.assertEqual(report['totals']['reasoning_output_tokens'], 3)
        self.assertEqual(report['observations'][0]['raw_usage']['output_tokens_details']['text_tokens'], 5)

    def test_overlapping_exports_order_each_field_by_its_original_snapshot(self):
        early = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=5))
        middle = assistant('msg_a', dict(input_tokens=3, output_tokens=6))
        middle['timestamp'] = '2026-10-12T20:00:01Z'
        late = assistant('msg_a', dict(output_tokens=8))
        late['timestamp'] = '2026-10-12T20:00:02Z'
        report = self.observe_sources([[early, late], [middle]])
        self.assertEqual(report['totals']['server_input_tokens'], 23)
        self.assertEqual(report['totals']['output_tokens'], 8)
        self.assertEqual(len(report['observations']), 1)

    def test_same_response_with_conflicting_models_is_not_silently_reassigned(self):
        spec = importlib.util.spec_from_file_location('usage_identity', SCRIPT)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        usage = dict(input_tokens=2, cache_creation_input_tokens=0,
            cache_read_input_tokens=20, output_tokens=5)
        a, b = assistant('msg_a', usage), assistant('msg_a', usage)
        b['message']['model'] = 'claude-opus-5-5'
        b['timestamp'] = '2026-10-12T20:00:01Z'
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, row in enumerate((a, b)):
                path = Path(directory) / f'native-{index}.jsonl'
                path.write_text(json.dumps(row) + '\n')
                paths.append(path)
            with self.assertRaises(ValueError):
                api.observe('anthropic', paths)

    def test_explicit_later_zero_is_observed_and_missing_fields_stay_unknown(self):
        early = assistant('msg_a', dict(input_tokens=2, output_tokens=5))
        late = assistant('msg_a', dict(output_tokens=0))
        late['timestamp'] = '2026-10-12T20:00:01Z'
        report = self.observe_sources([[early], [late]])
        self.assertEqual(report['totals']['ordinary_input_tokens'], 2)
        self.assertEqual(report['totals']['output_tokens'], 0)
        self.assertIsNone(report['totals']['server_input_tokens'])
        self.assertIsNone(report['totals']['cache_read_tokens'])
        self.assertIsNone(report['actual_charge'])

    def test_compaction_iterations_are_atomic_not_positionally_merged(self):
        early = assistant('msg_summary', dict(input_tokens=0, output_tokens=0,
            iterations=[dict(type='compaction', input_tokens=37, cache_creation_input_tokens=5,
                cache_read_input_tokens=0, output_tokens=12)]))
        late = assistant('msg_summary', dict(input_tokens=0, output_tokens=0,
            iterations=[dict(type='compaction', input_tokens=37)]))
        late['timestamp'] = '2026-10-12T20:00:01Z'
        report = self.observe_sources([[early], [late]])
        self.assertIsNone(report['totals']['server_input_tokens'])
        self.assertIsNone(report['totals']['output_tokens'])
        self.assertEqual(report['observations'][0]['usage_basis'], 'provider-iterations')

    def observe(self, provider, rows, duplicate_source=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            args = [sys.executable, str(SCRIPT), "--provider", provider,
                    "--source", str(path)]
            if duplicate_source:
                args += ["--source", str(path)]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_codex_repeated_cumulative_snapshot_is_not_another_response(self):
        a, b = counter(100, 40, 10), counter(250, 160, 30)
        result = self.observe("codex", [snapshot(a), snapshot(a),
                                       snapshot(b, counter(150, 120, 20)), snapshot(b)])
        self.assertEqual(result["totals"]["server_input_tokens"], 250)
        self.assertEqual(result["totals"]["output_tokens"], 30)
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(result["observations"][0]["unit"], "cumulative_interval")

    def legacy_codex_rows(self, session='session-a'):
        return [dict(type='session_meta', payload=dict(id=session)),
                snapshot(counter(100, 40, 10)),
                snapshot(counter(250, 160, 30), counter(150, 120, 20))]

    def test_codex_identical_same_session_legacy_exports_count_each_interval_once(self):
        rows = self.legacy_codex_rows()
        report = self.observe_sources([rows, rows], provider='codex')
        self.assertEqual(report['totals']['server_input_tokens'], 250)
        self.assertEqual(report['totals']['output_tokens'], 30)
        self.assertEqual(len(report['observations']), 2)
        self.assertTrue(all(len(row['usage_snapshot_locators']) == 2
                            for row in report['observations']))

    def test_codex_legacy_prefix_export_does_not_duplicate_its_shared_intervals(self):
        rows = self.legacy_codex_rows()
        for sources in ([rows, rows[:2]], [rows[:2], rows]):
            with self.subTest(sources=sources):
                report = self.observe_sources(sources, provider='codex')
                self.assertEqual(report['totals']['server_input_tokens'], 250)
                self.assertEqual(report['totals']['output_tokens'], 30)

    def test_codex_identical_counters_from_different_sessions_remain_additive(self):
        report = self.observe_sources([self.legacy_codex_rows('session-a'),
                                       self.legacy_codex_rows('session-b')], provider='codex')
        self.assertEqual(report['totals']['server_input_tokens'], 500)
        self.assertEqual(report['totals']['output_tokens'], 60)
        self.assertEqual(len(report['observations']), 4)

    def test_codex_ambiguous_partial_legacy_overlap_is_unknown_not_summed(self):
        other = [dict(type='session_meta', payload=dict(id='session-a')),
                 snapshot(counter(200, 80, 20)),
                 snapshot(counter(350, 200, 40), counter(150, 120, 20))]
        for sources in ([self.legacy_codex_rows(), other], [other, self.legacy_codex_rows()]):
            with self.subTest(sources=sources):
                report = self.observe_sources(sources, provider='codex')
                self.assertIsNone(report['totals']['server_input_tokens'])
                self.assertIsNone(report['totals']['output_tokens'])
                self.assertIsNone(report['observed_subtotals']['server_input_tokens'])
                self.assertTrue(any('overlap' in item['reason'].lower() for item in report['gaps']))

    def test_codex_legacy_copies_without_session_identity_cannot_establish_a_total(self):
        rows = self.legacy_codex_rows()[1:]
        report = self.observe_sources([rows, rows], provider='codex')
        self.assertIsNone(report['totals']['server_input_tokens'])
        self.assertTrue(any('overlap' in item['reason'].lower() for item in report['gaps']))

    def test_codex_same_session_counter_reuse_at_different_times_is_not_a_copy(self):
        first, second = self.legacy_codex_rows()[:2], self.legacy_codex_rows()[:2]
        second[-1]['timestamp'] = '2026-10-12T20:01:00Z'
        report = self.observe_sources([first, second], provider='codex')
        self.assertIsNone(report['totals']['server_input_tokens'])
        self.assertTrue(any('overlap' in item['reason'].lower() for item in report['gaps']))

    def test_codex_modern_response_id_deduplication_across_exports_is_preserved(self):
        usage = counter(100, 40, 10)
        rows = [dict(type='session_meta', payload=dict(id='session-a')),
                dict(type='token_usage_record', payload=dict(response_id='resp_1',
                     usage=usage, thread_token_usage=usage)), snapshot(usage)]
        report = self.observe_sources([rows, rows], provider='codex')
        self.assertEqual(report['totals']['server_input_tokens'], 100)
        self.assertEqual(len(report['observations']), 1)
        self.assertEqual(report['observations'][0]['unit'], 'server_response')

    def modern_codex_rows(self, session='session-a'):
        usage = counter(100, 40, 10)
        return [dict(type='session_meta', payload=dict(id=session)),
                dict(type='token_usage_record', payload=dict(response_id='resp-mixed',
                     thread_id=session, usage=usage, thread_token_usage=usage)), snapshot(usage)]

    def test_codex_mixed_same_session_exports_keep_raw_usage_but_not_additive_totals(self):
        legacy, modern = self.legacy_codex_rows()[:2], self.modern_codex_rows()
        for sources in ([legacy, modern], [modern, legacy]):
            with self.subTest(sources=sources):
                report = self.observe_sources(sources, provider='codex')
                self.assertIsNone(report['totals']['server_input_tokens'])
                self.assertIsNone(report['totals']['output_tokens'])
                self.assertIsNone(report['observed_subtotals']['server_input_tokens'])
                self.assertEqual(len(report['observations']), 2)
                self.assertEqual({row['unit'] for row in report['observations']},
                                 {'server_response', 'cumulative_interval'})
                for row in report['observations']:
                    raw = row['raw_usage'] if row['unit'] == 'server_response' else row['raw_usage']['total_token_usage']
                    self.assertEqual(raw, counter(100, 40, 10))
                self.assertTrue(any('mixed' in item['reason'].lower() for item in report['gaps']))

    def test_codex_mixed_different_native_sessions_remain_additive(self):
        modern = self.modern_codex_rows('session-b')
        for rows in (modern, modern[1:]):
            with self.subTest(rows=rows):
                report = self.observe_sources([self.legacy_codex_rows()[:2], rows], provider='codex')
                self.assertEqual(report['totals']['server_input_tokens'], 200)
                self.assertEqual(report['totals']['output_tokens'], 20)
                self.assertFalse(any('mixed' in item['reason'].lower() for item in report['gaps']))

    def test_codex_mixed_missing_or_conflicting_native_owner_stays_unknown(self):
        missing = self.modern_codex_rows()[1:]
        del missing[0]['payload']['thread_id']
        conflicting = self.modern_codex_rows('session-b')
        conflicting[1]['payload']['thread_id'] = 'session-c'
        for rows in (missing, conflicting):
            with self.subTest(rows=rows):
                report = self.observe_sources([self.legacy_codex_rows()[:2], rows], provider='codex')
                self.assertIsNone(report['totals']['server_input_tokens'])
                self.assertTrue(any('mixed' in item['reason'].lower() for item in report['gaps']))

    def test_codex_mixed_exports_still_deduplicate_modern_response_ids(self):
        modern = self.modern_codex_rows('session-b')
        report = self.observe_sources([modern, self.legacy_codex_rows()[:2], modern], provider='codex')
        self.assertEqual(report['totals']['server_input_tokens'], 200)
        self.assertEqual(report['totals']['output_tokens'], 20)
        self.assertEqual(len(report['observations']), 2)
        self.assertEqual(report['call_coverage']['observed_server_response_count'], 1)

    def test_codex_duplicate_response_cannot_hide_conflicting_native_session_ownership(self):
        first, second = self.modern_codex_rows('session-a'), self.modern_codex_rows('session-b')
        for modern in ([first, second], [second, first]):
            with self.subTest(modern=modern):
                report = self.observe_sources([self.legacy_codex_rows()[:2], *modern], provider='codex')
                self.assertIsNone(report['totals']['server_input_tokens'])
                self.assertEqual(report['call_coverage']['observed_server_response_count'], 1)

    def test_codex_same_source_duplicate_response_preserves_conflicting_ownership(self):
        first = self.modern_codex_rows('session-a')[1]
        second = self.modern_codex_rows('session-b')[1]
        for records in ([first, second], [second, first]):
            for partitions in ([records], [[records[0]], [records[1]]]):
                with self.subTest(partitions=partitions):
                    report = self.observe_sources([self.legacy_codex_rows()[:2], *partitions],
                                                  provider='codex')
                    self.assertIsNone(report['totals']['server_input_tokens'])
                    self.assertIsNone(report['totals']['output_tokens'])
                    self.assertEqual(report['call_coverage']['observed_server_response_count'], 1)
                    self.assertTrue(any('Conflicting response-copy session identities' in gap['reason']
                                        for gap in report['gaps']))

    def test_native_write_zero_does_not_prove_server_zero(self):
        result = self.observe("codex", [snapshot(counter(115278, 114432, 100))])
        self.assertEqual(result["totals"]["server_input_tokens"], 115278)
        self.assertEqual(result["totals"]["native_reported_cache_read_tokens"], 114432)
        self.assertIsNone(result["totals"]["cache_write_tokens"])
        self.assertIsNone(result["totals"]["ordinary_input_tokens"])
        self.assertIsNone(result["estimated_cost"])
        self.assertIsNone(result["actual_charge"])

    def test_anthropic_final_snapshot_replaces_streaming_snapshot(self):
        early = {"input_tokens": 2, "cache_creation_input_tokens": 25056,
                 "cache_read_input_tokens": 55682, "output_tokens": 1}
        final = dict(early, output_tokens=2149, cache_creation={
            "ephemeral_1h_input_tokens": 25056, "ephemeral_5m_input_tokens": 0},
            output_tokens_details={"thinking_tokens": 333})
        result = self.observe("anthropic", [assistant("msg_a", early),
                                            assistant("msg_a", final), assistant("msg_a", final)])
        self.assertEqual(result["totals"]["server_input_tokens"], 80740)
        self.assertEqual(result["totals"]["output_tokens"], 2149)
        self.assertEqual(result["totals"]["cache_write_1h_tokens"], 25056)
        self.assertEqual(len(result["observations"]), 1)

    def test_another_message_id_is_another_paid_call(self):
        usage = {"input_tokens": 2, "cache_creation_input_tokens": 4,
                 "cache_read_input_tokens": 10, "output_tokens": 3}
        result = self.observe("anthropic", [assistant("msg_a", usage), assistant("msg_b", usage)])
        self.assertEqual(result["totals"]["server_input_tokens"], 32)

    def test_compaction_iterations_are_counted_once_instead_of_zero_reply_totals(self):
        usage = dict(input_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                     output_tokens=0, iterations=[dict(type='compaction', input_tokens=37,
                         cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=12),
                         dict(type='message', input_tokens=3, cache_creation_input_tokens=0,
                              cache_read_input_tokens=10, output_tokens=4)])
        result = self.observe('anthropic', [assistant('msg-summary', usage), assistant('msg-summary', usage)])
        self.assertEqual(len(result['observations']), 1)
        self.assertEqual(result['totals']['server_input_tokens'], 50)
        self.assertEqual(result['totals']['output_tokens'], 16)
        self.assertEqual(result['observations'][0]['usage_basis'], 'provider-iterations')

    def test_missing_compaction_iteration_usage_stays_unknown_despite_top_level_zero(self):
        usage = dict(input_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                     output_tokens=0, iterations=[dict(type='compaction', input_tokens=37)])
        result = self.observe('anthropic', [assistant('msg-summary', usage)])
        self.assertIsNone(result['totals']['server_input_tokens'])
        self.assertIsNone(result['totals']['cache_read_tokens'])
        self.assertIsNone(result['totals']['output_tokens'])

    def test_compaction_preserves_observed_iteration_ttl_and_thinking_details(self):
        iterations = [dict(type='compaction', input_tokens=37, cache_creation_input_tokens=5,
                          cache_read_input_tokens=0, output_tokens=12,
                          cache_creation={'ephemeral_5m_input_tokens': 2, 'ephemeral_1h_input_tokens': 3},
                          output_tokens_details={'thinking_tokens': 3}),
                      dict(type='message', input_tokens=3, cache_creation_input_tokens=5,
                           cache_read_input_tokens=10, output_tokens=4,
                           cache_creation={'ephemeral_5m_input_tokens': 3, 'ephemeral_1h_input_tokens': 2},
                           output_tokens_details={'thinking_tokens': 2})]
        result = self.observe('anthropic', [assistant('msg-details', dict(input_tokens=0,
            output_tokens=0, iterations=iterations))])
        self.assertEqual(result['totals']['server_input_tokens'], 60)
        self.assertEqual(result['totals']['cache_write_5m_tokens'], 5)
        self.assertEqual(result['totals']['cache_write_1h_tokens'], 5)
        self.assertEqual(result['totals']['reasoning_output_tokens'], 5)

    def test_missing_cache_field_does_not_become_zero(self):
        result = self.observe("anthropic", [assistant("msg_a", {"input_tokens": 2, "output_tokens": 3})])
        self.assertIsNone(result["totals"]["server_input_tokens"])
        self.assertIsNone(result["totals"]["cache_read_tokens"])
        self.assertTrue(result["gaps"])

    def test_ttl_details_are_parts_of_cache_write(self):
        result = self.observe("anthropic", [assistant("msg_a", {
            "input_tokens": 2, "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20, "output_tokens": 5,
            "cache_creation": {"ephemeral_5m_input_tokens": 4, "ephemeral_1h_input_tokens": 6}})])
        self.assertEqual(result["totals"]["server_input_tokens"], 32)
        self.assertEqual(result["totals"]["cache_write_tokens"], 10)

    def test_invalid_ttl_subtotal_is_a_gap(self):
        result = self.observe("anthropic", [assistant("msg_a", {
            "input_tokens": 2, "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20, "output_tokens": 5,
            "cache_creation": {"ephemeral_5m_input_tokens": 4, "ephemeral_1h_input_tokens": 7}})])
        self.assertTrue(any("TTL" in gap["reason"] for gap in result["gaps"]))

    def test_duplicate_source_path_does_not_duplicate_bill(self):
        result = self.observe("anthropic", [assistant("msg_a", {
            "input_tokens": 2, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 20, "output_tokens": 5})], duplicate_source=True)
        self.assertEqual(result["totals"]["server_input_tokens"], 22)

    def test_synthetic_context_fill_is_not_server_usage(self):
        result = self.observe("codex", [snapshot(counter(100, 40, 10)),
                                       snapshot(counter(0, 0, 0, total=1000))])
        self.assertEqual(result["observed_subtotals"]["server_input_tokens"], 100)
        self.assertTrue(result["gaps"])

    def test_aggregate_jump_is_not_named_one_request(self):
        result = self.observe("codex", [snapshot(counter(100, 40, 10)),
                                       snapshot(counter(400, 140, 40), counter(150, 50, 10))])
        self.assertEqual(result["totals"]["server_input_tokens"], 400)
        self.assertFalse(result["observations"][-1]["matches_last_usage"])
        self.assertEqual(result["observations"][-1]["unit"], "cumulative_interval")

    def test_partial_record_does_not_erase_known_usage(self):
        result = self.observe("codex", [snapshot(counter(100, 40, 10)),
                                       {"type": "event_msg", "payload": {"type": "token_count", "info": None}}])
        self.assertEqual(result["observed_subtotals"]["server_input_tokens"], 100)

    def test_response_ids_precede_overlapping_cumulative_snapshots(self):
        usage = counter(100, 40, 10)
        row = {"type": "token_usage_record", "payload": {
            "response_id": "resp_1", "usage": usage, "thread_token_usage": usage}}
        result = self.observe("codex", [row, row, snapshot(usage)])
        self.assertEqual(result["totals"]["server_input_tokens"], 100)
        self.assertEqual(result["observations"][0]["unit"], "server_response")
        self.assertEqual(result["observations"][0]["response_id"], "resp_1")
        self.assertEqual(len(result["observations"]), 1)

    def test_record_without_token_count_is_still_observed(self):
        usage = counter(100, 40, 10)
        result = self.observe("codex", [{"type": "token_usage_record", "payload": {
            "response_id": "resp_1", "usage": usage, "thread_token_usage": usage}}])
        self.assertEqual(result["totals"]["server_input_tokens"], 100)

    def test_host_counter_cannot_drop_server_compaction_responses(self):
        usage = counter(100, 40, 10)
        rows = [{"type": "token_usage_record", "payload": {
            "response_id": "resp_answer", "usage": usage, "thread_token_usage": usage}},
            {"type": "token_usage_record", "payload": {
                "response_id": "resp_compaction", "usage": usage,
                "thread_token_usage": counter(200, 80, 20)}}, snapshot(usage)]
        result = self.observe("codex", rows)
        self.assertEqual(result["totals"]["server_input_tokens"], 200)
        self.assertEqual(result["totals"]["output_tokens"], 20)
        self.assertEqual({o["response_id"] for o in result["observations"]},
                         {"resp_answer", "resp_compaction"})

    def test_cutoff_reads_original_without_copying_later_calls(self):
        spec = importlib.util.spec_from_file_location("bounded_usage", SCRIPT)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        usage = counter(100, 40, 10)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.jsonl"
            rows = [{"type": "token_usage_record", "payload": {
                "response_id": "resp_1", "usage": usage, "thread_token_usage": usage}},
                {"type": "token_usage_record", "payload": {
                    "response_id": "resp_2", "usage": usage,
                    "thread_token_usage": counter(200, 80, 20)}}]
            original = "".join(json.dumps(row) + "\n" for row in rows)
            path.write_text(original)
            result = api.observe("codex", [path], through_line=1)
            self.assertEqual(result["totals"]["server_input_tokens"], 100)
            self.assertEqual(result["sources"][0]["path"], str(path.resolve()))
            self.assertEqual(result["sources"][0]["through_line"], 1)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_sparse_response_records_keep_cumulative_coverage(self):
        usage = counter(100, 40, 10)
        result = self.observe("codex", [snapshot(usage), {"type": "token_usage_record", "payload": {
            "response_id": "resp_2", "usage": usage,
            "thread_token_usage": counter(200, 80, 20)}}, snapshot(counter(200, 80, 20), usage)])
        self.assertEqual(result["totals"]["server_input_tokens"], 200)
        self.assertTrue(any("Response IDs" in g["reason"] for g in result["gaps"]))

    def test_model_changes_have_separate_totals(self):
        usage = {"input_tokens": 2, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 20, "output_tokens": 5}
        a, b = assistant("msg_a", usage), assistant("msg_b", usage)
        b["message"]["model"] = "claude-opus-5-5"
        result = self.observe("anthropic", [a, b])
        self.assertEqual(result["by_model"]["claude-sonnet-5-5"]["server_input_tokens"], 22)
        self.assertEqual(result["by_model"]["claude-opus-5-5"]["server_input_tokens"], 22)

    def test_synthetic_error_is_unknown_charge_not_free_call(self):
        row = assistant("msg_error", {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": 0, "output_tokens": 0})
        row["message"]["model"] = "<synthetic>"
        row["isApiErrorMessage"] = True
        result = self.observe("anthropic", [row])
        self.assertIsNone(result["totals"]["server_input_tokens"])
        self.assertEqual(result["observations"], [])
        self.assertTrue(result["gaps"])


class NativeInvocationReceiptTests(unittest.TestCase):
    """Native result metadata establishes session attribution, not whole-work scope."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location('receipt_usage', SCRIPT)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'native.jsonl'
        self.write_rows([self.native_message()])

    def native_message(self, session='session-a'):
        row = assistant('msg_a', dict(input_tokens=2, cache_creation_input_tokens=0,
                                     cache_read_input_tokens=20, output_tokens=5))
        row['sessionId'] = session
        row['uuid'] = 'native-record-a'
        return row

    def write_rows(self, rows):
        self.source.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def receipt(self, name='result.json', **changes):
        # Schema and separate counters mirror the original M8 CLI result shape.
        value = dict(type='result', subtype='success', session_id='session-a',
                     uuid='result-a', duration_ms=7858, duration_api_ms=74420,
                     num_turns=2, usage={'input_tokens': 999999, 'output_tokens': 8888},
                     modelUsage={'claude-sonnet-5-5': {'inputTokens': 999999}},
                     total_cost_usd=1.25, result='done')
        value.update(changes)
        path = self.root / name
        path.write_text(json.dumps(value))
        return path

    def observe(self, receipts=(), provider='anthropic', **options):
        return self.api.observe(provider, [self.source], receipt_paths=receipts, **options)

    def test_matched_native_result_preserves_separate_host_counters_and_source_binding(self):
        path = self.receipt()
        report = self.observe([path])
        receipt = report['invocation_receipts'][0]
        self.assertEqual(receipt['status'], 'matched')
        self.assertEqual(receipt['session_id'], 'session-a')
        self.assertEqual(receipt['uuid'], 'result-a')
        self.assertEqual(receipt['duration_ms'], 7858)
        self.assertEqual(receipt['duration_api_ms'], 74420)
        self.assertEqual(receipt['num_turns'], 2)
        self.assertEqual(receipt['duration_scope'], 'unknown')
        self.assertEqual(receipt['native_sources'][0]['identity_locator'], f'{self.source.resolve()}:1')
        self.assertEqual(receipt['native_sources'][0]['snapshot_sha256'], report['sources'][0]['snapshot_sha256'])
        self.assertEqual(report['receipt_sources'][0]['path'], str(path.resolve()))
        self.assertEqual(len(report['receipt_sources'][0]['snapshot_sha256']), 64)
        self.assertEqual(report['call_coverage']['identified_matched_result_count'], 1)
        self.assertEqual(report['call_coverage']['observed_server_message_count'], 1)
        self.assertIsNone(report['call_coverage']['paid_call_count'])
        self.assertIsNone(report['call_coverage']['model_api_call_count'])
        for field in ('duration_ms', 'duration_api_ms', 'paid_call_count', 'participating_models', 'actual_charge'):
            self.assertIsNone(report['whole_work'][field])
        self.assertTrue(report['whole_work']['gaps'])

    def test_receipt_tokens_and_reported_cost_do_not_change_native_totals_or_billing(self):
        without = self.api.observe('anthropic', [self.source])
        with_receipt = self.observe([self.receipt()])
        for field in ('totals', 'observed_subtotals', 'by_model', 'observations', 'gaps'):
            self.assertEqual(with_receipt[field], without[field])
        self.assertEqual(with_receipt['totals']['server_input_tokens'], 22)
        self.assertIsNone(with_receipt['actual_charge'])
        self.assertNotIn('usage', with_receipt['invocation_receipts'][0])
        self.assertNotIn('modelUsage', with_receipt['invocation_receipts'][0])
        self.assertNotIn('result', with_receipt['invocation_receipts'][0])

    def test_escaped_unpaired_surrogate_in_ignored_result_text_keeps_receipt_observable(self):
        path = self.receipt(result=chr(0xd800))
        original = path.read_bytes()
        report = self.observe([path])
        receipt = report['invocation_receipts'][0]
        self.assertEqual(receipt['status'], 'matched')
        self.assertEqual((receipt['session_id'], receipt['uuid']), ('session-a', 'result-a'))
        self.assertEqual((receipt['duration_ms'], receipt['duration_api_ms'], receipt['num_turns']),
                         (7858, 74420, 2))
        self.assertEqual(report['receipt_sources'][0]['snapshot_sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(receipt['semantic_sha256'], hashlib.sha256(json.dumps(
            json.loads(original), sort_keys=True, ensure_ascii=True).encode()).hexdigest())
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn('result', receipt)

    def test_missing_or_mismatched_receipt_session_is_unavailable_not_attributed(self):
        for session in (None, '', True, 'unrelated-session'):
            with self.subTest(session=session):
                report = self.observe([self.receipt(session_id=session)])
                receipt = report['invocation_receipts'][0]
                self.assertEqual(receipt['status'], 'unavailable')
                self.assertEqual(receipt['native_sources'], [])
                self.assertTrue(receipt['gaps'])
                self.assertEqual(report['call_coverage']['identified_matched_result_count'], 0)
                self.assertIsNone(report['whole_work']['duration_ms'])

    def test_native_identity_is_required_and_mixed_or_invalid_metadata_cannot_bind(self):
        for identities in ((None,), ('session-a', 'other'), ('session-a', 123)):
            with self.subTest(identities=identities):
                rows = []
                for identity in identities:
                    row = self.native_message(identity)
                    if identity is None:
                        del row['sessionId']
                    rows.append(row)
                self.write_rows(rows)
                receipt = self.observe([self.receipt()])['invocation_receipts'][0]
                self.assertEqual(receipt['status'], 'unavailable')
                self.assertEqual(receipt['native_sources'], [])

    def test_binding_uses_only_the_selected_original_cutoff(self):
        first = self.native_message()
        second = self.native_message('other-session')
        second['message']['id'] = 'msg_b'
        self.write_rows([first, second])
        report = self.observe([self.receipt()], through_line=1)
        self.assertEqual(report['invocation_receipts'][0]['status'], 'matched')
        self.assertEqual(report['invocation_receipts'][0]['native_sources'][0]['through_line'], 1)

    def test_empty_malformed_or_non_result_receipts_preserve_unavailable_provenance(self):
        for original in ('', '{', '[]', '{"type":"assistant","session_id":"session-a"}'):
            with self.subTest(original=original):
                path = self.root / 'invalid.json'
                path.write_text(original)
                report = self.observe([path])
                receipt = report['invocation_receipts'][0]
                self.assertEqual(receipt['status'], 'unavailable')
                self.assertIsNone(receipt['duration_ms'])
                self.assertTrue(receipt['gaps'])
                self.assertEqual(report['receipt_sources'][0]['snapshot_bytes'], len(original.encode()))
                self.assertEqual(path.read_text(), original)

    def test_invalid_or_missing_host_counters_are_unknown_while_valid_zero_is_observed(self):
        for value in (None, -1, True, '7858', 7.5):
            with self.subTest(value=value):
                receipt = self.observe([self.receipt(duration_ms=value, duration_api_ms=value,
                                                      num_turns=value)])['invocation_receipts'][0]
                self.assertEqual(receipt['status'], 'matched')
                for field in ('duration_ms', 'duration_api_ms', 'num_turns'):
                    self.assertIsNone(receipt[field])
                self.assertTrue(receipt['gaps'])
        path = self.receipt()
        data = json.loads(path.read_text())
        del data['duration_ms']
        path.write_text(json.dumps(data))
        self.assertIsNone(self.observe([path])['invocation_receipts'][0]['duration_ms'])
        receipt = self.observe([self.receipt(duration_ms=0, duration_api_ms=0,
                                             num_turns=0)])['invocation_receipts'][0]
        self.assertEqual((receipt['duration_ms'], receipt['duration_api_ms'], receipt['num_turns']), (0, 0, 0))

    def test_duplicate_receipt_occurrences_keep_provenance_without_token_or_result_doubling(self):
        path = self.receipt()
        duplicate = self.root / 'duplicate.json'
        duplicate.write_text(path.read_text())
        report = self.observe([path, path, duplicate])
        self.assertEqual(len(report['receipt_sources']), 3)
        self.assertEqual(len(report['invocation_receipts']), 3)
        self.assertEqual(report['call_coverage']['identified_matched_result_count'], 1)
        self.assertEqual(report['call_coverage']['observed_server_message_count'], 1)
        self.assertEqual(report['totals']['server_input_tokens'], 22)
        self.assertEqual({r['receipt_source_index'] for r in report['invocation_receipts']}, {0, 1, 2})

    def test_conflicting_same_result_identity_is_retained_and_flagged(self):
        a = self.receipt('a.json')
        b = self.receipt('b.json', duration_ms=9000)
        report = self.observe([a, b])
        self.assertEqual([r['duration_ms'] for r in report['invocation_receipts']], [7858, 9000])
        self.assertTrue(all(any('conflict' in g['reason'].lower() for g in r['gaps'])
                            for r in report['invocation_receipts']))
        self.assertEqual(report['call_coverage']['identified_matched_result_count'], 1)
        self.assertIsNone(report['whole_work']['duration_ms'])

    def test_missing_result_uuid_cannot_establish_an_identified_result(self):
        report = self.observe([self.receipt(uuid=None)])
        self.assertEqual(report['invocation_receipts'][0]['status'], 'matched')
        self.assertEqual(report['call_coverage']['identified_matched_result_count'], 0)
        self.assertEqual(report['call_coverage']['unidentified_matched_result_occurrences'], 1)
        self.assertTrue(report['invocation_receipts'][0]['gaps'])

    def test_multiple_result_receipts_cannot_be_summed_as_whole_elapsed_or_calls(self):
        report = self.observe([self.receipt('a.json'), self.receipt('b.json', uuid='result-b')])
        self.assertEqual(report['call_coverage']['identified_matched_result_count'], 2)
        self.assertIsNone(report['whole_work']['duration_ms'])
        self.assertIsNone(report['whole_work']['duration_api_ms'])
        self.assertIsNone(report['call_coverage']['paid_call_count'])

    def test_codex_does_not_attribute_a_claude_result_and_distinguishes_intervals(self):
        self.write_rows([dict(type='session_meta', payload={'id': 'session-a'}), snapshot(counter(100, 40, 10))])
        report = self.observe([self.receipt()], provider='codex')
        self.assertEqual(report['invocation_receipts'][0]['status'], 'unavailable')
        self.assertEqual(report['invocation_receipts'][0]['native_sources'], [])
        self.assertEqual(report['call_coverage']['observed_server_response_count'], 0)
        self.assertEqual(report['call_coverage']['cumulative_interval_count'], 1)
        self.assertIsNone(report['call_coverage']['paid_call_count'])
        usage = counter(100, 40, 10)
        row = dict(type='token_usage_record', payload={'response_id': 'resp_1',
                   'usage': usage, 'thread_token_usage': usage})
        self.write_rows([row, row, snapshot(usage)])
        report = self.observe(provider='codex')
        self.assertEqual(report['call_coverage']['observed_server_response_count'], 1)
        self.assertEqual(report['call_coverage']['cumulative_interval_count'], 0)

    def test_cli_receipt_input_and_output_protection(self):
        path = self.receipt()
        args = [sys.executable, str(SCRIPT), '--provider', 'anthropic',
                '--source', str(self.source), '--receipt', str(path)]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['invocation_receipts'][0]['duration_api_ms'], 74420)
        for output in (path, self.source):
            with self.subTest(output=output):
                original = output.read_bytes()
                result = subprocess.run(args + ['--output', str(output)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output.read_bytes(), original)

    def test_cli_output_cannot_overwrite_receipt_through_an_existing_file_alias(self):
        receipt = self.receipt()
        output = self.root / 'output.json'
        os.link(receipt, output)
        original = receipt.read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPT), '--provider', 'anthropic',
                                 '--source', str(self.source), '--receipt', str(receipt),
                                 '--output', str(output)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
