#!/usr/bin/env python3
"""Analyze Ghost-ALICE io-trace JSONL and emit report-only instinct candidates.

Dependencies: Python 3.11+ standard library and the Core helpers in `_shared`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
import install_destination  # noqa: E402
from ledger_access import load_ledger  # noqa: E402
from text_lines import committed_lines  # noqa: E402


def default_trace() -> Path:
    """The io-trace file under the Ghost-ALICE root the installation names."""
    return install_destination.ghost_root() / "io-trace.jsonl"


DEFAULT_WINDOW = 1000
DEFAULT_MIN_COUNT = 2
EMPTY_INTENT_CONTEXT: dict[str, Any] = {
    "schema_version": "session-intent-ledger.v1",
    "current_goal": "",
    "constraints": [],
    "non_goals": [],
    "open_questions": [],
    "decision_count": 0,
    "active_decisions": [],
    "latest_scope": {},
    "conduct_feedback": [],
}
SEQUENCE_SIZE = 3
TEST_LOOP_PATTERN = re.compile(
    r"\b(pytest|unittest|npm test|pnpm test|yarn test|cargo test|go test|swift test|xcodebuild test)\b"
    r"|(^|\s)tests?/",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Event:
    index: int
    session: str
    tool: str
    tool_id: str
    domain: str
    scope: str
    pattern: str


def slug(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    normalized = re.sub(r"[^a-z0-9.]+", "-", normalized)
    return normalized.strip("-") or "unknown"


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value:
                return value
            continue
        return str(value)
    return ""


def load_jsonl(path: Path, window: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in committed_lines(path.expanduser().read_bytes()).values():
        try:
            value = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    if window <= 0:
        return rows
    return rows[-window:]


def normalize_event(raw: dict[str, Any], index: int) -> Event | None:
    tool_input = raw.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    tool = first_text(
        raw.get("tool"),
        raw.get("tool_name"),
        raw.get("recipient_name"),
        raw.get("name"),
    )
    if not tool:
        return None

    path = first_text(
        raw.get("path"),
        tool_input.get("path"),
        tool_input.get("file_path"),
        tool_input.get("workdir"),
        raw.get("cwd"),
    )
    cwd = first_text(raw.get("cwd"), tool_input.get("cwd"), tool_input.get("workdir"))
    pattern = first_text(
        raw.get("pattern"),
        tool_input.get("cmd"),
        tool_input.get("command"),
        tool_input.get("query"),
        tool_input.get("q"),
    )
    session = first_text(raw.get("session"), raw.get("session_id"), raw.get("thread_id"), "global")
    domain = infer_domain(tool, path, pattern)
    scope = infer_scope(path, cwd)
    return Event(
        index=index,
        session=session,
        tool=tool,
        tool_id=slug(tool),
        domain=domain,
        scope=scope,
        pattern=pattern,
    )


def infer_domain(tool: str, path: str, pattern: str) -> str:
    combined = f"{tool} {path} {pattern}".lower()
    suffix = Path(path).suffix.lower() if path and path != "n/a" else ""
    if suffix == ".py" or "pytest" in combined or "python" in combined:
        return "python"
    if suffix in {".md", ".mdx", ".rst", ".txt"}:
        return "docs"
    if suffix in {".json", ".jsonl", ".toml", ".yaml", ".yml"}:
        return "config"
    if "bash" in combined or "exec-command" in slug(tool):
        return "shell"
    return "general"


def infer_scope(path: str, cwd: str = "") -> str:
    for candidate in (path, cwd):
        if not candidate or candidate == "n/a":
            continue
        scope = scope_from_path(candidate)
        if scope:
            return scope
    return "global"


def scope_from_path(value: str) -> str:
    candidate = Path(value).expanduser()
    parts = [part for part in candidate.parts if part not in {"", "/", "\\", candidate.anchor, ".", "~"}]
    if not parts:
        return ""
    if parts[0] in {"Users", "home"} and len(parts) >= 3:
        parts = parts[2:]
    while parts and parts[0] in {"tmp", "var", "private", "work"}:
        parts = parts[1:]
    if not parts:
        return ""
    if len(parts) == 1:
        return clean_scope(parts[0])
    first = clean_scope(parts[0])
    if first:
        return first
    return clean_scope(Path(value).parent.name)


def clean_scope(value: str) -> str:
    if not value or value == "n/a":
        return ""
    if Path(value).suffix:
        return ""
    return slug(value)


def most_common_scope(events: list[Event]) -> str:
    scopes = [event.scope for event in events if event.scope and event.scope != "global"]
    if not scopes:
        return "global"
    return Counter(scopes).most_common(1)[0][0]


def quality_metadata(events: list[Event], count: int) -> dict[str, Any]:
    session_count = len({event.session for event in events if event.session})
    reasons: list[str] = []
    if session_count >= 2:
        reasons.append("cross-session-evidence")
    else:
        reasons.append("single-session-evidence")
    if count < 3:
        reasons.append("low-occurrence-count")
    if is_debugging_or_test_loop(events):
        reasons.append("debugging-or-test-loop")

    if "debugging-or-test-loop" in reasons and session_count < 2:
        quality = "reject"
        suggested_next_step = "route-to-systematic-debugging"
    elif session_count >= 2:
        quality = "review"
        suggested_next_step = "necessity-gate"
    else:
        quality = "watch"
        suggested_next_step = "observe-more"

    return {
        "quality": quality,
        "suggested_next_step": suggested_next_step,
        "session_count": session_count,
        "quality_reasons": reasons,
    }


def is_debugging_or_test_loop(events: list[Event]) -> bool:
    tool_ids = {event.tool_id for event in events}
    if len(tool_ids) != 1:
        return False
    pattern_text = "\n".join(event.pattern for event in events if event.pattern)
    if not pattern_text:
        return False
    return bool(TEST_LOOP_PATTERN.search(pattern_text))


def analyze_events(events: list[Event], window: int, min_count: int) -> dict[str, Any]:
    instincts: list[dict[str, Any]] = []
    tool_counts: Counter[str] = Counter(event.tool_id for event in events)
    tool_display = {event.tool_id: event.tool for event in events}
    tool_scopes: dict[str, list[Event]] = defaultdict(list)
    workflow_counts: Counter[tuple[str, str, str]] = Counter()
    workflow_events: dict[tuple[str, str, str], list[Event]] = defaultdict(list)
    total = len(events)

    for event in events:
        tool_scopes[event.tool_id].append(event)
        workflow_key = (event.tool_id, event.domain, event.scope)
        workflow_counts[workflow_key] += 1
        workflow_events[workflow_key].append(event)

    for tool_id, count in sorted(tool_counts.items()):
        if count < min_count:
            continue
        display = tool_display.get(tool_id, tool_id)
        candidate = {
            "id": f"tool:{tool_id}",
            "trigger": f"{display} used frequently",
            "domain": "tool-usage",
            "scope": most_common_scope(tool_scopes[tool_id]),
            "evidence": f"{count} occurrences",
        }
        candidate.update(quality_metadata(tool_scopes[tool_id], count))
        instincts.append(candidate)

    for (tool_id, domain, scope), count in sorted(workflow_counts.items()):
        if count < min_count:
            continue
        workflow_key = (tool_id, domain, scope)
        candidate = {
            "id": f"workflow:{tool_id}:{domain}",
            "trigger": f"{tool_id} in {domain}",
            "domain": "workflow",
            "scope": scope,
            "evidence": f"{count} occurrences",
        }
        candidate.update(quality_metadata(workflow_events[workflow_key], count))
        instincts.append(candidate)

    instincts.extend(sequence_instincts(events, min_count))
    instincts.sort(key=lambda item: (item["domain"], item["id"], item["scope"]))
    quality_summary = {"review": 0, "watch": 0, "reject": 0}
    for item in instincts:
        quality_summary[item["quality"]] += 1
    return {
        "window": window,
        "event_count": total,
        "quality_summary": quality_summary,
        "instincts": instincts,
    }


def load_intent_context(path: Path | None) -> dict[str, Any] | None:
    """Read decision entries recorded by the response model, without inferring who decided.

    Only each entry's optional source claims who decided; Core code does not require it.
    active_decisions contains entries no later entry superseded, and decision_count counts them.
    """
    if path is None:
        return None
    path = path.expanduser()
    if path.name == "intent-state.json" and any((path.parents[2] / name).exists() for name in ("ghost-state.sqlite3", ".sqlite-authority.json")):
        snapshot = load_ledger().consumer_snapshot(path)
        return {key: snapshot[key] for key in EMPTY_INTENT_CONTEXT}
    # Portable reports retain their schema without loading the ledger or validating unexported fields.
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    decisions = [item for item in value.get("decisions", []) if isinstance(item, dict)]
    active_decisions = [item for item in decisions if not item.get("superseded")]
    latest_scope = value.get("latest_scope")
    context = dict(EMPTY_INTENT_CONTEXT)
    context.update({
        "schema_version": value.get("schema_version", EMPTY_INTENT_CONTEXT["schema_version"]),
        "current_goal": value.get("current_goal", ""),
        "constraints": list(value.get("constraints", [])),
        "non_goals": list(value.get("non_goals", [])),
        "open_questions": list(value.get("open_questions", [])),
        "decision_count": len(active_decisions),
        "active_decisions": deepcopy(active_decisions),
        "latest_scope": deepcopy(latest_scope) if isinstance(latest_scope, dict) else {},
        "conduct_feedback": [item for item in value.get("conduct_feedback", []) if isinstance(item, dict)],
    })
    return context


def attach_intent_context(report: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    if context is None:
        return report
    report["intent_context"] = context
    for item in report.get("instincts", []):
        if isinstance(item, dict):
            if context.get("current_goal"):
                reasons = item.setdefault("quality_reasons", [])
                if isinstance(reasons, list) and "session-intent-context" not in reasons:
                    reasons.append("session-intent-context")
    return report


def sequence_instincts(events: list[Event], min_count: int) -> list[dict[str, Any]]:
    by_session: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_session[event.session].append(event)

    sequence_counts: Counter[tuple[str, str, str]] = Counter()
    sequence_events: dict[tuple[str, str, str], list[Event]] = defaultdict(list)
    sequence_labels: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    for session_events in by_session.values():
        ordered = sorted(session_events, key=lambda event: event.index)
        for offset in range(0, max(0, len(ordered) - SEQUENCE_SIZE + 1)):
            window_events = ordered[offset : offset + SEQUENCE_SIZE]
            key = tuple(event.tool_id for event in window_events)
            labels = tuple(event.tool for event in window_events)
            sequence_counts[key] += 1
            sequence_events[key].extend(window_events)
            sequence_labels[key] = labels

    instincts: list[dict[str, Any]] = []
    for key, count in sorted(sequence_counts.items()):
        if count < min_count:
            continue
        labels = sequence_labels[key]
        candidate = {
            "id": "sequence:" + "-".join(key),
            "trigger": " -> ".join(labels) + " sequence",
            "domain": "sequence",
            "scope": most_common_scope(sequence_events[key]),
            "evidence": f"{count} occurrences",
        }
        candidate.update(quality_metadata(sequence_events[key], count))
        instincts.append(candidate)
    return instincts


def conduct_feedback_instincts(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Emit a first-class evolution candidate for each open conduct_feedback entry.

    Tool-frequency patterns and supported conduct_feedback supply candidate data.
    suggested_next_step names the script's proposed next step. The response model
    evaluates the candidate under skill-evolution and follows necessity-gate when
    proposing work that the human user's instructions authorize separately.
    """
    if not context:
        return []
    entries = context.get("conduct_feedback")
    if not isinstance(entries, list):
        return []
    instincts: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status", "open")) != "open":
            continue
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id:
            continue
        rule = str(entry.get("corrective_rule") or "").strip()
        pattern = str(entry.get("failure_pattern") or "").strip()
        source = load_ledger().canonical_source(entry.get("source"), "") or "unknown"
        instincts.append({
            "id": f"conduct:{entry_id}",
            "trigger": rule or pattern or entry_id,
            "domain": "conduct",
            "scope": "agent",
            "evidence": pattern or "recorded conduct feedback",
            "quality": "review",
            "suggested_next_step": "necessity-gate",
            "session_count": 1,
            "quality_reasons": ["conduct-feedback", f"source-{source}"],
            "source": source,
            "status": "open",
        })
    return instincts


