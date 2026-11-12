#!/usr/bin/env python3
"""Runner for Ghost-ALICE hook commands.

This module applies the disabled-hook break-glass list, validates the wrapped
command, and then runs it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import NoReturn

import install_destination
import ledger_access
from session_identity import is_exact

# Bind transport before source-provider imports. Invalid transport cannot open audit writes.
if "--binding-b64" in sys.argv:
    try:
        _early_binding = install_destination.decode_binding(sys.argv[sys.argv.index("--binding-b64")+1])
        os.environ.update(install_destination.binding_environment(_early_binding["destination"],os.environ))
    except (ValueError, IndexError) as error:
        sys.stderr.write(f"hook binding rejected: {error}\n")
        raise SystemExit(126)

import agent_visibility_policy
import pending_manifest
from fresh_agent_session import write_utf8
from hook_definitions import CORE_HOOK_IDS, decode_hook_payload, normalize_hook_id, read_platform
from hook_input import parse_json_object, read_stdin_utf8
import runtime_config
import work_impact_projection
import state_retention
import strict_session_log
from session_intent_analyzer_hook import bound_session_identity

SAFE_BARE_COMMANDS = {
    "bash",
    "node",
    "python",
    "python3",
}
SAFE_BARE_COMMAND_RE = re.compile(r"^python3\.[0-9]+$")
HOOK_PYTHON_SENTINEL = "__GHOST_ALICE_HOOK_PYTHON__"
SAFE_TRAILING_SUFFIX_RE = re.compile(r"\s*\|\|\s*true\s*(#.*)?\s*$")
FORBIDDEN_SHELL_OPERATORS = {";", "&&", "|", "||", ">", ">>", "<", "<<"}


class HookCommandRejected(ValueError):
    """Raised when a wrapped hook command is outside the allowlist."""

    def __init__(self, message, *, hook_payload=None):
        super().__init__(message)
        # Transient audit context only; it does not grant command authority.
        self.hook_payload = hook_payload


def _split_tokens(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part for part in re.split(r"[,;\s]+", raw) if part.strip()]


def disabled_hooks(env: dict[str, str] | None = None) -> set[str]:
    source = env if env is not None else os.environ
    return {normalize_hook_id(token) for token in _split_tokens(source.get("GHOST_ALICE_DISABLED_HOOKS"))}


def is_hook_enabled(
    hook_id: str,
    env: dict[str, str] | None = None,
) -> bool:
    normalized = normalize_hook_id(hook_id)
    if normalized in disabled_hooks(env):
        return False
    return True


def _resolve_allowed_roots(allowed_roots: list[str]) -> list[Path]:
    roots: list[Path] = []
    for root in allowed_roots:
        try:
            roots.append(Path(root).expanduser().resolve())
        except OSError:
            continue
    return roots


def _active_platform_from_argv(argv: list[str]) -> str | None:
    platform_indexes = [
        index
        for index, argument in enumerate(argv[1:], start=1)
        if argument == "--platform"
    ]
    equals_platform_arguments = [
        argument
        for argument in argv[1:]
        if argument.startswith("--platform=")
    ]
    if len(platform_indexes) + len(equals_platform_arguments) > 1:
        raise HookCommandRejected("duplicate hook platform argument rejected")
    if equals_platform_arguments:
        raise HookCommandRejected("unsupported hook platform argument form rejected")
    if not platform_indexes:
        return None
    platform_index = platform_indexes[0]
    if platform_index + 1 >= len(argv):
        raise HookCommandRejected("invalid hook platform argument rejected")
    platform = argv[platform_index + 1]
    try:
        if read_platform(platform, reject_unknown=True) != platform:
            raise ValueError("hook platform argument must be canonical")
    except ValueError:
        raise HookCommandRejected(f"invalid hook platform argument rejected: {platform}") from None
    return platform


def _is_path_node_runtime(path: Path, env: dict[str, str]) -> bool:
    if not runtime_config.is_node_executable(path):
        return False
    search_path = env.get("PATH")
    for executable in ("node", "node.exe"):
        resolved = shutil.which(executable, path=search_path)
        if not resolved:
            continue
        try:
            if (
                Path(resolved).resolve() == path
                and runtime_config.is_usable_node_runtime(path)
            ):
                return True
        except OSError:
            continue
    return False


def _is_same_file_as_trusted_node_runtime(
    path: Path,
    trusted_paths: list[Path],
) -> bool:
    for trusted in trusted_paths:
        try:
            if path.samefile(trusted):
                return True
        except OSError:
            continue
    return False


def _hook_platform(argv: list[str], env: dict[str, str]) -> str:
    outer = env.get("GHOST_ALICE_PLATFORM", "").strip()
    if outer:
        try:
            outer = read_platform(outer, reject_unknown=True)
        except ValueError:
            raise HookCommandRejected(f"invalid outer hook platform rejected: {outer.lower()}") from None
    inner = _active_platform_from_argv(argv)
    if outer and inner and outer != inner:
        raise HookCommandRejected(
            f"hook platform mismatch: outer={outer}, inner={inner}"
        )
    platform = outer or inner
    if not platform:
        raise HookCommandRejected(
            "dynamic Node hook command requires claude or codex platform"
        )
    return platform


def _resolve_dynamic_hook_node(argv: list[str], env: dict[str, str]) -> Path:
    platform = _hook_platform(argv, env)
    candidates = runtime_config.node_runtime_candidates(env, platform)
    if candidates:
        return candidates[0]
    raise HookCommandRejected(f"no trusted Node runtime available for {platform}")


def assert_allowed_command(
    argv: list[str],
    allowed_roots: list[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    if not argv:
        raise HookCommandRejected("empty hook command rejected")

    source_env = os.environ if env is None else env
    active_platform = _active_platform_from_argv(argv)
    executable = argv[0]
    if executable in SAFE_BARE_COMMANDS or SAFE_BARE_COMMAND_RE.fullmatch(executable):
        return

    path = Path(executable).expanduser()
    if not path.is_absolute():
        raise HookCommandRejected(f"relative hook executable rejected: {executable}")

    try:
        resolved = path.resolve()
    except OSError as exc:
        raise HookCommandRejected(f"hook executable cannot be resolved: {executable}") from exc
    # A registration that pinned a removed executable is rejected and audited here instead of failing at launch.
    if not resolved.is_file():
        raise HookCommandRejected(f"hook executable missing: {executable}")

    if runtime_config.is_node_executable(resolved):
        candidates = runtime_config.node_runtime_candidates(source_env, active_platform)
        if resolved in candidates or _is_path_node_runtime(resolved, source_env):
            return
        if _is_same_file_as_trusted_node_runtime(resolved, candidates):
            return

    for root in _resolve_allowed_roots(allowed_roots):
        try:
            resolved.relative_to(root)
            return
        except ValueError:
            continue

    raise HookCommandRejected(f"hook executable outside allowlist: {resolved}")


def default_allowed_roots() -> list[str]:
    roots = ["/bin", "/usr/bin", "/opt/homebrew"]
    executable_parent = Path(sys.executable).parent
    roots.append(str(executable_parent))
    roots.append(str(Path(__file__).resolve().parent))
    return roots


def _has_allowed_success_suffix(command: str) -> bool:
    return SAFE_TRAILING_SUFFIX_RE.search(command) is not None


def _strip_allowed_suffix(command: str) -> str:
    return SAFE_TRAILING_SUFFIX_RE.sub("", command).strip()


def _validate_shell_command(
    command: str,
    *,
    env: dict[str, str] | None = None,
) -> list[str]:
    check_command = _strip_allowed_suffix(command)
    if "$(" in check_command or "`" in check_command or "\n" in check_command or "\r" in check_command:
        raise HookCommandRejected("shell substitution or multiline command rejected")
    try:
        lexer = shlex.shlex(check_command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
        argv = shlex.split(check_command, comments=True, posix=True)
    except ValueError as exc:
        raise HookCommandRejected(f"hook command parse failed: {exc}") from exc
    if any(token in FORBIDDEN_SHELL_OPERATORS for token in tokens):
        raise HookCommandRejected("shell control operator rejected")
    if argv and argv[0] == HOOK_PYTHON_SENTINEL:
        argv[0] = sys.executable
    source_env = os.environ if env is None else env
    if argv and argv[0] == runtime_config.HOOK_NODE_SENTINEL:
        argv[0] = str(_resolve_dynamic_hook_node(argv, source_env))
    assert_allowed_command(argv, default_allowed_roots(), env=source_env)
    return argv


def _decode_payload(payload: str) -> str:
    try:
        return decode_hook_payload(payload)
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns a clear rejection.
        raise HookCommandRejected("hook payload decode failed") from exc


def _read_stdin() -> str:
    try:
        return read_stdin_utf8()
    except OSError:
        return ""


def _payload_from_stdin(stdin_text: str) -> dict[str, object]:
    try:
        return parse_json_object(stdin_text)
    except json.JSONDecodeError:
        return {}


def _first_text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _event_name(payload: dict[str, object], env: dict[str, str]) -> str:
    return str(
        payload.get("event")
        or payload.get("hook_event_name")
        or payload.get("hookEventName")
        or env.get("GHOST_ALICE_HOOK_EVENT")
        or ""
    )


def _pending_merge_manifest_path(env: dict[str, str], platform: str) -> Path | None:
    if not is_exact(platform):
        return None
    return pending_manifest.manifest_path(platform, env)


def _has_pending_merge_undecided(env: dict[str, str], platform: str) -> bool:
    path = _pending_merge_manifest_path(env, platform)
    if path is None:
        return False
    try:
        return pending_manifest.check_pending(path)["undecided_count"] > 0
    except Exception:  # A failed visibility fallback supplies no pending fact and must not skip the hook's audit.
        return False


# A hook marks its own routine state with this private key; the runner strips it before the host sees the output.
SURFACE_DECLARATION_KEY = "ghostAliceSurface"


def _take_surface_declaration(stdout: str) -> tuple[str, str]:
    """(declared surface, stdout without the private key). Only "routine" is a declaration; anything else is dropped."""
    try:
        protocol = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return "", stdout
    if not isinstance(protocol, dict) or SURFACE_DECLARATION_KEY not in protocol:
        return "", stdout
    declared = protocol.pop(SURFACE_DECLARATION_KEY)
    return ("routine" if declared == "routine" else ""), json.dumps(protocol, ensure_ascii=False) + "\n"


def _strip_private_protocol_metadata(stdout: str) -> str:
    """Remove consumed internal receipts at the host boundary; retain raw audit."""
    try:
        protocol = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout
    if not isinstance(protocol, dict) or "ghostAlicePendingMergeCheck" not in protocol:
        return stdout
    protocol.pop("ghostAlicePendingMergeCheck")
    return json.dumps(protocol, ensure_ascii=False) + "\n"


# Fields a hook may use to block or stop the host, and the native envelope the host receives. Core informs
# and stops: the runner removes the controls from every hook it runs, says so in systemMessage, keeps the
# hook's raw output in the strict log and drops keys outside the envelope (internal records).
HOST_CONTROL_FIELDS = ("decision", "reason", "stopReason")
PERMISSION_FIELDS = ("permissionDecision", "permissionDecisionReason")
HOST_ENVELOPE_FIELDS = frozenset({"continue", "suppressOutput", "systemMessage", "hookSpecificOutput", "additionalContext"})


def _native_protocol_output(stdout: str, *, hook_id: str) -> tuple[str, bool]:
    """Return the host envelope and whether the runner removed a blocking request."""
    try:
        protocol = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout, False
    if not isinstance(protocol, dict):
        return stdout, False
    asked: list[str] = []
    if protocol.get("decision") == "block":
        asked.append(f"block ({_control_reason(protocol.get('reason'), protocol.get('systemMessage'))})")
    if protocol.get("continue") is False:
        asked.append(f"stop the turn ({_control_reason(protocol.get('stopReason'), protocol.get('systemMessage'))})")
    projected = {key: value for key, value in protocol.items()
                 if key in HOST_ENVELOPE_FIELDS and key not in HOST_CONTROL_FIELDS}
    if protocol.get("continue") is False:
        projected["continue"] = True
    specific = projected.get("hookSpecificOutput")
    if isinstance(specific, dict) and any(key in specific for key in PERMISSION_FIELDS):
        permission = specific.get("permissionDecision")
        if isinstance(permission, str) and permission in {"deny", "ask"}:
            action = "ask at the host's permission prompt whether to run the tool call" if specific['permissionDecision'] == 'ask' else 'deny the tool call'
            asked.append(f"{action} "
                         f"({_control_reason(specific.get('permissionDecisionReason'), None)})")
        projected["hookSpecificOutput"] = {key: value for key, value in specific.items()
                                           if key not in PERMISSION_FIELDS}
    if asked:
        notice = f"{hook_id} asked the host to {'; '.join(asked)}; the hook runner removed that request before the host saw it."
        return _with_system_notice(projected, notice), True
    if projected == protocol:
        return stdout, False
    return json.dumps(projected, ensure_ascii=False) + "\n", False


def _control_reason(*candidates: object) -> str:
    return next((value.strip() for value in candidates if isinstance(value, str) and value.strip()), "no reason given")


def _visibility_context(
    hook_id: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    *,
    env: dict[str, str] | None = None,
    declared: str = "",
) -> dict[str, object]:
    source_env = env if env is not None else os.environ
    platform = read_platform(source_env.get("GHOST_ALICE_PLATFORM"))
    text = f"{stdout}\n{stderr}".lower()
    context: dict[str, object] = {}
    if hook_id == "session-start":
        # Pending state belongs to its check result, not every hook in the session.
        pending = None
        if exit_code == 0:
            try:
                receipt = json.loads(stdout).get("ghostAlicePendingMergeCheck")
                if (isinstance(receipt, dict) and receipt.get("platform") == platform
                        and receipt.get("manifest_path") == str(_pending_merge_manifest_path(source_env, platform))
                        and pending_manifest.valid_check(receipt)):
                    pending = receipt["undecided_count"] > 0
            except (ValueError, TypeError, AttributeError):
                pass
        if pending is None:
            pending = _has_pending_merge_undecided(source_env, platform)
        if pending:
            context["pending_merge_undecided"] = True
    if declared:
        # A declaring hook's own wording is not evidence of state; only the real state above and the exit code count.
        context["declared_surface"] = declared
        return context
    if "pending-merge" in text and "undecided" in text:
        context["pending_merge_undecided"] = True
    return context


def classify_surface_item(
    *,
    value_key: str,
    value_kind: str,
    exposure_class: str,
    profile: str,
    strict_log_ref: str,
    source_hook: str,
    value: str = "",
    verification_failed: bool = False,
) -> dict[str, object]:
    return work_impact_projection.make_item(
        source_hook=source_hook,
        value_key=value_key,
        value_kind=value_kind,
        exposure_class=exposure_class,
        value=value,
        strict_log_ref=strict_log_ref,
        profile=profile,
        verification_failed=verification_failed,
    )


def _result_value(stdout: str, stderr: str) -> str:
    return _first_text(stdout.strip(), stderr.strip())


def _classification_from_decision(hook_id: str, decision: dict[str, str]) -> tuple[str, str, str, bool]:
    reason = decision.get("reason", "")
    if reason == "declared-routine":
        return (normalize_hook_id(hook_id), "routine", "routine", False)
    if reason == "forced-pending-merge":
        return ("pending-merge", "risk", "forced", False)
    if reason == "forced-removed-host-control":
        return (normalize_hook_id(hook_id), "risk", "forced", False)
    if reason in {"forced-verification", "forced-nonzero-exit"}:
        return ("completion-check", "verification", "forced", True)
    return (normalize_hook_id(hook_id), "routine", "essential", False)


def _surface_item_for_result(
    *,
    hook_id: str,
    stdout: str,
    stderr: str,
    decision: dict[str, str],
    profile: str,
    strict_log_ref: str,
) -> dict[str, object]:
    value_key, value_kind, exposure_class, verification_failed = _classification_from_decision(hook_id, decision)
    return classify_surface_item(
        value_key=value_key,
        value_kind=value_kind,
        exposure_class=exposure_class,
        profile=profile,
        strict_log_ref=strict_log_ref,
        source_hook=hook_id,
        value=_result_value(stdout, stderr),
        verification_failed=verification_failed,
    )


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _labeled(value_key: str, value: str) -> str:
    return value if value.startswith(f"{value_key}:") else f"{value_key}: {value}"


def _render_model_surface(item: dict[str, object], stdout: str, stderr: str) -> str:
    stdout = _strip_private_protocol_metadata(stdout)
    level = str(item.get("model_surface") or "")
    value_key = str(item.get("value_key") or "surface-item")
    value = _one_line(str(item.get("value") or _result_value(stdout, stderr)))
    if level == "omitted":
        return ""
    if level == "full":
        return "\n".join(part for part in (stdout, stderr) if part)
    return _labeled(value_key, value) if value else f"{value_key} observed"


def _is_hook_noop_json(stdout: str) -> bool:
    try:
        value = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    if value == {}:
        return True
    return (
        isinstance(value, dict)
        and value.get("continue") is True
        and set(value).issubset({"continue", "systemMessage"})
        and value.get("systemMessage", "") == ""
    )


def _render_user_surface(item: dict[str, object], stdout: str, stderr: str) -> tuple[str, str]:
    stdout = _strip_private_protocol_metadata(stdout)
    level = str(item.get("user_surface") or "")
    if _is_hook_noop_json(stdout):
        return (stdout, "" if level in {"hidden", "focused"} else stderr)
    value_key = str(item.get("value_key") or "surface-item")
    if level in {"hidden", "focused"}:
        try:
            protocol = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            protocol = None
        if isinstance(protocol, dict) and protocol.keys() & {
            "continue", "stopReason", "suppressOutput", "systemMessage",
            "decision", "hookSpecificOutput",
        }:
            # stdout is also the platform's machine protocol. Visibility may
            # reduce its user warning, never model context or control fields.
            warning = protocol.get("systemMessage")
            if level == "hidden":
                protocol.pop("systemMessage", None)
            elif isinstance(warning, str) and warning:
                protocol["systemMessage"] = _labeled(value_key, _one_line(warning))
            return (json.dumps(protocol, ensure_ascii=False) + "\n", "")
    value = _one_line(str(item.get("value") or _result_value(stdout, stderr)))
    if level == "hidden":
        return ("", "")
    if level == "focused":
        return ((f"{_labeled(value_key, value)}\n" if value else f"{value_key} observed\n"), "")
    return (stdout, stderr)


def _emit_rendered_user_surface(user_stdout: str, user_stderr: str) -> None:
    if user_stdout:
        write_utf8(sys.stdout, user_stdout)
    if user_stderr:
        write_utf8(sys.stderr, user_stderr)


def run(hook_id: str, payload: str, *, platform: str | None = None, binding=None) -> int:
    if binding is None:
        return _run(hook_id,payload,platform=platform)
    binding = install_destination.decode_binding(install_destination.encode_binding(binding))
    with install_destination.use(binding["destination"]):
        return _run(hook_id,payload,platform=platform,binding=binding)


def _native_intake_session(env, hook_payload):
    if read_platform(env.get("GHOST_ALICE_PLATFORM")) != 'claude' or _event_name(hook_payload, env) not in ('PreToolUse', 'Stop'):
        return None
    return bound_session_identity('claude', hook_payload, env)


def _reconcile_native_intake(env, hook_payload):
    """One native lifecycle transition, shared by every bound tool/Stop hook."""
    session = _native_intake_session(env, hook_payload)
    if session is None:
        return
    module = ledger_access.load_ledger()
    module.reconcile_native_inputs(root=Path(env['GHOST_ALICE_SESSION_INTENT_ROOT']),
                                   platform='claude', session_id=session, record_wait=True)


# Core's waiting reasons, worded for the user here at the host boundary.
NATIVE_WAIT_REASONS = {
    'row-not-found': 'an input has no matching row in the native transcript',
    'row-ambiguous': 'an input matches more than one native transcript row',
    'transcript-unavailable': 'the native transcript changed or is missing',
    'unknown-actor': 'an input carries host markers Ghost-ALICE does not recognize',
}


def _accepts_system_notice(stdout: str, *, exit_code: int) -> bool:
    """The host reads one JSON object on every exit code. Empty output becomes JSON
    only on success, so a failing hook keeps its native error classification."""
    if not stdout.strip():
        return exit_code == 0
    try:
        return isinstance(json.loads(stdout), dict)
    except (json.JSONDecodeError, TypeError):
        return False


def _with_system_notice(protocol: dict[str, object], notice: str) -> str:
    """Add one user warning to the host envelope; decisions and model context stay unchanged."""
    warning = protocol.get('systemMessage')
    protocol['systemMessage'] = f'{warning}\n{notice}' if isinstance(warning, str) and warning else notice
    return json.dumps(protocol, ensure_ascii=False) + '\n'


def _native_intake_notice(env, hook_payload):
    """Claim this session's first notice for the current wait's cause and word it; None otherwise."""
    session = _native_intake_session(env, hook_payload)
    if session is None:
        return None
    try:
        notice = ledger_access.load_ledger().claim_native_input_notice(root=Path(env['GHOST_ALICE_SESSION_INTENT_ROOT']),
                                                        platform='claude', session_id=session)
    except Exception:  # noqa: BLE001 - the inner hook's output goes out; the wait stays recorded for a later hook
        return None
    if not notice:
        return None
    reason = notice['cause'].split(':', 1)[0]
    return (f"Ghost-ALICE intake: records that need an input that Core code matched to the host's transcript row "
            f"(the response model's `interpreted-as` record, the response route and the completion records) are paused "
            f"in this session because {NATIVE_WAIT_REASONS.get(reason, 'an input origin is unconfirmed')} "
            f"({reason}). They resume at your next input in this session. Later waits with this cause are only recorded; "
            "`bash install.sh --doctor` lists them.")


def _record_native_intake_failure(env, hook_payload, error):
    """Record a failed reconcile in the ledger, else in this run's session hook log row; the hook continues."""
    failure = dict(phase='reconcile', error=type(error).__name__, detail=str(error)[:200], recorded='ledger')
    if isinstance(error, sqlite3.OperationalError) and 'database is locked' in str(error):
        # The ledger record would wait for the same lock the reconcile already waited for.
        failure['recorded'] = 'hook-log'
        return failure
    try:
        session = bound_session_identity('claude', hook_payload, env)
        if session is None:
            raise ValueError('no bound native session')
        ledger_access.load_ledger().record_native_intake_failure(root=Path(env['GHOST_ALICE_SESSION_INTENT_ROOT']),
                                                  platform='claude', session_id=session, error=error)
    except Exception:  # noqa: BLE001 - the hook log row keeps the record when the ledger cannot
        failure['recorded'] = 'hook-log'
    return failure


