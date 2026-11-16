"""Installer output: messages, verbosity, the install log, progress lines,
the installation report and the event file. Record-move messages use English or Korean.

Wording and layout are fixed by `_shared/test_installer_report.py` and `scripts/tests/test_installer_default_auto.py`.
"""

from __future__ import annotations


def exception_lines(error: BaseException) -> list[str]:
    """Describe every grouped failure and recovery note without local values."""
    lines: list[str] = []
    def visit(current: BaseException, label: str) -> None:
        lines.append(f'{label}{type(current).__name__}: {current}')
        for note in getattr(current, '__notes__', ()):
            lines.append(f'{label}note: {note}')
        if isinstance(current, BaseExceptionGroup):
            for index, child in enumerate(current.exceptions, 1):
                visit(child, f'{label}{index}. ')
    visit(error, '')
    return lines

import contextlib
from dataclasses import dataclass, field
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
from typing import IO, Callable, Sequence, TypeVar

import hook_definitions
import install_report_events
from installer import locations
import state_retention
from text_lines import committed_byte_lines, committed_records

T = TypeVar("T")

PROGRESS_OFF_VALUES = frozenset({"0", "false", "False", "FALSE", "off", "OFF", "no", "NO"})
HOOK_SUITE_LABEL = ", ".join(hook_definitions.CORE_HOOK_IDS)
FAILURE_EXCERPT_PATTERN = (r"\[ERROR\]|\[WARN\] \[auto\].*failed|addon install collision|addon manifest error|"
                           r"Exception:|failed - aborting|clone failed|not found|Python 3\.11\+")
_FAILURE_EXCERPT_RE = re.compile(FAILURE_EXCERPT_PATTERN)
_COLORS = dict(INFO="\033[0;36m", OK="\033[0;32m", WARN="\033[0;33m", ERROR="\033[0;31m")
_RESET = "\033[0m"


def _display_language() -> str:
    """Use the original locale before shell UTF-8 repair, or the native Windows UI language."""
    for name in ("GHOST_ALICE_INSTALL_LOCALE", "LC_ALL", "LC_MESSAGES", "LANG"):
        if name in os.environ and (name == 'GHOST_ALICE_INSTALL_LOCALE' or os.environ[name]):
            value = os.environ[name]
            break
    else:
        value = ""
        if sys.platform == "win32":
            import ctypes
            import locale
            try:
                value = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
            except (AttributeError, OSError):
                pass
    return "ko" if re.split(r"[_.@-]", value.strip().lower(), maxsplit=1)[0] == "ko" else "en"


