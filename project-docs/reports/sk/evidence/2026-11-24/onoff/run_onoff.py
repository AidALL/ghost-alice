"""Run the ghost-chat answer-key benchmark with Ghost-ALICE ON or OFF.

ON  = the user's normal Claude Code setup (Ghost-ALICE hooks, CLAUDE.md contract, skills).
OFF = the same CLI with --safe-mode (CLAUDE.md, skills, hooks, plugins, MCP disabled; auth,
      model selection, built-in tools and permissions unchanged).

The existing bench code is reused unchanged; only the answer command gets --safe-mode for OFF.
The grader is identical in both conditions (isolated session, no user settings, no tools).

Usage: python run_onoff.py --condition on|off --slot <name> [--ids A01,U01] [--limit N]
Results: <this folder>/<condition>/ (bench.sqlite3, runs/<run_id>/, summary.csv, bench.log)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
GHOST_CHAT = HERE.parent
sys.path.insert(0, str(GHOST_CHAT))
from ghost_chat import bench, claude_runner  # noqa: E402

_original_build_command = claude_runner.build_command


def _safe_mode_build_command(executable, session_id, tools=claude_runner.READ_ONLY_TOOLS):
    return _original_build_command(executable, session_id, tools) + ['--safe-mode']


def main() -> int:
    parser = argparse.ArgumentParser(description='Ghost-ALICE ON/OFF benchmark run')
    parser.add_argument('--condition', choices=('on', 'off'), required=True)
    parser.add_argument('--slot', required=True)
    parser.add_argument('--ids', help='comma-separated question ids')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.condition == 'off':
        claude_runner.build_command = _safe_mode_build_command
    ids = [i.strip() for i in args.ids.split(',')] if args.ids else None
    data_dir = HERE / args.condition
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / 'bench.log'

    def log(message: str) -> None:
        line = f'{datetime.now().isoformat(timespec="seconds")} [{args.condition}] {message}'
        print(line, flush=True)
        with log_path.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')

    log(f'start slot={args.slot} ids={args.ids} limit={args.limit} '
        f'command={claude_runner.build_command("claude", None)}')
    try:
        run = bench.run_benchmark(GHOST_CHAT / 'bench', data_dir, f'{args.condition}-{args.slot}', args.limit,
                                  log=log, ids=ids)
    except Exception as error:
        log(f'FAILED: {type(error).__name__}: {error}')
        return 1
    log('done ' + run['run_id'] + ' ' + json.dumps(run['summary'], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
