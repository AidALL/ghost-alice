"""Core-owned native assessment service, shared by guard and Ghost-ALICE Autopilot consumers.

Callers supply native coordinates and the observed body, never policy inputs,
claim validity, selected work or evidence verdicts. This request/result boundary
does not depend on the host ordering its independently registered Stop hooks.
The current-session assistant follows Ghost-ALICE Autopilot instructions to judge further work.
Ghost-ALICE Autopilot adapter code selects recorded approved tasks from these validated results.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

from claim_evidence_runtime import evaluate_runtime_claim_report
from completion_check_validator import looks_like_evidence_claim, validate_completion_text
from completion_route import select_completion_route
from native_transcript import native_tool_activity


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
            ' Keep any explanation review internal; do not submit completion controls or Ghost-ALICE Autopilot publication for this explanation.'
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
Publication and application retain immutable provenance/integrity checks.
"""
    if phase not in {'before_tool', 'publication', 'final'} or not isinstance(text, str):
        raise ValueError('Invalid native assessment request')
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
                # Preparation waits for repair; it cannot adopt the rejected route.
                context_issue = str(exc)
        context = dict(platform=platform, session_id=session, input_event_id=event,
            response_route=route, verification_plan={'checks': []})
        context_status = 'available'
    except (ValueError, OSError, ImportError, AttributeError, TypeError, sqlite3.Error) as exc:
        context_issue = str(exc)
    report = dict(present=False, valid=False, residual_text=text, claims=[], issue=None)
    reviewed_report = False
    report_review_issue = None
    report_review_target = 'response_review'
    if phase == 'final' and text.strip():
        explanation = (isinstance(route, dict) and route.get('status') == 'ready'
            and route.get('response_purpose') == 'explanation')
        # A registered explanation belongs to its exact current review. Automatic
        # historical discovery cannot replace or preempt that review.
        report = evaluate_runtime_claim_report(text, platform, {'session_id': session}, source,
            automatic=not explanation)
        if ((not report['present'] or report['valid']) and not looks_like_evidence_claim(text)
                and explanation):
            # The internal review channel is independent of printed proof.
            # Exact current-input/body review is still mandatory: neither a
            # historical phrase nor an explanation label authenticates a claim.
            from completion_assessment import CompletionAssessmentError, lookup_current_response
            try:
                lookup_current_response(source, text, transaction=transaction)
                report = dict(present=True, valid=True, residual_text='', claims=[], issue=None)
                reviewed_report = True
            except (CompletionAssessmentError, ValueError, OSError, sqlite3.Error) as exc:
                reason = getattr(exc, 'reason', 'response-assessment-required')
                if reason in {'source-changed', 'source-unavailable'}:
                    report_review_issue = _source_status_issue([reason], explanation=True)
                    report_review_target = 'source_status'
                else:
                    report_review_issue = (f'{reason}: '
                        'The current-session assistant follows verification-before-completion, references/autopilot-publication.md, section Prepare and record the response review, for this exact explanation and its original sources. '
                        'Keep the review internal; do not submit completion controls or Ghost-ALICE Autopilot publication for this explanation.')
    selected = select_completion_route(context,
        dict(text=text, phase=phase, claim_report=report),
        {'failure_kind': 'none', 'criteria': []}, {'enabled': True, 'publication_status': 'unprepared'})
    validation_text = report['residual_text'] if report['valid'] else text
    marker = looks_like_evidence_claim(validation_text)
    issue = None
    publication_criteria = [] if reviewed_report else None
    repair_target = 'completion_metadata'
    native_activity = (native_tool_activity(platform, session, native_input, input_observation=input_observation)
        if phase == 'final' and native_input is not None else None)
    if phase == 'final':
        issue = selected['claim_report_issue']
        if issue is None and selected['route_binding'] != 'current' and native_activity is not None:
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
        if issue is None and report_review_issue:
            issue = report_review_issue
            repair_target = report_review_target
        # Review needs the current owned contract before it can interpret proof
        # fields. Report that prerequisite once, not after formatting retries.
        if issue is None and (marker or selected['required_completion_check']) and selected['route_binding'] != 'current':
            issue = ('route-unavailable: The current-session assistant must use task-router to register the current '
                'response-route through Core before submitting evidence. Retain existing proof; '
                'a missing route does not invalidate the business result.')
        if issue is None:
            issue = validate_completion_text(validation_text,
                require_completion_check=selected['required_completion_check'])
        if issue is None and selected['unbound_report_text'] and not marker:
            issue = ('claim-evidence-unbound: Surrounding prose is not covered by the original claims. '
                'The current-session assistant follows verification-before-completion, references/autopilot-publication.md, section Prepare and record the response review, for the complete response and retained sources; no business replay.')
        if issue is None and marker:
            # The current-session assistant judged meaning against original sources under Core verification instructions.
            # This function authenticates that exact review for the guard and Ghost-ALICE Autopilot adapter.
            from completion_assessment import (CompletionAssessmentError, lookup_current_response,
                                               assessment_coverage)
            try:
                reviewed = lookup_current_response(source, text, transaction=transaction)
                if reviewed.source_issues:
                    issue = _source_status_issue(reviewed.source_issues.values())
                    repair_target = 'source_status'
                elif any(j['verdict'] != 'supported' for j in reviewed.judgments):
                    issue = 'assessment-unsupported: The Core review does not support every asserted claim.'
                else:
                    # A supported report can leave every work criterion unmet.
                    # Ghost-ALICE Autopilot receives fulfilled criterion keys computed from the authenticated
                    # assistant review, rather than inferring them from selection.
                    candidate = reviewed.candidate
                    coverage = assessment_coverage(reviewed, candidate['contract'],
                        candidate['proof'], candidate['verified_at'])
                    publication_criteria = [dict(work_unit_id=unit, contract_revision=revision,
                        criterion_id=criterion) for unit, revision, criterion in sorted(coverage)]
            except (CompletionAssessmentError, ValueError, OSError, sqlite3.Error) as exc:
                reason = getattr(exc, 'reason', 'assessment-unavailable')
                if reason in {'source-changed', 'source-unavailable'}:
                    issue = _source_status_issue([reason])
                    repair_target = 'source_status'
                else:
                    issue = f'{reason}: {exc}'
    selected = dict(selected, publication_criteria=publication_criteria)
    if publication_criteria == [] and issue is None:
        # Keep selected work for Ghost-ALICE Autopilot's separately approved plan, without
        # manufacturing completion or demanding an empty publication.
        selected.update(publication_route='none', reason=(
            'The recorded assistant review supports the response but its proof fulfills no criterion. '
            'Preserve unresolved work; this response requires no publication.'))
    return dict(schema_version='native-completion.v1', platform=platform, session_id=session,
        input_event_id=context.get('input_event_id'), body_digest='sha256:' + hashlib.sha256(text.encode()).hexdigest(),
        phase=phase, context_status=context_status, context_issue=context_issue,
        native_activity=native_activity,
        response_route=route, completion_route=selected, body_issue=issue,
        repair_target=repair_target,
        validation_text=validation_text, has_evidence_marker=marker)
