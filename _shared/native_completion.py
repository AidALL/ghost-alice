"""Core-owned native assessment service behind the Stop observer.

Callers supply native coordinates and the observed body, never policy inputs,
claim validity, selected work or evidence verdicts. This request/result boundary
does not depend on the host ordering its independently registered Stop hooks.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import sys

from completion_check_validator import looks_like_evidence_claim, validate_completion_text
from completion_route import select_completion_route
from native_transcript import native_tool_activity, stop_response_text


def _source_status_issue(reasons, *, explanation=False):
    """Describe observed source state without replacing the assistant's judgment."""
    observations = {
        'source-changed': ('Original observation bytes differ from the reviewed digest. '
            'The current-session assistant compares only the affected claims with the changed source under verification-before-completion.'),
        'source-unavailable': ('An original observation source could not be read. '
            'The current-session assistant keeps affected acceptance pending until that source or authenticated retained proof is available for the affected review.'),
    }
    observed = ' '.join(f'{reason}: {observations[reason]}' for reason in sorted(set(reasons)))
    acceptance = 'Response' if explanation else 'Completion'
    return (f'{observed} {acceptance} acceptance is blocked for the affected review. '
        'This source state does not establish that a claim is false. '
        'Preserve the original proof, source references and verification time. '
        'Retain unaffected claims and evidence; continue independent authorized work. '
        'Do not replay business work or expand unrelated review.' + (
            ' Keep any explanation review internal; do not submit completion controls for this explanation.'
            if explanation else ''))


