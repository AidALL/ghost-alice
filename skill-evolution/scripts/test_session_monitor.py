"""Tests for skill-evolution session monitor.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
MONITOR = SCRIPT_DIR / "session_monitor.py"


class SessionMonitorTests(unittest.TestCase):
    def write_trace(self, rows: list[dict[str, object]]) -> pathlib.Path:
        temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="session-monitor-test-"))
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        trace = temp_dir / "io-trace.jsonl"
        trace.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        return trace

    def run_monitor(self, rows: list[dict[str, object]]) -> dict[str, object]:
        trace = self.write_trace(rows)
        completed = subprocess.run(
            [sys.executable, str(MONITOR), str(trace), "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_repeated_tool_pattern_in_recent_window_warns(self) -> None:
        rows = [
            {"session": "s1", "tool": "Bash", "pattern": "pytest tests/test_a.py"},
            {"session": "s1", "tool": "Read", "path": "/repo/a.py"},
            {"session": "s1", "tool": "Bash", "pattern": "pytest tests/test_a.py"},
            {"session": "s1", "tool": "Edit", "path": "/repo/a.py"},
            {"session": "s1", "tool": "Bash", "pattern": "pytest tests/test_a.py"},
        ]
        report = self.run_monitor(rows)
        signals = {signal["id"]: signal for signal in report["signals"]}

        self.assertEqual(signals["loop:tool-pattern"]["severity"], "warning")
        self.assertEqual(signals["loop:tool-pattern"]["evidence_count"], 3)

    def test_large_file_surface_and_repeated_failing_test_are_reported(self) -> None:
        rows = [
            {"session": "s2", "tool": "Read", "path": f"/repo/file-{index}.py"}
            for index in range(21)
        ]
        rows.extend(
            [
                {
                    "session": "s2",
                    "tool": "Bash",
                    "pattern": "pytest tests/test_b.py",
                    "exit_code": 1,
                }
                for _ in range(3)
            ]
        )
        report = self.run_monitor(rows)
        signals = {signal["id"]: signal for signal in report["signals"]}
        rendered = json.dumps(report, ensure_ascii=False)

        self.assertEqual(signals["scope:file-surface"]["severity"], "notice")
        self.assertEqual(signals["test:repeated-failure"]["severity"], "critical")
        self.assertEqual(signals["scope:file-surface"]["evidence_count"], 21)
        self.assertEqual(signals["test:repeated-failure"]["evidence_count"], 3)
        self.assertIn("21 distinct paths observed", signals["scope:file-surface"]["message"])
        self.assertIn("3 failures of the same command observed", signals["test:repeated-failure"]["message"])
        for signal_id in ("scope:file-surface", "test:repeated-failure"):
            self.assertEqual(signals[signal_id]["mode"], "report-only")
            self.assertIn("model first evaluates whether", signals[signal_id]["message"])
            self.assertIn("user's objective and current conditions", signals[signal_id]["message"])
        self.assertNotIn("verify scope before continuing", rendered)
        self.assertNotIn("switch to root-cause debugging", rendered)
        self.assertNotIn("automatically save files", rendered)
        self.assertNotIn("compact without asking the user", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
