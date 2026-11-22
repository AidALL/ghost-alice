"""The install source owns the repository's git hook path, from the install to the full uninstall, with the real
installer in a temporary HOME.

The install source is a copy of this checkout in its own repository (`installer_testing.checkout_copy`), never this
checkout: the install writes the git hook path of the repository it runs from, which every worktree inherits.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_shared"))
from installer_testing import checkout_copy, installer_env, inject_installer_python  # noqa: E402
import installed_payload  # noqa: E402

BASH = shutil.which("bash")
GIT = shutil.which("git")
# What an earlier checkout's hook file did: run the installer of the checkout it sits in.
OLD_HOOK = """#!/usr/bin/env bash
touch "$HOME/old-hook-ran"
"""


@unittest.skipIf(BASH is None or GIT is None or sys.platform == "win32", "bash and git on a POSIX host are required")
class HookPathOwnershipEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.source = checkout_copy(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.env = {key: value for key, value in installer_env(self.home).items()
                    if not key.startswith(("CLAUDE_", "CODEX_"))
                    or key in {"CLAUDE_CONFIG_DIR", "CODEX_HOME"}}
        self.env.update(HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1", GHOST_ALICE_INSTALL_PROGRESS="off",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        self.record = installed_payload.path(self.home / '.ghost-alice')

    def install(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([BASH, str(self.source / "install.sh"), *args], cwd=self.source, env=self.env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)

    def git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([GIT, "-C", str(cwd), *args], env=self.env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)

    def shared_hook_path(self) -> str | None:
        found = self.git(self.source, "config", "--local", "--get", "core.hooksPath")
        return found.stdout.strip() if found.returncode == 0 else None

    def commit_file(self, cwd: Path, branch: str, name: str) -> None:
        """A new branch at `cwd` with one more commit that adds the file `name`."""
        self.assertEqual(self.git(cwd, "checkout", "-q", "-b", branch).returncode, 0)
        (cwd / name).write_text(f"{name}\n", encoding="utf-8")
        for args in (("add", name), ("commit", "-q", "-m", name)):
            done = self.git(cwd, *args)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_install_source_owns_the_hook_path_from_install_to_uninstall(self):
        # The value an earlier installer wrote: relative, so every worktree ran its own hook file.
        self.git(self.source, "config", "core.hooksPath", "hooks")
        installed = self.install("--platform", "claude", "--skip-source-health", "task-router")
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        record = json.loads(self.record.read_text(encoding="utf-8"))
        hooks = (Path(record['installer']['active_root']) / 'hooks').as_posix()
        self.assertEqual(self.shared_hook_path(), hooks)
        self.assertEqual((record["source"]["root"], record["source"]["hooks_path"]), (str(self.source), hooks))
        state = json.loads((self.home / ".ghost-alice" / "install-state" / "claude.json").read_text(encoding="utf-8"))
        change = next(item for item in state["system_env_changes"] if item["kind"] == "source_repo_hook_path")
        self.assertEqual((change["before_present"], change["after"]), (False, hooks))
        # The record of the write, made in the same step as the write, for an install that stops before the end.
        write_record = self.home / ".ghost-alice" / "install-state" / "source-repo-hook-path.json"
        self.assertEqual(json.loads(write_record.read_text(encoding="utf-8")), change)

        # A worktree whose own commit holds an older hook file: its merge runs the install source's hook, which
        # names the worktree to the install source's installer, and nothing is installed.
        worktree = self.root / "old-checkout"
        added = self.git(self.source, "worktree", "add", "-q", "-b", "old", str(worktree), "main")
        self.assertEqual(added.returncode, 0, added.stderr)
        (worktree / "hooks" / "post-merge").write_text(OLD_HOOK, encoding="utf-8")
        self.assertEqual(self.git(worktree, "commit", "-q", "-am", "old hook").returncode, 0)
        self.commit_file(worktree, "old-topic", "old-topic.txt")
        self.git(worktree, "checkout", "-q", "old")
        record_before = self.record.read_bytes()
        merged = self.git(worktree, "merge", "-q", "--ff-only", "old-topic")
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertIn(f"Post-merge: the merged checkout {worktree} is not the install source ({self.source}); "
                      "nothing was installed.", merged.stdout + merged.stderr)
        self.assertFalse((self.home / "old-hook-ran").exists(), merged.stdout + merged.stderr)
        self.assertEqual(self.record.read_bytes(), record_before)

        # A merge in the registered checkout installs the published main commit.
        self.git(self.source, 'checkout', '-q', '-b', 'topic')
        skill = self.source / 'task-router/SKILL.md'
        skill.write_text(skill.read_text() + '\nLocal merge content not published to a remote.\n')
        self.assertEqual(self.git(self.source, 'commit', '-q', '-am', 'local skill change').returncode, 0)
        self.git(self.source, "checkout", "-q", "main")
        # Observe the checkout of the Python process that sends the package its install request.
        inject_installer_python(self.env, """
import sys
def observe_request(event, args):
    if event == 'subprocess.Popen' and args[3]:
        request = args[3].get('GHOST_ALICE_INSTALL_REQUEST')
        if request and not os.environ.get('GHOST_ALICE_INSTALL_REQUEST'):
            import json
            if not json.loads(request).get('stage'):
                (Path.home() / 'request-writer').write_text(str(Path(sys.argv[0]).resolve()))
sys.addaudithook(observe_request)
""")
        merged = self.git(self.source, "merge", "-q", "--ff-only", "topic")
        self.assertEqual(merged.returncode, 0, merged.stderr)
        release_repo = Path(self.git(self.source, 'remote', 'get-url', 'origin').stdout.strip())
        head = self.git(release_repo, 'rev-parse', 'main').stdout.strip()
        record = json.loads(self.record.read_text(encoding="utf-8"))
        self.assertEqual((record["source"]["root"], record["source"]["commit"]), (str(self.source), head),
                         merged.stdout + merged.stderr)
        self.assertNotIn('Local merge content not published to a remote.',
                      (self.home / '.claude/skills/task-router/SKILL.md').read_text())
        self.assertEqual(self.shared_hook_path(), str(Path(record['installer']['active_root']) / 'hooks'))
        self.assertEqual(Path((self.home / 'request-writer').read_text()), Path(hooks).parent / '_shared/installer/cli.py')

        # The full uninstall of the last host puts back what was there before Ghost-ALICE: no value.
        removed = self.install("--uninstall")
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertIsNone(self.shared_hook_path())
        self.assertFalse(write_record.exists())


if __name__ == "__main__":
    unittest.main()
