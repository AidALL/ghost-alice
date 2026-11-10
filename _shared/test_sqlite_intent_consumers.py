"""SQLite authority for the readers outside the ledger: the doctor's read and the io-trace hook.

The ledger's own tests (session-intent-analyzer/scripts/test_sqlite_authority.py) own the read
semantics: database authority over exports, corrupt databases and deleted databases.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SHARED = Path(__file__).resolve().parent
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(SHARED.parent / "session-intent-analyzer" / "scripts"))
import session_intent_ledger as ledger
import io_trace_hook as trace
import install_doctor


class SQLiteIntentConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.platform, self.sid = "codex", "sqlite-session"
        self.directory = self.root / self.platform / self.sid
        self.directory.mkdir(parents=True)
        self.home = self.root / "home"

    def observe(self, text="bounded request"):
        ledger.record_turn(root=self.root, platform=self.platform, session_id=self.sid,
                           raw_input=text)

    def test_posttool_audits_tools_without_inferring_missing_semantic_changes(self):
        for index in range(3):
            self.observe(f"request {index}")
        (self.directory / "intent-events.jsonl").unlink(missing_ok=True)
        env = {"HOME": str(self.home), "GHOST_ALICE_PLATFORM": self.platform,
               "GHOST_ALICE_SESSION_INTENT_ROOT": str(self.root)}
        payload = json.dumps({"session_id": self.sid, "tool_name": "Read",
                              "tool_input": {"file_path": "/tmp/source.txt"}})
        with patch.dict(os.environ, env, clear=True):
            for _ in range(2):
                with patch.object(sys, "stdin", io.StringIO(payload)):
                    self.assertEqual(trace.main(), 0)
        audit = self.home / ".ghost-alice" / "io-trace.jsonl"
        rows = [json.loads(line) for line in audit.read_text().splitlines()]
        self.assertEqual([row["tool"] for row in rows], ["Read", "Read"])
        self.assertTrue(all(row["session"] == self.sid and
                            row["path"] == "/tmp/source.txt" for row in rows))

    def test_doctor_read_does_not_create_a_database_or_rewrite_legacy_export(self):
        run_process = install_doctor.subprocess.run
        read_states = []

        def run_with_legacy_input(command, **kwargs):
            if "--root" in command:
                # Supply legacy input at the writer boundary; execute the doctor's own reader below.
                root = Path(command[command.index("--root") + 1])
                payload = json.loads(kwargs["input"])
                legacy = {"schema_version": "session-intent-ledger.v1", "platform": self.platform,
                          "session_id": payload["session_id"], "ledger_revision": 1,
                          "latest_input_event_id": "legacy-input",
                          "latest_input_digest": "sha256:" + hashlib.sha256(payload["prompt"].encode()).hexdigest(),
                          "latest_input_char_count": len(payload["prompt"])}
                export = root / self.platform / payload["session_id"] / "intent-state.json"
                export.parent.mkdir(parents=True)
                export.write_text(json.dumps(legacy))
                return install_doctor.subprocess.CompletedProcess(command, 0, '{"continue": true}', '')
            root = Path(kwargs["env"]["GHOST_ALICE_SESSION_INTENT_ROOT"])
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            result = run_process(command, **kwargs)
            self.assertEqual(result.returncode, 0, result.stderr)
            read_states.append(json.loads(result.stdout)["material"]["state"])
            self.assertEqual({path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)
            self.assertFalse((root / "ghost-state.sqlite3").exists())
            return result

        with patch.object(install_doctor.subprocess, "run", run_with_legacy_input):
            status = install_doctor._runtime_session_intent_golden_status(SHARED, self.platform, repo_root=self.root)
        self.assertEqual(status, (install_doctor.STATUS_OK, "golden-pass"))
        self.assertEqual([state["latest_input_event_id"] for state in read_states], ["legacy-input"])


if __name__ == "__main__":
    unittest.main()
