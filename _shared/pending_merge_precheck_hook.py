#!/usr/bin/env python3
"""Session-start pending-merge precheck hook.

At session start this hook checks the current platform's pending-merge manifest and reports the result
on the user's screen; it adds nothing to the model context. merge-companion re-checks the manifest
itself (merge-companion/SKILL.md#entry-and-activation); no input hook repeats this check. A manifest
that keeps changing during the check is reported as an unfinished check; the hook never blocks the host.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from merge_companion_messages import render_pending_merge_message
from hook_input import parse_json_object, read_stdin_utf8
import pending_manifest
import state_retention
from session_check_cache import observe_check, UnstableCheckTarget
from session_intent_analyzer_hook import bound_session_identity


def _pending_line(platform: str, count: int) -> str:
    return (
        f"merge-companion precheck: ~/.ghost-alice/pending-merges/{platform}/manifest.json "
        f"has {count} undecided entr{'y' if count == 1 else 'ies'}. "
        "Apply merge-companion before changing affected skills or when the human user requests backup review. Unrelated read-only work may continue; a deferral that the human user states explicitly leaves entries undecided."
    )


def _unstable_line(platform: str) -> str:
    return (
        f"merge-companion precheck: ~/.ghost-alice/pending-merges/{platform}/manifest.json kept changing "
        "while this hook checked it, so the check did not finish. Re-check it before changing an installed skill."
    )


def _clean_contract_line(platform: str) -> str:
    return (
        f"merge-companion-precheck: clean (hook-verified) at session start. "
        f"This hook checked ~/.ghost-alice/pending-merges/{platform}/manifest.json; "
        "re-check it before changing an installed skill."
    )


def _read_hook_input() -> dict[str, Any]:
    try:
        return parse_json_object(read_stdin_utf8())
    except Exception:
        return {}


def _text_payload(platform: str, result: dict | None = None) -> str:
    value = pending_manifest.check_pending(pending_manifest.manifest_path(platform)) if result is None else result["value"]
    count = value["undecided_count"]
    if count:
        if result is not None and result["status"] == "reused":
            return f"merge-companion precheck: pending={count} (unchanged; session check reused)."
        return "\n".join([_pending_line(platform, count), render_pending_merge_message("session_start")])

    return _clean_contract_line(platform)


def _json_payload(platform: str, result: dict | None = None) -> str:
    value = pending_manifest.check_pending(pending_manifest.manifest_path(platform)) if result is None else result["value"]
    count = value["undecided_count"]
    receipt = {"platform": platform, "manifest_path": str(pending_manifest.manifest_path(platform)),
               "manifest_state": value["manifest_state"], "undecided_count": count}
    if result is not None:
        receipt.update({"status": result["status"], "checked_at": result["checked_at"]})
    body: dict[str, Any] = {"continue": True, "ghostAlicePendingMergeCheck": receipt}
    if count:
        message = (f"merge-companion precheck: pending={count} (unchanged; session check reused)."
                   if result is not None and result["status"] == "reused" else _pending_line(platform, count))
        body['systemMessage'] = message
        return json.dumps(body, ensure_ascii=False)
    message = _clean_contract_line(platform)
    # A clean pass is routine.
    body.update(systemMessage=message, ghostAliceSurface='routine')
    return json.dumps(body, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True)
    parser.add_argument("--format", required=True, choices=["text", "json"])
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()

    input_payload = _read_hook_input()
    result = None
    session_id = bound_session_identity(args.platform, input_payload)
    try:
        if args.root is not None:
            # Cache only a successful observation and count, never raw manifest entries.
            path = pending_manifest.manifest_path(args.platform)
            result = observe_check(args.root, args.platform, session_id or "unknown", "pending-merges", path,
                                   lambda: pending_manifest.check_pending(path), validate=pending_manifest.valid_check)
        output = _json_payload(args.platform, result) if args.format == "json" else _text_payload(args.platform, result)
    except (OSError, ValueError, RecursionError) as error:
        # A failed observation cannot supply a clean receipt. It does not stop the host.
        if isinstance(error, UnstableCheckTarget):
            message = _unstable_line(args.platform)
            output = json.dumps({"continue": True, "systemMessage": message}, ensure_ascii=False) if args.format == "json" else message
        else:
            state_retention.record_failure("pending-merges-state", pending_manifest.manifest_path(args.platform), error)
            output = json.dumps({"continue": True}) if args.format == "json" else ""
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
