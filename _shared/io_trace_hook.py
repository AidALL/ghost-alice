#!/usr/bin/env python3
"""PostToolUse audit hook for Ghost-ALICE.

This is the cross-platform replacement for io-trace-hook.sh. It intentionally
returns success for malformed or incomplete hook payloads so tool execution is
not blocked by audit logging.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_LINES = 10000


def _home() -> Path:
    configured = os.environ.get("HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home()


def _log_file() -> Path:
    return _home() / ".ghost-alice" / "io-trace.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_text(value: Any) -> str:
    text = str(value)
    return "".join(
        f"\\u{ord(character):04x}" if 0xD800 <= ord(character) <= 0xDFFF else character
        for character in text
    )


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = _safe_text(value)
    return text if text else fallback


def _extract(payload: dict[str, Any]) -> dict[str, str]:
    tool = _text(payload.get("tool_name") or payload.get("tool"), "unknown")
    session = _text(payload.get("session_id") or payload.get("sessionId"), "unknown")
    tool_input = _as_dict(payload.get("tool_input"))
    path = "n/a"
    pattern = ""

    if tool in {"Read", "Edit", "Write"}:
        path = _text(tool_input.get("file_path"), "n/a")
    elif tool in {"Grep", "Glob"}:
        path = _text(tool_input.get("path"), "cwd")
        pattern = _text(tool_input.get("pattern"))
    elif tool == "Bash":
        pattern = _text(tool_input.get("command"))[:200]
    elif tool == "Agent":
        pattern = _text(tool_input.get("description"))
    elif tool == "Skill":
        pattern = _text(tool_input.get("skill"))
    elif tool in {"WebFetch", "WebSearch"}:
        path = _text(tool_input.get("url") or tool_input.get("query"), "n/a")
    else:
        path = _text(tool_input.get("file_path") or tool_input.get("path"), "n/a")

    return {"session": session, "tool": tool, "path": path, "pattern": pattern}


def _capture_governance_event(raw: str) -> None:
    script = Path(__file__).with_name("governance_events.py")
    if not script.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(script)],
            input=_safe_text(raw),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return


def _append_log(row: dict[str, str]) -> None:
    path = _log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": _utc_now(), **row}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    _rotate(path)


def _rotate(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_LINES:
        return
    keep = MAX_LINES // 2
    path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")


def _session_intent_root() -> Path:
    configured = os.environ.get("GHOST_ALICE_SESSION_INTENT_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    ledger_dir = Path(__file__).resolve().parents[1] / "session-intent-analyzer" / "scripts"
    if str(ledger_dir) not in sys.path:
        sys.path.insert(0, str(ledger_dir))
    try:
        from session_intent_ledger import default_root
    except Exception:
        return Path.home() / ".ghost-alice" / "session-intent"
    return default_root()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _is_semantic_delta_recovery(payload: dict[str, Any]) -> bool:
    tool_input = _as_dict(payload.get("tool_input"))
    command = _text(tool_input.get("cmd") or tool_input.get("command"))
    return "session_intent_ledger.py" in command and "--delta-json" in command


def _dedup_key_seen(dedup_key: str) -> bool:
    try:
        lines = _log_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("dedup_key") == dedup_key:
            return True
    return False


def _semantic_delta_warning(payload: dict[str, Any]) -> dict[str, str] | None:
    session = _text(payload.get("session_id") or payload.get("sessionId"), "")
    platform = _text(payload.get("platform"), "codex")
    if not session or _is_semantic_delta_recovery(payload):
        return None
    session_dir = _session_intent_root() / platform / session
    state_path = session_dir / "intent-state.json"
    events_path = session_dir / "intent-events.jsonl"
    state = _read_json(state_path)
    if state.get("last_semantic_delta_status") != "not-provided":
        return None
    user_events = [
        row for row in _read_jsonl(events_path)
        if row.get("event") == "user-input-observed"
    ]
    if len(user_events) < 3:
        return None
    recent = user_events[-3:]
    if any(row.get("intent_delta_status", "not-provided") != "not-provided" for row in recent):
        return None
    latest_event_id = _text(recent[-1].get("event_id"), "")
    if not latest_event_id:
        return None
    dedup_key = f"semantic-delta-starvation:{platform}:{session}:{latest_event_id}"
    if _dedup_key_seen(dedup_key):
        return None
    return {
        "session": session,
        "tool": "governance-warning",
        "path": str(state_path),
        "pattern": "semantic-delta-starvation: 3 consecutive digest-only user inputs",
        "dedup_key": dedup_key,
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        _capture_governance_event(raw)
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        _append_log(_extract(payload))
        warning = _semantic_delta_warning(payload)
        if warning:
            _append_log(warning)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
