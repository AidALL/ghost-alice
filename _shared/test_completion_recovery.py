"""Completion regressions from observed native sessions and contrast cases."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import completion_check_validator as validator


HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")
VALID = """Business result: 82 hours 40 minutes.

[completion-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- acceptance-criteria:
  - calculation: recompute the operating time [source: user-explicit]
- claim-evidence-map:
  - claim: operating time was checked
    criterion: calculation
    evidence: python check.py exited 0
    verdict: pass
- unverified:
  - none
[io-trace]
- skills-loaded: [verification-before-completion]
"""


class CompletionRecoveryTests(unittest.TestCase):
    def check(self, text):
        return validator.validate_completion_text(text, require_completion_check=True)

    def hook(self, text, *, retry=False, platform="codex"):
        result = subprocess.run(
            [sys.executable, "-B", str(HOOK), "--platform", platform],
            input=json.dumps({"last_assistant_message": text, "stop_hook_active": retry}),
            text=True, capture_output=True, check=True,
        )
        return json.loads(result.stdout)

    def test_korean_nominal_and_conditional_completion_are_not_assertions(self):
        # The first phrase is the observed short-answer false positive.
        for text in (
            "작업 간 완료 의존관계는 그대로입니다.",
            "작업 완료 시간은 14분입니다.",
            "작업 완료 여부에 따라 일정을 계산합니다.",
            "테스트 통과 조건을 설명합니다.",
            "빌드 성공 가능성을 비교합니다.",
            "작업이 완료되면 다음 단계가 시작됩니다.",
            "작업을 완료한 뒤 결과를 확인하세요.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.check(text))

    def test_genuine_korean_closure_still_requires_evidence(self):
        for text in (
            "작업 완료", "작업을 완료했습니다.", "작업이 완료되었습니다.",
            "수정했습니다.", "테스트 통과", "테스트를 통과했습니다.",
            "빌드 성공입니다.", "작업 완료했고 원인은 확인하지 못했습니다.",
            "작업 완료; 배포는 보류합니다.", "작업이 완료된 상태입니다.",
            "작업 완료 시간은 14분입니다. 버그를 고쳤습니다.",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(self.check(text))

    def test_korean_incomplete_status_does_not_start_stop_recovery(self):
        for text in (
            "수정 작업은 아직 미완료입니다.",
            "같은 종료 훅이 계속 재시도하고 있습니다. 현재 실행을 중지한 뒤 "
            "Autopilot 훅을 수동으로 임시 해제해야 합니다. 수정 작업은 아직 미완료입니다.",
            "설치본 적용과 Claude PTY 검증은 확인되지 않았습니다.",
        ):
            for platform in ("codex", "claude"):
                for retry in (False, True):
                    with self.subTest(text=text, platform=platform, retry=retry):
                        self.assertEqual(self.hook(text, retry=retry, platform=platform), {"continue": True})

    def test_korean_incomplete_status_keeps_real_claims_blocked(self):
        for text in (
            "수정했습니다. 테스트가 통과했습니다.",
            "빌드는 미완료지만 테스트가 통과했습니다.",
            "테스트가 통과했고 빌드는 미완료입니다.",
            "수정 작업은 아직 미완료입니다. 테스트가 통과했습니다.",
        ):
            for platform in ("codex", "claude"):
                for retry in (False, True):
                    with self.subTest(text=text, platform=platform, retry=retry):
                        self.assertEqual(self.hook(text, retry=retry, platform=platform).get("decision"), "block")

    def test_readonly_closure_requires_proof_in_actual_stop_entrypoint(self):
        for text in (
            "The read-only fixture review is complete.",
            "The read-only report is complete.",
            "I completed the read-only review.",
            "읽기 전용 검토 완료",
        ):
            for platform in ("codex", "claude"):
                for retry in (False, True):
                    with self.subTest(text=text, platform=platform, retry=retry):
                        payload = self.hook(text, retry=retry, platform=platform)
                        self.assertEqual(payload.get("decision"), "block")
                        self.assertIn("require a [completion-check]", payload["reason"])

    def test_readonly_nonclosure_does_not_start_stop_recovery(self):
        for text in (
            "Here are the tradeoffs for a read-only review; no work has been executed.",
            "Goal: the review is complete, the report is complete.",
            "The read-only fixture review is not complete.",
            "읽기 전용 검토는 아직 미완료입니다.",
            "Historical response:\n```text\nThe read-only fixture review is complete.\n```",
        ):
            for platform in ("codex", "claude"):
                for retry in (False, True):
                    with self.subTest(text=text, platform=platform, retry=retry):
                        self.assertEqual(self.hook(text, retry=retry, platform=platform), {"continue": True})

    def test_readonly_closure_retains_proof_validation_in_actual_stop_entrypoint(self):
        supported = VALID.replace("Business result: 82 hours 40 minutes.",
                                 "The read-only fixture review is complete.")
        self.assertEqual(self.hook(supported), {"continue": True})
        unsupported = supported.replace("    evidence: python check.py exited 0\n", "")
        payload = self.hook(unsupported)
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("must include evidence", payload["reason"])

    def test_predicate_scope_nonclosures_allow_actual_stop(self):
        for text in (
            "I have not finished the review.", "I haven't finished the report.",
            "I never finished the analysis.",
            "By tomorrow, I will have completed the read-only review.",
            "I will mark the review complete after checking the sources.",
        ):
            for platform in ("codex", "claude"):
                for retry in (False, True):
                    with self.subTest(text=text, platform=platform, retry=retry):
                        self.assertEqual(self.hook(text, platform=platform, retry=retry), {"continue": True})

    def test_predicate_scope_actual_assertions_still_block_in_stop(self):
        for text in (
            "The read-only report has been completed.", "I completed a read-only review.",
            "All reviews are complete.", "The analysis is now complete.",
            "I have not finished the review; the report has been completed.",
            "I will mark the review complete; the analysis is now complete.",
        ):
            for platform in ("codex", "claude"):
                for retry in (False, True):
                    with self.subTest(text=text, platform=platform, retry=retry):
                        self.assertEqual(self.hook(text, platform=platform, retry=retry).get("decision"), "block")

    def test_inline_none_is_equivalent_to_nested_none(self):
        self.assertIsNone(self.check(VALID.replace("- unverified:\n  - none", "- unverified: none")))

    def test_semicolon_skill_list_and_sentence_punctuation_are_equivalent(self):
        for value in ("task-router; verification-before-completion.",
                      "task-router; verification-before-completion; boundary-contract",
                      "verification-before-completion."):
            with self.subTest(value=value):
                self.assertIsNone(self.check(VALID.replace('skill-call: verification-before-completion (this turn)', 'skill-call: '+value)))

    def test_skill_call_prefix_or_mention_does_not_count_as_execution(self):
        for value in ("task-router; verification-before-completion-v2.",
                      "task-router; skipped verification-before-completion.",
                      "task-router; verification-before-completion.fake"):
            with self.subTest(value=value):
                self.assertIsNotNone(self.check(VALID.replace('skill-call: verification-before-completion (this turn)', 'skill-call: '+value)))

    def test_multiple_declared_criteria_accept_equivalent_delimiters(self):
        text = VALID.replace('- claim-evidence-map:', '  - boundary: Preserve the protected file. [source: user-explicit]\n- claim-evidence-map:')
        for value in ('calculation,boundary', 'calculation boundary', 'calculation; boundary'):
            with self.subTest(value=value):
                self.assertIsNone(self.check(text.replace('criterion: calculation', 'criterion: '+value)))
        for value in ('calculation; missing', ';', 'calculation; boundary; pending'):
            with self.subTest(value=value):
                self.assertIsNotNone(self.check(text.replace('criterion: calculation', 'criterion: '+value)))

    def test_semicolon_entry_retains_all_explicit_fields(self):
        text = VALID.replace(
            "  - claim: operating time was checked\n    criterion: calculation\n"
            "    evidence: python check.py exited 0\n    verdict: pass",
            "  - claim: operating time was checked; criterion: calculation; "
            "evidence: python check.py exited 0; verdict: pass",
        )
        self.assertIsNone(self.check(text))
        entries = validator.extract_claim_evidence_entries(
            validator.extract_control_block(text, "completion-check")
        )
        self.assertEqual(entries[0]["evidence"], "python check.py exited 0")

    def test_quoted_delimiter_is_evidence_not_a_fabricated_field(self):
        for quote in ('"', "'", "`"):
            text = VALID.replace(
                "    evidence: python check.py exited 0\n    verdict: pass",
                f"    evidence: {quote}echo ; verdict: pass{quote}; verdict: fail",
            )
            with self.subTest(quote=quote):
                self.assertIsNone(self.check(text))
                entries = validator.extract_claim_evidence_entries(
                    validator.extract_control_block(text, "completion-check")
                )
                self.assertEqual(entries[0]["verdict"], "fail")

    def test_missing_evidence_or_claim_is_never_inferred(self):
        for text in (
            VALID.replace("    evidence: python check.py exited 0\n", ""),
            VALID.replace("claim: operating time was checked", "claim: "),
            VALID.replace("evidence: python check.py exited 0", "evidence: "),
            VALID.replace("  - claim: operating time was checked\n    criterion: calculation\n"
                          "    evidence: python check.py exited 0\n    verdict: pass",
                          "  - calculation: Python output matched — verdict: pass"),
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(self.check(text))

    def test_none_prefix_cannot_hide_unverified_work(self):
        for value in ("none; pending result", "none but tests not run", "none\n  - pending result"):
            with self.subTest(value=value):
                self.assertIsNotNone(self.check(VALID.replace("  - none", "  - " + value)))

    def test_duplicate_sections_cannot_hide_conflicting_evidence(self):
        for extra in ("- unverified: pending verification", "- claim-evidence-map:",
                      "- acceptance-criteria:\n  - other: an omitted requirement"):
            with self.subTest(extra=extra):
                self.assertIsNotNone(self.check(VALID.replace("[io-trace]", extra + "\n[io-trace]")))

    def test_conflicting_or_duplicate_fields_do_not_overwrite(self):
        for replacement in (
            "    verdict: fail\n    verdict: pass",
            "    verdict: unverified; verdict: pass",
            "    evidence: different command\n    verdict: pass",
        ):
            with self.subTest(replacement=replacement):
                self.assertIsNotNone(self.check(VALID.replace("    verdict: pass", replacement)))

    def test_malformed_second_entry_does_not_hide_behind_valid_entry(self):
        text = VALID.replace("- unverified:", "  - calculation: missing explicit evidence\n- unverified:")
        self.assertIsNotNone(self.check(text))

    def test_honest_failed_verification_can_be_reported(self):
        text = VALID.replace("verdict: pass", "verdict: fail").replace("exited 0", "exited 1")
        self.assertIsNone(self.check(text))
        self.assertTrue(self.hook(text)["continue"])

    def test_punctuated_verdict_has_actionable_diagnostic_in_both_layouts_and_stop_attempts(self):
        multiline = VALID.replace("verdict: pass", "verdict: pass.")
        inline = multiline.replace(
            "  - claim: operating time was checked\n    criterion: calculation\n"
            "    evidence: python check.py exited 0\n    verdict: pass.",
            "  - claim: operating time was checked; criterion: calculation; "
            "evidence: python check.py exited 0; verdict: pass.",
        )
        for text in (multiline, inline):
            with self.subTest(text=text):
                diagnostic = self.check(text)
                self.assertIn("Invalid verdict value", diagnostic)
                self.assertIn("without punctuation", diagnostic)
                self.assertIsNone(self.check(text.replace("verdict: pass.", "verdict: pass")))
            for retry in (False, True):
                with self.subTest(text=text, retry=retry):
                    payload = self.hook(text, retry=retry)
                    self.assertEqual(payload.get("decision"), "block")
                    self.assertIn("Invalid verdict value", payload["reason"])
                    self.assertIn("without punctuation", payload["reason"])
                    self.assertIn("Preserve the substantive answer", payload["reason"])
                    self.assertIn("Do not invent evidence", payload["reason"])

    def test_missing_or_empty_verdict_keeps_distinct_missing_diagnostic(self):
        for text in (
            VALID.replace("    verdict: pass\n", ""),
            VALID.replace("verdict: pass", "verdict:"),
        ):
            with self.subTest(text=text):
                diagnostic = self.check(text)
                self.assertIn("Every claim-evidence-map entry must include verdict:", diagnostic)
                self.assertNotIn("Invalid verdict value", diagnostic)
                for retry in (False, True):
                    with self.subTest(retry=retry):
                        payload = self.hook(text, retry=retry)
                        self.assertEqual(payload.get("decision"), "block")
                        self.assertIn("Every claim-evidence-map entry must include verdict:", payload["reason"])
                        self.assertNotIn("Invalid verdict value", payload["reason"])

    def test_invalid_retry_is_not_accepted_as_verified_completion(self):
        invalid = VALID.replace("    evidence: python check.py exited 0\n", "")
        for retry in (False, True):
            with self.subTest(retry=retry):
                payload = self.hook(invalid, retry=retry)
                self.assertEqual(payload.get("decision"), "block")
                self.assertNotEqual(payload.get("continue"), True)

    def test_equivalent_inline_form_needs_no_repair_generation(self):
        payload = self.hook(VALID.replace("- unverified:\n  - none", "- unverified: none"))
        self.assertTrue(payload.get("continue"))
        self.assertNotIn("systemMessage", payload)

    def test_recovery_preserves_business_payload_and_does_not_invent_evidence(self):
        payload = self.hook(VALID.replace("    evidence: python check.py exited 0\n", ""))
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("complete standalone final answer", payload["reason"])
        self.assertIn("corrected metadata", payload["reason"])
        self.assertIn("Preserve the substantive answer", payload["reason"])
        self.assertIn("Do not invent evidence", payload["reason"])
        self.assertIn("withdraws unsupported", payload["reason"])

    def test_honest_unverified_retry_can_end_without_false_success(self):
        payload = self.hook("I have not verified that the tests pass. Verification remains unresolved.", retry=True)
        self.assertTrue(payload.get("continue"))
        self.assertNotIn("systemMessage", payload)

    def test_missing_skill_retry_fills_evidence_from_existing_results(self):
        # Blocked subjects re-read files they had just written because this retry text ordered a new check.
        result = subprocess.run(
            [sys.executable, "-B", str(HOOK), "--platform", "claude"],
            input=json.dumps({"last_assistant_message": VALID, "transcript_path": ""}),
            text=True, capture_output=True, check=True,
        )
        reason = json.loads(result.stdout)["reason"]
        self.assertIn('{"skill": "verification-before-completion"}', reason)
        self.assertIn("map each claim to evidence that already exists in this turn", reason)
        self.assertIn("never repeat a successful check just to fill a field", reason)
        self.assertNotIn("perform the fresh evidence check", reason)

    def test_missing_skill_reminder_can_retain_valid_earlier_business_evidence(self):
        result = subprocess.run(
            [sys.executable, "-B", str(HOOK), "--platform", "claude"],
            input=json.dumps({"last_assistant_message": VALID, "transcript_path": ""}),
            text=True, capture_output=True, check=True,
        )
        reason = json.loads(result.stdout)["reason"]
        self.assertIn("valid earlier results", reason)
        self.assertIn("original evidence times", reason)

    def test_explanation_label_never_bypasses_actual_closure_validation(self):
        payload = self.hook("[task-router]\n- work-kind: nonexecution\n\nExplanation: the task is complete.")
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("require a [completion-check]", payload["reason"])

    def test_fenced_completion_example_needs_no_recovery(self):
        payload = self.hook("Example:\n```text\n[completion-check]\n- claim-evidence-map:\n```\nUse that shape.")
        self.assertTrue(payload.get("continue"))
        self.assertNotIn("systemMessage", payload)

    def test_actual_markdown_examples_do_not_trigger_stop_recovery(self):
        for text in ("````text\n```\n[completion-check]\nThe task is complete.\n```\n````\n",
                     "    [completion-check]\n    The task is complete.\n",
                     "> Quoted example:\nThe task is complete.\n[completion-check]\n"):
            with self.subTest(text=text):
                payload = self.hook(text)
                self.assertTrue(payload.get("continue"))
                self.assertNotIn("systemMessage", payload)

    def test_long_example_preserves_stop_validation_of_outside_closure(self):
        text = "````text\n```\n[completion-check]\n```\n````\n\nThe task is complete."
        payload = self.hook(text)
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("require a [completion-check]", payload["reason"])

    def test_balanced_explicit_multiline_quote_needs_no_stop_recovery(self):
        for opening, closing in (("“", "”"), ('"', '"')):
            text = "A sample response is:\n" + opening + "The task is complete.\n[completion-check]\n- claim-evidence-map:\n" + closing
            with self.subTest(text=text):
                payload = self.hook(text)
                self.assertTrue(payload.get("continue"))
                self.assertNotIn("systemMessage", payload)

    def test_inline_quote_boundaries_cannot_bypass_stop_validation(self):
        for literal in ("OK.", "pending.", "(Next section)"):
            text = f'The command returned "{literal}"\n\nThe task is complete.\n\n"[io-trace]"'
            with self.subTest(text=text):
                payload = self.hook(text)
                self.assertEqual(payload.get("decision"), "block")
                self.assertIn("require a [completion-check]", payload["reason"])

    def test_inline_quotes_cannot_hide_a_malformed_unquoted_control_block(self):
        text = 'The command returned "OK."\n\n[completion-check]\n- claim-evidence-map:\n\n"[io-trace]"'
        payload = self.hook(text)
        self.assertEqual(payload.get("decision"), "block")

    def test_quoted_list_lazy_continuation_needs_no_stop_recovery(self):
        for prefix in ("> - ", "> 1. ", "> 1. > "):
            text = prefix + "Example output:\nThe task is complete.\n[completion-check]\n"
            with self.subTest(text=text):
                payload = self.hook(text)
                self.assertTrue(payload.get("continue"))
                self.assertNotIn("systemMessage", payload)

    def test_closed_nested_container_fence_needs_no_stop_recovery_for_lazy_quote(self):
        prefix = "> - > ```\n>   > code\n>   > ```\n>\n> A quoted paragraph.\n"
        for tail in ("The task is complete.\n[completion-check]\n", "> The task is complete.\n> [completion-check]\n"):
            with self.subTest(tail=tail):
                payload = self.hook(prefix + tail)
                self.assertTrue(payload.get("continue"))
                self.assertNotIn("systemMessage", payload)


if __name__ == "__main__":
    unittest.main()
