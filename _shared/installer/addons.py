"""Addon sources and the addon entry points the installer drives."""

from __future__ import annotations

from datetime import datetime, timezone
import contextlib
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
from typing import TYPE_CHECKING

import addon_registry
from installer import locations, report, runner
from installer.runner import InstallerExit

if TYPE_CHECKING:
    from installer.cli import Context
    from installer.selection import Target

_SAFE_ID = re.compile(r"^[a-z][a-z0-9-]*$")


def source_is_git_url(source: str) -> bool:
    """http://, https://, ssh://, file:// or `git@host:`."""
    if source.startswith(("http://", "https://", "ssh://", "file://")):
        return True
    return bool(re.match(r"^git@[^:]+:", source))


def selected_ref(tags: list[str]) -> str:
    """The single `--addon-tag`; more than one → error exit 1."""
    if not tags:
        return ""
    if len(tags) > 1:
        report.error("--addon-tag accepts one branch/tag for git URL addon sources")
        raise InstallerExit(1)
    return tags[0]


def source_cache_key(source: str, ref: str) -> str:
    """First 24 hex chars of sha256(`source\\0ref`)."""
    return hashlib.sha256((source + "\0" + ref).encode("utf-8")).hexdigest()[:24]


def clone_git_source(source: str, ref: str) -> Path:
    """Shallow clone into `<cache root>/<key>` through a `.tmp.<pid>` dir;
    messages `Cloning addon source: ...` / `Addon source clone failed: ...`."""
    if shutil.which("git") is None:
        report.error("git not found; cannot clone addon source URL")
        raise InstallerExit(1)
    cache_root = locations.addon_source_cache_root()
    dest = cache_root / source_cache_key(source, ref)
    tmp = Path(f"{dest}.tmp.{os.getpid()}")
    cache_root.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    if ref:
        report.info(f"Cloning addon source: {source} ({ref})")
        argv = ["git", "clone", "--quiet", "--depth", "1", "--branch", ref, source, str(tmp)]
    else:
        report.info(f"Cloning addon source: {source}")
        argv = ["git", "clone", "--quiet", "--depth", "1", source, str(tmp)]
    if not runner.run_command(argv).ok:
        shutil.rmtree(tmp, ignore_errors=True)
        report.error(f"Addon source clone failed: {source}")
        raise InstallerExit(1)
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(tmp, dest)
    return dest


def prepare_sources(ctx: "Context") -> None:
    """Resolve git URLs to cache paths once; a tag with a local source is an error."""
    if ctx.addon_sources_prepared:
        return
    if ctx.options.addon_skip or not ctx.addon_sources:
        ctx.addon_sources_prepared = True
        return
    ref = selected_ref(list(ctx.options.addon_tags))
    prepared: list[str] = []
    for source in ctx.addon_sources:
        if source_is_git_url(source):
            prepared.append(str(clone_git_source(source, ref)))
            continue
        if ref:
            report.error("--addon-tag can only be used with git URL addon sources; check out local sources yourself")
            raise InstallerExit(1)
        prepared.append(source)
    ctx.addon_sources = prepared
    ctx.addon_sources_prepared = True


def _core_skill_args(ctx: "Context") -> list[str]:
    from installer import selection

    args: list[str] = []
    for skill in selection.ALL_SKILLS:
        args.extend(["--core-skill", skill])
        for target in selection.expand_skill_targets(skill):
            args.extend(["--core-skill", target.name])
    return args


def selection_args(sources: list[str], ids: list[str] | None, source_option: str) -> list[str]:
    """`--source S`... and, for a host that keeps only the addons it holds, `--addon-id ID`..."""
    args: list[str] = []
    for source in sources:
        args.extend([source_option, str(source)])
    for addon_id in ids or ():
        args.extend(["--addon-id", addon_id])
    return args


def collect_addon_targets(ctx: "Context") -> "list[Target]":
    """`addon_installer.py --source ... --core-skill ... --platform P --format json` → targets with addon ids."""
    from installer.selection import Target

    if ctx.options.addon_skip or not ctx.addon_sources:
        return []
    prepare_sources(ctx)
    result = runner.run_tool("_shared/addon_installer.py", *selection_args(ctx.addon_sources, ctx.addon_ids, "--source"), *_core_skill_args(ctx),
                             "--platform", ctx.platform, "--format", "json", capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip("\n"))
    if not result.ok:
        raise InstallerExit(1)
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        report.error("addon manifest error: unreadable addon target listing")
        raise InstallerExit(1)
    return [Target(entry["name"], Path(entry["source"]), entry.get("addon_id"))
            for entry in payload.get("targets", []) if entry.get("name")]


def held_ids(ctx: "Context", hosts: list[str]) -> dict[str, dict[str, list[str]]]:
    """Per host and addon source of the command, the addon ids that the source supplies and the host's addon
    registry names. The caller holds the install lock, so no other command changes the registry meanwhile."""
    held: dict[str, dict[str, list[str]]] = {}
    for host in hosts:
        directory = locations.addons_dir(host)
        records, skipped = addon_registry.scan_records(addons_dir=directory)
        if skipped:
            details = '; '.join(f'{directory / filename}: {reason}' for filename, reason in skipped)
            raise ValueError(report.message('addon_registry_unreadable', host=host, details=details))
        installed = {record['addon_id'] for record in records}
        held[host] = {}
        for source in ctx.addon_sources:
            every = dataclasses.replace(ctx, platform=host, addon_sources=[source], addon_ids=None)
            ids = sorted({target.addon_id for target in collect_addon_targets(every)} & installed)
            if ids:
                held[host][source] = ids
    return held


