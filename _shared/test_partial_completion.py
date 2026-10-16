"""Scoped evidence for mixed successful work and unresolved verification."""
import unittest

import completion_check_validator as validator
import test_completion_skill_evidence as skill_evidence
from test_completion_skill_evidence import assert_stop_observation, user, skill_call, reply

PARTIAL_BODY = """[partial-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- scope: partial
- acceptance-criteria:
  - behavior: targeted behavior checks [source: user-said]
  - lint: Clippy warning assessment [source: user-said]
  - console: console warning assessment [source: user-said]
- claim-evidence-map:
  - claim: targeted tests passed
    criterion: behavior
    evidence: retained targeted test output exited 0
    verdict: pass
- unverified:
  - lint: Clippy warnings remain unresolved
  - console: console warnings remain unresolved
"""
PARTIAL = "Targeted tests passed. Clippy and console warnings remain unresolved.\n\n" + PARTIAL_BODY


# Exact first response supplied by the user; this fixture exercises the evidence
# protocol only and neither reruns nor re-verifies its historical business work.
SUPPLIED_KOREAN_ANSWER = """수정 후 Xcode에서 다시 빌드했고, **이슈 목록은 No Issues**입니다. 경고를 숨기는 설정은 넣지 않았습니다.
- Rust 컴파일 경고, 보안 상태 복원, 앱 내부 레이아웃 재진입·음수 높이 문제를 수정했습니다.
- Rust 테스트 147개, 문서 테스트 4개, Xcode 테스트 5개가 통과했습니다.
- autoShortcut은 말씀하신 대로 서명 문제로 남겨뒀습니다.
- 남은 레이아웃 메시지는 기본 메뉴 막대 아이콘만 있는 별도 앱에서도 재현되는 AppKit 문제입니다.
다만 엄격한 Clippy 검사는 아직 실패하므로 전체 검증이 모두 통과한 상태는 아닙니다. 자동 완료 기록도 보류했습니다."""
SUPPLIED_PARTIAL_EVIDENCE = """[partial-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- scope: partial
- acceptance-criteria:
  - xcode: Xcode issue list without suppressing warnings [source: previous-tool]
  - repairs: Rust warnings, security restoration, layout fixes [source: previous-tool]
  - tests: Rust, documentation and Xcode checks [source: previous-tool]
  - appkit: independent minimal application reproduction [source: previous-tool]
  - clippy: strict Clippy verification [source: user-said]
  - recording: automatic completion recording [source: system-doc]
- claim-evidence-map:
  - claim: Xcode issue list is No Issues without suppressing warnings
    criterion: xcode
    evidence: fixture retained Xcode build and configuration observations
    verdict: pass
  - claim: Rust warning, security restoration and application layout fixes
    criterion: repairs
    evidence: fixture retained owning repair diff and regression observations
    verdict: pass
  - claim: Rust 147, documentation 4, Xcode 5 tests passed
    criterion: tests
    evidence: fixture retained original Rust, documentation and Xcode test results
    verdict: pass
  - claim: remaining layout message reproduced in the minimal AppKit application
    criterion: appkit
    evidence: fixture retained minimal menu-bar application reproduction
    verdict: pass
- unverified:
  - clippy: strict check remains failing and unresolved
  - recording: automatic completion recording remains deferred
"""


class PartialCompletionTests(unittest.TestCase):
    def check(self, text):
        return validator.validate_completion_text(text, require_completion_check=True)

    def test_mixed_success_and_unresolved_warnings_have_an_honest_evidence_route(self):
        self.assertIsNone(self.check(PARTIAL))

    def test_partial_marker_is_evidence_bearing_but_not_full_completion(self):
        self.assertTrue(validator.looks_like_evidence_claim(PARTIAL_BODY))
        self.assertFalse(validator.looks_like_completion_claim(PARTIAL_BODY))
        self.assertEqual(validator.extract_partial_unverified_ids(PARTIAL_BODY), ['lint', 'console'])

    def test_partial_label_without_evidence_still_rejects_success_claim(self):
        reason = self.check('Overall status: partial. Targeted tests passed; Clippy warnings remain.')
        self.assertIsNotNone(reason)
        self.assertIn('[partial-check]', reason)

    def test_partial_requires_scope_criteria_proof_verdict_and_unresolved_ids(self):
        changes = (
            ('- scope: partial\n', ''),
            ('- scope: partial', '- scope: complete'),
            ('    evidence: retained targeted test output exited 0\n', ''),
            ('    criterion: behavior', '    criterion: absent'),
            ('    verdict: pass', '    verdict: unverified'),
            ('  - lint: Clippy warnings remain unresolved', '  - lint: '),
            ('  - console: console warnings remain unresolved', '  - absent: console warnings remain'),
            ('  - console: console warnings remain unresolved\n', ''),
        )
        for old, new in changes:
            with self.subTest(old=old, new=new):
                self.assertIsNotNone(self.check(PARTIAL.replace(old, new)))
        self.assertIsNotNone(self.check(PARTIAL[:PARTIAL.index('- unverified:')] + '- unverified:\n  - none\n'))
        self.assertIsNotNone(self.check('[partial-check]\n- scope: partial\n- unverified:\n  - lint: pending\n'))

    def test_partial_shape_does_not_establish_support_for_broader_claims(self):
        for claim in ('The task is complete.', 'I completed the requested work.', '작업을 완료했습니다.'):
            with self.subTest(claim=claim):
                text = claim + '\n' + PARTIAL
                self.assertIsNone(self.check(text))
                result = skill_evidence.StopHookSkillEvidenceTests.run_hook(
                    self, platform='codex', message=text, review_verdict='unsupported')
                assert_stop_observation(self, result, issue='response-unsupported:')
        self.assertIsNotNone(self.check(PARTIAL + '\n[completion-check]\n- unverified:\n  - none'))

    def test_full_and_partial_markers_cannot_finalize_the_same_response(self):
        from test_completion_recovery import VALID
        self.assertIsNotNone(self.check(PARTIAL + '\n' + VALID))

    def test_negated_full_completion_and_quoted_example_are_allowed_with_partial_evidence(self):
        for lead in ('The task is not complete yet.', 'Example: "The task is complete."',
                     '작업은 아직 미완료입니다.'):
            with self.subTest(lead=lead):
                self.assertIsNone(self.check(lead + '\n' + PARTIAL))

    def test_full_completion_still_requires_unverified_none(self):
        reason = self.check(PARTIAL.replace('[partial-check]', '[completion-check]'))
        self.assertIn("unverified section to be 'none'", reason)

    def test_partial_structure_does_not_require_skill_self_report(self):
        missing = PARTIAL_BODY.replace('- verification-before-completion: done\n', '')
        self.assertIsNone(self.check(missing))

    def test_partial_examples_do_not_trigger_evidence_gate(self):
        self.assertIsNone(validator.validate_completion_text(
            'Example:\n```text\n' + PARTIAL_BODY + '```', require_completion_check=False))


