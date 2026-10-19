"""Runtime configuration values the installer decides.

Today that is one value: the effective visibility profile. Reading and writing
`config.json` moves here when the runtime-config step is ported.
"""

from __future__ import annotations

VISIBILITY_PROFILES = ("strict", "dynamic", "minimal")
DEFAULT_VISIBILITY = "dynamic"


def resolve_effective_visibility(flag: str | None) -> str:
    """The `--visibility` value when given, else `dynamic`. An existing
    `config.json` is not a default: a plain install repairs stale profiles
    (`report.sh:13-21`)."""
    return flag if flag else DEFAULT_VISIBILITY
