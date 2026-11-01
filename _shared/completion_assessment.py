"""Core authentication of the response model's evidence review.

This module checks provenance and exact bindings. It does not rerun business
observations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from completion_check_validator import (extract_original_proof, parse_original_completion_evidence,
                                        looks_like_evidence_claim, review_comparison_text)
from native_completion import _ledger
from text_lines import committed_byte_lines

_SEAL = object()
_DEFINITION = ('work_unit_id', 'contract_revision', 'id', 'summary', 'source', 'admitted')
_SOURCE_RESPONSE_KIND = 'source-response-review.v2'
_RESPONSE_SCOPE_KIND = 'response-review-scope.v1'
_NATIVE_RESPONSE_KIND = 'native-source-response-review.v1'


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
    __slots__ = ('_json', '_seal', '_issues', '_candidate_json')

    def __init__(self, payload, seal, source_issues=None, *, candidate=None):
        _require(seal is _SEAL, 'assessment-invalid', 'A Core database assessment is required')
        self._json, self._seal = _json(payload), seal
        self._candidate_json = _json(candidate if candidate is not None else payload['candidate'])
        self._issues = tuple((source_issues or {}).items())

    @property
    def candidate(self):
        return json.loads(self._candidate_json)

    @property
    def candidate_reference(self):
        return json.loads(self._json)['candidate_reference']

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
        'assessment-required', 'The response model must review the original proof and record its judgments through completion_assessment.py')
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
    from source_reference import FORMAT, CORE_FORMAT, verify_source
    issues = {}
    for source in candidate['sources']:
        if source_ids is not None and source['source_id'] not in source_ids:
            continue
        reference = source.get('storage') in (FORMAT, CORE_FORMAT)
        try:
            if reference:
                verify_source(source)
            elif _digest(Path(source['locator']).read_bytes()) != source['digest']:
                issues[source['source_id']] = 'source-changed'
        except OSError:
            issues[source['source_id']] = 'source-unavailable'
        except ValueError:
            issues[source['source_id']] = 'source-changed'
    return issues


def _require_record_sources(candidate, judgments):
    """A new supported judgment needs its original bytes; existing records remain historical facts."""
    from source_reference import FORMAT, CORE_FORMAT
    selected = {sid for judgment in judgments if judgment.get('verdict') == 'supported'
                for sid in judgment['source_ids']}
    references = dict(candidate, sources=[s for s in candidate['sources'] if s.get('storage') in (FORMAT, CORE_FORMAT)])
    issues = _source_issues(references, selected)
    _require(not issues, next(iter(issues.values()), 'source-unavailable'),
             'Original bytes for a new supported judgment are unavailable or changed')


def _reference_sources(source_paths, appendable_sources=()):
    from source_reference import reference_source
    _require(isinstance(source_paths, list) and len(source_paths) == len(set(source_paths))
        and all(isinstance(p, str) and Path(p).is_absolute() for p in source_paths),
        'source-unavailable', 'Unique explicit absolute source paths are required')
    _require(set(appendable_sources) <= set(source_paths), 'source-unavailable',
        'Appendable native logs must belong to this source selection')
    return [reference_source(path, appendable=path in appendable_sources) for path in source_paths]


def _native_row_input(connection, owner, native_input_row):
    """Resolve a host-retained Claude row through Core's committed actor audit."""
    import native_transcript
    _require(owner['platform'] == 'claude' and isinstance(native_input_row, dict)
        and set(native_input_row) == {'message_id', 'transcript_path'}
        and all(isinstance(value, str) and bool(value) for value in native_input_row.values())
        and Path(native_input_row['transcript_path']).is_absolute(),
        'input-unavailable', 'An exact native Claude message and transcript are required')
    path = Path(native_input_row['transcript_path']).resolve()
    records = {}
    try:
        with path.open('rb') as stream:
            identity = os.fstat(stream.fileno())
            cursor = hashlib.sha256(json.dumps([str(path), identity.st_dev, identity.st_ino]).encode()).hexdigest()
            for start, offset, line in committed_byte_lines(stream.read()):
                try:
                    entry = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(entry, dict) or not isinstance(entry.get('uuid'), str):
                    continue
                actor = native_transcript.native_input_actor(entry)
                records.setdefault(entry['uuid'], []).append(dict(owner=entry.get('sessionId'),
                    parent=entry.get('parentUuid'), actor=actor,
                    text=native_transcript._input_text(entry) if actor == 'human' else None,
                    locator=dict(cursor_key=cursor, start_offset=start, end_offset=offset)))
    except OSError as exc:
        raise CompletionAssessmentError('input-unavailable', 'Original native transcript is unavailable') from exc
    identity, seen = native_input_row['message_id'], set()
    while True:
        _require(isinstance(identity, str) and bool(identity),
            'input-unavailable', 'Native parent identity is unavailable')
        matches = records.get(identity, [])
        _require(len(matches) == 1 and identity not in seen,
            'input-unavailable', 'One unambiguous native parent chain is required')
        row = matches[0]
        _require(row['owner'] == owner['session_id'], 'input-unavailable', 'Native parent belongs to another session')
        if not seen:
            _require(row['actor'] in ('human', 'notification'),
                'input-unavailable', 'Native response input must be human or a host-stamped task result')
        if row['actor'] == 'human':
            locator, text = row['locator'], row['text']
            break
        seen.add(identity)
        identity = row['parent']
    _require(isinstance(text, str), 'input-unavailable', 'Native human input text is unavailable')
    rows = connection.execute('''SELECT event_json FROM intent_events WHERE platform=? AND session_id=?
        AND json_extract(event_json, '$.event')='native-input-resolved' ''',
        (owner['platform'], owner['session_id'])).fetchall()
    resolved = [event for raw, in rows if (event := json.loads(raw)).get('actor') == 'human'
        and event.get('native_row') == locator]
    _require(len(resolved) == 1, 'input-unavailable', 'Native row lacks one committed Core actor resolution')
    return resolved[0]['input_event_id'], _digest(text), len(text), text


