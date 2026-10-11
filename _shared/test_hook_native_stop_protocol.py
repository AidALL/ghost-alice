"""Exercise registered-command output at the native Stop envelope boundary."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(__file__).with_name("hook_profile_gate.py")
STOP_FIELDS = {"continue", "decision", "reason", "stopReason", "suppressOutput", "systemMessage"}
ACTIONABLE = "Recording is parked. Preserve the supported answer and continue eligible authorized work."


class NativeStopProtocolTests(unittest.TestCase):
    def run_command(self, body, *, platform="codex", event="Stop", profile="dynamic"):
        with tempfile.TemporaryDirectory(prefix="native-stop-envelope-") as temporary:
            home = Path(temporary)
            state = home / "ap-state-fixture.json"
            code = (
                "from pathlib import Path; import json; "
                f"Path({str(state)!r}).write_text(json.dumps({body['recovery']!r})); "
                f"print({json.dumps(body)!r})"
            )
            command = f"{sys.executable.replace(chr(92), '/')} -c {shlex.quote(code)}"
            wrapped = base64.urlsafe_b64encode(command.encode()).decode()
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("GHOST_ALICE_")}
            env.update(HOME=str(home), GHOST_ALICE_PLATFORM=platform,
                       GHOST_ALICE_AGENT_VISIBILITY=profile)
            result = subprocess.run(
                [sys.executable, "-B", str(RUNNER), "run", "adapter-autopilot-mode-continue", platform, wrapped],
                input=json.dumps({"session_id": "envelope-fixture", "hook_event_name": event}),
                text=True, capture_output=True, env=env, check=False, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            host = json.loads(result.stdout)
            log = home / ".ghost-alice/session-logs" / platform / "envelope-fixture/strict-hook-output.jsonl"
            record = json.loads(log.read_text().splitlines()[-1])
            self.assertEqual(json.loads(record["stdout"]), body, "Strict audit lost internal output")
            self.assertEqual(json.loads(state.read_text()), body["recovery"], "Projection changed stored AP state")
            self.assertEqual(json.loads(record["user_surface_output"]), host)
            self.assertIn("completion_recording", record["model_surface_output"])
            return host

    @staticmethod
    def body(**changes):
        return {"continue": True, "decision": "block", "reason": ACTIONABLE,
                "systemMessage": ACTIONABLE,
                "completion_recording": {"status": "diagnostic", "business_proof_preserved": True},
                "recovery": {"status": "parked", "same_failure": True}, **changes}

    def test_codex_stop_projects_supported_fields_and_retains_full_audit(self):
        body = self.body(stopReason="Owned stop reason", suppressOutput=False)
        for profile in ("strict", "dynamic", "minimal"):
            with self.subTest(profile=profile):
                host = self.run_command(body, profile=profile)
                self.assertLessEqual(set(host), STOP_FIELDS, "Native Stop rejects unknown fields")
                self.assertEqual(host, {key: value for key, value in body.items() if key in STOP_FIELDS})

    def test_codex_block_reason_uses_original_actionable_message_when_missing(self):
        body = self.body()
        body.pop("reason")
        host = self.run_command(body)
        self.assertEqual(host.get("reason"), ACTIONABLE, "Native blocking requires a nonempty reason")
        self.assertEqual(host["decision"], "block")
        self.assertLessEqual(set(host), STOP_FIELDS)

    def test_codex_empty_block_reason_uses_original_actionable_message(self):
        host = self.run_command(self.body(reason="  "))
        self.assertEqual(host.get("reason"), ACTIONABLE)

    def test_codex_subagent_stop_uses_the_same_supported_envelope(self):
        host = self.run_command(self.body(), event="SubagentStop")
        self.assertLessEqual(set(host), STOP_FIELDS)
        self.assertEqual(host["reason"], ACTIONABLE)

    def test_codex_parked_recording_notice_does_not_invent_a_block(self):
        body = self.body()
        body.pop("decision")
        body.pop("reason")
        host = self.run_command(body)
        self.assertLessEqual(set(host), STOP_FIELDS)
        self.assertIs(host["continue"], True)
        self.assertNotIn("decision", host)
        self.assertNotIn("reason", host)
        self.assertEqual(host["systemMessage"], ACTIONABLE)

    def test_codex_block_without_a_message_retains_a_nonempty_protocol_diagnostic(self):
        host = self.run_command(self.body(reason=None, systemMessage=""))
        self.assertEqual(host["decision"], "block")
        self.assertIsInstance(host["reason"], str)
        self.assertTrue(host["reason"].strip())

    def test_claude_stop_keeps_its_existing_envelope(self):
        body = self.body()
        self.assertEqual(self.run_command(body, platform="claude"), body)

    def test_pretool_retains_native_controls_and_audits_internal_recording(self):
        body = self.body(hookSpecificOutput={"hookEventName": "PreToolUse", "permissionDecision": "deny"})
        self.assertEqual(self.run_command(body, event="PreToolUse"),
                         {key: value for key, value in body.items() if key != "completion_recording"})


if __name__ == "__main__":
    unittest.main()
