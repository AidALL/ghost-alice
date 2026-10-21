"""hooks/post-merge: after a successful merge the hook runs the merged checkout's installer step `--post-merge`,
and a merge never fails because of it.

The installer step decides what happens (the install in the install source, one line elsewhere;
`_shared/test_installer_cli.py` covers it). These tests replace `install.sh` with a stub that records how the hook
called it, so no installer runs and nothing outside the temporary folder changes.
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
printf '%s|%s\\n' "$0" "$*" >> "$POST_MERGE_STUB_LOG"
exit "${POST_MERGE_STUB_EXIT:-0}"
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
        self.repo = self.make_repo(self.root / "repo")

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
        (repo / "hooks").mkdir()
        shutil.copy2(HOOK, repo / "hooks" / "post-merge")
        (repo / "hooks" / "post-merge").chmod(0o755)
        (repo / "install.sh").write_text(STUB, encoding="utf-8")
        (repo / "install.sh").chmod(0o755)
        (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "one")
        # What the installer's git hook step writes: the repository setting every worktree inherits.
        self.git(repo, "config", "core.hooksPath", "hooks")
        self.commit_change(repo, "topic", "two\n")
        self.git(repo, "checkout", "-q", "main")
        return repo

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def test_a_merge_runs_the_merged_checkouts_post_merge_step(self):
        merged = self.git(self.repo, "merge", "-q", "--ff-only", "topic")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual(self.calls(), [f"{self.repo}/install.sh|--post-merge"])

    def test_an_installer_step_that_fails_never_fails_the_merge(self):
        merged = self.git(self.repo, "merge", "-q", "--ff-only", "topic", POST_MERGE_STUB_EXIT="3")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual((self.repo / "tracked.txt").read_text(encoding="utf-8"), "two\n")
        self.assertIn("[ghost-alice post-merge] The installer exited 3; the merge itself succeeded.",
                      merged.stdout + merged.stderr)

    def test_a_linked_worktree_runs_its_own_installer_step(self):
        worktree = self.root / "worktree"
        self.git(self.repo, "worktree", "add", "-q", "-b", "work", str(worktree), "main")
        self.commit_change(worktree, "work-topic", "three\n")
        self.git(worktree, "checkout", "-q", "work")
        merged = self.git(worktree, "merge", "-q", "--ff-only", "work-topic")
        self.assertEqual(merged.returncode, 0)
        # The installer step of the merged worktree judges whether that worktree is the install source.
        self.assertEqual(self.calls(), [f"{worktree}/install.sh|--post-merge"])

    def test_a_checkout_without_the_installer_is_left_alone(self):
        self.git(self.repo, "checkout", "-q", "-b", "no-installer")
        self.git(self.repo, "rm", "-q", "install.sh")
        self.git(self.repo, "commit", "-q", "-m", "drop installer")
        self.git(self.repo, "checkout", "-q", "main")
        merged = self.git(self.repo, "merge", "-q", "--no-edit", "no-installer")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertNotIn("post-merge", merged.stdout + merged.stderr)


if __name__ == "__main__":
    unittest.main()
