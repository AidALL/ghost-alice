import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "merge-companion" / "scripts" / "install_verifier.py"


class InstallPostflightValidationTest(unittest.TestCase):
    def test_failed_postflight_preserves_inventory_through_corrected_retry(self) -> None:
        for previous_install in (False, True):
            with self.subTest(previous_install=previous_install), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, dest, state = root / "source", root / "alpha", root / "state" / "codex.json"
                source.mkdir()
                dest.mkdir()
                (source / "SKILL.md").write_text("expected\n", encoding="utf-8")
                (dest / "SKILL.md").write_text("partial\n", encoding="utf-8")
                beta = dict(target_name="beta", source_path=str(root / "old-source"),
                            dest_path=str(root / "beta"), install_mode="copy", target_tree_hash="original-baseline",
                            installed_at="2026-01-01T00:00:00Z")
                changes = [dict(kind="source_repo_hook_path", repo_root=str(root / "repo"),
                                before_present=True, before="user-hooks", after="managed-hooks")]
                if previous_install:
                    state.parent.mkdir()
                    state.write_text(json.dumps(dict(schema_version=1, platform="codex", targets=[beta],
                        installed_at="2026-01-01T00:00:00Z", system_env_changes=changes)), encoding="utf-8")
                args = ("--platform", "codex", "--state-root", str(state.parent),
                        "--target", "alpha", str(source), str(dest), "copy")
                failed = self._run_verifier(*args)
                self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
                observed = json.loads(state.read_text(encoding="utf-8"))
                self.assertEqual(observed.get("targets"), [beta] if previous_install else [])
                self.assertEqual(observed.get("system_env_changes"), changes if previous_install else [])
                self.assertEqual(observed["failures"][0]["reason"], "tree-hash-mismatch")
                if previous_install:
                    self.assertEqual(observed["installed_at"], "2026-01-01T00:00:00Z")
                (dest / "SKILL.md").write_text("expected\n", encoding="utf-8")
                corrected = self._run_verifier(*args)
                self.assertEqual(corrected.returncode, 0, corrected.stdout + corrected.stderr)
                written = subprocess.run([sys.executable, str(REPO_ROOT / "_shared" / "install_state_writer.py"),
                    "codex", str(source), "main", "fixture", "clean", str(state),
                    "alpha", str(source), str(dest), "copy"], capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(written.returncode, 0, written.stdout + written.stderr)
                current = json.loads(state.read_text(encoding="utf-8"))
                rows = {row["target_name"]: row for row in current["targets"]}
                self.assertEqual(set(rows), {"alpha", "beta"} if previous_install else {"alpha"})
                if previous_install:
                    self.assertEqual(rows["beta"], beta)
                    self.assertEqual(current["system_env_changes"], changes)
                self.assertNotIn("partial_failure", current)
                self.assertNotIn("failures", current)

    def test_failed_postflight_preserves_invalid_current_inventory_bytes(self) -> None:
        for body in ('{broken', '[]', '{"targets": null}', '{"platform": "claude", "targets": []}'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state = root / "state" / "codex.json"
                state.parent.mkdir()
                state.write_text(body, encoding="utf-8")
                before = state.read_bytes()
                failed = self._run_verifier("--platform", "codex", "--state-root", str(state.parent),
                    "--target", "alpha", str(root / "source"), str(root / "dest"), "missing")
                self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
                self.assertEqual(state.read_bytes(), before)
                self.assertIn("partial state could not be recorded", failed.stderr)

    def test_verifier_rejects_missing_and_unsupported_targets(self) -> None:
        cases = [("missing", True, False, "missing-target"),
                 ("copy", False, False, "missing-source"),
                 ("copy", False, True, "missing-source"),
                 ("copy-fallback", True, False, "missing-target"),
                 ("unrecognized", True, True, "unsupported-install-mode")]
        for mode, source_exists, dest_exists, reason in cases:
            with self.subTest(mode=mode, source=source_exists, dest=dest_exists), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, dest, state = root / "source", root / "dest", root / "state"
                if source_exists:
                    source.mkdir()
                if dest_exists:
                    dest.mkdir()
                result = self._run_verifier("--platform", "codex", "--state-root", str(state),
                                            "--target", "expected", str(source), str(dest), mode)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                record = json.loads((state / "codex.json").read_text())
                self.assertEqual(record["status"], "partial_failure")
                self.assertEqual(record["failures"][0]["reason"], reason)

    def _make_directory_symlink_or_skip(self, source: Path, dest: Path) -> None:
        try:
            os.symlink(source, dest, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"directory symlink unavailable: {exc}")

    def _make_junction_or_symlink_or_skip(self, source: Path, dest: Path) -> None:
        if os.name != "nt":
            self._make_directory_symlink_or_skip(source, dest)
            return

        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dest), str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            self.skipTest(f"junction unavailable: {result.stderr or result.stdout}")

    def _run_verifier(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(VERIFY_SCRIPT.exists(), "install verifier script is missing")
        return subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_verifier_passes_matching_copy_target_without_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "task-router"
            dest = root / "dest" / "task-router"
            state_root = root / "state"
            source.mkdir(parents=True)
            dest.mkdir(parents=True)
            (source / "SKILL.md").write_text("same\n", encoding="utf-8")
            (dest / "SKILL.md").write_text("same\n", encoding="utf-8")

            result = self._run_verifier(
                "--platform",
                "codex",
                "--state-root",
                str(state_root),
                "--target",
                "task-router",
                str(source),
                str(dest),
                "copy",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse((state_root / "codex.json").exists())

    def test_verifier_fails_mismatched_copy_target_and_records_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "task-router"
            dest = root / "dest" / "task-router"
            state_root = root / "state"
            source.mkdir(parents=True)
            dest.mkdir(parents=True)
            (source / "SKILL.md").write_text("repo copy\n", encoding="utf-8")
            (dest / "SKILL.md").write_text("partial copy\n", encoding="utf-8")

            result = self._run_verifier(
                "--platform",
                "codex",
                "--state-root",
                str(state_root),
                "--target",
                "task-router",
                str(source),
                str(dest),
                "copy",
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            state = json.loads((state_root / "codex.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "partial_failure")
            self.assertTrue(state["partial_failure"])
            self.assertEqual(state["failures"][0]["target_name"], "task-router")
            self.assertEqual(state["failures"][0]["reason"], "tree-hash-mismatch")
            self.assertNotEqual(
                state["failures"][0]["source_tree_hash"],
                state["failures"][0]["target_tree_hash"],
            )

    def test_verifier_ignores_generated_cache_files_in_copy_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "_shared"
            dest = root / "dest" / "_shared"
            state_root = root / "state"
            source.mkdir(parents=True)
            dest.mkdir(parents=True)
            (source / "install_hooks.py").write_text("print('same')\n", encoding="utf-8")
            (dest / "install_hooks.py").write_text("print('same')\n", encoding="utf-8")
            (source / "__pycache__").mkdir()
            (source / "__pycache__" / "install_hooks.cpython-312.pyc").write_bytes(b"cache")
            (source / "helper.pyc").write_bytes(b"cache")
            (dest / "__pycache__").mkdir()
            (dest / "__pycache__" / "install_hooks.cpython-314.pyc").write_bytes(b"other-cache")
            (dest / ".pytest_cache").mkdir()
            (dest / ".pytest_cache" / "state").write_text("generated\n", encoding="utf-8")

            result = self._run_verifier(
                "--platform",
                "codex",
                "--state-root",
                str(state_root),
                "--target",
                "_shared",
                str(source),
                str(dest),
                "copy",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse((state_root / "codex.json").exists())

    def test_verifier_detects_mismatched_nested_symlink_directory_in_copy_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "task-router"
            dest = root / "dest" / "task-router"
            state_root = root / "state"
            source_link_target = root / "source-linked-dir"
            dest_link_target = root / "dest-linked-dir"
            source.mkdir(parents=True)
            dest.mkdir(parents=True)
            source_link_target.mkdir()
            dest_link_target.mkdir()
            (source / "SKILL.md").write_text("same\n", encoding="utf-8")
            (dest / "SKILL.md").write_text("same\n", encoding="utf-8")
            self._make_directory_symlink_or_skip(source_link_target, source / "linked")
            self._make_directory_symlink_or_skip(dest_link_target, dest / "linked")

            result = self._run_verifier(
                "--platform",
                "codex",
                "--state-root",
                str(state_root),
                "--target",
                "task-router",
                str(source),
                str(dest),
                "copy",
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            state = json.loads((state_root / "codex.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "partial_failure")
            self.assertEqual(state["failures"][0]["reason"], "tree-hash-mismatch")

    def test_verifier_passes_matching_symlink_target_without_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "task-router"
            dest = root / "dest" / "task-router"
            state_root = root / "state"
            source.mkdir(parents=True)
            dest.parent.mkdir(parents=True)
            (source / "SKILL.md").write_text("same\n", encoding="utf-8")
            self._make_directory_symlink_or_skip(source, dest)

            result = self._run_verifier(
                "--platform",
                "codex",
                "--state-root",
                str(state_root),
                "--target",
                "task-router",
                str(source),
                str(dest),
                "symlink",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse((state_root / "codex.json").exists())

    def test_verifier_fails_mismatched_symlink_target_and_records_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "task-router"
            wrong_source = root / "source" / "other"
            dest = root / "dest" / "task-router"
            state_root = root / "state"
            source.mkdir(parents=True)
            wrong_source.mkdir(parents=True)
            dest.parent.mkdir(parents=True)
            (source / "SKILL.md").write_text("expected\n", encoding="utf-8")
            (wrong_source / "SKILL.md").write_text("wrong\n", encoding="utf-8")
            self._make_directory_symlink_or_skip(wrong_source, dest)

            result = self._run_verifier(
                "--platform",
                "codex",
                "--state-root",
                str(state_root),
                "--target",
                "task-router",
                str(source),
                str(dest),
                "symlink",
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            state = json.loads((state_root / "codex.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "partial_failure")
            self.assertTrue(state["partial_failure"])
            self.assertEqual(state["failures"][0]["target_name"], "task-router")
            self.assertEqual(state["failures"][0]["reason"], "link-target-mismatch")
            self.assertEqual(state["failures"][0]["expected_link_target"], source.resolve().as_posix())
            self.assertEqual(state["failures"][0]["actual_link_target"], wrong_source.resolve().as_posix())

    def test_verifier_fails_symlink_mode_when_target_is_regular_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "task-router"
            dest = root / "dest" / "task-router"
            state_root = root / "state"
            source.mkdir(parents=True)
            dest.mkdir(parents=True)
            (source / "SKILL.md").write_text("expected\n", encoding="utf-8")
            (dest / "SKILL.md").write_text("regular directory\n", encoding="utf-8")

            result = self._run_verifier(
                "--platform",
                "codex",
                "--state-root",
                str(state_root),
                "--target",
                "task-router",
                str(source),
                str(dest),
                "symlink",
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            state = json.loads((state_root / "codex.json").read_text(encoding="utf-8"))
            self.assertEqual(state["failures"][0]["target_name"], "task-router")
            self.assertEqual(state["failures"][0]["reason"], "link-target-mismatch")
            self.assertEqual(state["failures"][0]["expected_link_target"], source.resolve().as_posix())
            self.assertEqual(state["failures"][0]["actual_link_target"], dest.resolve().as_posix())

    def test_verifier_fails_mismatched_junction_target_and_records_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "task-router"
            wrong_source = root / "source" / "other"
            dest = root / "dest" / "task-router"
            state_root = root / "state"
            source.mkdir(parents=True)
            wrong_source.mkdir(parents=True)
            dest.parent.mkdir(parents=True)
            (source / "SKILL.md").write_text("expected\n", encoding="utf-8")
            (wrong_source / "SKILL.md").write_text("wrong\n", encoding="utf-8")
            self._make_junction_or_symlink_or_skip(wrong_source, dest)

            result = self._run_verifier(
                "--platform",
                "codex",
                "--state-root",
                str(state_root),
                "--target",
                "task-router",
                str(source),
                str(dest),
                "junction",
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            state = json.loads((state_root / "codex.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "partial_failure")
            self.assertTrue(state["partial_failure"])
            self.assertEqual(state["failures"][0]["target_name"], "task-router")
            self.assertEqual(state["failures"][0]["reason"], "link-target-mismatch")
            self.assertEqual(state["failures"][0]["expected_link_target"], source.resolve().as_posix())
            self.assertEqual(state["failures"][0]["actual_link_target"], wrong_source.resolve().as_posix())


if __name__ == "__main__":
    unittest.main()
