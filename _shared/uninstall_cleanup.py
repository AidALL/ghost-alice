#!/usr/bin/env python3
"""Install-state driven cleanup for Ghost-ALICE-managed artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from installer_assets import (
    OWNERSHIP_ABSENT,
    OWNERSHIP_GHOST_ALICE_MANAGED,
    OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED,
    classify_skill_root,
)
from global_rule_blocks import GlobalRuleBlockError, remove_claude_bootstrap, remove_codex_rule_file
from install_hooks import (
    _codex_hook_feature_state_in_content,
    _codex_project_trust_state_in_content,
    _edit_toml_key,
    _find_toml_header_bounds,
    _write_text_file,
    uninstall_hook,
)
from installer import locations
import state_retention
import runtime_config
from text_lines import split_lines

REMOVABLE_TARGET_OWNERSHIPS = {
    OWNERSHIP_GHOST_ALICE_MANAGED,
}


def _ghost_alice_root() -> Path:
    return locations.record_root()


def _codex_home() -> Path:
    return locations.codex_home()


def _claude_home() -> Path:
    return locations.claude_home()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_report_path(platform: str, *, apply_changes: bool) -> Path:
    mode = "apply" if apply_changes else "dry-run"
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
        return [locations.shared_skills_dir()]
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


def _platforms_referencing_target(path: Path | None, *, current_manifest: Path,
                                  current_platform: str, include_current: bool = False,
                                  source_tree: bool = False) -> list[str]:
    """Confirm other hosts' live references; None asks about all shared support.

    Obsolete-copy cleanup also checks live targets' sources inside the copy.
    A malformed record or a reference whose content cannot be identified is an
    unresolved dependency, not evidence that no other host uses the target.
    """
    import addon_registry
    import content_digest
    import hash_utils
    import installed_payload

    def references(dest: Path, *sources: object) -> bool:
        if path is None:
            return True
        if not source_tree:
            return _same_path(dest, path)
        if not os.path.lexists(dest):
            return False
        candidates = [dest]
        if dest.is_symlink() or _is_junction(dest):
            candidates.append(dest.resolve())
            sources = (*sources, os.readlink(dest))
        for source in sources:
            if isinstance(source, str) and source:
                candidate = Path(source).expanduser()
                candidates.append(candidate if candidate.is_absolute() else dest.parent / candidate)
        return any(_is_under(candidate, path) for candidate in candidates)

    root = current_manifest.parent.parent
    platforms: set[str] = set()
    record = installed_payload.read(root)
    for dest, entry in (record or {}).get('targets', {}).items():
        sources = (entry.get('source'), entry.get('link_target')) if isinstance(entry, dict) else ()
        if not references(Path(dest), *sources):
            continue
        entry = installed_payload._check_entry(dest, entry)
        hosts = set(entry['used_by']) - (set() if include_current else {current_platform})
        if not hosts or not os.path.lexists(dest):
            continue
        if (entry['baseline'] == 'unknown' or
                (entry['mode'] == 'copy' and not isinstance(entry.get('files'), dict)) or
                any(content_digest.compare(entry, dest).values())):
            raise ValueError(f'other host reference cannot be confirmed: {dest}')
        platforms.update(hosts)
    for host in locations.PLATFORMS:
        if host == current_platform and not include_current:
            continue
        candidate = current_manifest.parent / f'{host}.json'
        manifest, error = _load_manifest(candidate)
        if error and candidate.exists():
            raise ValueError(f'other host inventory cannot be read: {candidate}: {error}')
        if manifest is not None:
            if _manifest_platform(manifest) not in {'', host}:
                raise ValueError(f'other host inventory platform mismatch: {candidate}')
            if path is None:
                platforms.add(host)
            for target in manifest.get('targets', []):
                if not isinstance(target, dict) or not isinstance(target.get('dest_path'), str):
                    raise ValueError(f'invalid target in {candidate}')
                dest = Path(target['dest_path']).expanduser()
                if (not references(dest, target.get('source_path')) or
                        target.get('install_mode') == 'missing' or not os.path.lexists(dest)):
                    continue
                classification = classify_skill_root(dest, expected_asset_id=target.get('target_name'),
                                                     repo_root=_manifest_repo_root(manifest))
                if classification.ownership not in REMOVABLE_TARGET_OWNERSHIPS | {OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED}:
                    raise ValueError(f'other host target ownership cannot be confirmed: {dest}')
                platforms.add(host)
        addons_dir = root / 'addons' / host
        records, skipped = addon_registry.scan_records(addons_dir=addons_dir)
        if skipped:
            raise ValueError(f'other host addon records cannot be read: {addons_dir}: {skipped}')
        for addon in records:
            if addon.get('platform') != host:
                raise ValueError(f'other host addon platform mismatch: {addons_dir}')
            for target in addon['provided']:
                dest = target.get('target')
                mode = target.get('install_mode', 'missing')
                if (target.get('kind') == 'adapter' or mode == 'missing' or not dest or
                        not references(Path(dest), addon.get('source')) or not os.path.lexists(dest)):
                    continue
                fingerprint = target.get('content_hash')
                if not fingerprint or hash_utils.hash_target(dest, mode, recorded_hash=fingerprint) != fingerprint:
                    raise ValueError(f'other host addon target cannot be confirmed: {dest}')
                platforms.add(host)
    return sorted(platforms)


def release_target_records(*, root: Path, platform: str, destination: Path,
                           kind: str, provider: str = 'core', state_path: Path | None = None) -> None:
    """Deliver a confirmed removal to each record owner; keep inventory until last.

    The caller owns file/host attribution. If a record write fails, its caller
    keeps that attribution so a rerun can finish after the file is already absent.
    """
    import installed_payload
    import install_state_writer
    from installer import runner

    snapshot = root / 'pending-merges' / platform / 'snapshot.json'
    if snapshot.exists():
        result = runner.run_tool('merge-companion/scripts/snapshot_cli.py', '--prune',
            '--output', str(snapshot), '--platform', platform, '--provider', provider,
            '--prune-target', kind, str(destination), capture=True)
        if not result.ok:
            raise ValueError(f'snapshot cleanup failed for {destination}: {result.stderr or result.stdout}')
    record = installed_payload.read(root)
    if str(destination) in (record or {}).get('targets', {}):
        installed_payload.release_target(root, destination=destination, provider=provider, platform=platform)
    install_state_writer.remove_targets(state_path or root/'install-state'/f'{platform}.json',
                                       destinations=[destination])


def _pending_original_entries(root: Path, platform: str) -> list[dict]:
    pending = root/'pending-merges'/platform/'manifest.json'
    data = json.loads(pending.read_text()) if pending.exists() else {'entries': []}
    if (not isinstance(data, dict) or not isinstance(data.get('entries'), list) or
            any(not isinstance(entry, dict) for entry in data['entries'])):
        raise ValueError(f'invalid pending manifest: {pending}')
    return data['entries']


def _original_dependency_reason(copy: Path, *, root: Path, platform: str,
                                pending_entries: list[dict] | None = None) -> str | None:
    """Confirm whether a pending review, live host or registered service uses an original."""
    import installed_payload

    entries = _pending_original_entries(root, platform) if pending_entries is None else pending_entries
    if any(not entry.get('decided') and (
            (entry.get('source_path') and _is_under(Path(entry['source_path']), copy)) or
            (entry.get('backup_path') and (_is_under(copy, Path(entry['backup_path'])) or
             _is_under(Path(entry['backup_path']), copy)))) for entry in entries):
        return 'pending-original-review'
    if _platforms_referencing_target(copy, current_manifest=root/'install-state'/f'{platform}.json',
                                     current_platform=platform, include_current=True, source_tree=True):
        return 'live-host-uses-original'
    service = (installed_payload.read(root) or {}).get('service')
    if service is not None:
        service = installed_payload._check_service(service)
        if _is_under(Path(service['program']), copy) or _is_under(Path(service['runtime_root']), copy):
            return 'registered-service-uses-original'
    return None


def _obsolete_copy_item(copy: Path, *, root: Path, platform: str, completed: list[dict]) -> dict:
    """Classify an old copy against the requested scope and its actual dependencies."""
    import content_digest
    import installed_payload
    from installer_assets import _load_marker

    item = dict(kind='obsolete-copy', path=str(copy), action='manual-review', reason='cleanup-scope-unconfirmed')
    match = re.fullmatch(r'rollback-(.+)-\d{8}T\d{6}Z-\d+-[0-9a-f]{8}', copy.name)
    if match is None:
        item.update(action='preserved', reason='unidentified-original-material')
        return item
    name = match[1]
    marker = None
    if not copy.is_symlink():
        marker, error = _load_marker(copy)
        if error or not marker or marker.get('asset_id') != name:
            return item
        if marker.get('platform') != platform:
            item.update(action='preserved', reason='other-host-original')
            return item
        provider = f"addon:{marker['addon_id']}" if marker.get('owner') == 'addon' and marker.get('addon_id') else 'core'
        if marker.get('owner', 'ghost-alice') not in {'ghost-alice', 'addon'}:
            return item
        classified = classify_skill_root(copy, expected_asset_id=name, whole_copy=True)
        if classified.ownership != OWNERSHIP_GHOST_ALICE_MANAGED:
            item.update(action='preserved', reason=classified.reason)
            return item
    else:
        provider = 'core'
    pending_entries = _pending_original_entries(root, platform)
    link_source = None
    if marker is None:
        link_source = Path(os.readlink(copy))
        if not link_source.is_absolute():
            # A relative link needs its recorded base to prove source ownership.
            parents = {copy.parent.parent} if copy.parent.name == '.ghost-alice-install-rollbacks' else set()
            parents.update(Path(entry['source_path']).parent for entry in pending_entries
                if entry.get('platform', platform) == platform and isinstance(entry.get('backup_path'), str)
                and _lexical_absolute(Path(entry['backup_path'])) == _lexical_absolute(copy)
                and isinstance(entry.get('source_path'), str) and Path(entry['source_path']).is_absolute())
            if len(parents) != 1:
                return item
            link_source = parents.pop()/link_source
    matches = []
    for result in completed:
        if result.get('target_name') != name or result.get('provider', 'core') != provider:
            continue
        dest = Path(result['dest_path'])
        source = result.get('source_path')
        if not source or not dest.is_absolute():
            continue
        if marker is not None:
            if (not marker.get('source_repo') or not marker.get('source_commit') or
                    not _same_path(Path(marker['source_repo']), Path(result.get('source_root', ''))) or
                    not _is_under(Path(source), Path(marker['source_repo']))):
                continue
        elif not _same_path(link_source, Path(source)):
            continue
        matches.append(result)
    if len(matches) != 1:
        return item
    result = matches[0]
    dest = Path(result['dest_path'])
    # This is the current requested target, not evidence of the copy's old path.
    item['target_path'] = str(dest)
    if result['action'] == 'replacement-verified':
        entry = (installed_payload.read(root) or {}).get('targets', {}).get(str(dest))
        if (not isinstance(entry, dict) or entry.get('baseline') != 'recorded' or
                entry.get('provider') != provider or platform not in entry.get('used_by', []) or
                not os.path.lexists(dest) or any(content_digest.compare(entry, dest).values())):
            item['reason'] = 'replacement-not-confirmed'
            return item
    elif result['action'] not in {'removed', 'quarantined-removed', 'missing'} or os.path.lexists(dest):
        item['reason'] = 'target-removal-not-confirmed'
        return item
    reason = _original_dependency_reason(copy, root=root, platform=platform, pending_entries=pending_entries)
    if reason:
        item.update(action='preserved', reason=reason)
        return item
    item.update(action='would-remove', reason='obsolete-managed-copy')
    return item


def cleanup_obsolete_copies(*, root: Path, platform: str, completed: list[dict],
                            apply_changes: bool = True) -> list[dict]:
    """Delete only confirmed unnecessary originals; modern publish retention is separate."""
    import retired_assets
    items = []
    legacy_roots = (root/'install-rollbacks', locations.skills_dir('claude')/'.ghost-alice-install-rollbacks')
    for folder in legacy_roots:
        if folder.is_symlink():
            items.append(dict(kind='obsolete-copy', path=str(folder), action='manual-review', reason='original-root-is-link'))
            continue
        if not folder.exists():
            continue
        for copy in sorted(folder.iterdir()):
            item = dict(kind='obsolete-copy', path=str(copy), action='manual-review')
            try:
                item = _obsolete_copy_item(copy, root=root, platform=platform, completed=completed)
                if item['action'] == 'would-remove' and apply_changes:
                    _remove_path(copy)
                    item['action'] = 'removed'
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                item.update(action='manual-review', reason='original-cleanup-failed', errors=[str(error)])
            items.append(item)
        if apply_changes and not any(folder.iterdir()):
            folder.rmdir()
    for folder in (root/'deprecated-skill-backups', locations.claude_home()/'retired-command-backups'):
        if folder.is_symlink():
            items.append(dict(kind='obsolete-copy', path=str(folder), action='manual-review', reason='original-root-is-link'))
            continue
        if not folder.exists():
            continue
        for group in sorted(folder.iterdir()):
            work_path = group/'work.json'
            if group.is_symlink() or not work_path.is_file():
                items.append(dict(kind='obsolete-copy', path=str(group), action='manual-review',
                                  reason='retirement-location-unconfirmed'))
                continue
            item = dict(kind='obsolete-copy', path=str(group), action='manual-review')
            try:
                work = json.loads(work_path.read_text())
                if work.get('platform') != platform:
                    continue
                # The retirement owner validates its work receipt and expected backup location.
                found = retired_assets._work_for(Path(work['source_path']), folder, platform, work['kind'])
                if found is None or found[0] != work_path:
                    raise ValueError(f'retirement receipt mismatch: {work_path}')
                backup = Path(work['backup_path'])
                item.update(path=str(backup), work_path=str(work_path), original_path=work['source_path'])
                if work['phase'] != 'complete':
                    item['reason'] = 'retirement-record-cleanup-pending'
                elif work['ownership'] != OWNERSHIP_GHOST_ALICE_MANAGED:
                    item.update(action='preserved', reason='retired-external-changes')
                elif any(child not in {backup, work_path} for child in group.iterdir()):
                    item.update(action='preserved', reason='unrecorded-original-content')
                elif os.path.lexists(backup) and (retired_assets.content_identity(backup) != work['content_identity'] or
                        [backup.lstat().st_dev, backup.lstat().st_ino] != work.get('entry_identity')):
                    item.update(action='preserved', reason='retirement-backup-changed')
                elif (os.path.lexists(backup) and work['kind'] == 'skill' and not backup.is_symlink()
                        and classify_skill_root(backup, expected_asset_id=backup.name,
                                                whole_copy=True).ownership != OWNERSHIP_GHOST_ALICE_MANAGED):
                    item.update(action='preserved', reason='retired-external-changes')
                elif reason := _original_dependency_reason(backup, root=root, platform=platform):
                    item.update(action='preserved', reason=reason)
                else:
                    item.update(action='would-remove', reason='retirement-complete')
                    if apply_changes:
                        if os.path.lexists(backup):
                            _remove_path(backup)
                        work_path.unlink()
                        group.rmdir()
                        item['action'] = 'removed'
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                item.update(action='manual-review', reason='original-cleanup-failed', errors=[str(error)])
            items.append(item)
        if apply_changes and not any(folder.iterdir()):
            folder.rmdir()
    return items


def _source_repo_hook_item_from_change(change: dict[str, Any], *, apply_changes: bool) -> dict[str, Any] | None:
    raw_repo_root = change.get("repo_root") or change.get("path")
    if not isinstance(raw_repo_root, str) or not raw_repo_root:
        return None

    repo_root = Path(raw_repo_root).expanduser()
    after = str(change.get("after") or "")
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
    except OSError as exc:
        item.update(action="manual-review", reason="source-repo-hook-path-read-failed", errors=[str(exc)])
        return item

    if current.returncode == 1:
        item.update(action="unchanged", reason="source-repo-hook-path-absent")
        return item
    if current.returncode != 0:
        item.update(action="manual-review", reason="source-repo-hook-path-read-failed",
                    errors=[f"git config read failed for {repo_root}: {current.stderr.strip() or current.returncode}"])
        return item

    hook_path = current.stdout.strip()
    item["current"] = hook_path
    if hook_path != after:
        item.update(action="unchanged", reason="non-ghost-alice-hook-path")
        return item

    if not apply_changes:
        if before_present:
            item.update(action="would-restore-source-repo-hook-path", reason="trace-backed-source-repo-hook-path")
        else:
            item.update(action="would-remove-source-repo-hook-path", reason="trace-backed-source-repo-hook-path")
        return item

    if before_present:
        if not isinstance(before, str):
            item.update(action="manual-review", reason="invalid-source-repo-hook-before")
            return item
    operation = "restore" if before_present else "unset"
    arguments = ["core.hooksPath", before] if before_present else ["--unset", "core.hooksPath"]
    try:
        result = subprocess.run(["git", "-C", str(repo_root), "config", "--local", *arguments],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        item.update(action="manual-review", reason=f"source-repo-hook-path-{operation}-failed", errors=[str(exc)])
        return item
    if result.returncode != 0:
        item.update(action="manual-review", reason=f"source-repo-hook-path-{operation}-failed",
                    errors=[f"git config {operation} failed for {repo_root}: {result.stderr.strip() or result.returncode}"])
        return item
    item.update(action="restored-source-repo-hook-path" if before_present else "removed-source-repo-hook-path",
                reason="trace-backed-source-repo-hook-path")
    if before_present:
        item['restored_to'] = before
    return item


def _source_repo_hook_items(
    manifest: dict[str, Any], *, apply_changes: bool, other_platforms: list[str]
) -> list[dict[str, Any]]:
    """The source repository's shared `core.hooksPath`, restored only by the full removal of the last host.

    Every host installs from the one install source, so while another host's install-state remains the hook path
    stays for it. The last host restores the value the install-state records from before Ghost-ALICE wrote one,
    when the value is still the one the install wrote. Without a record of the change the value stays: a value alone
    does not say that this install wrote it.
    """
    changes = [change for change in manifest.get("system_env_changes", [])
               if isinstance(change, dict) and change.get("kind") == "source_repo_hook_path"]
    repo_root = _manifest_repo_root(manifest)
    if not changes:
        if repo_root is None:
            return []
        return [{
            "kind": "source-repo-hook-config",
            "target_name": "source-repo-core-hooks-path",
            "path": repo_root.as_posix(),
            "action": "unchanged",
            "reason": "source-repo-hook-path-unrecorded",
        }]
    items: list[dict[str, Any]] = []
    for change in changes:
        if other_platforms:
            items.append({
                "kind": "source-repo-hook-config",
                "target_name": "source-repo-core-hooks-path",
                "path": str(change.get("repo_root") or ""),
                "expected_current": change.get("after"),
                "action": "unchanged",
                "reason": "source-repo-hook-path-in-use-by-other-platform",
                "shared_platforms": other_platforms,
            })
            continue
        item = _source_repo_hook_item_from_change(change, apply_changes=apply_changes)
        if item is not None:
            items.append(item)
    return items


def _render_text_with_trailing_newline(lines: list[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"


def _toml_basic_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _restore_codex_hooks_feature_content(content: str, before_state: str) -> tuple[str, bool]:
    lines = split_lines(content)
    changed = _edit_toml_key(lines, "[features]", "hooks", None if before_state == "missing" else before_state == "true")
    return _render_text_with_trailing_newline(lines), changed


def _codex_project_trust_header(project_path: str) -> str:
    return f"[projects.{_toml_basic_string(project_path)}]"


def _restore_codex_project_trust_content(
    content: str,
    *,
    project_path: str,
    before_state: str,
) -> tuple[str, bool]:
    lines = split_lines(content)
    header = _codex_project_trust_header(project_path)
    changed = _edit_toml_key(lines, header, "trust_level", None if before_state == "missing" else before_state)
    if before_state == "missing" and changed:
        start, end = _find_toml_header_bounds(lines, header)
        if start is not None:
            meaningful = [
                line
                for line in lines[start + 1:end]
                if line.strip() and not line.strip().startswith("#")
            ]
            if not meaningful:
                del lines[start:end]
    return _render_text_with_trailing_newline(lines), changed


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


def _codex_hook_feature_item_from_change(change: dict[str, Any], *, apply_changes: bool,
                                         backup_sources=None) -> dict[str, Any] | None:
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
        item.update(action="preserved-existing", reason="codex-hooks-feature-required-by-non-ghost-hooks")
        return item
    try:
        current = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        item.update(action="manual-review", reason="codex-config-read-failed", errors=[str(exc)])
        return item

    try:
        current_state = _codex_hook_feature_state_in_content(current)
        if current_state != after_state:
            item.update(action="unchanged" if current_state == before_state else "preserved-existing",
                        reason="codex-hooks-feature-differs-from-recorded-install")
            return item
        restored, changed = _restore_codex_hooks_feature_content(current, before_state)
    except ValueError as exc:
        item.update(action="manual-review", reason="codex-config-edit-failed", errors=[str(exc)])
        return item
    if not changed:
        item.update(action="unchanged", reason="codex-hooks-feature-already-restored")
        return item
    if not apply_changes:
        item.update(
            action="would-restore-codex-hooks-feature-flag",
            reason="trace-backed-codex-hooks-feature-flag",
            restored_to=before_state,
        )
        return item
    try:
        _write_text_file(path, restored, backup_sources=backup_sources)
    except OSError as exc:
        item.update(action="manual-review", reason="codex-config-write-failed", errors=[str(exc)])
        return item
    item.update(
        action="restored-codex-hooks-feature-flag",
        reason="trace-backed-codex-hooks-feature-flag",
        restored_to=before_state,
    )
    return item


def _codex_hook_feature_items(manifest: dict[str, Any], *, apply_changes: bool, backup_sources=None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for change in manifest.get("system_env_changes", []):
        if isinstance(change, dict) and change.get("kind") == "codex_hooks_feature_flag":
            item = _codex_hook_feature_item_from_change(change, apply_changes=apply_changes, backup_sources=backup_sources)
            if item is not None:
                items.append(item)
    return items


def _codex_project_trust_item_from_change(change: dict[str, Any], *, apply_changes: bool,
                                        backup_sources=None) -> dict[str, Any] | None:
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
    except (OSError, UnicodeError) as exc:
        item.update(action="manual-review", reason="codex-config-read-failed", errors=[str(exc)])
        return item

    try:
        current_state = _codex_project_trust_state_in_content(current, raw_project_path)
        if current_state != after_state:
            item.update(action="unchanged" if current_state == before_state else "preserved-existing",
                        reason="codex-project-trust-differs-from-recorded-install")
            return item
        restored, changed = _restore_codex_project_trust_content(
            current,
            project_path=raw_project_path,
            before_state=before_state,
        )
    except ValueError as exc:
        item.update(action="manual-review", reason="codex-config-edit-failed", errors=[str(exc)])
        return item
    if not changed:
        item.update(action="unchanged", reason="codex-project-trust-already-restored")
        return item
    if not apply_changes:
        item.update(
            action="would-remove-codex-project-trust"
            if before_state == "missing"
            else "would-restore-codex-project-trust",
            reason="trace-backed-codex-project-trust",
            restored_to=before_state,
        )
        return item
    try:
        _write_text_file(path, restored, backup_sources=backup_sources)
    except OSError as exc:
        item.update(action="manual-review", reason="codex-config-write-failed", errors=[str(exc)])
        return item
    item.update(
        action="removed-codex-project-trust"
        if before_state == "missing"
        else "restored-codex-project-trust",
        reason="trace-backed-codex-project-trust",
        restored_to=before_state,
    )
    return item


def _codex_project_trust_items(manifest: dict[str, Any], *, apply_changes: bool, backup_sources=None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for change in manifest.get("system_env_changes", []):
        if isinstance(change, dict) and change.get("kind") == "codex_project_trust":
            item = _codex_project_trust_item_from_change(change, apply_changes=apply_changes, backup_sources=backup_sources)
            if item is not None:
                items.append(item)
    return items


def _apply_remove_action(item: dict[str, Any], path: Path, *, reason: str) -> dict[str, Any]:
    _remove_path(path)
    item.update(action="removed", reason=reason)
    return item


def _uninstall_backup_root(platform: str) -> Path:
    return _ghost_alice_root() / "uninstall-backup" / platform


def _quarantine_then_remove(
    item: dict[str, Any],
    path: Path,
    *,
    reason: str,
    platform: str,
) -> dict[str, Any]:
    """Back a target with an external change up to the uninstall-backup root before removing it; the installer does not record the change's writer.

    Without --purge-modified, an EXTERNALLY_MODIFIED_MANAGED skill is never destroyed without a
    recoverable copy. The pending-merges quarantine is untouched here because
    uninstall cleanup does not own the human user's pending-merge quarantine decisions.
    """
    target_backup = _uninstall_backup_root(platform) / path.name
    suffix = 1
    while target_backup.exists() or target_backup.is_symlink():
        target_backup = _uninstall_backup_root(platform) / f"{path.name}-{suffix}"
        suffix += 1
    target_backup.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        os.symlink(os.readlink(path), target_backup)
    elif path.is_dir():
        shutil.copytree(path, target_backup, symlinks=True)
    else:
        shutil.copy2(path, target_backup)
    _remove_path(path)
    item.update(action="quarantined-removed", reason=reason, backup_path=target_backup.as_posix())
    return item


def _target_item(
    target: dict[str, Any],
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    platform: str,
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
    classification = classify_skill_root(
        path,
        expected_asset_id=name,
        repo_root=_manifest_repo_root(manifest),
        whole_copy=True,
    )
    item.update(
        ownership=classification.ownership,
        reason=classification.reason,
    )
    if classification.link_target:
        item["link_target"] = classification.link_target

    if classification.ownership in REMOVABLE_TARGET_OWNERSHIPS | {OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED}:
        try:
            shared = _platforms_referencing_target(path, current_manifest=manifest_path, current_platform=platform)
        except (OSError, ValueError) as error:
            item.update(action='manual-review', reason='other-host-references-unresolved', detail=str(error))
            return item
        if shared:
            item.update(action='would-release-reference', reason='shared-with-other-platform', shared_platforms=shared)
            return item

    if classification.ownership == OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED:
        item.update(action='would-remove' if purge_modified else 'would-quarantine-remove',
                    reason='external-change-purge' if purge_modified else 'external-change-quarantine')
        return item

    if classification.ownership in REMOVABLE_TARGET_OWNERSHIPS:
        item['action'] = 'would-remove'
        return item

    item["action"] = "manual-review"
    return item


def _apply_target_item(item, target, *, manifest, manifest_path, platform, purge_modified=False):
    """Recheck the owner's live evidence, then apply the same scheduled item."""
    if item.get('action') not in {'would-remove', 'would-quarantine-remove', 'would-release-reference'}:
        return
    item.update(_target_item(target, manifest=manifest, manifest_path=manifest_path,
                             platform=platform, purge_modified=purge_modified))
    path = Path(item['path'])
    if item['action'] == 'would-remove':
        _apply_remove_action(item, path, reason=item['reason'])
    elif item['action'] == 'would-quarantine-remove':
        _quarantine_then_remove(item, path, reason=item['reason'], platform=platform)
    elif item['action'] == 'would-release-reference':
        item['action'] = 'reference-released'


