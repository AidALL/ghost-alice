from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_SH = REPO_ROOT / "installer_lib" / "report.sh"


def suite_label(platform_label: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f'source "{REPORT_SH}"; hook_suite_label "$1"', "_", platform_label],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    )
    return result.stdout.strip()


class ReportHookSuiteLabelTests(unittest.TestCase):
    def test_codex_report_lists_the_web_search_first_hook(self):
        self.assertIn("web-search-first", suite_label("codex"))

    def test_claude_report_does_not_list_a_hook_it_does_not_install(self):
        self.assertNotIn("web-search-first", suite_label("claude"))

    def test_shared_hooks_are_listed_for_every_platform(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                label = suite_label(platform)
                for hook in ("prompt", "session-intent", "tool-checkpoint", "completion", "session-start", "io-trace"):
                    self.assertIn(hook, label)


if __name__ == "__main__":
    unittest.main()
