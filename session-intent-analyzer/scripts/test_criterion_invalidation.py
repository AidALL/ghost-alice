"""Observed proof invalidation reopens only its exact currently selected owner.

Dependencies: Python 3.11+ standard library and session_intent_ledger.
"""
from pathlib import Path
import tempfile
import unittest
import session_intent_ledger as ledger


class CriterionInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.coords=dict(root=self.root,platform='codex',session_id='owner')
        self.rows=[dict(id='same',summary='Output '+u,source='user-explicit',admitted=True,
            work_unit_id=u,contract_revision=1) for u in ('one','two')]
        ledger.record_turn(**self.coords,raw_user_input='Verify owned outputs',intent_delta={'acceptance_criteria':self.rows})
        self.event=ledger.read_session_state(**self.coords)['latest_input_event_id'];self.route()
        for row in self.rows:
            ledger.mark_acceptance_criterion_met(**self.coords,criterion_id='same',expected_input_event_id=self.event,
                expected_criterion=row,completion_check_digest='a'*64)

    def route(self,selected=('one','two')):
        surface=dict(intent_relation='continuation',primary_request='Verify owned outputs',causal_axis='n/a',
            response_mode='normal',response_order='imperative-result-first-after-necessary-work',change_depth='localized',
            focus_layer='meso',verification_complexity='level-2',boundary_contract='required',forced_visibility='no',reason='Test')
        ledger.record_response_route(**self.coords,expected_input_event_id=self.event,boundary_ref='test',candidate=dict(
            schema_version='response-route.v1',routing_surface=surface,selected_work_unit_ids=list(selected),
            work_units=[dict(work_unit_id=u,contract_revision=1,kind='execution',admitted=True,
                owner=dict(platform='codex',session_id='owner')) for u in selected],
            selected_criteria=[dict(work_unit_id=u,contract_revision=1,criterion_id='same') for u in selected]))

    def invalidate(self,**changes):
        args=dict(**self.coords,criterion_id='same',expected_input_event_id=self.event,
            expected_criterion=self.rows[1],expected_completion_check_digest='a'*64,
            invalidation_reason='dependency-mismatch')
        args.update(changes)
        self.assertTrue(callable(getattr(ledger,'invalidate_acceptance_criterion',None)), 'Missing public proof-CAS invalidation API')
        return ledger.invalidate_acceptance_criterion(**args)

    def state(self):return ledger.read_session_state(**self.coords)
    def events(self):return ledger.read_session_events(**self.coords)

    def test_exact_proof_reopens_only_qualified_owner_and_preserves_history(self):
        before=self.state();self.invalidate();after=self.state()
        self.assertEqual(after['acceptance_criteria'][0],before['acceptance_criteria'][0])
        changed=after['acceptance_criteria'][1]
        self.assertEqual(changed['status'],'unmet')
        self.assertNotIn('met_completion_check_digest',changed);self.assertNotIn('met_at',changed)
        self.assertEqual({k:changed[k] for k in self.rows[1]},self.rows[1])
        event=[e for e in self.events() if e['event']=='acceptance-criterion-evidence-invalidated'][0]
        self.assertEqual(event['prior_completion_check_digest'],'a'*64)
        self.assertEqual(event['prior_met_at'],before['acceptance_criteria'][1]['met_at'])
        self.assertEqual(event['criterion_source'],'user-explicit')
        self.assertNotIn('artifact',event);self.assertNotIn('proof',event)

    def test_duplicate_same_invalidation_is_noop_and_newer_proof_is_not_undone(self):
        self.invalidate();before=self.state();events=self.events();self.invalidate()
        self.assertEqual(self.state(),before);self.assertEqual(self.events(),events)
        ledger.mark_acceptance_criterion_met(**self.coords,criterion_id='same',expected_input_event_id=self.event,
            expected_criterion=self.rows[1],completion_check_digest='b'*64)
        before=self.state()
        with self.assertRaises(ValueError):self.invalidate()
        self.assertEqual(self.state(),before)

    def test_stale_foreign_unselected_or_revised_snapshot_is_atomic(self):
        for change in (dict(expected_input_event_id='old'),dict(session_id='foreign'),
                       dict(expected_criterion=dict(self.rows[1],summary='Other meaning')),
                       dict(expected_criterion=dict(self.rows[1],contract_revision=2)),
                       dict(expected_completion_check_digest='c'*64),dict(invalidation_reason='unknown')):
            before=self.state();events=self.events()
            with self.subTest(change=change),self.assertRaises(ValueError):self.invalidate(**change)
            self.assertEqual(self.state(),before);self.assertEqual(self.events(),events)
        self.route(selected=('one',));before=self.state()
        with self.assertRaises(ValueError):self.invalidate()
        self.assertEqual(self.state(),before)

    def test_unmet_without_prior_same_invalidation_is_not_a_duplicate(self):
        ledger.record_turn(**self.coords,intent_delta={'acceptance_criteria':[dict(self.rows[1],admitted=False)]},
            expected_input_event_id=self.event)
        with self.assertRaises(ValueError):self.invalidate()

    def test_shared_transaction_rolls_back_state_and_invalidation_event(self):
        before=self.state();events=self.events()
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with ledger.storage_transaction(root=self.root) as connection:
                self.invalidate(transaction=connection)
                raise RuntimeError('rollback')
        self.assertEqual(self.state(),before);self.assertEqual(self.events(),events)