def response_input_binding(source, expected_input_event_id=None, *, native_input_row=None,
                           include_native_prompt=False, transaction=None):
    """Authenticate an original native intake without promoting it to current input."""
    core, root, owner = _coordinates(source)
    with core._read_connection(root, transaction) as connection:
        _require(connection is not None, 'input-unavailable', 'Original Core intake is unavailable')
        native = _native_row_input(connection, owner, native_input_row) if native_input_row is not None else None
        if native is not None:
            _require(expected_input_event_id is None or expected_input_event_id == native[0],
                'input-unavailable', 'Native row and supplied original input disagree')
            expected_input_event_id = native[0]
        _require(isinstance(expected_input_event_id, str) and bool(expected_input_event_id),
            'input-unavailable', 'An exact original input event is required')
        state = core._stored_state(connection, **owner)
        _require(state is not None, 'input-unavailable', 'Original Core owner is unavailable')
        rows = connection.execute('''SELECT sequence,event_json FROM intent_events
            WHERE platform=? AND session_id=? AND json_extract(event_json, '$.event_id')=?''',
            (owner['platform'], owner['session_id'], expected_input_event_id)).fetchall()
        _require(len(rows) == 1, 'input-unavailable', 'One original Core intake is required')
        sequence, raw = rows[0]
        event = json.loads(raw)
        digest, count = event.get('input_digest'), event.get('input_char_count')
        _require(isinstance(digest, str) and digest.startswith('sha256:')
            and len(digest) == 71 and type(count) is int and count >= 0
            and core._observation_matches(event, owner['platform'], owner['session_id'],
                expected_input_event_id, digest, count), 'input-unavailable', 'Original intake binding is invalid')
        _require(native is None or native[:3] == (expected_input_event_id, digest, count),
            'input-unavailable', 'Native row differs from its original Core intake')
        canonical = core._canonical_intake_record(connection, state)
        protected = canonical is not None and tuple(canonical) == (
            expected_input_event_id, digest, count, sequence)
        if not protected:
            imported = core._imported_event_count(connection, root, state)
            _require(imported is not None and sequence > imported,
                'input-unavailable', 'Copied legacy input cannot establish native review authority')
    binding = dict(owner, input_event_id=expected_input_event_id, input_digest=digest, input_char_count=count)
    if include_native_prompt and native is not None:
        binding['native_prompt'] = native[3]
    return binding


def _scope_contract(scope, work_unit_id=None):
    route = scope['route']
    _require(isinstance(route, dict), 'route-unavailable', 'Original owned route snapshot is required')
    owner = {key: scope['input_binding'][key] for key in ('platform', 'session_id')}
    units = [unit for unit in route['work_units'] if unit['work_unit_id'] in route['selected_work_unit_ids']
        and (work_unit_id is None or unit['work_unit_id'] == work_unit_id)]
    _require(len(units) == 1 and units[0]['owner'] == owner,
        'criterion-unbound', 'Select exactly one originally owned work unit')
    unit = units[0]
    rows = [{key: row[key] for key in _DEFINITION} for row in scope['canonical_criteria']
        if row['work_unit_id'] == unit['work_unit_id'] and row['contract_revision'] == unit['contract_revision']]
    return dict(work_unit_id=unit['work_unit_id'], contract_revision=unit['contract_revision'],
        owner=owner, criteria=rows, relevant_context=scope['relevant_context'])


