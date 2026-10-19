"""The one fingerprint of installed content and the one comparison of an install record entry with the disk.

A file's fingerprint is `sha256:<hex>` of its bytes. A link has no fingerprint; it is
recorded by the path it points to. A folder's fingerprint is the fingerprint of every
file under it, keyed by its path relative to the folder, without caches, generated
files and the ownership marker an earlier installer keeps inside copied folders.
`installed_payload` records these values and `compare` reads them back.

Dependencies: Python standard library.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

PREFIX = "sha256:"
# The ownership marker earlier installers write into each copied folder. It changes on
# every install and is the installer's own bookkeeping, not installed content.
MARKER_FILENAME = ".ghost-alice-install.json"
EXCLUDED_DIR_NAMES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".git"})
EXCLUDED_FILE_NAMES = frozenset({".DS_Store", MARKER_FILENAME})
EXCLUDED_FILE_SUFFIXES = frozenset({".pyc"})


def file_digest(path: Path | str) -> str:
    """`sha256:<hex>` of the file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return PREFIX + digest.hexdigest()


def link_target(path: Path | str) -> str:
    """The path a link points to, as the link stores it."""
    return os.readlink(path)


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def tree(path: Path | str) -> dict[str, str]:
    """`{relative posix path: file_digest}` for every regular file under the folder.

    Excluded folder names, file names and suffixes are left out; a linked file is left
    out and a linked folder is not entered, so the result describes only the bytes that
    live in this folder. A regular file is its own one entry, `{".": file_digest}`.
    """
    root = Path(path)
    if root.is_file() and not _is_link(root):
        return {".": file_digest(root)}
    files: dict[str, str] = {}
    for current, dirnames, filenames in os.walk(root):
        folder = Path(current)
        dirnames[:] = sorted(name for name in dirnames
                             if name not in EXCLUDED_DIR_NAMES and not _is_link(folder / name))
        for name in sorted(filenames):
            file = folder / name
            if name in EXCLUDED_FILE_NAMES or file.suffix in EXCLUDED_FILE_SUFFIXES or _is_link(file):
                continue
            if file.is_file():
                files[file.relative_to(root).as_posix()] = file_digest(file)
    return files


def _is_generated(name: str, is_dir: bool) -> bool:
    if is_dir:
        return name in EXCLUDED_DIR_NAMES
    return name in EXCLUDED_FILE_NAMES or Path(name).suffix in EXCLUDED_FILE_SUFFIXES


def state(path: Path | str) -> dict | None:
    """Everything at a path except generated files, so a restore can tell whether the path still holds
    exactly what it saw.

    None when nothing is there; `{"link": target}` for a link (not followed); `{"file": digest}` for a
    file; `{"folder": {relative path: entry}}` for a folder, where an entry is the digest of a file,
    `link:<target>` for a link (not entered), `folder` for every folder (an empty one too) and `other`
    for anything else. Caches, generated files and the ownership marker are left out, as in `tree`.
    """
    root = Path(path)
    if not os.path.lexists(root):
        return None
    if _is_link(root):
        return {"link": link_target(root)}
    if root.is_file():
        return {"file": file_digest(root)}
    if not root.is_dir():
        return {"other": root.lstat().st_mode}
    entries: dict[str, str] = {}
    for current, dirnames, filenames in os.walk(root):
        folder = Path(current)
        kept = []
        for name in sorted(dirnames):
            child = folder / name
            if _is_generated(name, True):
                continue
            rel = child.relative_to(root).as_posix()
            if _is_link(child):
                entries[rel] = "link:" + link_target(child)
            else:
                entries[rel] = "folder"
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            child = folder / name
            if _is_generated(name, False):
                continue
            rel = child.relative_to(root).as_posix()
            if _is_link(child):
                entries[rel] = "link:" + link_target(child)
            elif child.is_file():
                entries[rel] = file_digest(child)
            else:
                entries[rel] = "other"
    return {"folder": dict(sorted(entries.items()))}


def _current_files(path: Path) -> dict[str, str]:
    """The files a copy has now: none when the path is absent or a link."""
    if _is_link(path) or not (path.is_dir() or path.is_file()):
        return {}
    return tree(path)


def _current_link_target(path: Path) -> str | None:
    try:
        return link_target(path)
    except OSError:
        return None


def compare(record_target: dict, path: Path | str) -> dict:
    """Differences between one install record entry and the path it names.

    A link entry differs when the path is not a link to its recorded `link_target`.
    A copy entry lists the recorded files that are `missing` or `changed` and the
    current files the record does not name (`extra`), each sorted. A copy entry without
    `files` (its baseline is unknown) has nothing to compare against.
    """
    path = Path(path)
    result = {"missing": [], "changed": [], "extra": [], "link_target_differs": False}
    if record_target.get("mode") == "link":
        result["link_target_differs"] = _current_link_target(path) != record_target.get("link_target")
        return result
    recorded = record_target.get("files")
    if recorded is None:
        return result
    current = _current_files(path)
    result["missing"] = sorted(set(recorded) - set(current))
    result["changed"] = sorted(rel for rel in set(recorded) & set(current) if recorded[rel] != current[rel])
    result["extra"] = sorted(set(current) - set(recorded))
    return result