# Each pair is English, Korean. Callers supply observed facts and choose the message.
_MESSAGES = {
    "install_complete": ("Ghost-ALICE installation is complete.", "Ghost-ALICE 설치를 완료했습니다."),
    "update_complete": ("Ghost-ALICE update is complete.", "Ghost-ALICE 업데이트를 완료했습니다."),
    "move_unsupported": (
        "The code installation is complete. This installer version does not support moving existing work records, so the move was not started.",
        "코드 설치를 완료했습니다. 이 인스톨러 버전은 기존 작업 기록 옮기기를 지원하지 않아 기록 이동을 시작하지 않았습니다."),
    "host_claude": ("Claude Code", "Claude Code"),
    "host_codex": ("Codex", "Codex"),
    "move_intro": (
        "Ghost-ALICE currently stores your work records inside the folder used for installation.",
        "현재 Ghost-ALICE는 설치에 사용한 폴더 안에 작업 기록을 저장하고 있습니다."),
    "move_locations": (
        "  Folder used for installation: {source_path}\n  Current record location:      {current_path}\n"
        "  New record location:          {new_path}",
        "  설치에 사용한 폴더: {source_path}\n  현재 기록 위치:     {current_path}\n  새 기록 위치:       {new_path}"),
    "move_explanation": (
        "This keeps your work records separate from the folder used for installation.\n"
        "After the move, Ghost-ALICE will also store new work records in the new location.",
        "기존 작업 기록을 설치에 사용한 폴더와 분리된 위치에 보관하려고 합니다.\n"
        "기존 작업 기록을 옮기면 앞으로의 작업 기록도 새 위치에 저장합니다."),
    "move_question": (
        "Move your existing work records to the new location? [Y/n]", "기존 작업 기록을 새 위치로 옮길까요? [Y/n]"),
    "move_choices": (
        "Enter: move records · n: leave them where they are for now", "Enter: 옮기기 · n: 지금은 옮기지 않기"),
    "move_invalid": ("Press Enter, or type y, yes, n, or no.", "Enter를 누르거나 y, yes, n, no 중 하나를 입력하세요."),
    "move_wait_invalid": ("Press Enter, or type n or no.", "Enter를 누르거나 n 또는 no를 입력하세요."),
    "move_complete": (
        "Your existing work records from {current_path} are now at {new_path}. Ghost-ALICE will also store new work records there.",
        "기존 작업 기록을 {current_path}에서 {new_path}로 옮겼습니다. 앞으로의 작업 기록도 이 위치에 저장합니다."),
    "move_cancelled": (
        "Your existing work records remain at {current_path}. Ghost-ALICE will ask again after a later installation "
        "completes when you run {entry_command} or {entry_command} update in a terminal.",
        "기존 작업 기록은 그대로 {current_path}에 있습니다. 나중에 터미널에서 {entry_command} 또는 "
        "{entry_command} update를 실행해 설치를 마치면 다시 묻습니다."),
    "move_deferred": (
        "Your existing work records remain at {current_path}. Run {entry_command} in a terminal; after installation "
        "completes, Ghost-ALICE will ask whether to move them.",
        "기존 작업 기록은 그대로 {current_path}에 있습니다. 터미널에서 {entry_command}를 실행해 설치를 마치면 "
        "새 위치로 옮길지 묻습니다."),
    "move_host_running": (
        "A {host_name} app or CLI, or a record-writing command it started, is still running. "
        "Finish that work and close the app or end the CLI commands, then press Enter. Press n to stop moving the records.",
        "{host_name} 앱·CLI 또는 그 호스트가 시작한 기록 명령이 아직 실행 중입니다. 작업을 마치고 해당 앱을 닫거나 CLI 명령을 종료한 뒤 Enter를 누르세요. "
        "n을 누르면 기록 옮기기를 그만둡니다."),
    "move_host_unknown": (
        "Ghost-ALICE could not check whether {host_name} or its record-writing commands are running. "
        "Check the process-inspection tools and permissions in the diagnostic details. After resolving the lookup problem, press Enter to check again, "
        "or press n to stop moving the records.",
        "{host_name} 및 그 호스트의 기록 명령이 실행 중인지 확인하지 못했습니다. 진단 상세에 나온 프로세스 조회 도구와 권한을 확인하세요. "
        "조회 문제를 해결한 뒤 Enter를 누르면 다시 확인합니다. n을 누르면 기록 옮기기를 그만둡니다."),
    "move_not_moved": (
        "Your existing work records were not moved and remain at {current_path}.",
        "기존 작업 기록을 옮기지 않았습니다. 기록 위치는 그대로 {current_path}입니다."),
    "move_restored": (
        "Ghost-ALICE could not move your existing work records and restored the previous state. "
        "Your records remain at {current_path}.",
        "기존 작업 기록을 옮기지 못해 옮기기 전 상태로 되돌렸습니다. 기록 위치는 그대로 {current_path}입니다."),
    "move_recovery_incomplete": (
        "Moving your existing work records from {current_path} stopped, and Ghost-ALICE could not finish restoring the previous state. "
        "The move is not committed; recovery is restoring the previous state. Source: {current_path}. Destination: {new_path}.\n"
        "Data and configuration targets of this move:\n{affected_paths}\n"
        "Recovery records: {recovery_paths}. Journal phase: {phase}.\n{recovery}",
        "인스톨러가 {current_path}의 기존 작업 기록을 옮기다가 멈췄고, 이전 상태 복원을 마치지 못했습니다. "
        "이동을 확정하지 못했으며 현재 단계는 이전 상태 복원입니다. 원래 위치: {current_path}. 새 위치: {new_path}.\n"
        "이 이동의 데이터·설정 대상:\n{affected_paths}\n"
        "복구 기록 위치: {recovery_paths}. 저널 단계: {phase}.\n{recovery}"),
    "move_committed_pending": (
        "Your existing work records from {current_path} are at {new_path}, and Ghost-ALICE is configured to use that location. "
        "Unfinished step: {step}. The next basic installation or update will retry this step when it passes "
        "its installation preparation checks and proceeds.",
        "{current_path}의 기존 작업 기록은 {new_path}에 있고, Ghost-ALICE의 기록 위치도 이 경로로 설정했습니다. 마치지 못한 단계: {step}. "
        "다음 기본 설치 또는 업데이트가 설치 준비를 마치고 진행할 때 이 단계를 다시 시도합니다."),
    "move_cleanup_failed": (
        "Your existing work records from {current_path} are now at {new_path}. The installer could not finish cleaning up "
        "completed installation artifacts.",
        "기존 작업 기록을 {current_path}에서 {new_path}로 옮겼습니다. 인스톨러가 완료된 배치의 보관 자료 정리를 "
        "마치지 못했습니다."),
    "retained_installation_artifacts": (
        "The installer retained these installation artifacts for the listed reasons:\n{paths}",
        "인스톨러가 다음 배치 보관 자료를 유지했습니다. 각 위치와 이유는 다음과 같습니다:\n{paths}"),
    "move_doctor": (
        "Your existing work records are inside the folder used to install Ghost-ALICE: {current_path}. "
        "Run {entry_command} in a terminal; after installation completes, Ghost-ALICE will ask whether to move them.",
        "기존 작업 기록이 설치에 사용한 폴더 안에 있습니다: {current_path}. 터미널에서 {entry_command}를 실행해 "
        "설치를 마치면 새 위치로 옮길지 묻습니다."),
    "move_uninstall": (
        "Your existing work records were not deleted: {current_path}. This location is inside the folder used "
        "to install Ghost-ALICE ({source_path}).",
        "기존 작업 기록은 지우지 않았습니다: {current_path}. 이 위치는 설치에 사용한 폴더({source_path}) 안에 있습니다."),
    "move_relation_unknown": (
        "The current work record location is {current_path}. Ghost-ALICE could not establish its relation to the "
        "folder used for installation.",
        "현재 작업 기록 위치는 {current_path}입니다. 이 위치와 설치에 사용한 폴더의 관계를 확인하지 못했습니다."),
    "move_locations_unreadable": (
        "Ghost-ALICE could not read the existing work record locations. Check the installation configuration and diagnostic details.",
        "Ghost-ALICE가 기존 작업 기록 위치를 조회하지 못했습니다. 설치 설정과 진단 상세를 확인하세요."),
    "move_location_unknown": (
        "Ghost-ALICE could not establish the installed record location for host {host_name}. "
        "Check that host's installation configuration and diagnostic details before retrying the move.",
        "Ghost-ALICE가 호스트 {host_name}의 설치된 기록 위치를 확인하지 못했습니다. "
        "기록 이동을 다시 시도하기 전에 해당 호스트의 설치 설정과 진단 상세를 확인하세요."),
    "move_lookup_deferred": (
        "Moving existing work records was not started because the required installation locations could not be read.",
        "필요한 설치 위치를 조회하지 못해 기존 작업 기록 옮기기를 시작하지 않았습니다."),
    "move_input_unavailable": (
        "Ghost-ALICE could not prepare terminal input for the move question, so the move was not started. Retry in a working terminal.",
        "Ghost-ALICE가 기록 이동 질문의 터미널 입력을 준비하지 못해 이동을 시작하지 않았습니다. 입력이 가능한 터미널에서 다시 실행하세요."),
    "move_service_blocked": (
        "Ghost-ALICE could not confirm the gateway conditions required to move the records. "
        "Check the diagnostic details for its registration and running state. After resolving the cause, press Enter to check again, or n to stop moving the records.",
        "Ghost-ALICE가 기록 이동에 필요한 게이트웨이 조건을 확인하지 못했습니다. "
        "진단 상세에서 등록·실행 상태를 확인하세요. 원인을 해결한 뒤 Enter를 누르면 다시 확인합니다. n을 누르면 기록 옮기기를 그만둡니다."),
    "move_not_run": (
        "Moving your existing work records at {current_path} was not started because the previous move has unfinished recovery or completion work.",
        "앞선 기록 이동의 복구 또는 마무리가 끝나지 않아 {current_path}에 있는 기존 작업 기록 옮기기를 시작하지 않았습니다."),
    "recovery_retry": (
        "Keep the affected host apps and CLI commands closed until recovery finishes. Reopening them may write new records at the destination. "
        "The next basic installation or update retries recovery during installation preparation only if the unfinished data and configuration have not changed. "
        "If they have changed, automatic recovery refuses to overwrite them. Preserve both record locations and the recovery records; do not delete the destination to force a retry.",
        "복구가 끝날 때까지 관련 호스트 앱과 CLI 명령을 다시 실행하지 마세요. 다시 실행하면 새 위치에 기록이 추가될 수 있습니다. "
        "다음 기본 설치 또는 업데이트는 설치 준비 중 미완료 데이터·설정이 바뀌지 않았을 때만 복구를 다시 시도합니다. "
        "바뀐 경우 자동 복구는 덮어쓰기를 거부합니다. 두 기록 위치와 복구 기록을 보존하고, 재시도를 통과시키려고 새 기록 위치를 삭제하지 마세요."),
    "step_restart": ("Restarting the context gateway", "문맥 게이트웨이 다시 시작"),
    "step_mark": ("Saving the completion record", "완료 기록 저장"),
}