def _recovery_layout(path: Path) -> tuple[Path | None, list[tuple[Path, int]]]:
    """Locate junctions and original link mtimes without following link destinations."""
    pending = [(path, Path("."))]
    links: list[tuple[Path, int]] = []
    while pending:
        current, relative = pending.pop()
        if current.is_symlink():
            links.append((relative, current.lstat().st_mtime_ns))
            continue
        if _is_junction(current):
            return current, links
        if current.is_dir():
            pending.extend((child, relative / child.name) for child in current.iterdir())
    return None, links


def _support_artifact_item(
    name: str,
    path: Path,
    *,
    shared_platforms: list[str] | None = None,
    recovery_platform: str | None = None,
) -> dict[str, Any]:
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
    if shared_platforms:
        item.update(action="manual-review", reason="shared-support-in-use-by-other-platform",
                    shared_platforms=shared_platforms)
        return item
    if recovery_platform is not None:
        junction, _ = _recovery_layout(path)
        if junction is not None:
            item.update(action="manual-review", reason="recovery-junction-needs-review",
                        junction_path=junction.as_posix())
            return item
        backup_root = _uninstall_backup_root(recovery_platform) / "support-state"
        item.update(action="would-quarantine-remove", reason="preserve-original-recovery-material",
                    backup_root=backup_root.as_posix())
        return item
    item.update(action='would-remove', reason='trace-backed-support-artifact')
    return item


