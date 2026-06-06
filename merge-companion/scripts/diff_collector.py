"""Compare current files against snapshot, register user-modified files in manifest.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""
from __future__ import annotations
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from file_walker import EXCLUDED_DIR_NAMES, EXCLUDED_FILE_NAMES, EXCLUDED_FILE_SUFFIXES
from snapshot import file_hash, load_snapshot, snapshot_records
from manifest_io import append_entry, append_entry_if_absent

_SHARED_DIR = Path(__file__).resolve().parents[2] / "_shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))
from merge_companion_messages import render_pending_merge_message

README_FIRST_TEMPLATE = f"""# READ-ME-FIRST. Ghost-ALICE User Change Quarantine Directory

{render_pending_merge_message("readme")}

## To Review Immediately

Ask the AI chat:

    Please review backed-up changes.

## Automatic Activation Paths (eight-surface activation guarantee)

pending-merge-session-start layer. SessionStart hook
pending-merge-user-prompt layer. UserPromptSubmit hook payload
pending-merge-prose-rule layer. AGENTS.md rule 0-A
pending-merge-task-router-precheck layer. task-router 1.0 manifest check
pending-merge-bootstrap-self-check layer. platform bootstrap self-check rule
pending-merge-install-tail layer. install completion tail guidance
pending-merge-readme layer. this READ-ME-FIRST.md file itself
pending-merge-session-start-failsafe layer. SessionStart hook fail-safe || true

## Directory Structure

- manifest.json is the metadata SSOT for pending and decided entries.
- snapshot.json is the file hash snapshot from the previous install.
- *.bak files are quarantined user-change backups and are not rotation targets.