def message(key: str, **fields: object) -> str:
    """Format user-facing text in this run's language; field values remain literal."""
    if key in ('move_host_running', 'move_host_unknown') and fields.get('host_name') in ('claude', 'codex'):
        fields['host_name'] = message('host_' + str(fields['host_name']))
    output = _MESSAGES[key][1 if _state.language == "ko" else 0].format(**fields)
    if fields.get('cause'):
        output += ('\n진단 상세: ' if _state.language == 'ko' else '\nDiagnostic details: ') + str(fields['cause'])
    return output


@dataclass
class OutputState:
    """The output state of this process: verbosity, report-child mode, log and event files, the sink."""

    verbose: bool = False
    report_child: bool = False
    log_file: Path | None = None
    event_file: Path | None = None
    tty: bool = False
    progress_env: str = "auto"
    home: Path | None = None
    language: str = "en"
    sink: IO[str] | None = field(default=None, repr=False)


_state = OutputState()


def configure(*, verbose: bool = False, report_child: bool = False, log_file: Path | None = None,
              event_file: Path | None = None, tty: bool | None = None, progress_env: str | None = None,
              home: Path | None = None) -> OutputState:
    """Set the output state for this process from options and environment.

    `log_file`/`event_file` are honored only when `report_child` is true.
    `tty` defaults to `sys.stdout.isatty()`
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
                         tty=tty, progress_env=progress_env, home=home, language=_display_language())
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
    emit('\n'.join(f'{prefix} {line}' for line in message.split('\n')))


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
    """`ok(message)` only in verbose mode."""
    if _state.verbose:
        ok(message)


def detail_warn(message: str) -> None:
    """`warn(message)` only in verbose mode."""
    if _state.verbose:
        warn(message)


def colors_enabled() -> bool:
    """ANSI colors only when stdout is a terminal."""
    return _state.tty


def compact_output_enabled() -> bool:
    """Not verbose."""
    return not _state.verbose


def report_enabled() -> bool:
    """Compact and not a report child."""
    return compact_output_enabled() and not _state.report_child


def live_counter_enabled() -> bool:
    """Progress animation: progress env not in PROGRESS_OFF_VALUES, not verbose, stdout a TTY."""
    if _state.progress_env in PROGRESS_OFF_VALUES:
        return False
    if _state.verbose:
        return False
    return _state.tty


def log_init() -> Path:
    """Choose `<home>/.ghost-alice/install/YYYY-MM-DD-HHMMSS-<run-id>.log` once and export
    `GHOST_ALICE_INSTALL_LOG_FILE`; an already chosen log file is kept. Before it opens a new
    log, the folder keeps the last 30 runs (`state_retention` install-logs)."""
    if _state.log_file is None:
        root = locations.install_log_root(_state.home)
        root.mkdir(parents=True, exist_ok=True)
        state_retention.apply("install-logs", root)
        _state.log_file = root / f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{uuid.uuid4().hex}.log"
    os.environ["GHOST_ALICE_INSTALL_LOG_FILE"] = str(_state.log_file)
    return _state.log_file


def run_logged(step: Callable[..., T], *args, **kwargs) -> T:
    """Run `step` with its output appended to the install log in compact mode,
    directly otherwise. Surface warnings from this step even when it succeeds."""
    if not compact_output_enabled():
        return step(*args, **kwargs)
    log = log_init()
    previous = _state.sink
    with open(log, "a+", encoding="utf-8") as handle:
        start = handle.tell()
        _state.sink = handle
        try:
            with contextlib.redirect_stdout(handle), contextlib.redirect_stderr(handle):
                return step(*args, **kwargs)
        finally:
            handle.flush()
            _state.sink = previous
            if previous is None and not _state.report_child:
                print_warnings(log, start=start)


def print_warnings(path: Path, *, start: int = 0) -> None:
    """Surface warnings from a logged step or a platform child, once at its outer output boundary."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        handle.seek(start)
        for line in handle:
            if line.lstrip().startswith("[WARN]"):
                emit(line.rstrip())


