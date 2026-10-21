"""installer.source: source health, git hook path, source identity, the install source check, --update-source.

Expected texts come from installer_lib/source_health.sh and installer_lib/install.sh:538-551.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installed_payload  # noqa: E402
from installer import report  # noqa: E402
from installer import runner  # noqa: E402
from installer import source  # noqa: E402
from installer_testing import make_context, use_source  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def init_repo(path: Path, *, upstream: Path | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if upstream is None:
        git(path, "init", "-q", "-b", "main")
        (path / "tracked.txt").write_text("one\n", encoding="utf-8")
        git(path, "add", "tracked.txt")
        git(path, "commit", "-q", "-m", "one")
        return path
    subprocess.run(["git", "clone", "-q", str(upstream), str(path)], check=True, env={**os.environ, **GIT_ENV})
    return path


class SourceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # The CI runner puts the temp root inside the checkout: stop git from finding the checkout above it.
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.root / "home"),
                                                "GIT_CEILING_DIRECTORIES": str(self.root.resolve().parent),
                                                **GIT_ENV}, clear=False)
        self.env.start()
        (self.root / "home").mkdir()
        report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=self.root / "home")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def check(self, repo: Path, *, skip: bool = False) -> tuple[bool, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with use_source(repo):
                result = source.check_source_health(skip=skip)
        return result, out.getvalue()


class SourceHealthTests(SourceCase):
    def test_skip_flag_warns_and_passes(self):
        # source_health.sh:6-9
        ok, out = self.check(self.root / "nowhere", skip=True)
        self.assertTrue(ok)
        self.assertIn("[WARN] Skipping source health gate (--skip-source-health)", out)

    def test_missing_git_is_unverified(self):
        # source_health.sh:11-14
        with mock.patch("installer.source.shutil.which", return_value=None):
            ok, out = self.check(init_repo(self.root / "repo"))
        self.assertTrue(ok)
        self.assertIn("[WARN] git not found; source health is unverified (continuing)", out)

    def test_plain_directory_is_unverified(self):
        # source_health.sh:16-19
        plain = self.root / "plain"
        plain.mkdir()
        ok, out = self.check(plain)
        self.assertTrue(ok)
        self.assertIn("[WARN] source is not a git worktree; source health is unverified (continuing)", out)

    def test_clean_branch_without_upstream(self):
        # source_health.sh:25-41; test_source_health_gate.py:268-270
        repo = init_repo(self.root / "repo")
        ok, out = self.check(repo)
        self.assertTrue(ok)
        self.assertRegex(out, r"\[INFO\] Source health: branch=main, head=[0-9a-f]{7,}, upstream=none")
        self.assertNotIn("[WARN]", out)

    def test_detached_head(self):
        # test_source_health_gate.py:279
        repo = init_repo(self.root / "repo")
        git(repo, "checkout", "-q", "--detach")
        ok, out = self.check(repo)
        self.assertTrue(ok)
        self.assertIn("branch=DETACHED", out)

    def test_behind_upstream_refuses(self):
        # source_health.sh:55-60; test_source_health_gate.py:343-345
        upstream = init_repo(self.root / "upstream")
        clone = init_repo(self.root / "clone", upstream=upstream)
        (upstream / "tracked.txt").write_text("two\n", encoding="utf-8")
        git(upstream, "commit", "-q", "-am", "two")
        git(clone, "fetch", "-q")
        ok, out = self.check(clone)
        self.assertFalse(ok)
        self.assertIn("[ERROR] Source branch is behind its upstream; refusing to install from stale source.", out)
        self.assertIn("Run git fetch origin, inspect git status, then fast-forward or otherwise reconcile this checkout "
                      "before installing. If this local source is intentional, rerun with --skip-source-health.", out)
        self.assertIn("To preserve source-local changes while updating, run bash install.sh --update-source.", out)
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_COMMAND": ".\\install.cmd"}):
            _, out = self.check(clone)
        self.assertIn("To preserve source-local changes while updating, run .\\install.cmd --update-source.", out)
        self.assertIn("If an old checkout is already blocked by local changes during git pull, use the bootstrap "
                      "one-command update in docs/getting-started/troubleshooting.md.", out)

    def test_diverged_warns_and_continues(self):
        # source_health.sh:52-54; test_source_health_gate.py:322-323
        upstream = init_repo(self.root / "upstream")
        clone = init_repo(self.root / "clone", upstream=upstream)
        (upstream / "tracked.txt").write_text("two\n", encoding="utf-8")
        git(upstream, "commit", "-q", "-am", "two")
        (clone / "local.txt").write_text("local\n", encoding="utf-8")
        git(clone, "add", "local.txt")
        git(clone, "commit", "-q", "-m", "local")
        git(clone, "fetch", "-q")
        ok, out = self.check(clone)
        self.assertTrue(ok)
        self.assertIn("[WARN] Source branch has diverged from its upstream; continuing because local commits may be intentional.", out)
        self.assertIn("[WARN] Before release, run git fetch origin, inspect git status, and reconcile this checkout history.", out)

    def test_ahead_warns_and_continues(self):
        # source_health.sh:61-63
        upstream = init_repo(self.root / "upstream")
        clone = init_repo(self.root / "clone", upstream=upstream)
        (clone / "local.txt").write_text("local\n", encoding="utf-8")
        git(clone, "add", "local.txt")
        git(clone, "commit", "-q", "-m", "local")
        ok, out = self.check(clone)
        self.assertTrue(ok)
        self.assertIn("[WARN] Source branch is ahead of its upstream; continuing because local commits may be intentional.", out)

    def test_untracked_and_tracked_changes_warn(self):
        # source_health.sh:69-76; test_source_health_gate.py:289-300
        repo = init_repo(self.root / "repo")
        (repo / "note.txt").write_text("untracked\n", encoding="utf-8")
        ok, out = self.check(repo)
        self.assertTrue(ok)
        self.assertIn("[WARN] Source tree has untracked files; continuing because tracked files are clean.", out)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        ok, out = self.check(repo)
        self.assertTrue(ok)
        self.assertIn("[WARN] Source tree has tracked local changes; continuing. Installed target user edits remain "
                      "protected by pending-merges backup and manifest checks.", out)


class GitHooksTests(SourceCase):
    def test_setup_git_hooks_sets_and_records_the_path(self):
        # source_health.sh:91-116
        repo = init_repo(self.root / "repo")
        self.enterContext(use_source(repo))
        hook = repo / "hooks" / "post-merge"
        hook.parent.mkdir()
        hook.write_text("#!/bin/sh\n", encoding="utf-8")
        hook.chmod(0o644)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            change = source.setup_git_hooks()
        self.assertEqual(change, source.HookPathChange(changed=True, before_present=False, before="", after="hooks"))
        self.assertEqual(git(repo, "config", "--local", "core.hooksPath"), "hooks")
        self.assertIn("[INFO] Git hook path → hooks (post-merge auto-refresh enabled)", out.getvalue())
        self.assertTrue(hook.stat().st_mode & stat.S_IXUSR)
        with contextlib.redirect_stdout(io.StringIO()):
            again = source.setup_git_hooks()
        self.assertFalse(again.changed)
        git(repo, "config", "--local", "core.hooksPath", "elsewhere")
        with contextlib.redirect_stdout(io.StringIO()):
            replaced = source.setup_git_hooks()
        self.assertEqual(replaced, source.HookPathChange(changed=True, before_present=True, before="elsewhere", after="hooks"))

    def test_setup_git_hooks_outside_git_warns(self):
        plain = self.root / "plain"
        plain.mkdir()
        self.enterContext(use_source(plain))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            change = source.setup_git_hooks()
        self.assertFalse(change.changed)
        self.assertIn("[WARN] Failed to set Git hook path. Post-merge hook may not fire", out.getvalue())


class SourceIdentityTests(SourceCase):
    def test_resolve_source_in_a_repo(self):
        # installer_lib/install.sh:538-551
        repo = init_repo(self.root / "repo")
        (repo / "sub").mkdir()
        with use_source(repo / "sub"):
            info = source.resolve_source()
        self.assertEqual(info.root, repo.resolve())
        self.assertEqual(info.branch, "main")
        self.assertEqual(info.head, git(repo, "rev-parse", "HEAD"))
        self.assertEqual(info.dirty, "clean")
        (repo / "x.txt").write_text("x", encoding="utf-8")
        with use_source(repo):
            self.assertEqual(source.resolve_source().dirty, "dirty")
        git(repo, "checkout", "-q", "--detach")
        with use_source(repo):
            self.assertEqual(source.resolve_source().branch, "DETACHED")

    def test_resolve_source_outside_git(self):
        plain = self.root / "plain"
        plain.mkdir()
        with use_source(plain):
            info = source.resolve_source()
        self.assertEqual(info, source.SourceInfo(root=plain, branch="DETACHED", head="unknown", dirty="unknown"))

    def test_update_source_calls_the_updater(self):
        # source_health.sh:81-89
        with mock.patch("installer.runner.run_tool", return_value=runner.ToolResult(returncode=3)) as run_tool:
            with use_source(self.root):
                self.assertEqual(source.update_source(), 3)
        run_tool.assert_called_once_with("_shared/installer_source_update.py", "--source-dir", str(self.root))

    def test_session_gate_contract_must_exist(self):
        # bootstrap.sh:5-10
        root = self.root / "src"
        (root / "skill-catalog").mkdir(parents=True)
        self.enterContext(use_source(root))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit) as stop:
            source.assert_session_gate_contract()
        self.assertEqual(stop.exception.code, 1)
        self.assertIn(f"[ERROR] Session gate contract file missing: {root / 'skill-catalog' / 'session-gates.json'}", out.getvalue())
        (root / "skill-catalog" / "session-gates.json").write_text("{}", encoding="utf-8")
        source.assert_session_gate_contract()


STOP_HINT = ("Rerun this command from the install source, or add --adopt-source to install from this checkout "
             "and record it as the install source.")


class InstallSourceTests(SourceCase):
    """The install record names the checkout the installation came from (`source.root`). Before an install or a
    repair changes anything, the installer compares that folder with the checkout it runs from, both with links
    followed: no record or no source goes on, the same folder goes on, another folder asks at a terminal and
    stops elsewhere."""

    def setUp(self):
        super().setUp()
        self.here = init_repo(self.root / "here")
        self.enterContext(use_source(self.here))
        self.other = init_repo(self.root / "other")
        self.home = self.root / "home"
        self.record = installed_payload.path(self.home / ".ghost-alice")
        self.ctx = make_context(self.home, "codex")

    def write_record(self, source_root: Path | None, **extra) -> None:
        record = {"schema_version": 1, "targets": {}, **extra}
        if source_root is not None:
            record["source"] = {"root": str(source_root), "commit": "c0ffee", "dirty": "clean", "hooks_path": "hooks"}
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text(json.dumps(record), encoding="utf-8")

    def run_check(self, ctx=None, *, interactive=False, answer=None):
        ctx = self.ctx if ctx is None else ctx
        out = io.StringIO()
        replies = mock.Mock(side_effect=answer if isinstance(answer, BaseException) else [answer])
        with contextlib.redirect_stdout(out), mock.patch("installer.source.interactive", return_value=interactive), \
                mock.patch("builtins.input", replies):
            try:
                source.check_install_source(ctx)
            except runner.InstallerExit as stop:
                return stop.code, out.getvalue(), replies
        return 0, out.getvalue(), replies

    def test_without_a_record_the_first_install_goes_on(self):
        self.assertEqual(source.match_install_source().state, "unrecorded")
        self.assertEqual(self.run_check()[:2], (0, ""))
        # A record that names no source (written before any publish) is the same as no record.
        self.write_record(None)
        self.assertEqual(source.match_install_source().state, "unrecorded")
        self.assertEqual(self.run_check()[:2], (0, ""))

    def test_the_recorded_checkout_goes_on_with_links_followed(self):
        link = self.root / "link-to-here"
        link.symlink_to(self.here, target_is_directory=True)
        self.write_record(link)
        match = source.match_install_source()
        self.assertEqual((match.state, match.here, match.recorded), ("same", self.here.resolve(), link))
        self.assertEqual(self.run_check()[:2], (0, ""))
        # The installer's own location is compared with its links followed too.
        self.write_record(self.here)
        with use_source(link):
            self.assertEqual(source.match_install_source().state, "same")

    def test_another_checkout_stops_a_non_interactive_command_with_exit_2_and_the_command_to_run(self):
        self.write_record(self.other)
        before = self.record.read_bytes()
        match = source.match_install_source()
        self.assertEqual((match.state, match.recorded), ("other", self.other))
        code, out, replies = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn("[ERROR] This checkout is not the install source the install record names.", out)
        self.assertIn(f"[ERROR]   Install source: {self.other}\n", out)
        self.assertIn(f"[ERROR]   This checkout:  {self.here.resolve()}\n", out)
        self.assertIn(f"[ERROR] {STOP_HINT}", out)
        replies.assert_not_called()
        self.assertEqual(self.record.read_bytes(), before)
        self.assertFalse(self.ctx.options.adopt_source)

    def test_a_recorded_checkout_that_no_longer_exists_is_replaced_without_a_question(self):
        # A fresh clone at another path after the old one was removed: nothing else can be the install source.
        gone = self.root / "removed-clone"
        self.write_record(gone)
        for interactive in (False, True):
            with self.subTest(interactive=interactive):
                ctx = make_context(self.home, "codex")
                code, out, replies = self.run_check(ctx, interactive=interactive, answer="n")
                self.assertEqual(code, 0)
                self.assertEqual(out, f"[INFO] Install source: {gone} no longer exists; this checkout replaces it\n")
                replies.assert_not_called()
                # The decision travels with the command, so the auto mode's children do not judge it again.
                self.assertTrue(ctx.options.adopt_source)

    def test_adopt_source_installs_from_another_checkout(self):
        self.write_record(self.other)
        ctx = make_context(self.home, "codex", adopt_source=True)
        code, out, replies = self.run_check(ctx)
        self.assertEqual(code, 0)
        self.assertIn(f"[INFO] Install source: this checkout replaces {self.other}", out)
        replies.assert_not_called()
        self.assertTrue(ctx.options.adopt_source)

    def test_a_terminal_asks_and_the_answer_decides(self):
        self.write_record(self.other)
        code, out, replies = self.run_check(interactive=True, answer="y")
        self.assertEqual(code, 0)
        self.assertEqual(replies.call_args.args, ("Replace the install source with this checkout? [y/N]: ",))
        self.assertIn(f"[WARN]   Install source: {self.other}\n", out)
        self.assertIn(f"[WARN]   This checkout:  {self.here.resolve()}\n", out)
        self.assertIn(f"[INFO] Install source: this checkout replaces {self.other}", out)
        # The answer travels with the command, so the auto mode's children do not ask again.
        self.assertTrue(self.ctx.options.adopt_source)
        for answer in ("", "n", "no", "maybe", EOFError()):
            with self.subTest(answer=answer):
                ctx = make_context(self.home, "codex")
                code, out, _ = self.run_check(ctx, interactive=True, answer=answer)
                self.assertEqual(code, 2)
                self.assertIn(f"[ERROR] {STOP_HINT}", out)
                self.assertFalse(ctx.options.adopt_source)

    def test_a_publish_that_stopped_halfway_does_not_decide_the_source(self):
        # The check runs before the lock, so before the lock restores unfinished publishes: it reads the record the
        # restore will put back (the copy the oldest unfinished publish saved), not the half-written one.
        self.write_record(self.here)
        older = self.home / ".ghost-alice" / "rollbacks" / "20261021T000000000000Z-1-aaaaaaaa"
        newer = self.home / ".ghost-alice" / "rollbacks" / "20261021T000001000000Z-1-bbbbbbbb"
        for folder in (older, newer):
            folder.mkdir(parents=True)
            shutil.copyfile(self.record, folder / installed_payload.FILENAME)
            (folder / "journal.json").write_text(json.dumps({
                "schema_version": 1, "phase": "prepare", "record_copy": installed_payload.FILENAME, "targets": []}),
                encoding="utf-8")
        self.write_record(self.other)
        self.assertEqual(source.match_install_source().state, "same")
        self.assertEqual(self.run_check()[:2], (0, ""))
        # The oldest unfinished publish found no record: after the restore there is none, so nothing is compared.
        (older / installed_payload.FILENAME).unlink()
        (older / "journal.json").write_text(json.dumps({"schema_version": 1, "phase": "prepare", "record_copy": None,
                                                        "targets": []}), encoding="utf-8")
        self.assertEqual(source.match_install_source().state, "unrecorded")
        # A journal that cannot be read leaves the record unknown; the lock's restore reports that journal.
        (older / "journal.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(source.match_install_source().state, "unreadable")

    def test_an_unreadable_record_skips_the_check_with_a_warning(self):
        # The comparison cannot be made; the command goes on as it did before the check existed.
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text("{broken", encoding="utf-8")
        self.assertEqual(source.match_install_source().state, "unreadable")
        code, out, _ = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn(f"[WARN] Install source check skipped: {self.record} is not JSON", out)

    def test_interactive_needs_a_terminal_on_both_ends(self):
        for stdin_tty, stdout_tty, expected in ((True, True, True), (True, False, False), (False, True, False)):
            with self.subTest(stdin=stdin_tty, stdout=stdout_tty), \
                    mock.patch("installer.source.sys.stdin") as stdin, mock.patch("installer.source.sys.stdout") as stdout:
                stdin.isatty.return_value, stdout.isatty.return_value = stdin_tty, stdout_tty
                self.assertEqual(source.interactive(), expected)


if __name__ == "__main__":
    unittest.main()