def _apply_support_artifact_item(item):
    if item.get('action') not in {'would-remove', 'would-quarantine-remove'}:
        return
    path = Path(item['path'])
    if not _is_allowed_support_path(path):
        item.update(action='manual-review', reason='outside-ghost-alice-root')
        return
    if not path.exists() and not path.is_symlink():
        item.update(action='missing', reason='support-artifact-absent')
        return
    if item['action'] == 'would-quarantine-remove':
        junction, original_links = _recovery_layout(path)
        if junction is not None:
            item.update(action='manual-review', reason='recovery-junction-needs-review',
                        junction_path=junction.as_posix())
            return
        backup_root = Path(item['backup_root'])
        backup_root.mkdir(parents=True, exist_ok=True)
        # A new directory keeps earlier removal originals; copy first so failure
        # cannot erase the only original. Preserve links rather than their targets.
        backup_dir = Path(tempfile.mkdtemp(prefix=f"{item['target_name']}-", dir=backup_root))
        target_backup = backup_dir / "original"
        if path.is_symlink():
            os.symlink(os.readlink(path), target_backup)
            shutil.copystat(path, target_backup, follow_symlinks=False)
        elif path.is_dir():
            shutil.copytree(path, target_backup, symlinks=True)
        else:
            shutil.copy2(path, target_backup)
        # copystat may silently skip unsupported link timestamps. Check every
        # root/nested link against its pre-copy metadata before deleting the tree.
        for relative, original_mtime in original_links:
            copied_link = target_backup / relative
            if not copied_link.is_symlink() or copied_link.lstat().st_mtime_ns != original_mtime:
                item.update(action="manual-review", reason="recovery-link-time-not-preserved",
                            backup_path=target_backup.as_posix(), link_path=(path / relative).as_posix())
                return
        _remove_path(path)
        item.update(action="quarantined-removed", reason="preserve-original-recovery-material",
                    backup_path=target_backup.as_posix())
        return
    _apply_remove_action(item, path, reason="trace-backed-support-artifact")


