"""Specialized verification instructions have explicit, resolvable owners."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'coding-convention/verification-before-completion'


class VerificationDisclosureTests(unittest.TestCase):
    def test_specialized_commands_stay_in_triggered_references(self):
        body = (SKILL / 'SKILL.md').read_text()
        for payload in ('--verified-at', 'prepare-response', '- web-search-evidence:'):
            self.assertNotIn(payload, body)
        for filename, markers in {
            'response-review.md': ('--verified-at', 'prepare-response', 'record-response'),
            'external-tool-evidence.md': ('Select evidence for the claim', 'source-locator'),
            'evaluator-artifacts.md': ('verifier-result.json', 'accepted'),
        }.items():
            with self.subTest(reference=filename):
                self.assertIn('references/' + filename, body)
                reference = (SKILL / 'references' / filename).read_text()
                for marker in markers:
                    self.assertIn(marker, reference)

    def test_review_reference_describes_core_alone(self):
        self.assertFalse((SKILL / 'references/autopilot-publication.md').exists())
        body = (SKILL / 'references/response-review.md').read_text()
        for phrase in ('Autopilot', 'publication_route', '--receipt-token', '--reuse-evidence',
                       'pending-adapter', 'publish'):
            self.assertNotIn(phrase, body)
        for owner in ('SKILL.md', 'references/verify-or-reuse.md'):
            with self.subTest(owner=owner):
                self.assertNotIn('autopilot-publication', (SKILL / owner).read_text())

    def test_review_exposes_claim_sources_and_separate_response_judgment(self):
        body = (SKILL / 'references/response-review.md').read_text()
        for phrase in ('candidate_reference', 'sources[].source_id', 'bindings[]', 'claim_index',
                       'read-claims', 'caller retains the draft',
                       'Finish this claim comparison before reviewing the answer as a whole',
                       'exactly `claims` and `response`', 'It does not compare meaning or decide truth',
                       'matching bytes do not prove semantic support', 'supported | unsupported | unavailable'):
            self.assertIn(phrase, body)

    def test_delivery_is_an_observation_that_keeps_original_proof(self):
        body = (SKILL / 'references/response-review.md').read_text()
        for phrase in ('continue: true', 'response_assessment.body_issue', 'strict audit',
                       'does not hold or replace desktop output',
                       'A Stop observation does not establish that the user saw the answer',
                       'A later clock reading cannot supply a missing original time',
                       'Preserve immutable judgments, supported original proof and original observation times'):
            self.assertIn(phrase, body)


if __name__ == '__main__':
    unittest.main()
