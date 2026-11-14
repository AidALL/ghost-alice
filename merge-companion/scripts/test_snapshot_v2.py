"""Tests for merge-companion snapshot v2 behavior.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snapshot
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


class TestSnapshotPruning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "pending-merges" / "codex" / "snapshot.json"
        self.path.parent.mkdir(parents=True)
        self.target = self.root / "skills" / "alpha"

    def write_snapshot(self, version=2):
        paths = [self.target / "SKILL.md", self.target / "references" / "deep.md",
                 self.root / "skills" / "alphabet" / "SKILL.md",
                 self.root / "elsewhere" / "alpha" / "SKILL.md"]
        data = {"version": version, "platform": "codex", "captured_at": "unchanged",
                "files": {str(path): str(index) for index, path in enumerate(paths)},
                "metadata": {"keep": True}}
        if version == 2:
            data["file_records"] = {
                str(path): {"path": str(path), "sha256": str(index), "relative_path": "alpha/SKILL.md"}
                for index, path in enumerate(paths)}
            # Each mapping can contain a path absent from the other mapping.
            data["file_records"][str(self.target / "only-record.md")] = {"sha256": "extra"}
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def prune(self, targets=None, platform="codex"):
        self.assertTrue(callable(getattr(snapshot, "prune_snapshot", None)),
                        "snapshot must own cleanup of both baseline path mappings")
        return snapshot.prune_snapshot(self.path, targets or [("skill", self.target)], platform,
                                       provider="addon-x")

    def test_prunes_both_maps_for_v1_and_v2_without_name_prefix_or_relative_metadata_matches(self):
        for version in (1, 2):
            with self.subTest(version=version):
                original = self.write_snapshot(version)
                other_host = self.path.parent.parent / "claude" / "snapshot.json"
                other_host.parent.mkdir(exist_ok=True)
                other_host.write_bytes(self.path.read_bytes())
                saved = other_host.read_bytes()
                self.assertTrue(self.prune())
                result = load_snapshot(self.path)
                keep = {str(self.root / "skills" / "alphabet" / "SKILL.md"),
                        str(self.root / "elsewhere" / "alpha" / "SKILL.md")}
                for field in ("files", "file_records") if version == 2 else ("files",):
                    self.assertEqual(result[field], {key: value for key, value in original[field].items()
                                                     if key in keep})
                self.assertEqual(result["metadata"], original["metadata"])
                self.assertEqual(result["captured_at"], original["captured_at"])
                self.assertEqual(other_host.read_bytes(), saved)
                after = self.path.read_bytes()
                self.assertFalse(self.prune())
                self.assertEqual(self.path.read_bytes(), after)

    def test_file_targets_remove_exact_path_and_directory_targets_include_children(self):
        command = self.root / "commands" / "alpha.md"
        paths = [command, command / "child", self.root / "commands" / "alpha.md.extra"]
        self.path.write_text(json.dumps({"version": 1, "platform": "codex",
                                        "files": {str(path): "hash" for path in paths}}))
        self.prune([("command", command)])
        self.assertEqual(load_snapshot(self.path)["files"], {str(path): "hash" for path in paths[1:]})

    def test_missing_snapshot_is_a_noop_and_legacy_missing_platform_is_supported(self):
        self.assertFalse(self.prune())
        self.assertFalse(self.path.exists())
        self.path.write_text(json.dumps({"version": 1, "files": {str(self.target / "a"): "hash"}}))
        self.prune()
        self.assertEqual(load_snapshot(self.path)["files"], {})

    def test_pruning_uses_destination_without_following_symlinks_or_dotdot_outside_it(self):
        source = self.root / "source" / "alpha"
        source.mkdir(parents=True)
        self.target.parent.mkdir()
        self.target.symlink_to(source, target_is_directory=True)
        destination_file = str(self.target / "SKILL.md")
        source_file = str(source / "SKILL.md")
        escaped_file = str(self.target / ".." / "outside.md")
        self.path.write_text(json.dumps({"version": 1, "files": {
            destination_file: "installed", source_file: "source", escaped_file: "outside"}}))
        self.prune()
        self.assertEqual(load_snapshot(self.path)["files"], {source_file: "source", escaped_file: "outside"})

    def test_invalid_target_kinds_and_relative_paths_preserve_the_snapshot(self):
        self.write_snapshot()
        before = self.path.read_bytes()
        for targets in ([("unknown", self.target)], [("skill", "relative/alpha")]):
            with self.subTest(targets=targets), self.assertRaises(SnapshotError):
                self.prune(targets)
            self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_records_and_platform_mismatch_leave_bytes_unchanged(self):
        valid = self.write_snapshot()
        malformed = ["{broken", "[]", json.dumps({**valid, "version": 9}),
                     json.dumps({**valid, "platform": "claude"}),
                     json.dumps({**valid, "files": []}), json.dumps({**valid, "file_records": []}),
                     json.dumps({**valid, "file_records": {str(self.target / "a"): None}})]
        for raw in malformed:
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding="utf-8")
                before = self.path.read_bytes()
                with self.assertRaises(SnapshotError):
                    self.prune()
                self.assertEqual(self.path.read_bytes(), before)

    def test_failed_atomic_write_preserves_record_and_retry_completes(self):
        self.write_snapshot()
        before = self.path.read_bytes()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.prune()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertTrue(self.prune())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(Path(__file__).with_name("snapshot_cli.py")), *args],
                              cwd=self.root, text=True, capture_output=True)

    def test_checkout_cli_prunes_without_installed_skill_or_capture_arguments(self):
        self.write_snapshot()
        args = ["--prune", "--output", str(self.path), "--platform", "codex",
                "--prune-target", "skill", str(self.target), "--provider", "addon-x"]
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(self.target / "SKILL.md"), load_snapshot(self.path)["files"])
        self.assertNotIn(str(self.target / "only-record.md"), load_snapshot(self.path)["file_records"])
        after = self.path.read_bytes()
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.path.read_bytes(), after)

    def test_capture_cli_keeps_existing_arguments(self):
        skill_file = self.target / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("installed skill")
        (self.target / ".ghost-alice-install.json").write_text('{"asset_id": "alpha"}')
        result = self.run_cli("--output", str(self.path), "--platform", "codex",
                              "--skills-dir", str(self.target.parent))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(skill_file), load_snapshot(self.path)["files"])

    def test_checkout_cli_reports_malformed_snapshot_without_changing_it(self):
        self.path.write_text("{broken")
        before = self.path.read_bytes()
        result = self.run_cli("--prune", "--output", str(self.path), "--platform", "codex",
                              "--prune-target", "skill", str(self.target))
        self.assertEqual(result.returncode, 1)
        self.assertIn("snapshot cleanup failed", result.stderr)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
