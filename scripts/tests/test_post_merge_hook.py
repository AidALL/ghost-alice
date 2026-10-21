"""hooks/post-merge: git runs the install source's hook after a successful merge in any worktree, because the
installer writes the repository's shared core.hooksPath as the install source's hooks folder. The hook calls the
installer step `--post-merge` of the checkout that holds the hook file and names the checkout the merge ran in; a
merge never fails because of it.

The installer step decides what happens (the install when the merged checkout is the install source, one line
otherwise; `_shared/test_installer_cli.py` covers it). These tests replace `install.sh` with a stub that records how
the hook called it, so no installer runs and nothing outside the temporary folder changes.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "post-merge"
BASH = shutil.which("bash")
GIT = shutil.which("git")
STUB = """#!/usr/bin/env bash
printf '%s|%s|GIT_DIR=%s|GIT_WORK_TREE=%s\\n' "$0" "$*" "${GIT_DIR-unset}" "${GIT_WORK_TREE-unset}" \\
    >> "$POST_MERGE_STUB_LOG"
exit "${POST_MERGE_STUB_EXIT:-0}"
"""
# What an earlier checkout's hook file did: install from the checkout it sits in.
OLD_HOOK = """#!/usr/bin/env bash
echo "old-hook-ran $(pwd)" >> "$POST_MERGE_STUB_LOG"
"""


@unittest.skipIf(BASH is None or GIT is None or sys.platform == "win32", "bash and git on a POSIX host are required")
class PostMergeHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.log = self.root / "stub.log"
        home = self.root / "home"
        (home / ".codex").mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "GHOST_ALICE_"))}
        self.env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", POST_MERGE_STUB_LOG=str(self.log),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
                        GIT_CEILING_DIRECTORIES=str(self.root.parent))
        self.source = self.make_repo(self.root / "source")

    def git(self, cwd: Path, *args: str, check: bool = True, **env) -> subprocess.CompletedProcess:
        return subprocess.run([GIT, "-C", str(cwd), *args], env={**self.env, **env}, check=check,
                              capture_output=True, text=True)

    def commit_change(self, cwd: Path, branch: str, text: str) -> None:
        self.git(cwd, "checkout", "-q", "-b", branch)
        (cwd / "tracked.txt").write_text(text, encoding="utf-8")
        self.git(cwd, "commit", "-q", "-am", text.strip())

    def make_repo(self, repo: Path) -> Path:
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "config", "maintenance.auto", "false")
        (repo / "hooks").mkdir()
        shutil.copy2(HOOK, repo / "hooks" / "post-merge")
        (repo / "hooks" / "post-merge").chmod(0o755)
        (repo / "install.sh").write_text(STUB, encoding="utf-8")
        (repo / "install.sh").chmod(0o755)
        (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "one")
        # What the installer's git hook step writes: the install source's hooks folder, by absolute path, in the
        # repository setting every worktree inherits.
        self.git(repo, "config", "core.hooksPath", (repo / "hooks").as_posix())
        self.commit_change(repo, "topic", "two\n")
        self.git(repo, "checkout", "-q", "main")
        return repo

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def add_worktree_with_an_old_hook(self) -> Path:
        """A linked worktree whose own commit holds a hook file that installs from where it sits."""
        worktree = self.root / "old-checkout"
        self.git(self.source, "worktree", "add", "-q", "-b", "old", str(worktree), "main")
        (worktree / "hooks" / "post-merge").write_text(OLD_HOOK, encoding="utf-8")
        self.git(worktree, "commit", "-q", "-am", "old hook")
        self.commit_change(worktree, "old-topic", "three\n")
        self.git(worktree, "checkout", "-q", "old")
        return worktree

    def test_a_merge_in_the_install_source_names_it_as_the_merged_checkout(self):
        merged = self.git(self.source, "merge", "-q", "--ff-only", "topic")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual(self.calls(), [f"{self.source}/install.sh|--post-merge --merged-checkout {self.source}"
                                        "|GIT_DIR=unset|GIT_WORK_TREE=unset"])

    def test_a_merge_in_another_worktree_runs_the_install_sources_hook_and_installer(self):
        worktree = self.add_worktree_with_an_old_hook()
        merged = self.git(worktree, "merge", "-q", "--ff-only", "old-topic")
        self.assertEqual(merged.returncode, 0)
        # The worktree's own hook file and installer stay out; the install source's installer judges the worktree,
        # without the variables git exported for the merged worktree.
        self.assertEqual(self.calls(), [f"{self.source}/install.sh|--post-merge --merged-checkout {worktree}"
                                        "|GIT_DIR=unset|GIT_WORK_TREE=unset"])

    def test_a_merge_with_an_explicit_git_dir_keeps_the_installer_in_its_own_checkout(self):
        merged = self.git(self.source, "--git-dir", str(self.source / ".git"), "--work-tree", str(self.source),
                          "merge", "-q", "--ff-only", "topic")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual(self.calls(), [f"{self.source}/install.sh|--post-merge --merged-checkout {self.source}"
                                        "|GIT_DIR=unset|GIT_WORK_TREE=unset"])

    def test_an_installer_step_that_fails_never_fails_the_merge(self):
        merged = self.git(self.source, "merge", "-q", "--ff-only", "topic", POST_MERGE_STUB_EXIT="3")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual((self.source / "tracked.txt").read_text(encoding="utf-8"), "two\n")
        self.assertIn("[ghost-alice post-merge] The installer exited 3; the merge itself succeeded.",
                      merged.stdout + merged.stderr)

    def test_an_install_source_without_the_installer_is_left_alone(self):
        self.git(self.source, "checkout", "-q", "-b", "no-installer")
        self.git(self.source, "rm", "-q", "install.sh")
        self.git(self.source, "commit", "-q", "-m", "drop installer")
        self.git(self.source, "checkout", "-q", "main")
        merged = self.git(self.source, "merge", "-q", "--no-edit", "no-installer")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertNotIn("post-merge", merged.stdout + merged.stderr)


if __name__ == "__main__":
    unittest.main()
