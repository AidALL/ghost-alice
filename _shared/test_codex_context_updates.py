"""Explicit source-local updates through real Codex/Core request selection."""
import copy
from contextlib import redirect_stdout
import io
import json
import os
import unittest
from unittest.mock import patch

import context_lifecycle as lifecycle
import test_context_lifecycle as fixtures


class CodexContextUpdateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ContextLifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.first = dict(self.fixture.selection, disposition='omit')
        self.first_receipt = self.fixture.record([self.first])
        self.opaque = dict(type='reasoning', encrypted_content='unchanged opaque reasoning', summary=[])
        self.call = dict(type='function_call', call_id='second', name='lookup', arguments='{"path":"source"}')
        self.result = dict(type='function_call_output', call_id='second', output='second consumed original')
        for item in (self.opaque, self.call, self.result):
            self.fixture.rows.append(dict(type='response_item', payload=item))
            self.fixture.request['input'].append(item)
        self.fixture.write_rows()
        self.second = dict(record=7, block_index=0, disposition='omit',
            summary='The second source comparison is resolved.', next_need='Retrieve if that comparison changes.')

    def update(self, choices, *, event=None):
        return lifecycle.record_disposition(self.fixture.source, event or self.fixture.fixture.event,
            choices, selection_mode='update')

    def select(self):
        return lifecycle.select_request(self.fixture.request, self.fixture.fixture.root, self.fixture.home)

    def current_payload(self):
        core, root, _ = lifecycle.core_api._coordinates(self.fixture.source)
        with core._read_connection(root) as connection:
            row = connection.execute('''SELECT d.reference,d.payload_json FROM core_context_current c
                JOIN core_context_dispositions d ON d.reference=c.reference
                WHERE c.platform='codex' AND c.session_id='current' ''').fetchone()
        return row[0], json.loads(row[1])

    def assert_both_omitted(self, result):
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.request['input'][2]['output'], [self.fixture.output['output'][1]])
        self.assertEqual(result.request['input'][-1]['output'], '')
        self.assertEqual(result.request['input'][0], self.fixture.user)
        self.assertEqual(result.request['input'][3], self.opaque)
        self.assertEqual(result.request['input'][1], self.fixture.call)
        self.assertEqual(result.request['input'][-2], self.call)

    def test_updating_second_source_keeps_first_explicit_omission(self):
        originals = self.fixture.log.read_bytes()
        request = copy.deepcopy(self.fixture.request)
        receipt = self.update([self.second])
        self.assertEqual(receipt['selected_count'], 2)
        result = self.select()
        self.assert_both_omitted(result)
        self.assertEqual(self.fixture.request, request)
        self.assertEqual(self.fixture.log.read_bytes(), originals)
        _, payload = self.current_payload()
        self.assertEqual(payload['schema_version'], 'context-disposition.v4')
        self.assertEqual(payload['retained'], [dict(reference=self.first_receipt['reference'], selected_index=0)])
        old = next(item for item in result.observations if item['original'].endswith('record=4,output=0'))
        self.assertEqual(old['reference'], self.first_receipt['reference'])

    def test_source_local_update_changes_resurrection_behavior_without_changing_full_refresh(self):
        self.fixture.record([self.second])
        refreshed = self.select()
        self.assertEqual(refreshed.request['input'][2], self.fixture.output)
        self.assertEqual(refreshed.request['input'][-1]['output'], '')
        self.fixture.record([self.first])
        self.update([self.second])
        self.assert_both_omitted(self.select())

    def test_retained_choice_must_exist_in_its_original_source_prefix(self):
        self.update([self.second])
        _, original = self.current_payload()
        core, root, _ = lifecycle.core_api._coordinates(self.fixture.source)
        with core._read_connection(root) as connection:
            old = json.loads(connection.execute('SELECT payload_json FROM core_context_dispositions WHERE reference=?',
                (self.first_receipt['reference'],)).fetchone()[0])
        old['selected'] = [original['selected'][0]]
        reference = lifecycle.fingerprint(old)
        with core._transaction(root) as connection:
            connection.execute('INSERT INTO core_context_dispositions VALUES (?,?)', (reference, json.dumps(old)))
        forged = dict(copy.deepcopy(original), selected=[], retained=[dict(reference=reference, selected_index=0)])
        self.replace_current_for_negative_fixture(forged)
        result = self.select()
        self.assertEqual(result.request, self.fixture.request)
        self.assertEqual(result.observations, ())

    def test_line_separator_inside_a_record_does_not_split_a_retained_original(self):
        """Codex writes U+2028 unescaped inside JSON strings; only a newline ends a record."""
        self.fixture.rows[0]['payload']['instructions'] = 'first second'
        self.fixture.log.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n'
                                            for row in self.fixture.rows), encoding='utf-8')
        self.fixture.record([self.first])
        self.update([self.second])
        self.assert_both_omitted(self.select())

    def test_targeted_keep_restores_only_the_selected_source(self):
        self.update([self.second])
        self.update([dict(self.first, disposition='keep')])
        result = self.select()
        self.assertEqual(result.request['input'][2], self.fixture.output)
        self.assertEqual(result.request['input'][-1]['output'], '')
        self.assertEqual(len(result.observations), 1)

    def test_empty_update_preserves_choices_and_default_empty_refresh_clears_them(self):
        receipt = self.update([self.second])
        empty = self.update([])
        self.assertEqual(empty['selected_count'], 2)
        self.assert_both_omitted(self.select())
        _, payload = self.current_payload()
        self.assertEqual(payload['retained'], [dict(reference=self.first_receipt['reference'], selected_index=0),
            dict(reference=receipt['reference'], selected_index=0)])
        self.fixture.record([])
        self.assertEqual(self.select().request, self.fixture.request)

    def test_retained_decision_keeps_original_input_binding_after_new_human(self):
        first_event = self.fixture.fixture.event
        next_event = self.fixture.fixture.intake('continue the second comparison')
        self.fixture.rows.append(dict(type='response_item', payload=dict(self.fixture.user,
            content=[dict(type='input_text', text='continue the second comparison')])))
        self.fixture.write_rows()
        self.fixture.request['input'].append(self.fixture.rows[-1]['payload'])
        self.update([self.second], event=next_event)
        result = self.select()
        old = next(item for item in result.observations if item['original'].endswith('record=4,output=0'))
        self.assertEqual(old['disposition_input_event_id'], first_event)
        self.assertEqual(old['current_input_event_id'], next_event)
        self.assertEqual(result.request['input'][-1], self.fixture.rows[-1]['payload'])

    def test_changed_retained_original_rejects_update_without_changing_current_state(self):
        before = self.current_payload()
        self.fixture.rows[3]['payload']['output'][0]['text'] = 'changed original'
        self.fixture.write_rows()
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            self.update([self.second])
        self.assertEqual(self.current_payload(), before)

    def test_explicit_exchange_update_supersedes_earlier_output_choices(self):
        self.update([self.second])
        self.update([dict(self.first, kind='exchange')])
        result = self.select()
        self.assertNotIn(self.fixture.call, result.request['input'])
        self.assertFalse(any(item.get('call_id') == 'consumed' for item in result.request['input']))
        self.assertIn(self.call, result.request['input'])
        self.assertEqual(result.request['input'][-1]['output'], '')
        self.assertEqual(len(result.observations), 2)

    def test_partial_update_cannot_silently_narrow_retained_exchange_scope(self):
        self.fixture.record([dict(self.first, kind='exchange')])
        before = self.current_payload()
        with self.assertRaisesRegex(lifecycle.ContextSelectionRejected, 'exchange'):
            self.update([dict(self.first, block_index=1, disposition='keep')])
        self.assertEqual(self.current_payload(), before)

    def test_updates_to_prior_message_blocks_preserve_current_human_and_group_omissions(self):
        fixture = fixtures.ContextLifecycleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        prior = dict(type='message', role='assistant', content=[
            dict(type='output_text', text='first used passage'), dict(type='output_text', text='second used passage')])
        fixture.rows.insert(1, dict(type='response_item', payload=prior))
        fixture.write_rows()
        fixture.request['input'].insert(0, prior)
        first = dict(fixture.selection, record=2, kind='message', disposition='omit')
        fixture.record([first])
        lifecycle.record_disposition(fixture.source, fixture.fixture.event,
            [dict(first, block_index=1, kind='assistant')], selection_mode='update')
        result = lifecycle.select_request(fixture.request, fixture.fixture.root, fixture.home)
        self.assertEqual(result.request['input'], fixture.request['input'][1:])
        self.assertEqual(len(result.observations), 2)
        self.assertTrue(all(item['forwarded_item_digest'] is None for item in result.observations))

    def test_stateful_request_preserves_original_even_after_update(self):
        self.update([self.second])
        self.fixture.request['previous_response_id'] = 'provider-owned-history'
        result = self.select()
        self.assertEqual(result.request, self.fixture.request)
        self.assertEqual(result.reason, 'stateful-request-selection-unsupported')
        self.assertEqual(result.observations, ())

    def test_existing_cli_publishes_source_local_update_under_actual_owner(self):
        stream = io.StringIO()
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'current'}), redirect_stdout(stream):
            lifecycle.main(['--root', str(self.fixture.fixture.root), '--native-dir', str(self.fixture.home),
                '--session-id', 'current', '--expected-input-event-id', self.fixture.fixture.event,
                '--selection-mode', 'update', '--selection-json', json.dumps([self.second])])
        receipt = json.loads(stream.getvalue())
        self.assertEqual(receipt['selection_mode'], 'update')
        self.assertEqual(receipt['updated_count'], 1)
        self.assert_both_omitted(self.select())

    def replace_current_for_negative_fixture(self, payload):
        reference = lifecycle.fingerprint(payload)
        core, root, _ = lifecycle.core_api._coordinates(self.fixture.source)
        with core._transaction(root) as connection:
            connection.execute('INSERT INTO core_context_dispositions VALUES (?,?)',
                (reference, json.dumps(payload)))
            connection.execute('UPDATE core_context_current SET reference=?', (reference,))

    def test_duplicate_or_malformed_retained_locators_cannot_select_bodies(self):
        self.update([self.second])
        _, original = self.current_payload()
        malformed = [dict(reference='unavailable', selected_index=0),
            dict(reference=self.first_receipt['reference'], selected_index=-1),
            dict(reference=self.first_receipt['reference'], selected_index=True),
            dict(reference=self.first_receipt['reference'], selected_index=0, extra='unknown')]
        payloads = [dict(copy.deepcopy(original), retained=[locator]) for locator in malformed]
        payloads.append(dict(copy.deepcopy(original), retained=original['retained'] * 2))
        for payload in payloads:
            with self.subTest(retained=payload['retained']):
                self.replace_current_for_negative_fixture(payload)
                result = self.select()
                self.assertEqual(result.request, self.fixture.request)
                self.assertEqual(result.state, 'preserved')
                self.assertEqual(result.observations, ())
                before = self.current_payload()
                with self.assertRaises(lifecycle.ContextSelectionRejected):
                    self.update([self.second])
                self.assertEqual(self.current_payload(), before)

    def test_retained_locator_must_point_to_originating_choice_not_an_update_chain(self):
        empty = self.update([])
        _, original = self.current_payload()
        forged = dict(copy.deepcopy(original),
            retained=[dict(reference=empty['reference'], selected_index=0)], selected=[dict(self.second,
                item_digest=lifecycle.fingerprint(self.result), block_digest=lifecycle.fingerprint(self.result['output']))])
        self.replace_current_for_negative_fixture(forged)
        result = self.select()
        self.assertEqual(result.request, self.fixture.request)
        self.assertEqual(result.observations, ())

    def test_dangling_current_reference_cannot_silently_drop_prior_decisions(self):
        core, root, _ = lifecycle.core_api._coordinates(self.fixture.source)
        with core._transaction(root) as connection:
            connection.execute('UPDATE core_context_current SET reference=?', ('unavailable',))
            before = connection.execute('SELECT COUNT(*) FROM core_context_dispositions').fetchone()[0]
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            self.update([self.second])
        with core._read_connection(root) as connection:
            self.assertEqual(connection.execute('SELECT reference FROM core_context_current').fetchone()[0], 'unavailable')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM core_context_dispositions').fetchone()[0], before)

    def corrupt_retained_file_reference(self, transform, *, select_second=False):
        self.update([self.second])
        _, current = self.current_payload()
        core, root, _ = lifecycle.core_api._coordinates(self.fixture.source)
        with core._read_connection(root) as connection:
            origin = json.loads(connection.execute('SELECT payload_json FROM core_context_dispositions WHERE reference=?',
                (self.first_receipt['reference'],)).fetchone()[0])
        transform(origin['original'])
        if select_second:
            origin['selected'] = current['selected']
        reference = lifecycle.fingerprint(origin)
        with core._transaction(root) as connection:
            connection.execute('INSERT INTO core_context_dispositions VALUES (?,?)', (reference, json.dumps(origin)))
        self.replace_current_for_negative_fixture(dict(current, selected=[],
            retained=[dict(reference=reference, selected_index=0)]))

    def test_retained_original_requires_native_file_descriptor_without_crashing(self):
        self.corrupt_retained_file_reference(lambda original: (original.clear(), original.update(locator=str(self.fixture.log.resolve()))))
        before = self.current_payload()
        result = self.select()
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.request, self.fixture.request)
        self.assertEqual(result.observations, ())
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            self.update([self.second])
        self.assertEqual(self.current_payload(), before)

    def test_inline_fallback_cannot_substitute_a_future_block_for_original_file_prefix(self):
        self.corrupt_retained_file_reference(lambda original: original.update(storage='unclassified-inline',
            text=self.fixture.log.read_text()), select_second=True)
        before = self.current_payload()
        result = self.select()
        self.assertEqual(result.request, self.fixture.request)
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.observations, ())
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            self.update([self.second])
        self.assertEqual(self.current_payload(), before)


if __name__ == '__main__':
    unittest.main()
