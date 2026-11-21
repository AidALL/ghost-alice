"""Ask once after installation, then move records through the existing transaction owner."""

from __future__ import annotations

from pathlib import Path

import install_transaction
import installed_payload

from installer import gateway_participant, hosts, locations, lock, report, source, terminal, transaction


def read_locations() -> transaction.LedgerLocations | None:
    """Report unavailable facts without replacing the result of installation, diagnosis or removal."""
    try:
        observed = transaction.installed_ledger_locations()
    except (OSError, ValueError, installed_payload.RecordError) as exc:
        report.warn(report.message('move_locations_unreadable', cause=exc))
        return None
    for path, cause in observed.observation_errors.items():
        report.warn(report.message('move_locations_unreadable', cause=f'{path}: {cause}'))
    for host, cause in observed.unavailable_hosts.items():
        report.warn(report.message('move_location_unknown', host_name=host, cause=cause))
    for host, cause in observed.host_config_errors.items():
        report.warn(report.message('move_location_unknown', host_name=host, cause=cause))
    return observed


def _answer(question: str, *, waiting: bool = False) -> bool | None:
    while True:
        try:
            answer = terminal.read_answer(question)
        except (OSError, ValueError) as exc:
            report.warn(report.message('move_input_unavailable', cause=exc))
            return None
        if answer is None:
            return False
        answer = answer.strip().lower()
        if answer == '' or (not waiting and answer in ('y', 'yes')):
            return True
        if answer in ('n', 'no'):
            return False
        report.emit(report.message('move_wait_invalid' if waiting else 'move_invalid'))


def _writers(current: Path, fixed: Path, bindings: dict) -> list[tuple[str, dict]]:
    platforms = dict.fromkeys(host for root, value in bindings.items() if root in (current, fixed)
                              for host in value['hosts'])
    blocked = []
    for host, seen in hosts.observe_writers(platforms).items():
        if seen.state == 'running':
            blocked.append(('move_host_running', dict(host_name=host)))
        elif seen.state != 'absent':
            blocked.append(('move_host_unknown', dict(host_name=host, cause=seen.detail)))
    _, cause = gateway_participant.ledger_migration_participants(locations.record_root(), current, fixed)
    if cause is not None:
        blocked.append(('move_service_blocked', dict(cause=cause)))
    return blocked


def run(execution_cause: str) -> int:
    """Preserve each path's result; user cancellation never changes installed record bindings."""
    if execution_cause not in ('install', 'update', 'post-merge'):
        raise ValueError('Unknown installation execution cause')
    report.emit(report.message('update_complete' if execution_cause == 'update' else 'install_complete'))
    fixed = locations.ledger_root().resolve()
    observed = read_locations()
    if observed is None or observed.observation_errors or observed.active_config_errors:
        report.emit(report.message('move_lookup_deferred'))
        return 0
    if observed.retired_roots:
        handle = lock.acquire('record-move', 'installer')
        try:
            for root in transaction.cleanup_retired_ledgers():
                report.emit(report.message('move_source_removed', current_path=root, new_path=fixed))
        except (OSError, ValueError, install_transaction.InstallTransactionError) as exc:
            report.error(report.message('move_source_cleanup_failed', new_path=fixed, cause=exc))
            return 1
        finally:
            lock.release(handle)
    bindings = observed.roots
    candidates = []
    for root, value in bindings.items():
        if value['state'] in ('fixed', 'retired'):
            continue
        if value['state'] == 'unknown':
            report.emit(report.message('move_relation_unknown', current_path=root))
            report.emit(report.message('move_not_moved', current_path=root, cause=''))
        else:
            candidates.append(root)
    if not candidates:
        return 0
    report.emit(report.message('move_intro'))
    for root in candidates:
        report.emit(report.message('move_locations', source_path=bindings[root]['source_root'],
                                   current_path=root, new_path=fixed))
    if observed.unavailable_hosts or execution_cause == 'post-merge' or not source.interactive():
        for root in candidates:
            report.emit(report.message('move_deferred', current_path=root, entry_command=locations.management_command()))
        return 0
    report.emit(report.message('move_explanation'))
    answer = _answer(report.message('move_question') + '\n' + report.message('move_choices'))
    if answer is None:
        return 0
    if not answer:
        for root in candidates:
            report.emit(report.message('move_cancelled', current_path=root, entry_command=locations.management_command()))
        return 0
    exit_code = 0
    uncertain = None
    for root in candidates:
        if uncertain is not None:
            report.emit(report.message('move_not_run', current_path=root, cause=uncertain))
            continue
        while True:
            observed = read_locations()
            if observed is None or observed.dependency_errors:
                result = transaction.LedgerMoveResult('deferred')
                break
            bindings = observed.roots
            if root not in bindings or bindings[root]['state'] != 'source-bound':
                result = transaction.LedgerMoveResult('deferred', 'Installed record bindings changed before the move')
                break
            blocked = _writers(root, fixed, bindings)
            if not blocked:
                handle = lock.acquire('record-move', 'installer')
                try:
                    observed = read_locations()
                    if observed is None or observed.dependency_errors:
                        result = transaction.LedgerMoveResult('deferred')
                        break
                    bindings = observed.roots
                    if root not in bindings or bindings[root]['state'] != 'source-bound':
                        result = transaction.LedgerMoveResult('deferred', 'Installed record bindings changed before the move')
                        break
                    blocked = _writers(root, fixed, bindings)
                    if not blocked:
                        try:
                            result = transaction.migrate_ledger(root, writers_stopped=True)
                        except (OSError, ValueError, install_transaction.InstallTransactionError) as exc:
                            result = transaction.LedgerMoveResult('failed', str(exc))
                        break
                finally:
                    lock.release(handle)
            answer = _answer('\n'.join(report.message(key, **fields) for key, fields in blocked), waiting=True)
            if answer is None:
                return exit_code
            if not answer:
                for remaining in candidates[candidates.index(root):]:
                    report.emit(report.message('move_cancelled', current_path=remaining, entry_command=locations.management_command()))
                return exit_code
        fields = dict(current_path=root, new_path=fixed, cause=result.detail)
        if result.status == 'complete':
            key = 'move_complete'
        elif result.status == 'cleanup_failed':
            key = 'move_cleanup_failed'
            exit_code = 1
        elif result.status in ('resume_incomplete', 'mark_incomplete'):
            key = 'move_committed_pending'
            fields['step'] = report.message('step_restart' if result.status == 'resume_incomplete' else 'step_mark')
            uncertain = result.detail
            exit_code = 1
        elif result.status == 'restore_incomplete':
            key = 'move_recovery_incomplete'
            fields.update(recovery_paths=', '.join(result.recovery_paths), recovery=report.message('recovery_retry'))
            fields.update(phase=result.phase, affected_paths='\n'.join(result.affected_paths))
            uncertain = result.detail
            exit_code = 1
        elif result.status == 'restored':
            key = 'move_restored'
            exit_code = 1
        else:
            key = 'move_not_moved'
            if result.status != 'deferred':
                exit_code = 1
        report.emit(report.message(key, **fields))
        if result.retained:
            report.emit(report.message('retained_installation_artifacts', paths='\n'.join(result.retained)))
    return exit_code