def prepare_response_scope(source, *, expected_input_event_id, source_paths, appendable_sources=(), native_output=None):
    """Bind the original intake, recorded route and source references before nomination.

    A scope without a recorded route supports ordinary nomination only. This
    record supplies no claim judgment, completion admission or display receipt.
    """
    core, root, owner = _coordinates(source)
    with core._transaction(root) as connection:
        binding = response_input_binding(source, expected_input_event_id, transaction=connection)
        rows = connection.execute('''SELECT event_json FROM intent_events WHERE platform=? AND session_id=?
            AND json_extract(event_json, '$.event')='response-route-recorded'
            AND json_extract(event_json, '$.input_event_id')=? ORDER BY sequence DESC''',
            (owner['platform'], owner['session_id'], expected_input_event_id)).fetchall()
        scope = dict(kind=_RESPONSE_SCOPE_KIND, intent_root=str(root), input_binding=binding,
            route=None, relevant_context=None, canonical_criteria=[], sources=_reference_sources(source_paths, appendable_sources))
        if native_output is not None:
            _require(isinstance(native_output, dict) and set(native_output) == {'turn_id', 'message_id'}
                and all(isinstance(v, str) and bool(v) for v in native_output.values()),
                'scope-invalid', 'Exact native output coordinates are required')
            scope['native_output'] = dict(native_output)
        if rows:
            event = json.loads(rows[0][0])
            retained = event.get('review_scope')
            if retained is not None:
                _require(isinstance(retained, dict) and retained.get('input_binding') == binding
                    and isinstance(retained.get('route'), dict)
                    and core.json_digest(retained['route']) == event.get('response_route_digest')
                    and all(retained['route'].get(key) == binding[key]
                        for key in ('platform', 'session_id', 'input_event_id'))
                    and retained['route'].get('status') == 'ready'
                    and isinstance(retained.get('relevant_context'), dict)
                    and isinstance(retained.get('canonical_criteria'), list),
                    'scope-invalid', 'Original route snapshot does not match its owning intake')
                scope.update(route=retained['route'], relevant_context=retained['relevant_context'],
                    canonical_criteria=retained['canonical_criteria'])
        _schema(connection)
        reference = _reference(scope)
        connection.execute('INSERT OR IGNORE INTO core_completion_candidates VALUES (?,?,?)',
            (reference, reference, _json(scope)))
    return dict(scope, scope_reference=reference)


def _stored_response_scope(connection, reference, root, owner):
    scope = _stored(connection, 'core_completion_candidates', reference)
    _require(scope.get('kind') == _RESPONSE_SCOPE_KIND and scope.get('intent_root') == str(root)
        and all(scope.get('input_binding', {}).get(key) == value for key, value in owner.items()),
        'owner-mismatch', 'Review scope belongs to another native owner')
    return scope


def read_response_scope(source, scope_reference, expected_input_event_id):
    """Read original source coordinates and scope without loading source bodies."""
    core, root, owner = _coordinates(source)
    with core._read_connection(root) as connection:
        scope = _stored_response_scope(connection, scope_reference, root, owner)
        _require(scope['input_binding']['input_event_id'] == expected_input_event_id,
            'scope-invalid', 'Review scope belongs to another input')
    return dict(scope, sources=_scope_sources(scope, [s['locator'] for s in scope['sources']]),
                scope_reference=scope_reference)


def _record_source_references(sources, root, owner, candidate_reference):
    from source_reference import core_source_reference
    return [core_source_reference(item, root, owner, candidate_reference) for item in sources]


def _same_sources(left, right):
    # Source IDs authenticate the original bytes and locator, independently of storage location.
    return [{k: s[k] for k in ('source_id', 'locator', 'digest')} for s in left] == [
        {k: s[k] for k in ('source_id', 'locator', 'digest')} for s in right]


def _scope_sources(scope, source_paths):
    owner = {k: scope['input_binding'][k] for k in ('platform', 'session_id')}
    available = {item['locator']: item for item in _record_source_references(scope['sources'],
        scope['intent_root'], owner, _reference(scope))}
    _require(isinstance(source_paths, list) and all(isinstance(path, str) for path in source_paths)
        and len(source_paths) == len(set(source_paths)) and set(source_paths) <= available.keys(),
        'source-unavailable', 'Nomination may select only original scope sources')
    return [available[path] for path in source_paths]


def _response_review_key(root, owner, route, text):
    return _reference(dict(kind=_SOURCE_RESPONSE_KIND, intent_root=str(root),
        owner=owner, route=route, text=text.strip()))


def _response_review_context(route):
    """Bind review to its task, not later planning or proof-recording labels.

    The full original route remains immutable in the candidate. Current route
    validation still authenticates admission, ownership and the input before
    this projection is compared; the optional reason does not change answer bytes.
    """
    selected = set(route['selected_work_unit_ids'])
    return dict(
        **{key: route.get(key) for key in ('schema_version', 'platform', 'session_id',
            'input_event_id', 'boundary_ref')},
        routing_surface={key: route['routing_surface'][key]
            for key in ('response_mode', 'boundary_contract')},
        work_units=sorted((unit for unit in route['work_units']
            if unit['work_unit_id'] in selected), key=lambda unit: unit['work_unit_id']),
        selected_work_unit_ids=sorted(selected),
        selected_criteria=sorted(({key: row[key] for key in _DEFINITION}
            for row in route['selected_criteria']),
            key=lambda row: (row['work_unit_id'], row['contract_revision'], row['id'])))


