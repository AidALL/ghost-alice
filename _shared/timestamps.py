"""UTC time as ISO 8601 text, and its parsing.

Dependencies: Python 3.11+ standard library.
"""
from __future__ import annotations

from datetime import datetime, timezone


def format_timestamp(moment: datetime, *, microseconds: bool = False) -> str:
    """`moment` in UTC as ISO 8601 text ending in `Z`, to the second unless `microseconds` is true."""
    moment = moment.astimezone(timezone.utc)
    if not microseconds:
        moment = moment.replace(microsecond=0)
    return moment.isoformat().replace("+00:00", "Z")


def utc_now(*, microseconds: bool = False) -> str:
    """The current UTC time as `format_timestamp` writes it."""
    return format_timestamp(datetime.now(timezone.utc), microseconds=microseconds)


def parse_timestamp(value: object) -> datetime | None:
    """The aware datetime of ISO 8601 text with `Z` or an offset; None for any other value."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
