from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / "_shared"))
from installer import report  # noqa: E402


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
