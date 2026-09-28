"""Visibility consumes the same bound intent snapshot as routing and gates.

Dependencies: Python 3.11+ standard library only.
"""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

import hook_profile_gate as runner


class VisibilityIntentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "selected-ledger"
        self.home = self.base / "home"
        self.sid = "bound-session"
        self.session = self.root / "codex" / self.sid
        self.session.mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.home), CODEX_THREAD_ID="", GHOST_ALICE_SESSION_ID="",
                        GHOST_ALICE_PLATFORM="codex", GHOST_ALICE_SESSION_INTENT_ROOT=str(self.root),
                        GHOST_ALICE_AGENT_VISIBILITY="minimal")

    def save(self, name, value, directory=None):
        directory = directory or self.session
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(json.dumps(value) + "\n")

    def seed(self, *, model_block=False, gate=False, event_id="new-input", directory=None):
        self.save("intent-state.json", {"schema_version": "session-intent-ledger.v1", "platform": "codex",
            "session_id": self.sid, "ledger_revision": 2, "latest_input_event_id": "new-input",
            "latest_input_digest": "sha256:new", "latest_input_char_count": 10,
            "model_security_decision": {"decision": "block", "input_event_id": "new-input"} if model_block else None}, directory)
        self.save("intent-events.jsonl", {"event": "user-input-observed", "platform": "codex", "session_id": self.sid,
            "event_id": event_id, "input_digest": "sha256:" + ("new" if event_id == "new-input" else "old")}, directory)
        if gate:
            self.save("downstream-gates.json", {"schema_version": "downstream-gates.v1", "platform": "codex",
                "session_id": self.sid, "gate": "jailbreak-detector", "decision": "block", "opened": False,
                "input_event_id": event_id}, directory)

    def context(self, payload=None):
        return runner._visibility_context("prompt", "routine clean pass already persisted", "", 0,
                                          env=self.env, hook_payload={"session_id": self.sid} if payload is None else payload)

    def native_visible_decision(self, payload):
        code = "print('routine clean pass already persisted')"
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
        encoded = base64.urlsafe_b64encode(command.encode()).decode()
        result = subprocess.run([sys.executable, "-B", str(Path(runner.__file__)), "run", "prompt", encoded],
                                env=self.env, input=json.dumps(payload), capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        logs = list((self.home / ".ghost-alice/session-logs").rglob("strict-hook-output.jsonl"))
        self.assertEqual(len(logs), 1)
        return json.loads(logs[0].read_text().splitlines()[-1])["visible_decision"]

    def test_native_unbound_call_cannot_borrow_foreign_pointer_block(self):
        self.seed(gate=True)
        (self.root / "codex/current-session.json").write_text(json.dumps({
            "schema_version": "session-intent-current.v1", "platform": "codex", "session_id": self.sid}))
        self.assertEqual(self.native_visible_decision({}), "hide")

    def test_native_current_model_block_surfaces_before_gate_projection(self):
        self.seed(model_block=True, event_id="old-input")
        self.assertEqual(self.native_visible_decision({"session_id": self.sid}), "force_show")

    def test_native_old_gate_and_audit_do_not_override_new_state_input(self):
        self.seed(gate=True, event_id="old-input")
        self.assertEqual(self.native_visible_decision({"session_id": self.sid}), "hide")

    def test_foreign_state_and_gate_are_not_visibility_evidence(self):
        for target in ("intent-state.json", "downstream-gates.json"):
            with self.subTest(target=target):
                self.seed(gate=True)
                content = json.loads((self.session / target).read_text())
                content["session_id"] = "foreign"
                self.save(target, content)
                self.assertNotIn("security_boundary", self.context())

    def test_invalid_identity_cannot_alias_a_visibility_session(self):
        self.seed(gate=True)
        self.assertNotIn("security_boundary", self.context({"session_id": "bound/session"}))

    def test_explicit_root_does_not_fall_back_to_other_stores(self):
        self.seed()
        other = self.home / ".ghost-alice/session-intent/codex" / self.sid
        self.seed(gate=True, directory=other)
        self.assertNotIn("security_boundary", self.context())

    def test_canonical_current_gate_still_forces_visibility(self):
        self.seed(gate=True)
        self.assertTrue(self.context()["security_boundary"])

    def test_failed_intake_marker_invalidates_prior_block_visibility(self):
        self.seed(model_block=True, gate=True)
        self.save("ledger-degraded.json", {"reason": "ledger-write-failed"})
        self.assertEqual(self.native_visible_decision({"session_id": self.sid}), "hide")


if __name__ == "__main__":
    unittest.main(verbosity=2)
