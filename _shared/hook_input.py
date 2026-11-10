"""Decode hook stdin without imposing a consumer's failure policy."""

from __future__ import annotations

import json
import sys
from typing import Any


def read_stdin_utf8() -> str:
    """Read UTF-8 hook bytes without the host's cp949/cp1252 text decoder."""
    if sys.stdin.isatty():
        return ""
    buffer = getattr(sys.stdin, "buffer", None)
    data = buffer.read() if buffer is not None else sys.stdin.read()
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)


def parse_json_object(raw: str) -> dict[str, Any]:
    """Return an object or an empty mapping; leave malformed JSON to the caller."""
    if not raw.strip():
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}
