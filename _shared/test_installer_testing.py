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
from installer_testing import checkout_copy  # noqa: E402

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
