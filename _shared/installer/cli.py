"""The installer command line: options, the command table, dispatch and the install step order.

Port of `install.sh:29-37, 49-72, 115-269, 448-562` and `installer_lib/install.sh:880-1139`.
`COMMAND_LINE` is the one table of commands: `parse_options`, `find_command`, `dispatch`,
`run_command` and `print_help` read it. `run_install` holds the install step order that
`docs/reference/installer-architecture.md` draws.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __name__ == "__main__" and not __package__:
    # Run as a script by the thin shells (`python _shared/installer/cli.py ...`):
    # sys.path[0] is this directory, which would shadow standard-library names
    # and does not make `installer` importable. Replace it with `_shared` and
    # run the package module once.
    sys.path[0] = str(Path(__file__).resolve().parents[1])
    from installer.cli import main as _main  # noqa: E402

    raise SystemExit(_main())

from dataclasses import dataclass, field  # noqa: E402
import os  # noqa: E402
from typing import Callable  # noqa: E402

from installer import (addons, auto, config, diagnostics, hosts, lifecycle, locations, lock, prerequisites,  # noqa: E402
                       preservation, report, runner, selection, source, transaction)
from installer.runner import InstallerExit  # noqa: E402

ISOLATED_REJECTION = "isolated destinations require the Python hook installer; shell source integration is unsupported"
VISIBILITY_FLAGS = ("--visibility", "--agent-visibility")


@dataclass
class Options:
    """What `parse_options` reads from argv (`install.sh:49-72, 115-201`)."""

    platform: str = "claude"
    platform_explicit: bool = False
    prompt_platform: bool = False
    skip_source_health: bool = False
    update_source: bool = False
    cleanup_pending: bool = False
    addon_sources: list[str] = field(default_factory=list)
    addon_tags: list[str] = field(default_factory=list)
    addon_skip: bool = False
    list_addons: bool = False
    verbose: bool = False
    visibility: str | None = None
    args: list[str] = field(default_factory=list)


@dataclass
class Context:
    """What the shell kept in globals for one command. The checkout the installer
    runs from is `runner.REPO_ROOT`, never a field here."""

    options: Options
    platform: str = "claude"
    skills_dir: Path | None = None
    addon_sources: list[str] = field(default_factory=list)
    addon_sources_prepared: bool = False
    addon_targets: list = field(default_factory=list)
    visibility: str = "dynamic"
    hook_change: object | None = None
    record_source: dict | None = None

    @property
    def verbose(self) -> bool:
        return self.options.verbose


@dataclass(frozen=True)
class HelpRow:
    """One `--help` row under one section: the usage column and its description."""

    section: str
    usage: str
    text: str


@dataclass(frozen=True)
class Command(HelpRow):
    """One installer command and how `run_command` runs it.

    `names` are the tokens that select it: `args[0]`, or, with `flag`, an
    option that `parse_options` records in that `Options` field wherever it
    appears. Install has no names: it runs when `args[0]` is empty or a skill
    name. `run(ctx)` returns the exit code. `lock` is the install-lock command
    name held around `run` (None: no lock); `before_lock` runs first, outside
    the lock. `platform` False: the command needs no platform, so
    `--prompt-platform` does not ask and the addon sources are not prepared.
    """

    names: tuple[str, ...]
    run: Callable[["Context"], int]
    flag: str | None = None
    lock: str | None = None
    platform: bool = True
    before_lock: Callable[["Context"], None] | None = None


COMMON, REMOVAL, ADVANCED = "Common commands", "Removal commands", "Advanced/operator commands"
HELP_SECTIONS = (COMMON, REMOVAL, ADVANCED)
# The usage examples: arguments after the install command, and the comment.
HELP_USAGE = (
    ("", "Install to detected AI tools"),
    (" --addon autopilot", "Install official autopilot addon to detected AI tools"),
    (" --platform claude", "Install only to Claude Code"),
    (" --platform codex", "Install to Codex"),
)

# The handlers are lambdas, so they call the owning module's function as it is at run time.
UNINSTALL = Command(REMOVAL, "--uninstall, -u",
                    "Full uninstall: remove managed hooks, bootstrap, support state, and install targets",
                    names=("--uninstall", "--remove", "-u"),
                    run=lambda ctx: lifecycle.uninstall(ctx, ctx.options.args[1:]), lock="uninstall")
INSTALL = Command(ADVANCED, "SKILL_NAMES", "Install specified skills only", names=(),
                  run=lambda ctx: run_install(ctx), lock="install", before_lock=lambda ctx: prepare_install(ctx))
COMMAND_LINE: tuple[HelpRow, ...] = (
    HelpRow(COMMON, "--platform, -p PLAT", "Choose claude | codex"),
    HelpRow(COMMON, "--prompt-platform", "Ask which AI tool to install"),
    Command(COMMON, "--status, -s", "Show install status", names=("--status", "-s"),
            run=lambda ctx: diagnostics.check_status(ctx)),
    HelpRow(COMMON, "--visibility strict|dynamic|minimal", "Set user-facing governance message visibility profile"),
    HelpRow(COMMON, "--agent-visibility strict|dynamic|minimal", "Alias for --visibility"),
    Command(COMMON, "--doctor", "Run install diagnostics", names=("--doctor",),
            run=lambda ctx: diagnostics.run_doctor(ctx)),
    Command(COMMON, "--repair", "Re-provision missing managed targets (never overwrites user-owned slots)",
            names=("--repair",), run=lambda ctx: lifecycle.run_repair(ctx), lock="repair"),
    HelpRow(COMMON, "--verbose, -v", "Show per-target install details"),
    HelpRow(COMMON, "--addon autopilot", "Install official autopilot addon; --platform is optional"),
    UNINSTALL,
    HelpRow(REMOVAL, "--platform PLAT --uninstall SKILL_NAMES", "Remove selected skills from one platform"),
    INSTALL,
    Command(ADVANCED, "--list, -l", "List available skills", names=("--list", "-l"),
            run=lambda ctx: selection.list_skills(), platform=False),
    Command(ADVANCED, "--cleanup-pending", "Clean false-positive pending entries", names=("--cleanup-pending",),
            run=lambda ctx: preservation.cleanup_pending(ctx), flag="cleanup_pending", lock="cleanup-pending"),
    Command(ADVANCED, "--update-source", "Stash source checkout local changes and run git pull --ff-only",
            names=("--update-source",), run=lambda ctx: source.update_source(), flag="update_source",
            platform=False),
    HelpRow(ADVANCED, "--addon-source PATH|URL", "Add local addon repo or git URL source"),
    HelpRow(ADVANCED, "--addon-tag TAG", "Checkout branch/tag for git URL addon sources"),
    HelpRow(ADVANCED, "--addon-skip", "Disable addon installation"),
    Command(ADVANCED, "--list-addons", "List addon manifest targets", names=("--list-addons",),
            run=lambda ctx: addons.list_addons(ctx), flag="list_addons"),
    HelpRow(ADVANCED, "--skip-source-health", "Skip source health gate"),
    Command(ADVANCED, "--sync-commands", "Sync workspace commands", names=("--sync-commands",),
            run=lambda ctx: hosts.sync_commands(ctx, "sync", ctx.platform)),
    Command(ADVANCED, "--check-commands", "Check workspace commands only", names=("--check-commands",),
            run=lambda ctx: hosts.sync_commands(ctx, "check", ctx.platform)),
    Command(ADVANCED, "gateway ARGS...", "Run the context gateway installer once with ARGS", names=("gateway",),
            run=lambda ctx: runner.run_tool("_shared/context_gateway_install.py", *ctx.options.args[1:]).returncode,
            lock="gateway", platform=False),
    Command(ADVANCED, "--help, -h", "Show this help", names=("--help", "-h"), run=lambda ctx: print_help(),
            platform=False),
)
COMMANDS: tuple[Command, ...] = tuple(row for row in COMMAND_LINE if isinstance(row, Command))


def _fail(message: str, code: int = 1) -> None:
    report.error(message)
    raise InstallerExit(code)


def parse_options(argv: list[str]) -> Options:
    """`--platform|-p`, `--prompt-platform`, `--auto|-a` (no-op), `--skip-source-health`,
    `--addon-source`, `--addon-tag`, `--addon-skip`, `--verbose|-v`,
    `--visibility|--agent-visibility` and the table's flag commands
    (`Command.flag`); everything else stays in `args` in order. Missing or
    invalid values print the shell's error and exit 1 (`install.sh:115-201`)."""
    options = Options()
    flag_fields = {name: command.flag for command in COMMANDS if command.flag for name in command.names}
    tokens = list(argv)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        has_value = index + 1 < len(tokens)
        if token in ("--platform", "-p"):
            if not has_value:
                _fail("--platform requires a value (claude|codex)")
            options.platform = tokens[index + 1]
            options.platform_explicit = True
            if options.platform not in locations.PLATFORMS:
                _fail(f"Unknown platform: {options.platform} (must be claude or codex)")
            index += 2
        elif token == "--prompt-platform":
            options.prompt_platform = True
            index += 1
        elif token in ("--auto", "-a"):
            index += 1
        elif token == "--skip-source-health":
            options.skip_source_health = True
            index += 1
        elif token in flag_fields:
            setattr(options, flag_fields[token], True)
            index += 1
        elif token == "--addon-source":
            if not has_value:
                _fail("--addon-source requires a value")
            options.addon_sources.append(tokens[index + 1])
            index += 2
        elif token == "--addon-tag":
            if not has_value:
                _fail("--addon-tag requires a value")
            options.addon_tags.append(tokens[index + 1])
            index += 2
        elif token == "--addon-skip":
            options.addon_skip = True
            index += 1
        elif token in ("--verbose", "-v"):
            options.verbose = True
            index += 1
        elif token in VISIBILITY_FLAGS:
            if not has_value:
                _fail(f"{token} requires a value (strict|dynamic|minimal)")
            options.visibility = tokens[index + 1]
            if options.visibility not in config.VISIBILITY_PROFILES:
                _fail(f"Unknown agent visibility profile: {options.visibility} (must be strict, dynamic, or minimal)")
            index += 2
        else:
            options.args.append(token)
            index += 1
    return options


