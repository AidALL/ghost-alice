"""Shell e2e: addon uninstall failures must propagate through install.sh.

`install.sh --uninstall --addon <id>` must return a NONZERO exit code on failure
(unknown addon, or a partial/manual-review outcome) so automation can detect it.
The Python CLI already returns 1/2; the bash wrapper used to swallow it to 0.

Run: python3 -m pytest _shared/test_addon_uninstall_exit_code_shell.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"


def _python_311() -> bool:
    candidates = [sys.executable, shutil.which("python3"), "/opt/homebrew/bin/python3",
                  "/usr/local/bin/python3", "/usr/bin/python3"]
    for candidate in candidates:
        if candidate and subprocess.run(
            [candidate, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
            capture_output=True,
        ).returncode == 0:
            return True
    return False


class UninstallExitCodeTest(unittest.TestCase):
    def _env(self, home):
        env = os.environ.copy()
        env["HOME"] = home
        env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
        return env

    def _run(self, env, *args):
        return subprocess.run(
            [shutil.which("bash"), str(REPO_ROOT / "install.sh"), *args], cwd=REPO_ROOT,
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, check=False)

    def setUp(self):
        if not shutil.which("bash"):
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")

    def test_unknown_addon_uninstall_is_nonzero(self):
        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            # install something so hooks/state exist, then uninstall a NON-existent addon.
            self._run(env, "--platform", "claude", "--addon-source", str(FIXTURE),
                      "--skip-source-health", "task-router")
            res = self._run(env, "--platform", "claude", "--uninstall", "--addon", "ghost")
            self.assertNotEqual(res.returncode, 0, msg="unknown addon must fail: " + res.stderr + res.stdout)

    def test_resume_completed_uninstall_is_success_not_false_failure(self):
        # A prior partial uninstall left a .removing marker; the next uninstall's
        # resume completes it. The explicit pass then finds nothing, but the addon
        # IS gone -> must report success (rc 0), not a false failure.
        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            install = self._run(env, "--platform", "claude", "--addon-source", str(FIXTURE),
                                "--skip-source-health", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            skill = Path(home) / ".claude" / "skills" / "noop"
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            orig = os.readlink(skill)
            # drift -> first uninstall yields partial, leaving a .removing marker
            other = Path(home) / "user-noop"
            other.mkdir()
            (other / "SKILL.md").write_text("user", encoding="utf-8")
            skill.unlink()
            skill.symlink_to(other)
            self._run(env, "--platform", "claude", "--uninstall", "--addon", "noop")
            self.assertTrue(sidecar.with_name("noop.json.removing").exists())
            # restore the link so the hash matches again, then re-run uninstall
            skill.unlink()
            skill.symlink_to(orig)
            res = self._run(env, "--platform", "claude", "--uninstall", "--addon", "noop")
            self.assertEqual(res.returncode, 0,
                             msg="resume-completed uninstall must be success: " + res.stderr + res.stdout)
            self.assertFalse(os.path.lexists(skill))
            self.assertFalse(sidecar.exists())

    def test_partial_manual_review_uninstall_is_nonzero(self):
        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            install = self._run(env, "--platform", "claude", "--addon-source", str(FIXTURE),
                                "--skip-source-health", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            skill = Path(home) / ".claude" / "skills" / "noop"
            self.assertTrue(os.path.lexists(skill))
            # Drift the installed symlink so its hash no longer matches the sidecar
            # -> uninstall yields manual-review (partial), which must be nonzero.
            other = Path(home) / "user-noop"
            other.mkdir()
            (other / "SKILL.md").write_text("user", encoding="utf-8")
            skill.unlink()
            skill.symlink_to(other)
            res = self._run(env, "--platform", "claude", "--uninstall", "--addon", "noop")
            self.assertNotEqual(res.returncode, 0, msg="partial/manual-review must fail: " + res.stderr + res.stdout)
            # and the drifted target must be preserved (not clobbered).
            self.assertTrue(os.path.lexists(skill))


if __name__ == "__main__":
    unittest.main()
