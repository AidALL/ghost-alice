"""Core authentication of the current-session assistant's evidence review.

The current-session assistant reads the candidate and judges meaning. This
module checks provenance and exact bindings, not the truth of model reasoning.
Ghost-ALICE Autopilot adapter code consumes authenticated results; IDs and metadata cannot replace
the assistant's review. This module does not rerun business observations.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from completion_check_validator import (extract_original_proof, parse_original_completion_evidence,
                                        looks_like_evidence_claim, review_comparison_text)
from native_completion import _ledger

_SEAL = object()
_DEFINITION = ('work_unit_id', 'contract_revision', 'id', 'summary', 'source', 'admitted')
_SOURCE_RESPONSE_KIND = 'source-response-review.v2'


class CompletionAssessmentError(ValueError):
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason


def _require(condition, reason, detail):
    if not condition:
        raise CompletionAssessmentError(reason, detail)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _digest(value):
    return 'sha256:' + hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def _reference(value):
    return _digest(_json(value))


class VerifiedAssessment:
    """Read-only in-process result minted only after a Core database lookup."""
    __slots__ = ('_json', '_seal', '_issues')

    def __init__(self, payload, seal, source_issues=None):
        _require(seal is _SEAL, 'assessment-invalid', 'A Core database assessment is required')
        self._json, self._seal = _json(payload), seal
        self._issues = tuple((source_issues or {}).items())

    @property
    def candidate(self):
        return json.loads(self._json)['candidate']

    @property
    def judgments(self):
        return json.loads(self._json)['judgments']

    @property
    def reference(self):
        return _digest(self._json)

    @property
    def source_issues(self):
        return dict(self._issues)


def _coordinates(source):
    root = source.get('GHOST_ALICE_SESSION_INTENT_ROOT')
    owner = {k: source.get('GHOST_ALICE_' + k.upper()) for k in ('platform', 'session_id')}
    _require(isinstance(root, str) and Path(root).is_absolute(), 'authority-unavailable', 'Exact ledger root is required')
    _require(owner['platform'] in {'codex', 'claude', 'agent-runtime'}
        and isinstance(owner['session_id'], str) and bool(owner['session_id']),
        'owner-mismatch', 'Exact native owner is required')
    _require(owner['platform'] != 'codex' or source.get('CODEX_THREAD_ID') in (None, '', owner['session_id']),
             'owner-mismatch', 'Native owner conflicts with the host')
    core = _ledger(source)
    core.validate_identity(**owner)
    return core, Path(root).resolve(), owner


def _schema(connection):
    for table in ('core_completion_candidates', 'core_completion_assessments', 'core_completion_responses'):
        connection.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
            reference TEXT PRIMARY KEY, lookup_key TEXT NOT NULL, payload_json TEXT NOT NULL)''')
        for operation in ('UPDATE', 'DELETE'):
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_{operation.lower()}
                BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable Core assessment'); END''')


def _stored(connection, table, reference):
    _require(connection is not None and connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(),
        'assessment-required', 'The current-session assistant must review the original proof and record its judgments through completion_assessment.py')
    row = connection.execute(f'SELECT payload_json FROM {table} WHERE reference=?', (reference,)).fetchone()
    _require(row is not None, 'assessment-required', 'Referenced Core review is unavailable')
    value = json.loads(row[0])
    _require(_reference(value) == reference, 'assessment-invalid', 'Stored review bytes do not match their reference')
    return value


def _current_response_context(core, root, owner, connection):
    state = core.read_session_state(root=root, **owner, transaction=connection)
    route = core.read_response_route(root=root, **owner, transaction=connection)
    _require(not state.get('pending_audit_event') and isinstance(route, dict) and route.get('status') == 'ready'
        and route.get('input_event_id') == state.get('latest_input_event_id')
        and core._canonical_input_bound(connection, root, state), 'route-unavailable', 'Current owned route is required')
    return state, route


def _current_contract(core, root, owner, connection, work_unit_id=None):
    state, route = _current_response_context(core, root, owner, connection)
    units = [u for u in route['work_units'] if u['work_unit_id'] in route['selected_work_unit_ids']
             and (work_unit_id is None or u['work_unit_id'] == work_unit_id)]
    _require(len(units) == 1 and units[0]['owner'] == owner, 'criterion-unbound', 'Select exactly one owned work unit')
    unit = units[0]
    selected_ids = {r['criterion_id'] for r in route['selected_criteria'] if r['work_unit_id'] == unit['work_unit_id']}
    rows = [{k: c[k] for k in _DEFINITION} for c in state['acceptance_criteria']
            if (c.get('work_unit_id'), c.get('contract_revision')) == (unit['work_unit_id'], unit['contract_revision'])
            and c['id'] in selected_ids]
    contract = dict(work_unit_id=unit['work_unit_id'], contract_revision=unit['contract_revision'], owner=owner,
        criteria=rows, relevant_context={k: state.get(k, {} if k == 'latest_scope' else [])
            for k in ('constraints', 'non_goals', 'decisions', 'latest_scope')})
    return contract, state['latest_input_event_id'], route['selected_criteria']


def _lookup_key(contract, proof, verified_at):
    return _reference(dict(contract=contract, proof_digest=_digest(proof), verified_at=verified_at))


def _source_issues(candidate, source_ids=None):
    # Accepted-proof reuse reads the authenticated retained bytes. Current
    # business artifact dependencies are checked independently of those bytes.
    if candidate.get('reuse_evidence'):
        return {}
    issues = {}
    for source in candidate['sources']:
        if source_ids is not None and source['source_id'] not in source_ids:
            continue
        if source.get('kind') == 'accepted-proof':
            continue  # Core retained bytes after trusted Ghost-ALICE Autopilot chain authentication.
        try:
            actual = _digest(Path(source['locator']).read_bytes())
        except OSError:
            issues[source['source_id']] = 'source-unavailable'
            continue
        if actual != source['digest']:
            issues[source['source_id']] = 'source-changed'
    return issues


def _snapshot_sources(source_paths):
    _require(isinstance(source_paths, list) and len(source_paths) == len(set(source_paths))
        and all(isinstance(p, str) and Path(p).is_absolute() for p in source_paths),
        'source-unavailable', 'Unique explicit absolute source paths are required')
    sources = []
    for path in source_paths:
        raw = Path(path).read_bytes()
        item = dict(locator=path, digest=_digest(raw), bytes_base64=base64.b64encode(raw).decode())
        item['source_id'] = _reference(item)
        item['text'] = raw.decode('utf-8', errors='replace')
        sources.append(item)
    return sources


def _response_review_key(root, owner, route, text):
    return _reference(dict(kind=_SOURCE_RESPONSE_KIND, intent_root=str(root),
        owner=owner, route=route, text=text.strip()))


def _response_review_context(route):
    """Bind review to its task, not later planning or proof-recording labels.

    The full original route remains immutable in the candidate. Current route
    validation still authenticates admission, ownership and the input before
    this projection is compared; focus and burden do not change answer bytes.
    """
    selected = set(route['selected_work_unit_ids'])
    return dict(
        **{key: route.get(key) for key in ('schema_version', 'platform', 'session_id',
            'input_event_id', 'boundary_ref', 'response_purpose')},
        routing_surface={key: route['routing_surface'][key]
            for key in ('response_mode', 'boundary_contract')},
        work_units=sorted((unit for unit in route['work_units']
            if unit['work_unit_id'] in selected), key=lambda unit: unit['work_unit_id']),
        selected_work_unit_ids=sorted(selected),
        selected_criteria=sorted(({key: row[key] for key in _DEFINITION}
            for row in route['selected_criteria']),
            key=lambda row: (row['work_unit_id'], row['contract_revision'], row['id'])))


def _source_response_records(connection, root, owner, route, text):
    """Resolve existing immutable records without rewriting their original keys."""
    rows = connection.execute('''SELECT c.reference, r.reference
        FROM core_completion_candidates c LEFT JOIN core_completion_responses r
        ON json_extract(r.payload_json, '$.candidate_reference')=c.reference
        WHERE json_extract(c.payload_json, '$.kind')=?
        AND json_extract(c.payload_json, '$.intent_root')=?
        AND json_extract(c.payload_json, '$.owner.platform')=?
        AND json_extract(c.payload_json, '$.owner.session_id')=?
        AND json_extract(c.payload_json, '$.route.input_event_id')=?
        AND trim(json_extract(c.payload_json, '$.response_binding.text'), ?)=?
        ORDER BY r.reference IS NULL, c.rowid''',
        (_SOURCE_RESPONSE_KIND, str(root), owner['platform'], owner['session_id'], route['input_event_id'],
         ' \t\r\n', text.strip())).fetchall()
    context = _response_review_context(route)
    records = []
    for reference, review in rows:
        candidate = _stored(connection, 'core_completion_candidates', reference)
        if _response_review_context(candidate['route']) == context:
            records.append((reference, candidate, review))
    return records


def prepare_response_review(source, *, response_text, source_paths, verified_at=None,
                            reuse_candidate=None, reuse_evidence=None, work_unit_id=None,
                            claim_statements=None):
    """Prepare one complete answer; derive completion proof from that same text.

    Source timestamps remain in the original bytes. observed_at dates this
    inspection only, never the business result described by those sources.
    """
    _require(isinstance(response_text, str) and bool(response_text.strip()),
        'response-unbound', 'A complete response draft is required')
    marker = looks_like_evidence_claim(response_text)
    if marker or reuse_candidate or reuse_evidence:
        _require(claim_statements is None, 'response-unbound',
            'Completion claim bindings come from the original proof block')
        proof = extract_original_proof(response_text) if marker else None
        if marker and proof.strip() == response_text.strip():
            proof = response_text
        if reuse_candidate:
            core, root, _ = _coordinates(source)
            with core._read_connection(root) as connection:
                original = _stored(connection, 'core_completion_candidates', reuse_candidate)
            verified_at = original['verified_at'] if verified_at is None else verified_at
            if not marker:
                proof = original['proof']
        _require(isinstance(verified_at, str) and bool(verified_at),
            'proof-invalid', 'Completion evidence requires its original verification time')
        return prepare_assessment(source, proof=proof, verified_at=verified_at,
            source_paths=source_paths, reuse_candidate=reuse_candidate, reuse_evidence=reuse_evidence,
            work_unit_id=work_unit_id, response_text=response_text)
    core, root, owner = _coordinates(source)
    _require(verified_at is None, 'response-unbound', 'An explanation does not create a verification timestamp')
    _require(bool(source_paths), 'source-unavailable', 'Explanation sources are required')
    _require(isinstance(claim_statements, list) and bool(claim_statements)
        and all(isinstance(statement, str) and bool(statement.strip())
            and statement in response_text for statement in claim_statements)
        and len(claim_statements) == len(set(claim_statements)),
        'response-unbound', 'Name each source-backed claim with its exact statement from the explanation draft')
    bindings = []
    for index, statement in enumerate(claim_statements):
        binding = dict(claim_index=index, claim=statement)
        binding['binding_id'] = _reference(binding)
        bindings.append(binding)
    with core._transaction(root) as connection:
        _, route = _current_response_context(core, root, owner, connection)
        _require(route.get('response_purpose') == 'explanation', 'response-unbound',
            'Register the current explanation route before reviewing its sources')
        response = dict(text=response_text, purpose='explanation')
        response['binding_id'] = _reference(response)
        candidate = dict(kind=_SOURCE_RESPONSE_KIND, intent_root=str(root), owner=owner,
            route=route, sources=_snapshot_sources(source_paths), bindings=bindings, response_binding=response)
        _schema(connection)
        key = _response_review_key(root, owner, route, response_text)
        retained = _source_response_records(connection, root, owner, route, response_text)
        for reference, original, _ in retained:
            if ({k: v for k, v in original.items() if k not in {'observed_at', 'route'}}
                    == {k: v for k, v in candidate.items() if k != 'route'}):
                return dict(original, candidate_reference=reference)
        candidate['observed_at'] = datetime.now(timezone.utc).isoformat()
        reference = _reference(candidate)
        connection.execute('INSERT OR IGNORE INTO core_completion_candidates VALUES (?,?,?)',
            (reference, key, _json(candidate)))
    return dict(candidate, candidate_reference=reference)


def _validate_response_review(candidate, root, owner, route):
    _require(candidate.get('kind') == _SOURCE_RESPONSE_KIND
        and candidate.get('intent_root') == str(root) and candidate.get('owner') == owner
        and _response_review_context(candidate['route']) == _response_review_context(route)
        and route.get('response_purpose') == 'explanation',
        'response-unbound', 'Explanation review must retain its exact current owner, input and selected contract')


def record_response_review(source, candidate_reference, review):
    """Commit claim and whole-answer judgments together; return only reviewed text."""
    _require(isinstance(review, dict) and set(review) == {'claims', 'response'}
        and isinstance(review['claims'], list), 'response-assessment-required',
        'One review must contain claims and response judgments')
    core, root, owner = _coordinates(source)
    with core._transaction(root) as connection:
        candidate = _stored(connection, 'core_completion_candidates', candidate_reference)
        judgment = review['response']
        if candidate.get('schema_version') == 'completion-assessment-candidate.v1':
            _require(candidate.get('response_binding') is not None, 'response-unbound',
                'Prepare the complete response draft before recording its review')
            result = record_assessment(source, candidate_reference, review['claims'],
                response_judgment=judgment, transaction=connection)
            supported = all(j['verdict'] == 'supported' for j in review['claims'] + [judgment])
            result['status'] = 'reviewed' if supported else 'unsupported'
            if supported:
                result['response_text'] = candidate['response_binding']['text']
            return result
        _, route = _current_response_context(core, root, owner, connection)
        _validate_response_review(candidate, root, owner, route)
        _validate_claim_judgments(candidate, review['claims'])
        _require(_valid_judgment(judgment, candidate['response_binding']['binding_id'],
            {s['source_id'] for s in candidate['sources']}), 'response-assessment-required',
            'Review the complete explanation against identified sources with an explicit rationale')
        issues = _source_issues(candidate, {sid for j in review['claims'] + [judgment] for sid in j['source_ids']})
        _require(not issues, next(iter(issues.values()), 'source-changed'), 'Explanation source changed or is unavailable')
        payload = dict(kind=candidate['kind'], candidate_reference=candidate_reference,
            judgments=review['claims'], judgment=judgment)
        key = _response_review_key(root, owner, route, candidate['response_binding']['text'])
        previous = [reference for prepared, _, reference in _source_response_records(connection, root,
            owner, route, candidate['response_binding']['text'])
            if prepared == candidate_reference and reference is not None]
        reference = _reference(payload)
        _require(not previous or (len(previous) == 1 and previous[0] == reference),
            'assessment-conflict', 'This explanation candidate already has an immutable review')
        connection.execute('INSERT OR IGNORE INTO core_completion_responses VALUES (?,?,?)',
            (reference, key, _json(payload)))
    supported = all(j['verdict'] == 'supported' for j in review['claims'] + [judgment])
    result = dict(response_reference=reference, status='reviewed' if supported else 'unsupported')
    if supported:
        result['response_text'] = candidate['response_binding']['text']
    return result


def _lookup_source_response(core, root, owner, connection, text):
    _, route = _current_response_context(core, root, owner, connection)
    rows = [(candidate, reference) for _, candidate, reference in _source_response_records(
        connection, root, owner, route, text) if reference is not None]
    if not rows:
        return None
    current, source_issues = [], {}
    for candidate, reference in rows:
        reviewed = _stored(connection, 'core_completion_responses', reference)
        _validate_response_review(candidate, root, owner, route)
        _validate_claim_judgments(candidate, reviewed['judgments'])
        _require(candidate['response_binding']['text'].strip() == text.strip()
            and _valid_judgment(reviewed['judgment'], candidate['response_binding']['binding_id'],
                {s['source_id'] for s in candidate['sources']}), 'response-unsupported',
            'The recorded review must bind the exact explanation')
        issues = _source_issues(candidate,
            {sid for j in reviewed['judgments'] + [reviewed['judgment']] for sid in j['source_ids']})
        if issues:
            source_issues.update(issues)
        else:
            current.append(reviewed)
    # Earlier source snapshots remain immutable evidence, but cannot authenticate
    # this answer against source bytes that have since changed.
    _require(bool(current), next(iter(source_issues.values()), 'source-changed'),
        'Explanation source changed or is unavailable')
    _require(len(current) == 1, 'response-unbound', 'One exact current explanation review is required')
    reviewed = current[0]
    _require(all(j['verdict'] == 'supported' for j in reviewed['judgments'] + [reviewed['judgment']]),
        'response-unsupported', 'The recorded review must support the exact explanation')
    return reviewed


def _accepted_observation(source, reference, contract, verified_at, *, transaction=None, criterion=None):
    from original_claim_evidence import _managed_archive_provider
    try:
        provider = _managed_archive_provider(source)
        with provider.open_claim_archive(source) as archive:
            record = archive.accepted_evidence(contract['work_unit_id'], reference, datetime.now(timezone.utc).isoformat())
        _require(isinstance(record, dict), 'assessment-required', 'Accepted original evidence is unavailable')
        _require(record['contract']['owner'] == contract['owner']
            and record['contract']['work_unit_id'] == contract['work_unit_id'] and record['verified_at'] == verified_at,
            'assessment-invalid', 'Historical reuse must preserve accepted owner, unit and original time')
        if criterion is not None:
            _require(any((row['work_unit_id'], row['contract_revision'], row['id']) ==
                (criterion['work_unit_id'], criterion['contract_revision'], criterion['criterion_id'])
                for row in record['criteria']), 'criterion-unbound', 'Predecessor must cover the affected qualified criterion')
        try:
            original = lookup_assessment(source, record['contract'], record['proof'], record['verified_at'],
                transaction=transaction, historical=True)
            return original.candidate['sources'], original.candidate.get('capture_binding'), record['proof']
        except CompletionAssessmentError as exc:
            if exc.reason != 'assessment-required':
                raise
        raw = record['proof'].encode()
        item = dict(kind='accepted-proof', locator=reference, digest=_digest(raw),
            bytes_base64=base64.b64encode(raw).decode(), text=record['proof'],
            verified_at=record['verified_at'], original_contract=record['contract'], dependencies=record['dependencies'])
        item['source_id'] = _reference(item)
        return [item], None, record['proof']
    except CompletionAssessmentError:
        raise
    except (ValueError, OSError, ImportError, AttributeError, KeyError, TypeError) as exc:
        raise CompletionAssessmentError('assessment-required', 'Trusted accepted original source is unavailable') from exc


def _capture_binding(source, contract, event, connection):
    """Read the existing Ghost-ALICE Autopilot capture before snapshotting new review sources.

    Ghost-ALICE Autopilot is optional. A provider without capture_binding supplies no causal proof. An invalid
    capture record is an error, never permission to infer ordering.
    """
    from original_claim_evidence import _managed_archive_provider, _Rejected
    try:
        provider = _managed_archive_provider(source)
        capture = getattr(provider, 'capture_binding', None)
        binding = capture(source, contract, transaction=connection) if callable(capture) else None
        return binding if binding is not None and binding.get(
            'authority_input_event_id', binding['input_event_id']) == event else None
    except _Rejected as exc:
        if exc.reason == 'archive-provider-unavailable':
            return None
        raise CompletionAssessmentError('capture-invalid', str(exc)) from exc
    except (ValueError, OSError, ImportError) as exc:
        raise CompletionAssessmentError('capture-invalid', str(exc)) from exc


def prepare_assessment(source, *, proof, verified_at, source_paths, reuse_candidate=None,
                       reuse_evidence=None, work_unit_id=None, response_text=None, transaction=None):
    """Snapshot explicit observation paths and canonical conditions for review.

    This proof-storage primitive is also used for retained Ghost-ALICE Autopilot evidence. Native
    answer producers use prepare_response_review and record_response_review;
    proof-only records do not approve a complete native answer.
    Reuse copies a prior candidate's actual bytes/time for recording correction;
    it never reads a changed file and presents it as the original observation.
    """
    core, root, owner = _coordinates(source)
    _require(isinstance(proof, str) and bool(proof.strip()), 'proof-invalid', 'Original proof is required')
    try:
        when = datetime.fromisoformat(verified_at.replace('Z', '+00:00'))
        _require(when.tzinfo is not None, 'proof-invalid', 'Original time needs a timezone')
        parsed = parse_original_completion_evidence(proof, allow_partial=True, allow_failed_completion=True)
        report_only = response_text is not None and not looks_like_evidence_claim(response_text)
        if response_text is not None:
            _require(isinstance(response_text, str) and bool(response_text.strip()),
                'response-unbound', 'The complete response must be nonempty text')
            if not report_only:
                _require(extract_original_proof(response_text).strip() == proof.strip(),
                    'response-unbound', 'A completion response must retain the exact original proof block')
    except (ValueError, TypeError, AttributeError) as exc:
        raise CompletionAssessmentError('proof-invalid', str(exc)) from exc
    with core._transaction(root, transaction) as connection:
        contract, event, selected = _current_contract(core, root, owner, connection, work_unit_id)
        _schema(connection)
        _require(not (reuse_candidate and reuse_evidence), 'assessment-invalid', 'Choose one original source for reuse')
        if reuse_evidence:
            _require(not source_paths, 'assessment-invalid', 'Historical reuse cannot import newly read observation bytes')
            sources, capture, original_proof = _accepted_observation(
                source, reuse_evidence, contract, verified_at, transaction=connection)
            if proof.strip() == original_proof.strip():
                proof = original_proof
        elif reuse_candidate:
            old = _stored(connection, 'core_completion_candidates', reuse_candidate)
            _require(old['contract']['owner'] == owner and old['intent_root'] == str(root)
                and old['contract']['work_unit_id'] == contract['work_unit_id'] and not source_paths
                and old['verified_at'] == verified_at, 'assessment-invalid', 'Reuse preserves original owner, sources and time')
            sources = old['sources']
            capture = old.get('capture_binding')
            if proof.strip() == old['proof'].strip():
                proof = old['proof']
        else:
            capture = _capture_binding(source, contract, event, connection)
            try:
                sources = _snapshot_sources(source_paths)
            except OSError as exc:
                raise CompletionAssessmentError('source-unavailable', str(exc)) from exc
        rows = {row['id']: row for row in contract['criteria']}
        selected_ids = {r['criterion_id'] for r in selected if r['work_unit_id'] == contract['work_unit_id']}
        bindings = []
        for index, entry in enumerate(parsed['claims']):
            for criterion_id in re.split(r'[,;\s]+', entry['criterion']):
                _require(criterion_id in rows and criterion_id in selected_ids and rows[criterion_id]['admitted'],
                    'criterion-unbound', 'Proof must bind a current selected canonical criterion')
                binding = dict(criterion=rows[criterion_id], claim_index=index,
                    claim=entry['claim'], evidence=entry['evidence'], declared_verdict=entry['verdict'])
                binding['binding_id'] = _reference(binding)
                bindings.append(binding)
        candidate = dict(schema_version='completion-assessment-candidate.v1', intent_root=str(root),
            input_event_id=event, contract=contract, proof=proof, proof_digest=_digest(proof),
            verified_at=verified_at, sources=sources, bindings=bindings,
            reuse_candidate=reuse_candidate, reuse_evidence=reuse_evidence)
        if capture is not None:
            candidate['capture_binding'] = capture
        if response_text is not None:
            response = dict(text=response_text, proof_digest=_digest(proof))
            if report_only:
                route = core.read_response_route(root=root, **owner, transaction=connection)
                _require(route.get('response_purpose') == 'explanation' and bool(reuse_candidate)
                    and old['proof'] == proof and old['contract'] == contract,
                    'response-unbound', 'An explanation must reuse an original reviewed proof under its current owned contract')
                original = lookup_assessment(source, contract, proof, verified_at, transaction=connection)
                _require(all(j['verdict'] == 'supported' for j in original.judgments),
                    'assessment-unsupported', 'An explanation requires supported original judgments')
                response['purpose'] = 'explanation'
            response['binding_id'] = _reference(response)
            candidate['response_binding'] = response
        reference = _reference(candidate)
        connection.execute('INSERT OR IGNORE INTO core_completion_candidates VALUES (?,?,?)',
            (reference, _lookup_key(contract, proof, verified_at), _json(candidate)))
    return dict(candidate, candidate_reference=reference)


def _valid_judgment(judgment, binding_id, source_ids):
    return (isinstance(judgment, dict) and set(judgment) == {'binding_id', 'verdict', 'source_ids', 'rationale'}
        and judgment['binding_id'] == binding_id
        and judgment['verdict'] in {'supported', 'unsupported', 'unavailable'}
        and isinstance(judgment['rationale'], str) and bool(judgment['rationale'].strip())
        and isinstance(judgment['source_ids'], list)
        and all(isinstance(sid, str) for sid in judgment['source_ids'])
        and len(judgment['source_ids']) == len(set(judgment['source_ids']))
        and set(judgment['source_ids']) <= source_ids
        and (judgment['verdict'] == 'unavailable' or bool(judgment['source_ids'])))


def _validate_claim_judgments(candidate, judgments):
    source_ids = {s['source_id'] for s in candidate['sources']}
    bindings = {b['binding_id'] for b in candidate['bindings']}
    _require(isinstance(judgments, list) and len(judgments) == len(bindings),
        'assessment-invalid', 'Every claim binding needs one separate source judgment')
    seen = set()
    for judgment in judgments:
        binding_id = judgment.get('binding_id') if isinstance(judgment, dict) else None
        _require(isinstance(binding_id, str) and binding_id in bindings and binding_id not in seen
            and _valid_judgment(judgment, binding_id, source_ids),
            'assessment-invalid', 'Judgment must connect exact bindings, inspected sources and rationale')
        seen.add(binding_id)


def record_assessment(source, candidate_reference, judgments, *, response_judgment=None, transaction=None):
    """Record the current-session assistant's explicit binding/source judgments append-only.

    A rationale is review evidence, not a machine-verifiable semantic proof.
    Missing observations should be recorded unavailable, never guessed pass.
    Response review covers the entire displayed candidate, including prose
    outside its proof. It appends to, and never rewrites, original proof review.
    """
    core, root, owner = _coordinates(source)
    with core._transaction(root, transaction) as connection:
        candidate = _stored(connection, 'core_completion_candidates', candidate_reference)
        contract, event, _ = _current_contract(core, root, owner, connection, candidate['contract']['work_unit_id'])
        _require(candidate['intent_root'] == str(root) and candidate['contract'] == contract
            and candidate['input_event_id'] == event, 'assessment-stale', 'Review candidate no longer matches its current owner/contract/input')
        source_ids = {s['source_id'] for s in candidate['sources']}
        _validate_claim_judgments(candidate, judgments)
        response = candidate.get('response_binding')
        _require((response is None and response_judgment is None)
            or (response is not None and _valid_judgment(response_judgment, response['binding_id'], source_ids)),
            'response-assessment-required', 'The review must cover the complete response as well as its claim bindings')
        if not candidate['reuse_candidate']:
            reviewed_sources = judgments + ([response_judgment] if response_judgment is not None else [])
            issues = _source_issues(candidate, {sid for j in reviewed_sources for sid in j['source_ids']})
            _require(not issues, next(iter(issues.values()), 'source-changed'), 'Reviewed observation source changed or is unavailable')
        key = _lookup_key(contract, candidate['proof'], candidate['verified_at'])
        previous = connection.execute('SELECT reference FROM core_completion_assessments WHERE lookup_key=?', (key,)).fetchall()
        if previous:
            old = _stored(connection, 'core_completion_assessments', previous[0][0])
            if response is None:
                _require(old['candidate_reference'] == candidate_reference and old['judgments'] == judgments,
                         'assessment-conflict', 'An immutable assessment already exists for this original proof')
            else:
                # A later response review retains the accepted observations and
                # proof verdicts. Its new rationale/wording is a separate record.
                def decisions(rows):
                    return {j['binding_id']: (j['verdict'], sorted(j['source_ids'])) for j in rows}
                _require(old['candidate']['sources'] == candidate['sources']
                    and old['candidate']['bindings'] == candidate['bindings']
                    and old['candidate'].get('capture_binding') == candidate.get('capture_binding')
                    and decisions(old['judgments']) == decisions(judgments),
                    'assessment-conflict', 'Response review cannot replace original observations or proof judgments')
            reference = previous[0][0]
        else:
            original = {k: v for k, v in candidate.items() if k != 'response_binding'}
            original_reference = _reference(original)
            connection.execute('INSERT OR IGNORE INTO core_completion_candidates VALUES (?,?,?)',
                (original_reference, key, _json(original)))
            payload = dict(candidate_reference=original_reference, candidate=original,
                judgments=judgments, assessed_at=datetime.now(timezone.utc).isoformat())
            reference = _reference(payload)
            connection.execute('INSERT INTO core_completion_assessments VALUES (?,?,?)', (reference, key, _json(payload)))
        result = dict(assessment_reference=reference, candidate_reference=candidate_reference)
        if response is not None:
            response_identity = dict(assessment_reference=reference, body=response['text'].strip())
            if response.get('purpose') == 'explanation':
                response_identity['input_event_id'] = event
            response_key = _reference(response_identity)
            prior = connection.execute('SELECT reference FROM core_completion_responses WHERE lookup_key=?',
                (response_key,)).fetchall()
            if prior:
                retained = _stored(connection, 'core_completion_responses', prior[0][0])
                _require(retained['judgment'] == response_judgment, 'assessment-conflict',
                         'The exact response already has an immutable recorded assistant judgment')
                result['response_reference'] = prior[0][0]
            else:
                payload = dict(assessment_reference=reference, candidate_reference=candidate_reference,
                    response_binding=response, judgment=response_judgment, reviewed_at=datetime.now(timezone.utc).isoformat())
                response_reference = _reference(payload)
                connection.execute('INSERT INTO core_completion_responses VALUES (?,?,?)',
                    (response_reference, response_key, _json(payload)))
                result['response_reference'] = response_reference
    return result


def lookup_assessment(source, contract, proof, verified_at, *, transaction=None, historical=False):
    """Authenticate retained Core review; current routing is unnecessary for reads."""
    core, root, owner = _coordinates(source)
    _require(contract.get('owner') == owner, 'owner-mismatch', 'Review belongs to another owner')
    with core._read_connection(root, transaction) as connection:
        _require(connection is not None and connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='core_completion_assessments'").fetchone(),
            'assessment-required', 'The current-session assistant must review the original proof and record its judgments through completion_assessment.py')
        rows = connection.execute('SELECT reference FROM core_completion_assessments WHERE lookup_key=?',
            (_lookup_key(contract, proof, verified_at),)).fetchall()
        _require(len(rows) == 1, 'assessment-required', 'One exact original Core assessment is required')
        payload = _stored(connection, 'core_completion_assessments', rows[0][0])
        candidate = _stored(connection, 'core_completion_candidates', payload['candidate_reference'])
        _require(candidate == payload['candidate'] and candidate['intent_root'] == str(root),
                 'assessment-invalid', 'Review candidate authority differs')
        issues = {} if historical else _source_issues(candidate, {sid for j in payload['judgments'] for sid in j['source_ids']})
        result = VerifiedAssessment(payload, _SEAL, issues)
        coverage = assessment_coverage(result, contract, proof, verified_at)
        _require(not issues or bool(coverage), next(iter(issues.values()), 'source-changed'),
                 'Reviewed observation sources changed or are unavailable')
        return result


verified_assessment = lookup_assessment


def lookup_current_proof(source, proof, *, transaction=None, work_unit_id=None):
    """Resolve delivered controls to one reviewed original; retain its bytes/time."""
    core, root, owner = _coordinates(source)
    with core._read_connection(root, transaction) as connection:
        _require(connection is not None, 'assessment-required', 'Owning Core review is unavailable')
        route = core.read_response_route(root=root, **owner, transaction=connection)
        _require(isinstance(route, dict), 'route-unavailable', 'Current owned route is required')
        units = [work_unit_id] if work_unit_id is not None else route.get('selected_work_unit_ids', [])
        contracts = [_current_contract(core, root, owner, connection, unit)[0] for unit in units]
        _require(connection.execute("SELECT 1 FROM sqlite_master WHERE name='core_completion_assessments'").fetchone(),
                 'assessment-required', 'The current-session assistant must review the original proof and record its judgments through completion_assessment.py')
        exact, equivalent = [], []
        comparison = review_comparison_text(proof)
        for contract in contracts:
            rows = connection.execute('''SELECT reference FROM core_completion_assessments WHERE
                json_extract(payload_json, '$.candidate.contract.owner.platform')=? AND
                json_extract(payload_json, '$.candidate.contract.owner.session_id')=? AND
                json_extract(payload_json, '$.candidate.contract.work_unit_id')=?''',
                (owner['platform'], owner['session_id'], contract['work_unit_id']))
            for reference, in rows:
                candidate = _stored(connection, 'core_completion_assessments', reference)['candidate']
                if candidate['contract'] != contract:
                    continue
                if candidate['proof'].strip() == proof.strip():
                    exact.append(candidate)
                elif review_comparison_text(candidate['proof']) == comparison:
                    equivalent.append(candidate)
        matches = exact or equivalent
        _require(len(matches) == 1, 'assessment-required', 'Final block must match exactly one recorded assistant review of its original proof')
        candidate = matches[0]
        return lookup_assessment(source, candidate['contract'], candidate['proof'], candidate['verified_at'], transaction=connection)


def lookup_current_response(source, text, *, transaction=None):
    """Authenticate reviewed content and original sources, allowing control-marker spacing."""
    core, root, owner = _coordinates(source)
    report_only = not looks_like_evidence_claim(text)
    if not report_only:
        proof = extract_original_proof(text)
        assessment = lookup_current_proof(source, proof, transaction=transaction)
    with core._read_connection(root, transaction) as connection:
        _require(connection is not None and connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='core_completion_responses'").fetchone(),
            'response-assessment-required', 'The current-session assistant must review this complete response and record its judgment')
        if report_only:
            reviewed_source = _lookup_source_response(core, root, owner, connection, text)
            if reviewed_source is not None:
                return reviewed_source
            contract, event, _ = _current_contract(core, root, owner, connection)
            route = core.read_response_route(root=root, **owner, transaction=connection)
            _require(route.get('response_purpose') == 'explanation', 'response-unbound',
                'A retained explanation requires the current explanation route')
            rows = connection.execute('''SELECT r.reference FROM core_completion_responses r
                JOIN core_completion_candidates c ON c.reference=json_extract(r.payload_json, '$.candidate_reference')
                WHERE trim(json_extract(c.payload_json, '$.response_binding.text'), ?)=?
                AND json_extract(c.payload_json, '$.response_binding.purpose')='explanation'
                AND json_extract(c.payload_json, '$.input_event_id')=?
                AND json_extract(c.payload_json, '$.intent_root')=?
                AND json_extract(c.payload_json, '$.contract.owner.platform')=?
                AND json_extract(c.payload_json, '$.contract.owner.session_id')=?
                AND json_extract(c.payload_json, '$.contract.work_unit_id')=?''',
                (' \t\r\n', text.strip(), event, str(root), owner['platform'], owner['session_id'],
                 contract['work_unit_id'])).fetchall()
        else:
            key = _reference(dict(assessment_reference=assessment.reference, body=text.strip()))
            rows = connection.execute('SELECT reference FROM core_completion_responses WHERE lookup_key=?', (key,)).fetchall()
            if not rows:
                candidates = connection.execute('''SELECT reference FROM core_completion_responses
                    WHERE json_extract(payload_json, '$.assessment_reference')=?''', (assessment.reference,))
                comparison = review_comparison_text(text)
                rows = [(reference,) for reference, in candidates
                    if review_comparison_text(_stored(connection, 'core_completion_responses', reference)
                        ['response_binding']['text']) == comparison]
        _require(len(rows) == 1, 'response-assessment-required', 'Complete response text must match exactly one recorded assistant review')
        reviewed = _stored(connection, 'core_completion_responses', rows[0][0])
        candidate = _stored(connection, 'core_completion_candidates', reviewed['candidate_reference'])
        if report_only:
            _require(candidate['contract'] == contract and candidate.get('reuse_candidate'),
                'response-unbound', 'The explanation must retain the current owned original proof')
            assessment = lookup_assessment(source, contract, candidate['proof'], candidate['verified_at'],
                transaction=connection)
        response = candidate.get('response_binding')
        _require(response == reviewed['response_binding']
            and review_comparison_text(response['text']) == review_comparison_text(text)
            and reviewed['assessment_reference'] == assessment.reference
            and candidate['contract'] == assessment.candidate['contract']
            and candidate['proof'] == assessment.candidate['proof']
            and candidate['verified_at'] == assessment.candidate['verified_at']
            and candidate['sources'] == assessment.candidate['sources'],
            'response-unbound', 'Reviewed response differs from its original proof and observations')
        _require(reviewed['judgment']['verdict'] == 'supported', 'response-unsupported',
                 'The recorded assistant judgment does not support this complete response')
        issues = _source_issues(candidate, set(reviewed['judgment']['source_ids']))
        _require(not issues, next(iter(issues.values()), 'source-changed'), 'Response observations changed or are unavailable')
    return assessment


def lookup_publication_assessment(source, contract, text, verified_at, *, transaction=None):
    """Give Ghost-ALICE Autopilot the original proof of a reviewed answer, without rewriting it."""
    _require(isinstance(text, str) and bool(text.strip()), 'proof-invalid', 'Publication requires reviewed evidence')
    assessment = lookup_current_response(source, text, transaction=transaction)
    _require(assessment.candidate['contract'] == contract
        and assessment.candidate['verified_at'] == verified_at,
        'response-unbound', 'Publication must retain the reviewed contract and original verification time')
    return assessment


def assessed_definitions(source, contract, proof, verified_at, *, transaction=None):
    """Return reviewed canonical rows, including unsupported rows needed for correction."""
    assessment = lookup_assessment(source, contract, proof, verified_at, transaction=transaction)
    return assessment_definitions(assessment, contract, proof, verified_at)


def assessment_definitions(assessment, contract, proof, verified_at):
    """Return canonical declared rows from an authenticated Core assessment."""
    bindings = _assessed_bindings(assessment, contract, proof, verified_at)
    declared = set(parse_original_completion_evidence(proof, allow_partial=True)['criterion_ids'])
    rows = {b['criterion']['id']: b['criterion'] for b, _ in bindings
            if b['criterion']['id'] in declared}
    return list(rows.values())


def _assessed_bindings(assessment, contract, proof, verified_at):
    _require(isinstance(assessment, VerifiedAssessment) and assessment._seal is _SEAL,
             'assessment-required', 'Only authenticated Core assessments can establish coverage')
    candidate = assessment.candidate
    _require(candidate['contract'] == contract and candidate['proof'] == proof
        and candidate['proof_digest'] == _digest(proof) and candidate['verified_at'] == verified_at,
        'assessment-invalid', 'Assessment does not bind these canonical conditions, proof and original time')
    judgments = {j['binding_id']: j for j in assessment.judgments}
    return [(b, dict(judgments[b['binding_id']], verdict='unavailable')
             if set(judgments[b['binding_id']]['source_ids']) & assessment.source_issues.keys()
             else judgments[b['binding_id']]) for b in candidate['bindings']]


def assessment_coverage(assessment, contract, proof, verified_at):
    """Pure provenance gate: all submitted claims for a criterion must be supported."""
    supported, rejected = set(), set()
    declared = set(parse_original_completion_evidence(proof, allow_partial=True, allow_failed_completion=True)['criterion_ids'])
    for binding, judgment in _assessed_bindings(assessment, contract, proof, verified_at):
        row = binding['criterion']; key = (row['work_unit_id'], row['contract_revision'], row['id'])
        (supported if judgment['verdict'] == 'supported' and binding['declared_verdict'] == 'pass' and row['id'] in declared
         else rejected).add(key)
    return supported - rejected


def assessment_capture_binding(assessment, contract, proof, verified_at):
    """Return causal review provenance only from an authenticated Core record.

    The capture preceded this candidate's source snapshot. That sequence does
    not attest when a historical business operation actually ran.
    """
    _assessed_bindings(assessment, contract, proof, verified_at)
    return assessment.candidate.get('capture_binding')


def assessment_rejections(assessment, contract, proof, verified_at):
    """Return criterion keys explicitly marked unsupported in the recorded assistant review."""
    return {(b['criterion']['work_unit_id'], b['criterion']['contract_revision'], b['criterion']['id'])
            for b, j in _assessed_bindings(assessment, contract, proof, verified_at) if j['verdict'] == 'unsupported'}


def assessment_reviewed(assessment, contract, proof, verified_at):
    reviewed, unavailable = set(), set()
    for binding, judgment in _assessed_bindings(assessment, contract, proof, verified_at):
        row = binding['criterion']; key = (row['work_unit_id'], row['contract_revision'], row['id'])
        (unavailable if judgment['verdict'] == 'unavailable' else reviewed).add(key)
    return reviewed - unavailable


def authorize_correction(source, *, criterion, proof, verified_at, contract,
                         original_evidence_reference=None, transaction=None):
    assessment = lookup_assessment(source, contract, proof, verified_at, transaction=transaction)
    candidate = assessment.candidate
    _require(isinstance(original_evidence_reference, str) and bool(original_evidence_reference)
        and candidate.get('reuse_evidence') == original_evidence_reference,
        'correction-source-unbound', 'Correction must retain the exact accepted predecessor through reuse_evidence')
    original_sources, original_capture, _ = _accepted_observation(source, original_evidence_reference, contract, verified_at,
        transaction=transaction, criterion=criterion)
    _require(candidate['sources'] == original_sources and candidate.get('capture_binding') == original_capture,
             'correction-source-unbound',
             'Correction sources must be the authenticated original observation snapshots')
    key = tuple(criterion[k] for k in ('work_unit_id', 'contract_revision', 'criterion_id'))
    _require(key in assessment_reviewed(assessment, contract, proof, verified_at),
             'assessment-required', 'The current-session assistant must record a judgment for every claim binding affected by the correction')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    response_prepare = commands.add_parser('prepare-response')
    response_prepare.add_argument('--response-file', required=True)
    response_prepare.add_argument('--source', action='append', default=[])
    response_prepare.add_argument('--claims-file', help='JSON array of exact source-backed statements from an explanation draft')
    response_prepare.add_argument('--verified-at')
    response_prepare.add_argument('--reuse-candidate')
    response_prepare.add_argument('--reuse-evidence')
    response_prepare.add_argument('--work-unit-id')
    response_record = commands.add_parser('record-response')
    response_record.add_argument('--candidate', required=True)
    response_record.add_argument('--judgment-file', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare-response':
            result = prepare_response_review(os.environ,
                response_text=Path(args.response_file).read_text(), source_paths=args.source,
                verified_at=args.verified_at, reuse_candidate=args.reuse_candidate,
                reuse_evidence=args.reuse_evidence, work_unit_id=args.work_unit_id,
                claim_statements=json.loads(Path(args.claims_file).read_text()) if args.claims_file else None)
        else:
            result = record_response_review(os.environ, args.candidate,
                json.loads(Path(args.judgment_file).read_text()))
        print(json.dumps(result, ensure_ascii=False))
    except (CompletionAssessmentError, ValueError, OSError) as exc:
        print(json.dumps(dict(status='unavailable', reason=getattr(exc, 'reason', 'assessment-unavailable'), detail=str(exc))))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
