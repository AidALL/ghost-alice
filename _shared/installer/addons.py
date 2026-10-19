"""Addon sources and the addon entry points the installer drives.

Port of `install.sh:203-213`, `installer_lib/targets.sh:28-148`,
`installer_lib/uninstall.sh:50-79, 110-140, 269-281, 457-478` and
`installer_lib/install.sh:612-638, 665-675, 766-781`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import TYPE_CHECKING

from installer import locations, report, runner
from installer.runner import InstallerExit

if TYPE_CHECKING:
    from installer.cli import Context
    from installer.selection import Target

OFFICIAL_AUTOPILOT_SOURCE = "https://github.com/AidALL/ghost-alice-autopilot.git"
_SAFE_ID = re.compile(r"^[a-z][a-z0-9-]*$")


def official_addon_source(name: str) -> str | None:
    """`autopilot`/`autopilot-mode` → `$GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE` or the GitHub URL; else None."""
    if name in ("autopilot", "autopilot-mode"):
        return os.environ.get("GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE") or OFFICIAL_AUTOPILOT_SOURCE
    return None


def source_is_git_url(source: str) -> bool:
    """http://, https://, ssh://, file:// or `git@host:` (`targets.sh:28-34`)."""
    if source.startswith(("http://", "https://", "ssh://", "file://")):
        return True
    return bool(re.match(r"^git@[^:]+:", source))


def selected_ref(tags: list[str]) -> str:
    """The single `--addon-tag`; more than one → error exit 1 (`targets.sh:36-46`)."""
    if not tags:
        return ""
    if len(tags) > 1:
        report.error("--addon-tag accepts one branch/tag for git URL addon sources")
        raise InstallerExit(1)
    return tags[0]


def source_cache_key(source: str, ref: str) -> str:
    """First 24 hex chars of sha256(`source\\0ref`) (`targets.sh:48-62`)."""
    return hashlib.sha256((source + "\0" + ref).encode("utf-8")).hexdigest()[:24]


def clone_git_source(source: str, ref: str) -> Path:
    """Shallow clone into `<cache root>/<key>` through a `.tmp.<pid>` dir;
    messages `Cloning addon source: ...` / `Addon source clone failed: ...` (`targets.sh:64-94`)."""
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
    """Resolve git URLs to cache paths once; a tag with a local source is an error (`targets.sh:96-121`)."""
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


def _source_args(ctx: "Context") -> list[str]:
    args: list[str] = []
    for source in ctx.addon_sources:
        args.extend(["--source", str(source)])
    return args


