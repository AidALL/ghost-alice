"""Auto mode: one command, every detected platform, each in a child installer process.

Port of `install.sh:271-299` (plain full uninstall fan-out), `:306-446`
(auto install with the live counter) and `installer_lib/targets.sh:150-228`
(`count_auto_common_targets`).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING

from installer import addons, report, runner, selection

if TYPE_CHECKING:
    from installer.cli import Context

PROGRESS_SUFFIX = "common targets synced on all platforms"


def platform_target_keys(ctx: "Context", platform: str, skills: list[str]) -> list[str]:
    """`support:_shared`, `skill:<name>` for core targets and addon targets of one platform (`targets.sh:150-179`)."""
    keys: list[str] = []
    if (runner.REPO_ROOT / "_shared").is_dir():
        keys.append("support:_shared")
    for skill in skills:
        for target in selection.expand_skill_targets(skill):
            keys.append(f"skill:{target.name}")
    if not ctx.options.addon_skip and ctx.addon_sources:
        platform_ctx = dataclasses.replace(ctx, platform=platform)
        for target in addons.collect_addon_targets(platform_ctx):
            keys.append(f"skill:{target.name}")
    return keys


def count_common_targets(ctx: "Context", platforms: list[str], skills: list[str]) -> int:
    """Size of the intersection of every platform's key set (`targets.sh:181-228`)."""
    if not platforms:
        return 0
    common: set[str] | None = None
    for platform in platforms:
        keys = set(platform_target_keys(ctx, platform, skills))
        common = keys if common is None else common & keys
    return len(common or ())


def child_command(ctx: "Context", platform: str, *, uninstall: bool = False) -> list[str]:
    """`python cli.py --platform P [--skip-source-health] [--visibility V] [--verbose]
    [--addon-source S]... [--addon-tag T]... [--addon-skip] ARGS...`; tags are
    dropped after the parent prepared git sources (`install.sh:315-333, 385`)."""
    argv = [sys.executable, str(runner.REPO_ROOT / "_shared" / "installer" / "cli.py"), "--platform", platform]
    if uninstall:
        return argv + ["--uninstall"]
    options = ctx.options
    if options.skip_source_health:
        argv.append("--skip-source-health")
    if options.visibility:
        argv.extend(["--visibility", options.visibility])
    if options.verbose:
        argv.append("--verbose")
    for source in ctx.addon_sources:
        argv.extend(["--addon-source", str(source)])
    if not ctx.addon_sources_prepared:
        for tag in options.addon_tags:
            argv.extend(["--addon-tag", tag])
    if options.addon_skip:
        argv.append("--addon-skip")
    argv.extend(options.args)
    return argv


def _run_verbose(ctx: "Context", detected: list[str]) -> int:
    rc = 0
    total = len(detected)
    report.info(f"Detected platforms: {' '.join(detected)}")
    report.info(f"Auto install will process {total} detected platform target(s) in sequence; each has a separate "
                "install path (not a duplicate install).")
    for index, platform in enumerate(detected, 1):
        report.emit("")
        report.info(f"[auto] ({index}/{total}) Starting install for {platform}")
        child_rc = runner.run_command(child_command(ctx, platform)).returncode
        if child_rc != 0:
            rc = 1
            report.warn(f"[auto] Install failed for {platform} (exit code {child_rc})")
    report.emit("")
    if rc == 0:
        report.ok(f"[auto] Install complete for all {total} detected install target(s)")
    else:
        report.error("[auto] Some platform installs failed. Check the log above.")
    return rc


def _run_compact(ctx: "Context", detected: list[str]) -> int:
    log = report.log_init()
    event_file = Path(str(log)[:-len(".log")] + ".events.jsonl") if str(log).endswith(".log") else Path(f"{log}.events.jsonl")
    event_file.write_text("", encoding="utf-8")
    skills = list(ctx.options.args) or list(selection.ALL_SKILLS)
    total = len(detected)
    common = count_common_targets(ctx, detected, skills)
    label = ", ".join(detected)
    visibility = ctx.visibility
    live = report.live_counter_enabled()
    displayed = synced = 0
    rc = 0
    if live:
        report.print_auto_start(label, common, visibility)
    env = {"GHOST_ALICE_INSTALL_REPORT_CHILD": "1", "GHOST_ALICE_INSTALL_LOG_FILE": str(log),
           "GHOST_ALICE_INSTALL_EVENT_FILE": str(event_file)}
    for index, platform in enumerate(detected, 1):
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(f"\n[auto] ({index}/{total}) Starting install for {platform} (separate install path; "
                         "not a duplicate install)\n")
            process = runner.popen(child_command(ctx, platform), env=env, stdout=handle)
            while process.poll() is None:
                if live:
                    synced = report.read_weighted_common_target_progress(event_file, total, common)
                    report.auto_animate_common_target_progress_line(displayed, synced, common, PROGRESS_SUFFIX)
                    displayed = synced
                time.sleep(0.1)
            child_rc = process.wait()
        if child_rc != 0:
            rc = 1
            if live:
                report.emit("")
            report.warn(f"[auto] Install failed for {platform} (exit code {child_rc})")
        if live:
            synced = report.read_weighted_common_target_progress(event_file, total, common)
            report.auto_animate_common_target_progress_line(displayed, synced, common, PROGRESS_SUFFIX)
            displayed = synced
    synced = report.read_all_common_target_progress(event_file, total)
    if rc == 0:
        if live:
            report.auto_animate_common_target_progress_line(displayed, synced, common, PROGRESS_SUFFIX)
            displayed = min(synced, common)
            report.auto_update_common_target_progress_line(displayed, common, PROGRESS_SUFFIX)
            report.emit("")
            report.print_tail(label, visibility)
        else:
            report.print_auto_full(label, common, visibility, synced)
    else:
        if live:
            report.emit("")
        report.error("[auto] Some platform installs failed. Check the log above.")
        report.error(f"Details log: {log}")
        for line in report.failure_excerpt(log):
            report.error(f"[auto] {line}")
    return rc


def run_install_children(ctx: "Context", detected: list[str]) -> int:
    """Verbose: sequential children with their output; compact: children writing
    to the install log with the common-target progress line (`install.sh:306-446`)."""
    if not report.compact_output_enabled():
        return _run_verbose(ctx, detected)
    return _run_compact(ctx, detected)


def run_uninstall_children(ctx: "Context", detected: list[str]) -> int:
    """`[uninstall] (i/n) Starting <platform> full cleanup` and one child each (`install.sh:278-299`)."""
    rc = 0
    total = len(detected)
    report.info(f"Detected uninstall targets: {' '.join(detected)}")
    for index, platform in enumerate(detected, 1):
        report.emit("")
        report.info(f"[uninstall] ({index}/{total}) Starting {platform} full cleanup")
        if runner.run_command(child_command(ctx, platform, uninstall=True)).returncode != 0:
            rc = 1
    return rc
