"""Install-time snapshot of files for merge-companion change detection.

Dependencies: Python 3.11+ standard library only.

Schema:
{
  "version": 2,
  "platform": "codex",
  "captured_at": "<ISO timestamp>",
  "files": {"<absolute path>": "<sha256 hex>"},
  "file_records": {
    "<absolute path>": {
      "path": "<absolute path>",
      "sha256": "<sha256 hex>",
      "asset_id": "<skill root name>",
      "relative_path": "<path relative to skills_dir>",
      "file_kind": "file",
      "encoding": "utf-8" | "binary-or-non-utf8",
      "install_marker_path": "<skill root>/.ghost-alice-install.json",
      "install_marker_hash": "<sha256 hex>"
    }
  }
}

The "files" mapping supplies path hashes; file_records supplies structured
metadata. Both fields are part of the snapshot contract.

Note on symlinks:
file_hash opens and reads the target. Symlinks are followed (correct: we hash
content). Filtering of which paths to include in the snapshot is the caller's
responsibility (see snapshot_cli.py). Callers that walk an installed skills
directory must skip entries that are symlinks pointing into the repo, otherwise
repo updates distort the baseline for detecting external changes to installed copies.
"""
from __future__ import annotations
import hashlib, json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

class SnapshotError(Exception):
    pass

