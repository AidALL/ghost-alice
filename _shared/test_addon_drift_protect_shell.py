"""Shell e2e for review finding H1: reinstalling the SAME addon over a target the
user has drifted (repointed symlink / edited copy) must ABORT instead of silently
clobbering the user's change. The collision gate now hash-checks same-addon targets.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_drift_protect_shell.py -q
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


class DriftProtectTest(unittest.TestCase):
    def test_reinstall_over_drifted_target_aborts_and_preserves(self):
        if not shutil.which("bash"):
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"

            def run(*args):
                return subprocess.run(
                    [shutil.which("bash"), str(REPO_ROOT / "install.sh"), *args], cwd=REPO_ROOT,
                    env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=180, check=False)

            first = run("--platform", "claude", "--addon-source", str(FIXTURE),
                        "--skip-source-health", "task-router")
            self.assertEqual(first.returncode, 0, msg=first.stderr + first.stdout)
            skill = Path(home) / ".claude" / "skills" / "noop"

            # User drift: repoint the managed symlink at their own dir.
            user_dir = Path(home) / "my-noop"
            user_dir.mkdir()
            (user_dir / "SKILL.md").write_text("# USER OWNED\n", encoding="utf-8")
            skill.unlink()
            skill.symlink_to(user_dir)

            second = run("--platform", "claude", "--addon-source", str(FIXTURE),
                         "--skip-source-health", "task-router")
            # Install must abort (collision) rather than overwrite the drift.
            self.assertNotEqual(second.returncode, 0,
                                msg="reinstall over drift must abort: " + second.stderr + second.stdout)
            # The user's drifted link must be untouched.
            self.assertTrue(skill.is_symlink())
            self.assertEqual(os.path.realpath(skill), os.path.realpath(user_dir))
            self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "# USER OWNED\n")


if __name__ == "__main__":
    unittest.main()
