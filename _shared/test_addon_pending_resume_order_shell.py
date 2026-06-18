"""Shell e2e: pending addon uninstall must resume before reinstall.

A leftover `.removing` marker must be resumed BEFORE the install step, so
re-running install can never delete the addon it just installed (which left
sidecar=present but skill=missing).

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_pending_resume_order_shell.py -q
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


class PendingResumeOrderTest(unittest.TestCase):
    def test_reinstall_with_pending_marker_keeps_installed_skill(self):
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
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            marker = sidecar.with_name("noop.json.removing")
            self.assertTrue(os.path.lexists(skill))
            self.assertTrue(sidecar.exists())

            # Simulate a crashed prior uninstall: a leftover .removing intent marker.
            marker.write_text('{"addon_id":"noop","stage":"removing","targets":["noop"]}',
                              encoding="utf-8")

            second = run("--platform", "claude", "--addon-source", str(FIXTURE),
                         "--skip-source-health", "task-router")
            self.assertEqual(second.returncode, 0, msg=second.stderr + second.stdout)

            # The just-installed skill must survive, the marker must be cleared,
            # and the sidecar must still describe a present skill (consistent state).
            self.assertTrue(os.path.lexists(skill),
                            msg="reinstall deleted the freshly installed skill: " + second.stderr + second.stdout)
            self.assertFalse(marker.exists(), msg="pending marker not cleared")
            self.assertTrue(sidecar.exists(), msg="sidecar missing after reinstall")


if __name__ == "__main__":
    unittest.main()
