"""Select completion routes from supplied assessments; perform no verification or writes.

Input contracts (JSON-compatible dictionaries):
* context: platform, session_id, input_event_id, response_route (the existing
  read_response_route projection), verification_plan.checks. Each planned check
  has check_id, work_unit_id, contract_revision, criterion_id.
* claim_scope: text, phase (final/before_tool), optional work_unit_ids narrowing
  the current selection, optional consequential (the assistant's claim-scope judgment).
* evidence_state: failure_kind (none/recording/authority/integrity/contract/
  unknown), criteria. Each assessment has the same qualified criterion key,
  validity (valid/invalid/unknown), relevant_change (none/changed/unknown),
  check_ids (planned checks covered), proof_ref, verified_at, core_receipt. The
  caller supplies these assessments. This selector checks their structure;
  completion_assessment.py authenticates recorded assistant judgments separately.

Output completion-route.v1 contains evidence_route, frozen selected
units/criteria, per-unit/criterion decisions, required_completion_check,
route_binding and reason. Per-criterion decisions are authoritative; aggregate
priority is reopen_cause > fresh_affected > owner_verification >
recording_repair_only > reuse > no_external_check. The aggregate is a summary,
never an instruction to rerun every unit. ``check_ids`` is affected work to run;
``required_check_ids`` preserves the supplied verification_plan.checks for that
criterion. Missing administrative receipts retain valid business proof with no
business checks selected.
No result grants permission, marks a criterion met, or verifies domain truth.
Evidence dates are retained verbatim and are never compared with input age.
"""

from __future__ import annotations

from copy import deepcopy

from completion_check_validator import (
    looks_like_completion_claim, extract_all_control_blocks,
    validate_completion_text,
)
from response_purpose import NONCLOSING_PURPOSES, response_purpose
from response_route import RouteSchemaError, validate_routing_surface

SCHEMA_VERSION = "completion-route.v1"
_EVIDENCE_PRIORITY = ("no_external_check", "reuse", "recording_repair_only", "owner_verification",
                      "fresh_affected", "reopen_cause")
_REOPEN_FAILURES = {"authority", "integrity", "contract", "unknown"}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _key(value):
    value = _mapping(value)
    return value.get("work_unit_id"), value.get("contract_revision"), value.get("criterion_id")


def _qualified_key_valid(value):
    unit, revision, criterion = _key(value)
    return (isinstance(unit, str) and bool(unit.strip()) and type(revision) is int and revision > 0
            and isinstance(criterion, str) and bool(criterion.strip()))


def _aggregate(routes):
    return max(routes, key=_EVIDENCE_PRIORITY.index) if routes else "no_external_check"


def _assessment_shape_valid(value):
    return (isinstance(value, dict) and _qualified_key_valid(value)
            and value.get("validity") in ("valid", "invalid", "unknown")
            and value.get("relevant_change") in ("none", "changed", "unknown")
            and isinstance(value.get("check_ids"), list)
            and all(isinstance(check, str) and check for check in value["check_ids"])
            and all(isinstance(value.get(field), str) and value[field].strip()
                    for field in ("proof_ref", "verified_at")))


def _assessment_may_belong(value, key):
    """Scope incomplete ownership conservatively; exclude known other owners."""
    if not isinstance(value, dict):
        return True
    for field, expected in zip(("work_unit_id", "contract_revision", "criterion_id"), key):
        actual = value.get(field)
        known = type(actual) is int and actual > 0 if field == "contract_revision" else isinstance(actual, str) and bool(actual.strip())
        if known and actual != expected:
            return False
    return True


