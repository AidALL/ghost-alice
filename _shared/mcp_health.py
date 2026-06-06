#!/usr/bin/env python3
"""MCP health classification and backoff helpers.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable


UNHEALTHY_STATUS_CODES = {401, 403, 429, 503}


def is_mcp_payload(payload: dict[str, Any]) -> bool:
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    if tool_name.startswith("mcp__"):
        return True
    tool_input = payload.get("tool_input")
    return isinstance(tool_input, dict) and bool(tool_input.get("mcp_server"))


def classify_probe_result(status_code: int | None = None, error: str | None = None) -> dict[str, str]:
    if error:
        return {"status": "unhealthy", "reason": "transport-error"}
    if status_code in UNHEALTHY_STATUS_CODES:
        return {"status": "unhealthy", "reason": str(status_code)}
    return {"status": "healthy", "reason": "ok"}


class McpHealthState:
    def __init__(self, ttl_seconds: int = 300, clock: Callable[[], float] | None = None) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.time
        self.records: dict[str, dict[str, Any]] = {}

    def should_probe(self, server: str) -> bool:
        record = self.records.get(server)
        if not record:
            return True
        last_checked = float(record.get("last_checked", 0.0))
        return self.clock() - last_checked >= self.ttl_seconds

    def record(self, server: str, result: dict[str, str]) -> dict[str, Any]:
        previous = self.records.get(server, {})
        status = result.get("status", "unknown")
        if status == "healthy":
            failure_count = 0
            backoff_seconds = 0
        else:
            failure_count = int(previous.get("failure_count", 0)) + 1
            backoff_seconds = min(3600, 5 * (2 ** (failure_count - 1)))
        record = {
            "server": server,
            "status": status,
            "reason": result.get("reason", "unknown"),
            "last_checked": self.clock(),
            "failure_count": failure_count,
            "backoff_seconds": backoff_seconds,
        }
        self.records[server] = record
        return record


def load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"servers": {}}
    if not isinstance(value, dict):
        return {"servers": {}}
    servers = value.get("servers")
    if not isinstance(servers, dict):
        value["servers"] = {}
    return value


def summarize_state(path: Path) -> tuple[str, str]:
    state = load_state(path)
    servers = state.get("servers", {})
    unhealthy = [
        name
        for name, record in servers.items()
        if isinstance(record, dict) and record.get("status") == "unhealthy"
    ]
    if unhealthy:
        return "warning", f"unhealthy ({len(unhealthy)} servers)"
    return "ok", f"present ({len(servers)} servers)"
