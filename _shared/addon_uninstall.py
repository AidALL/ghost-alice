#!/usr/bin/env python3
"""Per-addon uninstall for Ghost-ALICE.

Removes ONLY the targets recorded in one addon's sidecar, and only when:
- the target path is CONTAINED under an allowed managed root (e.g. the skills
  dir) -- a tampered sidecar cannot point ``target`` at /etc or ~/.ssh; and
- the live target still matches the ``content_hash`` the sidecar recorded at
  install time. That recorded hash is the ownership proof: a target that still
  matches is safe to remove; a target that has drifted (re-pointed symlink,
  edited copy) or has no recorded hash is PRESERVED as manual-review. Never
  clobber an asset with an external change: a change after install that the installer's records do not hold; the installer does not record who made it. Fail closed when ownership is unprovable.

Safety properties:
- Containment FIRST: ``target``'s location (its parent dir, resolved) must be
  inside an allowed root; otherwise the entry is ``refused`` and nothing is
  touched. install_mode ``missing`` means the asset was never installed -> no-op.
- Symlink-safe: a symlink target is removed by unlinking the LINK, never the
  resolved target; a copy target is removed as a directory tree.
- Two-phase + resumable: a ``<addon_id>.json.removing`` removal marker
  is written before any removal and cleared only after the sidecar is deleted.
  ``resume_pending`` finds leftover markers and finishes the removal idempotently.
- Sidecar deleted LAST: only after every target is removed or safely
  skipped; if anything is blocked (manual-review/refused), the sidecar and marker
  stay so a retry can resume and the state stays auditable. Managed addon hooks
  are still removed on partial uninstall because they are executable code and
  have exact marker ownership independent of file-target drift.

Pure standard library; reuses addon_registry (read/remove/sidecar_path) and
hash_utils (the same content-hash function the install path records with).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from installer.config import APPLY_UNSET, resolve_apply_changes

import addon_registry
import hash_utils
import pending_manifest

_REMOVING_SUFFIX = ".json.removing"
_BLOCKED_ACTIONS = frozenset({"manual-review", "refused"})


def _removing_marker_path(addon_id: str, *, addons_dir: str | os.PathLike[str]) -> Path:
    # sidecar_path validates the addon_id charset and enforces containment.
    sidecar = addon_registry.sidecar_path(addon_id, addons_dir=addons_dir)
    return sidecar.with_name(sidecar.name + ".removing")


def _write_marker(marker: Path, addon_id: str, provided: list[dict[str, Any]]) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "addon_id": addon_id,
        "stage": "removing",
        "targets": [entry.get("name") for entry in provided],
    }
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _within_allowed(dest: str, allowed_roots: list[Path]) -> bool:
    """True if dest's LOCATION (parent dir, resolved) is inside an allowed root.

    The parent is resolved (not the leaf), so a managed symlink under the skills
    dir is allowed while its link destination is irrelevant; a dest like
    ``~/.claude/skills/../../.ssh/key`` resolves its parent to ~/.ssh and is
    rejected. An empty allow-list rejects everything (fail closed).
    """
    if not allowed_roots:
        return False
    name = Path(dest).name
    if not name or name in (".", ".."):
        return False
    try:
        parent = Path(dest).parent.resolve()
    except OSError:
        return False
    for root in allowed_roots:
        try:
            root_real = Path(root).resolve()
        except OSError:
            continue
        if parent == root_real or root_real in parent.parents:
            return True
    return False


def _is_reparse_point(path: Path) -> bool:
    """True for a Windows reparse point (junction or directory/file symlink).

    Path.is_symlink() returns False for a directory junction and for an MSYS /
    Git-bash directory symlink, yet those entries must be removed as links, not
    recursed into. Detect any reparse point via the Windows file attribute.
    On non-Windows there is no st_file_attributes, so this returns False and the
    POSIX is_symlink() path handles real symlinks.
    """
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _symlink_safe_remove(path: Path) -> None:
    if path.is_symlink():
        path.unlink()  # POSIX/real symlink: remove the link, NEVER the target
        return
    if os.name == "nt" and _is_reparse_point(path):
        # Windows junction or directory reparse point (e.g. an MSYS/Git-bash directory symlink): is_symlink() is False and shutil.rmtree refuses on a link. Remove the link itself with rmdir (dir reparse), falling back to unlink (file reparse) -- never the resolved target.
        try:
            os.rmdir(path)
        except OSError:
            os.unlink(path)
        return
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _path_state(path: str | os.PathLike[str]) -> tuple[str, str | None]:
    """Observe the entry itself; denied metadata access does not establish absence."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return 'absent', None
    except OSError as error:
        return 'unknown', str(error)
    return 'present', None


