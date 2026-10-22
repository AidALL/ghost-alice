"""Definitions that the installer and the hook runtime read together.

One owner for the names both layers need: hook ids and host event names, the
gateway provider name, and the privileged adapter allowlist. Standard library
only; nothing here imports an installer or runtime module, so neither layer has
to import the other to read a definition.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

# The Codex model provider name the context gateway registers and the hooks look for.
PROVIDER = 'ghost_alice_context'

# Addon ids, adapter ids and hook ids share one safe-name rule.
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
    """Raised when an addon manifest or allowlist cannot be safely used."""


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


# ── Privileged adapter allowlist ──────────────────────────
#
# The official file is core-owned repository data; the user file is a local
# machine-owner trust record. Addon manifests request adapter ids only and never
# supply implementation fields. The hook runtime reads the same allowlist to
# verify a registered adapter before running it.

CORE_PRIVILEGED_ADAPTER_ALLOWLIST: dict[str, dict[str, Any]] = {}
PRIVILEGED_ADAPTER_ALLOWLIST_SCHEMA_VERSION = 1
CORE_PRIVILEGED_ADAPTER_ALLOWLIST_PATH = (
    Path(os.environ.get("GHOST_ALICE_CORE_ROOT") or Path(__file__).resolve().parents[1]) / "skill-catalog" / "privileged-adapters.json"
)
USER_PRIVILEGED_ADAPTER_ALLOWLIST_REL = Path(".ghost-alice") / "privileged-adapters.json"
_DEFAULT_USER_PRIVILEGED_ADAPTER_ALLOWLIST = object()
PRIVILEGED_ADAPTER_REQUIRED_FIELDS = frozenset({
    "adapter_id",
    "allowed_addon_id",
    "expected_skill_name",
    "events",
    "script_rel_by_event",
    "hook_id_by_event",
    "args_policy",
    "marker_template",
    "runner_namespace",
    "source_policy",
    "hash_policy",
    "uninstall_matcher",
})
PRIVILEGED_ADAPTER_ALLOWED_EVENTS = frozenset({
    "on_user_prompt",
    "pre_tool_use",
    "post_tool_use",
    "on_agent_stop",
    "on_session_start",
})


def render_adapter_template(
    template: str,
    *,
    adapter_id: str,
    hook_id: str,
    context: Path | None,
    field: str,
) -> str:
    where = f"{context}: " if context is not None else ""
    # Safe substitution: honor only the two whitelisted placeholders. A leftover brace means an unsupported field; str.format attribute/index access (e.g. {x.__class__}, {0}) is never reachable.
    rendered = template.replace("{adapter_id}", adapter_id).replace("{hook_id}", hook_id)
    if "{" in rendered or "}" in rendered:
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} {field} template has unsupported fields "
            "(only {adapter_id} and {hook_id} are allowed)"
        )
    if not isinstance(rendered, str) or not rendered.strip():
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} {field} template rendered empty"
        )
    return rendered


def adapter_marker(adapter_id: str, hook_id: str) -> str:
    return f"[adapter:{adapter_id}] {hook_id}"


def _adapter_runner_id(adapter_id: str, hook_id: str) -> str:
    # Canonical single source of truth for the privileged adapter runner id.
    return f"adapter-{adapter_id}-{hook_id}"


def adapter_marker_from_spec(
    spec: dict[str, Any],
    adapter_id: str,
    hook_id: str,
    *,
    context: Path | None,
) -> str:
    marker = render_adapter_template(
        spec["marker_template"],
        adapter_id=adapter_id,
        hook_id=hook_id,
        context=context,
        field="marker_template",
    )
    expected = adapter_marker(adapter_id, hook_id)
    if marker != expected:
        where = f"{context}: " if context is not None else ""
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} marker_template must render {expected!r}"
        )
    return marker


def adapter_runner_id_from_spec(
    spec: dict[str, Any],
    adapter_id: str,
    hook_id: str,
    *,
    context: Path | None,
) -> str:
    runner_id = render_adapter_template(
        spec["runner_namespace"],
        adapter_id=adapter_id,
        hook_id=hook_id,
        context=context,
        field="runner_namespace",
    )
    if not SAFE_ID_RE.fullmatch(runner_id):
        where = f"{context}: " if context is not None else ""
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} runner_namespace must render a safe hook id"
        )
    expected = _adapter_runner_id(adapter_id, hook_id)
    if runner_id != expected:
        where = f"{context}: " if context is not None else ""
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} runner_namespace must render {expected!r} "
            "(single source of truth with the spec=None runner id)"
        )
    return runner_id


def privileged_adapter_runner_id(
    adapter_id: str,
    hook_id: str,
    spec: dict[str, Any] | None = None,
    *,
    context: Path | None = None,
) -> str:
    if spec is None:
        return _adapter_runner_id(adapter_id, hook_id)
    return adapter_runner_id_from_spec(spec, adapter_id, hook_id, context=context)


def validate_privileged_adapter_script_rel(
    script_rel: str,
    adapter_id: str,
    event: str,
    context: Path | None,
) -> None:
    where = f"{context}: " if context is not None else ""
    rel = Path(script_rel)
    if rel.is_absolute() or script_rel.startswith(("/", "\\")):
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} script for {event!r} must be relative"
        )
    if any(ch in script_rel for ch in (";", "|", "&", "`", "$", "\n", "\0")):
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} script for {event!r} must be a contained path"
        )
    # A backslash is never valid in a contained POSIX adapter path; rejecting it closes the posix-evasion where "..\\..\\x" is a single rel.part.
    if "\\" in script_rel:
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} script for {event!r} must be a contained path"
        )
    # Detect ".." under both separator styles for defense in depth.
    segments = re.split(r"[\\/]+", script_rel)
    if ".." in segments or ".." in rel.parts or os.path.normpath(script_rel) in ("", "."):
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} script for {event!r} escapes expected skill source"
        )


def _adapter_allowlist(
    allowlist: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    if allowlist is not None:
        return allowlist
    loaded = load_privileged_adapter_allowlist()
    for adapter_id, spec in CORE_PRIVILEGED_ADAPTER_ALLOWLIST.items():
        if adapter_id in loaded:
            raise AddonManifestError(f"duplicate privileged adapter id {adapter_id!r} in core allow-list overlay")
        loaded[adapter_id] = spec
    return loaded


def privileged_adapter_spec(
    adapter_id: str,
    *,
    allowlist: dict[str, dict[str, Any]] | None = None,
    context: Path | None = None,
) -> dict[str, Any]:
    registry = _adapter_allowlist(allowlist)
    spec = registry.get(adapter_id)
    where = f"{context}: " if context is not None else ""
    if not isinstance(spec, dict):
        raise AddonManifestError(f"{where}unknown privileged adapter id {adapter_id!r}")
    missing = sorted(PRIVILEGED_ADAPTER_REQUIRED_FIELDS - set(spec))
    if missing:
        raise AddonManifestError(f"{where}privileged adapter {adapter_id!r} missing core allow-list fields: {missing}")
    if spec.get("adapter_id") != adapter_id:
        raise AddonManifestError(f"{where}privileged adapter {adapter_id!r} has mismatched adapter_id")
    for key in ("allowed_addon_id", "expected_skill_name"):
        value = spec.get(key)
        if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
            raise AddonManifestError(f"{where}privileged adapter {adapter_id!r} {key} must match {SAFE_ID_RE.pattern}")
    events = spec.get("events")
    if not isinstance(events, list) or not events or not all(isinstance(event, str) and event for event in events):
        raise AddonManifestError(f"{where}privileged adapter {adapter_id!r} events must be a non-empty string array")
    script_rel_by_event = spec.get("script_rel_by_event")
    hook_id_by_event = spec.get("hook_id_by_event")
    if not isinstance(script_rel_by_event, dict) or not isinstance(hook_id_by_event, dict):
        raise AddonManifestError(
            f"{where}privileged adapter {adapter_id!r} script_rel_by_event and hook_id_by_event must be objects"
        )
    for key in ("args_policy", "marker_template", "runner_namespace", "source_policy", "hash_policy", "uninstall_matcher"):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise AddonManifestError(f"{where}privileged adapter {adapter_id!r} {key} must be a non-empty string")
    for event in events:
        if event not in PRIVILEGED_ADAPTER_ALLOWED_EVENTS:
            raise AddonManifestError(
                f"{where}privileged adapter {adapter_id!r} event {event!r} is not a known hook intent"
            )
        script_rel = script_rel_by_event.get(event)
        hook_id = hook_id_by_event.get(event)
        if not isinstance(script_rel, str) or not script_rel.strip():
            raise AddonManifestError(f"{where}privileged adapter {adapter_id!r} missing script for event {event!r}")
        if not isinstance(hook_id, str) or not SAFE_ID_RE.fullmatch(hook_id):
            raise AddonManifestError(
                f"{where}privileged adapter {adapter_id!r} hook id for event {event!r} must match {SAFE_ID_RE.pattern}"
            )
        validate_privileged_adapter_script_rel(script_rel, adapter_id, event, context)
        adapter_marker_from_spec(spec, adapter_id, hook_id, context=context)
        adapter_runner_id_from_spec(spec, adapter_id, hook_id, context=context)
    required_policies = {
        "args_policy": "no_args",
        "source_policy": "expected_skill_source",
        "hash_policy": "content_hash",
        "uninstall_matcher": "exact_marker_runner",
    }
    for key, expected in required_policies.items():
        if spec[key] != expected:
            raise AddonManifestError(
                f"{where}privileged adapter {adapter_id!r} {key} must be {expected!r}"
            )
    return spec


def read_privileged_adapter_allowlist_file(
    path: Path,
    *,
    expected_trust_owner: str,
) -> dict[str, dict[str, Any]]:
    data = read_json_object(path)
    if data.get("schema_version") != PRIVILEGED_ADAPTER_ALLOWLIST_SCHEMA_VERSION:
        raise AddonManifestError(
            f"{path}: schema_version must be {PRIVILEGED_ADAPTER_ALLOWLIST_SCHEMA_VERSION}"
        )
    if data.get("trust_owner") != expected_trust_owner:
        raise AddonManifestError(f"{path}: trust_owner must be {expected_trust_owner!r}")
    adapters = data.get("adapters")
    if not isinstance(adapters, dict):
        raise AddonManifestError(f"{path}: adapters must be an object")

    out: dict[str, dict[str, Any]] = {}
    for adapter_id, spec in adapters.items():
        if not isinstance(adapter_id, str) or not SAFE_ID_RE.fullmatch(adapter_id):
            raise AddonManifestError(f"{path}: adapter id {adapter_id!r} must match {SAFE_ID_RE.pattern}")
        if not isinstance(spec, dict):
            raise AddonManifestError(f"{path}: adapter {adapter_id!r} spec must be an object")
        candidate = dict(spec)
        privileged_adapter_spec(adapter_id, allowlist={adapter_id: candidate}, context=path)
        out[adapter_id] = candidate
    return out


def merge_allowlist_file(
    merged: dict[str, dict[str, Any]],
    path: str | Path | None,
    *,
    expected_trust_owner: str,
) -> None:
    if path is None:
        return
    allowlist_path = Path(path).expanduser()
    if not allowlist_path.exists():
        return
    entries = read_privileged_adapter_allowlist_file(
        allowlist_path,
        expected_trust_owner=expected_trust_owner,
    )
    for adapter_id, spec in entries.items():
        if adapter_id in merged:
            raise AddonManifestError(f"{allowlist_path}: duplicate privileged adapter id {adapter_id!r}")
        merged[adapter_id] = spec


def default_user_privileged_adapter_allowlist_path() -> Path:
    if ghost := os.environ.get("GHOST_ALICE_GHOST_ROOT"):
        return Path(ghost)/"privileged-adapters.json"
    home = os.environ.get("HOME")
    if home:
        return Path(home).expanduser() / USER_PRIVILEGED_ADAPTER_ALLOWLIST_REL
    return Path.home() / USER_PRIVILEGED_ADAPTER_ALLOWLIST_REL


def load_privileged_adapter_allowlist(
    *,
    official_path: str | Path | None = CORE_PRIVILEGED_ADAPTER_ALLOWLIST_PATH,
    user_path: Any = _DEFAULT_USER_PRIVILEGED_ADAPTER_ALLOWLIST,
) -> dict[str, dict[str, Any]]:
    """Load privileged adapter specs from separated trust roots."""
    merged: dict[str, dict[str, Any]] = {}
    merge_allowlist_file(merged, official_path, expected_trust_owner="core")
    resolved_user_path = (
        default_user_privileged_adapter_allowlist_path()
        if user_path is _DEFAULT_USER_PRIVILEGED_ADAPTER_ALLOWLIST
        else user_path
    )
    merge_allowlist_file(merged, resolved_user_path, expected_trust_owner="machine-owner")
    return merged
