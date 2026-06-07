#!/usr/bin/env python3
"""Runner for Ghost-ALICE hook commands.

Agent visibility controls user-facing message exposure, not whether a registered
hook runs. This module applies the disabled-hook break-glass list, validates the
wrapped command, and then runs it.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

import agent_visibility_policy
import runtime_config
import work_impact_projection
import strict_session_log

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


def normalize_hook_id(value: str) -> str:
    text = (value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def _split_tokens(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part for part in re.split(r"[,;\s]+", raw) if part.strip()]


def disabled_hooks(env: dict[str, str] | None = None) -> set[str]:
    source = env if env is not None else os.environ
    return {normalize_hook_id(token) for token in _split_tokens(source.get("GHOST_ALICE_DISABLED_HOOKS"))}


def is_hook_enabled(
    hook_id: str,
    profiles: str | dict[str, str] | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    if isinstance(profiles, dict) and env is None:
        env = profiles
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


def assert_allowed_command(argv: list[str], allowed_roots: list[str]) -> None:
    if not argv:
        raise HookCommandRejected("empty hook command rejected")

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


def _validate_shell_command(command: str) -> list[str]:
    check_command = _strip_allowed_suffix(command)
    if "$(" in check_command or "`" in check_command or "\n" in check_command or "\r" in check_command:
        raise HookCommandRejected("shell substitution or multiline command rejected")
    lexer = shlex.shlex(check_command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    if any(token in FORBIDDEN_SHELL_OPERATORS for token in lexer):
        raise HookCommandRejected("shell control operator rejected")
    try:
        argv = shlex.split(check_command, comments=True, posix=True)
    except ValueError as exc:
        raise HookCommandRejected(f"hook command parse failed: {exc}") from exc
    if argv and argv[0] == HOOK_PYTHON_SENTINEL:
        argv[0] = sys.executable
    assert_allowed_command(argv, default_allowed_roots())
    return argv


def _decode_payload(payload: str) -> str:
    try:
        return base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns a clear rejection.
        raise HookCommandRejected("hook payload decode failed") from exc


def _home_from_env(env: dict[str, str]) -> Path | None:
    home = env.get("HOME")
    if not home:
        return None
    return Path(home)


def _read_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    try:
        return sys.stdin.read()
    except OSError:
        return ""


def _payload_from_stdin(stdin_text: str) -> dict[str, object]:
    if not stdin_text.strip():
        return {}
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _safe_path_component(value: object) -> str:
    text = str(value or "unknown").strip()
    text = re.sub(r"[^A-Za-z0-9_.=-]+", "-", text)
    text = re.sub(r"^[.-]+|[.-]+$", "", text)
    return text or "unknown"


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


def _platform_name(env: dict[str, str]) -> str:
    return _safe_path_component(env.get("GHOST_ALICE_PLATFORM") or "unknown")


def _pending_merge_manifest_path(env: dict[str, str], platform: str) -> Path | None:
    home = _home_from_env(env)
    if home is None:
        return None
    return home / ".ghost-alice" / "pending-merges" / platform / "manifest.json"


def _has_pending_merge_undecided(env: dict[str, str], platform: str) -> bool:
    path = _pending_merge_manifest_path(env, platform)
    if path is None:
        return False
    data = _read_json(path)
    entries = data.get("entries")
    if not isinstance(entries, list):
        return False
    return any(isinstance(entry, dict) and entry.get("decided") is False for entry in entries)


def _session_intent_root_candidates(env: dict[str, str]) -> list[Path]:
    candidates: list[Path] = []
    configured = str(env.get("GHOST_ALICE_SESSION_INTENT_ROOT") or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path(__file__).resolve().parents[1] / ".tmp" / "session-intent")
    home = _home_from_env(env)
    if home is not None:
        candidates.append(home / ".ghost-alice" / "session-intent")
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _current_session_pointer(root: Path, platform: str) -> dict[str, object]:
    pointer = _read_json(root / _safe_path_component(platform) / "current-session.json")
    if pointer.get("schema_version") != "session-intent-current.v1":
        return {}
    return pointer


def _resolve_session_id(root: Path, platform: str, payload: dict[str, object], env: dict[str, str]) -> str:
    pointer = _current_session_pointer(root, platform)
    return _safe_path_component(_first_text(
        payload.get("session_id"),
        payload.get("sessionId"),
        payload.get("conversation_id"),
        payload.get("thread_id"),
        env.get("GHOST_ALICE_SESSION_ID"),
        pointer.get("session_id"),
        "",
    ))


def _session_dir(root: Path, platform: str, session_id: str) -> Path:
    return root / _safe_path_component(platform) / _safe_path_component(session_id)


def _latest_intent_event(session_dir: Path) -> dict[str, object]:
    events_path = session_dir / "intent-events.jsonl"
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("event") == "user-input-observed":
            return row
    return {}


def _downstream_gate_matches_latest_event(gate: dict[str, object], latest_event: dict[str, object]) -> bool:
    if not gate.get("input_event_id") and not gate.get("input_digest"):
        return False
    if not latest_event:
        return False
    if gate.get("input_event_id") and latest_event.get("event_id"):
        return gate["input_event_id"] == latest_event["event_id"]
    if gate.get("input_digest") and latest_event.get("input_digest"):
        return gate["input_digest"] == latest_event["input_digest"]
    return False


def _has_current_downstream_block(env: dict[str, str], payload: dict[str, object], platform: str) -> bool:
    for root in _session_intent_root_candidates(env):
        session_id = _resolve_session_id(root, platform, payload, env)
        if session_id == "unknown":
            continue
        session = _session_dir(root, platform, session_id)
        gate = _read_json(session / "downstream-gates.json")
        if gate.get("schema_version") != "downstream-gates.v1" or gate.get("gate") != "jailbreak-detector":
            continue
        if not _downstream_gate_matches_latest_event(gate, _latest_intent_event(session)):
            continue
        decision = str(gate.get("decision") or "").strip().lower()
        if gate.get("opened") is False or decision == "block":
            return True
    return False


def _visibility_context(
    hook_id: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    *,
    env: dict[str, str] | None = None,
    hook_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    source_env = env if env is not None else os.environ
    payload = hook_payload or {}
    platform = _platform_name(source_env)
    text = f"{stdout}\n{stderr}".lower()
    context: dict[str, object] = {}
    routing_surface = payload.get("routing_surface")
    if routing_surface is None:
        routing_surface = payload.get("routing-surface")
    if isinstance(routing_surface, dict):
        context["routing_surface"] = routing_surface
    if _has_pending_merge_undecided(source_env, platform):
        context["pending_merge_undecided"] = True
    if _has_current_downstream_block(source_env, payload, platform):
        context["security_boundary"] = True
    if (
        "no pending warning from this hook means merge-companion-precheck is clean" in text
        or "routine clean pass" in text
        or "clean pass already persisted" in text
    ):
        context["signal"] = "routine-clean-pass"
    if "duplicate reminder" in text or "no state change" in text:
        context["duplicate"] = True
    if '"decision":"deny"' in text or '"decision": "deny"' in text:
        context["decision"] = "deny"
    if "pending-merge" in text and "undecided" in text:
        context["pending_merge_undecided"] = True
    if "external tool" in text or hook_id == "web-search-first":
        context["external_tool_claim"] = True
    if "destructive" in text or "external side effect" in text:
        context["destructive"] = True
    if "secret" in text or "security boundary" in text:
        context["security_boundary"] = True
    if exit_code != 0:
        context["failed_verification"] = True
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


def _classification_from_decision(
    hook_id: str,
    context: dict[str, object],
    decision: dict[str, str],
) -> tuple[str, str, str, bool]:
    reason = decision.get("reason", "")
    if reason == "routine-clean-pass":
        return ("merge-precheck", "routine", "routine", False)
    if reason == "duplicate-reminder":
        return ("duplicate-reminder", "routine", "routine", False)
    if reason == "noop-audit":
        return ("noop-audit", "debug", "audit-only", False)
    if reason == "forced-pending-merge":
        return ("pending-merge", "risk", "forced", False)
    if reason == "forced-security-boundary":
        return ("downstream-block", "risk", "forced", False)
    if reason == "forced-action-denial":
        return ("tool-checkpoint", "gate", "forced", False)
    if reason in {"forced-verification", "forced-nonzero-exit"}:
        return ("completion-check", "verification", "forced", True)
    if reason == "forced-routing-surface":
        return ("routing-surface", "gate", "forced", False)
    if reason in {"forced-side-effect", "forced-security-boundary"}:
        return ("safety-boundary", "risk", "forced", False)
    if reason == "routing-surface-fail-closed":
        return ("routing-surface", "routing", "unknown", False)
    if reason == "routing-surface-full":
        return ("routing-surface", "routing", "essential", False)
    if reason == "routing-surface-focused":
        return ("routing-surface", "routing", "focused", False)
    if context.get("external_tool_claim"):
        return ("web-search-first", "routing", "focused", False)
    return (normalize_hook_id(hook_id), "routine", "essential", False)


def _surface_item_for_result(
    *,
    hook_id: str,
    stdout: str,
    stderr: str,
    context: dict[str, object],
    decision: dict[str, str],
    profile: str,
    strict_log_ref: str,
) -> dict[str, object]:
    value_key, value_kind, exposure_class, verification_failed = _classification_from_decision(
        hook_id,
        context,
        decision,
    )
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


def _render_model_surface(item: dict[str, object], stdout: str, stderr: str) -> str:
    level = str(item.get("model_surface") or "")
    value_key = str(item.get("value_key") or "surface-item")
    value = _one_line(str(item.get("value") or _result_value(stdout, stderr)))
    if level == "omitted":
        return ""
    if level == "marker":
        return f"{value_key} observed"
    if level in {"digest", "focused"}:
        return f"{value_key}: {value}" if value else f"{value_key} observed"
    if level == "full":
        return "\n".join(part for part in (stdout, stderr) if part)
    return f"{value_key}: {value}" if value else f"{value_key} observed"


def _render_user_surface(item: dict[str, object], stdout: str, stderr: str) -> tuple[str, str]:
    level = str(item.get("user_surface") or "")
    value_key = str(item.get("value_key") or "surface-item")
    value = _one_line(str(item.get("value") or _result_value(stdout, stderr)))
    if level == "hidden":
        return ("", "")
    if level == "compact":
        return (f"{value_key} observed\n", "")
    if level == "focused":
        return ((f"{value_key}: {value}\n" if value else f"{value_key} observed\n"), "")
    if level in {"full", "forced"}:
        return (stdout, stderr)
    return (stdout, stderr)


def _emit_rendered_user_surface(user_stdout: str, user_stderr: str) -> None:
    if user_stdout:
        sys.stdout.write(user_stdout)
    if user_stderr:
        sys.stderr.write(user_stderr)


def run(hook_id: str, payload: str) -> int:
    env = os.environ
    if not is_hook_enabled(hook_id, env):
        return 0

    command = _decode_payload(payload)
    force_success = _has_allowed_success_suffix(command)
    argv = _validate_shell_command(command)
    stdin_text = _read_stdin()
    started = time.perf_counter()
    result = subprocess.run(
        argv,
        input=stdin_text if stdin_text else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    observed_duration = time.perf_counter() - started
    hook_payload = _payload_from_stdin(stdin_text)
    config = runtime_config.load_config(env=env, home=_home_from_env(env))
    profile = config["agent_visibility"]["profile"]
    event = _event_name(hook_payload, env)
    context = _visibility_context(
        hook_id,
        result.stdout,
        result.stderr,
        result.returncode,
        env=env,
        hook_payload=hook_payload,
    )
    decision = agent_visibility_policy.decide(
        profile=profile,
        hook_id=hook_id,
        event=event,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        context=context,
    )
    session_id = strict_session_log.session_id_from_payload(hook_payload, env=env)
    platform = env.get("GHOST_ALICE_PLATFORM", "unknown")
    log_ref = str(strict_session_log.log_path(_home_from_env(env), platform, session_id))
    item = _surface_item_for_result(
        hook_id=hook_id,
        stdout=result.stdout,
        stderr=result.stderr,
        context=context,
        decision=decision,
        profile=profile,
        strict_log_ref=log_ref,
    )
    model_surface_output = _render_model_surface(item, result.stdout, result.stderr)
    user_stdout, user_stderr = _render_user_surface(item, result.stdout, result.stderr)
    strict_session_log.append_event(
        home=_home_from_env(env),
        platform=platform,
        session_id=session_id,
        event={
            "hook_id": hook_id,
            "event": event,
            "stdin": stdin_text,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "agent_visibility_profile": profile,
            "visible_decision": decision["visible_decision"],
            "observed_duration_s": observed_duration,
            "observed_duration_source": "hook-runner",
            "surface_item": item,
            "model_surface_output": model_surface_output,
            "user_surface_output": f"{user_stdout}{user_stderr}",
        },
    )
    _emit_rendered_user_surface(user_stdout, user_stderr)
    if force_success:
        return 0
    return result.returncode


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
    normalized_args = _strip_trailing_marker_args(args)
    if normalized_args is None or normalized_args[0] != "run":
        sys.stderr.write("usage: hook_profile_gate.py run <hook-id> [legacy-visibility-csv] <payload-b64>\n")
        raise SystemExit(2)

    try:
        if len(normalized_args) == 3:
            raise SystemExit(run(normalized_args[1], normalized_args[2]))
        if len(normalized_args) == 4:
            raise SystemExit(run(normalized_args[1], normalized_args[3]))
        sys.stderr.write("usage: hook_profile_gate.py run <hook-id> [legacy-visibility-csv] <payload-b64>\n")
        raise SystemExit(2)
    except HookCommandRejected as exc:
        sys.stderr.write(f"hook command rejected: {exc}\n")
        raise SystemExit(126)


if __name__ == "__main__":
    main()