def analyze_trace(
    path: Path,
    window: int = DEFAULT_WINDOW,
    min_count: int = DEFAULT_MIN_COUNT,
    intent_ledger: Path | None = None,
) -> dict[str, Any]:
    rows = load_jsonl(path, window)
    events = [event for index, row in enumerate(rows) if (event := normalize_event(row, index))]
    report = analyze_events(events, window, min_count)
    context = load_intent_context(intent_ledger)
    conduct = conduct_feedback_instincts(context)
    if conduct:
        report["instincts"].extend(conduct)
        report["instincts"].sort(key=lambda item: (item["domain"], item["id"], item["scope"]))
        summary = {"review": 0, "watch": 0, "reject": 0}
        for item in report["instincts"]:
            summary[item["quality"]] += 1
        report["quality_summary"] = summary
    return attach_intent_context(report, context)


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"window: {report['window']}",
        f"event_count: {report['event_count']}",
        f"quality_summary: {report.get('quality_summary', {})}",
        "instincts:",
    ]
    for item in report["instincts"]:
        lines.append(
            f"- {item['id']} | {item['quality']} | {item['scope']} | {item['evidence']}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze io-trace JSONL into report-only instinct candidates.")
    parser.add_argument("path", nargs="?", default=str(default_trace()), help="io-trace JSONL path")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="number of recent events to inspect")
    parser.add_argument("--min-count", type=int, default=DEFAULT_MIN_COUNT, help="minimum occurrences")
    parser.add_argument("--intent-ledger", default="", help="session-intent-analyzer intent-state.json path")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    intent_ledger = Path(args.intent_ledger) if args.intent_ledger else None
    report = analyze_trace(Path(args.path), window=args.window, min_count=args.min_count, intent_ledger=intent_ledger)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
