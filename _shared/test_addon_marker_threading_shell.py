"""Shell e2e for plan task C-THREAD-1: a codex copy-mode addon install writes an
ownership marker attributed to the addon (owner=addon, addon_id=<id>), so
classify_skill_root(expected_addon_id=<id>) (plan task T2.9) proves addon
ownership end-to-end instead of failing closed.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_marker_threading_shell.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"
sys.path.insert(0, str(REPO_ROOT / "_shared"))

from installer_assets import OWNERSHIP_GHOST_ALICE_MANAGED, classify_skill_root  # noqa: E402


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


class CodexCopyModeMarkerTest(unittest.TestCase):
    def test_copy_mode_addon_marker_is_addon_attributed(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
            install = subprocess.run(
                [bash, str(REPO_ROOT / "install.sh"), "--platform", "codex",
                 "--addon-source", str(FIXTURE), "--skip-source-health", "task-router"],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180, check=False)
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)

            skill = Path(home) / ".agents" / "skills" / "noop"
            marker_path = skill / ".ghost-alice-install.json"
            self.assertTrue(marker_path.exists(), msg=install.stderr + install.stdout)
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["install_mode"], "copy")  # codex => copy mode
            self.assertEqual(marker["owner"], "addon", msg=str(marker))
            self.assertEqual(marker["addon_id"], "noop", msg=str(marker))
            self.assertEqual(marker["provided_kind"], "skill")
            # plan task T2.9 end-to-end: addon proves ownership of its own copied skill.
            self.assertEqual(
                classify_skill_root(skill, expected_addon_id="noop").ownership,
                OWNERSHIP_GHOST_ALICE_MANAGED)
            # a core skill copied alongside must NOT be addon-attributed.
            core_marker = json.loads(
                (Path(home) / ".agents" / "skills" / "task-router" / ".ghost-alice-install.json")
                .read_text(encoding="utf-8"))
            self.assertEqual(core_marker["owner"], "ghost-alice", msg=str(core_marker))
            self.assertIsNone(core_marker["addon_id"], msg=str(core_marker))


if __name__ == "__main__":
    unittest.main()
