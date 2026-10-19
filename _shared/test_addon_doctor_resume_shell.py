"""Shell e2e: doctor reports a pending addon uninstall marker without resuming it,
and stays non-ok while a removal marker remains; the next install resumes the removal.

Run: python3 -m pytest _shared/test_addon_doctor_resume_shell.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _shared.test_addon_installer import _find_test_bash
from _shared.addon_uninstall import _symlink_safe_remove
from _shared.install_transaction import _create_directory_link

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


class DoctorResumeTest(unittest.TestCase):
    def setUp(self):
        self.bash = _find_test_bash()
        if not self.bash:
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")

    def _env(self, home):
        env = os.environ.copy()
        env["HOME"] = home
        env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
        return env

    def _run(self, env, *args):
        return subprocess.run(
            [self.bash, str(REPO_ROOT / "install.sh"), *args], cwd=REPO_ROOT,
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, check=False)

    def test_doctor_reports_a_clean_pending_marker_and_the_next_install_resumes_it(self):
        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            install = self._run(env, "--platform", "claude", "--addon-source", str(FIXTURE),
                                "--skip-source-health", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            marker = sidecar.with_name("noop.json.removing")
            marker.write_text('{"addon_id":"noop","stage":"removing","targets":["noop"]}',
                              encoding="utf-8")
            doctor = self._run(env, "--platform", "claude", "--doctor")
            # Doctor is read-only: it reports the leftover marker and leaves the addon in place.
            self.assertNotEqual(doctor.returncode, 0, msg=doctor.stderr + doctor.stdout)
            self.assertIn(".removing", doctor.stderr + doctor.stdout)
            self.assertTrue(marker.exists())
            self.assertTrue(sidecar.exists())
            # The next install resumes the clean (hash-matching) removal before it installs anything.
            reinstall = self._run(env, "--platform", "claude", "--skip-source-health", "task-router")
            self.assertEqual(reinstall.returncode, 0, msg=reinstall.stderr + reinstall.stdout)
            self.assertFalse(marker.exists())
            self.assertFalse(sidecar.exists())
            self.assertFalse(os.path.lexists(Path(home) / ".claude" / "skills" / "noop"))

    def test_doctor_nonzero_when_residual_marker_cannot_resume(self):
        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            self._run(env, "--platform", "claude", "--addon-source", str(FIXTURE),
                      "--skip-source-health", "task-router")
            skill = Path(home) / ".claude" / "skills" / "noop"
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            marker = sidecar.with_name("noop.json.removing")
            # Drift the target so a resume would preserve it (manual-review) -> marker stays.
            other = Path(home) / "user-noop"
            other.mkdir()
            (other / "SKILL.md").write_text("user", encoding="utf-8")
            _symlink_safe_remove(skill)
            _create_directory_link(other, skill)
            marker.write_text('{"addon_id":"noop","stage":"removing","targets":["noop"]}',
                              encoding="utf-8")
            doctor = self._run(env, "--platform", "claude", "--doctor")
            self.assertNotEqual(doctor.returncode, 0,
                                msg="doctor must be non-ok with a residual .removing marker: "
                                + doctor.stderr + doctor.stdout)
            self.assertTrue(marker.exists(), msg="residual marker should remain (manual-review)")
            self.assertTrue(os.path.lexists(skill), msg="drifted target must be preserved")


if __name__ == "__main__":
    unittest.main()
