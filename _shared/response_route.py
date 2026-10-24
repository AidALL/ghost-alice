#!/usr/bin/env python3
"""Core response-route validation and receipt-bound API.

Dependencies: Python 3.11+ standard library. SQLite authority belongs to the ledger.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "session-intent-analyzer" / "scripts"))
try:
    import session_intent_ledger as ledger
except ModuleNotFoundError as exc:
    if exc.name != "session_intent_ledger":
        raise
    ledger = None

SCHEMA_VERSION = "response-route.v1"
ROUTING_ENUMS = {
    "response_mode": {"normal", "clarification-only", "direct-response"},
    "boundary_contract": {"required", "n/a"},
}
ROUTING_TEXT = {"reason"}
ROUTING_CONTROLS = {"response_mode", "boundary_contract"}
CANDIDATE_KEYS = {"schema_version", "routing_surface", "work_units", "selected_work_unit_ids", "selected_criteria"}
UNIT_KEYS = {"work_unit_id", "contract_revision", "kind", "admitted", "owner"}
REFERENCE_KEYS = {"work_unit_id", "contract_revision", "criterion_id"}
CRITERION_KEYS = {"id", "summary", "source", "status", "admitted", "work_unit_id", "contract_revision"}
CRITERION_PROOF_KEYS = {"met_completion_check_digest", "met_at"}


class ResponseRouteError(ValueError):
    code = "response-route"


class RouteBindingError(ResponseRouteError):
    code = "route-binding"


class RouteSchemaError(ResponseRouteError):
    code = "route-schema"


def _load_installed_ledger(platform):
    """Resolve code from the selected installation; never infer session identity."""
    root = os.environ.get("GHOST_ALICE_CORE_ROOT") or os.environ.get("GHOST_ALICE_SKILLS_DIR")
    if not root:
        if not platform or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in platform):
            raise RouteBindingError("Explicit installation platform is invalid")
        ghost = Path(os.environ.get("GHOST_ALICE_GHOST_ROOT", str(Path.home() / ".ghost-alice")))
        installation = json.loads((ghost / "install-state" / f"{platform}.json").read_text())
        if not isinstance(installation, dict) or installation.get("platform") != platform:
            raise RouteBindingError("Installation platform does not match --platform")
        root = installation.get("source_root")
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise RouteBindingError("The selected installation must supply an absolute Core source root")
    scripts = Path(root) / "session-intent-analyzer" / "scripts"
    if not (scripts / "session_intent_ledger.py").is_file():
        raise RouteBindingError("The selected installation has no Core ledger module")
    sys.path.insert(0, str(scripts))
    import session_intent_ledger
    return session_intent_ledger


def _object(value, keys, label, optional=()):
    if (not isinstance(value, dict) or not set(keys) <= set(value)
            or not set(value) <= set(keys) | set(optional)):
        raise RouteSchemaError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _identifier(value, label):
    if (not isinstance(value, str) or not value or len(value) > 120
            or ledger.safe_component(value, "") != value):
        raise RouteSchemaError(f"{label} must be an exact canonical identifier")
    return value


def _unit_key(value):
    unit = _identifier(value.get("work_unit_id"), "work_unit_id")
    revision = value.get("contract_revision")
    if type(revision) is not int or not 1 <= revision <= ledger.MAX_SHARED_INTEGER:
        raise RouteSchemaError("contract_revision must be a positive shared integer")
    return unit, revision


def _unique(values, label):
    try:
        valid = isinstance(values, list) and len(values) == len(set(values))
    except TypeError:
        valid = False
    if not valid:
        raise RouteSchemaError(f"{label} must be a list of unique references")
    return values


def validate_routing_surface(value):
    """Validate the route controls and the optional reason."""
    surface = _object(value, ROUTING_CONTROLS, "routing_surface", optional=ROUTING_TEXT)
    for key in ROUTING_CONTROLS:
        if not isinstance(surface[key], str) or surface[key] not in ROUTING_ENUMS[key]:
            raise RouteSchemaError(f"unknown routing_surface.{key}")
    for key in set(surface) & ROUTING_TEXT:
        if not isinstance(surface[key], str) or not surface[key].strip() or len(surface[key]) > 500:
            raise RouteSchemaError(f"routing_surface.{key} must be a compressed nonempty summary")
    return surface


def _validate_terminal_selection(surface, units, selected):
    if (surface["response_mode"] in ("direct-response", "clarification-only")
            and any(units[unit_id]["kind"] == "execution" for unit_id in selected)):
        raise RouteSchemaError("terminal response mode cannot select execution work")


def _criterion(value, *, resolved=False):
    required = CRITERION_KEYS | ({"criterion_id"} if resolved else set())
    if (not isinstance(value, dict) or not required <= set(value)
            or not set(value) <= required | CRITERION_PROOF_KEYS):
        raise RouteSchemaError("owned criterion fields are incomplete or unknown")
    key = (*_unit_key(value), _identifier(value["id"], "criterion id"))
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        raise RouteSchemaError("owned criterion summary must be a nonempty string")
    if not isinstance(value["source"], str) or ledger.canonical_source(value["source"], "") not in ledger.ACCEPTANCE_CRITERIA_SOURCES:
        raise RouteSchemaError("owned criterion source is invalid")
    if not isinstance(value["status"], str) or value["status"] not in ledger.ACCEPTANCE_CRITERIA_STATUSES:
        raise RouteSchemaError("owned criterion status is invalid")
    if type(value["admitted"]) is not bool:
        raise RouteSchemaError("owned criterion admission must be boolean")
    for field in CRITERION_PROOF_KEYS & set(value):
        if not isinstance(value[field], str) or not value[field]:
            raise RouteSchemaError(f"owned criterion {field} must be a nonempty string")
    if resolved and value["criterion_id"] != key[2]:
        raise RouteSchemaError("stored criterion local-ID alias is inconsistent")
    return key


def _owned_criteria(value):
    if not isinstance(value, list):
        raise RouteSchemaError("stored acceptance criteria must be a list")
    owned = {}
    for criterion in value:
        if not isinstance(criterion, dict) or not {"work_unit_id", "contract_revision"} & set(criterion):
            continue  # Unknown ownership remains unbound.
        key = _criterion(criterion)
        if key in owned:
            raise RouteSchemaError("stored owned criterion is ambiguous")
        owned[key] = criterion
    return owned


def _require_execution_criteria(units, selected, owned):
    for unit_id in selected:
        unit = units[unit_id]
        if unit['kind'] == 'execution' and not any(
                key[:2] == (unit_id, unit['contract_revision']) and criterion.get('admitted') is True
                for key, criterion in owned.items()):
            raise RouteSchemaError('selected execution work needs an admitted owned criterion; record the actual task condition before routing')



def _validate_continuation_feedback(value, selected, *, resolved=False):
    _object(value, {'schema_version', 'control', 'required_unprocessed'}, 'continuation feedback')
    if (value['schema_version'] != 'continuation-feedback.v1'
        or not isinstance(value['control'], str) or value['control'] not in {'active', 'human-stop'}):
        raise RouteSchemaError('Unknown current continuation feedback control')
    if not isinstance(value['required_unprocessed'], list):
        raise RouteSchemaError('Current required-unprocessed work must be an explicit list')
    admitted = {(row['work_unit_id'], row['contract_revision'], row['criterion_id']) for row in selected}
    seen = set()
    for row in value['required_unprocessed']:
        _object(row, {'criterion_refs', 'reason', 'next_information', 'source_refs', 'prerequisite'},
                'required-unprocessed condition')
        if not isinstance(row['prerequisite'], str) or row['prerequisite'] not in {'ready', 'missing'}:
            raise RouteSchemaError('A current prerequisite disposition is required')
        if any(not isinstance(row[key], str) or not row[key].strip() for key in ('reason', 'next_information')):
            raise RouteSchemaError('The model supplies current necessity and next needed information')
        if not isinstance(row['criterion_refs'], list) or not row['criterion_refs']:
            raise RouteSchemaError('Needed work must refer to selected owned criteria')
        for ref in row['criterion_refs']:
            _object(ref, REFERENCE_KEYS, 'required criterion reference')
            key = (*_unit_key(ref), _identifier(ref['criterion_id'], 'criterion_id'))
            if key not in admitted or key in seen:
                raise RouteSchemaError('Required work must be selected, admitted and nonoverlapping')
            seen.add(key)
        if not isinstance(row['source_refs'], list):
            raise RouteSchemaError('Needed original selectors must be an explicit list')
        sources = set()
        for selector in row['source_refs']:
            _object(selector, {'candidate_reference', 'source_id'} | ({'source'} if resolved else set()),
                    'required original selector')
            if any(not isinstance(selector[key], str) or not selector[key] for key in ('candidate_reference', 'source_id')):
                raise RouteSchemaError('Existing Core candidate and source identities are required')
            key = selector['candidate_reference'], selector['source_id']
            if key in sources:
                raise RouteSchemaError('Original selectors cannot be duplicated in one work condition')
            sources.add(key)
            if resolved:
                source = selector['source']
                if (not isinstance(source, dict) or source.get('source_id') != selector['source_id']
                    or source.get('storage') not in {'source-reference.v1', 'core-source-reference.v1'}
                    or 'text' in source or 'bytes_base64' in source):
                    raise RouteSchemaError('Feedback retains authenticated source coordinates, never another body')
    return value


def _continuation_original(selector, root, owner, connection):
    import base64
    import hashlib
    from completion_assessment import _stored, CompletionAssessmentError
    from source_reference import core_source_reference, verify_source, SourceReferenceError
    root = Path(root).resolve()
    try:
        candidate = _stored(connection, 'core_completion_candidates', selector['candidate_reference'])
    except CompletionAssessmentError as error:
        raise RouteBindingError('source-unverified') from error
    original_owner = candidate.get('owner') or candidate.get('contract', {}).get('owner') or candidate.get('input_binding', {})
    if (candidate.get('intent_root') != str(root)
        or any(original_owner.get(key) != owner[key] for key in ('platform', 'session_id'))):
        raise RouteBindingError('source-owner-unbound')
    matches = [source for source in candidate.get('sources', []) if source.get('source_id') == selector['source_id']]
    if len(matches) != 1:
        raise RouteBindingError('source-unverified')
    original = matches[0]
    try:
        if 'text' in original or 'bytes_base64' in original:
            raw = (base64.b64decode(original['bytes_base64'], validate=True) if 'bytes_base64' in original
                   else original['text'].encode())
            if 'sha256:' + hashlib.sha256(raw).hexdigest() != original['digest']:
                raise RouteBindingError('source-changed')
        else:
            verify_source(original)
    except SourceReferenceError as error:
        raise RouteBindingError('source-changed') from error
    except OSError as error:
        raise RouteBindingError('source-unavailable') from error
    return deepcopy(core_source_reference(original, root, owner, selector['candidate_reference']))


def authenticate_continuation_feedback(projection, root, connection):
    """Authenticate the declared originals in the existing route transaction."""
    if 'continuation_feedback' not in projection:
        return projection
    result = deepcopy(projection)
    owner = {key:result[key] for key in ('platform', 'session_id')}
    for row in result['continuation_feedback']['required_unprocessed']:
        for selector in row['source_refs']:
            selector['source'] = _continuation_original(selector, root, owner, connection)
    return result


def refresh_continuation_feedback(projection, root, connection):
    """Unavailable originals suppress only dependent feedback, retaining proof."""
    if projection.get('status') != 'ready' or 'continuation_feedback' not in projection:
        return projection
    owner = {key:projection[key] for key in ('platform', 'session_id')}
    issues = []
    for index, row in enumerate(projection['continuation_feedback']['required_unprocessed']):
        try:
            for selector in row['source_refs']:
                if _continuation_original(selector, root, owner, connection) != selector['source']:
                    raise RouteBindingError('source-unverified')
        except RouteBindingError as error:
            issues.append(dict(pointer='/continuation_feedback/required_unprocessed/'+str(index),
                               issue=str(error)))
    if issues:
        result = deepcopy(projection)
        result['continuation_feedback_issues'] = issues
        return result
    return projection


def validate_response_route(state: dict[str, Any], candidate: dict[str, Any],
                            boundary_ref: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate current selection against owned criteria and immutable contracts."""
    _object(candidate, CANDIDATE_KEYS, "candidate", optional={"continuation_feedback"})
    if candidate["schema_version"] != SCHEMA_VERSION:
        raise RouteSchemaError("unsupported response route schema")
    surface = validate_routing_surface(candidate["routing_surface"])
    if boundary_ref is not None:
        _identifier(boundary_ref, "boundary_ref")
    if surface["boundary_contract"] == "required" and boundary_ref is None:
        raise RouteSchemaError("a required boundary must have a boundary_ref")
    if not isinstance(candidate["work_units"], list):
        raise RouteSchemaError("work_units must be a list")
    registry = deepcopy(state.get("work_units", []))
    if not isinstance(registry, list):
        raise RouteSchemaError("stored work unit registry is invalid")
    units = {}
    owned = _owned_criteria(state.get("acceptance_criteria", []))
    for raw in candidate["work_units"]:
        unit = deepcopy(_object(raw, UNIT_KEYS, "work unit"))
        key = _unit_key(unit)
        if key[0] in units:
            raise RouteSchemaError("a current projection must name each work unit once")
        if unit["kind"] not in ("execution", "nonexecution") or type(unit["admitted"]) is not bool:
            raise RouteSchemaError("unit kind or admission is invalid")
        owner = _object(unit["owner"], {"platform", "session_id"}, "unit owner")
        if owner != {"platform": state.get("platform"), "session_id": state.get("session_id")}:
            raise RouteSchemaError("unit owner must match the authoritative session")
        definitions = sorted([{k: c[k] for k in ("id", "summary", "source")}
                              for (u, r, _), c in owned.items() if (u, r) == key], key=lambda c: c["id"])
        if any(not isinstance(u, dict) for u in registry):
            raise RouteSchemaError("stored work unit registry entry is invalid")
        prior = [u for u in registry if _unit_key(u) == key]
        if len(prior) > 1:
            raise RouteSchemaError("stored work unit is ambiguous")
        if prior:
            if any(prior[0].get(k) != unit[k] for k in ("kind", "owner")) or prior[0].get("criteria") != definitions:
                raise RouteSchemaError("same work unit revision cannot change kind, owner or criterion definition")
            prior[0]["admitted"] = unit["admitted"]
        else:
            revisions = [u["contract_revision"] for u in registry if u.get("work_unit_id") == key[0]]
            if revisions and key[1] <= max(revisions):
                raise RouteSchemaError("a changed contract needs a newer revision")
            registry.append(dict(unit, criteria=definitions))
        latest = max(u["contract_revision"] for u in registry if u.get("work_unit_id") == key[0])
        if key[1] != latest:
            raise RouteSchemaError("a current projection cannot revive a superseded contract revision")
        units[key[0]] = unit
    selected_ids = candidate["selected_work_unit_ids"]
    if not isinstance(selected_ids, list):
        raise RouteSchemaError("selected_work_unit_ids must be a list")
    for unit_id in selected_ids:
        _identifier(unit_id, "selected work unit")
    _unique(selected_ids, "selected_work_unit_ids")
    for unit_id in selected_ids:
        if unit_id not in units or units[unit_id]["admitted"] is not True:
            raise RouteSchemaError("selected work unit must be explicitly present and admitted")
    _validate_terminal_selection(surface, units, selected_ids)
    _require_execution_criteria(units, selected_ids, owned)
    references = candidate["selected_criteria"]
    if not isinstance(references, list):
        raise RouteSchemaError("selected_criteria must be a list")
    resolved, seen = [], set()
    for reference in references:
        _object(reference, REFERENCE_KEYS, "selected criterion")
        key = (*_unit_key(reference), _identifier(reference["criterion_id"], "criterion_id"))
        if key in seen:
            raise RouteSchemaError("duplicate selected criterion")
        seen.add(key)
        unit = units.get(key[0])
        criterion = owned.get(key)
        if (key[0] not in selected_ids or unit is None or unit["contract_revision"] != key[1]
                or criterion is None or criterion.get("admitted") is not True):
            raise RouteSchemaError("selected criterion needs an admitted current owner and admitted ledger condition")
        resolved.append(dict(deepcopy(criterion), criterion_id=key[2]))
    projection = {
        "schema_version": SCHEMA_VERSION, "status": "ready", "platform": state["platform"],
        "session_id": state["session_id"], "input_event_id": state["latest_input_event_id"],
        "boundary_ref": boundary_ref, "routing_surface": deepcopy(surface),
        "work_units": list(units.values()), "selected_work_unit_ids": deepcopy(selected_ids),
        "selected_criteria": resolved,
    }
    if 'continuation_feedback' in candidate:
        projection['continuation_feedback'] = deepcopy(_validate_continuation_feedback(
            candidate['continuation_feedback'], resolved))
    return projection, registry


