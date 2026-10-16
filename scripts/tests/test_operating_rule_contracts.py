"""Operating model responsibilities and the procedures that own their detail."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class OperatingRuleContractTest(unittest.TestCase):
    def test_terminal_routes_preserve_authority_and_avoid_governance_work(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        intent = common.split('## Session Intent\n', 1)[1].split('\n## ', 1)[0]
        selection = common.split('## Work Selection\n', 1)[1].split('\n## ', 1)[0]
        for term in ('does not cancel authorized work',
                     'When your interpretation changes the next action',
                     'ask the user before that action'):
            self.assertIn(term, intent)
        for term in ('`task-router` decides between a direct answer and work',
                     'A direct answer reads no skills, accesses no ledger, registers no route'):
            self.assertIn(term, selection)
        self.assertIn('Do not substitute another session', common)
        self.assertIn('stored result or available tool grants no authority', common)
        router = (ROOT / 'task-router/SKILL.md').read_text()
        self.assertIn('Work Selection section', router)
        self.assertIn('register the Core response-route using the session-start receipt', router)
        self.assertIn('Core binds the route to the latest recorded input', router)

    def test_reuse_instruction_contract_stays_at_its_owner(self):
        reference = (ROOT / 'coding-convention/verification-before-completion/references/verify-or-reuse.md').read_text()
        for phrase in ('## Instruction Body Reuse', 'same session', 'new user input alone',
                       'observed instruction change', 'retained body is lost',
                       'execute the applicable workflow for the current input'):
            self.assertIn(phrase, reference)

    def test_scope_depth_and_sufficient_change_are_distinct_decisions(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        for term in ('micro', 'meso', 'macro', 'meta', 'Verification depth is a separate choice'):
            self.assertIn(term, common)
        router = (ROOT / 'task-router/SKILL.md').read_text()
        self.assertIn('sufficient correction depth', router)
        self.assertIn('systematic-debugging', router)

    def test_focus_changes_are_shown_with_a_reason(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        operating = common.split('## Operating Model\n', 1)[1].split('\n## ', 1)[0]
        for phrase in ('the focus of the next judgment', 'When the focus differs from the last one shown',
                       '`[focus: micro -> meta]`', 'with a one-phrase reason'):
            self.assertIn(phrase, operating)
        selection = common.split('## Work Selection\n', 1)[1].split('\n## ', 1)[0]
        self.assertIn('other than a focus line', selection)

    def test_visibility_does_not_disable_audit_or_execution(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        self.assertIn('strict audit independently of displayed guidance', common)
        self.assertIn('Do not print full audit records', common)
        self.assertIn('A displayed trace does not establish proof', common)

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