def file_hash(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _relative_path(path: Path, skills_dir: Optional[Path]) -> str:
    if skills_dir is None:
        return path.name
    candidates = [
        (path, skills_dir),
        (path.resolve(strict=False), skills_dir.resolve(strict=False)),
    ]
    for candidate_path, candidate_root in candidates:
        try:
            return candidate_path.relative_to(candidate_root).as_posix()
        except ValueError:
            continue
    return path.name


def _asset_id(path: Path, skills_dir: Optional[Path]) -> str:
    if skills_dir is not None:
        candidates = [
            (path, skills_dir),
            (path.resolve(strict=False), skills_dir.resolve(strict=False)),
        ]
        for candidate_path, candidate_root in candidates:
            try:
                rel = candidate_path.relative_to(candidate_root)
                if rel.parts:
                    return rel.parts[0]
            except ValueError:
                continue
    return path.parent.name or path.name


def _encoding_label(path: Path) -> str:
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return "binary-or-non-utf8"
    return "utf-8"


def _install_marker(path: Path, skills_dir: Optional[Path]) -> Optional[Path]:
    if skills_dir is None:
        marker = path.parent / ".ghost-alice-install.json"
        return marker if marker.exists() else None
    asset_id = _asset_id(path, skills_dir)
    marker = skills_dir / asset_id / ".ghost-alice-install.json"
    return marker if marker.exists() else None


def _file_record(path: Path, sha256: str, skills_dir: Optional[Path]) -> dict:
    marker = _install_marker(path, skills_dir)
    return {
        "path": str(path),
        "sha256": sha256,
        "asset_id": _asset_id(path, skills_dir),
        "relative_path": _relative_path(path, skills_dir),
        "file_kind": "file",
        "encoding": _encoding_label(path),
        "install_marker_path": str(marker) if marker is not None else None,
        "install_marker_hash": file_hash(marker) if marker is not None else None,
    }


def capture_snapshot(
    snapshot_path: Path,
    files: Iterable[Path],
    platform: str,
    skills_dir: Optional[Path] = None,
) -> None:
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = {
        "version": 2,
        "platform": platform,
        "captured_at": captured_at,
        "files": {},
        "file_records": {},
    }
    for f in files:
        h = file_hash(f)
        if h is not None:
            record["files"][str(f)] = h
            record["file_records"][str(f)] = _file_record(
                f,
                h,
                skills_dir,
            )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_snapshot(snapshot_path: Path) -> dict:
    if not snapshot_path.exists():
        raise SnapshotError(f"snapshot missing: {snapshot_path}")
    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SnapshotError(f"snapshot corrupt {snapshot_path}: {e}")


def snapshot_records(snapshot: dict, skills_dir: Optional[Path] = None) -> dict[str, dict]:
    """Return normalized per-file records for snapshot v1 and v2."""
    normalized_skills_dir = skills_dir.resolve() if skills_dir is not None else None
    files = snapshot.get("files", {})
    raw_records = snapshot.get("file_records", {})
    records: dict[str, dict] = {}

    if isinstance(raw_records, dict):
        for path_str, raw in raw_records.items():
            if not isinstance(raw, dict):
                continue
            path = Path(path_str)
            sha256 = raw.get("sha256") or files.get(path_str)
            records[path_str] = {
                "path": raw.get("path", path_str),
                "sha256": sha256,
                "asset_id": raw.get("asset_id") or _asset_id(path, normalized_skills_dir),
                "relative_path": raw.get("relative_path") or _relative_path(path, normalized_skills_dir),
                "file_kind": raw.get("file_kind", "file"),
                "encoding": raw.get("encoding"),
                "install_marker_path": raw.get("install_marker_path"),
                "install_marker_hash": raw.get("install_marker_hash"),
            }

    if isinstance(files, dict):
        for path_str, sha256 in files.items():
            if path_str in records:
                if not records[path_str].get("sha256"):
                    records[path_str]["sha256"] = sha256
                continue
            path = Path(path_str)
            records[path_str] = {
                "path": path_str,
                "sha256": sha256,
                "asset_id": _asset_id(path, normalized_skills_dir),
                "relative_path": _relative_path(path, normalized_skills_dir),
                "file_kind": "file",
                "encoding": None,
                "install_marker_path": None,
                "install_marker_hash": None,
            }

    return records


def prune_snapshot(snapshot_path: Path, targets: Iterable[tuple[str, str | Path]],
                   platform: str, *, provider: Optional[str] = None) -> bool:
    """Remove confirmed target paths from this host's baseline, returning whether it changed.

    Callers supply removed or released destinations, never source paths. Directory
    kinds include descendants; file kinds match exactly. Relative metadata and
    filesystem symlink resolution do not establish destination ownership.
    """
    directory_kinds = {"skill", "support", "runtime", "directory"}
    file_kinds = {"command", "adapter", "resource", "file"}
    removed = []
    for kind, target in targets:
        if kind not in directory_kinds | file_kinds or not Path(target).is_absolute():
            raise SnapshotError(f"invalid removed snapshot target: {kind!r} {target!r}")
        removed.append((kind in directory_kinds, Path(os.path.normpath(target))))
    if not removed:
        return False
    if not isinstance(platform, str) or not platform:
        raise SnapshotError("snapshot pruning requires a platform")
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"snapshot corrupt {snapshot_path}: {exc}") from exc
    if (not isinstance(data, dict) or type(data.get("version", 1)) is not int
            or data.get("version", 1) not in (1, 2) or data.get("platform", platform) != platform
            or not isinstance(data.get("files"), dict)
            or ("file_records" in data and not isinstance(data["file_records"], dict))):
        raise SnapshotError(f"invalid pending snapshot for {platform}: {snapshot_path}")
    files = data["files"]
    records = data.get("file_records", {})
    if (any(not isinstance(value, str) for value in files.values())
            or any(not isinstance(value, dict) for value in records.values())):
        raise SnapshotError(f"invalid pending snapshot entries: {snapshot_path}")

    def matches(path: str) -> bool:
        candidate = Path(os.path.normpath(path))
        return any(candidate == target or (directory and target in candidate.parents)
                   for directory, target in removed)

    paths = {path for path in files.keys() | records.keys() if matches(path)}
    if not paths:
        return False
    for path in paths:
        files.pop(path, None)
        records.pop(path, None)
    events = data.get("maintenance_events")
    if not isinstance(events, list):
        events = []
        data["maintenance_events"] = events
    events.append({"event": "targets-uninstall-pruned", "provider": provider, "platform": platform,
                   "pruned_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})
    descriptor, temporary = tempfile.mkstemp(prefix=f".{snapshot_path.name}.", suffix=".tmp",
                                            dir=snapshot_path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, snapshot_path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return True
