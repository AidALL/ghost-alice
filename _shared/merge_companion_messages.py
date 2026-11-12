"""Core code's guidance for the human user about pending merge-companion entries.

Pending-merge guidance for the human user is English-only.
"""
from __future__ import annotations

from typing import Dict, Literal

PendingMessageContext = Literal[
    "session_start",
    "readme",
]


_USER_COPY: Dict[PendingMessageContext, str] = {
    "session_start": (
        "This conversation is a new session. Backups of external changes found during a previous "
        "agent tool update may still await the human user's merge, discard or defer choice. "
        "The human user can ask 'Please review backed-up changes.' in this conversation to request a review."
    ),
    "readme": (
        "This is the backup folder guide. During the agent tool update, external changes found after installation "
        "were copied here for the human user to choose whether to merge, discard or defer. "
        "The human user can ask 'Please review backed-up changes.' in an existing or new AI chat to request a review."
    ),
}


_TECH_COPY = (
    "merge-companion treats entries with decided=false in " "~/.ghost-alice/pending-merges/<platform>/manifest.json as pending. source_path is the " "original file and backup_path is the isolated backup. Missing, empty, or unparsable " "manifests pass silently."
)


def render_pending_merge_message(context: PendingMessageContext) -> str:
    """Return English pending-merge guidance for a concrete display context."""
    if context not in _USER_COPY:
        valid = ", ".join(sorted(_USER_COPY))
        raise ValueError(f"unknown pending merge message context: {context!r}; expected one of {valid}")
    return f"For the human user: {_USER_COPY[context]}\nTech: {_TECH_COPY}"
