#!/usr/bin/env python3
"""Stop hook guard for verification-before-completion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from completion_check_validator import (  # noqa: E402
    looks_like_completion_claim,
    validate_completion_text,
)

VERIFICATION_SKILL = "verification-before-completion"


def _read_hook_input() -> dict[str, Any]:
    # Decode stdin as UTF-8 explicitly (Windows default is cp949/cp1252), so a
    # non-ASCII transcript_path or inline message is not mis-decoded.
    try:
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is not None:
            data = buffer.read()
            raw = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        else:
            raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _iter_transcript(path_value: Any) -> list[dict[str, Any]]:
    if not path_value:
        return []
    path = Path(str(path_value)).expanduser()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    entries: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _content_items(entry: dict[str, Any]) -> list[Any]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, list):
        return content
    if content is None:
        return []
    return [content]


def _is_actual_user_prompt(entry: dict[str, Any]) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    message = entry.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    items = _content_items(entry)
    if not items:
        return False
    for item in items:
        if isinstance(item, dict) and item.get("type") == "tool_result":
            return False
    return True


def _uses_verification_skill(entry: dict[str, Any]) -> bool:
    message = entry.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    for item in _content_items(entry):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "tool_use" or item.get("name") != "Skill":
            continue
        tool_input = item.get("input")
        if isinstance(tool_input, dict) and tool_input.get("skill") == VERIFICATION_SKILL:
            return True
    return False


def _turn_start_index(entries: list[dict[str, Any]]) -> int:
    for index in range(len(entries) - 1, -1, -1):
        if _is_actual_user_prompt(entries[index]):
            return index
    return -1


def _verification_skill_loaded_this_turn(entries: list[dict[str, Any]]) -> bool:
    start = _turn_start_index(entries)
    if start < 0:
        return False
    return any(_uses_verification_skill(entry) for entry in entries[start + 1 :])


def _assistant_text_this_turn(entries: list[dict[str, Any]]) -> str:
    # Validate the FINAL response only: the last assistant message, which is the one
    # that triggered this stop. AskUserQuestion answers and tool results are not real
    # user prompts, so a "since last user prompt" span would concatenate the
    # completion-checks of several earlier responses and re-validate a stale one
    # (causing repeated false blocks).
    for entry in reversed(entries):
        message = entry.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        texts: list[str] = []
        for item in _content_items(entry):
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
        return "\n".join(texts)
    return ""


def _assistant_text_from_stop_input(input_data: dict[str, Any]) -> str:
    for key in ("last_assistant_message", "lastAssistantMessage", "prompt_response", "promptResponse"):
        value = input_data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _already_retrying(input_data: dict[str, Any]) -> bool:
    return bool(input_data.get("stop_hook_active") or input_data.get("stopHookActive"))


def _allow_payload(message: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"continue": True}
    if message:
        payload["systemMessage"] = message
    return payload


def _block_payload(reason: str) -> dict[str, Any]:
    return {
        "decision": "block",
        "reason": reason,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--platform", choices=("claude", "codex"), default="claude")
    return parser.parse_known_args(argv)[0]


def main() -> int:
    args = _parse_args(sys.argv[1:])
    input_data = _read_hook_input()
    entries = _iter_transcript(input_data.get("transcript_path") or input_data.get("transcriptPath"))

    input_text = _assistant_text_from_stop_input(input_data)
    if args.platform == "codex":
        final_text = input_text or _assistant_text_this_turn(entries)
    else:
        final_text = _assistant_text_this_turn(entries) or input_text
    if not final_text.strip():
        print(json.dumps(_allow_payload(), ensure_ascii=False))
        return 0

    skill_loaded = args.platform == "codex" or _verification_skill_loaded_this_turn(entries)
    # Completion-body-validation invariant: Stop validates executed-work closure
    # claims and explicit completion-check blocks. Routine explanations are not
    # forced through verification-before-completion.
    body_issue = validate_completion_text(final_text, require_completion_check=True)
    has_completion_marker = looks_like_completion_claim(final_text)

    if body_issue is None and (skill_loaded or not has_completion_marker):
        print(json.dumps(_allow_payload(), ensure_ascii=False))
        return 0

    missing_marker = not has_completion_marker and body_issue is not None
    if missing_marker:
        reason = body_issue
    elif not skill_loaded:
        reason = (
            "completion-reminder: verification-before-completion is an always-on completion lifecycle gate. "
            "Before the final response, call the Claude Code Skill tool with input "
            '{"skill": "verification-before-completion"} in this turn. '
            "Do not ask the user whether to use the skill; invoke it directly. "
            "Then perform the fresh evidence check, and only then write [completion-check] with "
            "skill-call: verification-before-completion (this turn). Do not infer this from task-router, "
            "metadata, prior context, or evidence-only status inspection. "
            "The retry must be a complete standalone final answer that includes the requested answer payload again. "
            "Begin with the user's requested answer, not with the verification process. "
            "Do not begin with verification process notes. "
            "Do not refer to a previous answer with phrases such as above, earlier, already provided, or previously."
        )
    else:
        reason = body_issue

    if _already_retrying(input_data):
        print(json.dumps(_allow_payload(reason), ensure_ascii=False))
        return 0

    print(json.dumps(_block_payload(reason), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