def list_addons(ctx: "Context") -> int:
    """`--list-addons`: disabled → info; no source → error 1; else the text listing."""
    if ctx.options.addon_skip:
        report.info("Addon installation is disabled (--addon-skip)")
        return 0
    if not ctx.addon_sources:
        report.error("--list-addons requires --addon-source")
        return 1
    prepare_sources(ctx)
    return runner.run_tool("_shared/addon_installer.py", *selection_args(ctx.addon_sources, ctx.addon_ids, "--source"), *_core_skill_args(ctx),
                           "--platform", ctx.platform, "--format", "text").returncode


def write_sidecars(ctx: "Context", skills_dir: Path) -> bool:
    """`addon_installer.py write-sidecars ...` after install when sources exist."""
    if not ctx.addon_sources:
        return True
    installed_at = datetime.now(timezone.utc).isoformat()
    result = runner.run_tool("_shared/addon_installer.py", "write-sidecars", *selection_args(ctx.addon_sources, ctx.addon_ids, "--source"),
                             "--platform", ctx.platform, "--addons-dir", str(locations.addons_dir(ctx.platform)),
                             "--skills-dir", str(skills_dir), "--installed-at", installed_at,
                             "--claude-commands-dir", str(locations.claude_commands_dir()),
                             "--resources-dir", str(locations.resources_dir(ctx.platform)))
    if result.ok:
        report.info("Addon sidecars written")
        return True
    report.error("Addon sidecar write failed; aborting install")
    return False


def migrate_state_once(ctx: "Context") -> None:
    """`addon_migration.py` when an install-state file exists; failures ignored."""
    state_path = locations.install_state_path(ctx.platform)
    if not state_path.is_file():
        return
    args = ["--platform", ctx.platform, "--install-state", str(state_path),
            "--addons-dir", str(locations.addons_dir(ctx.platform)), *_core_skill_args(ctx)]
    runner.run_tool("_shared/addon_migration.py", *args, capture=True)


def _addon_location_args(ctx: "Context") -> list[str]:
    return ["--addons-dir", str(locations.addons_dir(ctx.platform)), "--skills-dir", str(ctx.skills_dir),
            "--claude-commands-dir", str(locations.claude_commands_dir()),
            "--resources-dir", str(locations.resources_dir(ctx.platform))]


def repair_missing(ctx: "Context") -> tuple[int, int] | None:
    """Return the repaired and occupied addon target counts, or report the failure and return None."""
    if not locations.addons_dir(ctx.platform).is_dir():
        return 0, 0
    result = runner.run_tool("_shared/addon_installer.py", "repair-missing", "--platform", ctx.platform,
                             *_addon_location_args(ctx), capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip("\n"))
    if not result.stdout:
        if not result.stderr:
            report.error(f"Addon repair produced no result (exit {result.returncode})")
        return None
    summary = json.loads(result.stdout)
    for error in summary['errors']:
        identity = error.get('addon_id') or error.get('sidecar')
        target = f" {error['target']}" if 'target' in error else ''
        report.error(f"Addon repair: {identity}{target}: {error['reason']}")
    if not result.ok or summary['errors']:
        return None
    return len(summary['repaired']), len(summary['kept'])


def _uninstall_args(ctx: "Context") -> list[str]:
    return ["--addons-dir", str(locations.addons_dir(ctx.platform)), "--skills-dir", str(ctx.skills_dir),
            "--skills-dir", str(locations.claude_commands_dir()),
            "--skills-dir", str(locations.resources_dir(ctx.platform)), "--platform", ctx.platform, "--apply"]


def resume_pending_uninstalls(ctx: "Context", *, backup_sources=None) -> None:
    """Run the addon command owner in this command's backup scope; retain its resume output."""
    if not locations.addons_dir(ctx.platform).is_dir():
        return
    import addon_uninstall
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        addon_uninstall.main(["--resume-pending", *_uninstall_args(ctx)], backup_sources=backup_sources)
    log = locations.ghost_root() / "addon-resume.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(output.getvalue(), encoding="utf-8")
    except OSError:
        pass


def uninstall_addon(ctx: "Context", addon_id: str, *, backup_sources=None) -> int:
    """Use the addon command owner's output and exit policy with this command's original bytes."""
    import addon_uninstall
    return addon_uninstall.main(["--addon-id", addon_id, *_uninstall_args(ctx)], backup_sources=backup_sources)


def dependents_rc(ctx: "Context", skill: str) -> int:
    """`addon_uninstall.py --dependents <skill>`; 2 means an installed addon depends on it."""
    return runner.run_tool("_shared/addon_uninstall.py", "--dependents", skill,
                           "--addons-dir", str(locations.addons_dir(ctx.platform)), capture=True).returncode


def installed_addon_id(addon_id: str, addons_dir: Path) -> bool:
    """A safe id with `<id>.json` or `<id>.json.removing` in the addons dir."""
    if not _SAFE_ID.match(addon_id):
        return False
    addons_dir = Path(addons_dir)
    return (addons_dir / f"{addon_id}.json").is_file() or (addons_dir / f"{addon_id}.json.removing").is_file()


def promote_bare_installed_addon_ids(ctx: "Context", names: list[str]) -> tuple[list[str], list[str]]:
    """Split requested names into (addon ids, skills): a name that is not a core
    target but is an installed addon id becomes an addon id."""
    from installer import selection

    addons_dir = locations.addons_dir(ctx.platform)
    addon_ids: list[str] = []
    kept: list[str] = []
    for name in names:
        if not selection.expand_skill_targets(name) and installed_addon_id(name, addons_dir):
            if name not in addon_ids:
                addon_ids.append(name)
        else:
            kept.append(name)
    return addon_ids, kept
