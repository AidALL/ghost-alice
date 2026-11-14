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
  4. Preserve unrelated settings and register addon observational hooks.
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
from contextlib import ExitStack, redirect_stdout
from dataclasses import asdict
from functools import partial
from installer.report import exception_lines
import codex_config_toml as tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

MIN_PYTHON_VERSION = (3, 11)
HOOK_PYTHON_SENTINEL = "__GHOST_ALICE_HOOK_PYTHON__"

if sys.version_info < MIN_PYTHON_VERSION:
    detected = ".".join(str(part) for part in sys.version_info[:3])
    sys.stderr.write(f"install_hooks.py requires Python 3.11+; detected Python {detected}\n")
    raise SystemExit(1)

from merge_companion_messages import render_pending_merge_message
import hook_definitions
from hook_definitions import POSIX_HOOK_PYTHON_LAUNCHER, normalize_hook_id, resolve_hook_event
import runtime_config
import install_destination
import state_retention
from addon_installer import AddonManifestError, load_addon_targets
import retired_assets
from text_lines import split_lines

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

# ── Hook Payload Definitions ──────────────────────────────

def _shell_print_message(message: str, *, payload: bool = False) -> str:
    message_payload = base64.b64encode((message + "\n").encode("utf-8")).decode("ascii")
    code = f"import sys,base64;sys.stdout.buffer.write(base64.b64decode('{message_payload}'))"
    return _hook_python_command("-c", code, payload=payload)


def _shell_print_json(payload: dict[str, Any], *, payload_mode: bool = False) -> str:
    return _shell_print_message(json.dumps(payload, ensure_ascii=False), payload=payload_mode)


def _localized_bridge(
    internal_instruction: str,
    human_guidance_en: str,
    tech_en: str,
) -> str:
    return "\n".join([
        f"Internal instruction: {internal_instruction}",
        f"For the human user: {human_guidance_en}",
        f"Tech: {tech_en}",
    ])


def _quote_static_arg(value: str | Path) -> str:
    if os.name != "nt" and not sys.platform.startswith("win"):
        return _quote_posix_arg(str(value))
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
        return script.as_posix()
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
    """Retain one ledger root from the selected installed hook chain.

    Reads only the chosen platform settings, never discovery/latest-checkout state.
    Multiple managed roots require GHOST_ALICE_SESSION_INTENT_ROOT, --ledger-root or --destination-file to identify the ledger root. The installer does not check who supplies the value.
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
                if not any((globals().get("_hook_runner_id_match") or (lambda *_: False))(command,identity) for identity in ("session-intent","tool-checkpoint","session-start","session-receipt")):
                    continue
                runner = hook_definitions.parse_hook_runner_command(command)
                if runner["binding"] is not None:
                    binding=install_destination.decode_binding(runner["binding"])
                    roots.add(binding["destination"]["ledger_root"])
                else:
                    inner=shlex.split(runner["command"],comments=True)
                    for flag in ("--root","--session-intent-root"):
                        if flag in inner:
                            root=inner[inner.index(flag)+1]
                            if Path(root).is_absolute(): roots.add(root)
    if len(roots)>1:
        raise ValueError("installed managed hooks contain conflicting ledger roots; identify one with GHOST_ALICE_SESSION_INTENT_ROOT, --ledger-root or --destination-file")
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
    parser_source = source_root / '_shared/codex_config_toml'
    sources.extend((source, target / source.relative_to(source_root / '_shared'))
                   for source in parser_source.rglob('*')
                   if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc')
    for source, output in sources:
        install_destination.assert_mutation_path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists() or output.read_bytes() != source.read_bytes():
            shutil.copy2(source, output)
    # The runtime mirrors the current source set. A module removed from the
    # source leaves the runtime too, so an installed hook never imports it.
    expected = {output for _, output in sources}
    for stale in (target.iterdir() if target.is_dir() else ()):
        if stale.is_file() and stale.suffix in {'.py', '.mjs', '.json'} and stale not in expected:
            install_destination.assert_mutation_path(stale)
            stale.unlink()
    for stale in (target / 'codex_config_toml').rglob('*'):
        if stale.is_file() and stale not in expected:
            install_destination.assert_mutation_path(stale)
            stale.unlink()


def _pending_merge_precheck_command(
    *,
    platform: str,
    output_format: str,
    internal_instruction: str,
    payload_mode: bool = False,
) -> str:
    script = _resolve_pending_merge_precheck_script()
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
        ])

    if output_format == "json":
        return _shell_print_json({"continue": True, "systemMessage": internal_instruction}, payload_mode=payload_mode)

    return _shell_print_message(
        f"Internal instruction: {internal_instruction}\n" + render_pending_merge_message("session_start"),
        payload=payload_mode,
    )

# 1. Retired input pending-merge hook: the marker lets install and uninstall remove old entries.
# merge-companion re-checks the manifest before work changes an installed skill.
PROMPT_PENDING_MERGE_MARKER = "[merge-companion] prompt-check"

# 1-B. Retired task-router reminder: the marker lets install and uninstall remove old entries.
HOOK_MARKER = "[hook-reminder] AGENTS.md"

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
STOP_HOOK_INTERNAL = "completion-reminder: Core authenticates the response model's review of claims, original sources and the complete response. The response model follows verification-before-completion for new completion claims and retains unchanged evidence at its original source and time. Core returns the rejected requirement and the response delivery target. Printed skill or trace fields do not establish evidence; strict audit remains active."
STOP_HOOK_MESSAGE = _localized_bridge(
    STOP_HOOK_INTERNAL,
    "Core checks the authenticated claim, source and complete-response review.",
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
SESSION_START_INTERNAL = "merge-companion session-check: This hook checks the current platform's pending-merge manifest. The response model applies merge-companion to undecided entries when the human user asks to review them or the selected work changes an affected skill. Missing, empty, fully decided or unparsable manifests pass silently under the current contract; a clean hook result requires no second shell inspection."


def _session_start_command(*, platform: str, output_format: str, payload_mode: bool = False) -> str:
    return (
        _pending_merge_precheck_command(
            platform=platform,
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
        "Field names and literal tokens stay in English. Human notes use the current user language.",
    ))
    + f" # {TOOL_CHECKPOINT_MARKER}"
)
# ── io-trace hook payload ─────────────────────────────────
# Write an io-trace record for each tool call the host reports, on the events in each platform's io_trace_intents.
# stdout is not visible to the user on these events, but the hook log keeps it.

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
        print(f"Addon manifest error: {exc}", file=sys.stderr)
        return 1

    if not targets:
        print("No addon skill targets discovered.")
        return 0

    print("Addon skill targets:")
    for target in targets:
        print(f"  {target.name} ({target.origin}) -> {target.source}")
    return 0


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
    normalized = normalize_hook_id(hook_id)
    binding_platform = platform_key or os.environ.get("GHOST_ALICE_PLATFORM") or "codex"
    destination = install_destination.current() or _default_destination(binding_platform)
    binding = destination.binding()
    runner_args = hook_definitions.encode_hook_runner_args(
        hook_id, command, platform_key, install_destination.encode_binding(binding))
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


# Files beside the dispatcher that nothing reads: copies an older installer made for the retired task-router
# reminder, and the checkpoint surface state the dispatcher wrote before 8a5e388.
RETIRED_HOOK_FILES = ("session_intent_snapshot.mjs", "reminder_texts.json", "tool-checkpoint-surface-state.json")


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
    node_runtime: str | Path | None | object = _NODE_RUNTIME_OMITTED,
) -> str:
    """The dispatcher reads only the platform and the event."""
    return " ".join([
        _node_runtime_command(platform, node_runtime),
        _quote_command_arg(_resolve_installed_hook_dispatcher()),
        "--platform",
        platform,
        "--event",
        event,
    ])


def _command_entry(command: str) -> dict[str, Any]:
    return {
        "matcher": "",
        "hooks": [{"type": "command", "command": command}],
    }


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
    if platform_key in hook_definitions.SUPPORTED_PLATFORMS:
        command = _dispatcher_hook_command(platform_key, event, node_runtime)
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


def _addon_hooks(targets) -> list[tuple[str, str, str, str]]:
    """Return each validated addon's hooks once, even when it supplies several skills."""
    out: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for target in targets:
        if target.addon_id in seen:
            continue
        seen.add(target.addon_id)
        for hook_id, event_intent, script_path in target.hooks:
            out.append((target.addon_id, hook_id, event_intent, script_path))
    return out


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

    retired = [path for path in (_resolve_ghost_alice_hooks_dir() / name for name in RETIRED_HOOK_FILES) if path.exists()]
    current = False
    if target.exists():
        try:
            current = source.read_bytes() == target.read_bytes()
        except OSError:
            current = False

    if current and not retired:
        _log("  Ghost-ALICE hook dispatcher is already current")
        return False

    if dry_run:
        if not current:
            _log(f"DRY-RUN: Would copy {source} -> {target}")
        for path in retired:
            _log(f"DRY-RUN: Would remove retired hook file {path}")
        return True

    if not current:
        install_destination.assert_mutation_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        _log(f"  Ghost-ALICE hook file installed: {target}")
    for path in retired:
        install_destination.assert_mutation_path(path)
        path.unlink()
        _log(f"  Removed retired hook file {path}")
    return True


