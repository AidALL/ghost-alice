"""SQLite authority for the readers outside the ledger: the doctor's read and the io-trace hook.

The ledger's own tests (session-intent-analyzer/scripts/test_sqlite_authority.py) own the read
semantics: database authority over exports, corrupt databases and deleted databases.
"""

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
                           raw_user_input=text)

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
        legacy = {"schema_version": "session-intent-ledger.v1", "platform": self.platform,
                  "session_id": self.sid, "ledger_revision": 1,
                  "latest_input_event_id": "legacy-input", "latest_input_digest": "sha256:legacy",
                  "latest_input_char_count": 1}
        export = self.directory / "intent-state.json"
        text = json.dumps(legacy)
        export.write_text(text)
        # The doctor reads the ledger this way (install_doctor's session-intent golden check).
        state = ledger.read_session_state(root=self.root, platform=self.platform, session_id=self.sid,
                                          recover_audit=False)
        self.assertEqual(state["latest_input_event_id"], "legacy-input")
        self.assertEqual(export.read_text(), text)
        self.assertFalse((self.root / "ghost-state.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
