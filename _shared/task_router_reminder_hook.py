#!/usr/bin/env python3
"""Prompt hook that releases task-router after exact intent preflight.

Dependencies: Python 3.11+ standard library and the sibling intake hook.
"""

from __future__ import annotations

from reminder_texts import instruction_override, reminder_text

import argparse
import importlib
import json
import shlex
import sys
from pathlib import Path
from typing import Any

from session_intent_analyzer_hook import (
    bound_session_identity, _safe_component as safe_path_component, TERMINAL_ROUTING_INSTRUCTION,
    terminal_routing_instruction,
)


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / ".tmp" / "session-intent"
DEFAULT_INTERNAL = reminder_text('task-router')


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def resolve_session_id(root: Path, platform: str, payload: dict[str, Any]) -> str:
    return bound_session_identity(platform, payload) or "unknown"


def session_dir(root: Path, platform: str, session_id: str) -> Path:
    return root / safe_path_component(platform) / safe_path_component(session_id)



def session_material(root: Path, platform: str, session_id: str) -> dict[str, Any]:
    directory = session_dir(root, platform, session_id)
    degraded = (directory / "ledger-degraded.json").exists()
    try:
        ledger = importlib.import_module("session_intent_ledger")
        state = ledger.read_session_state(root=root, platform=platform, session_id=session_id,
                                          recover_audit=False)
    except Exception:
        # A broken authoritative store cannot be replaced by an older export.
        return {'state': {}, 'degraded': True, 'latest_input': {}}
    return {'state': state, 'degraded': degraded,
            'latest_input': {} if degraded else _input_from_state(state, directory, platform, session_id)}


def session_events(root: Path, platform: str, session_id: str) -> list[dict[str, Any]]:
    try:
        ledger = importlib.import_module("session_intent_ledger")
        return ledger.read_session_events(root=root, platform=platform, session_id=session_id)
    except Exception:
        return []


def latest_intent_event(root: Path, platform: str, session_id: str) -> dict[str, Any]:
    return session_material(root, platform, session_id)['latest_input']


def _input_from_state(state: dict[str, Any], directory: Path,
                      platform: str, session_id: str) -> dict[str, Any]:
    if (state.get("schema_version") != "session-intent-ledger.v1"
            or state.get("platform") != platform or state.get("session_id") != session_id):
        return {}
    anchor_keys = ("ledger_revision", "latest_input_event_id", "latest_input_digest", "latest_input_char_count")
    if any(key in state for key in anchor_keys):
        event_id, digest = state.get("latest_input_event_id"), state.get("latest_input_digest")
        count = state.get("latest_input_char_count")
        revision = state.get("ledger_revision")
        if (not isinstance(event_id, str) or not event_id or not isinstance(digest, str) or not digest
                or type(count) is not int or not 0 <= count <= 2**53 - 1
                or type(revision) is not int or not 0 <= revision <= 2**53 - 1):
            return {}
        return {"event": "user-input-observed", "event_id": event_id, "input_digest": digest,
                "input_char_count": count, "platform": platform, "session_id": session_id,
                "_state_anchor": True}
    for row in reversed(session_events(directory.parents[1], platform, session_id)):
        if isinstance(row, dict) and row.get("event") == "user-input-observed":
            if (row.get("platform") == platform and row.get("session_id") == session_id
                    and ((isinstance(row.get("event_id"), str) and row["event_id"])
                         or (isinstance(row.get("input_digest"), str) and row["input_digest"]))):
                return row
            return {}
    return {}



def ledger_state_path(root: Path, platform: str, session_id: str) -> str:
    return str(session_dir(root, platform, session_id) / "intent-state.json")


def ledger_read_instruction(root: Path, platform: str, session_id: str) -> str:
    ledger = importlib.import_module("session_intent_ledger")
    args = [sys.executable, str(Path(ledger.__file__).resolve()),
            "--read-intent", "--root", str(root),
            "--platform", platform, "--session-id", session_id]
    return (f"Normal route only. intent-ledger: use {shlex.join(args)} after session-intent preflight. "
            "SQLite is authoritative; compatibility exports are not.")


def intent_history_instruction(state: dict[str, Any]) -> list[str]:
    """Diagnostic record only; accumulated conditions never select current work."""
    criteria = state.get("acceptance_criteria")
    if not isinstance(criteria, list):
        return []
    count = sum(1 for item in criteria if isinstance(item, dict)
                and item.get("admitted") is True and item.get("status") == "unmet"
                and isinstance(item.get("id"), str) and item["id"])
    if not count:
        return []
    return [
        f"intent-history-diagnostic: {count} admitted unmet conditions in the ledger; "
        "history, not a current work selection or execution authorization. "
        "Select via current Core response-route and exact owned contract revision; respect stop, pause, cancellation or replacement.",
    ]


