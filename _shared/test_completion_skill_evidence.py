#!/usr/bin/env python3
"""Native completion eligibility comes from Core's authenticated response review."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import completion_check_validator as validator

HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")
BODY = """[completion-check]
- acceptance-criteria:
  - c1: recompute the operating time [source: user-said]
- claim-evidence-map:
  - claim: operating time was checked
    criterion: c1
    evidence: python check.py exited 0
    verdict: pass
- unverified:
  - none
- evidence: python check.py exited 0
"""
IO_TRACE = "[io-trace]\n- commands-run: [python check.py]\n"
WITHOUT_SELF_REPORT = f"Business result: 82 hours 40 minutes.\n\n{BODY}{IO_TRACE}"
SELF_REPORT_LINES = (
    "- verification-before-completion: done\n"
    "- skill-call: verification-before-completion (this turn)\n"
)
WITH_SELF_REPORT = WITHOUT_SELF_REPORT.replace(
    "[completion-check]\n", f"[completion-check]\n{SELF_REPORT_LINES}", 1,
).replace("[io-trace]\n", "[io-trace]\n- skills-loaded: [verification-before-completion]\n", 1)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def skill_call():
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": "verification-before-completion"}}]}}


def reply(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def assert_stop_observation(case, payload, *, issue=None, repair_target=None):
    """Stop transport must not substitute for acceptance of the observed body."""
    case.assertIs(payload.get('continue'), True)
    for key in ('decision', 'reason', 'systemMessage'):
        case.assertNotIn(key, payload)
    observation = payload['response_assessment']
    case.assertEqual(observation['status'], 'unaccepted' if issue else 'no-issue')
    if issue:
        case.assertIn(issue, observation['body_issue'])
    else:
        case.assertIsNone(observation['body_issue'])
    if repair_target is not None:
        case.assertEqual(observation['repair_target'], repair_target)
    return observation


class ValidatorOptionTests(unittest.TestCase):
    def check(self, text, **kwargs):
        return validator.validate_completion_text(text, require_completion_check=True, **kwargs)

    def test_evidence_structure_does_not_require_procedural_self_report(self):
        self.assertIsNone(self.check(WITHOUT_SELF_REPORT))

    def test_evidence_structure_accepts_optional_old_self_report_lines(self):
        self.assertIsNone(self.check(WITH_SELF_REPORT))

    def test_evidence_structure_still_rejects_a_missing_evidence_field(self):
        broken = WITHOUT_SELF_REPORT.replace("    evidence: python check.py exited 0\n", "")
        self.assertIn("evidence", self.check(broken))

    def test_evidence_structure_accepts_missing_or_reordered_optional_trace(self):
        self.assertIsNone(self.check(f"Result.\n\n{BODY}"))
        before = f"Result.\n\n{IO_TRACE}\n{BODY}"
        self.assertIsNone(self.check(before))

    def test_retired_gate_state_after_the_block_is_ordinary_text(self):
        # Decision 14 retired the printed [gate-state] block.
        late = WITHOUT_SELF_REPORT + "\n[gate-state]\n- task-router: done\n"
        self.assertNotIn("gate-state", self.check(late) or "")


class StopHookSkillEvidenceTests(unittest.TestCase):
    def run_hook(self, entries=None, *, platform="claude", message=None,
                 review_verdict="supported", mutate_source=False, append_text="",
                 response_purpose=None, record_review=True):
        env = {k: v for k, v in os.environ.items()
               if k not in {"CLAUDE_CODE_ENTRYPOINT", "CODEX_THREAD_ID"}
               and not k.startswith('GHOST_ALICE_')}
        env["CLAUDE_CODE_ENTRYPOINT"] = "claude-desktop"
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = {"session_id": "fixture-stop"}
            if message is not None:
                payload["last_assistant_message"] = message
            if entries is not None:
                transcript = Path(temp_dir) / "transcript.jsonl"
                transcript.write_text("".join(json.dumps(dict(e, sessionId=payload['session_id'])) + "\n"
                    for e in entries), encoding="utf-8")
                payload["transcript_path"] = str(transcript)
            text = message or next((part['text'] for entry in reversed(entries or [])
                for part in entry.get('message', {}).get('content', [])
                if isinstance(part, dict) and part.get('type') == 'text'), '')
            observation = StopHookSkillEvidenceTests._review_format_fixture(
                Path(temp_dir), platform, text, env, review_verdict,
                response_purpose=response_purpose, record_review=record_review)
            if mutate_source:
                observation.write_text("The original observation changed.", encoding="utf-8")
            if append_text:
                payload["last_assistant_message"] = text + append_text
            result = subprocess.run([sys.executable, "-B", str(HOOK), "--platform", platform],
                                    input=json.dumps(payload), text=True, capture_output=True, check=True, env=env)
        return json.loads(result.stdout)

    @staticmethod
    def _review_format_fixture(root, platform, text, env, review_verdict="supported", *,
                               session_id="fixture-stop", ledger_root=None, core_root=None,
                               response_purpose=None, record_review=True,
                               input_text='Synthetic protocol fixture'):
        """Give native protocol cases explicit synthetic reviewed observations.

        These cases isolate host protocol, not semantic review quality. Missing,
        unsupported and changed reviews are exercised by NativeProofBindingTests.
        No original application checks or real session records are touched.
        """
        try:
            proof = validator.extract_original_proof(text)
            parsed = validator.parse_original_completion_evidence(
                proof, allow_partial=True, allow_failed_completion=True)
        except ValueError:
            proof, parsed = '', None
        import test_response_route as fixtures
        from completion_assessment import prepare_response_review, record_response_review
        owner = dict(platform=platform, session_id=session_id)
        coords = dict(root=ledger_root or root / 'ledger', **owner)
        fixtures.ledger.record_turn(**coords, raw_user_input=input_text, source='hook')
        event = fixtures.ledger.read_session_state(**coords)['latest_input_event_id']
        ids = validator.extract_acceptance_criteria_ids(proof) if proof else ['c1']
        criteria = [dict(id=key, summary='Synthetic criterion ' + key, source='user-said',
                        admitted=True, work_unit_id='first', contract_revision=1) for key in ids]
        fixtures.ledger.record_turn(**coords, expected_input_event_id=event,
            intent_delta={'acceptance_criteria': criteria})
        route = fixtures.ResponseRouteTests().candidate()
        if response_purpose is not None:
            route['response_purpose'] = response_purpose
        route['work_units'][0]['owner'] = owner
        route['selected_criteria'] = [dict(work_unit_id='first', contract_revision=1,
                                          criterion_id=key) for key in ids]
        fixtures.ledger.record_response_route(**coords, expected_input_event_id=event,
            candidate=route, boundary_ref='synthetic-protocol')
        source = dict(GHOST_ALICE_CORE_ROOT=str(core_root or HOOK.parent.parent),
            GHOST_ALICE_SESSION_INTENT_ROOT=str(coords['root']),
            GHOST_ALICE_PLATFORM=platform, GHOST_ALICE_SESSION_ID=owner['session_id'])
        env.update(source)
        if not record_review or (parsed is None and response_purpose != 'explanation'):
            return None
        observation = root / 'synthetic-observations.txt'
        observation.write_text(json.dumps(parsed['claims']) if parsed is not None else text, encoding='utf-8')
        review_inputs = (dict(verified_at='2026-10-10T12:00:00.000000+00:00')
                        if parsed is not None else dict(claim_statements=[text]))
        candidate = prepare_response_review(source, response_text=text,
            source_paths=[str(observation)], **review_inputs)
        claims = [dict(binding_id=b['binding_id'],
            source_ids=[candidate['sources'][0]['source_id']], verdict='supported',
            rationale='The explicit synthetic fixture supplies this observation for host protocol testing.')
            for b in candidate['bindings']]
        record_response_review(source, candidate['candidate_reference'], dict(claims=claims,
            response=dict(binding_id=candidate['response_binding']['binding_id'],
                source_ids=[candidate['sources'][0]['source_id']], verdict=review_verdict,
                rationale='The synthetic source supports the complete answer only when the review says supported.')))
        env.update(source)
        return observation

    def test_claude_allows_a_block_without_self_report_when_the_skill_was_called(self):
        payload = self.run_hook([user("Compute it."), skill_call(), reply(WITHOUT_SELF_REPORT)])
        assert_stop_observation(self, payload)

    def test_claude_still_allows_the_old_self_report_lines(self):
        payload = self.run_hook([user("Compute it."), skill_call(), reply(WITH_SELF_REPORT)])
        assert_stop_observation(self, payload)

    def test_claude_accepts_authenticated_response_without_skill_call(self):
        payload = self.run_hook([user("Compute it."), reply(WITH_SELF_REPORT)])
        assert_stop_observation(self, payload)

    def test_first_claude_stop_accepts_review_without_procedural_markers(self):
        payload = self.run_hook([user("Compute it."), reply(WITHOUT_SELF_REPORT)])
        assert_stop_observation(self, payload)

    def test_claude_records_missing_evidence_as_unaccepted_without_continuation(self):
        broken = WITHOUT_SELF_REPORT.replace("    evidence: python check.py exited 0\n", "")
        payload = self.run_hook([user("Compute it."), skill_call(), reply(broken)])
        assert_stop_observation(self, payload, issue='evidence', repair_target='completion_metadata')

    def test_first_codex_stop_accepts_review_without_procedural_markers(self):
        payload = self.run_hook(platform="codex", message=WITHOUT_SELF_REPORT)
        assert_stop_observation(self, payload)

    def test_authenticated_whole_answer_still_needs_supported_judgment(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                payload = self.run_hook(platform=platform, message=WITHOUT_SELF_REPORT,
                    review_verdict="unsupported")
                assert_stop_observation(self, payload, issue='response-unsupported')

    def test_changed_complete_answer_cannot_borrow_review(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                payload = self.run_hook(platform=platform, message=WITHOUT_SELF_REPORT,
                    append_text="\n\nAll unrelated work is also complete.")
                assert_stop_observation(self, payload, issue='response-assessment-required')

    def test_changed_source_keeps_authenticated_response_unaccepted(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                payload = self.run_hook(platform=platform, message=WITHOUT_SELF_REPORT,
                    mutate_source=True)
                assert_stop_observation(self, payload, issue='source-changed', repair_target='source_status')


if __name__ == "__main__":
    unittest.main()
