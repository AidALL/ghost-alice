"""Installer output: messages, verbosity, the install log, progress lines,
the installation report and the event file. Installer messages are English-only.

Port of `installer_lib/report.sh`. Wording is fixed by tests
(`scripts/tests/test_installer_default_auto.py`, `test_install_progress_line_width.py`,
`test_report_hook_suite_label.py`, `test_install_tail_pending_message.py`).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import datetime
import io
import os
from pathlib import Path
import re
import sys
import time
from typing import IO, Callable, Sequence, TypeVar

import install_report_events
from installer import locations

T = TypeVar("T")

PROGRESS_OFF_VALUES = frozenset({"0", "false", "False", "FALSE", "off", "OFF", "no", "NO"})
HOOK_SUITE_LABEL = "prompt, session-intent, tool-checkpoint, completion, session-start, io-trace"
FAILURE_EXCERPT_PATTERN = (r"\[ERROR\]|\[WARN\] \[auto\].*failed|addon install collision|addon manifest error|"
                           r"Exception:|failed - aborting|clone failed|not found|Python 3\.11\+")
_FAILURE_EXCERPT_RE = re.compile(FAILURE_EXCERPT_PATTERN)
_COLORS = dict(INFO="\033[0;36m", OK="\033[0;32m", WARN="\033[0;33m", ERROR="\033[0;31m")
_RESET = "\033[0m"


@dataclass
class OutputState:
    """What the shell kept in globals: VERBOSE, INSTALL_REPORT_CHILD, log and event files, the sink."""

    verbose: bool = False
    report_child: bool = False
    log_file: Path | None = None
    event_file: Path | None = None
    tty: bool = False
    progress_env: str = "auto"
    home: Path | None = None
    sink: IO[str] | None = field(default=None, repr=False)


_state = OutputState()


def configure(*, verbose: bool = False, report_child: bool = False, log_file: Path | None = None,
              event_file: Path | None = None, tty: bool | None = None, progress_env: str | None = None,
              home: Path | None = None) -> OutputState:
    """Set the output state for this process from options and environment.

    `log_file`/`event_file` are honored only when `report_child` is true
    (the rule install.ps1 already follows). `tty` defaults to `sys.stdout.isatty()`
    and `progress_env` to `GHOST_ALICE_INSTALL_PROGRESS` or `auto`.
    """
    global _state
    if report_child:
        if log_file is None and os.environ.get("GHOST_ALICE_INSTALL_LOG_FILE"):
            log_file = Path(os.environ["GHOST_ALICE_INSTALL_LOG_FILE"])
        if event_file is None and os.environ.get("GHOST_ALICE_INSTALL_EVENT_FILE"):
            event_file = Path(os.environ["GHOST_ALICE_INSTALL_EVENT_FILE"])
    else:
        log_file, event_file = None, None
    if tty is None:
        try:
            tty = sys.stdout.isatty()
        except (AttributeError, ValueError):
            tty = False
    if progress_env is None:
        progress_env = os.environ.get("GHOST_ALICE_INSTALL_PROGRESS", "auto")
    _state = OutputState(verbose=verbose, report_child=report_child, log_file=log_file, event_file=event_file,
                         tty=tty, progress_env=progress_env, home=home)
    return _state


def state() -> OutputState:
    return _state


def sink() -> IO[str] | None:
    """Where messages and child output go right now: the log file in a logged step, else None (stdout)."""
    return _state.sink


DEFAULT_INSTALL_COMMAND = "bash install.sh"


def install_command() -> str:
    """The command the user runs to start the installer, as the help and the hints
    name it: `$GHOST_ALICE_INSTALL_COMMAND` when the shell reports one
    (`install.ps1` reports `.\\install.cmd`), else `bash install.sh`."""
    return os.environ.get("GHOST_ALICE_INSTALL_COMMAND") or DEFAULT_INSTALL_COMMAND


def emit(text: str, *, newline: bool = True) -> None:
    """Write raw text to the sink (stdout when no sink)."""
    stream = _state.sink if _state.sink is not None else sys.stdout
    stream.write(text + ("\n" if newline else ""))
    stream.flush()


def _message(level: str, message: str) -> None:
    prefix = f"[{level}]"
    if colors_enabled():
        prefix = f"{_COLORS[level]}{prefix}{_RESET}"
    emit(f"{prefix} {message}")


def info(message: str) -> None:
    """`[INFO] message`, the prefix in cyan when colors are on."""
    _message("INFO", message)


def ok(message: str) -> None:
    """`[OK] message`, green prefix."""
    _message("OK", message)


def warn(message: str) -> None:
    """`[WARN] message`, yellow prefix."""
    _message("WARN", message)


def error(message: str) -> None:
    """`[ERROR] message`, red prefix."""
    _message("ERROR", message)


def detail_ok(message: str) -> None:
    """`ok(message)` only in verbose mode (`report.sh:23-26`)."""
    if _state.verbose:
        ok(message)


def detail_warn(message: str) -> None:
    """`warn(message)` only in verbose mode (`report.sh:28-31`)."""
    if _state.verbose:
        warn(message)


def colors_enabled() -> bool:
    """ANSI colors only when stdout is a terminal (`install.sh:95-99`)."""
    return _state.tty


def compact_output_enabled() -> bool:
    """Not verbose (`report.sh:70-72`)."""
    return not _state.verbose


def report_enabled() -> bool:
    """Compact and not a report child (`report.sh:74-76`)."""
    return compact_output_enabled() and not _state.report_child


def live_counter_enabled() -> bool:
    """Progress animation: progress env not in PROGRESS_OFF_VALUES, not verbose, stdout a TTY (`report.sh:33-39`)."""
    if _state.progress_env in PROGRESS_OFF_VALUES:
        return False
    if _state.verbose:
        return False
    return _state.tty


def _home() -> Path:
    return _state.home if _state.home is not None else locations.home()


def log_init() -> Path:
    """Create `<home>/.ghost-alice/install/YYYY-MM-DD-HHMMSS.log` once and export
    `GHOST_ALICE_INSTALL_LOG_FILE`; an already chosen log file is kept (`report.sh:78-89`)."""
    if _state.log_file is None:
        root = _home() / ".ghost-alice" / "install"
        root.mkdir(parents=True, exist_ok=True)
        _state.log_file = root / (datetime.now().strftime("%Y-%m-%d-%H%M%S") + ".log")
    os.environ["GHOST_ALICE_INSTALL_LOG_FILE"] = str(_state.log_file)
    return _state.log_file


def run_logged(step: Callable[..., T], *args, **kwargs) -> T:
    """Run `step` with its output appended to the install log in compact mode,
    directly otherwise (`report.sh:103-110`)."""
    if not compact_output_enabled():
        return step(*args, **kwargs)
    log = log_init()
    previous = _state.sink
    with open(log, "a", encoding="utf-8") as handle:
        _state.sink = handle
        try:
            with contextlib.redirect_stdout(handle), contextlib.redirect_stderr(handle):
                return step(*args, **kwargs)
        finally:
            handle.flush()
            _state.sink = previous


def failure_excerpt(path: Path | None, max_lines: int = 8) -> list[str]:
    """The last `max_lines` log lines matching FAILURE_EXCERPT_PATTERN, right-stripped (`report.sh:91-101`)."""
    if path is None or not Path(path).is_file():
        return []
    matched = [line.rstrip() for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
               if _FAILURE_EXCERPT_RE.search(line)]
    return matched[-max_lines:] if max_lines > 0 else []


def join_by(separator: str, items: Sequence[str]) -> str:
    return separator.join(items)


def count_label(count: int, singular: str, plural: str) -> str:
    """`1 skill target` / `3 skill targets` (`report.sh:41-50`)."""
    return f"{count} {singular if count == 1 else plural}"


def print_skill_sync_summary(skill_targets: int, support_targets: int, installed: int, updated: int,
                             skipped: int, mode_label: str = "") -> None:
    """`[2/5] Skill sync: ...` info line (`report.sh:52-68`)."""
    message = (f"[2/5] Skill sync: {count_label(skill_targets, 'skill target', 'skill targets')}, "
               f"{count_label(support_targets, 'support target', 'support targets')}; "
               f"{installed} installed, {updated} updated, {skipped} skipped")
    if mode_label:
        message = f"{message} ({mode_label})"
    info(message)


def hook_suite_label(platform: str = "") -> str:
    """`prompt, session-intent, tool-checkpoint, completion, session-start, io-trace` (`report.sh:126-129`)."""
    return HOOK_SUITE_LABEL


def skill_sync_line(current: int, updated: int, new: int) -> str:
    """`  [2/5] Skill sync          [c] [Current], [u] [updated], [n] [newly added]` (`report.sh:131-135`)."""
    return f"  [2/5] Skill sync          [{current}] [Current], [{updated}] [updated], [{new}] [newly added]"


def common_skill_sync_line(common_targets: int) -> str:
    return f"  [2/5] Skill sync          [{common_targets}] common targets"


def progress_bar(done: int, total: int, width: int = 20) -> str:
    """`#` for the filled part, `-` for the rest; clamped (`report.sh:142-156`)."""
    filled = done * width // total if total > 0 else 0
    filled = min(max(filled, 0), width)
    return "#" * filled + "-" * (width - filled)


def common_target_progress_line(done: int, total: int, suffix: str = "common targets synced") -> str:
    """`        Common targets      [<bar 30>] [done/total] suffix` (`report.sh:158-163`)."""
    return f"        Common targets      [{progress_bar(done, total, 30)}] [{done}/{total}] {suffix}"


def clear_current_line() -> str:
    """`\\r\\x1b[2K` (`report.sh:165-167`)."""
    return "\r\x1b[2K"


def auto_update_common_target_progress_line(done: int, total: int, suffix: str) -> None:
    emit(clear_current_line() + common_target_progress_line(done, total, suffix), newline=False)


def auto_animate_common_target_progress_line(from_count: int, to_count: int, total: int, suffix: str,
                                             delay: float = 0.02) -> None:
    """Draw every count from `from_count + 1` to `to_count`, clamped to `total` (`report.sh:174-185`)."""
    from_count = max(from_count, 0)
    to_count = max(to_count, from_count)
    to_count = min(to_count, total)
    for completed in range(from_count + 1, to_count + 1):
        auto_update_common_target_progress_line(completed, total, suffix)
        if completed < to_count and delay > 0:
            time.sleep(delay)


def _print_header(platform_label: str, skills_line: str, visibility: str) -> None:
    log_init()
    emit("Ghost-ALICE OS installation Process Report\n")
    emit("Target")
    emit(f"  Platform: {platform_label}")
    emit(skills_line)
    emit("  Hooks: enabled")
    emit(f"  Visibility Level: [{visibility}]\n")
    emit("Progress")
    emit("  [1/5] Preflight           ok")


def print_start(platform_label: str, total_targets: int, visibility: str = "dynamic",
                target_unit: str = "targets") -> None:
    """Report head plus `[1/5] Preflight ok` and the zero sync line (`report.sh:187-199`)."""
    _print_header(platform_label, f"  Skills: [{total_targets}] {target_unit}", visibility)
    emit(skill_sync_line(0, 0, 0), newline=False)


def print_tail(platform_label: str, visibility: str = "dynamic") -> None:
    """`[3/5] Hooks`, `[4/5] Runtime config`, `[5/5] Verification`, Attention, Details (`report.sh:201-211`)."""
    log = log_init()
    emit(f"  [3/5] Hooks               {hook_suite_label(platform_label)} enabled")
    emit(f"  [4/5] Runtime config      {platform_label} hooks=true, Visibility Level=[{visibility}]")
    emit("  [5/5] Verification        ok\n")
    emit("Attention")
    emit("  - visibility can be changed later with /visibility between: dynamic | minimal | strict\n")
    emit("Details")
    emit(f"  log: {log}")
    emit("  rerun with --verbose to show per-skill actions")


def print_full(platform_label: str, total_targets: int, current: int, updated: int, new: int,
               visibility: str = "dynamic", target_unit: str = "targets") -> None:
    _print_header(platform_label, f"  Skills: [{total_targets}] {target_unit}", visibility)
    emit(skill_sync_line(current, updated, new))
    print_tail(platform_label, visibility)


def print_auto_start(platform_label: str, common_targets: int, visibility: str = "dynamic") -> None:
    _print_header(platform_label, f"  Skills: [{common_targets}] common targets", visibility)
    emit(common_skill_sync_line(common_targets))
    emit(common_target_progress_line(0, common_targets), newline=False)


def print_auto_full(platform_label: str, common_targets: int, visibility: str = "dynamic",
                    synced_targets: int = 0) -> None:
    _print_header(platform_label, f"  Skills: [{common_targets}] common targets", visibility)
    emit(common_skill_sync_line(common_targets))
    emit(common_target_progress_line(synced_targets, common_targets, "common targets synced on all platforms"))
    print_tail(platform_label, visibility)


def _append_event(line: str) -> None:
    if _state.event_file is None:
        return
    _state.event_file.parent.mkdir(parents=True, exist_ok=True)
    with open(_state.event_file, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_event(platform: str, total_targets: int, current: int, updated: int, new: int) -> None:
    """Append a `platform-result` JSON line to the event file when one is set (`report.sh:263-268`)."""
    _append_event('{"type":"platform-result","platform":"%s","total_targets":%s,"current":%s,"updated":%s,'
                  '"new":%s,"verification":"ok","hooks":"enabled"}' % (platform, total_targets, current, updated, new))


def write_target_event(platform: str, target_id: str, target_kind: str, status: str) -> None:
    """Append a `target-result` JSON line to the event file when one is set (`report.sh:270-275`)."""
    _append_event('{"type":"target-result","platform":"%s","target_id":"%s","target_kind":"%s","status":"%s"}'
                  % (platform, target_id, target_kind, status))


def _read_progress(reader: Callable[..., None], event_file: Path, *args) -> int:
    path = Path(event_file)
    if not path.is_file() or path.stat().st_size == 0:
        return 0
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        reader(str(path), *args)
    try:
        return int(buffer.getvalue().strip() or 0)
    except ValueError:
        return 0


def read_all_common_target_progress(event_file: Path, platform_count: int) -> int:
    """0 for a missing or empty file, else `install_report_events.read_all_common_target_progress`."""
    return _read_progress(install_report_events.read_all_common_target_progress, event_file, platform_count)


def read_weighted_common_target_progress(event_file: Path, platform_count: int, total: int) -> int:
    return _read_progress(install_report_events.read_weighted_common_target_progress, event_file, platform_count, total)
