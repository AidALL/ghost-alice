"""Shell e2e for plan T2.3: `install.sh --uninstall --addon <id>` removes the
addon's installed targets AND its sidecar (and resume + dep-block wiring loads).

Run: python3 -m pytest _shared/test_addon_uninstall_shell.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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


class ShellUninstallAddonTest(unittest.TestCase):
    def test_install_then_uninstall_addon(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"

            def run(*args):
                return subprocess.run(
                    [bash, str(REPO_ROOT / "install.sh"), *args],
                    cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=120, check=False)

            install = run("--platform", "claude", "--addon-source", str(FIXTURE),
                          "--skip-source-health", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            skill = Path(home) / ".claude" / "skills" / "noop"
            self.assertTrue(sidecar.exists(), msg=install.stderr + install.stdout)
            self.assertTrue(os.path.lexists(skill))

            uninstall = run("--platform", "claude", "--uninstall", "--addon", "noop")
            self.assertEqual(uninstall.returncode, 0, msg=uninstall.stderr + uninstall.stdout)
            self.assertFalse(os.path.lexists(skill), msg=uninstall.stderr + uninstall.stdout)
            self.assertFalse(sidecar.exists(), msg=uninstall.stderr + uninstall.stdout)

    def test_addon_flag_without_value_errors_not_full_uninstall(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"

            def run(*args):
                return subprocess.run([bash, str(REPO_ROOT / "install.sh"), *args], cwd=REPO_ROOT,
                                      env=env, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=120, check=False)

            run("--platform", "claude", "--addon-source", str(FIXTURE), "--skip-source-health", "task-router")
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            self.assertTrue(sidecar.exists())
            # `--addon` with NO id must error, NOT silently fall through to a full uninstall
            bad = run("--platform", "claude", "--uninstall", "--addon")
            self.assertNotEqual(bad.returncode, 0, msg=bad.stderr + bad.stdout)
            self.assertTrue(sidecar.exists(), msg="must NOT have full-uninstalled: " + bad.stderr + bad.stdout)
            self.assertTrue(os.path.lexists(Path(home) / ".claude" / "skills" / "noop"))


if __name__ == "__main__":
    unittest.main()
