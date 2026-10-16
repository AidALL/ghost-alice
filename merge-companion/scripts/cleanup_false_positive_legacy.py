#!/usr/bin/env python3
"""Clean installer-created false-positive pending entries and entries outside Ghost-ALICE skill roots.

Dependencies: Python 3.11+ standard library + local merge-companion/_shared modules.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_SHARED = _HERE.parents[1] / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from installer_assets import (
    OWNERSHIP_ABSENT,
    OWNERSHIP_GHOST_ALICE_MANAGED,
    OWNERSHIP_USER_OWNED,
    classify_skill_root,
)
from manifest_io import read_manifest, write_manifest


CLEANUP_REASON = "clean-ghost-alice-managed-false-positive"
REPO_JUNCTION_CLEANUP_REASON = "repo-junction-source-change-false-positive"
UNMANAGED_ROOT_CLEANUP_REASON = "unmanaged-skill-root"


def _default_pending(platform: str) -> Path:
    return Path.home() / ".ghost-alice" / "pending-merges" / platform


def _default_repo_root() -> Path:
    return _HERE.parents[1]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _is_unresolved_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.absolute().relative_to(parent.absolute())
        return True
    except ValueError:
        return False


def _classify_clean(path: Path, *, asset_id: str, repo_root: Path) -> tuple[bool, str]:
    result = classify_skill_root(path, expected_asset_id=asset_id, repo_root=repo_root)
    return result.ownership == OWNERSHIP_GHOST_ALICE_MANAGED, result.reason


def _legacy_false_positive_status(entry: dict, *, pending_dir: Path, repo_root: Path) -> tuple[bool, str]:
    if entry.get("decided", False):
        return False, "already-decided"
    if entry.get("reason") != "legacy-no-baseline" and entry.get("current_hash") != "legacy-no-baseline":
        return False, "not-legacy-no-baseline"

    asset_id = str(entry.get("skill") or "")
    if not asset_id:
        return False, "missing-skill"

    source_raw = entry.get("source_path")
    backup_raw = entry.get("backup_path")
    if not source_raw or not backup_raw:
        return False, "missing-source-or-backup"

    source = Path(str(source_raw))
    backup = Path(str(backup_raw))
    if not _is_relative_to(backup, pending_dir):
        return False, "backup-outside-pending"
    if not backup.exists():
        return False, "backup-missing"

    source_clean, source_reason = _classify_clean(source, asset_id=asset_id, repo_root=repo_root)
    if not source_clean:
        return False, f"source-not-clean:{source_reason}"
    backup_clean, backup_reason = _classify_clean(backup, asset_id=asset_id, repo_root=repo_root)
    if not backup_clean:
        return False, f"backup-not-clean:{backup_reason}"
    return True, CLEANUP_REASON


def _repo_junction_false_positive_status(entry: dict, *, pending_dir: Path, repo_root: Path) -> tuple[bool, str]:
    if entry.get("decided", False):
        return False, "already-decided"

    source_raw = entry.get("source_path")
    backup_raw = entry.get("backup_path")
    if not source_raw or not backup_raw:
        return False, "missing-source-or-backup"

    source = Path(str(source_raw))
    backup = Path(str(backup_raw))
    if not _is_relative_to(backup, pending_dir):
        return False, "backup-outside-pending"
    if not backup.exists():
        return False, "backup-missing"
    if not source.exists():
        return False, "source-missing"
    if _is_unresolved_relative_to(source, repo_root):
        return False, "source-already-repo-path"
    if not _is_relative_to(source, repo_root):
        return False, "source-not-repo-resolved"
    return True, REPO_JUNCTION_CLEANUP_REASON


def _false_positive_status(entry: dict, *, pending_dir: Path, repo_root: Path) -> tuple[bool, str]:
    legacy_ok, legacy_reason = _legacy_false_positive_status(
        entry,
        pending_dir=pending_dir,
        repo_root=repo_root,
    )
    if legacy_ok:
        return True, legacy_reason

    junction_ok, junction_reason = _repo_junction_false_positive_status(
        entry,
        pending_dir=pending_dir,
        repo_root=repo_root,
    )
    if junction_ok:
        return True, junction_reason
    if legacy_reason == "not-legacy-no-baseline":
        return False, junction_reason
    return False, legacy_reason


def _managed_skill_roots(install_state_path: Path | None, repo_root: Path) -> tuple[set[Path], set[str]] | None:
    """Return recorded install destinations and catalog skill names, or None when either is unknown."""
    if install_state_path is None:
        return None
    try:
        targets = json.loads(install_state_path.read_text(encoding="utf-8")).get("targets")
        skills = json.loads((repo_root / "skill-catalog" / "skills.json").read_text(encoding="utf-8")).get("skills")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(targets, list) or not isinstance(skills, list):
        return None
    destinations = {Path(str(target["dest_path"])).absolute() for target in targets
                    if isinstance(target, dict) and target.get("dest_path")}
    names: set[str] = set()
    for skill in skills:
        if isinstance(skill, dict):
            if skill.get("name"):
                names.add(str(skill["name"]))
            if skill.get("path"):
                names.add(Path(str(skill["path"])).parent.name)
    return destinations, names


def _entry_skill_root(entry: dict) -> Path | None:
    source_raw, relative_raw = entry.get("source_path"), entry.get("relative_path")
    if not source_raw or not relative_raw:
        return None
    source, relative = Path(str(source_raw)), Path(str(relative_raw))
    parts = relative.parts
    if relative.is_absolute() or ".." in parts or len(source.parts) <= len(parts) or source.parts[-len(parts):] != parts:
        return None
    return Path(*source.parts[: -len(parts)]) / parts[0]


def _unmanaged_root_status(
    entry: dict,
    *,
    pending_dir: Path,
    repo_root: Path,
    managed: tuple[set[Path], set[str]] | None,
) -> tuple[bool, str]:
    """An entry under a root that Ghost-ALICE never installed is not a change to a Ghost-ALICE skill."""
    if managed is None:
        return False, "install-state-unknown"
    root = _entry_skill_root(entry)
    if root is None:
        return False, "skill-root-unknown"
    backup_raw = entry.get("backup_path")
    if backup_raw and not _is_relative_to(Path(str(backup_raw)), pending_dir):
        return False, "backup-outside-pending"
    destinations, names = managed
    if root.absolute() in destinations or root.name in names:
        return False, "ghost-alice-skill-root"
    ownership = classify_skill_root(root, repo_root=repo_root).ownership
    if ownership not in {OWNERSHIP_USER_OWNED, OWNERSHIP_ABSENT}:
        return False, f"skill-root-{ownership}"
    return True, UNMANAGED_ROOT_CLEANUP_REASON


def _remove_backup(backup: Path) -> bool:
    if not backup.exists():
        return False
    if backup.is_dir() and not backup.is_symlink():
        shutil.rmtree(backup)
    else:
        backup.unlink()
    return True


def cleanup_false_positive_legacy(
    *,
    manifest_path: Path,
    pending_dir: Path,
    repo_root: Path,
    apply: bool,
    install_state_path: Path | None = None,
) -> dict:
    data = read_manifest(manifest_path)
    entries = data.setdefault("entries", [])
    managed = _managed_skill_roots(install_state_path, repo_root)
    cleanable: list[tuple[dict, Path, str]] = []
    removable: list[dict] = []
    skipped: list[dict] = []

    for entry in entries:
        if not isinstance(entry, dict):
            skipped.append({"id": None, "reason": "entry-not-object"})
            continue
        unmanaged, _ = _unmanaged_root_status(entry, pending_dir=pending_dir, repo_root=repo_root, managed=managed)
        if unmanaged:
            removable.append(entry)
            continue
        ok, reason = _false_positive_status(entry, pending_dir=pending_dir, repo_root=repo_root)
        if ok:
            cleanable.append((entry, Path(str(entry["backup_path"])), reason))
        else:
            if not entry.get("decided", False):
                skipped.append({"id": entry.get("id"), "reason": reason})

    removed_backups = 0
    cleanup_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if apply and (cleanable or removable):
        for entry, backup, reason in cleanable:
            entry["decided"] = True
            entry["decision"] = "discarded"
            entry["cleanup_reason"] = reason
            entry["cleanup_at"] = cleanup_at
            if _remove_backup(backup):
                removed_backups += 1
        # Such an entry never was a change to a Ghost-ALICE skill, so it leaves the store with its backup.
        for entry in removable:
            backup_raw = entry.get("backup_path")
            if backup_raw and _remove_backup(Path(str(backup_raw))):
                removed_backups += 1
        removed_ids = {id(entry) for entry in removable}
        data["entries"] = [entry for entry in entries if id(entry) not in removed_ids]
        legacy_root = pending_dir / "legacy-targets"
        if legacy_root.exists() and legacy_root.is_dir():
            try:
                next(legacy_root.iterdir())
            except StopIteration:
                legacy_root.rmdir()
        write_manifest(manifest_path, data)

    return {
        "manifest": manifest_path.as_posix(),
        "pending": pending_dir.as_posix(),
        "apply": apply,
        "scanned": len(entries),
        "cleanable": len(cleanable),
        "cleaned": len(cleanable) if apply else 0,
        "removable": len(removable),
        "removed_entries": len(removable) if apply else 0,
        "removed_backups": removed_backups,
        "skipped": len(skipped),
        "skipped_entries": skipped[:20],
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("claude", "codex"), default="claude")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--pending", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--install-state", type=Path, default=None,
                        help="Install-state file that lists Ghost-ALICE install destinations.")
    parser.add_argument("--apply", action="store_true",
                        help="Mark clean false positives decided, remove entries outside Ghost-ALICE skill roots, "
                             "and remove their backups.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    pending = args.pending or _default_pending(args.platform)
    manifest = args.manifest or pending / "manifest.json"
    repo_root = args.repo_root or _default_repo_root()
    install_state = args.install_state or pending.parent.parent / "install-state" / f"{args.platform}.json"
    summary = cleanup_false_positive_legacy(
        manifest_path=manifest,
        pending_dir=pending,
        repo_root=repo_root,
        apply=args.apply,
        install_state_path=install_state,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        mode = "cleaned" if args.apply else "cleanable"
        print(
            f"false-positive legacy cleanup: scanned={summary['scanned']} "
            f"cleanable={summary['cleanable']} {mode}={summary['cleaned']} "
            f"removable={summary['removable']} removed={summary['removed_entries']} "
            f"skipped={summary['skipped']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