def reject_isolated_destination(argv: list[str]) -> None:
    """`--destination-file`, `--destination-file=...` or `--isolated` anywhere → ISOLATED_REJECTION, exit 2 (`install.sh:29-37`)."""
    for arg in argv:
        if arg in ("--destination-file", "--isolated") or arg.startswith("--destination-file="):
            print(f"[ERROR] {ISOLATED_REJECTION}", file=sys.stderr)
            raise InstallerExit(2)


def args_request_uninstall(args: list[str]) -> bool:
    """Any name of the uninstall command in args (`install.sh:215-225`)."""
    return any(arg in UNINSTALL.names for arg in args)


def normalize_official_addon_shortcuts(options: Options) -> None:
    """`--addon NAME` pairs become addon sources unless an uninstall is requested;
    a missing or unknown name is an error exit 1 (`install.sh:227-253`)."""
    if args_request_uninstall(options.args):
        return
    normalized: list[str] = []
    index = 0
    while index < len(options.args):
        if options.args[index] == "--addon":
            index += 1
            if index >= len(options.args):
                _fail("--addon requires a value (autopilot)")
            name = options.args[index]
            addon_source = addons.official_addon_source(name)
            if addon_source is None:
                _fail(f"Unknown official addon: {name} (supported: autopilot)")
            options.addon_sources.append(addon_source)
        else:
            normalized.append(options.args[index])
        index += 1
    options.args = normalized