def _other_platform_install_states(manifest_path: Path, current_platform: str) -> list[str]:
    """Platforms (other than current) that still have an install-state manifest.

    A platform install-state manifest is exactly ``<platform>.json``; suffixed
    sidecars such as ``<platform>-hook-feature-change.json`` are not manifests.
    """
    install_state_dir = manifest_path.parent
    if not install_state_dir.is_dir():
        return []
    others: list[str] = []
    for candidate in sorted(install_state_dir.glob("*.json")):
        if candidate == manifest_path:
            continue
        stem = candidate.stem
        if "-" in stem or "." in stem:
            continue
        if stem != current_platform:
            others.append(stem)
    return others


def _support_artifact_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    manifest = args.install_state_manifest
    root = _ghost_alice_root()
    reference_error = None
    try:
        others = _platforms_referencing_target(None, current_manifest=manifest, current_platform=args.platform)
    except (OSError, ValueError) as error:
        others, reference_error = [], str(error)
    items = [
        _support_artifact_item('pending-merges', root / 'pending-merges' / args.platform,
                               recovery_platform=args.platform),
        _support_artifact_item('hook-dispatcher-assets', root / 'hooks', shared_platforms=others),
        _support_artifact_item('install-rollbacks', root / 'install-rollbacks',
                               shared_platforms=others, recovery_platform=args.platform),
        _support_artifact_item('publish-rollbacks', root / 'rollbacks',
                               shared_platforms=others, recovery_platform=args.platform),
        _support_artifact_item('install-record', root / 'installed-payload.json', shared_platforms=others),
        _support_artifact_item('source-repo-hook-path-record', manifest.parent / 'source-repo-hook-path.json',
                               shared_platforms=others),
        _support_artifact_item('addon-sidecars', root / 'addons' / args.platform),
        _support_artifact_item('runtime-core', root / 'runtime', shared_platforms=others),
        _support_artifact_item('install-state-events', manifest.parent / f'{args.platform}-events.jsonl'),
        _support_artifact_item('codex-hook-feature-change', manifest.parent / f'{args.platform}-hook-feature-change.json'),
        _support_artifact_item('codex-project-trust-change', manifest.parent / f'{args.platform}-project-trust-change.json'),
        _support_artifact_item('install-state-manifest', manifest),
    ]
    for item in items:
        if reference_error and item['target_name'] in {'hook-dispatcher-assets', 'install-rollbacks',
                'publish-rollbacks', 'install-record', 'source-repo-hook-path-record', 'runtime-core'}:
            if item['action'] != 'missing':
                item.update(action='manual-review', reason='other-host-references-unresolved', detail=reference_error)
        if item['target_name'] == 'runtime-core' and item.get('shared_platforms'):
            item['reason'] = 'shared-runtime-in-use-by-other-platform'
    return items


