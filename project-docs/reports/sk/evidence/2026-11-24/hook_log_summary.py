"""Summarize Ghost-ALICE strict hook logs for the jy issue 1-4 reproduction (sk, 2026-11-24).

Usage: python hook_log_summary.py A=<session_id> B=<session_id> C=<session_id>
Reads ~/.ghost-alice/session-logs/claude/<session_id>/strict-hook-output.jsonl (read-only) and prints
Markdown tables: runs and observed_duration_s per event . hook_id, PreToolUse/PostToolUse counts,
Stop decisions with block reasons, and non-hidden outputs that contain the issue-3 keywords.
"""

from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

LOGS = Path.home() / '.ghost-alice' / 'session-logs' / 'claude'
KEYWORDS = ('completion-gate', 'autopilot', 'route-unavailable', 'intent-events')
HANGUL = re.compile('[가-힣]')


def text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def parse_stdout(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value) if value else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def one_line(value: str, limit: int = 200) -> str:
    return value.replace('\n', ' ').replace('|', '\\|')[:limit]


def summarize(label: str, session_id: str) -> list[str]:
    path = LOGS / session_id / 'strict-hook-output.jsonl'
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    out = [f'### Session {label} ({session_id})', '', '| event . hook_id | runs | observed_duration_s |', '| --- | ---: | ---: |']
    totals: OrderedDict = OrderedDict()
    for row in rows:
        entry = totals.setdefault((row['event'], row['hook_id']), [0, 0.0])
        entry[0] += 1
        entry[1] += row.get('observed_duration_s') or 0
    for (event, hook_id), (runs, seconds) in totals.items():
        out.append(f'| {event} . {hook_id} | {runs} | {seconds:.2f} |')
    out.append(f'| total | {len(rows)} | {sum(v[1] for v in totals.values()):.2f} |')
    pre = sum(row['event'] == 'PreToolUse' for row in rows)
    post = sum(row['event'] in ('PostToolUse', 'PostToolUseFailure') for row in rows)
    out += ['', f'- PreToolUse runs: {pre}; PostToolUse runs (including PostToolUseFailure): {post}']
    stops = [row for row in rows if row['event'] == 'Stop']
    blocks = [parse_stdout(row.get('stdout')) for row in stops]
    blocks = [stdout for stdout in blocks if stdout.get('decision') == 'block']
    out.append(f'- Stop runs: {len(stops)}; decision "block": {len(blocks)}')
    for stdout in blocks:
        reason = stdout.get('reason') or ''
        out.append(f'  - reason (first 200 chars): {one_line(reason)}; Korean text: {bool(HANGUL.search(reason))}')
    for row in stops:
        notice = parse_stdout(row.get('stdout')).get('stop_notice') or {}
        out.append(f'  - {row.get("ts")}: stop_notice.state={notice.get("state")}, issues={notice.get("issues")}')
    shown = [row for row in rows if row.get('visible_decision') != 'hide' and row['event'] != 'Stop']
    out += ['', '| ts | event . hook_id | visibility profile | issue-3 keywords | user surface (first 200 chars) |',
            '| --- | --- | --- | --- | --- |']
    for row in shown:
        blob = ' '.join(text(row.get(key)) for key in ('stdout', 'stderr', 'user_surface_output', 'model_surface_output') if row.get(key))
        hits = ', '.join(word for word in KEYWORDS if word in blob) or '-'
        surface = one_line(text(row.get('user_surface_output') or '').strip()) or '(empty)'
        out.append(f'| {row.get("ts")} | {row["event"]} . {row["hook_id"]} | {row.get("agent_visibility_profile")} | {hits} | {surface} |')
    return out + ['']


def main(argv: list[str]) -> int:
    pairs = [arg.split('=', 1) for arg in argv]
    if not pairs or any(len(pair) != 2 for pair in pairs):
        print(__doc__)
        return 2
    lines: list[str] = []
    for label, session_id in pairs:
        lines += summarize(label, session_id)
    sys.stdout.reconfigure(encoding='utf-8')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
