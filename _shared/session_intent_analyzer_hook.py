#!/usr/bin/env python3
"""UserPromptSubmit hook for the session-intent-analyzer ledger."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


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
    home = _home()
    candidates = [
        REPO_ROOT / "session-intent-analyzer" / "scripts",
        home / ".agents" / "skills" / "session-intent-analyzer" / "scripts",
        home / ".claude" / "skills" / "session-intent-analyzer" / "scripts",
    ]
    claude_home = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if claude_home:
        candidates.append(Path(claude_home) / "skills" / "session-intent-analyzer" / "scripts")
    return candidates


for LEDGER_DIR in _ledger_dir_candidates():
    if (LEDGER_DIR / "session_intent_ledger.py").is_file() and str(LEDGER_DIR) not in sys.path:
        sys.path.insert(0, str(LEDGER_DIR))
        break

try:
    from session_intent_ledger import (  # noqa: E402
        DEFAULT_ROOT,
        build_input_observation,
        record_turn,
        resolve_session_id,
    )
    _LEDGER_IMPORT_BROKEN = False
except ImportError as _ledger_exc:
    # ImportError naming the ledger module itself means it is genuinely absent
    # (skill uninstalled, mid-reinstall, or relocated) -> degrade as "unavailable".
    # An ImportError naming a DIFFERENT module means the ledger is present but a
    # transitive dependency is broken -> flag as broken, not absent. Either way
    # degrade gracefully instead of crashing at import time.
    DEFAULT_ROOT = ".tmp/session-intent"
    build_input_observation = None
    record_turn = None
    resolve_session_id = None
    _LEDGER_IMPORT_BROKEN = getattr(_ledger_exc, "name", None) not in (None, "session_intent_ledger")
except Exception:
    # Present but broken at import (syntax error, a failing transitive dependency).
    # Still degrade non-blockingly, but flag it as broken so main() reports a
    # distinct message instead of silently labelling a real defect "unavailable".
    DEFAULT_ROOT = ".tmp/session-intent"
    build_input_observation = None
    record_turn = None
    resolve_session_id = None
    _LEDGER_IMPORT_BROKEN = True


DEFAULT_INTERNAL = (
    "session-intent-analyzer: Observe every user input by recording an input digest, "
    "current-session pointer, and digest-only intake status. "
    "Agents add semantic summaries, constraints, and decisions only when intent materially changes. "
    "Never persist raw prompts, conversation text, tool outputs, system messages, or secrets. "
    "Use the ledger as context for skill-evolution and jailbreak-detector."
)
LEDGER_UNAVAILABLE_DEGRADE = (
    "Ledger dependency unavailable non-blockingly; continue without raw prompt persistence."
)
LEDGER_WRITE_FAILED_DEGRADE = (
    "Ledger write failed non-blockingly; continue without raw prompt persistence."
)
LEDGER_BROKEN_DEGRADE = (
    "Ledger module present but failed to load non-blockingly; "
    "continue without raw prompt persistence."
)
DEGRADE_MARKER_FILE = "ledger-degraded.json"
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.=-]+")


def _safe_component(value: Any) -> str:
    # Must stay character-identical to task_router_reminder_hook.safe_path_component:
    # the router rebuilds this path itself to find the degrade marker, so any
    # charset/normalization drift silently hides the marker (fail-open) -- the
    # exact cross-module-drift class this marker exists to prevent.
    text = str(value or "unknown").strip()
    text = _SAFE_COMPONENT_RE.sub("-", text)
    text = re.sub(r"^[.-]+|[.-]+$", "", text)
    return text or "unknown"


def _degrade_marker_path(root: Path, platform: str, payload: dict[str, Any]) -> Path:
    # Session component mirrors task_router_reminder_hook.resolve_session_id
    # candidate order so producer and consumer key the same directory.
    pointer_session = ""
    try:
        pointer = json.loads(
            (root / _safe_component(platform) / "current-session.json").read_text(encoding="utf-8")
        )
        if isinstance(pointer, dict):
            pointer_session = str(pointer.get("session_id") or "")
    except Exception:
        pointer_session = ""
    session = ""
    for candidate in (
        payload.get("session_id"),
        payload.get("sessionId"),
        payload.get("conversation_id"),
        payload.get("thread_id"),
        os.environ.get("GHOST_ALICE_SESSION_ID"),
        pointer_session,
    ):
        if candidate and str(candidate).strip():
            session = str(candidate)
            break
    return root / _safe_component(platform) / _safe_component(session) / DEGRADE_MARKER_FILE


def _write_degrade_marker(root: Path, platform: str, payload: dict[str, Any], reason: str) -> None:
    # Durable, ledger-independent record that THIS turn's input was NOT
    # observed, so freshness consumers (task-router reminder, gate lineage
    # checks) can fail closed instead of riding a stale anchor. Best-effort:
    # the audit marker must never break the hook itself.
    try:
        path = _degrade_marker_path(root, platform, payload)
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
        _degrade_marker_path(root, platform, payload).unlink()
    except Exception:
        return


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
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


def render_payload(output_format: str, message: str, ledger_root: Path) -> str:
    if output_format == "json":
        return json.dumps({"continue": True, "systemMessage": message}, ensure_ascii=False)
    return "\n".join([
        f"Internal instruction: {message}",
        "User: Session intent is tracked without storing raw prompts.",
        f"Tech: intent-state.json and intent-events.jsonl are updated under {ledger_root}.",
        "",
    ])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record session intent guard hook event.")
    parser.add_argument("--platform", default="codex")
    parser.add_argument("--hook", default="session-intent")
    parser.add_argument("--context", default="prompt_submit")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--internal-b64", default="")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="session intent ledger root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    message = DEFAULT_INTERNAL
    if args.internal_b64:
        try:
            message = base64.urlsafe_b64decode(args.internal_b64.encode("ascii")).decode("utf-8")
        except Exception:
            message = DEFAULT_INTERNAL

    payload = read_payload()
    ledger_root = Path(args.root).expanduser()
    try:
        ledger_available = all(
            callable(func)
            for func in (resolve_session_id, build_input_observation, record_turn)
        )
        if not ledger_available:
            # Distinguish a genuinely absent ledger from one that is installed but
            # broken at import, so a real defect is not silently mislabeled
            # "unavailable". Both remain non-blocking (rc 0). A BROKEN ledger also
            # leaves a durable degrade marker so freshness consumers fail closed
            # instead of riding the frozen lineage anchor; an ABSENT ledger is the
            # documented baseline and stays marker-free.
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
            )
            prompt = extract_prompt(payload)
            if prompt:
                observation = build_input_observation(
                    platform=args.platform,
                    session_id=session_id,
                    raw_user_input=prompt,
                )
                record_turn(
                    root=ledger_root,
                    platform=args.platform,
                    session_id=session_id,
                    raw_user_input=prompt,
                    intent_delta=None,
                    source="hook",
                    observation=observation,
                )
                _clear_degrade_marker(ledger_root, args.platform, payload)
    except Exception:
        _write_degrade_marker(ledger_root, args.platform, payload, "ledger-write-failed")
        message = message + " " + LEDGER_WRITE_FAILED_DEGRADE

    sys.stdout.write(render_payload(args.format, message, ledger_root))
    if args.format == "json":
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
