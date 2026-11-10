"""Tests for merge-companion manifest I/O.

Dependencies: Python 3.11+ standard library, the local merge-companion modules and the Core `file_lock` they use.
"""

import errno
import os
import stat
import threading
import unittest
import tempfile
from pathlib import Path
from unittest import mock
import manifest_io
from manifest_io import (
    read_manifest, append_entry, append_entry_if_absent,
    mark_decided, list_pending, ManifestError,
)
import file_lock  # manifest_io puts the Core helpers on the import path

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

    def test_ambiguous_historical_id_cannot_decide_an_arbitrary_occurrence(self):
        append_entry(self.path, {"id": "old-shared-id", "decided": False, "decision": None})
        append_entry(self.path, {"id": "old-shared-id", "decided": False, "decision": None})
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ManifestError, "ambiguous"):
            mark_decided(self.path, "old-shared-id", "discarded")
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_history_does_not_hide_the_unique_pending_occurrence(self):
        append_entry(self.path, {"id": "old-id", "decided": True, "decision": "merged"})
        append_entry(self.path, {"id": "old-id", "decided": False, "decision": None})
        mark_decided(self.path, "old-id", "discarded")
        self.assertEqual([entry["decision"] for entry in read_manifest(self.path)["entries"]], ["merged", "discarded"])

    def test_missing_decided_and_deferred_entries_remain_pending_and_prevent_duplicates(self):
        entry = {"id": "pending", "source_path": "/skill"}
        append_entry(self.path, entry)
        for deferred in (False, True):
            with self.subTest(deferred=deferred):
                if deferred:
                    mark_decided(self.path, "pending", "deferred")
                before = self.path.read_bytes()
                self.assertEqual([e["id"] for e in list_pending(self.path)], ["pending"])
                self.assertFalse(append_entry_if_absent(self.path, dict(entry, id="duplicate"), ["source_path"]))
                self.assertEqual(self.path.read_bytes(), before)

    def test_companion_does_not_silently_ignore_non_object_entries(self):
        manifest_io.write_manifest(self.path, {"entries": ["invalid-entry"]})
        with self.assertRaises(AttributeError):
            list_pending(self.path)
        with self.assertRaises(AttributeError):
            append_entry_if_absent(self.path, {"id": "new"}, ["id"])
        self.assertEqual(read_manifest(self.path), {"entries": ["invalid-entry"]})

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_concurrent_duplicate_checks_share_the_locked_read_and_write(self):
        start = threading.Barrier(2)
        results, errors = [], []
        def writer(identity):
            try:
                start.wait(timeout=5)
                results.append(append_entry_if_absent(self.path, {"id": identity, "source_path": "/same"}, ["source_path"]))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=writer, args=(identity,)) for identity in ("first", "second")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(read_manifest(self.path)["entries"]), 1)

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

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_lock_held_past_the_limit_raises_lock_timeout(self):
        # A writer that stopped while holding the manifest lock must not stop the installer behind it.
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        outcome = []

        def writer():
            try:
                append_entry(self.path, {"id": "late", "decided": False})
            except file_lock.LockTimeout:
                outcome.append("timeout")

        with mock.patch.object(manifest_io, "MANIFEST_LOCK_WAIT_S", 0.2), file_lock.exclusive(lock):
            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
            thread.join(5)
        self.assertEqual(outcome, ["timeout"])
        self.assertEqual(read_manifest(self.path), {"entries": []})

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_lock_the_file_system_refuses_raises_and_writes_nothing(self):
        # An entry points at a backed-up external change, so two writers without the lock could lose it without a trace.
        with mock.patch.object(file_lock.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")), \
                self.assertRaises(OSError):
            append_entry(self.path, {"id": "late", "decided": False})
        self.assertEqual(read_manifest(self.path), {"entries": []})

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_holder_slower_than_the_log_limit_does_not_fail_the_write(self):
        # A timeout here aborts the install, so a holder that is only slow must not trip it.
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        held = threading.Event()
        release = threading.Event()

        def holder():
            with file_lock.exclusive(lock):
                held.set()
                release.wait(5)

        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        self.assertTrue(held.wait(5))
        timer = threading.Timer(0.5, release.set)
        timer.start()
        with mock.patch.object(file_lock, "LOCK_WAIT_S", 0.1):
            append_entry(self.path, {"id": "patient", "decided": False})
        thread.join(5)
        timer.join(5)
        self.assertEqual([entry["id"] for entry in read_manifest(self.path)["entries"]], ["patient"])

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_a_rewrite_keeps_the_manifest_mode(self):
        self.path.write_text('{"entries": []}\n', encoding="utf-8")
        os.chmod(self.path, 0o640)
        manifest_io.write_manifest(self.path, {"version": 1, "entries": []})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        self.assertEqual(read_manifest(self.path), {"version": 1, "entries": []})

    def test_append_if_absent_preserves_manifest_without_a_lock_backend(self):
        with mock.patch.object(file_lock, "fcntl", None), mock.patch.object(file_lock, "msvcrt", None):
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
            with self.assertRaises(OSError):
                append_entry_if_absent(
                    self.path,
                    entry,
                    ["platform", "source_path", "snapshot_hash", "current_hash", "change_kind"],
                )
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_suffix(self.path.suffix + ".lock").exists())

if __name__ == "__main__":
    unittest.main()
