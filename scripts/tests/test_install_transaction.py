import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_LOCK = REPO_ROOT / "_shared" / "install_lock.py"
CLEANUP_FALSE_POSITIVE_LEGACY = REPO_ROOT / "merge-companion" / "scripts" / "cleanup_false_positive_legacy.py"
SHARED = REPO_ROOT / "_shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

from installer_assets import write_ownership_marker



def _load_install_lock():
    if not INSTALL_LOCK.exists():
        raise AssertionError("_shared/install_lock.py must exist")
    spec = importlib.util.spec_from_file_location("install_lock_under_test", INSTALL_LOCK)
    if spec is None or spec.loader is None:
        raise AssertionError("install_lock.py must be importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class InstallTransactionTest(unittest.TestCase):
    def _managed_copy(self, source: Path, dest: Path, *, asset_id: str) -> Path:
        shutil.copytree(source, dest)
        write_ownership_marker(
            dest,
            platform="codex",
            asset_id=asset_id,
            source_repo="/old/ghost-alice",
            source_commit="old-head",
            install_mode="copy",
        )
        return dest

    def test_cleanup_pending_apply_marks_repo_junction_false_positive_decided(self) -> None:
        if not sys.platform.startswith("win"):
            self.skipTest("Windows junction behavior is platform-specific")
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("PowerShell executable is required for junction cleanup test")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo_shared = repo / "_shared"
            repo_shared.mkdir(parents=True)
            source_file = repo_shared / "install_doctor.py"
            source_file.write_text("# repo-managed source\n", encoding="utf-8")
            skills = root / ".claude" / "skills"
            skills.mkdir(parents=True)
            junction = skills / "_shared"
            source = str(repo_shared).replace("'", "''")
            dest = str(junction).replace("'", "''")

            junction_result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"New-Item -ItemType Junction -Path '{dest}' -Target '{source}' | Out-Null",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(junction_result.returncode, 0, msg=junction_result.stderr + junction_result.stdout)

            pending = root / ".ghost-alice" / "pending-merges" / "claude"
            pending.mkdir(parents=True)
            backup = pending / "repo-junction.bak"
            shutil.copy2(skills / "_shared" / "install_doctor.py", backup)
            manifest = pending / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": [
                            {
                                "id": "repo-junction",
                                "platform": "claude",
                                "skill": "_shared",
                                "source_path": (skills / "_shared" / "install_doctor.py").as_posix(),
                                "backup_path": backup.as_posix(),
                                "snapshot_hash": "old",
                                "current_hash": "new",
                                "change_kind": "modified",
                                "relative_path": "_shared/install_doctor.py",
                                "decided": False,
                                "decision": None,
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(CLEANUP_FALSE_POSITIVE_LEGACY),
                    "--platform",
                    "claude",
                    "--manifest",
                    str(manifest),
                    "--pending",
                    str(pending),
                    "--repo-root",
                    str(repo),
                    "--apply",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["cleaned"], 1)
            self.assertFalse(backup.exists())
            data = json.loads(manifest.read_text(encoding="utf-8"))
            entry = data["entries"][0]
            self.assertTrue(entry["decided"])
            self.assertEqual(entry["decision"], "discarded")
            self.assertEqual(entry["cleanup_reason"], "repo-junction-source-change-false-positive")

    def test_install_lock_blocks_fresh_second_acquire(self) -> None:
        lock = _load_install_lock()
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "install.lock"

            lock.acquire_lock(lock_path, stale_seconds=60, owner="first")
            try:
                with self.assertRaises(lock.InstallLockError):
                    lock.acquire_lock(lock_path, stale_seconds=60, owner="second")
            finally:
                lock.release_lock(lock_path)

            self.assertFalse(lock_path.exists())

    def test_install_lock_recovers_stale_lock(self) -> None:
        lock = _load_install_lock()
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "install.lock"
            lock_path.write_text('{"owner":"old"}\n', encoding="utf-8")
            stale_time = time.time() - 7200
            os.utime(lock_path, (stale_time, stale_time))

            lock.acquire_lock(lock_path, stale_seconds=1, owner="new")
            try:
                body = lock_path.read_text(encoding="utf-8")
                self.assertIn('"owner": "new"', body)
            finally:
                lock.release_lock(lock_path)

    def test_cleanup_pending_apply_marks_clean_legacy_entry_decided_and_removes_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skills = root / ".agents" / "skills"
            pending = root / ".ghost-alice" / "pending-merges" / "codex"
            legacy = pending / "legacy-targets"
            source = self._managed_copy(REPO_ROOT / "task-router", skills / "task-router", asset_id="task-router")
            backup = self._managed_copy(REPO_ROOT / "task-router", legacy / "old-task-router", asset_id="task-router")
            modified_backup = self._managed_copy(
                REPO_ROOT / "session-intent-analyzer",
                legacy / "old-session-intent-analyzer",
                asset_id="session-intent-analyzer",
            )
            (modified_backup / "SKILL.md").write_text("user edit\n", encoding="utf-8")

            manifest = pending / "manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": [
                            {
                                "id": "clean-legacy",
                                "platform": "codex",
                                "skill": "task-router",
                                "source_path": source.as_posix(),
                                "backup_path": backup.as_posix(),
                                "current_hash": "legacy-no-baseline",
                                "reason": "legacy-no-baseline",
                                "decided": False,
                                "decision": None,
                            },
                            {
                                "id": "modified-legacy",
                                "platform": "codex",
                                "skill": "session-intent-analyzer",
                                "source_path": (skills / "session-intent-analyzer").as_posix(),
                                "backup_path": modified_backup.as_posix(),
                                "current_hash": "legacy-no-baseline",
                                "reason": "legacy-no-baseline",
                                "decided": False,
                                "decision": None,
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            self._managed_copy(
                REPO_ROOT / "session-intent-analyzer",
                skills / "session-intent-analyzer",
                asset_id="session-intent-analyzer",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(CLEANUP_FALSE_POSITIVE_LEGACY),
                    "--platform",
                    "codex",
                    "--manifest",
                    str(manifest),
                    "--pending",
                    str(pending),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--apply",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["cleaned"], 1)
            self.assertEqual(summary["skipped"], 1)
            self.assertFalse(backup.exists())
            self.assertTrue(modified_backup.exists())

            data = json.loads(manifest.read_text(encoding="utf-8"))
            by_id = {entry["id"]: entry for entry in data["entries"]}
            self.assertTrue(by_id["clean-legacy"]["decided"])
            self.assertEqual(by_id["clean-legacy"]["decision"], "discarded")
            self.assertEqual(
                by_id["clean-legacy"]["cleanup_reason"],
                "clean-ghost-alice-managed-false-positive",
            )
            self.assertFalse(by_id["modified-legacy"]["decided"])

    def test_cleanup_pending_dry_run_reports_cleanable_entry_without_mutating_manifest_or_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pending = root / ".ghost-alice" / "pending-merges" / "codex"
            source = self._managed_copy(
                REPO_ROOT / "task-router",
                root / ".agents" / "skills" / "task-router",
                asset_id="task-router",
            )
            backup = self._managed_copy(
                REPO_ROOT / "task-router",
                pending / "legacy-targets" / "old-task-router",
                asset_id="task-router",
            )
            manifest = pending / "manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "id": "clean-legacy",
                                "platform": "codex",
                                "skill": "task-router",
                                "source_path": source.as_posix(),
                                "backup_path": backup.as_posix(),
                                "current_hash": "legacy-no-baseline",
                                "reason": "legacy-no-baseline",
                                "decided": False,
                                "decision": None,
                            }
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            before = manifest.read_text(encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(CLEANUP_FALSE_POSITIVE_LEGACY),
                    "--platform",
                    "codex",
                    "--manifest",
                    str(manifest),
                    "--pending",
                    str(pending),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["cleanable"], 1)
            self.assertEqual(summary["cleaned"], 0)
            self.assertEqual(manifest.read_text(encoding="utf-8"), before)
            self.assertTrue(backup.exists())

    def test_installers_expose_false_positive_pending_cleanup_command(self) -> None:
        cli_py = (REPO_ROOT / "_shared" / "installer" / "cli.py").read_text(encoding="utf-8")
        preservation_py = (REPO_ROOT / "_shared" / "installer" / "preservation.py").read_text(encoding="utf-8")
        self.assertIn("--cleanup-pending", cli_py)
        self.assertIn("def cleanup_pending", preservation_py)
        self.assertIn("cleanup_false_positive_legacy.py", preservation_py)
        self.assertIn("--apply", preservation_py)


if __name__ == "__main__":
    unittest.main()
