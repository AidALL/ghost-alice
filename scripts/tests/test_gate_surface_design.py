"""Consumer guidance refers to owning procedures without duplicating them."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class GateSurfaceDesignTest(unittest.TestCase):
    def test_common_skill_call_template_records_actual_work(self):
        for name in ('AGENTS.md', 'platforms/claude/CLAUDE.md', 'platforms/codex/AGENTS.md'):
            with self.subTest(path=name):
                text = (ROOT / name).read_text()
                self.assertIn('skill-call: <skills actually executed this turn>', text)
                self.assertIn('does not mark a skill done after reading only its name or description', text)
                self.assertNotIn('skill-call: session-intent-analyzer (this turn); task-router (this turn)', text)

    def test_router_keeps_scope_reconciliation_and_refers_to_gate_schema(self):
        body = (ROOT / 'task-router/SKILL.md').read_text()
        self.assertIn('references/routing-details.md', body)
        owner = (ROOT / 'task-router/references/routing-details.md').read_text()
        self.assertIn('current human instructions', owner)
        self.assertIn('bounded change without formal revocation wording', owner)
        self.assertIn("common contract's `[gate-state]` schema", body)
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
