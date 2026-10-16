"""Core owns native assessment inputs; Ghost-ALICE Autopilot receives decisions, not policy knobs."""
import hashlib
import importlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_response_route as fixtures
from test_completion_skill_evidence import assert_stop_observation


# The actual status response rejected by the native Stop hook. This is a
# historical report, not a request to rerun the business checks described here.
REJECTED_STATUS_REPORT = """제가 한 일은 근거 검증·접수·정정 코드 수정과 설치, 그리고 그 오류들의 재발 검사였습니다. 사용자 목적을 받아 Core와 AP가 판단하고 다음 업무까지 이어가는 전체 동작을 확인한 것은 아니었습니다. 그 차이를 분명하게 보고하지 않았습니다. 죄송합니다.

실제로 실행한 검사는 다음과 같습니다.

| 검사 | 확인한 내용과 결과 |
|---|---|
| Core 8건 | 근거의 원본·세션 소유권·재사용·정정 조건을 검사했습니다. 통과했습니다. |
| AP 17건 | 원래 검증 시각 보존, 같은 초에 기록된 근거의 순서 구분, 정정 이력 보존, 영향 없는 항목 유지, 새 근거에 과거 시각을 붙이는 시도 거부 등을 검사했습니다. 통과했습니다. |
| 설치본 1건 | 같은 초에 기록된 근거를 실제 설치본에 접수하고, 격리된 Stop 호출로 최종 적용하는 경로를 검사했습니다. 통과했습니다. |
| 과거 차단 응답 3개 | 설치본 Stop에 재입력했습니다. 세 응답 모두 `route-unavailable`로 차단됐습니다. 필요한 경로 등록을 먼저 요구하는지 확인했을 뿐, 정상 보고가 끝까지 통과한 검사가 아닙니다. |
| 문구·문서 검사 | 행동 주체를 명시했는지, 안내 문구와 문서·다이어그램이 맞는지 검사했습니다. 마지막 수정은 주로 문구·주석 변경이며 실행 로직은 그대로였습니다. |

앞의 검사들은 [실행 기록](/tmp/ghost-alice-test-evidence/final-verification.json), 차단 응답 결과는 [재생 기록](/tmp/ghost-alice-test-evidence/replay.json)에 남아 있습니다.

핵심 코드도 수정했습니다. AP가 Core 평가를 받아 조건을 적용하도록 연결했고, 부분 완료와 실패 보고를 구분했으며, 정정 시 원본과 영향 없는 항목을 보존하도록 했습니다. 정밀한 타임스탬프 대신 인증된 기록 순서로 구분하는 수정도 설치했습니다.

하지만 제가 작업을 개별 접수·차단 오류의 수정에 집중하면서, 그 검사 결과를 전체 작동 수준으로 확대해 보고했습니다. 현재 도달한 곳은 ‘검증·기록·적용 기능을 수정하고 해당 사례를 확인한 단계’입니다. 사용자께서 요구한 ‘목적에 맞는 판단과 자동 후속 수행이 함께 돌아가는 단계’까지 해결했다는 결과는 내놓지 못했습니다.

[io-trace]
- commands-run: 기존 검사·설치 기록 읽기. 검사 재실행과 파일 수정 없음.
- subagents: 기존 근거의 검사 범위 대조; 추가 도구 호출 없음."""


class HistoricalResponseReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.event = self.fixture.intake()
        fixtures.ledger.record_turn(**self.fixture.coords,
            expected_input_event_id=self.fixture.event, intent_delta={'acceptance_criteria': [
                self.fixture.criterion(summary='Report the existing Core/AP checks and their limits.')]})
        self.fixture.record()
        self.source = dict(GHOST_ALICE_CORE_ROOT=str(fixtures.ROOT),
            GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root),
            GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='current', CODEX_THREAD_ID='current')
        self.api = importlib.import_module('completion_assessment')
        self.time = '2026-10-10T20:47:11+00:00'
        self.observation = Path(self.fixture.tmp.name) / 'original-observation.json'
        self.observation.write_text(json.dumps(dict(core_cases=8, ap_cases=17,
            installed_case='publication and isolated Stop application passed',
            replay=['route-unavailable'] * 3, real_business_continuation='not observed')))
        self.proof = '\n'.join(['[completion-check]', '- acceptance-criteria:',
            '  - AC1: Report the existing Core/AP checks and their limits. [source: user-said]',
            '- claim-evidence-map:', '  - claim: Existing tests cover recording, not complete business continuation.',
            '    criterion: AC1', '    evidence: ' + str(self.observation), '    verdict: pass',
            '- unverified:', '  - none', '- evidence: ' + str(self.observation)])
        self.original = self.api.prepare_assessment(self.source, proof=self.proof,
            verified_at=self.time, source_paths=[str(self.observation)])
        self.api.record_assessment(self.source, self.original['candidate_reference'],
            self.judgments(self.original))
        self.explanation_route()

    def explanation_route(self):
        route = self.fixture.candidate()
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)

    def judgments(self, candidate):
        return [dict(binding_id=b['binding_id'], verdict='supported',
            source_ids=[candidate['sources'][0]['source_id']],
            rationale='Retain the recorded original scope; this fixture tests authentication, not semantic inference.')
            for b in candidate['bindings']]

    def review_report(self, *, verdict='supported'):
        try:
            candidate = self.api.prepare_assessment(self.source, proof=self.proof,
                verified_at=self.time, source_paths=[],
                reuse_candidate=self.original['candidate_reference'], response_text=REJECTED_STATUS_REPORT)
        except self.api.CompletionAssessmentError as exc:
            self.fail(f'An original-backed explanation cannot be reviewed without repeating its proof block: {exc}')
        self.api.record_assessment(self.source, candidate['candidate_reference'], self.judgments(candidate),
            response_judgment=dict(binding_id=candidate['response_binding']['binding_id'], verdict=verdict,
                source_ids=[candidate['sources'][0]['source_id']],
                rationale='The report retains original scope/time and makes no new completion claim.'))
        return candidate

    def assess(self, body=REJECTED_STATUS_REPORT):
        return importlib.import_module('native_completion').assess_native_completion(
            self.source, text=body, phase='final')

    def run_stop(self, body):
        hook = Path(os.environ.get('GHOST_ALICE_TEST_SHARED_ROOT', Path(__file__).parent)) / 'claude_stop_verification_hook.py'
        env = dict(os.environ, **self.source, PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', str(hook), '--platform', 'codex'],
            input=json.dumps(dict(session_id='current', last_assistant_message=body)),
            text=True, capture_output=True, check=True, env=env)
        return json.loads(result.stdout)

    def test_actual_report_reuses_review_through_stop_without_new_completion(self):
        self.assertIsNotNone(self.assess()['body_issue'])
        diagnostic = self.run_stop(REJECTED_STATUS_REPORT)
        assert_stop_observation(self, diagnostic, issue='response-assessment-required', repair_target='response_review')
        before = fixtures.ledger.read_session_state(**self.fixture.coords)['acceptance_criteria']
        reviewed = self.review_report()
        result = self.assess()
        self.assertIsNone(result['body_issue'])
        self.assertTrue(result['completion_route']['report_only'])
        self.assertEqual(result['completion_route']['publication_route'], 'none')
        self.assertEqual(result['completion_route']['publication_criteria'], [])
        assert_stop_observation(self, self.run_stop(REJECTED_STATUS_REPORT))
        self.assertEqual(fixtures.ledger.read_session_state(**self.fixture.coords)['acceptance_criteria'], before)
        self.assertEqual(reviewed['verified_at'], self.time)
        self.assertEqual(reviewed['sources'], self.original['sources'])
        # An already authenticated original paragraph must not hide the newer
        # whole-response review from either native consumer.
        matched_original = dict(present=True, valid=True, residual_text='Additional historical context.',
            claims=[], issue=None, automatic=True)
        with patch('native_completion.evaluate_runtime_claim_report', return_value=matched_original):
            self.assertIsNone(self.assess()['body_issue'])
        ap_root = os.environ.get('GHOST_ALICE_TEST_AP_SKILL_ROOT')
        if ap_root:
            sys.path.insert(0, str(Path(ap_root) / 'adapters'))
            try:
                from autopilot_intent_recovery import current_execution_projection
                state = fixtures.ledger.read_session_state(**self.fixture.coords)
                material = dict(state_path=str(self.fixture.root / 'codex/current/intent-state.json'),
                    platform='codex', session_id='current', intent_state=state)
                source = dict(self.source, GHOST_ALICE_COMPLETION_SCHEMA='autopilot-completion-receipt.v2')
                projected = current_execution_projection(material, source, Path(self.fixture.tmp.name) / 'ap',
                    claim_scope=dict(text=REJECTED_STATUS_REPORT, phase='final'))
                self.assertEqual(projected['reason'], 'response-no-publication')
                self.assertEqual(projected['completion_route']['publication_criteria'], [])
            finally:
                sys.path.pop(0)

    def test_report_review_cannot_cover_changed_body_source_or_current_input(self):
        self.review_report()
        changed = REJECTED_STATUS_REPORT + '\n\n방금 새 작업까지 모두 완료했습니다.'
        self.assertIsNotNone(self.assess(changed)['body_issue'])
        assert_stop_observation(self, self.run_stop(changed), issue='response-assessment-required')
        original = self.observation.read_text()
        self.observation.write_text('The original observation changed.')
        self.assertIsNotNone(self.assess()['body_issue'])
        self.observation.write_text(original)
        self.fixture.event = self.fixture.intake('A different status question')
        self.explanation_route()
        self.assertIsNotNone(self.assess()['body_issue'])
        self.review_report()
        self.assertIsNone(self.assess()['body_issue'])

    def test_registered_review_owns_the_response_instead_of_automatic_claim_discovery(self):
        body = 'Existing tests cover recording, not complete business continuation.'
        candidate = self.api.prepare_response_review(self.source, response_text=body, source_paths=[],
            reuse_candidate=self.original['candidate_reference'])
        self.api.record_response_review(self.source, candidate['candidate_reference'], dict(
            claims=self.judgments(candidate), response=dict(
                binding_id=candidate['response_binding']['binding_id'], verdict='supported',
                source_ids=[candidate['sources'][0]['source_id']],
                rationale='The complete response preserves the original observation and its limits.')))
        claim = dict(statement=body, work_unit_id='first', contract_revision=1,
            criterion_id='AC1', proof_digest='sha256:' + 'a' * 64)
        with patch('original_claim_evidence.list_retained_claim_candidates',
                return_value=dict(status='candidates', claims=[claim])), patch(
                'original_claim_evidence.resolve_retained_claims',
                return_value=dict(status='unavailable', claims=[])):
            result = self.assess(body)
        self.assertIsNone(result['body_issue'])
        self.assertEqual(result['completion_route']['publication_route'], 'none')
        self.assertEqual(result['completion_route']['publication_criteria'], [])

    def test_explanation_source_failure_requests_source_status_before_affected_review(self):
        self.review_report()
        original = self.observation.read_bytes()
        for status in ('source-changed', 'source-unavailable'):
            with self.subTest(status=status):
                if status == 'source-changed':
                    self.observation.write_text('Changed observation bytes.')
                else:
                    self.observation.unlink()
                try:
                    result = self.assess()
                    self.assertTrue(result['body_issue'].startswith(status + ':'), result['body_issue'])
                    self.assertIn('Response acceptance is blocked for the affected review', result['body_issue'])
                    if status == 'source-changed':
                        self.assertIn('compares only the affected claims with the changed source under verification-before-completion', result['body_issue'])
                    else:
                        self.assertIn('until that source or authenticated retained proof is available', result['body_issue'])
                    self.assertNotIn('if needed', result['body_issue'])
                    self.assertEqual(result['repair_target'], 'source_status')
                    stopped = self.run_stop(REJECTED_STATUS_REPORT)
                    observed = assert_stop_observation(self, stopped, issue=status + ':', repair_target='source_status')
                    self.assertEqual(observed['body_issue'], result['body_issue'])
                finally:
                    self.observation.write_bytes(original)
        self.assertIsNone(self.assess()['body_issue'])


