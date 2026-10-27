"""Operating model responsibilities and the procedures that own their detail."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_shared'))
from global_rule_blocks import platform_contract_text


class OperatingRuleContractTest(unittest.TestCase):
    def test_terminal_routes_preserve_authority_and_avoid_governance_work(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        intent = common.split('## Session Intent\n', 1)[1].split('\n## ', 1)[0]
        selection = common.split('## Work Selection\n', 1)[1].split('\n## ', 1)[0]
        for term in ('does not cancel authorized work',
                     "When the user's words leave more than one reasonable reading",
                     'ask the user before that action'):
            self.assertIn(term, intent)
        for term in ('`task-router` owns this rule and this section carries it, so applying it loads no skill',
                     'A direct answer reads no skills, accesses no ledger, registers no route'):
            self.assertIn(term, selection)
        self.assertIn('Do not substitute another session', common)
        self.assertIn('Missing or pending identity blocks dependent writes', common)
        self.assertIn('stored result or available tool grants no authority', common)
        self.assertIn('The native host executes tools and enforces permission', common)
        self.assertIn('`session-intent-analyzer`: reading or writing the intent record', common)
        router = (ROOT / 'task-router/SKILL.md').read_text()
        self.assertIn('Work Selection section', router)
        self.assertIn('register the Core response-route at that point, using the session-start receipt', router)
        self.assertIn('the input ID that `--read-intent` returned when this input', router)

    def test_intake_and_work_selection_name_the_light_path_and_the_delegate(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        intent = common.split('## Session Intent\n', 1)[1].split('\n## ', 1)[0]
        selection = common.split('## Work Selection\n', 1)[1].split('\n## ', 1)[0]
        for term in ('`user-said`, `interpreted-as`, `previous-tool` or `system-doc`',
                     'a light-path lookup records none',
                     'Calls that load this contract, `session-intent-analyzer` or its references'):
            self.assertIn(term, intent)
        for term in ('takes the light path',
                     'leaves the light path: it records `interpreted-as` before its next tool call',
                     'Any other request that needs inspection, execution, modification or fresh verification is work',
                     "When the user's own direct input arrives in that session after the handoff"):
            self.assertIn(term, selection)
        intake = (ROOT / 'session-intent-analyzer/SKILL.md').read_text()
        self.assertIn('The ledger stores a condition labeled `interpreted-as` as unadmitted', intake)
        schema = (ROOT / 'session-intent-analyzer/references/ledger-schema.md').read_text()
        self.assertIn('replaces that field as a whole, so an empty list or object clears it', schema)
        self.assertNotIn('retain active authority until explicitly superseded', schema)

    def test_checks_and_checkpoints_use_conditions_known_before_acting(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        calls = common.split('## Tool Calls\n', 1)[1].split('\n## ', 1)[0]
        self.assertIn('a contrary observation, an explicit recheck or a materially broader claim reopens only the affected check', calls)
        self.assertIn('- Do not repeat an unchanged attempt.\n', calls)
        self.assertNotIn('run a check whose outcomes cannot change the next decision', calls)
        trace = common.split('## io-trace Transparency Rule\n', 1)[1].split('\n## ', 1)[0]
        for phrase in ('Before a tool call that changes an authority boundary, a tool call made to obtain information for an open work decision',
                       'an active boundary contract adds `contract-ref` and `contract-check`',
                       'Repeated healthy tool calls need no checkpoint'):
            self.assertIn(phrase, trace)
        self.assertNotIn('new input receipts', trace)
        boundary = (ROOT / 'boundary-contract/SKILL.md').read_text()
        for phrase in ('the coverage criterion states the condition that defines the set',
                       'maps every tool call to an allowed-surface item or a next-allowed-action before making it'):
            self.assertIn(phrase, boundary)
        router = (ROOT / 'task-router/SKILL.md').read_text()
        for phrase in ('When the current session receipt carries `context_selection`, select the information',
                       'verification-before-completion before a new completion, fix, success, verified-status or material recommendation claim and before rereading an artifact to support a claim'):
            self.assertIn(phrase, router)
        verification = (ROOT / 'coding-convention/verification-before-completion/SKILL.md').read_text()
        self.assertIn('Before claiming a result that adversarial-verification reviewed at level-3-high-stakes', verification)
        self.assertNotIn('verification-complexity-level-3', verification)

    def test_rendered_contracts_carry_no_retired_instructions(self):
        for name in ('platforms/codex/AGENTS.md', 'platforms/claude/CLAUDE.md'):
            body = platform_contract_text(ROOT / name)
            for retired in ('jailbreak-detector',
                            'End every normal response with `[io-trace]`',
                            'Output an `[io-trace]` block at the end of every normal response'):
                with self.subTest(path=name, retired=retired):
                    self.assertNotIn(retired, body)

    def test_reuse_instruction_contract_stays_at_its_owner(self):
        reference = (ROOT / 'coding-convention/verification-before-completion/references/verify-or-reuse.md').read_text()
        for phrase in ('## Instruction Body Reuse', 'same session', 'new user input alone',
                       'observed instruction change', 'retained body is lost',
                       'execute the applicable workflow for the current input'):
            self.assertIn(phrase, reference)

    def test_focus_further_checks_and_sufficient_correction_are_distinct_decisions(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        for term in ('micro', 'meso', 'macro', 'meta', 'runs a further check only when'):
            self.assertIn(term, common)
        router = (ROOT / 'task-router/SKILL.md').read_text()
        self.assertIn('sufficient correction depth', router)
        self.assertIn('systematic-debugging', router)

    def test_focus_changes_are_shown_with_a_reason(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        operating = common.split('## Operating Model\n', 1)[1].split('\n## ', 1)[0]
        for phrase in ('the object of the current judgment', 'When the focus differs from the last one shown',
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