def find_command(options: Options) -> Command | None:
    """The table's command for these options: a set flag command (table order),
    else the command `args[0]` names, else install when `args[0]` is empty or a
    skill name. None for any other `args[0]` that starts with `-`: no skill
    name does."""
    for command in COMMANDS:
        if command.flag is not None and getattr(options, command.flag):
            return command
    first = options.args[0] if options.args else ""
    for command in COMMANDS:
        if first in command.names:
            return command
    return None if first.startswith("-") else INSTALL


def auto_detect_requested(options: Options) -> bool:
    """Install without an explicit or prompted platform runs once per detected
    platform; a command runs once (`install.sh:255-269`)."""
    return not options.platform_explicit and not options.prompt_platform and find_command(options) is INSTALL


def plain_full_uninstall_requested(options: Options) -> bool:
    """The uninstall command alone, without an explicit or prompted platform (`install.sh:271-278`)."""
    return (find_command(options) is UNINSTALL and len(options.args) == 1
            and not options.platform_explicit and not options.prompt_platform)


def prompt_platform() -> str:
    """Interactive platform choice; no TTY → error exit 1; `q` → `Installation cancelled.` exit 0 (`uninstall.sh:5-38`)."""
    if not sys.stdin.isatty():
        _fail("--prompt-platform requires an interactive terminal.")
    while True:
        report.emit("Select AI tool to install:")
        report.emit("  1) Claude Code  (~/.claude/skills)")
        report.emit("  2) Codex        (~/.agents/skills)")
        report.emit("  q) Cancel")
        choice = input("Enter number or name: ")
        lowered = choice.strip().lower()
        if lowered in ("1", "claude", "claude code"):
            return "claude"
        if lowered in ("2", "codex", "openai codex"):
            return "codex"
        if lowered in ("q", "quit", "exit", "cancel"):
            report.info("Installation cancelled.")
            raise InstallerExit(0)
        report.warn(f"Unknown input: {choice}")
        report.emit("")