def _running_on_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


PLATFORMS: dict[str, dict[str, Any]] = {
    "claude": {
        "name": "Claude Code",
        "config_dir": _resolve_claude_dir,
        "hook_file": lambda: _resolve_claude_dir() / "settings.json",
        "hook_intent": "on_prompt_submit",
        "stop_hook_intent": "on_agent_stop",
        "stop_hook_entry": STOP_HOOK_ENTRY,
        "session_start_intent": "on_session_start",
        "session_start_entry": SESSION_START_ENTRY,
        "io_trace_intents": ("post_tool_use", "post_tool_use_failure"),
        "detect": lambda: _resolve_claude_dir().is_dir(),
    },
    "codex": {
        "name": "Codex",
        "config_dir": _resolve_codex_dir,
        "hook_file": lambda: _resolve_codex_dir() / "hooks.json",
        "hook_intent": "on_prompt_submit",
        "stop_hook_intent": "on_agent_stop",
        "stop_hook_entry": STOP_HOOK_ENTRY_CODEX,
        "session_start_intent": "on_session_start",
        "session_start_entry": SESSION_START_ENTRY_CODEX,
        "io_trace_intents": ("post_tool_use",),
        "detect": lambda: _resolve_codex_dir().is_dir(),
    },
}


def _ensure_node_runtime_for_hook_install(platform_key: str) -> str | None:
    if platform_key not in hook_definitions.SUPPORTED_PLATFORMS:
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


def _read_settings(path: Path, *, backup_corrupt: bool = True, strict: bool = False) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError('Hook settings must be a JSON object')
        return data
    except FileNotFoundError:
        if os.path.lexists(path):
            raise
        return {}
    except json.JSONDecodeError as e:
        if strict:
            raise
        _log(f"WARNING: Failed to parse JSON at {path} ({e})")
        if backup_corrupt:
            backup = path.with_suffix(".json.corrupt-bak")
            install_destination.assert_mutation_path(backup)
            shutil.copy2(path, backup)
            print(f"[WARN] Unreadable hook settings at {path}; original saved at {backup}. Continuing with empty settings.")
        else:
            _log("  File may be corrupted. Continuing with empty settings without modifying it.")
        return {}
    except OSError as e:
        if strict:
            raise
        _log(f"WARNING: Failed to read {path} ({e})")
        return {}


def _report_saved(path: Path) -> None:
    try:
        _log(f"Saved: {path}")
    except Exception:
        pass


def _settings_backup_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        if os.path.lexists(path):
            raise
        return None


def settings_backup_sources(platform_key: str) -> dict[Path, bytes | None]:
    """Capture command-start files; only a genuinely absent path has no backup."""
    paths = [PLATFORMS[platform_key]['hook_file']()]
    if platform_key == 'codex':
        paths.append(_resolve_codex_config_toml())
    return {path: _settings_backup_bytes(path) for path in paths}


def _write_settings_backup(path: Path, backup_sources: Mapping[Path, bytes | None] | None) -> Path | None:
    """Publish the supplied command original, or this standalone writer's original."""
    if backup_sources is None:
        if not path.exists():
            return None
    else:
        original = backup_sources[path]
        if original is None:
            return None
    backup = path.with_suffix(f'{path.suffix}.bak')
    install_destination.assert_mutation_path(backup)
    if backup_sources is None:
        shutil.copy2(path, backup)
    else:
        runtime_config._atomic_write_bytes(backup, original)
        if path.exists():
            shutil.copystat(path, backup)
    _log(f"Backup created: {backup}")
    return backup


def _write_settings(
    path: Path,
    data: dict,
    dry_run: bool = False,
    report_saved: bool = True,
    *,
    backup_sources: Mapping[Path, bytes | None] | None = None,
) -> None:
    if dry_run:
        _log(f"DRY-RUN: Would write to {path}:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    install_destination.assert_mutation_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _write_settings_backup(path, backup_sources) is not None:
        state_retention.apply("hook-settings-backups", path, protect=_pending_merges_root())
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


def _pending_merges_root() -> list[Path]:
    """The pending-merges folder: the installer's backups of external changes are never rotated; the installer does not record who made each change."""
    try:
        return [(install_destination.ghost_root(_home()) / "pending-merges").resolve()]
    except OSError:
        return []


def _read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_text_file(path: Path, content: str, dry_run: bool = False, *,
                     backup_sources: Mapping[Path, bytes | None] | None = None) -> None:
    if dry_run:
        _log(f"DRY-RUN: Would write text to {path}:")
        print(content, end="" if content.endswith("\n") else "\n")
        return

    install_destination.assert_mutation_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_settings_backup(path, backup_sources)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    _log(f"Saved: {path}")


def _toml_table_headers(lines: list[str]) -> Iterator[tuple[int, dict[str, Any], str]]:
    """Yield each header's line, decoded table and trailing comment/newline for text edits."""
    section_start = 0
    for idx, raw_line in enumerate(lines):
        if not raw_line.removeprefix("\ufeff").lstrip().startswith("["):
            continue
        try:
            table = tomllib.loads(raw_line)
            # A header-looking line inside a multiline string or array is not
            # a table. The preceding section must contain complete values.
            tomllib.loads("\n".join(line.rstrip("\r\n") for line in lines[section_start:idx]))
        except tomllib.TOMLDecodeError:
            continue
        for end, character in enumerate(raw_line):
            if character != "]":
                continue
            try:
                if tomllib.loads(raw_line[:end + 1]) == table:
                    section_start = idx
                    yield idx, table, raw_line[end + 1:]
                    break
            except tomllib.TOMLDecodeError:
                continue  # a bracket inside a quoted key, or the first of ]]


def _find_toml_header_bounds(lines: list[str], header: str) -> tuple[Optional[int], int]:
    expected = tomllib.loads(header)
    start: Optional[int] = None
    for idx, table, _suffix in _toml_table_headers(lines):
        if start is not None:
            return start, idx
        if table == expected:
            start = idx
    return start, len(lines)


def _toml_table_assignments(
    lines: list[str], start: int, end: int,
) -> Iterator[tuple[int, int, dict[str, Any], str]]:
    """Yield complete assignments and their source spans, leaving multiline values intact."""
    first = start + 1
    while first < end:
        if not lines[first].strip() or lines[first].lstrip().startswith("#"):
            first += 1
            continue
        for last in range(first, end):
            statement = "\n".join(lines[first:last + 1])
            try:
                values = tomllib.loads(statement)
            except tomllib.TOMLDecodeError:
                continue
            suffix = ""
            last_line_start = statement.rfind("\n") + 1
            for offset, character in enumerate(lines[last]):
                if character != "#":
                    continue
                prefix = statement[:last_line_start + offset]
                try:
                    tomllib.loads(prefix)
                except tomllib.TOMLDecodeError:
                    continue  # this # belongs to the value, not a trailing comment
                suffix = statement[len(prefix.rstrip(" \t")):]
                break
            yield first, last + 1, values, suffix
            first = last + 1
            break
        else:
            return


def _toml_table_values(content: str, header: str) -> dict[str, Any] | None:
    """Read a table by its decoded path, independently of its source notation."""
    values = tomllib.loads(content)
    path = tomllib.loads(header)
    while path:
        name, path = next(iter(path.items()))
        if not isinstance(values, dict) or name not in values:
            return None
        values = values[name]
    return values if isinstance(values, dict) else None


def _toml_header_path(table: dict[str, Any]) -> tuple[str, ...] | None:
    path = []
    while table:
        if len(table) != 1:
            return None
        name, table = next(iter(table.items()))
        if not isinstance(table, dict):
            return None  # array tables are not managed setting owners
        path.append(name)
    return tuple(path)


def _toml_value(value: Any) -> str:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(key)} = {_toml_value(item)}" for key, item in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, float):
        return repr(value)
    return json.dumps(value, ensure_ascii=False)


