"""Shell e2e for review finding M4 (plan task T2.7): `--doctor` must resume any
pending addon uninstall at its start, and must report NON-ok when a `.removing`
marker remains (a stuck uninstall), instead of silently saying overall ok.

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
        if not shutil.which("bash"):
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
            [shutil.which("bash"), str(REPO_ROOT / "install.sh"), *args], cwd=REPO_ROOT,
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, check=False)

    def test_doctor_resumes_clean_pending_marker(self):
        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            self._run(env, "--platform", "claude", "--addon-source", str(FIXTURE),
                      "--skip-source-health", "task-router")
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            marker = sidecar.with_name("noop.json.removing")
            marker.write_text('{"addon_id":"noop","stage":"removing","targets":["noop"]}',
                              encoding="utf-8")
            doctor = self._run(env, "--platform", "claude", "--doctor")
            # A clean (hash-matching) pending uninstall is completed by doctor:
            # marker cleared, sidecar removed.
            self.assertFalse(marker.exists(), msg="doctor did not resume the pending marker: "
                             + doctor.stderr + doctor.stdout)
            self.assertFalse(sidecar.exists())

    def test_doctor_nonzero_when_residual_marker_cannot_resume(self):
        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            self._run(env, "--platform", "claude", "--addon-source", str(FIXTURE),
                      "--skip-source-health", "task-router")
            skill = Path(home) / ".claude" / "skills" / "noop"
            sidecar = Path(home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            marker = sidecar.with_name("noop.json.removing")
            # Drift the target so resume preserves it (manual-review) -> marker stays.
            other = Path(home) / "user-noop"
            other.mkdir()
            (other / "SKILL.md").write_text("user", encoding="utf-8")
            skill.unlink()
            skill.symlink_to(other)
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
