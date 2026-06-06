"""Verify installed copy targets before recording postflight baselines.

Dependencies: Python 3.11+ standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

COPY_MODES = {"copy", "copy-fallback"}
LINK_MODES = {"symlink", "junction"}
EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".git",
}
EXCLUDED_FILE_NAMES = {".DS_Store"}
EXCLUDED_FILE_SUFFIXES = {".pyc"}


@dataclass(frozen=True)
class InstallTarget:
    name: str
    source_path: Path
    dest_path: Path
    install_mode: str


def _as_posix(path: Path) -> str:
    return path.as_posix()


def _resolved_path(path: Path) -> Path:
    return path.resolve(strict=False)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_symlink():
        return hashlib.sha256(f"link:{os.readlink(path)}".encode("utf-8")).hexdigest()
    if path.is_file():
        return hashlib.sha256(f"file:{_file_hash(path)}".encode("utf-8")).hexdigest()

    digest = hashlib.sha256()
    children: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(path):
        current = Path(dirpath)
        traversable_dirnames: list[str] = []
        for name in dirnames:
            child = current / name
            if name in EXCLUDED_DIR_NAMES:
                continue
            if child.is_symlink():
                children.append(child)
                continue
            traversable_dirnames.append(name)
        dirnames[:] = traversable_dirnames
        for filename in filenames:
            child = current / filename
            if filename in EXCLUDED_FILE_NAMES or child.suffix in EXCLUDED_FILE_SUFFIXES:
                continue
            children.append(child)
    children = sorted(children, key=lambda child: child.relative_to(path).as_posix())
    for child in children:
        rel = child.relative_to(path).as_posix()
        if child.is_symlink():
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0link\0")
            digest.update(os.readlink(child).encode("utf-8"))
            digest.update(b"\0")
        elif child.is_file():
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0file\0")
            digest.update(_file_hash(child).encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def validate_copy_targets(targets: list[InstallTarget]) -> list[dict]:
    failures: list[dict] = []
    for target in targets:
        if target.install_mode in LINK_MODES:
            source_exists = target.source_path.exists()
            dest_exists = target.dest_path.exists() or target.dest_path.is_symlink()
            expected_link_target = _resolved_path(target.source_path)
            actual_link_target = _resolved_path(target.dest_path)

            if source_exists and dest_exists and actual_link_target == expected_link_target:
                continue

            if not source_exists:
                reason = "missing-source"
            elif not dest_exists:
                reason = "missing-target"
            else:
                reason = "link-target-mismatch"

            failures.append(
                {
                    "target_name": target.name,
                    "source_path": _as_posix(target.source_path),
                    "dest_path": _as_posix(target.dest_path),
                    "install_mode": target.install_mode,
                    "expected_link_target": _as_posix(expected_link_target),
                    "actual_link_target": _as_posix(actual_link_target),
                    "reason": reason,
                }
            )
            continue

        if target.install_mode not in COPY_MODES:
            continue

        source_hash = tree_hash(target.source_path)
        dest_hash = tree_hash(target.dest_path)
        if source_hash == dest_hash:
            continue

        if source_hash == "missing":
            reason = "missing-source"
        elif dest_hash == "missing":
            reason = "missing-target"
        else:
            reason = "tree-hash-mismatch"

        failures.append(
            {
                "target_name": target.name,
                "source_path": _as_posix(target.source_path),
                "dest_path": _as_posix(target.dest_path),
                "install_mode": target.install_mode,
                "source_tree_hash": source_hash,
                "target_tree_hash": dest_hash,
                "reason": reason,
            }
        )
    return failures


def write_partial_state(state_root: Path, platform: str, failures: list[dict]) -> Path:
    state_root.mkdir(parents=True, exist_ok=True)
    state_path = state_root / f"{platform}.json"
    installed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state = {
        "schema_version": 1,
        "platform": platform,
        "installed_at": installed_at,
        "status": "partial_failure",
        "partial_failure": True,
        "remote_freshness_state": "unverified",
        "failures": failures,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state_path


def _parse_targets(raw_targets: list[list[str]]) -> list[InstallTarget]:
    return [
        InstallTarget(name=name, source_path=Path(source), dest_path=Path(dest), install_mode=mode)
        for name, source, dest, mode in raw_targets
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--target", action="append", nargs=4, default=[], metavar=("NAME", "SOURCE", "DEST", "MODE"))
    args = parser.parse_args()

    failures = validate_copy_targets(_parse_targets(args.target))
    if not failures:
        return 0

    state_path = write_partial_state(Path(args.state_root), args.platform, failures)
    print(f"install verification failed; partial state written to {state_path}", file=sys.stderr)
    for failure in failures:
        print(
            f"- {failure['target_name']}: {failure['reason']} "
            f"({failure['source_path']} -> {failure['dest_path']})",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
