#!/usr/bin/env python3
"""
install_hooks.py - automatic installer/remover for AI coding agent hooks

Supported frameworks:
  - Claude Code  (~/.claude/settings.json)
  - Codex        (~/.codex/hooks.json + ~/.codex/config.toml)

Install behavior:
  1. Read each framework's hook config file, creating it when absent.
  2. Merge the managed prompt, tool, completion, session-start and audit hooks.
  3. Replace stale managed entries and retain matching entries.
  4. Preserve unrelated settings and register approved privileged adapters.
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

from reminder_texts import reminder_text

import argparse
import base64
import copy
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

MIN_PYTHON_VERSION = (3, 11)
HOOK_PYTHON_SENTINEL = "__GHOST_ALICE_HOOK_PYTHON__"
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

if sys.version_info < MIN_PYTHON_VERSION:
    detected = ".".join(str(part) for part in sys.version_info[:3])
    sys.stderr.write(f"install_hooks.py requires Python 3.11+; detected Python {detected}\n")
    raise SystemExit(1)

from merge_companion_messages import render_pending_merge_message
from hook_profile_gate import normalize_hook_id
import runtime_config
import install_destination
import addon_installer
from addon_installer import AddonManifestError, load_addon_targets

# Prevent Windows cp949 stdout encoding issues.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

VERSION = "1.7.0"
HOOK_SHARED_DIR_ENV = "GHOST_ALICE_HOOK_SHARED_DIR"
NODE_JS_DOWNLOAD_URL = "https://nodejs.org/en/download"
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
    # Report instruction-backed hook installation separately from verified native event semantics; the structured status requires live smoke evidence.
    pending_smoke_evidence: bool = False

# ── Hook Payload Definitions ──────────────────────────────

def _shell_print_message(message: str, *, payload: bool = False) -> str:
    message_payload = base64.b64encode((message + "\n").encode("utf-8")).decode("ascii")
    code = f"import sys,base64;sys.stdout.buffer.write(base64.b64decode('{message_payload}'))"
    return _hook_python_command("-c", code, payload=payload)


def _shell_print_json(payload: dict[str, Any], *, payload_mode: bool = False) -> str:
    return _shell_print_message(json.dumps(payload, ensure_ascii=False), payload=payload_mode)


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


def _quote_posix_arg(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _hook_python_invocation(*args: str | Path) -> str:
    quoted_args = [_quote_static_arg(arg) for arg in args]
    if os.name == "nt" or sys.platform.startswith("win"):
        python = sys.executable.replace("\\", "/")
        return " ".join([_quote_static_arg(python), *quoted_args])
    # Installation already requires this interpreter to be Python 3.11+.
    # Keep runtime discovery first so a removed/versioned install runtime is
    # not a permanent pin, but retain it for GUI hosts with a narrower PATH.
    launcher = POSIX_HOOK_PYTHON_LAUNCHER.replace(
        "__GHOST_ALICE_INSTALL_PYTHON__", _quote_posix_arg(sys.executable)
    )
    return " ".join([
        "/bin/sh",
        "-c",
        _quote_posix_arg(launcher),
        "ghost-alice-python",
        *quoted_args,
    ])


def _hook_python_payload_invocation(*args: str | Path) -> str:
    return " ".join([HOOK_PYTHON_SENTINEL, *[_quote_static_arg(arg) for arg in args]])


def _hook_python_command(*args: str | Path, payload: bool = False) -> str:
    if payload:
        return _hook_python_payload_invocation(*args)
    return _hook_python_invocation(*args)


def _hook_shared_dir() -> Path:
    configured = os.environ.get(HOOK_SHARED_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).parent


def _default_installed_hook_shared_dir(platform_key: str) -> Path:
    configured = os.environ.get("GHOST_ALICE_RUNTIME_SHARED_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return install_destination.ghost_root(_home()) / "runtime" / "current" / "_shared"


def _hook_shared_dir_for_platform(platform_key: str, explicit: str | None = None) -> Path | None:
    if explicit and explicit.strip():
        return Path(explicit).expanduser()
    configured = os.environ.get(HOOK_SHARED_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return _default_installed_hook_shared_dir(platform_key)


def _resolve_shared_hook_script(name: str) -> str:
    script = _hook_shared_dir() / name
    active = install_destination.current()
    source = Path(active.core_source_root)/"_shared"/name if active else script
    if script.exists() or (active and source.is_file()):
        return str(script).replace("\\", "/")
    return ""


def _resolve_pending_merge_precheck_script() -> str:
    return _resolve_shared_hook_script("pending_merge_precheck_hook.py")


def _repo_root_from_this_file() -> Path:
    return Path(__file__).resolve().parents[1]


def _session_intent_root(platform: str | None = None) -> Path:
    configured = os.environ.get("GHOST_ALICE_SESSION_INTENT_ROOT", "").strip()
    if configured:
        if not Path(configured).is_absolute():
            raise ValueError("session intent root must be absolute")
        return Path(configured)
    if "PLATFORMS" not in globals():
        return _repo_root_from_this_file()/".tmp/session-intent"
    return Path(_default_destination(platform or "codex").ledger_root)


def _default_destination(platform_key: str) -> install_destination.InstallDestination:
    source = Path(os.environ.get("GHOST_ALICE_CORE_ROOT") or _repo_root_from_this_file())
    ghost = install_destination.ghost_root(_home())
    retained = runtime_config.load_config(home=_home()).get("install_locations", {}).get(platform_key, {})
    ledger = os.environ.get("GHOST_ALICE_SESSION_INTENT_ROOT") or retained.get("ledger_root") or _retained_installed_ledger(platform_key) or str(source / ".tmp/session-intent")
    return install_destination.InstallDestination.parse(dict(mode="normal", owned_root=str(_home()),
        platform_dir=str(_resolve_codex_dir() if platform_key == "codex" else _resolve_claude_dir()),
        skills_dir=str(_home()/".agents/skills" if platform_key == "codex" else _resolve_claude_dir()/"skills"),
        ghost_root=str(ghost), ledger_root=str(ledger), project_root=str(source), core_source_root=str(source),
        runtime_shared_dir=str(os.environ.get(HOOK_SHARED_DIR_ENV) or os.environ.get("GHOST_ALICE_RUNTIME_SHARED_DIR") or ghost/"runtime/current/_shared")))


def _retained_installed_ledger(platform_key):
    """Retain a single explicit authority from the selected installed hook chain.

    Reads only the chosen platform settings, never discovery/latest-checkout state.
    Multiple managed roots require an explicit choice instead of mixed authority.
    """
    import shlex
    path=(_resolve_codex_dir()/"hooks.json" if platform_key=="codex" else _resolve_claude_dir()/"settings.json")
    try:
        settings=json.loads(path.read_text())
    except (OSError,ValueError):
        return None
    roots=set()
    for groups in settings.get("hooks",{}).values():
        for group in groups if isinstance(groups,list) else []:
            for hook in group.get("hooks",[]):
                command=hook.get("command","")
                if not any((globals().get("_hook_runner_id_match") or (lambda *_: False))(command,identity) for identity in ("session-intent","prompt","tool-checkpoint","session-start","pending-merge-prompt","session-receipt")):
                    continue
                outer=shlex.split(command,comments=True)
                if "--binding-b64" in outer:
                    binding=install_destination.decode_binding(outer[outer.index("--binding-b64")+1])
                    roots.add(binding["destination"]["ledger_root"])
                else:
                    args=outer[outer.index("run")+1:]
                    inner=shlex.split(base64.urlsafe_b64decode(args[-1]).decode(),comments=True)
                    for flag in ("--root","--session-intent-root"):
                        if flag in inner:
                            root=inner[inner.index(flag)+1]
                            if Path(root).is_absolute(): roots.add(root)
    if len(roots)>1:
        raise ValueError("installed managed hooks contain conflicting ledger roots; supply an explicit binding")
    return next(iter(roots),None)


def _destination_action(platform_key, destination, action, *, dry_run=False):
    resolved = install_destination.InstallDestination.parse(destination)
    if not (Path(resolved.core_source_root)/"session-intent-analyzer/scripts/session_intent_ledger.py").is_file():
        raise ValueError("Core source root does not provide the session ledger API")
    if not (Path(resolved.core_source_root)/"_shared/completion_assessment.py").is_file():
        raise ValueError("Core source root does not provide the response API")
    if resolved.mode == "isolated":
        if action not in {"install", "status", "uninstall"}:
            raise ValueError("isolated destination does not support this action")
        if install_destination.current() is None and (os.environ.get("GHOST_ALICE_HOOK_SHARED_DIR") or os.environ.get("GHOST_ALICE_RUNTIME_SHARED_DIR")):
            raise ValueError("isolated destination conflicts with runtime overrides")
    return resolved


def _prepare_destination_runtime(destination, *, dry_run=False):
    if dry_run:
        return
    target = Path(destination.runtime_shared_dir)
    install_destination.assert_mutation_path(target)
    if destination.mode == "isolated":
        Path(destination.platform_dir).mkdir(parents=True, exist_ok=True)
    source_root = Path(destination.core_source_root)
    ledger = Path('session-intent-analyzer/scripts/session_intent_ledger.py')
    sources = [(source_root / ledger, target.parent / ledger)]
    sources.extend((source, target / source.name) for source in (source_root / '_shared').iterdir()
                   if source.is_file() and source.suffix in {'.py', '.mjs', '.json'})
    for source, output in sources:
        install_destination.assert_mutation_path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists() or output.read_bytes() != source.read_bytes():
            shutil.copy2(source, output)



def _pending_merge_precheck_command(
    *,
    platform: str,
    hook: str,
    context: str,
    output_format: str,
    internal_instruction: str,
    payload_mode: bool = False,
) -> str:
    script = _resolve_pending_merge_precheck_script()
    if script:
        instruction_payload = base64.urlsafe_b64encode(internal_instruction.encode("utf-8")).decode("ascii")
        return " ".join([
            _hook_python_command(payload=payload_mode),
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
            instruction_payload,
            "--root",
            _quote_static_arg(_session_intent_root(platform)),
        ])

    if output_format == "json":
        return _shell_print_json({"continue": True, "systemMessage": internal_instruction}, payload_mode=payload_mode)

    return _shell_print_message(
        f"Internal instruction: {internal_instruction}\n"
        + render_pending_merge_message(context),  # type: ignore[arg-type]
        payload=payload_mode,
    )

# 1. Retired input pending-merge hook: the marker lets install and uninstall remove old entries.
# merge-companion re-checks the manifest before work changes an installed skill.
PROMPT_PENDING_MERGE_MARKER = "[merge-companion] prompt-check"
# Codex /visibility pseudo-command; Claude Code runs /visibility as a workspace command.
VISIBILITY_COMMAND_MARKER = "[agent-visibility] command"

# 1-B. Work-start hook: reminder to call task-router.
HOOK_MARKER = "[hook-reminder] AGENTS.md"
HOOK_INTERNAL = reminder_text('task-router')


def _resolve_task_router_reminder_hook_script() -> str:
    return _resolve_shared_hook_script("task_router_reminder_hook.py")


def _hook_reminder_command(*, platform: str, output_format: str, payload_mode: bool = False) -> str:
    script = _resolve_task_router_reminder_hook_script()
    if script:
        return " ".join([
            _hook_python_command(payload=payload_mode),
            _quote_static_arg(script),
            "--platform",
            platform,
            "--format",
            output_format,
            "--root",
            _quote_static_arg(_session_intent_root(platform)),
        ]) + f" # {HOOK_MARKER}"
    if output_format == "json":
        return _shell_print_json({"continue": True, "systemMessage": HOOK_INTERNAL}, payload_mode=payload_mode) + f" # {HOOK_MARKER}"
    return _shell_print_message(_localized_bridge(
        HOOK_INTERNAL,
        "After exact session-intent preflight, run task-router.",
        "Run task-router after exact session-intent preflight; native permission belongs to its host.",
        "Terminal routing uses supplied context without tools; normal routing reads the exact ledger and assigns skills.",
        "Terminal routing uses supplied context without tools; normal routing reads the exact ledger and assigns skills.",
    ), payload=payload_mode) + f" # {HOOK_MARKER}"


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
# Session start delivers the fixed session receipt; inputs then add nothing to model context.
SESSION_RECEIPT_MARKER = "[session-intent-receipt] session-start"
LEGACY_SESSION_INTENT_MARKERS = ("[" + "session-intent-" + "guard" + "]",)
SESSION_INTENT_INTERNAL = reminder_text('session-intent')


def _resolve_session_intent_analyzer_hook_script() -> str:
    return _resolve_shared_hook_script("session_intent_analyzer_hook.py")


def _session_intent_analyzer_command(*, platform: str, output_format: str, payload_mode: bool = False,
                                     context: str = "prompt_submit") -> str:
    script = _resolve_session_intent_analyzer_hook_script()
    if script:
        return " ".join([
            _hook_python_command(payload=payload_mode),
            _quote_static_arg(script),
            "--platform",
            platform,
            "--hook",
            "session-intent",
            "--context",
            context,
            "--format",
            output_format,
            "--root",
            _quote_static_arg(_session_intent_root(platform)),
        ])
    if output_format == "json":
        return _shell_print_json({"continue": True, "systemMessage": SESSION_INTENT_INTERNAL}, payload_mode=payload_mode)
    return _shell_print_message(f"Internal instruction: {SESSION_INTENT_INTERNAL}", payload=payload_mode)


SESSION_INTENT_COMMAND = (
    _session_intent_analyzer_command(platform="claude", output_format="text")
    + f" # {SESSION_INTENT_MARKER}"
)
SESSION_INTENT_ENTRY = {
    "matcher": "",
    "hooks": [{"type": "command", "command": SESSION_INTENT_COMMAND}],
}
# 2. Work-stop hook: verification-before-completion reminder.
STOP_HOOK_MARKER = "[completion-reminder] AGENTS.md"
STOP_HOOK_INTERNAL = 'completion-reminder: Core authenticates the current-session assistant review of claims, original sources and the complete response. The current-session assistant follows verification-before-completion for new completion claims and retains unchanged evidence at its original source and time. Core returns the rejected requirement and the response delivery target. Printed skill or trace fields do not establish evidence; strict audit remains active.'
STOP_HOOK_MESSAGE = _localized_bridge(
    STOP_HOOK_INTERNAL,
    "Core checks the authenticated claim, source and complete-response review.",
    "Core checks the authenticated claim, source and complete-response review.",
    "Use verification-before-completion to judge claims against original sources.",
    "Use verification-before-completion to judge claims against original sources.",
)


def _resolve_claude_stop_verification_script() -> str:
    return _resolve_shared_hook_script("claude_stop_verification_hook.py")


def _stop_hook_command(platform: str, *, payload_mode: bool = False) -> str:
    script = _resolve_claude_stop_verification_script()
    if script:
        return f'{_hook_python_command(script, "--platform", platform, payload=payload_mode)} # {STOP_HOOK_MARKER}'
    return _shell_print_message(STOP_HOOK_MESSAGE, payload=payload_mode) + f" # {STOP_HOOK_MARKER}"


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
SESSION_START_INTERNAL = "merge-companion session-check: This hook checks the current platform's pending-merge manifest. The current-session assistant applies merge-companion to undecided entries when the user asks to review them or the selected work changes an affected skill. Missing, empty, fully decided or unparsable manifests pass silently under the current contract; a clean hook result requires no second shell inspection."


def _session_start_command(*, platform: str, output_format: str, payload_mode: bool = False) -> str:
    return (
        _pending_merge_precheck_command(
            platform=platform,
            hook="session-check",
            context="session_start",
            output_format=output_format,
            internal_instruction=SESSION_START_INTERNAL,
            payload_mode=payload_mode,
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

# Managed marker retained only to remove the obsolete unconditional input reminder.
WEB_SEARCH_FIRST_MARKER = "[web-search-first]"

# 5. Pre-tool hook: tool-checkpoint at the before-tool stage.
TOOL_CHECKPOINT_MARKER = "[tool-checkpoint] pre-tool-check"
LEGACY_TOOL_CHECKPOINT_MARKERS = (f"[{'action'}-{'gate'}] pre-tool-check",)
TOOL_CHECKPOINT_INTERNAL = reminder_text("tool-checkpoint")
TOOL_CHECKPOINT_COMMAND = (
    _shell_print_message(_localized_bridge(
        TOOL_CHECKPOINT_INTERNAL,
        "stage: before-tool. This checkpoint runs before a tool call.",
        "stage: before-tool. This checkpoint runs before a tool call.",
        "Field names and literal tokens stay in English. Human notes use the current user language.",
        "Field names and literal tokens stay in English. Human notes use the current user language.",
    ))
    + f" # {TOOL_CHECKPOINT_MARKER}"
)
# ── io-trace hook payload ─────────────────────────────────
# Record file paths from every tool call into the audit log through PostToolUse events.
# stdout is not visible to the user because this is PostToolUse, but the log file keeps it.

IO_TRACE_MARKER = "[io-trace] audit"
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


def _ghost_alice_installed_skill_names() -> list[str]:
    """Return Core skill names from the managed catalog.

    Arbitrary directories under an installed skills root do not authorize
    Claude Skill permissions.
    """
    return _discover_ghost_alice_skill_names_from_catalog()


def _verified_addon_skill_names(addon_sources: Any, platform_key: str) -> list[str]:
    """Return names from manifest-validated addon targets for one platform."""
    if not addon_sources:
        return []
    targets = load_addon_targets(
        list(addon_sources),
        core_skill_names=_discover_ghost_alice_skill_names_from_catalog(),
        platform=platform_key,
    )
    return sorted({target.name for target in targets})


def _claude_ghost_alice_skill_permissions(addon_sources: Any = ()) -> list[str]:
    names = set(_ghost_alice_installed_skill_names())
    names.update(_verified_addon_skill_names(addon_sources, "claude"))
    return [f"Skill({name})" for name in sorted(names)]


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


def _hook_runner_command(
    hook_id: str,
    command: str,
    marker: str,
    *,
    platform_key: str | None = None,
) -> str:
    script = _resolve_hook_runner_script()
    if not script:
        if runtime_config.HOOK_NODE_SENTINEL in command:
            raise RuntimeError(
                "dynamic Node hook command requires hook_profile_gate.py"
            )
        return command
    payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
    normalized = normalize_hook_id(hook_id)
    runner_args = ["run", normalized]
    if platform_key is not None:
        platform = platform_key.strip().lower()
        if platform not in {"claude", "codex"}:
            raise ValueError(f"unsupported hook runner platform: {platform_key}")
        runner_args.append(platform)
    runner_args.append(payload)
    binding_platform = platform_key or os.environ.get("GHOST_ALICE_PLATFORM") or "codex"
    destination = install_destination.current() or _default_destination(binding_platform)
    binding = destination.binding()
    if normalized.startswith("adapter-autopilot-mode-"):
        inner_argv = __import__('shlex').split(command,comments=True)
        if len(inner_argv) != 2:
            raise ValueError("registered privileged adapter requires no arguments")
        adapter_script = Path(inner_argv[1])
        binding["adapter"] = dict(adapter_id="autopilot-mode", runner_id=normalized,
            script=str(adapter_script), content_hash=hashlib.sha256(adapter_script.read_bytes()).hexdigest(),
            event="Stop" if normalized.endswith("-continue") else "PreToolUse")
    runner_args.extend(["--binding-b64", install_destination.encode_binding(binding)])
    visible_hint = " ghost-alice-hook.mjs" if "ghost-alice-hook.mjs" in command else ""
    return (
        f"{_hook_python_invocation(script, *runner_args)} "
        f"# {marker} [hook-runner:{normalized}]{visible_hint}"
    )

def _hook_runner_command_entry(
    hook_id: str,
    command: str,
    marker: str,
    *,
    platform_key: str | None = None,
) -> dict[str, Any]:
    return _command_entry(
        _hook_runner_command(
            hook_id,
            command,
            marker,
            platform_key=platform_key,
        )
    )

def _resolve_io_trace_script() -> str:
    """Return the io_trace_hook.py path relative to the install directory."""
    return _resolve_shared_hook_script("io_trace_hook.py")

# ── Semantic Intent -> Platform Event Name Mapping ────────
#
# Each platform uses different event names for hooks with the same intent. Add only a mapping in this table when adding a new platform.
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
    env = os.environ.get("HOME")
    if env:
        return Path(env)
    return Path.home()


def _resolve_claude_dir() -> Path:
    active = install_destination.current()
    if active:
        return Path(active.platform_dir)
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env)
    return _home() / ".claude"


def _resolve_codex_dir() -> Path:
    active = install_destination.current()
    if active:
        return Path(active.platform_dir)
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env)
    return _home() / ".codex"


def _resolve_codex_config_toml() -> Path:
    return _resolve_codex_dir() / "config.toml"


def _resolve_ghost_alice_hooks_dir() -> Path:
    return install_destination.ghost_root(_home()) / "hooks"


def _resolve_source_hook_dispatcher() -> Path:
    active = install_destination.current()
    return Path(active.core_source_root)/"_shared/ghost-alice-hook.mjs" if active else Path(__file__).with_name("ghost-alice-hook.mjs")


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


_NODE_RUNTIME_OMITTED = object()


def _normalized_node_runtime(value: str | Path | None) -> str | None:
    text = str(value or "").strip().strip('"')
    if not text:
        return None
    candidate = Path(text).expanduser()
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if resolved.name.lower() not in {"node", "node.exe"} or not resolved.is_file():
        return None
    if not runtime_config.is_usable_node_runtime(resolved):
        return None
    return str(resolved)


def _configured_node_runtime_from_env() -> str | None:
    for env_name in ("GHOST_ALICE_NODE", "NODE_REPL_NODE_PATH"):
        configured = _normalized_node_runtime(os.environ.get(env_name))
        if configured:
            return configured
    return None


def _configured_node_runtime_from_codex_config() -> str | None:
    config_file = _resolve_codex_config_toml()
    if not config_file.exists():
        return None
    try:
        with config_file.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None

    mcp_servers = data.get("mcp_servers")
    if not isinstance(mcp_servers, Mapping):
        return None
    node_repl = mcp_servers.get("node_repl")
    if not isinstance(node_repl, Mapping):
        return None
    env = node_repl.get("env")
    if not isinstance(env, Mapping):
        return None
    value = env.get("NODE_REPL_NODE_PATH")
    if not isinstance(value, str):
        return None
    return _normalized_node_runtime(value)


def _registered_node_runtime(platform_key: str | None) -> str | None:
    if not platform_key:
        return None
    config = runtime_config.load_config(env={}, home=_home())
    node_registrations = config.get("hook_runtime", {}).get("node", {})
    if not isinstance(node_registrations, dict):
        return None
    value = node_registrations.get(platform_key)
    return _normalized_node_runtime(value if isinstance(value, str) else None)


def _persist_node_runtime(platform_key: str, node_runtime: str | Path) -> bool:
    selected = _normalized_node_runtime(node_runtime)
    if selected is None:
        raise RuntimeError(f"Invalid Node.js runtime selected for {platform_key}: {node_runtime}")
    if _registered_node_runtime(platform_key) == selected:
        return False
    runtime_config.save_config(
        {"hook_runtime": {"node": {platform_key: selected}}},
        home=_home(),
    )
    return True


def _path_node_runtime_available() -> bool:
    return bool(shutil.which("node") or shutil.which("node.exe"))


def _node_full_capability_guidance() -> str:
    return (
        f"For full capability, install Node.js: {NODE_JS_DOWNLOAD_URL} "
        "and ensure node is available on PATH."
    )


def _resolve_node_runtime(platform_key: str | None = None) -> str | None:
    configured = _configured_node_runtime_from_env()
    if configured:
        return configured
    if platform_key == "codex":
        configured = _configured_node_runtime_from_codex_config()
        if configured:
            return configured
    registered = _registered_node_runtime(platform_key)
    if registered:
        return registered
    found = shutil.which("node") or shutil.which("node.exe")
    normalized = _normalized_node_runtime(found)
    if normalized:
        return normalized
    return _configured_node_runtime_from_codex_config()


def _node_runtime_command(
    platform_key: str | None = None,
    node_runtime: str | Path | None | object = _NODE_RUNTIME_OMITTED,
) -> str:
    if (
        node_runtime is not _NODE_RUNTIME_OMITTED
        and node_runtime is not None
        and _normalized_node_runtime(node_runtime) is None
    ):
        raise RuntimeError(f"Invalid Node.js runtime selected for {platform_key}: {node_runtime}")
    return _quote_command_arg(runtime_config.HOOK_NODE_SENTINEL)


def _dispatcher_hook_command(
    platform: str,
    event: str,
    hook_name: str,
    marker: str,
    hook_id: str,
    node_runtime: str | Path | None | object = _NODE_RUNTIME_OMITTED,
) -> str:
    dispatcher = _resolve_installed_hook_dispatcher()
    parts = [
        _node_runtime_command(platform, node_runtime),
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
        command = _hook_reminder_command(platform="codex", output_format="json", payload_mode=True)
    else:
        command = _hook_reminder_command(platform="claude", output_format="json", payload_mode=True)
    return _hook_runner_command_entry("prompt", command, HOOK_MARKER, platform_key=platform_key)


def _platform_visibility_command_entry(platform_key: str, event: str) -> dict[str, Any]:
    script = _resolve_shared_hook_script("agent_visibility_hook.py")
    command = " ".join([_hook_python_command(payload=True), _quote_static_arg(script)]) + f" # {VISIBILITY_COMMAND_MARKER}"
    return _hook_runner_command_entry("visibility-command", command, VISIBILITY_COMMAND_MARKER, platform_key=platform_key)


def _platform_session_intent_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key == "codex":
        command = _session_intent_analyzer_command(platform="codex", output_format="json", payload_mode=True) + f" # {SESSION_INTENT_MARKER}"
    else:
        command = _session_intent_analyzer_command(platform="claude", output_format="json", payload_mode=True) + f" # {SESSION_INTENT_MARKER}"
    return _hook_runner_command_entry("session-intent", command, SESSION_INTENT_MARKER, platform_key=platform_key)


def _platform_tool_checkpoint_entry(
    platform_key: str,
    event: str,
    node_runtime: str | Path | None | object = _NODE_RUNTIME_OMITTED,
) -> dict[str, Any]:
    if platform_key in {"claude", "codex"}:
        command = _dispatcher_hook_command(
            platform_key,
            event,
            "tool-checkpoint",
            TOOL_CHECKPOINT_MARKER,
            "tool-checkpoint",
            node_runtime,
        )
        return _hook_runner_command_entry(
            "tool-checkpoint",
            command,
            TOOL_CHECKPOINT_MARKER,
            platform_key=platform_key,
        )
    return _hook_runner_command_entry("tool-checkpoint", TOOL_CHECKPOINT_COMMAND, TOOL_CHECKPOINT_MARKER)


def _platform_stop_hook_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key == "codex":
        return _hook_runner_command_entry(
            "completion", _stop_hook_command("codex", payload_mode=True), STOP_HOOK_MARKER, platform_key=platform_key,
        )
    return _hook_runner_command_entry(
        "completion", _stop_hook_command("claude", payload_mode=True), STOP_HOOK_MARKER, platform_key=platform_key,
    )


def _platform_session_receipt_entry(platform_key: str, event: str) -> dict[str, Any]:
    platform = "codex" if platform_key == "codex" else "claude"
    command = _session_intent_analyzer_command(
        platform=platform, output_format="json", payload_mode=True, context="session_start",
    ) + f" # {SESSION_RECEIPT_MARKER}"
    return _hook_runner_command_entry("session-receipt", command, SESSION_RECEIPT_MARKER, platform_key=platform_key)


def _platform_session_start_entry(platform_key: str, event: str) -> dict[str, Any]:
    if platform_key == "codex":
        command = _session_start_command(platform="codex", output_format="json", payload_mode=True)
    else:
        command = _session_start_command(platform="claude", output_format="json", payload_mode=True)
    return _hook_runner_command_entry("session-start", command, SESSION_START_MARKER, platform_key=platform_key)


def _resolve_addon_hooks(addon_sources: Any, platform_key: str) -> list[tuple[str, str, str, str]]:
    """Resolve observational addon hooks for one platform.

    Returns [(addon_id, hook_id, event_intent, script_abs_path)]. Re-uses
    addon_installer.load_addon_targets, which fail-closes on a forbidden hook
    event or an escaping script, so a bad addon manifest aborts the hook install.
    """
    if not addon_sources:
        return []
    import addon_installer
    targets = addon_installer.load_addon_targets(list(addon_sources), platform=platform_key)
    out: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for target in targets:
        if target.addon_id in seen:
            continue
        seen.add(target.addon_id)
        for hook_id, event_intent, script_path in target.hooks:
            out.append((target.addon_id, hook_id, event_intent, script_path))
    return out


def _resolve_privileged_adapter_hooks(
    addon_sources: Any,
    platform_key: str,
    *,
    skills_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Resolve core-owned privileged adapter hooks for one platform."""
    if not addon_sources:
        return []
    targets = addon_installer.load_addon_targets(list(addon_sources), platform=platform_key)
    return addon_installer.iter_privileged_adapter_hook_specs(targets, skills_dir=skills_dir)