diff_collector.py automatically creates and updates this file. If a user edits
it, the next install overwrites it.
"""


def _file_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.is_dir():
        return "directory"
    if path.exists():
        return "other"
    return "absent"


def _encoding_label(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return "binary-or-non-utf8"
    return "utf-8"


def _is_excluded_generated_path(path: Path, snapshot_record: Optional[dict] = None) -> bool:
    paths = [path]
    if snapshot_record:
        relative_path = snapshot_record.get("relative_path")
        if isinstance(relative_path, str) and relative_path:
            paths.append(Path(relative_path))
    for candidate in paths:
        if any(part in EXCLUDED_DIR_NAMES for part in candidate.parts):
            return True
        if candidate.name in EXCLUDED_FILE_NAMES or candidate.suffix in EXCLUDED_FILE_SUFFIXES:
            return True
    return False


def _install_marker_hash(path: Path, snapshot_record: Optional[dict], skills_dir: Optional[Path]) -> Optional[str]:
    marker_path = snapshot_record.get("install_marker_path") if snapshot_record else None
    if marker_path:
        return file_hash(Path(marker_path))
    if skills_dir is None or snapshot_record is None:
        return None
    asset_id = snapshot_record.get("asset_id")
    if not asset_id:
        return None
    return file_hash(skills_dir / asset_id / ".ghost-alice-install.json")


def _has_ownership_conflict(
    snapshot_record: Optional[dict],
    current_marker_hash: Optional[str],
) -> bool:
    if snapshot_record is None:
        return False
    snapshot_marker_hash = snapshot_record.get("install_marker_hash")
    if snapshot_marker_hash is None:
        return False
    return snapshot_marker_hash != current_marker_hash


def _marker_path(snapshot_record: Optional[dict], skills_dir: Optional[Path]) -> Optional[Path]:
    if snapshot_record is None:
        return None
    marker_path = snapshot_record.get("install_marker_path")
    if marker_path:
        return Path(marker_path)
    if skills_dir is None:
        return None
    asset_id = snapshot_record.get("asset_id")
    if not asset_id:
        return None
    return skills_dir / asset_id / ".ghost-alice-install.json"


def _marker_relative_path(snapshot_record: dict, path: Path) -> Optional[str]:
    relative_path = snapshot_record.get("relative_path")
    asset_id = snapshot_record.get("asset_id")
    if isinstance(relative_path, str) and relative_path:
        prefix = f"{asset_id}/" if asset_id else ""
        if prefix and relative_path.startswith(prefix):
            return relative_path[len(prefix) :]
        return relative_path
    return path.name


def _is_benign_ghost_alice_marker_refresh(
    path: Path,
    snapshot_record: Optional[dict],
    current_hash: Optional[str],
    current_marker_hash: Optional[str],
    skills_dir: Optional[Path],
) -> bool:
    if snapshot_record is None or current_hash is None:
        return False
    if snapshot_record.get("install_marker_hash") == current_marker_hash:
        return False
    marker = _marker_path(snapshot_record, skills_dir)
    if marker is None or not marker.is_file():
        return False
    try:
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(marker_data, dict) or marker_data.get("managed_by") != "Ghost-ALICE":
        return False
    asset_id = snapshot_record.get("asset_id")
    if asset_id and marker_data.get("asset_id") != asset_id:
        return False
    content_hashes = marker_data.get("content_hashes")
    if not isinstance(content_hashes, dict):
        return False
    marker_relative = _marker_relative_path(snapshot_record, path)
    return bool(marker_relative and content_hashes.get(marker_relative) == current_hash)


def _change_record(
    path: Path,
    snapshot_record: Optional[dict],
    current_hash: Optional[str],
    change_kind: str,
    skills_dir: Optional[Path],
    current_file_kind: Optional[str] = None,
    current_encoding: Optional[str] = None,
    current_install_marker_hash: Optional[str] = None,
) -> dict:
    path_str = str(path)
    asset_id = None
    relative_path = None
    if snapshot_record:
        asset_id = snapshot_record.get("asset_id")
        relative_path = snapshot_record.get("relative_path")
    if skills_dir is not None and (asset_id is None or relative_path is None):
        candidates = [
            (path, skills_dir),
            (path.resolve(strict=False), skills_dir.resolve(strict=False)),
        ]
        for candidate_path, candidate_root in candidates:
            try:
                rel = candidate_path.relative_to(candidate_root)
                asset_id = asset_id or (rel.parts[0] if rel.parts else path.parent.name)
                relative_path = relative_path or rel.as_posix()
                break
            except ValueError:
                continue
    asset_id = asset_id or path.parent.name or path.name
    relative_path = relative_path or path.name
    snapshot_file_kind = snapshot_record.get("file_kind") if snapshot_record else None
    snapshot_encoding = snapshot_record.get("encoding") if snapshot_record else None
    return {
        "path": path_str,
        "snapshot_hash": snapshot_record.get("sha256") if snapshot_record else None,
        "current_hash": current_hash,
        "change_kind": change_kind,
        "asset_id": asset_id,
        "relative_path": relative_path,
        "snapshot_file_kind": snapshot_file_kind,
        "current_file_kind": current_file_kind or _file_kind(path),
        "snapshot_encoding": snapshot_encoding,
        "current_encoding": current_encoding if current_encoding is not None else _encoding_label(path),
        "snapshot_install_marker_hash": snapshot_record.get("install_marker_hash") if snapshot_record else None,
        "current_install_marker_hash": current_install_marker_hash,
    }


def _changed_file_kind(
    old_record: Optional[dict],
    current_kind: str,
    old_hash: Optional[str],
    new_hash: Optional[str],
    current_encoding: Optional[str],
) -> str:
    if old_record is not None and old_record.get("file_kind") != current_kind:
        return "type-changed"
    if (
        old_record is not None
        and old_record.get("encoding") == "utf-8"
        and current_encoding == "binary-or-non-utf8"
    ):
        return "encoding-invalid"
    if old_hash is None:
        return "added"
    if old_hash != new_hash:
        return "modified"
    return "unchanged"


def collect_user_changes(
    snapshot_path: Path,
    files: Iterable[Path],
    skills_dir: Optional[Path] = None,
) -> list[dict]:
    snap = load_snapshot(snapshot_path)
    records = snapshot_records(snap, skills_dir=skills_dir)
    current_paths = {str(f): f for f in files if not _is_excluded_generated_path(f)}
    changes = []
    for path_str, f in current_paths.items():
        old_record = records.get(path_str)
        old = old_record.get("sha256") if old_record else None
        new = file_hash(f)
        current_kind = _file_kind(f)
        current_encoding = _encoding_label(f)
        current_marker_hash = _install_marker_hash(f, old_record, skills_dir)
        benign_ghost_alice_refresh = _is_benign_ghost_alice_marker_refresh(
            f,
            old_record,
            new,
            current_marker_hash,
            skills_dir,
        )
        if old is None and new is None:
            continue
        if benign_ghost_alice_refresh:
            continue
        if _has_ownership_conflict(old_record, current_marker_hash):
            changes.append(
                _change_record(
                    f,
                    old_record,
                    new,
                    "ownership-conflict",
                    skills_dir,
                    current_file_kind=current_kind,
                    current_encoding=current_encoding,
                    current_install_marker_hash=current_marker_hash,
                )
            )
        elif old != new:
            change_kind = _changed_file_kind(
                old_record,
                current_kind,
                old,
                new,
                current_encoding,
            )
            if change_kind != "unchanged":
                changes.append(
                    _change_record(
                        f,
                        old_record,
                        new,
                        change_kind,
                        skills_dir,
                        current_file_kind=current_kind,
                        current_encoding=current_encoding,
                        current_install_marker_hash=current_marker_hash,
                    )
                )

    for path_str, old_record in records.items():
        if path_str in current_paths:
            continue
        f = Path(path_str)
        if _is_excluded_generated_path(f, old_record):
            continue
        current_kind = _file_kind(f)
        if current_kind != "absent" and old_record.get("file_kind") == current_kind:
            continue
        change_kind = "deleted" if current_kind == "absent" else "type-changed"
        changes.append(
            _change_record(
                f,
                old_record,
                None,
                change_kind,
                skills_dir,
                current_file_kind=current_kind,
                current_encoding=_encoding_label(f),
                current_install_marker_hash=_install_marker_hash(f, old_record, skills_dir),
            )
        )

    return _coalesce_moved_changes(changes)


def _coalesce_moved_changes(changes: list[dict]) -> list[dict]:
    deleted_by_key: dict[tuple[str, str], list[dict]] = {}
    for change in changes:
        if change.get("change_kind") != "deleted" or not change.get("snapshot_hash"):
            continue
        key = (change.get("asset_id") or "", change["snapshot_hash"])
        deleted_by_key.setdefault(key, []).append(change)

    consumed_deleted: set[int] = set()
    consumed_added: set[int] = set()
    moved: list[dict] = []
    for index, change in enumerate(changes):
        if change.get("change_kind") != "added" or not change.get("current_hash"):
            continue
        key = (change.get("asset_id") or "", change["current_hash"])
        candidates = deleted_by_key.get(key, [])
        if not candidates:
            continue
        deleted = candidates.pop(0)
        consumed_added.add(index)
        consumed_deleted.add(id(deleted))
        moved_change = dict(change)
        moved_change["change_kind"] = "moved"
        moved_change["snapshot_hash"] = deleted["snapshot_hash"]
        moved_change["snapshot_file_kind"] = deleted.get("snapshot_file_kind")
        moved_change["snapshot_encoding"] = deleted.get("snapshot_encoding")
        moved_change["snapshot_install_marker_hash"] = deleted.get("snapshot_install_marker_hash")
        moved_change["previous_path"] = deleted["path"]
        moved_change["previous_relative_path"] = deleted.get("relative_path")
        moved.append(moved_change)

    result: list[dict] = []
    for index, change in enumerate(changes):
        if index in consumed_added or id(change) in consumed_deleted:
            continue
        result.append(change)
    result.extend(moved)
    return result


def _safe_filename(src: Path) -> str:
    return src.name.replace("/", "_").replace("\\", "_").replace("..", "_")


def _pending_entry_id(entry_seed: dict) -> str:
    payload = json.dumps(entry_seed, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"pending-{hashlib.sha256(payload).hexdigest()[:16]}"


def _manifest_entry(
    change: dict,
    src: Path,
    backup_path: Optional[Path],
    platform: str,
    timestamp: str,
) -> dict:
    skill = change.get("asset_id") or src.parent.name or src.name
    safe_name = _safe_filename(src)
    change_kind = change.get("change_kind", "modified")
    entry_seed = {
        "platform": platform,
        "source_path": str(src),
        "snapshot_hash": change["snapshot_hash"],
        "current_hash": change["current_hash"],
        "change_kind": change_kind,
        "relative_path": change.get("relative_path") or safe_name,
    }
    entry = {
        "id": _pending_entry_id(entry_seed),
        "platform": platform,
        "skill": skill,
        "source_path": str(src),
        "backup_path": str(backup_path) if backup_path is not None else None,
        "snapshot_hash": change["snapshot_hash"],
        "current_hash": change["current_hash"],
        "change_kind": change_kind,
        "relative_path": change.get("relative_path") or safe_name,
        "snapshot_file_kind": change.get("snapshot_file_kind"),
        "current_file_kind": change.get("current_file_kind"),
        "snapshot_encoding": change.get("snapshot_encoding"),
        "current_encoding": change.get("current_encoding"),
        "snapshot_install_marker_hash": change.get("snapshot_install_marker_hash"),
        "current_install_marker_hash": change.get("current_install_marker_hash"),
        "previous_path": change.get("previous_path"),
        "previous_relative_path": change.get("previous_relative_path"),
        "decided": False,
        "decision": None,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if change_kind == "deleted":
        entry["deleted_snapshot_hash"] = change["snapshot_hash"]
    return entry


def _append_pending_entry_if_absent(manifest_path: Path, entry: dict) -> bool:
    return append_entry_if_absent(
        manifest_path,
        entry,
        [
            "platform",
            "source_path",
            "snapshot_hash",
            "current_hash",
            "change_kind",
        ],
    )


def _backup_change(change: dict, src: Path, pending_dir: Path, timestamp: str) -> Optional[Path]:
    if change.get("change_kind") == "deleted":
        return None
    if not src.exists() or not src.is_file():
        return None
    skill = change.get("asset_id") or src.parent.name or src.name
    safe_name = _safe_filename(src)
    backup_name = f"{timestamp}-{skill}-{safe_name}.bak"
    backup_path = pending_dir / backup_name
    suffix = 1
    while backup_path.exists():
        backup_path = pending_dir / f"{timestamp}-{skill}-{safe_name}-{suffix}.bak"
        suffix += 1
    shutil.copy2(src, backup_path)
    return backup_path


def _write_readme_first(pending_dir: Path) -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    readme = pending_dir / "READ-ME-FIRST.md"
    readme.write_text(README_FIRST_TEMPLATE, encoding="utf-8")


def register_changes_in_manifest(
    changes: list[dict],
    pending_dir: Path,
    manifest_path: Path,
    platform: str,
) -> None:
    if not changes:
        return
    pending_dir.mkdir(parents=True, exist_ok=True)
    _write_readme_first(pending_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    for change in changes:
        src = Path(change["path"])
        if change.get("change_kind") != "deleted" and not src.exists():
            continue
        backup_path = _backup_change(change, src, pending_dir, timestamp)
        entry = _manifest_entry(change, src, backup_path, platform, timestamp)
        if not _append_pending_entry_if_absent(manifest_path, entry) and backup_path is not None:
            try:
                backup_path.unlink()
            except OSError:
                pass


def quarantine_legacy_target(
    target_path: Path,
    target_name: str,
    pending_dir: Path,
    manifest_path: Path,
    platform: str,
):
    if not target_path.exists() or target_path.is_symlink():
        return None

    pending_dir.mkdir(parents=True, exist_ok=True)
    _write_readme_first(pending_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    safe_name = target_name.replace("/", "_").replace("\\", "_").replace("..", "_")
    legacy_root = pending_dir / "legacy-targets"
    legacy_root.mkdir(parents=True, exist_ok=True)
    backup_path = legacy_root / f"{timestamp}-{safe_name}"
    suffix = 1
    while backup_path.exists():
        backup_path = legacy_root / f"{timestamp}-{safe_name}-{suffix}"
        suffix += 1

    shutil.move(str(target_path), str(backup_path))
    entry = {
        "id": f"{timestamp}-{safe_name}-legacy-target",
        "platform": platform,
        "skill": target_name,
        "source_path": str(target_path),
        "backup_path": str(backup_path),
        "snapshot_hash": None,
        "current_hash": "legacy-no-baseline",
        "reason": "legacy-no-baseline",
        "decided": False,
        "decision": None,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    append_entry(manifest_path, entry)
    return backup_path
