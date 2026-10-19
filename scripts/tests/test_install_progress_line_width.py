from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PS1 = REPO_ROOT / "installer_lib" / "report.ps1"

sys.path.insert(0, str(REPO_ROOT / "_shared"))
from installer import report  # noqa: E402


def _find_test_bash() -> str | None:
    candidates = [
        shutil.which("bash"),
        shutil.which("bash.exe"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        normalized = path.as_posix().lower()
        if sys.platform.startswith("win") and (
            normalized.endswith("/windows/system32/bash.exe")
            or normalized.endswith("/appdata/local/microsoft/windowsapps/bash.exe")
        ):
            continue
        return str(path)
    return None


class CommonTargetProgressLineUsesSemanticLength(unittest.TestCase):
    def test_formatter_does_not_pad_to_a_character_count(self) -> None:
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
        self.assertNotIn("PadRight", body)
        self.assertNotIn("fixed width", body.lower())

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
    def test_formatter_preserves_the_full_suffix_without_padding(self) -> None:
        def line(suffix: str) -> str:
            script = (
                f". '{REPORT_PS1.as_posix()}'; "
                f"Format-CommonTargetProgressLine -DoneCount 25 -TotalCount 25 "
                f"-Suffix '{suffix}'"
            )
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip().splitlines()[-1]

        short = line("For claude [1/2]")
        long_line = line("common targets synced on all platforms")
        self.assertTrue(short.endswith("For claude [1/2]"), short)
        self.assertTrue(
            long_line.endswith("common targets synced on all platforms"), long_line
        )
        self.assertNotEqual(len(short), len(long_line))

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
    def test_live_frame_clears_a_longer_previous_suffix(self) -> None:
        script = (
            f". '{REPORT_PS1.as_posix()}'; "
            "Write-CommonTargetProgressFrame -DoneCount 1 -TotalCount 2 -Suffix 'a deliberately longer progress suffix'; "
            "Write-CommonTargetProgressFrame -DoneCount 2 -TotalCount 2 -Suffix 'done'; "
            "Complete-CommonTargetProgressFrame"
        )
        result = subprocess.run(["pwsh", "-NoProfile", "-Command", script], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

        rendered: list[str] = []
        cursor = 0
        for character in result.stdout.decode("utf-8", errors="replace"):
            if character == "\r":
                cursor = 0
            elif character == "\n":
                break
            else:
                if cursor == len(rendered):
                    rendered.append(character)
                else:
                    rendered[cursor] = character
                cursor += 1
        expected = "        Common targets      [##############################] [2/2] done"
        self.assertEqual("".join(rendered).rstrip(), expected)


class PythonCommonTargetProgressLineUsesSemanticLength(unittest.TestCase):
    def test_live_updates_reuse_the_semantic_formatter(self) -> None:
        suffix = "a deliberately longer progress suffix"
        with mock.patch.object(report, "common_target_progress_line", return_value="LINE") as formatter, \
                contextlib.redirect_stdout(io.StringIO()):
            report.auto_update_common_target_progress_line(1, 2, suffix)
        formatter.assert_called_once_with(1, 2, suffix)

    def test_formatter_preserves_the_full_suffix(self) -> None:
        line = report.common_target_progress_line(26, 26, "common targets synced on all platforms")

        self.assertIn("Common targets", line)
        self.assertIn("[26/26]", line)
        self.assertTrue(line.endswith("common targets synced on all platforms"), line)


if __name__ == "__main__":
    unittest.main()
