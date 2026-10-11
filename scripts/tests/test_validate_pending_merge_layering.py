"""Regression tests for pending-merge/session-start layered gate validation."""
from __future__ import annotations
import os
from dataclasses import replace
import tempfile
from unittest.mock import patch

from scripts import validate_pending_merge_layering as validator
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

    def test_verification_skills_reach_the_evaluator_contract_owner(self):
        checks = [check for check in validator.CHECKS if check.layer == "EVAL" and check.owner]
        self.assertEqual(len(checks), 2)
        for check in checks:
            with self.subTest(consumer=check.file):
                ok, message = validator.check_layer(check)
                self.assertTrue(ok, message)

    def test_owned_contracts_reject_broken_reference_or_missing_requirements(self):
        for check in validator.CHECKS:
            if check.owner is None:
                continue
            consumer = check.file.read_text(encoding="utf-8")
            owner = check.owner.read_text(encoding="utf-8")
            for defect in ("reference", "owner", "heading", "requirements"):
                with self.subTest(layer=check.layer, consumer=check.file, defect=defect):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        caller = root / "consumer.md"
                        target = root / "owner.md"
                        caller.write_text("" if defect == "reference" else consumer, encoding="utf-8")
                        if defect != "owner":
                            target.write_text(
                                "# Unrelated owner\n" if defect == "heading" else
                                "# " + check.owner_section + "\n\n# Unrelated\n" + owner
                                if defect == "requirements" else owner, encoding="utf-8")
                        with patch.object(validator, "REPO", root):
                            ok, message = validator.check_layer(replace(check, file=caller, owner=target))
                        self.assertFalse(ok, message)



if __name__ == "__main__":
    unittest.main()