def _platform_io_trace_entry(platform_key: str, event: str) -> Optional[dict[str, Any]]:
    io_command = _io_trace_hook_command(payload_mode=True)
    if not io_command:
        return None
    return _command_entry(_hook_runner_command("io-trace", io_command, IO_TRACE_MARKER, platform_key=platform_key))


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
        install_destination.assert_mutation_path(dst)
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


def _ensure_node_runtime_for_hook_install(platform_key: str) -> str | None:
    if platform_key not in {"claude", "codex"}:
        return None
    if platform_key == "codex" and not _codex_hooks_supported():
        return None
    platform = PLATFORMS[platform_key]
    if not platform["detect"]():
        return None
    node_runtime = _resolve_node_runtime(platform_key)
    if node_runtime:
        return node_runtime
    name = platform["name"]
    raise RuntimeError(
        f"Node.js runtime is required for {name} hook enforcement because "
        f"tool-checkpoint runs ghost-alice-hook.mjs. {_node_full_capability_guidance()} "
        "Then rerun the installer."
    )

# ── Core Logic ─────────────────────────────────────────────

def _log(msg: str) -> None:
    print(f"  [install_hooks] {msg}")


def _t(ko: str, en: str) -> str:
    """Return the English message; callers may also supply an unused localized value."""
    return en


