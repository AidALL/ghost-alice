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


def removal_item_lines(item: dict) -> list[tuple[str, str]]:
    """Translate one removal result for both full and selected removal screens."""
    lines = []
    for journal, source in item.get('pending_record_sources', {}).items():
        lines.append(('WARN', message('record_source_journal_retained', current_path=source, journal_path=journal)))
    if item.get('kind') == 'record-source-references':
        lines.append(('WARN', message('record_source_references_failed', path=item['path'])))
    elif item.get('kind') == 'record-source':
        key = {'removed': 'record_source_removed', 'absent': 'record_source_absent',
               'manual-review': 'record_source_failed'}[item['action']]
        lines.append(('WARN' if item['action'] == 'manual-review' else 'INFO',
                      message(key, current_path=item['path'], new_path=item['destination'])))
    path = item.get('path') or item.get('target')
    errors = item.get('errors') or []
    detail = item.get('detail')
    if item.get('removal_incomplete') and detail and not errors:
        errors = [detail]
    if path and item.get('action') == 'manual-review':
        reason = item.get('reason', 'review-required')
        if item.get('kind') == 'source-repo-hook-config':
            reason = f'core.hooksPath: {reason}'
        key = 'removal_path_absent' if item.get('file_state') == 'absent' else 'removal_retained'
        lines.append(('WARN', message(key, path=path, reason=reason, cause=None if errors else detail)))
        if item.get('backup_path'):
            lines.append(('INFO', message('removal_backup_path', path=item['backup_path'])))
    for cause in errors:
        lines.append(('ERROR', message('removal_failed', path=path or item.get('kind', 'uninstall'), cause=cause)))
    for retained in item.get('retained_originals', []):
        if item.get('action') == 'quarantined-removed' and item.get('path') and item.get('backup_path'):
            prefix = str(Path(item['path'])) + os.sep
            if retained.startswith(prefix):
                retained = str(Path(item['backup_path'])) + os.sep + retained[len(prefix):]
        lines.append(('INFO', retained))
    return lines


