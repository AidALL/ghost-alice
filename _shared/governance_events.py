#!/usr/bin/env python3
"""Capture redacted governance events from tool hook payloads.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*=?\s*['\"]?[A-Za-z0-9._-]{20,}"),
]
SENSITIVE_PATH_RE = re.compile(r"(?i)(^|[ /])(\.env|[^ ]+\.(pem|key)|id_rsa)([ /]|$)")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def command_fingerprint(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()[:12]


def command_name(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return "unknown"
    return Path(parts[0]).name or "unknown"


def destructive_rule(command: str) -> str | None:
    lowered = " ".join(command.lower().split())
    if re.search(r"\bgit\s+push\b.*\s--force(?:\s|$)", lowered):
        return "destructive-command"
    if re.search(r"\bgit\s+reset\s+--hard(?:\s|$)", lowered):
        return "destructive-command"
    if re.search(r"\brm\s+-[a-z]*r[a-z]*f|rm\s+-[a-z]*f[a-z]*r", lowered):
        return "destructive-command"
    return None


def extract_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "cmd"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def event_from_payload(payload: dict[str, Any]) -> dict[str, str] | None:
    command = extract_command(payload)
    tool = str(payload.get("tool_name") or payload.get("tool") or "unknown")
    haystack = command
    rule = None
    severity = "warning"
    if any(pattern.search(haystack) for pattern in SECRET_PATTERNS):
        rule = "secret-pattern"
        severity = "critical"
    elif destructive_rule(command):
        rule = "destructive-command"
    elif SENSITIVE_PATH_RE.search(haystack):
        rule = "sensitive-path"
    if rule is None:
        return None
    return {
        "ts": utc_now(),
        "severity": severity,
        "rule": rule,
        "tool": tool,
        "command_name": command_name(command),
        "fingerprint": command_fingerprint(command),
        "detail": "raw command redacted",
    }


def output_path() -> Path:
    configured = os.environ.get("GHOST_ALICE_GOVERNANCE_EVENTS")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".ghost-alice" / "governance-events.jsonl"


def append_event(event: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    event = event_from_payload(payload)
    if event is None:
        return 0
    append_event(event, output_path())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
