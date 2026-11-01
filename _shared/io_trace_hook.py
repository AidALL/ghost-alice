#!/usr/bin/env python3
"""PostToolUse audit hook for Ghost-ALICE.

Records tool operations for the strict audit. Malformed or incomplete hook
payloads return success so audit parsing does not block tool execution.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import install_destination
from typing import Any

from session_intent_analyzer_hook import bound_session_identity, _safe_component
import state_retention

# Best-effort neutral extraction of a shell row's operation + file target, so a reader can render it platform-neutrally (e.g. "read <path>") instead of leaking a per-runtime command string. The raw command is still stored in `pattern` (the audit log is never reduced); `op`/`path` are an additive structured view. Extraction is conservative and never executes input.
_SHELL_READ_VERBS = {"cat", "less", "more", "head", "tail", "type", "get-content", "gc"}
_SHELL_WRITE_VERBS = {"tee", "set-content", "out-file", "add-content"}
_SHELL_SEARCH_VERBS = {"grep", "rg", "egrep", "fgrep", "select-string", "findstr", "sls"}
_SHELL_UNSTRUCTURED_VERBS = {
    "rm",
    "del",
    "erase",
    "rmdir",
    "remove-item",
    "ri",
    "move-item",
    "mv",
    "copy-item",
    "cp",
    "curl",
    "wget",
    "invoke-webrequest",
    "iwr",
}
_QUOTED_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'')
_LITERAL_PATH_RE = re.compile(r"-(?:Literal)?Path\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))", re.IGNORECASE)
_PATHISH_RE = re.compile(r"(?<!:)(?:[A-Za-z]:[\\/]|[\\/]{1,2}|~[\\/]|\.{1,2}[\\/])[^\s\"']*")


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or bool(re.match(r"[A-Za-z]:", value))


def _first_pathish(command: str) -> str:
    match = _LITERAL_PATH_RE.search(command)
    if match:
        return match.group(1) or match.group(2) or match.group(3) or ""
    for quoted in _QUOTED_RE.finditer(command):
        value = quoted.group(1) or quoted.group(2) or ""
        if _looks_like_path(value):
            return value
    bare = _PATHISH_RE.search(command)
    return bare.group(0) if bare else ""


def _classify_shell_verb(token: str) -> str:
    verb = token.lower().strip("()[]{}|;&`'\"").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if verb in _SHELL_UNSTRUCTURED_VERBS:
        return "unstructured"
    if verb in _SHELL_READ_VERBS:
        return "read"
    if verb in _SHELL_WRITE_VERBS:
        return "write"
    if verb in _SHELL_SEARCH_VERBS:
        return "search"
    return ""


def _shell_op_and_path(command: str) -> tuple[str, str]:
    # Scan every token, not just the first: agents often lead with a variable assignment or a subshell (e.g. `$path = "..."; $lines = Get-Content $path`), so the recognized verb is not token[0]. Destructive/network/mixed shell commands stay unstructured so continuation messages preserve the raw command instead of misrepresenting it as a simple file read.
    ops: list[str] = []
    for token in command.split():
        classified = _classify_shell_verb(token)
        if classified:
            ops.append(classified)
    if "unstructured" in ops:
        return "", ""
    op = ops[0] if ops else ""
    path = _first_pathish(command)
    if not op or not path:
        return "", ""
    return op, path


_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$", re.MULTILINE)


def _patch_paths(patch: str) -> list[str]:
    """Return the files a Codex apply_patch body names, in order and without duplicates."""
    paths: list[str] = []
    for match in _PATCH_FILE_RE.finditer(patch):
        if match.group(1) not in paths:
            paths.append(match.group(1))
    return paths


def _home() -> Path:
    configured = os.environ.get("HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home()


def _log_file() -> Path:
    return install_destination.ghost_root(_home()) / "io-trace.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_stdin_utf8() -> str:
    """Read stdin as UTF-8 regardless of the platform default encoding.

    Claude Code delivers the hook payload as UTF-8 JSON. On Windows the default
    stdin encoding is cp949/cp1252, so a bare ``sys.stdin.read()`` decodes the
    bytes with the wrong codec and corrupts non-ASCII (Korean) text into mojibake
    and lone surrogates before it is ever logged. Reading the raw byte buffer and
    decoding UTF-8 explicitly avoids that; ``errors="replace"`` keeps a stray
    non-UTF-8 byte from crashing the audit hook.
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        try:
            data = buffer.read()
        except Exception:
            return ""
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)
    return sys.stdin.read()


def _safe_text(value: Any) -> str:
    text = str(value)
    return "".join(
        f"\\u{ord(character):04x}" if 0xD800 <= ord(character) <= 0xDFFF else character
        for character in text
    )


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = _safe_text(value)
    return text if text else fallback


def _platform(payload: dict[str, Any]) -> str:
    # Installer-supplied host context wins over incidental payload metadata.
    value = os.environ.get("GHOST_ALICE_PLATFORM") or payload.get("platform")
    if not isinstance(value, str) or not value or len(value) > 120 or _safe_component(value) != value:
        return "unknown"
    return value


def _extract(payload: dict[str, Any]) -> dict[str, str]:
    tool = _text(payload.get("tool_name") or payload.get("tool"), "unknown")
    platform = _platform(payload)
    session = bound_session_identity(platform, payload) or "unknown"
    tool_input = _as_dict(payload.get("tool_input"))
    path = "n/a"
    pattern = ""
    op = ""

    if tool in {"Read", "Edit", "Write"}:
        path = _text(tool_input.get("file_path"), "n/a")
    elif tool in {"Grep", "Glob"}:
        path = _text(tool_input.get("path"), "cwd")
        pattern = _text(tool_input.get("pattern"))
    elif tool == "Bash":
        command = _text(tool_input.get("command"))
        pattern = command[:200]
        op, extracted = _shell_op_and_path(command)
        if extracted:
            path = extracted
    elif tool == "apply_patch":
        paths = _patch_paths(_text(tool_input.get("command")))
        if paths:
            path = paths[0]
            # The file list is the audit record, so unlike a shell command it is never cut.
            pattern = "; ".join(paths)
    elif tool == "Agent":
        pattern = _text(tool_input.get("description"))
    elif tool == "Skill":
        pattern = _text(tool_input.get("skill"))
    elif tool in {"WebFetch", "WebSearch"}:
        path = _text(tool_input.get("url") or tool_input.get("query"), "n/a")
    else:
        path = _text(tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path"), "n/a")

    row = {"platform": platform, "session": session, "tool": tool, "path": path, "pattern": pattern}
    if op and path != "n/a":
        row["op"] = op
    return row


def _capture_governance_event(raw: str) -> None:
    script = Path(__file__).with_name("governance_events.py")
    if not script.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(script)],
            input=_safe_text(raw),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return


def _append_log(row: dict[str, str]) -> None:
    payload = {"ts": _utc_now(), **row}
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    state_retention.append("io-trace", _log_file(), line)


def main() -> int:
    try:
        raw = _read_stdin_utf8()
        _capture_governance_event(raw)
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        _append_log(_extract(payload))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
