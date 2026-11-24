"""The installer command line: options, the command table, dispatch and the install step order.

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
    try:
        from installer.cli import main as _main  # noqa: E402
    except ImportError as error:
        # This installer lost one of its modules; the shells report a lost `cli.py` with the same sentence.
        raise SystemExit(f"[ERROR] This installer's files are incomplete: {error}. Run the repair from a Ghost-ALICE "
                         "checkout that holds the same Core files.")

    raise SystemExit(_main())

from dataclasses import dataclass, field, replace  # noqa: E402
import os  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from typing import Callable  # noqa: E402

import install_transaction  # noqa: E402
import installed_payload  # noqa: E402
from installer import (addons, auto, config, diagnostics, hosts, lifecycle, locations, lock, prerequisites,  # noqa: E402
                       preservation, report, runner, selection, source, terminal, transaction)
from installer.runner import InstallerExit  # noqa: E402

ISOLATED_REJECTION = "isolated destinations require the Python hook installer; shell source integration is unsupported"
VISIBILITY_FLAGS = ("--visibility", "--agent-visibility")
# The install source that the calling installer read before it downloaded the release.
START_ORIGIN_ENV = 'GHOST_ALICE_START_ORIGIN'
# The request that a starting installer sends to the release that it downloaded.
ACQUIRED_RELEASE = '--install-acquired-release'


@dataclass
class Options:
    """What `parse_options` reads from argv."""

    platform: str = "claude"
    platform_explicit: bool = False
    prompt_platform: bool = False
    skip_source_health: bool = False
    local_source: bool = False
    adopt_source: bool = False
    merged_checkout: str | None = None
    update_source: bool = False
    cleanup_pending: bool = False
    addon_sources: list[str] = field(default_factory=list)
    addon_ids: list[str] = field(default_factory=list)
    addon_tags: list[str] = field(default_factory=list)
    addon_skip: bool = False
    list_addons: bool = False
    verbose: bool = False
    visibility: str | None = None
    args: list[str] = field(default_factory=list)
    argv: tuple[str, ...] = ()


@dataclass
class Context:
    """The state of one command. The checkout the installer
    runs from is `runner.REPO_ROOT`, never a field here."""

    options: Options
    platform: str = "claude"
    skills_dir: Path | None = None
    addon_sources: list[str] = field(default_factory=list)  # Effective install sources; Options retains the raw request.
    addon_sources_prepared: bool = False
    addon_ids: list[str] | None = None  # The addons of the sources that this host's process installs; None means all.
    held_addons: dict[str, dict[str, list[str]]] | None = None  # Per host and source, the ids `addons.held_ids` read.
    visibility: str = "dynamic"
    record_source: dict | None = None
    source_match: object | None = None

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
    name held around `run` (None: no lock). `platform` False: the command needs no platform, so
    `--prompt-platform` does not ask and the addon sources are not prepared.
    `checks_source`: `dispatch` compares the install record's source with this
    checkout (`source.check_install_source`) before anything else changes, and
    `run_command` compares again under the lock. `adopts_source`: the command
    may replace the install source; without it only the recorded checkout runs it.
    `active_runs`: the active installer runs the command. Another installer starts it with the
    same options and returns its exit code while the active package is complete
    (`source.active_installer`); otherwise this installer runs the command.
    """

    names: tuple[str, ...]
    run: Callable[["Context"], int]
    flag: str | None = None
    lock: str | None = None
    platform: bool = True
    checks_source: bool = False
    adopts_source: bool = False
    active_runs: bool = False


COMMON, REMOVAL, ADVANCED = "Common commands", "Removal commands", "Advanced commands"
HELP_SECTIONS = (COMMON, REMOVAL, ADVANCED)
# The usage examples: arguments after the install command, and the comment.
HELP_USAGE = (
    ("", "Install to detected AI tools"),
    (" --platform claude", "Install only to Claude Code"),
    (" --platform codex", "Install to Codex"),
)

# The handlers are lambdas, so they call the owning module's function as it is at run time.
UNINSTALL = Command(REMOVAL, "--uninstall, -u",
                    "Full uninstall: remove managed hooks, bootstrap, support state, and install targets",
                    names=("--uninstall", "--remove", "-u"),
                    run=lambda ctx: lifecycle.uninstall(ctx, ctx.options.args[1:]), lock="uninstall")
INSTALL = Command(ADVANCED, "SKILL_NAMES", "Install specified skills only", names=(),
                  run=lambda ctx: run_install(ctx), lock="install", checks_source=True, adopts_source=True)
