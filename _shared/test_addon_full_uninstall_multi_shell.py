"""Scenario F3 (shell e2e): a FULL `install.sh --uninstall` (no --addon) with
multiple installed addons, ONE of which the user has drifted (re-pointed skill
symlink), must:

- preserve the drifted addon's target (not clobber the user's change),
- still remove the OTHER, clean addon,
- HALT before the core wipe (run_full_uninstall aborts when any addon needs
  manual review), so core skills survive for manual review, and
- exit nonzero so automation/the user sees the unfinished state.

This is the multi-addon counterpart of the single-addon full-uninstall-drift
test in test_addon_commands_resources_shell.py.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_full_uninstall_multi_shell.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RICH = REPO_ROOT / "_shared" / "tests" / "fixtures" / "rich-addon"
DUMMY = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"


def _python_311() -> bool:
    for candidate in (sys.executable, shutil.which("python3"), "/opt/homebrew/bin/python3",
                      "/usr/local/bin/python3", "/usr/bin/python3"):
        if candidate and subprocess.run(
            [candidate, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
            capture_output=True,
        ).returncode == 0:
            return True
    return False


class FullUninstallMultiAddonDriftTest(unittest.TestCase):
    def test_full_uninstall_halts_on_one_drifted_addon_preserving_it(self):
        if not shutil.which("bash"):
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")
        with __import__("tempfile").TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"

            def run(*args):
                return subprocess.run(
                    [shutil.which("bash"), str(REPO_ROOT / "install.sh"), *args], cwd=REPO_ROOT,
                    env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=240, check=False)

            # Install two addons (rich + noop) plus the core task-router dependency.
            inst = run("--platform", "claude", "--addon-source", str(RICH),
                       "--addon-source", str(DUMMY), "--skip-source-health", "task-router")
            self.assertEqual(inst.returncode, 0, msg=inst.stderr + inst.stdout)

            rich_skill = Path(home) / ".claude" / "skills" / "richskill"
            noop_skill = Path(home) / ".claude" / "skills" / "noop"
            core_skill = Path(home) / ".claude" / "skills" / "task-router"
            rich_side = Path(home) / ".ghost-alice" / "addons" / "claude" / "rich.json"
            noop_side = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            self.assertTrue(rich_skill.is_symlink(), msg="rich install precondition")
            self.assertTrue(noop_skill.is_symlink(), msg="noop install precondition")
            self.assertTrue(core_skill.exists(), msg="core task-router install precondition")

            # User drifts ONE addon: re-point richskill at their own dir.
            user_dir = Path(home) / "my-rich"
            user_dir.mkdir()
            (user_dir / "SKILL.md").write_text("# USER OWNED\n", encoding="utf-8")
            rich_skill.unlink()
            rich_skill.symlink_to(user_dir)

            # FULL uninstall (no --addon).
            full = run("--platform", "claude", "--uninstall")

            # Halted for manual review -> nonzero.
            self.assertNotEqual(full.returncode, 0,
                                msg="full uninstall must halt when an addon is drifted: " + full.stderr + full.stdout)
            # Drifted addon preserved (link + user content + sidecar).
            self.assertTrue(rich_skill.is_symlink(), msg="drifted rich skill link must be preserved")
            self.assertEqual(os.path.realpath(rich_skill), os.path.realpath(user_dir))
            self.assertEqual((rich_skill / "SKILL.md").read_text(encoding="utf-8"), "# USER OWNED\n")
            self.assertTrue(rich_side.exists(), msg="drifted addon sidecar must survive for manual review")
            # The other, clean addon was removed by the per-addon loop.
            self.assertFalse(os.path.lexists(noop_skill), msg="clean addon should be removed")
            self.assertFalse(noop_side.exists(), msg="clean addon sidecar should be removed")
            # Core wipe was gated: core skills survive until the addon drift is resolved.
            self.assertTrue(core_skill.exists(),
                            msg="core skills must survive a halted full uninstall (cleanup is gated)")


if __name__ == "__main__":
    unittest.main()
