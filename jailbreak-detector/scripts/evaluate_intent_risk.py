#!/usr/bin/env python3
"""Carry a model-recorded security decision into a gate result.

The jailbreak decision is made by the model (jailbreak-detector / session-intent-
analyzer) and recorded in the session intent ledger as `model_security_decision`.
This module performs NO keyword, regex, or heuristic matching after the
model-recorded security decision migration:
it only carries the recorded decision.

- Block comes solely from the model record (model-record-only block invariant).
- Absence of a block decision means allow (absence-means-allow invariant).
- Raw prompt text never enters the decision path (raw-input-exclusion invariant): the
  decision is read from the structured record, not from intent_summary text.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _decision_record(current: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    """Return the model-recorded security decision, preferring the current turn."""
    for source in (current, state):
        if isinstance(source, dict):
            record = source.get("model_security_decision")
            if isinstance(record, dict):
                return record
    return None


def evaluate(
    current: dict[str, Any],
    *,
    state_path: Path | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Carry the model's recorded security decision. No matching, no heuristics."""
    loaded_state = state if state is not None else load_state(state_path)
    record = _decision_record(current if isinstance(current, dict) else {}, loaded_state)
    if record is not None and str(record.get("decision", "")).strip().lower() == "block":
        rules = sorted({str(flag).strip() for flag in record.get("risk_flags", []) if str(flag).strip()})
        return {
            "decision": "block",
            "rules": rules,
            "state_goal": loaded_state.get("current_goal", ""),
            "evidence_summary": "carried model block decision; raw prompt omitted",
        }
    return {
        "decision": "allow",
        "rules": [],
        "state_goal": loaded_state.get("current_goal", ""),
        "evidence_summary": "no model block decision recorded",
    }


def hash_text(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def append_security_event(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "event": "intent-risk-evaluated",
        "decision": result.get("decision", "allow"),
        "rules": list(result.get("rules", [])),
        "state_goal_digest": hash_text(str(result.get("state_goal", ""))),
        "evidence_summary": result.get("evidence_summary", ""),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Carry a model-recorded security decision into a gate result.")
    parser.add_argument("--state", default="", help="intent-state.json path")
    parser.add_argument("--current-json", default="", help="current intent summary JSON")
    parser.add_argument("--security-log", default="", help="optional security-events.jsonl path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.current_json:
        current = json.loads(args.current_json)
    else:
        current = json.loads(sys.stdin.read() or "{}")
    if not isinstance(current, dict):
        raise SystemExit("current intent must be a JSON object")
    state_path = Path(args.state) if args.state else None
    result = evaluate(current, state_path=state_path)
    if args.security_log:
        append_security_event(Path(args.security_log), result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
