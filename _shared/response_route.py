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
import session_intent_ledger as ledger

SCHEMA_VERSION = "response-route.v1"
ROUTING_ENUMS = {
    "intent_relation": {"new", "continuation", "accepted-continuation", "changed", "correction", "ambiguous"},
    "response_mode": {"normal", "clarification-only", "direct-response"},
    "response_order": {"causal-answer-first-after-necessary-evidence", "imperative-result-first-after-necessary-work",
                       "clarification-question-only", "resolved-intent-first"},
    "change_depth": {"minimal", "localized", "structural", "systemic"},
    "focus_layer": {"micro", "meso", "macro", "meta"},
    "verification_complexity": {"level-1", "level-2", "level-3"},
    "boundary_contract": {"required", "n/a"}, "forced_visibility": {"yes", "no"},
}
ROUTING_TEXT = {"primary_request", "causal_axis", "reason"}
CANDIDATE_KEYS = {"schema_version", "routing_surface", "work_units", "selected_work_unit_ids", "selected_criteria"}
UNIT_KEYS = {"work_unit_id", "contract_revision", "kind", "admitted", "owner"}
REFERENCE_KEYS = {"work_unit_id", "contract_revision", "criterion_id"}


class ResponseRouteError(ValueError):
    code = "response-route"


class RouteBindingError(ResponseRouteError):
    code = "route-binding"


class RouteSchemaError(ResponseRouteError):
    code = "route-schema"


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
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


def _surface(value):
    surface = _object(value, set(ROUTING_ENUMS) | ROUTING_TEXT, "routing_surface")
    for key, choices in ROUTING_ENUMS.items():
        if not isinstance(surface[key], str) or surface[key] not in choices:
            raise RouteSchemaError(f"unknown routing_surface.{key}")
    for key in ROUTING_TEXT:
        if not isinstance(surface[key], str) or not surface[key].strip() or len(surface[key]) > 500:
            raise RouteSchemaError(f"routing_surface.{key} must be a compressed nonempty summary")
    required_order = {"clarification-only": "clarification-question-only", "direct-response": "resolved-intent-first"}
    if surface["response_mode"] in required_order and surface["response_order"] != required_order[surface["response_mode"]]:
        raise RouteSchemaError("terminal response mode and response order disagree")
    return surface


def validate_response_route(state: dict[str, Any], candidate: dict[str, Any],
                            boundary_ref: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate current selection against owned criteria and immutable contracts."""
    _object(candidate, CANDIDATE_KEYS, "candidate")
    if candidate["schema_version"] != SCHEMA_VERSION:
        raise RouteSchemaError("unsupported response route schema")
    surface = _surface(candidate["routing_surface"])
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
    owned = {}
    criteria = state.get("acceptance_criteria", [])
    if not isinstance(criteria, list):
        raise RouteSchemaError("stored acceptance criteria are invalid")
    for criterion in criteria:
        if not isinstance(criterion, dict) or "work_unit_id" not in criterion:
            continue  # Historical ownership is unknown, never inferred.
        key = (*_unit_key(criterion), _identifier(criterion.get("id"), "criterion id"))
        if key in owned:
            raise RouteSchemaError("stored owned criterion is ambiguous")
        owned[key] = criterion
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
            "stored response route")
    _surface(stored["routing_surface"])
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
    seen = set()
    for criterion in stored["selected_criteria"]:
        if not isinstance(criterion, dict):
            raise RouteSchemaError("stored selected criterion is invalid")
        key = (*_unit_key(criterion), _identifier(criterion.get("criterion_id"), "criterion_id"))
        if (key in seen or key[0] not in selected or units[key[0]]["contract_revision"] != key[1]
                or criterion.get("id") != key[2] or criterion.get("admitted") is not True
                or criterion.get("status") not in ("met", "unmet")):
            raise RouteSchemaError("stored selected criterion binding is invalid")
        seen.add(key)
    route = deepcopy(stored)
    if route.get("input_event_id") != current or state.get("canonical_input_event_id") != current:
        route["status"] = "stale"
    criteria = state.get("acceptance_criteria", [])
    for selected_criterion in route["selected_criteria"]:
        matches = [c for c in criteria if isinstance(c, dict)
                   and (c.get("work_unit_id"), c.get("contract_revision"), c.get("id")) ==
                   (*_unit_key(selected_criterion), selected_criterion["criterion_id"])]
        if len(matches) != 1 or any(matches[0].get(k) != selected_criterion.get(k)
                                   for k in ("summary", "source", "admitted")):
            route["status"] = "stale"
    route["current_input_event_id"] = current
    return route


def record_response_route(root, platform, session_id, expected_input_event_id, candidate, boundary_ref, *, transaction=None):
    return ledger.record_response_route(root=Path(root), platform=platform, session_id=session_id,
        expected_input_event_id=expected_input_event_id, candidate=candidate, boundary_ref=boundary_ref, transaction=transaction)


def read_response_route(root, platform, session_id, *, expected_input_event_id=None, transaction=None):
    return ledger.read_response_route(root=Path(root), platform=platform, session_id=session_id,
        expected_input_event_id=expected_input_event_id, transaction=transaction)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--expected-input-event-id")
    parser.add_argument("--boundary-ref")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--read", action="store_true")
    group.add_argument("--candidate-json", help="compressed route candidate, never answer text")
    args = parser.parse_args(argv)
    try:
        if args.read:
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
                                           args.expected_input_event_id, candidate, args.boundary_ref)
    except (ResponseRouteError, ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": getattr(exc, "code", "route-storage"), "message": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