def _read_settings(path: Path, *, backup_corrupt: bool = True) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _log(_t(f"WARNING: Failed to parse JSON at {path} ({e})", f"WARNING: Failed to parse JSON at {path} ({e})"))
        if backup_corrupt:
            _log(_t("  File may be corrupted. Backing up and starting with empty settings.", "  File may be corrupted. Backing up and starting with empty settings."))
            backup = path.with_suffix(".json.corrupt-bak")
            install_destination.assert_mutation_path(backup)
            shutil.copy2(path, backup)
            _log(_t(f"  Corrupted file backed up: {backup}", f"  Corrupted file backed up: {backup}"))
        else:
            _log(_t(
                "  File may be corrupted. Continuing with empty settings without modifying it.",
                "  File may be corrupted. Continuing with empty settings without modifying it.",
            ))
        return {}
    except OSError as e:
        _log(_t(f"WARNING: Failed to read {path} ({e})", f"WARNING: Failed to read {path} ({e})"))
        return {}


def _report_saved(path: Path) -> None:
    try:
        _log(_t(f"Saved: {path}", f"Saved: {path}"))
    except Exception:
        pass


def _write_settings(
    path: Path,
    data: dict,
    dry_run: bool = False,
    report_saved: bool = True,
) -> None:
    if dry_run:
        _log(_t(f"DRY-RUN: Would write to {path}:", f"DRY-RUN: Would write to {path}:"))
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    install_destination.assert_mutation_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(".json.bak")
        install_destination.assert_mutation_path(backup)
        shutil.copy2(path, backup)
        _log(_t(f"Backup created: {backup}", f"Backup created: {backup}"))
        _rotate_backups(path, keep=3)
    runtime_config._atomic_write_json(path, data)
    if report_saved:
        _report_saved(path)


