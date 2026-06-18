"""Shell e2e: addon command/resource lifecycle stays tied to the addon.

A rich addon's command and resource are provisioned on install, recorded in the
sidecar, verified by --doctor, and removed on per-addon uninstall with the skill.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_commands_resources_shell.py -q
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


class CommandResourceLifecycleTest(unittest.TestCase):
    def test_install_doctor_uninstall_command_and_resource(self):
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

            install = run("--platform", "claude", "--addon-source", str(RICH),
                          "--skip-source-health", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)

            command = Path(home) / ".claude" / "commands" / "richcmd.md"
            resource = Path(home) / ".ghost-alice" / "resources" / "claude" / "rich" / "ref.txt"
            skill = Path(home) / ".claude" / "skills" / "richskill"
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "rich.json"
            self.assertTrue(command.is_file(), msg="command not provisioned: " + install.stdout)
            self.assertTrue(resource.is_file(), msg="resource not provisioned")
            self.assertTrue(os.path.lexists(skill))

            rec = json.loads(sidecar.read_text(encoding="utf-8"))
            kinds = {(p["kind"], p["name"]) for p in rec["provided"]}
            self.assertIn(("command", "richcmd"), kinds)
            self.assertIn(("resource", "ref.txt"), kinds)

            # observational hook (Phase 4): recorded top-level + present in claude config
            self.assertEqual([h["marker"] for h in rec.get("hooks", [])], ["[addon:rich] richobs"])
            settings = json.loads((Path(home) / ".claude" / "settings.json").read_text(encoding="utf-8"))
            post = [h["command"] for e in settings["hooks"]["PostToolUse"] for h in e.get("hooks", [])]
            self.assertTrue(any("[addon:rich] richobs" in c for c in post), msg="addon hook not installed")

            # doctor verifies all recorded hashes are intact
            doctor = run("--platform", "claude", "--doctor")
            self.assertEqual(doctor.returncode, 0, msg=doctor.stderr + doctor.stdout)
            # tamper a resource -> doctor must flag content_hash mismatch
            resource.write_text("TAMPERED\n", encoding="utf-8")
            doctor2 = run("--platform", "claude", "--doctor")
            self.assertNotEqual(doctor2.returncode, 0, msg="doctor missed resource tamper")
            resource.write_text("rich addon resource fixture payload\n", encoding="utf-8")  # restore

            uninstall = run("--platform", "claude", "--uninstall", "--addon", "rich")
            self.assertEqual(uninstall.returncode, 0, msg=uninstall.stderr + uninstall.stdout)
            self.assertFalse(command.exists(), msg="command not removed on uninstall")
            self.assertFalse(resource.exists(), msg="resource not removed on uninstall")
            self.assertFalse(os.path.lexists(skill))
            self.assertFalse(sidecar.exists())
            # the observational hook must be removed from the platform config too
            settings_after = json.loads((Path(home) / ".claude" / "settings.json").read_text(encoding="utf-8"))
            post_after = [h["command"] for e in settings_after.get("hooks", {}).get("PostToolUse", [])
                          for h in e.get("hooks", [])]
            self.assertFalse(any("[addon:rich]" in c for c in post_after), msg="addon hook orphaned after uninstall")

    def test_full_uninstall_removes_command_and_resource(self):
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

            run("--platform", "claude", "--addon-source", str(RICH), "--skip-source-health", "task-router")
            command = Path(home) / ".claude" / "commands" / "richcmd.md"
            resource = Path(home) / ".ghost-alice" / "resources" / "claude" / "rich" / "ref.txt"
            self.assertTrue(command.is_file())
            self.assertTrue(resource.is_file())

            # FULL uninstall (no --addon) must not orphan the addon command/resource
            full = run("--platform", "claude", "--uninstall")
            self.assertEqual(full.returncode, 0, msg=full.stderr + full.stdout)
            self.assertFalse(command.exists(), msg="full uninstall orphaned the addon command")
            self.assertFalse(resource.exists(), msg="full uninstall orphaned the addon resource")

    def test_full_uninstall_fails_and_preserves_sidecar_when_addon_extra_modified(self):
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

            install = run("--platform", "claude", "--addon-source", str(RICH), "--skip-source-health", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            command = Path(home) / ".claude" / "commands" / "richcmd.md"
            resource = Path(home) / ".ghost-alice" / "resources" / "claude" / "rich" / "ref.txt"
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "rich.json"
            command.write_text("USER COMMAND EDIT\n", encoding="utf-8")
            resource.write_text("USER RESOURCE EDIT\n", encoding="utf-8")

            full = run("--platform", "claude", "--uninstall")

            self.assertNotEqual(full.returncode, 0, msg="full uninstall must not hide addon manual-review")
            self.assertEqual(command.read_text(encoding="utf-8"), "USER COMMAND EDIT\n")
            self.assertEqual(resource.read_text(encoding="utf-8"), "USER RESOURCE EDIT\n")
            self.assertTrue(sidecar.exists(), msg="sidecar evidence must survive partial addon uninstall")


if __name__ == "__main__":
    unittest.main()
