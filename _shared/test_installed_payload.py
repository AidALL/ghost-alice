"""installed_payload: the install record's schema, read, the one writer under the install lock, and the one-time migration."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_lock  # noqa: E402
import installed_payload  # noqa: E402


def _hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(**overrides) -> dict:
    entry = {"name": "alpha", "kind": "skill", "mode": "link", "provider": "core", "source": "/src/alpha",
             "source_commit": "c0ffee", "used_by": ["claude"], "link_target": "/src/alpha",
             "installed_at": "2026-10-20T00:00:00+00:00", "baseline": "recorded"}
    entry.update(overrides)
    return entry


class RecordCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.ghost = self.home / ".ghost-alice"
        self.ghost.mkdir()
        self.record = self.ghost / "installed-payload.json"

    def tearDown(self):
        self.temp.cleanup()

    def hold_lock(self) -> None:
        install_lock.acquire_lock(self.ghost / "install.lock", owner="installer:install:claude:test")
        self.addCleanup(install_lock.release_lock, self.ghost / "install.lock")


class SchemaAndWriteTests(RecordCase):
    def test_the_record_lives_in_the_ghost_root_and_reads_none_when_absent(self):
        self.assertEqual(installed_payload.path(self.ghost), self.record)
        self.assertIsNone(installed_payload.read(self.ghost))

    def test_update_writes_a_version_one_record_with_only_the_sections_it_was_given(self):
        self.hold_lock()
        target = str(self.home / ".claude" / "skills" / "alpha")
        written = installed_payload.update(self.ghost, {
            "targets": {target: _entry()},
            "source": {"root": "/src", "commit": "c0ffee", "dirty": "clean", "hooks_path": "hooks"},
        })
        on_disk = json.loads(self.record.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, written)
        self.assertEqual(installed_payload.read(self.ghost), on_disk)
        self.assertEqual(on_disk["schema_version"], 1)
        self.assertEqual(on_disk["targets"], {target: _entry()})
        self.assertEqual(on_disk["source"]["hooks_path"], "hooks")
        for absent in ("installer", "last_transaction", "addons", "adapters", "host_changes", "service"):
            self.assertNotIn(absent, on_disk)

    def test_update_changes_only_the_given_sections_and_target_paths(self):
        self.hold_lock()
        installed_payload.update(self.ghost, {
            "targets": {"/a": _entry(name="a"), "/b": _entry(name="b")},
            "installer": {"commit": "old", "schema_version": 1},
            "source": {"root": "/src", "commit": "old", "dirty": "clean", "hooks_path": "hooks"},
        })
        installed_payload.update(self.ghost, {"targets": {"/b": _entry(name="b", link_target="/new")},
                                              "installer": {"commit": "new", "schema_version": 1}})
        record = installed_payload.read(self.ghost)
        self.assertEqual(record["targets"]["/a"], _entry(name="a"))
        self.assertEqual(record["targets"]["/b"]["link_target"], "/new")
        self.assertEqual(record["installer"], {"commit": "new", "schema_version": 1})
        self.assertEqual(record["source"]["commit"], "old")

    def test_the_hosts_that_use_one_path_merge_into_one_entry(self):
        self.hold_lock()
        runtime = str(self.ghost / "runtime" / "current" / "_shared")
        installed_payload.update(self.ghost, {"targets": {runtime: _entry(kind="runtime", used_by=["codex"])}})
        installed_payload.update(self.ghost, {"targets": {runtime: _entry(kind="runtime", used_by=["claude"])}})
        self.assertEqual(installed_payload.read(self.ghost)["targets"][runtime]["used_by"], ["claude", "codex"])

    def test_update_replaces_the_file_through_a_temporary_file_in_the_same_folder(self):
        self.hold_lock()
        renames = []
        real_replace = os.replace

        def recording_replace(src, dst):
            renames.append((Path(src), Path(dst)))
            return real_replace(src, dst)

        with mock.patch.object(installed_payload.os, "replace", side_effect=recording_replace):
            installed_payload.update(self.ghost, {"targets": {"/a": _entry()}})
        self.assertEqual(len(renames), 1)
        temporary, final = renames[0]
        self.assertEqual(final, self.record)
        self.assertEqual(temporary.parent, self.ghost)
        self.assertNotEqual(temporary, final)
        self.assertEqual(sorted(p.name for p in self.ghost.iterdir()), ["install.lock", "installed-payload.json"])

    def test_a_failed_write_keeps_the_previous_record_and_leaves_no_temporary_file(self):
        self.hold_lock()
        installed_payload.update(self.ghost, {"targets": {"/a": _entry()}})
        before = self.record.read_bytes()
        with mock.patch.object(installed_payload.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                installed_payload.update(self.ghost, {"targets": {"/b": _entry()}})
        self.assertEqual(self.record.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.ghost.iterdir()), ["install.lock", "installed-payload.json"])

    def test_an_unknown_section_is_refused(self):
        self.hold_lock()
        with self.assertRaises(installed_payload.RecordError):
            installed_payload.update(self.ghost, {"host_changes": {}})
        self.assertFalse(self.record.exists())

    def test_an_unknown_schema_version_is_refused_by_read_and_update(self):
        self.hold_lock()
        self.record.write_text(json.dumps({"schema_version": 2, "targets": {}}), encoding="utf-8")
        with self.assertRaisesRegex(installed_payload.RecordError, "schema_version"):
            installed_payload.read(self.ghost)
        with self.assertRaises(installed_payload.RecordError):
            installed_payload.update(self.ghost, {"targets": {"/a": _entry()}})
        self.assertEqual(json.loads(self.record.read_text(encoding="utf-8"))["schema_version"], 2)
        self.record.write_text("{broken", encoding="utf-8")
        with self.assertRaises(installed_payload.RecordError):
            installed_payload.read(self.ghost)


class LockOwnerTests(RecordCase):
    def test_update_without_the_install_lock_is_refused(self):
        with self.assertRaises(installed_payload.NotLockHolder):
            installed_payload.update(self.ghost, {"targets": {"/a": _entry()}})
        (self.ghost / "install.lock").write_text(json.dumps({"owner": "other", "pid": 424242}), encoding="utf-8")
        with self.assertRaises(installed_payload.NotLockHolder):
            installed_payload.update(self.ghost, {"targets": {"/a": _entry()}})
        (self.ghost / "install.lock").write_text("{broken", encoding="utf-8")
        with self.assertRaises(installed_payload.NotLockHolder):
            installed_payload.update(self.ghost, {"targets": {"/a": _entry()}})
        self.assertFalse(self.record.exists())

    def test_a_child_of_the_lock_holder_may_write(self):
        (self.ghost / "install.lock").write_text(json.dumps({"owner": "installer", "pid": os.getppid()}),
                                                 encoding="utf-8")
        installed_payload.update(self.ghost, {"targets": {"/a": _entry()}})
        self.assertIn("/a", installed_payload.read(self.ghost)["targets"])

    def test_restore_puts_back_a_saved_copy_or_removes_a_record_that_did_not_exist(self):
        self.hold_lock()
        installed_payload.update(self.ghost, {"targets": {"/a": _entry()}})
        saved = self.home / "saved.json"
        saved.write_bytes(self.record.read_bytes())
        installed_payload.update(self.ghost, {"targets": {"/b": _entry()}})
        installed_payload.restore(self.ghost, saved)
        self.assertEqual(self.record.read_bytes(), saved.read_bytes())
        installed_payload.restore(self.ghost, None)
        self.assertFalse(self.record.exists())
        installed_payload.restore(self.ghost, None)
        self.assertFalse(self.record.exists())

    def test_restore_without_the_install_lock_is_refused(self):
        saved = self.home / "saved.json"
        saved.write_text(json.dumps({"schema_version": 1, "targets": {}}), encoding="utf-8")
        with self.assertRaises(installed_payload.NotLockHolder):
            installed_payload.restore(self.ghost, saved)
        self.assertFalse(self.record.exists())


class MigrationTests(RecordCase):
    """A home that only the earlier installer wrote: install-state, markers, snapshot and sidecars."""

    def setUp(self):
        super().setUp()
        self.claude_skills = self.home / ".claude" / "skills"
        self.codex_skills = self.home / ".agents" / "skills"
        self.source = self.home / "src"
        for name in ("_shared", "alpha", "beta"):
            (self.source / name).mkdir(parents=True)
            (self.source / name / "SKILL.md").write_text(name, encoding="utf-8")
        self.addon = self.home / "addon-src" / "noop"
        self.addon.mkdir(parents=True)
        self.claude_skills.mkdir(parents=True)
        for name in ("_shared", "alpha"):
            (self.claude_skills / name).symlink_to(self.source / name, target_is_directory=True)
        (self.claude_skills / "noop").symlink_to(self.addon, target_is_directory=True)
        for name in ("_shared", "alpha", "beta", "noop"):
            (self.codex_skills / name).mkdir(parents=True)
            (self.codex_skills / name / "SKILL.md").write_text(f"{name} as installed", encoding="utf-8")
        self.command = self.home / ".claude" / "commands" / "noopcmd.md"
        self.command.parent.mkdir(parents=True)
        self.command.write_text("command", encoding="utf-8")
        self.write_state("claude", "2026-10-01T00:00:00+00:00", "aaa111", [
            self.state_target("_shared", self.source / "_shared", self.claude_skills / "_shared", "symlink"),
            self.state_target("alpha", self.source / "alpha", self.claude_skills / "alpha", "symlink"),
            dict(self.state_target("noop", self.addon, self.claude_skills / "noop", "symlink"),
                 addon_id="noop", owner="addon", origin="local"),
            self.state_target("noopcmd", self.addon, self.command, "copy"),
            self.state_target("gone", self.source / "gone", self.claude_skills / "gone", "missing"),
        ], hook_after="hooks")
        self.write_state("codex", "2026-10-02T00:00:00+00:00", "bbb222", [
            self.state_target("_shared", self.source / "_shared", self.codex_skills / "_shared", "copy"),
            self.state_target("alpha", self.source / "alpha", self.codex_skills / "alpha", "copy"),
            self.state_target("beta", self.source / "beta", self.codex_skills / "beta", "copy-fallback"),
            self.state_target("noop", self.addon, self.codex_skills / "noop", "copy"),
            self.state_target("stop", self.addon, self.codex_skills / "noop" / "adapters" / "stop.py", "copy"),
        ])
        self.marker = self.codex_skills / "_shared" / ".ghost-alice-install.json"
        self.marker.write_text(json.dumps({
            "schema_version": 1, "managed_by": "Ghost-ALICE", "asset_id": "_shared", "install_mode": "copy",
            "content_hashes": {"SKILL.md": _hex(b"_shared from the marker")}}), encoding="utf-8")
        self.snapshot = self.ghost / "pending-merges" / "codex" / "snapshot.json"
        self.snapshot.parent.mkdir(parents=True)
        alpha_file = str(self.codex_skills / "alpha" / "SKILL.md")
        self.snapshot.write_text(json.dumps({"version": 2, "files": {
            alpha_file: _hex(b"alpha from the snapshot"),
            str(self.codex_skills / "alpha" / "references" / "deep.md"): _hex(b"deep"),
            str(self.codex_skills / "alphabet" / "SKILL.md"): _hex(b"another folder"),
        }}), encoding="utf-8")
        self.write_sidecar("claude", "noop", [
            {"kind": "skill", "name": "noop", "target": str(self.claude_skills / "noop"), "install_mode": "symlink"},
            {"kind": "command", "name": "noopcmd", "target": str(self.command), "install_mode": "copy"},
        ])
        self.write_sidecar("codex", "noop", [
            {"kind": "skill", "name": "noop", "target": str(self.codex_skills / "noop"), "install_mode": "copy"},
            {"kind": "adapter", "name": "stop", "target": str(self.codex_skills / "noop" / "adapters" / "stop.py"),
             "install_mode": "copy"},
        ])

    @staticmethod
    def state_target(name, source, dest, mode):
        return {"target_name": name, "source_path": Path(source).as_posix(), "dest_path": Path(dest).as_posix(),
                "install_mode": mode, "target_tree_hash": "old-hash", "managed_markers": [],
                "installed_at": "2026-10-01T00:00:00+00:00"}

    def write_state(self, host, installed_at, head, targets, hook_after=None):
        changes = []
        if hook_after:
            changes.append({"kind": "source_repo_hook_path", "after": hook_after})
        state = {"schema_version": 1, "platform": host, "installed_at": installed_at,
                 "source_root": self.source.as_posix(), "source_branch": "main", "source_head": head,
                 "source_dirty_state": "clean", "targets": targets, "system_env_changes": changes}
        path = self.ghost / "install-state" / f"{host}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")

    def write_sidecar(self, host, addon_id, provided):
        path = self.ghost / "addons" / host / f"{addon_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": "1.0", "addon_id": addon_id, "platform": host,
                                    "provided": provided}), encoding="utf-8")

    def old_records(self) -> dict[Path, bytes]:
        paths = [*(self.ghost / "install-state").glob("*.json"), self.marker, self.snapshot,
                 *(self.ghost / "addons").rglob("*.json")]
        return {path: path.read_bytes() for path in paths}

    def migrate(self):
        return installed_payload.migrate(self.ghost, ("claude", "codex"), installer_commit="ccc333")

    def test_migration_builds_one_entry_per_installed_path_from_the_earlier_records(self):
        self.hold_lock()
        before = self.old_records()
        summary = self.migrate()
        record = installed_payload.read(self.ghost)
        targets = record["targets"]
        self.assertEqual(sorted(targets), sorted(p.as_posix() for p in (
            self.claude_skills / "_shared", self.claude_skills / "alpha", self.claude_skills / "noop", self.command,
            self.codex_skills / "_shared", self.codex_skills / "alpha", self.codex_skills / "beta",
            self.codex_skills / "noop")))
        self.assertEqual(summary, {"targets": 8, "migrated": 5, "unknown": 3})
        # The earlier records stay byte-for-byte as they were.
        self.assertEqual(self.old_records(), before)
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["installer"], {"commit": "ccc333", "schema_version": 1})
        # The latest install-state names the source.
        self.assertEqual(record["source"], {"root": self.source.as_posix(), "commit": "bbb222", "dirty": "clean",
                                            "hooks_path": None})
        for absent in ("last_transaction", "addons", "adapters", "host_changes", "service"):
            self.assertNotIn(absent, record)

    def test_a_link_records_where_it_points_now(self):
        self.hold_lock()
        self.migrate()
        entry = installed_payload.read(self.ghost)["targets"][(self.claude_skills / "alpha").as_posix()]
        self.assertEqual(entry, {
            "name": "alpha", "kind": "skill", "mode": "link", "provider": "core",
            "source": (self.source / "alpha").as_posix(), "source_commit": "aaa111", "used_by": ["claude"],
            "link_target": str(self.source / "alpha"), "installed_at": "2026-10-01T00:00:00+00:00",
            "baseline": "migrated"})
        self.assertEqual(installed_payload.read(self.ghost)["targets"][(self.claude_skills / "_shared").as_posix()]
                         ["kind"], "support")

    def test_a_copy_takes_its_baseline_from_the_marker_then_the_snapshot_then_nothing(self):
        self.hold_lock()
        self.migrate()
        targets = installed_payload.read(self.ghost)["targets"]
        shared = targets[(self.codex_skills / "_shared").as_posix()]
        self.assertEqual((shared["mode"], shared["kind"], shared["baseline"]), ("copy", "support", "migrated"))
        self.assertEqual(shared["files"], {"SKILL.md": "sha256:" + _hex(b"_shared from the marker")})
        alpha = targets[(self.codex_skills / "alpha").as_posix()]
        self.assertEqual(alpha["baseline"], "migrated")
        self.assertEqual(alpha["files"], {"SKILL.md": "sha256:" + _hex(b"alpha from the snapshot"),
                                          "references/deep.md": "sha256:" + _hex(b"deep")})
        # No earlier fingerprint: the current files are never adopted as the baseline.
        beta = targets[(self.codex_skills / "beta").as_posix()]
        self.assertEqual((beta["mode"], beta["baseline"]), ("copy", "unknown"))
        self.assertNotIn("files", beta)

    def test_the_provider_comes_from_the_install_state_or_a_sidecar(self):
        self.hold_lock()
        self.migrate()
        targets = installed_payload.read(self.ghost)["targets"]
        self.assertEqual(targets[(self.claude_skills / "noop").as_posix()]["provider"], "addon:noop")
        self.assertEqual(targets[(self.codex_skills / "noop").as_posix()]["provider"], "addon:noop")
        command = targets[self.command.as_posix()]
        self.assertEqual((command["provider"], command["kind"], command["mode"]), ("addon:noop", "command", "copy"))
        self.assertEqual(targets[(self.codex_skills / "alpha").as_posix()]["provider"], "core")
        # An adapter script is not an install location of its own, and a missing target was never installed.
        self.assertNotIn((self.codex_skills / "noop" / "adapters" / "stop.py").as_posix(), targets)
        self.assertNotIn((self.claude_skills / "gone").as_posix(), targets)

    def test_hosts_that_share_a_path_share_one_entry(self):
        self.hold_lock()
        shared_dir = self.home / "shared-skills" / "alpha"
        shared_dir.mkdir(parents=True)
        for host in ("claude", "codex"):
            state = json.loads((self.ghost / "install-state" / f"{host}.json").read_text(encoding="utf-8"))
            state["targets"] = [self.state_target("alpha", self.source / "alpha", shared_dir, "copy")]
            (self.ghost / "install-state" / f"{host}.json").write_text(json.dumps(state), encoding="utf-8")
        self.migrate()
        targets = installed_payload.read(self.ghost)["targets"]
        self.assertEqual(list(targets), [shared_dir.as_posix()])
        self.assertEqual(targets[shared_dir.as_posix()]["used_by"], ["claude", "codex"])

    def test_migration_runs_once_and_only_over_an_earlier_install(self):
        self.hold_lock()
        self.assertIsNotNone(self.migrate())
        written = self.record.read_bytes()
        self.assertIsNone(self.migrate())
        self.assertEqual(self.record.read_bytes(), written)
        self.record.unlink()
        for state in (self.ghost / "install-state").glob("*.json"):
            state.unlink()
        self.assertIsNone(self.migrate())
        self.assertFalse(self.record.exists())

    def test_install_states_with_unusual_install_times_still_name_the_source(self):
        # A time without an offset counts as UTC, and a value that is not a time counts as the earliest.
        self.hold_lock()
        for claude_time, codex_time in (("2026-10-01T00:00:00", "2026-10-03T00:00:00+00:00"),
                                        ("not a time", "2026-10-03T00:00:00+00:00"),
                                        ("2026-10-01T00:00:00+09:00", "2026-10-03T00:00:00")):
            with self.subTest(claude=claude_time, codex=codex_time):
                self.record.unlink(missing_ok=True)
                for host, installed_at in (("claude", claude_time), ("codex", codex_time)):
                    state_file = self.ghost / "install-state" / f"{host}.json"
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    state["installed_at"] = installed_at
                    state_file.write_text(json.dumps(state), encoding="utf-8")
                self.migrate()
                self.assertEqual(installed_payload.read(self.ghost)["source"]["commit"], "bbb222")

    def test_an_unreadable_install_state_contributes_no_entries(self):
        self.hold_lock()
        (self.ghost / "install-state" / "claude.json").write_text("{broken", encoding="utf-8")
        summary = self.migrate()
        targets = installed_payload.read(self.ghost)["targets"]
        self.assertEqual(summary["targets"], 4)
        self.assertTrue(all("codex" in entry["used_by"] for entry in targets.values()))

    def test_migration_writes_only_under_the_install_lock(self):
        with self.assertRaises(installed_payload.NotLockHolder):
            self.migrate()
        self.assertFalse(self.record.exists())


if __name__ == "__main__":
    unittest.main()