def reminder_message(base_message: str, root: Path, platform: str, payload: dict[str, Any]) -> str:
    return reminder_result(base_message, root, platform, payload)[0]


def reminder_result(base_message: str, root: Path, platform: str, payload: dict[str, Any]) -> tuple[str, bool]:
    """Preserve terminal routing before every normal-route intake diagnostic."""
    message, routine = _reminder_result(base_message, root, platform, payload)
    # Unavailable intake always carries the full fallback. Successful intake
    # coalesces only policy already delivered for this exact native input.
    return (message if routine else TERMINAL_ROUTING_INSTRUCTION + "\n" + message), routine


def _reminder_result(base_message: str, root: Path, platform: str, payload: dict[str, Any]) -> tuple[str, bool]:
    """(message, routine). Only successful exact intake is routine; every unavailable input state is not."""
    session_id = resolve_session_id(root, platform, payload)
    if session_id == "unknown":
        return (
            "hook-reminder: Normal-route task-router registration is withheld until the current host supplies a valid session identity. "
            "Keep dependent normal-route writes closed; tool-free terminal routing remains available from supplied context."
        ), False

    # Fail-closed on a degraded ledger: when session-intent-analyzer could not record the latest input (broken import or write failure), the "latest event" anchor is stale, so releasing routing here would ride a previous turn's lineage. The marker is cleared by the analyzer hook on the next successful observation.
    degrade_marker = session_dir(root, platform, session_id) / "ledger-degraded.json"
    if degrade_marker.exists():
        reason = "degraded"
        try:
            marker = json.loads(degrade_marker.read_text(encoding="utf-8"))
            if isinstance(marker, dict):
                reason = str(marker.get("reason") or reason)
        except (OSError, json.JSONDecodeError):
            reason = "unreadable-marker"
        return (
            "hook-reminder: Normal-route task-router registration is withheld: the session-intent ledger is degraded "
            f"({reason}) and the latest input was NOT recorded, so current-lineage checks "
            "would ride a stale anchor. For an authorized normal route, repair the session-intent ledger before dependent registration. "
            "Do not start repair merely to deliver a tool-free direct answer."
        ), False

    material = session_material(root, platform, session_id)
    if material['degraded']:
        return ("hook-reminder: Normal-route task-router registration is withheld: the session-intent ledger is degraded. "
                "For an authorized normal route, repair the authoritative reader before dependent registration; "
                "do not reuse a compatibility export or start repair for a tool-free direct answer."), False
    if not material['latest_input']:
        return (
            "hook-reminder: Normal-route task-router registration is withheld until session-intent-analyzer records the current input "
            f"for session {session_id}. Continue intake/bootstrap only for an authorized normal route; "
            "do not ask for another input or start bootstrap for a tool-free direct answer."
        ), False
    return "\n".join([
        terminal_routing_instruction(root, platform, session_id, material['latest_input'].get('event_id')),
        base_message,
        f"intake-ready: current input recorded for session {session_id}.",
        ledger_read_instruction(root, platform, session_id),
        *intent_history_instruction(material['state']),
    ]), True


def render_payload(output_format: str, message: str, *, routine: bool = False) -> str:
    if output_format == "json":
        # Visibility controls the user notice, not the routing instructions.
        # Match the intake hook's model-context channel through the installed wrapper.
        body: dict[str, Any] = {
            "continue": True,
            "systemMessage": message,
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": message,
            },
        }
        if routine:
            body["ghostAliceSurface"] = "routine"
        return json.dumps(body, ensure_ascii=False)
    return "\n".join([
        f"Internal instruction: {message}",
        "User: Run task-router after exact session-intent preflight; native permission stays with its host.",
        "Tech: Terminal routing uses supplied context without tools; normal routing reads the exact ledger and assigns skills.",
        "",
    ])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Release task-router reminder after exact intent preflight.")
    parser.add_argument("--platform", default="codex")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--internal-b64", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, _unknown = parser.parse_known_args(argv)
    try:
        base_message = instruction_override(args.internal_b64) or ""
    except (ValueError, UnicodeError):
        parser.error('Invalid custom instruction encoding')
    root = Path(args.root).expanduser()

    payload = read_payload()
    if (payload.get("hook_event_name") or payload.get("hookEventName")) == "UserPromptSubmit":
        # Cached native registrations can outlive an install. The intake hook
        # owns this event; a sibling cannot prove it has finished current intake.
        sys.stdout.write(json.dumps({"continue": True, "ghostAliceSurface": "routine"}) + "\n")
        return 0
    message, routine = reminder_result(base_message, root, args.platform, payload)
    sys.stdout.write(render_payload(args.format, message, routine=routine))
    if args.format == "json":
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
