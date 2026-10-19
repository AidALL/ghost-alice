"""installer.source: source health, git hook path, source identity, --update-source.

Expected texts come from installer_lib/source_health.sh and installer_lib/install.sh:538-551.
"""
from __future__ import annotations

import contextlib
import io
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
from installer import report  # noqa: E402
from installer import runner  # noqa: E402
from installer import source  # noqa: E402

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
            result = source.check_source_health(repo, skip=skip)
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
        hook = repo / "hooks" / "post-merge"
        hook.parent.mkdir()
        hook.write_text("#!/bin/sh\n", encoding="utf-8")
        hook.chmod(0o644)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            change = source.setup_git_hooks(repo)
        self.assertEqual(change, source.HookPathChange(changed=True, before_present=False, before="", after="hooks"))
        self.assertEqual(git(repo, "config", "--local", "core.hooksPath"), "hooks")
        self.assertIn("[INFO] Git hook path → hooks (post-merge auto-refresh enabled)", out.getvalue())
        self.assertTrue(hook.stat().st_mode & stat.S_IXUSR)
        with contextlib.redirect_stdout(io.StringIO()):
            again = source.setup_git_hooks(repo)
        self.assertFalse(again.changed)
        git(repo, "config", "--local", "core.hooksPath", "elsewhere")
        with contextlib.redirect_stdout(io.StringIO()):
            replaced = source.setup_git_hooks(repo)
        self.assertEqual(replaced, source.HookPathChange(changed=True, before_present=True, before="elsewhere", after="hooks"))

    def test_setup_git_hooks_outside_git_warns(self):
        plain = self.root / "plain"
        plain.mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            change = source.setup_git_hooks(plain)
        self.assertFalse(change.changed)
        self.assertIn("[WARN] Failed to set Git hook path. Post-merge hook may not fire", out.getvalue())


class SourceIdentityTests(SourceCase):
    def test_resolve_source_in_a_repo(self):
        # installer_lib/install.sh:538-551
        repo = init_repo(self.root / "repo")
        info = source.resolve_source(repo / "_shared" if (repo / "_shared").exists() else repo)
        self.assertEqual(info.root, repo.resolve())
        self.assertEqual(info.branch, "main")
        self.assertEqual(info.head, git(repo, "rev-parse", "HEAD"))
        self.assertEqual(info.dirty, "clean")
        (repo / "x.txt").write_text("x", encoding="utf-8")
        self.assertEqual(source.resolve_source(repo).dirty, "dirty")
        git(repo, "checkout", "-q", "--detach")
        self.assertEqual(source.resolve_source(repo).branch, "DETACHED")

    def test_resolve_source_outside_git(self):
        plain = self.root / "plain"
        plain.mkdir()
        info = source.resolve_source(plain)
        self.assertEqual(info, source.SourceInfo(root=plain, branch="DETACHED", head="unknown", dirty="unknown"))

    def test_update_source_calls_the_updater(self):
        # source_health.sh:81-89
        with mock.patch("installer.runner.run_tool", return_value=runner.ToolResult(returncode=3)) as run_tool:
            self.assertEqual(source.update_source(self.root), 3)
        run_tool.assert_called_once_with("_shared/installer_source_update.py", "--source-dir", str(self.root))

    def test_session_gate_contract_must_exist(self):
        # bootstrap.sh:5-10
        root = self.root / "src"
        (root / "skill-catalog").mkdir(parents=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit) as stop:
            source.assert_session_gate_contract(root)
        self.assertEqual(stop.exception.code, 1)
        self.assertIn(f"[ERROR] Session gate contract file missing: {root / 'skill-catalog' / 'session-gates.json'}", out.getvalue())
        (root / "skill-catalog" / "session-gates.json").write_text("{}", encoding="utf-8")
        source.assert_session_gate_contract(root)


if __name__ == "__main__":
    unittest.main()
