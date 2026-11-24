"""Which skills and support files install, and from where."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import installed_payload
from installed_payload import inventory_destination, read_inventory
from installer import addons, locations, report, runner, source
from installer.runner import InstallerExit

if TYPE_CHECKING:
    from installer.cli import Context

ALL_SKILLS = (
    "adversarial-verification",
    "boundary-contract",
    "coding-convention",
    "skill-evolution",
    "agent-security-scan",
    "merge-companion",
    "necessity-gate",
    "session-intent-analyzer",
    "compact-handoff",
    "task-router",
)

@dataclass(frozen=True)
class Target:
    name: str
    path: Path
    addon_id: str | None = None

    @property
    def provider(self) -> str:
        """`core`, or `addon:<id>` for an addon's target: the provider the install record names."""
        return f"addon:{self.addon_id}" if self.addon_id else "core"


@dataclass
class Collected:
    """The selected identities for one installation; later stages keep these targets when the source changes."""

    skill_targets: list[Target]
    addon_targets: list[Target]
    skill_target_total: int
    support_target_total: int

    @property
    def all_targets(self) -> list[Target]:
        return [*self.skill_targets, *self.addon_targets]

    @property
    def total_target_count(self) -> int:
        return self.skill_target_total + self.support_target_total


def _skill_hosts(target: str, entry: dict, record_path: Path | None) -> list[str]:
    try:
        return installed_payload.target_hosts(target, entry)
    except installed_payload.RecordError as exc:
        if record_path is None:
            raise
        raise installed_payload.RecordError(report.message('invalid_skill_hosts', file=record_path, target=target)) from exc


def installed_core_targets(record: dict, record_path: Path | None = None) -> dict[str, dict[Path, str]]:
    """Per host, the recorded paths of its Core skills with their leaf names."""
    selected: dict[str, dict[Path, str]] = {}
    for target, entry in record.get('targets', {}).items():
        if not isinstance(entry, dict):
            raise installed_payload.RecordError(f"{record_path}: target {target} is not an object")
        if entry.get('kind') == 'skill' and entry.get('provider') == 'core':
            if not isinstance(entry.get('name'), str) or not entry['name']:
                raise installed_payload.RecordError(f"{record_path}: Core target {target} needs a skill name")
            for host in _skill_hosts(target, entry, record_path):
                selected.setdefault(host, {})[Path(target)] = entry['name']
    return selected


def installed_core_selection(record: dict, record_path: Path | None = None) -> dict[str, list[str]]:
    """The recorded Core leaf names per host, independent of current adapter availability."""
    return {host: sorted(set(paths.values()))
            for host, paths in sorted(installed_core_targets(record, record_path).items())}


def requested_skills(ctx: 'Context', platform: str) -> list[str]:
    """An update carries exact host selections; an ordinary install keeps its existing default."""
    from installer import source
    selected = (source.install_request() or {}).get('selections')
    if selected is not None:
        if not selected.get(platform):
            raise ValueError(f'Update has no selected Core skills for {platform}')
        return list(selected[platform])
    return list(ctx.options.args) or list(ALL_SKILLS)


def expand_skill_targets(skill: str, *, source_root: Path | None = None) -> list[Target]:
    """A skill with `SKILL.md` in the checkout is one target; a family dir yields
    one target per direct sub-directory with `SKILL.md`; a missing dir yields
    none."""
    source_root = source_root or runner.REPO_ROOT
    root = source_root / skill
    if not root.is_dir():
        return [target for family in ALL_SKILLS if (source_root / family).is_dir()
                for target in expand_skill_targets(family, source_root=source_root) if target.name == skill]
    if (root / "SKILL.md").is_file():
        return [Target(root.name, root)]
    return [Target(sub.name, sub) for sub in sorted(root.iterdir())
            if sub.is_dir() and not sub.name.startswith(".") and (sub / "SKILL.md").is_file()]


def leaf_names(skills: list[str]) -> list[str]:
    """The names of the targets that `skills` expand to; a name without a target stays."""
    return sorted({name for skill in skills
                   for name in [target.name for target in expand_skill_targets(skill)] or [skill]})


def iter_install_targets(skills: list[str], addon_targets: list[Target] = (), *, source_root: Path | None = None) -> list[Target]:
    """Expanded core targets in skill order, then the addon targets."""
    targets: list[Target] = []
    for skill in skills:
        targets.extend(expand_skill_targets(skill, source_root=source_root))
    targets.extend(addon_targets)
    return targets


def validate_requested_skills(skills: list[str]) -> None:
    """`Skill not found: X` + `Run <install command> --list to see available skills.` and exit 1."""
    for skill in skills:
        if not expand_skill_targets(skill):
            report.error(f"Skill not found: {skill}")
            report.error(f"Run {report.install_command()} --list to see available skills.")
            raise InstallerExit(1)


def _present(path: Path) -> bool:
    return path.is_symlink() or path.is_dir()


