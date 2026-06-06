#!/usr/bin/env python3
"""Validate compact-handoff blocks without modifying the workspace.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = [
    "objective",
    "completed",
    "in-progress",
    "next-step",
    "files-changed",
    "tests-run",
    "forbidden-surface",
    "rollback",
]
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9._-]{20,}"),
]
LIVE_CONFIG_PATTERNS = [
    "/.claude/settings.json",
    "/.codex/hooks.json",
    "/.codex/config.toml",
    "/.agents/skills/",
]
BLANK_VALUES = {"", "n/a", "na", "none", "null", "-", "not set"}


def read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).expanduser().read_text(encoding="utf-8")


def parse_handoff(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    in_block = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "[compact-handoff]":
            in_block = True
            continue
        if in_block and line.startswith("[") and line.endswith("]"):
            break
        if not in_block or not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        normalized_key = key.strip().lower()
        if normalized_key:
            fields[normalized_key] = value.strip()
    return fields


def is_blank(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip().lower() in BLANK_VALUES


def contains_secret_like_value(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def touches_live_config(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return any(pattern in normalized for pattern in LIVE_CONFIG_PATTERNS)


def validate_text(text: str) -> dict[str, Any]:
    fields = parse_handoff(text)
    errors: list[str] = []
    warnings: list[str] = []

    if not fields:
        errors.append("missing:block")

    for field in REQUIRED_FIELDS:
        if is_blank(fields.get(field)):
            errors.append(f"missing:{field}")

    if contains_secret_like_value(text):
        errors.append("secret-like-value")

    files_changed = fields.get("files-changed", "")
    rollback = fields.get("rollback", "")
    if touches_live_config(files_changed) and is_blank(rollback):
        errors.append("rollback-required-for-live-config")

    tests_run = fields.get("tests-run", "")
    if tests_run.strip().lower() in {"not run", "not-run", "pending"}:
        warnings.append("tests-not-complete")

    errors = sorted(set(errors))
    warnings = sorted(set(warnings))
    return {
        "status": "fail" if errors else "pass",
        "field_count": len(fields),
        "present_fields": sorted(fields),
        "errors": errors,
        "warnings": warnings,
        "mode": "report-only",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"status: {report['status']}",
        f"field_count: {report['field_count']}",
    ]
    if report["errors"]:
        lines.append("errors:")
        lines.extend(f"- {error}" for error in report["errors"])
    if report["warnings"]:
        lines.append("warnings:")
        lines.extend(f"- {warning}" for warning in report["warnings"])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a compact-handoff block.")
    parser.add_argument("path", nargs="?", default="-", help="handoff text file, or stdin when omitted")
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = validate_text(read_input(args.path))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
