"""Tests for merge-companion diff collection v2 behavior.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""

import tempfile
import os
import shutil
import threading
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from diff_collector import README_FIRST_TEMPLATE, collect_external_changes, register_changes_in_manifest, quarantine_legacy_target
from file_walker import walk_installed_copy_files
from manifest_io import read_manifest, mark_decided
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

    def test_quarantine_append_failure_restores_original_tree(self):
        source = self.skills_dir / "legacy"
        source.mkdir()
        (source / "SKILL.md").write_bytes(b"user bytes\n\xff\x00")
        (source / "empty").mkdir()
        with mock.patch("diff_collector.append_entry", side_effect=OSError("manifest unavailable")):
            with self.assertRaisesRegex(OSError, "manifest unavailable"):
                quarantine_legacy_target(source, "legacy", self.pending, self.manifest, "codex")
        self.assertTrue(source.is_dir())
        self.assertEqual((source / "SKILL.md").read_bytes(), b"user bytes\n\xff\x00")
        self.assertTrue((source / "empty").is_dir())
        self.assertEqual(list((self.pending / "legacy-targets").iterdir()), [])
        self.assertFalse(self.manifest.exists())
        backup = quarantine_legacy_target(source, "legacy", self.pending, self.manifest, "codex")
        self.assertFalse(source.exists())
        self.assertEqual((backup / "SKILL.md").read_bytes(), b"user bytes\n\xff\x00")
        self.assertEqual(read_manifest(self.manifest)["entries"][0]["backup_path"], str(backup))

    def test_legacy_occurrences_in_one_second_have_distinct_decision_ids(self):
        source = self.skills_dir / 'legacy'
        now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        with mock.patch('diff_collector.datetime') as clock:
            clock.now.return_value = now
            for content in ('first', 'second'):
                source.mkdir()
                (source / 'SKILL.md').write_text(content)
                quarantine_legacy_target(source, 'legacy', self.pending, self.manifest, 'codex')
        entries = read_manifest(self.manifest)['entries']
        self.assertEqual(len({entry['id'] for entry in entries}), 2)
        self.assertEqual([(Path(entry['backup_path']) / 'SKILL.md').read_text() for entry in entries], ['first', 'second'])

    def test_quarantine_preserves_a_dangling_link_at_the_preferred_backup_path(self):
        source = self.skills_dir / 'legacy'
        source.mkdir()
        (source / 'SKILL.md').write_text('preserved tree')
        now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        existing = self.pending / 'legacy-targets' / '2026-11-10T00-00-00Z-legacy'
        existing.parent.mkdir(parents=True)
        existing.symlink_to(self.root / 'absent')
        with mock.patch('diff_collector.datetime') as clock:
            clock.now.return_value = now
            backup = quarantine_legacy_target(source, 'legacy', self.pending, self.manifest, 'codex')
        self.assertTrue(existing.is_symlink())
        self.assertNotEqual(backup, existing)
        self.assertEqual((backup / 'SKILL.md').read_text(), 'preserved tree')

    def test_quarantine_append_failure_preserves_new_source_and_reports_backup(self):
        for empty in (False, True):
            with self.subTest(empty_replacement=empty):
                source = self.skills_dir / f"legacy-{empty}"
                source.mkdir()
                (source / "SKILL.md").write_bytes(b"historical user bytes")

                def fail_with_new_source(*args):
                    source.mkdir()
                    if not empty:
                        (source / "SKILL.md").write_bytes(b"new source bytes")
                    raise OSError("manifest unavailable")

                with mock.patch("diff_collector.append_entry", side_effect=fail_with_new_source):
                    with self.assertRaisesRegex(RuntimeError, "recovery backup remains at") as failure:
                        quarantine_legacy_target(source, source.name, self.pending, self.manifest, "codex")
                if empty:
                    # A plain POSIX rename could replace this directory.
                    self.assertEqual(list(source.iterdir()), [])
                else:
                    self.assertEqual((source / "SKILL.md").read_bytes(), b"new source bytes")
                backups = list((self.pending / "legacy-targets").glob(f"*-{source.name}"))
                self.assertEqual(len(backups), 1)
                self.assertEqual((backups[0] / "SKILL.md").read_bytes(), b"historical user bytes")
                self.assertIn(str(backups[0]), str(failure.exception))
                self.assertIn(str(source), str(failure.exception))
                self.assertIsInstance(failure.exception.__cause__, OSError)

    def test_unchanged_file_yields_no_change(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("same content", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        self.assertEqual(collect_external_changes(snap, [skill_file], skills_dir=self.skills_dir), [])

    def test_plain_snapshot_modified_file_yields_change_without_skill_root(self):
        file_path = self.root / "a.md"
        file_path.write_text("original", encoding="utf-8")
        snap = self.root / "plain-snapshot.json"
        capture_snapshot(snap, [file_path], "codex")

        file_path.write_text("user edit", encoding="utf-8")
        changes = collect_external_changes(snap, [file_path])

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
        changes = collect_external_changes(snap, [skill_file], skills_dir=self.skills_dir)

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

        self.assertIn("For the human user:", body)
        self.assertIn("Tech:", body)
        self.assertLess(body.index("For the human user:"), body.index("Tech:"))
        self.assertIn("This is the backup folder guide", body)
        self.assertIn("external changes", body)
        self.assertIn("merge-companion", body)
        self.assertIn("Please review backed-up changes.", body)
        self.assertIn("Entry and activation", body)
        self.assertIn("id, source_path and backup_path", body)
        self.assertIn("references/manifest-schema.md", body)
        self.assertIn("this generated guide supplies no approval", body)

    def test_deleted_file_yields_deleted_change(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        skill_file.unlink()
        changes = collect_external_changes(snap, [], skills_dir=self.skills_dir)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["path"], str(skill_file))
        self.assertEqual(changes[0]["change_kind"], "deleted")
        self.assertEqual(changes[0]["asset_id"], "task-router")
        self.assertEqual(changes[0]["relative_path"], "task-router/SKILL.md")
        self.assertIsNone(changes[0]["current_hash"])

    def test_generated_file_deleted_from_stale_snapshot_is_ignored(self):
        generated_file = self.skills_dir / "custom-local-skill" / "examples" / ".DS_Store"
        generated_file.parent.mkdir(parents=True)
        generated_file.write_text("generated", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [generated_file], "codex", skills_dir=self.skills_dir)

        generated_file.unlink()
        changes = collect_external_changes(snap, [], skills_dir=self.skills_dir)

        self.assertEqual(changes, [])

    def test_generated_file_passed_as_current_file_is_ignored(self):
        generated_file = self.skills_dir / "custom-local-skill" / "examples" / ".DS_Store"
        generated_file.parent.mkdir(parents=True)
        generated_file.write_text("generated", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [], "codex", skills_dir=self.skills_dir)

        changes = collect_external_changes(snap, [generated_file], skills_dir=self.skills_dir)

        self.assertEqual(changes, [])

    def test_nested_file_uses_skill_root_as_asset_id(self):
        nested = self.skills_dir / "task-router" / "references" / "policy.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [nested], "codex", skills_dir=self.skills_dir)

        nested.write_text("user edit", encoding="utf-8")
        changes = collect_external_changes(snap, [nested], skills_dir=self.skills_dir)

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
        changes = collect_external_changes(snap, [], skills_dir=self.skills_dir)

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
        changes = collect_external_changes(snap, [skill_file], skills_dir=self.skills_dir)

        register_changes_in_manifest(
            changes,
            pending_dir=self.pending,
            manifest_path=self.manifest,
            platform="codex",
        )
        first_id = read_manifest(self.manifest)["entries"][0]["id"]
        register_changes_in_manifest(
            changes,
            pending_dir=self.pending,
            manifest_path=self.manifest,
            platform="codex",
        )

        manifest = read_manifest(self.manifest)
        self.assertEqual(len(manifest["entries"]), 1)
        self.assertEqual(manifest["entries"][0]["id"], first_id)
        self.assertEqual(len(list(self.pending.rglob("*.bak"))), 1)
        self.assertEqual(Path(manifest["entries"][0]["backup_path"]).read_text(), "user edit")

    def test_file_replaced_by_directory_yields_type_changed(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        skill_file.unlink()
        skill_file.mkdir()
        changes = collect_external_changes(snap, [], skills_dir=self.skills_dir)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_kind"], "type-changed")
        self.assertEqual(changes[0]["current_file_kind"], "directory")
        self.assertEqual(changes[0]["snapshot_file_kind"], "file")
        self.assertIsNone(changes[0]["current_hash"])

    def test_file_replaced_by_directory_keeps_child_bytes_for_review(self):
        source = self.skills_dir / "task-router" / "SKILL.md"
        source.parent.mkdir()
        source.write_text("original")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [source], "codex", skills_dir=self.skills_dir)
        source.unlink()
        source.mkdir()
        child = source / "user.txt"
        child.write_bytes(b"directory child\0\xff")
        changes = collect_external_changes(snap, walk_installed_copy_files(self.skills_dir, ["task-router"]), skills_dir=self.skills_dir)
        register_changes_in_manifest(changes, self.pending, self.manifest, "codex")
        entries = {entry["source_path"]: entry for entry in read_manifest(self.manifest)["entries"]}
        self.assertEqual(entries[str(source)]["current_file_kind"], "directory")
        self.assertIsNone(entries[str(source)]["backup_path"])
        shutil.rmtree(source)
        self.assertEqual(Path(entries[str(child)]["backup_path"]).read_bytes(), b"directory child\0\xff")

    def test_invalid_utf8_change_yields_encoding_invalid(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("original", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        skill_file.write_bytes(b"\xff\xfeinvalid")
        changes = collect_external_changes(snap, [skill_file], skills_dir=self.skills_dir)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_kind"], "encoding-invalid")
        self.assertEqual(changes[0]["snapshot_encoding"], "utf-8")
        self.assertEqual(changes[0]["current_encoding"], "binary-or-non-utf8")

    def test_recurrent_change_keeps_decisions_and_backups_on_their_own_occurrence(self):
        source = self.skills_dir / "task-router" / "SKILL.md"
        source.parent.mkdir()
        source.write_text("original")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [source], "codex", skills_dir=self.skills_dir)
        source.write_text("user edit")
        changes = collect_external_changes(snap, [source], skills_dir=self.skills_dir)
        register_changes_in_manifest(changes, self.pending, self.manifest, "codex")
        first = read_manifest(self.manifest)["entries"][0]
        mark_decided(self.manifest, first["id"], "merged")
        history = read_manifest(self.manifest)["entries"][0]
        register_changes_in_manifest(changes, self.pending, self.manifest, "codex")
        second = read_manifest(self.manifest)["entries"][1]
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["backup_path"], second["backup_path"])
        mark_decided(self.manifest, second["id"], "discarded")
        entries = read_manifest(self.manifest)["entries"]
        self.assertEqual(entries[0], history)
        self.assertEqual(entries[1]["decision"], "discarded")
        self.assertTrue(entries[1]["decided"])
        for entry in entries:
            self.assertEqual(Path(entry["backup_path"]).read_text(), "user edit")

    def test_type_changed_link_target_survives_after_live_tree_is_removed(self):
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                skill = self.skills_dir / f"links-{dangling}"
                skill.mkdir()
                source = skill / "SKILL.md"
                source.write_text("original")
                snap = self.root / f"snapshot-{dangling}.json"
                capture_snapshot(snap, [source], "codex", skills_dir=self.skills_dir)
                source.unlink()
                target = "../missing" if dangling else "../referent"
                if not dangling:
                    (self.skills_dir / "referent").write_bytes(b"referent bytes are not the link")
                source.symlink_to(target)
                changes = collect_external_changes(snap, walk_installed_copy_files(self.skills_dir, [skill.name]), skills_dir=self.skills_dir)
                self.assertEqual(changes[0]["change_kind"], "type-changed")
                register_changes_in_manifest(changes, self.pending, self.manifest, "codex")
                entry = next(e for e in read_manifest(self.manifest)["entries"] if e["source_path"] == str(source))
                self.assertEqual(entry["current_file_kind"], "symlink")
                self.assertEqual(entry["current_link_target"], target)
                self.assertIsNone(entry["current_hash"])
                self.assertIsNone(entry["backup_path"])
                shutil.rmtree(skill)
                recovered = self.root / f"recovered-{dangling}"
                recovered.symlink_to(entry["current_link_target"])
                self.assertEqual(os.readlink(recovered), target)

    def test_different_link_targets_are_distinct_pending_changes(self):
        source = self.skills_dir / "links" / "SKILL.md"
        source.parent.mkdir()
        source.write_text("original")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [source], "codex", skills_dir=self.skills_dir)
        for target in ("first-missing", "second-missing"):
            source.unlink()
            source.symlink_to(target)
            changes = collect_external_changes(snap, [], skills_dir=self.skills_dir)
            register_changes_in_manifest(changes, self.pending, self.manifest, "codex")
        self.assertEqual({e["current_link_target"] for e in read_manifest(self.manifest)["entries"]}, {"first-missing", "second-missing"})

    def test_root_ancestor_names_do_not_exclude_user_edits_or_deletions(self):
        for ancestor in (".git", "node_modules"):
            with self.subTest(ancestor=ancestor):
                skills = self.root / ancestor / "skills"
                source = skills / "task-router" / "SKILL.md"
                source.parent.mkdir(parents=True)
                cache = source.parent / "node_modules" / "cache.txt"
                cache.parent.mkdir()
                source.write_text("original")
                cache.write_text("generated")
                snap = self.root / f"snapshot-{ancestor}.json"
                capture_snapshot(snap, [source, cache], "codex", skills_dir=skills)
                source.write_text("user edit")
                cache.write_text("new cache")
                changes = collect_external_changes(snap, walk_installed_copy_files(skills, ["task-router"]), skills_dir=skills)
                self.assertEqual([(c["path"], c["change_kind"]) for c in changes], [(str(source), "modified")])
                register_changes_in_manifest(changes, self.pending, self.manifest, "codex")
                self.assertEqual(Path(read_manifest(self.manifest)["entries"][-1]["backup_path"]).read_text(), "user edit")
                source.unlink()
                changes = collect_external_changes(snap, [], skills_dir=skills)
                self.assertEqual([(c["path"], c["change_kind"]) for c in changes], [(str(source), "deleted")])

    def test_concurrent_registrars_keep_exclusive_backup_bytes(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                pending = self.root / f"concurrent-{duplicate}"
                manifest = pending / "manifest.json"
                paths = [self.skills_dir / f"branch-{i}" / "same.md" for i in range(2)]
                for index, path in enumerate(paths):
                    path.parent.mkdir(exist_ok=True)
                    path.write_text(f"user bytes {index}")
                if duplicate:
                    paths[1] = paths[0]
                changes = [{"path": str(path), "asset_id": "same-skill", "snapshot_hash": "old", "current_hash": file_hash(path), "change_kind": "modified"} for path in paths]
                ready_to_copy = threading.Barrier(2)
                copied = threading.Barrier(2)
                real_copy = shutil.copy2
                errors = []

                def synchronized_copy(src, dst, *args, **kwargs):
                    ready_to_copy.wait(timeout=5)
                    result = real_copy(src, dst, *args, **kwargs)
                    copied.wait(timeout=5)
                    return result

                def register(change):
                    try:
                        register_changes_in_manifest([change], pending, manifest, "codex")
                    except Exception as error:
                        errors.append(error)

                with mock.patch("diff_collector.shutil.copy2", side_effect=synchronized_copy):
                    threads = [threading.Thread(target=register, args=(change,)) for change in changes]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(10)
                        self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                entries = read_manifest(manifest)["entries"]
                self.assertEqual(len(entries), 1 if duplicate else 2)
                self.assertEqual(len({entry["backup_path"] for entry in entries}), len(entries))
                for entry in entries:
                    self.assertEqual(Path(entry["backup_path"]).read_bytes(), Path(entry["source_path"]).read_bytes())

    def test_renamed_file_yields_single_moved_change(self):
        skill_file = self.skills_dir / "task-router" / "SKILL.md"
        moved_file = self.skills_dir / "task-router" / "README.md"
        skill_file.parent.mkdir()
        skill_file.write_text("same content", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, [skill_file], "codex", skills_dir=self.skills_dir)

        skill_file.rename(moved_file)
        changes = collect_external_changes(snap, [moved_file], skills_dir=self.skills_dir)

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
        changes = collect_external_changes(snap, [skill_file], skills_dir=self.skills_dir)

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

        changes = collect_external_changes(snap, [skill_file], skills_dir=self.skills_dir)

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

        changes = collect_external_changes(snap, [skill_file], skills_dir=self.skills_dir)

        self.assertEqual(changes, [])


    def test_walker_tracks_only_skill_roots_with_install_marker(self):
        managed = self.skills_dir / "task-router"
        managed.mkdir()
        (managed / ".ghost-alice-install.json").write_text('{"managed_by": "Ghost-ALICE"}\n', encoding="utf-8")
        (managed / "SKILL.md").write_text("managed", encoding="utf-8")
        synced = self.skills_dir / "synced" / "bucket" / "pptx"
        synced.mkdir(parents=True)
        (synced / "SKILL.md").write_text("synced by the Claude app", encoding="utf-8")
        own = self.skills_dir / "my-notes"
        own.mkdir()
        (own / "SKILL.md").write_text("the user's own skill", encoding="utf-8")

        # Only a marked root holds Ghost-ALICE files that the installer replaces.
        self.assertEqual(walk_installed_copy_files(self.skills_dir), [managed / "SKILL.md"])

    def test_walker_tracks_a_replaced_root_without_its_marker(self):
        replaced = self.skills_dir / "task-router"
        replaced.mkdir()
        (replaced / "SKILL.md").write_text("managed", encoding="utf-8")
        own = self.skills_dir / "my-notes"
        own.mkdir()
        (own / "SKILL.md").write_text("the user's own skill", encoding="utf-8")

        # A root this install replaces is checked even when its marker is gone.
        self.assertEqual(walk_installed_copy_files(self.skills_dir, ["task-router"]), [replaced / "SKILL.md"])

    def test_app_synced_change_never_enters_pending_store(self):
        synced_file = self.skills_dir / "synced" / "bucket" / "pptx" / "SKILL.md"
        synced_file.parent.mkdir(parents=True)
        synced_file.write_text("version 1", encoding="utf-8")
        snap = self.root / "snapshot.json"
        capture_snapshot(snap, walk_installed_copy_files(self.skills_dir), "claude", skills_dir=self.skills_dir)

        synced_file.write_text("version 2 from the app", encoding="utf-8")

        self.assertEqual(collect_external_changes(snap, walk_installed_copy_files(self.skills_dir), skills_dir=self.skills_dir), [])


if __name__ == "__main__":
    unittest.main()