def print_help() -> int:
    """The `--help` text: the usage lines with the reported install command, then
    each section's rows in table order (`install.sh:507-546`)."""
    report.emit("Usage:")
    for arguments, comment in HELP_USAGE:
        report.emit(f"  {report.install_command() + arguments:<45}# {comment}")
    for section in HELP_SECTIONS:
        report.emit("")
        report.emit(f"{section}:")
        for row in COMMAND_LINE:
            if row.section != section:
                continue
            # The usage column is 23 wide; a longer usage stands alone and its text follows on the next line.
            if len(row.usage) <= 21:
                report.emit(f"  {row.usage:<23}{row.text}")
            else:
                report.emit(f"  {row.usage}")
                report.emit(f"{'':26}{row.text}")
    return 0


def build_context(options: Options) -> Context:
    """Platform, skills dir, prepared addon sources and the effective visibility for one command."""
    return Context(options=options, platform=options.platform,
                   skills_dir=locations.skills_dir(options.platform), addon_sources=list(options.addon_sources),
                   visibility=config.resolve_effective_visibility(options.visibility))


def _details_log() -> None:
    log = report.state().log_file
    if log is not None:
        report.error(f"Details log: {log}")


def _place_targets(ctx: Context, collected: selection.Collected, copy_only: bool) -> tuple[transaction.SyncCounts, int, int]:
    """S12: `_shared` and every target, as links (claude) or one copy batch (codex) (`install.sh:910-1058`)."""
    skills_dir = Path(ctx.skills_dir)
    shared_src = runner.REPO_ROOT / "_shared"
    shared_dest = skills_dir / "_shared"
    counts = transaction.SyncCounts()
    batch = transaction.CopyBatch()
    installed = skipped = 0
    if copy_only:
        if shared_src.is_dir():
            if shared_dest.exists() or shared_dest.is_symlink():
                counts.updated += 1
                status = "updated"
                report.detail_warn("_shared. Overwriting existing copy")
            else:
                counts.installed += 1
                status = "new"
            batch.add(shared_src, shared_dest, "_shared", "support", status)
    elif shared_src.is_dir():
        if shared_dest.is_symlink() and os.readlink(shared_dest) == str(shared_src):
            counts.skipped += 1
            status = "current"
        elif shared_dest.exists() or shared_dest.is_symlink():
            counts.updated += 1
            status = "updated"
        else:
            counts.installed += 1
            status = "new"
        rc = transaction.install_shared(ctx, skills_dir, False)
        if rc != 0:
            raise InstallerExit(rc)
        report.write_target_event(ctx.platform, "_shared", "support", status)
    for target in collected.all_targets:
        dest = skills_dir / target.name
        if copy_only:
            if dest.exists() or dest.is_symlink():
                counts.updated += 1
                status = "updated"
                report.detail_warn(f"{target.name}. Overwriting existing install")
            else:
                counts.installed += 1
                status = "new"
            batch.add(target.path, dest, target.name, "skill", status, target.provider)
            installed += 1
            continue
        if dest.is_symlink() and os.readlink(dest) == str(target.path):
            counts.skipped += 1
            report.detail_warn(f"{target.name}. Already installed (skipped)")
            report.write_target_event(ctx.platform, target.name, "skill", "current")
            skipped += 1
            continue
        if dest.exists() or dest.is_symlink():
            counts.updated += 1
            status = "updated"
            report.detail_warn(f"{target.name}. Overwriting existing install")
        else:
            counts.installed += 1
            status = "new"
        if transaction.link_or_copy(ctx, target, dest) != 0:
            report.error(f"Managed target replacement failed: {dest}")
            raise InstallerExit(1)
        report.detail_ok(f"{target.name} -> installed")
        report.write_target_event(ctx.platform, target.name, "skill", status)
        installed += 1
    if copy_only and batch.pairs:
        label = "  [2/5] Skill sync         " if report.report_enabled() and report.live_counter_enabled() else None
        events_during_batch = report.state().event_file is not None
        if not transaction.copy_targets_batch(ctx, batch, progress_label=label):
            raise InstallerExit(1)
        if not events_during_batch:
            for name, kind, status in zip(batch.labels, batch.kinds, batch.statuses):
                report.write_target_event(ctx.platform, name, kind, status)
        for name in batch.labels:
            if name == "_shared":
                report.detail_ok("_shared → copied")
            else:
                report.detail_ok(f"{name} → copied (copy-only compatibility mode)")
    return counts, installed, skipped