COMMAND_LINE: tuple[HelpRow, ...] = (
    HelpRow(COMMON, "--platform, -p PLAT", "Choose claude | codex"),
    HelpRow(COMMON, "--prompt-platform", "Ask which AI tool to install"),
    Command(COMMON, "--status, -s", "Show install status", names=("--status", "-s"),
            run=lambda ctx: diagnostics.check_status(ctx)),
    HelpRow(COMMON, "--visibility strict|dynamic|minimal", "Set user-facing governance message visibility profile"),
    HelpRow(COMMON, "--agent-visibility strict|dynamic|minimal", "Alias for --visibility"),
    Command(COMMON, "--doctor", "Run install diagnostics", names=("--doctor",),
            run=lambda ctx: diagnostics.run_doctor(ctx)),
    Command(COMMON, "update [--remote URL]", "Update installed Core skills from the remote main branch",
            names=('update',), run=lambda ctx: run_update(ctx), platform=False),
    Command(COMMON, "--repair", "Re-provision missing managed targets (never overwrites an occupied slot that is not a clean managed target)",
            names=("--repair",), run=lambda ctx: lifecycle.run_repair(ctx), lock="repair", checks_source=True,
            active_runs=True),
    HelpRow(COMMON, "--verbose, -v", "Show per-target install details"),
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
    Command(ADVANCED, "--post-merge",
            "Run by the git post-merge hook: install when the merged checkout is the install source",
            names=("--post-merge",), run=lambda ctx: run_post_merge(ctx), platform=False),
    HelpRow(ADVANCED, "--merged-checkout PATH",
            "With --post-merge: the checkout the merge ran in (default: this checkout)"),
    HelpRow(ADVANCED, "--addon-source PATH|URL", "Add local addon repo or git URL source"),
    HelpRow(ADVANCED, "--addon-tag TAG", "Checkout branch/tag for git URL addon sources"),
    HelpRow(ADVANCED, "--addon-skip", "Disable addon installation"),
    Command(ADVANCED, "--list-addons", "List addon manifest targets", names=("--list-addons",),
            run=lambda ctx: addons.list_addons(ctx), flag="list_addons"),
    HelpRow(ADVANCED, "--skip-source-health", "Skip source health gate"),
    HelpRow(ADVANCED, "--local-source", "Install the files in this checkout or saved package"),
    HelpRow(ADVANCED, "--adopt-source", "Record this checkout as the development source"),
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


def install_release(root: Path, argv: list[str], *, remote: str | None = None, origin: dict | None = None) -> int:
    """Download the release, let it install itself for `root` and return its exit code. Another command can change the
    install source during the download, so the release receives the one read before it: `origin`, the registration of
    an update, else the source of `root` with the registration."""
    prerequisites.require_git_for_release()
    if origin is None:
        try:
            record = install_transaction.record_after_recovery(locations.record_root())
        except (installed_payload.RecordError, install_transaction.InstallTransactionError):
            # The downloaded release may support a record that the starting installer cannot read.
            record = None
        origin = dict(source.input_record(root, record=record), registered=source.registration(record))
    remote = remote or origin.get('remote') or source.DEFAULT_REMOTE
    with source.acquire_release(remote) as release:
        code = runner.run_command([sys.executable, str(release / '_shared/installer/cli.py'), ACQUIRED_RELEASE,
                                   str(root), remote, *argv], inherit_stdin=True,
                                  env={START_ORIGIN_ENV: json.dumps(origin)}).returncode
        if code == 1:
            report.info(report.message('release_older', remote=remote))
        return code


def install_acquired(options: Options, root: str, remote: str) -> int:
    """Install this downloaded release for `root`, the folder the calling installer started from, and for the install
    source that it read before the download."""
    if (source.install_request() or options.local_source or not remote or not Path(root).is_absolute()
            or not Path(root).is_dir()):
        raise ValueError('The downloaded release installs its own files for the folder and the remote that the calling '
                         'installer passes')
    if options.args[:1] == ['update']:
        return update_acquired(build_context(options), remote=remote)
    merged = options.args[:1] == ['--post-merge']
    if not merged and find_command(options) is not INSTALL:
        raise ValueError('The downloaded release accepts an installation, an update or a post-merge installation')
    start = json.loads(os.environ.get(START_ORIGIN_ENV) or '{}')
    if 'registered' not in start:
        raise ValueError('The calling installer passes the install source that it read before the download')
    # The files come from `remote`. The registration of the start decides, inside the install lock, whether this
    # command may replace the installation.
    return dispatch(Options() if merged else options,
                    provenance=source.release_input(runner.REPO_ROOT, dict(start, remote=remote)),
                    reinstall_from=Path(root), execution_cause='post-merge' if merged else 'install',
                    registered=start['registered'])


def _update_arguments(options: Options, *, downloaded: bool):
    import argparse
    parser = argparse.ArgumentParser(prog='ghost-alice update', add_help=downloaded)
    parser.add_argument('--remote')
    parser.add_argument('--ref')
    arguments = options.args[1:]
    args = parser.parse_args(arguments) if downloaded else parser.parse_known_args(arguments)[0]
    if args.ref not in (None, 'main', source.RELEASE_REF) or '--local-source' in options.argv:
        raise ValueError('Core update installs the remote main branch. To install local files, run the install script with --local-source.')
    return args


def _recorded_installation() -> tuple[dict, Path | None]:
    record_path = install_transaction.record_path_after_recovery(locations.record_root())
    record = installed_payload.read_file(record_path) or {}
    if source.registration(record) is None:
        raise ValueError('Update found no recorded installation source')
    return record, record_path


def _require_registration(record: dict, expected: dict | None, cause: str) -> None:
    if source.registration(record) != expected:
        raise ValueError(report.message('update_origin_changed' if cause == 'update' else 'install_origin_changed'))


def run_update(ctx: Context) -> int:
    """Download the release from the recorded remote and let it update the installation."""
    args = _update_arguments(ctx.options, downloaded=False)
    record, _ = _recorded_installation()
    origin = source.registration(record)
    return install_release(runner.REPO_ROOT, list(ctx.options.argv),
                           remote=args.remote or origin['remote'], origin=origin)


def update_acquired(ctx: Context, *, remote: str | None = None, coordinates: list[str] | None = None) -> int:
    """Update the installation from this downloaded release. `coordinates` holds the root, remote and ref that an
    earlier installer passes with `--install-acquired-update`."""
    args = _update_arguments(ctx.options, downloaded=True)
    record, record_path = _recorded_installation()
    registered = source.registration(record)
    if remote is not None:
        start_origin = os.environ.get(START_ORIGIN_ENV)
        if start_origin is not None:
            _require_registration(record, json.loads(start_origin), 'update')
        if remote != (args.remote or registered['remote']):
            raise ValueError(report.message('update_origin_changed'))
    origin = dict(record['source'])
    origin['remote'] = args.remote or registered['remote']
    if coordinates is not None and (coordinates[:2] != [origin['root'], origin['remote']]
                                    or coordinates[2:] not in (['main'], [source.RELEASE_REF])):
        raise ValueError(report.message('update_origin_changed'))
    return dispatch(replace(ctx.options, args=[], addon_skip=True), execution_cause='update',
                    provenance=source.release_input(runner.REPO_ROOT, origin), registered=registered,
                    update_core=(record, selection.installed_core_selection(record, record_path)))


def parse_options(argv: list[str]) -> Options:
    """`--platform|-p`, `--prompt-platform`, `--auto|-a` (no-op), `--skip-source-health`,
    `--adopt-source`, `--merged-checkout`, `--addon-source`, `--addon-tag`, `--addon-skip`, `--verbose|-v`,
    `--visibility|--agent-visibility` and the table's flag commands
    (`Command.flag`); everything else stays in `args` in order. Missing or
    invalid values print an error and exit 1."""
    options = Options(argv=tuple(argv))
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
        elif token == "--local-source":
            options.local_source = True
            index += 1
        elif token == "--adopt-source":
            options.adopt_source = True
            index += 1
        elif token == "--merged-checkout":
            if not has_value:
                _fail("--merged-checkout requires a value")
            options.merged_checkout = tokens[index + 1]
            index += 2
        elif token in flag_fields:
            setattr(options, flag_fields[token], True)
            index += 1
        elif token == "--addon-source":
            if not has_value:
                _fail("--addon-source requires a value")
            options.addon_sources.append(tokens[index + 1])
            index += 2
        elif token == "--addon-id":
            if os.environ.get('GHOST_ALICE_INSTALL_HOST_CHILD') != '1':
                _fail(report.message('private_addon_id'))
            if not has_value:
                _fail("--addon-id requires a value")
            options.addon_ids.append(tokens[index + 1])
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
    if options.addon_ids and (not options.platform_explicit or find_command(options) is not INSTALL):
        _fail(report.message('private_addon_id'))
    return options


def reject_isolated_destination(argv: list[str]) -> None:
    """`--destination-file`, `--destination-file=...` or `--isolated` anywhere → ISOLATED_REJECTION, exit 2."""
    for arg in argv:
        if arg in ("--destination-file", "--isolated") or arg.startswith("--destination-file="):
            print(f"[ERROR] {ISOLATED_REJECTION}", file=sys.stderr)
            raise InstallerExit(2)


def args_request_uninstall(args: list[str]) -> bool:
    """Any name of the uninstall command in args."""
    return any(arg in UNINSTALL.names for arg in args)


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
    platform; a command runs once."""
    return not options.platform_explicit and not options.prompt_platform and find_command(options) is INSTALL


def plain_full_uninstall_requested(options: Options) -> bool:
    """The uninstall command alone, without an explicit or prompted platform."""
    return (find_command(options) is UNINSTALL and len(options.args) == 1
            and not options.platform_explicit and not options.prompt_platform)


def prompt_platform() -> str:
    """Interactive platform choice; no TTY → error exit 1; `q` → `Installation cancelled.` exit 0."""
    if not sys.stdin.isatty():
        _fail("--prompt-platform requires an interactive terminal.")
    while True:
        try:
            choice = terminal.read_answer("Select AI tool to install:\n"
                                          "  1) Claude Code  (~/.claude/skills)\n"
                                          "  2) Codex        (~/.agents/skills)\n"
                                          "  q) Cancel\nEnter number or name: ")
        except (OSError, ValueError) as exc:
            _fail(report.message('terminal_input_unavailable', cause=exc))
        if choice is None:
            _fail(report.message('terminal_input_unavailable'))
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
    each section's rows in table order."""
    report.emit("Usage:")
    for arguments, comment in HELP_USAGE:
        report.emit(f"  {report.install_command() + arguments:<44} # {comment}")
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
                   skills_dir=locations.skills_dir(options.platform),
                   addon_sources=[] if options.addon_skip else list(options.addon_sources),
                   addon_ids=list(options.addon_ids) or None,
                   visibility=config.resolve_effective_visibility(options.visibility))


