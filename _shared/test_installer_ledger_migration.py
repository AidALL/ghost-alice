"""The installer switches stopped ledger writers together or restores the prior installation."""
import json
import os
from pathlib import Path
import shutil
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
from installer_testing import destination_fixture


class LedgerMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.reg = gateway_testing.registered_home(self, self.home)
        self.enterContext(mock.patch.dict(os.environ, {
            'HOME': str(self.home), 'GHOST_ALICE_GHOST_ROOT': str(self.reg.ghost),
        }))
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

    def migrate(self, **kwargs):
        return transaction.migrate_ledger(self.a, writers_stopped=kwargs.pop('writers_stopped', True), **kwargs)

    def test_migration_preserves_history_then_next_same_session_input_uses_b(self):
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
        self.migrate()
        self.assertEqual(self.effects.service_calls(), ['stop', 'start', 'ready'])
        self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='same-session'), self.before)
        settings = json.loads((self.reg.claude / 'settings.json').read_bytes())
        for groups in settings['hooks'].values():
            for group in groups:
                for handler in group['hooks']:
                    parsed = hook_definitions.parse_hook_runner_command(handler['command'])
                    bound = install_destination.decode_binding(parsed['binding'])['destination']
                    self.assertEqual(bound['ledger_root'], str(self.b))
        shutil.rmtree(self.a)
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
        with self.assertRaisesRegex(ValueError, 'writers'):
            self.migrate(writers_stopped=False)
        self.assertFalse(self.b.exists())
        self.assertEqual(self.reg.state_path.read_bytes(), original)
        self.assertEqual(self.effects.calls, [])

    def test_completed_migration_removes_redundant_originals_and_preparation(self):
        self.b.mkdir()
        (self.b / 'existing.txt').write_text('existing B')
        self.migrate()
        self.assertEqual((self.b / 'existing.txt').read_text(), 'existing B')
        self.assertEqual(list(install_transaction.rollbacks_root(self.reg.ghost).iterdir()), [])
        self.assertEqual(list(self.reg.ghost.glob('ledger-migration-*')), [])

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
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.migrate()
        self.assertEqual((self.a / 'original.txt').read_text(), 'A')
        self.assertEqual((self.b / 'original.txt').read_text(), 'B')
        self.assertEqual(runtime_config.config_path().read_bytes(), original)
        self.assertEqual(self.effects.service_calls(), ['stop', 'start', 'ready'])

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
        with mock.patch.object(installed_payload, 'update', side_effect=failed):
            with self.assertRaisesRegex(OSError, 'record failure'):
                self.migrate()
        self.assertTrue(config.is_symlink())
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.assertEqual(sorted(path.name for path in self.b.iterdir()), ['retained.txt'])
        self.assertTrue(self.effects.running)

    def test_restart_failure_keeps_committed_b_and_new_records(self):
        original_start = self.effects.start
        def start(domain, agent):
            original_start(domain, agent)
            self.ledger.record_turn(root=self.b, platform='claude', session_id='same-session', raw_input='new write')
        self.effects.start = start
        self.effects.ready_answers = [False]
        with self.assertRaisesRegex(RuntimeError, 'committed'):
            self.migrate()
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
