#!/usr/bin/env python3
"""
install_hooks.py - automatic installer/remover for AI coding agent hooks

Supported frameworks:
  - Claude Code  (~/.claude/settings.json)
  - Codex        (~/.codex/hooks.json + ~/.codex/config.toml)

Install behavior:
  1. Read each framework's hook config file, creating it when absent.
  2. Merge the UserPromptSubmit hook when missing or stale.
  3. Skip with a message when the same hook already exists (idempotent).
  4. Preserve existing settings.
  5. For Codex, install `hooks.json` and ensure `[features] hooks = true` in `config.toml`.

Uninstall behavior:
  1. Remove only hook entries that contain the managed marker from each framework config.
  2. Leave all other settings untouched.

Usage:
  python install_hooks.py                     # Install to all detected frameworks
  python install_hooks.py --platform claude   # Claude Code only
  python install_hooks.py --platform codex    # Codex only
  python install_hooks.py --uninstall         # Remove hooks
  python install_hooks.py --status            # Check install status
  python install_hooks.py --dry-run           # Preview without changes

Exit codes:
  0 = success (install/remove/status completed)
  1 = partial failure (one or more frameworks errored)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

MIN_PYTHON_VERSION = (3, 11)

if sys.version_info < MIN_PYTHON_VERSION:
    detected = ".".join(str(part) for part in sys.version_info[:3])
    sys.stderr.write(f"install_hooks.py requires Python 3.11+; detected Python {detected}\n")
    raise SystemExit(1)

from merge_companion_messages import render_pending_merge_message
from hook_profile_gate import normalize_hook_id
import runtime_config
from addon_installer import AddonManifestError, load_addon_targets

# Prevent Windows cp949 stdout encoding issues.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

VERSION = "1.7.0"
HOOK_SHARED_DIR_ENV = "GHOST_ALICE_HOOK_SHARED_DIR"
STALE_LEGACY_CHECKOUT_NAMES = ("Ghost-ALICE", "Ghost-ALICE")


@dataclass(frozen=True)
class HookStatus:
    platform: str
    status_token: str
    status_label: str
    legacy_status: str
    details: dict[str, Any]
    missing_reason: str | None = None
    unsupported: bool = False
    # True when hook files are present but their runtime event semantics are not
    # yet confirmed by a live smoke run. Per docs/policies/platform-adapter-compliance.md,
    # Codex stays instruction-backed (pending smoke evidence) rather than being
    # reported as native runtime-verified. This does not change install behavior;
    # it only keeps the status report honest about unverified event semantics.
    pending_smoke_evidence: bool = False

# ── Hook Payload Definitions ──────────────────────────────

def _shell_print_message(message: str) -> str:
    payload = base64.b64encode((message + "\n").encode("utf-8")).decode("ascii")
    code = f"import sys,base64;sys.stdout.buffer.write(base64.b64decode('{payload}'))"
    python = sys.executable.replace("\\", "/")
    return f'"{python}" -c "{code}"'


def _shell_print_json(payload: dict[str, Any]) -> str:
    return _shell_print_message(json.dumps(payload, ensure_ascii=False))


def _localized_bridge(
    internal_instruction: str,
    user_ko: str,
    user_en: str,
    tech_ko: str,
    tech_en: str,
) -> str:
    return "\n".join([
        f"Internal instruction: {internal_instruction}",
        f"User: {user_en}",
        f"Tech: {tech_en}",
    ])


def _quote_static_arg(value: str | Path) -> str:
    text = str(value).replace("\\", "/")
    if os.name == "nt" and re.fullmatch(r"[A-Za-z]:/[^\s\"<>|]+", text):
        return text
    return '"' + text.replace('"', '\\"') + '"'

def _hook_shared_dir() -> Path:
    configured = os.environ.get(HOOK_SHARED_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).parent


def _default_installed_hook_shared_dir(platform_key: str) -> Path:
    if platform_key == "claude":
        return _resolve_claude_dir() / "skills" / "_shared"
    return _home() / ".agents" / "skills" / "_shared"


def _hook_shared_dir_for_platform(platform_key: str, explicit: str | None = None) -> Path | None:
    if explicit and explicit.strip():
        return Path(explicit).expanduser()
    configured = os.environ.get(HOOK_SHARED_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    installed_shared = _default_installed_hook_shared_dir(platform_key)
    if installed_shared.exists():
        return installed_shared
    return None


def _resolve_shared_hook_script(name: str) -> str:
    script = _hook_shared_dir() / name
    if script.exists():
        return str(script).replace("\\", "/")
    return ""


def _resolve_pending_merge_precheck_script() -> str:
    return _resolve_shared_hook_script("pending_merge_precheck_hook.py")


def _pending_merge_precheck_command(
    *,
    platform: str,
    hook: str,
    context: str,
    output_format: str,
    internal_instruction: str,
) -> str:
    script = _resolve_pending_merge_precheck_script()
    if script:
        payload = base64.urlsafe_b64encode(internal_instruction.encode("utf-8")).decode("ascii")
        python = sys.executable.replace("\\", "/")
        return " ".join([
            _quote_static_arg(python),
            _quote_static_arg(script),
            "--platform",
            platform,
            "--hook",
            hook,
            "--context",
            context,
            "--format",
            output_format,
            "--internal-b64",
            payload,
        ])

    if output_format == "json":
        return _shell_print_json({"continue": True, "systemMessage": internal_instruction})

    return _shell_print_message(
        f"Internal instruction: {internal_instruction}\n"
        + render_pending_merge_message(context)  # type: ignore[arg-type]
    )

# 1. User input pre-hook: run only the pending-merge precheck first.
PROMPT_PENDING_MERGE_MARKER = "[merge-companion] prompt-check"
PROMPT_PENDING_MERGE_INTERNAL = (
    "merge-companion prompt-check: Check only the current platform pending-merge manifest "
    "before the user-input governance graph continues. "
    "If this hook reports undecided entries, surface merge-companion first; "
    "a user-explicit defer/skip may continue with the entry still undecided. "
    "If there is no pending warning, treat merge-companion-precheck as clean and do not run an extra shell manifest check."
)


def _prompt_pending_merge_command(*, platform: str, output_format: str) -> str:
    return (
        _pending_merge_precheck_command(
            platform=platform,
            hook="prompt-check",
            context="prompt_submit",
            output_format=output_format,
            internal_instruction=PROMPT_PENDING_MERGE_INTERNAL,
        )
        + f" # {PROMPT_PENDING_MERGE_MARKER}"
    )


PROMPT_PENDING_MERGE_COMMAND = _prompt_pending_merge_command(platform="claude", output_format="text")
PROMPT_PENDING_MERGE_COMMAND_CODEX = _prompt_pending_merge_command(platform="codex", output_format="json")

PROMPT_PENDING_MERGE_ENTRY = {
    "matcher": "",
    "hooks": [{"type": "command", "command": PROMPT_PENDING_MERGE_COMMAND}],
}
PROMPT_PENDING_MERGE_ENTRY_CODEX = {
    "matcher": "",
    "hooks": [{"type": "command", "command": PROMPT_PENDING_MERGE_COMMAND_CODEX}],
}

# 1-B. Work-start hook: reminder to call task-router.
HOOK_MARKER = "[hook-reminder] AGENTS.md"
HOOK_INTERNAL = (
    "hook-reminder: task-router waits until session-intent preflight exists and no current-lineage block gate is recorded. "
    "Absent downstream-gates.json means silent allow. "
    "After release, read the ledger, decompose user input into atomic meaning units, choose focus-layer/scope-reopen, then assign output, verification, lifecycle, and boundary skills. "
    "task-router consumes session-intent and jailbreak gate context; it performs routing decisions but does not infer raw user intent, own intake, or own tool permission. "
    "Every user input requires task-router, including a simple question, opinion, clarification, status comment, or follow-up. "
    "Do not skip task-router for answer-only turns or prior routing; never place task-router before session-intent-analyzer or the jailbreak-detector downstream gate."
)


def _repo_root_from_this_file() -> Path:
    return Path(__file__).resolve().parents[1]


def _session_intent_root() -> Path:
    return _repo_root_from_this_file() / ".tmp" / "session-intent"


def _resolve_task_router_reminder_hook_script() -> str:
    return _resolve_shared_hook_script("task_router_reminder_hook.py")


def _hook_reminder_command(*, platform: str, output_format: str) -> str:
    script = _resolve_task_router_reminder_hook_script()
    if script:
        payload = base64.urlsafe_b64encode(HOOK_INTERNAL.encode("utf-8")).decode("ascii")
        python = sys.executable.replace("\\", "/")
        return " ".join([
            _quote_static_arg(python),
            _quote_static_arg(script),
            "--platform",
            platform,
            "--format",
            output_format,
            "--root",
            _quote_static_arg(_session_intent_root()),
            "--internal-b64",
            payload,
        ]) + f" # {HOOK_MARKER}"
    if output_format == "json":
        return _shell_print_json({"continue": True, "systemMessage": HOOK_INTERNAL}) + f" # {HOOK_MARKER}"
    return _shell_print_message(_localized_bridge(
        HOOK_INTERNAL,
        "After session-intent preflight, run task-router when there is no current-lineage block gate and the path is silent allow.",
        "Run task-router after session-intent preflight; absent current-lineage block gate is silent allow.",
        "task-router reads the ledger, decomposes intent, chooses focus-layer and scope-reopen, then assigns skills.",
        "task-router reads the ledger, decomposes intent, chooses focus-layer and scope-reopen, then assigns skills.",
    )) + f" # {HOOK_MARKER}"


HOOK_COMMAND = _hook_reminder_command(platform="claude", output_format="text")
HOOK_COMMAND_CODEX = _hook_reminder_command(platform="codex", output_format="json")

HOOK_ENTRY = {
    "matcher": "",
    "hooks": [
        {
            "type": "command",
            "command": HOOK_COMMAND,
        }
    ],
}
HOOK_ENTRY_CODEX = {
    "matcher": "",
    "hooks": [
        {
            "type": "command",
            "command": HOOK_COMMAND_CODEX,
        }
    ],
}

# 1-A. Session intent hook: update the per-session intent ledger without the raw prompt.
SESSION_INTENT_MARKER = "[session-intent-analyzer]"
LEGACY_SESSION_INTENT_MARKERS = ("[" + "session-intent-" + "guard" + "]",)
SESSION_INTENT_INTERNAL = (
    "session-intent-analyzer: Observe every user input by recording an input digest, "
    "current-session pointer, and digest-only intake status. "
    "Agents add semantic summaries, constraints, and decisions only when intent materially changes. "
    "Never persist raw prompts, conversation text, tool outputs, system messages, or secrets. "
    "Use the ledger as context for skill-evolution and jailbreak-detector. "
    "skill-evolution is a report-only branch that terminates. jailbreak-detector records model_security_decision in the ledger; only current-lineage block decisions are carried to downstream-gates.json, and absent current block means silent allow. "
    "Capture trigger: when this input corrects the agent's prior behavior or understanding, record a compressed conduct_feedback entry (id, failure_pattern, corrective_rule, source=user-explicit, status=open) with session_intent_ledger.py so skill-evolution and the /evolution backlog surface it. "
    "Basis for the correction judgment: a mismatch the input asserts between the agent's prior action/claim and the ledger's accumulated goal, constraints, non_goals, decisions, and acceptance_criteria. This is the same input-vs-accumulated comparison jailbreak-detector applies for security, not keyword matching; typical mismatches are under-delivery, wrong scope, a wrong assumption, a rejected output, or an arbitrary choice. Do not skip this; model discretion alone has proven unreliable."
)


def _resolve_session_intent_analyzer_hook_script() -> str:
    return _resolve_shared_hook_script("session_intent_analyzer_hook.py")


def _session_intent_analyzer_command(*, platform: str, output_format: str) -> str:
    script = _resolve_session_intent_analyzer_hook_script()
    if script:
        payload = base64.urlsafe_b64encode(SESSION_INTENT_INTERNAL.encode("utf-8")).decode("ascii")
        python = sys.executable.replace("\\", "/")
        return " ".join([
            _quote_static_arg(python),
            _quote_static_arg(script),
            "--platform",
            platform,
            "--hook",
            "session-intent",
            "--context",
            "prompt_submit",
            "--format",
            output_format,
            "--root",
            _quote_static_arg(_session_intent_root()),
            "--internal-b64",
            payload,
        ])
    if output_format == "json":
        return _shell_print_json({"continue": True, "systemMessage": SESSION_INTENT_INTERNAL})
    return _shell_print_message(f"Internal instruction: {SESSION_INTENT_INTERNAL}")


SESSION_INTENT_COMMAND = (
    _session_intent_analyzer_command(platform="claude", output_format="text")
    + f" # {SESSION_INTENT_MARKER}"
)
SESSION_INTENT_COMMAND_CODEX = (
    _session_intent_analyzer_command(platform="codex", output_format="json")
    + f" # {SESSION_INTENT_MARKER}"
)

SESSION_INTENT_ENTRY = {
    "matcher": "",
    "hooks": [{"type": "command", "command": SESSION_INTENT_COMMAND}],
}
SESSION_INTENT_ENTRY_CODEX = {
    "matcher": "",
    "hooks": [{"type": "command", "command": SESSION_INTENT_COMMAND_CODEX}],
}

# 2. Work-stop hook: verification-before-completion reminder.
STOP_HOOK_MARKER = "[completion-reminder] AGENTS.md"
STOP_HOOK_INTERNAL = (
    "completion-reminder: Before final response, run verification-before-completion as an always-on completion lifecycle gate. "
    "Include a [completion-check] block and an [io-trace] block in the final response. "
    "When available, put a top-of-response [observed-timing] block with observable durations only, rounded to two decimals. "
    "Use unavailable for unobserved phases. Do not infer hidden reasoning time or treat timing as quality evidence. "
    "On visible Skill surfaces such as Claude Code Skill, actually load "
    "verification-before-completion before writing skill-call: verification-before-completion. "
    "Do not infer verification from task-router, metadata, prior context, or routing notes. "
    "Do not claim skill-call: verification-before-completion unless verification-before-completion was actually loaded this turn."
)
STOP_HOOK_MESSAGE = _localized_bridge(
    STOP_HOOK_INTERNAL,
    "Verification evidence and the work trace are checked after verification-before-completion is actually loaded.",
    "Verification evidence and the work trace are checked after verification-before-completion is actually loaded.",
    "Claude Code uses Skill; record skill-call only after the verification skill was actually loaded.",
    "Claude Code uses Skill; record skill-call only after the verification skill was actually loaded.",
)


def _resolve_claude_stop_verification_script() -> str:
    return _resolve_shared_hook_script("claude_stop_verification_hook.py")


def _stop_hook_command(platform: str) -> str:
    script = _resolve_claude_stop_verification_script()
    if script:
        python = sys.executable.replace("\\", "/")
        return f'"{python}" "{script}" --platform {platform} # {STOP_HOOK_MARKER}'
    return _shell_print_message(STOP_HOOK_MESSAGE) + f" # {STOP_HOOK_MARKER}"


def _claude_stop_hook_command() -> str:
    return _stop_hook_command("claude")


STOP_HOOK_COMMAND = _claude_stop_hook_command()

STOP_HOOK_ENTRY = {
    "matcher": "",
    "hooks": [
        {
            "type": "command",
            "command": STOP_HOOK_COMMAND,
        }
    ],
}

STOP_HOOK_COMMAND_CODEX = _stop_hook_command("codex")

STOP_HOOK_ENTRY_CODEX = {
    "matcher": "",
    "hooks": [
        {
            "type": "command",
            "command": STOP_HOOK_COMMAND_CODEX,
        }
    ],
}

# 3. Session-start hook: automatic merge-companion check (pending-merge-session-start layer).
SESSION_START_MARKER = "[merge-companion] session-check"
SESSION_START_INTERNAL = (
    "merge-companion session-check: This Ghost-ALICE hook checks only the current platform pending-merge manifest. "
    "If this hook reports undecided entries, run merge-companion. Missing, empty, fully decided, or invalid JSON manifests pass silently; "
    "do not run a second shell check just to prove clean."
)


def _session_start_command(*, platform: str, output_format: str) -> str:
    return (
        _pending_merge_precheck_command(
            platform=platform,
            hook="session-check",
            context="session_start",
            output_format=output_format,
            internal_instruction=SESSION_START_INTERNAL,
        )
        + f" || true # {SESSION_START_MARKER}"
    )


SESSION_START_COMMAND = _session_start_command(platform="claude", output_format="text")
SESSION_START_COMMAND_CODEX = _session_start_command(platform="codex", output_format="json")

SESSION_START_ENTRY = {
    "matcher": "",
    "hooks": [{"type": "command", "command": SESSION_START_COMMAND}],
}
SESSION_START_ENTRY_CODEX = {
    "matcher": "",
    "hooks": [{"type": "command", "command": SESSION_START_COMMAND_CODEX}],
}

# 4. User input hook (auxiliary): require web search before external tool claims (rule 10).
# This is an agent governance hook with the same shape as the task-router hook.
# It injects text on every user input to require community signal checks before external tool claims.
WEB_SEARCH_FIRST_MARKER = "[web-search-first]"
WEB_SEARCH_FIRST_INTERNAL = (
    "web-search-first: AGENTS.md Rule 10. Before factual claims about external tools, libraries, CLIs, SDKs, frameworks, versions, or platform behavior, cross-check at least three community sources with WebSearch. Official docs alone are not enough for runtime behavior."
)
WEB_SEARCH_FIRST_COMMAND = (
    _shell_print_message(_localized_bridge(
        WEB_SEARCH_FIRST_INTERNAL,
        "Current field reports are checked before describing external tool or version behavior.",
        "Current field reports are checked before describing external tool or version behavior.",
        "Rule 10: check at least three community signals such as GitHub issues, Reddit, HN, or Stack Overflow.",
        "Rule 10: check at least three community signals such as GitHub issues, Reddit, HN, or Stack Overflow.",
    ))
    + f" # {WEB_SEARCH_FIRST_MARKER}"
)
WEB_SEARCH_FIRST_COMMAND_CODEX = (
    _shell_print_json({
        "continue": True,
        "systemMessage": WEB_SEARCH_FIRST_INTERNAL,
    })
    + f" # {WEB_SEARCH_FIRST_MARKER}"
)

WEB_SEARCH_FIRST_ENTRY = {
    "matcher": "",
    "hooks": [{"type": "command", "command": WEB_SEARCH_FIRST_COMMAND}],
}
WEB_SEARCH_FIRST_ENTRY_CODEX = {
    "matcher": "",
    "hooks": [{"type": "command", "command": WEB_SEARCH_FIRST_COMMAND_CODEX}],
}

# 5. Pre-tool hook: tool-checkpoint hook-enforced retry point.
TOOL_CHECKPOINT_MARKER = "[tool-checkpoint] pre-tool-check"
LEGACY_TOOL_CHECKPOINT_MARKERS = (f"[{'action'}-{'gate'}] pre-tool-check",)
def _reminder_text(key: str) -> str:
    import json as _json
    try:
        return _json.loads(Path(__file__).with_name("reminder_texts.json").read_text(encoding="utf-8"))[key]
    except Exception:
        return ""


TOOL_CHECKPOINT_INTERNAL = _reminder_text("tool-checkpoint")
TOOL_CHECKPOINT_COMMAND = (
    _shell_print_message(_localized_bridge(
        TOOL_CHECKPOINT_INTERNAL,
        "hook-stage is PreToolUse. This hook is a tool-call retry checkpoint, not user-input intake.",
        "hook-stage is PreToolUse. This hook is a tool-call retry checkpoint, not user-input intake.",
        "Field names and literal tokens stay in English. Human notes use the current user language.",
        "Field names and literal tokens stay in English. Human notes use the current user language.",
    ))
    + f" # {TOOL_CHECKPOINT_MARKER}"
)
TOOL_CHECKPOINT_ENTRY = {
    "matcher": "",
    "hooks": [{"type": "command", "command": TOOL_CHECKPOINT_COMMAND}],
}

# ── io-trace hook payload ─────────────────────────────────
# Record file paths from every tool call into the audit log through PostToolUse events.
# stdout is not visible to the user because this is PostToolUse, but the log file keeps it.

IO_TRACE_MARKER = "[io-trace] audit"
HOOK_RUNNER_MARKER_PREFIX = "[hook-runner:"


def _discover_ghost_alice_skill_names_from_catalog() -> list[str]:
    catalog = _repo_root_from_this_file() / "skill-catalog" / "skills.json"
    if not catalog.exists():
        return []
    try:
        data = json.loads(catalog.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    skills = data.get("skills")
    if not isinstance(skills, list):
        return []
    names = sorted({
        skill.get("name")
        for skill in skills
        if isinstance(skill, dict) and isinstance(skill.get("name"), str)
    })
    return names


def _print_addon_target_summary(addon_sources: list[str], platform: str | None = None) -> int:
    try:
        targets = load_addon_targets(
            addon_sources,
            core_skill_names=_discover_ghost_alice_skill_names_from_catalog(),
            platform=platform,
        )
    except AddonManifestError as exc:
        print(_t(f"Addon manifest error: {exc}", f"Addon manifest error: {exc}"), file=sys.stderr)
        return 1

    if not targets:
        print(_t("No addon skill targets discovered.", "No addon skill targets discovered."))
        return 0

    print(_t("Addon skill targets:", "Addon skill targets:"))
    for target in targets:
        print(f"  {target.name} ({target.origin}) -> {target.source}")
    return 0


def _discover_ghost_alice_skill_names_from_installed_tree() -> list[str]:
    root = _home() / ".agents" / "skills"
    if not root.exists():
        return []
    names: set[str] = set()
    for skill_md in root.glob("*/SKILL.md"):
        name = skill_md.parent.name
        if name.startswith(".") or name == "_shared":
            continue
        names.add(name)
    return sorted(names)


def _ghost_alice_installed_skill_names() -> list[str]:
    names = _discover_ghost_alice_skill_names_from_catalog()
    if names:
        return names
    return _discover_ghost_alice_skill_names_from_installed_tree()


def _claude_ghost_alice_skill_permissions() -> list[str]:
    return [f"Skill({name})" for name in _ghost_alice_installed_skill_names()]


def _is_stale_legacy_checkout_path_permission(item: Any) -> bool:
    if not isinstance(item, str):
        return False
    normalized = item.replace("\\", "/")
    for legacy_name in STALE_LEGACY_CHECKOUT_NAMES:
        if f"/{legacy_name}/" in normalized:
            return True
        if f"/{legacy_name} " in normalized or f"/{legacy_name})" in normalized:
            return True
        if normalized.endswith(f"/{legacy_name}"):
            return True
    return False


def _resolve_hook_runner_script() -> str:
    return _resolve_shared_hook_script("hook_profile_gate.py")


def _hook_runner_command(hook_id: str, command: str, marker: str) -> str:
    script = _resolve_hook_runner_script()
    if not script:
        return command
    payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
    normalized = normalize_hook_id(hook_id)
    python = sys.executable.replace("\\", "/")
    visible_hint = " ghost-alice-hook.mjs" if "ghost-alice-hook.mjs" in command else ""
    return (
        f"{_quote_static_arg(python)} {_quote_static_arg(script)} run {normalized} {payload} "
        f"# {marker} [hook-runner:{normalized}]{visible_hint}"
    )

def _hook_runner_command_entry(hook_id: str, command: str, marker: str) -> dict[str, Any]:
    return _command_entry(_hook_runner_command(hook_id, command, marker))

def _resolve_io_trace_script() -> str:
    """Return the io_trace_hook.py path relative to the install directory."""
    return _resolve_shared_hook_script("io_trace_hook.py")

IO_TRACE_ENTRY = {
    "matcher": "",
    "hooks": [
        {
            "type": "command",
            "command": "",  # Filled dynamically during install.
        }
    ],
}

# ── Semantic Intent -> Platform Event Name Mapping ────────
#
# Each platform uses different event names for hooks with the same intent.
# Add only a mapping in this table when adding a new platform.
#
# Notes:
#   - claude/codex: UserPromptSubmit fires once when user input is submitted.

HOOK_EVENT_MAP: dict[str, dict[str, str]] = {
    "on_user_prompt": {
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
    "on_agent_stop": {
        "claude": "Stop",
        "codex":  "Stop",
    },
}

HOOK_EVENT_MAP["on_session_start"] = {
    "claude": "SessionStart",
    "codex":  "SessionStart",
}


def _resolve_hook_event(intent: str, platform: str) -> str:
    """Convert a semantic intent to the concrete event name for a platform."""
    try:
        return HOOK_EVENT_MAP[intent][platform]
    except KeyError:
        raise ValueError(
            f"Unknown hook intent '{intent}' or platform '{platform}'. "
            f"Add the required mapping to HOOK_EVENT_MAP."
        )


# ── Framework Config Paths ────────────────────────────────
#
# Official path interpretation for each framework:
#   - Claude Code: CLAUDE_CONFIG_DIR first, otherwise ~/.claude
#     (not documented officially; verified through GitHub Issue #25762)
#   - Codex: CODEX_HOME first, otherwise ~/.codex (officially supported)
#     Codex hooks are stored in hooks.json and enabled with a config.toml feature flag.

def _home() -> Path:
    return Path.home()


def _resolve_claude_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env)
    return _home() / ".claude"


def _resolve_codex_dir() -> Path:
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env)
    return _home() / ".codex"


def _resolve_codex_config_toml() -> Path:
    return _resolve_codex_dir() / "config.toml"


def _resolve_ghost_alice_hooks_dir() -> Path:
    return _home() / ".ghost-alice" / "hooks"


def _resolve_source_hook_dispatcher() -> Path:
    return Path(__file__).with_name("ghost-alice-hook.mjs")


def _resolve_installed_hook_dispatcher() -> Path:
    return _resolve_ghost_alice_hooks_dir() / "ghost-alice-hook.mjs"


def _resolve_hook_companion_modules() -> list[Path]:
    """Files the dispatcher needs alongside it: relatively-imported .mjs modules and the
    reminder_texts.json data file it reads at runtime.

    These must be installed alongside the dispatcher, or the installed dispatcher fails at
    load with ERR_MODULE_NOT_FOUND or falls back to a minimal tool-checkpoint reminder.
    """
    source = _resolve_source_hook_dispatcher()
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return []
    names: list[str] = []
    marker = 'from "./'
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("import "):
            continue
        idx = stripped.find(marker)
        if idx == -1:
            continue
        rest = stripped[idx + len(marker):]
        end = rest.find('"')
        if end == -1:
            continue
        name = rest[:end]
        if name.endswith(".mjs") and name not in names:
            names.append(name)
    modules: list[Path] = []
    for name in names:
        candidate = source.with_name(name)
        if candidate.exists():
            modules.append(candidate)
    reminder_json = source.with_name("reminder_texts.json")
    if reminder_json.exists():
        modules.append(reminder_json)
    return modules


def _quote_command_arg(value: str | Path) -> str:
    text = str(value).replace("\\", "/")
    return '"' + text.replace('"', '\\"') + '"'


def _dispatcher_hook_command(platform: str, event: str, hook_name: str, marker: str, hook_id: str) -> str:
    dispatcher = _resolve_installed_hook_dispatcher()
    parts = [
        "node",
        _quote_command_arg(dispatcher),
        "--platform",
        platform,
        "--event",
        event,
        "--hook",
        hook_name,
        "--marker",
        _quote_command_arg(marker),
    ]
    if normalize_hook_id(hook_id) in {"session-intent", "tool-checkpoint", "hook-reminder"}:
        parts.extend([
            "--session-intent-root",
            _quote_command_arg(_session_intent_root()),
        ])
    return " ".join(parts)


def _command_entry(command: str) -> dict[str, Any]:
    return {
        "matcher": "",
        "hooks": [{"type": "command", "command": command}],
    }


def _platform_hook_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key == "codex":
        command = _hook_reminder_command(platform="codex", output_format="json")
    else:
        command = _hook_reminder_command(platform="claude", output_format="text")
    return _hook_runner_command_entry("prompt", command, HOOK_MARKER)


def _platform_prompt_pending_merge_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key == "codex":
        command = _prompt_pending_merge_command(platform="codex", output_format="json")
    else:
        command = _prompt_pending_merge_command(platform="claude", output_format="text")
    return _hook_runner_command_entry("pending-merge-prompt", command, PROMPT_PENDING_MERGE_MARKER)


def _platform_session_intent_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key == "codex":
        command = _session_intent_analyzer_command(platform="codex", output_format="json") + f" # {SESSION_INTENT_MARKER}"
    else:
        command = _session_intent_analyzer_command(platform="claude", output_format="text") + f" # {SESSION_INTENT_MARKER}"
    return _hook_runner_command_entry("session-intent", command, SESSION_INTENT_MARKER)


def _platform_web_search_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key == "codex":
        return _hook_runner_command_entry("web-search-first", WEB_SEARCH_FIRST_COMMAND_CODEX, WEB_SEARCH_FIRST_MARKER)
    return _hook_runner_command_entry("web-search-first", WEB_SEARCH_FIRST_COMMAND, WEB_SEARCH_FIRST_MARKER)


def _platform_tool_checkpoint_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key in {"claude", "codex"}:
        command = _dispatcher_hook_command(platform_key, event, "tool-checkpoint", TOOL_CHECKPOINT_MARKER, "tool-checkpoint")
        return _hook_runner_command_entry("tool-checkpoint", command, TOOL_CHECKPOINT_MARKER)
    return _hook_runner_command_entry("tool-checkpoint", TOOL_CHECKPOINT_COMMAND, TOOL_CHECKPOINT_MARKER)


def _platform_stop_hook_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key == "codex":
        return _hook_runner_command_entry("completion", _stop_hook_command("codex"), STOP_HOOK_MARKER)
    return _hook_runner_command_entry("completion", _claude_stop_hook_command(), STOP_HOOK_MARKER)


def _platform_session_start_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key == "codex":
        command = _session_start_command(platform="codex", output_format="json")
    else:
        command = _session_start_command(platform="claude", output_format="text")
    return _hook_runner_command_entry("session-start", command, SESSION_START_MARKER)


def _platform_io_trace_entry(platform_key: str, event: str) -> Optional[dict[str, Any]]:
    io_command = _io_trace_hook_command()
    if not io_command:
        return None
    return _command_entry(_hook_runner_command("io-trace", io_command, IO_TRACE_MARKER))


def _ensure_hook_dispatcher_installed(dry_run: bool = False) -> bool:
    source = _resolve_source_hook_dispatcher()
    target = _resolve_installed_hook_dispatcher()
    if not source.exists():
        raise FileNotFoundError(f"Ghost-ALICE hook dispatcher not found: {source}")

    hooks_dir = _resolve_ghost_alice_hooks_dir()
    pairs: list[tuple[Path, Path]] = [(source, target)]
    for companion in _resolve_hook_companion_modules():
        pairs.append((companion, hooks_dir / companion.name))

    pending: list[tuple[Path, Path]] = []
    for src, dst in pairs:
        if dst.exists():
            try:
                if src.read_bytes() == dst.read_bytes():
                    continue
            except OSError:
                pass
        pending.append((src, dst))

    if not pending:
        _log(_t("  Ghost-ALICE hook dispatcher is already current", "  Ghost-ALICE hook dispatcher is already current"))
        return False

    if dry_run:
        for src, dst in pending:
            _log(_t(f"DRY-RUN: Would copy {src} -> {dst}", f"DRY-RUN: Would copy {src} -> {dst}"))
        return True

    for src, dst in pending:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        _log(_t(f"  Ghost-ALICE hook file installed: {dst}", f"  Ghost-ALICE hook file installed: {dst}"))
    return True


def _running_on_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def _codex_hooks_supported() -> bool:
    """Codex hooks are supported by the current official Codex configuration surface."""
    return True


PLATFORMS: dict[str, dict[str, Any]] = {
    "claude": {
        "name": "Claude Code",
        "config_dir": _resolve_claude_dir,
        "hook_file": lambda: _resolve_claude_dir() / "settings.json",
        "hook_intent": "on_user_prompt",
        "hook_entry": HOOK_ENTRY,
        "stop_hook_intent": "on_agent_stop",
        "stop_hook_entry": STOP_HOOK_ENTRY,
        "session_start_intent": "on_session_start",
        "session_start_entry": SESSION_START_ENTRY,
        "detect": lambda: _resolve_claude_dir().is_dir(),
    },
    "codex": {
        "name": "Codex",
        "config_dir": _resolve_codex_dir,
        "hook_file": lambda: _resolve_codex_dir() / "hooks.json",
        "hook_intent": "on_user_prompt",
        "hook_entry": HOOK_ENTRY_CODEX,
        "stop_hook_intent": "on_agent_stop",
        "stop_hook_entry": STOP_HOOK_ENTRY_CODEX,
        "session_start_intent": "on_session_start",
        "session_start_entry": SESSION_START_ENTRY_CODEX,
        "detect": lambda: _resolve_codex_dir().is_dir(),
    },
}


def _node_runtime_available() -> bool:
    return bool(shutil.which("node") or shutil.which("node.exe"))


def _ensure_node_runtime_for_hook_install(platform_key: str) -> None:
    if platform_key not in {"claude", "codex"}:
        return
    if platform_key == "codex" and not _codex_hooks_supported():
        return
    platform = PLATFORMS[platform_key]
    if not platform["detect"]():
        return
    if _node_runtime_available():
        return
    name = platform["name"]
    raise RuntimeError(
        f"Node.js runtime is required for {name} hook enforcement because "
        "tool-checkpoint runs ghost-alice-hook.mjs. Install Node.js and rerun the installer."
    )

# ── Core Logic ─────────────────────────────────────────────

def _log(msg: str) -> None:
    print(f"  [install_hooks] {msg}")


def _t(ko: str, en: str) -> str:
    """Return the English message. Retained for call-site compatibility."""
    return en


def _read_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _log(_t(f"WARNING: Failed to parse JSON at {path} ({e})", f"WARNING: Failed to parse JSON at {path} ({e})"))
        _log(_t("  File may be corrupted. Backing up and starting with empty settings.", "  File may be corrupted. Backing up and starting with empty settings."))
        backup = path.with_suffix(".json.corrupt-bak")
        shutil.copy2(path, backup)
        _log(_t(f"  Corrupted file backed up: {backup}", f"  Corrupted file backed up: {backup}"))
        return {}
    except OSError as e:
        _log(_t(f"WARNING: Failed to read {path} ({e})", f"WARNING: Failed to read {path} ({e})"))
        return {}


def _write_settings(path: Path, data: dict, dry_run: bool = False) -> None:
    if dry_run:
        _log(_t(f"DRY-RUN: Would write to {path}:", f"DRY-RUN: Would write to {path}:"))
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(".json.bak")
        shutil.copy2(path, backup)
        _log(_t(f"Backup created: {backup}", f"Backup created: {backup}"))
        _rotate_backups(path, keep=3)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    _log(_t(f"Saved: {path}", f"Saved: {path}"))


def _rotate_backups(
    base_path: Path,
    keep: int = 3,
    _pending_root: Optional[Path] = None,
) -> None:
    """Collect base_path.name-prefixed .bak files in the same parent.

    Keep the newest entries by mtime and unlink the rest.

    Never rotate:
      1. symlinks, to avoid touching unintended targets
      2. paths under ~/.ghost-alice/pending-merges/, which are isolated user-change copies
    """
    parent = base_path.parent
    pattern = base_path.name + ".bak*"

    # Compute the absolute pending-merges prefix for the guard.
    if _pending_root is not None:
        pending_root: Optional[Path] = _pending_root
    else:
        try:
            pending_root = (Path.home() / ".ghost-alice" / "pending-merges").resolve()
        except OSError:
            pending_root = None

    candidates: list[Path] = []
    for p in parent.glob(pattern):
        if p.is_symlink():
            continue
        if not p.is_file():
            continue
        if pending_root is not None:
            try:
                resolved = p.resolve()
                if str(resolved).startswith(str(pending_root)):
                    continue
            except OSError:
                pass
        candidates.append(p)

    candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    for old in candidates[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def _read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_text_file(path: Path, content: str, dry_run: bool = False) -> None:
    if dry_run:
        _log(_t(f"DRY-RUN: Would write text to {path}:", f"DRY-RUN: Would write text to {path}:"))
        print(content, end="" if content.endswith("\n") else "\n")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(f"{path.suffix}.bak")
        shutil.copy2(path, backup)
        _log(_t(f"Backup created: {backup}", f"Backup created: {backup}"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    _log(_t(f"Saved: {path}", f"Saved: {path}"))


def _find_toml_section_bounds(lines: list[str], section_name: str) -> Tuple[Optional[int], int]:
    """Find the start/end line indices for a TOML section."""
    start: Optional[int] = None
    end = len(lines)
    section_header = f"[{section_name}]"

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not (stripped.startswith("[") and stripped.endswith("]")):
            continue
        if stripped.startswith("[["):
            continue
        if stripped == section_header:
            if start is None:
                start = idx
            continue
        if start is not None and idx > start:
            end = idx
            break

    return start, end


def _render_text_with_trailing_newline(lines: list[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"


def _codex_hook_feature_enabled_in_content(content: str) -> bool:
    return _codex_hook_feature_state_in_content(content) == "true"


def _codex_hook_feature_state_in_content(content: str) -> str:
    lines = content.splitlines()
    start, end = _find_toml_section_bounds(lines, "features")
    if start is None:
        return "missing"

    for raw_line in lines[start + 1:end]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != "hooks":
            continue
        return "true" if value.split("#", 1)[0].strip().lower() == "true" else "false"

    return "missing"


def _merge_codex_hook_feature_flag(content: str) -> tuple[str, bool]:
    """Ensure [features] hooks = true in config.toml content."""
    lines = content.splitlines()
    start, end = _find_toml_section_bounds(lines, "features")

    if start is None:
        new_content = content.rstrip("\n")
        if new_content:
            new_content += "\n\n"
        new_content += "[features]\nhooks = true\n"
        return new_content, True

    hooks_idx: Optional[int] = None
    deprecated_indices: list[int] = []

    for idx in range(start + 1, end):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _value = stripped.split("=", 1)
        key = key.strip()
        if key == "hooks":
            hooks_idx = idx
        elif key == "codex_hooks":
            deprecated_indices.append(idx)

    changed = bool(deprecated_indices)
    for idx in reversed(deprecated_indices):
        del lines[idx]

    if deprecated_indices:
        start, end = _find_toml_section_bounds(lines, "features")
        hooks_idx = None
        for idx in range(start + 1, end):
            stripped = lines[idx].strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _value = stripped.split("=", 1)
            if key.strip() == "hooks":
                hooks_idx = idx
                break

    if hooks_idx is not None:
        if _codex_hook_feature_enabled_in_content(_render_text_with_trailing_newline(lines)):
            return _render_text_with_trailing_newline(lines), changed
        lines[hooks_idx] = "hooks = true"
        return _render_text_with_trailing_newline(lines), True

    lines.insert(end, "hooks = true")
    return _render_text_with_trailing_newline(lines), True


def _ensure_codex_hook_feature_enabled(dry_run: bool = False) -> bool:
    """Ensure the hooks feature flag is enabled in Codex config.toml."""
    config_file = _resolve_codex_config_toml()
    current_content = _read_text_file(config_file)
    before_state = _codex_hook_feature_state_in_content(current_content)
    updated_content, changed = _merge_codex_hook_feature_flag(current_content)

    if not changed:
        _log(_t("  Codex config.toml already enables hooks", "  Codex config.toml already enables hooks"))
        return False

    _write_text_file(config_file, updated_content, dry_run=dry_run)
    if before_state != "true":
        _write_codex_hook_feature_change(
            config_file,
            before_state=before_state,
            after_state="true",
            dry_run=dry_run,
        )
    _log(_t("  Enabled hooks feature flag in Codex config.toml", "  Enabled hooks feature flag in Codex config.toml"))
    return True


def _codex_hook_feature_change_path() -> Path:
    return _home() / ".ghost-alice" / "install-state" / "codex-hook-feature-change.json"


def _write_codex_hook_feature_change(
    config_file: Path,
    *,
    before_state: str,
    after_state: str,
    dry_run: bool = False,
) -> None:
    change = {
        "schema_version": "codex-hook-feature-change.v1",
        "kind": "codex_hooks_feature_flag",
        "path": config_file.as_posix(),
        "before_state": before_state,
        "after_state": after_state,
        "applied_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    path = _codex_hook_feature_change_path()
    if dry_run:
        _log(_t(f"  DRY-RUN: Would record Codex hooks feature flag change: {path}", f"  DRY-RUN: Would record Codex hooks feature flag change: {path}"))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(change, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _log(_t("  Recorded Codex hooks feature flag rollback metadata", "  Recorded Codex hooks feature flag rollback metadata"))


def _codex_hook_feature_enabled() -> bool:
    config_file = _resolve_codex_config_toml()
    if not config_file.exists():
        return False
    try:
        content = _read_text_file(config_file)
    except OSError:
        return False
    return _codex_hook_feature_enabled_in_content(content)


CODEX_HOOK_EVENT_KEY_LABELS = {
    "PreToolUse": "pre_tool_use",
    "PermissionRequest": "permission_request",
    "PostToolUse": "post_tool_use",
    "PreCompact": "pre_compact",
    "PostCompact": "post_compact",
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
}


CODEX_HOOK_EVENTS_WITH_MATCHER = {
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "SubagentStart",
    "SubagentStop",
}


def _codex_hook_event_key_label(event_name: str) -> str:
    return CODEX_HOOK_EVENT_KEY_LABELS.get(event_name, event_name)


def _codex_matcher_for_event(event_name: str, group: dict[str, Any]) -> Optional[str]:
    if event_name not in CODEX_HOOK_EVENTS_WITH_MATCHER:
        return None
    matcher = group.get("matcher")
    return matcher if isinstance(matcher, str) else None


def _codex_command_hook_hash(event_name: str, group: dict[str, Any], hook: dict[str, Any]) -> str:
    command = hook.get("command", "")
    if _running_on_windows():
        command = hook.get("commandWindows") or hook.get("command_windows") or command

    timeout_raw = hook.get("timeout", 600)
    try:
        timeout = max(int(timeout_raw), 1)
    except (TypeError, ValueError):
        timeout = 600

    normalized_handler: dict[str, Any] = {
        "async": bool(hook.get("async", False)),
        "command": command,
        "timeout": timeout,
        "type": "command",
    }
    status_message = hook.get("statusMessage")
    if status_message is not None:
        normalized_handler["statusMessage"] = status_message

    identity: dict[str, Any] = {
        "event_name": _codex_hook_event_key_label(event_name),
        "hooks": [normalized_handler],
    }
    matcher = _codex_matcher_for_event(event_name, group)
    if matcher is not None:
        identity["matcher"] = matcher

    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _codex_hook_state_key(settings_file: Path, event_name: str, group_index: int, handler_index: int) -> str:
    return f"{settings_file.as_posix()}:{_codex_hook_event_key_label(event_name)}:{group_index}:{handler_index}"


def _is_ghost_alice_hook_command(command: str) -> bool:
    return any(marker in command for marker in GHOST_ALICE_HOOK_MARKERS)


def _codex_trusted_hook_state_entries(settings_file: Path, settings: dict[str, Any]) -> dict[str, str]:
    hooks_obj = settings.get("hooks")
    if not isinstance(hooks_obj, dict):
        return {}

    entries: dict[str, str] = {}
    for event_name, groups in hooks_obj.items():
        if not isinstance(groups, list):
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            hooks = group.get("hooks")
            if not isinstance(hooks, list):
                continue
            for handler_index, hook in enumerate(hooks):
                if not isinstance(hook, dict) or hook.get("type") != "command":
                    continue
                command = hook.get("command", "")
                if not isinstance(command, str) or not _is_ghost_alice_hook_command(command):
                    continue
                key = _codex_hook_state_key(settings_file, event_name, group_index, handler_index)
                entries[key] = _codex_command_hook_hash(event_name, group, hook)
    return entries


def _toml_basic_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _find_toml_header_bounds(lines: list[str], header: str) -> tuple[Optional[int], int]:
    start: Optional[int] = None
    end = len(lines)
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped == header:
            start = idx
            continue
        if start is not None and stripped.startswith("[") and stripped.endswith("]"):
            end = idx
            break
    return start, end


def _merge_codex_hook_trust_state(content: str, state_entries: dict[str, str]) -> tuple[str, bool]:
    lines = content.splitlines()
    changed = False

    for key, trusted_hash in state_entries.items():
        header = f"[hooks.state.{_toml_basic_string(key)}]"
        start, end = _find_toml_header_bounds(lines, header)
        trusted_line = f'trusted_hash = "{trusted_hash}"'

        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([header, trusted_line])
            changed = True
            continue

        trusted_idx: Optional[int] = None
        for idx in range(start + 1, end):
            stripped = lines[idx].strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            candidate_key, _candidate_value = stripped.split("=", 1)
            if candidate_key.strip() == "trusted_hash":
                trusted_idx = idx
                break

        if trusted_idx is None:
            lines.insert(end, trusted_line)
            changed = True
        elif lines[trusted_idx].strip() != trusted_line:
            lines[trusted_idx] = trusted_line
            changed = True

    return _render_text_with_trailing_newline(lines), changed


def _ensure_codex_hook_trust_state(
    settings_file: Path,
    settings: dict[str, Any],
    dry_run: bool = False,
) -> bool:
    state_entries = _codex_trusted_hook_state_entries(settings_file, settings)
    if not state_entries:
        return False

    config_file = _resolve_codex_config_toml()
    current_content = _read_text_file(config_file)
    updated_content, changed = _merge_codex_hook_trust_state(current_content, state_entries)

    if not changed:
        _log(_t("  Codex hook trust state already current", "  Codex hook trust state already current"))
        return False

    _write_text_file(config_file, updated_content, dry_run=dry_run)
    _log(_t("  Trusted installed Ghost-ALICE Codex hooks", "  Trusted installed Ghost-ALICE Codex hooks"))
    return True


def _is_dispatcher_command(command: str) -> bool:
    return "ghost-alice-hook.mjs" in command


def _hook_already_exists(
    hooks_list: list,
    marker: str,
    *,
    require_dispatcher: bool = False,
    expected_command: Optional[str] = None,
) -> bool:
    """Return whether the hook list already contains the same marker."""
    for entry in hooks_list:
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if (
                marker in cmd
                and (not require_dispatcher or _is_dispatcher_command(cmd))
                and (expected_command is None or cmd == expected_command)
            ):
                return True
    return False


def _entry_command(entry: dict[str, Any]) -> str:
    hooks = entry.get("hooks", [])
    if not hooks:
        return ""
    return hooks[0].get("command", "")


def _hook_marker_match_status(
    hooks_list: list,
    marker: str,
    *,
    require_dispatcher: bool = False,
    expected_command: Optional[str] = None,
) -> tuple[bool, bool]:
    marker_present = False
    exact_match = False
    for entry in hooks_list:
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if marker not in cmd:
                continue
            marker_present = True
            dispatcher_ok = not require_dispatcher or _is_dispatcher_command(cmd)
            command_ok = expected_command is None or cmd == expected_command
            if dispatcher_ok and command_ok:
                exact_match = True
    return marker_present, exact_match


def _io_trace_hook_command() -> str:
    io_script = _resolve_io_trace_script()
    if not io_script:
        return ""
    python = sys.executable.replace("\\", "/")
    return f'"{python}" "{io_script}" # {IO_TRACE_MARKER}'


def _hook_runner_id_match(command: str, hook_id: str | None) -> bool:
    if not hook_id:
        return False
    normalized = re.escape(normalize_hook_id(hook_id))
    return re.search(rf"hook_profile_gate\.py[\"']?\s+run\s+{normalized}(?:\s|$)", command) is not None


def _remove_stale_hook_entries(
    hooks_list: list,
    marker: str,
    expected_command: str,
    *,
    require_dispatcher: bool = False,
    extra_markers: tuple[str, ...] = (),
    hook_id: str | None = None,
) -> int:
    """Remove stale entries for the same managed hook when command content differs."""
    markers = (marker, *extra_markers)
    removed = 0
    i = 0
    while i < len(hooks_list):
        entry = hooks_list[i]
        marker_commands = [
            hook.get("command", "")
            for hook in entry.get("hooks", [])
            if any(candidate in hook.get("command", "") for candidate in markers)
            or _hook_runner_id_match(hook.get("command", ""), hook_id)
        ]
        if marker_commands and not any(
            cmd == expected_command and (not require_dispatcher or _is_dispatcher_command(cmd))
            for cmd in marker_commands
        ):
            hooks_list.pop(i)
            removed += 1
        else:
            i += 1
    return removed


def _remove_hook_entries(hooks_list: list, marker: str) -> int:
    """Remove hook-list entries containing the marker and return the removal count."""
    removed = 0
    i = 0
    while i < len(hooks_list):
        entry = hooks_list[i]
        has_marker = False
        for hook in entry.get("hooks", []):
            if marker in hook.get("command", ""):
                has_marker = True
                break
        if has_marker:
            hooks_list.pop(i)
            removed += 1
        else:
            i += 1
    return removed


def _entry_contains_marker(entry: dict[str, Any], marker: str) -> bool:
    return any(marker in hook.get("command", "") for hook in entry.get("hooks", []))


def _reorder_user_prompt_governance_hooks(hooks_list: list) -> bool:
    """Keep managed UserPromptSubmit hook entries in install-surface order.

    This is not the full semantic graph. session-intent-analyzer fans out to
    skill-evolution (report-only) and jailbreak-detector; HOOK_MARKER is only
    the task-router reminder that waits for downstream-gates.json.
    """
    ordered_markers = [
        PROMPT_PENDING_MERGE_MARKER,
        SESSION_INTENT_MARKER,
        HOOK_MARKER,  # task-router reminder, not task-router execution.
        WEB_SEARCH_FIRST_MARKER,
    ]
    managed: dict[str, dict[str, Any]] = {}
    first_managed_index: int | None = None

    for index, entry in enumerate(hooks_list):
        marker = next((item for item in ordered_markers if _entry_contains_marker(entry, item)), "")
        if not marker:
            continue
        managed[marker] = entry
        if first_managed_index is None:
            first_managed_index = index

    if first_managed_index is None or any(marker not in managed for marker in ordered_markers):
        return False

    rebuilt: list[dict[str, Any]] = []
    inserted = False
    for entry in hooks_list:
        marker = next((item for item in ordered_markers if _entry_contains_marker(entry, item)), "")
        if not marker:
            rebuilt.append(entry)
            continue
        if not inserted:
            rebuilt.extend(managed[item] for item in ordered_markers)
            inserted = True

    if rebuilt == hooks_list:
        return False
    hooks_list[:] = rebuilt
    return True


GHOST_ALICE_HOOK_MARKERS = (
    PROMPT_PENDING_MERGE_MARKER,
    HOOK_MARKER,
    SESSION_INTENT_MARKER,
    WEB_SEARCH_FIRST_MARKER,
    TOOL_CHECKPOINT_MARKER,
    STOP_HOOK_MARKER,
    SESSION_START_MARKER,
    IO_TRACE_MARKER,
)


def _remove_all_ghost_alice_hooks(hooks_obj: dict[str, Any]) -> int:
    removed = 0
    for hook_list in hooks_obj.values():
        if not isinstance(hook_list, list):
            continue
        for marker in GHOST_ALICE_HOOK_MARKERS:
            removed += _remove_hook_entries(hook_list, marker)
    return removed


def _ensure_claude_ghost_alice_skill_permissions(settings: dict[str, Any]) -> bool:
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions

    allow = permissions.get("allow")
    if not isinstance(allow, list):
        allow = []
        permissions["allow"] = allow

    original_len = len(allow)
    allow[:] = [item for item in allow if not _is_stale_legacy_checkout_path_permission(item)]
    existing = {item for item in allow if isinstance(item, str)}
    changed = len(allow) != original_len
    for rule in _claude_ghost_alice_skill_permissions():
        if rule in existing:
            continue
        allow.append(rule)
        existing.add(rule)
        changed = True
    return changed


# ── Install ───────────────────────────────────────────────

def install_hook(platform_key: str, dry_run: bool = False) -> str:
    """Install hooks into a single framework.

    Return values:
      "installed" - newly installed
      "already"   - skipped because the same hook already exists
      "skipped"   - skipped because the framework is not installed
      "unsupported" - skipped because the current runtime does not support it
      "error"     - install failed
    """
    platform = PLATFORMS[platform_key]
    name = platform["name"]
    settings_file = platform["hook_file"]()
    config_dir = platform["config_dir"]()
    hook_key = _resolve_hook_event(platform["hook_intent"], platform_key)

    _log(f"── {name} ──")

    if platform_key == "codex" and not _codex_hooks_supported():
        _log(_t("  Codex hooks are unavailable in this runtime. Skipping", "  Codex hooks are unavailable in this runtime. Skipping"))
        return "unsupported"

    if not platform["detect"]():
        _log(_t(f"  {config_dir} not found. Assuming {name} is not installed, skipping", f"  {config_dir} not found. Assuming {name} is not installed, skipping"))
        return "skipped"

    _log(_t(f"  Hook config file: {settings_file}", f"  Hook config file: {settings_file}"))
    settings = _read_settings(settings_file)

    changed = False

    if platform_key == "claude":
        if _ensure_claude_ghost_alice_skill_permissions(settings):
            _log(_t("  Updated Ghost-ALICE Skill permissions", "  Updated Ghost-ALICE Skill permissions"))
            changed = True

    if "hooks" not in settings:
        settings["hooks"] = {}
    hooks_obj = settings["hooks"]

    if hook_key not in hooks_obj:
        hooks_obj[hook_key] = []
    hook_list = hooks_obj[hook_key]

    require_dispatcher = False

    if platform_key in {"claude", "codex"}:
        if _ensure_hook_dispatcher_installed(dry_run=dry_run):
            changed = True

    # Install the prompt pending-merge precheck hook.
    # This is the first entry on the UserPromptSubmit hook surface.
    # Keep it separate from the task-router reminder so user-asset protection
    # runs before semantic routing.
    prompt_pending_merge_entry = _platform_prompt_pending_merge_entry(platform_key, hook_key)
    prompt_pending_merge_command = _entry_command(prompt_pending_merge_entry)
    removed = _remove_stale_hook_entries(
        hook_list,
        PROMPT_PENDING_MERGE_MARKER,
        prompt_pending_merge_command,
        require_dispatcher=require_dispatcher,
        hook_id="pending-merge-prompt",
    )
    if removed:
        _log(_t(f"  Removed {removed} stale prompt pending-merge hook entry(ies)", f"  Removed {removed} stale prompt pending-merge hook entry(ies)"))
        changed = True

    if _hook_already_exists(
        hook_list,
        PROMPT_PENDING_MERGE_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=prompt_pending_merge_command,
    ):
        _log(_t("  prompt pending-merge hook already exists. Skipping", "  prompt pending-merge hook already exists. Skipping"))
    else:
        hook_list.append(prompt_pending_merge_entry)
        _log(_t("  prompt pending-merge hook added", "  prompt pending-merge hook added"))
        changed = True

    hook_entry = _platform_hook_entry(platform_key, hook_key)
    hook_command = _entry_command(hook_entry)

    removed = _remove_stale_hook_entries(
        hook_list,
        HOOK_MARKER,
        hook_command,
        require_dispatcher=require_dispatcher,
        hook_id="prompt",
    )
    if removed:
        _log(_t(f"  Removed {removed} stale UserPromptSubmit hook entry(ies)", f"  Removed {removed} stale UserPromptSubmit hook entry(ies)"))
        changed = True

    if _hook_already_exists(
        hook_list,
        HOOK_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=hook_command,
    ):
        _log(_t("  UserPromptSubmit hook already exists. Skipping", "  UserPromptSubmit hook already exists. Skipping"))
    else:
        hook_list.append(hook_entry)
        _log(_t("  UserPromptSubmit hook added", "  UserPromptSubmit hook added"))
        changed = True

    # Install the session-intent-analyzer hook (updates the session intent ledger for every user input).
    session_intent_entry = _platform_session_intent_entry(platform_key, hook_key)
    session_intent_command = _entry_command(session_intent_entry)
    removed = _remove_stale_hook_entries(
        hook_list,
        SESSION_INTENT_MARKER,
        session_intent_command,
        require_dispatcher=require_dispatcher,
        hook_id="session-intent",
    )
    for legacy_marker in LEGACY_SESSION_INTENT_MARKERS:
        if _hook_already_exists(hook_list, legacy_marker):
            removed += _remove_hook_entries(hook_list, legacy_marker)
    if removed:
        _log(_t(f"  Removed {removed} stale session-intent-analyzer hook entry(ies)", f"  Removed {removed} stale session-intent-analyzer hook entry(ies)"))
        changed = True

    if _hook_already_exists(
        hook_list,
        SESSION_INTENT_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=session_intent_command,
    ):
        _log(_t("  session-intent-analyzer hook already exists. Skipping", "  session-intent-analyzer hook already exists. Skipping"))
    else:
        hook_list.append(session_intent_entry)
        _log(_t("  session-intent-analyzer hook added", "  session-intent-analyzer hook added"))
        changed = True

    # Install the web-search-first hook (rule 10: web search before external tool claims).
    # Separate entry on the same UserPromptSubmit event; injects text alongside the task-router reminder.
    web_search_entry = _platform_web_search_entry(platform_key, hook_key)
    web_search_command = _entry_command(web_search_entry)
    removed = _remove_stale_hook_entries(
        hook_list,
        WEB_SEARCH_FIRST_MARKER,
        web_search_command,
        require_dispatcher=require_dispatcher,
        hook_id="web-search-first",
    )
    if removed:
        _log(_t(f"  Replaced {removed} stale web-search-first hook entry(ies)", f"  Replaced {removed} stale web-search-first hook entry(ies)"))
        changed = True

    if _hook_already_exists(
        hook_list,
        WEB_SEARCH_FIRST_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=web_search_command,
    ):
        _log(_t("  web-search-first hook already exists. Skipping", "  web-search-first hook already exists. Skipping"))
    else:
        hook_list.append(web_search_entry)
        _log(_t("  web-search-first hook added (rule 10)", "  web-search-first hook added (rule 10)"))
        changed = True

    if _reorder_user_prompt_governance_hooks(hook_list):
        _log(_t(
            "  Reordered UserPromptSubmit hook surface: pending-merge -> session-intent(fan-out: skill-evolution report-only, jailbreak-detector gate) -> task-router-reminder -> web-search-first",
            "  Reordered UserPromptSubmit hook surface: pending-merge -> session-intent(fan-out: skill-evolution report-only, jailbreak-detector gate) -> task-router-reminder -> web-search-first",
        ))
        changed = True

    # Install the pre_tool_use hook (tool-checkpoint: enforce narration before tool calls).
    tool_checkpoint_key = _resolve_hook_event("pre_tool_use", platform_key)
    tool_checkpoint_entry = _platform_tool_checkpoint_entry(platform_key, tool_checkpoint_key)
    tool_checkpoint_command = _entry_command(tool_checkpoint_entry)
    if tool_checkpoint_key not in hooks_obj:
        hooks_obj[tool_checkpoint_key] = []
    tool_checkpoint_list = hooks_obj[tool_checkpoint_key]
    removed = _remove_stale_hook_entries(
        tool_checkpoint_list,
        TOOL_CHECKPOINT_MARKER,
        tool_checkpoint_command,
        require_dispatcher=require_dispatcher,
        extra_markers=LEGACY_TOOL_CHECKPOINT_MARKERS,
        hook_id="tool-checkpoint",
    )
    if removed:
        _log(_t(f"  Removed {removed} stale {tool_checkpoint_key} tool-checkpoint hook entry(ies)", f"  Removed {removed} stale {tool_checkpoint_key} tool-checkpoint hook entry(ies)"))
        changed = True

    if _hook_already_exists(
        tool_checkpoint_list,
        TOOL_CHECKPOINT_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=tool_checkpoint_command,
    ):
        _log(_t(f"  {tool_checkpoint_key} tool-checkpoint hook already exists. Skipping", f"  {tool_checkpoint_key} tool-checkpoint hook already exists. Skipping"))
    else:
        tool_checkpoint_list.append(tool_checkpoint_entry)
        _log(_t(f"  {tool_checkpoint_key} tool-checkpoint hook added", f"  {tool_checkpoint_key} tool-checkpoint hook added"))
        changed = True

    # Install the on_agent_stop hook (verification-before-completion reminder).
    stop_intent = platform.get("stop_hook_intent")
    if stop_intent and platform.get("stop_hook_entry"):
        stop_key = _resolve_hook_event(stop_intent, platform_key)
        stop_entry = _platform_stop_hook_entry(platform_key, stop_key)
        stop_command = _entry_command(stop_entry)
        if stop_key not in hooks_obj:
            hooks_obj[stop_key] = []
        stop_list = hooks_obj[stop_key]
        removed = _remove_stale_hook_entries(
            stop_list,
            STOP_HOOK_MARKER,
            stop_command,
            require_dispatcher=require_dispatcher,
            hook_id="completion",
        )
        if removed:
            _log(_t(f"  Removed {removed} stale {stop_key} completion hook entry(ies)", f"  Removed {removed} stale {stop_key} completion hook entry(ies)"))
            changed = True
        if _hook_already_exists(
            stop_list,
            STOP_HOOK_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=stop_command,
        ):
            _log(_t(f"  {stop_key} completion hook already exists. Skipping", f"  {stop_key} completion hook already exists. Skipping"))
        else:
            stop_list.append(stop_entry)
            _log(_t(f"  {stop_key} completion hook added", f"  {stop_key} completion hook added"))
            changed = True

        # Install the SessionStart hook (pending-merge-session-start layer: automatic merge-companion check).
    ss_intent = platform.get("session_start_intent")
    if ss_intent and platform.get("session_start_entry"):
        ss_key = _resolve_hook_event(ss_intent, platform_key)
        ss_entry = _platform_session_start_entry(platform_key, ss_key)
        ss_command = _entry_command(ss_entry)
        if ss_key not in hooks_obj:
            hooks_obj[ss_key] = []
        ss_list = hooks_obj[ss_key]
        removed = _remove_stale_hook_entries(
            ss_list,
            SESSION_START_MARKER,
            ss_command,
            require_dispatcher=require_dispatcher,
            hook_id="session-start",
        )
        if removed:
            _log(_t(f"  Removed {removed} stale {ss_key} session-start hook entry(ies)", f"  Removed {removed} stale {ss_key} session-start hook entry(ies)"))
            changed = True
        if _hook_already_exists(
            ss_list,
            SESSION_START_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=ss_command,
        ):
            _log(_t(f"  {ss_key} session-start hook already exists. Skipping", f"  {ss_key} session-start hook already exists. Skipping"))
        else:
            ss_list.append(ss_entry)
            _log(_t(f"  {ss_key} session-start hook added", f"  {ss_key} session-start hook added"))
            changed = True

    # Install the io-trace tool-use audit hook.
    post_tool_key = _resolve_hook_event("post_tool_use", platform_key)
    io_entry = _platform_io_trace_entry(platform_key, post_tool_key)
    if io_entry:
        io_command = _entry_command(io_entry)
        if post_tool_key not in hooks_obj:
            hooks_obj[post_tool_key] = []
        pt_list = hooks_obj[post_tool_key]
        removed = _remove_stale_hook_entries(
            pt_list,
            IO_TRACE_MARKER,
            io_command,
            require_dispatcher=require_dispatcher,
            hook_id="io-trace",
        )
        if removed:
            _log(_t(f"  Removed {removed} stale {post_tool_key} io-trace hook entry(ies)", f"  Removed {removed} stale {post_tool_key} io-trace hook entry(ies)"))
            changed = True
        if not _hook_already_exists(
            pt_list,
            IO_TRACE_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=io_command,
        ):
            pt_list.append(io_entry)
            _log(_t(f"  {post_tool_key} io-trace hook added", f"  {post_tool_key} io-trace hook added"))
            changed = True
        else:
            _log(_t(f"  {post_tool_key} io-trace hook already exists. Skipping", f"  {post_tool_key} io-trace hook already exists. Skipping"))
    elif platform_key in {"claude", "codex"}:
        _log(_t("  io_trace_hook.py not found. Skipping io-trace hook", "  io_trace_hook.py not found. Skipping io-trace hook"))

    if platform_key == "codex":
        if _ensure_codex_hook_feature_enabled(dry_run=dry_run):
            changed = True
        if _ensure_codex_hook_trust_state(settings_file, settings, dry_run=dry_run):
            changed = True

    if not changed:
        return "already"

    _write_settings(settings_file, settings, dry_run=dry_run)
    _log(_t("  Hook installation complete", "  Hook installation complete"))
    return "installed"


# ── Uninstall ─────────────────────────────────────────────

def uninstall_hook(platform_key: str, dry_run: bool = False) -> str:
    """Remove hooks from a single framework.

    Return values:
      "removed"   - hooks removed
      "not_found" - no hooks to remove
      "skipped"   - skipped because the framework is not installed
      "unsupported" - skipped because the current runtime does not support it
      "error"     - removal failed
    """
    platform = PLATFORMS[platform_key]
    name = platform["name"]
    settings_file = platform["hook_file"]()
    config_dir = platform["config_dir"]()
    hook_key = _resolve_hook_event(platform["hook_intent"], platform_key)

    _log(f"── {name} ──")

    if platform_key == "codex" and not _codex_hooks_supported():
        _log(_t("  Codex hooks are unavailable in this runtime. Skipping", "  Codex hooks are unavailable in this runtime. Skipping"))
        return "unsupported"

    if not platform["detect"]():
        _log(_t(f"  {config_dir} not found. Skipping", f"  {config_dir} not found. Skipping"))
        return "skipped"

    if not settings_file.exists():
        _log(_t("  No hook config file. There are no hooks to remove", "  No hook config file. There are no hooks to remove"))
        return "not_found"

    _log(_t(f"  Hook config file: {settings_file}", f"  Hook config file: {settings_file}"))
    settings = _read_settings(settings_file)

    hooks_obj = settings.get("hooks", {})
    hook_list = hooks_obj.get(hook_key, [])

    total_removed = 0

    if _hook_already_exists(hook_list, PROMPT_PENDING_MERGE_MARKER):
        total_removed += _remove_hook_entries(hook_list, PROMPT_PENDING_MERGE_MARKER)

    if _hook_already_exists(hook_list, HOOK_MARKER):
        total_removed += _remove_hook_entries(hook_list, HOOK_MARKER)

    # Remove the session-intent-analyzer hook (same UserPromptSubmit event).
    if _hook_already_exists(hook_list, SESSION_INTENT_MARKER):
        total_removed += _remove_hook_entries(hook_list, SESSION_INTENT_MARKER)
    for legacy_marker in LEGACY_SESSION_INTENT_MARKERS:
        if _hook_already_exists(hook_list, legacy_marker):
            total_removed += _remove_hook_entries(hook_list, legacy_marker)

    # Remove the web-search-first hook (same UserPromptSubmit event).
    if _hook_already_exists(hook_list, WEB_SEARCH_FIRST_MARKER):
        total_removed += _remove_hook_entries(hook_list, WEB_SEARCH_FIRST_MARKER)

    # Remove the tool-checkpoint PreToolUse hook.
    tool_checkpoint_key = _resolve_hook_event("pre_tool_use", platform_key)
    tool_checkpoint_list = hooks_obj.get(tool_checkpoint_key, [])
    for marker in (TOOL_CHECKPOINT_MARKER, *LEGACY_TOOL_CHECKPOINT_MARKERS):
        if _hook_already_exists(tool_checkpoint_list, marker):
            total_removed += _remove_hook_entries(tool_checkpoint_list, marker)

    # Remove the on_agent_stop hook.
    stop_intent = platform.get("stop_hook_intent")
    if stop_intent:
        stop_key = _resolve_hook_event(stop_intent, platform_key)
        stop_list = hooks_obj.get(stop_key, [])
        if _hook_already_exists(stop_list, STOP_HOOK_MARKER):
            total_removed += _remove_hook_entries(stop_list, STOP_HOOK_MARKER)

    # Remove the SessionStart hook.
    ss_intent = platform.get("session_start_intent")
    if ss_intent:
        ss_key = _resolve_hook_event(ss_intent, platform_key)
        ss_list = hooks_obj.get(ss_key, [])
        if _hook_already_exists(ss_list, SESSION_START_MARKER):
            total_removed += _remove_hook_entries(ss_list, SESSION_START_MARKER)

    # Also remove the io-trace PostToolUse hook.
    post_tool_key = _resolve_hook_event("post_tool_use", platform_key)
    pt_list = hooks_obj.get(post_tool_key, [])
    if _hook_already_exists(pt_list, IO_TRACE_MARKER):
        total_removed += _remove_hook_entries(pt_list, IO_TRACE_MARKER)

    if total_removed == 0:
        _log(_t("  No hooks found. Nothing to remove", "  No hooks found. Nothing to remove"))
        return "not_found"

    _write_settings(settings_file, settings, dry_run=dry_run)
    _log(_t(f"  Removed {total_removed} hook entry(ies)", f"  Removed {total_removed} hook entry(ies)"))
    return "removed"


# ── Status Check ──────────────────────────────────────────

def check_status_detail(platform_key: str) -> HookStatus:
    """Return structured hook installation status for a single framework."""
    platform = PLATFORMS[platform_key]
    name = platform["name"]
    settings_file = platform["hook_file"]()
    config_dir = platform["config_dir"]()
    hook_key = _resolve_hook_event(platform["hook_intent"], platform_key)

    if platform_key == "codex" and not _codex_hooks_supported():
        _log(_t(f"  {name}: hooks are unavailable in this runtime", f"  {name}: hooks are unavailable in this runtime"))
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_UNSUPPORTED",
            status_label=STATUS_LABELS["unsupported"],
            legacy_status="unsupported",
            details={"reason": "runtime_unsupported"},
            unsupported=True,
        )

    if not platform["detect"]():
        _log(_t(f"  {name}: not installed (directory missing)", f"  {name}: not installed (directory missing)"))
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_PLATFORM_MISSING",
            status_label=STATUS_LABELS["skipped"],
            legacy_status="skipped",
            details={"reason": "platform_dir_missing"},
        )

    if not settings_file.exists():
        _log(_t(f"  {name}: no hooks (hook config file missing)", f"  {name}: no hooks (hook config file missing)"))
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_MISSING",
            status_label=_t("hook config file missing", "hook config file missing"),
            legacy_status="missing",
            details={"missing": ["config-file"]},
            missing_reason="config_file_absent",
        )

    settings = _read_settings(settings_file)
    hooks_obj = settings.get("hooks", {})
    hook_list = hooks_obj.get(hook_key, [])

    require_dispatcher = False
    prompt_pending_merge_entry = _platform_prompt_pending_merge_entry(platform_key, hook_key)
    prompt_pending_merge_present, prompt_pending_merge_ok = _hook_marker_match_status(
        hook_list,
        PROMPT_PENDING_MERGE_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=_entry_command(prompt_pending_merge_entry),
    )

    prompt_entry = _platform_hook_entry(platform_key, hook_key)
    prompt_present, prompt_ok = _hook_marker_match_status(
        hook_list,
        HOOK_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=_entry_command(prompt_entry),
    )

    web_search_entry = _platform_web_search_entry(platform_key, hook_key)
    web_search_present, web_search_ok = _hook_marker_match_status(
        hook_list,
        WEB_SEARCH_FIRST_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=_entry_command(web_search_entry),
    )

    session_intent_entry = _platform_session_intent_entry(platform_key, hook_key)
    session_intent_present, session_intent_ok = _hook_marker_match_status(
        hook_list,
        SESSION_INTENT_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=_entry_command(session_intent_entry),
    )

    tool_checkpoint_key = _resolve_hook_event("pre_tool_use", platform_key)
    tool_checkpoint_list = hooks_obj.get(tool_checkpoint_key, [])
    tool_checkpoint_entry = _platform_tool_checkpoint_entry(platform_key, tool_checkpoint_key)
    tool_checkpoint_present, tool_checkpoint_ok = _hook_marker_match_status(
        tool_checkpoint_list,
        TOOL_CHECKPOINT_MARKER,
        require_dispatcher=require_dispatcher,
        expected_command=_entry_command(tool_checkpoint_entry),
    )

    stop_ok = False
    stop_present = False
    stop_intent = platform.get("stop_hook_intent")
    if stop_intent:
        stop_key = _resolve_hook_event(stop_intent, platform_key)
        stop_list = hooks_obj.get(stop_key, [])
        stop_entry = _platform_stop_hook_entry(platform_key, stop_key)
        stop_present, stop_ok = _hook_marker_match_status(
            stop_list,
            STOP_HOOK_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=_entry_command(stop_entry),
        )

    session_start_ok = True
    session_start_present = True
    ss_intent = platform.get("session_start_intent")
    if ss_intent and platform.get("session_start_entry"):
        ss_key = _resolve_hook_event(ss_intent, platform_key)
        ss_list = hooks_obj.get(ss_key, [])
        ss_entry = _platform_session_start_entry(platform_key, ss_key)
        session_start_present, session_start_ok = _hook_marker_match_status(
            ss_list,
            SESSION_START_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=_entry_command(ss_entry),
        )

    io_trace_ok = True
    io_trace_present = True
    post_tool_key = _resolve_hook_event("post_tool_use", platform_key)
    io_entry = _platform_io_trace_entry(platform_key, post_tool_key)
    if io_entry:
        pt_list = hooks_obj.get(post_tool_key, [])
        io_trace_present, io_trace_ok = _hook_marker_match_status(
            pt_list,
            IO_TRACE_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=_entry_command(io_entry),
        )

    if platform_key == "codex" and not _codex_hook_feature_enabled():
        _log(_t(f"  {name}: hooks.json exists but config.toml does not enable hooks", f"  {name}: hooks.json exists but config.toml does not enable hooks"))
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_CONFIG_DISABLED",
            status_label=_t("hook config disabled", "hook config disabled"),
            legacy_status="missing",
            details={"reason": "feature_flag_disabled"},
        )

    required = {
        "pending-merge-prompt": (prompt_pending_merge_present, prompt_pending_merge_ok),
        "prompt": (prompt_present, prompt_ok),
        "session-intent": (session_intent_present, session_intent_ok),
        "web-search-first": (web_search_present, web_search_ok),
        "tool-checkpoint": (tool_checkpoint_present, tool_checkpoint_ok),
        "completion": (stop_present, stop_ok),
        "session-start": (session_start_present, session_start_ok),
        "io-trace": (io_trace_present, io_trace_ok),
    }
    missing = [label for label, (present, ok) in required.items() if not present]
    drifted = [label for label, (present, ok) in required.items() if present and not ok]
    incomplete = missing + drifted

    if not incomplete:
        if platform_key == "codex":
            # Codex hook files are wired, but its hook event semantics are not
            # confirmed by a live runtime smoke. Stay instruction-backed rather than
            # report native runtime-verified. legacy_status stays "installed" so the
            # installer flow and legacy string API are unchanged; only the report
            # surface and the structured flag are made honest.
            _log(_t(
                f"  {name}: hook files installed (instruction-backed, pending runtime smoke evidence)",
                f"  {name}: hook files installed (instruction-backed, pending runtime smoke evidence)",
            ))
            installed_label = STATUS_LABELS["installed"]
            return HookStatus(
                platform=platform_key,
                status_token="HOOK_INSTALLED_INSTRUCTION_BACKED",
                status_label=_t(
                    f"{installed_label} (instruction-backed, pending smoke evidence)",
                    f"{installed_label} (instruction-backed, pending smoke evidence)",
                ),
                legacy_status="installed",
                details={
                    "installed": list(required.keys()),
                    "runtime_semantics": "unverified-pending-smoke",
                },
                pending_smoke_evidence=True,
            )
        _log(_t(f"  {name}: hooks installed (full hook suite)", f"  {name}: hooks installed (full hook suite)"))
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_INSTALLED_OK",
            status_label=STATUS_LABELS["installed"],
            legacy_status="installed",
            details={"installed": list(required.keys())},
        )

    if missing:
        _log(_t(
            f"  {name}: hook suite incomplete (missing: {', '.join(incomplete)})",
            f"  {name}: hook suite incomplete (missing: {', '.join(incomplete)})",
        ))
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_MISSING",
            status_label=_t("hook suite incomplete", "hook suite incomplete"),
            legacy_status="missing",
            details={"missing": missing, "drifted": drifted},
            missing_reason="suite_incomplete",
        )

    _log(_t(
        f"  {name}: hook suite drifted (mismatched: {', '.join(drifted)})",
        f"  {name}: hook suite drifted (mismatched: {', '.join(drifted)})",
    ))
    return HookStatus(
        platform=platform_key,
        status_token="HOOK_INSTALLED_DRIFT",
        status_label=_t("hook suite drifted", "hook suite drifted"),
        legacy_status="missing",
        details={"missing": [], "drifted": drifted},
    )


def check_status(platform_key: str) -> str:
    """Return hook installation status through the legacy string API.

    Return values:
      "installed" - hooks are installed
      "missing"   - hooks are absent or differ from the current expected state
      "skipped"   - framework is not installed
      "unsupported" - current runtime does not support hooks
    """
    return check_status_detail(platform_key).legacy_status


# ── Result Summary Output ─────────────────────────────────

STATUS_LABELS = {
    "installed": _t("installed", "installed"),
    "already": _t("already exists (skipped)", "already exists (skipped)"),
    "skipped": _t("not installed (skipped)", "not installed (skipped)"),
    "unsupported": _t("unsupported", "unsupported"),
    "removed": _t("removed", "removed"),
    "not_found": _t("not applicable", "not applicable"),
    "missing": _t("no hooks", "no hooks"),
    "error": _t("error", "error"),
}


def _print_summary(action: str, results: dict[str, str | HookStatus]) -> None:
    _log(_t(f"── {action} results ──", f"── {action} results ──"))
    for key, status in results.items():
        label = status.status_label if isinstance(status, HookStatus) else STATUS_LABELS.get(status, status)
        _log(f"  {PLATFORMS[key]['name']}: {label}")


def _print_runtime_visibility_status() -> None:
    config = runtime_config.load_config(home=_home())
    profile = config["agent_visibility"]["profile"]
    strict_mode = config["strict_session_log"]["mode"]
    _log(f"  Agent visibility: profile={profile}")
    _log(f"  Strict session log: mode={strict_mode}")


def _print_runtime_visibility_guidance() -> None:
    _log("  Agent visibility guidance:")
    _log("    Default profile is dynamic unless --visibility overrides it.")
    _log("    --agent-visibility remains accepted for compatibility.")
    _log(
        "    Profiles adjust user-facing governance message volume only; "
        "they do not disable hooks or gates."
    )
    _log(
        "    Use /visibility to inspect, or /visibility strict, /visibility dynamic, "
        "or /visibility minimal to change it in trusted Codex sessions."
    )
    _log(
        "    Claude Code: /visibility strict|dynamic|minimal; "
        "all platforms: python3 _shared/agent_visibility_cli.py show|set <profile>."
    )
    _log("    Hook execution, governance gates, and strict session logging remain unchanged.")


# ── Main ──────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=_t("Auto-install/remove AI coding agent UserPromptSubmit hooks", "Auto-install/remove AI coding agent UserPromptSubmit hooks")
    )
    parser.add_argument(
        "--platform",
        choices=list(PLATFORMS.keys()),
        help=_t("Target a specific framework only (default: all detected)", "Target a specific framework only (default: all detected)"),
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help=_t("Remove hooks", "Remove hooks"),
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help=_t("Check installation status only", "Check installation status only"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=_t("Preview changes without applying them", "Preview changes without applying them"),
    )
    parser.add_argument(
        "--visibility",
        "--agent-visibility",
        dest="agent_visibility",
        choices=sorted(runtime_config.VALID_AGENT_VISIBILITY_PROFILES),
        help=_t(
            "User-facing governance message visibility profile",
            "User-facing governance message visibility profile",
        ),
    )
    parser.add_argument(
        "--hook-shared-dir",
        help=_t(
            "Installed _shared directory that generated hook commands should reference",
            "Installed _shared directory that generated hook commands should reference",
        ),
    )
    parser.add_argument(
        "--addon-source",
        action="append",
        default=[],
        help=_t("Addon repo or local manifest path", "Addon repo or local manifest path"),
    )
    parser.add_argument(
        "--list-addons",
        action="store_true",
        help=_t("List addon manifest targets only", "List addon manifest targets only"),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"install_hooks.py {VERSION}",
    )
    args = parser.parse_args()

    _log(f"install_hooks.py v{VERSION}")
    _log(_t(f"Home directory: {Path.home()}", f"Home directory: {Path.home()}"))
    if args.list_addons:
        if not args.addon_source:
            parser.error("--list-addons requires at least one --addon-source")
        return _print_addon_target_summary(args.addon_source, platform=args.platform)
    if args.addon_source and args.dry_run:
        rc = _print_addon_target_summary(args.addon_source, platform=args.platform)
        if rc != 0:
            return rc
    if args.dry_run:
        _log(_t("Mode: DRY-RUN (no actual changes)", "Mode: DRY-RUN (no actual changes)"))
    if args.agent_visibility:
        runtime_config.save_config(
            {"agent_visibility": {"profile": args.agent_visibility}},
            home=_home(),
        )
        _log(f"  Agent visibility: profile={args.agent_visibility}")

    targets = [args.platform] if args.platform else list(PLATFORMS.keys())
    results: dict[str, str | HookStatus] = {}
    has_error = False

    for key in targets:
        previous_hook_shared_dir = os.environ.get(HOOK_SHARED_DIR_ENV)
        hook_shared_dir = _hook_shared_dir_for_platform(key, args.hook_shared_dir)
        if hook_shared_dir is not None:
            os.environ[HOOK_SHARED_DIR_ENV] = str(hook_shared_dir)
        else:
            os.environ.pop(HOOK_SHARED_DIR_ENV, None)
        try:
            if args.status:
                results[key] = check_status_detail(key)
            elif args.uninstall:
                results[key] = uninstall_hook(key, dry_run=args.dry_run)
            else:
                _ensure_node_runtime_for_hook_install(key)
                results[key] = install_hook(key, dry_run=args.dry_run)
        except Exception as e:
            _log(f"ERROR: {PLATFORMS[key]['name']}. {e}")
            results[key] = "error"
            has_error = True
        finally:
            if previous_hook_shared_dir is None:
                os.environ.pop(HOOK_SHARED_DIR_ENV, None)
            else:
                os.environ[HOOK_SHARED_DIR_ENV] = previous_hook_shared_dir

    action = _t("status", "status") if args.status else (_t("uninstall", "uninstall") if args.uninstall else _t("install", "install"))
    _print_summary(action, results)
    if not args.uninstall:
        _print_runtime_visibility_status()
        _print_runtime_visibility_guidance()

    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