class UnroutedNativeWorkTests(unittest.TestCase):
    def test_claude_file_write_requires_route_without_inventing_completion(self):
        fixture = fixtures.ResponseRouteTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.coords['platform'] = 'claude'
        fixture.event = fixture.intake('Create the weekly memo from the supplied notes.')
        source = dict(GHOST_ALICE_CORE_ROOT=str(fixtures.ROOT),
            GHOST_ALICE_SESSION_INTENT_ROOT=str(fixture.root),
            GHOST_ALICE_PLATFORM='claude', GHOST_ALICE_SESSION_ID='current')
        native = importlib.import_module('native_completion')
        with patch.object(native._ledger(source), 'read_response_route',
                side_effect=ValueError('selected execution work needs an admitted owned criterion')):
            rejected_route = native.assess_native_completion(source, phase='before_tool')
        self.assertEqual(rejected_route['input_event_id'], fixture.event)
        self.assertEqual(rejected_route['completion_route']['publication_route'], 'none')
        transcript = Path(fixture.tmp.name) / 'native.jsonl'
        entries = [dict(type='user', sessionId='current', message=dict(role='user', content='Create the memo.')),
            dict(type='assistant', sessionId='current', message=dict(role='assistant', content=[
                dict(type='tool_use', name='Write', input=dict(file_path='/isolated/memo.md', content='memo'))]))]
        transcript.write_text(''.join(json.dumps(row) + '\n' for row in entries))
        body = '주간업무메모-20261011.md를 다운로드 폴더에 만들었습니다. 파일 하나를 만드는 작업이라 라우팅 절차를 생략했습니다.'
        hook = Path(os.environ.get('GHOST_ALICE_TEST_SHARED_ROOT', Path(__file__).parent)) / 'claude_stop_verification_hook.py'
        def stop():
            result = subprocess.run([sys.executable, '-B', str(hook)],
                input=json.dumps(dict(session_id='current', transcript_path=str(transcript),
                    last_assistant_message=body)), text=True, capture_output=True, check=True,
                env=dict(os.environ, **source, CLAUDE_CODE_ENTRYPOINT='claude-desktop', PYTHONDONTWRITEBYTECODE='1'))
            return json.loads(result.stdout)
        rejected = stop()
        assert_stop_observation(self, rejected, issue='route-unavailable', repair_target='response_route')
        candidate = fixture.candidate()
        candidate['work_units'][0]['owner']['platform'] = 'claude'
        candidate['selected_criteria'] = []
        with self.assertRaisesRegex(fixture.api.RouteSchemaError, 'admitted.*criterion'):
            fixture.record(candidate)
        # The same route becomes usable once the assistant supplies the actual condition.
        fixtures.ledger.record_turn(**fixture.coords, expected_input_event_id=fixture.event,
            intent_delta={'acceptance_criteria': [fixture.criterion(summary='Create the memo from the supplied notes.')]})
        candidate['selected_criteria'] = [dict(work_unit_id='first', contract_revision=1, criterion_id='AC1')]
        fixture.record(candidate)
        assert_stop_observation(self, stop())
        # A later tool-free input must not inherit the preceding Write call.
        entries.append(dict(type='user', sessionId='current', message=dict(role='user', content='Thanks.')))
        transcript.write_text(''.join(json.dumps(row) + '\n' for row in entries))
        assert_stop_observation(self, stop())


class NativeActivityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.event = self.fixture.intake('Append the requested line.')
        self.source = dict(GHOST_ALICE_CORE_ROOT=str(fixtures.ROOT),
            GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root),
            GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='current')
        self.transcript = Path(self.fixture.tmp.name) / 'rollout.jsonl'
        self.payload = dict(session_id='current', turn_id='followup',
            transcript_path=str(self.transcript), last_assistant_message='memo.txt: requested line')
        self.rows = [dict(type='session_meta', payload=dict(id='current')),
            dict(type='turn_context', payload=dict(turn_id='followup')),
            self.item('message', 'followup', role='user', content=[dict(type='input_text', text='Append the requested line.')])]
        self.rows[-1]['payload']['internal_chat_message_metadata_passthrough']['content_item_kinds'] = ['user.text']

    def item(self, kind, turn, **fields):
        return dict(type='response_item', payload=dict(type=kind,
            internal_chat_message_metadata_passthrough=dict(turn_id=turn), **fields))

    def write_rows(self, rows=None):
        self.transcript.write_text('\n'.join(json.dumps(row) for row in (self.rows if rows is None else rows)) + '\n')

    def assess(self, payload=None):
        return importlib.import_module('native_completion').assess_native_completion(
            self.source, phase='final', text=self.payload['last_assistant_message'],
            native_input=self.payload if payload is None else payload)

    def test_conversation_explanation_does_not_submit_an_incidental_retained_claim(self):
        body = '''네. 검토한 최종본은 모두 문서 본문에 들어가 있습니다.

- Core 한·영 아키텍처·웹페이지·스킬 참고자료: Mermaid로 반영했습니다.
- Ghost-ALICE Autopilot README·위키·설치 문서: 이미 본문에 있던 최종 Mermaid 다이어그램을 유지했습니다.

19개 다이어그램을 Mermaid로 통일하고 SVG 자산과 생성 스크립트를 제거했습니다.

[한국어 아키텍처 최종본 보기](https://aidall.github.io/ghost-alice/architecture_ko.html)'''
        claim = dict(statement=body.split('\n\n')[2], work_unit_id='diagrams',
            contract_revision=1, criterion_id='format', proof_digest='sha256:' + 'a' * 64)
        accepted = dict(status='valid', claims=[dict(claim, validity='valid',
            original_claims=[claim['statement']], original_verified_at='2026-10-11T20:11:49Z',
            evidence_reference='original-evidence', application_reference='accepted-application')])
        self.write_rows()
        with patch('original_claim_evidence.list_retained_claim_candidates',
                return_value=dict(status='candidates', claims=[claim])), patch(
                'original_claim_evidence.resolve_retained_claims', return_value=accepted):
            self.payload['last_assistant_message'] = body
            ordinary = self.assess()
            self.assertIsNone(ordinary['body_issue'])
            self.assertEqual(ordinary['validation_text'], body)
            self.assertFalse(ordinary['completion_route']['report_only'])
            self.assertEqual(ordinary['completion_route']['publication_route'], 'none')
            self.assertEqual(ordinary['native_activity']['status'], 'absent')
            self.payload['last_assistant_message'] = body + '\n\n[evidence-report]\n' + json.dumps(
                dict(schema_version='claim-evidence.v1', claims=[claim])) + '\n[/evidence-report]'
            explicit = self.assess()
            self.assertIn('claim-evidence-unbound', explicit['body_issue'])
            self.assertFalse(explicit['completion_route']['report_only'])
            self.assertEqual(explicit['completion_route']['publication_route'], 'none')

    def test_codex_native_stop_requires_route_for_typed_current_tool_calls(self):
        hook = Path(__file__).with_name('claude_stop_verification_hook.py')
        for kind in ('custom_tool_call', 'function_call'):
            with self.subTest(kind=kind):
                self.write_rows(self.rows + [self.item(kind, 'followup', name='exec', call_id='call-1')])
                result = subprocess.run([sys.executable, '-B', str(hook), '--platform', 'codex'],
                    input=json.dumps(self.payload), text=True, capture_output=True, check=True,
                    env=dict(os.environ, **self.source, CODEX_THREAD_ID='current', PYTHONDONTWRITEBYTECODE='1'))
                rejected = json.loads(result.stdout)
                assert_stop_observation(self, rejected, issue='route-unavailable', repair_target='response_route')
                assessed = self.assess()
                self.assertEqual(assessed.get('native_activity', {}).get('status'), 'observed')
                self.assertFalse(assessed['has_evidence_marker'])

    def test_codex_tool_activity_reads_complete_records_only(self):
        """A raw U+2028 stays inside its record; the record Codex is still appending is not read yet."""
        rows = self.rows + [self.item('message', 'followup', role='assistant', content=[
                dict(type='output_text', text='first\u2028second')]),
            self.item('custom_tool_call', 'followup', name='exec', call_id='call-1')]
        self.transcript.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
            + '{"type":"response_item","payload":{"type":"mess', encoding='utf-8')
        self.assertEqual(self.assess().get('native_activity', {}).get('status'), 'observed')

    def test_codex_current_tool_free_turn_does_not_inherit_prior_tools(self):
        prior = [dict(type='turn_context', payload=dict(turn_id='prior')),
            self.item('custom_tool_call', 'prior', name='exec', call_id='old-call')]
        self.write_rows(self.rows[:1] + prior + self.rows[1:] + [
            self.item('message', 'followup', role='assistant', content=[
                dict(type='output_text', text='The phrase custom_tool_call is just text.')])])
        assessed = self.assess()
        self.assertEqual(assessed.get('native_activity', {}).get('status'), 'absent')
        self.assertIsNone(assessed['body_issue'])

    def test_codex_steered_question_uses_current_input_not_entire_turn(self):
        # A native turn can contain work followed by a new, tool-free question.
        question = 'What is in that retained data folder?'
        self.fixture.event = self.fixture.intake(question)
        human = self.item('message', 'followup', role='user',
            content=[dict(type='input_text', text=question)])
        human['payload']['internal_chat_message_metadata_passthrough']['content_item_kinds'] = ['user.text']
        rows = self.rows + [self.item('custom_tool_call', 'followup', name='exec', call_id='earlier-work'),
            human, self.item('message', 'followup', role='assistant',
                content=[dict(type='output_text', text=self.payload['last_assistant_message'])])]
        self.write_rows(rows)
        assessed = self.assess()
        self.assertEqual(assessed['native_activity']['status'], 'absent')
        self.assertIsNone(assessed['body_issue'])
        self.assertEqual(assessed['completion_route']['publication_route'], 'none')

        hook = Path(os.environ.get('GHOST_ALICE_TEST_SHARED_ROOT', Path(__file__).parent)) / 'claude_stop_verification_hook.py'
        stopped = subprocess.run([sys.executable, '-B', str(hook), '--platform', 'codex'],
            input=json.dumps(self.payload), text=True, capture_output=True, check=True,
            env=dict(os.environ, **self.source, CODEX_THREAD_ID='current', PYTHONDONTWRITEBYTECODE='1'))
        assert_stop_observation(self, json.loads(stopped.stdout))

        # Tool use after the new question still requires the current route.
        rows.append(self.item('function_call', 'followup', name='exec', call_id='current-work'))
        self.write_rows(rows)
        self.assertTrue(self.assess()['body_issue'].startswith('route-unavailable:'))

        # Native hook feedback is not a new human input boundary.
        feedback = self.item('message', 'followup', role='user',
            content=[dict(type='input_text', text=question)])
        feedback['payload']['internal_chat_message_metadata_passthrough']['content_item_kinds'] = ['unknown']
        self.write_rows(rows + [feedback])
        self.assertTrue(self.assess()['body_issue'].startswith('route-unavailable:'))

        # A different observed input cannot borrow the earlier human boundary.
        self.fixture.event = self.fixture.intake('A different current input.')
        self.assertEqual(self.assess()['native_activity']['status'], 'unavailable')

    def test_unavailable_native_identity_or_transcript_is_not_tool_absence(self):
        self.write_rows()
        cases = [dict(self.payload, transcript_path=None),
            dict(self.payload, transcript_path='relative.jsonl'),
            dict(self.payload, transcript_path=str(self.transcript.with_name('missing.jsonl'))),
            dict(self.payload, session_id='foreign'), dict(self.payload, turn_id=None),
            dict(self.payload, turn_id='foreign')]
        for payload in cases:
            with self.subTest(payload=payload):
                assessed = self.assess(payload)
                self.assertEqual(assessed.get('native_activity', {}).get('status'), 'unavailable')
                self.assertTrue(assessed['body_issue'].startswith('native-activity-unavailable:'))
                self.assertEqual(assessed['repair_target'], 'response_route')
        self.write_rows([dict(type='session_meta', payload=dict(id='foreign'))] + self.rows[1:])
        self.assertEqual(self.assess().get('native_activity', {}).get('status'), 'unavailable')
        self.transcript.write_text('{"type":')
        self.assertEqual(self.assess().get('native_activity', {}).get('status'), 'unavailable')

    def test_claude_notification_does_not_reset_current_native_tool_activity(self):
        self.fixture.coords['platform'] = 'claude'
        self.fixture.event = self.fixture.intake('Append the requested line.')
        self.source['GHOST_ALICE_PLATFORM'] = 'claude'
        self.write_rows([
            dict(type='user', sessionId='current', message=dict(role='user', content='Append the line.')),
            dict(type='assistant', sessionId='current', message=dict(role='assistant', content=[
                dict(type='tool_use', name='Write', id='write-1', input={})])),
            dict(type='user', sessionId='current', promptSource='system', turnOrigin='task_notification',
                origin=dict(kind='task-notification', producer='session-task'),
                message=dict(role='user', content='The helper finished.'))])
        assessed = self.assess()
        self.assertEqual(assessed.get('native_activity', {}).get('status'), 'observed')
        self.assertTrue(assessed['body_issue'].startswith('route-unavailable:'))


class NativeCompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.event = self.fixture.setup_owned()
        self.fixture.record()
        self.source = dict(GHOST_ALICE_CORE_ROOT=str(Path(__file__).resolve().parents[1]),
            GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root),
            GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='current')

    def service(self):
        path = Path(__file__).with_name('native_completion.py')
        self.assertTrue(path.is_file(), 'Core native assessment service is missing')
        return importlib.import_module('native_completion').assess_native_completion

    def test_rejected_progress_report_does_not_infer_a_new_completion(self):
        # The actual follow-up rejected after the previous repair had applied.
        # Its words do not constitute a new structured completion submission.
        body = '''원하신 수준에는 아직 못 미칩니다. 현재 상태는 다음과 같습니다.

| 원하신 동작 | 현재 도달한 곳 |
|---|---|
| Core가 주장과 원본 근거를 검토하고, AP가 그 결과를 받아 접수·적용·정정한다 | 해당 기능은 구현됐고, 격리 검사에서 확인했습니다. |
| 기존 결과를 설명할 때 업무를 다시 실행하거나 완료 블록을 반복 제출하지 않는다 | 이번에 이 보고 경로를 수정하고 설치했습니다. |
| Core–AP 내부 처리와 사용자에게 보여 주는 업무 답변을 분리한다 | 부분적으로만 됐습니다. 기존 결과 설명은 분리했지만, 새로운 작업의 완료 보고는 여전히 출력 블록에 의존합니다. |
| 사용자 목적에 따라 필요한 후속 작업을 판단하고 실제 수행까지 이어 간다 | 판단·선택·전달 코드는 있지만, 전체 흐름이 요구대로 작동한다고 입증한 결과는 없습니다. |

가장 명확한 미완성은 방금 제 답변입니다. 긴 `[completion-check]`가 사용자 화면에 그대로 나왔습니다. 기존 보고의 반복 차단은 고쳤지만, 내부 기록 절차를 업무 답변에서 완전히 분리하지는 못했습니다.

따라서 지금은 “검증·접수·정정 기능과 특정 보고 오류를 고친 상태”입니다. 원하신 “Core와 AP가 내부에서 협력하고, 사용자는 업무 지시와 결과에 집중하는 상태”까지 완성됐다고 말할 수는 없습니다.'''
        result = self.service()(self.source, text=body, phase='final')
        self.assertIsNone(result['body_issue'])
        self.assertFalse(result['completion_route']['required_completion_check'])
        self.assertEqual(result['completion_route']['publication_route'], 'none')
        hook = Path(os.environ.get('GHOST_ALICE_TEST_SHARED_ROOT', Path(__file__).parent)) / 'claude_stop_verification_hook.py'
        executed = subprocess.run([sys.executable, '-B', str(hook), '--platform', 'codex'],
            input=json.dumps(dict(session_id='current', last_assistant_message=body)),
            text=True, capture_output=True, check=True,
            env=dict(os.environ, **self.source, CODEX_THREAD_ID='current', PYTHONDONTWRITEBYTECODE='1'))
        assert_stop_observation(self, json.loads(executed.stdout))

    def test_canonical_completion_purpose_requires_proof_without_lexical_hint(self):
        candidate = self.fixture.candidate()
        candidate['response_purpose'] = 'completion'
        self.fixture.record(candidate)
        text = 'Everything requested is represented here.'
        result = self.service()(self.source, text=text, phase='final')
        self.assertTrue(result['completion_route']['required_completion_check'])
        self.assertIsNotNone(result['body_issue'])
        self.assertEqual(result['input_event_id'], self.fixture.event)
        self.assertEqual(result['body_digest'], 'sha256:' + hashlib.sha256(text.encode()).hexdigest())

    def test_report_only_keeps_selected_work_for_ap_followup_judgment(self):
        assess = self.service()
        report = dict(present=True, valid=True, residual_text='', claims=[], issue=None)
        with patch('native_completion.evaluate_runtime_claim_report', return_value=report):
            result = assess(self.source, text='Saved report matches input', phase='final')
        self.assertTrue(result['completion_route']['report_only'])
        self.assertEqual(result['completion_route']['publication_route'], 'none')
        self.assertEqual(result['completion_route']['selected_work_unit_ids'], ['first'])
        self.assertIsNone(result['body_issue'])

    def test_stale_route_cannot_be_replaced_by_caller_policy_fields(self):
        self.fixture.intake('New current input')
        result = self.service()(self.source, phase='before_tool')
        self.assertEqual(result['completion_route']['route_binding'], 'stale')
        # Canonical intake precedes route registration. Waiting requests no
        # publication and cannot reuse the previous input's selected authority.
        self.assertEqual(result['completion_route']['publication_route'], 'none')
        self.assertEqual(result['completion_route']['selected_work_unit_ids'], [])
        self.assertEqual(result['completion_route']['selected_criteria'], [])
        self.assertIn('awaits route registration', result['completion_route']['reason'])
        self.assertIsNone(result['body_issue'])
        with self.assertRaises(TypeError):
            self.service()(self.source, claim_report={'valid': True})

    def test_missing_authority_does_not_disable_body_validation_or_create_state(self):
        result = self.service()({}, text='[completion-check]', phase='final')
        self.assertIsNotNone(result['body_issue'])
        self.assertEqual(result['context_status'], 'unavailable')

    def test_missing_route_precedes_successive_control_field_repairs(self):
        self.fixture.intake('Is the release ready?')
        for body in ('[partial-check]\n- scope: partial',
                     '[partial-check]\n- verification-before-completion: done\n'
                     '- skill-call: verification-before-completion (this turn)\n- scope: partial'):
            with self.subTest(body=body):
                result = self.service()(self.source, text=body, phase='final')
                self.assertTrue(result['body_issue'].startswith('route-unavailable:'))
                self.assertIn('task-router', result['body_issue'])
                self.assertEqual(result['completion_route']['selected_work_unit_ids'], [])

    def test_missing_route_keeps_canonical_input_binding_without_inventing_selection(self):
        coords = dict(self.fixture.coords, session_id='unrouted')
        fixtures.ledger.record_turn(**coords, raw_user_input='Explain existing results', source='hook')
        event = fixtures.ledger.read_session_state(**coords)['latest_input_event_id']
        assess = self.service()
        source = dict(self.source, GHOST_ALICE_SESSION_ID='unrouted')
        report = dict(present=True, valid=True, residual_text='', claims=[], issue=None)
        with patch('native_completion.evaluate_runtime_claim_report', return_value=report):
            result = assess(source, text='Original accepted result', phase='final')
        self.assertEqual(result['input_event_id'], event)
        self.assertIsNone(result['response_route'])
        self.assertEqual(result['completion_route']['selected_work_unit_ids'], [])
        self.assertEqual(result['completion_route']['publication_route'], 'none')

    def test_pretool_never_runs_final_body_or_original_evidence_assessment(self):
        assess = self.service()
        with patch('native_completion.evaluate_runtime_claim_report', side_effect=AssertionError('unexpected proof lookup')):
            result = assess(self.source, phase='before_tool')
        self.assertEqual(result['completion_route']['publication_route'], 'prepare')
        self.assertIsNone(result['body_issue'])

    def test_runtime_mirror_resolves_ledger_from_trusted_installation_binding(self):
        import original_claim_evidence as original
        @contextmanager
        def archive(source):
            connection = sqlite3.connect((self.fixture.root / 'ghost-state.sqlite3').as_uri() + '?mode=ro',
                uri=True, isolation_level=None)
            try:
                connection.execute('BEGIN')
                yield SimpleNamespace(connection=connection, root=self.fixture.root,
                    owner=dict(platform='codex', session_id='current'))
            finally:
                connection.close()
        claim = dict(statement='Original result', work_unit_id='first', contract_revision=1,
            criterion_id='AC1', proof_digest='sha256:' + 'a' * 64)
        mirror = Path(self.fixture.tmp.name) / 'runtime/current/_shared/original_claim_evidence.py'
        with patch.object(original, '__file__', str(mirror)):
            result = original.resolve_retained_claims(self.source, [claim],
                archive_provider=SimpleNamespace(open_claim_archive=archive))
        self.assertEqual(result['claims'][0]['reason'], 'current-proof-mismatch')
        self.assertEqual(result['status'], 'invalid')