def project_stored_response_route(state, expected_input_event_id=None):
    current = state.get("latest_input_event_id")
    if expected_input_event_id is not None and expected_input_event_id != current:
        raise RouteBindingError("read receipt does not match the current input event")
    stored = state.get("response_route")
    if stored is None:
        return None
    if (not isinstance(stored, dict) or stored.get("schema_version") != SCHEMA_VERSION
            or stored.get("status") not in ("ready", "stale")
            or stored.get("platform") != state.get("platform") or stored.get("session_id") != state.get("session_id")):
        raise RouteSchemaError("stored response route schema or owner is invalid")
    _object(stored, CANDIDATE_KEYS | {"status", "platform", "session_id", "input_event_id", "boundary_ref"},
            "stored response route", optional={"continuation_feedback"})
    validate_routing_surface(stored["routing_surface"])
    if not isinstance(stored["input_event_id"], str) or not stored["input_event_id"]:
        raise RouteSchemaError("stored route input event is invalid")
    if stored["boundary_ref"] is not None:
        _identifier(stored["boundary_ref"], "boundary_ref")
    if stored["routing_surface"]["boundary_contract"] == "required" and stored["boundary_ref"] is None:
        raise RouteSchemaError("stored required boundary reference is missing")
    # Validate frozen shape independently of later business-evidence mutations.
    if not all(isinstance(stored[k], list) for k in ("work_units", "selected_work_unit_ids", "selected_criteria")):
        raise RouteSchemaError("stored selection lists are invalid")
    units = {}
    for unit in stored["work_units"]:
        _object(unit, UNIT_KEYS, "stored unit")
        key = _unit_key(unit)
        if key[0] in units or unit["kind"] not in ("execution", "nonexecution") or type(unit["admitted"]) is not bool:
            raise RouteSchemaError("stored unit identity, kind or admission is invalid")
        if unit["owner"] != {"platform": state["platform"], "session_id": state["session_id"]}:
            raise RouteSchemaError("stored unit owner is invalid")
        units[key[0]] = unit
    selected = stored["selected_work_unit_ids"]
    for unit_id in selected:
        _identifier(unit_id, "selected work unit")
    _unique(selected, "stored selected_work_unit_ids")
    if any(u not in units or units[u]["admitted"] is not True for u in selected):
        raise RouteSchemaError("stored selected unit is not admitted")
    _validate_terminal_selection(stored["routing_surface"], units, selected)
    owned = _owned_criteria(state.get("acceptance_criteria", []))
    _require_execution_criteria(units, selected, owned)
    seen = set()
    for criterion in stored["selected_criteria"]:
        key = _criterion(criterion, resolved=True)
        if (key in seen or key[0] not in selected or units[key[0]]["contract_revision"] != key[1]
                or criterion.get("id") != key[2] or criterion.get("admitted") is not True
                or criterion.get("status") not in ("met", "unmet")):
            raise RouteSchemaError("stored selected criterion binding is invalid")
        seen.add(key)
    if 'continuation_feedback' in stored:
        _validate_continuation_feedback(stored['continuation_feedback'], stored['selected_criteria'], resolved=True)
    route = deepcopy(stored)
    if (route.get("input_event_id") != current or state.get("canonical_input_event_id") != current
        or state.get('current_context_input_event_id')):
        route["status"] = "stale"
    for selected_criterion in route["selected_criteria"]:
        criterion = owned.get(_criterion(selected_criterion, resolved=True))
        if criterion is None or any(criterion[k] != selected_criterion[k] for k in ("summary", "source", "admitted")):
            route["status"] = "stale"
    route["current_input_event_id"] = current
    return route


