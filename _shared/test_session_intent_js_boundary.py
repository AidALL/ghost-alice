"""Real Node hook identity and authoritative-input controls.

Dependencies: Python 3.11+ standard library; node on PATH.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


HOOK = Path(__file__).with_name("ghost-alice-hook.mjs")


class JavascriptLedgerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.platform = "codex"
        self.sid = "session-a"
        self.directory = self.root / self.platform / self.sid
        self.directory.mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.root / "home"), CODEX_THREAD_ID="", GHOST_ALICE_SESSION_ID="")

    def save(self, name, value):
        (self.directory / name).write_text(json.dumps(value) + "\n")

    def state(self, decision=None, *, legacy=False, **overrides):
        value = {"schema_version": "session-intent-ledger.v1", "platform": self.platform, "session_id": self.sid,
                 "model_security_decision": decision}
        if not legacy:
            value.update(ledger_revision=2, latest_input_event_id="new-input", latest_input_digest="sha256:new", latest_input_char_count=10)
        value.update(overrides)
        self.save("intent-state.json", value)

    def event(self, event_id="new-input", **overrides):
        value = {"event": "user-input-observed", "platform": self.platform, "session_id": self.sid,
                 "event_id": event_id, "input_digest": "sha256:new", "input_char_count": 10}
        value.update(overrides)
        self.save("intent-events.jsonl", value)

    def gate(self, **overrides):
        value = {"schema_version": "downstream-gates.v1", "platform": self.platform, "session_id": self.sid,
                 "gate": "jailbreak-detector", "decision": "block", "opened": False,
                 "input_event_id": "new-input", "input_digest": "sha256:new"}
        value.update(overrides)
        self.save("downstream-gates.json", value)

    def run_hook(self, *, sid="session-a", reminder=False):
        payload = {"tool_name": "Bash"}
        if sid is not None:
            payload["session_id"] = sid
        result = subprocess.run(["node", str(HOOK), "--platform", self.platform, "--event", "PreToolUse",
                                 "--hook", "hook-reminder" if reminder else "tool-checkpoint",
                                 "--session-intent-root", str(self.root)], input=json.dumps(payload),
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def denied(self, **kwargs):
        return self.run_hook(**kwargs).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_authoritative_anchor_carries_current_block_without_audit_file(self):
        self.state({"decision": "block", "input_event_id": "new-input"})
        self.assertTrue(self.denied())
        gate = json.loads((self.directory / "downstream-gates.json").read_text())
        self.assertEqual(gate["input_event_id"], "new-input")

    def test_pending_audit_cannot_hide_current_block(self):
        self.state({"decision": "block", "input_event_id": "new-input"}, pending_audit_event={"event_id": "new-input"})
        self.event("old-input", input_digest="sha256:old")
        self.assertTrue(self.denied())

    def test_old_audit_cannot_revive_old_decision_or_gate(self):
        self.state({"decision": "block", "input_event_id": "old-input"})
        self.event("old-input", input_digest="sha256:old")
        self.gate(input_event_id="old-input", input_digest="sha256:old")
        self.assertFalse(self.denied())

    def test_foreign_state_cannot_derive_or_enforce_block(self):
        for key, value in [("platform", "claude"), ("session_id", "foreign")]:
            with self.subTest(key=key):
                self.state({"decision": "block", "input_event_id": "new-input"}, **{key: value})
                self.event()
                self.gate()
                self.assertFalse(self.denied())

    def test_foreign_or_headerless_legacy_event_is_not_current_input(self):
        for overrides in ({"platform": "claude"}, {"session_id": "foreign"}, {"platform": None}, {"session_id": None}):
            with self.subTest(overrides=overrides):
                self.state({"decision": "block", "input_event_id": "new-input"}, legacy=True)
                self.event(**overrides)
                self.gate()
                self.assertFalse(self.denied())

    def test_foreign_gate_is_ignored_even_with_matching_input(self):
        self.state()
        self.event()
        for overrides in ({"platform": "claude"}, {"session_id": "foreign"}, {"platform": None}, {"session_id": None}):
            with self.subTest(overrides=overrides):
                self.gate(**overrides)
                self.assertFalse(self.denied())

    def test_missing_identity_cannot_borrow_shared_pointer(self):
        self.state({"decision": "block", "input_event_id": "new-input"})
        self.event()
        (self.root / self.platform / "current-session.json").write_text(json.dumps({
            "schema_version": "session-intent-current.v1", "platform": self.platform, "session_id": self.sid,
            "state_path": str(self.directory / "intent-state.json")}))
        self.assertFalse(self.denied(sid=None))
        self.assertFalse((self.directory / "downstream-gates.json").exists())

    def test_unsafe_identity_cannot_alias_current_session(self):
        self.state({"decision": "block", "input_event_id": "new-input"})
        self.event()
        for sid in ("session/a", " session-a", ".session-a"):
            with self.subTest(sid=sid):
                self.assertFalse(self.denied(sid=sid))

    def test_canonical_state_does_not_require_or_follow_pointer_path(self):
        self.state()
        self.event()
        canonical = str(self.directory / "intent-state.json")
        self.assertIn(canonical, json.dumps(self.run_hook(reminder=True)))
        foreign = str(self.root / "unrelated" / "intent-state.json")
        (self.root / self.platform / "current-session.json").write_text(json.dumps({
            "schema_version": "session-intent-current.v1", "platform": self.platform, "session_id": self.sid,
            "state_path": foreign}))
        output = json.dumps(self.run_hook(reminder=True))
        self.assertIn(canonical, output)
        self.assertNotIn(foreign, output)

    def test_new_anchor_requires_event_id_not_only_reused_digest(self):
        self.state({"decision": "block", "input_digest": "sha256:new"})
        self.event()
        self.assertFalse(self.denied())
        self.gate(input_event_id="")
        self.assertFalse(self.denied())

    def test_conflicting_digest_cannot_match_by_id_alone(self):
        self.state({"decision": "block", "input_event_id": "new-input", "input_digest": "sha256:wrong"})
        self.event()
        self.assertFalse(self.denied())

    def test_partial_new_anchor_never_falls_back_to_old_audit(self):
        self.state({"decision": "block", "input_event_id": "new-input"}, latest_input_event_id=None)
        self.event()
        self.assertFalse(self.denied())

    def test_valid_legacy_identity_and_event_still_carry_current_block(self):
        self.state({"decision": "block", "input_event_id": "new-input"}, legacy=True)
        self.event()
        self.assertTrue(self.denied())

    def test_missing_state_creates_no_block_from_a_gate_and_event(self):
        self.event()
        self.gate()
        self.assertFalse(self.denied())

    def test_foreign_latest_legacy_input_does_not_revive_older_own_input(self):
        self.state({"decision": "block", "input_event_id": "new-input"}, legacy=True)
        self.event()
        with (self.directory / "intent-events.jsonl").open("a") as handle:
            handle.write(json.dumps({"event": "user-input-observed", "platform": "claude",
                                     "session_id": "foreign", "event_id": "foreign-input", "input_digest": "sha256:foreign"}) + "\n")
        self.gate()
        self.assertFalse(self.denied())

    def test_unverifiable_record_id_cannot_fall_back_to_legacy_digest(self):
        self.state({"decision": "block", "input_event_id": "unverifiable", "input_digest": "sha256:new"}, legacy=True)
        self.event(event_id="")
        self.assertFalse(self.denied())

    def test_failed_new_intake_marker_prevents_old_block_being_called_current(self):
        self.state({"decision": "block", "input_event_id": "new-input"})
        self.event()
        self.gate()
        self.save("ledger-degraded.json", {"schema_version": "session-intent-degrade.v1", "reason": "ledger-write-failed"})
        self.assertFalse(self.denied())
        reminder = json.dumps(self.run_hook(reminder=True))
        self.assertIn("withheld", reminder)
        self.assertIn("degraded", reminder)

    def test_missing_current_input_with_old_gate_withholds_routing(self):
        for overrides in ({"session_id": "foreign"}, {"latest_input_event_id": None}):
            with self.subTest(overrides=overrides):
                self.state(**overrides)
                self.event()
                self.gate()
                reminder = json.dumps(self.run_hook(reminder=True))
                self.assertIn("withheld", reminder)
                self.assertNotIn("Continue intake/routing", reminder)


if __name__ == "__main__":
    unittest.main(verbosity=2)
