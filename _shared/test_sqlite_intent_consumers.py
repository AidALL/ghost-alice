"""SQLite authority through the live Python/Node hook and diagnostic readers."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SHARED = Path(__file__).resolve().parent
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(SHARED.parent / "session-intent-analyzer" / "scripts"))
import session_intent_ledger as ledger
import task_router_reminder_hook as router
import hook_profile_gate as profile
import io_trace_hook as trace


class SQLiteIntentConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.platform, self.sid = "codex", "sqlite-session"
        self.directory = self.root / self.platform / self.sid
        self.directory.mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.root / "home"), CODEX_THREAD_ID="",
                        GHOST_ALICE_SESSION_ID="", GHOST_ALICE_PYTHON=sys.executable,
                        GHOST_ALICE_PLATFORM=self.platform,
                        GHOST_ALICE_SESSION_INTENT_ROOT=str(self.root))

    def observe(self, text="bounded request"):
        ledger.record_turn(root=self.root, platform=self.platform, session_id=self.sid,
                           raw_user_input=text)
        return ledger.read_session_state(root=self.root, platform=self.platform,
                                         session_id=self.sid)

    def block(self):
        state = self.observe()
        ledger.record_turn(root=self.root, platform=self.platform, session_id=self.sid,
                           expected_input_event_id=state["latest_input_event_id"],
                           intent_delta={"model_security_decision": {
                               "decision": "block", "input_event_id": state["latest_input_event_id"]}})
        return ledger.read_session_state(root=self.root, platform=self.platform,
                                         session_id=self.sid)

    def node(self, hook="tool-checkpoint", *, script=None, env=None, sid=None):
        process = subprocess.run(
            [shutil.which("node"), str(script or SHARED / "ghost-alice-hook.mjs"), "--platform", self.platform,
             "--event", "PreToolUse", "--hook", hook, "--session-intent-root", str(self.root)],
            input=json.dumps({"session_id": sid or self.sid, "tool_name": "Bash"}), env=env or self.env,
            capture_output=True, text=True, check=True)
        return json.loads(process.stdout)

    def test_stale_json_cannot_hide_sqlite_current_model_block(self):
        state = self.block()
        state["model_security_decision"] = None
        (self.directory / "intent-state.json").write_text(json.dumps(state))
        material = router.session_material(self.root, self.platform, self.sid)
        self.assertIsInstance(material["state"].get("model_security_decision"), dict)
        self.assertEqual(material["state"]["model_security_decision"]["decision"], "block")
        self.assertEqual(self.node().get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_missing_exports_do_not_hide_sqlite_preflight_or_block(self):
        self.block()
        for name in ("intent-state.json", "intent-events.jsonl"):
            (self.directory / name).unlink(missing_ok=True)
        material = router.session_material(self.root, self.platform, self.sid)
        self.assertTrue(material["latest_input"])
        self.assertTrue(profile._has_current_downstream_block(self.env, {"session_id": self.sid}, self.platform))
        self.assertEqual(self.node().get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_stale_json_block_cannot_override_sqlite_no_decision(self):
        state = self.observe()
        state["model_security_decision"] = {"decision": "block", "input_event_id": state["latest_input_event_id"]}
        (self.directory / "intent-state.json").write_text(json.dumps(state))
        self.assertFalse(router.gate_state(self.root, self.platform, self.sid))
        self.assertNotEqual(self.node().get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_diagnostic_reads_committed_events_without_jsonl_export(self):
        for index in range(3):
            self.observe(f"request {index}")
        (self.directory / "intent-events.jsonl").unlink(missing_ok=True)
        with patch.dict(os.environ, self.env, clear=True):
            self.assertIsNotNone(trace._semantic_delta_warning({"session_id": self.sid}))

    def test_corrupt_database_never_falls_back_to_json_decision(self):
        state = self.block()
        (self.directory / "intent-state.json").write_text(json.dumps(state))
        (self.root / "ghost-state.sqlite3").write_bytes(b"not a SQLite database")
        material = router.session_material(self.root, self.platform, self.sid)
        self.assertTrue(material["degraded"])
        self.assertFalse(material["latest_input"])
        result = self.node()
        self.assertNotEqual(result.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
        self.assertIn("degraded", json.dumps(self.node("hook-reminder")))

    def test_bound_identity_cannot_borrow_database_discovery_session(self):
        self.block()
        self.assertFalse(router.session_material(self.root, self.platform, "other-session")["latest_input"])
        result = self.node(sid="other-session")
        self.assertNotEqual(result.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
        self.assertIn("withheld", json.dumps(self.node("hook-reminder", sid="other-session")))

    def test_installed_node_reader_uses_installed_api_and_selected_python(self):
        self.block()
        home = Path(self.env["HOME"])
        scripts = home / ".agents" / "skills" / "session-intent-analyzer" / "scripts"
        shutil.copytree(SHARED.parent / "session-intent-analyzer" / "scripts", scripts,
                        ignore=shutil.ignore_patterns("__pycache__", "test*"))
        installed = home / ".ghost-alice" / "hooks"
        installed.mkdir(parents=True)
        for filename in ("ghost-alice-hook.mjs", "derive_downstream_gate.mjs", "reminder_texts.json"):
            shutil.copy2(SHARED / filename, installed / filename)
        # A Node process launched by the selected Python runner must also work
        # when that interpreter is not discoverable through the child's PATH.
        env = dict(self.env, PATH="")
        (self.directory / "intent-state.json").unlink(missing_ok=True)
        result = self.node(script=installed / "ghost-alice-hook.mjs", env=env)
        self.assertEqual(result.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_reading_hook_does_not_create_a_database_or_rewrite_legacy_export(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        legacy = {"schema_version": "session-intent-ledger.v1", "platform": self.platform,
                  "session_id": self.sid, "ledger_revision": 1,
                  "latest_input_event_id": "legacy-input", "latest_input_digest": "sha256:legacy",
                  "latest_input_char_count": 1}
        export = self.directory / "intent-state.json"
        text = json.dumps(legacy)
        export.write_text(text)
        self.assertTrue(router.session_material(self.root, self.platform, self.sid)["latest_input"])
        self.node()
        self.assertEqual(export.read_text(), text)
        self.assertFalse((self.root / "ghost-state.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
