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
    """Return an object or an empty mapping; report decode failures as JSONDecodeError."""
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except RecursionError as error:
        raise json.JSONDecodeError(
            "JSON nesting exceeds the decoder's supported depth", raw, 0
        ) from error
    except json.JSONDecodeError:
        raise
    except ValueError as error:
        raise json.JSONDecodeError(str(error), raw, 0) from error
    return value if isinstance(value, dict) else {}
