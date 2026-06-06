"""Tests for merge-companion snapshot v2 behavior.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""

import tempfile
import unittest
from pathlib import Path

from snapshot import SnapshotError, capture_snapshot, file_hash, load_snapshot, snapshot_records


class TestSnapshotV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.skills_dir = self.root / "skills"
        self.skills_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_hash_deterministic(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("hello", encoding="utf-8")

        self.assertEqual(file_hash(skill_file), file_hash(skill_file))
        self.assertEqual(len(file_hash(skill_file)), 64)

    def test_file_hash_missing_returns_none(self):
        self.assertIsNone(file_hash(self.skills_dir / "missing" / "SKILL.md"))

    def test_load_missing_raises(self):
        with self.assertRaises(SnapshotError):
            load_snapshot(self.root / "missing-snapshot.json")

    def test_snapshot_v2_records_metadata_and_legacy_files_map(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("safe utf-8 body\n", encoding="utf-8")
        snap = self.root / "snapshot.json"

        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)
        loaded = load_snapshot(snap)

        self.assertEqual(loaded["version"], 2)
        self.assertEqual(loaded["files"][str(skill_file)], loaded["file_records"][str(skill_file)]["sha256"])
        record = loaded["file_records"][str(skill_file)]
        self.assertEqual(record["asset_id"], "task-router")
        self.assertEqual(record["relative_path"], "task-router/SKILL.md")
        self.assertEqual(record["file_kind"], "file")
        self.assertEqual(record["encoding"], "utf-8")
        self.assertIsInstance(record["size"], int)
        self.assertIsInstance(record["mtime_ns"], int)
        self.assertRegex(record["mode"], r"^0o[0-7]{3,4}$")

    def test_snapshot_v2_records_install_marker_hash(self):
        skill_root = self.skills_dir / "task-router"
        skill_root.mkdir()
        skill_file = skill_root / "SKILL.md"
        marker = skill_root / ".ghost-alice-install.json"
        skill_file.write_text("skill\n", encoding="utf-8")
        marker.write_text('{"asset_id":"task-router"}\n', encoding="utf-8")
        snap = self.root / "snapshot.json"

        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)
        loaded = load_snapshot(snap)
        record = loaded["file_records"][str(skill_file)]

        self.assertEqual(record["install_marker_path"], str(marker))
        self.assertEqual(record["install_marker_hash"], file_hash(marker))

    def test_snapshot_records_normalizes_v1_snapshot(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        v1 = {
            "version": 1,
            "platform": "codex",
            "captured_at": "2026-05-05T00:00:00Z",
            "files": {str(skill_file): "abc"},
        }

        records = snapshot_records(v1, skills_dir=self.skills_dir)

        self.assertEqual(records[str(skill_file)]["sha256"], "abc")
        self.assertEqual(records[str(skill_file)]["asset_id"], "task-router")
        self.assertEqual(records[str(skill_file)]["relative_path"], "task-router/SKILL.md")
        self.assertIsNone(records[str(skill_file)]["install_marker_hash"])

    def test_capture_snapshot_records_v1_migration_event(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("skill\n", encoding="utf-8")
        snap = self.root / "snapshot.json"
        snap.write_text(
            '{"version":1,"platform":"codex","captured_at":"old","files":{}}\n',
            encoding="utf-8",
        )

        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)
        loaded = load_snapshot(snap)

        self.assertEqual(loaded["migration_events"][0]["event"], "snapshot-v1-to-v2")
        self.assertEqual(loaded["migration_events"][0]["from_version"], 1)
        self.assertEqual(loaded["migration_events"][0]["to_version"], 2)


if __name__ == "__main__":
    unittest.main()