def has_managed_installs(skills_root: Path, platform: str, *, excluding: tuple[Path, ...] = (),
                        on_error=report.warn) -> bool | None:
    """Whether a Core slot remains after the specified releases. None when its ownership is unknown."""
    skills_root = Path(skills_root).absolute()
    excluded = {Path(path).absolute() for path in excluding}
    state_path = locations.install_state_path(platform)
    try:
        state = read_inventory(state_path, platform=platform)
    except (OSError, ValueError) as exc:
        on_error(f"Keeping hooks and shared support because the install inventory could not be read: {exc}")
        return None
    if state is not None:
        for target in state["targets"]:
            path = inventory_destination(target["dest_path"]).absolute()
            if (target["target_name"] != "_shared" and not target.get("addon_id")
                    and target.get("owner") != "addon" and path.parent == skills_root
                    and path not in excluded and _present(path)):
                return True
    # After a host reference was released, another host's shared file must not
    # recreate the first host's installation merely by retaining a Core name.
    from uninstall_cleanup import platforms_referencing_target
    try:
        record_path = installed_payload.read_path(locations.record_root())
        record = installed_payload.read_file(record_path)
        recorded = installed_core_targets(record or {}, record_path).get(platform, {})
        if any(path not in excluded for path in recorded):
            return True
    except (OSError, ValueError) as exc:
        on_error(f'Keeping hooks and shared support because the install record could not be read: {exc}')
        return None

    def remains_for_host(path: Path) -> bool | None:
        if path.absolute() in excluded:
            return False
        try:
            return _present(path) and not platforms_referencing_target(path, current_manifest=state_path,
                                                                        current_platform=platform)
        except (OSError, ValueError) as exc:
            on_error(f'Keeping hooks and shared support because host references are unresolved for {path}: {exc}')
            return None

    # Source-name presence remains evidence when no other-host attribution excludes it.
    for skill in ALL_SKILLS:
        targets = expand_skill_targets(skill)
        for name in [target.name for target in targets] or [skill]:
            remains = remains_for_host(skills_root / name)
            if remains is not False:
                return remains
    return False


def cleanup_deprecated_installed_skills(ctx: "Context", skills_root: Path) -> bool:
    """Let the retirement owner classify candidates and resume unfinished work."""
    result = runner.run_tool("_shared/retired_assets.py",
                             "--skills-root", str(skills_root), "--source-root", source.input_record()['root'],
                             "--ghost-root", str(locations.record_root()), "--platform", ctx.platform,
                             "--commands-root", str(locations.claude_commands_dir()), no_bytecode=True)
    return result.ok


def collect_targets(ctx: "Context", skills: list[str]) -> Collected:
    """Validate the requested skills, read the addon targets, expand everything
    and count. Support total is 1 when
    `<checkout>/_shared` exists."""
    validate_requested_skills(skills)
    addon_targets = list(addons.collect_addon_targets(ctx))
    skill_targets = iter_install_targets(skills)
    all_targets = [*skill_targets, *addon_targets]
    support_total = 1 if (runner.REPO_ROOT / "_shared").is_dir() else 0
    return Collected(skill_targets=skill_targets, addon_targets=addon_targets,
                     skill_target_total=len(all_targets), support_target_total=support_total)


def check_addon_collisions(ctx: "Context") -> None:
    """`addon_installer.py detect-collisions ...`; rc 2 → `Addon install collision
    detected; aborting install` exit 1, other non-zero → `Addon manifest error;
    aborting install` exit 1."""
    if not ctx.addon_sources:
        return
    args: list[str] = ["detect-collisions", *addons.selection_args(ctx.addon_sources, ctx.addon_ids, "--source")]
    args.extend(["--platform", ctx.platform, "--skills-dir", str(ctx.skills_dir),
                 "--addons-dir", str(locations.addons_dir(ctx.platform)),
                 "--claude-commands-dir", str(locations.claude_commands_dir()),
                 "--resources-dir", str(locations.resources_dir(ctx.platform))])
    for skill in ALL_SKILLS:
        args.extend(["--core-skill", skill])
        for target in expand_skill_targets(skill):
            args.extend(["--core-skill", target.name])
    result = runner.run_tool("_shared/addon_installer.py", *args)
    if result.returncode == 2:
        report.error("Addon install collision detected; aborting install")
        raise InstallerExit(1)
    if result.returncode != 0:
        report.error("Addon manifest error; aborting install")
        raise InstallerExit(1)


def skill_description(skill_md: Path) -> str:
    """The `description:` value from the first 8 lines, quotes stripped, cut to
    77 chars + `...` when longer than 80."""
    path = Path(skill_md)
    if not path.is_file():
        return ""
    with open(path, encoding="utf-8", errors="replace") as handle:
        head = [next(handle, "") for _ in range(8)]
    for line in head:
        if "description:" not in line:
            continue
        text = line.rstrip("\n").rsplit("description:", 1)[1].lstrip(" ")
        if text.startswith('"'):
            text = text[1:]
        if text.endswith('"'):
            text = text[:-1]
        return text[:77] + "..." if len(text) > 80 else text
    return ""


def list_skills() -> int:
    """`Available skills:` listing with families and sub-skills; exit code 0."""
    report.emit("Available skills:")
    report.emit("")
    for skill in ALL_SKILLS:
        targets = expand_skill_targets(skill)
        if not targets:
            report.emit(f"  {skill:<28} (source missing)")
            continue
        if targets[0].name == skill and len(targets) == 1:
            report.emit(f"  {skill:<28} {skill_description(targets[0].path / 'SKILL.md')}")
            continue
        report.emit(f"  {skill} (family, {len(targets)} sub-skills)")
        for target in targets:
            report.emit(f"    └ {target.name:<26} {skill_description(target.path / 'SKILL.md')}")
    return 0
