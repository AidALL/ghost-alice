"""Synthetic response-review records and real Stop subprocesses for protocol tests.

Dependencies: Python 3.11+ standard library. These fixtures do not evaluate claim meanings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import completion_check_validator as validator


HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")
# These source labels are the test's fixtures for the response model's claims about the criteria's origin.
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
WITHOUT_SELF_REPORT = f"Business result: 82 hours 40 minutes.\n\n{BODY}"
# The human user retired printed skill-loading self-reports; the guards below keep those lines from establishing completion.
SELF_REPORT_LINES = (
    "- verification-before-completion: done\n"
    "- skill-call: verification-before-completion (this turn)\n"
)
WITH_SELF_REPORT = WITHOUT_SELF_REPORT.replace(
    "[completion-check]\n", f"[completion-check]\n{SELF_REPORT_LINES}", 1,
) + "[io-trace]\n- skills-loaded: [verification-before-completion]\n"


def user_role_row(text):
    """A host transcript user-role row; the role does not authenticate its sender."""
    return {"type": "user", "message": {"role": "user", "content": text}}


def skill_call():
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": "verification-before-completion"}}]}}


def reply(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def stop_issue(payload):
    """The first issue the Stop notice message records, or an empty text."""
    return next((issue['detail'] for issue in payload['stop_notice']['issues']), '')


def assert_stop_observation(case, payload, *, issue=None, repair_target=None):
    """Stop informs and stops: no control or context field, and the notice text only when the message has one."""
    case.assertIs(payload.get('continue'), True)
    for key in ('decision', 'reason', 'stopReason', 'additionalContext', 'hookSpecificOutput'):
        case.assertNotIn(key, payload)
    message = payload['stop_notice']
    case.assertEqual(message['schema_version'], 'stop-notice.v1')
    case.assertIn(message['state'], ('notice', 'quiet'))
    if message['state'] == 'notice':
        case.assertEqual(payload.get('systemMessage'), message['text'])
    else:
        case.assertNotIn('systemMessage', payload)
    details = [entry['detail'] for entry in message['issues']]
    if issue:
        case.assertEqual(message['state'], 'notice')
        case.assertTrue(any(issue in detail for detail in details), details)
    else:
        case.assertEqual(details, [])
        case.assertEqual(message['unsupported_claims'], [])
    if repair_target is not None:
        case.assertEqual(message['issues'][0]['repair_target'], repair_target)
    return dict(status='unaccepted' if details else 'no-issue', body_issue=details[0] if details else None,
                repair_target=message['issues'][0]['repair_target'] if details else None,
                input_event_id=message['input_event_id'], body_digest=message['body_digest'])


def run_reviewed_stop(entries=None, *, platform="claude", message=None,
             review_verdict="supported", mutate_source=False, append_text="",
             record_review=True):
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC"}}
    env["CLAUDE_CODE_ENTRYPOINT"] = "claude-desktop"
    with tempfile.TemporaryDirectory() as temp_dir:
        env.update(HOME=temp_dir, USERPROFILE=temp_dir, TMPDIR=temp_dir, TMP=temp_dir, TEMP=temp_dir)
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
        observation = review_format_fixture(
            Path(temp_dir), platform, text, env, review_verdict,
            record_review=record_review)
        if mutate_source:
            observation.write_text("The original observation changed.", encoding="utf-8")
        if append_text:
            payload["last_assistant_message"] = text + append_text
        result = subprocess.run([sys.executable, "-B", str(HOOK), "--platform", platform],
                                input=json.dumps(payload), text=True, capture_output=True, check=True, env=env)
    return json.loads(result.stdout)


def review_format_fixture(root, platform, text, env, review_verdict="supported", *,
                           session_id="fixture-stop", ledger_root=None, core_root=None,
                           record_review=True, input_text='Synthetic protocol fixture',
                           claim_verdicts=None, declared_remaining_work=None):
    """Give native protocol cases explicit synthetic reviewed observations.

    These cases isolate host protocol, not semantic review quality. Missing,
    unsupported and changed reviews are exercised by NativeProofBindingTests.
    No original application checks or real session records are touched.
    claim_verdicts sets each claim's verdict in binding order; declared_remaining_work
    is stored with the route as the response model's declared remaining work.
    """
    try:
        proof = validator.extract_original_proof(text)
        parsed = validator.parse_original_completion_evidence(
            proof, allow_partial=True, allow_failed_completion=True)
    except ValueError:
        proof, parsed = '', None
    from ledger_access import load_ledger
    ledger = load_ledger()
    from completion_assessment import prepare_response_review, record_response_review
    owner = dict(platform=platform, session_id=session_id)
    coords = dict(root=ledger_root or root / 'ledger', **owner)
    ledger.record_turn(**coords, raw_input=input_text, recording_path='hook')
    event = ledger.read_session_state(**coords)['latest_input_event_id']
    ids = validator.extract_acceptance_criteria_ids(proof) if proof else ['c1']
    # The test writes the response model's source claims and interpreted-as update; Core's source default does not check the sender.
    criteria = [dict(id=key, summary='Synthetic criterion ' + key, source='user-said',
                    selectable=True, work_unit_id='first', contract_revision=1) for key in ids]
    ledger.record_turn(**coords, expected_input_event_id=event,
        intent_delta={'acceptance_criteria': criteria})
    route = dict(schema_version='response-route.v2',
        routing_surface=dict(response_mode='normal', boundary_contract='required'),
        work_units=[dict(work_unit_id='first', contract_revision=1, kind='execution',
                         selectable=True, owner=owner)], selected_work_unit_ids=['first'])
    route['selected_criteria'] = [dict(work_unit_id='first', contract_revision=1,
                                      criterion_id=key) for key in ids]
    if declared_remaining_work is not None:
        route['declared_remaining_work'] = declared_remaining_work
    ledger.record_response_route(**coords, expected_input_event_id=event,
        candidate=route, boundary_ref='synthetic-protocol')
    source = dict(GHOST_ALICE_CORE_ROOT=str(core_root or HOOK.parent.parent),
        GHOST_ALICE_SESSION_INTENT_ROOT=str(coords['root']),
        GHOST_ALICE_PLATFORM=platform, GHOST_ALICE_SESSION_ID=owner['session_id'])
    env.update(source)
    if not record_review or (parsed is None and validator.looks_like_evidence_claim(text)):
        # A malformed control block has no reviewable proof; an answer without a block is an explanation.
        return None
    observation = root / 'synthetic-observations.txt'
    observation.write_text(json.dumps(parsed['claims']) if parsed is not None else text, encoding='utf-8')
    review_inputs = (dict(verified_at='2026-10-10T12:00:00.000000+00:00')
                    if parsed is not None else dict(claim_statements=[text]))
    candidate = prepare_response_review(source, response_text=text,
        source_paths=[str(observation)], **review_inputs)
    verdicts = claim_verdicts or ['supported'] * len(candidate['bindings'])
    claims = [dict(binding_id=b['binding_id'],
        source_ids=[candidate['sources'][0]['source_id']], verdict=verdict,
        rationale='The explicit synthetic fixture supplies this observation for host protocol testing.')
        for b, verdict in zip(candidate['bindings'], verdicts)]
    record_response_review(source, candidate['candidate_reference'], dict(claims=claims,
        response=dict(binding_id=candidate['response_binding']['binding_id'],
            source_ids=[candidate['sources'][0]['source_id']], verdict=review_verdict,
            rationale='The synthetic source supports the complete answer only when the review says supported.')))
    return observation
