"""Claude Stop-hook retry shape: field repair across all host types, one retry per turn."""

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
INTERACTIVE = ("claude-desktop", "claude-vscode", "remote_desktop", "remote_mobile")


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
    def test_unlinked_retained_result_summary_does_not_supply_proof(self):
        HISTORICAL_CONVERSATION_SUMMARY = "The retained records cover targeted checks; overall verification remains unresolved."
        entries = [prompt("앞서 검증한 결과로 두 문제가 마무리됐는지 설명해 주세요."),
                   reply(HISTORICAL_CONVERSATION_SUMMARY)]
        self.assertEqual(self.run_hook(entries, platform="codex", response_purpose="explanation", record_review=False).get("decision"), "block")
        self.assertEqual(self.run_hook(entries + [reply(HISTORICAL_CONVERSATION_SUMMARY +
            "\n\n별도 버그도 수정했습니다.")], platform="codex").get("decision"), "block")

    def test_historical_install_assertion_requires_runtime_proof(self):
        historical = "기존 검증 기록에 따르면 로컬 설치 수정을 완료했습니다. 원래 근거와 시각을 설명합니다."
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                entries = [prompt("Explain the prior installation result without new work."), reply(historical)]
                self.assertEqual(self.run_hook(entries, platform=platform).get("decision"), "block")
                entries.append(reply(historical + " 이번 수정도 완료했습니다."))
                self.assertEqual(self.run_hook(entries, platform=platform).get("decision"), "block")

    def test_partial_withdrawal_requires_review_and_cannot_supply_false_pass(self):
        partial = ("앞선 [completion-check]의 최종화 형식을 철회합니다. 상태는 부분 완료입니다. "
                   "일반 DBCS 자동 교정과 명시된 미검증 항목이 남아 있으며, 기존 검증 근거·시각·결론은 유지합니다. "
                   "추가 검증은 실행하지 않았습니다.")
        history = [prompt("Report the current result."), reply(DEFECTIVE), stop_feedback("Invalid evidence"), reply(partial)]
        payload = self.run_hook(history, platform="codex", response_purpose="explanation", record_review=False)
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("repair-target: response_review", payload["reason"])
        payload = self.run_hook(history, platform="codex", response_purpose="explanation")
        self.assertEqual(payload, {"continue": True})
        payload = self.run_hook(history + [reply(partial + " 수정은 완료했습니다.")], platform="codex",
                                response_purpose="explanation", review_verdict="unsupported")
        self.assertEqual(payload.get("decision"), "block")

    def test_metadata_repair_reports_each_format_defect_once(self):
        malformed = "[completion-check]\n- claim-evidence-map:\n  - claim: report verified\n    verdict: pass (9 checks)\n- unverified:\n  - pending evidence\n"
        payload = self.run_hook([prompt("Report the result."), reply(malformed)], platform="codex")
        self.assertEqual(payload.get("decision"), "block")
        for expected in ("include evidence",
                         "Invalid verdict value", "unverified section"):
            self.assertEqual(payload["reason"].count(expected), 1)

    def run_hook(self, entries, entrypoint=None, platform="claude", host_context=None, claims_changed=False, scratchpad_dir=None,
                 response_purpose="completion", record_review=True, review_verdict="supported", stop_hook_active=False):
        env = {k: v for k, v in os.environ.items()
               if k not in {"CLAUDE_CODE_ENTRYPOINT", "CODEX_THREAD_ID"}
               and not k.startswith("GHOST_ALICE_")}
        if entrypoint is not None:
            env["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
            from test_completion_skill_evidence import StopHookSkillEvidenceTests
            text = next(part['text'] for entry in reversed(entries)
                        for part in entry.get('message', {}).get('content', [])
                        if isinstance(part, dict) and part.get('type') == 'text')
            StopHookSkillEvidenceTests._review_format_fixture(
                Path(temp_dir), platform, text, env, review_verdict,
                response_purpose=response_purpose, record_review=record_review)
            hook_input = {"session_id": "fixture-stop", "transcript_path": str(transcript),
                          "stop_hook_active": stop_hook_active}
            if host_context is not None:
                hook_input["host_context"] = host_context
            if claims_changed:
                hook_input["claims_changed"] = True
            if scratchpad_dir is not None:
                hook_input["scratchpad_dir"] = scratchpad_dir
            result = subprocess.run(
                [sys.executable, "-B", str(HOOK), "--platform", platform],
                input=json.dumps(hook_input),
                text=True, capture_output=True, check=True, env=env,
            )
        return json.loads(result.stdout)

    def test_interactive_surface_asks_only_for_the_corrected_blocks(self):
        for entrypoint in INTERACTIVE:
            with self.subTest(entrypoint=entrypoint):
                payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)], entrypoint)
                self.assertEqual(payload.get("decision"), "block")
                self.assertIn("Every claim-evidence-map entry must include evidence", payload["reason"])
                self.assertIn("The prior answer is visible", payload["reason"])
                self.assertIn("without repeating that answer", payload["reason"])
                self.assertIn("delivery-route: preserve_answer_and_patch_metadata", payload["reason"])
                self.assertIn("repair-target: completion_metadata", payload["reason"])
                self.assertNotIn("delivery-route: assembled_complete_payload", payload["reason"])

    def test_recorded_entrypoint_does_not_reinstate_replay(self):
        payload = self.run_hook([prompt("Compute it.", "claude-desktop"), skill_call(), reply(DEFECTIVE)])
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("delivery-route: preserve_answer_and_patch_metadata", payload["reason"])

    def test_bare_cli_is_ambiguous_and_resolves_from_supplied_context(self):
        payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)], "cli")
        self.assertIn("If the answer is already visible", payload["reason"])
        self.assertNotIn("delivery-route: preserve_answer_and_patch_metadata", payload["reason"])

    def test_observed_visible_cli_only_patches_metadata(self):
        payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)], "cli",
                                host_context={"delivery_surface": "visible_transcript"})
        self.assertIn("delivery-route: preserve_answer_and_patch_metadata", payload["reason"])
        self.assertNotIn("delivery-route: assembled_complete_payload", payload["reason"])

    def test_native_interactive_cli_payload_preserves_visible_answer(self):
        # Stop payload fixture supplies cli and scratchpad_dir.
        payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)], "cli",
                                scratchpad_dir="/tmp/native-probe-scratchpad")
        self.assertIn("delivery-route: preserve_answer_and_patch_metadata", payload["reason"])
        self.assertNotIn("delivery-route: assembled_complete_payload", payload["reason"])

    def test_native_print_sdk_cli_remains_final_only_with_scratchpad(self):
        payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)], "sdk-cli",
                                scratchpad_dir="/tmp/native-probe-scratchpad")
        self.assertIn("delivery-route: assembled_complete_payload", payload["reason"])
        self.assertNotIn("delivery-route: preserve_answer_and_patch_metadata", payload["reason"])

    def test_substantive_claim_correction_needs_the_complete_corrected_answer(self):
        payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)], "claude-desktop",
                                claims_changed=True)
        self.assertIn("delivery-route: corrected_complete_answer", payload["reason"])
        self.assertNotIn("delivery-route: preserve_answer_and_patch_metadata", payload["reason"])

    def test_headless_and_unknown_surface_have_distinct_recovery(self):
        # The process entrypoint outranks a transcript value; no entrypoint at all is unknown.
        cases = [(ep, "claude-desktop") for ep in ("sdk-cli", "sdk-ts", "sdk-py", "some-future-host")] + [(None, None)]
        for entrypoint, recorded in cases:
            with self.subTest(entrypoint=entrypoint):
                payload = self.run_hook([prompt("Compute it.", recorded), skill_call(), reply(DEFECTIVE)], entrypoint)
                self.assertEqual(payload.get("decision"), "block")
                self.assertIn("delivery-route: assembled_complete_payload" if entrypoint in {"sdk-cli", "sdk-ts", "sdk-py"}
                              else "If the answer is already visible", payload["reason"])
                self.assertNotIn("delivery-route: preserve_answer_and_patch_metadata", payload["reason"])

    def test_first_block_names_every_missing_part(self):
        payload = self.run_hook([prompt("Fix it."), reply(UNMARKED_CLOSURE)], "claude-desktop")
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("requires a [completion-check]", payload["reason"])
        self.assertNotIn('Invoke the Claude Code Skill tool', payload["reason"])

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
                self.assertIn("after one retry", second.get("systemMessage", ""))
                self.assertIn("Core has not accepted the submitted completion proof", second["systemMessage"])
                self.assertIn("do not rerun unchanged business checks", second["systemMessage"])

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

    def test_codex_retry_uses_field_repair(self):
        payload = self.run_hook([prompt("Compute it."), reply(DEFECTIVE)], platform="codex",
                                host_context={"delivery_surface": "visible_transcript"})
        self.assertEqual(payload.get("decision"), "block")
        self.assertNotIn("delivery-route: assembled_complete_payload", payload["reason"])

    def test_codex_second_metadata_defect_does_not_restart_business_answer(self):
        payload = self.run_hook([prompt("Compute it."), reply(CHECK_WITHOUT_EVIDENCE)],
                                platform="codex", stop_hook_active=True)
        self.assertEqual(payload.get('decision'), 'block')
        self.assertIn('Every claim-evidence-map entry must include evidence', payload['reason'])
        self.assertIn('If the answer is already visible', payload['reason'])
        self.assertNotIn('Do not output a correction-only note', payload['reason'])


if __name__ == "__main__":
    unittest.main()