def _global_rule_item(platform: str, *, apply_changes: bool, companion: bool = False) -> dict[str, Any] | None:
    if platform == "codex":
        path = _codex_home() / ("ghost-alice-governance.md" if companion else "AGENTS.md")
        remover = remove_codex_rule_file
    elif platform == "claude":
        path = _claude_home() / "CLAUDE.md"
        remover = remove_claude_bootstrap
    else:
        return None

    if path.is_symlink():
        return {"kind": "global-rule", "path": path.as_posix(), "action": "manual-review", "reason": "rule-file-symlink"}
    if not path.exists():
        return {"kind": "global-rule", "path": path.as_posix(), "action": "missing", "reason": "rule-file-absent"}
    if not path.is_file():
        return {"kind": "global-rule", "path": path.as_posix(), "action": "manual-review", "reason": "rule-path-not-file"}

    try:
        result = remover(path, apply_changes=apply_changes)
    except GlobalRuleBlockError as exc:
        if apply_changes or not isinstance(exc.__cause__, UnicodeDecodeError):
            raise
        return {
            "kind": "global-rule",
            "path": path.as_posix(),
            "action": "manual-review",
            "reason": "encoding-invalid",
        }
    action = "unchanged"
    if result.status in {"removed", "updated"}:
        action = "removed-global-rule" if apply_changes else "would-remove-global-rule"
    return {
        "kind": "global-rule",
        "path": path.as_posix(),
        "action": action,
        "reason": result.status,
    }