def _toml_dotted_assignments(values: dict[str, Any], prefix: tuple[str, ...] = ()) -> list[str]:
    rendered = []
    for name, item in values.items():
        path = (*prefix, name)
        if isinstance(item, dict) and item:
            rendered.extend(_toml_dotted_assignments(item, path))
        else:
            rendered.append(".".join(json.dumps(part, ensure_ascii=False) for part in path) + " = " + _toml_value(item))
    return rendered


def _edit_toml_without_header(lines: list[str], header: str, key: str, value: bool | str | None) -> bool:
    """Edit the containing dotted/inline assignment, preserving unrelated assignments."""
    target = (*_toml_header_path(tomllib.loads(header)), key)
    target_exists = key in (_toml_table_values("\n".join(lines), header) or {})
    headers = [(idx, _toml_header_path(table)) for idx, table, _ in _toml_table_headers(lines)]
    sections = [(-1, ())] + headers
    for index, (start, context) in enumerate(sections):
        end = sections[index + 1][0] if index + 1 < len(sections) else len(lines)
        if context is None or target[:len(context)] != context:
            continue
        relative = target[len(context):]
        for first, last, values, suffix in _toml_table_assignments(lines, start, end):
            owner = values
            ancestors = []
            for part in relative[:-1]:
                if not isinstance(owner.get(part), dict):
                    break
                ancestors.append((owner, part))
                owner = owner[part]
            else:
                if target_exists and relative[-1] not in owner:
                    continue
                if value is None:
                    if relative[-1] not in owner:
                        continue
                    del owner[relative[-1]]
                    for parent, part in reversed(ancestors):
                        if parent[part]:
                            break
                        del parent[part]
                else:
                    owner[relative[-1]] = value
                replacement = _toml_dotted_assignments(values)
                if suffix:
                    if replacement:
                        replacement[-1] += suffix
                    else:
                        replacement = [suffix.lstrip()]
                candidate = lines[:first] + replacement + lines[last:]
                tomllib.loads("\n".join(candidate))
                lines[:] = candidate
                return True
    if value is None:
        return False
    # A root dotted assignment can extend tables declared by other dotted keys.
    end = headers[0][0] if headers else len(lines)
    replacement = ".".join(json.dumps(part, ensure_ascii=False) for part in target) + " = " + _toml_value(value)
    candidate = lines[:end] + [replacement] + lines[end:]
    tomllib.loads("\n".join(candidate))
    lines[:] = candidate
    return True


def _edit_toml_key(lines: list[str], header: str, key: str, value: bool | str | None) -> bool:
    """Set or remove one managed assignment; None removes it while retaining its comment."""
    tomllib.loads("\n".join(lines))
    start, end = _find_toml_header_bounds(lines, header)
    replacement = f"{key} = {json.dumps(value, ensure_ascii=False)}" if value is not None else None
    if start is None:
        values = _toml_table_values("\n".join(lines), header) or {}
        if key in values and type(values[key]) is type(value) and values[key] == value:
            return False
        if replacement is None:
            return _edit_toml_without_header(lines, header, key, value)
        candidate = [*lines, *([""] if lines and lines[-1].strip() else []), header, replacement]
        try:
            tomllib.loads("\n".join(candidate))
        except tomllib.TOMLDecodeError:
            return _edit_toml_without_header(lines, header, key, value)
        lines[:] = candidate
        return True

    for first, last, values, suffix in _toml_table_assignments(lines, start, end):
        if key not in values:
            continue
        if type(values[key]) is type(value) and values[key] == value:
            return False
        if replacement is None:
            lines[first:last] = [suffix.lstrip()] if suffix else []
        else:
            lines[first:last] = [replacement + suffix]
        return True
    if replacement is None:
        return False
    lines.insert(end, replacement)
    return True


