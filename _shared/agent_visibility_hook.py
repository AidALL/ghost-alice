#!/usr/bin/env python3
"""Codex input hook for the /visibility pseudo-command.

Claude Code runs /visibility as a workspace command. Codex has no workspace command, so this input
hook reads the submitted prompt, shows or sets the agent visibility profile, and blocks the original
prompt. Every other prompt passes through without model context or a screen message.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

import runtime_config


def _home() -> Path:
    for key in ("HOME", "USERPROFILE"):
        value = os.environ.get(key, "").strip()
        if value:
            return Path(value)
    drive = os.environ.get("HOMEDRIVE", "").strip()
    path = os.environ.get("HOMEPATH", "").strip()
    if drive and path:
        return Path(f"{drive}{path}")
    return Path.home()

def _read_hook_input() -> dict[str, Any]:
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""

def _extract_prompt(input_payload: dict[str, Any]) -> str:
    message = input_payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            text_parts = [
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            ]
            message = "\n".join(part for part in text_parts if part)
        else:
            message = content
    return _first_non_empty(
        input_payload.get("prompt"),
        input_payload.get("user_prompt"),
        input_payload.get("userPrompt"),
        input_payload.get("input"),
        input_payload.get("text"),
        message,
        input_payload.get("content"),
    )

def _parse_visibility_prompt(prompt: str) -> tuple[str, str | None] | None:
    first_line = prompt.strip().splitlines()[0].strip() if prompt.strip() else ""
    if not first_line:
        return None
    try:
        parts = shlex.split(first_line)
    except ValueError:
        parts = first_line.split()
    if not parts:
        return None
    command = parts[0].lower()
    if command not in {"/visibility", "/show"}:
        return None
    if command == "/show" and len(parts) == 1:
        return ("show", None)
    if len(parts) == 1:
        return ("show", None)
    if len(parts) == 2 and parts[1].lower() in {"show", "status", "current"}:
        return ("show", None)
    if len(parts) == 2:
        return ("set", parts[1])
    return ("invalid", None)

def _visibility_json_payload(input_payload: dict[str, Any]) -> str | None:
    parsed = _parse_visibility_prompt(_extract_prompt(input_payload))
    if parsed is None:
        return None

    action, profile = parsed
    if action == "show":
        current = runtime_config.load_config(home=_home())["agent_visibility"]["profile"]
        reason = (
            f"Ghost-ALICE agent visibility profile is {current}. "
            "Use /visibility strict, /visibility dynamic, or /visibility minimal to change it."
        )
        return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)

    if action == "set" and profile is not None:
        normalized = runtime_config.canonical_agent_visibility_profile(profile)
        if normalized == str(profile).strip().lower().replace("_", "-"):
            runtime_config.save_config({"agent_visibility": {"profile": normalized}}, home=_home())
            reason = (
                f"Ghost-ALICE agent visibility profile set to {normalized}. "
                "Hook execution and strict session logging remain unchanged."
            )
            return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)

    reason = (
        "Unknown Ghost-ALICE visibility command. " "Use /visibility, /visibility strict, /visibility dynamic, or /visibility minimal."
    )
    return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)


def main() -> int:
    payload = _visibility_json_payload(_read_hook_input())
    print(payload if payload is not None else json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