def _process_target(entry: dict[str, Any], *, allowed_roots: list[Path],
                    addons_dir: str | os.PathLike[str], platform: str) -> dict[str, Any]:
    name = entry.get("name")
    dest = entry.get("target")
    mode = entry.get("install_mode") or "missing"
    recorded = entry.get("content_hash")
    item: dict[str, Any] = {"kind": "addon-target", "name": name, "target": dest, "install_mode": mode}
    if mode == "missing":
        item["action"] = "missing"  # never installed -> nothing to uninstall
        return item
    if not dest or not os.path.lexists(dest):  # lexists: do not follow the symlink
        item["action"] = "missing"
        return item
    if not _within_allowed(dest, allowed_roots):
        item["action"] = "refused"
        item["reason"] = "target escapes allowed roots"
        return item
    if not recorded:
        item["action"] = "manual-review"
        item["reason"] = "no content_hash recorded (ownership unprovable)"
        return item
    current = hash_utils.hash_target(dest, mode, recorded_hash=recorded)
    if current != recorded:
        item["action"] = "manual-review"
        item["reason"] = "modified-since-install (preserved)"
        return item
    from uninstall_cleanup import _platforms_referencing_target
    try:
        shared = _platforms_referencing_target(Path(dest),
            current_manifest=_ghost_alice_root_for_addons(addons_dir)/'install-state'/f'{platform}.json',
            current_platform=platform)
    except (OSError, ValueError) as error:
        item.update(action='manual-review', reason='other-host-references-unresolved', detail=str(error))
        return item
    item["action"] = "would-release-reference" if shared else "would-remove"
    if shared:
        item['shared_platforms'] = shared
    return item


def _apply_target(item, entry, *, allowed_roots, addons_dir, platform):
    if item['action'] not in {'would-remove', 'would-release-reference'}:
        return
    item.update(_process_target(entry, allowed_roots=allowed_roots, addons_dir=addons_dir, platform=platform))
    if item['action'] == 'would-remove':
        _symlink_safe_remove(Path(item['target']))
        item['action'] = 'removed'
    elif item['action'] == 'would-release-reference':
        item['action'] = 'reference-released'


def _ghost_alice_root_for_addons(addons_dir: str | os.PathLike[str]) -> Path:
    # addons_dir is ~/.ghost-alice/addons/<platform>.
    return Path(addons_dir).parent.parent


def _removed_file_targets(provided: list[dict[str, Any]]) -> list[tuple[str, str, Path]]:
    targets: list[tuple[str, str, Path]] = []
    for entry in provided:
        if entry.get("kind") == "adapter":
            # Retired kind: the script lives inside the owning skill; _retired_hook_items detaches the registration.
            continue
        kind = entry.get("kind")
        name = entry.get("name")
        target = entry.get("target")
        mode = entry.get("install_mode") or "missing"
        if not isinstance(kind, str) or not isinstance(name, str) or not isinstance(target, str) or mode == "missing":
            continue
        targets.append((kind, name, Path(target)))
    return targets


def _path_matches_removed_target(path: str, removed: list[tuple[str, str, Path]]) -> bool:
    candidate = Path(path)
    for _kind, _name, target in removed:
        if candidate == target:
            return True
        try:
            candidate.relative_to(target)
            return True
        except ValueError:
            continue
    return False


def _relative_path_matches_removed_skill_target(relative_path: Any, removed: list[tuple[str, str, Path]]) -> bool:
    if not isinstance(relative_path, str) or not relative_path:
        return False
    for kind, name, _target in removed:
        if kind != "skill":
            continue
        if relative_path == name or relative_path.startswith(name + "/"):
            return True
    return False