def prepare_install(ctx: Context) -> None:
    """S1 prerequisites and S2 the git hook path, before the install lock."""
    prerequisites.install_prerequisites()
    ctx.hook_change = source.setup_git_hooks()


def run_install(ctx: Context) -> int:
    """S4–S18 for the skills in `args` (every skill when none), under the install
    lock that `run_command` takes and releases (`docs/reference/installer-architecture.md`)."""
    options = ctx.options
    skills = list(options.args) or list(selection.ALL_SKILLS)
    skills_dir = Path(ctx.skills_dir)
    visibility = ctx.visibility
    # The first install over an earlier installer's records creates the install record before anything is placed.
    report.run_logged(transaction.migrate_record, ctx)
    # S4 source, S5 selection (contradiction 9: the source check comes first)
    if not report.run_logged(source.check_source_health, skip=options.skip_source_health):
        raise InstallerExit(1)
    collected = selection.collect_targets(ctx, skills)
    skills_dir.mkdir(parents=True, exist_ok=True)
    # S6–S10: checks before any placement. Deprecated installed skills leave the
    # discovery path first, so that the encoding guard does not scan their content.
    if not report.run_logged(selection.cleanup_deprecated_installed_skills, ctx, skills_dir):
        raise InstallerExit(1)
    if not report.run_logged(preservation.encoding_guard, ctx, skills):
        report.error(f"encoding guard failed; invalid-utf8 or semantic asset details: {report.state().log_file}")
        raise InstallerExit(1)
    if not report.run_logged(preservation.preflight, ctx, skills):
        raise InstallerExit(1)
    report.run_logged(addons.resume_pending_uninstalls, ctx)
    selection.check_addon_collisions(ctx)
    # S11 report start
    if report.report_enabled() and report.live_counter_enabled():
        report.print_start(ctx.platform, collected.total_target_count, visibility)
    elif not report.compact_output_enabled():
        report.info("[1/5] Preflight: ok")
    # S12 placement
    copy_only = locations.prefers_copy_install(ctx.platform)
    counts, installed, skipped = _place_targets(ctx, collected, copy_only)
    if not report.compact_output_enabled():
        report.print_skill_sync_summary(collected.skill_target_total, collected.support_target_total, counts.installed,
                                        counts.updated, counts.skipped, "copy-only compatibility mode" if copy_only else "")
        report.info(f"[3/5] Runtime config: platform={ctx.platform}, visibility={visibility}")
        report.emit("")
        report.ok(f"Done: {installed} installed, {skipped} skipped")
        report.emit("")
        report.info(f"Install path: {skills_dir}")
        if ctx.platform == "codex" and copy_only:
            report.info(f"To update skills safely: cd {runner.REPO_ROOT} && {report.install_command()} --update-source, "
                        f"then re-run {report.install_command()}")
        else:
            report.info(f"To update skills safely: cd {runner.REPO_ROOT} && {report.install_command()} --update-source")
        if installed > 0 and not copy_only:
            report.info("With symlink install, safe source updates refresh linked skills after the checkout fast-forwards.")
    # S13 runtime, S14 hooks, S15 managed blocks, S16 commands (contradiction 9: blocks after the runtime and hooks)
    if not report.run_logged(transaction.sync_runtime_shared, ctx):
        report.error("Runtime shared core sync failed. Aborting hook install")
        _details_log()
        raise InstallerExit(1)
    if not report.run_logged(hosts.install_hooks, ctx, "install", ctx.platform):
        report.error("Hook install failed. Aborting skill install")
        _details_log()
        raise InstallerExit(1)
    if not report.compact_output_enabled():
        report.info("[4/5] Hooks: install complete")
    bootstrap = hosts.ensure_claude_bootstrap if ctx.platform == "claude" else hosts.ensure_codex_bootstrap
    if not report.run_logged(bootstrap, ctx):
        raise InstallerExit(1)
    if report.run_logged(hosts.sync_commands, ctx, "sync", ctx.platform) != 0:
        report.warn("Commands sync failed (continuing)")
    # S17 records (a later change replaces these with one install record)
    if not report.run_logged(transaction.verify_after_copy, ctx, skills_dir, copy_only, skills):
        raise InstallerExit(1)
    if not report.run_logged(transaction.write_ownership_markers, ctx, skills_dir, copy_only, skills):
        raise InstallerExit(1)
    if not report.run_logged(transaction.snapshot_after_install, ctx):
        raise InstallerExit(1)
    report.run_logged(addons.migrate_state_once, ctx)
    if not report.run_logged(addons.write_sidecars, ctx, skills_dir):
        raise InstallerExit(1)
    if not report.run_logged(transaction.write_install_state_manifest, ctx, skills_dir, copy_only, skills, ctx.hook_change):
        raise InstallerExit(1)
    # S18 result
    report.write_event(ctx.platform, collected.total_target_count, counts.skipped, counts.updated, counts.installed)
    if report.report_enabled():
        if report.live_counter_enabled():
            if not copy_only:
                report.emit("\r" + report.skill_sync_line(counts.skipped, counts.updated, counts.installed))
            report.print_tail(ctx.platform, visibility)
        else:
            report.print_full(ctx.platform, collected.total_target_count, counts.skipped, counts.updated,
                              counts.installed, visibility)
    else:
        report.ok("[5/5] Verification: install state recorded")
        report.emit("")
        report.info("[Ghost-ALICE] User: when local changes are detected during the agent tool update, "
                    "they are backed up instead of overwritten. Next time you open Claude/Codex, please ask the line below.")
        report.emit("")
        report.emit("    Please review backed-up changes.")
        report.emit("")
        report.info("[Ghost-ALICE] Tech: undecided entries live in ~/.ghost-alice/pending-merges/<platform>/manifest.json. "
                    "Missing, empty, or unparsable manifests pass silently.")
    return 0


