"""Tests for merge-companion manifest I/O.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""

import json
import unittest
import tempfile
from pathlib import Path
import manifest_io
from manifest_io import (
    read_manifest, write_manifest, append_entry, append_entry_if_absent,
    mark_decided, list_pending, ManifestError,
)

class TestManifestIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "manifest.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_missing_returns_empty(self):
        self.assertEqual(read_manifest(self.path), {"entries": []})

    def test_read_corrupt_returns_empty(self):
        self.path.write_text("not json")
        self.assertEqual(read_manifest(self.path), {"entries": []})

    def test_append_creates_entry(self):
        entry = {
            "id": "2026-04-15T10-00-00Z-skill-foo",
            "platform": "codex",
            "skill": "foo",
            "backup_path": "/tmp/.bak",
            "snapshot_hash": "abc",
            "current_hash": "def",
            "decided": False,
            "decision": None,
            "created_at": "2026-04-15T10:00:00Z",
        }
        append_entry(self.path, entry)
        m = read_manifest(self.path)
        self.assertEqual(len(m["entries"]), 1)
        self.assertEqual(m["entries"][0]["id"], entry["id"])

    def test_mark_decided_updates_entry(self):
        entry = {
            "id": "x", "platform": "codex", "skill": "f",
            "backup_path": "/p", "snapshot_hash": "a", "current_hash": "b",
            "decided": False, "decision": None, "created_at": "t",
        }
        append_entry(self.path, entry)
        mark_decided(self.path, "x", decision="merged")
        m = read_manifest(self.path)
        self.assertTrue(m["entries"][0]["decided"])
        self.assertEqual(m["entries"][0]["decision"], "merged")

    def test_list_pending_filters_decided(self):
        for i, dec in enumerate([False, True, False]):
            append_entry(self.path, {
                "id": f"e{i}", "platform": "codex", "skill": "s",
                "backup_path": "/", "snapshot_hash": "a", "current_hash": "b",
                "decided": dec, "decision": "merged" if dec else None,
                "created_at": "t",
            })
        pending = list_pending(self.path)
        self.assertEqual({e["id"] for e in pending}, {"e0", "e2"})

    def test_mark_decided_unknown_id_raises(self):
        with self.assertRaises(ManifestError):
            mark_decided(self.path, "no-such-id", decision="merged")

    def test_atomic_write_no_temp_left_behind(self):
        entry = {"id":"x","platform":"codex","skill":"f","backup_path":"/","snapshot_hash":"a","current_hash":"b","decided":False,"decision":None,"created_at":"t"}
        append_entry(self.path, entry)
        # no leftover .manifest.*.tmp in same dir
        leftover = list(self.path.parent.glob(".manifest.*.tmp"))
        self.assertEqual(leftover, [])

    def test_corrupt_read_creates_backup(self):
        self.path.write_text("garbage{")
        read_manifest(self.path)
        backup = self.path.with_suffix(self.path.suffix + ".corrupt-bak")
        self.assertTrue(backup.exists())

    def test_append_if_absent_works_without_fcntl(self):
        old_has_fcntl = manifest_io.HAS_FCNTL
        manifest_io.HAS_FCNTL = False
        try:
            entry = {
                "id": "pending-a",
                "platform": "codex",
                "skill": "task-router",
                "source_path": "/tmp/SKILL.md",
                "backup_path": "/tmp/SKILL.md.bak",
                "snapshot_hash": "abc",
                "current_hash": "def",
                "change_kind": "modified",
                "decided": False,
                "decision": None,
                "created_at": "t",
            }
            self.assertTrue(
                append_entry_if_absent(
                    self.path,
                    entry,
                    ["platform", "source_path", "snapshot_hash", "current_hash", "change_kind"],
                )
            )
            self.assertFalse(
                append_entry_if_absent(
                    self.path,
                    dict(entry, id="pending-b"),
                    ["platform", "source_path", "snapshot_hash", "current_hash", "change_kind"],
                )
            )
        finally:
            manifest_io.HAS_FCNTL = old_has_fcntl

        self.assertEqual(len(read_manifest(self.path)["entries"]), 1)
        self.assertFalse(self.path.with_suffix(self.path.suffix + ".lock").exists())

if __name__ == "__main__":
    unittest.main()
