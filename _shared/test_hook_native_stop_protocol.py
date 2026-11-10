"""Exercise registered-command output at the native envelope boundary: the runner never blocks the host."""
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
HOST_FIELDS = {"continue", "suppressOutput", "systemMessage", "hookSpecificOutput", "additionalContext"}
HOST_CONTROLS = {"decision", "reason", "stopReason"}
ACTIONABLE = "Recording is parked. Preserve the supported answer and continue eligible work that the human user's instructions authorize."
NOT_BLOCKING = "the hook runner removed that request before the host saw it"


class NativeEnvelopeTests(unittest.TestCase):
    def run_command(self, body, *, platform="codex", event="Stop", profile="dynamic", exit_code=0):
        with tempfile.TemporaryDirectory(prefix="native-envelope-") as temporary:
            home = Path(temporary)
            state = home / "state-fixture.json"
            code = (
                "from pathlib import Path; import json, sys; "
                f"Path({str(state)!r}).write_text(json.dumps({body['recovery']!r})); "
                f"print({json.dumps(body)!r}); sys.exit({exit_code})"
            )
            command = f"{sys.executable.replace(chr(92), '/')} -c {shlex.quote(code)}"
            wrapped = base64.urlsafe_b64encode(command.encode()).decode()
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("GHOST_ALICE_")}
            env.update(HOME=str(home), GHOST_ALICE_PLATFORM=platform,
                       GHOST_ALICE_AGENT_VISIBILITY=profile)
            result = subprocess.run(
                [sys.executable, "-B", str(RUNNER), "run", "envelope-fixture", platform, wrapped],
                input=json.dumps({"session_id": "envelope-fixture", "hook_event_name": event}),
                text=True, capture_output=True, env=env, check=False, timeout=30,
            )
            self.assertEqual(result.returncode, 1 if exit_code == 2 else exit_code, result.stderr)
            host = json.loads(result.stdout)
            log = home / ".ghost-alice/session-logs" / platform / "envelope-fixture/strict-hook-output.jsonl"
            record = json.loads(log.read_text().splitlines()[-1])
            self.assertEqual(json.loads(record["stdout"]), body, "Strict audit lost internal output")
            self.assertEqual(record["exit_code"], exit_code)
            self.assertEqual(json.loads(state.read_text()), body["recovery"], "Projection changed stored state")
            self.assertEqual(json.loads(record["user_surface_output"]), host)
            self.assertIn("completion_recording", record["model_surface_output"])
            self.assertLessEqual(set(host), HOST_FIELDS, "The host receives only its native envelope")
            self.assertFalse(set(host) & HOST_CONTROLS, "The host never receives a blocking control")
            return host

    @staticmethod
    def body(**changes):
        return {"continue": True, "decision": "block", "reason": ACTIONABLE,
                "systemMessage": ACTIONABLE,
                "completion_recording": {"status": "diagnostic", "business_proof_preserved": True},
                "recovery": {"status": "parked", "same_failure": True}, **changes}

    def test_stop_block_becomes_continue_with_the_reason_as_a_warning(self):
        for platform in ("codex", "claude"):
            for event in ("Stop", "SubagentStop"):
                for profile in ("strict", "dynamic", "minimal"):
                    with self.subTest(platform=platform, event=event, profile=profile):
                        host = self.run_command(self.body(), platform=platform, event=event, profile=profile)
                        self.assertIs(host["continue"], True)
                        self.assertIn(ACTIONABLE, host["systemMessage"])
                        self.assertIn(NOT_BLOCKING, host["systemMessage"])

    def test_block_without_a_message_still_tells_the_user(self):
        host = self.run_command(self.body(reason=None, systemMessage=""))
        self.assertIs(host["continue"], True)
        self.assertIn("block", host["systemMessage"])
        self.assertIn(NOT_BLOCKING, host["systemMessage"])

    def test_continue_false_becomes_true_and_keeps_the_stop_reason_as_a_warning(self):
        body = self.body(**{"continue": False, "stopReason": "Owned stop reason"})
        body.pop("decision")
        body.pop("reason")
        host = self.run_command(body)
        self.assertIs(host["continue"], True)
        self.assertNotIn("stopReason", host)
        self.assertIn("Owned stop reason", host["systemMessage"])
        self.assertIn(NOT_BLOCKING, host["systemMessage"])

    def test_pretool_permission_denial_is_removed_and_reported(self):
        body = self.body(hookSpecificOutput={"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "The current gate is closed.",
                                             "additionalContext": "kept"})
        body.pop("decision")
        body.pop("reason")
        host = self.run_command(body, platform="claude", event="PreToolUse")
        self.assertEqual(host["hookSpecificOutput"], {"hookEventName": "PreToolUse", "additionalContext": "kept"})
        self.assertIn("The current gate is closed.", host["systemMessage"])
        self.assertIn(NOT_BLOCKING, host["systemMessage"])

    def test_user_prompt_block_is_removed_on_both_hosts(self):
        for platform in ("codex", "claude"):
            with self.subTest(platform=platform):
                host = self.run_command(self.body(), platform=platform, event="UserPromptSubmit")
                self.assertIs(host["continue"], True)
                self.assertIn(ACTIONABLE, host["systemMessage"])

    def test_pretool_controls_and_recording_metadata_stay_in_raw_audit_not_native_envelope(self):
        for platform in ('codex', 'claude'):
            for denied in (False, True):
                with self.subTest(platform=platform, denied=denied):
                    body = self.body(**{'continue': not denied, 'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse', 'permissionDecision': 'deny' if denied else 'allow',
                        'permissionDecisionReason': 'Current gate is blocked.' if denied else 'Current gate is open.',
                        'additionalContext': 'Preserve the original supported evidence.',
                    }, 'completion_recording': {'status': 'recording-unavailable' if denied else 'prepared',
                        'receipt_token': 'audit-only-original-receipt'}})
                    for field in ('decision', 'reason', 'systemMessage'):
                        body.pop(field)
                    if denied:
                        body.update(systemMessage='Recording failed; the current gate remains blocked.',
                                    stopReason='Current gate is blocked.')
                    host = self.run_command(body, platform=platform, event='PreToolUse',
                                            profile='minimal', exit_code=2 if denied else 0)
                    self.assertIs(host['continue'], True)
                    self.assertEqual(host['hookSpecificOutput'], {
                        'hookEventName': 'PreToolUse',
                        'additionalContext': 'Preserve the original supported evidence.'})
                    if denied:
                        self.assertTrue(host['systemMessage'].startswith(body['systemMessage']))
                        self.assertIn('stop the turn (Current gate is blocked.)', host['systemMessage'])
                        self.assertIn('deny the tool call (Current gate is blocked.)', host['systemMessage'])
                        self.assertIn(NOT_BLOCKING, host['systemMessage'])
                    else:
                        self.assertNotIn('systemMessage', host)

    def test_parked_recording_notice_without_a_block_passes_unchanged(self):
        body = self.body()
        body.pop("decision")
        body.pop("reason")
        host = self.run_command(body)
        self.assertEqual(host, {"continue": True, "systemMessage": ACTIONABLE})

    def test_internal_records_stay_in_the_strict_log_on_every_event(self):
        body = self.body(suppressOutput=False)
        body.pop("decision")
        body.pop("reason")
        for event in ("Stop", "PreToolUse", "PostToolUse", "SessionStart"):
            with self.subTest(event=event):
                host = self.run_command(body, event=event)
                self.assertEqual(host, {"continue": True, "suppressOutput": False, "systemMessage": ACTIONABLE})


if __name__ == "__main__":
    unittest.main()
