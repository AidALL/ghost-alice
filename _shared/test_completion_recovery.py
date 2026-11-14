"""Declared completion routes and owned assessment without response retries."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import completion_check_validator as validator
from stop_hook_testing import HOOK, assert_stop_observation, stop_issue


VALID = """Business result: 82 hours 40 minutes.

[completion-check]
- acceptance-criteria:
  - calculation: recompute the operating time [source: user-said]
- claim-evidence-map:
  - claim: operating time was checked
    criterion: calculation
    evidence: python check.py exited 0
    verdict: pass
- unverified:
  - none
"""


class CompletionRecoveryTests(unittest.TestCase):
    def check(self, text):
        return validator.validate_completion_text(text, require_completion_check=True)

    def hook(self, text, *, retry=False, platform="codex", record_review=True, review_verdict="supported"):
        from stop_hook_testing import review_format_fixture
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('GHOST_ALICE_')
               and key not in {'CODEX_THREAD_ID', 'CLAUDE_CODE_ENTRYPOINT'}}
        with tempfile.TemporaryDirectory() as temporary:
            review_format_fixture(
                Path(temporary), platform, text, env, review_verdict, record_review=record_review)
            result = subprocess.run(
                [sys.executable, "-B", str(HOOK), "--platform", platform],
                input=json.dumps({"session_id": "fixture-stop", "last_assistant_message": text,
                                  "stop_hook_active": retry}),
                text=True, capture_output=True, check=True, env=env,
            )
        return json.loads(result.stdout)

    def test_closure_prose_without_a_block_is_not_a_submission(self):
        for text in ('The task is complete.', '작업을 완료했습니다.',
                     'The review is not complete.', '작업 완료 여부를 설명합니다.',
                     'No work has been executed. Here are the tradeoffs.',
                     '검증은 미완료이며 결과의 한계를 설명합니다.'):
            for platform in ('codex', 'claude'):
                with self.subTest(text=text, platform=platform):
                    payload = self.hook(text, platform=platform, record_review=False)
                    assert_stop_observation(self, payload)
                    self.assertEqual(set(payload), {"continue", "stop_notice"})

    def test_reviewed_explanation_does_not_infer_completion_from_prose(self):
        for text in ('The retained record says the review is complete.',
                     '기존 검증 기록에 따르면 검토를 완료했습니다.',
                     'The review is not complete.', '작업 완료 여부를 설명합니다.',
                     '[task-router]\n- work-kind: nonexecution\n\nExplanation: the task is complete.'):
            for platform in ('codex', 'claude'):
                with self.subTest(text=text, platform=platform):
                    assert_stop_observation(self, self.hook(text, platform=platform))

    def test_readonly_completion_retains_proof_and_review_validation(self):
        supported = VALID.replace('Business result: 82 hours 40 minutes.',
                                  'The read-only fixture review is complete.')
        assert_stop_observation(self, self.hook(supported))
        missing = self.hook(supported, record_review=False)
        assert_stop_observation(self, missing, issue='assessment-required:', repair_target='completion_metadata')
        unsupported = self.hook(supported, review_verdict='unsupported')
        assert_stop_observation(self, unsupported, issue='response-unsupported:')
        invalid = self.hook(supported.replace('    evidence: python check.py exited 0\n', ''))
        assert_stop_observation(self, invalid, issue='must include evidence', repair_target='completion_metadata')

    def test_inline_none_is_equivalent_to_nested_none(self):
        self.assertIsNone(self.check(VALID.replace("- unverified:\n  - none", "- unverified: none")))

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

    def test_none_prefix_cannot_hide_unverified_work(self):
        for value in ("none; pending result", "none but tests not run", "none\n  - pending result"):
            with self.subTest(value=value):
                self.assertIsNotNone(self.check(VALID.replace("  - none", "  - " + value)))

    def test_duplicate_sections_cannot_hide_conflicting_evidence(self):
        for extra in ("- unverified: pending verification", "- claim-evidence-map:",
                      "- acceptance-criteria:\n  - other: an omitted requirement"):
            with self.subTest(extra=extra):
                self.assertIsNotNone(self.check(VALID + extra + "\n"))

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
        assert_stop_observation(self, self.hook(text))

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
                    assert_stop_observation(self, payload, issue="Invalid verdict value",
                        repair_target="completion_metadata")
                    self.assertIn("without punctuation", stop_issue(payload))

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
                        assert_stop_observation(self, payload,
                            issue="Every claim-evidence-map entry must include verdict:",
                            repair_target="completion_metadata")
                        self.assertNotIn("Invalid verdict value", stop_issue(payload))

    def test_invalid_retry_is_not_accepted_as_verified_completion(self):
        invalid = VALID.replace("    evidence: python check.py exited 0\n", "")
        for retry in (False, True):
            with self.subTest(retry=retry):
                payload = self.hook(invalid, retry=retry)
                assert_stop_observation(self, payload, issue="must include evidence",
                    repair_target="completion_metadata")

    def test_metadata_defect_is_observed_without_requesting_another_answer(self):
        payload = self.hook(VALID.replace("    evidence: python check.py exited 0\n", ""))
        observed = assert_stop_observation(self, payload, issue="must include evidence",
                                          repair_target="completion_metadata")
        self.assertNotIn("delivery-route:", observed["body_issue"])
        self.assertNotIn("response_text", payload)

    def test_reviewed_unresolved_report_can_end_without_false_success(self):
        text = "I have not verified that the tests pass. Verification remains unresolved."
        assert_stop_observation(self, self.hook(text, retry=True))

    def test_skill_self_report_does_not_replace_authenticated_review(self):
        for platform in ('claude', 'codex'):
            with self.subTest(platform=platform):
                payload = self.hook(VALID, platform=platform, record_review=False)
                assert_stop_observation(self, payload, issue='assessment-required:', repair_target='completion_metadata')
                self.assertNotIn('Invoke the Claude Code Skill tool', stop_issue(payload))
                assert_stop_observation(self, self.hook(VALID, platform=platform))

    def test_fenced_completion_example_needs_no_recovery(self):
        payload = self.hook("Example:\n```text\n[completion-check]\n- claim-evidence-map:\n```\nUse that shape.")
        assert_stop_observation(self, payload)


if __name__ == "__main__":
    unittest.main()