def _bound_selection(context, claim_scope):
    route = context.get("response_route")
    if route is None:
        return "missing", [], []
    if not isinstance(route, dict) or route.get("schema_version") != "response-route.v1":
        return "invalid", [], []
    try:
        response_purpose(route)
    except ValueError:
        return "invalid", [], []
    if route.get("status") != "ready":
        return "stale", [], []
    for field in ("platform", "session_id", "input_event_id"):
        if not isinstance(context.get(field), str) or not context[field] or route.get(field) != context[field]:
            return "stale", [], []
    if route.get("current_input_event_id") != context["input_event_id"]:
        return "stale", [], []
    try:
        surface = validate_routing_surface(route.get("routing_surface"))
    except RouteSchemaError:
        return "invalid", [], []
    if surface["boundary_contract"] == "required" and not route.get("boundary_ref"):
        return "invalid", [], []
    units, selected, criteria = (route.get(field) for field in
                                 ("work_units", "selected_work_unit_ids", "selected_criteria"))
    if not all(isinstance(value, list) for value in (units, selected, criteria)):
        return "invalid", [], []
    by_id = {}
    owner = dict(platform=context["platform"], session_id=context["session_id"])
    for unit in units:
        unit = _mapping(unit)
        unit_id, revision = unit.get("work_unit_id"), unit.get("contract_revision")
        if (not isinstance(unit_id, str) or not unit_id or unit_id in by_id
                or type(revision) is not int or revision < 1 or unit.get("owner") != owner
                or unit.get("kind") not in ("execution", "nonexecution") or type(unit.get("admitted")) is not bool):
            return "invalid", [], []
        by_id[unit_id] = unit
    if (not all(isinstance(u, str) and u in by_id and by_id[u]["admitted"] is True for u in selected)
            or len(selected) != len(set(selected))):
        return "invalid", [], []
    # A no-work terminal route cannot simultaneously authorize current execution.
    # Reject the inconsistent projection, retaining independent closure checks.
    if (surface["response_mode"] in ("direct-response", "clarification-only")
            and any(by_id[unit_id]["kind"] == "execution" for unit_id in selected)):
        return "invalid", [], []
    seen = set()
    for criterion in criteria:
        key = _key(criterion)
        if (not isinstance(criterion, dict) or not isinstance(key[0], str) or key[0] not in selected
                or type(key[1]) is not int or key[1] != by_id[key[0]]["contract_revision"]
                or not isinstance(key[2], str) or not key[2] or key[2] != criterion.get("id")
                or criterion.get("admitted") is not True or key in seen):
            return "invalid", [], []
        seen.add(key)
    narrowed = claim_scope.get("work_unit_ids")
    if narrowed is not None:
        if not isinstance(narrowed, list) or not all(isinstance(u, str) for u in narrowed):
            return "invalid", [], []
        selected = [u for u in selected if u in narrowed]
    return "current", [deepcopy(by_id[u]) for u in selected], [deepcopy(c) for c in criteria
                                                                    if c["work_unit_id"] in selected]


def _criterion_decision(criterion, checks, assessments, failure, malformed_container=False):
    key = _key(criterion)
    required = [c["check_id"] for c in checks if _key(c) == key]
    relevant = [p for p in assessments if _assessment_may_belong(p, key)]
    malformed = malformed_container or any(not _assessment_shape_valid(p) for p in relevant)
    matches = [p for p in relevant if _assessment_shape_valid(p) and _key(p) == key]
    decision = dict(work_unit_id=key[0], contract_revision=key[1], criterion_id=key[2],
                    required_check_ids=required, check_ids=[])
    if malformed:
        # An administrative data defect cannot authorize another business check.
        # Retain a uniquely identified, still-valid covering proof without using
        # historical or foreign assessments to fill the selected owner's gap.
        retained = [p for p in matches if p["validity"] == "valid" and p["relevant_change"] == "none"
                    and required and all(check in p["check_ids"] for check in required)]
        if len(retained) == 1:
            decision.update(proof_ref=retained[0]["proof_ref"], verified_at=retained[0]["verified_at"])
            receipt = retained[0].get("core_receipt")
            if isinstance(receipt, str) and receipt.strip():
                decision["core_receipt"] = receipt
        return dict(decision, evidence_route="reopen_cause",
                    reason="Malformed selected-owner evidence: reopen the unknown/integrity cause without a business rerun.")
    if failure in _REOPEN_FAILURES or failure not in {"none", "recording"}:
        return dict(decision, evidence_route="reopen_cause", reason="The current-session assistant diagnoses the reported authority, integrity or contract failure before retrying dependent work.")
    if len(matches) > 1:
        return dict(decision, evidence_route="reopen_cause", reason="Criterion evidence is ambiguous.")
    proof = matches[0] if matches else None
    if proof is not None and not _qualified_key_valid(proof):
        return dict(decision, evidence_route="reopen_cause", reason="Criterion proof ownership or revision is invalid.")
    if not required:
        return dict(decision, evidence_route="reopen_cause" if failure == "recording" else "owner_verification",
                    reason="The current-session assistant must identify the checks needed for this criterion using verification-before-completion.")
    if proof is None:
        return dict(decision, evidence_route="reopen_cause" if failure == "recording" else "fresh_affected",
                    check_ids=required if failure != "recording" else [], reason="Selected owner criterion has no covering proof.")
    validity, change = proof.get("validity"), proof.get("relevant_change")
    covered = proof.get("check_ids")
    if (validity not in ("valid", "invalid") or change not in ("none", "changed")
            or not isinstance(covered, list) or not all(isinstance(c, str) for c in covered)):
        return dict(decision, evidence_route="reopen_cause", reason="The current-session assistant must resolve the evidence validity or affected criteria before selecting new checks.")
    references = all(isinstance(proof.get(field), str) and proof[field].strip()
                     for field in ("proof_ref", "verified_at"))
    if not references:
        return dict(decision, evidence_route="reopen_cause", reason="Original proof and time are required for evidence reuse.")
    missing = [check for check in required if check not in covered]
    valid = validity == "valid" and change == "none" and not missing
    receipt = proof.get("core_receipt")
    if valid and (not isinstance(receipt, str) or not receipt.strip()):
        return dict(decision, evidence_route="reopen_cause", proof_ref=proof["proof_ref"], verified_at=proof["verified_at"],
                    reason="Valid business evidence is retained; reopen only the missing Core receipt cause.")
    if valid:
        return dict(decision, evidence_route="recording_repair_only" if failure == "recording" else "reuse",
                    proof_ref=proof["proof_ref"], verified_at=proof["verified_at"], core_receipt=receipt,
                    reason="Reuse valid owner coverage at its original time; closure pending recording repair."
                    if failure == "recording" else "Reuse valid owner coverage at its original time.")
    if failure == "recording":
        return dict(decision, evidence_route="reopen_cause", reason="Recording failure conflicts with the owner proof or coverage.")
    return dict(decision, evidence_route="fresh_affected", check_ids=required if change == "changed" or validity == "invalid" else missing,
                reason="The current-session assistant runs only the checks listed in check_ids, within the user's authorization.")


