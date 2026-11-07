"""Session identity from a host payload and the environment, and the file-name form of an identity.

Dependencies: Python 3.11+ standard library.
"""
from __future__ import annotations

import re

SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.=-]+")
MAX_LENGTH = 120
# Every key a host writes its session under, in the order the first match wins.
PAYLOAD_KEYS = ("session_id", "sessionId", "conversation_id", "thread_id", "threadId")


def safe_component(value, fallback: str = "unknown") -> str:
    """`value` as a file or folder name: unsafe runs become `-`, edge dots and dashes go, at most MAX_LENGTH characters."""
    text = str(value or "").strip()
    if not text:
        return fallback
    cleaned = SAFE_COMPONENT.sub("-", text).strip(".-")
    return cleaned[:MAX_LENGTH] or fallback


def is_exact(value) -> bool:
    """An identity Core may write under: non-empty text that safe_component leaves unchanged, within MAX_LENGTH."""
    return (isinstance(value, str) and bool(value) and len(value) <= MAX_LENGTH
            and safe_component(value, "") == value)


def session_candidates(platform, payload, env=None) -> list:
    """Every non-empty session value the payload and then the environment offer, as the host wrote them."""
    environment = {} if env is None else env
    values = [payload.get(key) for key in PAYLOAD_KEYS]
    values.append(environment.get("CODEX_THREAD_ID") if platform == "codex" else None)
    values.append(environment.get("GHOST_ALICE_SESSION_ID"))
    return [value for value in values if value not in (None, "")]


def bound_session(platform, payload, env=None) -> str | None:
    """The exact session the host binds; None when it offers none, or offers one Core may not write under."""
    candidates = session_candidates(platform, payload, env)
    return candidates[0] if candidates and is_exact(candidates[0]) else None


def host_bound_session(platform, env) -> str:
    """The session the host environment binds a command to: the Codex thread on Codex, else GHOST_ALICE_SESSION_ID; empty when none."""
    bound = env.get("CODEX_THREAD_ID", "") if platform == "codex" else ""
    return bound or env.get("GHOST_ALICE_SESSION_ID", "")