def _mark_pending_manifest_deleted_entries(addon_id: str, *, addons_dir: str | os.PathLike[str], platform: str,
                                           provided: list[dict[str, Any]]) -> None:
    removed = _removed_file_targets(provided)
    if not removed:
        return
    manifest_path = _ghost_alice_root_for_addons(addons_dir) / "pending-merges" / platform / "manifest.json"
    def read(path):
        try:
            return pending_manifest.read_json(path)
        except FileNotFoundError:
            return {"entries": []}

    def change(data):
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError(f"invalid pending manifest: {manifest_path}")
        changed = False
        decided_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("decided") is True or entry.get("change_kind") != "deleted":
                continue
            source_path = entry.get("source_path")
            relative_path = entry.get("relative_path")
            source_matches = isinstance(source_path, str) and _path_matches_removed_target(source_path, removed)
            relative_matches = _relative_path_matches_removed_skill_target(relative_path, removed)
            if not (source_matches or relative_matches):
                continue
            entry["decided"] = True
            entry["decision"] = "addon-uninstalled"
            entry["decided_at"] = decided_at
            entry["resolved_by_addon_id"] = addon_id
            changed = True
        return changed

    pending_manifest.update_manifest(manifest_path, read, change, must_exist=True)


def _addon_hook_items(record: dict[str, Any], *, platform: str, apply_changes: bool,
                     backup_sources=None) -> list[dict[str, Any]]:
    """Remove (or, in dry-run, preview) the addon's observational hooks by exact marker.

    Hook markers are recorded top-level in the sidecar (kind not in provided[]); each
    is stripped from the platform hook config via install_hooks.remove_addon_hook. Best-effort: a hook already gone
    is reported "missing", never blocks the uninstall.
    """
    specs = record.get("hooks")
    if not isinstance(specs, list) or not specs:
        return []
    addon_id = record.get("addon_id")
    if not isinstance(addon_id, str) or not addon_registry.ADDON_ID_RE.fullmatch(addon_id):
        return []
    out: list[dict[str, Any]] = []
    remover = None
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        hook_id = spec.get("hook_id")
        # Re-derive the marker from the VALIDATED record addon_id + a charset-checked hook_id; never trust spec["marker"] (a tampered sidecar could forge a core marker). A bad hook_id is skipped -> it can never address a non-addon hook.
        if not isinstance(hook_id, str) or not addon_registry.ADDON_ID_RE.fullmatch(hook_id):
            continue
        marker_str = f"[addon:{addon_id}] {hook_id}"
        item: dict[str, Any] = {"kind": "hook", "name": hook_id, "marker": marker_str}
        if not apply_changes:
            item["action"] = "would-remove"
            out.append(item)
            continue
        if remover is None:
            import install_hooks
            remover = install_hooks.remove_addon_hook
        removed = remover(marker_str, platform_key=platform, dry_run=False, backup_sources=backup_sources)
        item["action"] = "removed" if removed else "missing"
        out.append(item)
    return out


def _retired_hook_items(provided: list[dict[str, Any]], *, platform: str, apply_changes: bool,
                       backup_sources=None) -> list[dict[str, Any]]:
    """Detach the registrations an older installer recorded under the retired kind "adapter" before any file goes.

    The installer removes every retired hook as one rule (install_hooks.remove_retired_hooks); this item names the
    sidecar entries so the uninstall report says what was detached. A registration already gone is "missing".
    """
    out: list[dict[str, Any]] = []
    removed = None
    for entry in provided:
        if not isinstance(entry, dict) or entry.get("kind") != "adapter":
            continue
        name = entry.get("name")
        metadata = entry.get("metadata")
        hook_id = metadata.get("hook_id") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not addon_registry.ADDON_ID_RE.fullmatch(name):
            continue
        if not isinstance(hook_id, str) or not addon_registry.ADDON_ID_RE.fullmatch(hook_id):
            continue
        item: dict[str, Any] = {"kind": "adapter", "name": name, "marker": f"[adapter:{name}] {hook_id}"}
        if not apply_changes:
            item["action"] = "would-remove"
        else:
            if removed is None:
                import install_hooks
                removed = install_hooks.remove_retired_hooks(platform_key=platform, dry_run=False,
                                                            backup_sources=backup_sources)
            item["action"] = "removed" if removed else "missing"
        out.append(item)
    return out


def _managed_executable_items(
    record: dict[str, Any],
    provided: list[dict[str, Any]],
    *,
    platform: str,
    apply_changes: bool,
    backup_sources=None,
) -> list[dict[str, Any]]:
    permissions = _skill_permission_items(record, platform=platform, apply_changes=apply_changes,
                                         backup_sources=backup_sources)
    if any(item.get('settings_state') in {'preserved', 'unreadable'} for item in permissions):
        return permissions
    return permissions + _addon_hook_items(record, platform=platform, apply_changes=apply_changes,
                                            backup_sources=backup_sources) + _retired_hook_items(
        provided, platform=platform, apply_changes=apply_changes, backup_sources=backup_sources)


