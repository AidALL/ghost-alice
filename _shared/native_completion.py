"""Core-owned native assessment service behind the Stop script.

Callers supply native coordinates and the observed body, never policy inputs,
claim validity, selected work or evidence verdicts. This request/result boundary
does not depend on the host ordering its independently registered Stop hooks.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

from completion_check_validator import extract_original_proof, looks_like_evidence_claim, validate_completion_text
from completion_route import select_completion_route
import ledger_access
from native_transcript import native_tool_activity


def _source_status_issue(reasons, *, explanation=False):
    """Describe observed source state without replacing the response model's judgment."""
    observations = {
        'source-changed': ('Original observation bytes differ from the reviewed digest. '
            'The response model compares only the affected claims with the changed source under verification-before-completion.'),
        'source-unavailable': ('An original observation source could not be read. '
            'The response model keeps affected acceptance pending until that source or authenticated retained proof is available for the affected review.'),
    }
    observed = ' '.join(f'{reason}: {observations[reason]}' for reason in sorted(set(reasons)))
    acceptance = 'response' if explanation else 'completion claim'
    return (f"{observed} Core code's Stop script does not pass the {acceptance} for the affected review. "
        'This source state does not establish that a claim is false. '
        'Preserve the original proof, source references and verification time. '
        "Retain unaffected claims and evidence; continue independent work that the human user's instructions authorize. "
        'Do not replay business work or expand unrelated review.' + (
            ' Keep any explanation review internal; do not submit completion controls for this explanation.'
            if explanation else ''))


def _claim_judgments(reviewed):
    """Each recorded claim binding with its verdict, so the Stop notice names the claims the recorded review does not support."""
    verdicts = {j['binding_id']: j['verdict'] for j in reviewed.judgments}
    return [dict(claim_index=b['claim_index'], criterion_id=b['criterion']['id'], verdict=verdicts.get(b['binding_id']))
            for b in reviewed.candidate['bindings']]


def _recorded_review_issue(source, root, owner, connection, text, selection, *, completion):
    from completion_assessment import CompletionAssessmentError, ResponseReviewNotRecorded, _lookup_registered_review
    try:
        _lookup_registered_review(source, root, owner, connection, text, selection, completion=completion)
    except ResponseReviewNotRecorded:
        pass  # A completion with no matching review still requires a current route.
    except CompletionAssessmentError as exc:
        if exc.reason in {'source-changed', 'source-unavailable'}:
            return _source_status_issue([exc.reason], explanation=not completion), 'source_status'
        if not completion and exc.reason in {'response-unbound', 'response-unsupported', 'criterion-unbound'}:
            return (f'{exc.reason}: The response model follows verification-before-completion, section Prepare and record the response review, for this exact explanation and its original sources. '
                    'Keep the review internal; do not submit completion controls for this explanation.'), 'response_review'
        return f'{exc.reason}: {exc}', 'response_review'
    return None, 'response_review'


def _completion_review(source, root, owner, connection, text, selection):
    issue, repair_target, claim_judgments = None, 'completion_metadata', None
    from completion_assessment import CompletionAssessmentError, _lookup_completion_response, _proof_for_selection
    try:
        reviewed = _lookup_completion_response(source, root, owner, connection, text, selection.state, selection.route)
        claim_judgments = _claim_judgments(reviewed)
        if reviewed.source_issues:
            issue = _source_status_issue(reviewed.source_issues.values())
            repair_target = 'source_status'
        elif any(j['verdict'] != 'supported' for j in reviewed.judgments):
            issue = 'assessment-unsupported: The recorded claim review does not support every asserted claim.'
    except (CompletionAssessmentError, ValueError, OSError, sqlite3.Error) as exc:
        reason = getattr(exc, 'reason', 'assessment-unavailable')
        if reason in {'source-changed', 'source-unavailable'}:
            issue = _source_status_issue([reason])
            repair_target = 'source_status'
        else:
            issue = f'{reason}: {exc}'
        if reason == 'response-unsupported':
            # The claim review still exists when the whole-answer judgment is unsupported.
            try:
                claim_judgments = _claim_judgments(_proof_for_selection(source, owner, connection,
                    extract_original_proof(text), selection.state, selection.route))
            except (CompletionAssessmentError, ValueError, OSError, sqlite3.Error):
                claim_judgments = None
    return issue, repair_target, claim_judgments


