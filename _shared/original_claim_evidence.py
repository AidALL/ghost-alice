"""Core-owned validity assessment of retained original claims.

Ghost-ALICE Autopilot supplies only source-bound read-only archive primitives. Ghost-ALICE Autopilot authenticates the original accepted chain; Core assesses
current qualified definitions and met events, proof text,
and criterion-specific dependencies. Reporting preserves original context/time;
it never publishes, marks work met, selects continuation, or repeats business
verification. Archive reads share an existing SQLite snapshot. SQLite SHM reader
marks are coordination metadata, not evidence or authority writes.

The immutable v2 format and accepted-chain integrity remain Ghost-ALICE Autopilot's storage
responsibility. They cannot establish current work or claim admissibility.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
import sqlite3
from typing import Mapping

DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')


_FIELDS = {'statement', 'work_unit_id', 'contract_revision', 'criterion_id', 'proof_digest'}


class _Rejected(ValueError):
    def __init__(self, reason, validity='invalid'):
        super().__init__(reason)
        self.reason, self.validity = reason, validity


def _require(condition, reason):
    if not condition:
        raise _Rejected(reason)


def _claims_valid(claims):
    return (isinstance(claims, list) and bool(claims) and all(
        isinstance(c, dict) and set(c) == _FIELDS
        and all(isinstance(c[k], str) and c[k].strip() for k in
                ('statement', 'work_unit_id', 'criterion_id', 'proof_digest'))
        and type(c['contract_revision']) is int and c['contract_revision'] > 0
        and DIGEST.fullmatch(c['proof_digest']) for c in claims))


def _result(claim, validity, reason):
    return dict(claim, original_claims=[], original_verified_at=None,
        evidence_reference=None, application_reference=None, validity=validity, reason=reason)


def _definition(row):
    fields = ('work_unit_id', 'contract_revision', 'id', 'summary', 'source', 'admitted')
    _require(isinstance(row, dict) and type(row.get('contract_revision')) is int
        and row['contract_revision'] > 0 and row.get('admitted') is True
        and all(isinstance(row.get(k), str) and row[k].strip()
                for k in ('work_unit_id', 'id', 'summary', 'source')), 'criterion-unavailable')
    return {key: row[key] for key in fields}


def _timestamp(value):
    # Format/order validation belongs to Ghost-ALICE Autopilot archive authentication. Core uses
    # these already authenticated times only for choosing the newest report.
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _resolve_one(archive, state, events, claim, root, owner, now, *, check_dependencies=True):
    key = (claim['work_unit_id'], claim['contract_revision'], claim['criterion_id'])
    rows = state.get('acceptance_criteria')
    _require(isinstance(rows, list), 'criterion-unavailable')
    matching = [c for c in rows if (c.get('work_unit_id'),c.get('contract_revision'),c.get('id')) == key]
    _require(len(matching) == 1, 'criterion-unavailable')
    _require(key[1] == max(c['contract_revision'] for c in rows if c.get('work_unit_id') == key[0]),
             'contract-revision-stale')
    row = matching[0]
    _require(row.get('status') == 'met' and 'sha256:' + str(row.get('met_completion_check_digest', '')).removeprefix('sha256:')
             == claim['proof_digest'], 'current-proof-mismatch')
    current = dict(work_unit_id=key[0], contract_revision=key[1], owner=owner,
        criteria=[_definition(c) for c in rows if (c.get('work_unit_id'),c.get('contract_revision')) == key[:2]],
        relevant_context={})
    _require(len(current['criteria']) == len({c['id'] for c in current['criteria']}),
             'criterion-unavailable')
    met_events = [e for e in events if e.get('platform') == owner['platform'] and e.get('session_id') == owner['session_id']
        and (e.get('work_unit_id'),e.get('contract_revision'),e.get('criterion_id')) == key
        and e.get('event') in ('acceptance-criterion-met', 'acceptance-criterion-evidence-invalidated')]
    _require(met_events and met_events[-1]['event'] == 'acceptance-criterion-met'
        and 'sha256:' + str(met_events[-1].get('completion_check_digest', '')).removeprefix('sha256:') == claim['proof_digest'],
        'met-event-unbound')
    matches, assessment_issue = {}, None
    # archive.accepted_records authenticates Ghost-ALICE Autopilot's accepted record chains.
    # This resolver and completion_evidence_policy compare their definitions
    # with current admitted work, met events and proof.
    for app_ref, app, records, accepted_contract in archive.accepted_records(key[0], now):
        # Reporting describes the original accepted context. Later session-wide
        # prose does not republish that history into a new context. Current
        # admitted definitions and met proof remain strict; publication uses
        # the shared policy with its own current-context contract.
        report_contract = dict(current, relevant_context=accepted_contract['relevant_context'])
        records = {ref: record for ref, record in records.items()
                   if record['proof_digest'] == claim['proof_digest']}
        if not records:
            continue
        snapshots = {d['locator']: d['digest'] for record in records.values()
                     for d in record['dependencies']}
        try:
            compatible = _evidence_policy.assess_reference_coverage(records, report_contract,
                app['target'], snapshots, allow_empty=True, source=archive.source,
                transaction=archive.connection, accepted=True)
        except _evidence_policy.CompletionEvidencePolicyError as exc:
            assessment_issue = exc.reason
            continue
        for applied in app['selected_criteria']:
            ref = applied['evidence_reference']
            if ref not in records:
                continue
            record = records[ref]
            if (applied['criterion_id'] != key[2] or compatible.get(key) != ref
                    or record['proof_digest'] != claim['proof_digest']):
                continue
            if app['binding']['input_event_id'] != met_events[-1]['input_event_id']:
                continue
            if ref not in matches or _timestamp(app['applied_at']) > _timestamp(matches[ref][2]):
                matches[ref] = (app_ref, record, app['applied_at'])
    if not matches and assessment_issue:
        raise _Rejected(assessment_issue, 'unavailable' if assessment_issue == 'assessment-required' else 'invalid')
    _require(len(matches) == 1, 'applied-evidence-unavailable')
    ref, (app_ref, record, applied_at) = next(iter(matches.items()))
    parsed = _completion_parser.parse_original_completion_evidence(record['proof'], allow_partial=True)
    _require(key[2] in parsed['criterion_ids'], 'proof-coverage-invalid')
    original_claims = [c['claim'] for c in parsed['claims'] if c['verdict'] == 'pass' and key[2] in re.split(r'[,;\s]+', c['criterion'])]
    _require(bool(original_claims), 'proof-claims-unavailable')
    if check_dependencies:
        dependencies = _evidence_policy.assess_criterion_dependencies(record, ref, key[2])
        if dependencies['error'] is not None:
            raise _Rejected('dependency-unavailable', 'unavailable')
        _require(not dependencies['changed'], 'dependency-changed')
    result = _result(claim, 'valid' if check_dependencies else 'candidate',
        'accepted-original-evidence' if check_dependencies else 'dependencies-not-observed')
    result.update(original_claims=original_claims, original_verified_at=record['verified_at'],
                  evidence_reference=ref, application_reference=app_ref)
    if not check_dependencies:
        result['_applied_at'] = applied_at
    return result


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise _Rejected('reader-unavailable', 'unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _managed_archive_provider(source):
    # Only trusted caller configuration selects executable archive code. Claims
    # cannot supply paths, adapters, session authority, or a policy callback.
    skills = source.get('GHOST_ALICE_SKILLS_DIR') if isinstance(source, Mapping) else None
    if not isinstance(skills, str) or not skills or not Path(skills).is_absolute():
        raise _Rejected('archive-provider-unavailable', 'unavailable')
    path = Path(skills) / 'autopilot-mode/adapters/autopilot_claim_evidence.py'
    if not path.is_file():
        raise _Rejected('archive-provider-unavailable', 'unavailable')
    return _load_module(path, 'ghost_alice_claim_archive')


@contextmanager
def _snapshot(source, archive_provider=None):
    provider = archive_provider if archive_provider is not None else _managed_archive_provider(source)
    if not callable(getattr(provider, 'open_claim_archive', None)):
        raise _Rejected('archive-provider-unavailable', 'unavailable')
    # A mirrored runtime _shared directory need not contain the ledger skill.
    # Use the same trusted installation binding as Core's native assessment.
    from native_completion import _ledger
    core = _ledger(source)
    with provider.open_claim_archive(source) as archive:
        conn, root, owner = archive.connection, archive.root, archive.owner
        if not conn.execute('SELECT 1 FROM sessions WHERE platform=? AND session_id=?',
                            (owner['platform'], owner['session_id'])).fetchone():
            raise _Rejected('session-unavailable', 'unavailable')
        state = core.read_session_state(root=root, **owner, transaction=conn)
        if state.get('pending_audit_event'):
            raise _Rejected('authority-audit-pending', 'unavailable')
        events = core.read_session_events(root=root, **owner, transaction=conn)
        yield archive, state, events, root, owner, datetime.now(timezone.utc).isoformat()


def _assess(snapshot, claim, *, check_dependencies=True):
    archive, state, events, root, owner, now = snapshot
    try:
        return _resolve_one(archive, state, events, claim, root, owner, now,
                            check_dependencies=check_dependencies)
    except _Rejected as exc:
        return _result(claim, exc.validity, exc.reason)
    except FileNotFoundError:
        return _result(claim, 'invalid', 'immutable-record-missing')
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration):
        return _result(claim, 'invalid', 'immutable-evidence-invalid')


def resolve_retained_claims(source, claims, *, archive_provider=None):
    """Return original authenticated claims; the Core parser checks exact spans.

    Invalid/missing authority or unavailable observation is typed, never an
    instruction to repeat business work. This function never creates a database,
    schema, run, receipt, publication, application, or met/invalidation event.
    """
    if not _claims_valid(claims):
        return dict(status='invalid', claims=[], reason='claim-schema-invalid')
    try:
        with _snapshot(source, archive_provider) as snapshot:
            results = [_assess(snapshot, claim) for claim in claims]
    except _Rejected as exc:
        results = [_result(c, exc.validity, exc.reason) for c in claims]
    except (OSError, ImportError, sqlite3.Error):
        results = [_result(c, 'unavailable', 'authority-unavailable') for c in claims]
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        results = [_result(c, 'invalid', 'authority-invalid') for c in claims]
    status = 'invalid' if any(c['validity'] == 'invalid' for c in results) else (
        'unavailable' if any(c['validity'] == 'unavailable' for c in results) else 'valid')
    return dict(status=status, claims=results)


def list_retained_claim_candidates(source, limit=16, *, archive_provider=None):
    """Offer exact accepted statements, without observing artifact dependencies.

    Candidates are discovery hints only. The final response must pass the normal
    resolver, including current dependencies; no candidate is reported valid.
    """
    if type(limit) is not int or not 1 <= limit <= 16:
        return dict(status='invalid', claims=[], reason='candidate-limit-invalid')
    candidates = []
    try:
        with _snapshot(source, archive_provider) as snapshot:
            state = snapshot[1]
            for row in state.get('acceptance_criteria', []):
                if row.get('status') != 'met':
                    continue
                claim = dict(statement='candidate', work_unit_id=row.get('work_unit_id'),
                    contract_revision=row.get('contract_revision'), criterion_id=row.get('id'),
                    proof_digest='sha256:'+str(row.get('met_completion_check_digest', '')).removeprefix('sha256:'))
                if not _claims_valid([claim]):
                    continue
                result = _assess(snapshot, claim, check_dependencies=False)
                if result['validity'] != 'candidate':
                    continue
                for statement in result['original_claims']:
                    # Return only compact references, never the complete proof.
                    candidates.append((_timestamp(result['_applied_at']), dict(claim, statement=statement,
                        original_verified_at=result['original_verified_at'],
                        evidence_reference=result['evidence_reference'],
                        application_reference=result['application_reference'],
                        validity='candidate', reason='dependencies-not-observed')))
    except _Rejected as exc:
        return dict(status=exc.validity, claims=[], reason=exc.reason)
    except (OSError, ImportError, sqlite3.Error):
        return dict(status='unavailable', claims=[], reason='authority-unavailable')
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return dict(status='invalid', claims=[], reason='authority-invalid')
    # Establish ambiguity over the complete owned accepted set before the
    # bounded discovery view hides older references. A statement with different
    # qualified proof bindings cannot be auto-attributed safely.
    bindings = {}
    for _, row in candidates:
        identity = tuple(row[k] for k in ('work_unit_id', 'contract_revision', 'criterion_id', 'proof_digest'))
        bindings.setdefault(row['statement'], set()).add(identity)
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    selected, seen = [], set()
    for _, row in candidates:
        statement = row['statement']
        if len(bindings[statement]) != 1 or statement in seen:
            continue
        seen.add(statement)
        selected.append(row)
        if len(selected) == limit:
            break
    return dict(status='candidates', claims=selected)


_completion_parser = _load_module(Path(__file__).with_name('completion_check_validator.py'),
                                  'ghost_alice_original_proof_parser')
_evidence_policy = _load_module(Path(__file__).with_name('completion_evidence_policy.py'),
                                'ghost_alice_original_evidence_policy')
