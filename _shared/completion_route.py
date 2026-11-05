from __future__ import annotations

from response_route import RouteSchemaError, validate_routing_surface


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _key(value):
    value = _mapping(value)
    return value.get("work_unit_id"), value.get("contract_revision"), value.get("criterion_id")


def _bound_selection(context):
    route = context.get("response_route")
    if route is None:
        return "missing"
    if not isinstance(route, dict) or route.get("schema_version") != "response-route.v1":
        return "invalid"
    if route.get("status") != "ready":
        return "stale"
    for field in ("platform", "session_id", "input_event_id"):
        if not isinstance(context.get(field), str) or not context[field] or route.get(field) != context[field]:
            return "stale"
    if route.get("current_input_event_id") != context["input_event_id"]:
        return "stale"
    try:
        surface = validate_routing_surface(route.get("routing_surface"))
    except RouteSchemaError:
        return "invalid"
    if surface["boundary_contract"] == "required" and not route.get("boundary_ref"):
        return "invalid"
    units, selected, criteria = (route.get(field) for field in
                                 ("work_units", "selected_work_unit_ids", "selected_criteria"))
    if not all(isinstance(value, list) for value in (units, selected, criteria)):
        return "invalid"
    by_id = {}
    owner = dict(platform=context["platform"], session_id=context["session_id"])
    for unit in units:
        unit = _mapping(unit)
        unit_id, revision = unit.get("work_unit_id"), unit.get("contract_revision")
        if (not isinstance(unit_id, str) or not unit_id or unit_id in by_id
                or type(revision) is not int or revision < 1 or unit.get("owner") != owner
                or unit.get("kind") not in ("execution", "nonexecution") or type(unit.get("admitted")) is not bool):
            return "invalid"
        by_id[unit_id] = unit
    if (not all(isinstance(u, str) and u in by_id and by_id[u]["admitted"] is True for u in selected)
            or len(selected) != len(set(selected))):
        return "invalid"
    seen = set()
    for criterion in criteria:
        key = _key(criterion)
        if (not isinstance(criterion, dict) or not isinstance(key[0], str) or key[0] not in selected
                or type(key[1]) is not int or key[1] != by_id[key[0]]["contract_revision"]
                or not isinstance(key[2], str) or not key[2] or key[2] != criterion.get("id")
                or criterion.get("admitted") is not True or key in seen):
            return "invalid"
        seen.add(key)
    return "current"


def select_completion_route(context):
    """Return how the current response route binds this input, without I/O or permission."""
    return dict(route_binding=_bound_selection(_mapping(context)))
