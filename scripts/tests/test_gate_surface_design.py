"""Consumer guidance refers to owning procedures without duplicating them."""
from pathlib import Path
import unittest
from _shared.global_rule_blocks import platform_contract_text

ROOT = Path(__file__).resolve().parents[2]


class GateSurfaceDesignTest(unittest.TestCase):
    def test_composed_contract_requires_actual_workflow_without_prefilled_audit(self):
        common = (ROOT / 'platforms/common-governance.md').read_text().strip()
        self.assertIn('installed common contract', (ROOT / 'AGENTS.md').read_text())
        for name in ('platforms/claude/CLAUDE.md', 'platforms/codex/AGENTS.md'):
            with self.subTest(path=name):
                text = platform_contract_text(ROOT / name)
                self.assertEqual(text.count(common), 1)
                self.assertNotIn('{{COMMON_GOVERNANCE}}', text)
                self.assertIn('Metadata alone is not execution' if 'codex' in name
                              else 'follows their bodies', text)
                self.assertNotIn('skill-call: session-intent-analyzer (this turn); task-router (this turn)', text)

    def test_router_follows_entry_sections_and_refers_to_gate_schema(self):
        body = (ROOT / 'task-router/SKILL.md').read_text()
        self.assertIn('references/routing-details.md', body)
        owner = (ROOT / 'task-router/references/routing-details.md').read_text()
        self.assertIn('current human instructions', owner)
        self.assertIn('bounded change without formal revocation wording', owner)
        self.assertIn("The common contract's Session Intent section interprets the input, and its Work Selection section sends work here", body)
        self.assertIn('Core validates the submitted response-route', body)
        self.assertNotIn('skill-call: session-intent-analyzer (this turn)', body)

    def test_finalization_allows_actual_returned_results_and_separate_reviews(self):
        body = (ROOT / 'coding-convention/verification-before-completion/SKILL.md').read_text()
        self.assertIn('A successful result already returned in this turn can supply fresh evidence', body)
        self.assertIn('run a further check only for a criterion it does not cover', body)
        self.assertIn('Compare each claim with its original sources and criterion, then separately judge the complete draft', body)
        self.assertIn('A verdict you endorse is your claim', body)
        self.assertIn('criterion and actual evidence', body)


if __name__ == '__main__':
    unittest.main()