def reviewed_for_input(source, input_event_id, *, transaction=None):
    """True when a recorded review already binds this owner and input."""
    core, root, owner = _coordinates(source)
    with core._read_connection(root, transaction) as connection:
        if connection is None or not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='core_completion_responses'").fetchone():
            return False
        row = connection.execute('''SELECT 1 FROM core_completion_responses r
            JOIN core_completion_candidates c ON c.reference=json_extract(r.payload_json, '$.candidate_reference')
            WHERE json_extract(c.payload_json, '$.intent_root')=?
            AND (json_extract(c.payload_json, '$.input_event_id')=?
                 OR json_extract(c.payload_json, '$.route.input_event_id')=?)
            AND coalesce(json_extract(c.payload_json, '$.owner.platform'),
                         json_extract(c.payload_json, '$.contract.owner.platform'))=?
            AND coalesce(json_extract(c.payload_json, '$.owner.session_id'),
                         json_extract(c.payload_json, '$.contract.owner.session_id'))=?
            LIMIT 1''', (str(root), input_event_id, input_event_id,
                         owner['platform'], owner['session_id'])).fetchone()
        return row is not None


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




def _validate_native_response(candidate, connection, root, owner):
    _require(candidate.get('kind') == _NATIVE_RESPONSE_KIND
        and candidate.get('intent_root') == str(root) and candidate.get('owner') == owner,
        'owner-mismatch', 'Native review belongs to another owner')
    scope = _stored_response_scope(connection, candidate['scope_reference'], root, owner)
    _require(candidate['input_event_id'] == scope['input_binding']['input_event_id']
        and candidate['sources'] == _scope_sources(scope, [s['locator'] for s in candidate['sources']]),
        'scope-invalid', 'Native review must retain its original input and source bytes')


def lookup_native_response_record(source, *, expected_input_event_id, text, native_output=None):
    """Return the historical review for the exact original native output, not current work evidence."""
    core, root, owner = _coordinates(source)
    with core._read_connection(root) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='core_completion_responses'").fetchone():
            return None
        rows = connection.execute('''SELECT r.reference, c.payload_json FROM core_completion_responses r
            JOIN core_completion_candidates c ON c.reference=json_extract(r.payload_json, '$.candidate_reference')
            WHERE json_extract(c.payload_json, '$.kind')=?
            AND json_extract(c.payload_json, '$.owner.platform')=?
            AND json_extract(c.payload_json, '$.owner.session_id')=?
            AND json_extract(c.payload_json, '$.input_event_id')=?
            AND json_extract(c.payload_json, '$.response_binding.text')=? ORDER BY r.rowid DESC''',
            (_NATIVE_RESPONSE_KIND, owner['platform'], owner['session_id'], expected_input_event_id, text)).fetchall()
        for reference, raw in rows:
            candidate = json.loads(raw)
            if native_output is not None and candidate.get('native_output') == native_output:
                return lookup_original_response_record(source, reference,
                    expected_input_event_id=expected_input_event_id, text=text, transaction=connection)
    return None


def prepare_response_review(source, *, response_text, source_paths, verified_at=None,
                            reuse_candidate=None, work_unit_id=None,
                            claim_statements=None, scope_reference=None):
    """Retain the complete answer and return references for the two judgments.

    Source timestamps remain in the original bytes. observed_at dates this
    inspection only, never the business result described by those sources.
    """
    _require(isinstance(response_text, str) and bool(response_text.strip()),
        'response-unbound', 'A complete response draft is required')
    marker = looks_like_evidence_claim(response_text)
    if marker or reuse_candidate:
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
        return review_receipt(prepare_assessment(source, proof=proof, verified_at=verified_at,
            source_paths=source_paths, reuse_candidate=reuse_candidate,
            work_unit_id=work_unit_id, response_text=response_text, scope_reference=scope_reference))
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
        scope = (_stored_response_scope(connection, scope_reference, root, owner)
            if scope_reference is not None else None)
        route = scope['route'] if scope is not None else _current_response_context(core, root, owner, connection)[1]
        _require(isinstance(route, dict), 'route-unavailable', 'Original owned route snapshot is required')
        response = dict(text=response_text, purpose='explanation')
        response['binding_id'] = _reference(response)
        candidate = dict(kind=_SOURCE_RESPONSE_KIND, intent_root=str(root), owner=owner,
            route=route, sources=_scope_sources(scope, source_paths) if scope is not None else _reference_sources(source_paths),
            bindings=bindings, response_binding=response)
        _schema(connection)
        key = _response_review_key(root, owner, route, response_text)
        retained = _source_response_records(connection, root, owner, route, response_text)
        for reference, original, _ in retained:
            if ({k: v for k, v in original.items() if k not in {'observed_at', 'route'}}
                    == {k: v for k, v in candidate.items() if k != 'route'}):
                return review_receipt(dict(original, candidate_reference=reference))
        candidate['observed_at'] = datetime.now(timezone.utc).isoformat()
        reference = _reference(candidate)
        connection.execute('INSERT OR IGNORE INTO core_completion_candidates VALUES (?,?,?)',
            (reference, key, _json(candidate)))
    return review_receipt(dict(candidate, candidate_reference=reference))


