"""manifest_io. Merge-companion manifest CRUD with atomic write and a file lock.

Dependencies: Python 3.11+ standard library and the Core helper `pending_manifest` in `_shared`.

Manifest-missing-or-invalid silent-pass rule: missing, empty, or unparsable manifest -> silent pass with an empty dict.
Atomic-manifest-write guarantee: atomic write (temporary file + rename) plus the lock on the sibling .lock file protects
against race and crash cases. A lock held past `MANIFEST_LOCK_WAIT_S` raises `file_lock.LockTimeout` to the caller, and
a lock the file system refuses raises its `OSError`.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[2] / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
import pending_manifest  # noqa: E402
from pending_manifest import write_manifest  # noqa: E402

# Longer than the log limit: a timeout here aborts the install, while a holder normally keeps the lock only for one
# read and one write of the manifest.
MANIFEST_LOCK_WAIT_S = pending_manifest.MANIFEST_LOCK_WAIT_S


class ManifestError(Exception):
    """Exception raised during manifest operations."""


def read_manifest(path: Path) -> dict:
    """Read a manifest and return a dict.

    Return {"entries": []} when the file is missing or parse fails
    (manifest-missing-or-invalid silent-pass rule). When a corrupt file is found,
    back it up to .corrupt-bak and print a warning to stderr.
    """
    if not path.exists():
        return {"entries": []}
    try:
        data = pending_manifest.read_json(path)
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + ".corrupt-bak")
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        print(
            f"[manifest_io] WARN: failed to parse {path}. Backed it up to {backup}.",
            file=sys.stderr,
        )
        return {"entries": []}
    except OSError:
        return {"entries": []}
    if not isinstance(data, dict) or "entries" not in data:
        return {"entries": []}
    return data


def append_entry(path: Path, entry: dict) -> None:
    """Append an entry to the manifest. flock protects concurrent writes."""
    def change(data):
        data.setdefault("version", 1)
        data["entries"].append(entry)
        return True
    pending_manifest.update_manifest(path, read_manifest, change, wait=MANIFEST_LOCK_WAIT_S)


def append_entry_if_absent(path: Path, entry: dict, unique_keys: list[str]) -> bool:
    """Append only when an identical pending entry is absent.

    The return value says whether a new entry was actually added.
    """
    def change(data):
        data.setdefault("version", 1)
        entries = data.setdefault("entries", [])
        for existing in entries:
            if not pending_manifest.is_pending(existing):
                continue
            if all(existing.get(key) == entry.get(key) for key in unique_keys):
                return False
        entries.append(entry)
        return True
    return pending_manifest.update_manifest(path, read_manifest, change, wait=MANIFEST_LOCK_WAIT_S)


def mark_decided(path: Path, entry_id: str, decision: str) -> None:
    """Record the caller's supplied merge, discard or defer outcome.

    The response model records the human user's choice after carrying it out;
    a caller the human user authorized may also record deferral in a non-interactive run.
    This function checks neither who chose nor who called it, and the entry records neither.
    decision must be one of "merged" | "discarded" | "deferred".
    Raises ManifestError when entry_id is missing or ambiguous in old history.
    """
    if decision not in {"merged", "discarded", "deferred"}:
        raise ManifestError(f"unknown decision: {decision}")
    def change(data):
        matches = [entry for entry in data["entries"] if entry["id"] == entry_id]
        if not matches:
            raise ManifestError(f"entry not found: {entry_id}")
        if len(matches) != 1:
            pending = [entry for entry in matches if pending_manifest.is_pending(entry)]
            if len(pending) != 1:
                raise ManifestError(f"ambiguous entry id: {entry_id}")
            matches = pending
        matches[0]["decided"] = (decision != "deferred")
        matches[0]["decision"] = decision
        return True
    pending_manifest.update_manifest(path, read_manifest, change, wait=MANIFEST_LOCK_WAIT_S)


def list_pending(path: Path) -> list[dict]:
    """Return entries where decided=False, including recorded deferrals.

    The outcome keys do not identify who wrote them or prove that the human user chose.
    """
    data = read_manifest(path)
    return [e for e in data["entries"] if pending_manifest.is_pending(e)]
