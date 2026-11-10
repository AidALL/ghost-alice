"""Shared content-hash helper for install-state targets and addon sidecars.

Both writers use the same ``content_hash`` calculation. Dependencies: Python
standard library.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# The Ghost-ALICE ownership marker (installer_assets.GHOST_ALICE_MARKER_FILENAME) carries a per-install installed_at timestamp, so it changes on every reinstall. It is our own bookkeeping, not addon content, and must be excluded from the content hash; otherwise a copy-mode target's recorded hash drifts from its live hash across reinstalls and falsely trips the same-addon drift gate.
_MANAGED_MARKER_FILENAME = ".ghost-alice-install.json"
_PYTHON_CACHE_DIR = "__pycache__"


def as_posix(path) -> str:
    return Path(path).as_posix()


def hash_target(path, install_mode: str, *, recorded_hash: str | None = None) -> str:
    """Hash an installed target the same way the install-state writer does.

    - symlink/junction: hash of ``link:<readlink target>`` (records the link, not
      the pointed-to tree).
    - missing / nonexistent: the literal string ``"missing"``.
    - file: sha256 of its bytes.
    - directory: ``tree-v2:`` plus sha256 over sorted, length-prefixed relative
      paths and fixed-length file digests. A reader supplies recorded_hash to
      select the historical algorithm; writers omit it and use tree-v2. Reading
      a v1 record preserves its historical comparison limits and never rewrites
      its baseline from the current disk.
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

    legacy = recorded_hash is not None and not recorded_hash.startswith("tree-v2:")
    digest = hashlib.sha256(b"" if legacy else b"Ghost-ALICE tree hash v2\0")
    for child in sorted(p for p in target.rglob("*") if p.is_file()):
        if child.name == _MANAGED_MARKER_FILENAME:
            continue  # our own marker (volatile timestamp), not addon content
        rel_path = child.relative_to(target)
        if _PYTHON_CACHE_DIR in rel_path.parts:
            continue  # runtime import cache, not installed addon content
        rel = rel_path.as_posix().encode("utf-8")
        if legacy:
            digest.update(rel + b"\0" + child.read_bytes() + b"\0")
        else:
            digest.update(len(rel).to_bytes(8, "big"))
            digest.update(rel)
            digest.update(hashlib.sha256(child.read_bytes()).digest())
    return ("" if legacy else "tree-v2:") + digest.hexdigest()