def _skill_permission_items(record, *, platform, apply_changes, backup_sources=None):
    if platform != "claude":
        return []
    import install_hooks
    import addon_skill_permissions
    settings_file = install_hooks.PLATFORMS[platform]["hook_file"]()
    core_names = set(install_hooks._discover_ghost_alice_skill_names_from_catalog())
    try:
        settings = install_hooks._read_settings(settings_file, strict=True)
    except (OSError, ValueError, UnicodeError) as error:
        return [dict(kind='permission', addon_id=record['addon_id'], action='manual-review',
                     reason='settings-read-failed', settings_state='unreadable', errors=[str(error)])]
    try:
        prepared = addon_skill_permissions.prepare_addon(record, settings_file, settings, core_names)
    except (OSError, ValueError, UnicodeError) as error:
        return [dict(kind='permission', addon_id=record['addon_id'], action='manual-review',
                     reason='permission-attribution-unresolved', settings_state='readable', detail=str(error))]
    def write(path, updated, **kwargs):
        install_hooks._write_settings(path, updated, backup_sources=backup_sources, **kwargs)
    items = addon_skill_permissions.detach(
        record, settings_file, settings, core_names, write,
        dry_run=not apply_changes, prepared=prepared,
    )
    for item in items:
        item.setdefault('settings_state', 'readable')
    return items


def _prepare_uninstall(addon_id, *, addons_dir, allowed_roots, platform):
    """Read one sidecar and classify its targets without changing references or files."""
    marker = _removing_marker_path(addon_id, addons_dir=addons_dir)
    try:
        record = addon_registry.read_record(addon_id, addons_dir=addons_dir)
    except addon_registry.RecordNotFound:
        if not marker.exists():
            raise
        return None, dict(addon_id=addon_id, status='dry-run', items=[], has_pending_marker=True)
    except addon_registry.RegistryError as error:
        return None, dict(addon_id=addon_id, status='error', reason=f'sidecar unreadable: {error}',
                          items=[], errors=[str(error)])
    provided = record['provided']
    items = [_process_target(entry, allowed_roots=allowed_roots, addons_dir=addons_dir, platform=platform)
             for entry in provided if entry.get('kind') != 'adapter']
    items.extend(_managed_executable_items(record, provided, platform=platform, apply_changes=False))
    for item in items:
        item['addon_id'] = addon_id
    return record, dict(addon_id=addon_id, status='dry-run', items=items,
                        has_pending_marker=marker.exists())


def detach_addon_references(record, result, *, addons_dir, platform, backup_sources=None):
    """Begin the existing recovery unit and detach references; retain every file."""
    if record is None:
        return
    provided = record['provided']
    _write_marker(_removing_marker_path(record['addon_id'], addons_dir=addons_dir), record['addon_id'], provided)
    references = _managed_executable_items(record, provided, platform=platform, apply_changes=True,
                                           backup_sources=backup_sources)
    for item in references:
        item['addon_id'] = record['addon_id']
    result['items'][:] = [item for item in result['items'] if item.get('kind') == 'addon-target'] + references
    blocked = [item for item in result['items'] if item['action'] in _BLOCKED_ACTIONS]
    if blocked:
        result['status'] = ('permissions-retained' if all(item.get('kind') == 'permission'
            and item.get('settings_state') == 'readable' for item in blocked) else 'partial')


