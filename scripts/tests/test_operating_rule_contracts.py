"""Operating model responsibilities and the procedures that own their detail."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class OperatingRuleContractTest(unittest.TestCase):
    def test_terminal_routes_preserve_authority_and_avoid_governance_work(self):
        common = (ROOT / 'AGENTS.md').read_text()
        first = common.split('## First-Entry Contract\n', 1)[1].split('\n## ', 1)[0]
        for term in ('direct-response', 'clarification-only', 'does not read skills',
                     'does not cancel', 'ambient working directory'):
            if term == 'does not cancel':
                self.assertIn('ongoing authorized work', first)
            else:
                self.assertIn(term, first)
        self.assertIn('do not authorize resumed work', first)
        router = (ROOT / 'task-router/SKILL.md').read_text()
        self.assertIn('First-Entry Contract', router)
        self.assertIn('Register the Core projection', router)

    def test_reuse_instruction_contract_stays_at_its_owner(self):
        reference = (ROOT / 'coding-convention/verification-before-completion/references/verify-or-reuse.md').read_text()
        for phrase in ('## Instruction Body Reuse', 'same session', 'new user input alone',
                       'observed instruction change', 'retained body is lost',
                       'execute the applicable workflow for the current input'):
            self.assertIn(phrase, reference)

    def test_scope_depth_and_sufficient_change_are_distinct_decisions(self):
        common = (ROOT / 'AGENTS.md').read_text()
        for term in ('micro', 'meso', 'macro', 'meta', 'level 1', 'minimal | localized | structural | systemic'):
            self.assertIn(term.lower(), common.lower())
        self.assertIn('selects verification depth separately from focus', common)
        self.assertIn('structural cause needs a structural repair', common)
        self.assertIn('not by authorship, recency or diff size', common)

    def test_visibility_does_not_disable_audit_or_execution(self):
        common = (ROOT / 'AGENTS.md').read_text()
        self.assertIn('controls display verbosity, not hook execution or strict logging', common)
        self.assertIn('The PostToolUse hook records strict audit data', common)
        self.assertIn('Display verbosity never disables that log', common)

    def test_external_and_evaluator_details_are_reachable_from_verification(self):
        skill = ROOT / 'coding-convention/verification-before-completion/SKILL.md'
        body = skill.read_text()
        for name in ('external-tool-evidence.md', 'evaluator-artifacts.md'):
            self.assertIn('references/' + name, body)
            reference = skill.parent / 'references' / name
            self.assertTrue(reference.is_file())
        external = (skill.parent / 'references/external-tool-evidence.md').read_text()
        for field in ('source-locator', 'accessible_url', 'file_path', 'region'):
            self.assertIn(field, external)

    def test_adversarial_protocol_has_evidence_driven_convergence(self):
        skill = (ROOT / 'adversarial-verification/SKILL.md').read_text()
        protocol = (ROOT / 'adversarial-verification/references/round-protocol.md').read_text()
        convergence = (ROOT / 'adversarial-verification/references/convergence-rules.md').read_text()
        self.assertIn('references/round-protocol.md', skill)
        self.assertIn('references/convergence-rules.md', skill)
        self.assertIn('first cycle includes every required distinct axis', protocol)
        self.assertIn('no decision-relevant uncertainty or attainable relevant delta remains', convergence)
        self.assertIn('default is unanimity', convergence)
        self.assertIn('50-round safety cap cannot be increased', convergence)
        self.assertIn('preserves the unmet convergence requirement', convergence)

    def test_development_entrypoint_defers_known_behavior_to_tdd(self):
        entry = (ROOT / 'coding-convention/using-coding-convention/SKILL.md').read_text()
        tdd = (ROOT / 'coding-convention/test-driven-development/SKILL.md').read_text()
        self.assertIn('test-driven-development', entry)
        self.assertIn('behavior contract', tdd)
        self.assertIn('observe this intended failure before writing production code', tdd)
        self.assertIn('Run the same relevant target and inspect its result', tdd)
        self.assertIn('affected additional checks', tdd)
        self.assertIn('A passing implementation must not depend on weakening a valid requirement', tdd)


if __name__ == '__main__':
    unittest.main()
