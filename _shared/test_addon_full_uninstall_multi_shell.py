"""Scenario F3 (shell e2e): a FULL `install.sh --uninstall` (no --addon) with
multiple installed addons, ONE of which has an external change (re-pointed skill
symlink), must:

- preserve the drifted addon's target (the installer does not record the external change's writer),
- still remove the OTHER, clean addon,
- retain that addon's declared Core dependencies while removing independent Core targets, and
- exit nonzero so automation/the user sees the unfinished state.

This is the multi-addon counterpart of the single-addon full-uninstall-drift
test in test_addon_commands_resources_shell.py.

Run: python3 -m pytest _shared/test_addon_full_uninstall_multi_shell.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from _shared.test_addon_installer import _find_test_bash
from _shared.addon_uninstall import _is_reparse_point, _symlink_safe_remove
from _shared.install_transaction import _create_directory_link

REPO_ROOT = Path(__file__).resolve().parents[1]
RICH = REPO_ROOT / "_shared" / "tests" / "fixtures" / "rich-addon"
DUMMY = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"
if str(REPO_ROOT / "_shared") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "_shared"))
from installer_testing import checkout_copy, installer_env  # noqa: E402


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
    def test_full_uninstall_preserves_drift_and_dependencies_but_removes_independent_targets(self):
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")
        with __import__("tempfile").TemporaryDirectory() as home:
            env = installer_env(home)

            source = checkout_copy(self)

            def run(*args):
                return subprocess.run(
                    [bash, str(source / "install.sh"), *args], cwd=source,
                    env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=240, check=False)

            # Both addons depend on task-router; skill-evolution is independent.
            inst = run("--platform", "claude", "--addon-source", str(RICH),
                       "--addon-source", str(DUMMY), "--skip-source-health", "task-router", "skill-evolution")
            self.assertEqual(inst.returncode, 0, msg=inst.stderr + inst.stdout)

            rich_skill = Path(home) / ".claude" / "skills" / "richskill"
            noop_skill = Path(home) / ".claude" / "skills" / "noop"
            core_skill = Path(home) / ".claude" / "skills" / "task-router"
            independent = Path(home) / '.claude/skills/skill-evolution'
            rich_side = Path(home) / ".ghost-alice" / "addons" / "claude" / "rich.json"
            noop_side = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            self.assertTrue(rich_skill.is_symlink() or _is_reparse_point(rich_skill), msg="rich install precondition")
            self.assertTrue(noop_skill.is_symlink() or _is_reparse_point(noop_skill), msg="noop install precondition")
            self.assertTrue(core_skill.exists(), msg="core task-router install precondition")
            self.assertTrue(independent.exists(), msg='independent Core install precondition')

            self.assertEqual(sorted(path.stem for path in rich_side.parent.glob("*.json")), ["noop", "rich"])
            # The first addon in the uninstall order is drifted; the later clean addon must still be removed.
            user_dir = Path(home) / "my-noop"
            user_dir.mkdir()
            (user_dir / "SKILL.md").write_text("# USER OWNED\n", encoding="utf-8")
            _symlink_safe_remove(noop_skill)
            _create_directory_link(user_dir, noop_skill)

            # FULL uninstall (no --addon).
            full = run("--platform", "claude", "--uninstall")

            # The requested addon removal is incomplete even though independent cleanup proceeds.
            self.assertNotEqual(full.returncode, 0,
                                msg="full uninstall must report incomplete addon removal: " + full.stderr + full.stdout)
            # Drifted addon preserved (link + externally changed content + sidecar).
            self.assertTrue(noop_skill.is_symlink() or _is_reparse_point(noop_skill), msg="drifted noop skill link must be preserved")
            self.assertEqual(os.path.realpath(noop_skill), os.path.realpath(user_dir))
            self.assertEqual((noop_skill / "SKILL.md").read_text(encoding="utf-8"), "# USER OWNED\n")
            self.assertTrue(noop_side.exists(), msg="drifted addon sidecar must survive for the human user to review")
            # The other, clean addon was removed by the per-addon loop.
            self.assertFalse(os.path.lexists(rich_skill), msg="later clean addon should be removed")
            self.assertFalse(rich_side.exists(), msg="later clean addon sidecar should be removed")
            self.assertTrue(core_skill.exists(),
                            msg="the retained addon still depends on task-router")
            self.assertFalse(independent.exists(), msg='independent Core cleanup must proceed')


if __name__ == "__main__":
    unittest.main()
