#!/usr/bin/env python3
"""Stop hook guard for verification-before-completion."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_completion import assess_native_completion  # noqa: E402
from native_transcript import read_entries as _iter_transcript, is_actual_user_prompt as _is_actual_user_prompt
from claim_evidence_runtime import native_claim_source  # noqa: E402
from response_delivery import observe_recovery_delivery  # noqa: E402

# Every Claude block reason carries this marker so a later Stop in the same turn can count earlier retries.
COMPLETION_GATE_MARKER = "[completion-gate]"
RETRIES_PER_TURN = 1
# Named surfaces retain the established transcript behavior. Bare cli stays
# unknown; native interactive evidence is recognized separately below.


def _read_hook_input() -> dict[str, Any]:
    # Decode stdin as UTF-8 explicitly (Windows default is cp949/cp1252), so a non-ASCII transcript_path or inline message is not mis-decoded.
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


def _turn_start_index(entries: list[dict[str, Any]]) -> int:
    for index in range(len(entries) - 1, -1, -1):
        if _is_actual_user_prompt(entries[index]):
            return index
    return -1


def _assistant_text_this_turn(entries: list[dict[str, Any]]) -> str:
    # Validate the FINAL response only: the last assistant message, which is the one that triggered this stop. AskUserQuestion answers and tool results are not real user prompts, so a "since last user prompt" span would concatenate the completion-checks of several earlier responses and re-validate a stale one (causing repeated false blocks).
    for entry in reversed(entries[_turn_start_index(entries) + 1 :]):
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


def _recovery_guidance(input_data, entries, platform, final_text, repair_target='completion_metadata'):
    return observe_recovery_delivery(input_data, platform=platform,
        entrypoint=_host_entrypoint(entries), final_text=final_text, repair_target=repair_target)["guidance"]


def _host_entrypoint(entries: list[dict[str, Any]]) -> str:
    # The running host's own value outranks the transcript record.
    value = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").strip()
    if value:
        return value
    for entry in reversed(entries):
        recorded = entry.get("entrypoint")
        if isinstance(recorded, str) and recorded.strip():
            return recorded.strip()
    return ""


def _entry_text(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in _content_items(entry):
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _gate_blocks_this_turn(entries: list[dict[str, Any]]) -> int:
    # Stop feedback is a meta user entry, so it never starts a new turn.
    count = 0
    for entry in entries[_turn_start_index(entries) + 1 :]:
        if entry.get("type") != "user" or not entry.get("isMeta"):
            continue
        text = _entry_text(entry)
        if "Stop hook feedback" in text and COMPLETION_GATE_MARKER in text:
            count += 1
    return count


def _retry_limit_notice(reason: str, repair_target='completion_metadata') -> str:
    if repair_target == 'response_route':
        return f"{COMPLETION_GATE_MARKER} Current work routing remains unresolved: {reason}"
    if repair_target == 'response_review':
        return f"{COMPLETION_GATE_MARKER} Internal response review remains unresolved: {reason}"
    return (
        f"{COMPLETION_GATE_MARKER} Core has not accepted the submitted completion proof after one retry. "
        f"Remaining defect: {reason} The business result and accepted completion proof are separate states. "
        "Retain valid proof at its original time; do not rerun unchanged business checks or repeat the requested answer for this notice."
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--platform", choices=("claude", "codex"), default="claude")
    return parser.parse_known_args(argv)[0]


def main() -> int:
    args = _parse_args(sys.argv[1:])
    input_data = _read_hook_input()
    entries = _iter_transcript(input_data.get("transcript_path") or input_data.get("transcriptPath"))

    input_text = _assistant_text_from_stop_input(input_data)
    # Native Stop carries the response that triggered this event. Transcript
    # writes can lag behind it, especially after a blocked-response repair.
    # Keep transcript-backed turn/retry context separate from response-body selection.
    final_text = input_text or _assistant_text_this_turn(entries)
    if not final_text.strip():
        print(json.dumps(_allow_payload(), ensure_ascii=False))
        return 0

    # Core authenticates the declared proof and complete response. Procedure
    # invocation belongs to workflow guidance/audit, not a second eligibility
    # judgment that can contradict the same review consumed by Ghost-ALICE Autopilot.
    # Native tool activity requires a current route independently of whether
    # the assistant submitted a completion claim. Prose does not supply either.
    try:
        source = native_claim_source(args.platform, input_data)
    except ValueError:
        # Missing native identity cannot adopt the process's ambient session.
        source = dict(os.environ, GHOST_ALICE_PLATFORM=args.platform, GHOST_ALICE_SESSION_ID='')
    assessment = assess_native_completion(source, text=final_text, phase='final', native_input=input_data)
    body_issue = assessment['body_issue']
    if body_issue is None:
        print(json.dumps(_allow_payload(), ensure_ascii=False))
        return 0

    # Core's specific defect is authoritative. A missing proof marker does not
    # mean a retained-claim reference is the repair; mixed claims need review.
    reason = body_issue or ""
    if args.platform == "codex":
        print(json.dumps(_block_payload(f"{reason} {_recovery_guidance(input_data, entries, args.platform, final_text, assessment.get('repair_target', 'completion_metadata'))}"), ensure_ascii=False))
        return 0

    # One retry per turn: a defect that survives it ends the turn with a visible notice instead of another
    # rewrite. The notice is not a pass; the answer stays marked as lacking valid evidence.
    if _gate_blocks_this_turn(entries) >= RETRIES_PER_TURN:
        print(json.dumps(_allow_payload(_retry_limit_notice(reason, assessment.get('repair_target', 'completion_metadata'))), ensure_ascii=False))
        return 0
    reason = f"{COMPLETION_GATE_MARKER} {reason} {_recovery_guidance(input_data, entries, args.platform, final_text, assessment.get('repair_target', 'completion_metadata'))}"
    print(json.dumps(_block_payload(reason), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
