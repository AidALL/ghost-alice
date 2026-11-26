#!/usr/bin/env python3
"""Install-state driven cleanup for Ghost-ALICE-managed artifacts."""

from __future__ import annotations

import argparse
import contextlib
import errno
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
import content_digest
import installed_payload
from installed_payload import inventory_destination, read_inventory
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


def _load_manifest(path: Path, *, platform: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    if _linked_under(path, _ghost_alice_root()):
        return None, "manifest-invalid:linked-path"
    try:
        data = read_inventory(path, platform=platform)
    except (OSError, ValueError) as exc:
        return None, f"manifest-invalid:{exc}"
    return (data, None) if data is not None else (None, "manifest-missing")


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


def _linked_under(path: Path, root: Path) -> bool:
    return any((part.is_symlink() or _is_junction(part)) for part in (path, *path.parents) if _is_under(part, root))


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


def _manifest_repo_root(manifest: dict[str, Any], target=None) -> Path | None:
    source_root = (target or {}).get('source_root') or manifest.get("source_root")
    if isinstance(source_root, str) and source_root:
        return Path(source_root).expanduser()
    return None


def _same_path(left: Path, right: Path) -> bool:
    return _lexical_absolute(left) == _lexical_absolute(right)


def platforms_referencing_target(path: Path | None, *, current_manifest: Path,
                                  current_platform: str, include_current: bool = False,
                                  source_tree: bool = False, include_current_addons: bool = False,
                                  include_current_management: bool = True) -> list[str]:
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
        if not include_current_management and entry['provider'] == 'core' and entry['kind'] in {'runtime', 'command'}:
            hosts.discard(current_platform)
        if not os.path.lexists(dest):
            if path is None:
                # Shared installation records keep a host's selections available to repair its lost files.
                platforms.update(hosts)
            continue
        if not hosts:
            continue
        # A current host's held target remains a consumer even when its bytes
        # prevent removal. Its recorded, present reference is enough to retain support.
        if current_platform in hosts:
            platforms.add(current_platform)
            hosts.discard(current_platform)
            if not hosts:
                continue
        if (entry['baseline'] == 'unknown' or
                (entry['mode'] == 'copy' and not isinstance(entry.get('files'), dict)) or
                any(content_digest.compare(entry, dest).values())):
            raise ValueError(f'other host reference cannot be confirmed: {dest}')
        platforms.update(hosts)
    hosts = {path.stem for path in locations.install_state_manifests(current_manifest.parent)}
    addon_root = root / 'addons'
    if addon_root.is_dir():
        hosts.update(path.name for path in addon_root.iterdir() if path.is_dir())
    for host in sorted(hosts):
        if host == current_platform and not include_current and not include_current_addons:
            continue
        candidate = current_manifest.parent / f'{host}.json'
        manifest, error = _load_manifest(candidate, platform=host) if host != current_platform or include_current else (None, None)
        if error and error != 'manifest-missing':
            raise ValueError(f'{host} inventory cannot be read: {candidate}: {error}')
        addons_dir = root / 'addons' / host
        records, skipped = addon_registry.scan_records(addons_dir=addons_dir)
        if skipped:
            raise ValueError(f'other host addon records cannot be read: {addons_dir}: {skipped}')
        if manifest is not None:
            for target in manifest.get('targets', []):
                if not isinstance(target, dict) or not isinstance(target.get('dest_path'), str):
                    raise ValueError(f'invalid target in {candidate}')
                dest = inventory_destination(target['dest_path'])
                if (not references(dest, target.get('source_path')) or
                        target.get('install_mode') == 'missing' or not os.path.lexists(dest)):
                    continue
                if host == current_platform:
                    platforms.add(host)
                    continue
                if target.get('owner') == 'addon':
                    # The addon registry reads an addon's files: its loop below confirms this one by its fingerprint.
                    # A file that no registry record names stays unconfirmed.
                    if not any(_same_path(dest, Path(entry['target'])) for addon in records
                               for entry in addon['provided'] if entry.get('target')):
                        raise ValueError(f'other host target ownership cannot be confirmed: {dest}')
                    continue
                classification = classify_skill_root(dest, expected_asset_id=target.get('target_name'),
                                                     repo_root=_manifest_repo_root(manifest, target))
                if classification.ownership not in REMOVABLE_TARGET_OWNERSHIPS | {OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED}:
                    raise ValueError(f'other host target ownership cannot be confirmed: {dest}')
                platforms.add(host)
        for addon in records:
            if addon.get('platform') != host:
                raise ValueError(f'other host addon platform mismatch: {addons_dir}')
            for target in addon['provided']:
                dest = target.get('target')
                mode = target.get('install_mode', 'missing')
                if (target.get('kind') == 'adapter' or mode == 'missing' or not dest or
                        not references(Path(dest), addon.get('source')) or not os.path.lexists(dest)):
                    continue
                if host == current_platform:
                    platforms.add(host)
                    continue
                fingerprint = target.get('content_hash')
                if not fingerprint or hash_utils.hash_target(dest, mode, recorded_hash=fingerprint) != fingerprint:
                    raise ValueError(f'other host addon target cannot be confirmed: {dest}')
                platforms.add(host)
    return sorted(platforms)


def release_target_records(*, root: Path, platform: str, destination: Path,
                           kind: str, provider: str = 'core', state_path: Path | None = None) -> None:
    """Deliver a confirmed removal to each record owner; keep the discovery owner until last.

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
    state = state_path or root/'install-state'/f'{platform}.json'
    # The payload owns support destinations that do not occur in the host inventory.
    # Keep its reference until all other record owners finish, so a failed write
    # cannot make a retained file disappear from the next attempt's enumeration.
    if kind in {'command', 'runtime'}:
        install_state_writer.remove_targets(state, destinations=[destination])
    targets = (installed_payload.read(root) or {}).get('targets', {})
    if installed_payload.target_key(destination, targets) in targets:
        installed_payload.release_target(root, destination=destination, provider=provider, platform=platform)
    if kind not in {'command', 'runtime'}:
        install_state_writer.remove_targets(state, destinations=[destination])


def _pending_original_entries(root: Path, platform: str, *, require_platform: bool = False) -> list[dict]:
    pending = root/'pending-merges'/platform/'manifest.json'
    data = json.loads(pending.read_text()) if pending.exists() else {'entries': []}
    if (not isinstance(data, dict) or (require_platform and data.get('platform') != platform)
            or not isinstance(data.get('entries'), list) or
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
    if platforms_referencing_target(copy, current_manifest=root/'install-state'/f'{platform}.json',
                                     current_platform=platform, include_current=True, source_tree=True):
        return 'live-host-uses-original'
    record = installed_payload.read(root) or {}
    active = record.get('installer', {}).get('active_root')
    if active and _is_under(Path(active), copy):
        return 'active-installer-uses-original'
    service = record.get('service')
    if service is not None:
        service = installed_payload._check_service(service)
        if _is_under(Path(service['program']), copy) or _is_under(Path(service['runtime_root']), copy):
            return 'registered-service-uses-original'
    return None


def _obsolete_copy_item(copy: Path, *, root: Path, platform: str, completed: list[dict],
                        after_verified_install: bool) -> dict:
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
        if after_verified_install and marker is None and error is None and copy.is_dir() and not _is_junction(copy):
            item.update(action='preserved', reason='no-ownership-marker')
            return item
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
        targets = (installed_payload.read(root) or {}).get('targets', {})
        entry = targets.get(installed_payload.target_key(dest, targets))
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
                            apply_changes: bool = True, after_verified_install: bool = False) -> list[dict]:
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
                item = _obsolete_copy_item(copy, root=root, platform=platform, completed=completed,
                                           after_verified_install=after_verified_install)
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


def _source_repo_hook_item_from_change(change: dict[str, Any], *, apply_changes: bool) -> dict[str, Any]:
    from installer import git_hooks

    repo_root, after = Path(change["repo_root"]), change["after"]
    before = git_hooks.previous_value(change)
    before_present = before is not None
    item: dict[str, Any] = {
        "kind": "source-repo-hook-config",
        "target_name": "source-repo-core-hooks-path",
        "path": repo_root.as_posix(),
        "expected_current": after,
        "before_present": before_present,
    }
    if before_present:
        item["restore_to"] = before
    registered = "common_dir" in change
    if registered:
        repository = Path(change["common_dir"])
        place = repository / "config"
        # `git config --file` answers "no value" for a file that it cannot open.
        git_config = ["git", "--git-dir", str(repository), "config", "--local"]
        item["config_file"] = place.as_posix()
    else:
        repository = place = repo_root
        git_config = ["git", "-C", str(repo_root), "config", "--local"]

    try:
        repository.lstat()
    except (FileNotFoundError, NotADirectoryError):
        item.update(action="missing", reason="source-repository-absent")
        return item
    except OSError as exc:
        item.update(action="manual-review", reason="source-repo-hook-path-read-failed", errors=[str(exc)])
        return item

    try:
        current = subprocess.run(
            [*git_config, "--get", "core.hooksPath"],
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
                    errors=[f"git config read failed for {place}: {current.stderr.strip() or current.returncode}"])
        return item

    hook_path = current.stdout.strip()
    item["current"] = hook_path
    if hook_path != after and not (registered and git_hooks.written_here(hook_path)):
        item.update(action="unchanged", reason="non-ghost-alice-hook-path")
        return item

    if not apply_changes:
        if before_present:
            item.update(action="would-restore-source-repo-hook-path", reason="trace-backed-source-repo-hook-path")
        else:
            item.update(action="would-remove-source-repo-hook-path", reason="trace-backed-source-repo-hook-path")
        return item

    operation = "restore" if before_present else "unset"
    arguments = ["core.hooksPath", before] if before_present else ["--unset", "core.hooksPath"]
    try:
        result = subprocess.run([*git_config, *arguments],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        item.update(action="manual-review", reason=f"source-repo-hook-path-{operation}-failed", errors=[str(exc)])
        return item
    if result.returncode != 0:
        item.update(action="manual-review", reason=f"source-repo-hook-path-{operation}-failed",
                    errors=[f"git config {operation} failed for {place}: {result.stderr.strip() or result.returncode}"])
        return item
    item.update(action="restored-source-repo-hook-path" if before_present else "removed-source-repo-hook-path",
                reason="trace-backed-source-repo-hook-path")
    if before_present:
        item['restored_to'] = before
    return item


def _source_repo_hook_items(
    manifest: dict[str, Any], *, apply_changes: bool, other_platforms: list[str] | None = None,
    state_path: Path | None = None, platform: str | None = None, current_items: Sequence[dict] = (),
) -> list[dict[str, Any]]:
    """Restore shared `core.hooksPath` when full or final selected removal releases its last consumer.

    Every host installs from the one install source, so while another host has live targets the hook path
    stays for it. The last consumer restores the value that a row of `git_hooks.rows` records from before Ghost-ALICE
    wrote one, when the value is still one the installation wrote. Without a row the value stays: a value alone does
    not say which value to restore.
    """
    from installer import git_hooks

    unreadable = None
    try:
        changes = git_hooks.rows()
    except ValueError as error:
        # The record stands for the rows that it holds.
        unreadable, changes = str(error), [dict(repo_root=str(git_hooks.record_file()))]
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
    reference_error = None
    if other_platforms is None:
        try:
            other_platforms = platforms_referencing_target(None, current_manifest=state_path, current_platform=platform,
                include_current=apply_changes, include_current_management=False)
        except (OSError, ValueError) as error:
            other_platforms, reference_error = [], str(error)
    if not apply_changes and any(item.get('kind') in {'install-target', 'addon-target'} and item.get('action')
            not in {'would-remove', 'would-quarantine-remove', 'would-release-reference', 'missing'}
            for item in current_items):
        other_platforms = sorted(set(other_platforms) | {platform})
    for change in changes:
        if reference_error:
            items.append(dict(kind='source-repo-hook-config', target_name='source-repo-core-hooks-path',
                path=change['repo_root'], action='manual-review',
                reason='other-host-references-unresolved', errors=[reference_error]))
            continue
        if other_platforms:
            items.append({
                "kind": "source-repo-hook-config",
                "target_name": "source-repo-core-hooks-path",
                "path": change["repo_root"],
                "expected_current": change.get("after"),
                "action": "unchanged",
                "reason": ("source-repo-hook-path-in-use-by-current-platform" if platform in other_platforms
                           else "source-repo-hook-path-in-use-by-other-platform"),
                "shared_platforms": other_platforms,
            })
            continue
        if unreadable:
            items.append(dict(kind='source-repo-hook-config', target_name='source-repo-core-hooks-path',
                path=change['repo_root'], action='manual-review',
                reason='source-repo-hook-path-record-unreadable', errors=[unreadable]))
            continue
        items.append(_source_repo_hook_item_from_change(change, apply_changes=apply_changes))
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
    return _same_path(path, _codex_home() / "config.toml") and not _linked_under(path, _codex_home())


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


_CODEX_SETTINGS = {
    'codex-hook-feature-change': ('codex_hooks_feature_flag', _codex_hook_feature_item_from_change),
    'codex-project-trust-change': ('codex_project_trust', _codex_project_trust_item_from_change),
}


def _codex_settings_items(platform: str, manifest: dict, *, apply_changes: bool, backup_sources=None) -> list[dict]:
    if platform != 'codex':
        return []
    owners = dict(_CODEX_SETTINGS.values())
    items = []
    for change in manifest.get("system_env_changes", []):
        if isinstance(change, dict) and isinstance(change.get('kind'), str) and (owner := owners.get(change['kind'])):
            item = owner(change, apply_changes=apply_changes, backup_sources=backup_sources)
            if item is not None:
                items.append(item)
    return items


def _retired_host_evidence(platform: str, support: list[dict], manifest: dict | None = None) -> tuple[dict, set[Path]]:
    """Read independently attributable settings and support left by earlier final skill removal."""
    changes, confirmed = [], set()
    for item in support:
        name, path = item['target_name'], Path(item['path'])
        if item['action'] not in {'would-remove', 'would-quarantine-remove'} or _linked_under(path, _ghost_alice_root()):
            continue
        try:
            if name == 'pending-merges':
                manifest_path = path / 'manifest.json'
                if not manifest_path.is_file() or _linked_under(manifest_path, path):
                    continue
                _pending_original_entries(_ghost_alice_root(), platform, require_platform=True)
            elif platform == 'codex' and name in _CODEX_SETTINGS:
                change = json.loads(path.read_text(encoding='utf-8'))
                kind, owner = _CODEX_SETTINGS[name]
                config = _codex_home() / 'config.toml'
                if (not isinstance(change, dict) or (change.get('schema_version') != name + '.v1'
                        and change not in (manifest or {}).get('system_env_changes', []))
                        or change.get('kind') != kind or change.get('path') != config.as_posix()
                        or not _is_allowed_codex_config_path(config) or
                        (kind == 'codex_project_trust' and not Path(change.get('project_path') or '').is_absolute())
                        or owner(change, apply_changes=False) is None):
                    continue
                changes.append(change)
            # The scoped dispatcher item verified its bytes; `_source_repo_hook_items` reads and restores the rows.
            elif name not in {'hook-dispatcher-assets', 'source-repo-hook-path-record'}:
                continue
        except (OSError, ValueError, TypeError):
            continue  # The missing-inventory check keeps this unconfirmed path for review.
        confirmed.add(path)
    return {'system_env_changes': changes}, confirmed


def _manifest_settings(platform: str, manifest: dict, support: list[dict]) -> dict:
    """Restore attributed sidecar changes that the inventory writer did not incorporate."""
    from install_state_writer import merge_system_env_changes
    receipts = [item for item in support if item['target_name'] in {*_CODEX_SETTINGS, 'source-repo-hook-path-record'}
                and item['action'] == 'would-remove']
    recovered, valid = _retired_host_evidence(platform, receipts, manifest)
    for item in receipts:
        if Path(item['path']) not in valid:
            item.update(action='manual-review', reason='invalid-setting-change-record', removal_incomplete=True)
    return dict(manifest, system_env_changes=merge_system_env_changes(
        recovered['system_env_changes'], manifest.get('system_env_changes', [])))


def _apply_remove_action(item: dict[str, Any], path: Path, *, reason: str,
                         backup_path: Path | None = None) -> dict[str, Any]:
    if backup_path is not None:
        item['backup_path'] = backup_path.as_posix()
    _remove_path(path)
    item.update(action="quarantined-removed" if backup_path is not None else "removed", reason=reason)
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
    return _apply_remove_action(item, path, reason=reason, backup_path=target_backup)


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

    path = inventory_destination(raw_dest)
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
        repo_root=_manifest_repo_root(manifest, target),
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
            shared = platforms_referencing_target(path, current_manifest=manifest_path, current_platform=platform)
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
    if _linked_under(path.parent, _ghost_alice_root()):
        item.update(action='manual-review', reason='linked-support-parent', removal_incomplete=True)
        return item
    if not path.exists() and not path.is_symlink():
        item.update(action="missing", reason="support-artifact-absent")
        return item
    if name in _SUPPORT_RECORD_FILES and (not path.is_file() or path.is_symlink() or _is_junction(path)):
        item.update(action='manual-review', reason='support-artifact-not-regular-file', removal_incomplete=True)
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


_SUPPORT_RECORD_FILES = {'install-state-manifest', 'install-record', 'install-state-events',
                         'source-repo-hook-path-record', *_CODEX_SETTINGS}


def _apply_support_artifact_item(item, *, state_path: Path | None = None):
    if item.get('target_name') == 'runtime-core' and item.get('recorded_runtime') and item.get('action') in {'would-remove', 'missing'}:
        item.update(_runtime_artifact_item(Path(item['path']), item['platform'], state_path=state_path))
    if item.get('action') not in {'would-remove', 'would-quarantine-remove'}:
        return
    path = Path(item['path'])
    if item['target_name'] == 'management-command':
        item.update(_management_command_item(path, item['platform'], state_path=state_path))
        if item['action'] != 'would-remove':
            return
    if not _is_allowed_support_path(path) and not item.get('recorded_runtime'):
        item.update(action='manual-review', reason='outside-ghost-alice-root')
        return
    if not path.exists() and not path.is_symlink():
        item.update(action='missing', reason='support-artifact-absent')
        return
    if item['target_name'] in _SUPPORT_RECORD_FILES:
        item.update(_support_artifact_item(item['target_name'], path))
        if item['action'] == 'would-remove':
            path.unlink()
            item.update(action='removed', reason='trace-backed-support-artifact')
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
        _apply_remove_action(item, path, reason="preserve-original-recovery-material", backup_path=target_backup)
        return
    if item['target_name'] == 'publish-rollbacks':
        path.rmdir()  # The completed-journal owner has confirmed that this root is empty.
        item.update(action='removed', reason='trace-backed-support-artifact')
    else:
        _apply_remove_action(item, path, reason="trace-backed-support-artifact")


def _management_command_item(path: Path, platform: str, *, state_path: Path | None = None) -> dict:
    """Use this command's recorded owners and bytes before removing its registered executable."""
    root = _ghost_alice_root()
    targets = (installed_payload.read(root) or {}).get('targets', {})
    entry = targets.get(installed_payload.target_key(path, targets), {})
    try:
        actual = platforms_referencing_target(path,
            current_manifest=state_path or root / 'install-state' / f'{platform}.json', current_platform=platform)
    except (OSError, ValueError) as error:
        return dict(kind='support-artifact', target_name='management-command', path=str(path), platform=platform,
                    action='manual-review', reason='other-host-references-unresolved', detail=str(error))
    item = _support_artifact_item('management-command', path,
        shared_platforms=sorted((set(entry.get('used_by', [])) - {platform}) | set(actual)))
    item.update(platform=platform, provider=entry.get('provider', 'core'))
    if path.parent != root / 'bin' or entry.get('kind') != 'command':
        item.update(action='manual-review', reason='management-command-record-mismatch')
    elif item['action'] == 'would-remove' and any(content_digest.compare(entry, path).values()):
        item.update(action='manual-review', reason='management-command-externally-modified')
    elif item.get('shared_platforms'):
        item['action'] = 'would-release-reference'
    return item


def _apply_managed_support(item: dict, platform: str, state_path: Path) -> None:
    """Apply one support removal and release only the references its result confirms."""
    root = _ghost_alice_root()
    if item['target_name'] == 'hook-dispatcher-assets':
        if item['action'] == 'would-remove':
            item.update(_dispatcher_item(platform, [], require_detached=True))
            if item['action'] == 'would-remove':
                dispatcher = Path(item['path'])
                dispatcher.unlink()
                item.update(action='removed', reason='trace-backed-support-artifact')
                try:
                    dispatcher.parent.rmdir()
                except OSError as error:
                    if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                        raise
        return
    targets = {}
    if item['target_name'] == 'runtime-core':
        record, targets, invalid = _read_removal_targets()
        if not item.get('recorded_runtime') and item['action'] == 'would-remove':
            if record is not None:
                item.update(action='manual-review', reason='runtime-record-changed', removal_incomplete=True)
                return
            try:
                peers = platforms_referencing_target(None, current_manifest=state_path, current_platform=platform)
                fresh, = _runtime_artifact_items(platform, peers, observation=(record, targets, invalid))
            except (OSError, ValueError) as error:
                item.update(action='manual-review', reason='other-host-references-unresolved', detail=str(error),
                            removal_incomplete=True)
                return
            item.update(fresh)
    _apply_support_artifact_item(item, state_path=state_path)
    releasing = item['action'] == 'would-release-reference'
    if item['target_name'] == 'management-command' and item['action'] in {'removed', 'missing', 'would-release-reference'}:
        release_target_records(root=root, platform=platform, destination=Path(item['path']),
                               kind='command', provider=item['provider'], state_path=state_path)
    if item['target_name'] == 'runtime-core' and item['action'] in {'removed', 'missing', 'would-release-reference'}:
        for dest, entry in targets.items():
            if (entry.get('kind') == 'runtime' and platform in entry.get('used_by', [])
                    and _is_under(Path(dest), Path(item['path']))):
                release_target_records(root=root, platform=platform, destination=Path(dest),
                                       kind='runtime', provider=entry['provider'], state_path=state_path)
        # Only the default runtime's empty containers belong to this layout.
        parent = Path(item['path']).parent
        while _is_under(parent, root / 'runtime'):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    if releasing:
        item.update(action='reference-released', file_state='present' if os.path.lexists(item['path']) else 'absent')


def _runtime_artifact_item(path: Path, platform: str, *, state_path: Path | None = None) -> dict:
    """Classify one recorded runtime destination through the existing baseline cleanup owner."""
    import install_transaction
    record = installed_payload.read(_ghost_alice_root()) or {}
    targets = record.get('targets', {})
    entry = targets.get(installed_payload.target_key(path, targets), {})
    item = dict(kind='support-artifact', target_name='runtime-core', path=str(path), platform=platform,
                recorded_runtime=True)
    if entry.get('kind') != 'runtime' or entry.get('provider') != 'core' or platform not in entry.get('used_by', []):
        return dict(item, action='manual-review', reason='runtime-record-mismatch')
    try:
        actual = platforms_referencing_target(path,
            current_manifest=state_path or _ghost_alice_root() / 'install-state' / f'{platform}.json',
            current_platform=platform, source_tree=True)
    except (OSError, ValueError) as error:
        return dict(item, action='manual-review', reason='other-host-references-unresolved', detail=str(error))
    others = sorted((set(entry['used_by']) - {platform}) | set(actual))
    if others:
        return dict(item, action='would-release-reference', reason='shared-runtime-in-use-by-other-platform', shared_platforms=others)
    if not os.path.lexists(path):
        return dict(item, action='missing', reason='support-artifact-absent')
    if entry.get('mode') == 'copy' and entry.get('baseline') in ('recorded', 'migrated') and isinstance(entry.get('files'), dict):
        reason = install_transaction.recorded_copy_cleanup_reason(path, entry, allow_missing=True)
    else:
        reason = install_transaction._original_cleanup_reason(path, {'dest': str(path), 'kind': 'runtime'}, record)
    return dict(item, action='manual-review' if reason else 'would-remove', reason=reason or 'recorded-runtime')


def _release_shared_support(items: list[dict], platform: str, state_path: Path, *, selected: bool = False) -> list[dict]:
    """Finalize command files, remaining shared references and discovery records together.

    Earlier file removal and hook/service detach stay completed. A normal failure in
    this final boundary restores only the command, binding and records needed to retry.
    Shared-only release keeps its existing committed-journal cleanup semantics.
    """
    import install_transaction
    root = _ghost_alice_root()
    commands = [item for item in items if item.get('target_name') == 'management-command'
                and item.get('action') in {'would-remove', 'missing', 'would-release-reference'}]
    references = [item for item in items if item.get('target_name') == 'runtime-core'
                  and item.get('action') == 'would-release-reference']
    records = [item for item in items if item.get('target_name') in {'install-state-manifest', 'install-record'}
               and item.get('action') == 'would-remove']
    if not commands and not references and not records:
        return []
    pending = [(item, dict(item)) for item in [*commands, *references, *records]]
    physical_commands = []
    journal = None
    try:
        for _, candidate in pending:
            if candidate.get('target_name') != 'management-command':
                continue
            candidate.update(_management_command_item(Path(candidate['path']), platform, state_path=state_path))
            if candidate['action'] == 'manual-review':
                raise ValueError(f"{candidate['path']}: {candidate['reason']}")
            if candidate['action'] == 'would-remove':
                path = Path(candidate['path'])
                if path.is_symlink() or _is_junction(path) or not path.is_file():
                    raise ValueError(f'{path}: management-command-not-regular-file')
                physical_commands.append(path)
        final_cleanup = bool(physical_commands) or not any(
            candidate.get('action') == 'would-release-reference' for _, candidate in pending)
        with install_transaction.record_update(root, cleanup_on_commit=final_cleanup) as journal:
            for record_item in records:
                file = Path(record_item['path'])
                if file.name == installed_payload.PENDING_SOURCE_FILENAME:
                    journal.preserve_metadata(file)
            for path in physical_commands:
                journal.preserve_metadata(path, restore_if_absent=True)
            journal.preserve_metadata(state_path)
            snapshot = root / 'pending-merges' / platform / 'snapshot.json'
            if snapshot.exists():
                journal.preserve_metadata(snapshot)
            for _, candidate in pending:
                if selected and candidate.get('target_name') == 'install-state-manifest':
                    manifest, error = _load_manifest(state_path)
                    if error and error != 'manifest-missing':
                        raise ValueError(f'{state_path}: {error}')
                    if manifest and manifest.get('targets'):
                        candidate.update(action='manual-review', reason='install-state-targets-remain', removal_incomplete=True)
                        continue
                if candidate.get('target_name') in {'install-state-manifest', 'install-record'}:
                    _apply_support_artifact_item(candidate)
                else:
                    _apply_managed_support(candidate, platform, state_path)
                    if candidate.get('action') not in {'removed', 'missing', 'reference-released'}:
                        raise ValueError(f"{candidate['path']}: {candidate.get('reason', 'support-removal-unresolved')}")
            record = installed_payload.read(root)
            if record and not record.get('targets') and not record.get('service') and record.get('installer', {}).get('active_root'):
                installed_payload.update(root, {'installer': {'active_root': None}}, journal=journal)
        for item, completed in pending:
            item.update(completed)
    except (OSError, ValueError, RuntimeError) as error:
        from installer.report import exception_lines
        for item, _ in pending:
            item.update(action='manual-review', reason='support-reference-update-failed',
                        errors=exception_lines(error), removal_incomplete=True,
                        file_state='present' if os.path.lexists(item['path']) else 'absent')
        if journal is not None and os.path.lexists(journal.folder):
            return [dict(kind='support-artifact', target_name='publish-rollbacks', path=str(journal.folder),
                        action='manual-review', reason='support-reference-recovery-retained',
                        removal_incomplete=True, file_state='present')]
        return []
    if final_cleanup:
        return _retry_completed_reference_cleanup(platform, state_path, apply_changes=True)
    # This shared-only journal protects metadata only, and its transaction is committed.
    # Keep cleanup outside the restore boundary and select only this journal:
    # final selected removal must not broaden into unrelated old-record cleanup.
    outcome = _completed_reference_cleanup_item(journal, apply_changes=True)
    return [outcome] if outcome.get('removal_incomplete') else []


def _completed_reference_cleanup_item(journal, *, apply_changes: bool) -> dict:
    """Apply the existing cleanup owner to one committed reference journal and report its result."""
    import install_transaction
    outcome = dict(kind='support-artifact', target_name='publish-rollbacks', path=str(journal.folder),
                   action='would-remove', reason='completed-reference-journal-cleanup')
    if not apply_changes:
        return outcome
    try:
        retained = install_transaction.cleanup_completed(_ghost_alice_root(), txids=(journal.txid,))
        if retained:
            outcome.update(action='manual-review', reason='completed-reference-journal-cleanup-pending',
                           retained_originals=list(retained), detail='; '.join(retained), removal_incomplete=True)
        else:
            outcome.update(action='removed', file_state='absent')
    except (OSError, ValueError, RuntimeError) as error:
        from installer.report import exception_lines
        outcome.update(action='manual-review', reason='completed-reference-journal-cleanup-failed',
                       errors=exception_lines(error), removal_incomplete=True,
                       file_state='present' if os.path.lexists(journal.folder) else 'absent')
    return outcome


def _read_removal_targets() -> tuple[dict | None, dict, list[dict]]:
    """Use the record owner's validation before any removal reader interprets ownership."""
    root = _ghost_alice_root()
    record = installed_payload.read(root)
    targets, invalid, causes = {}, [], []
    for path, raw in (record or {}).get('targets', {}).items():
        try:
            targets[path] = installed_payload._check_entry(path, raw)
        except installed_payload.RecordError as error:
            causes.append(str(error))
            kind = raw.get('kind') if isinstance(raw, dict) else None
            invalid.append(dict(kind='support-artifact',
                target_name={'command': 'management-command', 'runtime': 'runtime-core'}.get(kind, 'installed-target'),
                path=path, action='manual-review', reason='installed-target-ownership-unresolved',
                removal_incomplete=True, file_state='present' if os.path.lexists(path) else 'absent'))
    if causes:
        invalid.insert(0, dict(kind='support-artifact', target_name='install-record',
            path=str(installed_payload.read_path(root)), action='manual-review', reason='invalid-installed-target',
            errors=causes, removal_incomplete=True))
    return record, targets, invalid


def _retry_completed_reference_cleanup(platform: str, state_path: Path, *, apply_changes: bool) -> list[dict]:
    """Clean completed reference transactions identified by their exact retired host inventory.

    The final inventory deletion belongs to the reference transaction. Its journal
    keeps that identity if cleanup has already removed the saved inventory. This
    never authorizes restoring committed references or cleaning another publication.
    """
    import addon_registry
    import install_hooks
    import install_transaction
    root = _ghost_alice_root()
    if os.path.lexists(state_path):
        return []
    _, targets, invalid = _read_removal_targets()
    if invalid:
        return []  # The normal removal path reports invalid ownership and observes hooks.
    if any(platform in entry['used_by'] for entry in targets.values()):
        return []
    records, skipped = addon_registry.scan_records(addons_dir=root / 'addons' / platform)
    if records or skipped:
        return []
    rollbacks = install_transaction.rollbacks_root(root)
    if not rollbacks.is_dir() or rollbacks.is_symlink():
        return []
    expected = str(state_path.resolve())
    allowed = {expected, str((root / 'pending-merges' / platform / 'snapshot.json').resolve())}
    command_dir = root.resolve() / 'bin'
    candidates = []
    for folder in sorted(rollbacks.iterdir()):
        try:
            journal = install_transaction.Journal.load(root, folder.name)
        except install_transaction.InstallTransactionError:
            continue
        if (journal.phase not in install_transaction.FINISHED_PHASES or journal.data.get('host') not in {platform, 'installer'}
                or journal.data.get('targets')
                or journal.data.get('participants') or journal.data.get('source_cleanup') is not None
                or journal.data.get('preparation_dir') is not None):
            continue
        metadata = journal.data.get('metadata_records', [])
        inventory = [item for item in metadata if item['path'] == expected and item['copy'] is not None]
        # Only final retirement records a saved regular command directly in bin with restore_if_absent.
        command_rows = [item for item in metadata if Path(item['path']).parent == command_dir
                        and item.get('restore_if_absent') and item['copy'] is not None]
        commands = {item['path'] for item in command_rows}
        if len(inventory) != 1 or any(item['path'] not in allowed and item['path'] not in commands for item in metadata):
            continue
        if (command_rows or journal.phase == 'restored') and journal.data.get('host') != 'installer':
            continue
        removed = metadata if command_rows and journal.phase == 'done' else command_rows
        if any(os.path.lexists(item['path']) for item in removed):
            continue
        inventory_commands = {Path(path) for path in commands} | {root / 'bin' / Path(path).name for path in commands}
        saved, error = _load_manifest(folder / inventory[0]['copy'])
        if error == 'manifest-missing' and journal.phase == 'done':
            pass  # Cleanup may already have deleted this copy; its exact inventory path retains the identity.
        elif (error or saved.get('platform') != platform or not isinstance(saved.get('targets'), list)
              or any(not isinstance(row, dict) or not isinstance(row.get('dest_path'), str)
                     or Path(row['dest_path']) not in inventory_commands for row in saved['targets'])):
            continue
        candidates.append(journal)
    if not candidates:
        return []
    try:
        _, detached, _ = install_hooks._read_hook_references(platform)
    except (OSError, ValueError, UnicodeError):
        return []
    if not detached:
        return []
    return [_completed_reference_cleanup_item(journal, apply_changes=apply_changes) for journal in candidates]


def _runtime_artifact_items(platform: str, others: list[str], *, state_path: Path | None = None,
                            observation: tuple | None = None) -> list[dict]:
    record, targets, invalid = _read_removal_targets() if observation is None else observation
    if record is not None:
        runtimes = {path: entry for path, entry in targets.items()
                    if entry.get('kind') == 'runtime' and entry.get('provider') == 'core'}
        return [*invalid, *(_runtime_artifact_item(Path(path), platform, state_path=state_path)
                for path, entry in runtimes.items() if platform in entry['used_by'])]
    # Only recordless installations use the older default support trace. An empty
    # recorded runtime set also represents a removal that already released its rows.
    import install_hooks
    runtime = _ghost_alice_root() / 'runtime'
    if not os.path.lexists(runtime):
        return [_support_artifact_item('runtime-core', runtime)]
    consumers = set(others)
    for host in locations.PLATFORMS:
        if host == platform or host in consumers:
            continue
        inventory = locations.install_state_path(host)
        _, error = _load_manifest(inventory, platform=host)
        if error and error != 'manifest-missing':
            return [dict(kind='support-artifact', target_name='runtime-core', path=runtime.as_posix(),
                        action='manual-review', reason='other-host-references-unresolved', detail=error,
                        removal_incomplete=True)]
        try:
            settings, detached, _ = install_hooks._read_hook_references(host, peer=True)
            commands = install_hooks._hook_commands(settings.get('hooks', {}))
            if not detached or any(runtime.as_posix() + '/' in command.replace('\\', '/') for command in commands):
                consumers.add(host)
        except (OSError, ValueError) as cause:
            return [dict(kind='support-artifact', target_name='runtime-core', path=runtime.as_posix(),
                        action='manual-review', reason='hook-references-unresolved', detail=str(cause),
                        removal_incomplete=True)]
    item = _support_artifact_item('runtime-core', runtime, shared_platforms=sorted(consumers))
    if item.get('shared_platforms'):
        item['reason'] = 'shared-runtime-in-use-by-other-platform'
    return [item]


def retire_unused_management(platform: str, state_path: Path, *, apply_changes: bool = True,
                             backup_sources=None) -> list[dict]:
    """After selected removal, retire shared management only when no current consumer needs it."""
    root = _ghost_alice_root()
    record, targets, invalid = _read_removal_targets()
    record = record or {}
    manifest, error = _load_manifest(state_path, platform=platform)
    if invalid:
        retained = [*invalid, _support_artifact_item('install-state-manifest', state_path)]
        retained.extend(dict(kind='support-artifact',
            target_name='management-command' if entry['kind'] == 'command' else 'runtime-core', path=path,
            action='manual-review', reason='other-host-references-unresolved', removal_incomplete=True)
            for path, entry in targets.items() if entry.get('provider') == 'core'
            and entry['kind'] in {'runtime', 'command'} and platform in entry['used_by'])
        for item in retained:
            if item.get('action', '').startswith('would-'):
                item.update(action='manual-review', reason='other-host-references-unresolved', removal_incomplete=True)
        return retained
    missing = error == 'manifest-missing'
    if not missing and (not record or record.get('service') or any(platform in entry['used_by'] and
            (entry.get('provider') != 'core' or entry.get('kind') not in {'runtime', 'command'})
            for entry in targets.values())):
        return []
    try:
        if error and not missing:
            raise ValueError(error)
        others = platforms_referencing_target(None, current_manifest=state_path, current_platform=platform,
                                             include_current_addons=True)
    except (OSError, ValueError) as cause:
        held = {'install-state-manifest': [state_path.as_posix()], 'management-command': [], 'runtime-core': []}
        names = {'command': 'management-command', 'runtime': 'runtime-core'}
        for dest, entry in targets.items():
            if entry['provider'] == 'core' and entry['kind'] in names and platform in entry['used_by']:
                held[names[entry['kind']]].append(dest)
        return [dict(kind='support-artifact', target_name=name, path=path, action='manual-review',
                     reason='host-references-unresolved', detail=str(cause), removal_incomplete=True)
                for name, paths in held.items() for path in paths]
    if not missing and platform in others:
        return []
    if manifest is not None:
        retiring = {path for path, entry in targets.items() if platform in entry['used_by']
                    and entry['provider'] == 'core' and entry['kind'] in {'runtime', 'command'}}
        if any(installed_payload.target_key(inventory_destination(row['dest_path']), targets) not in retiring
               for row in manifest['targets']):
            state = _support_artifact_item('install-state-manifest', state_path)
            state.update(action='manual-review', reason='install-state-targets-remain', removal_incomplete=True)
            return [state]
    support = _support_artifact_items(platform, state_path, observation=(record, targets, invalid))
    retry = []
    if missing:
        retry = _retry_completed_reference_cleanup(platform, state_path, apply_changes=apply_changes)
        confirmed = set()
        if platform not in others and not record.get('service') and not any(platform in entry['used_by'] for entry in targets.values()):
            manifest, confirmed = _retired_host_evidence(platform, support)
        remaining = _missing_inventory_residue(platform, record, targets, support, retry, selected=True, confirmed=confirmed)
        if remaining or not confirmed:
            return [*retry, *remaining]
        items = [item for item in support if Path(item['path']) in confirmed]
    else:
        items = [item for item in support if item['target_name'] in {
            'pending-merges', 'hook-dispatcher-assets', 'source-repo-hook-path-record', 'install-state-events',
            'codex-hook-feature-change', 'codex-project-trust-change', 'runtime-core', 'management-command'}]
        manifest = _manifest_settings(platform, manifest, items)
    settings = _codex_settings_items(platform, manifest, apply_changes=apply_changes, backup_sources=backup_sources)
    settings.extend(_source_repo_hook_items(manifest, apply_changes=apply_changes, other_platforms=others))
    if not apply_changes or any(item.get('action') == 'manual-review' or item.get('errors') for item in settings):
        items.append(_support_artifact_item('install-state-manifest', state_path))
        if record.get('installer', {}).get('active_root'):
            items.append(dict(kind='install-binding', path=str(installed_payload.read_path(root)),
                              action='manual-review', reason='active-installer-binding-retained'))
        for item in [*settings, *items]:
            if item.get('action', '').startswith('would-'):
                item.update(action='manual-review', reason='earlier-removal-unresolved')
            if item.get('action') == 'manual-review':
                item['removal_incomplete'] = True
        return [*settings, *items]
    state = _support_artifact_item('install-state-manifest', state_path)
    for item in items:
        if item.get('action') == 'manual-review' and not item.get('shared_platforms'):
            item['removal_incomplete'] = True
    for item in items:
        if item.get('target_name') == 'management-command' or item.get('action') in {'would-release-reference', 'manual-review'}:
            continue
        if any(previous.get('removal_incomplete') for previous in items):
            item.update(action='manual-review', reason='file-removal-unresolved', removal_incomplete=True)
            continue
        try:
            _apply_managed_support(item, platform, state_path)
        except (OSError, ValueError, RuntimeError) as error:
            item.update(action='manual-review', reason='support-removal-or-record-cleanup-failed',
                        detail=str(error), removal_incomplete=True)
        if item['action'] == 'manual-review' and not item.get('shared_platforms'):
            item['removal_incomplete'] = True
    if any(item.get('removal_incomplete') for item in items):
        for item in [*items, state]:
            if item.get('action') in {'would-remove', 'would-release-reference'}:
                item.update(action='manual-review', reason='file-removal-unresolved', removal_incomplete=True)
        reference_cleanup = []
    else:
        reference_cleanup = _release_shared_support([*items, state], platform, state_path, selected=True)
    if state.get('action') != 'missing':
        items.append(state)
    items.extend(reference_cleanup)
    return [*retry, *settings, *items]


def _empty_publish_folders(path: Path, retiring: set[Path]) -> list[Path] | None:
    """Recognize empty transaction directories besides the journals already selected for cleanup."""
    if path.is_symlink() or _is_junction(path) or not path.is_dir():
        return None
    folders = [folder for folder in path.iterdir() if folder not in retiring]
    if all(re.fullmatch(r'[0-9]{8}T[0-9]{12}Z-[0-9]+-[0-9a-f]{8}', folder.name)
           and not folder.is_symlink() and not _is_junction(folder) and folder.is_dir()
           and not any(folder.iterdir()) for folder in folders):
        return folders
    return None


def _missing_inventory_residue(platform: str, record: dict | None, targets: dict, items: list[dict],
                               retry: list[dict], *, selected: bool = False,
                               confirmed: set[Path] = frozenset()) -> list[dict]:
    """Report this host's remaining references or unconfirmed ownership when its inventory is absent."""
    import install_hooks
    record = record or {}
    owned = {Path(path) for path, entry in targets.items() if platform in entry['used_by']}
    listed = {Path(item['path']) for item in items if item.get('path')}
    for path in sorted(owned - listed):
        items.append(dict(kind='support-artifact', target_name='installed-target', path=str(path),
                          action='manual-review', reason='host-install-reference-remains'))
    for name in locations.MANAGEMENT_COMMANDS:
        path = _ghost_alice_root() / 'bin' / name
        if os.path.lexists(path) and installed_payload.target_key(path, targets) not in targets and path not in listed:
            items.append(dict(kind='support-artifact', target_name='management-command', path=str(path),
                              action='manual-review', reason='management-command-record-mismatch'))
    retiring = {Path(item['path']) for item in retry if item['action'] == 'would-remove'}
    inactive = not targets and not record.get('service') and not record.get('installer', {}).get('active_root')
    # Removing skills leaves a separately registered gateway and the records needed to manage it.
    service_root = None
    if selected and record.get('service'):
        try:
            service_root = Path(installed_payload._check_service(record['service'])['runtime_root'])
        except installed_payload.RecordError:
            pass
    remaining = []
    for item in items:
        action = item.get('action', '')
        if service_root is not None and action in ('would-remove', 'would-quarantine-remove'):
            if item['target_name'] in ('install-record', 'publish-rollbacks'):
                continue
            if item.get('recorded_runtime') and Path(item['path']).is_relative_to(service_root):
                continue
        if Path(item['path']) not in owned:
            if Path(item['path']) in confirmed:
                continue
            if action == 'missing' or item.get('reason') in {'dispatcher-in-use',
                    'shared-support-in-use-by-other-platform', 'shared-runtime-in-use-by-other-platform'}:
                continue
            if item.get('target_name') == 'install-record' and inactive and action == 'would-remove':
                item.update(action='unchanged', reason='inactive-install-record')
                continue
            if item.get('target_name') == 'publish-rollbacks' and action == 'would-quarantine-remove':
                folders = _empty_publish_folders(Path(item['path']), retiring)
                if folders is not None:
                    if not folders:
                        item.update(action='would-remove', reason='trace-backed-support-artifact')
                    continue
        if action != 'manual-review':
            item.update(action='manual-review', reason='install-state-unavailable-recovery-retained')
        item['removal_incomplete'] = True
        remaining.append(item)
    if selected:
        # Selected removal retires management only after the host's hooks have been detached.
        try:
            _, detached, _ = install_hooks._read_hook_references(platform)
        except (OSError, ValueError, UnicodeError) as error:
            detached, errors = False, [str(error)]
        else:
            errors = []
        if not detached:
            remaining.append(dict(kind='hook-config', path=str(install_hooks.PLATFORMS[platform]['hook_file']()),
                                  action='manual-review', reason='hook-detach-unresolved', errors=errors,
                                  removal_incomplete=True))
    return remaining


def _installed_dispatcher_matches(path: Path, platform: str) -> bool:
    import install_hooks
    import install_transaction
    from installer import source
    record = installed_payload.read(_ghost_alice_root()) or {}
    package = record.get('installer', {}).get('active_root')
    if not package:
        package = install_hooks.observed_installation(platform, hook_fallback=False).get('core_source_root')
    if package:
        folder = Path(package).parent
        if Path(package).name == 'core' and _same_path(folder.parent, _ghost_alice_root() / 'versions'):
            if _linked_under(folder / 'version.json', _ghost_alice_root()):
                return False
            name, digest = 'core/_shared/' + path.name, install_transaction._file_hash(path)
            if source._version_metadata(folder)['files'].get(name) == digest:
                return True
            # A placement that did not finish leaves the dispatcher of another saved version in place.
            for other in folder.parent.iterdir():
                if not _linked_under(other / 'version.json', _ghost_alice_root()):
                    with contextlib.suppress(OSError, ValueError):
                        if source._version_metadata(other)['files'].get(name) == digest:
                            return True
            return False
    return install_hooks._hook_dispatcher_current(install_hooks._resolve_source_hook_dispatcher(), path)


def _dispatcher_item(platform: str, others: list[str], *, require_detached: bool = False) -> dict:
    import install_hooks
    path = install_hooks._resolve_installed_hook_dispatcher()
    item = _support_artifact_item('hook-dispatcher-assets', path, shared_platforms=others)
    if item['action'] != 'would-remove':
        return item
    try:
        if not path.is_file() or _linked_under(path, _ghost_alice_root()) or not _installed_dispatcher_matches(path, platform):
            item.update(action='manual-review', reason='dispatcher-ownership-unresolved', removal_incomplete=True)
            return item
        for host in locations.PLATFORMS:
            settings, detached, _ = install_hooks._read_hook_references(host, peer=host != platform)
            registrations = settings.get('hooks', {})
            if host == platform and not detached:
                if require_detached:
                    item.update(action='manual-review', reason='hook-detach-unresolved', removal_incomplete=True)
                    return item
                install_hooks._remove_managed_hook_entries(host, registrations)
            if any(path.as_posix() in command.replace('\\', '/') for command in install_hooks._hook_commands(registrations)):
                item.update(action='unchanged', reason='dispatcher-in-use')
                return item
    except (OSError, ValueError, TypeError, KeyError) as error:
        item.update(action='manual-review', reason='dispatcher-ownership-unresolved', errors=[str(error)], removal_incomplete=True)
    return item


def _support_artifact_items(platform: str, manifest: Path, *, observation: tuple | None = None) -> list[dict[str, Any]]:
    from installer import git_hooks
    root = _ghost_alice_root()
    reference_error = None
    try:
        others = platforms_referencing_target(None, current_manifest=manifest, current_platform=platform)
    except (OSError, ValueError) as error:
        others, reference_error = [], str(error)
    observation = _read_removal_targets() if observation is None else observation
    record, targets, _ = observation
    items = [
        _support_artifact_item('pending-merges', root / 'pending-merges' / platform,
                               recovery_platform=platform),
        _dispatcher_item(platform, others),
        _support_artifact_item('install-rollbacks', root / 'install-rollbacks',
                               shared_platforms=others, recovery_platform=platform),
        _support_artifact_item('publish-rollbacks', root / 'rollbacks',
                               shared_platforms=others, recovery_platform=platform),
        *(_support_artifact_item('install-record', root / name, shared_platforms=others)
          for name in installed_payload.INSTALL_RECORD_FILES if os.path.lexists(root / name)),
        _support_artifact_item('source-repo-hook-path-record', manifest.parent / 'source-repo-hook-path.json',
                               shared_platforms=others),
        *(_support_artifact_item('source-repo-hook-path-record', path, shared_platforms=others)
          for path in git_hooks.files() if os.path.lexists(path)),
        _support_artifact_item('addon-sidecars', root / 'addons' / platform),
        *_runtime_artifact_items(platform, others, state_path=manifest, observation=observation),
        _support_artifact_item('install-state-events', manifest.parent / f'{platform}-events.jsonl'),
        _support_artifact_item('codex-hook-feature-change', manifest.parent / f'{platform}-hook-feature-change.json'),
        _support_artifact_item('codex-project-trust-change', manifest.parent / f'{platform}-project-trust-change.json'),
        _support_artifact_item('install-state-manifest', manifest),
    ]
    for path, entry in targets.items():
        if (entry.get('kind') != 'command' or entry.get('provider') != 'core'
                or platform not in entry.get('used_by', [])):
            continue
        items.append(_management_command_item(Path(path), platform, state_path=manifest))
    for item in items:
        if item['target_name'] == 'publish-rollbacks' and others:
            # Even an absent root can be created by the final reference transaction.
            item['shared_platforms'] = others
        if reference_error and item['target_name'] in {'hook-dispatcher-assets', 'install-rollbacks',
                'publish-rollbacks', 'install-record', 'source-repo-hook-path-record', 'runtime-core'} and not item.get('recorded_runtime') and not item.get('errors') and not item.get('removal_incomplete'):
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
                  if result.get('status') in {'partial', 'error'} or result.get('errors') or any(item.get('action') in {'manual-review', 'refused'}
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
        if reason and item.get('action') in {'would-remove', 'would-quarantine-remove', 'would-release-reference'}:
            item.update(action='manual-review', reason=reason)
    for record, result in addons if hook_unresolved else unresolved:
        for item in result['items']:
            if item.get('kind') == 'addon-target' and item.get('action') in {'would-remove', 'would-release-reference'}:
                item.update(action='manual-review',
                            reason='hook-detach-unresolved' if hook_unresolved else 'addon-removal-unresolved')
    recovery_unresolved = [item for item in items if
        item.get('kind') == 'obsolete-copy' and item.get('action') == 'manual-review' or
        item.get('kind') == 'support-artifact' and (
        item.get('errors') or item.get('pending_record_sources') or
        item.get('reason') in {'recovery-junction-needs-review', 'recovery-link-time-not-preserved'})
    ]
    classification_unresolved = [item for item in items if
        item.get('kind') in {'install-target', 'install-state'} and (
            item.get('action') in {'manual-review', 'refused'} or item.get('errors')) or
        item.get('kind') == 'support-artifact' and (item.get('reason') in {
            'other-host-references-unresolved', 'invalid-setting-change-record', 'support-artifact-not-regular-file'} or
            item.get('target_name') in {'management-command', 'runtime-core', 'hook-dispatcher-assets'} and item.get('action') == 'manual-review' and
            not item.get('shared_platforms'))
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
        if item.get('kind') != 'support-artifact' or (
                item.get('action') not in {'would-remove', 'would-quarantine-remove', 'would-release-reference'}):
            continue
        reason = ('hook-detach-unresolved' if hook_unresolved else
                  'addon-removal-unresolved' if unresolved else None)
        if item.get('target_name') == 'install-state-events' and manifest_available:
            reason = None
        if recovery_unresolved and (item.get('target_name') in {
                'install-state-manifest', 'install-record', 'pending-merges', 'management-command', 'runtime-core'} or any(
                pending.get('path') and _is_under(Path(pending['path']), Path(item['path']))
                for pending in recovery_unresolved)):
            reason = 'recovery-removal-pending'
        if item.get('target_name') in retained_records or (
                retained_records and item.get('target_name') in {'install-state-manifest', 'management-command', 'runtime-core'}):
            reason = 'config-restoration-unresolved'
        if (effect_errors or classification_unresolved) and item.get('target_name') in {
                'install-state-manifest', 'install-record', 'pending-merges', 'addon-sidecars', 'management-command', 'runtime-core'}:
            reason = 'file-removal-unresolved'
        if reason:
            item.update(action='manual-review', reason=reason)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    import contextlib
    import io
    import addon_registry
    import addon_uninstall
    import install_hooks
    from installer import gateway_participant, transaction
    from installer.report import exception_lines

    manifest, manifest_error = _load_manifest(args.install_state_manifest, platform=args.platform)
    retry = []
    if manifest_error == 'manifest-missing':
        retry = _retry_completed_reference_cleanup(args.platform, args.install_state_manifest,
                                                   apply_changes=args.apply_changes)
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
    observation = _read_removal_targets()
    support = _support_artifact_items(args.platform, args.install_state_manifest, observation=observation)
    settings = _manifest_settings(args.platform, manifest, support) if manifest is not None else {}
    confirmed = set()
    if (manifest_error == 'manifest-missing' and not addons and not observation[2]
            and not any(args.platform in entry['used_by'] for entry in observation[1].values())
            and not (observation[0] or {}).get('service')):
        settings, confirmed = _retired_host_evidence(args.platform, support)
    missing_inventory_without_files = manifest_error == 'manifest-missing' and not _missing_inventory_residue(
        args.platform, observation[0], observation[1], support, retry, confirmed=confirmed) and not addons
    items.extend(support)
    if manifest is None and not missing_inventory_without_files:
        for item in items:
            if (item.get('kind') == 'support-artifact' and item.get('action', '').startswith('would-')
                    and Path(item['path']) not in confirmed):
                item.update(action='manual-review', reason='install-state-unavailable-recovery-retained')
    with contextlib.redirect_stdout(io.StringIO()):
        hooks = uninstall_hook(args.platform, dry_run=True)
    # Hook fallback can be the only evidence of a host's old record location.
    # Save that relation before detach so a failed cleanup and the next attempt retain it.
    record_locations, record_locations_error = None, None
    record_retention = transaction.RecordSourceRetention()
    if args.apply_changes:
        try:
            record_locations = transaction.installed_ledger_locations(ghost_root=_ghost_alice_root())
        except (OSError, ValueError) as error:
            record_locations_error = error
        record_retention = transaction.retain_record_source_writers(
            _ghost_alice_root(), record_locations, record_locations_error)
        for path, cause in record_retention.errors.items():
            items.append(dict(kind='record-source-references', path=path, action='manual-review',
                              reason='record-source-reference-retention-failed', errors=[cause], removal_incomplete=True))
        if args.platform in record_retention.blocked_hosts:
            hooks.details.update(hooks_detached=False)
            for path, cause in record_retention.blocked_hosts[args.platform].items():
                hooks.details['items'].append(dict(kind='record-source-references', path=path,
                    target_name=f'{args.platform}-hooks', action='manual-review',
                    reason='record-source-reference-retention-failed', errors=[cause], removal_incomplete=True))

    def cleanup_record_source(ghost, removal):
        result = dict(kind='record-source', path=removal.get('path') if isinstance(removal, dict) else None,
                      destination=removal.get('destination') if isinstance(removal, dict) else None)
        items.append(result)
        try:
            if record_locations_error is not None:
                raise ValueError(f'Cannot confirm pre-removal record references: {record_locations_error}')
            result.update(action=transaction.cleanup_record_source(ghost, removal, prior_locations=record_locations),
                          reason='completed-record-move')
        except (OSError, ValueError, RuntimeError) as error:
            result.update(action='manual-review', reason='record-source-cleanup-failed',
                          errors=exception_lines(error), removal_incomplete=True)
            raise
    gateway_item = None
    blocked = False
    try:
        gateway_item = gateway_participant.removal(_ghost_alice_root(), _removal_paths(items),
            remove_codex=args.platform == 'codex', apply_changes=False)
    except (ValueError, OSError, RuntimeError) as error:
        gateway_item = dict(kind='context-gateway', action='manual-review',
                            reason='gateway-detach-unresolved', detail=str(error), removal_incomplete=True)
        blocked = True
    if gateway_item['action'] != 'missing':
        items.insert(0, gateway_item)

    if args.apply_changes and not blocked and not errors and args.platform not in record_retention.blocked_hosts:
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
    elif args.apply_changes:
        # The preview is useful for planning, but none of these reference changes ran. A host without attached
        # hooks has none to detach.
        for item in hooks.details['items']:
            if item.get('action', '').startswith('would-') or (item.get('action') == 'hook-uninstall-dry-run'
                                                               and not item['hooks_detached']):
                item.update(action='manual-review', reason='hook-detach-not-attempted')
                item.pop('result', None)

    # Dependency retention needs the hook owner's errors before support records can be removed.
    reference_index = 1 if items and items[0] is gateway_item else 0
    items[reference_index:reference_index] = hooks.details['items']
    hook_unresolved = not hooks.details.get('hooks_detached' if args.apply_changes else 'would_detach', False)
    absent_without_artifacts = missing_inventory_without_files and not hook_unresolved
    if manifest is None and not absent_without_artifacts:
        items.append(dict(kind='install-state', path=str(args.install_state_manifest), action='manual-review',
                          reason=manifest_error or 'manifest-unavailable'))
    if not blocked and not errors and not hook_unresolved:
        items.extend(_codex_settings_items(args.platform, settings, apply_changes=args.apply_changes, backup_sources=backups))
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
                                reason='gateway-detach-unresolved', detail=str(error), removal_incomplete=True)
            if not any(item is gateway_item for item in items):
                items.insert(0, gateway_item)
            blocked = True

    if blocked:
        for _, result in addons:
            addon_uninstall.retain_addon_removal(result, 'gateway-detach-unresolved')
        for item in items:
            if item.get('action', '').startswith('would-'):
                item.update(action='manual-review', reason='gateway-detach-unresolved')
    elif args.apply_changes:
        for record, result in addons:
            if result.get('errors'):
                continue
            try:
                addon_uninstall.finalize_addon_removal(record, result, addons_dir=addons_dir,
                                                       allowed_roots=allowed_roots, platform=args.platform,
                                                       core_hooks=hooks)
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
        completed = [dict(target, action=item['action'], source_root=target.get('source_root') or (manifest or {}).get('source_root', ''),
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
        if settings:
            items.extend(_source_repo_hook_items(settings, apply_changes=True,
                state_path=args.install_state_manifest, platform=args.platform))
        _retain_dependencies(items, targets, addons, hooks, applied=True, manifest_available=manifest is not None)
        # Retire ordinary support before the final command and discovery-record boundary.
        support_items = [item for item in items if item.get('kind') == 'support-artifact']
        support_items.sort(key=lambda item: 3 if item['target_name'] in {'install-record', 'install-state-manifest'}
                           else 2 if item['target_name'] in {'management-command', 'runtime-core'}
                           or item.get('action') == 'would-release-reference'
                           else 1 if item['target_name'] == 'pending-merges' else 0)
        for item in support_items:
            if item.get('kind') == 'support-artifact':
                if item['target_name'] in {'management-command', 'install-record', 'install-state-manifest'} or item.get('action') == 'would-release-reference':
                    continue
                try:
                    if item['target_name'] == 'publish-rollbacks' and item['action'] == 'would-quarantine-remove':
                        import install_transaction
                        path = Path(item['path'])
                        if absent_without_artifacts:
                            folders = _empty_publish_folders(path, set())
                            if folders is None:
                                raise ValueError(f'Publish recovery contents changed: {path}')
                            # Preserve empty txids without journal identities in the recovery archive.
                            if not folders:
                                item['action'] = 'would-remove'
                            _apply_support_artifact_item(item)
                            continue
                        try:
                            item['retained_originals'] = list(install_transaction.cleanup_completed(_ghost_alice_root(),
                                record_source_cleanup=cleanup_record_source))
                        finally:
                            item['pending_record_sources'] = {str(journal): str(source)
                                for journal, source in record_retention.cleanup_sources.items()
                                if os.path.lexists(source)}
                        if item['pending_record_sources']:
                            item.update(action='manual-review', reason='record-source-cleanup-pending',
                                        removal_incomplete=True)
                        elif not path.is_symlink() and path.is_dir() and not any(path.iterdir()):
                            item['action'] = 'would-remove'
                    _apply_managed_support(item, args.platform, args.install_state_manifest)
                except (OSError, ValueError, RuntimeError) as error:
                    failure = exception_lines(error)
                    item.update(action='manual-review', reason='support-removal-or-record-cleanup-failed', errors=failure,
                                file_state='present' if os.path.lexists(item['path']) else 'absent')
                _retain_dependencies(items, targets, addons, hooks, applied=True, manifest_available=manifest is not None)
        if errors:
            for item in support_items:
                if item.get('action', '').startswith('would-'):
                    item.update(action='manual-review', reason='file-removal-unresolved', removal_incomplete=True)
        reference_cleanup = _release_shared_support(support_items, args.platform, args.install_state_manifest)
        items.extend(reference_cleanup)
        for publish in support_items:
            if (publish.get('target_name') != 'publish-rollbacks' or not os.path.lexists(publish['path'])
                    or publish['action'] not in {'removed', 'quarantined-removed', 'missing'}):
                continue
            if publish.get('shared_platforms'):
                publish.update(action='manual-review', reason='shared-support-in-use-by-other-platform',
                               file_state='present')
            else:
                publish.update(action='manual-review', reason='support-reference-recovery-retained',
                               removal_incomplete=True, file_state='present')
        _retain_dependencies(items, targets, addons, hooks, applied=True, manifest_available=manifest is not None)
    elif not blocked:
        items.extend(_global_rule_items(args.platform, apply_changes=False))
        if settings:
            items.extend(_source_repo_hook_items(settings, apply_changes=False,
                state_path=args.install_state_manifest, platform=args.platform, current_items=items))

    for record, result in addons:
        items.extend(item for item in result['items'] if item.get('kind') != 'addon-target')
        if result.get('errors') or any(item.get('action') in {'manual-review', 'refused'} for item in result['items']):
            items.append(dict(kind='addon', addon_id=result['addon_id'], action='manual-review',
                              reason='addon-removal-unresolved', status=result['status'], errors=result.get('errors', [])))
    items.extend(retry)
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
    from installer import lock, report as installer_report
    from installer.runner import InstallerExit
    installer_report.configure()
    args = build_parser().parse_args(argv)
    if args.report_path is None:
        args.report_path = _default_report_path(args.platform, apply_changes=args.apply_changes)
        # The reports folder keeps the last 30 reports before this one (`state_retention` uninstall-reports).
        state_retention.apply("uninstall-reports", args.report_path.parent)
    handle = None
    try:
        if args.apply_changes:
            handle = lock.acquire('uninstall', args.platform, _ghost_alice_root()/'install.lock', inherit=True)
        prepare_report_path(args.report_path)
        report = build_report(args)
        write_report(report, args.report_path)
    except InstallerExit as error:
        return error.code
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Uninstall cleanup failed: {error}', file=sys.stderr)
        return 1
    finally:
        lock.release(handle)
    mode = "APPLY" if args.apply_changes else "DRY-RUN"
    print(f"Uninstall cleanup {mode}: {args.platform}")
    print(f"Report: {args.report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    for item in report['items']:
        for _, line in installer_report.removal_item_lines(item):
            print(line)
    if report.get('removal_incomplete') or report.get('errors'):
        print(installer_report.message('removal_retry'))
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