def _global_rule_items(platform: str, *, apply_changes: bool) -> list[dict[str, Any]]:
    primary = _global_rule_item(platform, apply_changes=apply_changes)
    items = [primary] if primary is not None else []
    if platform == "codex":
        items.append(_global_rule_item(platform, apply_changes=apply_changes, companion=True))
    return items


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
            "would-quarantine-remove",
            "would-remove-global-rule",
            "would-remove-source-repo-hook-path",
            "would-remove-codex-project-trust",
        }:
            summary["would_remove"] += 1
        elif action in {
            "removed",
            "quarantined-removed",
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


def _removal_paths(items):
    return [Path(item.get('path') or item['target']) for item in items
            if item.get('kind') in {'install-target', 'support-artifact', 'addon-target'}
            and item.get('action') in {'would-remove', 'would-quarantine-remove'}]


def _retain_dependencies(items, targets, addons, hooks, *, applied, manifest_available):
    """Refine the scheduled items from reference results; never create a second removal plan."""
    details = hooks.details
    hook_unresolved = not details.get('hooks_detached' if applied else 'would_detach', False)
    unknown = details.get('settings_state') in {'unreadable', 'unknown'}
    commands = details.get('remaining_commands', [])
    unresolved = [(record, result) for record, result in addons
                  if result.get('errors') or any(item.get('action') in {'manual-review', 'refused'}
                                                for item in result['items'])]
    unreadable = any(record is None for record, _ in unresolved)
    names = {'_shared', 'merge-companion'} if unresolved else set()
    paths = set()
    for record, _ in unresolved:
        if record is not None:
            names.update(record.get('depends_on_core', []))
            paths.update(entry['target'] for entry in record.get('provided', [])
                         if isinstance(entry, dict) and isinstance(entry.get('target'), str))
    for target, item in targets:
        reason = None
        if hook_unresolved and (unknown or target.get('target_name') in {'_shared', 'merge-companion'}
                or target.get('owner') == 'addon' or target.get('addon_id')
                or any(str(target.get('dest_path', '')) in command for command in commands)):
            reason = 'hook-detach-unresolved'
        elif unreadable or target.get('target_name') in names or target.get('dest_path') in paths:
            reason = 'addon-removal-unresolved'
        if reason and item.get('action') in {'would-remove', 'would-quarantine-remove'}:
            item.update(action='manual-review', reason=reason)
    for record, result in addons if hook_unresolved else unresolved:
        for item in result['items']:
            if item.get('kind') == 'addon-target' and item.get('action') == 'would-remove':
                item.update(action='manual-review',
                            reason='hook-detach-unresolved' if hook_unresolved else 'addon-removal-unresolved')
    recovery_unresolved = [item for item in items if
        item.get('kind') == 'obsolete-copy' and item.get('action') == 'manual-review' or
        item.get('kind') == 'support-artifact' and (
        item.get('errors') or item.get('reason') in {'recovery-junction-needs-review', 'recovery-link-time-not-preserved'})
    ]
    classification_unresolved = [item for item in items if
        item.get('kind') in {'install-target', 'install-state'} and (
            item.get('action') in {'manual-review', 'refused'} or item.get('errors')) or
        item.get('kind') == 'support-artifact' and item.get('reason') == 'other-host-references-unresolved'
    ]
    for item in [*recovery_unresolved, *classification_unresolved]:
        item['removal_incomplete'] = True
    config_records = {'codex-hook-feature-config': 'codex-hook-feature-change',
                      'codex-project-trust-config': 'codex-project-trust-change',
                      'source-repo-hook-config': 'source-repo-hook-path-record'}
    retained_records = {config_records[item['kind']] for item in items
                        if item.get('kind') in config_records
                        and (item.get('action') == 'manual-review' or item.get('errors'))}
    effect_errors = any(item.get('errors') for item in items)
    for item in items:
        if item.get('kind') != 'support-artifact' or item.get('action') not in {'would-remove', 'would-quarantine-remove'}:
            continue
        reason = ('install-state-unavailable-recovery-retained' if not manifest_available else
                  'hook-detach-unresolved' if hook_unresolved else
                  'addon-removal-unresolved' if unresolved else None)
        if item.get('target_name') == 'install-state-events' and manifest_available:
            reason = None
        if recovery_unresolved and (item.get('target_name') in {
                'install-state-manifest', 'install-record', 'pending-merges'} or any(
                pending.get('path') and _is_under(Path(pending['path']), Path(item['path']))
                for pending in recovery_unresolved)):
            reason = 'recovery-removal-pending'
        if item.get('target_name') in retained_records or (
                retained_records and item.get('target_name') == 'install-state-manifest'):
            reason = 'config-restoration-unresolved'
        if (effect_errors or classification_unresolved) and item.get('target_name') in {
                'install-state-manifest', 'install-record', 'pending-merges', 'addon-sidecars'}:
            reason = 'file-removal-unresolved'
        if reason:
            item.update(action='manual-review', reason=reason)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    import contextlib
    import io
    import addon_registry
    import addon_uninstall
    import install_hooks
    from installer import gateway_participant
    from installer.report import exception_lines

    manifest, manifest_error = _load_manifest(args.install_state_manifest)
    items = []
    addons = []
    targets = []
    errors = []
    backups = None
    if args.apply_changes:
        try:
            backups = install_hooks.settings_backup_sources(args.platform)
        except (OSError, ValueError, UnicodeError) as error:
            errors.extend(exception_lines(error))
    addons_dir = _ghost_alice_root() / 'addons' / args.platform
    allowed_roots = [*_platform_target_roots(args.platform), locations.claude_commands_dir(),
                     locations.resources_dir(args.platform)]
    for addon_id in addon_registry.iter_addon_ids_on_disk(addons_dir=addons_dir):
        try:
            record, result = addon_uninstall._prepare_uninstall(
                addon_id, addons_dir=addons_dir, allowed_roots=allowed_roots, platform=args.platform)
        except (OSError, ValueError, addon_registry.RegistryError) as error:
            record, result = None, dict(addon_id=addon_id, status='error', items=[], errors=exception_lines(error))
        addons.append((record, result))
        items.extend(item for item in result['items'] if item.get('kind') == 'addon-target')
    addon_paths = {item['target'] for item in items if item.get('target')}
    for target in (manifest or {}).get('targets', []):
        if not isinstance(target, dict):
            items.append(dict(kind='install-target', action='manual-review', reason='invalid-target-record'))
        elif target.get('dest_path') not in addon_paths:
            item = _target_item(target, manifest=manifest, manifest_path=args.install_state_manifest,
                                platform=args.platform, purge_modified=getattr(args, 'purge_modified', False))
            items.append(item)
            targets.append((target, item))
    items.extend(_support_artifact_items(args))
    if manifest is None:
        items.append(dict(kind='install-state', path=str(args.install_state_manifest), action='manual-review',
                          reason=manifest_error or 'manifest-unavailable'))
        for item in items:
            if item.get('kind') == 'support-artifact' and item.get('action', '').startswith('would-'):
                item.update(action='manual-review', reason='install-state-unavailable-recovery-retained')
    with contextlib.redirect_stdout(io.StringIO()):
        hooks = uninstall_hook(args.platform, dry_run=True)
    gateway_item = None
    blocked = False
    try:
        gateway_item = gateway_participant.removal(_ghost_alice_root(), _removal_paths(items),
            remove_codex=args.platform == 'codex', apply_changes=False)
    except (ValueError, OSError, RuntimeError) as error:
        gateway_item = dict(kind='context-gateway', action='manual-review',
                            reason='gateway-detach-unresolved', detail=str(error))
        blocked = True
    if gateway_item['action'] != 'missing':
        items.insert(0, gateway_item)

    if args.apply_changes and not blocked and not errors:
        pending_owner = hooks.details.get('pending_owner')
        core_first = pending_owner == 'core'
        if core_first:
            hooks = uninstall_hook(args.platform, backup_sources=backups)
        addons.sort(key=lambda entry: entry[1]['addon_id'] != pending_owner)
        for record, result in addons:
            try:
                addon_uninstall.detach_addon_references(record, result, addons_dir=addons_dir,
                                                        platform=args.platform, backup_sources=backups)
            except (OSError, ValueError, ExceptionGroup) as error:
                result.update(status='error', errors=exception_lines(error))
        if not core_first:
            hooks = uninstall_hook(args.platform, backup_sources=backups)
        elif addons:
            # Later owner permission writes changed the shared settings. Read the
            # final reference state through the same hook owner without another detach.
            try:
                _, detached, commands = install_hooks._read_hook_references(args.platform)
                hooks.details.update(hooks_detached=detached, remaining_commands=commands)
            except (OSError, ValueError, UnicodeError) as error:
                hooks.details.update(hooks_detached=False, settings_state='unreadable')
                hooks.details.setdefault('errors', []).extend(exception_lines(error))

    # Dependency retention needs the hook owner's errors before support records can be removed.
    reference_index = 1 if items and items[0] is gateway_item else 0
    items[reference_index:reference_index] = hooks.details['items']
    hook_unresolved = not hooks.details.get('hooks_detached' if args.apply_changes else 'would_detach', False)
    if manifest is not None and not blocked and not errors and not hook_unresolved:
        items.extend(_codex_hook_feature_items(manifest, apply_changes=args.apply_changes, backup_sources=backups))
        items.extend(_codex_project_trust_items(manifest, apply_changes=args.apply_changes, backup_sources=backups))
    _retain_dependencies(items, targets, addons, hooks, applied=args.apply_changes,
                         manifest_available=manifest is not None)
    if not blocked:
        try:
            actual_gateway = gateway_participant.removal(_ghost_alice_root(), _removal_paths(items),
                remove_codex=args.platform == 'codex', apply_changes=args.apply_changes)
            gateway_item.clear()
            gateway_item.update(actual_gateway)
            if gateway_item['action'] != 'missing' and not any(item is gateway_item for item in items):
                items.insert(0, gateway_item)
        except (ValueError, OSError, RuntimeError) as error:
            gateway_item.update(kind='context-gateway', action='manual-review',
                                reason='gateway-detach-unresolved', detail=str(error))
            if not any(item is gateway_item for item in items):
                items.insert(0, gateway_item)
            blocked = True

    if blocked:
        for item in items:
            if item.get('action') in {'would-remove', 'would-quarantine-remove'}:
                item.update(action='manual-review', reason='gateway-detach-unresolved')
    elif args.apply_changes:
        for record, result in addons:
            if result.get('errors'):
                continue
            try:
                addon_uninstall.finalize_addon_removal(record, result, addons_dir=addons_dir,
                                                       allowed_roots=allowed_roots, platform=args.platform)
            except (OSError, ValueError, ExceptionGroup) as error:
                result.update(status='error', errors=exception_lines(error))
        _retain_dependencies(items, targets, addons, hooks, applied=True, manifest_available=manifest is not None)
        for target, item in targets:
            try:
                _apply_target_item(item, target, manifest=manifest, manifest_path=args.install_state_manifest,
                                   platform=args.platform, purge_modified=getattr(args, 'purge_modified', False))
            except (OSError, ValueError, RuntimeError) as error:
                item.update(action='manual-review', reason='target-removal-failed', errors=exception_lines(error),
                            file_state='present' if os.path.lexists(target['dest_path']) else 'absent')
        completed = [dict(target, action=item['action'], source_root=(manifest or {}).get('source_root', ''),
                         provider=f"addon:{target['addon_id']}" if target.get('addon_id') else 'core')
                     for target, item in targets if item['action'] in {'removed', 'quarantined-removed', 'missing', 'reference-released'}]
        originals = cleanup_obsolete_copies(root=_ghost_alice_root(), platform=args.platform, completed=completed)
        items.extend(originals)
        for target, item in targets:
            try:
                if item['action'] in {'removed', 'quarantined-removed', 'missing', 'reference-released'}:
                    if any(original['action'] == 'manual-review' and
                            (original.get('original_path') or original.get('target_path')) in {
                            None, target['dest_path']} for original in originals):
                        item.update(action='manual-review', reason='original-cleanup-pending',
                                    file_state='present' if os.path.lexists(target['dest_path']) else 'absent')
                        continue
                    release_target_records(root=_ghost_alice_root(), platform=args.platform,
                        destination=Path(target['dest_path']), kind='support' if target['target_name'] == '_shared' else 'skill',
                        provider=f"addon:{target['addon_id']}" if target.get('addon_id') else 'core',
                        state_path=args.install_state_manifest)
            except (OSError, ValueError, RuntimeError) as error:
                failure = exception_lines(error)
                item.update(action='manual-review', reason='target-removal-or-record-cleanup-failed', errors=failure,
                            file_state='present' if os.path.lexists(target['dest_path']) else 'absent')
        items.extend(_global_rule_items(args.platform, apply_changes=True))
        if manifest is not None:
            items.extend(_source_repo_hook_items(manifest, apply_changes=True,
                other_platforms=_other_platform_install_states(args.install_state_manifest, args.platform)))
        _retain_dependencies(items, targets, addons, hooks, applied=True, manifest_available=manifest is not None)
        # Retire physical support before deleting the records needed to retry it.
        support_items = [item for item in items if item.get('kind') == 'support-artifact']
        support_items.sort(key=lambda item: item['target_name'] in {
            'pending-merges', 'install-record', 'install-state-manifest'})
        for item in support_items:
            if item.get('kind') == 'support-artifact':
                if errors and item['target_name'] == 'install-state-manifest':
                    if item.get('action', '').startswith('would-'):
                        item.update(action='manual-review', reason='file-removal-unresolved')
                try:
                    runtime_targets = {}
                    if item['target_name'] == 'runtime-core':
                        import installed_payload
                        runtime_targets = (installed_payload.read(_ghost_alice_root()) or {}).get('targets', {})
                    if item['target_name'] == 'publish-rollbacks' and item['action'] == 'would-quarantine-remove':
                        import install_transaction
                        item['retained_originals'] = list(install_transaction.cleanup_completed(_ghost_alice_root()))
                        path = Path(item['path'])
                        if not path.is_symlink() and path.is_dir() and not any(path.iterdir()):
                            item['action'] = 'would-remove'
                    _apply_support_artifact_item(item)
                    if item['target_name'] == 'runtime-core' and (item['action'] in {'removed', 'missing'} or
                            item.get('reason') == 'shared-runtime-in-use-by-other-platform'):
                        for dest, entry in runtime_targets.items():
                            if entry.get('kind') == 'runtime' and args.platform in entry.get('used_by', []):
                                release_target_records(root=_ghost_alice_root(), platform=args.platform,
                                    destination=Path(dest), kind='runtime', provider=entry['provider'],
                                    state_path=args.install_state_manifest)
                except (OSError, ValueError, RuntimeError) as error:
                    failure = exception_lines(error)
                    item.update(action='manual-review', reason='support-removal-or-record-cleanup-failed', errors=failure,
                                file_state='present' if os.path.lexists(item['path']) else 'absent')
                _retain_dependencies(items, targets, addons, hooks, applied=True, manifest_available=manifest is not None)
    elif not blocked:
        items.extend(_global_rule_items(args.platform, apply_changes=False))
        if manifest is not None:
            items.extend(_source_repo_hook_items(manifest, apply_changes=False,
                other_platforms=_other_platform_install_states(args.install_state_manifest, args.platform)))

    for record, result in addons:
        items.extend(item for item in result['items'] if item.get('kind') != 'addon-target')
        if result.get('errors') or any(item.get('action') in {'manual-review', 'refused'} for item in result['items']):
            items.append(dict(kind='addon', addon_id=result['addon_id'], action='manual-review',
                              reason='addon-removal-unresolved', status=result['status'], errors=result.get('errors', [])))
    errors.extend(message for item in items for message in item.get('errors', []))
    return dict(schema_version=2, platform=args.platform, mode='apply' if args.apply_changes else 'dry-run',
        generated_at=_now(), install_state_manifest=str(args.install_state_manifest), items=items,
        summary=_summary(items), blocked=blocked,
        partial=hook_unresolved or any(item.get('action') in {'manual-review', 'preserved-existing'} for item in items),
        removal_incomplete=any(item.get('removal_incomplete') for item in items),
        hook_error=hooks.legacy_status == 'error' or bool(hooks.details.get('errors')),
        addon_error=any(result['status'] in {'partial', 'error'} for _, result in addons), errors=errors,
        hooks_detached=hooks.details['hooks_detached'])


def write_report(report: dict[str, Any], path: Path) -> None:
    runtime_config._atomic_write_json(path, report)


def prepare_report_path(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_dir():
        raise IsADirectoryError(path)
    # Probe the directory without replacing or pre-creating the final report.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.uninstall-report-'):
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ghost-ALICE install-state driven uninstall cleanup.")
    parser.add_argument("--platform", choices=["claude", "codex"], required=True)
    parser.add_argument("--install-state-manifest", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--apply", "--confirm", dest="apply_changes", action="store_true")
    parser.add_argument("--purge-modified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    import install_lock
    args = build_parser().parse_args(argv)
    if args.report_path is None:
        args.report_path = _default_report_path(args.platform, apply_changes=args.apply_changes)
        # The reports folder keeps the last 30 reports before this one (`state_retention` uninstall-reports).
        state_retention.apply("uninstall-reports", args.report_path.parent)
    try:
        with install_lock.command_lock(_ghost_alice_root()/'install.lock', owner=f'uninstall:{args.platform}:{os.getpid()}',
                                       enabled=args.apply_changes):
            prepare_report_path(args.report_path)
            report = build_report(args)
            write_report(report, args.report_path)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Uninstall cleanup failed: {error}', file=sys.stderr)
        return 1
    mode = "APPLY" if args.apply_changes else "DRY-RUN"
    print(f"Uninstall cleanup {mode}: {args.platform}")
    print(f"Report: {args.report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    if report.get('hook_error'):
        print('partial: hook removal or permission finalization failed; review retained items')
    elif report.get('addon_error'):
        print('partial: addon removal is incomplete; review retained items')
    elif any(item.get('kind') == 'permission' and item.get('action') in {'manual-review', 'preserved-existing'} for item in report['items']):
        print('partial: permissions retained for review')
    elif report.get('partial'):
        print('partial: retained items need review')
    return 1 if (report.get('blocked') or report.get('hook_error') or report.get('addon_error') or
                 (args.apply_changes and report.get('removal_incomplete')) or report.get('errors')) else 0


if __name__ == "__main__":
    sys.exit(main())
