"""Protect completion procedure discovery without unconditional expansion."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "coding-convention" / "verification-before-completion"
PROCEDURES = {
    "autopilot-publication.md": ("Autopilot Proof Publication", "receipt_token", "--verified-at"),
    "external-tool-evidence.md": ("External Tool Web-Search-First Gate", "web-search-first", "source-locator"),
    "evaluator-artifacts.md": ("Evaluator Artifact Contract", "verifier-result.json", "accepted"),
}


class VerificationDisclosureTests(unittest.TestCase):
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
            "When this Codex or Claude installation includes `autopilot-mode/scripts/autopilot_completion.py` and the current session has admitted criteria for authorized execution, read",
            "Category B runtime behavior and Category C version-dependent behavior require at least three WebSearch queries",
            "Require an accepted `verifier-result.json` with a rejected candidate; stop promotion when it is absent or rejected.",
        )
        for condition in prerequisites:
            with self.subTest(condition=condition):
                self.assertIn(condition, body)
        self.assertIn("sessions without admitted execution criteria do not activate this path", body)


if __name__ == "__main__":
    unittest.main()
