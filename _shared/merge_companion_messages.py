"""Context-aware user messages for merge-companion pending entries.

Pending-merge user messages are English-only.
"""
from __future__ import annotations

from typing import Dict, Literal

PendingMessageContext = Literal[
    "install_tail",
    "session_start",
    "prompt_submit",
    "git_pre_push",
    "readme",
]


_USER_COPY: Dict[PendingMessageContext, str] = {
    "install_tail": (
        "During the agent tool update, your local changes were backed up instead of being "
        "overwritten. The next time you open Claude/Codex, please ask "
        "'Please review backed-up changes.'"
    ),
    "session_start": (
        "This conversation is a new session. Backed-up personal changes from a previous "
        "agent tool update may still be unresolved. In this conversation, please ask "
        "'Please review backed-up changes.' to review what to keep or discard."
    ),
    "prompt_submit": (
        "There may still be unresolved backed-up changes. In the current conversation, "
        "please ask 'Please review backed-up changes.' to choose keep, discard, or later."
    ),
    "git_pre_push": (
        "There are unresolved backed-up changes. Review them before sending this work to "
        "the remote repository. The default setting does not block push."
    ),
    "readme": (
        "This is the backup folder guide. During the agent tool update, your local changes "
        "were copied here instead of being overwritten. If an AI chat is already open, "
        "please ask 'Please review backed-up changes.' Otherwise, open a new chat and ask the same thing."
    ),
}


_TECH_COPY = (
    "merge-companion treats entries with decided=false in "
    "~/.ghost-alice/pending-merges/<platform>/manifest.json as pending. source_path is the "
    "original file and backup_path is the isolated backup. Missing, empty, or unparsable "
    "manifests pass silently."
)


def render_pending_merge_message(context: PendingMessageContext) -> str:
    """Return English pending-merge guidance for a concrete display context."""
    if context not in _USER_COPY:
        valid = ", ".join(sorted(_USER_COPY))
        raise ValueError(f"unknown pending merge message context: {context!r}; expected one of {valid}")
    return f"User: {_USER_COPY[context]}\nTech: {_TECH_COPY}"