def run_command(command: Command, ctx: Context) -> int:
    """`before_lock`, then `run` under the command's install lock when it names one; the exit code of `run`."""
    if command.before_lock is not None:
        command.before_lock(ctx)
    if command.lock is None:
        return command.run(ctx)
    lock_path = lock.acquire(command.lock, ctx.platform)
    try:
        return command.run(ctx)
    finally:
        lock.release(lock_path)


def dispatch(options: Options) -> int:
    """`--addon` shortcuts, then the table's command (`find_command`): an unknown
    option ends with exit 1, the full uninstall and auto mode run once per
    detected platform, every other command runs once through `run_command`
    (`install.sh:253-562`)."""
    normalize_official_addon_shortcuts(options)
    command = find_command(options)
    if command is None:
        report.error(f"Unknown option: {options.args[0]}")
        report.emit(f"See {report.install_command()} --help")
        return 1
    if plain_full_uninstall_requested(options):
        detected = locations.detect_uninstall_platforms()
        if not detected:
            report.warn("No install targets detected. Need a platform home or ~/.ghost-alice/install-state/<platform>.json.")
            return 0
        return auto.run_uninstall_children(build_context(options), detected)
    if auto_detect_requested(options):
        detected = locations.detect_present_platforms()
        if not detected:
            report.warn("No AI platform home directories detected. At least one of ~/.claude, ~/.codex is required.")
            return 0
        ctx = build_context(options)
        if not options.addon_skip and ctx.addon_sources:
            addons.prepare_sources(ctx)
        return auto.run_install_children(ctx, detected)
    if command.platform and options.prompt_platform:
        if options.platform_explicit:
            report.warn("--prompt-platform ignored because --platform is already specified.")
        else:
            options.platform = prompt_platform()
    ctx = build_context(options)
    if command.platform and not options.addon_skip and ctx.addon_sources:
        addons.prepare_sources(ctx)
    return run_command(command, ctx)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the thin shells: returns the exit code; an early stop
    (`_fail`, the isolated rejection) becomes its code here."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        reject_isolated_destination(argv)
        options = parse_options(argv)
        report.configure(verbose=options.verbose,
                         report_child=os.environ.get("GHOST_ALICE_INSTALL_REPORT_CHILD") == "1")
        source.assert_session_gate_contract()
        return dispatch(options)
    except InstallerExit as stop:
        return stop.code