def emit_removal_item(item: dict) -> None:
    for level, line in removal_item_lines(item):
        {'INFO': info, 'WARN': warn, 'ERROR': error}[level](line)

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
    "invalid_skill_hosts": (
        "In installation record {file}, used_by for {target} must be a list of host names.",
        "설치 기록 {file}에서 {target}의 used_by 값은 호스트 이름의 목록이어야 합니다."),
    "active_version_mismatch": (
        "This installer's files differ from the saved files of the active installer at {package}, and repair restores only the active installer's files. To restore this version, run the repair from a checkout containing the same Core files with the same addon sources. To replace the installed Core skills with the newest release, run {update}; an update restores no addon file.",
        "이 인스톨러의 파일이 활성 인스톨러({package})의 보관 파일과 다르며, 복구는 활성 인스톨러의 파일만 복원합니다. 이 버전을 복원하려면 같은 Core 파일이 있는 원본에서 기존 애드온 원본을 지정해 복구를 실행해 주세요. 설치한 Core 스킬을 최신 릴리스로 교체하려면 {update} 명령을 실행해 주세요. 갱신은 애드온 파일을 복원하지 않습니다."),
    "active_core_lost": (
        "The active installer at {package} lost {file} and has only its own files to restore from. {restore} {replacement}",
        "활성 인스톨러({package})에서 {file} 항목이 누락되었고, 활성 인스톨러가 복원에 사용할 수 있는 파일은 자신의 보관 파일뿐입니다. {restore} {replacement}"),
    "restore_from_checkout": (
        "To restore this version, run the repair from a checkout containing the same Core files.",
        "이 버전을 복원하려면 같은 Core 파일이 있는 원본에서 복구를 실행해 주세요."),
    "restore_from_registered": (
        "To restore this version, run {repair}: the registered checkout restores it when it contains the same Core files.",
        "이 버전을 복원하려면 {repair} 명령을 실행해 주세요. 설치 기록의 원본 폴더에 같은 Core 파일이 있으면 이 명령이 누락된 항목을 복원합니다."),
    "active_addon_lost": (
        "The saved addon files of the active installer at {package} lost {file}, and this command names no addon source that holds it. Run the repair with --addon-source for each addon source that this version was installed with.",
        "활성 인스톨러({package})의 보관 애드온 파일에서 {file} 항목이 누락되었고, 이 명령은 그 항목이 있는 애드온 원본을 지정하지 않았습니다. 이 버전을 설치할 때 지정한 애드온 원본마다 --addon-source를 지정해 복구를 실행해 주세요."),
    "private_addon_id": (
        "Only the per-host installer accepts --addon-id. To remove one addon, use --uninstall --addon ID.",
        "--addon-id는 호스트별 설치 프로세스에서만 받습니다. 애드온 하나를 제거하려면 --uninstall --addon ID를 사용하세요."),
    "addon_registry_unreadable": (
        "Reinstall stopped because the installer could not read the {host} addon registry: {details}",
        "{host}의 애드온 등록 파일을 읽을 수 없어 재설치를 중단했습니다. {details}"),
    "active_entry_missing": (
        "The active installer is missing {entry}. Run --repair from a Ghost-ALICE checkout with the same Core files.",
        "활성 인스톨러에 {entry} 파일이 없습니다. 같은 Core 파일이 있는 Ghost-ALICE 체크아웃에서 --repair를 실행하세요."),
    "active_version_missing": (
        "The entire saved version at {path} is missing. {replacement}",
        "활성 인스톨러의 보관 버전 폴더 {path}이(가) 없습니다. {replacement}"),
    "version_metadata_missing": (
        "The saved file list is missing at {path}. To use this same version, restore its file list from backup. {replacement}",
        "보관 버전의 파일 목록 {path}이(가) 없습니다. 같은 버전을 사용하려면 백업에서 파일 목록을 복원해 주세요. {replacement}"),
    "replacement_update": (
        "To replace the installed Core skills with the latest release, run {command}. An update restores no addon files.",
        "설치한 Core 스킬을 최신 릴리스로 교체하려면 {command} 명령을 실행해 주세요. 갱신은 애드온 파일을 복원하지 않습니다."),
    "replacement_from_checkout": (
        "To install a replacement, use a Core checkout containing the desired version and specify its host, skills and addon sources with --local-source.",
        "새로 설치하려면 원하는 버전이 있는 Core 체크아웃에서 호스트, 스킬 및 애드온 원본을 지정해 --local-source로 설치해 주세요."),
    "repair_inventory_unreadable": (
        "Repair could not read the inventory {file}. Run {command} with the names of the skills to install, then run the repair again.",
        "복구가 설치 목록을 조회하지 못했습니다: {file}. {command}에 설치할 스킬 이름을 덧붙여 실행한 뒤 복구를 다시 실행해 주세요."),
    "repair_targets_unrecorded": (
        "The install record does not identify what is installed for {host}. Run {command} with the names of the skills to install.",
        "설치 기록에서 {host}에 설치된 대상을 확인할 수 없습니다. {command}에 설치할 스킬 이름을 덧붙여 실행해 주세요."),
    "repair_skill_missing": (
        "The install record names the Core skill {name}, but {package} contains no such skill.",
        "설치 기록에 있는 Core 스킬 {name}을 {package}에서 찾을 수 없습니다."),
    "repair_shared_unrecorded": (
        "The install record names skills of {host}, but {path} is missing and the install record does not name it. Run {command}.",
        "설치 기록에 {host}의 스킬이 있지만 {path} 폴더가 누락되었고 설치 기록에도 이 폴더가 없습니다. {command} 명령을 실행해 주세요."),
    "inventory_preserved": (
        "The inventory {file} could not be read ({cause}). The installer moved it to {backup} and continues without it.",
        "설치 목록 {file}을 읽을 수 없습니다({cause}). 인스톨러가 이 파일을 {backup}으로 옮기고 설치를 계속합니다."),
    "repair_other_folder": (
        "The install record names the skills of {host} in {recorded}, and this command uses {current}. Run the repair with the settings that place the skills in {recorded}.",
        "설치 기록에는 {host}의 스킬이 {recorded}에 있고, 이 명령은 {current}를 사용합니다. 스킬을 {recorded}에 배치하는 설정으로 복구를 실행해 주세요."),
    "repair_shared_occupied": (
        "A file occupies {path}. Repair kept it and could not restore the support directory.",
        "{path}에 파일이 있어 지원 폴더를 복원할 수 없습니다. 파일은 유지했습니다."),
    "update_origin_changed": (
        "The registered checkout or remote changed before installation. To use the current registration, run the command again without --remote.",
        "설치 전에 설치 기록의 원본 폴더 또는 원격 저장소가 바뀌었습니다. 현재 기록으로 갱신하려면 --remote 없이 명령을 다시 실행해 주세요."),
    "install_origin_changed": (
        "The registered checkout or remote changed before installation. Installation stopped.",
        "설치 전에 설치 기록의 원본 폴더 또는 원격 저장소가 바뀌어 설치를 중단했습니다."),
    "core_selection_changed": (
        "The recorded Core selection changed before installation. Run the command again to use the current selection.",
        "설치 전에 기록된 Core 스킬 선택이 바뀌었습니다. 현재 선택으로 설치하려면 명령을 다시 실행해 주세요."),
    "reinstall_command": (
        "Reinstall this version for the hosts and Core skills currently installed: {command}",
        "현재 설치된 호스트와 Core 스킬에 이 버전을 다시 설치하는 명령: {command}"),
    "merged_origin_changed": (
        "Post-merge: the registered installation source changed before placement. Installation stopped.",
        "병합 후 파일을 배치하기 전에 등록된 설치 원본이 바뀌어 설치를 중단했습니다."),
    "release_older": (
        "If the release from {remote} reported an unknown --install-acquired-release option, its installer did not accept this installer's request. Use a compatible main branch, or run this checkout's install script with --local-source to install its files.",
        "{remote}의 릴리스가 --install-acquired-release 옵션을 알 수 없다고 출력했다면 그 릴리스가 이 설치 요청을 지원하지 않습니다. 호환되는 main 브랜치를 사용하거나, 이 체크아웃의 파일을 설치하려면 설치 스크립트에 --local-source를 지정해 실행해 주세요."),
    "earlier_request": (
        "An earlier Ghost-ALICE installer started this package. This version does not continue that installation and changed no installed file or installation record. Run {command}. If your command named a host or skills, add the same --platform option and skill names.",
        "이전 Ghost-ALICE 인스톨러가 이 패키지를 실행했습니다. 이 버전은 그 설치를 이어서 실행하지 않으며, 설치된 파일과 설치 기록을 변경하지 않았습니다. {command}를 실행해 주세요. 중단된 명령에 호스트나 스킬을 지정했으면 같은 --platform 옵션과 스킬 이름을 덧붙여 주세요."),
    "install_unfinished": (
        "The installation did not finish. If an error above names another command, run that command. Otherwise resolve the reported cause and run the same command again.",
        "설치를 완료하지 못했습니다. 위 오류에 다른 명령이 안내되어 있으면 그 명령을 실행해 주세요. 없으면 표시된 원인을 해결한 뒤 같은 명령을 다시 실행해 주세요."),
    "merged_install_unfinished": (
        "The installation after the merge did not finish. Resolve the reported cause, then run {command}.",
        "병합 뒤 설치를 완료하지 못했습니다. 표시된 원인을 해결한 뒤 {command}를 실행해 주세요."),
    "active_installer": ("The active installer is {active}.", "활성 인스톨러는 {active}입니다."),
    "removal_backup_path": ("Backup location: {path}", "보관 사본 위치: {path}"),
    "host_claude": ("Claude Code", "Claude Code"),
    "host_codex": ("Codex", "Codex"),
    "shutdown_running": (
        "{host_name}: still running (PID: {pids})",
        "{host_name}: 실행 중 (PID: {pids})"),
    "shutdown_unknown": (
        "The installer could not check the running programs for {host_name}.",
        "인스톨러가 {host_name}의 실행 프로그램을 확인하지 못했습니다."),
    "shutdown_question": (
        "Save your work. May the installer close the programs listed above before installation? [y/N]",
        "진행 중인 작업을 저장해 주세요. 설치 전에 인스톨러가 위 프로그램을 종료할까요? [y/N]"),
    "shutdown_targets": (
        "Programs for this termination request:", "이번 종료 요청 대상:"),
    "shutdown_ancestor": (
        "Installation did not start because closing PID {pids} could stop this installer or its terminal. Run the install command from an independent terminal.",
        "PID {pids}를 종료하면 인스톨러나 실행 중인 터미널도 중단될 수 있어 설치를 시작하지 않았습니다. 독립된 터미널에서 설치 명령을 다시 실행해 주세요."),
    "shutdown_manual": (
        "The installer has no confirmed normal termination method for PID {pids}. Close these programs yourself, then check again.",
        "PID {pids}의 정상 종료를 요청할 수 있는 수단을 확인하지 못했습니다. 직접 프로그램을 종료한 뒤 다시 확인해 주세요."),
    "terminal_input_unavailable": (
        "The installer could not read an answer to the current question. Retry in a working terminal.",
        "인스톨러가 현재 질문의 답을 읽지 못했습니다. 입력이 가능한 터미널에서 다시 실행해 주세요."),
    "shutdown_retry": (
        "Close any remaining listed programs or resolve the reported lookup error. Press Enter to check again, or n to cancel installation.",
        "목록에 남은 프로그램을 종료하거나 표시된 조회 오류를 해결해 주세요. Enter를 누르면 다시 확인하고, n을 누르면 설치를 취소합니다."),
    "shutdown_terminal_required": (
        "Installation did not start. Run the install command in a terminal to confirm closing the listed programs, or close them before retrying.",
        "설치를 시작하지 않았습니다. 터미널에서 설치 명령을 실행해 표시된 프로그램의 종료에 동의하거나, 직접 종료한 뒤 다시 실행해 주세요."),
    "shutdown_cancelled": (
        "Installation was cancelled before installed files changed.",
        "설치된 파일을 변경하기 전에 설치를 취소했습니다."),
    "shutdown_request_failed": (
        "The installer could not request normal termination of PID {pid}.",
        "인스톨러가 PID {pid}의 정상 종료를 요청하지 못했습니다."),
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
        "Your existing work records from {current_path} are now at {new_path}. The installer deleted the old record folder. Ghost-ALICE will also store new work records there.",
        "기존 작업 기록을 {current_path}에서 {new_path}로 옮겼습니다. 인스톨러가 옛 기록 폴더를 삭제했습니다. 앞으로의 작업 기록도 이 위치에 저장합니다."),
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
        "Your work records are at {new_path}. The installer could not finish deleting the old record folder {current_path} or "
        "completed installation artifacts. The new record location remains active.",
        "작업 기록은 {new_path}에 있습니다. 인스톨러가 옛 기록 폴더 {current_path} 또는 완료된 배치의 보관 자료를 모두 삭제하지 못했습니다. "
        "새 기록 위치는 그대로 사용합니다."),
    "move_source_removed": (
        "The installer deleted the old record folder {current_path}. Your work records remain at {new_path}.",
        "인스톨러가 옛 기록 폴더 {current_path}를 삭제했습니다. 작업 기록은 {new_path}에 있습니다."),
    "move_source_cleanup_failed": (
        "The installer could not finish deleting an old record folder. Your work records remain at {new_path}.",
        "인스톨러가 옛 기록 폴더 삭제를 마치지 못했습니다. 작업 기록은 {new_path}에 있습니다."),
    "move_cleanup_pending": (
        "Old record folder deletion is pending.\n  Remaining source (A): {current_path}\n"
        "  Recorded destination (B): {new_path}\n  Completed journal: {journal_path}\n"
        "The next basic installation or update that proceeds to cleanup retries it. If the cause remains, it can fail again. "
        "Review the earlier cleanup error and resolve its reported cause before retrying.",
        "옛 기록 폴더 삭제가 대기 중입니다.\n  남은 원래 위치(A): {current_path}\n"
        "  저널에 기록된 이동 대상(B): {new_path}\n  완료 저널: {journal_path}\n"
        "다음 기본 설치 또는 업데이트가 정리 단계까지 진행하면 다시 시도합니다. 원인이 남아 있으면 다시 실패할 수 있습니다. "
        "앞선 정리 오류에 나온 원인을 확인하고 해결한 뒤 다시 시도하세요."),
    "move_cleanup_journal_unreadable": (
        "Could not inspect record cleanup journal: {journal_path}. Review the diagnostic details.",
        "기록 정리 저널을 확인하지 못했습니다: {journal_path}. 진단 상세를 확인하세요."),
    "record_source_removed": (
        "The old record folder was deleted: {current_path}. Recorded destination: {new_path}.",
        "옛 기록 폴더를 삭제했습니다: {current_path}. 저널에 기록된 이동 대상: {new_path}."),
    "record_source_absent": (
        "The old record folder is already absent: {current_path}. Recorded destination: {new_path}.",
        "옛 기록 폴더가 이미 없습니다: {current_path}. 저널에 기록된 이동 대상: {new_path}."),
    "record_source_failed": (
        "The old record folder could not be deleted: {current_path}. Recorded destination: {new_path}.",
        "옛 기록 폴더를 삭제하지 못했습니다: {current_path}. 저널에 기록된 이동 대상: {new_path}."),
    "record_source_references_failed": (
        "Could not preserve the host references needed to clean up old work records: {path}. Details follow.",
        "옛 작업 기록을 정리할 때 필요한 호스트 참조를 보존하지 못했습니다: {path}. 원인은 다음과 같습니다."),
    "removal_retained": (
        "Retained installation item: {path}. Reason: {reason}",
        "남긴 설치 항목: {path}. 이유: {reason}"),
    "removal_path_absent": (
        "Installation path already absent: {path}. Reason: {reason}",
        "이미 없는 설치 경로: {path}. 이유: {reason}"),
    "removal_failed": (
        "Removal incomplete for {path}.",
        "다음 항목의 제거를 마치지 못했습니다: {path}."),
    "removal_retry": (
        "After resolving the reported cause, run the same removal command again.",
        "표시된 원인을 해결한 뒤 같은 제거 명령을 다시 실행하세요."),
    "record_source_journal_retained": (
        "The old record folder remains: {current_path}. Its cleanup journal was kept at {journal_path} for the next attempt.",
        "옛 기록 폴더가 남아 있습니다: {current_path}. 다음 정리에 필요한 정리 저널을 {journal_path}에 유지했습니다."),
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
    "move_location_unconfirmed": (
        "A managed hook refers to the record location {current_path}. Ghost-ALICE could not confirm whether it is current or retired "
        "because the installation configuration could not be read.",
        "관리 훅이 가리키는 기록 위치는 {current_path}입니다. 설치 설정을 읽지 못해 "
        "현재 사용 중인지 사용이 종료됐는지 확인하지 못했습니다."),
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
    "git_hooks_skipped": (
        "The installer did not set a Git hook path because it found no Git repository at {root} or no Git program.",
        "{root}의 Git 저장소 또는 Git 프로그램을 찾지 못해 Git 훅 경로를 설정하지 않았습니다."),
    "git_hooks_repository_unreadable": (
        "Git could not read the repository at {root}. The installation stopped before it placed files.",
        "Git이 {root}의 저장소를 조회하지 못했습니다. 파일을 배치하기 전에 설치를 중단했습니다."),
    "git_hooks_failed": (
        "The installer could not register its Git hooks folder in {config}. "
        "The installation stopped before it placed files.",
        "{config}에 Ghost-ALICE의 Git 훅 폴더를 등록하지 못했습니다. 파일을 배치하기 전에 설치를 중단했습니다."),
    "git_hooks_locked": (
        "Git's lock file exists: {lock}. Delete it only when no Git command is running, then run the same command again.",
        "Git 잠금 파일이 있습니다: {lock}. 실행 중인 Git 명령이 없을 때만 이 파일을 삭제한 뒤 같은 명령을 다시 실행하세요."),
    "git_hooks_record_unreadable": (
        "The installer could not read the Git hook path record {path}. Repair it, then run the same command again. "
        "If it cannot be repaired, set core.hooksPath of the registered repository to its earlier value, "
        "move the file to another folder and run the same command again.",
        "Git 훅 경로 기록을 조회하지 못했습니다: {path}. 파일 내용을 수정한 뒤 같은 명령을 다시 실행하세요. "
        "수정할 수 없으면 등록된 저장소의 core.hooksPath를 이전 값으로 변경하고 파일을 다른 폴더로 이동한 뒤 같은 명령을 다시 실행하세요."),
    "step_restart": ("Restarting the context gateway", "문맥 게이트웨이 다시 시작"),
    "step_mark": ("Saving the completion record", "완료 기록 저장"),
}


