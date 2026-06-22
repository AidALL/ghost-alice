#!/usr/bin/env python3
"""Maintain a session-local semantic intent ledger.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "session-intent-ledger.v1"
CURRENT_SESSION_SCHEMA = "session-intent-current.v1"
SESSION_INTENT_ROOT_ENV = "GHOST_ALICE_SESSION_INTENT_ROOT"
LEGACY_DEFAULT_ROOT = Path("~/.ghost-alice/session-intent")
TEXT_FIELDS = ("current_goal", "user_intent_summary")
LIST_FIELDS = ("constraints", "non_goals", "open_questions", "risk_flags")
ACCEPTANCE_CRITERIA_SOURCES = {"user-explicit", "inferred", "previous-tool", "system-doc"}
SECURITY_DECISIONS = {"allow", "block"}
SECURITY_REASON_MAX = 240
SECURITY_RISK_FLAG_MAX = 12
CONDUCT_FEEDBACK_SOURCES = {"user-explicit", "inferred"}
CONDUCT_FEEDBACK_STATUS = {"open", "encoded"}
SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.=-]+")


def is_ghost_alice_repo_root(path: Path) -> bool:
    return (
        (path / "install.sh").exists()
        and (path / "skill-catalog").is_dir()
        and (path / "session-intent-analyzer").is_dir()
    )


def discover_repo_root(cwd: Path | None = None) -> Path | None:
    start = Path(cwd) if cwd is not None else Path.cwd()
    start = start.expanduser().resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if is_ghost_alice_repo_root(candidate):
            return candidate
    script_root = Path(__file__).resolve()
    for candidate in script_root.parents:
        if is_ghost_alice_repo_root(candidate):
            return candidate
    return None


def default_root(
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    source_env = os.environ if env is None else env
    configured = source_env.get(SESSION_INTENT_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    repo = discover_repo_root(cwd)
    if repo is not None:
        return repo / ".tmp" / "session-intent"
    return LEGACY_DEFAULT_ROOT.expanduser()


DEFAULT_ROOT = default_root()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_component(value: str | None, fallback: str = "unknown") -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    cleaned = SAFE_COMPONENT.sub("-", text).strip(".-")
    return cleaned[:120] or fallback


def input_digest(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return input_digest(encoded)


def build_input_observation(*, platform: str, session_id: str, raw_user_input: str | None) -> dict[str, Any]:
    observed_at = utc_now()
    observation: dict[str, Any] = {
        "observed_at": observed_at,
        "input_event_id": "",
        "input_digest": "",
        "input_char_count": 0,
    }
    if raw_user_input is None:
        return observation
    digest = input_digest(raw_user_input)
    observation["input_digest"] = digest
    observation["input_char_count"] = len(raw_user_input)
    observation["input_event_id"] = json_digest({
        "event": "user-input-observed",
        "platform": platform,
        "session_id": session_id,
        "input_digest": digest,
        "observed_at": observed_at,
        "event_nonce": uuid.uuid4().hex,
    })
    return observation


def session_paths(root: Path, platform: str, session_id: str) -> dict[str, Path]:
    session_dir = root.expanduser() / safe_component(platform) / safe_component(session_id)
    return {
        "dir": session_dir,
        "state": session_dir / "intent-state.json",
        "events": session_dir / "intent-events.jsonl",
        "security": session_dir / "security-events.jsonl",
    }


def current_session_pointer_path(root: Path, platform: str) -> Path:
    return root.expanduser() / safe_component(platform) / "current-session.json"


def write_current_session_pointer(root: Path, platform: str, session_id: str) -> Path:
    paths = session_paths(root, platform, session_id)
    pointer = current_session_pointer_path(root, platform)
    payload = {
        "schema_version": CURRENT_SESSION_SCHEMA,
        "platform": platform,
        "session_id": safe_component(session_id),
        "state_path": str(paths["state"]),
        "updated_at": utc_now(),
    }
    write_json(pointer, payload)
    return pointer


def read_current_session_pointer(root: Path, platform: str) -> str:
    path = current_session_pointer_path(root, platform)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("schema_version") != CURRENT_SESSION_SCHEMA:
        return ""
    return safe_component(str(data.get("session_id") or ""), "")


def resolve_session_id(
    *,
    root: Path = DEFAULT_ROOT,
    platform: str,
    explicit: str | None = None,
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    payload = payload or {}
    env = env or {}
    for candidate in (
        explicit,
        payload.get("session_id"),
        payload.get("sessionId"),
        payload.get("conversation_id"),
        payload.get("thread_id"),
        env.get("GHOST_ALICE_SESSION_ID"),
        read_current_session_pointer(root, platform),
    ):
        value = safe_component(str(candidate or ""), "")
        if value:
            return value
    return "unknown"


def default_state(platform: str, session_id: str) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "session_id": session_id,
        "created_at": now,
        "updated_at": now,
        "current_goal": "",
        "user_intent_summary": "",
        "constraints": [],
        "non_goals": [],
        "open_questions": [],
        "acceptance_criteria": [],
        "decisions": [],
        "conduct_feedback": [],
        "risk_flags": [],
        "consumer_hints": {},
        "model_security_decision": None,
        "intake_status": "pending",
        "last_intake_source": "",
        "last_semantic_delta_status": "not-provided",
        "semantic_delta_policy": "agent-updates-when-intent-materially-changes",
    }


def load_state(path: Path, platform: str = "unknown", session_id: str = "unknown") -> dict[str, Any]:
    if not path.exists():
        return default_state(platform, session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state(platform, session_id)
    if not isinstance(data, dict):
        return default_state(platform, session_id)
    state = default_state(
        str(data.get("platform") or platform),
        str(data.get("session_id") or session_id),
    )
    state.update(data)
    state["schema_version"] = SCHEMA_VERSION
    return state


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def merge_unique(existing: Any, incoming: Any) -> list[Any]:
    """Merge list-field entries without destroying their shape.

    Plain strings are kept as strings (deduped by value). Structured entries
    (dicts, e.g. {"id", "summary"}) are kept as objects and deduped by their
    "id" when present, else by a normalized JSON form. This only preserves the
    shape supplied by the caller; it does not impose or validate a schema.
    First occurrence wins, matching the prior string-dedup behavior.
    """
    values: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for source in (existing, incoming):
        if source is None:
            continue
        if isinstance(source, (str, dict)):
            iterable: list[Any] = [source]
        elif isinstance(source, list):
            iterable = source
        else:
            iterable = [source]
        for value in iterable:
            if isinstance(value, dict):
                entry_id = value.get("id")
                if isinstance(entry_id, str) and entry_id.strip():
                    key = ("id", entry_id.strip())
                else:
                    key = ("obj", json.dumps(value, sort_keys=True, ensure_ascii=False))
            else:
                text = str(value).strip()
                if not text:
                    continue
                key = ("str", text)
                value = text
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
    return values


def merge_consumer_hints(existing: Any, incoming: Any) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    if isinstance(existing, dict):
        for key, value in existing.items():
            merged[str(key)] = merge_unique([], value)
    if isinstance(incoming, dict):
        for key, value in incoming.items():
            merged[str(key)] = merge_unique(merged.get(str(key), []), value)
    return merged


def normalize_decision(raw: Any, timestamp: str) -> dict[str, Any] | None:
    if isinstance(raw, str):
        decision_id = safe_component(raw.lower(), "decision")
        summary = raw.strip()
        payload: dict[str, Any] = {"id": decision_id, "summary": summary}
    elif isinstance(raw, dict):
        payload = dict(raw)
        if not payload.get("id"):
            payload["id"] = safe_component(str(payload.get("summary") or "decision").lower(), "decision")
    else:
        return None
    payload["id"] = safe_component(str(payload["id"]), "decision")
    payload.setdefault("summary", "")
    payload.setdefault("created_at", timestamp)
    payload["updated_at"] = timestamp
    payload.setdefault("superseded", False)
    return payload


def normalize_acceptance_criterion(raw: Any) -> dict[str, str] | None:
    if isinstance(raw, str):
        summary = raw.strip()
        if not summary:
            return None
        return {
            "id": safe_component(summary.lower(), "criterion"),
            "summary": summary,
            "source": "inferred",
        }
    if not isinstance(raw, dict):
        return None
    summary = str(raw.get("summary") or raw.get("text") or "").strip()
    criterion_id = safe_component(str(raw.get("id") or summary.lower()), "criterion")
    if not summary:
        return None
    source = str(raw.get("source") or "inferred").strip()
    if source not in ACCEPTANCE_CRITERIA_SOURCES:
        source = "inferred"
    return {
        "id": criterion_id,
        "summary": summary,
        "source": source,
    }


def merge_acceptance_criteria(existing: Any, incoming: Any) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for source in (existing, incoming):
        iterable = source if isinstance(source, list) else [source]
        for item in iterable:
            criterion = normalize_acceptance_criterion(item)
            if criterion is None:
                continue
            criterion_id = criterion["id"]
            if criterion_id not in merged:
                order.append(criterion_id)
            merged[criterion_id] = criterion
    return [merged[criterion_id] for criterion_id in order]


def normalize_security_decision(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    decision = str(raw.get("decision", "")).strip().lower()
    if decision not in SECURITY_DECISIONS:
        return None
    flags: list[str] = []
    raw_flags = raw.get("risk_flags")
    if isinstance(raw_flags, list):
        for value in raw_flags:
            text = safe_component(str(value).lower(), "")
            if text and text not in flags:
                flags.append(text)
            if len(flags) >= SECURITY_RISK_FLAG_MAX:
                break
    decision_out: dict[str, Any] = {
        "decision": decision,
        "risk_flags": flags,
        "reason": str(raw.get("reason", "")).strip()[:SECURITY_REASON_MAX],
    }
    for key in ("input_event_id", "input_digest", "recorded_at"):
        value = str(raw.get(key, "")).strip()
        if value:
            decision_out[key] = value
    return decision_out


def normalize_conduct_feedback(raw: Any, timestamp: str) -> dict[str, Any] | None:
    if isinstance(raw, str):
        rule = raw.strip()
        if not rule:
            return None
        payload: dict[str, Any] = {
            "id": rule.lower(),
            "summary": rule,
            "corrective_rule": rule,
        }
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        return None
    summary = str(payload.get("summary") or "").strip()
    rule = str(payload.get("corrective_rule") or summary).strip()
    pattern = str(payload.get("failure_pattern") or "").strip()
    entry_id = str(payload.get("id") or rule or pattern or summary).strip()
    if not entry_id:
        return None
    source = str(payload.get("source") or "user-explicit").strip()
    if source not in CONDUCT_FEEDBACK_SOURCES:
        source = "user-explicit"
    status = str(payload.get("status") or "open").strip()
    if status not in CONDUCT_FEEDBACK_STATUS:
        status = "open"
    return {
        "id": safe_component(entry_id, "conduct"),
        "summary": summary or rule or pattern,
        "failure_pattern": pattern,
        "corrective_rule": rule,
        "source": source,
        "status": status,
        "occurrence_count": positive_int(payload.get("occurrence_count"), 1),
        "updated_at": timestamp,
    }


def positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return parsed


def conduct_feedback_marks_occurrence(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return True
    if "occurrence_count" in raw:
        return True
    for field in ("summary", "failure_pattern", "corrective_rule"):
        if str(raw.get(field) or "").strip():
            return True
    return False


def merge_conduct_feedback(existing: Any, incoming: Any, timestamp: str) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in existing if isinstance(existing, list) else []:
        if isinstance(item, dict) and item.get("id"):
            key = safe_component(str(item["id"]), "conduct")
            by_id[key] = dict(item)
            by_id[key]["occurrence_count"] = positive_int(by_id[key].get("occurrence_count"), 1)
            if key not in order:
                order.append(key)
    for raw in incoming if isinstance(incoming, list) else [incoming]:
        normalized = normalize_conduct_feedback(raw, timestamp)
        if normalized is None:
            continue
        key = normalized["id"]
        if key in by_id:
            current = by_id[key]
            if normalized["summary"]:
                current["summary"] = normalized["summary"]
            if normalized["failure_pattern"]:
                current["failure_pattern"] = normalized["failure_pattern"]
            if normalized["corrective_rule"]:
                current["corrective_rule"] = normalized["corrective_rule"]
            if isinstance(raw, dict) and "source" in raw:
                current["source"] = normalized["source"]
            if isinstance(raw, dict) and "status" in raw:
                current["status"] = normalized["status"]
            if conduct_feedback_marks_occurrence(raw):
                current["occurrence_count"] = positive_int(
                    current.get("occurrence_count"), 1
                ) + positive_int(normalized.get("occurrence_count"), 1)
            current["updated_at"] = timestamp
        else:
            normalized["created_at"] = timestamp
            by_id[key] = normalized
            order.append(key)
    return [by_id[key] for key in order]


def apply_delta(state: dict[str, Any], delta: dict[str, Any] | None) -> dict[str, Any]:
    if not delta:
        state["updated_at"] = utc_now()
        return state

    now = utc_now()
    for field in TEXT_FIELDS:
        value = delta.get(field)
        if isinstance(value, str) and value.strip():
            state[field] = value.strip()

    for field in LIST_FIELDS:
        if field in delta:
            state[field] = merge_unique(state.get(field, []), delta.get(field))

    if "acceptance_criteria" in delta:
        state["acceptance_criteria"] = merge_acceptance_criteria(
            state.get("acceptance_criteria", []),
            delta.get("acceptance_criteria"),
        )

    if "consumer_hints" in delta:
        state["consumer_hints"] = merge_consumer_hints(state.get("consumer_hints", {}), delta.get("consumer_hints"))

    if "conduct_feedback" in delta:
        state["conduct_feedback"] = merge_conduct_feedback(
            state.get("conduct_feedback", []), delta.get("conduct_feedback"), now
        )

    if "model_security_decision" in delta:
        normalized = normalize_security_decision(delta.get("model_security_decision"))
        if normalized is not None:
            normalized.setdefault("recorded_at", now)
            state["model_security_decision"] = normalized

    if isinstance(delta.get("latest_scope"), dict):
        state["latest_scope"] = delta["latest_scope"]

    existing_decisions: list[dict[str, Any]] = [
        item for item in state.get("decisions", []) if isinstance(item, dict)
    ]
    decision_by_id = {str(item.get("id")): item for item in existing_decisions if item.get("id")}
    incoming_decisions = [
        normalized for item in delta.get("decisions", [])
        if (normalized := normalize_decision(item, now)) is not None
    ] if isinstance(delta.get("decisions"), list) else []

    replacement_id = incoming_decisions[0]["id"] if incoming_decisions else ""
    supersedes = delta.get("supersedes", [])
    if isinstance(supersedes, str):
        supersedes = [supersedes]
    if isinstance(supersedes, list):
        for old_id in supersedes:
            old_key = str(old_id)
            if old_key in decision_by_id:
                decision_by_id[old_key]["superseded"] = True
                decision_by_id[old_key]["superseded_at"] = now
                if replacement_id:
                    decision_by_id[old_key]["superseded_by"] = replacement_id

    for decision in incoming_decisions:
        current = decision_by_id.get(decision["id"], {})
        current.update(decision)
        current.setdefault("superseded", False)
        decision_by_id[decision["id"]] = current

    if decision_by_id:
        state["decisions"] = list(decision_by_id.values())

    state["updated_at"] = now
    return state


def record_turn(
    *,
    root: Path = DEFAULT_ROOT,
    platform: str,
    session_id: str,
    raw_user_input: str | None = None,
    intent_delta: dict[str, Any] | None = None,
    source: str = "agent",
    observation: dict[str, Any] | None = None,
) -> dict[str, Path]:
    observation = observation or build_input_observation(
        platform=platform,
        session_id=session_id,
        raw_user_input=raw_user_input,
    )
    paths = session_paths(root, platform, session_id)
    state = load_state(paths["state"], platform=platform, session_id=session_id)
    state["platform"] = platform
    state["session_id"] = session_id
    state = apply_delta(state, intent_delta)
    state["intake_status"] = "observed"
    state["last_intake_source"] = source
    state["last_semantic_delta_status"] = "recorded" if intent_delta else "not-provided"
    state["semantic_delta_policy"] = "agent-updates-when-intent-materially-changes"
    write_json(paths["state"], state)

    event: dict[str, Any] = {
        "ts": observation["observed_at"],
        "event": "user-input-observed" if raw_user_input is not None else "intent-updated",
        "platform": platform,
        "session_id": session_id,
        "source": source,
        "intent_delta_status": "recorded" if intent_delta else "not-provided",
    }
    if raw_user_input is not None:
        event["event_id"] = observation["input_event_id"]
        event["input_digest"] = observation["input_digest"]
        event["input_char_count"] = observation["input_char_count"]
    if intent_delta:
        event["intent_delta_digest"] = json_digest(intent_delta)
        event["delta_keys"] = sorted(str(key) for key in intent_delta.keys())
    append_jsonl(paths["events"], event)
    if safe_component(session_id) != "unknown":
        write_current_session_pointer(root, platform, session_id)
    return paths


def consumer_snapshot(state_or_path: dict[str, Any] | Path) -> dict[str, Any]:
    if isinstance(state_or_path, Path):
        state = load_state(state_or_path)
    else:
        state = state_or_path
    decisions = [item for item in state.get("decisions", []) if isinstance(item, dict)]
    active_decisions = [item for item in decisions if not item.get("superseded")]
    return {
        "schema_version": SCHEMA_VERSION,
        "current_goal": state.get("current_goal", ""),
        "user_intent_summary": state.get("user_intent_summary", ""),
        "constraints": list(state.get("constraints", [])),
        "non_goals": list(state.get("non_goals", [])),
        "open_questions": list(state.get("open_questions", [])),
        "acceptance_criteria": list(state.get("acceptance_criteria", [])),
        "decision_count": len(active_decisions),
        "risk_flags": list(state.get("risk_flags", [])),
        "consumer_hints": dict(state.get("consumer_hints", {})),
        "conduct_feedback": list(state.get("conduct_feedback", [])),
        "model_security_decision": state.get("model_security_decision"),
        "intake_status": state.get("intake_status", "pending"),
        "last_semantic_delta_status": state.get("last_semantic_delta_status", "not-provided"),
        "semantic_delta_policy": state.get(
            "semantic_delta_policy",
            "agent-updates-when-intent-materially-changes",
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update or read a session intent ledger.")
    parser.add_argument("--root", default=str(default_root()), help="ledger root")
    parser.add_argument("--platform", default="codex", help="agent platform")
    parser.add_argument("--session-id", default="", help="session identifier")
    parser.add_argument("--input", default=None, help="raw input to hash only; never persisted")
    parser.add_argument("--delta-json", default=None, help="intent delta JSON")
    parser.add_argument("--snapshot", action="store_true", help="emit consumer snapshot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    delta = None
    if args.delta_json:
        delta = json.loads(args.delta_json)
        if not isinstance(delta, dict):
            raise SystemExit("--delta-json must decode to an object")
    root = Path(args.root)
    session_id = resolve_session_id(
        root=root,
        platform=args.platform,
        explicit=args.session_id,
    )
    if args.snapshot and args.input is None and delta is None:
        paths = session_paths(root, args.platform, session_id)
        state = load_state(paths["state"], platform=args.platform, session_id=session_id)
        print(json.dumps(consumer_snapshot(state), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    paths = record_turn(
        root=root,
        platform=args.platform,
        session_id=session_id,
        raw_user_input=args.input,
        intent_delta=delta,
        source="cli",
    )
    if args.snapshot:
        print(json.dumps(consumer_snapshot(paths["state"]), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps({key: str(value) for key, value in paths.items()}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
