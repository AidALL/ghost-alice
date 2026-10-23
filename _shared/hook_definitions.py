"""Definitions that the installer and the hook runtime read together.

One owner for the names both layers need: hook ids, host event names and the
gateway provider name. Standard library
only; nothing here imports an installer or runtime module, so neither layer has
to import the other to read a definition.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

# The Codex model provider name the context gateway registers and the hooks look for.
PROVIDER = 'ghost_alice_context'

# Addon ids and hook ids share one safe-name rule.
SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Semantic hook intent → concrete host event name.
#   - claude/codex: UserPromptSubmit fires once when user input is submitted.
HOOK_EVENT_MAP: dict[str, dict[str, str]] = {
    "on_user_prompt": {
        "claude": "UserPromptSubmit",
        "codex":  "UserPromptSubmit",
    },
    "pre_tool_use": {
        "claude": "PreToolUse",
        "codex":  "PreToolUse",
    },
    "post_tool_use": {
        "claude": "PostToolUse",
        "codex":  "PostToolUse",
    },
    "on_agent_stop": {
        "claude": "Stop",
        "codex":  "Stop",
    },
    "on_session_start": {
        "claude": "SessionStart",
        "codex":  "SessionStart",
    },
}

# Core hooks the installer registers on every host, in report order.
CORE_HOOK_IDS = ("session-intent", "tool-checkpoint", "completion", "session-start", "session-receipt", "io-trace")
# Core hooks an older installer registered and Core no longer has. The installer removes a
# registration with one of these runner ids at every install; an addon hook may not take one.
RETIRED_CORE_HOOK_IDS = ("prompt", "pending-merge-prompt", "web-search-first")


def resolve_hook_event(intent: str, platform: str) -> str:
    """Convert a semantic intent to the concrete event name for a platform."""
    try:
        return HOOK_EVENT_MAP[intent][platform]
    except KeyError:
        raise ValueError(
            f"Unknown hook intent '{intent}' or platform '{platform}'. "
            f"Add the required mapping to HOOK_EVENT_MAP."
        )


def normalize_hook_id(value: str) -> str:
    text = (value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


class AddonManifestError(Exception):
    """Raised when an addon manifest cannot be safely used."""


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AddonManifestError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AddonManifestError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AddonManifestError(f"manifest must be an object: {path}")
    return data

