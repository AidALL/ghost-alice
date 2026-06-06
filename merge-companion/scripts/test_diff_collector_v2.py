"""Tests for merge-companion diff collection v2 behavior.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""

import tempfile
import time
import unittest
import json
from pathlib import Path

from diff_collector import README_FIRST_TEMPLATE, collect_user_changes, register_changes_in_manifest
from manifest_io import read_manifest
from snapshot import capture_snapshot, file_hash


class TestDiffCollectorV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.skills_dir = self.root / "skills"
        self.pending = self.root / "pending-merges" / "codex"
        self.manifest = self.pending / "manifest.json"
        self.skills_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unchanged_file_yields_no_change(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("same content", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        self.assertEqual(collect_user_changes(snap, [skill_file], skills_dir=self.skills_dir), [])

    def test_plain_snapshot_modified_file_yields_change_without_skill_root(self):
        file_path = self.root / "a.md"
        file_path.write_text("original", encoding="utf-8")
        snap = self.root / "plain-snapshot.json"
        capture_snapshot(snap, [file_path], "codex")

        file_path.write_text("user edit", encoding="utf-8")
        changes = collect_user_changes(snap, [file_path])

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["path"], str(file_path))
        self.assertEqual(changes[0]["change_kind"], "modified")
        self.assertNotEqual(changes[0]["snapshot_hash"], changes[0]["current_hash"])

    def test_register_modified_change_writes_backup_manifest_and_readme(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)
        skill_file.write_text("user edit", encoding="utf-8")
        changes = collect_user_changes(snap, [skill_file], skills_dir=self.skills_dir)

        register_changes_in_manifest(
            changes,
            pending_dir=self.pending,
            manifest_path=self.manifest,
            platform="codex",
        )

        backup_files = list(self.pending.rglob("*.bak"))
        self.assertEqual(len(backup_files), 1)
        self.assertEqual(backup_files[0].read_text(encoding="utf-8"), "user edit")
        self.assertTrue((self.pending / "READ-ME-FIRST.md").exists())
        manifest = read_manifest(self.manifest)
        self.assertEqual(len(manifest["entries"]), 1)
        self.assertFalse(manifest["entries"][0]["decided"])
        self.assertEqual(manifest["entries"][0]["platform"], "codex")

    def test_readme_first_uses_backup_folder_context(self):
        body = README_FIRST_TEMPLATE

        self.assertIn("User:", body)
        self.assertIn("Tech:", body)
        self.assertLess(body.index("User:"), body.index("Tech:"))
        self.assertIn("This is the backup folder guide", body)
        self.assertIn("local changes", body)
        self.assertIn("merge-companion", body)
        self.assertIn("Please review backed-up changes.", body)
        self.assertNotIn("Non-developer note:", body)
        self.assertNotIn("Developer note:", body)
        self.assertNotIn("Type this", body)
        self.assertNotIn("Send this", body)
        self.assertNotIn("next AI CLI session starts", body)

    def test_deleted_file_yields_deleted_change(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        skill_file.unlink()
        changes = collect_user_changes(snap, [], skills_dir=self.skills_dir)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["path"], str(skill_file))
        self.assertEqual(changes[0]["change_kind"], "deleted")
        self.assertEqual(changes[0]["asset_id"], "task-router")
        self.assertEqual(changes[0]["relative_path"], "task-router/SKILL.md")
        self.assertIsNone(changes[0]["current_hash"])

    def test_generated_file_deleted_from_stale_snapshot_is_ignored(self):
        generated_file = self.skills_dir / "hwpx" / "examples" / ".DS_Store"
        generated_file.parent.mkdir(parents=True)
        generated_file.write_text("generated", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [generated_file], "codex", skills_dir=self.skills_dir)

        generated_file.unlink()
        changes = collect_user_changes(snap, [], skills_dir=self.skills_dir)

        self.assertEqual(changes, [])

    def test_generated_file_passed_as_current_file_is_ignored(self):
        generated_file = self.skills_dir / "hwpx" / "examples" / ".DS_Store"
        generated_file.parent.mkdir(parents=True)
        generated_file.write_text("generated", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [], "codex", skills_dir=self.skills_dir)

        changes = collect_user_changes(snap, [generated_file], skills_dir=self.skills_dir)

        self.assertEqual(changes, [])

    def test_nested_file_uses_skill_root_as_asset_id(self):
        nested = self.skills_dir / "task-router" / "references" / "policy.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [nested], "codex", skills_dir=self.skills_dir)

        nested.write_text("user edit", encoding="utf-8")
        changes = collect_user_changes(snap, [nested], skills_dir=self.skills_dir)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_kind"], "modified")
        self.assertEqual(changes[0]["asset_id"], "task-router")
        self.assertEqual(changes[0]["relative_path"], "task-router/references/policy.md")

    def test_register_deleted_change_writes_manifest_without_backup(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)
        skill_file.unlink()
        changes = collect_user_changes(snap, [], skills_dir=self.skills_dir)

        register_changes_in_manifest(
            changes,
            pending_dir=self.pending,
            manifest_path=self.manifest,
            platform="codex",
        )

        self.assertEqual(list(self.pending.rglob("*.bak")), [])
        manifest = read_manifest(self.manifest)
        self.assertEqual(len(manifest["entries"]), 1)
        entry = manifest["entries"][0]
        self.assertEqual(entry["skill"], "task-router")
        self.assertEqual(entry["change_kind"], "deleted")
        self.assertIsNone(entry["backup_path"])
        self.assertIsNone(entry["current_hash"])
        self.assertEqual(entry["deleted_snapshot_hash"], changes[0]["snapshot_hash"])
        self.assertFalse(entry["decided"])

    def test_register_deduplicates_pending_entry(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)
        skill_file.write_text("user edit", encoding="utf-8")
        changes = collect_user_changes(snap, [skill_file], skills_dir=self.skills_dir)

        register_changes_in_manifest(
            changes,
            pending_dir=self.pending,
            manifest_path=self.manifest,
            platform="codex",
        )
        register_changes_in_manifest(
            changes,
            pending_dir=self.pending,
            manifest_path=self.manifest,
            platform="codex",
        )

        manifest = read_manifest(self.manifest)
        self.assertEqual(len(manifest["entries"]), 1)
        self.assertEqual(len(list(self.pending.rglob("*.bak"))), 1)
        self.assertRegex(manifest["entries"][0]["id"], r"^pending-[0-9a-f]{16}$")

    def test_file_replaced_by_directory_yields_type_changed(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        skill_file.unlink()
        skill_file.mkdir()
        changes = collect_user_changes(snap, [], skills_dir=self.skills_dir)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_kind"], "type-changed")
        self.assertEqual(changes[0]["current_file_kind"], "directory")
        self.assertEqual(changes[0]["snapshot_file_kind"], "file")
        self.assertIsNone(changes[0]["current_hash"])

    def test_invalid_utf8_change_yields_encoding_invalid(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        skill_file.write_bytes(b"\xff\xfeinvalid")
        changes = collect_user_changes(snap, [skill_file], skills_dir=self.skills_dir)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_kind"], "encoding-invalid")
        self.assertEqual(changes[0]["snapshot_encoding"], "utf-8")
        self.assertEqual(changes[0]["current_encoding"], "binary-or-non-utf8")

    def test_same_change_id_is_stable_across_registration_attempts(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)
        skill_file.write_text("user edit", encoding="utf-8")
        changes = collect_user_changes(snap, [skill_file], skills_dir=self.skills_dir)

        register_changes_in_manifest(
            changes,
            pending_dir=self.pending,
            manifest_path=self.manifest,
            platform="codex",
        )
        first_id = read_manifest(self.manifest)["entries"][0]["id"]

        self.manifest.unlink()
        for backup in self.pending.rglob("*.bak"):
            backup.unlink()
        time.sleep(1.1)
        register_changes_in_manifest(
            changes,
            pending_dir=self.pending,
            manifest_path=self.manifest,
            platform="codex",
        )

        self.assertEqual(read_manifest(self.manifest)["entries"][0]["id"], first_id)

    def test_renamed_file_yields_single_moved_change(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        moved_file = self.skills_dir / "task-router" / "README.md"
        skill_file.parent.mkdir()
        skill_file.write_text("same content", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        skill_file.rename(moved_file)
        changes = collect_user_changes(snap, [moved_file], skills_dir=self.skills_dir)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_kind"], "moved")
        self.assertEqual(changes[0]["path"], str(moved_file))
        self.assertEqual(changes[0]["previous_path"], str(skill_file))
        self.assertEqual(changes[0]["relative_path"], "task-router/README.md")
        self.assertEqual(changes[0]["previous_relative_path"], "task-router/SKILL.md")
        self.assertEqual(changes[0]["snapshot_hash"], changes[0]["current_hash"])

    def test_install_marker_hash_drift_yields_ownership_conflict(self):
        skill_root = self.skills_dir / "task-router"
        skill_root.mkdir()
        skill_file = skill_root / "SKILL.md"
        marker = skill_root / ".ghost-alice-install.json"
        skill_file.write_text("same content", encoding="utf-8")
        marker.write_text('{"asset_id":"task-router","source":"old"}\n', encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        marker.write_text('{"asset_id":"task-router","source":"user"}\n', encoding="utf-8")
        changes = collect_user_changes(snap, [skill_file], skills_dir=self.skills_dir)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_kind"], "ownership-conflict")
        self.assertEqual(changes[0]["snapshot_hash"], changes[0]["current_hash"])
        self.assertNotEqual(
            changes[0]["snapshot_install_marker_hash"],
            changes[0]["current_install_marker_hash"],
        )

    def test_shared_platform_marker_refresh_without_file_change_is_not_user_change(self):
        skill_root = self.skills_dir / "task-router"
        skill_root.mkdir()
        skill_file = skill_root / "SKILL.md"
        marker = skill_root / ".ghost-alice-install.json"
        skill_file.write_text("same content", encoding="utf-8")
        content_hash = file_hash(skill_file)
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "managed_by": "Ghost-ALICE",
                    "platform": "codex",
                    "asset_id": "task-router",
                    "source_repo": "repo",
                    "source_commit": "abc",
                    "installed_at": "2026-01-01T00:00:00Z",
                    "install_mode": "copy",
                    "content_hashes": {"SKILL.md": content_hash},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "managed_by": "Ghost-ALICE",
                    "platform": "claude",
                    "asset_id": "task-router",
                    "source_repo": "repo",
                    "source_commit": "abc",
                    "installed_at": "2026-01-02T00:00:00Z",
                    "install_mode": "copy",
                    "content_hashes": {"SKILL.md": content_hash},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        changes = collect_user_changes(snap, [skill_file], skills_dir=self.skills_dir)

        self.assertEqual(changes, [])

    def test_shared_platform_source_refresh_is_not_user_change(self):
        skill_root = self.skills_dir / "task-router"
        skill_root.mkdir()
        skill_file = skill_root / "SKILL.md"
        marker = skill_root / ".ghost-alice-install.json"
        skill_file.write_text("old content", encoding="utf-8")
        old_hash = file_hash(skill_file)
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "managed_by": "Ghost-ALICE",
                    "platform": "claude",
                    "asset_id": "task-router",
                    "source_repo": "repo",
                    "source_commit": "old",
                    "installed_at": "2026-01-01T00:00:00Z",
                    "install_mode": "copy",
                    "content_hashes": {"SKILL.md": old_hash},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "claude", skills_dir=self.skills_dir)

        skill_file.write_text("new content", encoding="utf-8")
        new_hash = file_hash(skill_file)
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "managed_by": "Ghost-ALICE",
                    "platform": "codex",
                    "asset_id": "task-router",
                    "source_repo": "repo",
                    "source_commit": "new",
                    "installed_at": "2026-01-02T00:00:00Z",
                    "install_mode": "copy",
                    "content_hashes": {"SKILL.md": new_hash},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        changes = collect_user_changes(snap, [skill_file], skills_dir=self.skills_dir)

        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main()