def _run(hook_id: str, payload: str, *, platform: str | None = None, binding=None) -> int:
    env = dict(os.environ)
    if binding is not None:
        binding = install_destination.decode_binding(install_destination.encode_binding(binding))
        env = install_destination.binding_environment(binding["destination"],env)
    if platform is not None:
        env["GHOST_ALICE_PLATFORM"] = platform
    if not is_hook_enabled(hook_id, env):
        return 0

    command = _decode_payload(payload)
    force_success = _has_allowed_success_suffix(command)
    argv = _validate_shell_command(command, env=env)
    stdin_text = _read_stdin()
    hook_payload = _payload_from_stdin(stdin_text)
    if binding is not None:
        for flag in ("--root","--session-intent-root"):
            if flag in argv and argv[argv.index(flag)+1] != env["GHOST_ALICE_SESSION_INTENT_ROOT"]:
                raise HookCommandRejected("inner ledger root conflicts with trusted binding", hook_payload=hook_payload)
    native_intake_failure = None
    if binding is not None:
        try:
            _reconcile_native_intake(env, hook_payload)
        except Exception as error:  # noqa: BLE001 - Ghost-ALICE's own step fails without stopping the hook (P5)
            # Pending inputs still refuse dependent writes, so the inner hook gains no authority.
            native_intake_failure = _record_native_intake_failure(env, hook_payload, error)
    child_env = dict(env)
    # subprocess.run encodes the pipe as UTF-8 below; Python children must decode the same bytes independently of the Windows console code page.
    child_env["PYTHONIOENCODING"] = "utf-8"
    # Node's ledger reader must use the same selected Python as this runner,
    # including installed interpreters outside the child's PATH.
    if not child_env.get("GHOST_ALICE_PYTHON"):
        child_env["GHOST_ALICE_PYTHON"] = sys.executable
    stop_notice_failure = None
    # Only the runner names the notice file, and only for this Stop; an inherited value never reaches a hook.
    child_env.pop("GHOST_ALICE_STOP_NOTICE_FILE", None)
    if _event_name(hook_payload, env) == "Stop":
        # Core code's Stop script records this Stop's notice message at this path. Before an add-on Stop hook,
        # the runner builds that message and records it there first unless the file already holds it.
        try:
            import stop_notice
            notice = stop_notice.notice_path(env, hook_payload, read_platform(env.get("GHOST_ALICE_PLATFORM")))
            if normalize_hook_id(hook_id) not in CORE_HOOK_IDS:
                stop_notice_failure = stop_notice.ensure_stop_notice(
                    read_platform(env.get("GHOST_ALICE_PLATFORM")), hook_payload, env, notice).get("file_error")
            if stop_notice_failure is None:
                child_env[stop_notice.ENV_FILE] = str(notice)
        except Exception as error:  # noqa: BLE001 - Ghost-ALICE's own step fails without stopping the hook
            stop_notice_failure = f"{type(error).__name__}: {error}"
    started = time.perf_counter()
    launch_failure = None
    try:
        result = subprocess.run(
            argv,
            input=stdin_text if stdin_text else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            check=False,
        )
        stdout, stderr, child_exit_code = result.stdout, result.stderr, result.returncode
    except OSError as error:
        launch_failure = dict(error=type(error).__name__, errno=error.errno, detail=str(error))
        stdout, stderr, child_exit_code = '', f'{hook_id}: runner could not start the hook: {type(error).__name__}: {error}\n', None
    observed_duration = time.perf_counter() - started
    # A launch failure has no child exit code. Its audit record is hidden and the host receives exit 0 below.
    runner_exit_code = 1 if child_exit_code is None or child_exit_code == 2 else child_exit_code
    declared, host_stdout = _take_surface_declaration(stdout)
    native_stdout, removed_host_control = _native_protocol_output(host_stdout, hook_id=hook_id)
    config = runtime_config.load_config(env=env, home=install_destination.home_directory(env))
    profile = config["agent_visibility"]["profile"]
    event = _event_name(hook_payload, env)
    context = _visibility_context(
        hook_id,
        host_stdout,
        stderr,
        runner_exit_code,
        env=env,
        declared=declared,
    )
    if removed_host_control:
        context['removed_host_control'] = True
    decision = agent_visibility_policy.decide(
        profile=profile,
        hook_id=hook_id,
        event=event,
        stdout=host_stdout,
        stderr=stderr,
        exit_code=runner_exit_code,
        context=context,
    )
    if launch_failure is not None:
        decision = {"visible_decision": "hide", "reason": "runner-launch-failure-recorded"}
    platform = read_platform(env.get("GHOST_ALICE_PLATFORM"))
    session_id = strict_session_log.session_id_from_payload(hook_payload, env=env, platform=platform)
    log_ref = str(strict_session_log.log_path(install_destination.home_directory(env), platform, session_id))
    item = _surface_item_for_result(
        hook_id=hook_id,
        stdout=host_stdout,
        stderr=stderr,
        decision=decision,
        profile=profile,
        strict_log_ref=log_ref,
    )
    if native_intake_failure is not None:
        item['native_intake_failure'] = native_intake_failure
    if stop_notice_failure is not None:
        item['stop_notice_failure'] = stop_notice_failure
    if launch_failure is not None:
        item['launch_failure'] = launch_failure
        item['user_surface'] = 'hidden'
        item['model_surface'] = 'omitted'
    model_surface_output = _render_model_surface(item, host_stdout, stderr)
    user_stdout, user_stderr = _render_user_surface(item, native_stdout, stderr)
    # Claim the notice only when this envelope can carry it; visibility
    # profiles never hide it.
    if launch_failure is None and binding is not None and _accepts_system_notice(
            user_stdout, exit_code=0 if force_success else runner_exit_code):
        notice = _native_intake_notice(env, hook_payload)
        if notice:
            user_stdout = _with_system_notice(json.loads(user_stdout) if user_stdout.strip() else {}, notice)
    try:
        strict_session_log.append_event(
            home=install_destination.home_directory(env),
            platform=platform,
            session_id=session_id,
            event={
                "hook_id": hook_id,
                "event": event,
                "stdin": stdin_text,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": child_exit_code,
                "agent_visibility_profile": profile,
                "visible_decision": decision["visible_decision"],
                "observed_duration_s": observed_duration,
                "observed_duration_source": "hook-runner",
                "surface_item": item,
                "model_surface_output": model_surface_output,
                "user_surface_output": f"{user_stdout}{user_stderr}",
            },
        )
    except Exception as error:  # noqa: BLE001 - Ghost-ALICE's own step fails without stopping the hook
        state_retention.record_failure("session-logs", log_ref, error)
    if launch_failure is not None:
        return 0
    _emit_rendered_user_surface(user_stdout, user_stderr)
    if force_success:
        return 0
    return runner_exit_code


