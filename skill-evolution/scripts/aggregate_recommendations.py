#!/usr/bin/env python3
"""Aggregate conduct_feedback across session ledgers into a report-only backlog.

Read authoritative session states through the ledger API when SQLite authority
is present. A root containing only portable JSON state files can supply offline
report input. Neither report input nor the resulting recommendations grants
execution permission. Merge entries by ID across sessions and expose occurrence
count, session count, recency and source. Order by occurrences, sessions and
recency without a fabricated priority weight. The consuming model judges priority
from those signals. This script returns candidates; the consuming model judges
and reports proposed changes. The script does not edit skills.

Dependencies: Python 3.11+ standard library and the Core helpers in `_shared`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from ledger_access import load_ledger  # noqa: E402
from timestamps import format_timestamp, parse_timestamp  # noqa: E402


def positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return parsed


def discover_root(start: Path | None = None) -> Path:
    """The ledger root the ledger would use from `start`: the environment, then the repository, then the legacy home root."""
    return load_ledger().default_root(cwd=(start or Path(__file__)).resolve().parent)


def iter_state_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/*/intent-state.json"))


def aggregate(root: Path, now: str | None = None) -> dict[str, Any]:
    now_dt = parse_timestamp(now) or datetime.now(timezone.utc)
    by_id: dict[str, dict[str, Any]] = {}
    state_files = iter_state_files(root)
    if (root / "ghost-state.sqlite3").exists() or (root / ".sqlite-authority.json").exists():
        records = load_ledger().list_session_states(root)
        session_count = len(records)
    else:
        records = []
        session_count = len(state_files)
        for state_file in state_files:
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                records.append({"session_id": state_file.parent.name, "platform": state_file.parent.parent.name, **data})
    for data in records:
        if not isinstance(data, dict):
            continue
        session = (str(data.get("platform") or "unknown"), str(data.get("session_id") or "unknown"))
        for entry in data.get("conduct_feedback", []):
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id:
                continue
            # A recorded pattern class groups differently named corrections of one failure family.
            pattern_class = str(entry.get("pattern_class") or "").strip()
            agg = by_id.setdefault(pattern_class or entry_id, {
                "id": pattern_class or entry_id,
                "pattern_class": pattern_class,
                "member_ids": set(),
                "summary": "",
                "failure_pattern": "",
                "corrective_rule": "",
                "sources": set(),
                "sessions": set(),
                "occurrence_count": 0,
                "statuses": [],
                "first_seen": None,
                "last_seen": None,
            })
            agg["member_ids"].add(entry_id)
            if entry.get("summary"):
                agg["summary"] = str(entry["summary"])
            if entry.get("failure_pattern"):
                agg["failure_pattern"] = str(entry["failure_pattern"])
            if entry.get("corrective_rule"):
                agg["corrective_rule"] = str(entry["corrective_rule"])
            agg["sources"].add(load_ledger().canonical_source(entry.get("source"), "user-said"))
            agg["sessions"].add(session)
            agg["occurrence_count"] += positive_int(entry.get("occurrence_count"), 1)
            agg["statuses"].append(str(entry.get("status") or "open"))
            ts = parse_timestamp(entry.get("updated_at")) or parse_timestamp(entry.get("created_at"))
            if ts is not None:
                if agg["first_seen"] is None or ts < agg["first_seen"]:
                    agg["first_seen"] = ts
                if agg["last_seen"] is None or ts > agg["last_seen"]:
                    agg["last_seen"] = ts

    recommendations: list[dict[str, Any]] = []
    for agg in by_id.values():
        open_now = any(status != "encoded" for status in agg["statuses"])
        if not open_now:
            continue
        days_since_last: float | None = None
        if agg["last_seen"] is not None:
            days_since_last = round((now_dt - agg["last_seen"]).total_seconds() / 86400.0, 2)
        recommendations.append({
            "id": agg["id"],
            "pattern_class": agg["pattern_class"] or None,
            "member_ids": sorted(agg["member_ids"]) if agg["pattern_class"] else [],
            "summary": agg["summary"],
            "corrective_rule": agg["corrective_rule"],
            "failure_pattern": agg["failure_pattern"],
            "sources": sorted(agg["sources"]),
            "session_count": len(agg["sessions"]),
            "occurrence_count": agg["occurrence_count"],
            "first_seen": format_timestamp(agg["first_seen"], microseconds=True) if agg["first_seen"] else None,
            "last_seen": format_timestamp(agg["last_seen"], microseconds=True) if agg["last_seen"] else None,
            "days_since_last": days_since_last,
            "decision": "necessity-gate",
        })

    # Objective default ordering only: most recurring first, then most recent, then id. No fabricated composite weight. The consuming model judges priority by reasoning over the signals (occurrences, sessions, recency).
    recommendations.sort(key=lambda item: item["id"])
    recommendations.sort(key=lambda item: item["last_seen"] or "", reverse=True)
    recommendations.sort(key=lambda item: item["session_count"], reverse=True)
    recommendations.sort(key=lambda item: item["occurrence_count"], reverse=True)
    return {
        "root": str(root),
        "session_files": session_count,
        "recommendation_count": len(recommendations),
        "recommendations": recommendations,
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"root: {report['root']}",
        f"session_files: {report['session_files']}",
        f"open update recommendations: {report['recommendation_count']} "
        "(ordered by occurrences, sessions, then recency; judge priority from the signals, not the order)",
    ]
    if not report["recommendations"]:
        lines.append("  (none yet; backlog accumulates as conduct_feedback is recorded)")
    for rec in report["recommendations"]:
        label = rec["corrective_rule"] or rec["summary"] or rec["failure_pattern"]
        lines.append(
            f"- {rec['id']}: {label} "
            f"(sessions={rec['session_count']}, occurrences={rec['occurrence_count']}, "
            f"sources={'/'.join(rec['sources'])}, last_seen={rec['last_seen']}, "
            f"days_since_last={rec['days_since_last']}) -> {rec['decision']}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate conduct_feedback across session ledgers into a ranked update-recommendation backlog.",
    )
    parser.add_argument("--root", default="", help="session-intent root (default: auto-discover <repo>/.tmp/session-intent)")
    parser.add_argument("--now", default="", help="reference time ISO8601 for recency scoring (default: current UTC)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root) if args.root else discover_root()
    report = aggregate(root, now=args.now or None)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
