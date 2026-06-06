#!/usr/bin/env python3
"""Project hook-internal values by work-impact projection.

Hook execution and strict audit logging are never reduced here. The primary
question is whether a hook value changes the work boundary, focus layer,
verification burden, or recovery path. User visibility follows the runtime
profile. The model-facing field remains as a compatibility hint, but it is
derived from work impact instead of being a separate profile axis.

Materialization levels, low to high:
  model_surface: omitted | marker | digest | focused | full
  user_surface:  hidden | compact | focused | full | forced

Raw detail always remains reachable via strict_log_ref, independent of the two
projection levels.

Low-usefulness values are not deleted at hook time. They are classified as
routine or audit-only unless they change focus, boundary, verification, or
recovery. Examples include duplicate reminders, clean-pass status, noop audit
rows, debug counters, correlation ids, and historical wording that does not
change the next work decision.
"""

from __future__ import annotations

from typing import Any

VALUE_KINDS = {"gate", "risk", "routing", "verification", "routine", "debug"}
EXPOSURE_CLASSES = {"forced", "essential", "focused", "routine", "audit-only"}
USER_SURFACES = ("hidden", "compact", "focused", "full", "forced")
MODEL_SURFACES = ("omitted", "marker", "digest", "focused", "full")
PROFILES = {"strict", "dynamic", "minimal"}

# exposure_class -> {profile -> (user_surface, model_surface_hint)} for the
# non-forced, non-fail-closed cases. User surface follows the visibility
# profile. Model surface hints follow work impact, so they do not vary by
# profile for routine/focused/essential values.
_TABLE: dict[str, dict[str, tuple[str, str]]] = {
    "essential": {"dynamic": ("full", "full"), "minimal": ("focused", "full")},
    "focused": {"dynamic": ("focused", "digest"), "minimal": ("compact", "digest")},
    "routine": {"dynamic": ("hidden", "omitted"), "minimal": ("hidden", "omitted")},
    "audit-only": {"dynamic": ("hidden", "omitted"), "minimal": ("hidden", "omitted")},
}

# forced/risk/gate and failed verification reach both surfaces at the top.
_FORCED = ("forced", "full")
# fail-closed for unknown classification or unknown profile: full on both, but
# not the "forced" must-not-suppress flag (that is reserved for real risk).
_FULL_FULL = ("full", "full")


def project_surfaces(
    *,
    exposure_class: str,
    value_kind: str,
    profile: str,
    verification_failed: bool = False,
) -> tuple[str, str]:
    """Return (user_surface, model_surface) materialization levels.

    Forced/gate/risk values and failed verification reach both surfaces at the
    top (user=forced, model=full) on every profile. Unknown classification or
    unknown profile fails closed to (full, full). The strict log is never
    affected by this function.
    """
    prof = str(profile or "").strip().lower()
    klass = str(exposure_class or "").strip().lower()
    kind = str(value_kind or "").strip().lower()

    if klass == "forced" or kind in {"gate", "risk"}:
        return _FORCED
    if kind == "verification" and verification_failed:
        return _FORCED
    if klass not in EXPOSURE_CLASSES or kind not in VALUE_KINDS:
        return _FULL_FULL
    if prof not in PROFILES:
        return _FULL_FULL
    if prof == "strict":
        return ("full", _model_surface_for_work_impact(klass))
    by_profile = _TABLE.get(klass)
    if by_profile is None:
        return _FULL_FULL
    return by_profile.get(prof, _FULL_FULL)


def work_impact(
    *,
    exposure_class: str,
    value_kind: str,
    verification_failed: bool = False,
) -> str:
    """Classify how a hook value can affect work quality."""
    klass = str(exposure_class or "").strip().lower()
    kind = str(value_kind or "").strip().lower()
    if klass == "forced" or kind in {"gate", "risk"}:
        return "interrupts-work"
    if kind == "verification" and verification_failed:
        return "changes-verification"
    if klass not in EXPOSURE_CLASSES or kind not in VALUE_KINDS:
        return "fail-closed"
    if klass == "essential":
        return "changes-work-judgment"
    if klass == "focused":
        return "changes-focus-or-boundary"
    if klass == "routine":
        return "routine-noise"
    return "audit-only"


def _model_surface_for_work_impact(exposure_class: str) -> str:
    impact = work_impact(
        exposure_class=exposure_class,
        value_kind="routine",
        verification_failed=False,
    )
    if impact in {"interrupts-work", "changes-verification", "fail-closed", "changes-work-judgment"}:
        return "full"
    if impact == "changes-focus-or-boundary":
        return "digest"
    return "omitted"


def make_item(
    *,
    source_hook: str,
    value_key: str,
    value_kind: str,
    exposure_class: str,
    strict_log_ref: str,
    profile: str,
    value: str = "",
    verification_failed: bool = False,
) -> dict[str, Any]:
    """Build a surface_item with work-impact and materialization levels.

    `value` is the small structured payload (for example
    "session-intent-preflight=observed"). `strict_log_ref` always points at the
    full value in the strict log, independent of the two projection levels.
    """
    user_surface, model_surface = project_surfaces(
        exposure_class=exposure_class,
        value_kind=value_kind,
        profile=profile,
        verification_failed=verification_failed,
    )
    return {
        "source_hook": source_hook,
        "value_key": value_key,
        "value_kind": value_kind,
        "exposure_class": exposure_class,
        "work_impact": work_impact(
            exposure_class=exposure_class,
            value_kind=value_kind,
            verification_failed=verification_failed,
        ),
        "value": value,
        "user_surface": user_surface,
        "model_surface": model_surface,
        "strict_log_ref": strict_log_ref,
    }
