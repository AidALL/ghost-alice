#!/usr/bin/env python3
"""Session-start pending-merge precheck hook.

At session start, after resume, clear, compaction and fork, this hook checks the current platform's
pending-merge manifest and reports the result on the user's screen; it adds nothing to the model
context. An installer run can add entries during the session, so merge-companion re-checks the
manifest before work changes an installed skill; no input hook repeats this check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from merge_companion_messages import render_pending_merge_message
import install_destination
from session_check_cache import observe_check, UnstableCheckTarget
from session_intent_analyzer_hook import bound_session_identity


def _pending_manifest_path(platform: str) -> Path:
    return install_destination.ghost_root(_home()) / "pending-merges" / platform / "manifest.json"


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


def _pending_entries(platform: str) -> list[dict[str, Any]]:
    path = _pending_manifest_path(platform)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        return []
    return [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("decided") is False
    ]


def _pending_line(platform: str, count: int) -> str:
    return (
        f"merge-companion precheck: ~/.ghost-alice/pending-merges/{platform}/manifest.json "
        f"has {count} undecided entr{'y' if count == 1 else 'ies'}. "
        "Apply merge-companion before changing affected skills or when the user requests backup review. Unrelated read-only work may continue; an explicit defer leaves entries undecided."
    )


def _clean_contract_line(platform: str) -> str:
    return (
        f"merge-companion-precheck: clean (hook-verified) at session start. "
        f"This hook checked ~/.ghost-alice/pending-merges/{platform}/manifest.json; "
        "re-check it before changing an installed skill."
    )


def _read_hook_input() -> dict[str, Any]:
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _text_payload(platform: str, result: dict | None = None) -> str:
    count = len(_pending_entries(platform)) if result is None else result["value"]["undecided_count"]
    if count:
        if result is not None and result["status"] == "reused":
            return f"merge-companion precheck: pending={count} (unchanged; session check reused)."
        return "\n".join([_pending_line(platform, count), render_pending_merge_message("session_start")])

    return _clean_contract_line(platform)


def _json_payload(platform: str, result: dict | None = None) -> str:
    count = len(_pending_entries(platform)) if result is None else result["value"]["undecided_count"]
    receipt = {"platform": platform, "manifest_path": str(_pending_manifest_path(platform)), "undecided_count": count}
    if result is not None:
        receipt.update({"status": result["status"], "checked_at": result["checked_at"]})
    body: dict[str, Any] = {"continue": True, "ghostAlicePendingMergeCheck": receipt}
    if count:
        message = (f"merge-companion precheck: pending={count} (unchanged; session check reused)."
                   if result is not None and result["status"] == "reused" else _pending_line(platform, count))
        body['systemMessage'] = message
        return json.dumps(body, ensure_ascii=False)
    message = _clean_contract_line(platform)
    # A clean pass is routine: the hook runner keeps it off the user screen and in the strict log.
    body.update(systemMessage=message, ghostAliceSurface='routine')
    return json.dumps(body, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True)
    parser.add_argument("--format", required=True, choices=["text", "json"])
    parser.add_argument("--root", type=Path)
    args, _unknown = parser.parse_known_args()

    input_payload = _read_hook_input()
    result = None
    session_id = bound_session_identity(args.platform, input_payload)
    if args.root is not None:
        session_id = bound_session_identity(args.platform, input_payload) or "unknown"
        # Cache only normalized check metadata, not backup contents or raw manifest entries.
        try:
            result = observe_check(args.root, args.platform, session_id, "pending-merges", _pending_manifest_path(args.platform),
                                   lambda: {"undecided_count": len(_pending_entries(args.platform))},
                                   validate=lambda value: type(value.get("undecided_count")) is int and value["undecided_count"] >= 0)
        except UnstableCheckTarget as error:
            reason = f"merge-companion precheck: {error}"
            if args.format == "json":
                print(json.dumps({"decision": "block", "reason": reason}))
                return 0
            print(reason, file=sys.stderr)
            return 2
    if args.format == "json":
        print(_json_payload(args.platform, result))
    else:
        print(_text_payload(args.platform, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
