"""The installer switches stopped ledger writers together or restores the prior installation."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

import gateway_testing
import hook_definitions
import install_destination
import install_hooks
import install_transaction
import installed_payload
import ledger_access
import runtime_config
from installer import gateway_participant, transaction
from installer_testing import destination_fixture, installer_env, installer_observations


class LedgerMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.reg = gateway_testing.registered_home(self, self.home)
        self.enterContext(mock.patch.dict(os.environ, installer_env(self.home), clear=True))
        self.enterContext(installer_observations())
        self.a, self.b = self.reg.ledger, self.reg.ghost / 'session-intent'
        self.ledger = ledger_access.load_ledger()
        self.ledger.record_turn(root=self.a, platform='claude', session_id='same-session', raw_input='before migration')
        self.before = self.ledger.read_session_state(root=self.a, platform='claude', session_id='same-session')
        self.value = destination_fixture(self.home)
        self.value.update(ghost_root=str(self.reg.ghost), platform_dir=str(self.reg.claude),
                          ledger_root=str(self.a), runtime_shared_dir=str(self.reg.shared))
        install_hooks.install_hook('claude', destination=self.value)
        # The hook fixture publishes actual runtime code; the service record still owns its state.
        self.effects = gateway_testing.FakeEffects(self.home, running=True, agent=self.reg.agent)
        self.participant = gateway_participant.GatewayParticipant(self.reg.ghost, self.reg.service(),
                                                                  effects=self.effects, platform='darwin')
        self.enterContext(mock.patch.object(gateway_participant, 'participants', return_value=[self.participant]))
        self.enterContext(mock.patch.object(gateway_participant.sys, 'platform', 'darwin'))

    def migrate(self, **kwargs):
        return transaction.migrate_ledger(self.a, writers_stopped=kwargs.pop('writers_stopped', True), **kwargs)

    def test_migration_preserves_history_then_next_same_session_input_uses_b(self):
        import source_reference
        proof = self.a / 'original-proof.txt'
        proof.write_text('Original evidence retained through the move')
        reference = source_reference.reference_source(str(proof))
        for root in (self.a, self.b):
            self.ledger.write_current_session_pointer(root, 'claude', 'same-session')
        original_start = self.effects.start
        def start(domain, agent):
            config = runtime_config.load_config(strict=True)
            self.assertEqual(config['install_locations']['claude']['ledger_root'], str(self.b))
            self.assertIn(str(self.a), config['retired_ledger_roots'])
            self.assertEqual(json.loads(self.reg.state_path.read_bytes())['ledger_root'], str(self.b))
            txid, = install_transaction.unfinished(self.reg.ghost)
            self.assertEqual(install_transaction.Journal.load(self.reg.ghost, txid).phase, 'committed')
            original_start(domain, agent)
        self.effects.start = start
        self.assertEqual(self.migrate().status, 'complete')
        self.assertEqual(self.effects.service_calls(), ['stop', 'start', 'ready'])
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session'), self.before)
        settings = json.loads((self.reg.claude / 'settings.json').read_bytes())
        for groups in settings['hooks'].values():
            for group in groups:
                for handler in group['hooks']:
                    parsed = hook_definitions.parse_hook_runner_command(handler['command'])
                    bound = install_destination.decode_binding(parsed['binding'])['destination']
                    self.assertEqual(bound['ledger_root'], str(self.b))
        self.assertFalse(self.a.exists(), 'The installer must remove the old record folder')
        self.assertEqual(source_reference.source_text(reference, storage_root=self.b),
                         'Original evidence retained through the move')
        self.assertEqual(reference['locator'], str(proof))
        receipt_command = next(handler['command'] for group in settings['hooks']['SessionStart']
                               for handler in group['hooks']
                               if hook_definitions.parse_hook_runner_command(handler['command'])['hook_id'] == 'session-receipt')
        started = subprocess.run(receipt_command, shell=True,
            input=json.dumps({'session_id': 'same-session', 'source': 'startup', 'hook_event_name': 'SessionStart'}),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(started.returncode, 0, started.stderr)
        context = json.loads(started.stdout)['hookSpecificOutput']['additionalContext']
        receipt = json.loads(context.split('[session-intent-receipt]', 1)[1].split('[/session-intent-receipt]', 1)[0])
        self.assertEqual(receipt['ledger_root'], str(self.b))
        self.assertEqual(receipt['session_id'], 'same-session')
        self.ledger.record_turn(root=self.b, platform='claude', session_id='same-session', raw_input='after migration')
        with self.assertRaises(ledger_access.RetiredLedgerRoot):
            self.ledger.record_turn(root=self.a, platform='claude', session_id='same-session', raw_input='stale caller')
        self.assertFalse(self.a.exists())
        self.ledger.record_turn(root=self.home / 'independent', platform='claude', session_id='dev', raw_input='independent')

    def test_missing_stop_confirmation_changes_neither_files_nor_service(self):
        original = self.reg.state_path.read_bytes()
        self.assertEqual(self.migrate(writers_stopped=False).status, 'deferred')
        self.assertFalse(self.b.exists())
        self.assertEqual(self.reg.state_path.read_bytes(), original)
        self.assertEqual(self.effects.calls, [])

    def test_new_move_does_not_delete_source_changes_after_snapshot_validation(self):
        snapshot = self.ledger.snapshot_roots
        proof = self.a / 'changed-after-snapshot.txt'
        def mutate_after_snapshot(*args, **kwargs):
            baseline = snapshot(*args, **kwargs)
            proof.write_text('not copied to B')
            return baseline
        for initially_present in (False, True):
            with self.subTest(initially_present=initially_present):
                if initially_present:
                    proof.write_text('copied content')
                with mock.patch.object(self.ledger, 'snapshot_roots', side_effect=mutate_after_snapshot):
                    result = self.migrate()
                self.assertEqual(result.status, 'restored', result)
                self.assertEqual(proof.read_text(), 'not copied to B')
                self.assertEqual(runtime_config.load_config(strict=True)['install_locations']['claude']['ledger_root'], str(self.a))

    def test_fixed_destination_symlink_cannot_redirect_record_publication(self):
        external = self.home / 'unrelated'
        external.mkdir()
        (external / 'keep.txt').write_text('unrelated data')
        self.b.symlink_to(external, target_is_directory=True)
        before = {path: path.read_bytes() for path in (self.reg.state_path, runtime_config.config_path())}
        with self.assertRaisesRegex(ValueError, 'unlinked destination'):
            self.migrate()
        self.assertTrue(self.b.is_symlink())
        self.assertEqual(list(external.iterdir()), [external / 'keep.txt'])
        self.assertEqual((external / 'keep.txt').read_text(), 'unrelated data')
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(self.effects.calls, [])

    def _assert_destination_change_after_snapshot_is_preserved(self, initially_present):
        snapshot = self.ledger.snapshot_roots
        self.b.mkdir()
        proof = self.b / 'changed-after-snapshot.txt'
        def mutate_after_snapshot(*args, **kwargs):
            baselines = snapshot(*args, **kwargs)
            proof.write_text('B content not copied to the snapshot')
            return baselines
        if initially_present:
            proof.write_text('original B content')
        with mock.patch.object(self.ledger, 'snapshot_roots', side_effect=mutate_after_snapshot):
            result = self.migrate()
        self.assertTrue(proof.exists(), 'B content added after validation must survive the move attempt')
        self.assertEqual(proof.read_text(), 'B content not copied to the snapshot')
        self.assertEqual(result.status, 'restored', result)
        self.assertIn('destination changed', result.detail)
        self.assertTrue(self.a.is_dir())
        self.assertEqual(runtime_config.load_config(strict=True)['install_locations']['claude']['ledger_root'], str(self.a))

    def test_new_move_preserves_destination_addition_after_snapshot_validation(self):
        self._assert_destination_change_after_snapshot_is_preserved(False)

    def test_new_move_preserves_destination_change_after_snapshot_validation(self):
        self._assert_destination_change_after_snapshot_is_preserved(True)

    def test_move_preserves_a_regular_file_at_the_ledger_destination(self):
        data = b'original bytes at the fixed path'
        self.b.write_bytes(data)
        result = self.migrate()
        self.assertTrue(self.b.is_file(), result)
        self.assertEqual(self.b.read_bytes(), data)
        self.assertEqual(result.status, 'restored', result)
        self.assertTrue(self.a.is_dir())

    def test_move_preserves_a_regular_file_created_during_the_snapshot(self):
        data = b'bytes created after enumeration'
        snapshot_files = self.ledger._snapshot_files

        def create_after_enumeration(root):
            files = snapshot_files(root)
            if root == self.b and not self.b.exists():
                self.b.write_bytes(data)
            return files

        with mock.patch.object(self.ledger, '_snapshot_files', side_effect=create_after_enumeration):
            result = self.migrate()
        self.assertTrue(self.b.is_file(), result)
        self.assertEqual(self.b.read_bytes(), data)
        self.assertEqual(result.status, 'restored', result)
        self.assertTrue(self.a.is_dir())

    def test_retention_keeps_multiple_host_causes_out_of_shared_errors(self):
        another = self.home / 'another-retired-ledger'
        another.mkdir()
        observed = transaction.LedgerLocations(
            {root: dict(hosts={'claude': {}}) for root in (self.a, another)},
            retired_roots=(self.a, another))
        retained = transaction.retain_record_source_writers(self.reg.ghost, observed)
        self.assertEqual(retained.errors, {})
        self.assertEqual(set(retained.blocked_hosts['claude']), {str(self.a), str(another)})
        self.assertNotIn('codex', retained.blocked_hosts)
        for cause in retained.blocked_hosts['claude'].values():
            self.assertIn('No completed cleanup journal', cause)

    def test_retention_ignores_ordinary_files_but_reports_broken_journal_candidates(self):
        root = install_transaction.rollbacks_root(self.reg.ghost)
        root.mkdir(exist_ok=True)
        ordinary = root / '.DS_Store'
        ordinary.write_text('unrelated')
        missing = root / 'missing-journal'
        missing.mkdir()
        protected = self.home / 'private-journal-target'
        protected.mkdir()
        linked = root / 'linked-journal'
        linked.symlink_to(protected / 'missing-target')
        observed = transaction.LedgerLocations({self.a: dict(hosts={'claude': {}})}, retired_roots=(self.a,))
        protected.chmod(0)
        try:
            retained = transaction.retain_record_source_writers(self.reg.ghost, observed)
        finally:
            protected.chmod(0o700)
        self.assertNotIn(str(ordinary / install_transaction.JOURNAL_FILENAME), retained.errors)
        self.assertIn(str(missing / install_transaction.JOURNAL_FILENAME), retained.errors)
        self.assertIn(str(linked / install_transaction.JOURNAL_FILENAME), retained.errors)
        self.assertEqual(ordinary.read_text(), 'unrelated')

    @unittest.skipUnless(os.name == 'posix' and os.geteuid() != 0, 'requires POSIX directory permissions')
    def test_inaccessible_journal_entries_report_each_path_and_preserve_host_holds(self):
        root = install_transaction.rollbacks_root(self.reg.ghost)
        entries = [root / 'first', root / 'second']
        for entry in entries:
            entry.mkdir(parents=True)
        observed = transaction.LedgerLocations({self.a: dict(hosts={'claude': {}})}, retired_roots=(self.a,))
        root.chmod(0o400)
        try:
            retained = transaction.retain_record_source_writers(self.reg.ghost, observed)
        finally:
            root.chmod(0o700)
        for entry in entries:
            self.assertIn(str(entry), retained.errors[str(entry / install_transaction.JOURNAL_FILENAME)])
        self.assertIn(str(self.a), retained.blocked_hosts['claude'])

    def test_retention_saves_unknown_hosts_separately_from_observed_writers(self):
        journal = install_transaction.Journal.begin(dict(ghost_root=str(self.reg.ghost), host='installer'), [])
        journal.data['source_cleanup'] = dict(path=str(self.a), destination=str(self.b), baseline={})
        journal.mark('done')
        observed = transaction.LedgerLocations({self.a: dict(hosts={'claude': {}})},
            unavailable_hosts={'codex': 'actual hooks unreadable'}, retired_roots=(self.a,))
        retained = transaction.retain_record_source_writers(self.reg.ghost, observed)
        saved = install_transaction.Journal.load(self.reg.ghost, journal.txid).data['source_cleanup']
        self.assertEqual(saved['writer_hosts'], ['claude'])
        self.assertEqual(saved['unavailable_writer_hosts'], {'codex': 'actual hooks unreadable'})
        self.assertEqual(retained.blocked_hosts, {})

    def test_regular_publish_rejects_unavailable_config_before_changing_provenance(self):
        from types import SimpleNamespace
        config = runtime_config.config_path()
        config.write_text('{')
        context = SimpleNamespace(platform='claude', record_source=dict(
            root=str(self.home), commit='fixture', dirty='clean', hooks_path='hooks'))
        with mock.patch.object(install_transaction, 'publish', return_value='not-published') as publish:
            with self.assertRaisesRegex(ValueError, 'config.json'):
                transaction.publish(context, [])
        publish.assert_not_called()

    def test_inactive_other_host_config_error_does_not_block_shared_move(self):
        for destination in installed_payload.read(self.reg.ghost)['targets']:
            installed_payload.release_target(self.reg.ghost, destination=destination, provider='core', platform='codex')
        runtime_config.save_config({'install_locations': {'codex': 'not-an-object'}})
        result = self.migrate()
        self.assertEqual(result.status, 'complete', result)
        self.assertFalse(self.a.exists())
        self.assertTrue((self.b / 'ghost-state.sqlite3').is_file())
        self.assertEqual(runtime_config.load_config()['install_locations']['codex'], 'not-an-object')

    def test_active_other_host_config_error_defers_shared_move(self):
        runtime_config.save_config({'install_locations': {'claude': 'not-an-object'}})
        before = runtime_config.config_path().read_bytes()
        result = self.migrate()
        self.assertEqual(result.status, 'deferred', result)
        self.assertIn('claude', result.detail)
        self.assertEqual(runtime_config.config_path().read_bytes(), before)
        self.assertTrue(self.a.exists())

    def test_completed_migration_removes_redundant_originals_and_preparation(self):
        self.ledger.record_turn(root=self.a, platform='claude', session_id='same-session', raw_input='last writer closed')
        self.assertFalse((self.a / 'ghost-state.sqlite3-wal').exists())
        self.b.mkdir()
        (self.b / 'existing.txt').write_text('existing B')
        self.assertEqual(self.migrate().status, 'complete')
        self.assertEqual((self.b / 'existing.txt').read_text(), 'existing B')
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session')['latest_input_digest'],
                         self.ledger.text_digest('last writer closed'))
        self.assertEqual(list(install_transaction.rollbacks_root(self.reg.ghost).iterdir()), [])
        self.assertEqual(list(self.reg.ghost.glob('ledger-migration-*')), [])
        self.assertFalse(self.a.exists())

    def test_partial_source_deletion_retries_without_reverting_new_records(self):
        remove = install_transaction._remove_path
        def fail_after_database_delete(path):
            if Path(path) == self.a:
                (self.a / 'ghost-state.sqlite3').unlink()
                raise PermissionError('old record folder deletion denied')
            return remove(path)
        with mock.patch.object(install_transaction, '_remove_path', side_effect=fail_after_database_delete):
            result = self.migrate()
        self.assertEqual(result.status, 'cleanup_failed')
        self.assertTrue(self.a.exists())
        self.assertFalse((self.a / 'ghost-state.sqlite3').exists())
        self.assertEqual(runtime_config.load_config(strict=True)['install_locations']['claude']['ledger_root'], str(self.b))
        self.ledger.record_turn(root=self.b, platform='claude', session_id='same-session', raw_input='after cleanup failure')
        self.assertEqual(install_transaction.cleanup_completed(self.reg.ghost,
            record_source_cleanup=transaction.cleanup_record_source), ())
        self.assertFalse(self.a.exists())
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session')['latest_input_digest'],
                         self.ledger.text_digest('after cleanup failure'))

    def test_next_install_cleans_source_left_by_previous_migration_without_a_journal(self):
        import contextlib
        import io
        from installer import record_move
        with mock.patch.object(install_transaction, 'cleanup_completed', return_value=()):
            self.assertEqual(self.migrate().status, 'cleanup_failed')
        for folder in install_transaction.rollbacks_root(self.reg.ghost).iterdir():
            shutil.rmtree(folder)
        import sqlite3
        with sqlite3.connect(self.a / 'ghost-state.sqlite3') as connection:
            connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        connection.close()
        self.assertFalse((self.a / 'ghost-state.sqlite3-wal').exists())
        self.ledger.record_turn(root=self.b, platform='claude', session_id='same-session', raw_input='new B input')
        import install_lock
        lock_path = self.reg.ghost / 'install.lock'
        install_lock.release_lock(lock_path, json.loads(lock_path.read_text())['generation'])
        extra = self.a / 'not-moved.txt'
        extra.write_text('not in B')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(record_move.source, 'interactive', return_value=False):
            self.assertEqual(record_move.run('install'), 1)
        self.assertEqual(extra.read_text(), 'not in B')
        self.assertIn(str(self.a), output.getvalue())
        self.assertIn(extra.name, output.getvalue())
        extra.unlink()
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(record_move.source, 'interactive', return_value=False):
            self.assertEqual(record_move.run('install'), 0)
        self.assertFalse(self.a.exists())
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session')['latest_input_digest'],
                         self.ledger.text_digest('new B input'))

    def test_changed_source_or_rebound_host_prevents_completed_journal_deletion(self):
        with mock.patch.object(install_transaction, 'cleanup_completed', return_value=()):
            self.assertEqual(self.migrate().status, 'cleanup_failed')
        extra = self.a / 'not-moved.txt'
        extra.write_text('Only the retired location contains this file')
        with self.assertRaisesRegex(install_transaction.InstallTransactionError, 'source changed'):
            install_transaction.cleanup_completed(self.reg.ghost, record_source_cleanup=transaction.cleanup_record_source)
        self.assertEqual(extra.read_text(), 'Only the retired location contains this file')
        extra.unlink()
        install_hooks.install_hook('claude', destination=self.value)
        with self.assertRaisesRegex(install_transaction.InstallTransactionError, 'retired record source'):
            install_transaction.cleanup_completed(self.reg.ghost, record_source_cleanup=transaction.cleanup_record_source)
        self.assertTrue(self.a.exists())
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session'), self.before)

    def test_shared_publish_cleanup_requires_the_callers_record_deletion_policy(self):
        with mock.patch.object(install_transaction, 'cleanup_completed', return_value=()):
            self.assertEqual(self.migrate().status, 'cleanup_failed')
        journal, = (path for path in install_transaction.rollbacks_root(self.reg.ghost).iterdir()
                    if install_transaction.Journal.load(self.reg.ghost, path.name).data.get('source_cleanup'))
        with self.assertRaisesRegex(install_transaction.InstallTransactionError, 'record source cleanup policy'):
            install_transaction.cleanup_completed(self.reg.ghost)
        self.assertTrue(self.a.is_dir())
        self.assertTrue(journal.is_dir())
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session'), self.before)

    def test_source_created_after_retained_history_check_is_not_deleted(self):
        with mock.patch.object(install_transaction, 'cleanup_completed', return_value=()):
            self.assertEqual(self.migrate().status, 'cleanup_failed')
        for folder in install_transaction.rollbacks_root(self.reg.ghost).iterdir():
            shutil.rmtree(folder)
        self.ledger.record_turn(root=self.b, platform='claude', session_id='same-session', raw_input='new B input')
        config = runtime_config.config_path().read_bytes()
        service = self.reg.state_path.read_bytes()
        extra = self.a / 'not-moved.txt'
        relocate = self.ledger.relocate_moved_links

        def changed_after_verification(source, current):
            extra.write_text('Only the retired location contains this file')
            return relocate(source, current)

        with mock.patch.object(self.ledger, 'relocate_moved_links', side_effect=changed_after_verification):
            with self.assertRaisesRegex(install_transaction.InstallTransactionError, 'source changed'):
                transaction.cleanup_retired_ledgers()
        self.assertEqual(extra.read_text(), 'Only the retired location contains this file')
        self.assertFalse((self.b / extra.name).exists())
        self.assertEqual(runtime_config.config_path().read_bytes(), config)
        self.assertEqual(self.reg.state_path.read_bytes(), service)
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session')['latest_input_digest'],
                         self.ledger.text_digest('new B input'))

    def test_retired_folder_cleanup_checks_the_recorded_condition_change_at_the_destination(self):
        owner = dict(platform='claude', session_id='same-session')
        event = self.before['latest_input_event_id']
        first = dict(id='AC1', summary='first check', source='user-said', selectable=True,
                     work_unit_id='work', contract_revision=1)
        second = dict(first, id='AC2', summary='second check')
        self.ledger.record_turn(root=self.a, **owner, expected_input_event_id=event,
                                intent_delta={'acceptance_criteria': [first, second]})
        # An older installer left its original folder after committing the move.
        original = self.home / 'original-copy'
        self.ledger.snapshot_roots(self.a, self.home / 'absent', original, writers_stopped=True)
        self.assertEqual(self.migrate().status, 'complete')
        original.rename(self.a)
        self.ledger.record_turn(root=self.b, **owner, expected_input_event_id=event,
                                intent_delta={'acceptance_criteria': [dict(first, selectable=False)]})
        db = self.ledger.storage_database_path(self.b)
        # Restore only the deliberately damaged test row, so the valid withdrawal stays committed.
        with sqlite3.connect(db) as connection:
            saved = connection.execute("SELECT * FROM current_criteria WHERE criterion_id='AC2'").fetchone()
            connection.execute("DELETE FROM current_criteria WHERE criterion_id='AC2'")
        with self.assertRaisesRegex(ValueError, 'current_criteria'):
            transaction.cleanup_retired_ledgers()
        self.assertTrue(self.a.is_dir())
        with sqlite3.connect(db) as connection:
            placeholders = ','.join('?' for _ in saved)
            connection.execute(f"INSERT INTO current_criteria VALUES ({placeholders})", saved)
            connection.execute("UPDATE current_criteria SET selectable=0 WHERE criterion_id='AC2'")
        with self.assertRaisesRegex(ValueError, 'current_criteria'):
            transaction.cleanup_retired_ledgers()
        self.assertTrue(self.a.is_dir())
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE current_criteria SET selectable=1 WHERE criterion_id='AC2'")
        self.assertEqual(transaction.cleanup_retired_ledgers(), (self.a,))
        self.assertFalse(self.a.exists())
        self.assertEqual(self.ledger.read_session_state(root=self.b, **owner)['acceptance_criteria'],
                         [dict(first, selectable=False), second])

    def test_unreadable_other_host_prevents_shared_destination_move_without_changing_records(self):
        codex = self.home / '.codex'
        codex.mkdir(exist_ok=True)
        (codex / 'hooks.json').write_text('{"hooks": null}')
        original = runtime_config.config_path().read_bytes()
        result = self.migrate()
        self.assertEqual(result.status, 'deferred')
        self.assertIn('codex', result.detail)
        self.assertFalse(self.b.exists())
        self.assertEqual(runtime_config.config_path().read_bytes(), original)
        self.assertEqual(self.effects.calls, [])
        self.assertEqual(self.ledger.read_session_state(root=self.a, platform='claude', session_id='same-session'), self.before)

    def test_unverified_gateway_prevents_snapshot_and_preserves_configuration(self):
        original = runtime_config.config_path().read_bytes()
        journals = list(install_transaction.rollbacks_root(self.reg.ghost).glob('*'))
        with mock.patch.object(gateway_participant, 'ledger_migration_participants', return_value=([], 'unknown listener')):
            result = self.migrate()
        self.assertEqual(result.status, 'deferred')
        self.assertFalse(self.b.exists())
        self.assertEqual(runtime_config.config_path().read_bytes(), original)
        self.assertEqual(self.effects.calls, [])
        self.assertEqual(list(install_transaction.rollbacks_root(self.reg.ghost).glob('*')), journals)

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires process fork')
    def test_process_exit_during_preparation_recovers_without_orphan_snapshot(self):
        record_staged = install_transaction.Journal.record_staged
        pid = os.fork()
        if pid == 0:
            def exit_after_stage(journal, batch):
                record_staged(journal, batch)
                os._exit(75)
            with mock.patch.object(install_transaction.Journal, 'record_staged', exit_after_stage):
                self.migrate()
            os._exit(76)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 75)
        txid, = install_transaction.unfinished(self.reg.ghost)
        def participants(context, states, record):
            result = gateway_participant.recovery_participants(context, states, record)
            for participant in result:
                participant.effects = self.effects
            return result
        install_transaction.recover(self.reg.ghost, txid, participant_factory=participants)
        self.assertEqual(install_transaction.cleanup_completed(self.reg.ghost), ())
        self.assertEqual(list(install_transaction.rollbacks_root(self.reg.ghost).iterdir()), [])
        self.assertEqual(list(self.reg.ghost.glob('ledger-migration-*')), [])
        self.assertFalse(self.b.exists())

    def test_snapshot_conflict_preserves_b_and_resumes_prior_service(self):
        self.b.mkdir()
        (self.a / 'original.txt').write_text('A')
        (self.b / 'original.txt').write_text('B')
        original = runtime_config.config_path().read_bytes()
        for ready, expected in ((True, 'deferred'), (False, 'restore_incomplete')):
            with self.subTest(restored_service_ready=ready):
                self.effects.ready_answers = [ready]
                self.effects.calls.clear()
                result = self.migrate()
                self.assertEqual(result.status, expected)
                self.assertEqual((self.a / 'original.txt').read_text(), 'A')
                self.assertEqual((self.b / 'original.txt').read_text(), 'B')
                self.assertEqual(runtime_config.config_path().read_bytes(), original)
                self.assertEqual(self.effects.service_calls(), ['stop', 'start', 'ready'])
                if not ready:
                    txid, = install_transaction.unfinished(self.reg.ghost)
                    self.assertEqual(result.recovery_paths, (str(install_transaction.rollbacks_root(self.reg.ghost) / txid),))
                    self.assertIn('not ready', result.detail)

    def test_snapshot_validation_and_stage_copy_failures_restore_without_deferral(self):
        self.b.mkdir()
        (self.b / 'retained.txt').write_text('existing B')
        originals = {path: path.read_bytes() for path in (self.reg.state_path, runtime_config.config_path())}
        failures = ((self.ledger, 'snapshot_roots', ValueError('Ledger migration conflict: ordinary validation error')),
                    (install_transaction, '_copy_to_stage', OSError('copy failed')))
        for owner, method, error in failures:
            with self.subTest(error=error), mock.patch.object(owner, method, side_effect=error):
                self.effects.calls.clear()
                result = self.migrate()
            self.assertEqual(result.status, 'restored')
            self.assertIn(str(error), result.detail)
            self.assertEqual(self.ledger.read_session_state(root=self.a, platform='claude', session_id='same-session'), self.before)
            self.assertEqual(sorted(path.name for path in self.b.iterdir()), ['retained.txt'])
            self.assertEqual({path: path.read_bytes() for path in originals}, originals)
            self.assertEqual(self.effects.service_calls(), ['stop', 'start', 'ready'])
            self.assertTrue(self.effects.running)
            self.assertFalse(install_transaction.unfinished(self.reg.ghost))

    def test_record_failure_restores_original_settings_symlink_and_existing_b(self):
        config = self.reg.claude / 'settings.json'
        physical = config.with_name('user-settings.json')
        config.rename(physical)
        config.symlink_to(physical.name)
        originals = {path: path.read_bytes() for path in (physical, self.reg.state_path, runtime_config.config_path())}
        self.b.mkdir()
        (self.b / 'retained.txt').write_text('existing B')
        update = installed_payload.update
        def failed(root, patches, **kwargs):
            if patches.get('service', {}).get('state_digest') != self.reg.service()['state_digest']:
                raise OSError('record failure')
            return update(root, patches, **kwargs)
        with mock.patch.object(installed_payload, 'update', side_effect=failed), \
                mock.patch.object(install_transaction, 'cleanup_completed', return_value=()):
            result = self.migrate()
        self.assertEqual(result.status, 'restored')
        self.assertIn('record failure', result.detail)
        self.assertTrue(config.is_symlink())
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.assertEqual(sorted(path.name for path in self.b.iterdir()), ['retained.txt'])
        self.assertTrue(self.effects.running)

        journal, = (install_transaction.Journal.load(self.reg.ghost, path.name)
                    for path in install_transaction.rollbacks_root(self.reg.ghost).iterdir()
                    if install_transaction.Journal.load(self.reg.ghost, path.name).data.get('source_cleanup'))
        self.assertEqual(journal.phase, 'restored')
        self.assertEqual(transaction.cleanup_retired_ledgers(), ())
        self.assertEqual(self.ledger.read_session_state(root=self.a, platform='claude', session_id='same-session'), self.before)
        self.assertEqual(self.migrate().status, 'complete')
        self.assertEqual((self.b / 'retained.txt').read_text(), 'existing B')

    def test_restart_failure_keeps_committed_b_and_new_records(self):
        original_start = self.effects.start
        def start(domain, agent):
            original_start(domain, agent)
            self.ledger.record_turn(root=self.b, platform='claude', session_id='same-session', raw_input='new write')
        self.effects.start = start
        self.effects.ready_answers = [False]
        self.assertEqual(self.migrate().status, 'resume_incomplete')
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session')['latest_input_digest'],
                         self.ledger.text_digest('new write'))
        self.assertIn(str(self.a), runtime_config.load_config(strict=True)['retired_ledger_roots'])
        txid, = install_transaction.unfinished(self.reg.ghost)
        self.assertEqual(install_transaction.Journal.load(self.reg.ghost, txid).phase, 'committed')
        self.assertIn(txid, ' '.join(install_transaction.cleanup_completed(self.reg.ghost)))
        self.effects.ready_answers = [False]
        with self.assertRaisesRegex(install_transaction.InstallTransactionError, 'not ready') as caught:
            self.recover(txid)
        self.assertNotEqual(caught.exception.outcome, 'restored')
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [txid])
        self.effects.ready_answers = [True]
        self.recover(txid)
        self.assertFalse(install_transaction.unfinished(self.reg.ghost))
        self.assertEqual(self.effects.service_calls().count('start'), 1)
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session')['latest_input_digest'],
                         self.ledger.text_digest('new write'))

    def test_done_marker_write_failure_keeps_committed_b_and_running_service(self):
        save = install_transaction.Journal.save
        def fail_done(journal):
            if journal.phase == 'done':
                raise OSError('done marker write failed')
            return save(journal)
        with mock.patch.object(install_transaction.Journal, 'save', fail_done):
            result = self.migrate()
        self.assertEqual(result.status, 'mark_incomplete')
        self.assertIn('done marker write failed', result.detail)
        txid, = install_transaction.unfinished(self.reg.ghost)
        journal = install_transaction.Journal.load(self.reg.ghost, txid)
        self.assertEqual(journal.phase, 'committed')
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session'), self.before)
        config = runtime_config.load_config(strict=True)
        self.assertEqual(config['install_locations']['claude']['ledger_root'], str(self.b))
        self.assertIn(str(self.a), config['retired_ledger_roots'])
        self.assertEqual(json.loads(self.reg.state_path.read_bytes())['ledger_root'], str(self.b))
        self.assertTrue(self.effects.running)
        self.assertEqual(self.effects.service_calls(), ['stop', 'start', 'ready'])

    def test_cleanup_warning_returns_actual_retained_paths_after_completed_move(self):
        root = install_transaction.rollbacks_root(self.reg.ghost)
        root.mkdir(parents=True, exist_ok=True)
        retained = root / 'user-kept.txt'
        retained.write_text('preserve this unrelated file')
        result = self.migrate()
        self.assertEqual(result.status, 'complete')
        self.assertEqual(retained.read_text(), 'preserve this unrelated file')
        self.assertEqual(result.retained, (f'{retained}: unrecognized-publish-entry',))
        self.assertEqual(list(root.iterdir()), [retained])
        self.assertFalse(install_transaction.unfinished(self.reg.ghost))
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session'), self.before)
        self.assertEqual(runtime_config.load_config(strict=True)['install_locations']['claude']['ledger_root'], str(self.b))
        self.assertTrue(self.effects.running)

    def test_cleanup_deletion_failure_returns_error_without_reverting_committed_b(self):
        remove = install_transaction._remove_path
        failed = []
        def deny_original(path):
            path = Path(path)
            if path.name.startswith('rollback-'):
                failed.append(path)
                raise PermissionError(13, 'cleanup deletion denied', str(path))
            return remove(path)
        with mock.patch.object(install_transaction, '_remove_path', side_effect=deny_original):
            result = self.migrate()
        self.assertEqual(result.status, 'cleanup_failed')
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].exists())
        self.assertIn(str(failed[0]), result.detail)
        self.assertIn('cleanup deletion denied', result.detail)
        self.assertEqual(result.recovery_paths, ())
        self.assertEqual(install_transaction.Journal.load(self.reg.ghost, failed[0].parent.name).phase, 'done')
        self.assertFalse(install_transaction.unfinished(self.reg.ghost))
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session'), self.before)
        config = runtime_config.load_config(strict=True)
        self.assertEqual(config['install_locations']['claude']['ledger_root'], str(self.b))
        self.assertIn(str(self.a), config['retired_ledger_roots'])
        self.assertTrue(self.effects.running)
        self.assertEqual(self.effects.service_calls(), ['stop', 'start', 'ready'])

    def recover(self, txid):
        def participants(context, states, record):
            result = gateway_participant.recovery_participants(context, states, record)
            for participant in result:
                participant.effects = self.effects
            return result
        return install_transaction.recover(self.reg.ghost, txid, participant_factory=participants)

    def interrupted_resume(self, point):
        running = self.home / 'service-running'
        start, mark = self.effects.start, install_transaction.Journal.mark
        def interrupted_start(domain, agent):
            if point == 'before-start':
                os._exit(75)
            start(domain, agent)
            running.touch()
            self.ledger.record_turn(root=self.b, platform='claude', session_id='same-session', raw_input='resumed write')
            if point == 'after-start':
                os._exit(75)
        def interrupted_mark(journal, phase):
            if point == 'after-ready' and phase == 'done':
                os._exit(75)
            mark(journal, phase)
        pid = os.fork()
        if pid == 0:
            self.effects.start = interrupted_start
            with mock.patch.object(install_transaction.Journal, 'mark', interrupted_mark):
                self.migrate()
            os._exit(76)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 75)
        self.effects.running = running.exists()
        txid, = install_transaction.unfinished(self.reg.ghost)
        record = installed_payload.read(self.reg.ghost)
        self.assertEqual(install_transaction.record_after_recovery(self.reg.ghost), record)
        state = self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session')
        self.recover(txid)
        self.assertTrue(self.effects.running)
        self.assertEqual(self.effects.service_calls().count('start'), int(point == 'before-start'))
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session'), state)
        if point != 'before-start':
            self.assertEqual(state['latest_input_digest'], self.ledger.text_digest('resumed write'))
        self.assertEqual(installed_payload.read(self.reg.ghost)['service']['state_digest'], record['service']['state_digest'])
        self.assertIn(str(self.a), runtime_config.load_config(strict=True)['retired_ledger_roots'])
        self.assertEqual(runtime_config.load_config(strict=True)['install_locations']['claude']['ledger_root'], str(self.b))
        self.assertFalse(install_transaction.unfinished(self.reg.ghost))
        calls = list(self.effects.calls)
        self.recover(txid)
        self.assertEqual(self.effects.calls, calls)

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires process fork')
    def test_process_exit_after_commit_before_service_start_resumes_b(self):
        self.interrupted_resume('before-start')

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires process fork')
    def test_process_exit_after_service_start_preserves_new_b_write(self):
        self.interrupted_resume('after-start')

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires process fork')
    def test_process_exit_after_readiness_preserves_current_service_record(self):
        self.interrupted_resume('after-ready')

    def test_customized_managed_launcher_holds_migration_before_service_effects(self):
        settings_path = self.reg.claude / 'settings.json'
        original = json.loads(settings_path.read_text())
        for prefix in ('true && ', 'env CUSTOM=1 '):
            with self.subTest(prefix=prefix):
                settings = json.loads(json.dumps(original))
                handler = settings['hooks']['SessionStart'][0]['hooks'][0]
                handler['command'] = prefix + handler['command']
                settings_path.write_text(json.dumps(settings))
                before = settings_path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'customized launcher'):
                    self.migrate()
                self.assertEqual(settings_path.read_bytes(), before)
                self.assertFalse(self.b.exists())
                self.assertEqual(self.effects.calls, [])

    def test_interrupted_publication_recovers_linked_gateway_state_and_service(self):
        physical = self.home / 'external-state.json'
        self.reg.state_path.rename(physical)
        self.reg.state_path.symlink_to(physical)
        before = physical.read_bytes()
        with mock.patch.object(installed_payload, 'update', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.migrate()
        txid, = install_transaction.unfinished(self.reg.ghost)
        def participants(context, states, record):
            result = gateway_participant.recovery_participants(context, states, record)
            for participant in result:
                participant.effects = self.effects
            return result
        install_transaction.recover(self.reg.ghost, txid, participant_factory=participants)
        self.assertTrue(self.reg.state_path.is_symlink())
        self.assertEqual(physical.read_bytes(), before)
        self.assertTrue(self.effects.running)
        self.assertFalse(self.b.exists())

    def test_gateway_already_using_b_stops_before_b_is_replaced(self):
        from context_gateway_model import digest
        state = json.loads(self.reg.state_path.read_bytes())
        state['ledger_root'] = str(self.b)
        self.reg.state_path.write_text(json.dumps(state))
        service = dict(self.reg.service(), state_digest=digest(self.reg.state_path.read_bytes()))
        installed_payload.update(self.reg.ghost, {'service': service})
        self.participant.service = service
        snapshot = self.ledger.snapshot_roots
        def while_stopped(*args, **kwargs):
            self.assertFalse(self.effects.running, 'B has a gateway writer too')
            return snapshot(*args, **kwargs)
        with mock.patch.object(self.ledger, 'snapshot_roots', side_effect=while_stopped):
            self.migrate()
        self.assertEqual(self.effects.service_calls(), ['stop', 'start', 'ready'])


if __name__ == '__main__':
    unittest.main()
