#!/usr/bin/env python3
"""Compare the native answer with its recorded review, return the notice text for the host to show, and let the host stop.

Core authenticates proof independently. A Stop event is not a submission port:
its continuation decision starts another model input rather than holding output,
so this script never asks the host to block, add context or retry.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_transcript import assistant_text_this_turn as _assistant_text_this_turn  # noqa: F401
from stop_notice import ENV_FILE, ensure_stop_notice


def _read_hook_input() -> dict[str, Any]:
    # Decode stdin as UTF-8 explicitly (Windows default is cp949/cp1252), so a non-ASCII transcript_path or inline message is not mis-decoded.
    try:
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is not None:
            data = buffer.read()
            raw = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        else:
            raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--platform', choices=('claude', 'codex'), default='claude')
    args = parser.parse_args()
    # The script records the message only at the path the hook runner sets in GHOST_ALICE_STOP_NOTICE_FILE.
    message = ensure_stop_notice(args.platform, _read_hook_input(), os.environ, os.environ.get(ENV_FILE))
    output: dict[str, Any] = {'continue': True, 'stop_notice': message}
    if message['state'] == 'notice':
        output['systemMessage'] = message['text']
    # The runner sends the host only continue and systemMessage, and keeps this whole output in the hook log.
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
