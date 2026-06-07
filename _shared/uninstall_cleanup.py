#!/usr/bin/env python3
"""Install-state driven cleanup for Ghost-ALICE-managed artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from installer_assets import (
    OWNERSHIP_ABSENT,
    OWNERSHIP_GHOST_ALICE_MANAGED,
    OWNERSHIP_USER_MODIFIED_MANAGED,
    classify_skill_root,
)
from global_rule_blocks import remove_codex_bootstrap
from install_hooks import uninstall_hook

REMOVABLE_TARGET_OWNERSHIPS = {
    OWNERSHIP_GHOST_ALICE_MANAGED,
}


def _user_home() -> Path:
    configured = os.environ.get("HOME")
    if configured:
        return Path(configured)
    return Path.home()


def _ghost_alice_root() -> Path:
    return _user_home() / ".ghost-alice"


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured)
    return _user_home() / ".codex"


def _claude_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured)
    return _user_home() / ".claude"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_report_path(platform: str, *, confirm: bool) -> Path:
    mode = "confirm" if confirm else "dry-run"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _ghost_alice_root() / "uninstall-reports" / f"{platform}-{mode}-{stamp}.json"


def _load_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "manifest-missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"manifest-invalid:{exc.__class__.__name__}"
    if not isinstance(data, dict):
        return None, "manifest-invalid:not-object"
    targets = data.get("targets")
    if targets is not None and not isinstance(targets, list):
        return None, "manifest-invalid:targets-not-list"
    return data, None


def _is_junction(path: Path) -> bool:
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


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.normpath(os.path.abspath(os.fspath(path))))


def _is_under(path: Path, root: Path) -> bool:
    try:
        _lexical_absolute(path).relative_to(_lexical_absolute(root))
    except ValueError:
        return False
    return True


def _platform_target_roots(platform: str) -> list[Path]:
    if platform == "claude":
        return [_claude_home() / "skills"]
    if platform == "codex":
        return [_user_home() / ".agents" / "skills"]
    return []


def _is_allowed_target_path(path: Path, platform: str) -> bool:
    return any(_is_under(path, root) for root in _platform_target_roots(platform))


def _is_allowed_support_path(path: Path) -> bool:
    return _is_under(path, _ghost_alice_root())


def _remove_path(path: Path) -> None:
    if _is_junction(path):
        path.rmdir()
    elif path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _manifest_repo_root(manifest: dict[str, Any]) -> Path | None:
    source_root = manifest.get("source_root")
    if isinstance(source_root, str) and source_root:
        return Path(source_root).expanduser()
    return None


def _manifest_platform(manifest: dict[str, Any]) -> str:
    platform = manifest.get("platform")
    return str(platform) if isinstance(platform, str) else ""


def _same_path(left: Path, right: Path) -> bool:
    return _lexical_absolute(left) == _lexical_absolute(right)


def _platforms_referencing_target(path: Path, *, current_manifest: Path, current_platform: str) -> list[str]:
    install_state_dir = current_manifest.parent
    platforms: list[str] = []
    if not install_state_dir.exists():
        return platforms
    for candidate in sorted(install_state_dir.glob("*.json")):
        if candidate == current_manifest:
            continue
        manifest, error = _load_manifest(candidate)
        if manifest is None or error is not None:
            continue
        platform = _manifest_platform(manifest) or candidate.stem
        if platform == current_platform:
            continue
        for target in manifest.get("targets", []):
            if not isinstance(target, dict):
                continue
            raw_dest = target.get("dest_path")
            if isinstance(raw_dest, str) and _same_path(Path(raw_dest).expanduser(), path):
                if platform not in platforms:
                    platforms.append(platform)
                break
    return platforms


def _source_repo_hook_item_from_change(change: dict[str, Any], *, confirm: bool) -> dict[str, Any] | None:
    raw_repo_root = change.get("repo_root") or change.get("path")
    if not isinstance(raw_repo_root, str) or not raw_repo_root:
        return None

    repo_root = Path(raw_repo_root).expanduser()
    after = str(change.get("after") or "hooks")
    before = change.get("before")
    before_present = bool(change.get("before_present"))
    item: dict[str, Any] = {
        "kind": "source-repo-hook-config",
        "target_name": "source-repo-core-hooks-path",
        "path": repo_root.as_posix(),
        "expected_current": after,
        "before_present": before_present,
    }
    if before_present and isinstance(before, str):
        item["restore_to"] = before

    try:
        current = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--local", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        item.update(action="manual-review", reason="git-not-found")
        return item

    if current.returncode != 0:
        item.update(action="unchanged", reason="source-repo-hook-path-absent")
        return item

    hook_path = current.stdout.strip()
    item["current"] = hook_path
    if hook_path != after:
        item.update(action="unchanged", reason="non-ghost-alice-hook-path")
        return item

    if not confirm:
        if before_present:
            item.update(action="would-restore-source-repo-hook-path", reason="trace-backed-source-repo-hook-path")
        else:
            item.update(action="would-remove-source-repo-hook-path", reason="trace-backed-source-repo-hook-path")
        return item

    if before_present:
        if not isinstance(before, str):
            item.update(action="manual-review", reason="invalid-source-repo-hook-before")
            return item
        restored = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--local", "core.hooksPath", before],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if restored.returncode == 0:
            item.update(
                action="restored-source-repo-hook-path",
                reason="trace-backed-source-repo-hook-path",
                restored_to=before,
            )
        else:
            item.update(
                action="manual-review",
                reason="source-repo-hook-path-restore-failed",
                stderr=restored.stderr.strip(),
            )
        return item

    removed = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--local", "--unset", "core.hooksPath"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if removed.returncode == 0:
        item.update(action="removed-source-repo-hook-path", reason="trace-backed-source-repo-hook-path")
    else:
        item.update(
            action="manual-review",
            reason="source-repo-hook-path-unset-failed",
            stderr=removed.stderr.strip(),
        )
    return item


def _source_repo_hook_items(manifest: dict[str, Any], *, confirm: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for change in manifest.get("system_env_changes", []):
        if isinstance(change, dict) and change.get("kind") == "source_repo_hook_path":
            item = _source_repo_hook_item_from_change(change, confirm=confirm)
            if item is not None:
                items.append(item)
    if items:
        return items

    legacy = _legacy_source_repo_hook_item(manifest, confirm=confirm)
    return [legacy] if legacy is not None else []


def _find_toml_section_bounds(lines: list[str], section_name: str) -> tuple[int | None, int]:
    start: int | None = None
    end = len(lines)
    section_header = f"[{section_name}]"
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not (stripped.startswith("[") and stripped.endswith("]")):
            continue
        if stripped.startswith("[["):
            continue
        if stripped == section_header:
            if start is None:
                start = idx
            continue
        if start is not None and idx > start:
            end = idx
            break
    return start, end


def _render_text_with_trailing_newline(lines: list[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"


def _toml_basic_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _find_toml_header_bounds(lines: list[str], header: str) -> tuple[int | None, int]:
    start: int | None = None
    end = len(lines)
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped == header:
            start = idx
            continue
        if start is not None and stripped.startswith("[") and stripped.endswith("]"):
            end = idx
            break
    return start, end


def _restore_codex_hooks_feature_content(content: str, before_state: str) -> tuple[str, bool]:
    lines = content.splitlines()
    start, end = _find_toml_section_bounds(lines, "features")
    if start is None:
        if before_state == "missing":
            return content if content.endswith("\n") else content + "\n", False
        new_content = content.rstrip("\n")
        if new_content:
            new_content += "\n\n"
        new_content += f"[features]\nhooks = {before_state}\n"
        return new_content, True

    hooks_idx: int | None = None
    for idx in range(start + 1, end):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _value = stripped.split("=", 1)
        if key.strip() == "hooks":
            hooks_idx = idx
            break

    if before_state == "missing":
        if hooks_idx is None:
            return _render_text_with_trailing_newline(lines), False
        del lines[hooks_idx]
        return _render_text_with_trailing_newline(lines), True

    replacement = f"hooks = {before_state}"
    if hooks_idx is None:
        lines.insert(end, replacement)
        return _render_text_with_trailing_newline(lines), True
    if lines[hooks_idx].strip() == replacement:
        return _render_text_with_trailing_newline(lines), False
    lines[hooks_idx] = replacement
    return _render_text_with_trailing_newline(lines), True


def _codex_project_trust_header(project_path: str) -> str:
    return f"[projects.{_toml_basic_string(project_path)}]"


def _restore_codex_project_trust_content(
    content: str,
    *,
    project_path: str,
    before_state: str,
) -> tuple[str, bool]:
    lines = content.splitlines()
    header = _codex_project_trust_header(project_path)
    start, end = _find_toml_header_bounds(lines, header)

    if start is None:
        if before_state == "missing":
            return content if content.endswith("\n") else content + "\n", False
        new_content = content.rstrip("\n")
        if new_content:
            new_content += "\n\n"
        new_content += f"{header}\ntrust_level = \"{before_state}\"\n"
        return new_content, True

    trust_idx: int | None = None
    for idx in range(start + 1, end):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _value = stripped.split("=", 1)
        if key.strip() == "trust_level":
            trust_idx = idx
            break

    if before_state == "missing":
        if trust_idx is None:
            return _render_text_with_trailing_newline(lines), False
        del lines[trust_idx]
        start, end = _find_toml_header_bounds(lines, header)
        if start is not None:
            meaningful = [
                line
                for line in lines[start + 1:end]
                if line.strip() and not line.strip().startswith("#")
            ]
            if not meaningful:
                del lines[start:end]
        return _render_text_with_trailing_newline(lines), True

    replacement = f'trust_level = "{before_state}"'
    if trust_idx is None:
        lines.insert(end, replacement)
        return _render_text_with_trailing_newline(lines), True
    if lines[trust_idx].strip() == replacement:
        return _render_text_with_trailing_newline(lines), False
    lines[trust_idx] = replacement
    return _render_text_with_trailing_newline(lines), True


def _is_allowed_codex_config_path(path: Path) -> bool:
    return _same_path(path, _codex_home() / "config.toml")


def _codex_hooks_json_has_remaining_entries() -> bool:
    hooks_path = _codex_home() / "hooks.json"
    try:
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    hooks_obj = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks_obj, dict):
        return False
    for entries in hooks_obj.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("hooks"):
                return True
    return False


def _codex_hook_feature_item_from_change(change: dict[str, Any], *, confirm: bool) -> dict[str, Any] | None:
    raw_path = change.get("path")
    before_state = str(change.get("before_state") or "").strip().lower()
    after_state = str(change.get("after_state") or "").strip().lower()
    if not isinstance(raw_path, str) or not raw_path:
        return None
    if before_state not in {"missing", "false"} or after_state != "true":
        return None

    path = Path(raw_path).expanduser()
    item: dict[str, Any] = {
        "kind": "codex-hook-feature-config",
        "target_name": "codex-hooks-feature-flag",
        "path": path.as_posix(),
        "before_state": before_state,
        "after_state": after_state,
    }
    if not _is_allowed_codex_config_path(path):
        item.update(action="manual-review", reason="outside-codex-config-path")
        return item
    if not path.exists():
        item.update(action="missing", reason="codex-config-absent")
        return item
    if _codex_hooks_json_has_remaining_entries():
        item.update(action="manual-review", reason="codex-hooks-feature-required-by-non-ghost-hooks")
        return item
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        item.update(action="manual-review", reason="codex-config-read-failed", error=str(exc))
        return item

    restored, changed = _restore_codex_hooks_feature_content(current, before_state)
    if not changed:
        item.update(action="unchanged", reason="codex-hooks-feature-already-restored")
        return item
    if not confirm:
        item.update(
            action="would-restore-codex-hooks-feature-flag",
            reason="trace-backed-codex-hooks-feature-flag",
            restored_to=before_state,
        )
        return item
    try:
        path.write_text(restored, encoding="utf-8")
    except OSError as exc:
        item.update(action="manual-review", reason="codex-config-write-failed", error=str(exc))
        return item
    item.update(
        action="restored-codex-hooks-feature-flag",
        reason="trace-backed-codex-hooks-feature-flag",
        restored_to=before_state,
    )
    return item


def _codex_hook_feature_items(manifest: dict[str, Any], *, confirm: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for change in manifest.get("system_env_changes", []):
        if isinstance(change, dict) and change.get("kind") == "codex_hooks_feature_flag":
            item = _codex_hook_feature_item_from_change(change, confirm=confirm)
            if item is not None:
                items.append(item)
    return items


def _codex_project_trust_item_from_change(change: dict[str, Any], *, confirm: bool) -> dict[str, Any] | None:
    raw_path = change.get("path")
    raw_project_path = change.get("project_path")
    before_state = str(change.get("before_state") or "").strip().lower()
    after_state = str(change.get("after_state") or "").strip().lower()
    if not isinstance(raw_path, str) or not raw_path:
        return None
    if not isinstance(raw_project_path, str) or not raw_project_path:
        return None
    if before_state not in {"missing", "untrusted", "other"} or after_state != "trusted":
        return None

    path = Path(raw_path).expanduser()
    item: dict[str, Any] = {
        "kind": "codex-project-trust-config",
        "target_name": "codex-project-trust",
        "path": path.as_posix(),
        "project_path": raw_project_path,
        "before_state": before_state,
        "after_state": after_state,
    }
    if before_state == "other":
        item.update(
            action="manual-review",
            reason="codex-project-trust-before-state-not-restorable",
        )
        return item
    if not _is_allowed_codex_config_path(path):
        item.update(action="manual-review", reason="outside-codex-config-path")
        return item
    if not path.exists():
        item.update(action="missing", reason="codex-config-absent")
        return item
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        item.update(action="manual-review", reason="codex-config-read-failed", error=str(exc))
        return item

    restored, changed = _restore_codex_project_trust_content(
        current,
        project_path=raw_project_path,
        before_state=before_state,
    )
    if not changed:
        item.update(action="unchanged", reason="codex-project-trust-already-restored")
        return item
    if not confirm:
        item.update(
            action="would-remove-codex-project-trust"
            if before_state == "missing"
            else "would-restore-codex-project-trust",
            reason="trace-backed-codex-project-trust",
            restored_to=before_state,
        )
        return item
    try:
        path.write_text(restored, encoding="utf-8")
    except OSError as exc:
        item.update(action="manual-review", reason="codex-config-write-failed", error=str(exc))
        return item
    item.update(
        action="removed-codex-project-trust"
        if before_state == "missing"
        else "restored-codex-project-trust",
        reason="trace-backed-codex-project-trust",
        restored_to=before_state,
    )
    return item


def _codex_project_trust_items(manifest: dict[str, Any], *, confirm: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for change in manifest.get("system_env_changes", []):
        if isinstance(change, dict) and change.get("kind") == "codex_project_trust":
            item = _codex_project_trust_item_from_change(change, confirm=confirm)
            if item is not None:
                items.append(item)
    return items


def _legacy_source_repo_hook_item(manifest: dict[str, Any], *, confirm: bool) -> dict[str, Any] | None:
    repo_root = _manifest_repo_root(manifest)
    if repo_root is None:
        return None

    item: dict[str, Any] = {
        "kind": "source-repo-hook-config",
        "target_name": "source-repo-core-hooks-path",
        "path": repo_root.as_posix(),
    }
    try:
        current = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--local", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        item.update(action="manual-review", reason="git-not-found")
        return item

    if current.returncode != 0:
        item.update(action="unchanged", reason="source-repo-hook-path-absent")
        return item

    hook_path = current.stdout.strip()
    item["before"] = hook_path
    if hook_path != "hooks":
        item.update(action="unchanged", reason="non-ghost-alice-hook-path")
        return item

    if not confirm:
        item.update(action="would-remove-source-repo-hook-path", reason="ghost-alice-post-merge-hook-path")
        return item

    removed = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--local", "--unset", "core.hooksPath"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if removed.returncode == 0:
        item.update(action="removed-source-repo-hook-path", reason="ghost-alice-post-merge-hook-path")
    else:
        item.update(
            action="manual-review",
            reason="source-repo-hook-path-unset-failed",
            stderr=removed.stderr.strip(),
        )
    return item


def _apply_remove_action(item: dict[str, Any], path: Path, *, confirm: bool, reason: str) -> dict[str, Any]:
    if confirm:
        _remove_path(path)
        item.update(action="removed", reason=reason)
    else:
        item.update(action="would-remove", reason=reason)
    return item


def _uninstall_backup_root(platform: str) -> Path:
    return _ghost_alice_root() / "uninstall-backup" / platform


def _quarantine_then_remove(
    item: dict[str, Any],
    path: Path,
    *,
    confirm: bool,
    reason: str,
    platform: str,
) -> dict[str, Any]:
    """Back a user-modified target up to the uninstall-backup root before removing it.

    Without --purge-modified, a USER_MODIFIED_MANAGED skill is never destroyed without a
    recoverable copy. The pending-merges quarantine is untouched here because
    uninstall cleanup does not own pending-merge quarantine decisions.
    """
    target_backup = _uninstall_backup_root(platform) / path.name
    if confirm:
        target_backup.parent.mkdir(parents=True, exist_ok=True)
        if target_backup.exists() or target_backup.is_symlink():
            _remove_path(target_backup)
        if path.is_symlink():
            os.symlink(os.readlink(path), target_backup)
        elif path.is_dir():
            shutil.copytree(path, target_backup)
        else:
            shutil.copy2(path, target_backup)
        _remove_path(path)
        item.update(action="quarantined-removed", reason=reason, backup_path=target_backup.as_posix())
    else:
        item.update(action="would-quarantine-remove", reason=reason, backup_path=target_backup.as_posix())
    return item


def _target_item(
    target: dict[str, Any],
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    platform: str,
    confirm: bool,
    purge_modified: bool = False,
) -> dict[str, Any]:
    name = str(target.get("target_name") or "")
    raw_dest = target.get("dest_path")
    mode = str(target.get("install_mode") or "unknown")
    item: dict[str, Any] = {
        "kind": "install-target",
        "target_name": name,
        "install_mode": mode,
        "path": str(raw_dest or ""),
    }

    if not name or not isinstance(raw_dest, str):
        item.update(action="manual-review", reason="invalid-target-record")
        return item

    path = Path(raw_dest).expanduser()
    item["path"] = path.as_posix()
    if not _is_allowed_target_path(path, platform):
        item.update(
            action="manual-review",
            reason="outside-allowed-roots",
            allowed_roots=[root.as_posix() for root in _platform_target_roots(platform)],
        )
        return item

    if not path.exists() and not path.is_symlink():
        item.update(action="missing", reason="target-absent", ownership=OWNERSHIP_ABSENT)
        return item

    shared_platforms = _platforms_referencing_target(
        path,
        current_manifest=manifest_path,
        current_platform=platform,
    )
    if shared_platforms:
        item.update(
            action="manual-review",
            reason="shared-with-other-platform",
            shared_platforms=shared_platforms,
        )
        return item

    classification = classify_skill_root(
        path,
        expected_asset_id=name,
        repo_root=_manifest_repo_root(manifest),
    )
    item.update(
        ownership=classification.ownership,
        reason=classification.reason,
    )
    if classification.link_target:
        item["link_target"] = classification.link_target

    if classification.ownership == OWNERSHIP_USER_MODIFIED_MANAGED:
        if purge_modified:
            return _apply_remove_action(item, path, confirm=confirm, reason="user-modified-purge")
        return _quarantine_then_remove(
            item, path, confirm=confirm, reason="user-modified-quarantine", platform=platform
        )

    if classification.ownership in REMOVABLE_TARGET_OWNERSHIPS:
        return _apply_remove_action(item, path, confirm=confirm, reason=classification.reason)

    item["action"] = "manual-review"
    return item


def _support_artifact_item(name: str, path: Path, *, confirm: bool) -> dict[str, Any]:
    item: dict[str, Any] = {
        "kind": "support-artifact",
        "target_name": name,
        "path": path.as_posix(),
    }
    if not _is_allowed_support_path(path):
        item.update(action="manual-review", reason="outside-ghost-alice-root")
        return item
    if not path.exists() and not path.is_symlink():
        item.update(action="missing", reason="support-artifact-absent")
        return item
    return _apply_remove_action(item, path, confirm=confirm, reason="trace-backed-support-artifact")


def _support_artifact_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    manifest_path = args.install_state_manifest
    install_state_dir = manifest_path.parent
    return [
        _support_artifact_item("install-state-manifest", manifest_path, confirm=args.confirm),
        _support_artifact_item(
            "install-state-events",
            install_state_dir / f"{args.platform}-events.jsonl",
            confirm=args.confirm,
        ),
        _support_artifact_item(
            "codex-hook-feature-change",
            install_state_dir / f"{args.platform}-hook-feature-change.json",
            confirm=args.confirm,
        ),
        _support_artifact_item(
            "codex-project-trust-change",
            install_state_dir / f"{args.platform}-project-trust-change.json",
            confirm=args.confirm,
        ),
        _support_artifact_item(
            "pending-merges",
            _ghost_alice_root() / "pending-merges" / args.platform,
            confirm=args.confirm,
        ),
        _support_artifact_item(
            "hook-dispatcher-assets",
            _ghost_alice_root() / "hooks",
            confirm=args.confirm,
        ),
        _support_artifact_item(
            "install-rollbacks",
            _ghost_alice_root() / "install-rollbacks",
            confirm=args.confirm,
        ),
    ]


def _global_rule_item(platform: str, *, confirm: bool) -> dict[str, Any] | None:
    if platform == "codex":
        path = _codex_home() / "AGENTS.md"
        remover = remove_codex_bootstrap
    else:
        return None

    if not path.exists():
        return {"kind": "global-rule", "path": path.as_posix(), "action": "missing", "reason": "rule-file-absent"}

    if confirm:
        result = remover(path)
        action = "removed-global-rule" if result.status in {"removed", "updated"} else "unchanged"
        return {
            "kind": "global-rule",
            "path": path.as_posix(),
            "action": action,
            "reason": result.status,
        }

    try:
        body = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return {
            "kind": "global-rule",
            "path": path.as_posix(),
            "action": "manual-review",
            "reason": "encoding-invalid",
        }
    has_ghost_alice_block = "Ghost-ALICE managed block begin" in body or body.startswith("# Ghost-ALICE")
    return {
        "kind": "global-rule",
        "path": path.as_posix(),
        "action": "would-remove-global-rule" if has_ghost_alice_block else "unchanged",
        "reason": "ghost-alice-block-present" if has_ghost_alice_block else "ghost-alice-block-absent",
    }


def _hook_item(platform: str, *, confirm: bool) -> dict[str, Any]:
    result = uninstall_hook(platform, dry_run=not confirm)
    return {
        "kind": "hook-config",
        "target_name": f"{platform}-hooks",
        "action": "hook-uninstall" if confirm else "hook-uninstall-dry-run",
        "result": result,
    }


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "would_remove": 0,
        "removed": 0,
        "manual_review": 0,
        "missing": 0,
        "unchanged": 0,
    }
    for item in items:
        action = str(item.get("action", ""))
        if action in {
            "would-remove",
            "would-remove-global-rule",
            "would-remove-source-repo-hook-path",
            "would-remove-codex-project-trust",
        }:
            summary["would_remove"] += 1
        elif action in {
            "removed",
            "removed-global-rule",
            "removed-source-repo-hook-path",
            "removed-codex-project-trust",
        }:
            summary["removed"] += 1
        elif action == "manual-review":
            summary["manual_review"] += 1
        elif action == "missing":
            summary["missing"] += 1
        elif action == "unchanged":
            summary["unchanged"] += 1
    return summary


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    manifest, manifest_error = _load_manifest(args.install_state_manifest)
    items: list[dict[str, Any]] = []
    items.append(_hook_item(args.platform, confirm=args.confirm))
    global_rule = _global_rule_item(args.platform, confirm=args.confirm)
    if global_rule is not None:
        items.append(global_rule)
    if manifest is None:
        items.append(
            {
                "kind": "install-state",
                "path": args.install_state_manifest.as_posix(),
                "action": "manual-review",
                "reason": manifest_error or "manifest-unavailable",
            }
        )
    else:
        items.extend(_source_repo_hook_items(manifest, confirm=args.confirm))
        items.extend(_codex_hook_feature_items(manifest, confirm=args.confirm))
        items.extend(_codex_project_trust_items(manifest, confirm=args.confirm))
        for target in manifest.get("targets", []):
            if isinstance(target, dict):
                items.append(
                    _target_item(
                        target,
                        manifest=manifest,
                        manifest_path=args.install_state_manifest,
                        platform=args.platform,
                        confirm=args.confirm,
                        purge_modified=getattr(args, "purge_modified", False),
                    )
                )
            else:
                items.append(
                    {
                        "kind": "install-target",
                        "action": "manual-review",
                        "reason": "invalid-target-record",
                    }
                )

    items.extend(_support_artifact_items(args))

    return {
        "schema_version": 1,
        "platform": args.platform,
        "mode": "confirm" if args.confirm else "dry-run",
        "generated_at": _now(),
        "install_state_manifest": args.install_state_manifest.as_posix(),
        "items": items,
        "summary": _summary(items),
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare_report_path(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_dir():
        raise IsADirectoryError(path)
    with path.open("a", encoding="utf-8"):
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ghost-ALICE install-state driven uninstall cleanup.")
    parser.add_argument("--platform", choices=["claude", "codex"], required=True)
    parser.add_argument("--install-state-manifest", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--purge-modified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.report_path is None:
        args.report_path = _default_report_path(args.platform, confirm=args.confirm)
    prepare_report_path(args.report_path)
    report = build_report(args)
    write_report(report, args.report_path)
    mode = "CONFIRM" if args.confirm else "DRY-RUN"
    print(f"Uninstall cleanup {mode}: {args.platform}")
    print(f"Report: {args.report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
