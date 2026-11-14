"""Shared installed-skill file walker for snapshot and diff collection.

Dependencies: Python 3.11+ standard library; _shared installed-payload and host definitions for recorded host scope.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Iterable

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".git",
}

INSTALL_MARKER_NAME = ".ghost-alice-install.json"
EXCLUDED_FILE_NAMES = {INSTALL_MARKER_NAME, ".DS_Store"}
EXCLUDED_FILE_SUFFIXES = {".pyc"}


def _is_excluded_file(path: Path) -> bool:
    return path.name in EXCLUDED_FILE_NAMES or path.suffix in EXCLUDED_FILE_SUFFIXES


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            if is_junction():
                return True
        except OSError:
            return False
    try:
        attrs = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def copy_comparison_scope(skills_dir: Path, platform: str, record_root: Path | None,
                          targets: Iterable[str] = ()) -> tuple[dict, set[Path]]:
    """Select this host's published baselines and other hosts' excluded roots.

    Unknown attribution keeps the existing marker-based comparison. An explicit
    replacement still inspects another host's copy before overwriting its files.
    """
    if record_root is None:
        return {}, set()
    shared = str(Path(__file__).resolve().parents[2] / '_shared')
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import installed_payload
    from hook_definitions import SUPPORTED_PLATFORMS
    try:
        entries = (installed_payload.read(record_root) or {}).get('targets', {})
    except installed_payload.RecordError as exc:
        print(f'[WARN] Cannot read published baseline; preserving snapshot comparison: {exc}')
        return {}, set()
    published, excluded = {}, set()
    replaced = set(targets)
    for destination, entry in entries.items():
        hosts = entry.get('used_by') if isinstance(entry, dict) else None
        if (not isinstance(hosts, list) or not hosts
                or any(not isinstance(host, str) or host not in SUPPORTED_PLATFORMS for host in hosts)):
            continue
        if platform in hosts:
            published[destination] = entry
        else:
            path = Path(destination)
            if (path.parent == skills_dir and entry.get('name') == path.name
                    and entry.get('kind') in {'skill', 'support'} and entry.get('mode') == 'copy'
                    and path.name not in replaced):
                excluded.add(path)
    return published, excluded


def walk_installed_copy_files(skills_dir: Path, targets: Iterable[str] = (), *,
                              excluded_roots: Iterable[Path] = ()) -> list[Path]:
    """Return files in Ghost-ALICE copy-mode skill roots, excluding generated/cache files.

    A root with the install marker holds Ghost-ALICE files that the installer replaces. So does a
    root the current install names in targets, even after its marker disappears. Any other root,
    such as the Claude app's synced folder or a skill outside the current install's targets, is never replaced, so its files
    never enter the pending-merge store. Symlink and junction roots point at the source checkout,
    where git tracks local changes without recording who made them.
    """
    replaced = set(targets)
    excluded = set(excluded_roots)
    files: list[Path] = []
    if not skills_dir.is_dir():
        return files

    for skill_root in sorted(skills_dir.iterdir()):
        if skill_root in excluded:
            continue
        if _is_reparse_point(skill_root) or not skill_root.is_dir():
            continue
        if skill_root.name not in replaced and not (skill_root / INSTALL_MARKER_NAME).is_file():
            continue

        for root, dirs, filenames in os.walk(skill_root):
            root_path = Path(root)
            dirs[:] = [
                dirname
                for dirname in dirs
                if dirname not in EXCLUDED_DIR_NAMES and not _is_reparse_point(root_path / dirname)
            ]

            for filename in sorted(filenames):
                path = root_path / filename
                if _is_reparse_point(path) or _is_excluded_file(path):
                    continue
                if path.is_file():
                    files.append(path)

    return files
