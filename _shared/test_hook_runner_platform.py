#!/usr/bin/env python3
"""Every platform hook entry names its platform to the hook runner, so strict logs land in that platform's folder."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import install_hooks

ENTRIES = {
    "prompt": (install_hooks._platform_hook_entry, "UserPromptSubmit"),
    "pending-merge-prompt": (install_hooks._platform_prompt_pending_merge_entry, "UserPromptSubmit"),
    "session-intent": (install_hooks._platform_session_intent_entry, "UserPromptSubmit"),
    "web-search-first": (install_hooks._platform_web_search_entry, "UserPromptSubmit"),
    "completion": (install_hooks._platform_stop_hook_entry, "Stop"),
    "session-start": (install_hooks._platform_session_start_entry, "SessionStart"),
}
RUNNER_ARGS = re.compile(r'hook_profile_gate\.py"?\s+"run"\s+"([^"]+)"\s+"([^"]+)"')


class HookRunnerPlatformTests(unittest.TestCase):
    def test_each_entry_passes_its_platform_to_the_runner(self):
        for platform in ("claude", "codex"):
            for hook_id, (build, event) in ENTRIES.items():
                with self.subTest(platform=platform, hook=hook_id):
                    match = RUNNER_ARGS.search(install_hooks._entry_command(build(platform, event)))
                    self.assertIsNotNone(match)
                    self.assertEqual(match.group(1), hook_id)
                    self.assertEqual(match.group(2), platform)

    @unittest.skipIf(os.name == "nt", "POSIX shell launcher")
    def test_prompt_hook_strict_log_lands_in_the_platform_folder(self):
        command = install_hooks._entry_command(install_hooks._platform_web_search_entry("claude", "UserPromptSubmit"))
        payload = json.dumps({"session_id": "s-platform-log", "prompt": "x", "hook_event_name": "UserPromptSubmit"})
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if k not in {"GHOST_ALICE_PLATFORM", "GHOST_ALICE_SESSION_ID"}}
            env["HOME"] = home
            result = subprocess.run(["/bin/sh", "-c", command], input=payload, capture_output=True, text=True,
                                    env=env, timeout=60)
            logs = Path(home) / ".ghost-alice" / "session-logs"
            written = sorted(str(p.relative_to(logs)) for p in logs.rglob("strict-hook-output.jsonl"))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(written, ["claude/s-platform-log/strict-hook-output.jsonl"])


if __name__ == "__main__":
    unittest.main()
