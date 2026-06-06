#!/usr/bin/env python3
"""User-facing visibility decisions for Ghost-ALICE hook output."""

from __future__ import annotations

from typing import Any

from runtime_config import canonical_agent_visibility_profile
import work_impact_projection

# Profiles are user/runtime preferences: strict, dynamic, minimal.
# Decisions are per-message UI actions after the hook has already run and the
# strict session log has already captured output.
VISIBLE_DECISIONS = {"show", "hide", "force_show"}
LOW_VALUE_SIGNALS = {"routine-clean-pass", "duplicate-reminder", "noop-audit"}
INTENT_RELATIONS = {"new", "continuation", "accepted-continuation", "changed", "correction", "ambiguous"}
CHANGE_DEPTHS = {"minimal", "localized", "structural", "systemic"}
FOCUS_LAYERS = {"micro", "meso", "macro", "meta"}
VERIFICATION_COMPLEXITIES = {"level-1", "level-2", "level-3"}
BOUNDARY_CONTRACTS = {"required", "n/a"}
USER_RELEVANT_CONTEXT_REASONS = (
    ("external_tool_claim", "user-relevant-external-tool-claim"),
    ("ambiguous_intent", "user-relevant-ambiguous-intent"),
    ("intent_changed", "user-relevant-intent-changed"),
    ("high_recovery_cost", "user-relevant-high-recovery-cost"),
    ("source_selection", "user-relevant-source-selection"),
    ("evidence_quality", "user-relevant-evidence-quality"),
    ("focus_boundary_crossing", "user-relevant-focus-boundary-crossing"),
)


def project_user_surface(
    *,
    exposure_class: str,
    value_kind: str,
    profile: str,
    verification_failed: bool = False,
) -> tuple[str, str]:
    """Project one classified value through the shared surface projector."""
    return work_impact_projection.project_surfaces(
        exposure_class=exposure_class,
        value_kind=value_kind,
        profile=canonical_agent_visibility_profile(profile),
        verification_failed=verification_failed,
    )