class SuppliedKoreanAnswerTests(unittest.TestCase):
    run_hook = skill_evidence.StopHookSkillEvidenceTests.run_hook

    def test_literal_first_answer_requires_evidence_without_demanding_full_success(self):
        result = self.run_hook(platform='codex', message=SUPPLIED_KOREAN_ANSWER, response_purpose='completion')
        assert_stop_observation(self, result, issue='[partial-check]', repair_target='completion_metadata')

    def test_literal_answer_with_qualified_partial_evidence_accepts_retained_results(self):
        result = self.run_hook(platform='codex', message=SUPPLIED_KOREAN_ANSWER + '\n\n' + SUPPLIED_PARTIAL_EVIDENCE)
        assert_stop_observation(self, result)

    def test_finalized_unresolved_response_points_to_partial_instead_of_prose_loop(self):
        finalized = SUPPLIED_PARTIAL_EVIDENCE.replace('[partial-check]', '[completion-check]')
        result = self.run_hook(platform='codex', message=SUPPLIED_KOREAN_ANSWER + '\n\n' + finalized)
        observed = assert_stop_observation(self, result,
            issue="unverified section to be 'none'", repair_target='completion_metadata')
        self.assertIn('use [partial-check]', observed['body_issue'])
        repaired = SUPPLIED_KOREAN_ANSWER + '\n\n' + SUPPLIED_PARTIAL_EVIDENCE
        assert_stop_observation(self, self.run_hook(platform='codex', message=repaired))

    def test_korean_negative_and_conditional_finished_status_remain_partial(self):
        for lead in ('모든 수정이 끝나지 않았습니다.', '모든 수정이 끝났을 때 다시 확인합니다.'):
            with self.subTest(lead=lead):
                self.assertIsNone(validator.validate_completion_text(
                    lead + '\n' + SUPPLIED_PARTIAL_EVIDENCE, require_completion_check=True))

    def test_explicit_korean_all_repairs_finished_cannot_use_partial_evidence(self):
        text = '모든 수정이 끝났습니다.\n' + SUPPLIED_PARTIAL_EVIDENCE
        self.assertIsNone(validator.validate_completion_text(text, require_completion_check=True))
        result = self.run_hook(platform='codex', message=text, review_verdict='unsupported')
        assert_stop_observation(self, result, issue='response-unsupported:')



class PartialRecoveryTests(unittest.TestCase):
    run_hook = skill_evidence.StopHookSkillEvidenceTests.run_hook

    def test_partial_diagnostic_does_not_request_replacement_output(self):
        payload = self.run_hook(platform='codex', message=PARTIAL_BODY, record_review=False)
        observed = assert_stop_observation(self, payload,
            issue='assessment-required:', repair_target='completion_metadata')
        self.assertNotIn('delivery-route:', observed['body_issue'])
        self.assertNotIn('prior_answer_ref', observed)

    def test_supported_partial_keeps_unresolved_business_conditions(self):
        payload = self.run_hook(platform='codex', message=PARTIAL_BODY)
        assert_stop_observation(self, payload)
        self.assertEqual(validator.extract_partial_unverified_ids(PARTIAL_BODY), ['lint', 'console'])
        self.assertNotIn('response_text', payload)



class PartialStopTests(unittest.TestCase):
    run_hook = skill_evidence.StopHookSkillEvidenceTests.run_hook

    def test_codex_accepts_supported_partial_without_forcing_none(self):
        assert_stop_observation(self, self.run_hook(platform='codex', message=PARTIAL))

    def test_claude_partial_uses_review_independently_of_skill_call(self):
        assert_stop_observation(self, self.run_hook([user('Check it'), reply(PARTIAL)]))
        assert_stop_observation(self, self.run_hook([user('Check it'), skill_call(), reply(PARTIAL)]))

    def test_skill_call_and_partial_marker_cannot_replace_response_review(self):
        body = PARTIAL_BODY.replace('targeted tests passed', 'checked the targeted behavior')
        assert_stop_observation(self,
            self.run_hook([user('Check it'), skill_call(), reply(body)], record_review=False),
            issue='assessment-required:', repair_target='completion_metadata')


if __name__ == '__main__':
    unittest.main()
