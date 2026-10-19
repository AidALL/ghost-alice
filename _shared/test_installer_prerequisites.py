"""installer.prerequisites: git and node through the host package manager (installer_lib/prerequisites.sh)."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import prerequisites  # noqa: E402
from installer.runner import ToolResult  # noqa: E402
from installer_testing import FakeRunner, quiet  # noqa: E402


class PrerequisiteCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        quiet(self.home)
        self.runner = FakeRunner()
        self.available: set[str] = set()
        self.patches = contextlib.ExitStack()
        self.patches.enter_context(mock.patch("installer.prerequisites.shutil.which",
                                              side_effect=lambda name: f"/usr/bin/{name}" if name in self.available else None))
        self.patches.enter_context(mock.patch("installer.prerequisites.os.geteuid", return_value=1000, create=True))

    def tearDown(self):
        self.patches.close()
        self.temp.cleanup()

    def run_install(self):
        out = io.StringIO()
        with self.runner.patch(), contextlib.redirect_stdout(out):
            result = prerequisites.install_prerequisites()
        return result, out.getvalue()


class PrerequisiteTests(PrerequisiteCase):
    def test_present_tools_run_nothing(self):
        # prerequisites.sh:76-78
        self.available = {"git", "node", "brew"}
        ok, out = self.run_install()
        self.assertTrue(ok)
        self.assertEqual(self.runner.commands(), [])
        self.assertEqual(out, "")

    def test_brew_installs_git_and_node(self):
        # prerequisites.sh:82-89; test_install_runtime_detection.py:282-283
        self.available = {"brew"}
        ok, out = self.run_install()
        self.assertTrue(ok)
        self.assertEqual(self.runner.commands(), [["brew", "install", "git"], ["brew", "install", "node"]])
        self.assertIn("[INFO] git not found; trying automatic prerequisite setup.", out)
        self.assertIn("[INFO] brew install git", out)
        self.assertIn("[INFO] node not found; trying automatic prerequisite setup.", out)
        self.assertIn("[INFO] brew install node", out)
        self.assertNotIn("[WARN]", out)

    def test_apt_get_without_sudo_runs_directly(self):
        # prerequisites.sh:9-17, 91-98; test_install_runtime_detection.py:350-352
        self.available = {"apt-get"}
        ok, out = self.run_install()
        self.assertTrue(ok)
        self.assertEqual(self.runner.commands(), [["apt-get", "update"], ["apt-get", "install", "-y", "git"],
                                                  ["apt-get", "update"], ["apt-get", "install", "-y", "nodejs"]])
        self.assertIn("[INFO] apt-get install -y git", out)
        self.assertIn("[INFO] apt-get install -y nodejs", out)

    def test_apt_get_with_sudo(self):
        self.available = {"apt-get", "sudo"}
        self.run_install()
        self.assertEqual(self.runner.commands()[0], ["sudo", "apt-get", "update"])
        self.assertEqual(self.runner.commands()[1], ["sudo", "apt-get", "install", "-y", "git"])

    def test_root_runs_without_sudo(self):
        self.available = {"dnf", "sudo"}
        self.patches.enter_context(mock.patch("installer.prerequisites.os.geteuid", return_value=0, create=True))
        self.run_install()
        self.assertEqual(self.runner.commands(), [["dnf", "install", "-y", "git"], ["dnf", "install", "-y", "nodejs"]])

    def test_manager_order_and_windows_managers(self):
        # prerequisites.sh:109-152
        for manager, expected in (
            ("yum", ["yum", "install", "-y", "git"]),
            ("pacman", ["pacman", "-Sy", "--noconfirm", "git"]),
            ("winget.exe", ["winget.exe", "install", "--id", "Git.Git", "--exact", "--accept-package-agreements",
                            "--accept-source-agreements"]),
            ("choco.exe", ["choco.exe", "install", "git", "-y"]),
            ("scoop", ["scoop", "install", "git"]),
        ):
            with self.subTest(manager=manager):
                self.runner = FakeRunner()
                self.available = {manager, "node"}
                self.run_install()
                self.assertEqual(self.runner.commands()[0], expected)
        self.runner = FakeRunner()
        self.available = {"brew", "apt-get", "node"}
        self.run_install()
        self.assertEqual(self.runner.commands(), [["brew", "install", "git"]])

    def test_failure_prints_the_manual_notice_and_continues(self):
        # prerequisites.sh:19-42, 158-174
        self.available = {"brew", "node"}
        self.runner.respond(lambda s, a: s == "command", ToolResult(1))
        ok, out = self.run_install()
        self.assertFalse(ok)
        self.assertIn("[WARN] Git was not found and automatic setup did not complete. Install Git, then rerun the installer.", out)
        self.assertIn("[WARN]   macOS:   brew install git", out)
        self.assertIn("[WARN]   Windows: winget install --id Git.Git --exact", out)
        self.assertIn("[WARN] Continuing after prerequisite guidance. A later install step may still require the missing tool.", out)
        self.runner = FakeRunner()
        self.available = {"git"}
        ok, out = self.run_install()
        self.assertFalse(ok)
        self.assertIn("[WARN] Node.js was not found on PATH. Ghost-ALICE can continue with a configured Codex runtime, "
                      "but full capability needs Node.js installed on PATH.", out)
        self.assertIn("[WARN]   Download: https://nodejs.org/en/download", out)
        self.assertIn("[WARN]   Windows:  winget install --id OpenJS.NodeJS.LTS --exact", out)
        self.assertEqual(self.runner.commands(), [])

    def test_unknown_tool(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(prerequisites.try_install_unix_prerequisite("ruby"))
        self.assertIn("[WARN] Prerequisite 'ruby' is missing and automatic setup did not complete.", out.getvalue())


if __name__ == "__main__":
    unittest.main()
