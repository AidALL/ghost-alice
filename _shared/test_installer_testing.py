"""installer_testing.checkout_copy and git_hook_paths.hook_paths: the copy of this checkout that end-to-end tests
install from, and the check that fails a test which changed the git hook paths of the checkout the tests run from."""
from __future__ import annotations

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
from git_hook_paths import hook_paths  # noqa: E402
import installer_testing  # noqa: E402
from installer_testing import checkout_copy, installer_env  # noqa: E402

GIT = shutil.which("git")


def git(cwd: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid")
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "maintenance.auto", "false")
    (path / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (path / "install.sh").chmod(0o755)
    git(path, "add", "-A")
    git(path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "one")
    return path.resolve()


def probe() -> unittest.TestCase:
    """A test case whose cleanups the tests below run by hand; defined here so discovery does not run it."""

    class Probe(unittest.TestCase):
        def runTest(self):
            pass

    return Probe()


@unittest.skipIf(GIT is None, "git is required")
class CheckoutCopyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repo = init_repo(self.root / "repo")

    def test_installer_child_uses_fixture_home_and_repository_despite_parent_overrides(self):
        sentinel = init_repo(self.root / "sentinel")
        home = self.root / "home"
        home.mkdir()
        inherited = {
            "GIT_DIR": str(sentinel / ".git"), "GIT_WORK_TREE": str(sentinel),
            "CLAUDE_CONFIG_DIR": str(sentinel / "claude"), "CODEX_HOME": str(sentinel / "codex"),
            "GHOST_ALICE_CONFIG": str(sentinel / "runtime.json"),
            "GHOST_ALICE_LEDGER_ROOT": str(sentinel / "ledger"),
            "CODEX_THREAD_ID": "parent-codex", "CLAUDE_SESSION_ID": "parent-claude",
            "CLAUDE_CODE_SESSION_ID": "parent-claude-code",
        }
        before = (sentinel / ".git" / "config").read_bytes()
        with mock.patch.dict(os.environ, inherited):
            original = dict(os.environ)
            env = installer_env(home)
            child = subprocess.run(
                [sys.executable, "-c", (
                    "import sys; from pathlib import Path; "
                    "sys.path.insert(0, sys.argv[1]); "
                    "from installer import source; "
                    "result = source._git(Path(sys.argv[2]), 'config', 'core.hooksPath', 'fixture-hooks'); "
                    "raise SystemExit(result.returncode)"
                ), str(Path(__file__).resolve().parent), str(self.repo)],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(dict(os.environ), original)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertEqual((sentinel / ".git" / "config").read_bytes(), before)
        self.assertEqual(git(self.repo, "config", "core.hooksPath"), "fixture-hooks")
        self.assertEqual(env["HOME"], str(home))
        self.assertEqual(env["TMPDIR"], str(home))
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(home / ".claude"))
        self.assertEqual(env["CODEX_HOME"], str(home / ".codex"))
        for name in inherited.keys() - {"CLAUDE_CONFIG_DIR", "CODEX_HOME"}:
            self.assertNotIn(name, env)

    def test_fixture_git_config_excludes_ambient_sources_and_accepts_explicit_overrides(self):
        home = self.root / "home"
        home.mkdir()
        (home / ".gitconfig").write_text("[fixture]\nowned = fixture-home\n", encoding="utf-8")
        ambient = self.root / "ambient-xdg" / "git" / "config"
        ambient.parent.mkdir(parents=True)
        ambient.write_text("[fixture]\nambient = outside-home\n", encoding="utf-8")
        system_config = self.root / "system.gitconfig"
        system_config.write_text("[fixture]\nsystem = outside-home\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(ambient.parent.parent),
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        }):
            original = dict(os.environ)
            env = installer_env(home)
            child = subprocess.run(
                [GIT, "config", "--get-regexp", r"^fixture\."], cwd=home,
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(dict(os.environ), original)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.splitlines(), ["fixture.owned fixture-home"])
        self.assertEqual(env["XDG_CONFIG_HOME"], str(home / ".config"))

        # Supply a temporary stand-in for machine configuration; the fixture disables that scope.
        env["GIT_CONFIG_SYSTEM"] = str(system_config)
        child = subprocess.run(
            [GIT, "config", "--get-regexp", r"^fixture\."], cwd=home,
            env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.splitlines(), ["fixture.owned fixture-home"])

        # A test can deliberately select its own global and system fixtures after building the defaults.
        env.update(GIT_CONFIG_GLOBAL=str(ambient), GIT_CONFIG_NOSYSTEM="0")
        child = subprocess.run(
            [GIT, "config", "--get-regexp", r"^fixture\."], cwd=home,
            env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(set(child.stdout.splitlines()), {
            "fixture.ambient outside-home", "fixture.system outside-home",
        })

    def test_the_shared_value_and_the_checkouts_own_value_are_read_by_file(self):
        worktree = self.root / "worktree"
        git(self.repo, "worktree", "add", "-q", "-b", "work", str(worktree))
        git(self.repo, "config", "core.hooksPath", "hooks")
        git(self.repo, "config", "extensions.worktreeConfig", "true")
        git(worktree, "config", "--worktree", "core.hooksPath", "/dev/null")
        common = self.repo / ".git"
        self.assertEqual(hook_paths(self.repo), {str(common / "config"): ["hooks"]})
        self.assertEqual(hook_paths(worktree), {
            str(common / "config"): ["hooks"],
            str(common / "worktrees" / "worktree" / "config.worktree"): ["/dev/null"],
        })

    def test_a_test_that_changes_the_checkouts_hook_path_fails_when_it_ends(self):
        with mock.patch.object(installer_testing, "CHECKOUT", self.repo), \
                mock.patch.object(installer_testing, "_copy_template", None):
            first = probe()
            copy = checkout_copy(first)
            # The copy is a repository of its own with the checkout's files: the install writes its configuration.
            self.assertEqual(git(copy, "rev-parse", "--show-toplevel"), str(copy))
            self.assertEqual(git(copy, "status", "--porcelain"), "")
            self.assertTrue((copy / "install.sh").stat().st_mode & stat.S_IXUSR)
            git(copy, "config", "core.hooksPath", (copy / "hooks").as_posix())
            self.assertTrue(first.doCleanups())
            self.assertFalse(copy.exists())

            second = probe()
            checkout_copy(second)
            git(self.repo, "config", "core.hooksPath", "hooks")
            self.assertFalse(second.doCleanups())


if __name__ == "__main__":
    unittest.main()
