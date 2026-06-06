"""manifest_io. Merge-companion manifest CRUD with atomic write and flock.

Dependencies: Python 3.11+ standard library only.

Manifest-missing-or-invalid silent-pass rule: missing, empty, or unparsable manifest -> silent pass with an empty dict.
Atomic-manifest-write guarantee: atomic write (tempfile + os.replace) plus POSIX flock protects against race and crash cases.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


class ManifestError(Exception):
    """Exception raised during manifest operations."""


# ---------------------------------------------------------------------------
# Internal: lock context manager
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _exclusive_lock(path: Path):
    """POSIX: apply fcntl.flock to a sibling .lock file. Windows: best-effort no-op."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    if not HAS_FCNTL:
        # Windows environment: proceed without a real lock.
        yield
        return
    f = open(lock_path, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


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
    """Write a manifest atomically with tempfile + fsync + os.replace.

    The existing file is preserved across partial writes or crashes
    (atomic-manifest-write guarantee).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".manifest.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
    """Update an entry to a decided state.

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
    """Return entries where decided=False."""
    data = read_manifest(path)
    return [e for e in data["entries"] if not e.get("decided", False)]