def _assess_answer(source, root, owner, connection, selection, binding, text, marker, encoding_issue):
    if encoding_issue:
        return encoding_issue, 'response_review', None
    event = selection.state.get('canonical_input_event_id') if selection else None
    route = selection.route if selection else None
    same_input = route and event and route['input_event_id'] == event
    if event and text.strip() and (not marker or (same_input and binding == 'stale')):
        issue, target = _recorded_review_issue(source, root, owner, connection, text, selection, completion=marker)
        if issue:
            return issue, target, None
    if marker and binding != 'current':
        if same_input and binding == 'stale':
            return ('route-stale: The registered selection cannot be used for this answer. Register the current selection.',
                    'response_route', None)
        return ('route-unavailable: The response model must use task-router to register the current '
                'response-route with Core code before submitting evidence. Retain existing proof; '
                'a missing route does not invalidate the business result.', 'response_route', None)
    issue = validate_completion_text(text)
    if issue or not marker:
        return issue, 'completion_metadata', None
    return _completion_review(source, root, owner, connection, text, selection)


def assess_native_completion(source, *, text='', transaction=None, hook_payload=None):
    """Return the canonical input/body-bound assessment without mutating proof.

The result is a transient view, not a transferable authorization token. Native
consumers obtain it directly from this Core API and check their input binding.
"""
    if not isinstance(text, str):
        raise ValueError('Invalid native assessment request')
    try:
        body_digest = 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()
        encoding_issue = None
    except UnicodeEncodeError:
        body_digest = None
        encoding_issue = (
            'response-unbound: Core could not encode the observed answer as UTF-8. '
            'No digest or recorded review was accepted for this answer.'
        )
    source = dict(source)
    platform, session = source.get('GHOST_ALICE_PLATFORM'), source.get('GHOST_ALICE_SESSION_ID')
    selection, event, route = None, None, None
    context_status, context_issue = 'unavailable', None
    input_observation = None
    marker = looks_like_evidence_claim(text)
    selected = select_completion_route({})
    try:
        ledger, root, owner = ledger_access.coordinates(source)
        with ledger._read_connection(root, transaction) as connection:
            if connection is not None:
                try:
                    selection = ledger.read_response_selection(root=root, **owner, transaction=connection)
                    state = selection.state
                    event = state.get('canonical_input_event_id') or None
                    route = selection.route
                except ValueError as exc:
                    # A damaged route does not erase its independently recorded input.
                    context_issue = str(exc)
                    state = ledger._session_header(connection, platform, session, fields=ledger.INTAKE_CONTEXT_FIELDS)
                    if state is not None and ledger._canonical_input_bound(connection, Path(root), state):
                        event = state['latest_input_event_id']
            if event is not None:
                input_observation = dict(input_digest=state.get('latest_input_digest'),
                    input_char_count=state.get('latest_input_char_count'))
            selected = select_completion_route(dict(**owner, input_event_id=event, response_route=route))
            context_status = 'available'
            issue, repair_target, claim_judgments = _assess_answer(source, root, owner, connection, selection,
                selected['route_binding'], text, marker, encoding_issue)
    except (ValueError, OSError, ImportError, AttributeError, TypeError, sqlite3.Error) as exc:
        context_issue = str(exc)
        if context_status == 'available':
            issue, repair_target, claim_judgments = f'assessment-unavailable: {exc}', 'response_review', None
        else:
            issue, repair_target, claim_judgments = _assess_answer(source, None, None, None, None,
                'missing', text, marker, encoding_issue)
    native_activity = (native_tool_activity(platform, session, hook_payload, input_observation=input_observation)
        if hook_payload is not None else None)
    return dict(schema_version='native-completion.v1', platform=platform, session_id=session,
        input_event_id=event, body_digest=body_digest,
        context_status=context_status, context_issue=context_issue,
        native_activity=native_activity,
        response_route=route, completion_route=selected, body_issue=issue,
        repair_target=repair_target, claim_judgments=claim_judgments,
        validation_text=text, has_evidence_marker=marker)
