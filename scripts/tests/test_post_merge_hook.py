"""hooks/post-merge: git runs this script after a successful merge in any worktree of a registered repository.

The installer copies the script to `git-hooks` in the Ghost-ALICE folder and names that folder in the repository's
shared core.hooksPath. The script calls the installed command `bin/ghost-alice` beside its own folder with
`--post-merge` and names the checkout the merge ran in; a merge never fails because of it.

The installer step decides what happens (the install when the merged checkout is the install source, one line
otherwise; `_shared/test_installer_cli.py` covers it). These tests replace the command with a stub that records how
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
{ printf '%s' "$0"; printf '|%s' "$@"
  printf '|GIT_DIR=%s|GIT_WORK_TREE=%s|stdin=%s\\n' "${GIT_DIR-unset}" "${GIT_WORK_TREE-unset}" "$(cat)"
} >> "$POST_MERGE_STUB_LOG"
exit "${POST_MERGE_STUB_EXIT:-0}"
"""
# A checkout's own hook file that installs from the checkout it sits in.
CHECKOUT_HOOK = """#!/usr/bin/env bash
echo "checkout-hook-ran $(pwd)" >> "$POST_MERGE_STUB_LOG"
"""


@unittest.skipIf(GIT is None, "git is required")
class HookFileTests(unittest.TestCase):
    def test_the_script_keeps_unix_line_endings_and_its_executable_bit(self):
        self.assertNotIn(b"\r", HOOK.read_bytes())
        attribute = subprocess.run([GIT, "-C", str(ROOT), "check-attr", "text", "eol", "--", "hooks/post-merge"],
                                   capture_output=True, text=True, check=True)
        self.assertEqual(attribute.stdout.splitlines(), ["hooks/post-merge: text: set", "hooks/post-merge: eol: lf"])
        listed = subprocess.run([GIT, "-C", str(ROOT), "ls-files", "-s", "--", "hooks/post-merge"],
                                capture_output=True, text=True, check=True)
        self.assertTrue(listed.stdout.startswith("100755 "), listed.stdout)


@unittest.skipIf(BASH is None or GIT is None or sys.platform == "win32", "bash and git on a POSIX host are required")
class PostMergeHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.log = self.root / "stub.log"
        # HOME names another folder: the script finds the command from its own place.
        home = self.root / "home"
        (home / ".codex").mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "GHOST_ALICE_"))}
        self.env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", POST_MERGE_STUB_LOG=str(self.log),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
                        GIT_CEILING_DIRECTORIES=str(self.root.parent))
        installed = self.root / "installed here" / ".ghost-alice"
        (installed / "git-hooks").mkdir(parents=True)
        shutil.copy2(HOOK, installed / "git-hooks" / "post-merge")
        (installed / "git-hooks" / "post-merge").chmod(0o755)
        self.command = installed / "bin" / "ghost-alice"
        self.command.parent.mkdir()
        self.command.write_text(STUB, encoding="utf-8")
        self.source = self.make_repo(self.root / "source repo", installed / "git-hooks")

    def git(self, cwd: Path, *args: str, check: bool = True, input: str | None = None,
            **env) -> subprocess.CompletedProcess:
        return subprocess.run([GIT, "-C", str(cwd), *args], env={**self.env, **env}, check=check,
                              capture_output=True, text=True, input=input)

    def commit_change(self, cwd: Path, branch: str, text: str) -> None:
        self.git(cwd, "checkout", "-q", "-b", branch)
        (cwd / "tracked.txt").write_text(text, encoding="utf-8")
        self.git(cwd, "commit", "-q", "-am", text.strip())

    def make_repo(self, repo: Path, hooks: Path) -> Path:
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "config", "maintenance.auto", "false")
        (repo / "hooks").mkdir()
        (repo / "hooks" / "post-merge").write_text(CHECKOUT_HOOK, encoding="utf-8")
        (repo / "hooks" / "post-merge").chmod(0o755)
        (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "one")
        self.git(repo, "config", "core.hooksPath", hooks.as_posix())
        self.commit_change(repo, "topic", "two\n")
        self.git(repo, "checkout", "-q", "main")
        return repo

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def call(self, checkout: Path) -> str:
        return (f"{self.command}|--post-merge|--merged-checkout|{checkout}"
                "|GIT_DIR=unset|GIT_WORK_TREE=unset|stdin=")

    def test_a_merge_calls_the_installed_command_with_the_merged_checkout(self):
        merged = self.git(self.source, "merge", "-q", "--ff-only", "topic")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual(self.calls(), [self.call(self.source)])
        self.assertEqual(merged.stderr, "")

    def test_a_merge_in_another_worktree_names_that_worktree(self):
        worktree = self.root / "other worktree"
        self.git(self.source, "worktree", "add", "-q", "-b", "other", str(worktree), "main")
        self.commit_change(worktree, "other-topic", "three\n")
        self.git(worktree, "checkout", "-q", "other")
        merged = self.git(worktree, "merge", "-q", "--ff-only", "other-topic")
        self.assertEqual(merged.returncode, 0)
        # The worktree's own hook file stays out, and the variables git exported for the merged worktree are cleared.
        self.assertEqual(self.calls(), [self.call(worktree)])

    def test_a_merge_with_an_explicit_git_dir_clears_the_variables_git_exports(self):
        merged = self.git(self.source, "--git-dir", str(self.source / ".git"), "--work-tree", str(self.source),
                          "merge", "-q", "--ff-only", "topic")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual(self.calls(), [self.call(self.source)])

    def test_the_command_reads_no_input_of_the_merge(self):
        merged = self.git(self.source, "merge", "-q", "--ff-only", "topic", input="typed\n")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual(self.calls(), [self.call(self.source)])

    def test_a_command_that_fails_never_fails_the_merge(self):
        merged = self.git(self.source, "merge", "-q", "--ff-only", "topic", POST_MERGE_STUB_EXIT="3")
        self.assertEqual(merged.returncode, 0)
        self.assertEqual((self.source / "tracked.txt").read_text(encoding="utf-8"), "two\n")
        self.assertEqual(self.calls(), [self.call(self.source)])
        self.assertEqual(merged.stderr,
                         "[ghost-alice post-merge] The installer exited 3; the merge itself succeeded.\n")

    def test_a_merge_without_the_installed_command_is_silent(self):
        self.command.unlink()
        merged = self.git(self.source, "merge", "-q", "--ff-only", "topic")
        self.assertEqual((merged.returncode, merged.stdout, merged.stderr), (0, "", ""))
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
