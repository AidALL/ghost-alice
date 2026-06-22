"""Shared content-hash helper for install-state targets and addon sidecars.

Extracted so both the install-state writer and the addon sidecar writer compute
``content_hash`` the same way. Pure standard library.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# The Ghost-ALICE ownership marker (installer_assets.GHOST_ALICE_MARKER_FILENAME)
# carries a per-install installed_at timestamp, so it changes on every reinstall.
# It is our own bookkeeping, not addon content, and must be excluded from the
# content hash; otherwise a copy-mode target's recorded hash drifts from its live
# hash across reinstalls and falsely trips the same-addon drift gate (review H1).
_MANAGED_MARKER_FILENAME = ".ghost-alice-install.json"
_PYTHON_CACHE_DIR = "__pycache__"


def as_posix(path) -> str:
    return Path(path).as_posix()


def hash_target(path, install_mode: str) -> str:
    """Hash an installed target the same way the install-state writer does.

    - symlink/junction: hash of ``link:<readlink target>`` (records the link, not
      the pointed-to tree).
    - missing / nonexistent: the literal string ``"missing"``.
    - file: sha256 of its bytes.
    - directory: sha256 over (relative path, bytes) of every file, sorted.
    """
    target = Path(path)
    if install_mode in {"symlink", "junction"}:
        try:
            link_target = os.readlink(target)
        except OSError:
            link_target = as_posix(path)
        return hashlib.sha256(f"link:{link_target}".encode("utf-8")).hexdigest()
    if install_mode == "missing" or not target.exists():
        return "missing"
    if target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()

    digest = hashlib.sha256()
    for child in sorted(p for p in target.rglob("*") if p.is_file()):
        if child.name == _MANAGED_MARKER_FILENAME:
            continue  # our own marker (volatile timestamp), not addon content
        rel_path = child.relative_to(target)
        if _PYTHON_CACHE_DIR in rel_path.parts:
            continue  # runtime import cache, not installed addon content
        rel = rel_path.as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