def _normalize_hook_id(value: str | None) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _text_contains_any(text: str, needles: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(needle in normalized for needle in needles)


def _context_bool(context: dict[str, Any], key: str) -> bool:
    return bool(context.get(key))


def _surface_value(surface: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in surface:
            return surface[key]
    return None


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _normalize_yes_no(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = _normalize_token(value)
    if text in {"yes", "true", "1", "force-show", "force"}:
        return "yes"
    if text in {"no", "false", "0", ""}:
        return "no"
    return "unknown"


def _routing_surface(context: dict[str, Any]) -> dict[str, Any] | None:
    value = context.get("routing_surface")
    if value is None:
        value = context.get("routing-surface")
    return value if isinstance(value, dict) else None


def _routing_surface_forces_visibility(context: dict[str, Any]) -> bool:
    surface = _routing_surface(context)
    if not surface:
        return False
    forced = _normalize_yes_no(_surface_value(surface, "forced_visibility", "forced-visibility"))
    return forced == "yes"


def _routing_surface_reason(context: dict[str, Any]) -> str | None:
    surface = _routing_surface(context)
    if not surface:
        return None

    intent_relation = _normalize_token(_surface_value(surface, "intent_relation", "intent-relation"))
    change_depth = _normalize_token(_surface_value(surface, "change_depth", "change-depth"))
    focus_layer = _normalize_token(_surface_value(surface, "focus_layer", "focus-layer"))
    verification_complexity = _normalize_token(
        _surface_value(surface, "verification_complexity", "verification-complexity")
    )
    boundary_contract = _normalize_token(_surface_value(surface, "boundary_contract", "boundary-contract"))
    forced_visibility = _normalize_yes_no(_surface_value(surface, "forced_visibility", "forced-visibility"))

    if (
        intent_relation not in INTENT_RELATIONS
        or change_depth not in CHANGE_DEPTHS
        or focus_layer not in FOCUS_LAYERS
        or verification_complexity not in VERIFICATION_COMPLEXITIES
        or boundary_contract not in BOUNDARY_CONTRACTS
        or forced_visibility not in {"yes", "no"}
    ):
        return "routing-surface-fail-closed"

    if intent_relation == "ambiguous":
        return "routing-surface-fail-closed"

    if intent_relation == "accepted-continuation" and not _context_bool(
        surface, "accepted_continuation_grounded"
    ) and not _context_bool(surface, "accepted-continuation-grounded"):
        return "routing-surface-fail-closed"

    if boundary_contract == "required":
        return "routing-surface-focused"
    if verification_complexity == "level-3" or change_depth == "systemic" or focus_layer == "meta":
        return "routing-surface-full"
    if (
        verification_complexity == "level-2"
        or change_depth in {"localized", "structural"}
        or focus_layer in {"meso", "macro"}
        or intent_relation in {"changed", "correction"}
    ):
        return "routing-surface-focused"
    return None


def _routine_reason(context: dict[str, Any]) -> str | None:
    signal = str(context.get("signal") or "").strip().lower().replace("_", "-")
    if signal in LOW_VALUE_SIGNALS:
        return signal
    if _context_bool(context, "duplicate"):
        return "duplicate-reminder"
    if _context_bool(context, "noop_audit"):
        return "noop-audit"
    if _context_bool(context, "routine_clean_pass"):
        return "routine-clean-pass"
    return None


def _forced_reason(
    *,
    hook_id: str,
    event: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    context: dict[str, Any],
) -> str | None:
    if exit_code != 0:
        return "forced-nonzero-exit"

    text = f"{hook_id}\n{event}\n{stdout}\n{stderr}"
    decision = str(context.get("decision") or "").strip().lower()
    if decision == "deny" or _text_contains_any(
        text,
        ('"decision":"deny"', '"decision": "deny"', "decision: deny", "tool checkpoint required"),
    ):
        return "forced-action-denial"

    if _context_bool(context, "pending_merge_undecided") or _text_contains_any(
        text,
        ("pending-merge", "undecided entries", "merge-companion precheck"),
    ):
        return "forced-pending-merge"

    if _routing_surface_forces_visibility(context):
        return "forced-routing-surface"

    if _context_bool(context, "destructive") or _context_bool(context, "external_side_effect"):
        return "forced-side-effect"

    if _context_bool(context, "secret_boundary") or _context_bool(context, "security_boundary"):
        return "forced-security-boundary"

    if _context_bool(context, "failed_verification") or _context_bool(context, "unresolved_completion"):
        return "forced-verification"

    if hook_id in {"completion", "completion-reminder"} and _text_contains_any(
        text,
        ("[completion-check]", "verification failed", "unresolved completion"),
    ):
        return "forced-verification"

    return None


def _user_relevant_reason(context: dict[str, Any]) -> str | None:
    for key, reason in USER_RELEVANT_CONTEXT_REASONS:
        if _context_bool(context, key):
            return reason
    return None


def decide(
    *,
    profile: str,
    hook_id: str,
    event: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    context: dict[str, object] | None = None,
) -> dict[str, str]:
    """Return the user-facing visibility decision for one hook result."""

    normalized_profile = canonical_agent_visibility_profile(profile)
    normalized_hook_id = _normalize_hook_id(hook_id)
    decision_context: dict[str, Any] = dict(context or {})

    forced_reason = _forced_reason(
        hook_id=normalized_hook_id,
        event=event,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        context=decision_context,
    )
    if forced_reason:
        return {"visible_decision": "force_show", "reason": forced_reason}

    if normalized_profile == "strict":
        return {"visible_decision": "show", "reason": "strict-profile"}

    routing_reason = _routing_surface_reason(decision_context)
    if routing_reason:
        return {"visible_decision": "show", "reason": routing_reason}

    routine_reason = _routine_reason(decision_context)
    if routine_reason:
        return {"visible_decision": "hide", "reason": routine_reason}

    relevant_reason = _user_relevant_reason(decision_context)
    if relevant_reason:
        return {"visible_decision": "show", "reason": relevant_reason}

    if normalized_profile == "dynamic":
        return {"visible_decision": "show", "reason": "dynamic-default-visible"}

    return {"visible_decision": "show", "reason": "minimal-default-visible"}
