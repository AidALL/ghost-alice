"""Tests for governance event capture.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parent
SCRIPT = ROOT / "governance_events.py"
IO_TRACE_HOOK = ROOT / "io_trace_hook.py"


class GovernanceEventsTests(unittest.TestCase):
    def run_script(self, payload: dict[str, object]) -> list[dict[str, object]]:
        temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="governance-events-test-"))
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        output = temp_dir / "governance-events.jsonl"
        env = os.environ.copy()
        env["GHOST_ALICE_GOVERNANCE_EVENTS"] = str(output)
        subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=json.dumps(payload),
            text=True,
            check=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not output.exists():
            return []
        return [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    def test_secret_like_command_is_fingerprinted_without_raw_value(self) -> None:
        raw_secret = "sk-test-abcdefghijklmnopqrstuvwxyz123456"
        events = self.run_script(
            {
                "tool_name": "Bash",
                "tool_input": {"command": f"python deploy.py --api-key {raw_secret}"},
            }
        )
        rendered = json.dumps(events, ensure_ascii=False)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rule"], "secret-pattern")
        self.assertEqual(events[0]["command_name"], "python")
        self.assertRegex(events[0]["fingerprint"], r"^[0-9a-f]{12}$")
        self.assertNotIn(raw_secret, rendered)
        self.assertNotIn("deploy.py --api-key", rendered)

    def test_destructive_and_sensitive_path_patterns_are_captured(self) -> None:
        cases = [
            ("git push origin main --force", "destructive-command"),
            ("git reset --hard HEAD~1", "destructive-command"),
            ("openssl rsa -in prod.pem", "sensitive-path"),
        ]
        for command, expected_rule in cases:
            with self.subTest(command=command):
                events = self.run_script({"tool_name": "Bash", "tool_input": {"command": command}})
                self.assertEqual(events[0]["rule"], expected_rule)
                self.assertNotIn(command, json.dumps(events, ensure_ascii=False))

    def test_io_trace_hook_invokes_governance_capture_best_effort(self) -> None:
        text = IO_TRACE_HOOK.read_text(encoding="utf-8")

        self.assertIn("governance_events.py", text)
        self.assertIn("check=False", text)


class GovernanceRetentionTests(unittest.TestCase):
    """A write that takes the event log past 5MB turns it into the one previous file."""

    def test_a_write_past_the_limit_rotates_the_log(self) -> None:
        sys.path.insert(0, str(ROOT))
        import governance_events
        import state_retention

        temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="governance-retention-test-"))
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        output = temp_dir / "governance-events.jsonl"
        output.write_text("x" * 80 + "\n", encoding="utf-8")
        policy = state_retention.POLICIES["governance-events"]._replace(limit=64)
        with mock.patch.dict(state_retention.POLICIES, {"governance-events": policy}), \
                mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(temp_dir)}):
            governance_events.append_event({"rule": "destructive-command"}, output)
        previous = output.with_name("governance-events.jsonl.1").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(previous[-1]), {"rule": "destructive-command"})
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
