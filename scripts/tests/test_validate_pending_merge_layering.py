"""Regression tests for pending-merge/session-start layered gate validation."""
from __future__ import annotations
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATOR_ARTIFACTS = [
    "scenario.json",
    "trace.json",
    "report.json",
    "candidate-playbook.md",
    "verifier-result.json",
]


VALIDATOR = "scripts/validate_pending_merge_layering.py"
LEGACY_OPAQUE_GATE_LABEL = "D" + "12"


class TestPendingMergeLayeredGate(unittest.TestCase):
    def test_validator_output_is_safe_when_stdout_uses_cp949(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp949"

        r = subprocess.run(
            [sys.executable, VALIDATOR],
            capture_output=True,
            text=True,
            encoding="cp949",
            errors="replace",
            cwd=str(REPO_ROOT),
            env=env,
        )

        self.assertNotEqual(r.stdout, "")
        self.assertNotIn("UnicodeEncodeError", r.stderr)

    def test_runs_clean_on_current_repo(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        r = subprocess.run(
            [sys.executable, VALIDATOR],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO_ROOT),
            env=env,
        )
        self.assertEqual(
            r.returncode, 0,
            msg=f"pending-merge layered gate validation failed\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}",
        )
        self.assertIn("pending-merge/session-start layered gate", r.stdout)
        self.assertNotIn(LEGACY_OPAQUE_GATE_LABEL, r.stdout)

    def test_evaluator_artifact_contract_names_required_shapes(self):
        contract = REPO_ROOT / "docs" / "policies" / "evaluator-artifact-contract.md"

        self.assertTrue(contract.is_file())
        text = contract.read_text(encoding="utf-8")
        for artifact in EVALUATOR_ARTIFACTS:
            self.assertIn(artifact, text)
        self.assertIn("read-only evaluator pass", text)
        self.assertIn("accepted verifier-result", text)
        self.assertIn("rejected candidate", text)

    def test_verification_skills_reference_evaluator_artifact_contract(self):
        files = [
            REPO_ROOT / "adversarial-verification" / "SKILL.md",
            REPO_ROOT / "coding-convention" / "verification-before-completion" / "SKILL.md",
        ]

        for path in files:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertIn("evaluator-artifact-contract.md", text)
                self.assertIn("verifier-result.json", text)


if __name__ == "__main__":
    unittest.main()
