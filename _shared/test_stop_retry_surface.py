"""Core Stop observes review defects without requesting response retries."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_completion_skill_evidence import assert_stop_observation


HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")
ANSWER = "Business result: 82 hours 40 minutes."
CHECK_WITHOUT_EVIDENCE = """[completion-check]
- acceptance-criteria:
  - calculation: recompute the operating time [source: user-said]
- claim-evidence-map:
  - claim: operating time was checked
    criterion: calculation
    verdict: pass
- unverified:
  - none
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
    def test_unknown_option_ends_with_a_usage_error(self):
        # Every Stop registration since v0.1.0 passes only --platform, through the runner that maps exit 2 to 1.
        result = subprocess.run([sys.executable, str(HOOK), "--platform", "claude", "--format", "json"],
                                input="{}", text=True, capture_output=True)
        self.assertEqual((result.returncode, result.stdout), (2, ""))

    def test_unlinked_retained_result_summary_does_not_supply_proof(self):
        HISTORICAL_CONVERSATION_SUMMARY = "The retained records cover targeted checks; overall verification remains unresolved."
        entries = [prompt("앞서 검증한 결과로 두 문제가 마무리됐는지 설명해 주세요."),
                   reply(HISTORICAL_CONVERSATION_SUMMARY)]
        assert_stop_observation(self, self.run_hook(entries, platform="codex", record_review=False))
        assert_stop_observation(self, self.run_hook(entries + [reply(HISTORICAL_CONVERSATION_SUMMARY +
            "\n\n별도 버그도 수정했습니다.")], platform="codex", record_review=False))

    def test_historical_closure_prose_is_not_a_submission(self):
        historical = "기존 검증 기록에 따르면 로컬 설치 수정을 완료했습니다. 원래 근거와 시각을 설명합니다."
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                entries = [prompt("Explain the prior installation result without new work."), reply(historical)]
                assert_stop_observation(self, self.run_hook(entries, platform=platform, record_review=False))
                entries.append(reply(historical + " 이번 수정도 완료했습니다."))
                assert_stop_observation(self, self.run_hook(entries, platform=platform, record_review=False))

    def test_partial_withdrawal_requires_review_and_cannot_supply_false_pass(self):
        partial = ("앞선 [completion-check]의 최종화 형식을 철회합니다. 상태는 부분 완료입니다. "
                   "일반 DBCS 자동 교정과 명시된 미검증 항목이 남아 있으며, 기존 검증 근거·시각·결론은 유지합니다. "
                   "추가 검증은 실행하지 않았습니다.")
        history = [prompt("Report the current result."), reply(DEFECTIVE), stop_feedback("Invalid evidence"), reply(partial)]
        payload = self.run_hook(history, platform="codex", record_review=False)
        assert_stop_observation(self, payload)
        payload = self.run_hook(history, platform="codex")
        assert_stop_observation(self, payload)
        payload = self.run_hook(history + [reply(partial + " 수정은 완료했습니다.")], platform="codex",
                                review_verdict="unsupported")
        assert_stop_observation(self, payload, issue="response-unsupported:")

    def test_metadata_repair_reports_each_format_defect_once(self):
        malformed = "[completion-check]\n- claim-evidence-map:\n  - claim: report verified\n    verdict: pass (9 checks)\n- unverified:\n  - pending evidence\n"
        payload = self.run_hook([prompt("Report the result."), reply(malformed)], platform="codex")
        observed = assert_stop_observation(self, payload, issue="include evidence",
                                           repair_target="completion_metadata")
        for expected in ("include evidence",
                         "Invalid verdict value", "unverified section"):
            self.assertEqual(observed["body_issue"].count(expected), 1)

    def run_hook(self, entries, entrypoint=None, platform="claude", host_context=None, claims_changed=False, scratchpad_dir=None,
                 record_review=True, review_verdict="supported", stop_hook_active=False):
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
                Path(temp_dir), platform, text, env, review_verdict, record_review=record_review)
            hook_input = {"session_id": "fixture-stop", "transcript_path": str(transcript),
                          "stop_hook_active": stop_hook_active, "last_assistant_message": text}
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

    def test_defective_proof_is_observed_without_delivery_requests_on_every_host(self):
        cases = [(ep, "claude-desktop") for ep in (*INTERACTIVE, "cli", "sdk-cli", "sdk-ts",
                 "sdk-py", "some-future-host")] + [(None, None), (None, "claude-desktop")]
        for platform in ("claude", "codex"):
            for entrypoint, recorded in cases:
                with self.subTest(platform=platform, entrypoint=entrypoint, recorded=recorded):
                    payload = self.run_hook([prompt("Compute it.", recorded), skill_call(), reply(DEFECTIVE)],
                                            entrypoint, platform=platform)
                    observed = assert_stop_observation(self, payload,
                        issue="Every claim-evidence-map entry must include evidence",
                        repair_target="completion_metadata")
                    self.assertNotIn("delivery-route:", observed["body_issue"])
                    self.assertNotIn("The prior answer is visible", observed["body_issue"])
                    self.assertNotIn("response_text", payload)

    def test_host_visibility_scratchpad_and_claim_delta_do_not_select_output_repair(self):
        options = ({"host_context": {"delivery_surface": "visible_transcript"}},
                   {"host_context": {"delivery_surface": "final_only"}},
                   {"host_context": {"delivery_surface": "unknown"}},
                   {"scratchpad_dir": "/tmp/native-probe-scratchpad"},
                   {"claims_changed": True})
        for platform in ("claude", "codex"):
            for entrypoint in ("cli", "claude-desktop", "sdk-cli", None):
                for supplied in options:
                    with self.subTest(platform=platform, entrypoint=entrypoint, supplied=supplied):
                        payload = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)],
                            entrypoint, platform=platform, **supplied)
                        observed = assert_stop_observation(self, payload,
                            issue="must include evidence", repair_target="completion_metadata")
                        self.assertNotIn("delivery-route:", observed["body_issue"])
                        self.assertNotIn("prior_answer_ref", observed)

    def test_unmarked_closure_prose_is_not_a_submission(self):
        payload = self.run_hook([prompt("Fix it."), reply(UNMARKED_CLOSURE)], "claude-desktop", record_review=False)
        assert_stop_observation(self, payload)
        self.assertNotIn("response_assessment", json.dumps(payload).replace('"response_assessment": {"input_event_id"', ''))

    def test_repeated_defects_remain_unaccepted_without_retry_notice_or_replay(self):
        for platform in ("claude", "codex"):
            for entrypoint in ("claude-desktop", "sdk-cli"):
                with self.subTest(platform=platform, entrypoint=entrypoint):
                    first = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE)],
                                          entrypoint, platform=platform)
                    observed = assert_stop_observation(self, first, issue="must include evidence")
                    second = self.run_hook([prompt("Compute it."), skill_call(), reply(DEFECTIVE),
                        stop_feedback(observed["body_issue"]), reply(CHECK_WITHOUT_EVIDENCE)],
                        entrypoint, platform=platform, stop_hook_active=True)
                    repeated = assert_stop_observation(self, second,
                        issue="must include evidence", repair_target="completion_metadata")
                    self.assertNotIn("after one retry", repeated["body_issue"])
                    self.assertNotIn("response_text", second)

    def test_old_or_unrelated_stop_feedback_does_not_change_current_assessment(self):
        old_issue = "Stop hook feedback: [completion-gate] old defect"
        other = "[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository."
        histories = ([prompt("Old report."), reply(DEFECTIVE), stop_feedback(old_issue), reply("Withdrawn."),
                      prompt("Now the second report."), skill_call(), reply(DEFECTIVE)],
                     [prompt("Compute it."), skill_call(), reply(DEFECTIVE), stop_feedback(other), reply(DEFECTIVE)])
        for platform in ("claude", "codex"):
            for entries in histories:
                with self.subTest(platform=platform, entries=entries):
                    payload = self.run_hook(entries, "claude-desktop", platform=platform)
                    assert_stop_observation(self, payload, issue="must include evidence",
                                            repair_target="completion_metadata")


if __name__ == "__main__":
    unittest.main()
