"""Protect completion procedure discovery without unconditional expansion."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "coding-convention" / "verification-before-completion"
PROCEDURES = {
    "autopilot-publication.md": ("Autopilot Proof Publication", "--receipt-token", "--verified-at"),
    "external-tool-evidence.md": ("External Tool Web-Search-First Gate", "web-search-first", "source-locator"),
    "evaluator-artifacts.md": ("Evaluator Artifact Contract", "verifier-result.json", "accepted"),
}


class VerificationDisclosureTests(unittest.TestCase):
    def assert_current_ap_activation(self, body):
        section = re.search(r"^## Autopilot Proof Publication\n(.*?)(?=^## |\Z)", body, re.M | re.S)
        self.assertIsNotNone(section)
        for condition in (
            "installation includes the compatible managed AP helper and Core selects current-ready-owned execution units",
            "Core owns applicability and unit selection; AP owns recording availability",
            "criteria qualified by work-unit ID and contract revision",
            "Plan-only replies, explanations and units without current admitted execution criteria do not activate publication",
            "Retained proof keeps its original source and verification time",
            "existing business authority and native tool permission intact",
        ):
            self.assertIn(condition, section[1])

    def assert_current_ap_preparation(self, reference):
        managed = reference.split("## Managed v2 workflow", 1)[1].split("## Original proof format", 1)[0]
        for condition in (
            "registered v2 pretool automatically calls preparation",
            "do not repeat its returned `prepare_command` merely because a verification is about to run",
            "Use the explicit returned helper only when the applicable preparation dependency remains genuinely unfulfilled",
            "actual returned `publish_command`", "prepared AP receipt", "Publication returns pending application",
            "Core writer in the shared owning transaction",
        ):
            self.assertIn(condition, managed)

    def test_ap_contract_rejects_backlog_activation_and_manual_prepare_obligation(self):
        body = (SKILL / "SKILL.md").read_text()
        for old, new in (("Core selects current-ready-owned execution units", "any accumulated backlog exists"),
                         ("do not activate publication", "always activate publication"),
                         ("Retained proof keeps its original source and verification time", "Refresh proof time on every input")):
            with self.subTest(change=old), self.assertRaises(AssertionError):
                self.assert_current_ap_activation(body.replace(old, new))
        reference = (SKILL / "references/autopilot-publication.md").read_text()
        self.assert_current_ap_preparation(reference)
        for old, new in (("registered v2 pretool automatically calls preparation", "agent must manually prepare every tool"),
                         ("Publication returns pending application", "Publication marks all criteria complete"),
                         ("Core writer in the shared owning transaction", "unprivileged publication writer")):
            with self.subTest(change=old), self.assertRaises(AssertionError):
                self.assert_current_ap_preparation(reference.replace(old, new))

    def test_specialized_command_payloads_are_not_unconditional_context(self):
        body = (SKILL / "SKILL.md").read_text()
        for payload in ("--receipt-token", "--reapprove-current-input", "- web-search-evidence:"):
            self.assertFalse(payload in body, f"Conditional procedure expanded by default: {payload}")

    def test_each_specialized_procedure_has_a_trigger_and_resolvable_reference(self):
        body = (SKILL / "SKILL.md").read_text()
        for filename, (title, *markers) in PROCEDURES.items():
            with self.subTest(procedure=title):
                section = re.search(r"^## " + re.escape(title) + r"\n(.*?)(?=^## |\Z)", body, re.M | re.S)
                self.assertIsNotNone(section)
                self.assertIn("read", section[1].lower())
                self.assertTrue("references/" + filename in section[1], f"Missing procedure reference: {filename}")
                reference = SKILL / "references" / filename
                self.assertTrue(reference.is_file())
                for marker in markers:
                    self.assertIn(marker, reference.read_text())

    def test_common_decision_rules_remain_available_without_specialized_files(self):
        body = (SKILL / "SKILL.md").read_text()
        for section in ("Acceptance Criteria Iron Law", "Verify-Or-Reuse Gate",
                        "Hard Finalization Order", "Gate Function",
                        "Evidence Selection And Stop Gate", "Completion-Check Format"):
            self.assertIn("## " + section, body)
        self.assertIn("While actionable authorized work remains", body)

    def test_activation_prerequisites_cannot_be_removed_or_inverted(self):
        body = (SKILL / "SKILL.md").read_text()
        prerequisites = (
            "Category B runtime behavior and Category C version-dependent behavior require at least three WebSearch queries",
            "Require an accepted `verifier-result.json` with a rejected candidate; stop promotion when it is absent or rejected.",
        )
        for condition in prerequisites:
            with self.subTest(condition=condition):
                self.assertIn(condition, body)
        self.assert_current_ap_activation(body)


if __name__ == "__main__":
    unittest.main()
