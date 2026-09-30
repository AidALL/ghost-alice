#!/usr/bin/env python3
"""Prompt hook that releases task-router after intent preflight or explicit gate allow.

Dependencies: Python 3.11+ standard library and the sibling intake hook.
"""

from __future__ import annotations

import argparse
import base64
import importlib
import json
import shlex
import sys
from pathlib import Path
from typing import Any

from session_intent_analyzer_hook import bound_session_identity, _safe_component as safe_path_component


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / ".tmp" / "session-intent"
DEFAULT_INTERNAL = (
    "hook-reminder: task-router waits until session-intent preflight exists and no current-lineage block gate is recorded. " "Absent downstream-gates.json means silent allow unless a current-lineage model block is recorded. " "After release, read the ledger, decompose accepted intent into atomic meaning units, choose focus-layer micro|meso|macro|meta " "plus scope-reopen target on mismatch, then assign output, verification, lifecycle, and boundary skills before downstream work/tool calls."
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


def resolve_session_id(root: Path, platform: str, payload: dict[str, Any]) -> str:
    return bound_session_identity(platform, payload) or "unknown"


def session_dir(root: Path, platform: str, session_id: str) -> Path:
    return root / safe_path_component(platform) / safe_path_component(session_id)


def gate_state(root: Path, platform: str, session_id: str,
               material: dict[str, Any] | None = None) -> dict[str, Any]:
    material = session_material(root, platform, session_id) if material is None else material
    latest = material['latest_input']
    record = material['state'].get('model_security_decision')
    if isinstance(record, dict) and record.get('decision') == 'block':
        if downstream_gate_matches_latest_event(record, latest).get('ok'):
            # A routing hint must not call a known current model block absent
            # merely because its derived gate file has not been published yet.
            return {'decision': 'block', 'opened': False}
    gate = read_json(session_dir(root, platform, session_id) / "downstream-gates.json")
    if gate.get("schema_version") != "downstream-gates.v1":
        return {}
    if gate.get("gate") != "jailbreak-detector":
        return {}
    if gate.get("platform") != platform or gate.get("session_id") != session_id:
        return {}
    match = downstream_gate_matches_latest_event(gate, latest)
    if not match.get("ok", False):
        gate["stale"] = True
        gate["stale_reason"] = match.get("reason", "stale downstream gate")
        gate["legacy"] = match.get("legacy", False)
    return gate


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


def downstream_gate_matches_latest_event(gate: dict[str, Any], latest_event: dict[str, Any]) -> dict[str, Any]:
    if not gate.get("input_event_id") and not gate.get("input_digest"):
        return {"ok": False, "legacy": True, "reason": "legacy downstream gate missing input lineage"}
    if not latest_event:
        return {"ok": False, "legacy": False, "reason": "stale downstream gate: latest input event missing"}
    if latest_event.get("event_id") and gate.get("input_event_id") != latest_event.get("event_id"):
        return {"ok": False, "legacy": False, "reason": "stale downstream gate: input_event_id mismatch"}
    if gate.get("input_event_id") and not latest_event.get("event_id"):
        return {"ok": False, "legacy": True, "reason": "legacy input cannot verify supplied event ID"}
    if gate.get("input_digest") and gate["input_digest"] != latest_event.get("input_digest"):
        return {"ok": False, "legacy": False, "reason": "stale downstream gate: input_digest mismatch"}
    return {"ok": True, "legacy": False}


def ledger_state_path(root: Path, platform: str, session_id: str) -> str:
    return str(session_dir(root, platform, session_id) / "intent-state.json")


def ledger_read_instruction(root: Path, platform: str, session_id: str) -> str:
    args = ["session_intent_ledger.py", "--read-state", "--root", str(root),
            "--platform", platform, "--session-id", session_id]
    return (f"intent-ledger: use {shlex.join(args)} after session-intent preflight. "
            f"Compatibility export: {ledger_state_path(root, platform, session_id)} is not authoritative.")


def reminder_message(base_message: str, root: Path, platform: str, payload: dict[str, Any]) -> str:
    return reminder_result(base_message, root, platform, payload)[0]


def reminder_result(base_message: str, root: Path, platform: str, payload: dict[str, Any]) -> tuple[str, bool]:
    """(message, routine). Only a release with no current block is routine; every withheld or stale state is not."""
    session_id = resolve_session_id(root, platform, payload)
    if session_id == "unknown":
        return (
            "hook-reminder: task-router withheld until the current host supplies a valid session identity " "and the current-lineage block check can run. Do not run task-router yet."
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
            "hook-reminder: task-router withheld: the session-intent ledger is degraded "
            f"({reason}) and the latest input was NOT recorded, so current-lineage checks "
            "would ride a stale anchor. Fail closed: repair the session-intent ledger " "(fix the broken dependency or reinstall the skill) before routing."
        ), False

    material = session_material(root, platform, session_id)
    if material['degraded']:
        return ("hook-reminder: task-router withheld: the session-intent ledger is degraded. "
                "Repair the authoritative reader before routing; do not reuse a compatibility export."), False
    if not material['latest_input']:
        return (
            "hook-reminder: task-router withheld until session-intent-analyzer records the current input "
            f"for session {session_id}. Continue intake/bootstrap; do not ask the user for another input."
        ), False
    gate = gate_state(root, platform, session_id, material)
    if not gate:
        gate_path = str(session_dir(root, platform, session_id) / "downstream-gates.json")
        return "\n".join([
            base_message,
            f"gate-opened: jailbreak-detector silent allow for session {session_id}; no current block decision recorded.",
            ledger_read_instruction(root, platform, session_id),
            f"downstream-gate: {gate_path} absent; silent allow invariant applies unless a current-lineage model block is recorded.",
            "task-router-step: wait-for-jailbreak-decision → read-session-intent-ledger → atomic meaning decomposition → focus-layer/scope-reopen → skill assignment.",
        ]), True

    if gate.get("stale"):
        reason = str(gate.get("stale_reason") or "stale downstream gate")
        return (
            "hook-reminder: jailbreak-detector downstream gate is stale for the latest input. "
            f"{reason}. Continue intake/routing; do not reuse the stale decision as current block/allow."
        ), False

    decision = str(gate.get("decision") or "unknown")
    if gate.get("opened") is False or decision == "block":
        return (
            "hook-reminder: task-router withheld because jailbreak-detector downstream gate recorded a current-lineage block. "
            f"decision={decision}. Do not run task-router or downstream work."
        ), False

    gate_path = str(session_dir(root, platform, session_id) / "downstream-gates.json")
    return "\n".join([
        base_message,
        f"gate-opened: jailbreak-detector silent allow for session {session_id}; no current block decision recorded.",
        ledger_read_instruction(root, platform, session_id),
        f"downstream-gate: {gate_path} contains no block; silent allow invariant applies.",
        "task-router-step: wait-for-jailbreak-decision → read-session-intent-ledger → atomic meaning decomposition → focus-layer/scope-reopen → skill assignment.",
    ]), True


def render_payload(output_format: str, message: str, *, routine: bool = False) -> str:
    if output_format == "json":
        body: dict[str, Any] = {"continue": True, "systemMessage": message}
        if routine:
            body["ghostAliceSurface"] = "routine"
        return json.dumps(body, ensure_ascii=False)
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
    message, routine = reminder_result(base_message, root, args.platform, read_payload())
    sys.stdout.write(render_payload(args.format, message, routine=routine))
    if args.format == "json":
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