def _rejection_audit_platform(payload: str, env: Mapping[str, str]) -> str:
    """Choose an audit namespace without contributing to authorization."""
    fallback = read_platform(env.get("GHOST_ALICE_PLATFORM"))
    try:
        command = _strip_allowed_suffix(_decode_payload(payload))
    except HookCommandRejected:
        return fallback
    try:
        argv = shlex.split(command, comments=True, posix=True)
    except ValueError:
        return fallback
    try:
        inner_platform = _active_platform_from_argv(argv)
    except HookCommandRejected:
        return "unknown"
    return inner_platform or "unknown"


def _append_rejection_audit(hook_id: str, stderr: str, *, platform: str,
                            hook_payload=None) -> None:
    env = os.environ
    if hook_payload is None:
        hook_payload = _payload_from_stdin(_read_stdin())
    config = runtime_config.load_config(env=env, home=install_destination.home_directory(env))
    profile = config["agent_visibility"]["profile"]
    session_id = strict_session_log.session_id_from_payload(hook_payload, env=env, platform=platform)
    log_ref = str(strict_session_log.log_path(install_destination.home_directory(env), platform, session_id))
    item = classify_surface_item(
        value_key="hook-command-rejection",
        value_kind="gate",
        exposure_class="forced",
        profile=profile,
        strict_log_ref=log_ref,
        source_hook=hook_id,
        value=_one_line(stderr),
    )
    strict_session_log.append_event(
        home=install_destination.home_directory(env),
        platform=platform,
        session_id=session_id,
        event={
            "hook_id": hook_id,
            "event": _event_name(hook_payload, env) or "hook-command-rejected",
            "stdout": "",
            "stderr": stderr,
            "exit_code": 126,
            "agent_visibility_profile": profile,
            "visible_decision": "force_show",
            "surface_item": item,
            "model_surface_output": stderr,
            "user_surface_output": stderr,
        },
    )


