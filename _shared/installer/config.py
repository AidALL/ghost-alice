"""The runtime configuration value the installer decides: the effective visibility profile."""

from __future__ import annotations

VISIBILITY_PROFILES = ("strict", "dynamic", "minimal")
DEFAULT_VISIBILITY = "dynamic"


def resolve_effective_visibility(flag: str | None) -> str:
    """The `--visibility` value when given, else `dynamic`. An existing
    `config.json` is not a default: a plain install repairs stale profiles."""
    return flag if flag else DEFAULT_VISIBILITY