class NativeProofBindingTests(unittest.TestCase):
    def test_cli_records_complete_partial_response_without_echoing_original(self):
        proof = self.proof.replace('[completion-check]', '[partial-check]\n- scope: partial').replace(
            '  - none', '  - AC1: Native application remains unverified')
        body = proof + '\n\nThe footer observation is retained. Native application remains unverified.'
        response_file = Path(self.owner.fixture.tmp.name) / 'answer.txt'
        response_file.write_text(body)
        shared = Path(os.environ.get('GHOST_ALICE_TEST_SHARED_ROOT', Path(__file__).parent))
        env = dict(os.environ, **self.owner.source, PYTHONDONTWRITEBYTECODE='1')
        def cli(*args):
            return subprocess.run([sys.executable, '-B', str(shared / 'completion_assessment.py'), *args],
                env=env, text=True, capture_output=True)
        prepared = cli('prepare-response', '--response-file', str(response_file),
            '--verified-at', self.owner.time, '--source', str(self.owner.observation))
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        candidate = json.loads(prepared.stdout)
        self.assertNotIn('text', candidate['response_binding'])
        self.assertNotIn('proof', candidate)
        self.assertNotIn(body, prepared.stdout)
        sources = [s['source_id'] for s in candidate['sources']]
        claims = [dict(binding_id=b['binding_id'], verdict='supported', source_ids=sources,
            rationale='The observed footer result supports only the retained partial claim.')
            for b in candidate['bindings']]
        judgment_file = response_file.with_suffix('.json')
        judgment_file.write_text(json.dumps(dict(claims=claims)))
        rejected = cli('record-response', '--candidate', candidate['candidate_reference'],
            '--judgment-file', str(judgment_file))
        self.assertNotEqual(rejected.returncode, 0)
        with self.assertRaises(self.api.CompletionAssessmentError):
            self.api.lookup_current_proof(self.owner.source, proof)
        judgment_file.write_text(json.dumps(dict(claims=claims, response=dict(
            binding_id=candidate['response_binding']['binding_id'], verdict='supported',
            source_ids=sources, rationale='The complete answer retains the same partial scope and uncertainty.'))))
        recorded = cli('record-response', '--candidate', candidate['candidate_reference'],
            '--judgment-file', str(judgment_file))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        receipt = json.loads(recorded.stdout)
        self.assertEqual(receipt['status'], 'reviewed')
        self.assertNotIn('response_text', receipt)
        self.api.lookup_current_response(self.owner.source, body)
        delivered = body
        self.assertIsNone(self.assess(delivered)['body_issue'])
        self.assertEqual(self.assess(delivered)['completion_route']['publication_route'], 'none')
        stopped = subprocess.run([sys.executable, '-B', str(shared / 'claude_stop_verification_hook.py'),
            '--platform', 'codex'], input=json.dumps(dict(session_id='current', last_assistant_message=delivered)),
            env=env, text=True, capture_output=True, check=True)
        assert_stop_observation(self, json.loads(stopped.stdout))
        self.assertIsNotNone(self.assess(delivered + '\nEverything is now complete.')['body_issue'])

    def setUp(self):
        from test_completion_assessment import CompletionAssessmentTests
        self.owner = CompletionAssessmentTests()
        self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)
        self.api = self.owner.api()
        self.proof = self.owner.proof().replace('[completion-check]',
            '[completion-check]\n- verification-before-completion: done\n'
            '- skill-call: verification-before-completion (this turn)')

    def assess(self, proof):
        return importlib.import_module('native_completion').assess_native_completion(
            self.owner.source, text=proof, phase='final')

    def review(self, verdict='supported'):
        self.review_response(self.proof, claim_verdict=verdict)

    def test_final_cannot_use_syntax_only_proof_without_core_review(self):
        result = self.assess(self.proof)
        self.assertIsNotNone(result['body_issue'])
        self.assertIn('assessment-required', result['body_issue'])

    def test_final_rejects_unreviewed_summary_borrowing_valid_proof(self):
        original = self.owner.prepare(self.proof)
        self.owner.review(original)
        for text in (self.proof,
                     self.proof + '\n\nAll security flaws are fixed and the production rollout is complete.',
                     self.proof + '\n\n설치본과 실제 호스트 적용까지 전부 완료됐습니다.'):
            with self.subTest(text=text):
                self.assertIn('response-assessment-required', self.assess(text)['body_issue'] or '')

    def review_response(self, body, *, verdict='supported', claim_verdict='supported', reuse_candidate=None):
        candidate = self.api.prepare_assessment(self.owner.source, proof=self.proof,
            verified_at=self.owner.time, source_paths=[] if reuse_candidate else [str(self.owner.observation)],
            reuse_candidate=reuse_candidate, response_text=body)
        judgments = [dict(binding_id=b['binding_id'], verdict=claim_verdict,
            source_ids=[candidate['sources'][0]['source_id']], rationale='The original footer observation supports this binding.')
            for b in candidate['bindings']]
        response = dict(binding_id=candidate['response_binding']['binding_id'], verdict=verdict,
            source_ids=[candidate['sources'][0]['source_id']], rationale='The complete displayed response stays within the original footer observation.')
        self.api.record_assessment(self.owner.source, candidate['candidate_reference'], judgments,
            response_judgment=response)
        return candidate

    def test_final_accepts_exact_reviewed_response_and_rejects_later_expansion(self):
        body = self.proof + '\n\nThe footer correction is verified.\n\n[io-trace]\n- files-read: original observation\n'
        self.review_response(body)
        self.assertIsNone(self.assess('\n' + body + '\n')['body_issue'])
        for changed in (body + 'The deployment is complete.', 'The deployment is complete.\n' + body,
                        body.replace('footer correction', 'cloud deployment')):
            with self.subTest(changed=changed):
                self.assertIsNotNone(self.assess(changed)['body_issue'])

    def test_response_review_appends_to_original_proof_without_refreshing_observation(self):
        original = self.owner.prepare(self.proof)
        self.owner.review(original)
        before = self.owner.lookup(original).reference
        body = self.proof + '\n\nThe footer correction is verified.'
        self.review_response(body, reuse_candidate=original['candidate_reference'])
        self.assertIsNone(self.assess(body)['body_issue'])
        self.assertEqual(self.owner.lookup(original).reference, before)
        self.assertEqual(self.owner.lookup(original).candidate['verified_at'], self.owner.time)

    def test_unsupported_response_does_not_erase_supported_original_proof(self):
        original = self.owner.prepare(self.proof)
        self.owner.review(original)
        before = self.owner.lookup(original).reference
        for body in (self.proof, self.proof + '\n\nThe cloud deployment is complete.'):
            with self.subTest(body=body):
                self.review_response(body, verdict='unsupported', reuse_candidate=original['candidate_reference'])
                self.assertIn('response-unsupported', self.assess(body)['body_issue'] or '')
                retained = self.owner.lookup(original)
                self.assertEqual(retained.reference, before)
                self.assertEqual(retained.candidate['verified_at'], self.owner.time)

    def test_reviewed_partial_and_honest_failure_response_remain_deliverable(self):
        partial = self.proof.replace('[completion-check]', '[partial-check]\n- scope: partial').replace(
            '  - none', '  - AC1: current delivery remains unverified')
        failed = self.proof.replace('verdict: pass', 'verdict: fail')
        for proof, summary in ((partial, 'The footer observation is retained; delivery remains unverified.'),
                               (failed, 'The checked footer requirement remains unmet.')):
            with self.subTest(summary=summary):
                self.proof = proof
                body = proof + '\n\n' + summary
                self.review_response(body)
                self.assertIsNone(self.assess(body)['body_issue'])
                self.assertIsNotNone(self.assess(body + '\nThe entire task is complete.')['body_issue'])

    def test_response_binding_keeps_sources_owner_and_current_contract_strict(self):
        body = self.proof + '\n\nThe footer correction is verified.'
        self.review_response(body)
        self.owner.observation.write_text('Changed original observation')
        self.assertIn('source-changed', self.assess(body)['body_issue'])
        self.owner.fixture.intake('A new input without a route')
        self.assertIsNotNone(self.assess(body)['body_issue'])

    def test_final_rejects_unsupported_or_changed_claim_after_review(self):
        self.review('unsupported')
        self.assertIsNotNone(self.assess(self.proof)['body_issue'])
        changed = self.proof.replace('폴더 탭의 푸터가 목록을 더 이상 가리지 않습니다.',
                                    'The writing arm is visible.')
        self.assertIsNotNone(self.assess(changed)['body_issue'])

    def test_final_rejects_observation_changed_after_review(self):
        self.review()
        self.owner.observation.write_text('The footer still overlaps the list.')
        self.assertIn('source-changed', self.assess(self.proof)['body_issue'])

    def assert_source_recovery(self, result, status):
        self.assertTrue(result['body_issue'].startswith(status + ':'), result['body_issue'])
        self.assertIn('Completion acceptance is blocked for the affected review', result['body_issue'])
        if status == 'source-changed':
            self.assertIn('compares only the affected claims with the changed source under verification-before-completion', result['body_issue'])
        else:
            self.assertIn('until that source or authenticated retained proof is available', result['body_issue'])
        self.assertNotIn('if needed', result['body_issue'])
        self.assertIn('does not establish that a claim is false', result['body_issue'])
        self.assertIn('original proof, source references and verification time', result['body_issue'])
        self.assertIn('Do not replay business work or expand unrelated review', result['body_issue'])
        self.assertEqual(result['repair_target'], 'source_status')
        self.assertIsNone(result['completion_route']['publication_criteria'])

    def test_source_failure_before_review_resolution_preserves_original_proof(self):
        candidate = self.review_response(self.proof)
        original = self.owner.observation.read_bytes()
        for status in ('source-changed', 'source-unavailable'):
            with self.subTest(status=status):
                if status == 'source-changed':
                    self.owner.observation.write_text('Changed source bytes, without a new semantic judgment.')
                else:
                    self.owner.observation.unlink()
                try:
                    self.assert_source_recovery(self.assess(self.proof), status)
                    retained = self.owner.lookup(candidate, historical=True)
                    self.assertEqual(retained.candidate['proof'], self.proof)
                    self.assertEqual(retained.candidate['verified_at'], self.owner.time)
                finally:
                    self.owner.observation.write_bytes(original)
        self.assertIsNone(self.assess(self.proof)['body_issue'])

    def test_available_coverage_does_not_turn_source_issues_into_semantic_refutation(self):
        independent = Path(self.owner.fixture.tmp.name) / 'independent-observation.txt'
        independent.write_text('The retained report shows the footer clear and the independent check passed.')
        criterion = dict(self.owner.fixture.criterion(revision=2, summary='Retain the independent check.'), id='AC2')
        fixtures.ledger.record_turn(**self.owner.fixture.coords,
            expected_input_event_id=self.owner.fixture.event,
            intent_delta={'acceptance_criteria': [
                self.owner.fixture.criterion(revision=2, summary=self.owner.summary), criterion]})
        route = self.owner.fixture.candidate()
        route['work_units'][0]['contract_revision'] = 2
        route['selected_criteria'][0]['contract_revision'] = 2
        route['selected_criteria'].append(dict(work_unit_id='first', contract_revision=2, criterion_id='AC2'))
        self.owner.fixture.record(route)
        self.proof = self.proof.replace('- claim-evidence-map:',
            '  - AC2: Retain the independent check. [source: user-said]\n- claim-evidence-map:')
        self.proof = self.proof.replace('- unverified:',
            '  - claim: The independent check passed.\n    criterion: AC2\n'
            '    evidence: ' + str(independent) + '\n    verdict: pass\n- unverified:')
        candidate = self.api.prepare_assessment(self.owner.source, proof=self.proof,
            verified_at=self.owner.time,
            source_paths=[str(self.owner.observation), str(independent)], response_text=self.proof)
        source_ids = {s['locator']: s['source_id'] for s in candidate['sources']}
        judgments = [dict(binding_id=b['binding_id'], verdict='supported',
            source_ids=[source_ids[str(self.owner.observation if b['criterion']['id'] == 'AC1' else independent)]],
            rationale='The identified original observation supports this criterion.') for b in candidate['bindings']]
        self.api.record_assessment(self.owner.source, candidate['candidate_reference'], judgments,
            response_judgment=dict(binding_id=candidate['response_binding']['binding_id'], verdict='supported',
                source_ids=[source_ids[str(independent)]], rationale='The retained report supports the complete answer.'))
        original = self.owner.observation.read_bytes()
        for status in ('source-changed', 'source-unavailable'):
            with self.subTest(status=status):
                if status == 'source-changed':
                    self.owner.observation.write_text('Changed bytes, with no claim review yet.')
                else:
                    self.owner.observation.unlink()
                try:
                    reviewed = self.api.lookup_current_response(self.owner.source, self.proof)
                    self.assertEqual(reviewed.source_issues, {source_ids[str(self.owner.observation)]: status})
                    self.assertEqual(self.api.assessment_coverage(reviewed, candidate['contract'], self.proof,
                        self.owner.time), {('first', 2, 'AC2')})
                    self.assert_source_recovery(self.assess(self.proof), status)
                    self.assertEqual(reviewed.candidate['proof'], self.proof)
                    self.assertEqual(reviewed.candidate['verified_at'], self.owner.time)
                finally:
                    self.owner.observation.write_bytes(original)
        self.assertIsNone(self.assess(self.proof)['body_issue'])

    def test_stop_preserves_source_failure_without_starting_delivery_repair(self):
        body = self.proof + '\n\nThe footer correction is verified.'
        self.review_response(body)
        original = self.owner.observation.read_bytes()
        shared = Path(os.environ.get('GHOST_ALICE_TEST_SHARED_ROOT', Path(__file__).parent))
        env = dict(os.environ, **self.owner.source, PYTHONDONTWRITEBYTECODE='1')
        for status in ('source-changed', 'source-unavailable'):
            with self.subTest(status=status):
                if status == 'source-changed':
                    self.owner.observation.write_text('Changed bytes without a semantic judgment.')
                else:
                    self.owner.observation.unlink()
                try:
                    stopped = subprocess.run([sys.executable, '-B', str(shared / 'claude_stop_verification_hook.py'),
                        '--platform', 'codex'], input=json.dumps(dict(session_id='current',
                        last_assistant_message=body)),
                        env=env, text=True, capture_output=True, check=True)
                    result = json.loads(stopped.stdout)
                    observed = assert_stop_observation(self, result, issue=status + ':', repair_target='source_status')
                    self.assertEqual(observed['body_issue'], self.assess(body)['body_issue'])
                finally:
                    self.owner.observation.write_bytes(original)

    def test_final_delivers_reviewed_honest_failure_without_marking_criterion_met(self):
        self.proof = self.proof.replace('폴더 탭의 푸터가 목록을 더 이상 가리지 않습니다.',
            'The footer still covers the list.').replace('verdict: pass', 'verdict: fail')
        self.owner.observation.write_text('The footer still covers the list.')
        before = fixtures.ledger.read_session_state(**self.owner.fixture.coords)
        try:
            self.review_response(self.proof)
        except self.api.CompletionAssessmentError as exc:
            self.fail('A supported fail verdict must reach native final delivery: ' + str(exc))
        result = self.assess(self.proof)
        self.assertIsNone(result['body_issue'])
        self.assertEqual(result['completion_route']['publication_route'], 'none')
        self.assertEqual(result['completion_route']['publication_criteria'], [])
        self.assertEqual(fixtures.ledger.read_session_state(**self.owner.fixture.coords), before)

    def test_reviewed_partial_with_only_unverified_criteria_needs_no_publication(self):
        self.proof = self.proof.replace('[completion-check]', '[partial-check]\n- scope: partial')
        self.proof = self.proof.replace('  - none', '  - AC1: The bounded observation does not establish all requested behavior')
        self.review()
        result = self.assess(self.proof)
        self.assertIsNone(result['body_issue'])
        self.assertEqual(result['completion_route']['publication_route'], 'none')
        self.assertEqual(result['completion_route']['publication_criteria'], [])
        self.assertEqual(result['completion_route']['selected_work_unit_ids'], ['first'])

    def test_reviewed_success_exposes_qualified_publication_coverage(self):
        self.review()
        result = self.assess(self.proof)
        self.assertIsNone(result['body_issue'])
        self.assertNotEqual(result['completion_route']['publication_route'], 'none')
        self.assertEqual(result['completion_route']['publication_criteria'], [
            dict(work_unit_id='first', contract_revision=1, criterion_id='AC1')])


