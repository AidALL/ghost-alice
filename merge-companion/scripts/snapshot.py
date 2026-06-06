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
      "size": 123,
      "mtime_ns": 123456789,
      "mode": "0o644",
      "encoding": "utf-8" | "binary-or-non-utf8",
      "install_marker_path": "<skill root>/.ghost-alice-install.json",
      "install_marker_hash": "<sha256 hex>"
    }
  }
}

The legacy "files" mapping is intentionally retained. Existing installers and
tests read that key directly, while newer diff code uses file_records.

Note on symlinks:
file_hash opens and reads the target. Symlinks are followed (correct: we hash
content). Filtering of which paths to include in the snapshot is the caller's
responsibility (see snapshot_cli.py). Callers that walk an installed skills
directory must skip entries that are symlinks pointing into the repo, otherwise
repo updates pollute the user-change baseline.
"""
from __future__ import annotations
import hashlib, json
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
    st = path.stat()
    marker = _install_marker(path, skills_dir)
    return {
        "path": str(path),
        "sha256": sha256,
        "asset_id": _asset_id(path, skills_dir),
        "relative_path": _relative_path(path, skills_dir),
        "file_kind": "file",
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "mode": f"0o{st.st_mode & 0o7777:o}",
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
    migration_events = _migration_events_for_existing_snapshot(snapshot_path, captured_at)
    if migration_events:
        record["migration_events"] = migration_events
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


def _migration_events_for_existing_snapshot(snapshot_path: Path, captured_at: str) -> list[dict]:
    if not snapshot_path.exists():
        return []
    try:
        previous = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    previous_version = previous.get("version")
    if previous_version == 2:
        return []
    return [
        {
            "event": "snapshot-v1-to-v2",
            "from_version": previous_version,
            "to_version": 2,
            "migrated_at": captured_at,
        }
    ]

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
                "size": raw.get("size"),
                "mtime_ns": raw.get("mtime_ns"),
                "mode": raw.get("mode"),
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
                "size": None,
                "mtime_ns": None,
                "mode": None,
                "encoding": None,
                "install_marker_path": None,
                "install_marker_hash": None,
            }

    return records
