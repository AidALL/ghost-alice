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
from stop_notice import ENV_FILE, ensure_stop_notice
from hook_input import parse_json_object, read_stdin_utf8
from hook_definitions import SUPPORTED_PLATFORMS


def _read_hook_input() -> dict[str, Any]:
    try:
        raw = read_stdin_utf8()
    except Exception:
        return {}
    try:
        return parse_json_object(raw)
    except json.JSONDecodeError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--platform', choices=SUPPORTED_PLATFORMS, default='claude')
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
