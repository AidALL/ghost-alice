"""Core-owned native assessment service behind the Stop script.

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

from completion_check_validator import extract_original_proof, looks_like_evidence_claim, validate_completion_text
from completion_route import select_completion_route
from native_transcript import native_tool_activity, stop_response_text


def _source_status_issue(reasons, *, explanation=False):
    """Describe observed source state without replacing the response model's judgment."""
    observations = {
        'source-changed': ('Original observation bytes differ from the reviewed digest. '
            'The response model compares only the affected claims with the changed source under verification-before-completion.'),
        'source-unavailable': ('An original observation source could not be read. '
            'The response model keeps affected acceptance pending until that source or authenticated retained proof is available for the affected review.'),
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


def _claim_judgments(reviewed):
    """Each recorded claim binding with its verdict, so the Stop notice names the claims the recorded review does not support."""
    verdicts = {j['binding_id']: j['verdict'] for j in reviewed.judgments}
    return [dict(claim_index=b['claim_index'], criterion_id=b['criterion']['id'], verdict=verdicts.get(b['binding_id']))
            for b in reviewed.candidate['bindings']]


def _ledger(source):
    """Load this Core module's sibling ledger; callers supply database and session coordinates."""
    path = Path(__file__).resolve().parents[1] / 'session-intent-analyzer/scripts/session_intent_ledger.py'
    name = '_native_completion_ledger_' + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError('The session-intent ledger API is unavailable')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def assess_native_completion(source, *, text='', transaction=None, native_input=None):
    """Return the canonical input/body-bound assessment without mutating proof.

The result is a transient view, not a transferable authorization token. Native
consumers obtain it directly from this Core API and check their input binding.
"""
    if not isinstance(text, str):
        raise ValueError('Invalid native assessment request')
    if native_input is not None and not text.strip():
        text = stop_response_text(native_input)
    source = dict(source)
    platform, session = source.get('GHOST_ALICE_PLATFORM'), source.get('GHOST_ALICE_SESSION_ID')
    route, context, context_status, context_issue = None, {}, 'unavailable', None
    input_observation = None
    try:
        root = source.get('GHOST_ALICE_SESSION_INTENT_ROOT')
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ValueError('Exact ledger root is missing')
        if platform not in {'codex', 'claude'} or not isinstance(session, str) or not session:
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
            response_route=route)
        context_status = 'available'
    except (ValueError, OSError, ImportError, AttributeError, TypeError, sqlite3.Error) as exc:
        context_issue = str(exc)
    marker = looks_like_evidence_claim(text)
    review_issue, review_target = None, 'response_review'
    if text.strip() and not marker and context_status == 'available':
        # An answer without a control block is judged only when a recorded review
        # binds this exact text; an unrecorded explanation is an ordinary answer.
        from completion_assessment import CompletionAssessmentError, lookup_current_response
        try:
            lookup_current_response(source, text, transaction=transaction)
        except (CompletionAssessmentError, ValueError, OSError, sqlite3.Error) as exc:
            reason = getattr(exc, 'reason', None)
            if reason in {'source-changed', 'source-unavailable'}:
                review_issue = _source_status_issue([reason], explanation=True)
                review_target = 'source_status'
            elif reason in {'response-unbound', 'response-unsupported', 'criterion-unbound'}:
                review_issue = (f'{reason}: '
                    'The response model follows verification-before-completion, section Prepare and record the response review, for this exact explanation and its original sources. '
                    'Keep the review internal; do not submit completion controls for this explanation.')
            elif reason in {'response-assessment-required', 'route-unavailable'}:
                # A review recorded for this input must cover the delivered text;
                # an input with no recorded review is an ordinary answer.
                from completion_assessment import reviewed_for_input
                if event is not None and reviewed_for_input(source, event, transaction=transaction):
                    review_issue = (f'{reason}: A recorded review binds this input but not this exact answer. '
                        'The response model follows verification-before-completion, section Prepare and record the response review, for the delivered text.')
            elif reason is None and context_issue is None:
                context_issue = str(exc)
    selected = select_completion_route(context)
    issue, claim_judgments = None, None
    repair_target = 'completion_metadata'
    native_activity = (native_tool_activity(platform, session, native_input, input_observation=input_observation)
        if native_input is not None else None)
    if review_issue:
        issue = review_issue
        repair_target = review_target
    # A completion or partial claim is judged against the current owned contract.
    if issue is None and marker and selected['route_binding'] != 'current':
        issue = ('route-unavailable: The response model must use task-router to register the current '
            'response-route with Core code before submitting evidence. Retain existing proof; '
            'a missing route does not invalidate the business result.')
        repair_target = 'response_route'
    if issue is None:
        issue = validate_completion_text(text)
    if issue is None and marker:
        # lookup_current_response authenticates the recorded review of this exact response for the Stop script.
        from completion_assessment import CompletionAssessmentError, lookup_current_proof, lookup_current_response
        try:
            reviewed = lookup_current_response(source, text, transaction=transaction)
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
                    claim_judgments = _claim_judgments(lookup_current_proof(
                        source, extract_original_proof(text), transaction=transaction))
                except (CompletionAssessmentError, ValueError, OSError, sqlite3.Error):
                    claim_judgments = None
    return dict(schema_version='native-completion.v1', platform=platform, session_id=session,
        input_event_id=context.get('input_event_id'), body_digest='sha256:' + hashlib.sha256(text.encode()).hexdigest(),
        context_status=context_status, context_issue=context_issue,
        native_activity=native_activity,
        response_route=route, completion_route=selected, body_issue=issue,
        repair_target=repair_target, claim_judgments=claim_judgments,
        validation_text=text, has_evidence_marker=marker)