def failure_excerpt(path: Path | None, max_lines: int = 8) -> list[str]:
    """The last `max_lines` log lines matching FAILURE_EXCERPT_PATTERN, right-stripped."""
    if path is None or not Path(path).is_file():
        return []
    matched = [line.rstrip() for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
               if _FAILURE_EXCERPT_RE.search(line)]
    return matched[-max_lines:] if max_lines > 0 else []


def count_label(count: int, singular: str, plural: str) -> str:
    """`1 skill target` / `3 skill targets`."""
    return f"{count} {singular if count == 1 else plural}"


def print_skill_sync_summary(skill_targets: int, support_targets: int, installed: int, updated: int,
                             skipped: int, mode_label: str = "") -> None:
    """`[2/5] Skill sync: ...` info line."""
    message = (f"[2/5] Skill sync: {count_label(skill_targets, 'skill target', 'skill targets')}, "
               f"{count_label(support_targets, 'support target', 'support targets')}; "
               f"{installed} installed, {updated} updated, {skipped} skipped")
    if mode_label:
        message = f"{message} ({mode_label})"
    info(message)


def hook_suite_label(platform: str = "") -> str:
    """The core hooks the installer registers on every host."""
    return HOOK_SUITE_LABEL


def skill_sync_line(current: int, updated: int, new: int) -> str:
    """`  [2/5] Skill sync          [c] [Current], [u] [updated], [n] [newly added]`."""
    return f"  [2/5] Skill sync          [{current}] [Current], [{updated}] [updated], [{new}] [newly added]"