def _details_log() -> None:
    log = report.state().log_file
    if log is not None:
        report.error(f"Details log: {log}")


def _place_targets(ctx: Context, collected: selection.Collected, copy_only: bool) -> tuple[transaction.SyncCounts, int, int]:
    """Publish Core as host-owned copies; retain each host's existing add-on placement mode."""
    skills_dir = Path(ctx.skills_dir)
    counts = transaction.SyncCounts()
    batch = transaction.CopyBatch()
    current_links = []
    installed = skipped = 0
    targets = [(target, "skill") for target in collected.all_targets]
    if collected.support_target_total:
        targets.insert(0, (selection.Target("_shared", runner.REPO_ROOT / "_shared"), "support"))
    for target, kind in targets:
        dest = skills_dir / target.name
        copies = copy_only or target.provider == "core"
        if not copies and dest.is_symlink() and os.readlink(dest) == str(target.path):
            counts.skipped += 1
            skipped += kind == "skill"
            current_links.append((target, dest, kind))
            report.write_target_event(ctx.platform, target.name, kind, "current")
            continue
        status = "updated" if dest.exists() or dest.is_symlink() else "new"
        if status == "updated":
            counts.updated += 1
        else:
            counts.installed += 1
        if copies:
            batch.add(target.path, dest, target.name, kind, status, target.provider)
        else:
            if transaction.link_or_copy(ctx, target, dest, kind) != 0:
                report.error(f"Managed target replacement failed: {dest}")
                raise InstallerExit(1)
            report.write_target_event(ctx.platform, target.name, kind, status)
        installed += kind == "skill"
    transaction.record_current_links(ctx, current_links)
    if batch.pairs:
        label = "  [2/5] Skill sync         " if report.report_enabled() and report.live_counter_enabled() else None
        events_during_batch = report.state().event_file is not None
        if not transaction.copy_targets_batch(ctx, batch, progress_label=label):
            raise InstallerExit(1)
        if not events_during_batch:
            for name, kind, status in zip(batch.labels, batch.kinds, batch.statuses):
                report.write_target_event(ctx.platform, name, kind, status)
        for name in batch.labels:
            report.detail_ok(f"{name} → copied")
    return counts, installed, skipped


