#!/usr/bin/env python3
"""Validate compact-handoff blocks without modifying the workspace.

Dependencies: Python 3.11+ standard library and Ghost-ALICE `_shared/text_lines.py`,
which the installer places beside every installed skill and which owns line boundaries.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_SHARED_DIR = Path(__file__).resolve().parents[2] / "_shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))
from secret_patterns import SECRET_PATTERNS as SHARED_SECRET_PATTERNS
from text_lines import split_lines


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
    *SHARED_SECRET_PATTERNS,
    # A handoff field reads `- field: value`, so a secret can follow a colon here.
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9._-]{20,}"),
]
BLANK_VALUES = {"", "n/a", "na", "none", "null", "-", "not set"}


def read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).expanduser().read_text(encoding="utf-8")


def parse_handoff(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    in_block = False
    for raw_line in split_lines(text):
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
