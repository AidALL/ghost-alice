#!/usr/bin/env python3
"""Core's Stop notice: one message per Stop for the user and for add-on Stop hooks.

Core code's Stop script builds the message, records it at the path the hook runner
sets in GHOST_ALICE_STOP_NOTICE_FILE and returns its text as systemMessage when the
message's state is notice. Before an add-on Stop hook runs, the runner builds this
Stop's message and records it unless the file already holds it, so that hook reads
the message Core code's Stop script returns, whichever of the two runs first, when
the Stop event JSON carries the answer text.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import atomic_write
from completion_check_validator import (extract_claim_evidence_entries, extract_control_block,
                                        extract_partial_unverified_entries, find_control_block_positions)
from native_completion import assess_native_completion
from native_transcript import stop_response_text
import strict_session_log

SCHEMA_VERSION = 'stop-notice.v1'
FILE_NAME = 'stop-notice.json'
ENV_FILE = 'GHOST_ALICE_STOP_NOTICE_FILE'
NATIVE_SESSION_KEYS = ('session_id', 'sessionId', 'thread_id', 'threadId')
CLAIM_LIMIT = 160
CODE = re.compile(r'([a-z]+(?:-[a-z]+)+):')
# The validator's diagnostic is written for the response model, so the text names only the problem;
# the message keeps the diagnostic in issues[].detail.
ISSUE_LABELS = {
    'completion-format': 'the completion block is missing or malformed',
    'assessment-required': 'no recorded review covers the claims in the completion block',
    'assessment-invalid': 'the recorded review is not valid for this answer',
    'assessment-unavailable': 'Core code could not look up a recorded review for this answer',
    'authority-unavailable': 'the ledger location for this session is not set',
    'input-unavailable': 'Core code could not identify the input this answer responds to',
    'proof-invalid': 'the evidence in the completion block could not be read as review proof',
    'route-unavailable': 'no current response route is registered for this answer',
    'response-assessment-required': 'no recorded review matches this answer',
    'response-unbound': 'the recorded review is not bound to this answer',
    'criterion-unbound': 'a reviewed claim is not bound to a criterion the current route selects',
    'response-unsupported': 'the recorded review does not support this answer',
    'source-changed': 'an original source changed after the review',
    'source-unavailable': 'an original source could not be read',
}
# An explanation whose input has a recorded review of another answer text reports route-unavailable or
# response-assessment-required with this phrase in its detail.
OTHER_TEXT_REVIEW = 'binds this input but not this exact answer'
# Why a declared row is on hold, for each feedback issue the route records against the row's original source.
HELD_ROW_LABELS = {
    'source-unverified': 'its original source could not be verified',
    'source-owner-unbound': 'its original source is not bound to this session',
    'source-changed': 'its original source changed',
    'source-unavailable': 'its original source could not be read',
}


def native_session(native_input: Mapping[str, Any]) -> str:
    """The session the host named; an unnamed session stays empty, never guessed."""
    return next((native_input[key] for key in NATIVE_SESSION_KEYS
                 if isinstance(native_input.get(key), str) and native_input[key].strip()), '')


def notice_path(env: Mapping[str, str], native_input: Mapping[str, Any], platform: str) -> Path:
    """The notice file in the session folder, next to the hook log."""
    home = next((Path(env[name]) for name in ('HOME', 'USERPROFILE') if str(env.get(name) or '').strip()), None)
    session = strict_session_log.session_id_from_payload(dict(native_input), dict(env), platform=platform)
    return strict_session_log.log_path(home, platform, session).parent / FILE_NAME


def build_stop_notice(platform: str, native_input: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    """Build this Stop's message from the recorded review and the declared remaining work."""
    text = stop_response_text(dict(native_input)) if native_input else ''
    session = native_session(native_input)
    message = dict(schema_version=SCHEMA_VERSION, platform=platform, session_id=session, input_event_id=None,
                   body_digest='sha256:' + hashlib.sha256(text.encode()).hexdigest(), state='quiet', control=None,
                   claims=None, unsupported_claims=[], issues=[], unresolved_criteria=[],
                   remaining_work={'declared': False}, text='')
    source = dict(env, GHOST_ALICE_PLATFORM=platform, GHOST_ALICE_SESSION_ID=session)
    # An empty answer has no claims or block to compare; the declaration in the current route still stands.
    assessment = assess_native_completion(source, text=text, phase='final', native_input=dict(native_input))
    control = _control(text)
    block = extract_control_block(text, control) if control else ''
    message.update(input_event_id=assessment['input_event_id'], control=control,
                   remaining_work=_remaining_work(assessment))
    judgments = assessment.get('claim_judgments')
    if judgments is not None:
        # One claim can name several criteria and so carry several recorded verdicts; count claims, not verdicts.
        entries = extract_claim_evidence_entries(block) if block else []
        claims: dict[int, list[str]] = {}
        for judgment in judgments:
            failed = claims.setdefault(judgment['claim_index'], [])
            if judgment['verdict'] != 'supported':
                failed.append(judgment['criterion_id'])
        message['claims'] = dict(reviewed=len(claims), supported=sum(not failed for failed in claims.values()))
        message['unsupported_claims'] = [dict(criteria=failed, claim=_claim(entries, index))
                                         for index, failed in sorted(claims.items()) if failed]
    if assessment['body_issue']:
        code = CODE.match(assessment['body_issue'])
        message['issues'] = [dict(code=code.group(1) if code else 'completion-format',
                                  repair_target=assessment['repair_target'], detail=assessment['body_issue'])]
    if control == 'partial-check':
        message['unresolved_criteria'] = [dict(criterion=criterion, reason=reason)
                                          for criterion, reason in extract_partial_unverified_entries(block)]
    if (message['unsupported_claims'] or message['issues'] or message['unresolved_criteria']
            or message['remaining_work'].get('items')):
        message.update(state='notice', text=render(message))
    return message


