"""installer.lock and installer.runner: the install lock around commands and the child-tool runner."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import lock  # noqa: E402
from installer import report  # noqa: E402
from installer import runner  # noqa: E402
from installer.runner import ToolResult  # noqa: E402
from installer_testing import FakeRunner  # noqa: E402


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
        path = lock.acquire("install", "claude")
        self.assertEqual(path, self.home / ".ghost-alice" / "install.lock")
        self.assertTrue(path.is_file())
        self.assertIn("installer:install:claude:", path.read_text(encoding="utf-8"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit) as held:
            lock.acquire("uninstall", "codex")
        self.assertEqual(held.exception.code, 1)
        self.assertIn(f"Another install is already running; aborting uninstall: {path}", out.getvalue())
        lock.release(path)
        self.assertFalse(path.exists())
        lock.release(path)
        lock.release(None)
        self.assertEqual(lock.acquire("repair", "claude"), path)
        lock.release(path)

    def test_explicit_lock_path(self):
        custom = self.home / "custom.lock"
        self.assertEqual(lock.acquire("cleanup-pending", "claude", lock_path=custom), custom)
        self.assertTrue(custom.is_file())
        lock.release(custom)


class UnfinishedPublishTests(unittest.TestCase):
    """Right after the lock, a publish that stopped halfway is restored before the command does anything else."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.ghost = self.home / ".ghost-alice"
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=self.home)
        self.runner = FakeRunner()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def journal(self, txid: str, phase: str) -> Path:
        folder = self.ghost / "rollbacks" / txid
        folder.mkdir(parents=True)
        (folder / "journal.json").write_text(json.dumps({"schema_version": 1, "txid": txid, "phase": phase}),
                                             encoding="utf-8")
        return folder

    def test_finished_publishes_need_no_restore(self):
        self.journal("20261020T000000Z-1-aaaaaaaa", "done")
        self.journal("20261020T000001Z-1-bbbbbbbb", "restored")
        with self.runner.patch():
            path = lock.acquire("install", "claude")
        self.assertEqual(self.runner.calls, [])
        self.assertTrue(path.is_file())
        lock.release(path)

    def test_unfinished_publishes_are_restored_newest_first_while_the_lock_is_held(self):
        self.journal("20261020T000000Z-1-aaaaaaaa", "prepare")
        self.journal("20261020T000002Z-1-cccccccc", "done")
        self.journal("20261020T000001Z-1-bbbbbbbb", "prepare")
        held = []

        def recover(script, args, kwargs):
            held.append((self.ghost / "install.lock").is_file())
            return ToolResult(0)

        self.runner.respond(lambda script, args: script.endswith("install_transaction.py"), recover)
        with self.runner.patch():
            path = lock.acquire("repair", "codex")
        self.assertEqual(self.runner.tool_calls("install_transaction.py"), [
            ["recover", "--ghost-root", str(self.ghost), "--txid", "20261020T000001Z-1-bbbbbbbb"],
            ["recover", "--ghost-root", str(self.ghost), "--txid", "20261020T000000Z-1-aaaaaaaa"]])
        self.assertEqual(held, [True, True])
        self.assertTrue(path.is_file())
        lock.release(path)

    def test_a_publish_that_cannot_be_restored_stops_the_command_and_releases_the_lock(self):
        folder = self.journal("20261020T000000Z-1-aaaaaaaa", "prepare")
        self.runner.respond_to("install_transaction.py", ToolResult(1))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.runner.patch(), self.assertRaises(runner.InstallerExit) as stop:
            lock.acquire("uninstall", "claude")
        self.assertEqual(stop.exception.code, 1)
        self.assertIn(f"[ERROR] An unfinished publish could not be restored; review {folder} "
                      "and run the installer again", out.getvalue())
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
        result = runner.run_tool("_shared/install_lock.py", "--help", capture=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)
        failed = runner.run_tool("_shared/install_lock.py", "release", capture=True)
        self.assertFalse(failed.ok)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("--lock", failed.stderr)

    def test_run_tool_passes_bytecode_flag_env_and_cwd(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("installer.runner.subprocess.run", side_effect=fake_run):
            runner.run_tool("_shared/retired_assets.py", "--skills-root", "x", no_bytecode=True,
                            env={"GHOST_ALICE_SOURCE_REPO_HOOK_CHANGED": "1"}, cwd=self.home)
        argv, kwargs = calls[0]
        self.assertEqual(argv[:3], [sys.executable, "-B", str(runner.REPO_ROOT / "_shared" / "retired_assets.py")])
        self.assertEqual(argv[3:], ["--skills-root", "x"])
        self.assertEqual(kwargs["env"]["GHOST_ALICE_SOURCE_REPO_HOOK_CHANGED"], "1")
        self.assertEqual(kwargs["env"]["HOME"], str(self.home))
        self.assertEqual(Path(kwargs["cwd"]), self.home)

    def test_run_tool_without_capture_writes_to_the_current_sink(self):
        # a logged step sends child output to the log, like `>>"$LOG" 2>&1`
        with contextlib.redirect_stdout(io.StringIO()):
            result = report.run_logged(runner.run_tool, "_shared/install_lock.py", "--help")
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