def run_install(ctx: Context) -> int:
    """For the skills in `args` (every skill when none), under the install
    lock that `run_command` takes and releases (`docs/reference/installer-architecture.md`)."""
    options = ctx.options
    skills = selection.requested_skills(ctx, ctx.platform)
    skills_dir = Path(ctx.skills_dir)
    visibility = ctx.visibility
    # Source, selection (the source check comes first)
    if not source.install_request() and not report.run_logged(source.check_source_health, skip=options.skip_source_health):
        raise InstallerExit(1)
    collected = selection.collect_targets(ctx, skills)
    skills_dir.mkdir(parents=True, exist_ok=True)
    # An inventory that cannot be read names nothing that is installed: its owner keeps it as a backup, and the
    # installation goes on with the install record and this command's selection.
    inventory = locations.install_state_path(ctx.platform)
    transaction.backup_unreadable_inventory(inventory)
    # Checks before any placement. Deprecated installed skills leave the
    # discovery path first, so that the encoding guard does not scan their content.
    if not report.run_logged(selection.cleanup_deprecated_installed_skills, ctx, skills_dir):
        raise InstallerExit(1)
    if not report.run_logged(preservation.encoding_guard, ctx, collected):
        report.error(f"encoding guard failed; invalid-utf8 or semantic asset details: {report.state().log_file}")
        raise InstallerExit(1)
    if not report.run_logged(preservation.preflight, ctx, collected):
        raise InstallerExit(1)
    report.run_logged(addons.resume_pending_uninstalls, ctx)
    selection.check_addon_collisions(ctx)
    # Report start
    if report.report_enabled() and report.live_counter_enabled():
        report.print_start(ctx.platform, collected.total_target_count, visibility)
    elif not report.compact_output_enabled():
        report.info("[1/5] Preflight: ok")
    # Placement
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
        report.info(f"To update the installed Core selection: {locations.management_command('update')}")
    # Runtime, hooks, managed blocks, commands (blocks after the runtime and hooks)
    if not report.run_logged(transaction.sync_runtime_shared, ctx):
        report.error("Runtime shared core sync failed. Aborting hook install")
        _details_log()
        raise InstallerExit(1)
    gateway = report.run_logged(transaction.report_gateway, ctx)
    notes = [gateway[0]] if gateway else []
    if gateway:
        report.write_gateway_event(ctx.platform, gateway[0], running=gateway[1])
    hook_status = report.run_logged(hosts.install_hooks, ctx, "install", ctx.platform)
    if hook_status.legacy_status == 'error':
        report.write_event(ctx.platform, collected.total_target_count, counts.skipped, counts.updated, counts.installed,
                           verification='not-run', hook_status=hook_status)
        report.error("Hook install failed. Aborting skill install")
        if report.compact_output_enabled():
            for line in hook_status.details.get('errors', []):
                report.error(line)
        _details_log()
        raise InstallerExit(1)
    if not report.compact_output_enabled():
        report.info(f'[4/5] Hooks: {hook_status.legacy_status}')
    bootstrap = hosts.ensure_claude_bootstrap if ctx.platform == "claude" else hosts.ensure_codex_bootstrap
    if not report.run_logged(bootstrap, ctx):
        raise InstallerExit(1)
    if report.run_logged(hosts.sync_commands, ctx, "sync", ctx.platform) != 0:
        report.warn("Commands sync failed (continuing)")
    # Records
    if not report.run_logged(transaction.verify_after_copy, ctx, skills_dir, copy_only, collected):
        raise InstallerExit(1)
    if not report.run_logged(transaction.snapshot_after_install, ctx):
        raise InstallerExit(1)
    report.run_logged(addons.migrate_state_once, ctx)
    if not report.run_logged(addons.write_sidecars, ctx, skills_dir):
        raise InstallerExit(1)
    if not report.run_logged(transaction.write_install_state_manifest, ctx, skills_dir, copy_only, collected):
        raise InstallerExit(1)
    if not report.run_logged(transaction.cleanup_obsolete_copies, ctx, collected):
        report.error('New placement is verified, but previous-original cleanup is incomplete; review the listed paths.')
        raise InstallerExit(1)
    try:
        notes.extend(report.run_logged(install_transaction.cleanup_completed, locations.record_root(),
                                       record_source_cleanup=transaction.cleanup_record_source))
    except install_transaction.InstallTransactionError as exc:
        report.error(f'Code placement finished, but completed-publish cleanup failed: {exc}')
        raise InstallerExit(1)
    # Result
    notes.extend(report.write_event(ctx.platform, collected.total_target_count, counts.skipped, counts.updated,
                                    counts.installed, verification='ok', hook_status=hook_status))
    if report.report_enabled():
        if report.live_counter_enabled():
            if not copy_only:
                report.emit("\r" + report.skill_sync_line(counts.skipped, counts.updated, counts.installed))
            report.print_tail(ctx.platform, visibility, notes, hook_statuses={ctx.platform: hook_status.legacy_status})
        else:
            report.print_full(ctx.platform, collected.total_target_count, counts.skipped, counts.updated,
                              counts.installed, visibility, notes=notes,
                              hook_statuses={ctx.platform: hook_status.legacy_status})
    else:
        report.ok("[5/5] Verification: install state recorded")
        report.emit("")
        report.info("[Ghost-ALICE] For the human user: the installer backs up external changes it finds in installed targets during updates; "
                    "its records do not hold those changes or identify their writer. To review the backups next time you open Claude/Codex, ask the line below.")
        report.emit("")
        report.emit("    Please review backed-up changes.")
        report.emit("")
        report.info("[Ghost-ALICE] Tech: undecided entries live in ~/.ghost-alice/pending-merges/<platform>/manifest.json. "
                    "Missing, empty, or unparsable manifests pass silently.")
    return 0