class RuntimeInstallationIsolationTests(unittest.TestCase):
    def test_installed_stop_modules_do_not_import_later_source_edits(self):
        import shutil
        import tempfile
        import install_destination
        import install_hooks

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source_shared = source / '_shared'
            source_shared.mkdir(parents=True)
            for path in Path(__file__).parent.iterdir():
                if path.is_file() and path.suffix in {'.py', '.mjs', '.json'}:
                    shutil.copy2(path, source_shared / path.name)
            ledger_relative = Path('session-intent-analyzer/scripts/session_intent_ledger.py')
            source_ledger = source / ledger_relative
            source_ledger.parent.mkdir(parents=True)
            shutil.copy2(fixtures.ROOT / ledger_relative, source_ledger)
            destination = install_destination.InstallDestination(
                mode='isolated', owned_root=str(root), platform_dir=str(root / 'platform'),
                skills_dir=str(root / 'skills'), ghost_root=str(root / 'ghost'),
                ledger_root=str(root / 'ledger'), project_root=str(root / 'project'),
                core_source_root=str(source))
            with install_destination.use(destination):
                install_hooks._prepare_destination_runtime(destination)

            # A source edit can introduce an API absent from the installed snapshot.
            with (source_shared / 'completion_check_validator.py').open('a') as stream:
                stream.write('\nuninstalled_source_api = True\n')
            (source_shared / 'completion_assessment.py').write_text(
                'from completion_check_validator import uninstalled_source_api\n')
            with source_ledger.open('a') as stream:
                stream.write('\ndef reconcile_native_inputs(**kwargs):\n'
                             '    raise AssertionError("Uninstalled source ledger executed")\n')

            environment = {key: value for key, value in os.environ.items()
                if not key.startswith('GHOST_ALICE_') and key not in {'CODEX_THREAD_ID', 'PYTHONPATH'}}
            environment = install_destination.binding_environment(destination, environment)
            environment.update(GHOST_ALICE_PLATFORM='claude', GHOST_ALICE_SESSION_ID='runtime-isolation')
            program = '''import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import hook_profile_gate
import native_completion
import completion_check_validator
core = native_completion._ledger(os.environ)
core.read_response_route(root=Path(os.environ['GHOST_ALICE_SESSION_INTENT_ROOT']),
    platform='claude', session_id='runtime-isolation')
import completion_assessment, response_route, session_intent_analyzer_hook
hook_profile_gate._reconcile_native_intake(os.environ,
    {'hook_event_name': 'Stop', 'session_id': 'runtime-isolation'})
print(json.dumps({
    'ledger': core.__file__,
    'intake_ledger': session_intent_analyzer_hook.record_turn.__code__.co_filename,
    'route': response_route.__file__,
    'assessment': completion_assessment.__file__,
    'validator': completion_check_validator.__file__,
    'ledger_root': os.environ['GHOST_ALICE_SESSION_INTENT_ROOT'],
    'source_root': os.environ['GHOST_ALICE_CORE_ROOT'],
}))
'''
            result = subprocess.run([sys.executable, '-B', '-c', program, destination.runtime_shared_dir],
                env=environment, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            loaded = json.loads(result.stdout)
            runtime = Path(destination.runtime_shared_dir).resolve().parent
            self.assertEqual(Path(loaded['ledger']).resolve(), runtime / ledger_relative)
            self.assertEqual(Path(loaded['intake_ledger']).resolve(), runtime / ledger_relative)
            for key, filename in (('route', 'response_route.py'),
                                  ('assessment', 'completion_assessment.py'),
                                  ('validator', 'completion_check_validator.py')):
                self.assertEqual(Path(loaded[key]).resolve(), runtime / '_shared' / filename)
            self.assertEqual(loaded['ledger_root'], destination.ledger_root)
            self.assertEqual(loaded['source_root'], destination.core_source_root)


if __name__ == '__main__':
    unittest.main()