def _ledger(source):
    """Load this Core module's sibling ledger; callers supply database and session coordinates."""
    path = Path(__file__).resolve().parents[1] / 'session-intent-analyzer/scripts/session_intent_ledger.py'
    name = '_native_completion_ledger_' + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError('Core ledger API is unavailable')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def assess_native_completion(source, *, text='', phase='before_tool', transaction=None, native_input=None):
    """Return the canonical input/body-bound assessment without mutating proof.

The result is a transient view, not a transferable authorization token. Native
consumers obtain it directly from this Core API and check their input binding.
"""
    if phase not in {'before_tool', 'final'} or not isinstance(text, str):
        raise ValueError('Invalid native assessment request')
    if phase == 'final' and native_input is not None and not text.strip():
        text = stop_response_text(native_input)
    source = dict(source)
    platform, session = source.get('GHOST_ALICE_PLATFORM'), source.get('GHOST_ALICE_SESSION_ID')
    route, context, context_status, context_issue = None, {}, 'unavailable', None
    input_observation = None
    try:
        root = source.get('GHOST_ALICE_SESSION_INTENT_ROOT')
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ValueError('Exact ledger root is missing')
        if platform not in {'codex', 'claude', 'agent-runtime'} or not isinstance(session, str) or not session:
            raise ValueError('Native owner identity is missing')
        if platform == 'codex' and source.get('CODEX_THREAD_ID') not in (None, '', session):
            raise ValueError('Native owner identity conflicts with host')
        ledger = _ledger(source)
        ledger.validate_identity(platform, session)
        # Intake identity exists independently of a selected response route.
        # Use one authoritative snapshot and never adopt compatibility JSON.
        with ledger._read_connection(Path(root), transaction) as connection:
            state = ledger._stored_state(connection, platform, session)
            event = (state.get('latest_input_event_id') if state is not None
                and ledger._canonical_input_bound(connection, Path(root), state) else None)
            if event is not None:
                input_observation = dict(input_digest=state.get('latest_input_digest'),
                    input_char_count=state.get('latest_input_char_count'))
            try:
                route = ledger.read_response_route(root=Path(root), platform=platform,
                    session_id=session, transaction=connection) if connection is not None else None
            except ValueError as exc:
                # Rejected routing must not erase independently authenticated intake.
                # Assessment waits for repair; it cannot adopt the rejected route.
                context_issue = str(exc)
        context = dict(platform=platform, session_id=session, input_event_id=event,
            response_route=route, verification_plan={'checks': []})
        context_status = 'available'
    except (ValueError, OSError, ImportError, AttributeError, TypeError, sqlite3.Error) as exc:
        context_issue = str(exc)
    marker = looks_like_evidence_claim(text)
    review_issue, review_target = None, 'response_review'
    if phase == 'final' and text.strip() and not marker:
        explanation = (isinstance(route, dict) and route.get('status') == 'ready'
            and route.get('response_purpose') == 'explanation')
        if explanation:
            # A registered explanation belongs to its exact current review. Neither
            # a historical phrase nor the explanation label authenticates its claims.
            from completion_assessment import CompletionAssessmentError, lookup_current_response
            try:
                lookup_current_response(source, text, transaction=transaction)
            except (CompletionAssessmentError, ValueError, OSError, sqlite3.Error) as exc:
                reason = getattr(exc, 'reason', 'response-assessment-required')
                if reason in {'source-changed', 'source-unavailable'}:
                    review_issue = _source_status_issue([reason], explanation=True)
                    review_target = 'source_status'
                else:
                    review_issue = (f'{reason}: '
                        'The current-session assistant follows verification-before-completion, section Prepare and record the response review, for this exact explanation and its original sources. '
                        'Keep the review internal; do not submit completion controls for this explanation.')
    selected = select_completion_route(context, dict(text=text, phase=phase),
        {'failure_kind': 'none', 'criteria': []})
    issue = None
    repair_target = 'completion_metadata'
    native_activity = (native_tool_activity(platform, session, native_input, input_observation=input_observation)
        if phase == 'final' and native_input is not None else None)
    if phase == 'final':
        if selected['route_binding'] != 'current' and native_activity is not None:
            if native_activity['status'] == 'observed':
                issue = ('route-unavailable: This input contains native tool calls but has no current Core response-route. '
                    'The current-session assistant follows task-router to register this input\'s work and response purpose. '
                    'Retain the tool results; route registration does not require repeating the work.')
            elif native_activity['status'] == 'unavailable':
                issue = ('native-activity-unavailable: Native current-turn activity could not be established '
                    f"({native_activity['reason']}); this is not evidence of a tool-free input. "
                    'Register the current Core response-route through task-router or repair the native transcript binding. '
                    'Retain existing results; do not repeat the work.')
            if issue is not None:
                repair_target = 'response_route'
        if issue is None and review_issue:
            issue = review_issue
            repair_target = review_target
        # Review needs the current owned contract before it can interpret proof
        # fields. Report that prerequisite once, not after formatting retries.
        if issue is None and (marker or selected['required_completion_check']) and selected['route_binding'] != 'current':
            issue = ('route-unavailable: The current-session assistant must use task-router to register the current '
                'response-route through Core before submitting evidence. Retain existing proof; '
                'a missing route does not invalidate the business result.')
        if issue is None:
            issue = validate_completion_text(text, require_completion_check=selected['required_completion_check'])
        if issue is None and marker:
            # The current-session assistant judged meaning against original sources under Core verification instructions.
            # This function authenticates that exact review for the Stop observer.
            from completion_assessment import CompletionAssessmentError, lookup_current_response
            try:
                reviewed = lookup_current_response(source, text, transaction=transaction)
                if reviewed.source_issues:
                    issue = _source_status_issue(reviewed.source_issues.values())
                    repair_target = 'source_status'
                elif any(j['verdict'] != 'supported' for j in reviewed.judgments):
                    issue = 'assessment-unsupported: The Core review does not support every asserted claim.'
            except (CompletionAssessmentError, ValueError, OSError, sqlite3.Error) as exc:
                reason = getattr(exc, 'reason', 'assessment-unavailable')
                if reason in {'source-changed', 'source-unavailable'}:
                    issue = _source_status_issue([reason])
                    repair_target = 'source_status'
                else:
                    issue = f'{reason}: {exc}'
    return dict(schema_version='native-completion.v1', platform=platform, session_id=session,
        input_event_id=context.get('input_event_id'), body_digest='sha256:' + hashlib.sha256(text.encode()).hexdigest(),
        phase=phase, context_status=context_status, context_issue=context_issue,
        native_activity=native_activity,
        response_route=route, completion_route=selected, body_issue=issue,
        repair_target=repair_target,
        validation_text=text, has_evidence_marker=marker)
