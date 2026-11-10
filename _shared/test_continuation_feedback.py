"""The response model's declared remaining work uses existing originals that Core records and binds.

Dependencies: Python 3.11+ standard library and the canonical Core test fixture.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest

import completion_assessment as assessment
import test_response_route as fixtures

ledger = fixtures.ledger


class ContinuationFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.event = self.fixture.setup_owned()
        self.fixture.record()
        self.source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root),
                           GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='current',
                           CODEX_THREAD_ID='current')
        self.original = Path(self.fixture.tmp.name) / 'original.txt'
        self.original.write_text('The unprocessed required result needs this original passage.')
        body = 'The unprocessed required result needs this original passage.'
        candidate = assessment.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.original)], claim_statements=[body])
        self.selector = dict(candidate_reference=candidate['candidate_reference'], source_id=candidate['sources'][0]['source_id'])
        self.ref = dict(work_unit_id='first', contract_revision=1, criterion_id='AC1')

    def candidate(self, rows=None, control='active'):
        candidate = self.fixture.candidate()
        candidate['declared_remaining_work'] = dict(schema_version='declared-remaining-work.v1',
            control=control, items=[] if rows is None else rows)
        return candidate

    def required(self):
        return dict(criterion_refs=[self.ref], reason='The required result still needs the original evidence.',
            next_information='Read the selected original passage and resolve its uncertainty.',
            source_refs=[self.selector], prerequisite='ready')

    def read(self):
        return self.fixture.api.read_response_route(**self.fixture.coords)

    def test_explicitly_declared_no_remaining_work_is_distinct_from_absence(self):
        self.assertNotIn('declared_remaining_work', self.read())
        self.fixture.record(self.candidate())
        self.assertEqual(self.read()['declared_remaining_work']['items'], [])

    def test_legacy_mixed_declarations_write_once_and_conflicts_roll_back(self):
        current = self.candidate([self.required()], control='model-reported-human-stop')
        legacy = deepcopy(current['declared_remaining_work'])
        legacy.update(schema_version='continuation-feedback.v1', control='human-stop')
        legacy['required_unprocessed'] = legacy.pop('items')
        candidate = deepcopy(current)
        candidate['continuation_feedback'] = legacy
        recorded = self.fixture.record(candidate)
        self.assertNotIn('continuation_feedback', recorded)
        self.assertEqual(recorded['declared_remaining_work']['control'], 'model-reported-human-stop')
        self.assertEqual(len(recorded['declared_remaining_work']['items']), 1)
        # A mixed legacy control/new container follows the same original-source authentication.
        candidate.pop('continuation_feedback')
        candidate['declared_remaining_work'] = legacy
        self.assertEqual(self.fixture.record(candidate), recorded)
        before = ledger.read_session_state(**self.fixture.coords)
        events = ledger.read_session_events(**self.fixture.coords)
        conflicting_container = deepcopy(current)
        conflicting_container['continuation_feedback'] = dict(legacy, control='active')
        conflicting_items = deepcopy(candidate)
        conflicting_items['declared_remaining_work']['items'] = []
        conflicting_types = deepcopy(current)
        conflicting_types['continuation_feedback'] = deepcopy(legacy)
        conflicting_types['continuation_feedback']['required_unprocessed'][0]['criterion_refs'][0]['contract_revision'] = True
        conflicting_item_types = deepcopy(current)
        conflicting_item_types['declared_remaining_work']['required_unprocessed'] = deepcopy(legacy['required_unprocessed'])
        conflicting_item_types['declared_remaining_work']['required_unprocessed'][0]['criterion_refs'][0]['contract_revision'] = True
        for invalid in (conflicting_container, conflicting_items, conflicting_types, conflicting_item_types):
            with self.subTest(candidate=invalid), self.assertRaises(self.fixture.api.RouteSchemaError):
                self.fixture.record(invalid)
            self.assertEqual(ledger.read_session_state(**self.fixture.coords), before)
            self.assertEqual(ledger.read_session_events(**self.fixture.coords), events)

    def test_stored_legacy_declaration_keeps_original_and_holds_only_changed_source_row(self):
        first, second = self.required(), self.required()
        second['criterion_refs'] = [dict(self.ref, work_unit_id='second')]
        second['source_refs'] = []
        candidate = self.fixture.candidate(units=('first', 'second'))
        candidate['declared_remaining_work'] = self.candidate([first, second])['declared_remaining_work']
        self.fixture.record(candidate)
        state = ledger.read_session_state(**self.fixture.coords)
        legacy = state['response_route'].pop('declared_remaining_work')
        legacy['schema_version'] = 'continuation-feedback.v1'
        legacy['required_unprocessed'] = legacy.pop('items')
        state['response_route'].update(schema_version='response-route.v1', continuation_feedback=legacy)
        with ledger.storage_transaction(self.fixture.root) as connection:
            connection.execute('UPDATE sessions SET state_json=? WHERE platform=? AND session_id=?',
                (json.dumps(state), 'codex', 'current'))
        self.original.write_text('Only the first original changed.')
        route = self.read()
        self.assertEqual(route['continuation_feedback'], legacy)
        self.assertEqual(route['declared_remaining_work_issues'], [dict(
            pointer='/declared_remaining_work/items/0', issue='source-changed')])
        import stop_notice
        work = stop_notice._remaining_work(dict(response_route=route, completion_route=dict(route_binding='current')))
        self.assertEqual([row.get('source_issue') for row in work['items']], ['source-changed', None])
        self.assertEqual(ledger.read_session_state(**self.fixture.coords)['response_route'], state['response_route'])

    def test_current_needed_work_resolves_existing_original_without_another_body_copy(self):
        self.fixture.record(self.candidate([self.required()]))
        route = self.read()
        ref = route['declared_remaining_work']['items'][0]['source_refs'][0]
        self.assertEqual(ref['candidate_reference'], self.selector['candidate_reference'])
        self.assertEqual(ref['source']['locator'], str(self.original))
        self.assertNotIn('text', ref['source'])
        self.assertNotIn('bytes_base64', ref['source'])
        self.assertEqual(route['input_event_id'], self.fixture.event)
        self.assertEqual(route['boundary_ref'], 'boundary-1')

    def test_source_free_declared_item_uses_current_recorded_input_and_selected_criterion(self):
        row = self.required(); row['source_refs'] = []
        self.fixture.record(self.candidate([row]))
        self.assertEqual(self.read()['declared_remaining_work']['items'], [row])

    def test_missing_prerequisite_and_human_stop_preserve_the_current_declared_judgment(self):
        row = self.required(); row['prerequisite'] = 'missing'
        self.fixture.record(self.candidate([row], control='model-reported-human-stop'))
        feedback = self.read()['declared_remaining_work']
        self.assertEqual(feedback['control'], 'model-reported-human-stop')
        self.assertEqual(feedback['items'][0]['prerequisite'], 'missing')

    def test_unselected_overlapping_and_unknown_rows_roll_back_the_work_record(self):
        invalid = []
        extra = self.required(); extra['criterion_refs'] = [dict(self.ref, work_unit_id='second')]; invalid.append([extra])
        invalid.append([self.required(), self.required()])
        bad = self.required(); bad['prerequisite']='assume-ready'; invalid.append([bad])
        bad = self.required(); bad['reason']=' '; invalid.append([bad])
        bad = self.required(); bad['source_refs']=[dict(self.selector, source={'locator':'/unverified'})]; invalid.append([bad])
        before = ledger.read_session_state(**self.fixture.coords)
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.fixture.record(self.candidate(rows))
            self.assertEqual(ledger.read_session_state(**self.fixture.coords), before)

    def test_fabricated_source_and_changed_original_are_rejected_atomically(self):
        before = ledger.read_session_state(**self.fixture.coords)
        row = self.required(); row['source_refs']=[dict(self.selector, candidate_reference='sha256:'+'0'*64)]
        with self.assertRaises(ValueError): self.fixture.record(self.candidate([row]))
        self.assertEqual(ledger.read_session_state(**self.fixture.coords), before)
        self.original.write_text('Relevant original changed.')
        with self.assertRaises(ValueError): self.fixture.record(self.candidate([self.required()]))
        self.assertEqual(ledger.read_session_state(**self.fixture.coords), before)

    def test_changed_source_suppresses_only_feedback_and_preserves_work_and_proof(self):
        self.fixture.record(self.candidate([self.required()]))
        before = ledger.read_session_state(**self.fixture.coords)
        self.original.write_text('Changed original after its feedback declaration.')
        route = self.read()
        self.assertEqual(route['status'], 'ready')
        self.assertEqual(route['selected_work_unit_ids'], ['first'])
        self.assertEqual(route['declared_remaining_work'], before['response_route']['declared_remaining_work'])
        self.assertEqual(route['declared_remaining_work_issues'], [dict(
            pointer='/declared_remaining_work/items/0', issue='source-changed')])
        self.assertEqual(ledger.read_session_state(**self.fixture.coords), before)

    def test_changed_original_holds_only_its_group_and_keeps_original_event_coordinates(self):
        first = self.required()
        second = self.required()
        second['criterion_refs'] = [dict(self.ref, work_unit_id='second')]
        second['source_refs'] = []
        candidate = self.fixture.candidate(units=('first', 'second'))
        candidate['declared_remaining_work'] = self.candidate([first, second])['declared_remaining_work']
        self.fixture.record(candidate)
        before = ledger.read_session_state(**self.fixture.coords)
        self.original.write_text('First group original changed; second group is independent.')
        route = self.read()
        self.assertEqual(route['declared_remaining_work']['items'],
            before['response_route']['declared_remaining_work']['items'])
        self.assertEqual(route['declared_remaining_work_issues'], [dict(
            pointer='/declared_remaining_work/items/0', issue='source-changed')])
        projected = {k:v for k,v in route.items()
            if k not in {'current_input_event_id', 'declared_remaining_work_issues'}}
        self.assertEqual(ledger.json_digest(projected), ledger.json_digest(before['response_route']))
        self.assertEqual(ledger.read_session_state(**self.fixture.coords), before)

    def test_structured_controls_are_rejected_as_schema_errors_without_mutation(self):
        before = ledger.read_session_state(**self.fixture.coords)
        for value in ({'control': 'active'}, ['ready']):
            with self.subTest(field='control', value=value), self.assertRaises(self.fixture.api.RouteSchemaError):
                self.fixture.record(self.candidate([self.required()], control=value))
            row = self.required(); row['prerequisite'] = value
            with self.subTest(field='prerequisite', value=value), self.assertRaises(self.fixture.api.RouteSchemaError):
                self.fixture.record(self.candidate([row]))
            self.assertEqual(ledger.read_session_state(**self.fixture.coords), before)

    def test_latest_input_makes_previous_feedback_stale_without_rebinding_its_original_input(self):
        self.fixture.record(self.candidate([self.required()]))
        current = self.fixture.intake('Stop the previous work.')
        route = self.read()
        self.assertEqual(route['status'], 'stale')
        self.assertEqual(route['input_event_id'], self.fixture.event)
        self.assertEqual(route['current_input_event_id'], current)

    def test_direct_canonical_writer_authenticates_sources_and_matches_original_event_digest(self):
        route = ledger.record_response_route(**self.fixture.coords,
            expected_input_event_id=self.fixture.event, candidate=self.candidate([self.required()]),
            boundary_ref='test-boundary')
        self.assertIn('source', route['declared_remaining_work']['items'][0]['source_refs'][0])
        state = ledger.read_session_state(**self.fixture.coords)
        projected = {k:v for k,v in self.read().items() if k!='current_input_event_id'}
        self.assertEqual(ledger.json_digest(projected), ledger.json_digest(state['response_route']))
        self.assertNotIn(self.original.read_text(), json.dumps(state['response_route']))


if __name__ == '__main__':
    unittest.main()
