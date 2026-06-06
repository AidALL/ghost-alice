"""Tests for compact-handoff report validation.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
VALIDATOR = SCRIPT_DIR / "compact_handoff.py"


class CompactHandoffValidatorTests(unittest.TestCase):
    def write_handoff(self, text: str) -> pathlib.Path:
        temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="compact-handoff-test-"))
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        handoff = temp_dir / "handoff.txt"
        handoff.write_text(text, encoding="utf-8")
        return handoff

    def run_validator(self, text: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        handoff = self.write_handoff(text)
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(handoff), "--json"],
            check=check,
            capture_output=True,
            text=True,
        )

    def test_valid_handoff_block_emits_pass_report(self) -> None:
        completed = self.run_validator(
            """
[compact-handoff]
- objective: finish compact-handoff validator
- completed: red tests written
- in-progress: validator implementation
- next-step: run focused tests
- files-changed: compact-handoff/scripts/test_compact_handoff.py
- tests-run: python3.14 -m unittest compact-handoff/scripts/test_compact_handoff.py -v = pass
- forbidden-surface: live ~/.claude settings
- rollback: git checkout -- compact-handoff
"""
        )

        report = json.loads(completed.stdout)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["field_count"], 8)

    def test_missing_required_fields_fails_with_field_names(self) -> None:
        completed = self.run_validator(
            """
[compact-handoff]
- objective: continue work
- completed: nothing yet
- next-step: inspect diff
""",
            check=False,
        )

        report = json.loads(completed.stdout)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(report["status"], "fail")
        self.assertIn("missing:in-progress", report["errors"])
        self.assertIn("missing:tests-run", report["errors"])
        self.assertIn("missing:rollback", report["errors"])

    def test_secret_like_values_fail_without_echoing_secret(self) -> None:
        token = "sk-test-abcdefghijklmnopqrstuvwxyz123456"
        completed = self.run_validator(
            f"""
[compact-handoff]
- objective: rotate leaked token {token}
- completed: investigation
- in-progress: cleanup
- next-step: redact context
- files-changed: compact-handoff/SKILL.md
- tests-run: not run
- forbidden-surface: secrets
- rollback: git checkout -- compact-handoff
""",
            check=False,
        )

        rendered = completed.stdout + completed.stderr
        report = json.loads(completed.stdout)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("secret-like-value", report["errors"])
        self.assertNotIn(token, rendered)

    def test_live_config_changes_require_concrete_rollback(self) -> None:
        completed = self.run_validator(
            """
[compact-handoff]
- objective: update hooks
- completed: settings changed
- in-progress: verifying
- next-step: run doctor
- files-changed: /Users/example/.claude/settings.json
- tests-run: zsh install.sh --platform claude --doctor = pending
- forbidden-surface: unrelated skills
- rollback: n/a
""",
            check=False,
        )

        report = json.loads(completed.stdout)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("rollback-required-for-live-config", report["errors"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
