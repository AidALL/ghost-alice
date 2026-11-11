"""Definitions that the installer and the hook runtime read together.

One owner for supported hosts, hook ids, host event names, runner command encoding and the gateway provider
name. Standard library only; nothing here imports an installer or runtime module, so neither layer has
to import the other to read a definition.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import shlex
from typing import Any

SUPPORTED_PLATFORMS = ("claude", "codex")


def read_platform(value: object, *, reject_unknown: bool = False) -> str:
    """Read a supported host name; callers retain command-form and authority checks."""
    platform = value.strip().lower() if isinstance(value, str) else ""
    if platform in SUPPORTED_PLATFORMS:
        return platform
    if reject_unknown:
        raise ValueError(f"unsupported hook platform: {value!r}")
    return "unknown"


PYTHON_VERSION_CHECK_CODE = (
    "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
)
POSIX_HOOK_PYTHON_LAUNCHER = (
    'for py in "${GHOST_ALICE_PYTHON:-}" python3 python '
    "/opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 /bin/python3 "
    "/opt/homebrew/bin/python3.[0-9]* /usr/local/bin/python3.[0-9]* "
    "/usr/bin/python3.[0-9]* /bin/python3.[0-9]* __GHOST_ALICE_INSTALL_PYTHON__; do "
    '[ -n "$py" ] || continue; '
    'if command -v "$py" >/dev/null 2>&1 || [ -x "$py" ]; then '
    f'"$py" -c "{PYTHON_VERSION_CHECK_CODE}" >/dev/null 2>&1 && exec "$py" "$@"; '
    "fi; "
    "done; "
    'echo "Ghost-ALICE hook requires Python 3.11+" >&2; exit 127'
)


def encode_hook_runner_args(hook_id: str, command: str, platform: str | None, binding: str) -> list[str]:
    """Build the runner arguments that the installer places after the Python script."""
    args = ["run", normalize_hook_id(hook_id)]
    if platform is not None:
        platform = platform.strip().lower()
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError(f"unsupported hook runner platform: {platform}")
        args.append(platform)
    args.extend([base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii"), "--binding-b64", binding])
    return args


def decode_hook_payload(payload: str) -> str:
    return base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")


def parse_hook_runner_command(command: str) -> dict[str, str | None] | None:
    """Read the current or legacy runner argv; callers separately validate binding authority."""
    try:
        argv = shlex.split(command, comments=True)
        if argv[:2] == ["/bin/sh", "-c"]:
            if len(argv) < 7 or argv[3] != "ghost-alice-python":
                return None
            prefix, suffix = POSIX_HOOK_PYTHON_LAUNCHER.split("__GHOST_ALICE_INSTALL_PYTHON__")
            if not argv[2].startswith(prefix) or not argv[2].endswith(suffix):
                return None
            runtime = shlex.split(argv[2][len(prefix):-len(suffix)])
            if len(runtime) != 1 or not Path(runtime[0]).is_absolute():
                return None
            script_index = 4
        else:
            if len(argv) < 4 or not re.fullmatch(r"python(?:3(?:\.[0-9]+)?)?(?:\.exe)?", Path(argv[0]).name):
                return None
            script_index = 1
        script = Path(argv[script_index])
        if not script.is_absolute() or script.name != "hook_profile_gate.py":
            return None
        args = argv[script_index + 1:]
        binding = None
        if len(args) in {5, 6} and args[-2] == "--binding-b64":
            binding = args[-1]
            args = args[:-2]
        if len(args) not in {3, 4} or args[0] != "run":
            return None
        return {"script": str(script), "hook_id": args[1],
                "platform": args[2] if len(args) == 4 else None,
                "command": decode_hook_payload(args[-1]), "binding": binding}
    except (ValueError, UnicodeError, IndexError):
        return None

# The Codex model provider name the context gateway registers and the hooks look for.
PROVIDER = 'ghost_alice_context'

# Addon ids and hook ids share one safe-name rule.
SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Semantic hook intent → concrete host event name.
#   - claude/codex: UserPromptSubmit fires when the host submits a prompt under the user role; the sender may be the human user or a script.
HOOK_EVENT_MAP: dict[str, dict[str, str]] = {
    "on_prompt_submit": {
        "claude": "UserPromptSubmit",
        "codex":  "UserPromptSubmit",
    },
    "pre_tool_use": {
        "claude": "PreToolUse",
        "codex":  "PreToolUse",
    },
    "post_tool_use": {
        "claude": "PostToolUse",
        "codex":  "PostToolUse",
    },
    # Claude Code runs PostToolUse only after a successful tool call and this event after a call that started and failed.
    # Codex has no such event; its PostToolUse also runs after a shell command that exits non-zero.
    "post_tool_use_failure": {
        "claude": "PostToolUseFailure",
    },
    "on_agent_stop": {
        "claude": "Stop",
        "codex":  "Stop",
    },
    "on_session_start": {
        "claude": "SessionStart",
        "codex":  "SessionStart",
    },
}

# Core hooks the installer registers on every host, in report order.
CORE_HOOK_IDS = ("session-intent", "tool-checkpoint", "completion", "session-start", "session-receipt", "io-trace")
# Core hooks an older installer registered and Core no longer has. The installer removes a
# registration with one of these runner ids at every install; an addon hook may not take one.
RETIRED_CORE_HOOK_IDS = ("prompt", "pending-merge-prompt", "web-search-first")


def resolve_hook_event(intent: str, platform: str) -> str:
    """Convert a semantic intent to the concrete event name for a platform."""
    if intent == "on_user_prompt":
        intent = "on_prompt_submit"
    try:
        return HOOK_EVENT_MAP[intent][platform]
    except KeyError:
        raise ValueError(
            f"Unknown hook intent '{intent}' or platform '{platform}'. "
            f"Add the required mapping to HOOK_EVENT_MAP."
        )


def normalize_hook_id(value: str) -> str:
    text = (value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


class AddonManifestError(Exception):
    """Raised when an addon manifest cannot be safely used."""


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AddonManifestError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AddonManifestError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AddonManifestError(f"manifest must be an object: {path}")
    return data

