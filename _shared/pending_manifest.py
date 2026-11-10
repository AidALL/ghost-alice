"""Pending-merge paths, facts and locked read-modify-write transactions.

Dependencies: Python 3.11+ standard library and Core atomic_write, file_lock and install_destination.
Readers receive parse and filesystem failures; each caller owns its recovery policy.
"""
from __future__ import annotations

import json
from pathlib import Path

import atomic_write
import file_lock
import install_destination

MANIFEST_LOCK_WAIT_S = 30.0


def manifest_path(platform: str, env=None) -> Path:
    return install_destination.ghost_root(env=env) / "pending-merges" / platform / "manifest.json"


def read_json(path: Path):
    """Read without changing the manifest or hiding an unavailable observation."""
    return json.loads(path.read_text(encoding="utf-8"))


def is_pending(entry: dict) -> bool:
    return not entry.get("decided", False)


def check_pending(path: Path) -> dict:
    """Observe an absent or parsed manifest; hook readers ignore non-object entries."""
    try:
        data = read_json(path)
    except FileNotFoundError:
        return {"manifest_state": "absent", "undecided_count": 0}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError("Pending manifest must contain an entries array")
    count = sum(is_pending(entry) for entry in data["entries"] if isinstance(entry, dict))
    return {"manifest_state": "parsed", "undecided_count": count}


def valid_check(value: object) -> bool:
    """A count-only result from the former reader cannot certify a successful read."""
    if not isinstance(value, dict) or type(value.get("undecided_count")) is not int:
        return False
    count = value["undecided_count"]
    return ((value.get("manifest_state") == "absent" and count == 0)
            or (value.get("manifest_state") == "parsed" and count >= 0))


def write_manifest(path: Path, data: dict) -> None:
    """Replace the manifest atomically; read-modify-write callers use update_manifest."""
    atomic_write.write_json(path, data)


def update_manifest(path: Path, reader, change, *, wait: float = MANIFEST_LOCK_WAIT_S, must_exist: bool = False) -> bool:
    """Hold the sibling lock through the caller's read, mutation and atomic write.

    The caller supplies its reader's recovery policy. A change returns False to leave
    the file untouched, or True to write. Lock timeout and refusal reach the caller.
    """
    if must_exist and not path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock.exclusive(path.with_suffix(path.suffix + ".lock"), wait=wait, must_lock=True):
        if must_exist and not path.exists():
            return False
        data = reader(path)
        if not change(data):
            return False
        write_manifest(path, data)
        return True
