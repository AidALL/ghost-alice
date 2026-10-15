#!/usr/bin/env python3
"""UserPromptSubmit hook for the session-intent-analyzer ledger."""

from __future__ import annotations

from reminder_texts import instruction_override, reminder_text

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
import install_destination


def _home() -> Path:
    for key in ("HOME", "USERPROFILE"):
        value = os.environ.get(key, "").strip()
        if value:
            return Path(value)
    drive = os.environ.get("HOMEDRIVE", "").strip()
    path = os.environ.get("HOMEPATH", "").strip()
    if drive and path:
        return Path(f"{drive}{path}")
    return Path.home()


def _ledger_dir_candidates() -> list[Path]:
    """The source and installed runtime both ship their own sibling ledger API."""
    return [REPO_ROOT / "session-intent-analyzer" / "scripts"]


for LEDGER_DIR in _ledger_dir_candidates():
    if (LEDGER_DIR / "session_intent_ledger.py").is_file() and str(LEDGER_DIR) not in sys.path:
        sys.path.insert(0, str(LEDGER_DIR))
        break

try:
    from session_intent_ledger import (  # noqa: E402
        DEFAULT_ROOT,
        build_input_observation,
        record_turn,
        stage_native_input,
        resolve_session_id,
    )
    _LEDGER_SCRIPT = Path(sys.modules['session_intent_ledger'].__file__).resolve()
    _LEDGER_IMPORT_BROKEN = False
except ImportError as _ledger_exc:
    # ImportError naming the ledger module itself means it is genuinely absent (skill uninstalled, mid-reinstall, or relocated) -> degrade as "unavailable". An ImportError naming a DIFFERENT module means the ledger is present but a transitive dependency is broken -> flag as broken, not absent. Either way degrade gracefully instead of crashing at import time.
    DEFAULT_ROOT = ".tmp/session-intent"
    build_input_observation = None
    record_turn = None
    stage_native_input = None
    resolve_session_id = None
    _LEDGER_IMPORT_BROKEN = getattr(_ledger_exc, "name", None) not in (None, "session_intent_ledger")
except Exception:
    # Present but broken at import (syntax error, a failing transitive dependency). Still degrade non-blockingly, but flag it as broken so main() reports a distinct message instead of silently labelling a real defect "unavailable".
    DEFAULT_ROOT = ".tmp/session-intent"
    build_input_observation = None
    record_turn = None
    stage_native_input = None
    resolve_session_id = None
    _LEDGER_IMPORT_BROKEN = True


TERMINAL_ROUTING_INSTRUCTION = reminder_text('terminal-routing')


def terminal_routing_instruction(root: Path, platform: str, session_id: str,
                                 input_event_id: str | None) -> str:
    """Deliver once within an exact native input; never cache a routing decision."""
    if not input_event_id or not session_id or session_id == "unknown":
        return TERMINAL_ROUTING_INSTRUCTION
    try:
        from session_check_cache import observe_check
        binding = {"input_event_id": input_event_id}
        delivery = observe_check(root, platform, session_id, "hook-terminal-routing", Path(__file__),
                                 lambda: binding, validate=lambda value: value == binding)
        if delivery["status"] == "reused":
            return ""
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        pass
    return TERMINAL_ROUTING_INSTRUCTION


DEFAULT_INTERNAL = reminder_text('session-intent')
LEDGER_UNAVAILABLE_DEGRADE = 'The intake hook cannot load the ledger dependency. The current-session assistant must not claim intake was recorded or substitute another ledger. Authorized ledger repair and a tool-free explanation remain possible.'
LEDGER_WRITE_FAILED_DEGRADE = 'The intake hook could not write this input to the ledger. The current-session assistant must not claim persistence or use an old input binding. Authorized ledger repair and a tool-free explanation remain possible.'
LEDGER_BROKEN_DEGRADE = 'The ledger module exists but the intake hook could not load it. The current-session assistant must not claim intake was recorded or substitute another ledger. Authorized module repair and a tool-free explanation remain possible.'
DEGRADE_MARKER_FILE = "ledger-degraded.json"
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.=-]+")


def _safe_component(value: Any) -> str:
    # The router imports this normalizer so both hooks address the same degrade marker.
    text = str(value or "unknown").strip()
    text = _SAFE_COMPONENT_RE.sub("-", text)
    text = re.sub(r"^[.-]+|[.-]+$", "", text)
    return text or "unknown"


