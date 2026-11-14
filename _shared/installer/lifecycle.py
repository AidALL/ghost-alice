"""Command flows: uninstall (selected skills, addons, full) and repair.

`--update-source` is `source.update_source`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from installer import addons, hosts, locations, preservation, report, runner, selection, transaction
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
            report.warn(f"Preserving {label}: {proposed['reason']}")
    if not selection.has_managed_installs(skills_dir, ctx.platform,
            excluding=tuple(path for _, path, _ in [*removal, *shared_references])):
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
            report.warn(f"Preserving _shared: {proposed['reason']}")
    guard = transaction.guard_gateway_removal(ctx, [path for _, path, _ in removal])
    if guard != 0:
        return guard
    if selection.has_managed_installs(skills_dir, ctx.platform,
            excluding=tuple(path for _, path, _ in [*removal, *shared_references])):
        report.info("Managed skills remain; keeping hooks installed.")
    elif hosts.install_hooks(ctx, "uninstall", ctx.platform, backup_sources=backups).legacy_status == 'error':
        report.error("Hook removal failed. Aborting skill removal")
        raise InstallerExit(1)
    absent_paths = {path for _, path, _ in absent}
    released_paths = []
    for label, path, source_path in sorted([*removal, *shared_references, *absent], key=lambda row: row[0] == '_shared'):
        if label == "_shared" and selection.has_managed_installs(skills_dir, ctx.platform, excluding=tuple(released_paths)):
            failed += 1
            report.warn("Preserving _shared: a Core target remains after removal")
            continue
        result = transaction.remove_installed_target(ctx, label, path, source_path=source_path,
                                                     only_if_absent=path in absent_paths)
        if result["action"] in {"removed", "quarantined-removed", "reference-released"}:
            removed += 1
            released_paths.append(path)
        elif result["action"] != "missing":
            failed += 1
            report.warn(f"Removal incomplete for {path}: {result['reason']}; "
                        f"file {result.get('file_state', 'retained')}; {result.get('detail', '')}")
    if ctx.platform == "claude":
        if hosts.remove_claude_bootstrap_if_unused(ctx, skills_dir):
            removed += 1
    elif ctx.platform == "codex":
        if hosts.remove_codex_bootstrap_if_unused(ctx, skills_dir):
            removed += 1
    if removed == 0:
        report.info("No skills to remove.")
    else:
        report.ok(f"{removed} item(s) removed.")
    return 1 if failed else 0


def run_full_uninstall(ctx: "Context") -> int:
    """Run the gateway, addon and Core cleanup as one itemized report."""
    report.info("Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets.")
    return runner.run_tool("_shared/uninstall_cleanup.py", "--platform", ctx.platform,
                           "--install-state-manifest", str(locations.install_state_path(ctx.platform)),
                           "--apply").returncode


def run_repair(ctx: "Context") -> int:
    """Re-provision missing managed targets; occupied slots that are not clean
    managed targets stay untouched."""
    report.emit("")
    report.info("Installer repair: re-provisioning missing managed targets...")
    # Repair publishes like install, so the first command that writes the install record migrates first.
    transaction.migrate_record(ctx)
    copy_only = locations.prefers_copy_install(ctx.platform)
    skills_dir = Path(ctx.skills_dir)
    repaired = kept = 0
    shared = skills_dir / "_shared"
    if not shared.exists():
        code = transaction.install_shared(ctx, skills_dir, copy_only)
        if code != 0:
            report.error("repair: _shared could not be re-provisioned")
            return code
        repaired += 1
    elif preservation.is_clean_managed_target(ctx, "_shared", shared):
        pass
    else:
        kept += 1
        report.warn("repair: _shared is occupied and not cleanly managed; left untouched")
    for target in selection.iter_install_targets(list(selection.ALL_SKILLS)):
        dest = skills_dir / target.name
        if not dest.exists() and not dest.is_symlink():
            if copy_only:
                if not transaction.copy_target(ctx, target, dest):
                    return 1
            else:
                code = transaction.link_or_copy(ctx, target, dest)
                if code != 0:
                    return code
            report.ok(f"repair: re-provisioned {target.name}")
            repaired += 1
        elif preservation.is_clean_managed_target(ctx, target.name, dest):
            pass
        else:
            kept += 1
            report.warn(f"repair: {target.name} is occupied and not cleanly managed; left untouched")
    if not addons.repair_missing(ctx):
        report.error("repair: addon target repair failed; see addon registry sidecars")
        return 1
    report.ok(f"Repair complete: {repaired} re-provisioned, {kept} left untouched")
    # The install's read-only gateway line, from the same report.
    transaction.report_gateway(ctx)
    return 0
