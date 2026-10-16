"""Completion procedure discovery and the owning evidence/response contract."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'coding-convention/verification-before-completion/SKILL.md'
OWNER = SKILL.parent / 'references/autopilot-publication.md'
sys.path.insert(0, str(ROOT / '_shared'))
from global_rule_blocks import platform_contract_text


def section(text, title):
    return text.split('## ' + title + '\n', 1)[1].split('\n## ', 1)[0]


class CompletionLifecycleContractTests(unittest.TestCase):
    def assert_review_contract(self, text):
        finalization = section(text, 'Finalization sequence')
        claim = finalization.index('Compare each claim with its original sources and criterion')
        whole = finalization.index('separately judge the complete draft')
        record = finalization.index('submit both judgments to Core')
        self.assertLess(claim, whole)
        self.assertLess(whole, record)
        self.assertIn('code does not decide their meaning', finalization)
        self.assertIn('references/autopilot-publication.md#prepare-and-record-the-response-review', finalization)

    def test_claim_review_precedes_whole_answer_review_and_recording(self):
        self.assert_review_contract(SKILL.read_text())

    def test_missing_judgment_or_false_code_semantics_rejects_contract(self):
        body = SKILL.read_text()
        for phrase in ('Compare each claim with its original sources and criterion',
                       'separately judge the complete draft', 'code does not decide their meaning'):
            with self.subTest(phrase=phrase), self.assertRaises((AssertionError, ValueError)):
                self.assert_review_contract(body.replace(phrase, 'removed'))

    def test_reuse_and_discretionary_checks_have_one_owner(self):
        body = SKILL.read_text()
        reuse = section(body, 'Verify-or-reuse gate')
        self.assertIn('references/verify-or-reuse.md', reuse)
        self.assertIn('Another user input', reuse)
        self.assertIn('is not an artifact mutation', reuse)
        selection = section(body, 'Evidence selection and stop gate')
        for phrase in ('unresolved criterion or competing explanation',
                       'why the method distinguishes the alternatives',
                       'which attainable outcomes would change a specific next action',
                       'Mandatory project and workflow checks remain required',
                       'Preserve unchanged valid proof with its original source and time'):
            self.assertIn(phrase, selection)

    def test_entrypoint_and_architecture_link_the_verification_owner(self):
        entry = (ROOT / 'coding-convention/using-coding-convention/SKILL.md').read_text()
        self.assertIn('verification-before-completion', entry)
        self.assertNotIn('--receipt-token', entry)
        architecture = (ROOT / 'architecture.md').read_text()
        self.assertIn('coding-convention/verification-before-completion/references/autopilot-publication.md#prepare-and-record-the-response-review', architecture)
        self.assertNotIn('--receipt-token', architecture)

    def test_common_contracts_keep_routing_independent_of_proof(self):
        common = (ROOT / 'platforms/common-governance.md').read_text()
        for phrase in ('Reuse unchanged evidence at its original time and source',
                       'A recording error does not refute the business result or authorize replay',
                       'Core authenticates records and source bindings',
                       '`verification-before-completion` owns evidence selection, the per-claim and whole-answer judgments'):
            self.assertIn(phrase, common)
        verification = (ROOT / 'coding-convention/verification-before-completion/SKILL.md').read_text()
        for phrase in ('then separately judge the complete draft',
                       'Core authenticates and stores them; code does not decide their meaning'):
            self.assertIn(phrase, verification)
        for name in ('platforms/claude/CLAUDE.md', 'platforms/codex/AGENTS.md'):
            with self.subTest(path=name):
                template = (ROOT / name).read_text()
                self.assertEqual(1, template.count('{{COMMON_GOVERNANCE}}'))
                self.assertNotIn(common.strip(), template)
                installed = platform_contract_text(ROOT / name)
                self.assertEqual(1, installed.count(common.strip()))
                self.assertNotIn('{{COMMON_GOVERNANCE}}', installed)
        project = (ROOT / 'AGENTS.md').read_text()
        self.assertIn('platforms/common-governance.md', project)
        self.assertIn("current host's installed common contract", project)
        self.assertNotIn(common.strip(), project)

    def test_proof_format_keeps_partial_and_failed_results_distinct(self):
        body = section(SKILL.read_text(), 'Completion-Check Format')
        for phrase in ('[completion-check]', '[partial-check]', 'scope: partial',
                       'failed verdict proves failure, not overall success',
                       'Preserve all required criteria', 'claim', 'criterion', 'evidence', 'verdict'):
            self.assertIn(phrase, body)
        self.assertIn('neither application nor continuation', body)
        self.assertIn('completion evidence coverage', body)
        self.assertIn('independent of the response-route\'s `focus_layer`', body)
        self.assertIn('A macro judgment can therefore have partial evidence coverage', body)


if __name__ == '__main__':
    unittest.main()
