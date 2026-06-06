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
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
INSTALL_LOCK = REPO_ROOT / "_shared" / "install_lock.py"
INSTALL_TRANSACTION = REPO_ROOT / "_shared" / "install_transaction.py"
CLEANUP_FALSE_POSITIVE_LEGACY = REPO_ROOT / "merge-companion" / "scripts" / "cleanup_false_positive_legacy.py"
SHARED = REPO_ROOT / "_shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

from installer_assets import write_ownership_marker

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


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


def _load_install_transaction():
    if not INSTALL_TRANSACTION.exists():
        raise AssertionError("_shared/install_transaction.py must exist")
    spec = importlib.util.spec_from_file_location("install_transaction_under_test", INSTALL_TRANSACTION)
    if spec is None or spec.loader is None:
        raise AssertionError("install_transaction.py must be importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _extract_bash_function(source: str, name: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"{name}() {{"):
            start = idx
            break
    if start is None:
        raise AssertionError(f"Function not found: {name}")

    body = []
    depth = 0
    for line in lines[start:]:
        body.append(line)
        depth += line.count("{")
        depth -= line.count("}")
        if depth == 0:
            return "".join(body)
    raise AssertionError(f"Function did not terminate: {name}")


def _extract_powershell_function(source: str, name: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"function {name} "):
            start = idx
            break
    if start is None:
        raise AssertionError(f"Function not found: {name}")

    body = []
    depth = 0
    for line in lines[start:]:
        body.append(line)
        depth += line.count("{")
        depth -= line.count("}")
        if depth == 0:
            return "".join(body)
    raise AssertionError(f"Function did not terminate: {name}")


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

    def test_install_sh_snapshot_failure_is_blocking(self) -> None:
        source = installer_bash_source()
        snapshot_fn = _extract_bash_function(source, "_run_snapshot_after_install")

        self.assertIn("aborting because merge-companion snapshot cannot run", snapshot_fn)
        self.assertIn("snapshot capture failed; aborting install", snapshot_fn)
        self.assertIn("return 1", snapshot_fn)
        self.assertNotIn("skipping merge-companion snapshot", snapshot_fn)
        self.assertNotIn("snapshot capture failed (continuing)", snapshot_fn)

    def test_install_ps1_snapshot_failure_is_blocking(self) -> None:
        source = installer_ps1_source()
        snapshot_fn = _extract_powershell_function(source, "Invoke-SnapshotAfterInstall")

        self.assertIn("aborting because merge-companion snapshot cannot run", snapshot_fn)
        self.assertIn("snapshot capture failed; aborting install", snapshot_fn)
        self.assertIn("throw", snapshot_fn)
        self.assertNotIn("Write-Warn", snapshot_fn)
        self.assertNotIn("continuing", snapshot_fn)

    def test_installers_wrap_install_with_lock_and_release(self) -> None:
        sh = installer_bash_source()
        self.assertIn("_acquire_install_lock", sh)
        self.assertIn("_release_install_lock", sh)
        self.assertIn("install_lock.py\" acquire", sh)
        self.assertIn("install_lock.py\" release", sh)
        self.assertIn("trap _release_install_lock EXIT", sh)

        ps1 = installer_ps1_source()
        self.assertIn("function Invoke-WithInstallLock", ps1)
        self.assertIn("function Enter-InstallLock", ps1)
        self.assertIn("function Exit-InstallLock", ps1)
        self.assertIn("install_lock.py\") \"acquire\"", ps1)
        self.assertIn("install_lock.py\") \"release\"", ps1)
        self.assertIn("finally", ps1)

    def test_staged_copy_replace_preserves_existing_dest_when_staging_fails(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dest = root / "skill"
            dest.mkdir()
            (dest / "SKILL.md").write_text("old skill\n", encoding="utf-8")

            with self.assertRaises(transaction.InstallTransactionError):
                transaction.staged_copy_replace(
                    root / "missing-source",
                    dest,
                    rollback_root=root / "rollbacks",
                )

            self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "old skill\n")

    def test_staged_copy_replace_records_failure_event_when_staging_fails(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dest = root / "skill"
            event_log = root / "install-state" / "codex-events.jsonl"
            dest.mkdir()
            (dest / "SKILL.md").write_text("old skill\n", encoding="utf-8")

            with self.assertRaises(transaction.InstallTransactionError):
                transaction.staged_copy_replace(
                    root / "missing-source",
                    dest,
                    rollback_root=root / "rollbacks",
                    event_log=event_log,
                )

            events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["schema_version"], 1)
            self.assertEqual(events[0]["event"], "copy_replace_failure")
            self.assertEqual(events[0]["phase"], "stage-copy")
            self.assertEqual(events[0]["source_path"], (root / "missing-source").as_posix())
            self.assertEqual(events[0]["dest_path"], dest.as_posix())
            self.assertIn("source does not exist", events[0]["error"])
            self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "old skill\n")

    def test_staged_copy_replace_replaces_dest_after_successful_stage(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            dest = root / "skill"
            source.mkdir()
            dest.mkdir()
            (source / "SKILL.md").write_text("new skill\n", encoding="utf-8")
            (dest / "SKILL.md").write_text("old skill\n", encoding="utf-8")

            transaction.staged_copy_replace(source, dest, rollback_root=root / "rollbacks")

            self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "new skill\n")

    def test_staged_copy_replace_prunes_old_success_rollbacks(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            dest = root / "skill"
            rollbacks = root / "rollbacks"
            source.mkdir()
            dest.mkdir()
            rollbacks.mkdir()
            (source / "SKILL.md").write_text("new skill\n", encoding="utf-8")
            (dest / "SKILL.md").write_text("old skill\n", encoding="utf-8")
            stale = rollbacks / "rollback-old-skill"
            stale.mkdir()
            (stale / "SKILL.md").write_text("stale rollback\n", encoding="utf-8")
            os.utime(stale, (1, 1))

            transaction.staged_copy_replace(
                source,
                dest,
                rollback_root=rollbacks,
                rollback_keep=1,
            )

            remaining = sorted(path.name for path in rollbacks.iterdir())
            self.assertEqual(len(remaining), 1)
            self.assertNotIn("rollback-old-skill", remaining)

    def test_staged_copy_replace_ignores_generated_metadata_files(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            dest = root / "skill"
            source.mkdir()
            dest.mkdir()
            (source / "SKILL.md").write_text("new skill\n", encoding="utf-8")
            (source / ".DS_Store").write_text("generated\n", encoding="utf-8")
            (source / "__pycache__").mkdir()
            (source / "__pycache__" / "helper.pyc").write_text("generated\n", encoding="utf-8")
            (dest / ".DS_Store").write_text("old generated\n", encoding="utf-8")

            transaction.staged_copy_replace(source, dest, rollback_root=root / "rollbacks")

            self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "new skill\n")
            self.assertFalse((dest / ".DS_Store").exists())
            self.assertFalse((dest / "__pycache__").exists())

    def test_staged_copy_replace_preserves_dest_when_stage_verification_fails(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            dest = root / "skill"
            event_log = root / "install-state" / "codex-events.jsonl"
            source.mkdir()
            dest.mkdir()
            (source / "SKILL.md").write_text("new skill\n", encoding="utf-8")
            (dest / "SKILL.md").write_text("old skill\n", encoding="utf-8")

            original_copy_to_stage = transaction._copy_to_stage

            def corrupting_copy_to_stage(src: Path, stage: Path) -> None:
                original_copy_to_stage(src, stage)
                (stage / "SKILL.md").write_text("corrupt stage\n", encoding="utf-8")

            transaction._copy_to_stage = corrupting_copy_to_stage
            try:
                with self.assertRaises(transaction.InstallTransactionError):
                    transaction.staged_copy_replace(
                        source,
                        dest,
                        rollback_root=root / "rollbacks",
                        event_log=event_log,
                    )
            finally:
                transaction._copy_to_stage = original_copy_to_stage

            self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "old skill\n")
            events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["phase"], "stage-verify")
            self.assertIn("staged copy verification failed", events[0]["error"])

    def test_staged_copy_replace_many_preserves_all_destinations_when_any_stage_fails(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_a = root / "source-a"
            missing_source = root / "missing-source"
            dest_a = root / "skills" / "skill-a"
            dest_b = root / "skills" / "skill-b"
            source_a.mkdir()
            dest_a.mkdir(parents=True)
            dest_b.mkdir(parents=True)
            (source_a / "SKILL.md").write_text("new skill a\n", encoding="utf-8")
            (dest_a / "SKILL.md").write_text("old skill a\n", encoding="utf-8")
            (dest_b / "SKILL.md").write_text("old skill b\n", encoding="utf-8")

            with self.assertRaises(transaction.InstallTransactionError):
                transaction.staged_copy_replace_many(
                    [
                        (source_a, dest_a),
                        (missing_source, dest_b),
                    ],
                    rollback_root=root / "rollbacks",
                )

            self.assertEqual((dest_a / "SKILL.md").read_text(encoding="utf-8"), "old skill a\n")
            self.assertEqual((dest_b / "SKILL.md").read_text(encoding="utf-8"), "old skill b\n")

    def test_staged_copy_replace_many_preserves_all_destinations_when_stage_verification_fails(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_a = root / "source-a"
            source_b = root / "source-b"
            dest_a = root / "skills" / "skill-a"
            dest_b = root / "skills" / "skill-b"
            event_log = root / "install-state" / "codex-events.jsonl"
            source_a.mkdir()
            source_b.mkdir()
            dest_a.mkdir(parents=True)
            dest_b.mkdir(parents=True)
            (source_a / "SKILL.md").write_text("new skill a\n", encoding="utf-8")
            (source_b / "SKILL.md").write_text("new skill b\n", encoding="utf-8")
            (dest_a / "SKILL.md").write_text("old skill a\n", encoding="utf-8")
            (dest_b / "SKILL.md").write_text("old skill b\n", encoding="utf-8")

            original_copy_to_stage = transaction._copy_to_stage

            def corrupting_copy_to_stage(src: Path, stage: Path) -> None:
                original_copy_to_stage(src, stage)
                if Path(src) == source_b:
                    (stage / "SKILL.md").write_text("corrupt stage\n", encoding="utf-8")

            transaction._copy_to_stage = corrupting_copy_to_stage
            try:
                with self.assertRaises(transaction.InstallTransactionError):
                    transaction.staged_copy_replace_many(
                        [
                            (source_a, dest_a),
                            (source_b, dest_b),
                        ],
                        rollback_root=root / "rollbacks",
                        event_log=event_log,
                    )
            finally:
                transaction._copy_to_stage = original_copy_to_stage

            self.assertEqual((dest_a / "SKILL.md").read_text(encoding="utf-8"), "old skill a\n")
            self.assertEqual((dest_b / "SKILL.md").read_text(encoding="utf-8"), "old skill b\n")
            events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["phase"], "stage-verify")
            self.assertEqual(events[0]["source_path"], source_b.as_posix())
            self.assertEqual(events[0]["dest_path"], dest_b.as_posix())
            self.assertIn("staged copy verification failed", events[0]["error"])

    def test_staged_copy_replace_many_replaces_all_destinations_after_successful_stage(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_a = root / "source-a"
            source_b = root / "source-b"
            dest_a = root / "skills" / "skill-a"
            dest_b = root / "skills" / "skill-b"
            source_a.mkdir()
            source_b.mkdir()
            dest_a.mkdir(parents=True)
            dest_b.mkdir(parents=True)
            (source_a / "SKILL.md").write_text("new skill a\n", encoding="utf-8")
            (source_b / "SKILL.md").write_text("new skill b\n", encoding="utf-8")
            (dest_a / "SKILL.md").write_text("old skill a\n", encoding="utf-8")
            (dest_b / "SKILL.md").write_text("old skill b\n", encoding="utf-8")

            transaction.staged_copy_replace_many(
                [
                    (source_a, dest_a),
                    (source_b, dest_b),
                ],
                rollback_root=root / "rollbacks",
            )

            self.assertEqual((dest_a / "SKILL.md").read_text(encoding="utf-8"), "new skill a\n")
            self.assertEqual((dest_b / "SKILL.md").read_text(encoding="utf-8"), "new skill b\n")

    def test_staged_copy_replace_many_reports_progress_after_each_publish(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_a = root / "source-a"
            source_b = root / "source-b"
            dest_a = root / "skills" / "skill-a"
            dest_b = root / "skills" / "skill-b"
            source_a.mkdir()
            source_b.mkdir()
            dest_a.mkdir(parents=True)
            dest_b.mkdir(parents=True)
            (source_a / "SKILL.md").write_text("new skill a\n", encoding="utf-8")
            (source_b / "SKILL.md").write_text("new skill b\n", encoding="utf-8")
            (dest_a / "SKILL.md").write_text("old skill a\n", encoding="utf-8")
            (dest_b / "SKILL.md").write_text("old skill b\n", encoding="utf-8")

            events: list[tuple[int, int, str, str]] = []

            def record_progress(done: int, total: int) -> None:
                events.append(
                    (
                        done,
                        total,
                        (dest_a / "SKILL.md").read_text(encoding="utf-8"),
                        (dest_b / "SKILL.md").read_text(encoding="utf-8"),
                    )
                )

            transaction.staged_copy_replace_many(
                [
                    (source_a, dest_a),
                    (source_b, dest_b),
                ],
                rollback_root=root / "rollbacks",
                progress_callback=record_progress,
            )

            self.assertEqual(
                events,
                [
                    (1, 2, "new skill a\n", "old skill b\n"),
                    (2, 2, "new skill a\n", "new skill b\n"),
                ],
            )

    def test_copy_replace_many_cli_progress_label_writes_one_line_counter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_a = root / "source-a"
            source_b = root / "source-b"
            dest_a = root / "skills" / "skill-a"
            dest_b = root / "skills" / "skill-b"
            source_a.mkdir()
            source_b.mkdir()
            (source_a / "SKILL.md").write_text("new skill a\n", encoding="utf-8")
            (source_b / "SKILL.md").write_text("new skill b\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_TRANSACTION),
                    "copy-replace-many",
                    "--progress-label",
                    "[2/5] Skill sync",
                    "--rollback-root",
                    str(root / "rollbacks"),
                    "--target",
                    str(source_a),
                    str(dest_a),
                    "--target",
                    str(source_b),
                    str(dest_b),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, msg=(result.stderr + result.stdout).decode("utf-8", "replace"))
            self.assertIn(b"\r[2/5] Skill sync [0/2]", result.stdout)
            self.assertIn(b"\r[2/5] Skill sync [1/2]", result.stdout)
            self.assertIn(b"\r[2/5] Skill sync [2/2]", result.stdout)
            self.assertTrue(result.stdout.endswith(b"\n"))

    def test_copy_replace_many_cli_writes_target_progress_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_a = root / "source-a"
            source_b = root / "source-b"
            dest_a = root / "skills" / "skill-a"
            dest_b = root / "skills" / "skill-b"
            progress_events = root / "progress.events.jsonl"
            source_a.mkdir()
            source_b.mkdir()
            (source_a / "SKILL.md").write_text("new skill a\n", encoding="utf-8")
            (source_b / "SKILL.md").write_text("new skill b\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_TRANSACTION),
                    "copy-replace-many",
                    "--rollback-root",
                    str(root / "rollbacks"),
                    "--progress-event-file",
                    str(progress_events),
                    "--progress-platform",
                    "codex",
                    "--progress-target-id",
                    "skill-a",
                    "--progress-target-kind",
                    "skill",
                    "--progress-target-status",
                    "new",
                    "--progress-target-id",
                    "skill-b",
                    "--progress-target-kind",
                    "skill",
                    "--progress-target-status",
                    "updated",
                    "--target",
                    str(source_a),
                    str(dest_a),
                    "--target",
                    str(source_b),
                    str(dest_b),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertEqual(
                [json.loads(line) for line in progress_events.read_text(encoding="utf-8").splitlines()],
                [
                    {
                        "type": "target-result",
                        "platform": "codex",
                        "target_id": "skill-a",
                        "target_kind": "skill",
                        "status": "new",
                    },
                    {
                        "type": "target-result",
                        "platform": "codex",
                        "target_id": "skill-b",
                        "target_kind": "skill",
                        "status": "updated",
                    },
                ],
            )

    def test_installers_use_staged_copy_replace_for_copy_installs(self) -> None:
        sh = installer_bash_source()
        self.assertIn("_install_copy_target", sh)
        self.assertIn("install_transaction.py\" copy-replace", sh)
        self.assertNotIn('cp -r "$src" "$dest"', sh)
        self.assertNotIn('cp -r "$shared_src" "$shared_dest"', sh)

        ps1 = installer_ps1_source()
        self.assertIn("function Invoke-StagedCopyReplace", ps1)
        self.assertIn('"copy-replace"', ps1)
        self.assertNotIn("Copy-Item -Path $src -Destination $dest -Recurse -Force", ps1)
        self.assertNotIn("Copy-Item -Path $Source -Destination $Dest -Recurse -Force", ps1)
        self.assertNotIn("Copy-Item -Path $sharedSrc -Destination $sharedDest -Recurse -Force", ps1)

    def test_installers_pass_event_log_to_staged_copy_replace(self) -> None:
        sh = installer_bash_source()
        copy_fn = _extract_bash_function(sh, "_install_copy_target")
        self.assertIn("--event-log", copy_fn)
        self.assertIn('${HOME}/.ghost-alice/install-state/${PLATFORM}-events.jsonl', copy_fn)

        ps1 = installer_ps1_source()
        copy_fn_ps1 = _extract_powershell_function(ps1, "Invoke-StagedCopyReplace")
        self.assertIn("--event-log", copy_fn_ps1)
        self.assertIn('$Platform-events.jsonl', copy_fn_ps1)

    def test_installers_use_batch_copy_transaction_for_copy_only_installs(self) -> None:
        sh = installer_bash_source()
        self.assertIn("_install_copy_targets", sh)
        self.assertIn("copy-replace-many", sh)
        install_fn = _extract_bash_function(sh, "install")
        self.assertIn("copy_target_args", install_fn)
        self.assertIn("_install_copy_targets", install_fn)

        ps1 = installer_ps1_source()
        self.assertIn("function Invoke-StagedCopyReplaceMany", ps1)
        self.assertIn('"copy-replace-many"', ps1)
        install_fn_ps1 = _extract_powershell_function(ps1, "Invoke-Install")
        self.assertIn("$copyTargets", install_fn_ps1)
        self.assertIn("Invoke-StagedCopyReplaceMany", install_fn_ps1)

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
        sh = installer_bash_source()
        self.assertIn("--cleanup-pending", sh)
        self.assertIn("cleanup_pending_false_positives", sh)
        self.assertIn("cleanup_false_positive_legacy.py", sh)
        self.assertIn("--apply", sh)

        ps1 = installer_ps1_source()
        self.assertIn("-CleanupPending", ps1)
        self.assertIn("Invoke-CleanupPendingFalsePositives", ps1)
        self.assertIn("cleanup_false_positive_legacy.py", ps1)
        self.assertIn("--apply", ps1)


class StagedSymlinkReplaceTests(unittest.TestCase):
    def test_staged_symlink_replace_creates_symlink_and_preserves_dest(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            dest = root / "skill"
            source.mkdir()
            (source / "SKILL.md").write_text("linked skill\n", encoding="utf-8")
            dest.mkdir()
            (dest / "user_edit.txt").write_text("keep-me\n", encoding="utf-8")
            rollbacks = root / "rollbacks"

            transaction.staged_symlink_replace(source, dest, rollback_root=rollbacks)

            self.assertTrue(dest.is_symlink())
            self.assertEqual(os.readlink(dest), str(source))
            preserved = list(rollbacks.glob("rollback-*"))
            self.assertTrue(preserved, "previous dest must be moved aside, not unbacked-removed")
            self.assertEqual(
                (preserved[0] / "user_edit.txt").read_text(encoding="utf-8"), "keep-me\n"
            )

    def test_staged_symlink_replace_into_empty_dest(self) -> None:
        transaction = _load_install_transaction()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("x\n", encoding="utf-8")
            dest = root / "skill"

            transaction.staged_symlink_replace(source, dest, rollback_root=root / "rollbacks")

            self.assertTrue(dest.is_symlink())
            self.assertEqual(os.readlink(dest), str(source))


class SymlinkInstallWiringTests(unittest.TestCase):
    def test_install_sh_symlink_branch_uses_staged_replace_not_unbacked_rm(self) -> None:
        sh = installer_bash_source()
        self.assertIn('install_transaction.py" symlink-replace', sh)
        install_fn = _extract_bash_function(sh, "install")
        self.assertNotIn('rm -rf "$dest"', install_fn)


if __name__ == "__main__":
    unittest.main()