def ensure_stop_notice(platform: str, native_input: Mapping[str, Any], env: Mapping[str, str],
                       path: str | Path | None = None) -> dict[str, Any]:
    """Return this Stop's message; with a path, keep the recorded message when it equals this one, otherwise record this one there."""
    message = build_stop_notice(platform, native_input, env)
    if path is None:
        return message
    path = Path(path)
    try:
        recorded = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        recorded = None
    if recorded == message:
        # The other Stop hook already recorded this same message; keep the file as it is.
        return recorded
    try:
        atomic_write.write_bytes(path, json.dumps(message, ensure_ascii=False, indent=2).encode('utf-8'), env=env)
    except (OSError, ValueError) as error:
        return dict(message, file_error=f'{type(error).__name__}: {error}')
    return message


def render(message: Mapping[str, Any]) -> str:
    """Write the notice text: English labels, and the claims and reasons as the response model wrote them."""
    unsupported, unresolved = message['unsupported_claims'], message['unresolved_criteria']
    issues = [issue for issue in message['issues'] if issue['code'] != 'assessment-unsupported']
    work = message['remaining_work']
    items, stopped = work.get('items') or [], work.get('control') == 'human-stop'
    summary = []
    if unsupported:
        reviewed = (message['claims'] or {}).get('reviewed', len(unsupported))
        summary.append(f'the recorded review does not support {len(unsupported)} of {reviewed} claims')
    if issues:
        summary.append(_count(len(issues), 'issue') + ' found while checking this answer')
    if unresolved:
        summary.append('partial completion with ' + _count(len(unresolved), 'unresolved criterion', 'unresolved criteria'))
    if items:
        summary.append(_count(len(items), 'declared remaining work item') + (', stopped by the user' if stopped else ''))
    lines = ['Ghost-ALICE Stop notice: ' + '; '.join(summary) + '.']
    lines += [f"- Not supported ({_criteria(claim['criteria'])}): {claim['claim']}" for claim in unsupported]
    lines += [f'- Issue: {_issue_label(issue)}' for issue in issues]
    lines += [f"- Unresolved ({_criteria([entry['criterion']])}): {entry['reason']}" for entry in unresolved]
    label = 'Declared remaining work, stopped by the user' if stopped else 'Declared remaining work'
    for item in items:
        # Continuation lines keep a row's reason, its next needed information and its state apart.
        lines += [f"- {label} ({_criteria(item['criteria'])}): {item['reason']}",
                  f"  Next needed information: {item['next_information']}"]
        if not stopped and item['prerequisite'] == 'missing':
            lines.append('  Prerequisite: not available yet')
        if item.get('source_issue'):
            lines.append('  On hold: ' + HELD_ROW_LABELS.get(item['source_issue'], item['source_issue']))
    if not items:
        lines.append('- Remaining work: ' + ('none, as the response model declared' if work.get('declared')
                                             else 'unknown; the response model made no declaration for this input'))
    return '\n'.join(lines)


def _control(text: str) -> str | None:
    found = [(offset, name) for name in ('completion-check', 'partial-check')
             for offset in find_control_block_positions(text, name)]
    return min(found)[1] if found else None


def _claim(entries: list[dict[str, Any]], index: int) -> str:
    claim = str(entries[index].get('claim', '')) if 0 <= index < len(entries) else ''
    return claim if len(claim) <= CLAIM_LIMIT else claim[:CLAIM_LIMIT].rstrip() + '…'


def _remaining_work(assessment: Mapping[str, Any]) -> dict[str, Any]:
    """Only the rows the response model declared in the current route; an unmet criterion alone is not one."""
    route = assessment.get('response_route')
    if (not route or route.get('status') != 'ready'
            or (assessment.get('completion_route') or {}).get('route_binding') != 'current'):
        return {'declared': False}
    feedback = route.get('continuation_feedback')
    if not feedback:
        return {'declared': False}
    issues = {entry['pointer']: entry['issue'] for entry in route.get('continuation_feedback_issues', [])}
    items = []
    for index, row in enumerate(feedback['required_unprocessed']):
        item = dict(criteria=[ref['criterion_id'] for ref in row['criterion_refs']], reason=row['reason'],
                    next_information=row['next_information'], prerequisite=row['prerequisite'])
        if issue := issues.get(f'/continuation_feedback/required_unprocessed/{index}'):
            item['source_issue'] = issue
        items.append(item)
    return dict(declared=True, control=feedback['control'], items=items)


def _issue_label(issue: Mapping[str, Any]) -> str:
    if OTHER_TEXT_REVIEW in str(issue['detail']):
        return 'a recorded review covers this input but not this exact answer'
    if issue['code'] in ISSUE_LABELS:
        return ISSUE_LABELS[issue['code']]
    return f"Core code could not check this answer ({issue['code']})"


def _criteria(ids: list[str]) -> str:
    return ('criterion ' if len(ids) == 1 else 'criteria ') + ', '.join(ids)


def _count(number: int, singular: str, plural: str | None = None) -> str:
    return f'{number} {singular if number == 1 else (plural or singular + "s")}'