def common_skill_sync_line(common_targets: int) -> str:
    return f"  [2/5] Skill sync          [{common_targets}] common targets"


def progress_bar(done: int, total: int, width: int = 20) -> str:
    """`#` for the filled part, `-` for the rest; clamped."""
    filled = done * width // total if total > 0 else 0
    filled = min(max(filled, 0), width)
    return "#" * filled + "-" * (width - filled)


def common_target_progress_line(done: int, total: int, suffix: str = "common targets synced") -> str:
    """`        Common targets      [<bar 30>] [done/total] suffix`."""
    return f"        Common targets      [{progress_bar(done, total, 30)}] [{done}/{total}] {suffix}"


def clear_current_line() -> str:
    """`\\r\\x1b[2K`."""
    return "\r\x1b[2K"


def auto_update_common_target_progress_line(done: int, total: int, suffix: str) -> None:
    emit(clear_current_line() + common_target_progress_line(done, total, suffix), newline=False)


def auto_animate_common_target_progress_line(from_count: int, to_count: int, total: int, suffix: str,
                                             delay: float = 0.02) -> None:
    """Draw every count from `from_count + 1` to `to_count`, clamped to `total`."""
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
    emit("  Hooks: registration requested")
    emit(f"  Visibility Level: [{visibility}]\n")
    emit("Progress")
    emit("  [1/5] Preflight           ok")


def print_start(platform_label: str, total_targets: int, visibility: str = "dynamic",
                target_unit: str = "targets") -> None:
    """Report head plus `[1/5] Preflight ok` and the zero sync line."""
    _print_header(platform_label, f"  Skills: [{total_targets}] {target_unit}", visibility)
    emit(skill_sync_line(0, 0, 0), newline=False)


def print_tail(platform_label: str, visibility: str = "dynamic", notes: Sequence[str] = (), *, hook_statuses=None) -> None:
    """`[3/5] Hooks`, `[4/5] Runtime config`, `[5/5] Verification`, Attention, Details.

    Attention carries the `/visibility` hint when Claude Code is among the platforms (Codex has
    no such command), then `notes`; with neither, the section is left out.
    """
    log = log_init()
    statuses = hook_statuses or {}
    for platform in (name.strip() for name in platform_label.split(',')):
        status = statuses.get(platform, 'unknown')
        label = 'skipped (platform directory missing)' if status == 'skipped' else status
        emit(f'  [3/5] {platform} Hooks: {label}')
        if status in {'installed', 'already'}:
            emit(f'        {hook_suite_label(platform)} registered')
    emit(f"  [4/5] Runtime config      Visibility Level=[{visibility}]")
    emit("  [5/5] Verification        ok\n")
    attention = list(notes)
    if "claude" in (platform.strip() for platform in platform_label.split(",")):
        attention.insert(0, "visibility can be changed later in Claude Code with /visibility between: dynamic | minimal | strict")
    if attention:
        emit("Attention")
        for item in attention:
            emit(f"  - {item}")
        emit("")
    emit("Details")
    emit(f"  log: {log}")
    emit("  rerun with --verbose to show per-skill actions")


