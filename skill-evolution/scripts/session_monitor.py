#!/usr/bin/env python3
"""Report-only monitor for loop, scope, and repeated failure signals.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_WINDOW = 1000


def load_jsonl(path: Path, window: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.expanduser().open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    if window <= 0:
        return rows
    return rows[-window:]


def text_value(row: dict[str, Any], *keys: str) -> str:
    tool_input = row.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    for key in keys:
        value = row.get(key, tool_input.get(key))
        if isinstance(value, str) and value:
            return value
    return ""


def session_id(row: dict[str, Any]) -> str:
    return text_value(row, "session", "session_id", "thread_id") or "global"


def tool_name(row: dict[str, Any]) -> str:
    return text_value(row, "tool", "tool_name", "name") or "unknown"


def pattern_value(row: dict[str, Any]) -> str:
    return text_value(row, "pattern", "command", "cmd", "query")


def path_value(row: dict[str, Any]) -> str:
    return text_value(row, "path", "file_path", "cwd", "workdir")


def exit_failed(row: dict[str, Any]) -> bool:
    value = row.get("exit_code")
    if isinstance(value, int):
        return value != 0
    value = row.get("status")
    if isinstance(value, str):
        return value.lower() in {"failed", "error", "nonzero"}
    return False


def signal(signal_id: str, severity: str, evidence_count: int, message: str) -> dict[str, Any]:
    return {
        "id": signal_id,
        "severity": severity,
        "evidence_count": evidence_count,
        "message": message,
        "mode": "report-only",
    }


def analyze(rows: list[dict[str, Any]], recent_window: int = 5) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    recent = rows[-recent_window:]
    recent_patterns = Counter(
        (tool_name(row), pattern_value(row))
        for row in recent
        if tool_name(row) != "unknown" and pattern_value(row)
    )
    if recent_patterns:
        (_, _), count = recent_patterns.most_common(1)[0]
        if count >= 3:
            signals.append(
                signal(
                    "loop:tool-pattern",
                    "warning",
                    count,
                    "same tool and pattern repeated in recent window; choose next action deliberately",
                )
            )

    paths_by_session: dict[str, set[str]] = defaultdict(set)
    failures = Counter()
    for row in rows:
        path = path_value(row)
        if path and path != "n/a":
            paths_by_session[session_id(row)].add(path)
        command = pattern_value(row)
        if exit_failed(row) and command:
            failures[(session_id(row), command)] += 1

    if paths_by_session:
        _, paths = max(paths_by_session.items(), key=lambda item: len(item[1]))
        if len(paths) > 20:
            signals.append(
                signal(
                    "scope:file-surface",
                    "notice",
                    len(paths),
                    "large file surface in one session; verify scope before continuing",
                )
            )

    if failures:
        (_, _), count = failures.most_common(1)[0]
        if count >= 3:
            signals.append(
                signal(
                    "test:repeated-failure",
                    "critical",
                    count,
                    "same failing test command repeated; switch to root-cause debugging",
                )
            )

    signals.sort(key=lambda item: (item["severity"], item["id"]))
    return {"event_count": len(rows), "signals": signals}


def render_text(report: dict[str, Any]) -> str:
    lines = [f"event_count: {report['event_count']}", "signals:"]
    for item in report["signals"]:
        lines.append(f"- {item['severity']} {item['id']} {item['evidence_count']}: {item['message']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze session loop and scope signals from io-trace JSONL.")
    parser.add_argument("path", nargs="?", default=str(Path("~/.ghost-alice/io-trace.jsonl")))
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = analyze(load_jsonl(Path(args.path), args.window))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
