#!/usr/bin/env python3
"""Strict hook output session log helpers for Ghost-ALICE."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
VISIBLE_DECISIONS = {"show", "hide", "force_show"}
AGENT_VISIBILITY_PROFILES = {"strict", "dynamic", "minimal"}
OBSERVED_DURATION_SOURCES = {"hook-runner", "tool-wrapper", "ledger", "agent-reported"}


def _safe_text(value: Any) -> str:
    text = str(value or "")
    return "".join(
        f"\\u{ord(character):04x}" if 0xD800 <= ord(character) <= 0xDFFF else character
        for character in text
    )


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        return {_safe_text(key): _safe_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_json_value(item) for item in value]
    return value


def safe_component(value: str | None, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = SAFE_COMPONENT_RE.sub("-", text)
    text = text.strip(".-")
    return text or fallback


def log_path(home: Path | None, platform: str, session_id: str) -> Path:
    base = Path(home) if home is not None else Path.home()
    return (
        base
        / ".ghost-alice"
        / "session-logs"
        / safe_component(platform)
        / safe_component(session_id)
        / "strict-hook-output.jsonl"
    )


def session_id_from_payload(payload: dict[str, Any], env: dict[str, str] | None = None) -> str:
    source_env = env or {}
    raw = (
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("conversation_id")
        or source_env.get("GHOST_ALICE_SESSION_ID")
    )
    return safe_component(str(raw) if raw is not None else None)


def _payload_digest(event: dict[str, Any]) -> str:
    if "stdin" in event:
        source = _safe_text(event.get("stdin") or "")
    else:
        safe_event = {key: value for key, value in event.items() if key != "stdin"}
        source = json.dumps(_safe_json_value(safe_event), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def visible_decision(value: str | None) -> str:
    decision = str(value or "").strip().lower()
    if decision in VISIBLE_DECISIONS:
        return decision
    return "show"


def agent_visibility_profile(value: str | None) -> str:
    profile = str(value or "").strip().lower().replace("_", "-")
    if profile in AGENT_VISIBILITY_PROFILES:
        return profile
    return "strict"


def observed_duration_s(value: Any) -> float | None:
    if value is None:
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if duration < 0:
        duration = 0.0
    return round(duration, 2)


def observed_duration_source(value: Any) -> str:
    source = str(value or "").strip().lower().replace("_", "-")
    if source in OBSERVED_DURATION_SOURCES:
        return source
    return "agent-reported"


def append_event(home: Path | None, platform: str, session_id: str, event: dict[str, Any]) -> Path:
    safe_platform = safe_component(platform)
    safe_session_id = safe_component(session_id)
    path = log_path(home=home, platform=safe_platform, session_id=safe_session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "schema_version": "strict-hook-output.v1",
        "ts": _utc_now(),
        "platform": safe_platform,
        "session_id": safe_session_id,
        "hook_id": safe_component(str(event.get("hook_id") or event.get("hook") or "unknown")),
        "event": _safe_text(event.get("event") or event.get("hook_event_name") or ""),
        "agent_visibility_profile": agent_visibility_profile(event.get("agent_visibility_profile")),
        "visible_decision": visible_decision(event.get("visible_decision")),
        "exit_code": int(event.get("exit_code", 0) or 0),
        "stdout": _safe_text(event.get("stdout") or ""),
        "stderr": _safe_text(event.get("stderr") or ""),
        "payload_digest": _payload_digest(event),
    }
    duration = observed_duration_s(event.get("observed_duration_s"))
    if duration is not None:
        row["observed_duration_s"] = duration
        row["observed_duration_source"] = observed_duration_source(event.get("observed_duration_source"))
    for optional_key in ("surface_item", "surface_items", "model_surface_output", "user_surface_output"):
        if optional_key in event:
            row[optional_key] = _safe_json_value(event[optional_key])
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path
