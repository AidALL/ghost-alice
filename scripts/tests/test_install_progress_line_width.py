from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PS1 = REPO_ROOT / "installer_lib" / "report.ps1"


class CommonTargetProgressLineFixedWidth(unittest.TestCase):
    """Guard against in-place progress-line residue regressions.

    Format-CommonTargetProgressLine receives suffixes with very different lengths
    ("For X [i/n]" at 16 characters versus "common targets synced on all platforms" at 38).
    The install.ps1 caller writes a carriage return and overwrites without clearing,
    so short frames can leave tails from earlier long frames unless the formatter pads
    to a fixed width independent of suffix length.
    """

    def test_formatter_pads_to_fixed_width_source(self) -> None:
        # Static guard: catch padding removal even without pwsh.
        source = REPORT_PS1.read_text(encoding="utf-8")
        match = re.search(
            r"function Format-CommonTargetProgressLine\b.*?\n\}",
            source,
            re.S,
        )
        self.assertIsNotNone(
            match, "Format-CommonTargetProgressLine definition not found"
        )
        body = match.group(0)
        self.assertIn(
            "PadRight",
            body,
            "fixed-width padding (PadRight) was removed; in-place progress-line residue risk",
        )

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
    def test_formatter_returns_fixed_width_runtime(self) -> None:
        def width(suffix: str) -> int:
            script = (
                f". '{REPORT_PS1.as_posix()}'; "
                f"(Format-CommonTargetProgressLine -DoneCount 25 -TotalCount 25 "
                f"-Suffix '{suffix}').Length"
            )
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return int(result.stdout.strip().splitlines()[-1])

        short = width("For claude [1/2]")
        long_suffix = width("common targets synced on all platforms")
        mid = width("common targets synced")
        self.assertEqual(
            short,
            long_suffix,
            "progress-line length varies by suffix; in-place residue regression",
        )
        self.assertEqual(short, mid, "progress line is not fixed width; in-place residue regression")


if __name__ == "__main__":
    unittest.main()
