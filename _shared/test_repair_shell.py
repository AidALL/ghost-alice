"""Shell e2e: `--repair` is the only mutating reconciliation path.

It calls the ownership classifier before replacing anything.

- A managed target that went MISSING is re-provisioned (mutation).
- A target the user replaced (not cleanly managed) is LEFT UNTOUCHED (classify
  before replace) -- repair never clobbers user/domain content.

Run: python3 -m pytest _shared/test_repair_shell.py -q
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
RICH = REPO_ROOT / "_shared" / "tests" / "fixtures" / "rich-addon"


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


class RepairTest(unittest.TestCase):
    def setUp(self):
        if not shutil.which("bash"):
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")

    def _run(self, env, *args):
        return subprocess.run(
            [shutil.which("bash"), str(REPO_ROOT / "install.sh"), *args], cwd=REPO_ROOT,
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, check=False)

    def test_repair_reprovisions_missing_and_preserves_user(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"

            install = self._run(env, "--platform", "claude", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            skill = Path(home) / ".claude" / "skills" / "task-router"
            self.assertTrue(skill.is_symlink())

            # 1) target went missing -> repair must re-provision it
            skill.unlink()
            self.assertFalse(os.path.lexists(skill))
            repair = self._run(env, "--platform", "claude", "--repair")
            self.assertEqual(repair.returncode, 0, msg=repair.stderr + repair.stdout)
            self.assertTrue(os.path.lexists(skill), msg="repair did not re-provision missing target: "
                            + repair.stderr + repair.stdout)

            # 2) user replaced the managed slot with their own dir -> repair must NOT clobber it
            skill.unlink()
            skill.mkdir()
            (skill / "USER_FILE").write_text("mine\n", encoding="utf-8")
            repair2 = self._run(env, "--platform", "claude", "--repair")
            self.assertTrue((skill / "USER_FILE").exists(),
                            msg="repair clobbered a user-owned slot: " + repair2.stderr + repair2.stdout)
            self.assertFalse(skill.is_symlink())  # still the user's dir, not re-symlinked

    def test_repair_fixes_dangling_shared(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
            install = self._run(env, "--platform", "claude", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            shared = Path(home) / ".claude" / "skills" / "_shared"
            # a DANGLING _shared symlink is functionally missing (every skill resolves
            # shared modules through it) -> repair must restore it, not silently skip.
            shared.unlink()
            shared.symlink_to(Path(home) / "nonexistent-target")
            self.assertFalse(shared.exists())  # dangling
            repair = self._run(env, "--platform", "claude", "--repair")
            self.assertEqual(repair.returncode, 0, msg=repair.stderr + repair.stdout)
            self.assertTrue(shared.exists(), msg="repair left _shared dangling: " + repair.stdout)
            self.assertTrue((shared / "hash_utils.py").exists())  # resolves into the repo again

    def test_repair_reprovisions_missing_addon_targets(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
            install = self._run(env, "--platform", "claude", "--addon-source", str(RICH),
                                "--skip-source-health", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            skill = Path(home) / ".claude" / "skills" / "richskill"
            command = Path(home) / ".claude" / "commands" / "richcmd.md"
            resource = Path(home) / ".ghost-alice" / "resources" / "claude" / "rich" / "ref.txt"
            self.assertTrue(os.path.lexists(skill))
            self.assertTrue(command.exists())
            self.assertTrue(resource.exists())

            skill.unlink()
            command.unlink()
            resource.unlink()

            repair = self._run(env, "--platform", "claude", "--repair")

            self.assertEqual(repair.returncode, 0, msg=repair.stderr + repair.stdout)
            self.assertTrue(os.path.lexists(skill), msg="repair left addon skill missing: " + repair.stdout)
            self.assertTrue(command.exists(), msg="repair left addon command missing: " + repair.stdout)
            self.assertTrue(resource.exists(), msg="repair left addon resource missing: " + repair.stdout)


if __name__ == "__main__":
    unittest.main()
