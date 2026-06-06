from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PS1 = REPO_ROOT / "installer_lib" / "report.ps1"
INSTALL_PS1 = REPO_ROOT / "install.ps1"

ANIMATE_FN = "Write-AutoAnimateCommonTargetProgress"


class AutoProgressTweenSource(unittest.TestCase):
    """Static guard against Windows auto-progress jump regressions.

    The shell installer uses report_auto_animate_target_operation_progress_line
    to tween one step at a time between milestones. The PowerShell port must
    call the per-step tween function from report.ps1 instead of jumping once
    per milestone from 0 to 25 to 50.
    """

    def test_report_defines_animate_function(self) -> None:
        source = REPORT_PS1.read_text(encoding="utf-8")
        self.assertIn(
            f"function {ANIMATE_FN}",
            source,
            f"{ANIMATE_FN} is not defined; per-step tween is missing (jump regression)",
        )

    def test_install_auto_loop_calls_animate_function(self) -> None:
        source = INSTALL_PS1.read_text(encoding="utf-8")
        # The install script must call the function, not define it.
        # The definition lives only in report.ps1, so a name occurrence in install.ps1 is a call.
        self.assertIn(
            ANIMATE_FN,
            source,
            f"install.ps1 auto path does not call {ANIMATE_FN}; milestone jump behavior remains",
        )


class AutoProgressTweenRuntime(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
    def test_animate_emits_one_frame_per_step(self) -> None:
        # From=0 and To=3 must emit frames 1, 2, and 3 in sequence; a single jump would emit only [3/3].
        script = (
            f". '{REPORT_PS1.as_posix()}'; "
            f"{ANIMATE_FN} -FromCount 0 -ToCount 3 -TotalCount 3 -Suffix 'tween-test'"
        )
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout

        for done in (1, 2, 3):
            self.assertIn(
                f"[{done}/3]",
                out,
                f"[{done}/3] frame was not emitted; this is not per-step tweening (jump)",
            )

        # Frame count is To-From = 3. Count appearances of the "Common targets" label.
        frame_count = len(re.findall(r"Common targets", out))
        self.assertEqual(
            frame_count,
            3,
            f"frame count is not 3 (={frame_count}); milestone jump regression",
        )

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
    def test_animate_no_extra_frame_when_from_equals_to(self) -> None:
        # From==To has no new step to draw (0 frames); avoid redundant emission.
        script = (
            f". '{REPORT_PS1.as_posix()}'; "
            f"{ANIMATE_FN} -FromCount 3 -ToCount 3 -TotalCount 3 -Suffix 'noop'"
        )
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        frame_count = len(re.findall(r"Common targets", result.stdout))
        self.assertEqual(
            frame_count,
            0,
            f"frames were drawn even though From==To (={frame_count}); redundant emission",
        )


if __name__ == "__main__":
    unittest.main()
