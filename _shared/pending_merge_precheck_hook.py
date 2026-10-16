#!/usr/bin/env python3
"""Runtime pending-merge precheck foreground hook.

Claude/Codex foreground hooks need to remind the agent about task-router without
making the agent run its own Bash manifest probe. This hook performs the probe
itself and prints a compact contract: pending entries are reported, and no
pending warning means the current platform precheck is clean.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Literal

from merge_companion_messages import render_pending_merge_message
import runtime_config
import install_destination
from session_check_cache import observe_check, UnstableCheckTarget
from session_intent_analyzer_hook import bound_session_identity

HookKind = Literal["prompt-check", "hook-reminder", "session-check"]


def _pending_manifest_path(platform: str) -> Path:
    return install_destination.ghost_root(_home()) / "pending-merges" / platform / "manifest.json"


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


def _pending_entries(platform: str) -> list[dict[str, Any]]:
    path = _pending_manifest_path(platform)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        return []
    return [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("decided") is False
    ]


def _decode_internal(value: str) -> str:
    return base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")


def _pending_line(platform: str, count: int) -> str:
    return (
        f"merge-companion precheck: ~/.ghost-alice/pending-merges/{platform}/manifest.json "
        f"has {count} undecided entr{'y' if count == 1 else 'ies'}. "
        "Apply merge-companion before changing affected skills or when the user requests backup review. Unrelated read-only work may continue; an explicit defer leaves entries undecided."
    )


def _clean_contract_line(platform: str) -> str:
    return (
        f"merge-companion-precheck: clean (hook-verified). "
        f"This hook checked ~/.ghost-alice/pending-merges/{platform}/manifest.json; "
        "do not run an extra shell manifest check."
    )


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


def _text_payload(platform: str, hook: HookKind, context: str, internal: str, result: dict | None = None) -> str:
    count = len(_pending_entries(platform)) if result is None else result["value"]["undecided_count"]
    if count:
        if result is not None and result["status"] == "reused":
            return f"merge-companion precheck: pending={count} (unchanged; session check reused)."
        return "\n".join([
            _pending_line(platform, count),
            render_pending_merge_message(context),  # type: ignore[arg-type]
        ])

    return _clean_contract_line(platform)


def _json_payload(platform: str, internal: str, result: dict | None = None, *,
                  context: str | None = None, session_id: str | None = None) -> str:
    count = len(_pending_entries(platform)) if result is None else result["value"]["undecided_count"]
    receipt = {"platform": platform, "manifest_path": str(_pending_manifest_path(platform)), "undecided_count": count}
    if result is not None:
        receipt.update({"status": result["status"], "checked_at": result["checked_at"]})
    body: dict[str, Any] = {"continue": True, "ghostAlicePendingMergeCheck": receipt}
    if context == 'prompt_submit' and result is not None and result["status"] == "reused":
        # An unchanged observation adds nothing to the model context or the screen; the session-start
        # check or an earlier input already delivered it. Visibility commands return before this point.
        return json.dumps(body, ensure_ascii=False)
    if result is not None and session_id and session_id != 'unknown':
        # Session start repeats after resume, clear and compaction; an input delivers only a changed or
        # first observation. Return its native binding and metadata, never manifest entries or the procedure.
        public = dict(receipt, session_id=session_id)
        body['hookSpecificOutput'] = {'hookEventName': 'SessionStart' if context == 'session_start' else 'UserPromptSubmit',
            'additionalContext': 'merge-companion-precheck: ' + json.dumps(public, ensure_ascii=False, separators=(',', ':'))}
    if count:
        message = (f"merge-companion precheck: pending={count} (unchanged; session check reused)."
                   if result is not None and result["status"] == "reused" else _pending_line(platform, count))
        body['systemMessage'] = message
        return json.dumps(body, ensure_ascii=False)
    message = _clean_contract_line(platform)
    # A clean pass is routine: the hook runner keeps it off the user screen and in the strict log.
    body.update(systemMessage=message, ghostAliceSurface='routine')
    return json.dumps(body, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True)
    parser.add_argument("--hook", required=True, choices=["prompt-check", "hook-reminder", "session-check"])
    parser.add_argument("--context", required=True, choices=["prompt_submit", "session_start"])
    parser.add_argument("--format", required=True, choices=["text", "json"])
    parser.add_argument("--internal-b64", required=True)
    parser.add_argument("--root", type=Path)
    args, _unknown = parser.parse_known_args()

    internal = _decode_internal(args.internal_b64)
    input_payload = _read_hook_input()
    if args.hook in {"prompt-check", "hook-reminder"} and args.context == "prompt_submit" and args.format == "json":
        visibility_payload = _visibility_json_payload(input_payload)
        if visibility_payload is not None:
            print(visibility_payload)
            return 0
    result = None
    session_id = bound_session_identity(args.platform, input_payload)
    if args.root is not None:
        session_id = bound_session_identity(args.platform, input_payload) or "unknown"
        # Cache only normalized check metadata, not backup contents or raw manifest entries.
        try:
            result = observe_check(args.root, args.platform, session_id, "pending-merges", _pending_manifest_path(args.platform),
                                   lambda: {"undecided_count": len(_pending_entries(args.platform))},
                                   validate=lambda value: type(value.get("undecided_count")) is int and value["undecided_count"] >= 0)
        except UnstableCheckTarget as error:
            reason = f"merge-companion precheck: {error}"
            if args.format == "json":
                print(json.dumps({"decision": "block", "reason": reason}))
                return 0
            print(reason, file=sys.stderr)
            return 2
    if args.format == "json":
        print(_json_payload(args.platform, internal, result, context=args.context, session_id=session_id))
    else:
        print(_text_payload(args.platform, args.hook, args.context, internal, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
