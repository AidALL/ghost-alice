"""installer.lock and installer.runner: the install lock around commands and the child-tool runner."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import gateway_participant, lock  # noqa: E402
from installer import report  # noqa: E402
from installer import runner  # noqa: E402
import file_lock  # noqa: E402
import install_transaction  # noqa: E402


class LockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        os.environ.pop("GHOST_ALICE_INSTALL_LOCK_STALE_SECONDS", None)
        report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=self.home)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_stale_seconds_and_owner(self):
        # installer_lib/install.sh:48-52
        self.assertEqual(lock.stale_seconds(), 1800)
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_LOCK_STALE_SECONDS": "60"}):
            self.assertEqual(lock.stale_seconds(), 60)
        self.assertEqual(lock.owner("install", "claude", pid=42), "installer:install:claude:42")
        self.assertRegex(lock.owner("uninstall", "codex"), r"^installer:uninstall:codex:\d+$")

    def test_acquire_release_and_second_holder(self):
        # installer_lib/install.sh:39-73
        handle = lock.acquire("install", "claude")
        path, generation = handle
        self.assertEqual(path, self.home / ".ghost-alice" / "install.lock")
        self.assertTrue(path.is_file())
        self.assertIn("installer:install:claude:", path.read_text(encoding="utf-8"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit) as held:
            lock.acquire("uninstall", "codex")
        self.assertEqual(held.exception.code, 1)
        self.assertIn(f"Another install is already running; aborting uninstall: {path}", out.getvalue())
        self.assertEqual(json.loads(path.read_text())["generation"], generation)
        lock.release(handle)
        self.assertFalse(path.exists())
        lock.release(handle)
        lock.release(None)
        next_handle = lock.acquire("repair", "claude")
        self.assertEqual(next_handle[0], path)
        self.assertNotEqual(next_handle[1], generation)
        lock.release(next_handle)

    def test_explicit_lock_path(self):
        custom = self.home / "custom.lock"
        handle = lock.acquire("cleanup-pending", "claude", lock_path=custom)
        self.assertEqual(handle[0], custom)
        self.assertTrue(custom.is_file())
        lock.release(handle)

    def test_a_stale_holder_cannot_release_its_replacement(self):
        path = self.home / 'generation.lock'
        first = lock.acquire('install', 'claude', path)
        old = time.time() - 1801
        os.utime(path, (old, old))
        second = lock.acquire('repair', 'codex', path)
        replacement = path.read_bytes()
        try:
            lock.release(first)
            self.assertTrue(path.exists(), 'the previous holder deleted its replacement')
            self.assertEqual(path.read_bytes(), replacement)
            with self.assertRaises(runner.InstallerExit):
                lock.acquire('uninstall', 'claude', path)
        finally:
            lock.release(second)

    def test_duplicate_release_cannot_delete_a_same_pid_reacquisition(self):
        path = self.home / 'generation.lock'
        first = lock.acquire('install', 'claude', path)
        lock.release(first)
        second = lock.acquire('repair', 'claude', path)
        replacement = path.read_bytes()
        try:
            lock.release(first)
            self.assertTrue(path.exists(), 'a repeated release deleted a newer generation in the same process')
            self.assertEqual(path.read_bytes(), replacement)
        finally:
            lock.release(second)

    @unittest.skipIf(file_lock.fcntl is None, 'requires the native POSIX guard')
    def test_stale_takeover_cannot_cross_the_release_read_unlink_boundary(self):
        path = self.home / 'generation.lock'
        first = lock.acquire('install', 'claude', path)
        old = time.time() - 1801
        os.utime(path, (old, old))
        at_unlink, resume = threading.Event(), threading.Event()
        failures = []
        original_unlink = Path.unlink

        def pause_release(target, *args, **kwargs):
            if target == path and threading.current_thread().name == 'old-holder-release':
                at_unlink.set()
                if not resume.wait(5):
                    raise AssertionError('release was never resumed')
            return original_unlink(target, *args, **kwargs)

        def release_first():
            try:
                lock.release(first)
            except BaseException as exc:
                failures.append(exc)

        second = None
        blocked = False
        with mock.patch.object(Path, 'unlink', pause_release), mock.patch.object(file_lock, 'LOCK_WAIT_S', 0.05):
            thread = threading.Thread(target=release_first, name='old-holder-release')
            thread.start()
            try:
                self.assertTrue(at_unlink.wait(5), 'release never reached deletion')
                try:
                    second = lock.acquire('repair', 'codex', path)
                except runner.InstallerExit:
                    blocked = True
            finally:
                resume.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        try:
            self.assertTrue(blocked, 'takeover crossed the guarded release boundary')
            second = lock.acquire('repair', 'codex', path)
            replacement = path.read_bytes()
            lock.release(first)
            self.assertEqual(path.read_bytes(), replacement)
        finally:
            lock.release(second)


class UnfinishedPublishTests(unittest.TestCase):
    """Right after the lock, a publish that stopped halfway is restored before the command does anything else."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.ghost = self.home / ".ghost-alice"
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=self.home)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def journal(self, txid: str, phase: str) -> Path:
        folder = self.ghost / "rollbacks" / txid
        folder.mkdir(parents=True)
        (folder / "journal.json").write_text(json.dumps({"schema_version": 1, "txid": txid, "phase": phase,
                                                        "targets": []}),
                                             encoding="utf-8")
        return folder

    def test_finished_publishes_need_no_restore(self):
        self.journal("20261020T000000Z-1-aaaaaaaa", "done")
        self.journal("20261020T000001Z-1-bbbbbbbb", "restored")
        with mock.patch.object(install_transaction, "recover", side_effect=AssertionError("finished journal")):
            handle = lock.acquire("install", "claude")
        self.assertTrue(handle[0].is_file())
        lock.release(handle)

    def test_unfinished_publishes_are_restored_newest_first_while_the_lock_is_held(self):
        self.journal("20261020T000000Z-1-aaaaaaaa", "prepare")
        self.journal("20261020T000002Z-1-cccccccc", "done")
        self.journal("20261020T000001Z-1-bbbbbbbb", "prepare")
        held, recovered = [], []
        original_recover = install_transaction.recover

        def recover(root, txid, *, participant_factory):
            held.append((self.ghost / "install.lock").is_file())
            recovered.append(txid)
            self.assertIs(participant_factory, gateway_participant.recovery_participants)
            return original_recover(root, txid, participant_factory=participant_factory)

        with mock.patch.object(install_transaction, "recover", side_effect=recover):
            handle = lock.acquire("repair", "codex")
        self.assertEqual(recovered, ["20261020T000001Z-1-bbbbbbbb", "20261020T000000Z-1-aaaaaaaa"])
        self.assertEqual(held, [True, True])
        self.assertEqual(install_transaction.unfinished(self.ghost), [])
        self.assertTrue(handle[0].is_file())
        lock.release(handle)

    def test_a_publish_that_cannot_be_restored_stops_the_command_and_releases_the_lock(self):
        folder = self.journal("20261020T000000Z-1-aaaaaaaa", "prepare")
        (folder / "journal.json").write_text("{broken", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit) as stop:
            lock.acquire("uninstall", "claude")
        self.assertEqual(stop.exception.code, 1)
        self.assertIn("cannot be read", out.getvalue())
        self.assertIn(f"review {folder}", out.getvalue())
        self.assertNotIn("An unfinished publish could not be restored", out.getvalue())
        self.assertFalse((self.ghost / "install.lock").exists())

    def test_target_read_error_stops_recovery_and_releases_the_lock(self):
        folder = self.journal("20261020T000000Z-1-aaaaaaaa", "prepare")
        journal = install_transaction.Journal.load(self.ghost, folder.name)
        journal.data["targets"] = [{"dest": str(self.home / "runtime")}]
        journal.save()
        out = io.StringIO()
        with mock.patch.object(install_transaction, "_recovery_action", side_effect=OSError("target unavailable")), \
                contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit) as stop:
            lock.acquire("repair", "claude")
        self.assertEqual(stop.exception.code, 1)
        self.assertIn("target unavailable", out.getvalue())
        self.assertIn(f"review {folder}", out.getvalue())
        self.assertIn("publish recovery failed", out.getvalue())
        self.assertNotIn("The installer restored the files, install record", out.getvalue())
        self.assertEqual(install_transaction.unfinished(self.ghost), [folder.name])
        self.assertFalse((self.ghost / "install.lock").exists())

    def test_restore_recording_and_staged_file_errors_keep_distinct_diagnostics(self):
        for index, point in enumerate(("inspect", "remove", "record")):
            with self.subTest(point=point):
                folder = self.journal(f"20261020T00000{index}Z-1-aaaaaaaa", "prepare")
                dest = self.home / f"runtime-{point}"
                stage = self.home / f"staged-runtime-{point}"
                original = folder / "original"
                dest.write_bytes(b"candidate")
                stage.write_bytes(b"candidate")
                original.write_bytes(b"original")
                payload = install_transaction.installed_payload
                prior = json.dumps({"schema_version": payload.SCHEMA_VERSION,
                                    "targets": {}, "installer": {"commit": "prior"}}).encode()
                saved = folder / payload.FILENAME
                saved.write_bytes(prior)
                payload.path(self.ghost).write_bytes(json.dumps({
                    "schema_version": payload.SCHEMA_VERSION,
                    "targets": {}, "installer": {"commit": "candidate"}}).encode())
                journal = install_transaction.Journal.load(self.ghost, folder.name)
                journal.data.update(participants={}, record_copy=saved.name, targets=[dict(
                    dest=str(dest), stage=str(stage), rollback=str(original),
                    before=install_transaction.content_digest.state(original),
                    candidate=install_transaction.content_digest.state(stage))])
                journal.save()
                real_state = install_transaction.content_digest.state
                real_remove = install_transaction._remove_path

                def state(path):
                    if point == "inspect" and Path(path) == stage:
                        raise OSError("staged file inspection denied")
                    return real_state(path)

                def remove(path):
                    if point == "remove" and Path(path) == stage:
                        raise OSError("staged file removal denied")
                    return real_remove(path)

                recording = (mock.patch.object(install_transaction.Journal, "save",
                             side_effect=OSError("restoration record write denied"))
                             if point == "record" else contextlib.nullcontext())
                out = io.StringIO()
                with recording, mock.patch.object(install_transaction.content_digest, "state", side_effect=state), \
                        mock.patch.object(install_transaction, "_remove_path", side_effect=remove), \
                        contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit) as stop:
                    lock.acquire("repair", "claude")
                self.assertEqual(stop.exception.code, 1)
                self.assertEqual(dest.read_bytes(), b"original")
                self.assertEqual(payload.path(self.ghost).read_bytes(), prior)
                self.assertEqual(stage.read_bytes(), b"candidate")
                self.assertEqual(install_transaction.Journal.load(self.ghost, folder.name).phase,
                                 "prepare" if point == "record" else "restored")
                self.assertIn(f"review {folder}", out.getvalue())
                self.assertNotIn("An unfinished publish could not be restored", out.getvalue())
                if point == "record":
                    self.assertIn("restoration record write denied", out.getvalue())
                    self.assertIn("publish recovery failed", out.getvalue())
                    self.assertNotIn("The installer restored the files, install record", out.getvalue())
                else:
                    self.assertIn(f"staged file {'inspection' if point == 'inspect' else 'removal'} denied",
                                  out.getvalue())
                    self.assertIn("The installer restored the files, install record and required service state for this publish, "
                                  "but could not inspect or remove staged files", out.getvalue())
                    self.assertNotIn("publish recovery failed", out.getvalue())
                self.assertFalse((self.ghost / "install.lock").exists())


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=self.home)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_repo_root_is_the_checkout(self):
        self.assertTrue((runner.REPO_ROOT / "install.sh").is_file())
        self.assertTrue((runner.REPO_ROOT / "_shared" / "installer" / "cli.py").is_file())
        self.assertEqual(runner.python_executable(), sys.executable)

    def test_installer_exit_carries_the_code(self):
        self.assertEqual(runner.InstallerExit().code, 1)
        self.assertEqual(runner.InstallerExit(2).code, 2)

    def test_run_tool_captures_stdout_and_exit_code(self):
        result = runner.run_tool("_shared/install_transaction.py", "--help", capture=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)
        failed = runner.run_tool("_shared/install_transaction.py", "recover", capture=True)
        self.assertFalse(failed.ok)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("--ghost-root", failed.stderr)

    def test_run_tool_passes_bytecode_flag_env_and_cwd(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("installer.runner.subprocess.run", side_effect=fake_run):
            runner.run_tool("_shared/retired_assets.py", "--skills-root", "x", no_bytecode=True,
                            env={"GHOST_ALICE_SOURCE_REPO_HOOK_RECORDED": "1"}, cwd=self.home)
        argv, kwargs = calls[0]
        self.assertEqual(argv[:3], [sys.executable, "-B", str(runner.REPO_ROOT / "_shared" / "retired_assets.py")])
        self.assertEqual(argv[3:], ["--skills-root", "x"])
        self.assertEqual(kwargs["env"]["GHOST_ALICE_SOURCE_REPO_HOOK_RECORDED"], "1")
        self.assertEqual(kwargs["env"]["HOME"], str(self.home))
        self.assertEqual(Path(kwargs["cwd"]), self.home)

    def test_run_tool_without_capture_writes_to_the_current_sink(self):
        # a logged step sends child output to the log, like `>>"$LOG" 2>&1`
        with contextlib.redirect_stdout(io.StringIO()):
            result = report.run_logged(runner.run_tool, "_shared/install_transaction.py", "--help")
        self.assertTrue(result.ok)
        self.assertIsNone(result.stdout)
        self.assertIn("usage:", report.state().log_file.read_text(encoding="utf-8"))

    def test_run_command(self):
        result = runner.run_command(["git", "--version"], capture=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.stdout.startswith("git version"))
        missing = runner.run_command(["definitely-not-a-command-ghost-alice"], capture=True)
        self.assertFalse(missing.ok)


if __name__ == "__main__":
    unittest.main()