def bound_session_identity(platform: str, payload: dict[str, Any],
                           env: dict[str, str] | None = None) -> str | None:
    """Exact host identity shared with the reminder, even if the ledger is broken."""
    environment = os.environ if env is None else env
    if not isinstance(platform, str) or not platform or _safe_component(platform) != platform or len(platform) > 120:
        return None
    for candidate in (
        payload.get("session_id"),
        payload.get("sessionId"),
        payload.get("conversation_id"),
        payload.get("thread_id"),
        environment.get("CODEX_THREAD_ID") if platform == "codex" else None,
        environment.get("GHOST_ALICE_SESSION_ID"),
    ):
        if candidate in (None, ""):
            continue
        if not isinstance(candidate, str) or len(candidate) > 120 or _safe_component(candidate) != candidate:
            return None
        return candidate
    return None


def _degrade_marker_path(root: Path, platform: str, payload: dict[str, Any]) -> Path | None:
    session = bound_session_identity(platform, payload)
    return root / platform / session / DEGRADE_MARKER_FILE if session else None


def _write_degrade_marker(root: Path, platform: str, payload: dict[str, Any], reason: str) -> None:
    # Durable, ledger-independent record that THIS turn's input was NOT observed, so freshness consumers (task-router reminder and input lineage checks) can fail closed instead of riding a stale anchor. Best-effort: the audit marker must never break the hook itself.
    try:
        path = _degrade_marker_path(root, platform, payload)
        if path is None:
            return
        install_destination.assert_mutation_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": "session-intent-degrade.v1",
                    "reason": reason,
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        return


def _clear_degrade_marker(root: Path, platform: str, payload: dict[str, Any]) -> None:
    try:
        path = _degrade_marker_path(root, platform, payload)
        if path is not None:
            path.unlink()
    except Exception:
        return


def read_payload() -> dict[str, Any]:
    # Hook runners send JSON as UTF-8. Read raw bytes so the Windows console code page cannot turn non-ASCII input into lone surrogate characters.
    buffer = getattr(sys.stdin, "buffer", None)
    try:
        data = buffer.read() if buffer is not None else sys.stdin.read()
    except OSError:
        return {}
    raw = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested = first_text(value.get("text"), value.get("content"), value.get("prompt"))
            if nested:
                return nested
        if isinstance(value, list):
            nested = first_text(*value)
            if nested:
                return nested
    return ""


def extract_prompt(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, dict):
        message = message.get("content")
    return first_text(
        payload.get("prompt"),
        payload.get("user_prompt"),
        payload.get("userPrompt"),
        payload.get("input"),
        payload.get("text"),
        message,
        payload.get("content"),
    )


