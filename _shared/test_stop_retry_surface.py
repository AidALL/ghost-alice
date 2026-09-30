"""Claude Stop-hook retry shape: partial repair where earlier messages stay visible, one retry per turn."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")
ANSWER = "Business result: 82 hours 40 minutes."
CHECK_WITHOUT_EVIDENCE = """[completion-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- acceptance-criteria:
  - calculation: recompute the operating time [source: user-explicit]
- claim-evidence-map:
  - claim: operating time was checked
    criterion: calculation
    verdict: pass
- unverified:
  - none
[io-trace]
- skills-loaded: [verification-before-completion]
"""
DEFECTIVE = f"{ANSWER}\n\n{CHECK_WITHOUT_EVIDENCE}"
UNMARKED_CLOSURE = "작업을 완료했습니다."
INTERACTIVE = ("cli", "claude-desktop", "claude-vscode", "remote_desktop", "remote_mobile")


def prompt(text, entrypoint=None):
    entry = {"type": "user", "message": {"role": "user", "content": text}}
    if entrypoint:
        entry["entrypoint"] = entrypoint
    return entry


def skill_call():
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": "verification-before-completion"}}]}}


def reply(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def stop_feedback(reason):
    return {"type": "user", "isMeta": True, "message": {"role": "user", "content": f"Stop hook feedback:\n{reason}"}}


class StopRetrySurfaceTests(unittest.TestCase):
    def run_hook(self, entries, entrypoint=None, platform="claude"):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ENTRYPOINT"}
        if entrypoint is not None:
            env["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(HOOK), "--platform", platform],
                input=json.dumps({"transcript_path": str(transcript)}),
                text=True, capture_output=True, check=True, env=env,
            )
        return json.loads(result.stdout)

    def test_interactive_surface_asks_only_for_the_corrected_blocks(self):
        for entrypoint in INTERACTIVE:
            with self.subTest(entrypoint=entrypoint):
                payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)], entrypoint)
                self.assertEqual(payload.get("decision"), "block")
                self.assertIn("Every claim-evidence-map entry must include evidence", payload["reason"])
                self.assertIn("stays visible to the user", payload["reason"])
                self.assertIn("Do not repeat or rewrite it", payload["reason"])
                self.assertIn("only the missing or corrected control blocks", payload["reason"])
                self.assertIn("Do not invent evidence", payload["reason"])
                self.assertNotIn("complete standalone final answer", payload["reason"])

    def test_transcript_entrypoint_decides_when_the_environment_has_none(self):
        payload = self.run_hook([prompt("Compute it.", "claude-desktop"), skill_call(), reply(DEFECTIVE)])
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("only the missing or corrected control blocks", payload["reason"])

    def test_headless_or_unknown_surface_keeps_the_standalone_rewrite(self):
        # The process entrypoint outranks a transcript value; no entrypoint at all is unknown.
        cases = [(ep, "claude-desktop") for ep in ("sdk-cli", "sdk-ts", "sdk-py", "some-future-host")] + [(None, None)]
        for entrypoint, recorded in cases:
            with self.subTest(entrypoint=entrypoint):
                payload = self.run_hook([prompt("Compute it.", recorded), skill_call(), reply(DEFECTIVE)], entrypoint)
                self.assertEqual(payload.get("decision"), "block")
                self.assertIn("complete standalone final answer", payload["reason"])
                self.assertNotIn("only the missing or corrected control blocks", payload["reason"])

    def test_first_block_names_every_missing_part(self):
        payload = self.run_hook([prompt("Fix it."), reply(UNMARKED_CLOSURE)], "claude-desktop")
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("require a [completion-check]", payload["reason"])
        self.assertIn('{"skill": "verification-before-completion"}', payload["reason"])

    def test_second_defect_in_the_same_turn_ends_with_a_visible_notice(self):
        for entrypoint in ("claude-desktop", "sdk-cli"):
            with self.subTest(entrypoint=entrypoint):
                first = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)], entrypoint)
                self.assertEqual(first.get("decision"), "block")
                second = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE),
                                        stop_feedback(first["reason"]), reply(CHECK_WITHOUT_EVIDENCE)], entrypoint)
                self.assertTrue(second.get("continue"))
                self.assertNotIn("decision", second)
                self.assertIn("Every claim-evidence-map entry must include evidence", second.get("systemMessage", ""))
                self.assertIn("without a valid [completion-check]", second.get("systemMessage", ""))

    def test_block_from_an_earlier_turn_does_not_count(self):
        first = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)], "claude-desktop")
        payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE), stop_feedback(first["reason"]),
                                 reply("Withdrawn."), prompt("Now the second report."), skill_call(), reply(DEFECTIVE)],
                                "claude-desktop")
        self.assertEqual(payload.get("decision"), "block")

    def test_feedback_from_another_stop_hook_does_not_count(self):
        other = "[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository."
        payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE), stop_feedback(other),
                                 reply(DEFECTIVE)], "claude-desktop")
        self.assertEqual(payload.get("decision"), "block")

    def test_codex_retry_text_is_unchanged(self):
        result = subprocess.run(
            [sys.executable, "-B", str(HOOK), "--platform", "codex"],
            input=json.dumps({"last_assistant_message": DEFECTIVE}),
            text=True, capture_output=True, check=True,
            env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "claude-desktop"},
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("complete standalone final answer", payload["reason"])


if __name__ == "__main__":
    unittest.main()