def record_response_route(root, platform, session_id, expected_input_event_id, candidate, boundary_ref, *, transaction=None,
                          legacy_criteria_binding=None, expected_revision=None,
                          context_selection=None, context_source=None, context_use_judgments=None,
                          context_selection_mode='replace'):
    if not isinstance(context_selection_mode, str) or context_selection_mode not in {'replace', 'update'}:
        raise RouteSchemaError('Context selection mode must be replace or update')
    if context_selection_mode == 'update':
        if context_selection is None:
            raise RouteBindingError('Context selection update requires an explicit selection array')
        if platform != 'codex':
            raise RouteBindingError('Source-local context selection updates currently require Codex')
    if context_selection is not None or context_use_judgments is not None:
        source = context_source if context_source is not None else dict(os.environ,
            GHOST_ALICE_SESSION_INTENT_ROOT=str(root), GHOST_ALICE_PLATFORM=platform,
            GHOST_ALICE_SESSION_ID=session_id)
        if any(source.get(key) != value for key, value in (
            ('GHOST_ALICE_SESSION_INTENT_ROOT', str(root)), ('GHOST_ALICE_PLATFORM', platform),
            ('GHOST_ALICE_SESSION_ID', session_id))):
            raise RouteBindingError('Context selection and work selection must use the same native owner')
        with ledger._transaction(Path(root), transaction) as connection:
            result = record_response_route(root, platform, session_id, expected_input_event_id,
                candidate, boundary_ref, transaction=connection,
                legacy_criteria_binding=legacy_criteria_binding, expected_revision=expected_revision)
            if context_selection is not None:
                from context_lifecycle import record_disposition
                result = dict(result, context_disposition=record_disposition(source,
                    expected_input_event_id, context_selection, transaction=connection,
                    selection_mode=context_selection_mode))
            if context_use_judgments is not None:
                from response_context_use import record_use_judgments
                result = dict(result, context_use_judgments=record_use_judgments(source,
                    expected_input_event_id, context_use_judgments, transaction=connection))
            return result
    if context_source is not None:
        raise RouteBindingError('Context source belongs to an explicit context selection')
    if legacy_criteria_binding is not None:
        _object(legacy_criteria_binding, {"work_unit_id", "contract_revision", "expected_criteria"}, "ownership binding")
        with ledger._transaction(Path(root), transaction) as connection:
            ledger.bind_legacy_acceptance_criteria(root=Path(root), platform=platform, session_id=session_id,
                expected_input_event_id=expected_input_event_id, expected_revision=expected_revision,
                transaction=connection, **legacy_criteria_binding)
            return ledger.record_response_route(root=Path(root), platform=platform, session_id=session_id,
                expected_input_event_id=expected_input_event_id, candidate=candidate, boundary_ref=boundary_ref, transaction=connection)
    if expected_revision is not None:
        raise RouteBindingError("expected-revision belongs to explicit ownership binding")
    return ledger.record_response_route(root=Path(root), platform=platform, session_id=session_id,
        expected_input_event_id=expected_input_event_id, candidate=candidate, boundary_ref=boundary_ref, transaction=transaction)