def finalize_addon_removal(record, result, *, addons_dir, allowed_roots, platform):
    """Apply the scheduled file items, then retire only this addon's records."""
    addon_id = result['addon_id']
    marker = _removing_marker_path(addon_id, addons_dir=addons_dir)
    if record is None:
        if result['status'] != 'error':
            marker.unlink(missing_ok=True)
            result['status'] = 'resumed-noop'
        return
    items = result['items']
    file_items = [item for item in items if item.get('kind') == 'addon-target']
    if any(item['action'] in _BLOCKED_ACTIONS for item in items if item.get('kind') != 'addon-target'):
        for item in file_items:
            if item['action'] in {'would-remove', 'would-release-reference'}:
                item.update(action='manual-review', reason='addon-reference-unresolved')
        if result['status'] != 'permissions-retained':
            result['status'] = 'partial'
        return
    provided = record['provided']
    from uninstall_cleanup import release_target_records
    failures = []
    for item, entry in zip(file_items, (entry for entry in provided if entry.get('kind') != 'adapter')):
        try:
            _apply_target(item, entry, allowed_roots=allowed_roots, addons_dir=addons_dir, platform=platform)
            if item['action'] in {'removed', 'missing', 'reference-released'} and entry.get('target'):
                release_target_records(root=_ghost_alice_root_for_addons(addons_dir), platform=platform,
                    destination=Path(entry['target']), kind=entry['kind'], provider=f'addon:{addon_id}')
                _mark_pending_manifest_deleted_entries(addon_id, addons_dir=addons_dir, platform=platform,
                                                       provided=[entry])
        except (OSError, ValueError, RuntimeError) as error:
            state, observation_error = _path_state(entry['target'])
            item.update(action='manual-review', reason='target-removal-or-record-cleanup-failed',
                        file_state=state, detail=str(error))
            if observation_error is not None:
                item['file_state_error'] = observation_error
            failures.append(error)
    if failures:
        result.update(status='error', errors=[str(error) for error in failures])
        # Keep the original error contract for callers; successful targets stay retired.
        raise failures[0]
    if any(item['action'] in _BLOCKED_ACTIONS for item in items):
        result['status'] = 'partial'
        return
    addon_registry.remove_record(addon_id, addons_dir=addons_dir)
    marker.unlink(missing_ok=True)
    result['status'] = 'removed'


def uninstall_addon(
    addon_id: str,
    *,
    addons_dir: str | os.PathLike[str],
    allowed_roots: list[Path],
    platform: str = "claude",
    apply_changes: bool = APPLY_UNSET,
    confirm: bool = APPLY_UNSET,
    backup_sources=None,
) -> dict[str, Any]:
    """Preview or execute the full-removal stages; failures retain their type and observed result.

    Command callers read the exception's `uninstall_result`; direct API callers
    still receive the original exception. The result contains data, never the exception.
    """
    apply_changes = resolve_apply_changes(apply_changes, confirm, default=True)
    import install_hooks
    if apply_changes and backup_sources is None:
        backup_sources = install_hooks.settings_backup_sources(platform)
    record, result = _prepare_uninstall(addon_id, addons_dir=addons_dir,
                                        allowed_roots=allowed_roots, platform=platform)
    try:
        if apply_changes:
            detach_addon_references(record, result, addons_dir=addons_dir, platform=platform, backup_sources=backup_sources)
            finalize_addon_removal(record, result, addons_dir=addons_dir, allowed_roots=allowed_roots, platform=platform)
    except (OSError, ValueError, RuntimeError, addon_registry.RegistryError) as error:
        error.uninstall_result = _error_result(addon_id, error, result)
        marker = _removing_marker_path(addon_id, addons_dir=addons_dir)
        result.update(has_pending_marker=None, retry_records=[], retry_record_errors={})
        for path in (addon_registry.sidecar_path(addon_id, addons_dir=addons_dir), marker):
            state, observation_error = _path_state(path)
            if observation_error is not None:
                result['retry_record_errors'][str(path)] = observation_error
            if state == 'present':
                result['retry_records'].append(str(path))
            if path == marker and state != 'unknown':
                result['has_pending_marker'] = state == 'present'
        raise
    return result


def _error_result(addon_id, error, result=None):
    """Return this operation's observed items to command callers, even after a raised failure."""
    if result is None:
        result = getattr(error, 'uninstall_result', None)
    if result is None:
        result = dict(addon_id=addon_id, items=[])
    result.update(status='error', reason=str(error))
    return result


