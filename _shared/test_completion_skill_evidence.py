#!/usr/bin/env python3
"""Claude Code checks the verification-before-completion call from the transcript, so its [completion-check] carries
no constant self-report lines. Codex has no visible Skill surface and keeps them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import completion_check_validator as validator

HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")
BODY = """[completion-check]
- acceptance-criteria:
  - c1: recompute the operating time [source: user-explicit]
- claim-evidence-map:
  - claim: operating time was checked
    criterion: c1
    evidence: python check.py exited 0
    verdict: pass
- unverified:
  - none
- evidence: python check.py exited 0
"""
IO_TRACE = "[io-trace]\n- commands-run: [python check.py]\n"
WITHOUT_SELF_REPORT = f"Business result: 82 hours 40 minutes.\n\n{BODY}{IO_TRACE}"
SELF_REPORT_LINES = (
    "- verification-before-completion: done\n"
    "- skill-call: verification-before-completion (this turn)\n"
)
WITH_SELF_REPORT = WITHOUT_SELF_REPORT.replace(
    "[completion-check]\n", f"[completion-check]\n{SELF_REPORT_LINES}", 1,
).replace("[io-trace]\n", "[io-trace]\n- skills-loaded: [verification-before-completion]\n", 1)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def skill_call():
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": "verification-before-completion"}}]}}


def reply(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class ValidatorOptionTests(unittest.TestCase):
    def check(self, text, **kwargs):
        return validator.validate_completion_text(text, require_completion_check=True, **kwargs)

    def test_default_keeps_requiring_the_self_report_for_codex(self):
        self.assertIn("verification-before-completion: done", self.check(WITHOUT_SELF_REPORT))

    def test_transcript_mode_accepts_a_block_without_self_report_lines(self):
        self.assertIsNone(self.check(WITHOUT_SELF_REPORT, require_skill_self_report=False))

    def test_transcript_mode_still_accepts_the_old_self_report_lines(self):
        self.assertIsNone(self.check(WITH_SELF_REPORT, require_skill_self_report=False))

    def test_transcript_mode_still_rejects_a_missing_evidence_field(self):
        broken = WITHOUT_SELF_REPORT.replace("    evidence: python check.py exited 0\n", "")
        self.assertIn("evidence", self.check(broken, require_skill_self_report=False))

    def test_transcript_mode_still_requires_io_trace_after_the_block(self):
        self.assertIn("io-trace", self.check(f"Result.\n\n{BODY}", require_skill_self_report=False))
        before = f"Result.\n\n{IO_TRACE}\n{BODY}"
        self.assertIn("io-trace", self.check(before, require_skill_self_report=False))

    def test_transcript_mode_still_rejects_gate_state_after_the_block(self):
        late = WITHOUT_SELF_REPORT + "\n[gate-state]\n- task-router: done\n"
        self.assertIn("gate-state", self.check(late, require_skill_self_report=False))


class StopHookSkillEvidenceTests(unittest.TestCase):
    def run_hook(self, entries=None, *, platform="claude", message=None):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ENTRYPOINT"}
        env["CLAUDE_CODE_ENTRYPOINT"] = "claude-desktop"
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = {"last_assistant_message": message} if message is not None else {}
            if entries is not None:
                transcript = Path(temp_dir) / "transcript.jsonl"
                transcript.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
                payload["transcript_path"] = str(transcript)
            result = subprocess.run([sys.executable, "-B", str(HOOK), "--platform", platform],
                                    input=json.dumps(payload), text=True, capture_output=True, check=True, env=env)
        return json.loads(result.stdout)

    def test_claude_allows_a_block_without_self_report_when_the_skill_was_called(self):
        payload = self.run_hook([user("Compute it."), skill_call(), reply(WITHOUT_SELF_REPORT)])
        self.assertTrue(payload.get("continue"))
        self.assertNotIn("decision", payload)

    def test_claude_still_allows_the_old_self_report_lines(self):
        payload = self.run_hook([user("Compute it."), skill_call(), reply(WITH_SELF_REPORT)])
        self.assertTrue(payload.get("continue"))

    def test_claude_blocks_when_the_skill_was_not_called_even_with_the_lines(self):
        payload = self.run_hook([user("Compute it."), reply(WITH_SELF_REPORT)])
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("completion-reminder", payload["reason"])

    def test_claude_reminder_does_not_ask_for_the_self_report_line(self):
        payload = self.run_hook([user("Compute it."), reply(WITHOUT_SELF_REPORT)])
        self.assertEqual(payload.get("decision"), "block")
        self.assertNotIn("skill-call: verification-before-completion (this turn)", payload["reason"])

    def test_claude_still_blocks_a_missing_evidence_field(self):
        broken = WITHOUT_SELF_REPORT.replace("    evidence: python check.py exited 0\n", "")
        payload = self.run_hook([user("Compute it."), skill_call(), reply(broken)])
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("evidence", payload["reason"])

    def test_codex_still_requires_the_self_report_lines(self):
        payload = self.run_hook(platform="codex", message=WITHOUT_SELF_REPORT)
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("verification-before-completion: done", payload["reason"])


if __name__ == "__main__":
    unittest.main()