def print_full(platform_label: str, total_targets: int, current: int, updated: int, new: int,
               visibility: str = "dynamic", target_unit: str = "targets", notes: Sequence[str] = (), *, hook_statuses=None) -> None:
    _print_header(platform_label, f"  Skills: [{total_targets}] {target_unit}", visibility)
    emit(skill_sync_line(current, updated, new))
    print_tail(platform_label, visibility, notes, hook_statuses=hook_statuses)


def print_auto_start(platform_label: str, common_targets: int, visibility: str = "dynamic") -> None:
    _print_header(platform_label, f"  Skills: [{common_targets}] common targets", visibility)
    emit(common_skill_sync_line(common_targets))
    emit(common_target_progress_line(0, common_targets), newline=False)


def print_auto_full(platform_label: str, common_targets: int, visibility: str = "dynamic",
                    synced_targets: int = 0, notes: Sequence[str] = (), *, hook_statuses=None) -> None:
    _print_header(platform_label, f"  Skills: [{common_targets}] common targets", visibility)
    emit(common_skill_sync_line(common_targets))
    emit(common_target_progress_line(synced_targets, common_targets, "common targets synced on all platforms"))
    print_tail(platform_label, visibility, notes, hook_statuses=hook_statuses)


def _append_event(line: str) -> None:
    if _state.event_file is None:
        return
    _state.event_file.parent.mkdir(parents=True, exist_ok=True)
    with open(_state.event_file, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_event(platform: str, total_targets: int, current: int, updated: int, new: int, *,
                verification: str, hook_status=None) -> list[str]:
    """Return the hook notices for direct output and send the same notices to the auto parent."""
    items = hook_status.details.get('items', []) if hook_status is not None else []
    notes = ['partial: permissions retained for review'] if any(item.get('action') == 'manual-review' for item in items) else []
    _append_event(json.dumps(dict(type='platform-result', platform=platform, total_targets=total_targets,
        current=current, updated=updated, new=new, verification=verification,
        hooks=hook_status.legacy_status if hook_status is not None else 'unknown',
        hook_notes=notes,
        hook_errors=hook_status.details.get('errors', []) if hook_status is not None else [])))
    return notes


def write_gateway_event(platform: str, summary: str, *, running: bool) -> None:
    """Append a `gateway-result` JSON line: the context gateway observation one child made for the parent's report."""
    _append_event(json.dumps({"type": "gateway-result", "platform": platform, "summary": summary, "running": running}))


def read_install_results(event_file: Path) -> tuple[dict[str, str], list[str], dict[str, list[str]]]:
    """Read child hook results and distinct notes, with gateway notes first; absence stays unknown."""
    statuses, gateway_notes, hook_notes, errors = {}, [], [], {}
    try:
        content = Path(event_file).read_bytes()
    except OSError:
        return statuses, [], errors
    for _, _, line in committed_byte_lines(content):
        text = line.decode("utf-8")
        try:
            events = committed_records(text).values()
        except ValueError:
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'gateway-result' and isinstance(event.get('summary'), str):
                if event['summary'] not in gateway_notes:
                    gateway_notes.append(event['summary'])
            elif event.get('type') == 'platform-result':
                if isinstance(event.get('platform'), str) and event.get('hooks') in {'installed', 'already', 'skipped', 'error'}:
                    statuses[event['platform']] = event['hooks']
                    if isinstance(event.get('hook_errors'), list) and all(isinstance(line, str) for line in event['hook_errors']):
                        errors[event['platform']] = event['hook_errors']
                for note in event.get('hook_notes', []):
                    if isinstance(note, str) and note not in hook_notes:
                        hook_notes.append(note)
    return statuses, gateway_notes + hook_notes, errors


def write_target_event(platform: str, target_id: str, target_kind: str, status: str) -> None:
    """Append a `target-result` JSON line to the event file when one is set."""
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