def resume_pending(
    *,
    addons_dir: str | os.PathLike[str],
    allowed_roots: list[Path],
    platform: str = "claude",
    apply_changes: bool = APPLY_UNSET,
    confirm: bool = APPLY_UNSET,
    backup_sources=None,
) -> list[dict[str, Any]]:
    """Finish an addon uninstall left unfinished by a failure, a killed process or an interrupt such as Ctrl-C, idempotently."""
    apply_changes = resolve_apply_changes(apply_changes, confirm, default=True)
    base = Path(addons_dir)
    if not base.is_dir():
        return []
    pending = [marker.name[:-len(_REMOVING_SUFFIX)] for marker in sorted(base.glob('*' + _REMOVING_SUFFIX))
               if addon_registry.ADDON_ID_RE.fullmatch(marker.name[:-len(_REMOVING_SUFFIX)])]
    if not pending:
        return []
    if apply_changes and backup_sources is None:
        import install_hooks
        try:
            backup_sources = install_hooks.settings_backup_sources(platform)
        except (OSError, ValueError) as exc:
            return [_error_result(stem, exc) for stem in pending]
    results: list[dict[str, Any]] = []
    for stem in pending:
        try:
            results.append(
                uninstall_addon(
                    stem, addons_dir=base, allowed_roots=allowed_roots,
                    platform=platform, apply_changes=apply_changes,
                    backup_sources=backup_sources,
                )
            )
        except addon_registry.RecordNotFound:
            # Sidecar and marker both vanished concurrently; nothing to do for this id.
            continue
        except (OSError, ValueError, RuntimeError, addon_registry.RegistryError) as exc:
            results.append(_error_result(stem, exc))
    return results


def core_skill_dependents(core_skill: str, *, addons_dir: str | os.PathLike[str]) -> list[str]:
    """addon_ids of installed addons that declare depends_on_core on ``core_skill``.

    Used to block uninstall of a core skill that an installed addon still needs.
    """
    dependents: list[str] = []
    for record in addon_registry.read_all(addons_dir=addons_dir):
        deps = record.get("depends_on_core")
        if isinstance(deps, list) and core_skill in deps:
            addon_id = record.get("addon_id")
            if isinstance(addon_id, str):
                dependents.append(addon_id)
    return sorted(dependents)


def main(argv: list[str] | None = None, *, backup_sources=None) -> int:
    import install_lock

    parser = argparse.ArgumentParser(description="Per-addon uninstall / resume for Ghost-ALICE addons")
    parser.add_argument("--addons-dir", required=True)
    parser.add_argument("--platform", choices=["claude", "codex"], default="claude")
    parser.add_argument("--skills-dir", action="append", default=[],
                        help="An allowed managed root for target containment (repeatable)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", "--confirm", dest="apply_changes", action="store_true",
                        help="apply removals (the default unless --dry-run)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--addon-id")
    group.add_argument("--resume-pending", action="store_true")
    group.add_argument("--dependents", metavar="CORE_SKILL",
                      help="print addon_ids depending on CORE_SKILL; exit 2 if any")
    args = parser.parse_args(argv)

    if args.dependents:
        # Fail closed: an unreadable sidecar could itself declare depends_on_core on this skill, so it must BLOCK the core uninstall instead of being silently treated as absence.
        records, skipped = addon_registry.scan_records(addons_dir=args.addons_dir)
        deps = sorted({
            record["addon_id"]
            for record in records
            if isinstance(record.get("depends_on_core"), list)
            and args.dependents in record["depends_on_core"]
            and isinstance(record.get("addon_id"), str)
        })
        print(json.dumps(
            {"core_skill": args.dependents, "dependents": deps,
             "skipped": [name for name, _reason in skipped]},
            ensure_ascii=False))
        return 2 if (deps or skipped) else 0

    allowed_roots = [Path(root) for root in args.skills_dir]
    apply_changes = not args.dry_run
    try:
        lock_path = _ghost_alice_root_for_addons(args.addons_dir) / 'install.lock'
        with install_lock.command_lock(lock_path, owner=f'addon-uninstall:{args.platform}:{os.getpid()}',
                                       enabled=apply_changes):
            if args.resume_pending:
                results = resume_pending(addons_dir=args.addons_dir, allowed_roots=allowed_roots,
                                         platform=args.platform, apply_changes=apply_changes,
                                         backup_sources=backup_sources)
            else:
                result = uninstall_addon(args.addon_id, addons_dir=args.addons_dir,
                                         allowed_roots=allowed_roots, platform=args.platform,
                                         apply_changes=apply_changes, backup_sources=backup_sources)
    except addon_registry.RecordNotFound:
        print(json.dumps({"addon_id": args.addon_id, "status": "not-found", "items": []}))
        return 1
    except (OSError, ValueError, RuntimeError, addon_registry.RegistryError, install_lock.InstallLockError) as exc:
        result = _error_result(args.addon_id, exc)
        if args.resume_pending:
            results = [result]
    if args.resume_pending:
        print(json.dumps({"resumed": results}, ensure_ascii=False, indent=2))
        return 2 if any(r["status"] not in {"removed", "dry-run", "resumed-noop"} for r in results) else 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"removed", "dry-run", "resumed-noop"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
