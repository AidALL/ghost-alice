#!/usr/bin/env python3
"""User-facing visibility decisions for Ghost-ALICE hook output."""

from __future__ import annotations

from typing import Any

from runtime_config import canonical_agent_visibility_profile

# Profiles are user/runtime preferences: strict, dynamic, minimal.
# Decisions are per-message UI actions after the hook has already run and the
# strict session log has already captured output.
VISIBLE_DECISIONS = {"show", "hide", "force_show"}


def _normalize_hook_id(value: str | None) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _text_contains_any(text: str, needles: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(needle in normalized for needle in needles)


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

    # A hook that declared its surface is judged by real state in `context`, not by the words of its own message.
    text = "" if context.get("declared_surface") else f"{hook_id}\n{event}\n{stdout}\n{stderr}"
    if _text_contains_any(text, ('"decision":"block"', '"decision": "block"', "decision: block")):
        return "forced-verification"

    if context.get("pending_merge_undecided") or _text_contains_any(
        text,
        ("pending-merge", "undecided entries", "merge-companion precheck"),
    ):
        return "forced-pending-merge"

    if hook_id == "completion" and _text_contains_any(text, ("[completion-check]",)):
        return "forced-verification"

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

    if decision_context.get("declared_surface") == "routine":
        return {"visible_decision": "hide", "reason": "declared-routine"}

    if normalized_profile == "dynamic":
        return {"visible_decision": "show", "reason": "dynamic-default-visible"}

    return {"visible_decision": "show", "reason": "minimal-default-visible"}
