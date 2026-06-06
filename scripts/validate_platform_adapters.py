#!/usr/bin/env python3
"""Validate platform adapter compliance records.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_IDS = {"claude", "codex", "terminal-only"}
REQUIRED_FIELDS = {
    "id",
    "state",
    "supported_assets",
    "unsupported_surfaces",
    "install_or_onramp",
    "verification_commands",
    "risk_notes",
    "last_verified_at",
    "owner",
    "source_docs",
}
VALID_STATES = {"native", "instruction-backed", "terminal-only"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_catalog(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_nonempty_string_list(value: object) -> bool:
    return isinstance(value, list) and all(is_nonempty_string(item) for item in value) and len(value) > 0


def validate_record(record: dict[str, Any], repo_root: Path) -> list[str]:
    errors: list[str] = []
    adapter_id = str(record.get("id", "<missing>"))
    missing = sorted(REQUIRED_FIELDS - set(record))
    for field in missing:
        errors.append(f"{adapter_id}: missing required field {field}")

    state = record.get("state")
    if state not in VALID_STATES:
        errors.append(f"{adapter_id}: invalid state {state!r}")
    if adapter_id == "codex" and state == "native":
        errors.append(f"{adapter_id}: native state is not allowed without verified native hook parity")
    if adapter_id == "terminal-only" and state != "terminal-only":
        errors.append("terminal-only: state must be terminal-only")

    for field in ("supported_assets", "verification_commands", "risk_notes", "source_docs"):
        if field in record and not is_nonempty_string_list(record[field]):
            errors.append(f"{adapter_id}: {field} must be a non-empty string list")
    if "unsupported_surfaces" in record:
        value = record["unsupported_surfaces"]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            errors.append(f"{adapter_id}: unsupported_surfaces must be a string list")

    for field in ("install_or_onramp", "owner"):
        if field in record and not is_nonempty_string(record[field]):
            errors.append(f"{adapter_id}: {field} must be a non-empty string")

    if "last_verified_at" in record and not (
        isinstance(record["last_verified_at"], str) and DATE_RE.match(record["last_verified_at"])
    ):
        errors.append(f"{adapter_id}: last_verified_at must be YYYY-MM-DD")

    if adapter_id == "codex":
        haystack = " ".join(
            str(item)
            for field in ("unsupported_surfaces", "risk_notes")
            for item in record.get(field, [])
        ).lower()
        if "hook" not in haystack or not any(term in haystack for term in ("evidence", "runtime", "smoke")):
            errors.append(f"{adapter_id}: must document hook evidence or runtime smoke surface before gate claims")
        if "event" not in haystack or "firing proof" not in haystack:
            errors.append(f"{adapter_id}: must distinguish hook event config from runtime firing proof")

    for source_doc in record.get("source_docs", []):
        if isinstance(source_doc, str) and not (repo_root / source_doc).is_file():
            errors.append(f"{adapter_id}: source_doc missing: {source_doc}")
    return errors


def validate_catalog(path: Path, repo_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        data = load_catalog(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: {exc.__class__.__name__}: {exc}"]

    records = data.get("adapters")
    if not isinstance(records, list):
        return ["top-level adapters must be a list"]
    ids = [record.get("id") for record in records if isinstance(record, dict)]
    if set(ids) != REQUIRED_IDS:
        errors.append(f"adapter ids must be {sorted(REQUIRED_IDS)}, got {sorted(str(item) for item in ids)}")
    if len(ids) != len(set(ids)):
        errors.append("adapter ids must be unique")

    for record in records:
        if not isinstance(record, dict):
            errors.append("adapter record must be an object")
            continue
        errors.extend(validate_record(record, repo_root))
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate platform adapter compliance records.")
    parser.add_argument("--catalog", default="skill-catalog/platform-adapters.json")
    parser.add_argument("--repo-root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    catalog = Path(args.catalog)
    if not catalog.is_absolute():
        catalog = repo_root / catalog
    errors = validate_catalog(catalog, repo_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 2
    print(f"OK: {catalog} ({len(REQUIRED_IDS)} adapters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
