import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
VERIFY_SCRIPT = REPO_ROOT / "merge-companion" / "scripts" / "install_verifier.py"


class InstallPostflightValidationTest(unittest.TestCase):
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

    def test_installers_run_verifier_before_snapshot_and_manifest(self) -> None:
        bash_body = installer_bash_source()
        bash_verify_call = '_verify_install_after_copy "$SKILLS_DIR"'
        self.assertIn(bash_verify_call, bash_body)
        self.assertLess(
            bash_body.index(bash_verify_call),
            bash_body.index("run_logged_if_compact _run_snapshot_after_install"),
        )
        self.assertLess(
            bash_body.index(bash_verify_call),
            bash_body.index("write_install_state_manifest \"$SKILLS_DIR\""),
        )

        ps_body = installer_ps1_source()
        ps_verify_call = "Invoke-PostflightInstallVerification -TargetPlatform $Platform"
        self.assertIn(ps_verify_call, ps_body)
        self.assertLess(
            ps_body.index(ps_verify_call),
            ps_body.index("Invoke-SnapshotAfterInstall -TargetPlatform $Platform"),
        )
        self.assertLess(
            ps_body.index(ps_verify_call),
            ps_body.index("Write-InstallStateManifest -TargetPlatform $Platform"),
        )


if __name__ == "__main__":
    unittest.main()
