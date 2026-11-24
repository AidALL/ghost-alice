"""Command flows: uninstall (selected skills, addons, full) and repair.

`--update-source` is `source.update_source`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import installed_payload
from installer import addons, hosts, locations, preservation, record_move, report, runner, selection, source, transaction
from installer.runner import InstallerExit

if TYPE_CHECKING:
    from installer.cli import Context


@dataclass
class UninstallRequest:
    addon_ids: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    force: bool = False


def parse_uninstall_args(args: list[str]) -> UninstallRequest:
    """`--addon ID` (missing id → error exit 1), `--force`, the rest are skill names."""
    request = UninstallRequest()
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--addon":
            index += 1
            if index >= len(args):
                report.error("--addon requires an addon id")
                raise InstallerExit(1)
            request.addon_ids.append(args[index])
        elif token == "--force":
            request.force = True
        else:
            request.skills.append(token)
        index += 1
    return request


def uninstall(ctx: "Context", args: list[str]) -> int:
    """The combined addon + skill removal flow."""
    request = parse_uninstall_args(args)
    # The removal judges the gateway from the install record: an installation that only an earlier
    # installer wrote gets its record, and its gateway's service section, before the first judgment.
    transaction.migrate_record(ctx)
    addons_dir = locations.addons_dir(ctx.platform)
    promoted, skills = addons.promote_bare_installed_addon_ids(ctx, request.skills)
    addon_ids = list(request.addon_ids)
    for addon_id in promoted:
        if addon_id not in addon_ids:
            addon_ids.append(addon_id)
    if not skills and not addon_ids:
        return run_full_uninstall(ctx)
    record_locations = record_move.read_locations()
    from install_hooks import settings_backup_sources
    backups = settings_backup_sources(ctx.platform)
    # Snapshot which requested addons exist before the resume runs.
    known_before = {addon_id for addon_id in addon_ids
                    if (addons_dir / f"{addon_id}.json").is_file() or (addons_dir / f"{addon_id}.json.removing").is_file()}
    addons.resume_pending_uninstalls(ctx, backup_sources=backups)
    removed = failed = 0
    for addon_id in addon_ids:
        code = addons.uninstall_addon(ctx, addon_id, backup_sources=backups)
        if code == 0:
            removed += 1
        elif code == 1:
            if addon_id in known_before:
                removed += 1
            else:
                failed += 1
                report.warn(f"Addon {addon_id}: not installed (nothing to remove)")
        else:
            failed += 1
            report.warn(f"Addon {addon_id}: uninstall left items for the human user to review")
    if not skills:
        if removed > 0:
            report.ok(f"{removed} addon(s) removed.")
        else:
            report.info("No addon targets to remove.")
        return 1 if failed else 0
    selection.validate_requested_skills(skills)
    report.info("Removing selected skills...")
    skills_dir = Path(ctx.skills_dir)
    candidates = {target.name: target for skill in skills for target in selection.expand_skill_targets(skill)}
    removal: list[tuple[str, Path, Path]] = []
    shared_references: list[tuple[str, Path, Path]] = []
    absent: list[tuple[str, Path, Path]] = []
    dependency_status: dict[str, int] = {}
    for label, target in candidates.items():
        if not request.force:
            names = {label} | {skill for skill in skills
                               if any(leaf.name == label for leaf in selection.expand_skill_targets(skill))}
            for name in sorted(names):
                if name not in dependency_status:
                    dependency_status[name] = addons.dependents_rc(ctx, name)
            statuses = [dependency_status[name] for name in sorted(names)]
            dependents = 2 if 2 in statuses else next((status for status in statuses if status != 0), 0)
            if dependents == 2:
                report.warn(f"Skipping {label}: an installed addon depends on it (use --force to override)")
                failed += 1
                continue
            if dependents != 0:
                report.warn(f"Dependency check failed for {label}; preserving its installed target")
                failed += 1
                continue
        path = skills_dir / label
        proposed = transaction.remove_installed_target(ctx, label, path, source_path=target.path, apply_changes=False)
        if proposed["action"] in {"would-remove", "would-quarantine-remove"}:
            removal.append((label, path, target.path))
        elif proposed['action'] == 'would-release-reference':
            shared_references.append((label, path, target.path))
        elif proposed["action"] == "missing":
            absent.append((label, path, target.path))
        else:
            failed += 1
            report.emit_removal_item(proposed)
    ownership_errors = set()

    def warn_unresolved(cause):
        if cause not in ownership_errors:
            report.warn(cause)
            ownership_errors.add(cause)

    managed_remain = selection.has_managed_installs(skills_dir, ctx.platform,
        excluding=tuple(path for _, path, _ in [*removal, *shared_references, *absent]), on_error=warn_unresolved)
    if managed_remain is False:
        shared = skills_dir / "_shared"
        proposed = transaction.remove_installed_target(ctx, "_shared", shared,
            source_path=runner.REPO_ROOT / "_shared", apply_changes=False)
        if proposed["action"] in {"would-remove", "would-quarantine-remove"}:
            removal.append(("_shared", shared, runner.REPO_ROOT / "_shared"))
        elif proposed['action'] == 'would-release-reference':
            shared_references.append(('_shared', shared, runner.REPO_ROOT / '_shared'))
        elif proposed["action"] == "missing":
            absent.append(("_shared", shared, runner.REPO_ROOT / "_shared"))
        else:
            failed += 1
            report.emit_removal_item(proposed)
    if managed_remain is False:
        if record_locations is None:
            return 1
        retention = transaction.retain_record_source_writers(locations.record_root(), record_locations)
        blocked = retention.blocked_hosts.get(ctx.platform, {})
        for causes in (retention.errors, blocked):
            for path, cause in causes.items():
                report.error(report.message('record_source_references_failed', path=path, cause=cause))
        if blocked:
            return 1
        failed += len(retention.errors)
    guard = transaction.guard_gateway_removal(ctx, [path for _, path, _ in removal])
    if guard != 0:
        return guard
    if managed_remain:
        report.info("Managed skills remain; keeping hooks installed.")
    elif managed_remain is False:
        hooks = hosts.install_hooks(ctx, "uninstall", ctx.platform, backup_sources=backups)
        for item in hooks.details.get('items', []):
            report.emit_removal_item(item)
        if hooks.legacy_status == 'error' or not hooks.details.get('hooks_detached'):
            report.error("Hook removal failed. Aborting skill removal")
            raise InstallerExit(1)
    absent_paths = {path for _, path, _ in absent}
    released_paths = []
    for label, path, source_path in sorted([*removal, *shared_references, *absent], key=lambda row: row[0] == '_shared'):
        if label == "_shared":
            remaining = selection.has_managed_installs(skills_dir, ctx.platform,
                excluding=tuple(released_paths), on_error=warn_unresolved)
            if remaining is not False:
                failed += 1
                if remaining:
                    report.warn("Preserving _shared: a Core target remains after removal")
                continue
        result = transaction.remove_installed_target(ctx, label, path, source_path=source_path,
                                                     only_if_absent=path in absent_paths)
        if result["action"] in {"removed", "quarantined-removed", "reference-released"}:
            removed += 1
            released_paths.append(path)
        elif result["action"] != "missing":
            failed += 1
            report.emit_removal_item(result)
    remaining = selection.has_managed_installs(skills_dir, ctx.platform, on_error=warn_unresolved)
    failed += len(ownership_errors)
    if remaining is False:
        if ctx.platform == "claude" and hosts.remove_claude_bootstrap_if_unused(ctx, skills_dir):
            removed += 1
        elif ctx.platform == "codex" and hosts.remove_codex_bootstrap_if_unused(ctx, skills_dir):
            removed += 1
        from uninstall_cleanup import retire_unused_management
        for item in retire_unused_management(ctx.platform, locations.install_state_path(ctx.platform),
                                             apply_changes=not failed, backup_sources=backups):
            if item.get('removal_incomplete'):
                failed += 1
            report.emit_removal_item(item)
        if failed:
            report.warn(report.message('removal_retry'))
    source.drop_unfinished_choices(ctx.platform, [label for label, _, _ in [*removal, *shared_references, *absent]])
    if removed == 0:
        report.info("No skills to remove.")
    else:
        report.ok(f"{removed} item(s) removed.")
    if remaining is False:
        _report_retained_records(record_locations, ctx.platform)
    return 1 if failed else 0


def run_full_uninstall(ctx: "Context") -> int:
    """Run the gateway, addon and Core cleanup as one itemized report."""
    record_locations = record_move.read_locations()
    report.info("Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets.")
    result = runner.run_tool("_shared/uninstall_cleanup.py", "--platform", ctx.platform,
                           "--install-state-manifest", str(locations.install_state_path(ctx.platform)),
                           "--apply").returncode
    source.drop_unfinished_choices(ctx.platform)
    _report_retained_records(record_locations, ctx.platform)
    return result


def _report_retained_records(record_locations: transaction.LedgerLocations | None, platform: str) -> None:
    """Use the relation observed before removal deleted the installation record."""
    if record_locations is None:
        return
    for root, value in record_locations.roots.items():
        if platform in value['hosts'] and value['state'] in ('source-bound', 'unknown', 'unconfirmed'):
            report.emit(report.message({'source-bound': 'move_uninstall', 'unknown': 'move_relation_unknown',
                                        'unconfirmed': 'move_location_unconfirmed'}[value['state']],
                                       current_path=root, source_path=value['source_root']))


def run_repair(ctx: "Context") -> int:
    """Re-provision missing managed targets; occupied slots that are not clean
    managed targets stay untouched."""
    report.emit("")
    report.info(f"Installer repair for {ctx.platform}: re-provisioning missing managed targets...")
    repair_from = source.repair_active_source(ctx)
    root = locations.record_root()

    def install_command(host: str, *names: str) -> str:
        return locations.package_command(runner.REPO_ROOT, '--platform', host, *names)

    # Repair publishes like install, so the first command that writes the install record migrates first.
    try:
        transaction.migrate_record(ctx)
    except installed_payload.InventoryError as exc:
        report.error(report.message('repair_inventory_unreadable', file=exc.file, command=install_command(exc.host),
                                    cause=exc))
        return 1
    record_path = installed_payload.read_path(root)
    record = installed_payload.read_file(record_path) or {}
    targets = record.get('targets', {})
    # The record names each skill with its folder: this repair restores the host's skills of this command's folder.
    selected = selection.installed_core_targets(record, record_path).get(ctx.platform, {})
    here = Path(ctx.skills_dir).resolve()
    destinations = {path: name for path, name in selected.items() if path.parent.resolve() == here}
    if selected and not destinations:
        # An installation under other settings holds them; placing them here would start a second installation.
        report.error(report.message('repair_other_folder', host=ctx.platform,
                                    recorded=sorted(path.parent for path in selected)[0], current=ctx.skills_dir))
        return 1
    if (not any(ctx.platform in installed_payload.target_hosts(target, entry) for target, entry in targets.items())
            and ('installer' not in record or locations.install_state_path(ctx.platform).exists())):
        # A recorded installation without this host's targets and inventory does not include the host.
        report.error(report.message('repair_targets_unrecorded', host=ctx.platform, command=install_command(ctx.platform)))
        return 1
    skills = sorted(set(destinations.values()))
    available = {target.name: target for target in selection.iter_install_targets(skills, source_root=repair_from)}
    missing = sorted(set(skills) - available.keys())
    if missing:
        for name in missing:
            report.error(report.message('repair_skill_missing', name=name, package=repair_from))
        return 1
    repaired = kept = 0
    # Resolve the parent for comparison and keep the recorded key for publication. Following the leaf
    # itself would treat a link in another slot as ownership of its destination.
    shared = next((Path(path) for path, entry in targets.items()
                   if Path(path).name == '_shared' and Path(path).parent.resolve() == here
                   and entry.get('kind') == 'support' and entry.get('provider') == 'core'
                   and ctx.platform in installed_payload.target_hosts(path, entry)), None)
    support = shared or Path(ctx.skills_dir) / "_shared"
    if (skills or shared) and support.exists() and not support.is_dir():
        report.error(report.message('repair_shared_occupied', path=support))
        return 1
    if skills and shared is None and not support.is_dir():
        report.error(report.message('repair_shared_occupied', path=support) if support.is_symlink() else
                     report.message('repair_shared_unrecorded', host=ctx.platform, path=support,
                                    command=install_command(ctx.platform, *skills)))
        return 1
    if shared is not None:
        if not shared.exists():
            code = transaction.install_shared(ctx, shared.parent, True, repair_from=repair_from)
            if code != 0:
                report.error("repair: _shared could not be re-provisioned")
                return code
            repaired += 1
        elif not preservation.is_clean_managed_target(ctx, "_shared", shared, source_root=repair_from):
            kept += 1
            report.warn("repair: _shared is occupied and not cleanly managed; left untouched")
    for dest, name in sorted(destinations.items()):
        target = available[name]
        if not dest.exists() and not dest.is_symlink():
            if not transaction.copy_target(ctx, target, dest, repair_from=repair_from):
                return 1
            report.ok(f"repair: re-provisioned {target.name}")
            repaired += 1
        elif preservation.is_clean_managed_target(ctx, target.name, dest, source_root=repair_from):
            pass
        else:
            kept += 1
            report.warn(f"repair: {target.name} is occupied and not cleanly managed; left untouched")
    addon_counts = addons.repair_missing(ctx)
    if addon_counts is None:
        return 1
    addon_repaired, addon_kept = addon_counts
    repaired += addon_repaired
    kept += addon_kept
    report.ok(f"Repair complete for {ctx.platform}: {repaired} re-provisioned, {kept} left untouched")
    # The install's read-only gateway line, from the same report.
    transaction.report_gateway(ctx)
    return 0
