"""Trace-format recovery preserves a valid business answer."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_completion_recovery import VALID
from test_completion_skill_evidence import skill_call, user
import test_completion_skill_evidence as skill_fixture
import global_rule_blocks

HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")
TRACE_FREE = VALID.split("[io-trace]", 1)[0]


class TraceDisplayRepairTests(unittest.TestCase):
    def test_native_latest_body_wins_over_unflushed_transcript(self):
        invalid = TRACE_FREE.replace("    evidence: python check.py exited 0\n", "")
        for platform in ("codex", "claude"):
            for key in ("last_assistant_message", "lastAssistantMessage", "prompt_response", "promptResponse"):
                for latest, stale, expected in ((TRACE_FREE, invalid, "allow"), (invalid, TRACE_FREE, "block")):
                    with self.subTest(platform=platform, key=key, expected=expected), tempfile.TemporaryDirectory() as temp:
                        path = Path(temp) / "unflushed-native-transcript.jsonl"
                        entries = [user("Check the fixture."), skill_call(),
                                   {"type": "assistant", "message": {"role": "assistant", "content": stale}}]
                        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
                        env = {k:v for k,v in os.environ.items() if not k.startswith(("GHOST_ALICE_", "CODEX_", "CLAUDE_"))}
                        skill_fixture.StopHookSkillEvidenceTests._review_format_fixture(Path(temp), platform, TRACE_FREE, env)
                        result = subprocess.run([sys.executable, "-B", str(HOOK), "--platform", platform],
                                                input=json.dumps({key: latest, "session_id":"fixture-stop", "transcript_path": str(path), "stop_hook_active": True}),
                                                text=True, capture_output=True, check=True, env=env)
                        payload = json.loads(result.stdout)
                        if expected == "allow":
                            self.assertEqual(payload, {"continue": True})
                        else:
                            self.assertEqual(payload.get("decision"), "block")

    def test_fresh_native_body_does_not_replace_core_review(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'no-skill.jsonl'
            path.write_text(json.dumps(user('Check the fixture.')) + '\n')
            env = {k:v for k,v in os.environ.items()
                   if not k.startswith(('GHOST_ALICE_', 'CODEX_', 'CLAUDE_'))}
            skill_fixture.StopHookSkillEvidenceTests._review_format_fixture(
                Path(temp), 'claude', TRACE_FREE, env, record_review=False)
            result = subprocess.run([sys.executable, '-B', str(HOOK), '--platform', 'claude'],
                input=json.dumps(dict(session_id='fixture-stop', last_assistant_message=TRACE_FREE,
                    transcript_path=str(path))), text=True, capture_output=True, check=True, env=env)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get('decision'), 'block')
        self.assertIn('assessment-required', payload['reason'])
        self.assertNotIn('Invoke the Claude Code Skill tool', payload['reason'])

    def hook(self, text, platform="codex", host="sdk-cli"):
        entries = [user("Recompute and report the result."), skill_call(),
                   {"type": "assistant", "message": {"role": "assistant", "content": text}}]
        with tempfile.TemporaryDirectory() as temp:
            transcript = Path(temp) / "transcript.jsonl"
            transcript.write_text("".join(json.dumps(e) + "\n" for e in entries))
            env = {k:v for k,v in os.environ.items() if not k.startswith(("GHOST_ALICE_", "CODEX_", "CLAUDE_"))}
            env["CLAUDE_CODE_ENTRYPOINT"] = host
            from completion_check_validator import validate_completion_text
            reviewed = TRACE_FREE if validate_completion_text(text, require_completion_check=True) else text
            skill_fixture.StopHookSkillEvidenceTests._review_format_fixture(Path(temp), platform, reviewed, env)
            result = subprocess.run(
                [sys.executable, "-B", str(HOOK), "--platform", platform],
                input=json.dumps({"session_id":"fixture-stop", "last_assistant_message": text, "transcript_path": str(transcript)}),
                text=True, capture_output=True, check=True,
                env=env,
            )
        return json.loads(result.stdout)

    def test_valid_completion_needs_no_display_trace(self):
        for platform in ("codex", "claude"):
            with self.subTest(platform=platform):
                self.assertEqual(self.hook(TRACE_FREE, platform), {"continue": True})

    def test_original_path_only_skill_trace_cannot_block_completion(self):
        text = TRACE_FREE + ("[io-trace]\n- skills-loaded: "
                             "[/Users/example/.agents/skills/verification-before-completion/SKILL.md]\n")
        self.assertEqual(self.hook(text), {"continue": True})

    def test_bad_evidence_repair_does_not_replay_business_payload(self):
        invalid = TRACE_FREE.replace("    evidence: python check.py exited 0\n", "")
        for platform in ("codex", "claude"):
            for host in ("cli", "sdk-cli", "unknown-host"):
                with self.subTest(platform=platform, host=host):
                    result = self.hook(invalid, platform, host)
                    self.assertEqual(result["decision"], "block")
                    self.assertIn("repair-target: completion_metadata", result["reason"])
                    self.assertIn("evidence", result["reason"])
                    self.assertIn("delivery-route:", result["reason"])
                    self.assertNotIn("only the missing or corrected control blocks", result["reason"])
                    self.assertNotIn("payload again", result["reason"])
                    self.assertNotIn("followed by [io-trace]", result["reason"])

    def test_codex_skill_audit_cannot_override_authenticated_response_review(self):
        for value in ("verification-before-completion-v2", "skipped verification-before-completion",
                      "verification-before-completion.fake"):
            with self.subTest(value=value):
                text = VALID.replace("skill-call: verification-before-completion (this turn)",
                                          "skill-call: " + value)
                result = skill_fixture.StopHookSkillEvidenceTests().run_hook(platform="codex", message=text)
                self.assertEqual(result, {"continue": True})

    def test_generated_platform_contracts_keep_trace_optional(self):
        repo = HOOK.parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            codex = root / "codex" / "AGENTS.md"
            claude = root / "claude" / "CLAUDE.md"
            global_rule_blocks.apply_codex_bootstrap(
                repo / "platforms/codex/AGENTS.md", codex,
                loader_path=repo / "platforms/codex/bootstrap.md",
            )
            global_rule_blocks.apply_claude_bootstrap(repo / "platforms/claude/CLAUDE.md", claude)
            for path in (root / "codex/ghost-alice-governance.md", claude):
                body = path.read_text()
                self.assertIn("Printed `[io-trace]` is optional", body)
                self.assertNotIn("End every normal response with `[io-trace]`", body)
                self.assertNotIn("Output an `[io-trace]` block at the end of every normal response", body)
                self.assertIn("The PostToolUse hook records strict audit data", body)
                self.assertIn("verification-before-completion", body)

    def test_new_turn_without_reply_cannot_revalidate_old_completion(self):
        for platform in ("codex", "claude"):
            for previous in (TRACE_FREE, "The requested work is complete."):
                with self.subTest(platform=platform, previous=previous):
                    entries = [user("Old request."), skill_call(),
                               {"type": "assistant", "message": {"role": "assistant", "content": previous}},
                               user("A new request without an assistant reply yet.")]
                    with tempfile.TemporaryDirectory() as temp:
                        path = Path(temp) / "transcript.jsonl"
                        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
                        result = subprocess.run(
                            [sys.executable, "-B", str(HOOK), "--platform", platform],
                            input=json.dumps({"transcript_path": str(path)}),
                            text=True, capture_output=True, check=True,
                        )
                    self.assertEqual(json.loads(result.stdout), {"continue": True})


if __name__ == "__main__":
    unittest.main()