def read_response_route(root, platform, session_id, *, expected_input_event_id=None, transaction=None):
    return ledger.read_response_route(root=Path(root), platform=platform, session_id=session_id,
        expected_input_event_id=expected_input_event_id, transaction=transaction)


def main(argv=None):
    global ledger
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--expected-input-event-id")
    parser.add_argument("--boundary-ref")
    parser.add_argument("--expected-revision", type=int, help="Current ledger revision required for ownership binding")
    parser.add_argument("--bind-legacy-criteria-json",
        help="Core binds existing admitted criterion snapshots to frozen ownership, records the candidate atomically, and returns the ready route")
    parser.add_argument('--context-selection-json',
        help='Current use conclusions and remaining needs; resolved and committed with this work selection')
    parser.add_argument('--context-selection-mode', choices=('replace', 'update'), default='replace',
        help='Replace the complete selection, or explicitly update only named Codex source scopes')
    parser.add_argument('--context-use-judgments-json',
        help='Post-use keep or replace judgments, committed with this work selection')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--read", action="store_true")
    group.add_argument("--candidate-json", help="compressed route candidate, never answer text")
    args = parser.parse_args(argv)
    try:
        if ledger is None:
            ledger = _load_installed_ledger(args.platform)
        if args.read:
            if (args.bind_legacy_criteria_json is not None or args.expected_revision is not None
                or args.context_selection_json is not None or args.context_use_judgments_json is not None
                or args.context_selection_mode != 'replace'):
                raise RouteBindingError("read cannot bind criterion ownership")
            result = read_response_route(args.root, args.platform, args.session_id,
                                         expected_input_event_id=args.expected_input_event_id)
        else:
            bound = os.environ.get("CODEX_THREAD_ID", "") if args.platform == "codex" else ""
            bound = bound or os.environ.get("GHOST_ALICE_SESSION_ID", "")
            if bound and bound != args.session_id:
                raise RouteBindingError("session identity mismatch with the bound host session")
            try:
                candidate = json.loads(args.candidate_json)
            except ValueError as exc:
                raise RouteSchemaError("candidate-json must be valid JSON") from exc
            result = record_response_route(args.root, args.platform, args.session_id,
                args.expected_input_event_id, candidate, args.boundary_ref,
                legacy_criteria_binding=json.loads(args.bind_legacy_criteria_json) if args.bind_legacy_criteria_json is not None else None,
                expected_revision=args.expected_revision,
                context_selection=json.loads(args.context_selection_json) if args.context_selection_json is not None else None,
                context_selection_mode=args.context_selection_mode,
                context_use_judgments=json.loads(args.context_use_judgments_json) if args.context_use_judgments_json is not None else None)
    except (ResponseRouteError, ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": getattr(exc, "code", "route-storage"), "message": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