def _restore_settings_snapshot(
    path: Path,
    existed: bool,
    contents: bytes | None,
) -> None:
    if existed:
        if contents is None:
            raise RuntimeError(f"Missing rollback snapshot for existing settings file: {path}")
        runtime_config._atomic_write_bytes(path, contents)
        return
    path = Path(path)
    if path.is_symlink():
        runtime_config._resolve_symlink_target(path).unlink(missing_ok=True)
        return
    install_destination.assert_mutation_path(path)
    path.unlink(missing_ok=True)


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
            pending_root = (install_destination.ghost_root(_home()) / "pending-merges").resolve()
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
            install_destination.assert_mutation_path(old)
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

    install_destination.assert_mutation_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(f"{path.suffix}.bak")
        install_destination.assert_mutation_path(backup)
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
    return install_destination.ghost_root(_home()) / "install-state" / "codex-hook-feature-change.json"


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
    install_destination.assert_mutation_path(path)
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


def _codex_trusted_hook_state_entries(
    settings_file: Path,
    settings: dict[str, Any],
    *,
    verified_addon_commands: set[tuple[str, str]] | None = None,
) -> dict[str, str]:
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
                if not isinstance(command, str):
                    continue
                if not _is_ghost_alice_hook_command(command) and (
                    event_name, command
                ) not in (verified_addon_commands or set()):
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


def _codex_hook_state_key_parts(key: str) -> Optional[tuple[str, str, str, str]]:
    parts = key.rsplit(":", 3)
    if len(parts) != 4:
        return None
    settings_path, event_name, group_index, handler_index = parts
    if event_name not in {
        "post_tool_use",
        "pre_tool_use",
        "session_start",
        "stop",
        "user_prompt_submit",
    }:
        return None
    if not group_index.isdigit() or not handler_index.isdigit():
        return None
    return settings_path.replace("\\", "/"), event_name, group_index, handler_index


def _codex_hook_state_header_key(line: str) -> Optional[str]:
    prefix = "[hooks.state."
    stripped = line.strip()
    if not stripped.startswith(prefix) or not stripped.endswith("]"):
        return None
    encoded_key = stripped[len(prefix):-1].strip()
    try:
        decoded = json.loads(encoded_key)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, str) else None


def _prune_codex_hook_state_path_variants(lines: list[str], keep_key: str) -> bool:
    keep_parts = _codex_hook_state_key_parts(keep_key)
    if keep_parts is None:
        return False

    changed = False
    index = 0
    while index < len(lines):
        existing_key = _codex_hook_state_header_key(lines[index])
        if existing_key is None or existing_key == keep_key:
            index += 1
            continue
        if _codex_hook_state_key_parts(existing_key) != keep_parts:
            index += 1
            continue

        end = index + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                break
            end += 1
        del lines[index:end]
        changed = True

    return changed


def _codex_project_trust_change_path() -> Path:
    return install_destination.ghost_root(_home()) / "install-state" / "codex-project-trust-change.json"


def _codex_project_trust_header(project_path: str) -> str:
    return f"[projects.{_toml_basic_string(project_path)}]"


def _toml_trust_level_value(raw_value: str) -> str:
    value = raw_value.split("#", 1)[0].strip()
    if value in {'"trusted"', "'trusted'"}:
        return "trusted"
    if value in {'"untrusted"', "'untrusted'"}:
        return "untrusted"
    return "other"


def _codex_project_trust_state_in_content(content: str, project_path: str) -> str:
    lines = content.splitlines()
    start, end = _find_toml_header_bounds(lines, _codex_project_trust_header(project_path))
    if start is None:
        return "missing"
    for raw_line in lines[start + 1:end]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "trust_level":
            return _toml_trust_level_value(value)
    return "missing"


def _merge_codex_project_trust(content: str, project_path: str) -> tuple[str, bool, str]:
    lines = content.splitlines()
    header = _codex_project_trust_header(project_path)
    start, end = _find_toml_header_bounds(lines, header)
    before_state = _codex_project_trust_state_in_content(content, project_path)

    if before_state == "trusted":
        return _render_text_with_trailing_newline(lines), False, before_state

    trusted_line = 'trust_level = "trusted"'
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([header, trusted_line])
        return _render_text_with_trailing_newline(lines), True, before_state

    trust_idx: Optional[int] = None
    for idx in range(start + 1, end):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _value = stripped.split("=", 1)
        if key.strip() == "trust_level":
            trust_idx = idx
            break

    if trust_idx is None:
        lines.insert(end, trusted_line)
    else:
        lines[trust_idx] = trusted_line
    return _render_text_with_trailing_newline(lines), True, before_state


