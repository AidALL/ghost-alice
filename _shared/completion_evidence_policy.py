"""Core evidence compatibility and focused dependency assessment.

Ghost-ALICE Autopilot archive code authenticates serialized records before calling these functions.
This module compares stored definitions, ownership, targets and dependencies and
consumes the assistant's recorded semantic judgments from completion_assessment.
It neither judges meaning itself nor publishes proof or schedules work.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


class CompletionEvidencePolicyError(ValueError):
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason


def _fail(reason, detail):
    raise CompletionEvidencePolicyError(reason, detail)


def _key(row):
    return row['work_unit_id'], row['contract_revision'], row['id']


def same_criterion_definition(original, current, old_contract, current_contract):
    """Compare owned criterion fields; context compatibility requires a separate Core judgment."""
    try:
        return (old_contract['owner'] == current_contract['owner']
            and old_contract['work_unit_id'] == current_contract['work_unit_id']
            and original['contract_revision'] <= current['contract_revision']
            and all(original[k] == current[k] for k in ('work_unit_id', 'id', 'summary', 'source', 'admitted')))
    except (KeyError, TypeError):
        _fail('criterion-unbound', 'Original and current owned definitions are required')


def compatible_definition(original, current, old_contract, current_contract):
    return (same_criterion_definition(original, current, old_contract, current_contract)
        and old_contract['relevant_context'] == current_contract['relevant_context'])


def compatible_criterion_keys(record, reference, current_contract, *, source=None, transaction=None):
    """Reuse changed context only after Core reviewed the exact retained evidence for that contract."""
    keys = {_key(current) for original in record['criteria'] for current in current_contract['criteria']
            if same_criterion_definition(original, current, record['contract'], current_contract)}
    if not keys or record['contract']['relevant_context'] == current_contract['relevant_context']:
        return keys
    from completion_assessment import lookup_assessment, assessment_coverage
    review = lookup_assessment(source, current_contract, record['proof'], record['verified_at'],
        transaction=transaction, historical=True)
    if review.candidate.get('reuse_evidence') != reference:
        _fail('assessment-required', 'Changed context requires a Core review of this accepted evidence reference')
    return keys & assessment_coverage(review, current_contract, record['proof'], record['verified_at'])


def assess_reference_coverage(records, current_contract, target, current_dependencies, *, allow_empty=False,
                              source=None, transaction=None, assessments=None, accepted=False):
    """Map records to current criteria using authenticated assistant judgments.

    Archive authentication does not call this policy. Callers either provide
    exact Core source coordinates or already authenticated Core result objects.
    Missing review is unavailable evidence, never an inferred supported verdict.
    Only callers that authenticated an accepted archive chain set accepted.
    Accepted records retain the original observations and recorded judgments;
    this function also checks current definitions, owner, target and dependencies.
    """
    from completion_assessment import (CompletionAssessmentError, assessment_coverage, lookup_assessment)
    try:
        current = {c['id']: c for c in current_contract['criteria']}
        coverage = {}
        for reference, record in records.items():
            if record['target'] != target:
                _fail('target-mismatch', 'Original evidence target/authority changed')
            if (record['contract']['owner'] != current_contract['owner']
                    or record['contract']['work_unit_id'] != current_contract['work_unit_id']):
                _fail('owner-mismatch', 'Original evidence belongs to another owned unit')
            relations = {(d['work_unit_id'], d['contract_revision'], d['criterion_id']): d['locators']
                for d in record['criterion_dependencies']}
            original_dependencies = {d['locator']: d['digest'] for d in record['dependencies']}
            assessment = (assessments or {}).get(reference)
            if assessment is None and source is not None:
                assessment = lookup_assessment(source, record['contract'], record['proof'], record['verified_at'],
                    transaction=transaction, historical=accepted)
            supported = assessment_coverage(assessment, record['contract'], record['proof'], record['verified_at'])
            compatible = compatible_criterion_keys(record, reference, current_contract,
                source=source, transaction=transaction)
            for original in record['criteria']:
                if _key(original) not in supported:
                    continue
                if any(current_dependencies.get(locator) != original_dependencies[locator]
                       for locator in relations[_key(original)]):
                    continue
                row = current.get(original['id'])
                if row is None or _key(row) not in compatible:
                    continue
                key = _key(row)
                if key in coverage:
                    _fail('proof-invalid', 'Overlapping evidence coverage is ambiguous')
                coverage[key] = reference
    except CompletionAssessmentError as exc:
        _fail(exc.reason, str(exc))
    except (KeyError, TypeError, AttributeError):
        _fail('proof-invalid', 'Authenticated original records and current conditions are required')
    if not coverage and not allow_empty:
        _fail('proof-invalid', 'Accepted original evidence references are required')
    return coverage


def assess_criterion_dependencies(record, evidence_reference, criterion_id):
    """Read only the original criterion's mapped bytes and judge their validity.

    Missing files are contrary evidence. Other read failures are uncertainty;
    they do not erase a separately observed mutation or assert unchanged proof.
    """
    try:
        originals = [c for c in record['criteria'] if c['id'] == criterion_id]
        if len(originals) != 1:
            _fail('criterion-unbound', 'One original qualified criterion is required')
        key = _key(originals[0])
        relations = [r for r in record['criterion_dependencies']
            if (r['work_unit_id'], r['contract_revision'], r['criterion_id']) == key]
        if len(relations) != 1:
            _fail('dependency-invalid', 'One original qualified dependency relation is required')
        paths = relations[0]['locators']
        prior = {d['locator']: d['digest'] for d in record['dependencies']}
        if (not isinstance(paths, list) or len(paths) != len(set(paths))
                or any(not isinstance(p, str) or not Path(p).is_absolute() or p not in prior for p in paths)):
            _fail('dependency-invalid', 'Original absolute dependency snapshots are required')
    except (KeyError, TypeError):
        _fail('dependency-invalid', 'Original qualified dependency relation is required')
    observations, changed, error = [], False, None
    for path in paths:
        try:
            actual = 'sha256:' + hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except FileNotFoundError:
            actual = None
        except OSError as exc:
            actual = dict(error=type(exc).__name__, errno=exc.errno)
            if error is None:
                error = exc
        if not isinstance(actual, dict):
            changed = changed or actual != prior[path]
        observations.append(dict(evidence_reference=evidence_reference, criterion_id=criterion_id,
            locator=path, observed=actual))
    return dict(observations=tuple(observations), changed=changed, error=error)
