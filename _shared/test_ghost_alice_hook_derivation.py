"""The dispatcher adds no permission decision or context to any tool call.

Dependencies: Python 3.11+ standard library; Node on PATH.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
HOOK = REPO / "_shared" / "ghost-alice-hook.mjs"


class DerivationTests(unittest.TestCase):
    def test_healthy_checkpoint_does_not_append_instruction_context(self) -> None:
        for platform in ("codex", "claude"):
            with self.subTest(platform=platform):
                out = subprocess.run(
                    ["node", str(HOOK), "--platform", platform, "--event", "PreToolUse"],
                    input=json.dumps({"session_id": "checkpoint", "tool_name": "Bash"}),
                    capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(out.stdout), {})

    def test_other_than_the_registered_options_end_with_a_usage_error(self) -> None:
        # The registration passes only --platform and --event PreToolUse through the runner, which maps exit 2 to 1;
        # the installer replaces the retired reminder and the pre-#84 tool-checkpoint forms.
        for options in (["--platform", "claude", "--event", "UserPromptSubmit", "--hook", "hook-reminder"],
                        ["--platform", "codex", "--event", "PreToolUse", "--hook", "tool-checkpoint",
                         "--marker", "[tool-checkpoint] pre-tool-check", "--session-intent-root", "/tmp/ledger"],
                        ["--platform", "claude"],
                        ["--platform", "gemini", "--event", "PreToolUse"]):
            with self.subTest(options=options):
                out = subprocess.run(["node", str(HOOK), *options], input=json.dumps({"session_id": "stale"}),
                                     capture_output=True, text=True, check=False)
                self.assertEqual((out.returncode, out.stdout), (2, ""))

    def test_no_tool_shape_gets_a_denial_or_added_context(self) -> None:
        # Core does not block tools: the host owns native permission, whatever the tool shape.
        tools = ({"tool_name": "shell", "tool_input": {"cmd": "rm -rf /tmp/ghost-alice"}},
                 {"tool_name": "Read", "tool_input": {"file_path": "README.md"}},
                 {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"}},
                 {"tool_name": "Skill", "tool_input": {"skill": "task-router"}})
        for platform in ("codex", "claude"):
            for tool in tools:
                with self.subTest(platform=platform, tool=tool["tool_name"]):
                    out = subprocess.run(["node", str(HOOK), "--platform", platform, "--event", "PreToolUse"],
                                         input=json.dumps({"session_id": "s-derive", **tool}),
                                         capture_output=True, text=True, check=False)
                    self.assertEqual((out.returncode, out.stderr), (0, ""))
                    self.assertEqual(json.loads(out.stdout), {})


if __name__ == "__main__":
    unittest.main()
