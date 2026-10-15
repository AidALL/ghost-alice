"""Specialized verification instructions have explicit, resolvable owners."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'coding-convention/verification-before-completion'


class VerificationDisclosureTests(unittest.TestCase):
    def test_specialized_commands_stay_in_triggered_references(self):
        body = (SKILL / 'SKILL.md').read_text()
        for payload in ('--receipt-token', '--reapprove-current-input', '- web-search-evidence:'):
            self.assertNotIn(payload, body)
        for filename, markers in {
            'autopilot-publication.md': ('--receipt-token', '--verified-at', 'publication_route'),
            'external-tool-evidence.md': ('Select evidence for the claim', 'source-locator'),
            'evaluator-artifacts.md': ('verifier-result.json', 'accepted'),
        }.items():
            with self.subTest(reference=filename):
                self.assertIn('references/' + filename, body)
                reference = (SKILL / 'references' / filename).read_text()
                for marker in markers:
                    self.assertIn(marker, reference)

    def assert_publication_boundary(self, body):
        for phrase in ('An existing run, cumulative history, missing route, planning reply or explanation does not create a publication obligation',
                       "Consume Core's `publication_route`", 'registered v2 pretool prepares',
                       'The assistant submits no separate publication command',
                       'It then applies the accepted criteria through the guarded Core writer',
                       '`status: pending-adapter`', '`status: applied`'):
            self.assertIn(phrase, body)

    def test_native_preparation_publication_and_application_have_distinct_owners(self):
        body = (SKILL / 'references/autopilot-publication.md').read_text()
        self.assert_publication_boundary(body)
        for phrase in ('does not create a publication obligation', 'guarded Core writer',
                       'The assistant submits no separate publication command'):
            with self.subTest(phrase=phrase), self.assertRaises(AssertionError):
                self.assert_publication_boundary(body.replace(phrase, 'removed'))

    def test_review_exposes_claim_sources_and_separate_response_judgment(self):
        body = (SKILL / 'references/autopilot-publication.md').read_text()
        for phrase in ('candidate_reference', 'sources[].source_id', 'bindings[]', 'claim_index',
                       'read-claims', 'caller retains the draft',
                       'Finish this claim comparison before reviewing the answer as a whole',
                       'exactly `claims` and `response`', 'It does not compare meaning or decide truth',
                       'matching bytes do not prove semantic support', 'supported | unsupported | unavailable'):
            self.assertIn(phrase, body)

    def test_original_proof_and_correction_require_owning_references(self):
        body = (SKILL / 'references/autopilot-publication.md').read_text()
        for phrase in ('--evidence-reference', '--reuse-evidence', '--correction-of',
                       '--correction-json', 'original verification time', 'Unaffected criteria retain their proof'):
            self.assertIn(phrase, body)
        self.assertIn('Do not hash a changed artifact later and call that its original snapshot', body)


if __name__ == '__main__':
    unittest.main()