def run_post_merge(ctx: Context) -> int:
    """The step that the registered `post-merge` hook runs after a merge in any worktree: when the merged checkout
    (`--merged-checkout`, else this checkout) is the checkout the install record names as the install source, the
    auto-mode install for every detected host; for any other checkout one line and nothing installed.

    `source.match_install_source`, the judgment of the install source check, decides. The step takes no lock: the
    installation that it starts takes it and refuses while another one holds it, and the exit code tells the hook.
    """
    merged = Path(ctx.options.merged_checkout) if ctx.options.merged_checkout else None
    try:
        record = install_transaction.record_after_recovery(locations.record_root())
    except (installed_payload.RecordError, install_transaction.InstallTransactionError) as error:
        report.warn(f"Post-merge: the install record cannot be read ({error}); nothing was installed.")
        return 0
    match = source.match_install_source(merged, record=record)
    if match.state == "same":
        return install_release(match.here, ['--post-merge'], remote=record['source'].get('remote'),
                               origin=dict(source.input_record(match.here), registered=source.registration(record)))
    if match.state == "other":
        report.info(f"Post-merge: the merged checkout {match.here} is not the install source ({match.recorded}); "
                    "nothing was installed.")
    else:
        report.info("Post-merge: no install record names an install source; nothing was installed.")
    return 0


def _checks_source(command: Command) -> bool:
    # The package has already checked the installation's starting registration under the lock its children inherit.
    return command.checks_source and not (command is INSTALL and 'start_registration' in (source.install_request() or {}))


def run_command(command: Command, ctx: Context) -> int:
    """`run` under the command's install lock when it names one; the exit code of `run`.
    A command that `checks_source` compares the install source again once it holds the lock."""
    if command.lock is None:
        return command.run(ctx)
    lock_handle = (lock.acquire(command.lock, ctx.platform, inherit=True) if source.install_request()
                   else lock.acquire(command.lock, ctx.platform))
    try:
        if _checks_source(command):
            source.recheck_install_source(ctx, can_adopt=command.adopts_source)
        return command.run(ctx)
    finally:
        lock.release(lock_handle)