def collect_addon_targets(ctx: "Context") -> "list[Target]":
    """`addon_installer.py --source ... --core-skill ... --platform P --format json` → targets with addon ids
    (`targets.sh:123-148`, the shell read `--format shell`)."""
    from installer.selection import Target

    if ctx.options.addon_skip or not ctx.addon_sources:
        return []
    prepare_sources(ctx)
    result = runner.run_tool("_shared/addon_installer.py", *_source_args(ctx), *_core_skill_args(ctx),
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


def list_addons(ctx: "Context") -> int:
    """`--list-addons`: disabled → info; no source → error 1; else the text listing (`uninstall.sh:110-140`)."""
    if ctx.options.addon_skip:
        report.info("Addon installation is disabled (--addon-skip)")
        return 0
    if not ctx.addon_sources:
        report.error("--list-addons requires --addon-source")
        return 1
    prepare_sources(ctx)
    return runner.run_tool("_shared/addon_installer.py", *_source_args(ctx), *_core_skill_args(ctx),
                           "--platform", ctx.platform, "--format", "text").returncode


def write_sidecars(ctx: "Context", skills_dir: Path) -> bool:
    """`addon_installer.py write-sidecars ...` after install when sources exist (`install.sh:612-638`)."""
    if not ctx.addon_sources:
        return True
    installed_at = datetime.now(timezone.utc).isoformat()
    result = runner.run_tool("_shared/addon_installer.py", "write-sidecars", *_source_args(ctx),
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
    """`addon_migration.py` when an install-state file exists; failures ignored (`install.sh:766-781`)."""
    from installer import selection

    state_path = locations.install_state_path(ctx.platform)
    if not state_path.is_file():
        return
    args = ["--platform", ctx.platform, "--install-state", str(state_path),
            "--addons-dir", str(locations.addons_dir(ctx.platform))]
    for skill in selection.ALL_SKILLS:
        args.extend(["--core-skill", skill])
    runner.run_tool("_shared/addon_migration.py", *args, capture=True)


def _addon_location_args(ctx: "Context") -> list[str]:
    return ["--addons-dir", str(locations.addons_dir(ctx.platform)), "--skills-dir", str(ctx.skills_dir),
            "--claude-commands-dir", str(locations.claude_commands_dir()),
            "--resources-dir", str(locations.resources_dir(ctx.platform))]


def repair_missing(ctx: "Context") -> bool:
    """`addon_installer.py repair-missing ...` when the addons dir exists (`install.sh:665-675`)."""
    if not locations.addons_dir(ctx.platform).is_dir():
        return True
    result = runner.run_tool("_shared/addon_installer.py", "repair-missing", "--platform", ctx.platform,
                             *_addon_location_args(ctx), capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip("\n"))
    return result.ok


def _uninstall_args(ctx: "Context") -> list[str]:
    return ["--addons-dir", str(locations.addons_dir(ctx.platform)), "--skills-dir", str(ctx.skills_dir),
            "--skills-dir", str(locations.claude_commands_dir()),
            "--skills-dir", str(locations.resources_dir(ctx.platform)), "--platform", ctx.platform, "--confirm"]


def resume_pending_uninstalls(ctx: "Context") -> None:
    """`addon_uninstall.py --resume-pending ...` with output kept in `~/.ghost-alice/addon-resume.log` (`uninstall.sh:269-281`)."""
    if not locations.addons_dir(ctx.platform).is_dir():
        return
    result = runner.run_tool("_shared/addon_uninstall.py", "--resume-pending", *_uninstall_args(ctx), capture=True)
    log = locations.ghost_root() / "addon-resume.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
    except OSError:
        pass


def uninstall_addon(ctx: "Context", addon_id: str) -> int:
    """`addon_uninstall.py --addon-id ... --confirm`; the child's exit code (`uninstall.sh:329-333`)."""
    return runner.run_tool("_shared/addon_uninstall.py", "--addon-id", addon_id, *_uninstall_args(ctx)).returncode


def dependents_rc(ctx: "Context", skill: str) -> int:
    """`addon_uninstall.py --dependents <skill>`; 2 means an installed addon depends on it (`uninstall.sh:367-368`)."""
    return runner.run_tool("_shared/addon_uninstall.py", "--dependents", skill,
                           "--addons-dir", str(locations.addons_dir(ctx.platform)), capture=True).returncode


def uninstall_all_before_full(ctx: "Context") -> bool:
    """Remove every addon with a sidecar before full cleanup; False when one is preserved (`uninstall.sh:457-478`)."""
    addons_dir = locations.addons_dir(ctx.platform)
    if not addons_dir.is_dir():
        return True
    failed = False
    for sidecar in sorted(addons_dir.glob("*.json")):
        addon_id = sidecar.name[:-len(".json")]
        if not _SAFE_ID.match(addon_id):
            continue
        result = runner.run_tool("_shared/addon_uninstall.py", "--addon-id", addon_id, *_uninstall_args(ctx),
                                 capture=True)
        if not result.ok:
            failed = True
            report.warn(f"Full uninstall preserved addon {addon_id}; manual review is required before sidecar cleanup")
    return not failed


def installed_addon_id(addon_id: str, addons_dir: Path) -> bool:
    """A safe id with `<id>.json` or `<id>.json.removing` in the addons dir (`uninstall.sh:50-55`)."""
    if not _SAFE_ID.match(addon_id):
        return False
    addons_dir = Path(addons_dir)
    return (addons_dir / f"{addon_id}.json").is_file() or (addons_dir / f"{addon_id}.json.removing").is_file()


def promote_bare_installed_addon_ids(ctx: "Context", names: list[str]) -> tuple[list[str], list[str]]:
    """Split requested names into (addon ids, skills): a name that is not a core
    target but is an installed addon id becomes an addon id (`uninstall.sh:57-79`)."""
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
