"""The install report names only the hooks every platform installs (`installer.report.hook_suite_label`)."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from installer.report import hook_suite_label as suite_label  # noqa: E402


class ReportHookSuiteLabelTests(unittest.TestCase):
    def test_codex_report_does_not_list_an_external_search_reminder(self):
        self.assertNotIn("web-search-first", suite_label("codex"))

    def test_claude_report_does_not_list_a_hook_it_does_not_install(self):
        self.assertNotIn("web-search-first", suite_label("claude"))

    def test_shared_hooks_are_listed_for_every_platform(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                label = suite_label(platform)
                for hook in ("session-intent", "tool-checkpoint", "completion", "session-start", "session-receipt", "io-trace"):
                    self.assertIn(hook, label)
                # The task-router reminder ("prompt") is retired.
                self.assertNotIn("prompt", label)


if __name__ == "__main__":
    unittest.main()
