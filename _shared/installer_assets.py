"""Installer asset inventory and ownership classification helpers."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


GHOST_ALICE_MARKER_FILENAME = ".ghost-alice-install.json"
GHOST_ALICE_MANAGED_BY = "Ghost-ALICE"

OWNERSHIP_ABSENT = "absent"
OWNERSHIP_GHOST_ALICE_MANAGED = "ghost-alice-managed"
OWNERSHIP_USER_MODIFIED_MANAGED = "user-modified-managed"
OWNERSHIP_USER_OWNED = "user-owned"
OWNERSHIP_LEGACY_NO_BASELINE = "legacy-no-baseline"
OWNERSHIP_CONFLICT = "ownership-conflict"

KIND_SKILL_ROOT = "skill-root"
KIND_GLOBAL_RULE = "global-rule"

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".git",
}
EXCLUDED_FILE_NAMES = {GHOST_ALICE_MARKER_FILENAME, ".DS_Store"}
EXCLUDED_FILE_SUFFIXES = {".pyc"}


@dataclass(frozen=True)
class AssetClassification:
    path: Path
    kind: str
    asset_id: str
    ownership: str
    reason: str
    marker: Optional[dict] = None
    link_target: Optional[str] = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_hashable_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in EXCLUDED_DIR_NAMES and not (current / name).is_symlink()
        ]
        for filename in sorted(filenames):
            path = current / filename
            if (
                filename in EXCLUDED_FILE_NAMES
                or path.suffix in EXCLUDED_FILE_SUFFIXES
                or path.is_symlink()
                or not path.is_file()
            ):
                continue
            files.append(path)
    return sorted(files)


def _content_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in _iter_hashable_files(root):
        rel = path.relative_to(root).as_posix()
        hashes[rel] = _sha256_file(path)
    return hashes


def _load_marker(root: Path) -> tuple[Optional[dict], Optional[str]]:
    marker_path = root / GHOST_ALICE_MARKER_FILENAME
    if not marker_path.exists():
        return None, None
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid-ghost-alice-marker:{exc.__class__.__name__}"
    if not isinstance(data, dict):
        return None, "invalid-ghost-alice-marker:not-object"
    if data.get("managed_by") != GHOST_ALICE_MANAGED_BY:
        return None, "invalid-ghost-alice-marker:managed-by"
    return data, None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_windows_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        return bool(is_junction())
    if os.name != "nt" or path.is_symlink():
        return False
    try:
        attrs = path.stat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _classify_repo_link(
    path: Path,
    *,
    asset_id: str,
    repo_root: Optional[Path],
    link_kind: str,
) -> AssetClassification:
    try:
        target = path.resolve(strict=True)
    except OSError:
        return AssetClassification(
            path=path,
            kind=KIND_SKILL_ROOT,
            asset_id=asset_id,
            ownership=OWNERSHIP_CONFLICT,
            reason=f"broken-{link_kind}",
        )

    if repo_root is not None and _is_relative_to(target, repo_root.resolve()):
        return AssetClassification(
            path=path,
            kind=KIND_SKILL_ROOT,
            asset_id=asset_id,
            ownership=OWNERSHIP_GHOST_ALICE_MANAGED,
            reason=f"{link_kind}-to-repo",
            link_target=str(target),
        )

    return AssetClassification(
        path=path,
        kind=KIND_SKILL_ROOT,
        asset_id=asset_id,
        ownership=OWNERSHIP_USER_OWNED,
        reason=f"{link_kind}-outside-repo",
        link_target=str(target),
    )


def write_ownership_marker(
    root: Path,
    *,
    platform: str,
    asset_id: str,
    source_repo: str,
    source_commit: str,
    install_mode: str,
    owner: str = "ghost-alice",
    addon_id: Optional[str] = None,
    provided_kind: str = "skill",
) -> Path:
    marker = {
        "schema_version": 1,
        "managed_by": GHOST_ALICE_MANAGED_BY,
        "platform": platform,
        "asset_id": asset_id,
        "owner": owner,
        "addon_id": addon_id,
        "provided_kind": provided_kind,
        "source_repo": source_repo,
        "source_commit": source_commit,
        "installed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "install_mode": install_mode,
        "content_hashes": _content_hashes(root),
        "encoding_contract": {
            "text": "utf-8-strict",
        },
    }
    path = root / GHOST_ALICE_MARKER_FILENAME
    path.write_text(json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def classify_skill_root(
    path: Path,
    *,
    expected_asset_id: Optional[str] = None,
    repo_root: Optional[Path] = None,
    expected_addon_id: Optional[str] = None,
) -> AssetClassification:
    """Classify a skill root's ownership.

    ``expected_addon_id`` (plan task T2.9) only gates copy-mode targets, which
    carry a ``.ghost-alice-install.json`` marker recording ``addon_id``. Symlink
    and junction targets carry NO marker — their addon ownership is proven by the
    recorded sidecar ``content_hash`` of the link, not here — so this function
    classifies them purely from the link target and does NOT consult
    ``expected_addon_id``.
    """
    asset_id = expected_asset_id or path.name

    if path.is_symlink():
        # Symlink contract: ownership comes from the link target; expected_addon_id
        # is intentionally not consulted (no marker exists on a symlink).
        return _classify_repo_link(path, asset_id=asset_id, repo_root=repo_root, link_kind="symlink")

    if not path.exists():
        reason = "expected-target-absent" if expected_asset_id else "path-absent"
        return AssetClassification(path, KIND_SKILL_ROOT, asset_id, OWNERSHIP_ABSENT, reason)

    if _is_windows_junction(path):
        return _classify_repo_link(path, asset_id=asset_id, repo_root=repo_root, link_kind="junction")

    if not path.is_dir():
        return AssetClassification(path, KIND_SKILL_ROOT, asset_id, OWNERSHIP_CONFLICT, "target-not-directory")

    marker, marker_error = _load_marker(path)
    if marker_error:
        return AssetClassification(path, KIND_SKILL_ROOT, asset_id, OWNERSHIP_CONFLICT, marker_error)

    if marker is None:
        if expected_asset_id:
            return AssetClassification(
                path,
                KIND_SKILL_ROOT,
                asset_id,
                OWNERSHIP_LEGACY_NO_BASELINE,
                "expected-ghost-alice-target-without-marker",
            )
        return AssetClassification(path, KIND_SKILL_ROOT, asset_id, OWNERSHIP_USER_OWNED, "no-ghost-alice-marker")

    marker_asset_id = marker.get("asset_id")
    if expected_asset_id and marker_asset_id != expected_asset_id:
        return AssetClassification(path, KIND_SKILL_ROOT, asset_id, OWNERSHIP_CONFLICT, "marker-asset-mismatch", marker)

    marker_addon_id = marker.get("addon_id")
    if expected_addon_id is not None and marker_addon_id != expected_addon_id:
        # Fail closed: a marker whose addon_id is absent (None) or differs cannot
        # prove the caller's addon owns this copy-mode skill.
        reason = "marker-addon-mismatch" if marker_addon_id is not None else "marker-addon-unattributed"
        return AssetClassification(path, KIND_SKILL_ROOT, asset_id, OWNERSHIP_CONFLICT, reason, marker)

    expected_hashes = marker.get("content_hashes")
    if isinstance(expected_hashes, dict) and expected_hashes != _content_hashes(path):
        return AssetClassification(
            path,
            KIND_SKILL_ROOT,
            marker_asset_id or asset_id,
            OWNERSHIP_USER_MODIFIED_MANAGED,
            "content-hash-mismatch",
            marker,
        )

    return AssetClassification(
        path,
        KIND_SKILL_ROOT,
        marker_asset_id or asset_id,
        OWNERSHIP_GHOST_ALICE_MANAGED,
        "marker-and-hash-match",
        marker,
    )


def classify_global_rule_file(
    path: Path,
    *,
    full_file_marker: str,
    managed_block_begin: Optional[str] = None,
    managed_block_end: Optional[str] = None,
) -> AssetClassification:
    asset_id = path.name
    if not path.exists():
        return AssetClassification(path, KIND_GLOBAL_RULE, asset_id, OWNERSHIP_ABSENT, "rule-file-absent")
    if not path.is_file():
        return AssetClassification(path, KIND_GLOBAL_RULE, asset_id, OWNERSHIP_CONFLICT, "rule-path-not-file")
    try:
        body = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return AssetClassification(path, KIND_GLOBAL_RULE, asset_id, OWNERSHIP_CONFLICT, f"encoding-invalid:{exc.__class__.__name__}")

    if body.startswith(full_file_marker):
        return AssetClassification(path, KIND_GLOBAL_RULE, asset_id, OWNERSHIP_GHOST_ALICE_MANAGED, "full-file-marker-match")

    if managed_block_begin and managed_block_end and managed_block_begin in body and managed_block_end in body:
        return AssetClassification(path, KIND_GLOBAL_RULE, asset_id, OWNERSHIP_GHOST_ALICE_MANAGED, "managed-block-present")

    return AssetClassification(path, KIND_GLOBAL_RULE, asset_id, OWNERSHIP_USER_OWNED, "markerless-existing-rule-file")


def inventory_skill_roots(
    skills_dir: Path,
    *,
    expected_asset_ids: Iterable[str] = (),
    repo_root: Optional[Path] = None,
) -> list[AssetClassification]:
    expected = set(expected_asset_ids)
    results: list[AssetClassification] = []
    seen: set[str] = set()

    if skills_dir.exists():
        for child in sorted(skills_dir.iterdir(), key=lambda p: p.name):
            if not (child.is_dir() or child.is_symlink()):
                continue
            asset_id = child.name
            seen.add(asset_id)
            results.append(
                classify_skill_root(
                    child,
                    expected_asset_id=asset_id if asset_id in expected else None,
                    repo_root=repo_root,
                )
            )

    for asset_id in sorted(expected - seen):
        results.append(
            classify_skill_root(
                skills_dir / asset_id,
                expected_asset_id=asset_id,
                repo_root=repo_root,
            )
        )

    return sorted(results, key=lambda item: item.asset_id)