def _validate_response_review(candidate, root, owner, route):
    _require(candidate.get('kind') == _SOURCE_RESPONSE_KIND
        and candidate.get('intent_root') == str(root) and candidate.get('owner') == owner
        and _response_review_context(candidate['route']) == _response_review_context(route),
        'response-unbound', 'Explanation review must retain its exact owner, input and selected contract')


def record_response_review(source, candidate_reference, review, *, context_use_judgments=None,
                           expected_input_event_id=None, transaction=None):
    """Commit both judgments and return their receipt without echoing the draft."""
    _require(isinstance(review, dict) and set(review) == {'claims', 'response'}
        and isinstance(review['claims'], list), 'response-assessment-required',
        'One review must contain claims and response judgments')
    core, root, owner = _coordinates(source)
    if context_use_judgments is not None:
        _require(isinstance(expected_input_event_id, str) and bool(expected_input_event_id),
            'input-unbound', 'Post-use judgments require the exact current intake event')
        from response_context_use import record_use_judgments
        with core._transaction(root, transaction=transaction) as connection:
            result = record_response_review(source, candidate_reference, review, transaction=connection)
            judgment_receipt = record_use_judgments(source, expected_input_event_id,
                context_use_judgments, transaction=connection)
            return dict(result, context_use_judgments=judgment_receipt)
    with core._transaction(root, transaction=transaction) as connection:
        candidate = _stored(connection, 'core_completion_candidates', candidate_reference)
        judgment = review['response']
        if candidate.get('schema_version') == 'completion-assessment-candidate.v1':
            _require(candidate.get('response_binding') is not None, 'response-unbound',
                'Prepare the complete response draft before recording its review')
            result = record_assessment(source, candidate_reference, review['claims'],
                response_judgment=judgment, transaction=connection)
            supported = all(j['verdict'] == 'supported' for j in review['claims'] + [judgment])
            result['status'] = 'reviewed' if supported else 'unsupported'
            return review_receipt(result)
        native = candidate.get('kind') == _NATIVE_RESPONSE_KIND
        route = candidate.get('route')
        if native:
            _validate_native_response(candidate, connection, root, owner)
        else:
            _validate_response_review(candidate, root, owner, route)
        _validate_claim_judgments(candidate, review['claims'])
        _require(_valid_judgment(judgment, candidate['response_binding']['binding_id'],
            {s['source_id'] for s in candidate['sources']}), 'response-assessment-required',
            'Review the complete explanation against identified sources with an explicit rationale')
        # These judgments concern the immutable bytes shown to both reviewers.
        # Current lookup independently checks whether their live sources changed.
        payload = dict(kind=candidate['kind'], candidate_reference=candidate_reference,
            judgments=review['claims'], judgment=judgment)
        key = candidate_reference if native else _response_review_key(root, owner, route, candidate['response_binding']['text'])
        previous = [row[0] for row in connection.execute('''SELECT reference FROM core_completion_responses
            WHERE json_extract(payload_json, '$.candidate_reference')=?''', (candidate_reference,))]
        reference = _reference(payload)
        _require(not previous or (len(previous) == 1 and previous[0] == reference),
            'assessment-conflict', 'This explanation candidate already has an immutable review')
        if not previous:
            _require_record_sources(candidate, review['claims'] + [judgment])
        connection.execute('INSERT OR IGNORE INTO core_completion_responses VALUES (?,?,?)',
            (reference, key, _json(payload)))
    supported = all(j['verdict'] == 'supported' for j in review['claims'] + [judgment])
    result = dict(response_reference=reference, status='reviewed' if supported else 'unsupported')
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


