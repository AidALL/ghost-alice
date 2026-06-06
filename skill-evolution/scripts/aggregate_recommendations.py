#!/usr/bin/env python3
"""Aggregate conduct_feedback across every session ledger into one ranked
    update-recommendation backlog.

The per-session ledger (`.tmp/session-intent/<platform>/<session-id>/intent-state.json`)
records conduct_feedback entries: user-explicit corrections and inferred
intent-vs-skill gaps. This script scans all of them, merges by entry id across
sessions, and surfaces the open recommendations with objective signals: how many
occurrences and sessions hit the same correction, how recently, and which
source. It orders them by occurrences, sessions, then recency as a transparent
default; it does not fabricate a
priority weight. The consuming model judges priority by reasoning over the
signals. Report-only: it proposes, it does not edit skills.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return parsed


def discover_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        candidate = parent / ".tmp" / "session-intent"
        if candidate.exists():
            return candidate
    return Path("~/.ghost-alice/session-intent").expanduser()


def iter_state_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/*/intent-state.json"))


def aggregate(root: Path, now: str | None = None) -> dict[str, Any]:
    now_dt = parse_ts(now) or datetime.now(timezone.utc)
    by_id: dict[str, dict[str, Any]] = {}
    state_files = iter_state_files(root)
    for state_file in state_files:
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        session = str(data.get("session_id") or state_file.parent.name)
        for entry in data.get("conduct_feedback", []):
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id:
                continue
            agg = by_id.setdefault(entry_id, {
                "id": entry_id,
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
            if entry.get("summary"):
                agg["summary"] = str(entry["summary"])
            if entry.get("failure_pattern"):
                agg["failure_pattern"] = str(entry["failure_pattern"])
            if entry.get("corrective_rule"):
                agg["corrective_rule"] = str(entry["corrective_rule"])
            agg["sources"].add(str(entry.get("source") or "user-explicit"))
            agg["sessions"].add(session)
            agg["occurrence_count"] += positive_int(entry.get("occurrence_count"), 1)
            agg["statuses"].append(str(entry.get("status") or "open"))
            ts = parse_ts(entry.get("updated_at")) or parse_ts(entry.get("created_at"))
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
            "summary": agg["summary"],
            "corrective_rule": agg["corrective_rule"],
            "failure_pattern": agg["failure_pattern"],
            "sources": sorted(agg["sources"]),
            "session_count": len(agg["sessions"]),
            "occurrence_count": agg["occurrence_count"],
            "first_seen": iso_z(agg["first_seen"]),
            "last_seen": iso_z(agg["last_seen"]),
            "days_since_last": days_since_last,
            "decision": "necessity-gate",
        })

    # Objective default ordering only: most recurring first, then most recent,
    # then id. No fabricated composite weight. The consuming model judges
    # priority by reasoning over the signals (occurrences, sessions, recency).
    recommendations.sort(key=lambda item: item["id"])
    recommendations.sort(key=lambda item: item["last_seen"] or "", reverse=True)
    recommendations.sort(key=lambda item: item["session_count"], reverse=True)
    recommendations.sort(key=lambda item: item["occurrence_count"], reverse=True)
    return {
        "root": str(root),
        "session_files": len(state_files),
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
