"""Command flows: uninstall (selected skills, addons, full) and repair.

Port of `installer_lib/uninstall.sh:283-488` and `installer_lib/install.sh:655-726`.
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
    """`--addon ID` (missing id → error exit 1), `--force`, the rest are skill names (`uninstall.sh:284-301`)."""
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
    """The combined addon + skill removal flow (`uninstall.sh:283-438`)."""
    request = parse_uninstall_args(args)
    addons_dir = locations.addons_dir(ctx.platform)
    promoted, skills = addons.promote_bare_installed_addon_ids(ctx, request.skills)
    addon_ids = list(request.addon_ids)
    for addon_id in promoted:
        if addon_id not in addon_ids:
            addon_ids.append(addon_id)
    # Snapshot which requested addons exist before the resume runs (`uninstall.sh:303-312`).
    known_before = {addon_id for addon_id in addon_ids
                    if (addons_dir / f"{addon_id}.json").is_file() or (addons_dir / f"{addon_id}.json.removing").is_file()}
    addons.resume_pending_uninstalls(ctx)
    removed = failed = 0
    for addon_id in addon_ids:
        code = addons.uninstall_addon(ctx, addon_id)
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
            report.warn(f"Addon {addon_id}: uninstall left items for manual review")
    if not skills:
        if addon_ids:
            if removed > 0:
                report.ok(f"{removed} addon(s) removed.")
            else:
                report.info("No addon targets to remove.")
            return 1 if failed else 0
        return run_full_uninstall(ctx)
    selection.validate_requested_skills(ctx.source_root, skills)
    report.info("Removing selected skills...")
    skills_dir = Path(ctx.skills_dir)
    removal: list[tuple[str, Path]] = []
    for skill in skills:
        if not request.force:
            dependents = addons.dependents_rc(ctx, skill)
            if dependents == 2:
                report.warn(f"Skipping {skill}: an installed addon depends on it (use --force to override)")
                continue
            if dependents != 0:
                report.warn(f"Dependency check failed for {skill}; proceeding with removal")
        targets = selection.expand_skill_targets(ctx.source_root, skill)
        if not targets:
            removal.append((skill, skills_dir / skill))
            continue
        for target in targets:
            removal.append((target.name, skills_dir / target.name))
    guard = transaction.guard_gateway_removal(ctx, [path for _, path in removal])
    if guard != 0:
        return guard
    for label, path in removal:
        if transaction.remove_installed_target(label, path):
            removed += 1
    shared_rc = transaction.cleanup_shared_if_unused(ctx, skills_dir)
    if shared_rc == 0:
        removed += 1
    elif shared_rc != 1:
        return shared_rc
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
    if selection.has_managed_installs(ctx.source_root, skills_dir):
        report.info("Managed skills remain; keeping hooks installed.")
    elif not hosts.install_hooks(ctx, "uninstall", ctx.platform):
        report.error("Hook removal failed. Aborting skill removal")
        raise InstallerExit(1)
    return 1 if failed else 0


def run_uninstall_cleanup(ctx: "Context") -> int:
    """`uninstall_cleanup.py --platform --install-state-manifest --confirm` (`uninstall.sh:440-455`)."""
    return runner.run_tool("_shared/uninstall_cleanup.py", "--platform", ctx.platform,
                           "--install-state-manifest", str(locations.install_state_path(ctx.platform)),
                           "--confirm").returncode


def run_full_uninstall(ctx: "Context") -> int:
    """Resume, remove every addon (stop when one is preserved), then cleanup (`uninstall.sh:480-488`)."""
    report.info("Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets.")
    addons.resume_pending_uninstalls(ctx)
    if not addons.uninstall_all_before_full(ctx):
        report.error("Full uninstall stopped because one or more addon targets need manual review.")
        return 1
    return run_uninstall_cleanup(ctx)


def run_repair(ctx: "Context") -> int:
    """Re-provision missing managed targets; occupied slots that are not clean
    managed targets stay untouched (`install.sh:655-726`)."""
    report.emit("")
    report.info("Installer repair: re-provisioning missing managed targets...")
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
    for target in selection.iter_install_targets(ctx.source_root, list(selection.ALL_SKILLS)):
        dest = skills_dir / target.name
        if not dest.exists() and not dest.is_symlink():
            if copy_only:
                if not transaction.copy_target(ctx, target.path, dest):
                    return 1
            else:
                code = transaction.link_or_copy(ctx, target.path, dest)
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
    return 0