def message(key: str, **fields: object) -> str:
    """Format user-facing text in this run's language; field values remain literal."""
    if key in ('move_host_running', 'move_host_unknown', 'shutdown_unknown') and 'host_' + str(fields.get('host_name')) in _MESSAGES:
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

    Explicit log paths come from the current installation request. Only report children
    may also inherit paths from environment variables; independent runs ignore stale paths.
    `tty` defaults to `sys.stdout.isatty()`
    and `progress_env` to `GHOST_ALICE_INSTALL_PROGRESS` or `auto`.
    """
    global _state
    if report_child:
        if log_file is None and os.environ.get("GHOST_ALICE_INSTALL_LOG_FILE"):
            log_file = Path(os.environ["GHOST_ALICE_INSTALL_LOG_FILE"])
        if event_file is None and os.environ.get("GHOST_ALICE_INSTALL_EVENT_FILE"):
            event_file = Path(os.environ["GHOST_ALICE_INSTALL_EVENT_FILE"])
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
    """The command printed by the public entry that started this process and its children."""
    return os.environ.get("GHOST_ALICE_INSTALL_COMMAND") or DEFAULT_INSTALL_COMMAND


def emit(text: str, *, newline: bool = True, record: bool = True) -> None:
    """Write to the sink or stdout, saving uncaptured terminal messages in the current log."""
    stream = _state.sink if _state.sink is not None else sys.stdout
    stream.write(text + ("\n" if newline else ""))
    stream.flush()
    if record and not _state.report_child and _state.sink is None and _state.log_file is not None:
        with _state.log_file.open('a', encoding='utf-8') as handle:
            handle.write(text + ("\n" if newline else ""))


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
    log, `state_retention` prunes superseded successes and bounds unsuccessful runs."""
    if _state.log_file is None:
        root = locations.install_log_root(_state.home)
        root.mkdir(parents=True, exist_ok=True)
        state_retention.apply("install-logs", root)
        _state.log_file = root / f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{uuid.uuid4().hex}.log"
    os.environ["GHOST_ALICE_INSTALL_LOG_FILE"] = str(_state.log_file)
    if _state.event_file is None:
        _state.event_file = _state.log_file.with_suffix('.events.jsonl')
    os.environ["GHOST_ALICE_INSTALL_EVENT_FILE"] = str(_state.event_file)
    return _state.log_file


def finish_install(source_digest: str, returncode: int) -> None:
    """The requesting installer records its final result after the processes it started have exited."""
    if _state.log_file is None or not _state.log_file.is_file():
        return  # Verbose output produces no shared detail log.
    _append_event(json.dumps(dict(type='install-result', source_digest=source_digest, returncode=returncode)))
    state_retention.apply('install-logs', _state.log_file.parent, protect=[_state.log_file])


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
                emit(line.rstrip(), record=False)


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
    emit(clear_current_line() + common_target_progress_line(done, total, suffix), newline=False, record=False)


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
