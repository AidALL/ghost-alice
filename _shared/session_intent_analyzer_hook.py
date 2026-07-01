#!/usr/bin/env python3
"""UserPromptSubmit hook for the session-intent-analyzer ledger."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
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
            # "unavailable". Both remain non-blocking (rc 0).
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
    except Exception:
        message = message + " " + LEDGER_WRITE_FAILED_DEGRADE

    sys.stdout.write(render_payload(args.format, message, ledger_root))
    if args.format == "json":
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
