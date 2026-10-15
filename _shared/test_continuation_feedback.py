"""Current necessary-work feedback uses existing Core originals and authority.

Dependencies: Python 3.11+ standard library and the canonical Core test fixture.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest

import completion_assessment as assessment
from test_response_route import ResponseRouteTests, ledger


class ContinuationFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.event = self.fixture.setup_owned()
        self.fixture.record()
        self.source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root),
                           GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='current',
                           CODEX_THREAD_ID='current')
        self.original = Path(self.fixture.tmp.name) / 'original.txt'
        self.original.write_text('The unprocessed required result needs this original passage.')
        scope = assessment.prepare_response_scope(self.source,
            expected_input_event_id=self.fixture.event, source_paths=[str(self.original)])
        self.selector = dict(candidate_reference=scope['scope_reference'], source_id=scope['sources'][0]['source_id'])
        self.ref = dict(work_unit_id='first', contract_revision=1, criterion_id='AC1')

    def candidate(self, rows=None, control='active'):
        candidate = self.fixture.candidate()
        candidate['continuation_feedback'] = dict(schema_version='continuation-feedback.v1',
            control=control, required_unprocessed=[] if rows is None else rows)
        return candidate

    def required(self):
        return dict(criterion_refs=[self.ref], reason='The required result still needs the original evidence.',
            next_information='Read the selected original passage and resolve its uncertainty.',
            source_refs=[self.selector], prerequisite='ready')

    def read(self):
        return self.fixture.api.read_response_route(**self.fixture.coords)

    def test_explicit_empty_feedback_is_distinct_from_absence_and_does_not_complete_work(self):
        self.assertNotIn('continuation_feedback', self.read())
        self.fixture.record(self.candidate())
        self.assertEqual(self.read()['continuation_feedback']['required_unprocessed'], [])
        state = ledger.read_session_state(**self.fixture.coords)
        self.assertEqual(next(row for row in state['acceptance_criteria'] if row.get('work_unit_id')=='first')['status'], 'unmet')

    def test_current_needed_work_resolves_existing_original_without_another_body_copy(self):
        self.fixture.record(self.candidate([self.required()]))
        route = self.read()
        ref = route['continuation_feedback']['required_unprocessed'][0]['source_refs'][0]
        self.assertEqual(ref['candidate_reference'], self.selector['candidate_reference'])
        self.assertEqual(ref['source']['locator'], str(self.original))
        self.assertNotIn('text', ref['source'])
        self.assertNotIn('bytes_base64', ref['source'])
        self.assertEqual(route['input_event_id'], self.fixture.event)
        self.assertEqual(route['boundary_ref'], 'boundary-1')

    def test_source_free_feedback_uses_current_canonical_human_and_selected_criterion(self):
        row = self.required(); row['source_refs'] = []
        self.fixture.record(self.candidate([row]))
        self.assertEqual(self.read()['continuation_feedback']['required_unprocessed'], [row])

    def test_missing_prerequisite_and_human_stop_preserve_the_current_declared_judgment(self):
        row = self.required(); row['prerequisite'] = 'missing'
        self.fixture.record(self.candidate([row], control='human-stop'))
        feedback = self.read()['continuation_feedback']
        self.assertEqual(feedback['control'], 'human-stop')
        self.assertEqual(feedback['required_unprocessed'][0]['prerequisite'], 'missing')

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
        self.assertEqual(route['continuation_feedback'], before['response_route']['continuation_feedback'])
        self.assertEqual(route['continuation_feedback_issues'], [dict(
            pointer='/continuation_feedback/required_unprocessed/0', issue='source-changed')])
        self.assertEqual(ledger.read_session_state(**self.fixture.coords), before)

    def test_changed_original_holds_only_its_group_and_keeps_original_event_coordinates(self):
        first = self.required()
        second = self.required()
        second['criterion_refs'] = [dict(self.ref, work_unit_id='second')]
        second['source_refs'] = []
        candidate = self.fixture.candidate(units=('first', 'second'))
        candidate['continuation_feedback'] = self.candidate([first, second])['continuation_feedback']
        self.fixture.record(candidate)
        before = ledger.read_session_state(**self.fixture.coords)
        self.original.write_text('First group original changed; second group is independent.')
        route = self.read()
        self.assertEqual(route['continuation_feedback']['required_unprocessed'],
            before['response_route']['continuation_feedback']['required_unprocessed'])
        self.assertEqual(route['continuation_feedback_issues'], [dict(
            pointer='/continuation_feedback/required_unprocessed/0', issue='source-changed')])
        projected = {k:v for k,v in route.items()
            if k not in {'current_input_event_id', 'continuation_feedback_issues'}}
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

    def test_latest_input_makes_previous_feedback_stale_without_rebinding_its_authority(self):
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
        self.assertIn('source', route['continuation_feedback']['required_unprocessed'][0]['source_refs'][0])
        state = ledger.read_session_state(**self.fixture.coords)
        projected = {k:v for k,v in self.read().items() if k!='current_input_event_id'}
        self.assertEqual(ledger.json_digest(projected), ledger.json_digest(state['response_route']))
        self.assertNotIn(self.original.read_text(), json.dumps(state['response_route']))


if __name__ == '__main__':
    unittest.main()
