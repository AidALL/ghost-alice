"""manifest_io. Merge-companion manifest CRUD with atomic write and a file lock.

Dependencies: Python 3.11+ standard library and the Core helpers `atomic_write` and `file_lock` in `_shared`.

Manifest-missing-or-invalid silent-pass rule: missing, empty, or unparsable manifest -> silent pass with an empty dict.
Atomic-manifest-write guarantee: atomic write (temporary file + rename) plus the lock on the sibling .lock file protects
against race and crash cases. A lock held past `MANIFEST_LOCK_WAIT_S` raises `file_lock.LockTimeout` to the caller, and
a lock the file system refuses raises its `OSError`.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[2] / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
import atomic_write  # noqa: E402
import file_lock  # noqa: E402

# Longer than the log limit: a timeout here aborts the install, while a holder normally keeps the lock only for one
# read and one write of the manifest.
MANIFEST_LOCK_WAIT_S = 30.0


class ManifestError(Exception):
    """Exception raised during manifest operations."""


# ---------------------------------------------------------------------------
# Internal: lock context manager
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _exclusive_lock(path: Path):
    """The lock on the sibling .lock file. The manifest itself is replaced by rename, so a lock on it would not guard
    the version that replaces it. An entry points at a backed-up external change, so a lock the file system refuses raises
    instead of letting the writer go on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock.exclusive(path.with_suffix(path.suffix + ".lock"), wait=MANIFEST_LOCK_WAIT_S, must_lock=True):
        yield


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_manifest(path: Path) -> dict:
    """Read a manifest and return a dict.

    Return {"entries": []} when the file is missing or parse fails
    (manifest-missing-or-invalid silent-pass rule). When a corrupt file is found,
    back it up to .corrupt-bak and print a warning to stderr.
    """
    if not path.exists():
        return {"entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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


def write_manifest(path: Path, data: dict) -> None:
    """Write a manifest atomically.

    The existing file is preserved across partial writes or crashes
    (atomic-manifest-write guarantee).
    """
    atomic_write.write_json(path, data)


def append_entry(path: Path, entry: dict) -> None:
    """Append an entry to the manifest. flock protects concurrent writes."""
    with _exclusive_lock(path):
        data = read_manifest(path)
        data.setdefault("version", 1)
        data["entries"].append(entry)
        write_manifest(path, data)


def append_entry_if_absent(path: Path, entry: dict, unique_keys: list[str]) -> bool:
    """Append only when an identical pending entry is absent.

    The return value says whether a new entry was actually added.
    """
    with _exclusive_lock(path):
        data = read_manifest(path)
        data.setdefault("version", 1)
        entries = data.setdefault("entries", [])
        for existing in entries:
            if existing.get("decided", False):
                continue
            if all(existing.get(key) == entry.get(key) for key in unique_keys):
                return False
        entries.append(entry)
        write_manifest(path, data)
        return True


def mark_decided(path: Path, entry_id: str, decision: str) -> None:
    """Record the caller's supplied merge, discard or defer outcome.

    The response model records the human user's choice after carrying it out;
    a caller the human user authorized may also record deferral in a non-interactive run.
    This function checks neither who chose nor who called it, and the entry records neither.
    decision must be one of "merged" | "discarded" | "deferred".
    Raises ManifestError when entry_id is not found.
    """
    if decision not in {"merged", "discarded", "deferred"}:
        raise ManifestError(f"unknown decision: {decision}")
    with _exclusive_lock(path):
        data = read_manifest(path)
        for e in data["entries"]:
            if e["id"] == entry_id:
                e["decided"] = (decision != "deferred")
                e["decision"] = decision
                write_manifest(path, data)
                return
        raise ManifestError(f"entry not found: {entry_id}")


def list_pending(path: Path) -> list[dict]:
    """Return entries where decided=False, including recorded deferrals.

    The outcome keys do not identify who wrote them or prove that the human user chose.
    """
    data = read_manifest(path)
    return [e for e in data["entries"] if not e.get("decided", False)]
