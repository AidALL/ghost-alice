"""Which skills and support files install, and from where."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import TYPE_CHECKING

from install_state_writer import read_state
from installer import addons, locations, report, runner
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

DEPRECATED_INSTALLED_SKILLS = (
    "harness-security-scan",
    "session-intent-guard",
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


def expand_skill_targets(skill: str) -> list[Target]:
    """A skill with `SKILL.md` in the checkout is one target; a family dir yields
    one target per direct sub-directory with `SKILL.md`; a missing dir yields
    none."""
    root = runner.REPO_ROOT / skill
    if not root.is_dir():
        return []
    if (root / "SKILL.md").is_file():
        return [Target(skill, root)]
    return [Target(sub.name, sub) for sub in sorted(root.iterdir())
            if sub.is_dir() and not sub.name.startswith(".") and (sub / "SKILL.md").is_file()]


def iter_install_targets(skills: list[str], addon_targets: list[Target] = ()) -> list[Target]:
    """Expanded core targets in skill order, then the addon targets."""
    targets: list[Target] = []
    for skill in skills:
        targets.extend(expand_skill_targets(skill))
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


def has_managed_installs(skills_root: Path, platform: str, *, excluding: tuple[Path, ...] = ()) -> bool:
    """Whether a Core slot remains after excluding the caller's confirmed removal paths."""
    skills_root = Path(skills_root).absolute()
    excluded = {Path(path).absolute() for path in excluding}
    state_path = locations.install_state_path(platform)
    try:
        state = read_state(state_path)
        if state is not None and state.get("platform", platform) != platform:
            raise ValueError(f"install-state platform mismatch: {state_path}")
    except (OSError, ValueError) as exc:
        report.warn(f"Keeping shared support because the install inventory could not be read: {exc}")
        return True
    if state is not None:
        for target in state["targets"]:
            path = Path(target["dest_path"]).absolute()
            if (target["target_name"] != "_shared" and not target.get("addon_id")
                    and target.get("owner") != "addon" and path.parent == skills_root
                    and path not in excluded and _present(path)):
                return True
    # Inventory and source-name presence are independent evidence of remaining Core slots.
    for skill in ALL_SKILLS:
        targets = expand_skill_targets(skill)
        if not targets:
            path = Path(skills_root) / skill
            if path.absolute() not in excluded and _present(path):
                return True
            continue
        for target in targets:
            path = Path(skills_root) / target.name
            if path.absolute() not in excluded and _present(path):
                return True
    return False


def cleanup_deprecated_installed_skills(ctx: "Context", skills_root: Path) -> bool:
    """Move each DEPRECATED_INSTALLED_SKILLS entry under `skills_root` to
    `~/.ghost-alice/deprecated-skill-backups/<name>-<stamp>[-n]`, then run
    `retired_assets.py`. False when a move or the
    retirement fails."""
    skills_root = Path(skills_root)
    if not skills_root.is_dir():
        return True
    for skill in DEPRECATED_INSTALLED_SKILLS:
        target = skills_root / skill
        if not (target.exists() or target.is_symlink()):
            continue
        backup_root = locations.deprecated_skill_backup_root()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine = backup_root / f"{skill}-{stamp}"
        suffix = 1
        backup_root.mkdir(parents=True, exist_ok=True)
        while quarantine.exists() or quarantine.is_symlink():
            quarantine = backup_root / f"{skill}-{stamp}-{suffix}"
            suffix += 1
        try:
            os.rename(target, quarantine)
        except OSError:
            report.error(f"Failed to move deprecated installed skill: {target}")
            return False
        report.warn(f"Deprecated installed skill moved out of discovery path: {skill} -> {quarantine}")
    result = runner.run_tool("_shared/retired_assets.py",
                             "--skills-root", str(skills_root), "--source-root", str(runner.REPO_ROOT),
                             "--ghost-root", str(locations.ghost_root()), "--platform", ctx.platform,
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
    args: list[str] = ["detect-collisions"]
    for source in ctx.addon_sources:
        args.extend(["--source", str(source)])
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
