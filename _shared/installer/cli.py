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
    from installer.cli import main as _main  # noqa: E402

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


@dataclass
class Options:
    """What `parse_options` reads from argv."""

    platform: str = "claude"
    platform_explicit: bool = False
    prompt_platform: bool = False
    skip_source_health: bool = False
    adopt_source: bool = False
    merged_checkout: str | None = None
    update_source: bool = False
    cleanup_pending: bool = False
    addon_sources: list[str] = field(default_factory=list)
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
    visibility: str = "dynamic"
    hook_change: object | None = None
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
    name held around `run` (None: no lock); `before_lock` runs first, outside
    the lock. `platform` False: the command needs no platform, so
    `--prompt-platform` does not ask and the addon sources are not prepared.
    `checks_source`: `dispatch` compares the install record's source with this
    checkout (`source.check_install_source`) before anything else changes, and
    `run_command` compares again under the lock. `adopts_source`: the command
    may replace the install source; without it only the recorded checkout runs it.
    """

    names: tuple[str, ...]
    run: Callable[["Context"], int]
    flag: str | None = None
    lock: str | None = None
    platform: bool = True
    before_lock: Callable[["Context"], None] | None = None
    checks_source: bool = False
    adopts_source: bool = False


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
                  run=lambda ctx: run_install(ctx), lock="install", before_lock=lambda ctx: prepare_install(ctx),
                  checks_source=True, adopts_source=True)
COMMAND_LINE: tuple[HelpRow, ...] = (
    HelpRow(COMMON, "--platform, -p PLAT", "Choose claude | codex"),
    HelpRow(COMMON, "--prompt-platform", "Ask which AI tool to install"),
    Command(COMMON, "--status, -s", "Show install status", names=("--status", "-s"),
            run=lambda ctx: diagnostics.check_status(ctx)),
    HelpRow(COMMON, "--visibility strict|dynamic|minimal", "Set user-facing governance message visibility profile"),
    HelpRow(COMMON, "--agent-visibility strict|dynamic|minimal", "Alias for --visibility"),
    Command(COMMON, "--doctor", "Run install diagnostics", names=("--doctor",),
            run=lambda ctx: diagnostics.run_doctor(ctx)),
    Command(COMMON, "update [--remote URL --ref NAME]", "Update installed Core skills from the recorded remote/ref",
            names=('update',), run=lambda ctx: run_update(ctx), platform=False),
    Command(COMMON, "--repair", "Re-provision missing managed targets (never overwrites an occupied slot that is not a clean managed target)",
            names=("--repair",), run=lambda ctx: lifecycle.run_repair(ctx), lock="repair", checks_source=True),
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
    HelpRow(ADVANCED, "--adopt-source", "Install from this checkout and record it as the install source"),
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


def run_update(ctx: Context, *, acquired_from: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='ghost-alice update', add_help=acquired_from is not None)
    parser.add_argument('--remote')
    parser.add_argument('--ref')
    arguments = ctx.options.args[1:]
    args = parser.parse_known_args(arguments)[0] if acquired_from is None else parser.parse_args(arguments)
    record_path = install_transaction.record_path_after_recovery(locations.record_root())
    record = installed_payload.read_file(record_path) or {}
    origin = dict(record.get('source', {}))
    origin.update({key: value for key, value in vars(args).items() if value is not None})
    coordinates = [origin.get(key) for key in ('root', 'remote', 'ref')]
    if acquired_from is None:
        if not origin.get('root'):
            raise ValueError('Update found no recorded installation source')
        with source.acquire_update(origin) as root:
            return runner.run_command([sys.executable, str(root / '_shared/installer/cli.py'),
                                       '--install-acquired-update', *coordinates, *ctx.options.argv], inherit_stdin=True).returncode
    if acquired_from != coordinates:
        raise ValueError(report.message('update_origin_changed'))
    selected = selection.installed_core_selection(record, record_path)
    if ctx.options.platform_explicit:
        selected = {host: names for host, names in selected.items() if host == ctx.platform}
    if not selected:
        raise ValueError('Update found no installed Core skills to update')
    provenance = source.input_record(runner.REPO_ROOT)
    provenance.update({key: origin.get(key) for key in ('root', 'remote', 'ref', 'branch', 'hooks_path')})
    return dispatch(replace(ctx.options, args=[], addon_skip=True), input_root=runner.REPO_ROOT,
                    provenance=provenance, selections=selected, execution_cause='update')


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
                   skills_dir=locations.skills_dir(options.platform),
                   addon_sources=[] if options.addon_skip else list(options.addon_sources),
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


def prepare_install(ctx: Context) -> None:
    """Prerequisites and the git hook path, before the install lock."""
    prerequisites.install_prerequisites()
    ctx.hook_change = source.setup_git_hooks()
    if ctx.hook_change.recorded:
        ctx.record_source = dict(source.input_record(), hooks_path=ctx.hook_change.after)


def run_install(ctx: Context) -> int:
    """For the skills in `args` (every skill when none), under the install
    lock that `run_command` takes and releases (`docs/reference/installer-architecture.md`)."""
    options = ctx.options
    skills = selection.requested_skills(ctx, ctx.platform)
    skills_dir = Path(ctx.skills_dir)
    visibility = ctx.visibility
    # The first install over an earlier installer's records creates the install record before anything is placed.
    report.run_logged(transaction.migrate_record, ctx)
    # Source, selection (the source check comes first)
    if not source.install_request() and not report.run_logged(source.check_source_health, skip=options.skip_source_health):
        raise InstallerExit(1)
    collected = selection.collect_targets(ctx, skills)
    skills_dir.mkdir(parents=True, exist_ok=True)
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
        report.info(f"To update the installed Core selection: {locations.management_command()} update")
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
    if not report.run_logged(transaction.write_install_state_manifest, ctx, skills_dir, copy_only, collected, ctx.hook_change):
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
    """The step the install source's `hooks/post-merge` runs after a merge in any worktree: when the merged checkout
    (`--merged-checkout`, else this checkout) is the checkout the install record names as the install source, the
    auto-mode install for every detected host; for any other checkout one line and nothing installed.

    `source.match_install_source`, the judgment of the install source check, decides. The step takes no lock:
    each host's install takes it and refuses while another install holds it, and the exit code tells the hook.
    """
    merged = Path(ctx.options.merged_checkout) if ctx.options.merged_checkout else None
    match = source.match_install_source(merged)
    if match.state == "same":
        if match.here != runner.REPO_ROOT.resolve():
            return runner.run_command([sys.executable, str(match.here / '_shared/installer/cli.py'),
                                       '--post-merge', '--merged-checkout', str(match.here)]).returncode
        return dispatch(Options(), input_root=match.here, provenance=source.input_record(match.here), execution_cause='post-merge')
    if match.state == "other":
        report.info(f"Post-merge: the merged checkout {match.here} is not the install source ({match.recorded}); "
                    "nothing was installed.")
    elif match.state == "unrecorded":
        report.info("Post-merge: no install record names an install source; nothing was installed.")
    else:
        report.warn(f"Post-merge: the install record cannot be read ({match.detail}); nothing was installed.")
    return 0


def run_command(command: Command, ctx: Context) -> int:
    """`before_lock`, then `run` under the command's install lock when it names one; the exit code of `run`.
    A command that `checks_source` compares the install source again once it holds the lock."""
    if command.before_lock is not None:
        command.before_lock(ctx)
    if command.lock is None:
        return command.run(ctx)
    lock_handle = (lock.acquire(command.lock, ctx.platform, inherit=True) if source.install_request()
                   else lock.acquire(command.lock, ctx.platform))
    try:
        if command.checks_source:
            source.recheck_install_source(ctx, can_adopt=command.adopts_source)
        return command.run(ctx)
    finally:
        lock.release(lock_handle)


def _dispatch(options: Options) -> int:
    """`--addon` shortcuts, then the table's command (`find_command`): an unknown
    option ends with exit 1, the full uninstall and auto mode run once per
    detected platform, every other command runs once through `run_command`.
    A command that `checks_source` compares the install
    source first, before the addon sources are prepared; the auto mode compares
    it once, in the parent."""
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
        ctx = build_context(options)
        if command.checks_source:
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
    if command.checks_source:
        source.check_install_source(ctx, can_adopt=command.adopts_source)
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


def dispatch(options: Options, *, input_root: Path | None = None, provenance: dict | None = None,
             selections: dict[str, list[str]] | None = None, execution_cause: str = 'install') -> int:
    """Run an install from a fixed candidate; activate it only after the whole request succeeds."""
    request = source.install_request()
    if request and request.get('stage') == 'record-move':
        from installer import record_move
        root = locations.record_root()
        if os.path.lexists(root / installed_payload.PENDING_SOURCE_FILENAME) or install_transaction.unfinished(root):
            handle = lock.acquire('activate', 'installer')
            try:
                installed_payload.finish_pending_source(root, selected_package=runner.REPO_ROOT)
            finally:
                lock.release(handle)
        return record_move.run(request['execution_cause'])
    if find_command(options) is not INSTALL:
        return _dispatch(options)
    if options.prompt_platform and not options.platform_explicit:
        options.platform = prompt_platform()
        options.platform_explicit = True
        options.prompt_platform = False
    selected = selections if selections is not None else (request or {}).get('selections')
    if selected is None and request is None and input_root is None and not options.args:
        selected = source.reinstall_selection()
        if selected is not None and options.platform_explicit:
            selected = {host: names for host, names in selected.items() if host == options.platform}
    platforms = list(selected) if selected is not None else (
        locations.detect_present_platforms() if auto_detect_requested(options) else [options.platform])
    if not platforms:
        report.warn('No AI platform home directories detected. At least one of ~/.claude, ~/.codex is required.')
        return 0
    ctx = build_context(options)
    if any(host not in locations.PLATFORMS for host in platforms):
        raise ValueError('Update requires adapters for installed hosts: ' + ', '.join(host for host in platforms if host not in locations.PLATFORMS))
    if request is None:
        source.check_install_source(ctx, match=source.match_install_source(Path(provenance['root'])) if provenance else None)
        confirmed_project = hosts.confirm_project_trust(platforms, project_root=Path(provenance['root']) if provenance else None)
        if not source.check_source_health(skip=options.skip_source_health, **({'input_root': input_root} if input_root else {})):
            return 1
    ghost_root = locations.record_root()
    record_path = install_transaction.record_path_after_recovery(ghost_root)
    installed = selection.installed_core_selection(installed_payload.read_file(record_path) or {}, record_path)
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
        if not options.addon_skip and ctx.addon_sources:
            addons.prepare_sources(ctx)
        input_source = provenance if provenance is not None else source.input_record()
        handle = lock.acquire('prepare', ctx.platform)
        try:
            folder, addon_commits = source.prepare_source(ctx, input_root=input_root, provenance=input_source,
                selections=selected if selected is not None else {host: options.args or list(selection.ALL_SKILLS) for host in platforms})
            source.check_record_reader(folder / 'core', locations.record_root())
        finally:
            lock.release(handle)
        ctx.addon_sources = list(addon_commits)
        ctx.addon_sources_prepared = True
        request = dict(package_root=str(folder / 'core'), source=input_source, addon_commits=addon_commits,
                       request_writer_reads_v3=True)
        if execution_cause == 'post-merge':
            request['execution_cause'] = execution_cause
        if log_file is not None:
            request['log_file'] = str(log_file)
        if selected is not None:
            request['selections'] = selected
        command = auto.child_command(ctx, options.platform if options.platform_explicit else None, package=folder / 'core')
        result = runner.run_command(command, env={source.REQUEST_ENV: json.dumps(request), 'GHOST_ALICE_INSTALL_HOST_CHILD': '0',
                                                   'GHOST_ALICE_CONFIRMED_PROJECT_TRUST': confirmed_project})
        if not result.ok:
            active = (install_transaction.record_after_recovery(locations.record_root()) or {}).get('installer', {}).get('active_root')
            if active:
                report.error(report.message('candidate_failed', active=active))
            else:
                report.error(report.message('candidate_first_retry', package=folder / 'core' /
                                            ('install.cmd' if os.name == 'nt' else 'install.sh')))
            report.finish_install(folder.name, result.returncode)
            return result.returncode
        active_record = install_transaction.record_after_recovery(locations.record_root()) or {}
        active = active_record.get('installer', {}).get('active_root')
        if not active:
            raise ValueError('The successful candidate did not record an active installer')
        saved = json.loads((Path(active).parent / 'version.json').read_text(encoding='utf-8'))
        if saved.get('record_move') is not True:
            report.info(report.message('move_unsupported'))
            report.finish_install(folder.name, 0)
            return 0
        followup = dict(package_root=active, source=active_record.get('source', {}),
                        stage='record-move', execution_cause=execution_cause)
        if log_file is not None:
            followup['log_file'] = str(log_file)
        result = runner.run_command([sys.executable, str(Path(active) / '_shared/installer/cli.py')],
            inherit_stdin=True, env={source.REQUEST_ENV: json.dumps(followup),
                                    'GHOST_ALICE_INSTALL_HOST_CHILD': '0'})
        report.finish_install(folder.name, result.returncode)
        return result.returncode
    if os.environ.get('GHOST_ALICE_INSTALL_HOST_CHILD') == '1':
        return _dispatch(options)
    handle = lock.acquire('install', ctx.platform)
    try:
        if request.get('execution_cause') == 'post-merge' and source.match_install_source(Path(request['source']['root'])).state != 'same':
            report.info(report.message('merged_origin_changed'))
            return 1
        if not source.request_writer_needs_v2():
            installed_payload.finish_pending_source(locations.record_root())
        initial = transaction.installed_ledger_locations().publication_source(source.input_record())
        interim = source.check_previous_manager(initial)
        result = _dispatch(options)
        if result == 0:
            for line in source.finalize_source(ctx, runner.REPO_ROOT.parent, platforms,
                                             before_handoff=initial if interim != initial else None):
                report.info(line)
        return result
    finally:
        lock.release(handle)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the thin shells: returns the exit code; an early stop
    (`_fail`, the isolated rejection) becomes its code here."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] == ['--management-entry']:
            record = install_transaction.record_after_recovery(locations.record_root()) or {}
            active = record.get('installer', {}).get('active_root')
            if not active:
                report.error('No successful installer package is active. Retry the candidate install from its reported path.')
                return 1
            entry = Path(active) / '_shared/installer/cli.py'
            if not entry.is_file():
                report.error(f'The active installer is missing: {entry}')
                return 1
            return runner.run_command([sys.executable, str(entry), *argv[1:]],
                                      inherit_stdin=True,
                                      env={source.REQUEST_ENV: '', 'GHOST_ALICE_INSTALL_HOST_CHILD': '0'}).returncode
        acquired_from = None
        if argv[:1] == ['--install-acquired-update']:
            acquired_from, argv = argv[1:4], argv[4:]
        reject_isolated_destination(argv)
        # The downloaded version owns adapter names and install options for the public update command.
        options = (Options(args=argv, argv=tuple(argv)) if argv[:1] == ['update'] and acquired_from is None
                   else parse_options(argv))
        request = source.install_request() or {}
        report.configure(verbose=options.verbose,
                         report_child=os.environ.get("GHOST_ALICE_INSTALL_REPORT_CHILD") == "1",
                         log_file=Path(request['log_file']) if request.get('log_file') else None)
        if request.get('log_file'):
            report.log_init()
        if acquired_from is not None:
            if options.args[:1] != ['update'] or request:
                raise ValueError('The acquired update entry requires an update command')
            return run_update(build_context(options), acquired_from=acquired_from)
        return dispatch(options)
    except InstallerExit as stop:
        return stop.code
    except (OSError, ValueError, install_transaction.InstallTransactionError) as error:
        report.error(str(error))
        return 1