def _strip_trailing_marker_args(args: list[str]) -> list[str] | None:
    if len(args) in {3, 4}:
        return args
    if len(args) > 3 and args[3] == "#":
        return args[:3]
    if len(args) > 4 and args[4] == "#":
        return args[:4]
    return None


def main(argv: list[str] | None = None) -> NoReturn:
    args = list(sys.argv[1:] if argv is None else argv)
    binding = None
    if "--binding-b64" in args:
        index = args.index("--binding-b64")
        if index + 1 >= len(args):
            raise SystemExit(126)
        try:
            binding = install_destination.decode_binding(args[index+1])
        except ValueError as error:
            sys.stderr.write(f"hook binding rejected: {error}\n")
            raise SystemExit(126)
        del args[index:index+2]
    normalized_args = _strip_trailing_marker_args(args)
    if normalized_args is None or normalized_args[0] != "run":
        sys.stderr.write("usage: hook_profile_gate.py run <hook-id> [platform|visibility-csv] <payload-b64>\n")
        # A malformed registration is Ghost-ALICE's own failure; exit 2 would block the host.
        raise SystemExit(1)

    payload = normalized_args[2] if len(normalized_args) == 3 else normalized_args[3]
    positional_platform: str | None = None
    if len(normalized_args) == 4:
        candidate = read_platform(normalized_args[2])
        if candidate != "unknown":
            positional_platform = candidate
    try:
        if len(normalized_args) == 3:
            raise SystemExit(run(normalized_args[1], payload,binding=binding))
        if len(normalized_args) == 4:
            if positional_platform is not None:
                raise SystemExit(
                    run(
                        normalized_args[1],
                        payload,
                        platform=positional_platform,
                        binding=binding,
                    )
                )
            raise SystemExit(run(normalized_args[1], payload,binding=binding))
        sys.stderr.write("usage: hook_profile_gate.py run <hook-id> [platform|visibility-csv] <payload-b64>\n")
        raise SystemExit(1)
    except HookCommandRejected as exc:
        rejection_stderr = f"hook command rejected: {exc}\n"
        sys.stderr.write(rejection_stderr)
        try:
            with install_destination.use(binding["destination"]) if binding else nullcontext():
                audit_platform = positional_platform or _rejection_audit_platform(
                    payload,
                    os.environ,
                )
                _append_rejection_audit(
                    normalized_args[1],
                    rejection_stderr,
                    platform=audit_platform,
                    hook_payload=exc.hook_payload,
                )
        except Exception as audit_exc:  # noqa: BLE001 - audit is best-effort.
            try:
                sys.stderr.write(f"hook rejection audit failed: {audit_exc}\n")
            except Exception:  # noqa: BLE001 - diagnostics must not mask rejection.
                pass
        raise SystemExit(126)


if __name__ == "__main__":
    main()
