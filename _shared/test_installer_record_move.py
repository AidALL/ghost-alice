"""The user flow joins observations and migration outcomes without duplicating their algorithms."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from installer import record_move, report, transaction
from installer.runner import ToolResult
from installer_testing import installer_env, run_pty


class RecordMoveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ghost = Path(temp.name).resolve()
        self.a, self.b = self.ghost / 'source/records', self.ghost / 'session-intent'
        self.bindings = {self.a: dict(hosts={'claude': {}}, source_root=self.ghost / 'source', state='source-bound'),
                         self.b: dict(hosts={'codex': {}}, source_root=None, state='fixed')}
        self.enterContext(mock.patch.object(record_move.locations, 'record_root', return_value=self.ghost))
        self.unavailable = {}
        self.lookup = self.enterContext(mock.patch.object(transaction, 'installed_ledger_locations',
            side_effect=lambda: transaction.LedgerLocations(self.bindings, self.unavailable)))
        self.interactive = self.enterContext(mock.patch.object(record_move.source, 'interactive', return_value=True))
        self.writers = self.enterContext(mock.patch.object(record_move.hosts, 'observe_writers',
            wraps=record_move.hosts.observe_writers,
            return_value={'claude': mock.Mock(state='absent'), 'codex': mock.Mock(state='absent')}))
        self.enterContext(mock.patch.object(record_move.gateway_participant, 'participants', return_value=[]))
        self.enterContext(mock.patch.object(record_move.gateway_participant, 'loaded_service'))
        self.listener = self.enterContext(mock.patch.object(record_move.gateway_participant, 'listener_binding',
                                                             return_value=mock.Mock(state='absent')))
        self.acquire = self.enterContext(mock.patch.object(record_move.lock, 'acquire'))
        self.release = self.enterContext(mock.patch.object(record_move.lock, 'release'))
        self.migrate = self.enterContext(mock.patch.object(transaction, 'migrate_ledger',
                                                         return_value=transaction.LedgerMoveResult('complete')))
        self.enterContext(mock.patch.dict('os.environ', {'GHOST_ALICE_INSTALL_LOCALE': 'en'}))
        report.configure(tty=False)

    def run_flow(self, answers=('',), cause='install'):
        output = io.StringIO()
        with mock.patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(output):
            code = record_move.run(cause)
        return code, output.getvalue()

    def test_input_choices_and_eof_preserve_default_yes_without_treating_eof_as_yes(self):
        for answers, moved in (([''], True), (['YES'], True), (['no'], False),
                               ([EOFError()], False), (['invalid', 'Y'], True)):
            with self.subTest(answers=answers):
                self.migrate.reset_mock()
                code, output = self.run_flow(answers)
                self.assertEqual(code, 0)
                self.assertEqual(self.migrate.called, moved)
                self.assertIn(str(self.a), output)
                self.assertIn(f'Folder used for installation: {self.ghost / "source"}\n', output)
                self.assertIn(f'Current record location:      {self.a}\n', output)
                self.assertIn(f'New record location:          {self.b}\n', output)

    def test_noninteractive_and_merge_never_request_input_or_migrate(self):
        for interactive, cause in ((False, 'install'), (True, 'post-merge')):
            self.interactive.return_value = interactive
            code, output = self.run_flow([], cause)
            self.assertEqual(code, 0)
            self.assertNotIn('[Y/n]', output)
            self.migrate.assert_not_called()

    def test_writer_reappearing_under_lock_returns_to_wait_without_moving(self):
        locked = False
        observed = iter(('running', 'absent', 'running'))
        last_writer = None
        moved = []
        answers = iter(('', '', 'n'))
        def observe(platforms):
            nonlocal last_writer
            self.assertEqual(set(platforms), {'claude', 'codex'})
            last_writer = next(observed)
            return {'codex': mock.Mock(state=last_writer)}
        def acquire(*args):
            nonlocal locked
            self.assertEqual(last_writer, 'absent')
            locked = True
        def release(handle):
            nonlocal locked
            locked = False
        def move(root, **kwargs):
            self.assertTrue(locked)
            self.assertEqual(last_writer, 'absent')
            moved.append(root)
            return transaction.LedgerMoveResult('complete')
        def answer():
            self.assertFalse(locked, 'The installer must release its lock while waiting for the user')
            return next(answers)
        self.writers.side_effect = observe
        self.acquire.side_effect = acquire
        self.release.side_effect = release
        self.migrate.side_effect = move
        code, output = self.run_flow(answer)
        self.assertEqual(code, 0)
        self.assertFalse(locked)
        self.assertEqual(moved, [])
        self.assertIn(f'records remain at {self.a}', output)
        self.assertIn('Codex app or CLI', output)

    def test_unregistered_unknown_listener_defers_and_releases_no_unacquired_lock(self):
        self.listener.return_value = mock.Mock(state='unknown', detail='state unavailable')
        code, output = self.run_flow(['', 'n'])
        self.assertEqual(code, 0)
        self.assertIn('state unavailable', output)
        self.acquire.assert_not_called()
        self.migrate.assert_not_called()

    def test_changed_binding_under_lock_prevents_migration(self):
        initial = self.bindings
        self.acquire.side_effect = lambda *args: setattr(self, 'bindings', {self.b: initial[self.b]})
        code, output = self.run_flow()
        self.assertEqual(code, 0)
        self.assertIn('bindings changed', output)
        self.migrate.assert_not_called()
        self.release.assert_called_once()

    def test_unknown_host_observation_holds_move_without_claiming_the_app_is_running(self):
        self.writers.return_value = {'claude': mock.Mock(state='unknown', detail='ps unavailable')}
        code, output = self.run_flow(['', 'n'])
        self.assertEqual(code, 0)
        self.assertIn('could not check', output)
        self.assertIn('Diagnostic details: ps unavailable', output)
        self.assertNotIn('Close it', output)
        self.assertNotIn('close it', output)
        self.acquire.assert_not_called()
        self.migrate.assert_not_called()

    def test_surviving_record_cli_prevents_migration_after_native_host_exits(self):
        self.writers.return_value = mock.DEFAULT
        self.bindings[self.b]['hosts'] = {}
        for argv, notice in (
                (['python3', '/runtime/response_route.py', '--platform', 'claude'], 'Claude Code'),
                (['python3', '/runtime/context_lifecycle.py', '--platform', 'claude'], 'Claude Code'),
                (['python3', '/runtime/source_lookup.py', '--platform', 'claude', '--purpose', 'review'], 'Claude Code'),
                (['python3', '/runtime/completion_assessment.py', 'record-response'], 'could not check')):
            with self.subTest(argv=argv), \
                    mock.patch.object(record_move.hosts.sys, 'platform', 'darwin'), \
                    mock.patch.object(record_move.hosts.runner, 'run_command', return_value=ToolResult(0,
                        '10 /Applications/Codex.app/Contents/MacOS/Codex\n11 /usr/bin/python3\n')), \
                    mock.patch.object(record_move.hosts, '_process_argv', return_value=argv):
                code, output = self.run_flow(['', 'n'])
                self.assertEqual(code, 0)
                self.assertIn(f'records remain at {self.a}', output)
                self.assertIn(notice, output)
                self.assertNotIn('are now at', output)
                self.acquire.assert_not_called()
                self.migrate.assert_not_called()

    def test_preparation_exception_preserves_failure_and_processes_next_path(self):
        c = self.ghost / 'other/records'
        self.bindings[c] = dict(hosts={'claude': {}}, source_root=c.parent, state='source-bound')
        self.migrate.side_effect = [OSError('preparation denied'), transaction.LedgerMoveResult('complete')]
        code, output = self.run_flow()
        self.assertEqual(code, 1)
        self.assertIn(f'were not moved and remain at {self.a}', output)
        self.assertIn(f'from {c} are now at {self.b}', output)
        self.assertIn('Diagnostic details: preparation denied', output)

    def test_unavailable_binding_retains_known_notice_but_defers_all_moves(self):
        self.unavailable['codex'] = 'managed hook conflict'
        code, output = self.run_flow([])
        self.assertEqual(code, 0)
        self.assertNotIn('[Y/n]', output)
        self.assertIn(str(self.a), output)
        self.assertIn('host codex', output)
        self.assertIn('Diagnostic details: managed hook conflict', output)
        self.migrate.assert_not_called()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            observed = record_move.read_locations()
        self.assertEqual(observed.roots, self.bindings)
        self.assertEqual(observed.unavailable_hosts, self.unavailable)
        self.assertIn('managed hook conflict', output.getvalue())

    def test_global_location_failure_preserves_install_result_and_never_moves(self):
        self.lookup.side_effect = ValueError('invalid installation config')
        code, output = self.run_flow([])
        self.assertEqual(code, 0)
        self.assertIn('installation is complete', output)
        self.assertIn('was not started', output)
        self.assertIn('Diagnostic details: invalid installation config', output)
        self.assertNotIn('[Y/n]', output)
        self.migrate.assert_not_called()

    def test_global_location_failure_under_lock_defers_and_releases_the_lock(self):
        observed = transaction.LedgerLocations(self.bindings)
        self.lookup.side_effect = [observed, observed, ValueError('invalid installation config')]
        code, output = self.run_flow()
        self.assertEqual(code, 0)
        self.assertIn(f'were not moved and remain at {self.a}', output)
        self.assertIn('Diagnostic details: invalid installation config', output)
        self.migrate.assert_not_called()
        self.release.assert_called_once()

    def test_binding_becoming_unavailable_under_lock_cannot_start_a_move(self):
        self.acquire.side_effect = lambda *args: setattr(self, 'unavailable', {'codex': 'managed hook conflict'})
        code, output = self.run_flow()
        self.assertEqual(code, 0)
        self.assertIn(f'were not moved and remain at {self.a}', output)
        self.migrate.assert_not_called()
        self.release.assert_called_once()

    def test_actual_outcome_selects_exit_and_next_path_without_parsing_error_text(self):
        c = self.ghost / 'other/records'
        self.bindings[c] = dict(hosts={'claude': {}}, source_root=c.parent, state='source-bound')
        complete = transaction.LedgerMoveResult('complete')
        retained = f'{self.ghost / "rollbacks/user-kept"}: unrecognized-publish-entry'
        recovery = str(self.ghost / 'rollbacks/unfinished-move')
        cases = [
            ('complete then deferred', complete, transaction.LedgerMoveResult('deferred', 'second history differs'),
             0, 'are now at', 'were not moved'),
            ('restored then complete', transaction.LedgerMoveResult('restored', 'first copy failed'), complete,
             1, 'restored the previous state', 'are now at'),
            ('retained artifacts then complete', transaction.LedgerMoveResult('complete', retained=(retained,)), complete,
             0, 'are now at', 'are now at'),
            ('cleanup failed then complete', transaction.LedgerMoveResult('cleanup_failed', 'cleanup denied'), complete,
             1, 'could not finish cleaning up', 'are now at'),
            ('restore incomplete', transaction.LedgerMoveResult('restore_incomplete', 'restore denied', (recovery,),
                affected_paths=(str(self.b), str(self.ghost / 'config.json')), phase='prepare'), None,
             1, 'could not finish restoring the previous state', 'was not started'),
            ('resume incomplete', transaction.LedgerMoveResult('resume_incomplete', 'service not ready', (recovery,)), None,
             1, 'Restarting the context gateway', 'was not started'),
            ('mark incomplete', transaction.LedgerMoveResult('mark_incomplete', 'completion write denied', (recovery,)), None,
             1, 'Saving the completion record', 'was not started'),
        ]
        for name, first, second, expected_code, first_message, second_message in cases:
            with self.subTest(outcomes=name):
                self.migrate.reset_mock()
                self.migrate.side_effect = [first] if second is None else [first, second]
                code, output = self.run_flow()
                self.assertEqual(code, expected_code)
                expected_roots = [self.a] if second is None else [self.a, c]
                self.assertEqual(self.migrate.call_args_list,
                                 [mock.call(root, writers_stopped=True) for root in expected_roots])
                result_lines = []
                for root, fragment in ((self.a, first_message), (c, second_message)):
                    matches = [line for line in output.splitlines() if str(root) in line and fragment in line]
                    self.assertEqual(len(matches), 1, output)
                    result_lines.append(matches[0])
                self.assertLess(output.index(result_lines[0]), output.index(result_lines[1]))
                if first.status in ('complete', 'cleanup_failed', 'resume_incomplete', 'mark_incomplete'):
                    self.assertIn(str(self.b), result_lines[0])
                if second is not None and second.status == 'complete':
                    self.assertIn(str(self.b), result_lines[1])
                if first.status == 'restore_incomplete':
                    self.assertIn(f'Recovery records: {recovery}', output)
                    self.assertIn('Journal phase: prepare', output)
                    for path in first.affected_paths:
                        self.assertIn(path, output)
                    self.assertIn('do not delete the destination', output)
                    self.assertIn('only if the unfinished data and configuration have not changed', output)
                if first.retained:
                    notice = 'The installer retained these installation artifacts for the listed reasons:'
                    self.assertIn(notice + '\n' + retained, output)
                    self.assertNotIn('could not finish cleaning up', output)

    def test_unknown_source_relation_is_reported_without_a_question(self):
        self.bindings[self.a]['source_root'] = None
        self.bindings[self.a]['state'] = 'unknown'
        code, output = self.run_flow([])
        self.assertEqual(code, 0)
        self.assertIn('could not establish', output)
        self.assertNotIn('[Y/n]', output)
        self.migrate.assert_not_called()

    def test_terminal_input_boundary_preserves_prompt_answers_and_wait_cancellation(self):
        question = 'Move your existing work records to the new location? [Y/n]'
        cases = ((True, 'n\n', False, False), (True, '\x04', False, False),
                 (False, '\n', False, True), (False, 'Y\n', False, True),
                 (False, '\nn\n', True, False), (False, '\n\x04', True, False))
        for queued, answer, running, moved in cases:
            with self.subTest(queued=queued, answer=repr(answer), running=running):
                script = f'''import select, sys
from pathlib import Path
from installer import record_move, report, transaction
ghost = Path({str(self.ghost)!r})
root = ghost / 'source/records'
record_move.locations.record_root = lambda: ghost
transaction.installed_ledger_locations = lambda: transaction.LedgerLocations({{root: dict(hosts={{'claude': {{}}}}, source_root=root.parent, state='source-bound')}})
record_move.hosts.observe_writers = lambda hosts: {{host: record_move.hosts.WriterObservation({'running' if running else 'absent'!r}) for host in hosts}}
record_move.gateway_participant.ledger_migration_participants = lambda *args: ([], None)
record_move.lock.acquire = lambda *args: None
record_move.lock.release = lambda handle: None
def migrate(*args, **kwargs):
    print('MIGRATION_STARTED', flush=True)
    return transaction.LedgerMoveResult('complete')
transaction.migrate_ledger = migrate
report.configure(tty=False)
if {queued!r}:
    print('BEFORE_RECORD_QUESTION', flush=True)
    assert select.select([sys.stdin], [], [], 5)[0], 'No queued input received'
raise SystemExit(record_move.run('install'))
'''
                env = installer_env(self.ghost)
                env.update(PYTHONPATH=str(Path(__file__).resolve().parent), GHOST_ALICE_INSTALL_LOCALE='en')
                responses = [('BEFORE_RECORD_QUESTION', '\n')] if queued else []
                code, output = run_pty([sys.executable, '-c', script], env=env, cwd=self.ghost,
                                       responses=responses + [(question, answer)], timeout=10)
                self.assertEqual(code, 0, output)
                self.assertEqual('MIGRATION_STARTED' in output, moved, output)
                self.assertIn('are now at' if moved else 'will ask again', output)
