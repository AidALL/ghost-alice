"""Shared installed-skill file walker for snapshot and diff collection.

Dependencies: Python 3.11+ standard library only.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".git",
}

EXCLUDED_FILE_NAMES = {".ghost-alice-install.json", ".DS_Store"}
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


def walk_user_files(skills_dir: Path) -> list[Path]:
    """Return user-owned installed skill files, excluding generated/cache files."""
    files: list[Path] = []
    if not skills_dir.is_dir():
        return files

    for skill_root in sorted(skills_dir.iterdir()):
        if _is_reparse_point(skill_root) or not skill_root.is_dir():
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
