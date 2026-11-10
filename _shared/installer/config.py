"""Resolve the installer's explicit execution selection and effective visibility profile."""

from __future__ import annotations

VISIBILITY_PROFILES = ("strict", "dynamic", "minimal")
DEFAULT_VISIBILITY = "dynamic"
APPLY_UNSET = object()


def resolve_apply_changes(apply_changes=APPLY_UNSET, confirm=APPLY_UNSET, *, default: bool) -> bool:
    """Read the public execution keyword and its legacy alias before any effects."""
    if apply_changes is not APPLY_UNSET and confirm is not APPLY_UNSET and apply_changes != confirm:
        raise ValueError("apply_changes and confirm conflict")
    if apply_changes is not APPLY_UNSET:
        return apply_changes
    return default if confirm is APPLY_UNSET else confirm


def resolve_effective_visibility(flag: str | None) -> str:
    """The `--visibility` value when given, else `dynamic`. An existing
    `config.json` is not a default: a plain install repairs stale profiles."""
    return flag if flag else DEFAULT_VISIBILITY
