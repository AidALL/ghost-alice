"""Declared completion routes and owned assessment without response retries."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import completion_check_validator as validator
from test_completion_skill_evidence import assert_stop_observation


HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")
VALID = """Business result: 82 hours 40 minutes.

[completion-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- acceptance-criteria:
  - calculation: recompute the operating time [source: user-said]
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

    def hook(self, text, *, retry=False, platform="codex", response_purpose="completion",
             record_review=True, review_verdict="supported"):
        from test_completion_skill_evidence import StopHookSkillEvidenceTests
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('GHOST_ALICE_')
               and key not in {'CODEX_THREAD_ID', 'CLAUDE_CODE_ENTRYPOINT'}}
        with tempfile.TemporaryDirectory() as temporary:
            StopHookSkillEvidenceTests._review_format_fixture(
                Path(temporary), platform, text, env, review_verdict,
                response_purpose=response_purpose, record_review=record_review)
            result = subprocess.run(
                [sys.executable, "-B", str(HOOK), "--platform", platform],
                input=json.dumps({"session_id": "fixture-stop", "last_assistant_message": text,
                                  "stop_hook_active": retry}),
                text=True, capture_output=True, check=True, env=env,
            )
        return json.loads(result.stdout)

    def test_declared_completion_requires_proof_independent_of_prose(self):
        for text in ('The task is complete.', '작업을 완료했습니다.',
                     'The review is not complete.', '작업 완료 여부를 설명합니다.'):
            for platform in ('codex', 'claude'):
                with self.subTest(text=text, platform=platform):
                    payload = self.hook(text, platform=platform)
                    assert_stop_observation(self, payload, issue='requires a [completion-check]')

    def test_reviewed_explanation_does_not_infer_completion_from_prose(self):
        for text in ('The retained record says the review is complete.',
                     '기존 검증 기록에 따르면 검토를 완료했습니다.',
                     'The review is not complete.', '작업 완료 여부를 설명합니다.'):
            for platform in ('codex', 'claude'):
                with self.subTest(text=text, platform=platform):
                    assert_stop_observation(self, self.hook(text, platform=platform,
                        response_purpose='explanation'))

    def test_unreviewed_explanation_is_not_accepted_from_nonclosure_wording(self):
        for text in ('No work has been executed. Here are the tradeoffs.',
                     '검증은 미완료이며 결과의 한계를 설명합니다.'):
            payload = self.hook(text, response_purpose='explanation', record_review=False)
            assert_stop_observation(self, payload, issue='response-assessment-required',
                repair_target='response_review')

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

    def test_optional_skill_audit_punctuation_does_not_change_evidence_structure(self):
        for value in ("task-router; verification-before-completion.",
                      "task-router; verification-before-completion; boundary-contract",
                      "verification-before-completion."):
            with self.subTest(value=value):
                self.assertIsNone(self.check(VALID.replace('skill-call: verification-before-completion (this turn)', 'skill-call: '+value)))

    def test_optional_skill_audit_does_not_establish_or_block_evidence(self):
        for value in ("task-router; verification-before-completion-v2.",
                      "task-router; skipped verification-before-completion.",
                      "task-router; verification-before-completion.fake"):
            with self.subTest(value=value):
                self.assertIsNone(self.check(VALID.replace('skill-call: verification-before-completion (this turn)', 'skill-call: '+value)))

    def test_multiple_declared_criteria_accept_equivalent_delimiters(self):
        text = VALID.replace('- claim-evidence-map:', '  - boundary: Preserve the protected file. [source: user-said]\n- claim-evidence-map:')
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
                    self.assertIn("without punctuation", payload["response_assessment"]["body_issue"])

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
                        self.assertNotIn("Invalid verdict value", payload["response_assessment"]["body_issue"])

    def test_invalid_retry_is_not_accepted_as_verified_completion(self):
        invalid = VALID.replace("    evidence: python check.py exited 0\n", "")
        for retry in (False, True):
            with self.subTest(retry=retry):
                payload = self.hook(invalid, retry=retry)
                assert_stop_observation(self, payload, issue="must include evidence",
                    repair_target="completion_metadata")

    def test_equivalent_inline_form_needs_no_repair_generation(self):
        payload = self.hook(VALID.replace("- unverified:\n  - none", "- unverified: none"))
        assert_stop_observation(self, payload)
        self.assertNotIn("systemMessage", payload)

    def test_metadata_defect_is_observed_without_requesting_another_answer(self):
        payload = self.hook(VALID.replace("    evidence: python check.py exited 0\n", ""))
        observed = assert_stop_observation(self, payload, issue="must include evidence",
                                          repair_target="completion_metadata")
        self.assertNotIn("delivery-route:", observed["body_issue"])
        self.assertNotIn("response_text", payload)

    def test_reviewed_unresolved_report_can_end_without_false_success(self):
        text = "I have not verified that the tests pass. Verification remains unresolved."
        assert_stop_observation(self, self.hook(text, retry=True, response_purpose='explanation'))

    def test_skill_self_report_does_not_replace_authenticated_review(self):
        for platform in ('claude', 'codex'):
            with self.subTest(platform=platform):
                payload = self.hook(VALID, platform=platform, record_review=False)
                assert_stop_observation(self, payload, issue='assessment-required:', repair_target='completion_metadata')
                self.assertNotIn('Invoke the Claude Code Skill tool', payload['response_assessment']['body_issue'])
                assert_stop_observation(self, self.hook(VALID, platform=platform))

    def test_explanation_label_never_bypasses_actual_closure_validation(self):
        payload = self.hook("[task-router]\n- work-kind: nonexecution\n\nExplanation: the task is complete.")
        assert_stop_observation(self, payload, issue="requires a [completion-check]")

    def test_fenced_completion_example_needs_no_recovery(self):
        payload = self.hook("Example:\n```text\n[completion-check]\n- claim-evidence-map:\n```\nUse that shape.", response_purpose="explanation")
        assert_stop_observation(self, payload)
        self.assertNotIn("systemMessage", payload)

    def test_actual_markdown_examples_do_not_trigger_stop_recovery(self):
        for text in ("````text\n```\n[completion-check]\nThe task is complete.\n```\n````\n",
                     "    [completion-check]\n    The task is complete.\n",
                     "> Quoted example:\nThe task is complete.\n[completion-check]\n"):
            with self.subTest(text=text):
                payload = self.hook(text, response_purpose='explanation')
                assert_stop_observation(self, payload)
                self.assertNotIn("systemMessage", payload)

    def test_long_example_preserves_stop_validation_of_outside_closure(self):
        text = "````text\n```\n[completion-check]\n```\n````\n\nThe task is complete."
        payload = self.hook(text)
        assert_stop_observation(self, payload, issue="requires a [completion-check]")

    def test_balanced_explicit_multiline_quote_needs_no_stop_recovery(self):
        for opening, closing in (("“", "”"), ('"', '"')):
            text = "A sample response is:\n" + opening + "The task is complete.\n[completion-check]\n- claim-evidence-map:\n" + closing
            with self.subTest(text=text):
                payload = self.hook(text, response_purpose='explanation')
                assert_stop_observation(self, payload)
                self.assertNotIn("systemMessage", payload)

    def test_inline_quote_boundaries_cannot_bypass_stop_validation(self):
        for literal in ("OK.", "pending.", "(Next section)"):
            text = f'The command returned "{literal}"\n\nThe task is complete.\n\n"[io-trace]"'
            with self.subTest(text=text):
                payload = self.hook(text)
                assert_stop_observation(self, payload, issue="requires a [completion-check]")

    def test_inline_quotes_cannot_hide_a_malformed_unquoted_control_block(self):
        text = 'The command returned "OK."\n\n[completion-check]\n- claim-evidence-map:\n\n"[io-trace]"'
        payload = self.hook(text)
        assert_stop_observation(self, payload, issue="claim-evidence-map")

    def test_quoted_list_lazy_continuation_needs_no_stop_recovery(self):
        for prefix in ("> - ", "> 1. ", "> 1. > "):
            text = prefix + "Example output:\nThe task is complete.\n[completion-check]\n"
            with self.subTest(text=text):
                payload = self.hook(text, response_purpose='explanation')
                assert_stop_observation(self, payload)
                self.assertNotIn("systemMessage", payload)

    def test_closed_nested_container_fence_needs_no_stop_recovery_for_lazy_quote(self):
        prefix = "> - > ```\n>   > code\n>   > ```\n>\n> A quoted paragraph.\n"
        for tail in ("The task is complete.\n[completion-check]\n", "> The task is complete.\n> [completion-check]\n"):
            with self.subTest(tail=tail):
                payload = self.hook(prefix + tail, response_purpose='explanation')
                assert_stop_observation(self, payload)
                self.assertNotIn("systemMessage", payload)


if __name__ == "__main__":
    unittest.main()
