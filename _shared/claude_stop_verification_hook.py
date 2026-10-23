#!/usr/bin/env python3
"""Observe native response assessment without starting a second response.

Core authenticates proof independently. A Stop event is not a submission port:
its continuation decision starts another model input rather than holding output.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_completion import assess_native_completion
from native_transcript import assistant_text_this_turn as _assistant_text_this_turn, stop_response_text

NATIVE_SESSION_KEYS = ('session_id', 'sessionId', 'thread_id', 'threadId')


def _native_source(platform: str, hook_input: dict[str, Any]) -> dict[str, str]:
    """Bind the assessment to the session the host named; an unnamed session stays empty, never guessed."""
    session = next((hook_input[key] for key in NATIVE_SESSION_KEYS
                    if isinstance(hook_input.get(key), str) and hook_input[key].strip()), '')
    return dict(os.environ, GHOST_ALICE_PLATFORM=platform, GHOST_ALICE_SESSION_ID=session)


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
    native_input = _read_hook_input()
    text = stop_response_text(native_input)
    if not text.strip():
        print(json.dumps({'continue': True}))
        return 0
    source = _native_source(args.platform, native_input)
    assessment = assess_native_completion(source, text=text, phase='final', native_input=native_input)
    # The wrapper retains this structured result in its strict audit and sends
    # only the native envelope to the host. No response is promoted by this log.
    observation = {key: assessment.get(key) for key in
        ('input_event_id', 'body_digest', 'body_issue', 'repair_target')}
    observation['status'] = 'unaccepted' if assessment['body_issue'] else 'no-issue'
    print(json.dumps({'continue': True, 'response_assessment': observation}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