def select_completion_route(context, claim_scope, evidence_state):
    """Select the evidence route for the delivered text, without I/O or permission."""
    context, claim_scope, evidence_state = _mapping(context), _mapping(claim_scope), _mapping(evidence_state)
    text = claim_scope.get("text", "")
    partial_evidence = (bool(extract_all_control_blocks(text, "partial-check")) and
                        validate_completion_text(text) is None)
    # The assistant records completion intent through the owned route or an
    # explicit control block. Prose words cannot supply that judgment.
    completion_submission = looks_like_completion_claim(text)
    binding, units, criteria = _bound_selection(context, claim_scope)
    purpose = response_purpose(context["response_route"]) if binding == "current" else None
    phase = claim_scope.get("phase", "final")
    route = _mapping(context.get("response_route"))
    input_id = context.get("input_event_id")
    awaiting_route = (phase == "before_tool" and isinstance(input_id, str) and bool(input_id)
        and (binding == "missing" or (binding == "stale"
            and all(route.get(k) == context.get(k) for k in ("platform", "session_id"))
            and route.get("current_input_event_id") == input_id
            and route.get("input_event_id") != input_id)))
    completion_submission = completion_submission or (phase == "final" and purpose == "completion")
    # A selected execution unit is work context, not a final-response claim.
    # Only a structured completion or partial submission closes a response.
    nonclosing_response = (not completion_submission and not partial_evidence
                           and (phase == "final" or purpose in NONCLOSING_PURPOSES))
    checks = _mapping(context.get("verification_plan")).get("checks", [])
    malformed_plan = not isinstance(checks, list) or any(not isinstance(c, dict) or not _qualified_key_valid(c)
                                                       or not isinstance(c.get("check_id"), str) or not c["check_id"] for c in checks)
    checks = [c for c in checks if isinstance(c, dict) and isinstance(c.get("check_id"), str) and c["check_id"]] if isinstance(checks, list) else []
    assessments = evidence_state.get("criteria", [])
    malformed_container = not isinstance(assessments, list)
    assessments = assessments if isinstance(assessments, list) else []
    failure = evidence_state.get("failure_kind", "none")
    if not isinstance(failure, str):
        failure = "unknown"
    unit_decisions = []
    for unit in units:
        owned = [c for c in criteria if c["work_unit_id"] == unit["work_unit_id"]]
        decisions = [_criterion_decision(c, checks, assessments, "unknown" if malformed_plan else failure,
                                         malformed_container) for c in owned]
        routes = [d["evidence_route"] for d in decisions]
        if not routes and unit["kind"] == "execution":
            routes = ["reopen_cause" if failure != "none" else "owner_verification"]
        unit_decisions.append(dict(work_unit_id=unit["work_unit_id"], contract_revision=unit["contract_revision"],
                                   evidence_route=_aggregate(routes), criteria=decisions))
    evidence_route = _aggregate([u["evidence_route"] for u in unit_decisions])
    if failure in _REOPEN_FAILURES or failure not in {"none", "recording"}:
        evidence_route = "reopen_cause"
    elif failure == "recording" and evidence_route != "recording_repair_only":
        evidence_route = "reopen_cause"
    elif (completion_submission or claim_scope.get("consequential") is True) and evidence_route == "no_external_check":
        evidence_route = "owner_verification"
    reason = f"Evidence: {evidence_route}. Preserve owner verification requirements."
    if nonclosing_response:
        reason += " No completion submission is registered for this response; preserve unfinished work."
    if awaiting_route:
        reason += " The current input awaits route registration."
    elif binding != "current":
        reason += " Missing or stale response binding requires route repair; authorized continuation and tool permission are unchanged."
    if failure == "recording":
        reason += " Mandatory closure pending; recording alone cannot justify rerunning unchanged business checks."
    if evidence_route == "reopen_cause":
        reason += " Reopen the smallest cause and use fuller reasoning for unknown or conflicting evidence."
    return dict(schema_version=SCHEMA_VERSION, evidence_route=evidence_route,
                selected_work_unit_ids=[u["work_unit_id"] for u in units], selected_criteria=criteria,
                unit_decisions=unit_decisions, required_completion_check=completion_submission,
                required_partial_check=partial_evidence,
                route_binding=binding, response_purpose=purpose, reason=reason)
