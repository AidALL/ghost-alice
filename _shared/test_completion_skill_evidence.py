#!/usr/bin/env python3
"""Core records the response model's per-claim and whole-response judgments and checks their original bindings."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import completion_check_validator as validator

from stop_hook_testing import (
    WITHOUT_SELF_REPORT, WITH_SELF_REPORT, user_role_row, skill_call, reply,
    assert_stop_observation, run_reviewed_stop,
)


class ValidatorOptionTests(unittest.TestCase):
    def check(self, text, **kwargs):
        return validator.validate_completion_text(text, require_completion_check=True, **kwargs)

    def test_evidence_structure_does_not_require_procedural_self_report(self):
        self.assertIsNone(self.check(WITHOUT_SELF_REPORT))

    def test_evidence_structure_accepts_optional_old_self_report_lines(self):
        self.assertIsNone(self.check(WITH_SELF_REPORT))


class StopHookSkillEvidenceTests(unittest.TestCase):
    def test_claude_allows_a_block_without_self_report_when_the_skill_was_called(self):
        payload = run_reviewed_stop([user_role_row("Compute it."), skill_call(), reply(WITHOUT_SELF_REPORT)])
        assert_stop_observation(self, payload)

    def test_claude_still_allows_the_old_self_report_lines(self):
        payload = run_reviewed_stop([user_role_row("Compute it."), skill_call(), reply(WITH_SELF_REPORT)])
        assert_stop_observation(self, payload)

    def test_claude_accepts_authenticated_response_without_skill_call(self):
        payload = run_reviewed_stop([user_role_row("Compute it."), reply(WITH_SELF_REPORT)])
        assert_stop_observation(self, payload)

    def test_first_claude_stop_accepts_review_without_procedural_markers(self):
        payload = run_reviewed_stop([user_role_row("Compute it."), reply(WITHOUT_SELF_REPORT)])
        assert_stop_observation(self, payload)

    def test_claude_records_missing_evidence_as_unaccepted_without_continuation(self):
        broken = WITHOUT_SELF_REPORT.replace("    evidence: python check.py exited 0\n", "")
        payload = run_reviewed_stop([user_role_row("Compute it."), skill_call(), reply(broken)])
        assert_stop_observation(self, payload, issue='evidence', repair_target='completion_metadata')

    def test_first_codex_stop_accepts_review_without_procedural_markers(self):
        payload = run_reviewed_stop(platform="codex", message=WITHOUT_SELF_REPORT)
        assert_stop_observation(self, payload)

    def test_codex_self_report_lines_neither_establish_nor_block_the_review(self):
        # Codex once read the printed skill-call line as its skill audit; Core's review now decides.
        for verdict, issue in (("supported", None), ("unsupported", "response-unsupported")):
            with self.subTest(verdict=verdict):
                payload = run_reviewed_stop(platform="codex", message=WITH_SELF_REPORT, review_verdict=verdict)
                assert_stop_observation(self, payload, issue=issue)

    def test_authenticated_whole_answer_still_needs_supported_judgment(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                payload = run_reviewed_stop(platform=platform, message=WITHOUT_SELF_REPORT,
                    review_verdict="unsupported")
                assert_stop_observation(self, payload, issue='response-unsupported')

    def test_changed_complete_answer_cannot_borrow_review(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                payload = run_reviewed_stop(platform=platform, message=WITHOUT_SELF_REPORT,
                    append_text="\n\nAll unrelated work is also complete.")
                assert_stop_observation(self, payload, issue='response-assessment-required')

    def test_changed_source_keeps_authenticated_response_unaccepted(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                payload = run_reviewed_stop(platform=platform, message=WITHOUT_SELF_REPORT,
                    mutate_source=True)
                assert_stop_observation(self, payload, issue='source-changed', repair_target='source_status')


if __name__ == "__main__":
    unittest.main()