def prepare_assessment(source, *, proof, verified_at, source_paths, reuse_candidate=None,
                       work_unit_id=None, response_text=None, transaction=None,
                       scope_reference=None):
    """Snapshot explicit observation paths and canonical conditions for review.

    Native answer producers use prepare_response_review and record_response_review;
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
        scope = (_stored_response_scope(connection, scope_reference, root, owner)
            if scope_reference is not None else None)
        if scope is None:
            contract, event, selected = _current_contract(core, root, owner, connection, work_unit_id)
        else:
            contract = _scope_contract(scope, work_unit_id)
            event, selected = scope['input_binding']['input_event_id'], scope['route']['selected_criteria']
        _schema(connection)
        if reuse_candidate:
            old = _stored(connection, 'core_completion_candidates', reuse_candidate)
            _require(old['contract']['owner'] == owner and old['intent_root'] == str(root)
                and old['contract']['work_unit_id'] == contract['work_unit_id'] and not source_paths
                and old['verified_at'] == verified_at, 'assessment-invalid', 'Reuse preserves original owner, sources and time')
            sources = _record_source_references(old['sources'], root, owner, reuse_candidate)
            if proof.strip() == old['proof'].strip():
                proof = old['proof']
        else:
            try:
                sources = _scope_sources(scope, source_paths) if scope is not None else _reference_sources(source_paths)
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
            reuse_candidate=reuse_candidate)
        if response_text is not None:
            response = dict(text=response_text, proof_digest=_digest(proof))
            if report_only:
                route = scope['route'] if scope is not None else core.read_response_route(root=root, **owner, transaction=connection)
                _require(bool(reuse_candidate) and old['proof'] == proof and old['contract'] == contract,
                    'response-unbound', 'An explanation must reuse an original reviewed proof under its current owned contract')
                original = lookup_assessment(source, contract, proof, verified_at, transaction=connection,
                    historical=scope is not None)
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
        and (judgment['verdict'] != 'supported' or bool(judgment['source_ids'])))


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
        _require(candidate.get('schema_version') == 'completion-assessment-candidate.v1'
            and candidate.get('intent_root') == str(root) and candidate.get('contract', {}).get('owner') == owner
            and isinstance(candidate.get('input_event_id'), str) and bool(candidate['input_event_id']),
            'owner-mismatch', 'Review must retain its original Core owner, input and contract')
        contract, event = candidate['contract'], candidate['input_event_id']
        source_ids = {s['source_id'] for s in candidate['sources']}
        _validate_claim_judgments(candidate, judgments)
        response = candidate.get('response_binding')
        _require((response is None and response_judgment is None)
            or (response is not None and _valid_judgment(response_judgment, response['binding_id'], source_ids)),
            'response-assessment-required', 'The review must cover the complete response as well as its claim bindings')
        # Recording a judgment preserves its original snapshots; it does not
        # attest that the same proof satisfies the latest work or source state.
        key = _lookup_key(contract, candidate['proof'], candidate['verified_at'])
        previous = connection.execute('SELECT reference FROM core_completion_assessments WHERE lookup_key=?', (key,)).fetchall()
        if previous:
            old = _stored(connection, 'core_completion_assessments', previous[0][0])
            original_candidate = _stored(connection, 'core_completion_candidates', old['candidate_reference'])
            _require('candidate' not in old or old['candidate'] == original_candidate,
                'assessment-invalid', 'Embedded candidate differs from its authenticated reference')
            if response is None:
                _require(old['candidate_reference'] == candidate_reference and old['judgments'] == judgments,
                         'assessment-conflict', 'An immutable assessment already exists for this original proof')
            else:
                # A later response review retains the accepted observations and
                # proof verdicts. Its new rationale/wording is a separate record.
                def decisions(rows):
                    return {j['binding_id']: (j['verdict'], sorted(j['source_ids'])) for j in rows}
                _require(_same_sources(original_candidate['sources'], candidate['sources'])
                    and original_candidate['bindings'] == candidate['bindings']
                    and decisions(old['judgments']) == decisions(judgments),
                    'assessment-conflict', 'Response review cannot replace original observations or proof judgments')
            reference = previous[0][0]
        else:
            _require_record_sources(candidate, judgments + ([response_judgment] if response_judgment else []))
            original = {k: v for k, v in candidate.items() if k != 'response_binding'}
            original_reference = _reference(original)
            connection.execute('INSERT OR IGNORE INTO core_completion_candidates VALUES (?,?,?)',
                (original_reference, key, _json(original)))
            payload = dict(candidate_reference=original_reference,
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
            'assessment-required', 'The response model must review the original proof and record its judgments through completion_assessment.py')
        rows = connection.execute('SELECT reference FROM core_completion_assessments WHERE lookup_key=?',
            (_lookup_key(contract, proof, verified_at),)).fetchall()
        _require(len(rows) == 1, 'assessment-required', 'One exact original Core assessment is required')
        payload = _stored(connection, 'core_completion_assessments', rows[0][0])
        candidate = _stored(connection, 'core_completion_candidates', payload['candidate_reference'])
        _require(('candidate' not in payload or candidate == payload['candidate']) and candidate['intent_root'] == str(root),
                 'assessment-invalid', 'Review candidate authority differs')
        issues = {} if historical else _source_issues(candidate, {sid for j in payload['judgments'] for sid in j['source_ids']})
        result = VerifiedAssessment(payload, _SEAL, issues, candidate=candidate)
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
                 'assessment-required', 'The response model must review the original proof and record its judgments through completion_assessment.py')
        exact, equivalent = [], []
        comparison = review_comparison_text(proof)
        for contract in contracts:
            rows = connection.execute('''SELECT a.reference FROM core_completion_assessments a
                JOIN core_completion_candidates c ON c.reference=json_extract(a.payload_json, '$.candidate_reference')
                WHERE json_extract(c.payload_json, '$.contract.owner.platform')=? AND
                json_extract(c.payload_json, '$.contract.owner.session_id')=? AND
                json_extract(c.payload_json, '$.contract.work_unit_id')=?''',
                (owner['platform'], owner['session_id'], contract['work_unit_id']))
            for reference, in rows:
                assessment = _stored(connection, 'core_completion_assessments', reference)
                candidate = _stored(connection, 'core_completion_candidates', assessment['candidate_reference'])
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
            'response-assessment-required', 'The response model must review this complete response and record its judgment')
        if report_only:
            reviewed_source = _lookup_source_response(core, root, owner, connection, text)
            if reviewed_source is not None:
                return reviewed_source
            _, route = _current_response_context(core, root, owner, connection)
            rows = connection.execute('''SELECT r.reference FROM core_completion_responses r
                JOIN core_completion_candidates c ON c.reference=json_extract(r.payload_json, '$.candidate_reference')
                WHERE trim(json_extract(c.payload_json, '$.response_binding.text'), ?)=?
                AND json_extract(c.payload_json, '$.response_binding.purpose')='explanation'
                AND json_extract(c.payload_json, '$.input_event_id')=?
                AND json_extract(c.payload_json, '$.intent_root')=?
                AND json_extract(c.payload_json, '$.contract.owner.platform')=?
                AND json_extract(c.payload_json, '$.contract.owner.session_id')=?
                AND json_extract(c.payload_json, '$.contract.work_unit_id') IN (SELECT value FROM json_each(?))''',
                (' \t\r\n', text.strip(), route['input_event_id'], str(root),
                 owner['platform'], owner['session_id'], _json(route['selected_work_unit_ids']))).fetchall()
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
            # Only a matching completion-backed review needs an execution contract.
            contract, _, _ = _current_contract(core, root, owner, connection,
                work_unit_id=candidate['contract']['work_unit_id'])
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
            and _same_sources(candidate['sources'], assessment.candidate['sources']),
            'response-unbound', 'Reviewed response differs from its original proof and observations')
        _require(reviewed['judgment']['verdict'] == 'supported', 'response-unsupported',
                 'The recorded assistant judgment does not support this complete response')
        issues = _source_issues(candidate, set(reviewed['judgment']['source_ids']))
        _require(not issues, next(iter(issues.values()), 'source-changed'), 'Response observations changed or are unavailable')
    return assessment


def lookup_current_response_record(source, text, *, transaction=None):
    """Return exact native output coordinates after the existing Core authentication.

    Native rendering preserves every byte. The broader control-spacing lookup
    remains available to its original consumers but cannot replace native text.
    """
    core, root, owner = _coordinates(source)
    with core._read_connection(root, transaction) as connection:
        authenticated = lookup_current_response(source, text, transaction=connection)
        if isinstance(authenticated, dict):
            reference = _reference(authenticated)
            reviewed = authenticated
        else:
            rows = connection.execute('''SELECT reference FROM core_completion_responses
                WHERE json_extract(payload_json, '$.assessment_reference')=?
                AND json_extract(payload_json, '$.response_binding.text')=?''',
                (authenticated.reference, text)).fetchall()
            _require(len(rows) == 1, 'response-unbound', 'Native output requires one exact reviewed body')
            reference = rows[0][0]
            reviewed = _stored(connection, 'core_completion_responses', reference)
        candidate = _stored(connection, 'core_completion_candidates', reviewed['candidate_reference'])
        _require(candidate['response_binding']['text'] == text,
            'response-unbound', 'Native output must preserve the exact reviewed body')
    return dict(status='reviewed', response_text=text, response_reference=reference,
        candidate_reference=reviewed['candidate_reference'])


def lookup_original_response_record(source, response_reference, *, expected_input_event_id, text,
                                    transaction=None):
    """Authenticate exact reviewed bytes for their original native input.

    This read preserves historical judgments and never grants current work
    completion or permission to replace another turn's output.
    """
    core, root, owner = _coordinates(source)
    with core._read_connection(root, transaction) as connection:
        reviewed = _stored(connection, 'core_completion_responses', response_reference)
        candidate = _stored(connection, 'core_completion_candidates', reviewed['candidate_reference'])
        if candidate.get('kind') == _NATIVE_RESPONSE_KIND:
            _validate_native_response(candidate, connection, root, owner)
            event = candidate['input_event_id']
            judgments = reviewed['judgments']
            _validate_claim_judgments(candidate, judgments)
        elif candidate.get('kind') == _SOURCE_RESPONSE_KIND:
            route = candidate['route']
            _validate_response_review(candidate, root, owner, route)
            event = route['input_event_id']
            judgments = reviewed['judgments']
            _validate_claim_judgments(candidate, judgments)
        else:
            _require(candidate.get('schema_version') == 'completion-assessment-candidate.v1'
                and candidate.get('intent_root') == str(root)
                and candidate.get('contract', {}).get('owner') == owner,
                'owner-mismatch', 'Original response belongs to another owner')
            event = candidate['input_event_id']
            assessment = lookup_assessment(source, candidate['contract'], candidate['proof'],
                candidate['verified_at'], transaction=connection, historical=True)
            _require(reviewed.get('assessment_reference') == assessment.reference
                and reviewed.get('response_binding') == candidate.get('response_binding')
                and _same_sources(candidate['sources'], assessment.candidate['sources'])
                and candidate['bindings'] == assessment.candidate['bindings'],
                'response-unbound', 'Response review differs from its authenticated original claim review')
            judgments = assessment.judgments
            _validate_claim_judgments(candidate, judgments)
        response = candidate.get('response_binding')
        _require(event == expected_input_event_id and isinstance(response, dict)
            and response.get('text') == text, 'response-unbound',
            'Return only the exact original body for its original input')
        _require(_valid_judgment(reviewed.get('judgment'), response['binding_id'],
            {item['source_id'] for item in candidate['sources']})
            and all(j['verdict'] == 'supported' for j in judgments + [reviewed['judgment']]),
            'response-unsupported', 'Both original claim and whole-response reviews must support the body')
    return dict(status='reviewed', response_text=text, response_reference=response_reference,
        candidate_reference=reviewed['candidate_reference'])


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


def read_review_claims(source, candidate_reference, claim_indices):
    """Retrieve explicitly selected original claims, bound to their Core owner."""
    core, root, owner = _coordinates(source)
    _require(isinstance(claim_indices, list) and bool(claim_indices)
        and all(type(i) is int and i >= 0 for i in claim_indices)
        and len(claim_indices) == len(set(claim_indices)), 'assessment-invalid', 'Select exact original claim indices')
    with core._read_connection(root) as connection:
        candidate = _stored(connection, 'core_completion_candidates', candidate_reference)
        original_owner = candidate.get('owner') or candidate.get('contract', {}).get('owner')
        _require(candidate.get('intent_root') == str(root) and original_owner == owner,
            'owner-mismatch', 'Original claims belong to another Core owner')
        bindings = [b for b in candidate['bindings'] if b['claim_index'] in claim_indices]
        _require({b['claim_index'] for b in bindings} == set(claim_indices),
            'assessment-invalid', 'Selected original claims are unavailable')
    return bindings


def review_receipt(result):
    """Return review coordinates to the caller without echoing its original inputs."""
    receipt = {k: result[k] for k in ('status', 'candidate_reference', 'response_reference',
        'assessment_reference', 'verified_at', 'issues', 'reason') if k in result}
    if 'bindings' in result:
        receipt['bindings'] = []
        for binding in result['bindings']:
            row = {k: binding[k] for k in ('binding_id', 'claim_index') if k in binding}
            if 'criterion' in binding:
                row['criterion'] = {k: binding['criterion'][k]
                    for k in ('id', 'work_unit_id', 'contract_revision') if k in binding['criterion']}
            receipt['bindings'].append(row)
    if 'sources' in result:
        receipt['sources'] = [{k: row[k] for k in ('source_id', 'locator', 'digest') if k in row}
                              for row in result['sources']]
    if 'response_binding' in result:
        receipt['response_binding'] = {k: v for k, v in result['response_binding'].items() if k != 'text'}
    if 'context_use_judgments' in result:
        receipt['context_use_judgments'] = {k: result['context_use_judgments'][k]
            for k in ('status', 'references', 'judgment_count') if k in result['context_use_judgments']}
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    response_prepare = commands.add_parser('prepare-response')
    response_prepare.add_argument('--response-file', required=True)
    response_prepare.add_argument('--source', action='append', default=[])
    response_prepare.add_argument('--claims-file', help='JSON array of exact source-backed statements from an explanation draft')
    response_prepare.add_argument('--verified-at')
    response_prepare.add_argument('--reuse-candidate')
    response_prepare.add_argument('--work-unit-id')
    response_record = commands.add_parser('record-response')
    response_record.add_argument('--candidate', required=True)
    response_record.add_argument('--judgment-file', required=True)
    response_record.add_argument('--context-use-judgments-json')
    response_record.add_argument('--expected-input-event-id')
    claims_read = commands.add_parser('read-claims')
    claims_read.add_argument('--candidate', required=True)
    claims_read.add_argument('--index', type=int, action='append', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'read-claims':
            print(json.dumps(read_review_claims(os.environ, args.candidate, args.index), ensure_ascii=False))
            return 0
        if args.action == 'prepare-response':
            result = prepare_response_review(os.environ,
                response_text=Path(args.response_file).read_text(), source_paths=args.source,
                verified_at=args.verified_at, reuse_candidate=args.reuse_candidate,
                work_unit_id=args.work_unit_id,
                claim_statements=json.loads(Path(args.claims_file).read_text()) if args.claims_file else None)
        else:
            result = record_response_review(os.environ, args.candidate,
                json.loads(Path(args.judgment_file).read_text()),
                context_use_judgments=json.loads(args.context_use_judgments_json)
                    if args.context_use_judgments_json is not None else None,
                expected_input_event_id=args.expected_input_event_id)
        print(json.dumps(review_receipt(result), ensure_ascii=False))
    except (CompletionAssessmentError, ValueError, OSError) as exc:
        print(json.dumps(dict(status='unavailable', reason=getattr(exc, 'reason', 'assessment-unavailable'), detail=str(exc))))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