def _write_codex_project_trust_change(
    config_file: Path,
    *,
    project_path: str,
    before_state: str,
    after_state: str,
    dry_run: bool = False,
) -> None:
    change = {
        "schema_version": "codex-project-trust-change.v1",
        "kind": "codex_project_trust",
        "path": config_file.as_posix(),
        "project_path": project_path,
        "before_state": before_state,
        "after_state": after_state,
        "applied_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    path = _codex_project_trust_change_path()
    if dry_run:
        _log(_t(f"  DRY-RUN: Would record Codex project trust change: {path}", f"  DRY-RUN: Would record Codex project trust change: {path}"))
        return
    install_destination.assert_mutation_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(change, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _log(_t("  Recorded Codex project trust rollback metadata", "  Recorded Codex project trust rollback metadata"))


def _ensure_codex_project_trusted(dry_run: bool = False) -> bool:
    config_file = _resolve_codex_config_toml()
    project_path = str(Path(install_destination.current().project_root).resolve() if install_destination.current() else _repo_root_from_this_file().resolve())
    current_content = _read_text_file(config_file)
    updated_content, changed, before_state = _merge_codex_project_trust(current_content, project_path)

    if not changed:
        _log(_t("  Codex project trust already current", "  Codex project trust already current"))
        return False

    _write_text_file(config_file, updated_content, dry_run=dry_run)
    if before_state != "trusted":
        _write_codex_project_trust_change(
            config_file,
            project_path=project_path,
            before_state=before_state,
            after_state="trusted",
            dry_run=dry_run,
        )
    _log(_t("  Trusted Ghost-ALICE project config layer in Codex config.toml", "  Trusted Ghost-ALICE project config layer in Codex config.toml"))
    return True


def _merge_codex_hook_trust_state(content: str, state_entries: dict[str, str]) -> tuple[str, bool]:
    lines = content.splitlines()
    changed = False

    for key, trusted_hash in state_entries.items():
        changed = _prune_codex_hook_state_path_variants(lines, key) or changed
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
    *,
    verified_addon_commands: set[tuple[str, str]] | None = None,
) -> bool:
    state_entries = _codex_trusted_hook_state_entries(
        settings_file, settings, verified_addon_commands=verified_addon_commands,
    )
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


def _io_trace_hook_command(*, payload_mode: bool = False) -> str:
    io_script = _resolve_io_trace_script()
    if not io_script:
        return ""
    return f'{_hook_python_command(io_script, payload=payload_mode)} # {IO_TRACE_MARKER}'


def _hook_runner_id_match(command: str, hook_id: str | None) -> bool:
    """Recognize the actual managed outer argv, including serialized binding.

    Marker text and a lookalike filename do not establish runner ownership.
    Commands without an outer binding are removable without granting v2 capability.
    """
    if not hook_id:
        return False
    import shlex
    try:
        argv = shlex.split(command,comments=True)
        if argv[:2] == ["/bin/sh","-c"]:
            if len(argv)<7 or argv[3]!="ghost-alice-python":
                return False
            prefix,suffix=POSIX_HOOK_PYTHON_LAUNCHER.split("__GHOST_ALICE_INSTALL_PYTHON__")
            if not argv[2].startswith(prefix) or not argv[2].endswith(suffix):
                return False
            middle=shlex.split(argv[2][len(prefix):-len(suffix)])
            if len(middle)!=1 or not Path(middle[0]).is_absolute():
                return False
            script_index=4
        else:
            if len(argv)<4 or not re.fullmatch(r"python(?:3(?:\.[0-9]+)?)?(?:\.exe)?",Path(argv[0]).name):
                return False
            script_index=1
        if not Path(argv[script_index]).is_absolute() or Path(argv[script_index]).name!="hook_profile_gate.py":
            return False
        args=argv[script_index+1:]
        if len(args) in {5,6} and args[-2]=="--binding-b64":
            install_destination.decode_binding(args[-1]);args=args[:-2]
        if len(args) not in {3,4} or args[:2] != ["run",normalize_hook_id(hook_id)]:
            return False
        base64.urlsafe_b64decode(args[-1]).decode('utf-8')
        return True
    except (ValueError,UnicodeError,IndexError):
        return False


def _managed_instruction_contract(command: str, identity: str):
    """Resolve the executor and explicit instruction ownership from its bound arguments."""
    import shlex
    if not _hook_runner_id_match(command, identity):
        return None
    try:
        outer = shlex.split(command, comments=True)
        script_index = 4 if outer[:2] == ["/bin/sh", "-c"] else 1
        args = outer[script_index + 1:]
        if len(args) != 6 or args[:2] != ["run", identity] or args[2] not in {"codex", "claude"} or args[4] != "--binding-b64":
            return None
        binding = install_destination.decode_binding(args[5])
        if set(binding) != {"schema_version", "destination"}:
            return None
        destination = install_destination.InstallDestination.parse(binding["destination"])
        if Path(outer[script_index]) != Path(destination.runtime_shared_dir) / "hook_profile_gate.py":
            return None
        script_name = {"prompt": "task_router_reminder_hook.py", "session-intent": "session_intent_analyzer_hook.py"}[identity]
        inner = base64.urlsafe_b64decode(args[3]).decode("utf-8")
        argv = shlex.split(inner, comments=True)
        if (len(argv) < 4 or argv[0] != HOOK_PYTHON_SENTINEL
                or Path(argv[1]) != Path(destination.runtime_shared_dir) / script_name
                or len(argv[2:]) % 2):
            return None
        options = dict(zip(argv[2::2], argv[3::2]))
        required = {"--platform", "--format", "--root"}
        if identity == "session-intent":
            required |= {"--hook", "--context"}
        if (set(options) - {"--internal-b64"} != required or len(options) != len(argv[2:]) // 2
                or options["--platform"] != args[2]
                or options["--format"] not in {"json", "text"}
                or options["--root"] != destination.ledger_root
                or (identity == "session-intent" and (options["--hook"], options["--context"]) != ("session-intent", "prompt_submit"))):
            return None
        return options
    except (ValueError, UnicodeError, IndexError, KeyError, TypeError):
        return None


def _managed_prompt_executor(command: str, identity: str):
    contract = _managed_instruction_contract(command, identity)
    return contract is not None and "--internal-b64" not in contract


def resolve_instruction_command(rows, identity, default_command):
    """An explicit caller policy selects one executor; ambiguity prevents replacement."""
    overrides = []
    for row in rows:
        for hook in row.get("hooks", []):
            command = hook.get("command", "")
            contract = _managed_instruction_contract(command, identity)
            if hook.get("type") == "command" and contract is not None and "--internal-b64" in contract:
                overrides.append(command)
    if len(overrides) > 1:
        raise ValueError("Instruction ownership is ambiguous")
    return overrides[0] if overrides else default_command


def remove_managed_prompt_reminders(hooks_list: list) -> int:
    """Retire the redundant default executor; explicit custom commands remain intact."""
    return _remove_hook_commands_by_command(hooks_list,
        lambda command: _managed_prompt_executor(command, "prompt") is True)


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
        stale_commands = [
            hook
            for hook in entry.get("hooks", [])
            if (any(candidate in hook.get("command", "") for candidate in markers)
                or _hook_runner_id_match(hook.get("command", ""), hook_id))
            and (hook.get("command", "") != expected_command
                 or (require_dispatcher and not _is_dispatcher_command(hook.get("command", ""))))
        ]
        if stale_commands:
            entry["hooks"] = [hook for hook in entry.get("hooks", []) if hook not in stale_commands]
            removed += 1
            if not entry["hooks"]:
                hooks_list.pop(i)
                continue
        i += 1
    return removed


def _remove_hook_entries(hooks_list: list, marker: str, *, hook_id: str | None = None) -> int:
    """Remove managed commands, preserving custom siblings; count affected entries."""
    removed = 0
    i = 0
    while i < len(hooks_list):
        entry = hooks_list[i]
        retained = [hook for hook in entry.get("hooks", [])
                    if marker not in hook.get("command", "")
                    and not _hook_runner_id_match(hook.get("command", ""), hook_id)]
        if len(retained) != len(entry.get("hooks", [])):
            entry["hooks"] = retained
            removed += 1
            if not retained:
                hooks_list.pop(i)
                continue
        i += 1
    return removed


def _entry_contains_marker(entry: dict[str, Any], marker: str) -> bool:
    return any(marker in hook.get("command", "") for hook in entry.get("hooks", []))


def _reorder_user_prompt_governance_hooks(hooks_list: list) -> bool:
    """Keep managed UserPromptSubmit hook entries in install-surface order.

    This is not the full semantic graph. session-intent-analyzer fans out to
    task-router and skill-evolution (report-only). Intake owns routing guidance.
    """
    ordered_markers = [
        VISIBILITY_COMMAND_MARKER,
        SESSION_INTENT_MARKER,
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
    present_markers = [marker for marker in ordered_markers if marker in managed]

    rebuilt: list[dict[str, Any]] = []
    inserted = False
    for entry in hooks_list:
        marker = next((item for item in ordered_markers if _entry_contains_marker(entry, item)), "")
        if not marker:
            rebuilt.append(entry)
            continue
        if not inserted:
            rebuilt.extend(managed[item] for item in present_markers)
            inserted = True

    if rebuilt == hooks_list:
        return False
    hooks_list[:] = rebuilt
    return True


GHOST_ALICE_HOOK_MARKERS = (
    PROMPT_PENDING_MERGE_MARKER,
    VISIBILITY_COMMAND_MARKER,
    HOOK_MARKER,
    SESSION_INTENT_MARKER,
    WEB_SEARCH_FIRST_MARKER,
    TOOL_CHECKPOINT_MARKER,
    STOP_HOOK_MARKER,
    SESSION_START_MARKER,
    SESSION_RECEIPT_MARKER,
    IO_TRACE_MARKER,
)


def _ensure_claude_ghost_alice_skill_permissions(
    settings: dict[str, Any],
    addon_sources: Any = (),
) -> bool:
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
    for rule in _claude_ghost_alice_skill_permissions(addon_sources):
        if rule in existing:
            continue
        allow.append(rule)
        existing.add(rule)
        changed = True
    return changed


# ── Install ───────────────────────────────────────────────

def install_hook(
    platform_key: str,
    dry_run: bool = False,
    addon_sources: Any = (),
    skills_dir: str | Path | None = None,
    node_runtime: str | Path | None = None,
    *, destination: install_destination.InstallDestination | dict | None = None,
) -> str:
    """Install hooks into a single framework.

    Return values:
      "installed" - newly installed
      "already"   - skipped because the same hook already exists
      "skipped"   - skipped because the framework is not installed
      "unsupported" - skipped because the current runtime does not support it
      "error"     - install failed
    """
    if destination is not None or install_destination.current() is None:
        destination = _default_destination(platform_key) if destination is None else destination
        resolved = _destination_action(platform_key,destination,"install",dry_run=dry_run)
        if resolved.mode == "isolated" and skills_dir is not None and Path(skills_dir) != Path(resolved.skills_dir):
            raise ValueError("isolated destination conflicts with skills override")
        if resolved.mode == "normal" and skills_dir is not None:
            resolved = __import__("dataclasses").replace(resolved,skills_dir=str(Path(skills_dir).expanduser().resolve()))
        with install_destination.use(resolved):
            if PLATFORMS[platform_key]["detect"]() or resolved.mode == "isolated":
                _prepare_destination_runtime(resolved,dry_run=dry_run)
            result = install_hook(platform_key,dry_run,addon_sources,resolved.skills_dir,node_runtime)
            if not dry_run and result in {"installed","already"}:
                runtime_config.save_config({"install_locations":{platform_key: __import__('dataclasses').asdict(resolved)}},home=_home())
            return result
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

    if node_runtime is None:
        selected_node_runtime = _ensure_node_runtime_for_hook_install(platform_key)
    else:
        selected_node_runtime = _normalized_node_runtime(node_runtime)
        if selected_node_runtime is None:
            raise RuntimeError(f"Invalid Node.js runtime selected for {name}: {node_runtime}")

    settings_existed = settings_file.exists()
    settings_snapshot = (
        settings_file.read_bytes()
        if settings_existed and not dry_run and selected_node_runtime is not None
        else None
    )
    runtime_config_file = runtime_config.config_path(_home())
    runtime_config_existed = runtime_config_file.exists()
    runtime_config_snapshot = (
        runtime_config_file.read_bytes()
        if runtime_config_existed and not dry_run and selected_node_runtime is not None
        else None
    )

    _log(_t(f"  Hook config file: {settings_file}", f"  Hook config file: {settings_file}"))
    settings = _read_settings(settings_file, backup_corrupt=not dry_run)

    permission_before = list(settings.get("permissions", {}).get("allow", [])) if isinstance(settings.get("permissions"), dict) and isinstance(settings.get("permissions", {}).get("allow", []), list) else []

    changed = False

    if platform_key == "claude":
        if _ensure_claude_ghost_alice_skill_permissions(settings, addon_sources):
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

    # The input pending-merge hook is retired; merge-companion re-checks before skill changes.
    removed = _remove_hook_entries(hook_list, PROMPT_PENDING_MERGE_MARKER, hook_id="pending-merge-prompt")
    if removed:
        _log(f"  Removed {removed} retired input pending-merge hook entry(ies)")
        changed = True

    if platform_key == "codex":
        # Codex has no workspace command, so this input hook serves /visibility.
        visibility_entry = _platform_visibility_command_entry(platform_key, hook_key)
        visibility_command = _entry_command(visibility_entry)
        removed = _remove_stale_hook_entries(
            hook_list,
            VISIBILITY_COMMAND_MARKER,
            visibility_command,
            require_dispatcher=require_dispatcher,
            hook_id="visibility-command",
        )
        if removed:
            _log(f"  Removed {removed} stale visibility-command hook entry(ies)")
            changed = True
        if _hook_already_exists(
            hook_list,
            VISIBILITY_COMMAND_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=visibility_command,
        ):
            _log("  visibility-command hook already exists. Skipping")
        else:
            hook_list.append(visibility_entry)
            _log("  visibility-command hook added")
            changed = True
    else:
        removed = _remove_hook_entries(hook_list, VISIBILITY_COMMAND_MARKER, hook_id="visibility-command")
        if removed:
            _log(f"  Removed {removed} visibility-command hook entry(ies); Claude Code uses its workspace command")
            changed = True

    removed = remove_managed_prompt_reminders(hook_list)
    if removed:
        _log(f"  Removed {removed} redundant managed task-router reminder(s); intake owns routing guidance")
        changed = True

    # Install the session-intent-analyzer hook (updates the session intent ledger for every user input).
    session_intent_entry = _platform_session_intent_entry(platform_key, hook_key)
    session_intent_command = resolve_instruction_command(hook_list, "session-intent", _entry_command(session_intent_entry))
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

    # External evidence selection belongs to the verification reference, not input intake.
    removed = _remove_hook_entries(hook_list, WEB_SEARCH_FIRST_MARKER, hook_id="web-search-first")
    if removed:
        _log(_t(
            f"  Removed {removed} web-search-first hook entry(ies)",
            f"  Removed {removed} web-search-first hook entry(ies)",
        ))
        changed = True

    if _reorder_user_prompt_governance_hooks(hook_list):
        _log(_t(
            "  Reordered UserPromptSubmit hook surface: pending-merge -> session-intent (routing guidance; skill-evolution report-only branch)",
            "  Reordered UserPromptSubmit hook surface: pending-merge -> session-intent (routing guidance; skill-evolution report-only branch)",
        ))
        changed = True

    # Install the pre_tool_use hook (tool-checkpoint: enforce narration before tool calls).
    tool_checkpoint_key = _resolve_hook_event("pre_tool_use", platform_key)
    tool_checkpoint_entry = _platform_tool_checkpoint_entry(
        platform_key,
        tool_checkpoint_key,
        selected_node_runtime,
    )
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

        # Install the session receipt hook: the host repeats session start after resume, clear and compaction.
        receipt_entry = _platform_session_receipt_entry(platform_key, ss_key)
        receipt_command = _entry_command(receipt_entry)
        removed = _remove_stale_hook_entries(
            ss_list,
            SESSION_RECEIPT_MARKER,
            receipt_command,
            require_dispatcher=require_dispatcher,
            hook_id="session-receipt",
        )
        if removed:
            _log(f"  Removed {removed} stale {ss_key} session-receipt hook entry(ies)")
            changed = True
        if _hook_already_exists(
            ss_list,
            SESSION_RECEIPT_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=receipt_command,
        ):
            _log(f"  {ss_key} session-receipt hook already exists. Skipping")
        else:
            ss_list.append(receipt_entry)
            _log(f"  {ss_key} session-receipt hook added")
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

    # Trust only exact event/command pairs produced by verified addon sources in
    # this install; marker text in pre-existing user hooks is not authority.
    verified_addon_commands: set[tuple[str, str]] = set()
    # Wire observational addon hooks after the Core suite. Empty addon_sources leaves the hook configuration unchanged.
    for addon_id, hook_id, event_intent, script_path in _resolve_addon_hooks(addon_sources, platform_key):
        event_name = _resolve_hook_event(event_intent, platform_key)
        marker = f"[addon:{addon_id}] {hook_id}"
        # The generated command embeds "{marker} [hook-runner:...]", so the marker is always followed by a space. Matching on "marker " makes removal exact and prevents a hook id that is a prefix of another (obs vs obs2) from colliding.
        match_marker = marker + " "
        inner = _hook_python_command(script_path, payload=True)
        entry = _hook_runner_command_entry(f"addon:{addon_id}:{hook_id}", inner, marker, platform_key=platform_key)
        command = _entry_command(entry)
        verified_addon_commands.add((event_name, command))
        if event_name not in hooks_obj:
            hooks_obj[event_name] = []
        ev_list = hooks_obj[event_name]
        # Prune only a STALE prior version of THIS managed addon hook -- proven by the exact marker + [hook-runner:] token + matching runner argv, the same ownership predicate remove_addon_hook uses. A user hook that merely contains the marker substring (no runner proof) is never deleted here.
        if _remove_hook_commands_by_command(
            ev_list,
            lambda cmd, m=marker, exp=command: cmd != exp and _is_exact_addon_hook_command(cmd, m),
        ):
            changed = True
        if not _hook_already_exists(ev_list, match_marker, expected_command=command):
            ev_list.append(entry)
            _log(_t(f"  {event_name} addon hook added: {marker}", f"  {event_name} addon hook added: {marker}"))
            changed = True
        else:
            _log(_t(f"  {event_name} addon hook already exists: {marker}", f"  {event_name} addon hook already exists: {marker}"))

    # Privileged adapters are core-owned: manifests request adapter ids only, while the concrete event/script/marker/runner namespace comes from addon_installer.CORE_PRIVILEGED_ADAPTER_ALLOWLIST.
    privileged_adapter_specs = _resolve_privileged_adapter_hooks(addon_sources, platform_key, skills_dir=skills_dir)
    if platform_key == "claude" and any(spec.get("adapter_id") == "autopilot-mode" for spec in privileged_adapter_specs):
        removed = _remove_legacy_autopilot_hook_entries(hooks_obj)
        if removed:
            _log(_t(
                f"  Removed {removed} legacy Claude autopilot hook entry(ies)",
                f"  Removed {removed} legacy Claude autopilot hook entry(ies)",
            ))
            changed = True
    for spec in privileged_adapter_specs:
        event_name = _resolve_hook_event(spec["event"], platform_key)
        marker = spec["marker"]
        match_marker = marker + " "
        inner = _hook_python_command(spec["script"], payload=True)
        entry = _hook_runner_command_entry(spec["runner_id"], inner, marker, platform_key=platform_key)
        command = _entry_command(entry)
        verified_addon_commands.add((event_name, command))
        if event_name not in hooks_obj:
            hooks_obj[event_name] = []
        ev_list = hooks_obj[event_name]
        if _remove_hook_commands_by_command(
            ev_list,
            lambda cmd, m=marker, exp=command: cmd != exp and _is_exact_adapter_hook_command(cmd, m),
        ):
            changed = True
        if not _hook_already_exists(ev_list, match_marker, expected_command=command):
            ev_list.append(entry)
            _log(_t(f"  {event_name} adapter hook added: {marker}", f"  {event_name} adapter hook added: {marker}"))
            changed = True
        else:
            _log(_t(f"  {event_name} adapter hook already exists: {marker}", f"  {event_name} adapter hook already exists: {marker}"))

    if platform_key == "codex":
        if _ensure_codex_hook_feature_enabled(dry_run=dry_run):
            changed = True
        if _ensure_codex_project_trusted(dry_run=dry_run):
            changed = True
        if _ensure_codex_hook_trust_state(
            settings_file, settings, dry_run=dry_run,
            verified_addon_commands=verified_addon_commands,
        ):
            changed = True

    if not changed:
        if not dry_run and selected_node_runtime is not None:
            _persist_node_runtime(platform_key, selected_node_runtime)
            if platform_key == "claude" and addon_sources:
                import addon_skill_permissions
                addon_skill_permissions.record_install(
                    settings_file,
                    load_addon_targets(list(addon_sources), core_skill_names=_ghost_alice_installed_skill_names(), platform="claude"),
                    permission_before, settings.get("permissions", {}).get("allow", []),
                )
        return "already"

    _write_settings(
        settings_file,
        settings,
        dry_run=dry_run,
        report_saved=False,
    )
    if not dry_run and selected_node_runtime is not None:
        try:
            _persist_node_runtime(platform_key, selected_node_runtime)
            if platform_key == "claude" and addon_sources:
                import addon_skill_permissions
                addon_skill_permissions.record_install(
                    settings_file,
                    load_addon_targets(list(addon_sources), core_skill_names=_ghost_alice_installed_skill_names(), platform="claude"),
                    permission_before, settings.get("permissions", {}).get("allow", []),
                )
        except Exception as persistence_error:
            rollback_errors: list[Exception] = []
            try:
                _restore_settings_snapshot(
                    settings_file,
                    settings_existed,
                    settings_snapshot,
                )
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
            try:
                _restore_settings_snapshot(
                    runtime_config_file,
                    runtime_config_existed,
                    runtime_config_snapshot,
                )
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
            if rollback_errors:
                raise ExceptionGroup(
                    "Node runtime persistence failed and transaction rollback failed",
                    [persistence_error, *rollback_errors],
                )
            raise
    if not dry_run:
        _report_saved(settings_file)
    _log(_t("  Hook installation complete", "  Hook installation complete"))
    return "installed"


# ── Uninstall ─────────────────────────────────────────────

# A well-formed addon hook marker -- the ONLY shape remove_addon_hook will act on. This makes the function structurally incapable of stripping a core/peer hook even if handed a forged/tampered marker.
_ADDON_HOOK_MARKER_RE = re.compile(
    r"^\[addon:(?P<addon_id>[a-z][a-z0-9-]*)\] (?P<hook_id>[a-z][a-z0-9-]*)$"
)
_MANAGED_ADDON_HOOK_COMMENT_RE = re.compile(
    r"\[addon:(?P<addon_id>[a-z][a-z0-9-]*)\] "
    r"(?P<hook_id>[a-z][a-z0-9-]*) "
    r"\[hook-runner:(?P=hook_id)\]"
)
_ADAPTER_HOOK_MARKER_RE = re.compile(
    r"^\[adapter:(?P<adapter_id>[a-z][a-z0-9-]*)\] (?P<hook_id>[a-z][a-z0-9-]*)$"
)
_MANAGED_ADAPTER_HOOK_COMMENT_RE = re.compile(
    r"\[adapter:(?P<adapter_id>[a-z][a-z0-9-]*)\] "
    r"(?P<hook_id>[a-z][a-z0-9-]*) "
    r"\[hook-runner:(?P<runner_id>[a-z0-9-]+)\]"
)
_LEGACY_AUTOPILOT_HOOKS = (
    ("[autopilot] reset-count", "reset_inject_count.py"),
    ("[autopilot] stop-inject", "autopilot_stop_hook.py"),
)


def _addon_runner_token(addon_id: str, hook_id: str) -> str:
    _ = addon_id  # marker carries addon ownership; hook runner normalizes to hook_id.
    return f"[hook-runner:{normalize_hook_id(hook_id)}]"


def _managed_addon_runner_id(addon_id: str, hook_id: str) -> str:
    _ = addon_id
    return normalize_hook_id(hook_id)


def _is_exact_addon_hook_command(command: str, marker: str) -> bool:
    match = _ADDON_HOOK_MARKER_RE.fullmatch(marker)
    if not match:
        return False
    addon_id = match.group("addon_id")
    hook_id = match.group("hook_id")
    runner_id = _managed_addon_runner_id(addon_id, hook_id)
    exact_comment = f"{marker} {_addon_runner_token(addon_id, hook_id)}"
    return exact_comment in command and _hook_runner_id_match(command, runner_id)


def _is_exact_adapter_hook_command(command: str, marker: str) -> bool:
    match = _ADAPTER_HOOK_MARKER_RE.fullmatch(marker)
    if not match:
        return False
    adapter_id = match.group("adapter_id")
    hook_id = match.group("hook_id")
    runner_id = addon_installer.privileged_adapter_runner_id(adapter_id, hook_id)
    exact_comment = f"{marker} [hook-runner:{runner_id}]"
    return exact_comment in command and _hook_runner_id_match(command, runner_id)


def _is_legacy_autopilot_hook_command(command: str) -> bool:
    normalized = command.replace("\\", "/")
    if "/autopilot-mode/scripts/" not in normalized:
        return False
    return any(marker in normalized and script in normalized for marker, script in _LEGACY_AUTOPILOT_HOOKS)


def _reconcile_codex_hook_trust(content, changes):
    """Remove owned hashes and relocate surviving occurrences without granting trust.

    Each change binds an original position to its new position (or removal) and
    original command hash. The removal pass supplies this mapping directly;
    hashes never identify duplicate occurrences. User-edited or colliding
    tables that cannot be relocated intact reject the whole removal.
    """
    parsed = tomllib.loads(content)
    hooks = parsed.get("hooks", {})
    if not isinstance(hooks, dict) or not isinstance(hooks.get("state", {}), dict):
        raise ValueError("Codex hook trust state must be a TOML table")
    tables = hooks.get("state", {})
    expected = copy.deepcopy(parsed)
    expected_tables = expected.get("hooks", {}).get("state", {})
    lines = content.splitlines(keepends=True)
    edits = []
    vacated = set()
    moves = {}
    for key, (new_key, expected_hash) in changes.items():
        table = tables.get(key)
        header = f"[hooks.state.{_toml_basic_string(key)}]"
        start, end = _find_toml_header_bounds(lines, header)
        if new_key is not None:
            if table is not None:
                if not isinstance(table, dict) or table.get("trusted_hash") != expected_hash or start is None:
                    raise ValueError(f"Cannot relocate user-edited Codex hook trust: {key}")
                replacement = lines[start:end].copy()
                replacement[0] = replacement[0].replace(header, f"[hooks.state.{_toml_basic_string(new_key)}]", 1)
                edits.append((start, end, replacement))
                vacated.add(key)
                moves[key] = (new_key, table)
            continue
        if not isinstance(table, dict) or table.get("trusted_hash") != expected_hash or start is None:
            continue
        trusted_index = next((i for i in range(start + 1, end)
            if lines[i].split("=", 1)[0].strip() == "trusted_hash" and "=" in lines[i]), None)
        if trusted_index is None:
            continue
        if lines[trusted_index].strip() != f'trusted_hash = "{expected_hash}"':
            continue  # User-edited representations/comments are not our unchanged line.
        if set(table) == {"trusted_hash"} and not any(line.strip().startswith("#") for line in lines[start + 1:end]):
            edits.append((start, end, []))
            expected_tables.pop(key)
            vacated.add(key)
        else:
            edits.append((trusted_index, trusted_index + 1, []))
            expected_tables[key].pop("trusted_hash")
    for key, (new_key, _) in changes.items():
        if new_key is not None and new_key in tables and new_key not in vacated:
            raise ValueError(f"Codex hook trust destination retains user data: {new_key}")
    for key in moves:
        expected_tables.pop(key)
    for new_key, table in moves.values():
        expected_tables[new_key] = table
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = replacement
    result = ''.join(lines)
    actual = tomllib.loads(result)
    # Removing the last generated nested header also removes its implicit
    # empty ancestors. Other user tables, including empty ones, stay exact.
    for value in (expected, actual):
        hook_tables = value.get("hooks")
        if isinstance(hook_tables, dict):
            if hook_tables.get("state") == {}:
                hook_tables.pop("state")
            if not hook_tables:
                value.pop("hooks")
    if actual != expected:
        raise ValueError("Codex hook trust representation cannot be reconciled without changing user data")
    return result


def _write_removed_hook_settings(settings_file, settings, platform_key, changes):
    """Commit trust/settings atomically per file; restore trust on settings failure."""
    if platform_key != "codex" or not changes:
        _write_settings(settings_file, settings, dry_run=False)
        return
    config_file = _resolve_codex_config_toml()
    if not config_file.exists():
        _write_settings(settings_file, settings, dry_run=False)
        return
    original = config_file.read_bytes()
    stat_before = config_file.stat()
    content = _reconcile_codex_hook_trust(original.decode('utf-8'), changes).encode('utf-8')
    if content == original:
        _write_settings(settings_file, settings, dry_run=False)
        return
    install_destination.assert_mutation_path(config_file)
    backup = config_file.with_suffix('.toml.bak')
    install_destination.assert_mutation_path(backup)
    shutil.copy2(config_file, backup)
    runtime_config._atomic_write_bytes(config_file, content)
    try:
        _write_settings(settings_file, settings, dry_run=False)
    except BaseException as error:
        try:
            runtime_config._atomic_write_bytes(config_file, original)
            target = runtime_config._atomic_write_destination(config_file)
            os.utime(target, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
        except BaseException as rollback_error:
            error.add_note(f"Codex trust rollback failed; original backup is {backup}: {rollback_error}")
        raise


def _remove_hook_commands_by_command(hooks_list: list, predicate: Callable[[str], bool], *, position_callback=None) -> int:
    removed = 0
    kept = []
    for old_group, entry in enumerate(hooks_list):
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            kept.append(entry)
            continue
        kept_hooks = []
        positions = []
        removed_from_entry = 0
        for old_handler, hook in enumerate(hooks):
            if isinstance(hook, dict) and predicate(hook.get("command", "")):
                removed += 1
                removed_from_entry += 1
                positions.append((old_handler, None, hook))
            else:
                positions.append((old_handler, len(kept_hooks), hook))
                kept_hooks.append(hook)
        if position_callback is not None:
            for old_handler, new_handler, hook in positions:
                if isinstance(hook, dict) and hook.get("type") == "command":
                    new_position = None if new_handler is None else (len(kept), new_handler)
                    position_callback((old_group, old_handler), new_position, entry, hook)
        if kept_hooks:
            if removed_from_entry:
                entry["hooks"] = kept_hooks
            kept.append(entry)
        elif not removed_from_entry:
            kept.append(entry)
        else:
            # All commands in this entry were managed addon commands; remove the now-empty entry instead of leaving a no-op hook container behind.
            pass
    hooks_list[:] = kept
    return removed


def _remove_hook_settings_commands(settings_file, hooks_obj, predicate, *, collect_trust=False):
    changes = {}
    total = 0
    for event, groups in hooks_obj.items():
        if not isinstance(groups, list):
            continue
        def position(old, new, group, hook):
            if old == new:
                return
            key = _codex_hook_state_key(settings_file, event, *old)
            new_key = None if new is None else _codex_hook_state_key(settings_file, event, *new)
            changes[key] = (new_key, _codex_command_hook_hash(event, group, hook))
        total += _remove_hook_commands_by_command(groups, predicate, position_callback=position if collect_trust else None)
    return total, changes


def _remove_legacy_autopilot_hook_entries(hooks_obj: dict[str, Any]) -> int:
    total = 0
    for event_list in hooks_obj.values():
        if isinstance(event_list, list):
            total += _remove_hook_commands_by_command(event_list, _is_legacy_autopilot_hook_command)
    return total


def remove_addon_hook(marker: str, *, platform_key: str, dry_run: bool = False) -> int:
    """Remove one addon's observational hook entries by exact marker.

    SECURITY: only a well-formed ``[addon:<id>] <hook_id>`` marker is honored; any
    other string (e.g. a forged core marker like ``[completion-reminder] AGENTS.md``)
    is refused, so a tampered sidecar can never strip a core governance hook. The
    trailing-space match boundary keeps removal exact across hook ids that are
    prefixes of one another. Returns the number of entries removed.
    """
    if not isinstance(marker, str) or not _ADDON_HOOK_MARKER_RE.fullmatch(marker):
        return 0
    platform = PLATFORMS.get(platform_key)
    if platform is None:
        return 0
    settings_file = platform["hook_file"]()
    if not settings_file.exists():
        return 0
    settings = _read_settings(settings_file, backup_corrupt=not dry_run)
    hooks_obj = settings.get("hooks", {})
    if not isinstance(hooks_obj, dict):
        return 0
    # Ownership proof: remove only the command generated by _hook_runner_command(). A user hook can contain the marker text, or even a fake [hook-runner:] token; it is not ours unless the comment carries the exact generated marker/runner signature and argv invokes the same normalized hook id.
    total, changes = _remove_hook_settings_commands(settings_file, hooks_obj,
        lambda command: _is_exact_addon_hook_command(command, marker), collect_trust=platform_key == "codex")
    if total and not dry_run:
        _write_removed_hook_settings(settings_file, settings, platform_key, changes)
    return total


def remove_adapter_hook(marker: str, *, platform_key: str, dry_run: bool = False) -> int:
    """Remove one core-owned privileged adapter hook by exact marker."""
    if not isinstance(marker, str) or not _ADAPTER_HOOK_MARKER_RE.fullmatch(marker):
        return 0
    platform = PLATFORMS.get(platform_key)
    if platform is None:
        return 0
    settings_file = platform["hook_file"]()
    if not settings_file.exists():
        return 0
    settings = _read_settings(settings_file, backup_corrupt=not dry_run)
    hooks_obj = settings.get("hooks", {})
    if not isinstance(hooks_obj, dict):
        return 0
    total, changes = _remove_hook_settings_commands(settings_file, hooks_obj,
        lambda command: _is_exact_adapter_hook_command(command, marker), collect_trust=platform_key == "codex")
    if total and not dry_run:
        _write_removed_hook_settings(settings_file, settings, platform_key, changes)
    return total


def _is_managed_addon_command(command: str) -> bool:
    """True for a Ghost-ALICE managed addon hook command.

    The comment must contain a matching ``[addon:<id>] <hook>`` marker and
    normalized ``[hook-runner:<hook>]`` token, and argv must invoke the hook
    runner with the same normalized id. This keeps a sweep from deleting
    non-managed hooks that merely copied the marker/comment text.
    """
    match = _MANAGED_ADDON_HOOK_COMMENT_RE.search(command)
    if not match:
        return False
    runner_id = _managed_addon_runner_id(match.group("addon_id"), match.group("hook_id"))
    return _hook_runner_id_match(command, runner_id)


def _is_managed_adapter_command(command: str) -> bool:
    match = _MANAGED_ADAPTER_HOOK_COMMENT_RE.search(command)
    if not match:
        return False
    expected = addon_installer.privileged_adapter_runner_id(match.group("adapter_id"), match.group("hook_id"))
    if match.group("runner_id") != expected:
        return False
    return _hook_runner_id_match(command, expected)


def _remove_managed_addon_entries(hooks_list: list) -> int:
    return _remove_hook_commands_by_command(hooks_list, _is_managed_addon_command)


def _remove_managed_adapter_entries(hooks_list: list) -> int:
    return _remove_hook_commands_by_command(hooks_list, _is_managed_adapter_command)


def remove_all_addon_hooks(*, platform_key: str, dry_run: bool = False) -> int:
    """Remove every owned addon hook entry during full uninstall.

    Removes only entries that are both ``[addon:`` and ``[hook-runner:`` (managed),
    so a full uninstall cleans addon hooks even when a per-addon uninstall left a
    partial (e.g. the addon's skill drifted) -- without touching a non-managed hook.
    """
    platform = PLATFORMS.get(platform_key)
    if platform is None:
        return 0
    settings_file = platform["hook_file"]()
    if not settings_file.exists():
        return 0
    settings = _read_settings(settings_file, backup_corrupt=not dry_run)
    hooks_obj = settings.get("hooks", {})
    if not isinstance(hooks_obj, dict):
        return 0
    total = 0
    for event_list in hooks_obj.values():
        if isinstance(event_list, list):
            total += _remove_managed_addon_entries(event_list)
            total += _remove_managed_adapter_entries(event_list)
    if total and not dry_run:
        _write_settings(settings_file, settings, dry_run=False)
    return total


def uninstall_hook(platform_key: str, dry_run: bool = False, *, destination=None) -> str:
    """Remove hooks from a single framework.

    Return values:
      "removed"   - hooks removed
      "not_found" - no hooks to remove
      "skipped"   - skipped because the framework is not installed
      "unsupported" - skipped because the current runtime does not support it
      "error"     - removal failed
    """
    if destination is not None:
        resolved = _destination_action(platform_key,destination,"uninstall",dry_run=dry_run)
        with install_destination.use(resolved):
            return uninstall_hook(platform_key,dry_run)
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
    settings = _read_settings(settings_file, backup_corrupt=not dry_run)

    hooks_obj = settings.get("hooks", {})
    hook_list = hooks_obj.get(hook_key, [])

    total_removed = 0

    if _hook_already_exists(hook_list, PROMPT_PENDING_MERGE_MARKER):
        total_removed += _remove_hook_entries(hook_list, PROMPT_PENDING_MERGE_MARKER)
    if _hook_already_exists(hook_list, VISIBILITY_COMMAND_MARKER):
        total_removed += _remove_hook_entries(hook_list, VISIBILITY_COMMAND_MARKER)

    if _hook_already_exists(hook_list, HOOK_MARKER):
        total_removed += _remove_hook_entries(hook_list, HOOK_MARKER)

    # Remove the session-intent-analyzer hook (same UserPromptSubmit event).
    if _hook_already_exists(hook_list, SESSION_INTENT_MARKER):
        total_removed += _remove_hook_entries(hook_list, SESSION_INTENT_MARKER)
    for legacy_marker in LEGACY_SESSION_INTENT_MARKERS:
        if _hook_already_exists(hook_list, legacy_marker):
            total_removed += _remove_hook_entries(hook_list, legacy_marker)

    # Remove the web-search-first hook (same UserPromptSubmit event).
    total_removed += _remove_hook_entries(hook_list, WEB_SEARCH_FIRST_MARKER, hook_id="web-search-first")

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
        if _hook_already_exists(ss_list, SESSION_RECEIPT_MARKER):
            total_removed += _remove_hook_entries(ss_list, SESSION_RECEIPT_MARKER)

    # Also remove the io-trace PostToolUse hook.
    post_tool_key = _resolve_hook_event("post_tool_use", platform_key)
    pt_list = hooks_obj.get(post_tool_key, [])
    if _hook_already_exists(pt_list, IO_TRACE_MARKER):
        total_removed += _remove_hook_entries(pt_list, IO_TRACE_MARKER)

    # Full uninstall also strips every MANAGED addon observational hook and every MANAGED privileged adapter hook, so neither a drifted addon hook nor an adapter hook is ever left firing live after a full uninstall.
    for ev_list in hooks_obj.values():
        if isinstance(ev_list, list):
            total_removed += _remove_managed_addon_entries(ev_list)
            total_removed += _remove_managed_adapter_entries(ev_list)

    if total_removed == 0:
        _log(_t("  No hooks found. Nothing to remove", "  No hooks found. Nothing to remove"))
        return "not_found"

    _write_settings(settings_file, settings, dry_run=dry_run)
    _log(_t(f"  Removed {total_removed} hook entry(ies)", f"  Removed {total_removed} hook entry(ies)"))
    return "removed"


# ── Status Check ──────────────────────────────────────────

def check_status_detail(
    platform_key: str,
    node_runtime: str | Path | None | object = _NODE_RUNTIME_OMITTED,
    addon_sources: Any = (),
    destination=None,
) -> HookStatus:
    """Return structured hook installation status for a single framework."""
    if destination is not None or install_destination.current() is None:
        destination = _default_destination(platform_key) if destination is None else destination
        resolved = _destination_action(platform_key,destination,"status")
        with install_destination.use(resolved):
            return check_status_detail(platform_key,node_runtime=node_runtime,addon_sources=addon_sources)
    platform = PLATFORMS[platform_key]
    name = platform["name"]
    settings_file = platform["hook_file"]()
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

    settings = _read_settings(settings_file, backup_corrupt=False)
    hooks_obj = settings.get("hooks", {})
    hook_list = hooks_obj.get(hook_key, [])
    selected_node_runtime = (
        _resolve_node_runtime(platform_key)
        if node_runtime is _NODE_RUNTIME_OMITTED
        else node_runtime
    )

    require_dispatcher = False
    visibility_present = visibility_ok = True
    if platform_key == "codex":
        visibility_present, visibility_ok = _hook_marker_match_status(
            hook_list,
            VISIBILITY_COMMAND_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=_entry_command(_platform_visibility_command_entry(platform_key, hook_key)),
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
    tool_checkpoint_entry = _platform_tool_checkpoint_entry(
        platform_key,
        tool_checkpoint_key,
        selected_node_runtime,
    )
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
    session_receipt_ok = True
    session_receipt_present = True
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
        session_receipt_present, session_receipt_ok = _hook_marker_match_status(
            ss_list,
            SESSION_RECEIPT_MARKER,
            require_dispatcher=require_dispatcher,
            expected_command=_entry_command(_platform_session_receipt_entry(platform_key, ss_key)),
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
        "visibility-command": (visibility_present, visibility_ok),
        "session-intent": (session_intent_present, session_intent_ok),
        "tool-checkpoint": (tool_checkpoint_present, tool_checkpoint_ok),
        "completion": (stop_present, stop_ok),
        "session-start": (session_start_present, session_start_ok),
        "session-receipt": (session_receipt_present, session_receipt_ok),
        "io-trace": (io_trace_present, io_trace_ok),
    }
    if platform_key == "claude":
        permissions = settings.get("permissions")
        allow = permissions.get("allow", []) if isinstance(permissions, dict) else []
        allowed_rules = (
            {item for item in allow if isinstance(item, str)}
            if isinstance(allow, list)
            else set()
        )
        for skill_name in _verified_addon_skill_names(addon_sources, platform_key):
            permission = f"Skill({skill_name})"
            present = permission in allowed_rules
            required[f"skill-permission:{skill_name}"] = (present, present)
    missing = [label for label, (present, ok) in required.items() if not present]
    drifted = [label for label, (present, ok) in required.items() if present and not ok]
    incomplete = missing + drifted

    if not incomplete:
        if platform_key == "codex":
            # Report Codex hooks as instruction-backed until live event semantics are verified. The string API reports installed; the structured result carries the native-verification scope.
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


def check_status(platform_key: str, addon_sources: Any = (), *, destination=None) -> str:
    """Return hook installation status through the string status API.

    Return values:
      "installed" - hooks are installed
      "missing"   - hooks are absent or differ from the current expected state
      "skipped"   - framework is not installed
      "unsupported" - current runtime does not support hooks
    """
    return check_status_detail(platform_key, addon_sources=addon_sources, destination=destination).legacy_status


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
        "    Profiles adjust user-facing governance message volume only; " "they do not disable hooks or gates."
    )
    _log(
        "    Use /visibility to inspect, or /visibility strict, /visibility dynamic, " "or /visibility minimal to change it in trusted Codex sessions."
    )
    _log(
        "    Claude Code: /visibility strict|dynamic|minimal; " "all platforms: python3 _shared/agent_visibility_cli.py show|set <profile>."
    )
    _log("    Hook execution, governance gates, and strict session logging remain unchanged.")


def _print_node_runtime_guidance(
    node_runtime: str | Path | None | object = _NODE_RUNTIME_OMITTED,
) -> None:
    if _path_node_runtime_available():
        return
    configured = (
        _resolve_node_runtime()
        if node_runtime is _NODE_RUNTIME_OMITTED
        else node_runtime
    )
    _log("  Node.js guidance:")
    if configured:
        _log(f"    Current hook install can use configured Node runtime: {configured}")
    _log(f"    {_node_full_capability_guidance()}")


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
        "--skills-dir",
        help=_t(
            "Installed skills directory used to resolve privileged adapter scripts in copy installs",
            "Installed skills directory used to resolve privileged adapter scripts in copy installs",
        ),
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
    parser.add_argument("--destination-file", help="JSON installation location binding")
    parser.add_argument("--core-source-root",help="Core source APIs independent of installed runtime")
    parser.add_argument("--ledger-root",help="Explicit authoritative ledger root independent of source")
    args = parser.parse_args()
    if args.status and args.uninstall:
        parser.error("--status and --uninstall select different actions")
    if args.list_addons and not args.addon_source:
        parser.error("--list-addons requires at least one --addon-source")
    destination = None
    for option,key in ((args.core_source_root,"GHOST_ALICE_CORE_ROOT"),(args.ledger_root,"GHOST_ALICE_SESSION_INTENT_ROOT")):
        if option:
            if not Path(option).is_absolute(): parser.error("explicit locations must be absolute")
            if args.destination_file: parser.error("explicit location flags conflict with --destination-file")
            os.environ[key]=option
    if args.destination_file:
        destination = install_destination.InstallDestination.parse(json.loads(Path(args.destination_file).read_text()))
        if not args.platform:
            parser.error("--destination-file requires --platform")
        _destination_action(args.platform,destination,"status" if args.status else "uninstall" if args.uninstall else "install",dry_run=args.dry_run)
        if destination.mode == "isolated" and args.agent_visibility and (args.status or args.uninstall):
            parser.error("isolated visibility changes require install action")
        if destination.mode == "isolated" and (args.hook_shared_dir or args.skills_dir or args.list_addons):
            parser.error("isolated destination conflicts with override/list action")

    if destination is not None:
        with install_destination.use(destination):
            return _execute_actions(args,destination)
    if not args.list_addons:
        for target in ([args.platform] if args.platform else PLATFORMS):
            _destination_action(target, _default_destination(target),
                "status" if args.status else "uninstall" if args.uninstall else "install", dry_run=args.dry_run)
    return _execute_actions(args)


def _execute_actions(args, destination=None):
    _log(f"install_hooks.py v{VERSION}")
    _log(_t(f"Home directory: {Path.home()}", f"Home directory: {Path.home()}"))
    if args.list_addons:
        return _print_addon_target_summary(args.addon_source, platform=args.platform)
    if args.addon_source and args.dry_run:
        rc = _print_addon_target_summary(args.addon_source, platform=args.platform)
        if rc != 0:
            return rc
    if args.dry_run:
        _log(_t("Mode: DRY-RUN (no actual changes)", "Mode: DRY-RUN (no actual changes)"))
    if args.agent_visibility and not args.dry_run:
        runtime_config.save_config(
            {"agent_visibility": {"profile": args.agent_visibility}},
            home=_home(),
        )
        _log(f"  Agent visibility: profile={args.agent_visibility}")

    targets = [args.platform] if args.platform else list(PLATFORMS.keys())
    results: dict[str, str | HookStatus] = {}
    selected_node_runtimes: list[str | None] = []
    has_error = False

    for key in targets:
        previous_hook_shared_dir = os.environ.get(HOOK_SHARED_DIR_ENV)
        hook_shared_dir = None if destination is not None else _hook_shared_dir_for_platform(key, args.hook_shared_dir)
        if hook_shared_dir is not None:
            os.environ[HOOK_SHARED_DIR_ENV] = str(hook_shared_dir)
        elif destination is None:
            os.environ.pop(HOOK_SHARED_DIR_ENV, None)
        try:
            if args.status:
                node_runtime = _resolve_node_runtime(key)
                selected_node_runtimes.append(node_runtime)
                results[key] = check_status_detail(
                    key,
                    node_runtime=node_runtime,
                    addon_sources=args.addon_source,
                    destination=destination,
                )
            elif args.uninstall:
                results[key] = uninstall_hook(key, dry_run=args.dry_run, destination=destination)
            else:
                node_runtime = _ensure_node_runtime_for_hook_install(key)
                if node_runtime is not None:
                    selected_node_runtimes.append(node_runtime)
                results[key] = install_hook(
                    key,
                    dry_run=args.dry_run,
                    addon_sources=args.addon_source,
                    skills_dir=args.skills_dir,
                    node_runtime=node_runtime,
                    destination=destination,
                )
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
        if selected_node_runtimes:
            _print_node_runtime_guidance(selected_node_runtimes[-1])
        else:
            _print_node_runtime_guidance()

    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
