#!/usr/bin/env python3
"""Prompt hook that releases task-router after intent preflight or explicit gate allow."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / ".tmp" / "session-intent"
DEFAULT_INTERNAL = (
    "hook-reminder: task-router waits until session-intent preflight exists and no current-lineage block gate is recorded. "
    "Absent downstream-gates.json means silent allow unless a current-lineage model block is recorded. "
    "After release, read the ledger, decompose accepted intent into atomic meaning units, choose focus-layer micro|meso|macro|meta "
    "plus scope-reopen target on mismatch, then assign output, verification, lifecycle, and boundary skills before downstream work/tool calls."
)


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def safe_path_component(value: Any) -> str:
    text = str(value or "unknown").strip()
    text = re.sub(r"[^A-Za-z0-9_.=-]+", "-", text)
    text = re.sub(r"^[.-]+|[.-]+$", "", text)
    return text or "unknown"


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def current_pointer(root: Path, platform: str) -> dict[str, Any]:
    pointer = read_json(root / platform / "current-session.json")
    if pointer.get("schema_version") != "session-intent-current.v1":
        return {}
    return pointer


def resolve_session_id(root: Path, platform: str, payload: dict[str, Any]) -> str:
    pointer = current_pointer(root, platform)
    return safe_path_component(first_text(
        payload.get("session_id"),
        payload.get("sessionId"),
        payload.get("conversation_id"),
        payload.get("thread_id"),
        os.environ.get("GHOST_ALICE_SESSION_ID"),
        pointer.get("session_id"),
        "",
    ))


def session_dir(root: Path, platform: str, session_id: str) -> Path:
    return root / safe_path_component(platform) / safe_path_component(session_id)


def gate_state(root: Path, platform: str, session_id: str) -> dict[str, Any]:
    gate = read_json(session_dir(root, platform, session_id) / "downstream-gates.json")
    if gate.get("schema_version") != "downstream-gates.v1":
        return {}
    if gate.get("gate") != "jailbreak-detector":
        return {}
    match = downstream_gate_matches_latest_event(gate, latest_intent_event(root, platform, session_id))
    if not match.get("ok", False):
        gate["stale"] = True
        gate["stale_reason"] = match.get("reason", "stale downstream gate")
        gate["legacy"] = match.get("legacy", False)
    return gate


def latest_intent_event(root: Path, platform: str, session_id: str) -> dict[str, Any]:
    events = session_dir(root, platform, session_id) / "intent-events.jsonl"
    try:
        rows = events.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in reversed(rows):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("event") == "user-input-observed":
            return row
    return {}


def downstream_gate_matches_latest_event(gate: dict[str, Any], latest_event: dict[str, Any]) -> dict[str, Any]:
    if not gate.get("input_event_id") and not gate.get("input_digest"):
        return {"ok": False, "legacy": True, "reason": "legacy downstream gate missing input lineage"}
    if not latest_event:
        return {"ok": False, "legacy": False, "reason": "stale downstream gate: latest input event missing"}
    if gate.get("input_event_id") and latest_event.get("event_id") and gate["input_event_id"] != latest_event["event_id"]:
        return {"ok": False, "legacy": False, "reason": "stale downstream gate: input_event_id mismatch"}
    if gate.get("input_digest") and latest_event.get("input_digest") and gate["input_digest"] != latest_event["input_digest"]:
        return {"ok": False, "legacy": False, "reason": "stale downstream gate: input_digest mismatch"}
    return {"ok": True, "legacy": False}


def ledger_state_path(root: Path, platform: str, session_id: str) -> str:
    pointer = current_pointer(root, platform)
    if safe_path_component(pointer.get("session_id", "")) == safe_path_component(session_id):
        state_path = first_text(pointer.get("state_path"))
        if state_path:
            return state_path
    return str(session_dir(root, platform, session_id) / "intent-state.json")


def reminder_message(base_message: str, root: Path, platform: str, payload: dict[str, Any]) -> str:
    session_id = resolve_session_id(root, platform, payload)
    if session_id == "unknown":
        return (
            "hook-reminder: task-router withheld until session-intent-analyzer writes current-session.json "
            "and the current-lineage block check can run. Do not run task-router yet."
        )

    gate = gate_state(root, platform, session_id)
    if not gate:
        latest_event = latest_intent_event(root, platform, session_id)
        if not latest_event:
            return (
                "hook-reminder: task-router withheld until session-intent-analyzer writes current-session.json "
                f"for session {session_id}. Continue intake/bootstrap; do not ask the user for another input."
            )
        state_path = ledger_state_path(root, platform, session_id)
        gate_path = str(session_dir(root, platform, session_id) / "downstream-gates.json")
        return "\n".join([
            base_message,
            f"gate-opened: jailbreak-detector silent allow for session {session_id}; no current block decision recorded.",
            f"intent-ledger: read {state_path} after session-intent preflight.",
            f"downstream-gate: {gate_path} absent; silent allow invariant applies unless a current-lineage model block is recorded.",
            "task-router-step: wait-for-jailbreak-decision → read-session-intent-ledger → atomic meaning decomposition → focus-layer/scope-reopen → skill assignment.",
        ])

    if gate.get("stale"):
        reason = str(gate.get("stale_reason") or "stale downstream gate")
        return (
            "hook-reminder: jailbreak-detector downstream gate is stale for the latest input. "
            f"{reason}. Continue intake/routing; do not reuse the stale decision as current block/allow."
        )

    decision = str(gate.get("decision") or "unknown")
    if gate.get("opened") is False or decision == "block":
        return (
            "hook-reminder: task-router withheld because jailbreak-detector downstream gate recorded a current-lineage block. "
            f"decision={decision}. Do not run task-router or downstream work."
        )

    state_path = ledger_state_path(root, platform, session_id)
    gate_path = str(session_dir(root, platform, session_id) / "downstream-gates.json")
    return "\n".join([
        base_message,
        f"gate-opened: jailbreak-detector silent allow for session {session_id}; no current block decision recorded.",
        f"intent-ledger: read {state_path} after session-intent preflight.",
        f"downstream-gate: {gate_path} contains no block; silent allow invariant applies.",
        "task-router-step: wait-for-jailbreak-decision → read-session-intent-ledger → atomic meaning decomposition → focus-layer/scope-reopen → skill assignment.",
    ])


def render_payload(output_format: str, message: str) -> str:
    if output_format == "json":
        return json.dumps({"continue": True, "systemMessage": message}, ensure_ascii=False)
    return "\n".join([
        f"Internal instruction: {message}",
        "User: Run task-router after session-intent preflight; absent current-lineage block gate is silent allow.",
        "Tech: After release, task-router reads the ledger, chooses focus-layer/scope-reopen, and assigns skills.",
        "",
    ])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Release task-router reminder after intent preflight or explicit gate allow.")
    parser.add_argument("--platform", default="codex")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--internal-b64", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args, _unknown = build_parser().parse_known_args(argv)
    base_message = DEFAULT_INTERNAL
    if args.internal_b64:
        try:
            base_message = base64.urlsafe_b64decode(args.internal_b64.encode("ascii")).decode("utf-8")
        except Exception:
            base_message = DEFAULT_INTERNAL
    root = Path(args.root).expanduser()
    message = reminder_message(base_message, root, args.platform, read_payload())
    sys.stdout.write(render_payload(args.format, message))
    if args.format == "json":
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