def native_context_capability(session_id: str, platform: str = 'codex') -> dict[str, str] | None:
    """Expose provider metadata, never permission or an adoption claim."""
    try:
        procedure = Path(__file__).resolve().with_name('context_lifecycle.md')
        if not procedure.is_file():
            return None
        if platform == 'claude':
            config = os.environ.get('GHOST_ALICE_CONTEXT_CONFIG')
            if not config or not Path(config).is_absolute():
                return None
            state = json.loads(Path(config).read_text())
            port, scope, home = state.get('port'), state.get('scope'), state.get('claude_dir')
            if (type(port) is not int or not 0 < port < 65536 or not isinstance(scope, str)
                    or not scope or not isinstance(home, str) or not Path(home).is_absolute()):
                return None
            headers = [line.partition(':') for line in os.environ.get('ANTHROPIC_CUSTOM_HEADERS', '').splitlines()]
            def values(name):
                return [value.strip() for key, separator, value in headers
                        if separator and key.strip().casefold() == name.casefold()]
            if (os.environ.get('ANTHROPIC_BASE_URL') != f'http://127.0.0.1:{port}/anthropic'
                    or values('X-Ghost-Context-Scope') != [scope]
                    or values('X-Ghost-Context-Session') != [session_id]):
                return None
            return dict(native_provider='anthropic-subscription-gateway', procedure=str(procedure), native_dir=home)
        if platform != 'codex':
            return None
        from native_transcript import codex_transcript_path
        from context_gateway_install import PROVIDER
        home = os.environ.get('GHOST_ALICE_CODEX_DIR') or os.environ.get('CODEX_HOME') or str(_home() / '.codex')
        path = codex_transcript_path(home, session_id)
        if path is None:
            return None
        with Path(path).open() as stream:
            metadata = json.loads(stream.readline())
        payload = metadata.get('payload', {})
        if (metadata.get('type') == 'session_meta' and payload.get('id') == session_id
                and payload.get('model_provider') == PROVIDER and procedure.is_file()):
            return {'native_provider': PROVIDER, 'procedure': str(procedure), 'native_dir': str(home)}
    except (ImportError, OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def observation_receipt(paths: dict[str, Path], observation: dict[str, Any] | None) -> str:
    """Expose canonical intake identity only after native actor reconciliation."""
    state_path = Path(paths["state"]).resolve()
    events_path = Path(paths["events"]).resolve()
    receipt = {
        "schema_version": "session-intent-observation-receipt.v1" if observation else "session-intent-transport-receipt.v1",
        "intake_status": "observed" if observation else "pending-native-origin",
        "ledger_root": str(state_path.parents[2]),
        "platform": state_path.parents[1].name,
        "session_id": state_path.parent.name,
        "state_path": str(state_path),
        "events_path": str(events_path),
        "database_path": str(state_path.parents[2] / "ghost-state.sqlite3"),
        "storage_backend": "sqlite",
    }
    if observation is not None:
        receipt["input_event_id"] = observation["input_event_id"]
    capability = native_context_capability(receipt['session_id'], receipt['platform'])
    if capability is not None:
        receipt['context_selection'] = capability
    return (
        "\n[session-intent-receipt]\n"
        + json.dumps(receipt, ensure_ascii=True, separators=(",", ":"))
        + "\n[/session-intent-receipt]\n"
    )


def render_payload(output_format: str, message: str, ledger_root: Path, *, routine: bool = False) -> str:
    if output_format == "json":
        # systemMessage is a host UI warning, not the model context channel.
        body: dict[str, object] = {
            "continue": True,
            "systemMessage": message,
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": message,
            },
        }
        if routine:
            # A recorded observation is routine for the user screen; the model context above is unchanged.
            body["ghostAliceSurface"] = "routine"
        return json.dumps(body, ensure_ascii=False)
    return "\n".join([
        f"Internal instruction: {message}",
        "User: Session intent tracking does not store raw prompts.",
        f"Tech: Configured ledger root: {ledger_root}.",
        "",
    ])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record session intent guard hook event.")
    parser.add_argument("--platform", default="codex")
    parser.add_argument("--hook", default="session-intent")
    parser.add_argument("--context", default="prompt_submit")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--internal-b64", default=None)
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="session intent ledger root")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        override = instruction_override(args.internal_b64)
    except (ValueError, UnicodeError):
        parser.error('Invalid custom instruction encoding')
    message = override or ""
    custom_instruction = (override + "\n") if override is not None else ""

    payload = read_payload()
    ledger_root = Path(args.root).expanduser()
    routine = False
    terminal = TERMINAL_ROUTING_INSTRUCTION
    try:
        ledger_available = all(
            callable(func)
            for func in (resolve_session_id, build_input_observation, record_turn)
        )
        if not ledger_available:
            # Distinguish a genuinely absent ledger from one that is installed but broken at import, so a real defect is not silently mislabeled "unavailable". Both remain non-blocking (rc 0). A BROKEN ledger also leaves a durable degrade marker so freshness consumers fail closed instead of riding the frozen lineage anchor; an ABSENT ledger is the documented baseline and stays marker-free.
            if _LEDGER_IMPORT_BROKEN:
                _write_degrade_marker(ledger_root, args.platform, payload, "ledger-broken")
            message = message + " " + (
                LEDGER_BROKEN_DEGRADE if _LEDGER_IMPORT_BROKEN else LEDGER_UNAVAILABLE_DEGRADE
            )
        else:
            session_id = resolve_session_id(
                root=ledger_root,
                platform=args.platform,
                payload=payload,
                env=os.environ,
                for_write=True,
            )
            prompt = extract_prompt(payload)
            if prompt and args.platform == 'claude':
                paths = stage_native_input(root=ledger_root, platform=args.platform, session_id=session_id,
                    raw_input=prompt, prompt_id=payload.get('prompt_id'),
                    transcript_path=payload.get('transcript_path') or payload.get('transcriptPath'))
                _clear_degrade_marker(ledger_root, args.platform, payload)
                message = custom_instruction + observation_receipt(paths, None)
                sys.stdout.write(render_payload(args.format, message, ledger_root, routine=True)
                                 + ('\n' if args.format == 'json' else ''))
                return 0
            if prompt:
                observation = build_input_observation(
                    platform=args.platform,
                    session_id=session_id,
                    raw_user_input=prompt,
                )
                paths = record_turn(
                    root=ledger_root,
                    platform=args.platform,
                    session_id=session_id,
                    raw_user_input=prompt,
                    intent_delta=None,
                    source="hook",
                    observation=observation,
                )
                _clear_degrade_marker(ledger_root, args.platform, payload)
                message = custom_instruction + observation_receipt(paths, observation)
                routine = True
                terminal = ""
    except Exception:
        _write_degrade_marker(ledger_root, args.platform, payload, "ledger-write-failed")
        message = message + " " + LEDGER_WRITE_FAILED_DEGRADE

    message = terminal + "\n" + message if terminal else message
    sys.stdout.write(render_payload(args.format, message, ledger_root, routine=routine))
    if args.format == "json":
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
