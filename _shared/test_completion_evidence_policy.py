"""Core owns current evidence compatibility and focused dependency judgment."""
import copy
import hashlib
import importlib
from pathlib import Path
import tempfile
import unittest

import test_response_route as fixtures


class CompletionEvidencePolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = importlib.import_module('completion_evidence_policy')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a = self.root / 'a'; self.a.write_text('original')
        self.b = self.root / 'b'; self.b.write_text('other')
        self.criteria = [dict(work_unit_id='unit', contract_revision=1, id=key,
            summary=key, source='user-said', admitted=True) for key in ('a', 'b')]
        self.contract = dict(owner=dict(platform='codex', session_id='session'), work_unit_id='unit',
            contract_revision=1, relevant_context={'constraint': ['local']}, criteria=self.criteria)
        self.target = dict(path=str(self.root), authority=str(self.root), owner=self.contract['owner'], selection_origin='derived')
        self.record = dict(contract=self.contract, target=self.target, criteria=self.criteria,
            dependencies=[dict(locator=str(p), digest='sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()) for p in (self.a,self.b)],
            criterion_dependencies=[dict(work_unit_id='unit', contract_revision=1, criterion_id=key, locators=[str(p)])
                for key,p in (('a',self.a), ('b',self.b))])
        self.fixture = fixtures.ResponseRouteTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.event = self.fixture.intake()
        self.criteria = [dict(c, work_unit_id='first') for c in self.criteria]
        fixtures.ledger.record_turn(**self.fixture.coords, expected_input_event_id=self.fixture.event,
            intent_delta={'acceptance_criteria': self.criteria})
        route = self.fixture.candidate()
        route['selected_criteria'] = [dict(work_unit_id='first', contract_revision=1, criterion_id=k) for k in ('a','b')]
        self.fixture.record(route)
        self.source = dict(GHOST_ALICE_CORE_ROOT=str(fixtures.ROOT),
            GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root), GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='current')
        import completion_assessment as assessment
        proof = '\n'.join(['[completion-check]', '- acceptance-criteria:',
            '  - a: a [source: user-said]', '  - b: b [source: user-said]', '- claim-evidence-map:',
            '  - claim: a', '    criterion: a', '    evidence: original a', '    verdict: pass',
            '  - claim: b', '    criterion: b', '    evidence: original b', '    verdict: pass',
            '- unverified:', '  - none', '- evidence: original observation'])
        candidate = assessment.prepare_assessment(self.source, proof=proof, verified_at='2026-10-10T16:20:10Z',
            source_paths=[str(self.a), str(self.b)])
        assessment.record_assessment(self.source, candidate['candidate_reference'], [dict(binding_id=b['binding_id'],
            source_ids=[candidate['sources'][i]['source_id']], verdict='supported', rationale='Fixture observation establishes the condition')
            for i,b in enumerate(candidate['bindings'])])
        self.contract = candidate['contract']
        self.record.update(contract=self.contract, criteria=self.criteria, proof=proof, verified_at=candidate['verified_at'])
        for relation in self.record['criterion_dependencies']: relation['work_unit_id'] = 'first'
        self.assessment = assessment.lookup_assessment(self.source, self.contract, proof, candidate['verified_at'])

    def coverage(self, current=None, dependencies=None):
        dependencies = dependencies if dependencies is not None else {d['locator']:d['digest'] for d in self.record['dependencies']}
        return self.policy.assess_reference_coverage({'reference':self.record}, current or self.contract,
            self.target, dependencies, source=self.source, allow_empty=True,
            assessments={'reference':self.assessment})

    def test_changed_definition_or_dependency_affects_only_owned_criterion(self):
        current = copy.deepcopy(self.contract)
        current['criteria'][0]['summary'] = 'different condition'
        self.assertEqual(self.coverage(current), {('first',1,'b'):'reference'})
        self.assertEqual(self.coverage(dependencies={str(self.a):None, str(self.b):self.record['dependencies'][1]['digest']}),
                         {('first',1,'b'):'reference'})

    def test_new_revision_does_not_adopt_changed_context(self):
        current = copy.deepcopy(self.contract)
        current['contract_revision'] = 2
        for row in current['criteria']: row['contract_revision'] = 2
        self.assertEqual(set(self.coverage(current)), {('first',2,'a'), ('first',2,'b')})
        current['relevant_context']['constraint'] = ['different']
        with self.assertRaises(self.policy.CompletionEvidencePolicyError) as rejected:
            self.coverage(current)
        self.assertEqual(rejected.exception.reason, 'assessment-required')

    def test_dependency_check_is_focused_and_missing_differs_from_unavailable(self):
        self.b.unlink(); self.b.mkdir()
        unaffected = self.policy.assess_criterion_dependencies(self.record, 'reference', 'a')
        self.assertFalse(unaffected['changed'])
        self.assertIsNone(unaffected['error'])
        unavailable = self.policy.assess_criterion_dependencies(self.record, 'reference', 'b')
        self.assertFalse(unavailable['changed'])
        self.assertIsInstance(unavailable['error'], OSError)
        self.a.unlink()
        missing = self.policy.assess_criterion_dependencies(self.record, 'reference', 'a')
        self.assertTrue(missing['changed'])
        self.assertIsNone(missing['error'])

    def test_unavailable_sibling_does_not_erase_known_mutation(self):
        self.record['criterion_dependencies'][0]['locators'].append(str(self.b))
        self.a.write_text('changed'); self.b.unlink(); self.b.mkdir()
        result = self.policy.assess_criterion_dependencies(self.record, 'reference', 'a')
        self.assertTrue(result['changed'])
        self.assertIsInstance(result['error'], OSError)
        self.assertEqual(len(result['observations']), 2)

    def test_changed_review_source_removes_only_its_bound_criterion(self):
        self.a.write_text('Changed observation')
        try:
            actual = self.policy.assess_reference_coverage({'reference':self.record}, self.contract,
                self.target, {d['locator']:d['digest'] for d in self.record['dependencies']},
                source=self.source, allow_empty=True)
        except self.policy.CompletionEvidencePolicyError as exc:
            self.fail('Sibling review must remain available: ' + str(exc))
        self.assertEqual(actual, {('first',1,'b'):'reference'})