def _dispatch(options: Options, *, install_context: Context | None = None) -> int:
    """`--addon` shortcuts, then the table's command (`find_command`): an unknown
    option ends with exit 1, the full uninstall and auto mode run once per
    detected platform, every other command runs once through `run_command`.
    A command that `checks_source` compares the install
    source first, before the addon sources are prepared; the auto mode compares
    it once, in the parent. A command that the active installer runs (`active_runs`)
    goes to it instead when another installer was started and the active package
    states that command (`source.active_installer`)."""
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
        requested = (source.install_request() or {}).get('selections')
        detected = list(requested) if requested is not None else locations.detect_present_platforms()
        if not detected:
            report.warn("No AI platform home directories detected. At least one of ~/.claude, ~/.codex is required.")
            return 0
        ctx = install_context or build_context(options)
        if _checks_source(command):
            source.check_install_source(ctx, can_adopt=command.adopts_source)
        if not options.addon_skip and ctx.addon_sources:
            addons.prepare_sources(ctx)
        return auto.run_install_children(ctx, detected)
    if command.platform and options.prompt_platform:
        if options.platform_explicit:
            report.warn("--prompt-platform ignored because --platform is already specified.")
        else:
            options.platform = prompt_platform()
    ctx = build_context(options)
    if _checks_source(command):
        source.check_install_source(ctx, can_adopt=command.adopts_source)
    if command.active_runs:
        # The active installer chooses and copies what this command restores; this installer has checked the install
        # source and now only starts that installer and returns its exit code.
        active = source.active_installer(command.names[0])
        if active is not None:
            return _start_active(auto.child_command(ctx, ctx.platform, package=active))
    if command.platform and not options.addon_skip and ctx.addon_sources:
        addons.prepare_sources(ctx)
    return run_command(command, ctx)


def confirm_host_shutdown(platforms, *, interactive: bool) -> bool:
    """Ask the terminal user once per displayed set; observe exit before installation changes."""
    approved = set()
    def running_text(host, seen, pids):
        names = dict(seen.programs)
        return '\n'.join(report.message('shutdown_running', host_name=names.get(pid, host), pids=pid)
                         for pid in pids)

    def requestable(observed):
        return {(host, pid, seen.macos_apps.get(pid)) for host, seen in observed.items()
                if seen.state == 'running' for pid in seen.pids if pid not in seen.manual_pids}

    def unsafe_ancestor(observed):
        ancestors = sorted({pid for seen in observed.values() for pid in seen.ancestor_pids})
        if ancestors:
            report.error(report.message('shutdown_ancestor', pids=', '.join(map(str, ancestors))))
        return bool(ancestors)

    while True:
        observed = hosts.observe_writers(platforms)
        blocked = {host: seen for host, seen in observed.items() if seen.state != 'absent'}
        if not blocked:
            return True
        proposed = requestable(blocked) - approved
        retry = not proposed or any(seen.state != 'running' for seen in blocked.values())
        show_status = (not interactive or retry or
                       any(seen.ancestor_pids for seen in blocked.values()) or
                       len(proposed) != sum(len(seen.pids) for seen in blocked.values()))
        if show_status:
            for host, seen in blocked.items():
                report.emit(running_text(host, seen, seen.pids) if seen.state == 'running'
                            else report.message('shutdown_unknown', host_name=host, cause=seen.detail))
                if seen.manual_pids:
                    report.emit(report.message('shutdown_manual', pids=', '.join(map(str, seen.manual_pids)), cause=seen.detail))
        if unsafe_ancestor(blocked):
            return False
        if not interactive:
            report.error(report.message('shutdown_terminal_required'))
            return False
        proposed_rows = '\n'.join(running_text(host, blocked[host], (pid,)) for host, pid, _ in sorted(proposed))
        question = report.message('shutdown_retry') if retry else '\n'.join([
            report.message('shutdown_targets'), proposed_rows, report.message('shutdown_question')])
        preparation_failure_text = None if show_status else proposed_rows
        while True:
            try:
                answer = terminal.read_answer(question, preparation_failure_text=preparation_failure_text)
            except (OSError, ValueError) as exc:
                report.error(report.message('terminal_input_unavailable', cause=exc))
                return False
            answer = answer.strip().lower() if answer is not None else None
            if answer is None or answer in ('n', 'no') or (not retry and answer == ''):
                report.emit(report.message('shutdown_cancelled'))
                return False
            if (retry and answer == '') or (not retry and answer in ('y', 'yes')):
                break
        if retry:
            continue
        approved.update(proposed)
        current = hosts.observe_writers(platforms)
        if unsafe_ancestor(current):
            return False
        targets = proposed & requestable(current)
        pids = sorted({pid for _, pid, _ in targets})
        apps = {pid: bundle for _, pid, bundle in targets if bundle}
        for pid, cause in hosts.request_writer_termination(pids, macos_apps=apps).items():
            report.warn(report.message('shutdown_request_failed', pid=pid, cause=cause))
        deadline = time.monotonic() + 5
        while any(seen.state != 'absent' for seen in hosts.observe_writers(platforms).values()):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)


def _start_active(command: list[str]) -> int:
    """Run `command` of the active installer with the terminal, outside any install request; its exit code."""
    return runner.run_command(command, inherit_stdin=True,
                              env={source.REQUEST_ENV: '', 'GHOST_ALICE_INSTALL_HOST_CHILD': '0'}).returncode