def _render_text_with_trailing_newline(lines: list[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"


def _codex_hook_feature_enabled_in_content(content: str) -> bool:
    return _codex_hook_feature_state_in_content(content) == "true"


def _codex_hook_feature_state_in_content(content: str) -> str:
    values = _toml_table_values(content, "[features]")
    if values is not None and "hooks" in values:
        return "true" if values["hooks"] is True else "false"
    return "missing"


def _merge_codex_hook_feature_flag(content: str) -> tuple[str, bool]:
    """Ensure [features] hooks = true in config.toml content."""
    lines = split_lines(content)
    changed = _edit_toml_key(lines, "[features]", "codex_hooks", None)
    changed = _edit_toml_key(lines, "[features]", "hooks", True) or changed
    return _render_text_with_trailing_newline(lines), changed


def _ensure_codex_hook_feature_enabled(dry_run: bool = False) -> bool:
    """Ensure the hooks feature flag is enabled in Codex config.toml."""
    config_file = _resolve_codex_config_toml()
    current_content = _read_text_file(config_file)
    before_state = _codex_hook_feature_state_in_content(current_content)
    updated_content, changed = _merge_codex_hook_feature_flag(current_content)

    if not changed:
        _log("  Codex config.toml already enables hooks")
        return False

    _write_text_file(config_file, updated_content, dry_run=dry_run)
    if before_state != "true":
        _write_codex_hook_feature_change(
            config_file,
            before_state=before_state,
            after_state="true",
            dry_run=dry_run,
        )
    _log("  Enabled hooks feature flag in Codex config.toml")
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
        _log(f"  DRY-RUN: Would record Codex hooks feature flag change: {path}")
        return
    install_destination.assert_mutation_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(change, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _log("  Recorded Codex hooks feature flag rollback metadata")


def _codex_hook_feature_enabled() -> bool:
    config_file = _resolve_codex_config_toml()
    if not config_file.exists():
        return False
    try:
        content = _read_text_file(config_file)
        return _codex_hook_feature_enabled_in_content(content)
    except (OSError, ValueError):
        return False


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


def _codex_hook_state_table_key(table: dict[str, Any]) -> Optional[str]:
    hooks = table.get("hooks")
    state = hooks.get("state") if isinstance(hooks, dict) else None
    if isinstance(state, dict) and len(state) == 1:
        key = next(iter(state))
        if state[key] == {}:
            return key
    return None


def _prune_codex_hook_state_path_variants(lines: list[str], keep_key: str) -> bool:
    keep_parts = _codex_hook_state_key_parts(keep_key)
    if keep_parts is None:
        return False

    changed = False
    headers = list(_toml_table_headers(lines))
    ends = [index for index, _table, _suffix in headers[1:]] + [len(lines)]
    for (index, table, _suffix), end in reversed(list(zip(headers, ends))):
        existing_key = _codex_hook_state_table_key(table)
        if existing_key is None or existing_key == keep_key:
            continue
        if _codex_hook_state_key_parts(existing_key) != keep_parts:
            continue
        del lines[index:end]
        changed = True

    return changed


def _codex_project_trust_change_path() -> Path:
    return install_destination.ghost_root(_home()) / "install-state" / "codex-project-trust-change.json"


def _codex_project_trust_header(project_path: str) -> str:
    return f"[projects.{_toml_basic_string(project_path)}]"


def _codex_project_trust_state_in_content(content: str, project_path: str) -> str:
    values = _toml_table_values(content, _codex_project_trust_header(project_path))
    if values is not None and "trust_level" in values:
        value = values["trust_level"]
        return value if value in ("trusted", "untrusted") else "other"
    return "missing"


def _merge_codex_project_trust(content: str, project_path: str) -> tuple[str, bool, str]:
    lines = split_lines(content)
    header = _codex_project_trust_header(project_path)
    before_state = _codex_project_trust_state_in_content(content, project_path)
    changed = _edit_toml_key(lines, header, "trust_level", "trusted")
    return _render_text_with_trailing_newline(lines), changed, before_state


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
        _log(f"  DRY-RUN: Would record Codex project trust change: {path}")
        return
    install_destination.assert_mutation_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(change, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _log("  Recorded Codex project trust rollback metadata")


def _ensure_codex_project_trusted(dry_run: bool = False) -> bool:
    config_file = _resolve_codex_config_toml()
    project_path = str(Path(install_destination.current().project_root).resolve() if install_destination.current() else _repo_root_from_this_file().resolve())
    current_content = _read_text_file(config_file)
    updated_content, changed, before_state = _merge_codex_project_trust(current_content, project_path)

    if not changed:
        _log("  Codex project trust already current")
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
    _log("  Trusted Ghost-ALICE project config layer in Codex config.toml")
    return True


def _merge_codex_hook_trust_state(content: str, state_entries: dict[str, str]) -> tuple[str, bool]:
    lines = split_lines(content)
    changed = False

    for key, trusted_hash in state_entries.items():
        changed = _prune_codex_hook_state_path_variants(lines, key) or changed
        header = f"[hooks.state.{_toml_basic_string(key)}]"
        changed = _edit_toml_key(lines, header, "trusted_hash", trusted_hash) or changed

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
        _log("  Codex hook trust state already current")
        return False

    _write_text_file(config_file, updated_content, dry_run=dry_run)
    _log("  Trusted installed Ghost-ALICE Codex hooks")
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
    return _hook_marker_match_status(
        hooks_list, marker, require_dispatcher=require_dispatcher,
        expected_command=expected_command,
    )[1]


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


def _hook_runner_id_match(command: str, hook_id: str | None, *, decode_binding: bool = True) -> bool:
    """Recognize the actual managed outer argv, including serialized binding.

    Marker text and a lookalike filename do not establish runner ownership.
    Commands without an outer binding are removable without granting v2 capability.
    A retired adapter registration carries a binding this Core rejects; `decode_binding=False`
    recognizes its argv for removal only, never for execution.
    """
    if not hook_id:
        return False
    runner = hook_definitions.parse_hook_runner_command(command)
    if runner is None or runner["hook_id"] != normalize_hook_id(hook_id):
        return False
    try:
        if decode_binding and runner["binding"] is not None:
            install_destination.decode_binding(runner["binding"])
        return True
    except (ValueError,UnicodeError,IndexError):
        return False


def _managed_session_intent_options(command: str):
    """Return the options of the current managed session-intent registration, or None for any other form."""
    import shlex
    runner = hook_definitions.parse_hook_runner_command(command)
    if runner is None or runner["hook_id"] != "session-intent":
        return None
    try:
        if runner["platform"] not in hook_definitions.SUPPORTED_PLATFORMS or runner["binding"] is None:
            return None
        binding = install_destination.decode_binding(runner["binding"])
        if set(binding) != {"schema_version", "destination"}:
            return None
        destination = install_destination.InstallDestination.parse(binding["destination"])
        if Path(runner["script"]) != Path(destination.runtime_shared_dir) / "hook_profile_gate.py":
            return None
        argv = shlex.split(runner["command"], comments=True)
        if (len(argv) < 4 or argv[0] != HOOK_PYTHON_SENTINEL
                or Path(argv[1]) != Path(destination.runtime_shared_dir) / "session_intent_analyzer_hook.py"
                or len(argv[2:]) % 2):
            return None
        options = dict(zip(argv[2::2], argv[3::2]))
        if (set(options) != {"--platform", "--format", "--root", "--context"}
                or len(options) != len(argv[2:]) // 2
                or options["--platform"] != runner["platform"]
                or options["--format"] not in {"json", "text"}
                or options["--root"] != destination.ledger_root
                or options["--context"] != "prompt_submit"):
            return None
        return options
    except (ValueError, UnicodeError, IndexError, KeyError, TypeError):
        return None


# Markers an older installer put on the retired core hooks (hook_definitions.RETIRED_CORE_HOOK_IDS).
RETIRED_HOOK_MARKERS = (HOOK_MARKER, PROMPT_PENDING_MERGE_MARKER, WEB_SEARCH_FIRST_MARKER)
# An older installer registered addon adapter hooks with this comment; Core has none, so every such
# registration is a retired hook too.
RETIRED_ADAPTER_COMMENT_RE = re.compile(
    r"\[adapter:[a-z][a-z0-9-]*\] [a-z][a-z0-9-]* \[hook-runner:(?P<runner_id>adapter-[a-z0-9-]+)\]")


def _is_retired_adapter_command(command: str) -> bool:
    """A managed adapter registration: the retired comment and a runner call with the same id.

    Its binding carries the adapter key this Core rejects, so the argv is read without decoding the binding.
    """
    match = RETIRED_ADAPTER_COMMENT_RE.search(command)
    return match is not None and _hook_runner_id_match(command, match.group("runner_id"), decode_binding=False)


def _is_retired_hook_command(command: str) -> bool:
    """Recognize every installed form of a retired hook by its marker or its runner id."""
    return (any(marker in command for marker in RETIRED_HOOK_MARKERS)
            or _is_retired_adapter_command(command)
            or any(_hook_runner_id_match(command, hook_id) for hook_id in hook_definitions.RETIRED_CORE_HOOK_IDS))


def _remove_retired_hooks(hooks_obj) -> int:
    """Remove every installed form of a retired hook from every event."""
    return _remove_hook_settings_commands(hooks_obj, _is_retired_hook_command)


def _remove_empty_hook_groups(hooks_obj) -> int:
    """Remove groups without handlers; an empty group runs nothing whatever its matcher."""
    removed = 0
    for groups in hooks_obj.values():
        if isinstance(groups, list):
            kept = [group for group in groups if not (isinstance(group, dict) and not group.get("hooks"))]
            removed += len(groups) - len(kept)
            groups[:] = kept
    return removed


def _remove_stale_hook_entries(
    hooks_list: list,
    marker: str,
    expected_command: str,
    *,
    require_dispatcher: bool = False,
    extra_markers: tuple[str, ...] = (),
    hook_id: str | None = None,
) -> int:
    """Remove the same managed hook's commands that differ from the expected command; count removed hooks."""
    markers = (marker, *extra_markers)

    def stale(command: str) -> bool:
        owned = any(candidate in command for candidate in markers) or _hook_runner_id_match(command, hook_id)
        return owned and (command != expected_command or (require_dispatcher and not _is_dispatcher_command(command)))

    return _remove_hook_commands_by_command(hooks_list, stale)


def _core_hook_specs(platform_key, node_runtime=_NODE_RUNTIME_OMITTED):
    """Declare Core registrations; entry builders run only for install and status."""
    platform = PLATFORMS[platform_key]
    rows = [
        ('session-intent', platform['hook_intent'], SESSION_INTENT_MARKER,
         LEGACY_SESSION_INTENT_MARKERS, 'session-intent-analyzer', _platform_session_intent_entry),
        ('tool-checkpoint', 'pre_tool_use', TOOL_CHECKPOINT_MARKER,
         LEGACY_TOOL_CHECKPOINT_MARKERS, 'tool-checkpoint',
         partial(_platform_tool_checkpoint_entry, node_runtime=node_runtime)),
        ('completion', platform.get('stop_hook_intent'), STOP_HOOK_MARKER,
         (), 'completion', _platform_stop_hook_entry),
        ('session-start', platform.get('session_start_intent'), SESSION_START_MARKER,
         (), 'session-start', _platform_session_start_entry),
        ('session-receipt', platform.get('session_start_intent'), SESSION_RECEIPT_MARKER,
         (), 'session-receipt', _platform_session_receipt_entry),
    ]
    rows.extend(('io-trace', intent, IO_TRACE_MARKER, (), 'io-trace', _platform_io_trace_entry)
                for intent in platform['io_trace_intents'])
    for hook_id, intent, marker, legacy_markers, label, builder in rows:
        if intent:
            event = resolve_hook_event(intent, platform_key)
            if hook_id != 'session-intent':
                label = f'{event} {label}'
            yield hook_id, event, marker, legacy_markers, label, partial(builder, platform_key, event)


def _install_core_hook(hooks_obj, event, entry, marker, hook_id, label, *, extra_markers=()):
    """Replace stale registrations and add the expected Core command once."""
    hooks = hooks_obj.setdefault(event, [])
    command = _entry_command(entry)
    removed = _remove_stale_hook_entries(
        hooks, marker, command, extra_markers=extra_markers, hook_id=hook_id)
    if removed:
        _log(f"  Removed {removed} stale {label} hook(s)")
    if _hook_already_exists(hooks, marker, expected_command=command):
        _log(f"  {label} hook already exists. Skipping")
        return bool(removed)
    hooks.append(entry)
    _log(f"  {label} hook added")
    return True


# Markers of the hooks Core installs now. Retired markers live in RETIRED_HOOK_MARKERS.
GHOST_ALICE_HOOK_MARKERS = (
    SESSION_INTENT_MARKER,
    TOOL_CHECKPOINT_MARKER,
    STOP_HOOK_MARKER,
    SESSION_START_MARKER,
    SESSION_RECEIPT_MARKER,
    IO_TRACE_MARKER,
)


def _ensure_claude_ghost_alice_skill_permissions(
    settings: dict[str, Any],
    core_names,
    addon_targets,
) -> bool:
    """Apply the validated grant selection that the caller also records in its receipt."""
    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])

    original_len = len(allow)
    retired = {f"Skill({name})" for name in retired_assets.RETIRED_SKILLS}
    allow[:] = [item for item in allow
                if not _is_stale_legacy_checkout_path_permission(item) and item not in retired]
    existing = {item for item in allow if isinstance(item, str)}
    changed = len(allow) != original_len
    for name in sorted(set(core_names) | {target.name for target in addon_targets}):
        rule = f"Skill({name})"
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
) -> HookStatus:
    """Install hooks into a single framework.

    Return values:
      "installed" - newly installed
      "already"   - skipped because the same hook already exists
      "skipped"   - skipped because the framework is not installed
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
            if not dry_run and result.legacy_status in {"installed","already"}:
                runtime_config.save_config({"install_locations":{platform_key: __import__('dataclasses').asdict(resolved)}},home=_home())
            return result
    platform = PLATFORMS[platform_key]
    name = platform["name"]
    settings_file = platform["hook_file"]()
    config_dir = platform["config_dir"]()

    _log(f"── {name} ──")

    if not platform["detect"]():
        _log(f"  {config_dir} not found. Assuming {name} is not installed, skipping")
        return HookStatus(platform_key, 'HOOK_INSTALL_SKIPPED', STATUS_LABELS['skipped'], 'skipped',
                          {'action': 'install', 'items': [], 'errors': []}, 'platform-directory-missing')

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

    _log(f"  Hook config file: {settings_file}")
    settings_error = None
    try:
        settings = _read_settings(settings_file, strict=True)
    except json.JSONDecodeError as error:
        settings_error = error
        settings = _read_settings(settings_file, backup_corrupt=not dry_run)

    changed = False

    permission_items = []
    permission_data = None
    if platform_key == "claude":
        import addon_skill_permissions
        try:
            if settings_error is not None:
                raise ValueError('Unreadable original settings; permission attribution preserved') from settings_error
            permission_before = list(addon_skill_permissions._allow(settings))
            permission_data = addon_skill_permissions.prepare_install(settings_file)
        except (ValueError, OSError, UnicodeError) as error:
            permission_items.append(dict(kind='permission', action='manual-review',
                                         reason='permission-attribution-unresolved', detail=str(error)))
            _log(f'[WARN] Permissions retained for review: {error}')
    core_names = _discover_ghost_alice_skill_names_from_catalog()
    addon_targets = (load_addon_targets(list(addon_sources), core_skill_names=core_names, platform=platform_key)
                     if addon_sources else [])
    if permission_data is not None:
        if _ensure_claude_ghost_alice_skill_permissions(settings, core_names, addon_targets):
            _log("  Updated Ghost-ALICE Skill permissions")
            changed = True

    if "hooks" not in settings:
        settings["hooks"] = {}
    _validate_hook_settings(settings)
    hooks_obj = settings["hooks"]
    # Codex keys trust by handler position; record positions before any edit so trust can follow its handler.
    hooks_before = _hook_positions(settings_file, hooks_obj) if platform_key == "codex" else []

    if platform_key in hook_definitions.SUPPORTED_PLATFORMS:
        if _ensure_hook_dispatcher_installed(dry_run=dry_run):
            changed = True

    removed = _remove_retired_hooks(hooks_obj)
    if removed:
        _log(f"  Removed {removed} retired hook(s)")
        changed = True

    for hook_id, event, marker, legacy_markers, label, build_entry in _core_hook_specs(platform_key, selected_node_runtime):
        if (hook_id == 'completion' and not platform.get('stop_hook_entry') or
                hook_id in {'session-start', 'session-receipt'} and not platform.get('session_start_entry')):
            continue
        entry = build_entry()
        if entry:
            changed |= _install_core_hook(
                hooks_obj, event, entry, marker, hook_id, label, extra_markers=legacy_markers)
        elif hook_id == 'io-trace' and platform_key in hook_definitions.SUPPORTED_PLATFORMS:
            _log("  io_trace_hook.py not found. Skipping io-trace hook")

    # Trust only exact event/command pairs produced by verified addon sources in
    # this install; marker text in pre-existing hooks this installer did not write is not ownership evidence.
    verified_addon_commands: set[tuple[str, str]] = set()
    # Wire observational addon hooks after the Core suite. Empty addon_sources leaves the hook configuration unchanged.
    for addon_id, hook_id, event_intent, script_path in _addon_hooks(addon_targets):
        event_name = resolve_hook_event(event_intent, platform_key)
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
        # Prune only a STALE prior version of THIS managed addon hook -- proven by the exact marker + [hook-runner:] token + matching runner argv, the same ownership predicate remove_addon_hook uses. An unmanaged hook that merely contains the marker substring (no runner proof) is never deleted here.
        if _remove_hook_commands_by_command(
            ev_list,
            lambda cmd, m=marker, exp=command: cmd != exp and _is_exact_addon_hook_command(cmd, m),
        ):
            changed = True
        if not _hook_already_exists(ev_list, match_marker, expected_command=command):
            ev_list.append(entry)
            _log(f"  {event_name} addon hook added: {marker}")
            changed = True
        else:
            _log(f"  {event_name} addon hook already exists: {marker}")

    removed = _remove_empty_hook_groups(hooks_obj)
    if removed:
        _log(f"  Removed {removed} hook group(s) without hooks")
        changed = True

    if platform_key == "codex":
        if _ensure_codex_hook_feature_enabled(dry_run=dry_run):
            changed = True
        if _ensure_codex_project_trusted(dry_run=dry_run):
            changed = True
        if _follow_codex_hook_trust(settings_file, settings, hooks_before, dry_run=dry_run):
            changed = True
        if _ensure_codex_hook_trust_state(
            settings_file, settings, dry_run=dry_run,
            verified_addon_commands=verified_addon_commands,
        ):
            changed = True

    if changed:
        _write_settings(settings_file, settings, dry_run=dry_run, report_saved=False)
    if not dry_run and selected_node_runtime is not None:
        try:
            _persist_node_runtime(platform_key, selected_node_runtime)
            if permission_data is not None:
                addon_skill_permissions.record_install(
                    settings_file, addon_targets,
                    permission_before, settings.get("permissions", {}).get("allow", []),
                    core_names=core_names,
                    prepared=permission_data,
                )
        except Exception as persistence_error:
            if not changed:
                raise
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
    if not changed:
        return HookStatus(platform_key, 'HOOK_INSTALL_ALREADY', STATUS_LABELS['already'], 'already',
                          {'action': 'install', 'items': permission_items, 'errors': []})
    if not dry_run:
        _report_saved(settings_file)
    _log("  Hook installation complete")
    return HookStatus(platform_key, 'HOOK_INSTALL_COMPLETE', STATUS_LABELS['installed'], 'installed',
                      {'action': 'install', 'items': permission_items, 'errors': [],
                       'settings_recovered': settings_error is not None,
                       'settings_read_errors': exception_lines(settings_error) if settings_error else [],
                       'settings_backup': str(settings_file.with_suffix('.json.corrupt-bak')) if settings_error and not dry_run else None})


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


def _hook_positions(settings_file: Path, hooks_obj) -> list[tuple[dict[str, Any], str]]:
    """Each handler with its Codex trust key, in hooks-file order."""
    positions = []
    if not isinstance(hooks_obj, dict):
        return positions
    for event, groups in hooks_obj.items():
        if not isinstance(groups, list):
            continue
        for group_index, group in enumerate(groups):
            hooks = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(hooks, list):
                continue
            for handler_index, hook in enumerate(hooks):
                if isinstance(hook, dict):
                    positions.append((hook, _codex_hook_state_key(settings_file, event, group_index, handler_index)))
    return positions


def _trust_moves(before, settings_file: Path, hooks_obj) -> dict[str, Optional[str]]:
    """Map each handler's original trust key to its current key, or to None once removed.

    `before` holds the original handler objects, so identity cannot match a handler created later.
    """
    after = {id(hook): key for hook, key in _hook_positions(settings_file, hooks_obj)}
    return {key: after.get(id(hook)) for hook, key in before if after.get(id(hook)) != key}


def _reconcile_codex_hook_trust(content: str, settings_file: Path, settings: dict[str, Any], moves) -> str:
    """Make Codex trust tables follow their handlers.

    `moves` maps a handler's original trust key to its new key, or to None for a
    removed handler. A table for this hooks file at a position without a handler
    is an orphan and goes. A table moves with all of its lines, so a stale hash
    still asks the user: moving grants no trust. Tables for other hook files stay
    unchanged. When the edit cannot be shown to keep every other value, the
    content stays unchanged and the reason is logged; trust bookkeeping never
    stops an install or a removal.
    """
    own = settings_file.as_posix().replace("\\", "/")
    current = {key for _, key in _hook_positions(settings_file, settings.get("hooks"))}

    def target(key):
        parts = _codex_hook_state_key_parts(key)
        if parts is None or parts[0] != own:
            return key
        canonical = ":".join((own, *parts[1:]))
        if canonical in moves:
            return moves[canonical]
        return key if canonical in current else None

    def unchanged(reason):
        _log(f"  Codex hook trust tables left unchanged: {reason}")
        return content

    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        return unchanged(f"config.toml does not parse ({error})")
    hooks = parsed.get("hooks", {})
    state = hooks.get("state", {}) if isinstance(hooks, dict) else None
    if not isinstance(state, dict):
        return unchanged("hooks.state is not a table")

    lines = content.split("\n")
    lines = [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])
    headers = list(_toml_table_headers(lines))
    ends = [index for index, _table, _suffix in headers[1:]] + [len(lines)]
    result = lines[:headers[0][0]] if headers else list(lines)
    written = set()
    for (start, table, suffix), end in zip(headers, ends):
        block = lines[start:end]
        key = _codex_hook_state_table_key(table)
        if key is not None:
            new_key = target(key)
            if new_key is None:
                continue
            if new_key in written:
                return unchanged(f"two tables would share {new_key}")
            written.add(new_key)
            if new_key != key:
                block = [f"[hooks.state.{_toml_basic_string(new_key)}]{suffix}", *block[1:]]
        result += block
    reconciled = "".join(result)

    expected = copy.deepcopy(parsed)
    if isinstance(expected.get("hooks"), dict):
        expected["hooks"]["state"] = {target(key): table for key, table in state.items() if target(key) is not None}
    try:
        actual = tomllib.loads(reconciled)
    except tomllib.TOMLDecodeError as error:
        return unchanged(f"the edited text does not parse ({error})")
    # Removing the last nested header also removes its implicit empty ancestors.
    for value in (expected, actual):
        hook_tables = value.get("hooks")
        if isinstance(hook_tables, dict):
            if hook_tables.get("state") == {}:
                hook_tables.pop("state")
            if not hook_tables:
                value.pop("hooks")
    if actual != expected:
        return unchanged("the edit would change other configuration")
    return reconciled


def _follow_codex_hook_trust(settings_file: Path, settings: dict[str, Any], before, *, dry_run: bool = False) -> bool:
    """Match Codex trust tables to the handlers this install moved or removed."""
    config_file = _resolve_codex_config_toml()
    if not config_file.exists():
        return False
    current = _read_text_file(config_file)
    updated = _reconcile_codex_hook_trust(current, settings_file, settings,
                                          _trust_moves(before, settings_file, settings.get("hooks")))
    if updated == current:
        return False
    _write_text_file(config_file, updated, dry_run=dry_run)
    _log("  Matched Codex hook trust to the current hooks")
    return True


def _write_removed_hook_settings(settings_file, settings, platform_key, moves, *, backup_sources=None):
    """Commit trust/settings atomically per file; restore trust on settings failure."""
    if platform_key != "codex":
        _write_settings(settings_file, settings, dry_run=False, backup_sources=backup_sources)
        return
    config_file = _resolve_codex_config_toml()
    if not config_file.exists():
        _write_settings(settings_file, settings, dry_run=False, backup_sources=backup_sources)
        return
    original = config_file.read_bytes()
    stat_before = config_file.stat()
    content = _reconcile_codex_hook_trust(original.decode('utf-8'), settings_file, settings, moves).encode('utf-8')
    if content == original:
        _write_settings(settings_file, settings, dry_run=False, backup_sources=backup_sources)
        return
    install_destination.assert_mutation_path(config_file)
    backup = _write_settings_backup(config_file, backup_sources)
    runtime_config._atomic_write_bytes(config_file, content)
    try:
        _write_settings(settings_file, settings, dry_run=False, backup_sources=backup_sources)
    except BaseException as error:
        try:
            runtime_config._atomic_write_bytes(config_file, original)
            target = runtime_config._atomic_write_destination(config_file)
            os.utime(target, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
        except BaseException as rollback_error:
            error.add_note(f"Codex trust rollback failed; original backup is {backup}: {rollback_error}")
        raise


def _remove_hook_commands_by_command(hooks_list: list, predicate: Callable[[str], bool]) -> int:
    removed = 0
    kept = []
    for entry in hooks_list:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            kept.append(entry)
            continue
        kept_hooks = [hook for hook in hooks if not (isinstance(hook, dict) and predicate(hook.get("command", "")))]
        removed_from_entry = len(hooks) - len(kept_hooks)
        removed += removed_from_entry
        if kept_hooks:
            if removed_from_entry:
                entry["hooks"] = kept_hooks
            kept.append(entry)
        elif not removed_from_entry:
            kept.append(entry)
        # An entry whose commands were all removed goes instead of staying as a no-op container.
    hooks_list[:] = kept
    return removed


def _remove_hook_settings_commands(hooks_obj, predicate) -> int:
    return sum(_remove_hook_commands_by_command(groups, predicate)
               for groups in hooks_obj.values() if isinstance(groups, list))


def remove_addon_hook(marker: str, *, platform_key: str, dry_run: bool = False, backup_sources=None) -> int:
    """Remove one addon's observational hook entries by exact marker.

    SECURITY: only a well-formed ``[addon:<id>] <hook_id>`` marker is honored; any
    other string (e.g. a forged core marker like ``[completion-reminder] AGENTS.md``)
    is refused, so a tampered sidecar can never strip a core governance hook. The
    trailing-space match boundary keeps removal exact across hook ids that are
    prefixes of one another. Returns the number of hooks removed.
    """
    if not isinstance(marker, str) or not _ADDON_HOOK_MARKER_RE.fullmatch(marker):
        return 0
    platform = PLATFORMS.get(platform_key)
    if platform is None:
        return 0
    settings_file = platform["hook_file"]()
    if backup_sources is None and not dry_run:
        backup_sources = settings_backup_sources(platform_key)
    settings = _read_settings(settings_file, strict=True)
    _validate_hook_settings(settings)
    hooks_obj = settings.get("hooks", {})
    # Ownership proof: remove only the command generated by _hook_runner_command(). An unmanaged hook can contain the marker text, or even a fake [hook-runner:] token; it is not ours unless the comment carries the exact generated marker/runner signature and argv invokes the same normalized hook id.
    before = _hook_positions(settings_file, hooks_obj)
    total = _remove_hook_settings_commands(hooks_obj, lambda command: _is_exact_addon_hook_command(command, marker))
    if total and not dry_run:
        _write_removed_hook_settings(settings_file, settings, platform_key, _trust_moves(before, settings_file, hooks_obj),
                                     backup_sources=backup_sources)
    return total


def remove_retired_hooks(*, platform_key: str, dry_run: bool = False, backup_sources=None) -> int:
    """Remove every retired hook registration from one platform's hook file.

    One rule (`_is_retired_hook_command`) for the retired core markers, the retired runner ids and the adapter
    registrations an older installer wrote; Codex trust tables follow their handlers. Returns the number of hooks removed.
    """
    platform = PLATFORMS.get(platform_key)
    if platform is None:
        return 0
    settings_file = platform["hook_file"]()
    if backup_sources is None and not dry_run:
        backup_sources = settings_backup_sources(platform_key)
    settings = _read_settings(settings_file, strict=True)
    _validate_hook_settings(settings)
    hooks_obj = settings.get("hooks", {})
    before = _hook_positions(settings_file, hooks_obj)
    total = _remove_retired_hooks(hooks_obj)
    if total and not dry_run:
        _write_removed_hook_settings(settings_file, settings, platform_key, _trust_moves(before, settings_file, hooks_obj),
                                     backup_sources=backup_sources)
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


def _remove_managed_addon_entries(hooks_list: list) -> int:
    return _remove_hook_commands_by_command(hooks_list, _is_managed_addon_command)


def uninstall_hook(platform_key: str, dry_run: bool = False, *, destination=None, backup_sources=None) -> HookStatus:
    """Remove hooks from a single framework.

    Return values:
      "removed"   - hooks removed
      "not_found" - no hooks to remove
      "skipped"   - skipped because the framework is not installed
      "error"     - removal failed
    """
    if destination is not None:
        resolved = _destination_action(platform_key,destination,"uninstall",dry_run=dry_run)
        with install_destination.use(resolved):
            return uninstall_hook(platform_key, dry_run, backup_sources=backup_sources)
    platform = PLATFORMS[platform_key]
    name = platform["name"]
    settings_file = platform["hook_file"]()

    _log(f"── {name} ──")

    settings_existed = settings_file.exists()
    if settings_existed:
        _log(f"  Hook config file: {settings_file}")
    details = dict(action='uninstall', items=[], hooks_detached=False, settings_state='unknown', errors=[])
    try:
        if backup_sources is None and not dry_run:
            backup_sources = settings_backup_sources(platform_key)
        settings = _read_settings(settings_file, strict=True)
        _validate_hook_settings(settings)
    except (OSError, ValueError, UnicodeError) as error:
        details.update(settings_state='unreadable', errors=exception_lines(error))
        details['items'].append(dict(kind='hook-config', target_name=f'{platform_key}-hooks',
                                     action='manual-review', reason='settings-unreadable', errors=details['errors']))
        return HookStatus(platform_key, 'HOOK_ACTION_ERROR', STATUS_LABELS['error'], 'error', details)
    original = copy.deepcopy(settings)
    candidate = copy.deepcopy(settings)
    hooks_obj = candidate.get('hooks', {})
    hooks_before = _hook_positions(settings_file, hooks_obj)
    total_removed = _remove_managed_hook_entries(platform_key, hooks_obj)
    if total_removed:
        _remove_empty_hook_groups(hooks_obj)
    permission_items = []
    permission_prepared = None
    if platform_key == "claude":
        import addon_skill_permissions
        try:
            core_names = _discover_ghost_alice_skill_names_from_catalog()
            permission_prepared = addon_skill_permissions.prepare_core(
                core_names, settings_file, settings)
            details['pending_owner'] = addon_skill_permissions.pending_owner(permission_prepared)
        except (OSError, ValueError, UnicodeError) as error:
            permission_items.append(dict(kind='permission', action='manual-review',
                                         reason='permission-attribution-unresolved', detail=str(error)))

    final_candidate = candidate
    def write_final(path, updated, **kwargs):
        nonlocal final_candidate
        final_candidate = copy.deepcopy(updated)
        _write_removed_hook_settings(path, updated, platform_key,
                                     _trust_moves(hooks_before, path, updated.get('hooks', {})),
                                     backup_sources=backup_sources)
    try:
        if permission_prepared is not None:
            permission_items.extend(addon_skill_permissions.detach_core(
                core_names, settings_file, settings, write_final,
                dry_run=dry_run, settings_after=candidate, prepared=permission_prepared))
        elif not dry_run and candidate != settings:
            write_final(settings_file, candidate)
    except (OSError, ValueError, ExceptionGroup) as error:
        details['errors'] = exception_lines(error)
        permission_items.append(dict(kind='permission' if platform_key == 'claude' else 'hook-config',
                                     action='manual-review', reason='settings-or-receipt-write-failed',
                                     errors=details['errors']))

    if dry_run:
        details.update(settings_state='readable' if settings_existed else 'missing',
                       would_detach=not any(item.get('settings_state') == 'preserved' for item in permission_items))
    else:
        try:
            actual, detached, commands = _read_hook_references(platform_key)
            details.update(hooks_detached=detached, remaining_commands=commands)
            details['settings_state'] = ('after' if actual == final_candidate else
                                         'before' if actual == original else 'other')
        except (OSError, ValueError, UnicodeError) as error:
            details['errors'].extend(exception_lines(error))
            details['settings_state'] = 'unreadable'
    details['items'].extend(permission_items)
    unowned = [item for item in permission_items if item.get('action') in {'manual-review', 'preserved-existing'}]
    if unowned:
        _log('[WARN] Permissions retained for review: removal lacks confirmed installer addition evidence or finalization.')
    for line in details['errors']:
        _log(f'[ERROR] {line}')
    failed = bool(details['errors']) or (not dry_run and not details['hooks_detached'])
    permission_removed = any(row['action'] in {'removed', 'would-remove'} for row in permission_items)
    legacy = ('error' if failed else 'removed' if total_removed or permission_removed else
              'skipped' if not platform['detect']() else 'not_found')
    if total_removed:
        _log(f'  {"Would remove" if dry_run else "Removed"} {total_removed} hook(s)')
    details['items'].append(dict(kind='hook-config', target_name=f'{platform_key}-hooks',
                                 action='manual-review' if failed and not details['hooks_detached'] else
                                 'hook-uninstall-dry-run' if dry_run else 'hook-uninstall',
                                 result=legacy, hooks_detached=details['hooks_detached'],
                                 reason='hook-detach-unresolved' if failed and not details['hooks_detached'] else 'managed-hooks-detached'))
    return HookStatus(platform_key, 'HOOK_ACTION_ERROR' if failed else 'HOOK_UNINSTALL_COMPLETE',
                      STATUS_LABELS[legacy], legacy, details)


def _read_hook_references(platform_key):
    """Read the effect and apply the same managed-command predicate as removal."""
    actual = _read_settings(PLATFORMS[platform_key]['hook_file'](), strict=True)
    _validate_hook_settings(actual)
    hooks = actual.get('hooks', {})
    detached = not _remove_managed_hook_entries(platform_key, copy.deepcopy(hooks))
    commands = [handler.get('command', '') for groups in hooks.values()
                for group in groups for handler in group.get('hooks', [])] if not detached else []
    return actual, detached, commands


def _validate_hook_settings(settings):
    hooks = settings.get('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('Hook settings hooks must be an object')
    for groups in hooks.values():
        if not isinstance(groups, list) or any(not isinstance(group, dict) or
                not isinstance(group.get('hooks', []), list) or
                any(not isinstance(handler, dict) or ('command' in handler and not isinstance(handler['command'], str))
                    for handler in group.get('hooks', [])) for group in groups):
            raise ValueError('Hook settings entries must contain lists of handler objects')


def _remove_managed_hook_entries(platform_key, hooks_obj):
    """Apply the same ownership predicates to the plan and the read-back check."""
    total_removed = _remove_retired_hooks(hooks_obj)
    for _, event, marker, legacy_markers, _, _ in _core_hook_specs(platform_key):
        markers = (marker, *legacy_markers)
        total_removed += _remove_hook_commands_by_command(
            hooks_obj.get(event, []), lambda command: any(value in command for value in markers))

    # Full uninstall also strips every MANAGED addon observational hook, so a drifted addon hook is never left firing
    # live after a full uninstall.
    for ev_list in hooks_obj.values():
        if isinstance(ev_list, list):
            total_removed += _remove_managed_addon_entries(ev_list)

    return total_removed


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

    if not platform["detect"]():
        _log(f"  {name}: not installed (directory missing)")
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_PLATFORM_MISSING",
            status_label=STATUS_LABELS["skipped"],
            legacy_status="skipped",
            details={"reason": "platform_dir_missing"},
        )

    if not settings_file.exists():
        _log(f"  {name}: no hooks (hook config file missing)")
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_MISSING",
            status_label="hook config file missing",
            legacy_status="missing",
            details={"missing": ["config-file"]},
            missing_reason="config_file_absent",
        )

    settings = _read_settings(settings_file, backup_corrupt=False)
    hooks_obj = settings.get("hooks", {})
    selected_node_runtime = (
        _resolve_node_runtime(platform_key)
        if node_runtime is _NODE_RUNTIME_OMITTED
        else node_runtime
    )

    required = {hook_id: (True, True) for hook_id in hook_definitions.CORE_HOOK_IDS}
    if not platform.get('stop_hook_intent'):
        required['completion'] = (False, False)
    for hook_id, event, marker, _, _, build_entry in _core_hook_specs(platform_key, selected_node_runtime):
        if hook_id in {'session-start', 'session-receipt'} and not platform.get('session_start_entry'):
            continue
        entry = build_entry()
        if entry:
            present, matches = _hook_marker_match_status(
                hooks_obj.get(event, []), marker, expected_command=_entry_command(entry))
            previous_present, previous_matches = required[hook_id]
            required[hook_id] = previous_present and present, previous_matches and matches

    if platform_key == "codex" and not _codex_hook_feature_enabled():
        _log(f"  {name}: hooks.json exists but config.toml does not enable hooks")
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_CONFIG_DISABLED",
            status_label="hook config disabled",
            legacy_status="missing",
            details={"reason": "feature_flag_disabled"},
        )

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
            _log(f"  {name}: hook files installed (instruction-backed, pending runtime smoke evidence)")
            installed_label = STATUS_LABELS["installed"]
            return HookStatus(
                platform=platform_key,
                status_token="HOOK_INSTALLED_INSTRUCTION_BACKED",
                status_label=f"{installed_label} (instruction-backed, pending smoke evidence)",
                legacy_status="installed",
                details={
                    "installed": list(required.keys()),
                    "runtime_semantics": "unverified-pending-smoke",
                },
            )
        _log(f"  {name}: hooks installed (full hook suite)")
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_INSTALLED_OK",
            status_label=STATUS_LABELS["installed"],
            legacy_status="installed",
            details={"installed": list(required.keys())},
        )

    if missing:
        _log(f"  {name}: hook suite incomplete (missing: {', '.join(incomplete)})")
        return HookStatus(
            platform=platform_key,
            status_token="HOOK_MISSING",
            status_label="hook suite incomplete",
            legacy_status="missing",
            details={"missing": missing, "drifted": drifted},
            missing_reason="suite_incomplete",
        )

    _log(f"  {name}: hook suite drifted (mismatched: {', '.join(drifted)})")
    return HookStatus(
        platform=platform_key,
        status_token="HOOK_INSTALLED_DRIFT",
        status_label="hook suite drifted",
        legacy_status="missing",
        details={"missing": [], "drifted": drifted},
    )


def check_status(platform_key: str, addon_sources: Any = (), *, destination=None) -> str:
    """Return hook installation status through the string status API.

    Return values:
      "installed" - hooks are installed
      "missing"   - hooks are absent or differ from the current expected state
      "skipped"   - framework is not installed
    """
    return check_status_detail(platform_key, addon_sources=addon_sources, destination=destination).legacy_status


# ── Result Summary Output ─────────────────────────────────

STATUS_LABELS = {
    "installed": "installed",
    "already": "already exists (skipped)",
    "skipped": "not installed (skipped)",
    "removed": "removed",
    "not_found": "not applicable",
    "missing": "no hooks",
    "error": "error",
}


def _print_summary(action: str, results: dict[str, str | HookStatus]) -> None:
    _log(f"── {action} results ──")
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
        description="Auto-install/remove AI coding agent UserPromptSubmit hooks"
    )
    parser.add_argument(
        "--platform",
        choices=list(PLATFORMS.keys()),
        help="Target a specific framework only (default: all detected)",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove hooks",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check installation status only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--visibility",
        "--agent-visibility",
        dest="agent_visibility",
        choices=sorted(runtime_config.VALID_AGENT_VISIBILITY_PROFILES),
        help="User-facing governance message visibility profile",
    )
    parser.add_argument(
        "--hook-shared-dir",
        help="Installed _shared directory that generated hook commands should reference",
    )
    parser.add_argument(
        "--addon-source",
        action="append",
        default=[],
        help="Addon repo or local manifest path",
    )
    parser.add_argument(
        "--skills-dir",
        help="Installed skills directory of the destination",
    )
    parser.add_argument(
        "--list-addons",
        action="store_true",
        help="List addon manifest targets only",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"install_hooks.py {VERSION}",
    )
    parser.add_argument("--destination-file", help="JSON installation location binding")
    parser.add_argument('--format', choices=('text', 'json'), default='text')
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

    if destination is None and not args.list_addons:
        for target in ([args.platform] if args.platform else PLATFORMS):
            _destination_action(target, _default_destination(target),
                "status" if args.status else "uninstall" if args.uninstall else "install", dry_run=args.dry_run)
    results = {}
    with ExitStack() as stack:
        if destination is not None:
            stack.enter_context(install_destination.use(destination))
        if args.format == 'json':
            stack.enter_context(redirect_stdout(sys.stderr))
        returncode = _execute_actions(args, destination, results=results)
    if args.format == 'json':
        print(json.dumps({'results': {key: asdict(value) for key, value in results.items()},
                          'returncode': returncode}))
    return returncode


def _execute_actions(args, destination, *, results):
    _log(f"install_hooks.py v{VERSION}")
    _log(f"Home directory: {Path.home()}")
    if args.list_addons:
        return _print_addon_target_summary(args.addon_source, platform=args.platform)
    if args.addon_source and args.dry_run:
        rc = _print_addon_target_summary(args.addon_source, platform=args.platform)
        if rc != 0:
            return rc
    if args.dry_run:
        _log("Mode: DRY-RUN (no actual changes)")
    if args.agent_visibility and not args.dry_run:
        runtime_config.save_config(
            {"agent_visibility": {"profile": args.agent_visibility}},
            home=_home(),
        )
        _log(f"  Agent visibility: profile={args.agent_visibility}")

    targets = [args.platform] if args.platform else list(PLATFORMS.keys())
    selected_node_runtimes: list[str | None] = []
    action = "status" if args.status else ("uninstall" if args.uninstall else "install")

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
        except Exception as error:
            errors = exception_lines(error)
            for line in errors:
                _log(f'[ERROR] {PLATFORMS[key]["name"]}: {line}')
            results[key] = HookStatus(key, 'HOOK_ACTION_ERROR', STATUS_LABELS['error'], 'error',
                                      {'action': action, 'errors': errors, 'items': []})
        finally:
            if previous_hook_shared_dir is None:
                os.environ.pop(HOOK_SHARED_DIR_ENV, None)
            else:
                os.environ[HOOK_SHARED_DIR_ENV] = previous_hook_shared_dir

    _print_summary(action, results)
    if not args.uninstall:
        _print_runtime_visibility_status()
        _print_runtime_visibility_guidance()
        if selected_node_runtimes:
            _print_node_runtime_guidance(selected_node_runtimes[-1])
        else:
            _print_node_runtime_guidance()

    return 1 if any(result.legacy_status == 'error' for result in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