def _report_unfinished(execution_cause: str, package: Path) -> None:
    """Name the command that continues the installation of `package` that did not finish, and the active installer."""
    active = source.active_root()
    if execution_cause == 'post-merge':
        # The plain command of another active package installs that package's release again.
        command = locations.management_command(*(() if active == str(package) else ('update',)))
        report.error(report.message('merged_install_unfinished', command=command))
    else:
        report.error(report.message('install_unfinished'))
    if active:
        report.error(report.message('active_installer', active=active))


def dispatch(options: Options, *, provenance: dict | None = None, reinstall_from: Path | None = None,
             execution_cause: str = 'install', registered: object = ..., update_core: tuple[dict, dict] | None = None) -> int:
    """Prepare this package as a fixed candidate; the candidate activates and places itself."""
    request = source.install_request()
    if request and request.get('stage') == 'record-move':
        from installer import record_move
        return record_move.run(request['execution_cause'])
    if find_command(options) is not INSTALL:
        return _dispatch(options)
    if options.prompt_platform and not options.platform_explicit:
        options.platform = prompt_platform()
        options.platform_explicit = True
        options.prompt_platform = False
    platform = options.platform if options.platform_explicit else None
    selected = (request or {}).get('selections')
    package_source = reinstall_from or runner.REPO_ROOT
    uses_recorded_selection = request is None and (update_core is not None or (
        execution_cause == 'install' and not options.args and source.saved_package(package_source)))
    if uses_recorded_selection:
        record, installed = update_core if update_core is not None else source.recorded_core()
        selected = source.reinstall_selection(None if update_core is not None else package_source,
                                              core=(record, installed), platform=platform)
    platforms = list(selected) if selected is not None else (
        locations.detect_present_platforms() if auto_detect_requested(options) else [options.platform])
    if not platforms:
        report.warn('No AI platform home directories detected. At least one of ~/.claude, ~/.codex is required.')
        return 0
    if not uses_recorded_selection:
        record, installed = source.recorded_core()
    ctx = build_context(options)
    chosen = selected if selected is not None else {host: options.args or list(selection.ALL_SKILLS) for host in platforms}
    if any(host not in locations.PLATFORMS for host in platforms):
        raise ValueError('Update requires adapters for installed hosts: ' + ', '.join(host for host in platforms if host not in locations.PLATFORMS))
    if request is None:
        if registered is ...:
            registered = source.registration(record)
        else:
            _require_registration(record, registered, execution_cause)
        if source.saved_package(runner.REPO_ROOT):
            source.check_saved_package(runner.REPO_ROOT.parent)
        selection.validate_requested_skills(
            sorted({name for names in selected.values() for name in names}) if selected is not None else options.args)
        input_source = provenance if provenance is not None else source.input_record(record=record)
        if update_core is None and execution_cause != 'post-merge':
            match = source.match_install_source(Path(input_source['root']), record=record)
            source.check_install_source(ctx, match=match)
        confirmed_project = hosts.confirm_project_trust(platforms, project_root=Path(provenance['root']) if provenance else None)
        if not source.check_source_health(skip=options.skip_source_health):
            return 1
    ghost_root = locations.record_root()
    shutdown_hosts = set(platforms) | set(installed)
    pending = install_transaction.unfinished(ghost_root)
    for txid in pending:
        host = install_transaction.Journal.load(ghost_root, txid).data.get('host')
        if host and host != 'installer':
            shutdown_hosts.add(host)
    if pending:
        try:
            current_path = installed_payload.read_path(ghost_root)
            current = installed_payload.read_file(current_path) or {}
            shutdown_hosts.update(selection.installed_core_selection(current, current_path))
        except installed_payload.RecordError:
            # The validated saved record and journal hosts cover the record that recovery will replace.
            pass
    log_file = report.log_init() if request is None and report.compact_output_enabled() else None
    if not confirm_host_shutdown(sorted(shutdown_hosts),
                                 interactive=request is None and execution_cause != 'post-merge' and source.interactive()):
        if log_file is not None:
            _details_log()
        return 1
    if request is None:
        prerequisites.install_prerequisites()
        if not options.addon_skip and ctx.addon_sources:
            addons.prepare_sources(ctx)
        handle = lock.acquire('prepare', ctx.platform)
        try:
            folder, addon_commits = source.prepare_source(
                ctx, provenance=input_source, selections=chosen,
                active_runs=tuple(name for row in COMMANDS if row.active_runs for name in row.names))
        finally:
            lock.release(handle)
        package = folder / 'core'
        ctx.addon_sources = list(addon_commits)
        ctx.addon_sources_prepared = True
        common = dict(package_root=str(package), source=input_source)
        if log_file is not None:
            common['log_file'] = str(log_file)
        request = dict(common, addon_commits=addon_commits, start_registration=registered)
        if uses_recorded_selection:
            request['start_core'] = source.for_platform(installed, platform)
            if update_core is None:
                request['reinstall_from'] = str(package_source)
        if execution_cause != 'install':
            request['execution_cause'] = execution_cause
        if selected is not None:
            request['selections'] = selected
            if auto_detect_requested(options) and ctx.addon_sources and folder.samefile(runner.REPO_ROOT.parent):
                # A package that installs itself again installs for each host the addons that the host holds. The
                # installer reads the host registrations under the install lock before activation.
                request['held_addons'] = True
        command = auto.child_command(ctx, options.platform if options.platform_explicit else None, package=package)
        result = runner.run_command(command, env={source.REQUEST_ENV: json.dumps(request), 'GHOST_ALICE_INSTALL_HOST_CHILD': '0',
                                                   'GHOST_ALICE_CONFIRMED_PROJECT_TRUST': confirmed_project})
        if result.ok:
            followup = dict(common, stage='record-move', execution_cause=execution_cause)
            result = runner.run_command([sys.executable, str(package / '_shared/installer/cli.py')], inherit_stdin=True,
                                        env={source.REQUEST_ENV: json.dumps(followup), 'GHOST_ALICE_INSTALL_HOST_CHILD': '0'})
        else:
            _report_unfinished(execution_cause, package)
        report.finish_install(folder.name, result.returncode)
        return result.returncode
    if os.environ.get('GHOST_ALICE_INSTALL_HOST_CHILD') == '1':
        return _dispatch(options)
    handle = lock.acquire('install', ctx.platform)
    try:
        if 'start_registration' in request:
            record, installed = source.recorded_core()
            _require_registration(record, request['start_registration'], request.get('execution_cause', 'install'))
            if request.get('execution_cause') == 'post-merge':
                origin = request['source']
                if source.match_install_source(Path(origin['root']), record=record).state != 'same':
                    raise ValueError(report.message('merged_origin_changed'))
                # A legacy record without a remote used the checkout's origin for this download.
                remote = record['source'].get('remote')
                if remote and remote != origin['remote']:
                    raise ValueError(report.message('install_origin_changed'))
            if 'start_core' in request:
                if source.for_platform(installed, platform) != request['start_core']:
                    raise ValueError(report.message('core_selection_changed'))
                if 'reinstall_from' in request and source.reinstall_selection(
                        Path(request['reinstall_from']), core=(record, installed), platform=platform) != selected:
                    raise ValueError(report.message('core_selection_changed'))
        else:
            match = source.match_install_source()
            if request.get('execution_cause') == 'post-merge' and match.state != 'same':
                report.info(report.message('merged_origin_changed'))
                return 1
            if match.state == 'other' and not options.adopt_source:
                source.check_install_source(ctx, match=match)
            elif match.state == 'unrecorded':
                options.adopt_source = True
        if request.get('held_addons'):
            ctx.held_addons = addons.held_ids(ctx, platforms)
        transaction.activate(ctx, chosen)
        result = _dispatch(options, install_context=ctx)
        if result == 0:
            for line in source.finalize_source(ctx, runner.REPO_ROOT.parent, platforms):
                report.info(line)
        return result
    finally:
        lock.release(handle)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the thin shells: returns the exit code; an early stop
    (`_fail`, the isolated rejection) becomes its code here."""
    if argv is None:
        argv = sys.argv[1:]
        if not os.environ.get('GHOST_ALICE_INSTALL_COMMAND'):
            os.environ['GHOST_ALICE_INSTALL_COMMAND'] = (locations.management_command() if argv[:1] == ['--management-entry']
                                                         else locations.package_command(runner.REPO_ROOT))
    argv = list(argv)
    try:
        if os.environ.get(source.EARLIER_REQUEST_ENV):
            report.configure()
            report.error(report.message('earlier_request', command=locations.package_command(runner.REPO_ROOT)))
            return 1
        if argv[:1] == ['--management-entry']:
            active = source.active_root()
            if active:
                entry = Path(active) / '_shared/installer/cli.py'
                if not entry.is_file():
                    report.error(report.message('active_entry_missing', entry=entry))
                    return 1
                return _start_active([sys.executable, str(entry), *argv[1:]])
            if '--post-merge' not in argv:
                report.error('No installer is active. Run the installation command again.')
                return 1
            argv = argv[1:]
        acquired = update_coordinates = None
        if argv[:1] == [ACQUIRED_RELEASE]:
            acquired, argv = argv[1:3], argv[3:]
        elif argv[:1] == ['--install-acquired-update']:
            update_coordinates, argv = argv[1:4], argv[4:]
        reject_isolated_destination(argv)
        # The downloaded version owns adapter names and install options for the public update command.
        options = (Options(args=argv, argv=tuple(argv))
                   if argv[:1] == ['update'] and acquired is None and update_coordinates is None else parse_options(argv))
        request = source.install_request() or {}
        report.configure(verbose=options.verbose,
                         report_child=os.environ.get("GHOST_ALICE_INSTALL_REPORT_CHILD") == "1",
                         log_file=Path(request['log_file']) if request.get('log_file') else None)
        if request.get('log_file'):
            report.log_init()
        if acquired is not None:
            return install_acquired(options, *(acquired + ['', ''])[:2])
        if update_coordinates is not None:
            if options.args[:1] != ['update'] or request:
                raise ValueError('The acquired update entry requires an update command')
            return update_acquired(build_context(options), coordinates=update_coordinates)
        if not request and find_command(options) is INSTALL and not options.local_source:
            return install_release(runner.REPO_ROOT, argv)
        return dispatch(options)
    except InstallerExit as stop:
        return stop.code
    except (OSError, ValueError, install_transaction.InstallTransactionError) as error:
        report.error(str(error))
        return 1
