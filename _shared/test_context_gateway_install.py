"""The gateway registration command: it writes the three service files from the install record's runtime
target, starts the service, confirms readiness and writes the record's `service` section. It copies no
module. A refresh keeps the listener's port and scope when the service files are as the record says, and
stops before any change when they are not.
"""
import contextlib
import io
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_gateway_install as install  # noqa: E402
import context_gateway_plan as planning  # noqa: E402
import gateway_testing  # noqa: E402
import installed_payload  # noqa: E402
from context_gateway_model import GatewayBlocked, LABEL, digest  # noqa: E402
from hook_definitions import PROVIDER  # noqa: E402


class ContextGatewayInstallTests(unittest.TestCase):
    def installation_fixture(self, *, service=True):
        """A registered (or, without `service`, a Core-only) home; returns the command's arguments and the
        bytes of every file the registration may touch."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        home = patch.object(install.Path, 'home', return_value=root)
        home.start(); self.addCleanup(home.stop)
        # These fixtures simulate a capable gateway interpreter as well as macOS/launchctl.
        capability = patch.object(install, '_require_gateway_runtime', return_value=None, create=True)
        capability.start(); self.addCleanup(capability.stop)
        self.capability_patch = capability
        self.reg = gateway_testing.registered_home(self, root, service=service)
        args = (self.reg.codex, self.reg.ledger, self.reg.state_dir, self.reg.agent)
        return args, self.snapshot()

    def snapshot(self):
        reg = self.reg
        paths = [reg.config, installed_payload.path(reg.ghost), *reg.runtime_root.rglob('*')]
        paths.extend(p for p in (reg.state_path, reg.agent) if p.exists())
        return {p: p.read_bytes() for p in paths if p.is_file()}

    def mocked_install(self, args, *, running=False, **kwargs):
        active = [running]
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install, '_service_state', side_effect=lambda _: 'running' if active[0] else 'absent'), \
             patch.object(install, '_start_service', side_effect=lambda *_: active.__setitem__(0, True)), \
             patch.object(install, '_stop_service', side_effect=lambda _: active.__setitem__(0, False)), \
             patch.object(install, '_ready', return_value=True):
            return install.install(*args, **kwargs)

    def service(self):
        return self.reg.service()

    # Registration reads the record and copies nothing.

    def test_registration_without_a_runtime_target_stops_before_any_change(self):
        args, original = self.installation_fixture(service=False)
        installed_payload.update(self.reg.ghost, {"targets": {}})
        record = installed_payload.read(self.reg.ghost)
        record["targets"] = {}
        installed_payload.path(self.reg.ghost).write_text(json.dumps(record), encoding='utf-8')
        with patch.object(install, '_service_state') as service, patch.object(install, '_write') as write:
            with self.assertRaisesRegex(ValueError, 'Install Core first') as blocked:
                self.mocked_install(args)
        self.assertEqual(blocked.exception.exit_code, 2)
        service.assert_not_called(); write.assert_not_called()
        self.assertFalse(self.reg.agent.exists())
        self.assertFalse(self.reg.state_path.exists())
        self.assertEqual(self.reg.config.read_text(encoding='utf-8'), 'model="native"\n')

    def test_registration_copies_no_module_and_takes_no_shared_dir(self):
        args, _ = self.installation_fixture(service=False)
        runtime_before = {p: (p.read_bytes(), p.stat().st_ino) for p in self.reg.runtime_root.rglob('*') if p.is_file()}
        result = self.mocked_install(args)
        self.assertEqual(result['status'], 'installed')
        self.assertEqual({p: (p.read_bytes(), p.stat().st_ino) for p in self.reg.runtime_root.rglob('*') if p.is_file()},
                         runtime_before)
        self.assertNotIn('files', result)
        self.assertNotIn('installed_files', result)
        for flag in ('--shared-dir', '--source-root'):
            with self.subTest(flag=flag):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
                    install.main([flag, str(self.reg.shared), '--codex-dir', str(self.reg.codex),
                                  '--ledger-root', str(self.reg.ledger), '--state-dir', str(self.reg.state_dir),
                                  '--launch-agent', str(self.reg.agent)])
                self.assertEqual(stop.exception.code, 2)
                self.assertIn('unrecognized arguments', stderr.getvalue())

    def test_a_first_registration_writes_the_service_files_and_the_record(self):
        args, _ = self.installation_fixture(service=False)
        with patch.object(install.socket, 'socket') as listener:
            listener.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 23456)
            result = self.mocked_install(args)
        self.assertEqual((result['status'], result['service'], result['listener_ready']), ('installed', LABEL, True))
        state = json.loads(self.reg.state_path.read_bytes())
        self.assertEqual((state['port'], state['ledger_root'], state['codex_dir']),
                         (23456, str(self.reg.ledger), str(self.reg.codex)))
        plist = plistlib.loads(self.reg.agent.read_bytes())
        self.assertEqual(plist['ProgramArguments'][1:], ['-B', str(self.reg.program), '--config', str(self.reg.state_path)])
        config = tomllib.loads(self.reg.config.read_text(encoding='utf-8'))
        self.assertEqual(config['model_provider'], PROVIDER)
        self.assertEqual(config['model_providers'][PROVIDER]['base_url'], 'http://127.0.0.1:23456/codex')
        section = self.service()
        expected = gateway_testing.service_section(self.reg)
        for key in ('registered_at', 'restarted_at', 'config_backup'):
            self.assertIsNotNone(section[key])
            expected[key] = section[key]
        self.assertEqual(section, expected)
        self.assertEqual(section['registered_at'], section['restarted_at'])
        self.assertTrue(Path(section['config_backup']).is_file())
        self.assertFalse((self.reg.state_dir / 'installation.json').exists())
        self.assertFalse((self.reg.ghost / 'context-gateway-binding.json').exists())

    def test_unchanged_service_files_refresh_with_the_same_listener_and_the_original_registration_time(self):
        args, original = self.installation_fixture()
        before = self.service()
        config = self.reg.config
        config.write_text(config.read_text(encoding='utf-8') + '\n[features]\nhooks=true\n', encoding='utf-8')
        installed_payload.update(self.reg.ghost, {"service": gateway_testing.service_section(self.reg)})
        before = self.service()
        with patch.object(install.socket, 'socket') as listener:
            result = self.mocked_install(args, running=True)
        listener.assert_not_called()
        self.assertEqual(result['status'], 'installed')
        state = json.loads(self.reg.state_path.read_bytes())
        self.assertEqual((state['port'], state['scope']), (gateway_testing.PORT, gateway_testing.SCOPE))
        after = self.service()
        self.assertEqual(after['registered_at'], before['registered_at'])
        self.assertNotEqual(after['restarted_at'], before['restarted_at'])
        self.assertEqual(after['launch_agent_digest'], digest(self.reg.agent.read_bytes()))
        self.assertEqual(after['state_digest'], digest(self.reg.state_path.read_bytes()))
        self.assertEqual(tomllib.loads(config.read_text(encoding='utf-8'))['features'], {'hooks': True})
        self.assertEqual(planning.remove_provider(config.read_text(encoding='utf-8')),
                         planning.remove_provider(original[config].decode()) + '\n[features]\nhooks=true\n')

    def test_changed_service_files_stop_the_refresh_before_any_change(self):
        mutations = {
            'launch-agent': lambda reg: reg.agent.write_bytes(b'user-edited service must stay'),
            'state': lambda reg: reg.state_path.write_text(json.dumps(dict(json.loads(reg.state_path.read_bytes()),
                                                                           scope='user-edited')), encoding='utf-8'),
            'provider-block': lambda reg: reg.config.write_text(
                reg.config.read_text(encoding='utf-8').replace('wire_api = "responses"', 'wire_api = "user-choice"'),
                encoding='utf-8'),
            'missing-closing-marker': lambda reg: reg.config.write_text(
                reg.config.read_text(encoding='utf-8').replace(install.TABLE_END, '# user removed the marker'),
                encoding='utf-8'),
            'missing-agent': lambda reg: reg.agent.unlink(),
        }
        for name, mutate in mutations.items():
            with self.subTest(change=name):
                args, _ = self.installation_fixture()
                mutate(self.reg)
                before = self.snapshot()
                with patch.object(install, '_service_state') as service, \
                     patch.object(install, '_write', wraps=install._write) as write, \
                     patch.object(install.socket, 'socket') as listener:
                    with self.assertRaises(ValueError) as blocked:
                        self.mocked_install(args)
                self.assertEqual(getattr(blocked.exception, 'exit_code', 2), 2)
                service.assert_not_called(); write.assert_not_called(); listener.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_other_coordinates_than_the_registered_ones_stop_the_refresh(self):
        args, _ = self.installation_fixture()
        other = self.reg.agent.with_name('other-agent.plist')
        other.write_bytes(self.reg.agent.read_bytes())
        before = self.snapshot()
        with patch.object(install, '_service_state') as service:
            with self.assertRaisesRegex(ValueError, 'registered gateway'):
                self.mocked_install((*args[:3], other))
        service.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_a_service_file_without_a_record_entry_is_preserved(self):
        for change in ('launch-agent', 'state-entries', 'provider-markers'):
            with self.subTest(change=change):
                args, _ = self.installation_fixture(service=False)
                reg = self.reg
                if change == 'launch-agent':
                    reg.agent.write_bytes(b'unrelated user-managed launch agent')
                elif change == 'state-entries':
                    (reg.state_dir / 'state.json').write_text('{"schema_version": "context-gateway.v1"}', encoding='utf-8')
                else:
                    reg.config.write_text(planning.render_provider('model="native"\n', 1, 'x'), encoding='utf-8')
                before = self.snapshot()
                with patch.object(install, '_service_state') as service, patch.object(install, '_write') as write:
                    with self.assertRaises(ValueError):
                        self.mocked_install(args)
                service.assert_not_called(); write.assert_not_called()
                self.assertEqual(self.snapshot(), before)
                self.assertIsNone(self.service())

    def test_linked_service_files_are_preserved_before_all_changes(self):
        for name in ('config', 'state.json', 'usage.jsonl', 'service.log', 'agent'):
            with self.subTest(name=name):
                args, _ = self.installation_fixture()
                reg = self.reg
                target = {'config': reg.config, 'agent': reg.agent}.get(name, reg.state_dir / name)
                destination = reg.root / ('user-owned-' + name)
                destination.write_bytes(target.read_bytes())
                target.unlink(); target.symlink_to(destination)
                before = target.lstat()
                raw = destination.read_bytes()
                with patch.object(install, '_service_state') as service, patch.object(install, '_write') as write:
                    with self.assertRaisesRegex(ValueError, '[Ss]ymlink'):
                        self.mocked_install(args)
                service.assert_not_called(); write.assert_not_called()
                after = target.lstat()
                self.assertEqual((target.readlink(), after.st_ino, after.st_mtime_ns),
                                 (destination, before.st_ino, before.st_mtime_ns))
                self.assertEqual(destination.read_bytes(), raw)

    def test_a_dangling_codex_config_link_is_preserved(self):
        args, _ = self.installation_fixture()
        config = self.reg.config
        target = self.reg.codex / 'user-managed.toml'
        config.rename(target); target.unlink(); config.symlink_to(target.name)
        with patch.object(install, '_service_state') as service:
            with self.assertRaisesRegex(ValueError, '[Ss]ymlinked Codex config'):
                self.mocked_install(args)
        service.assert_not_called()
        self.assertTrue(config.is_symlink())

    # Failures restore the service files, the record and the prior service.

    def test_a_failed_readiness_restores_the_files_the_record_and_the_prior_service(self):
        args, original = self.installation_fixture()
        before = self.service()
        active, starts = [True], []

        def start(*_args):
            active[0] = True
            starts.append(json.loads(self.reg.state_path.read_bytes())['scope'])

        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install, '_service_state', side_effect=lambda _: 'running' if active[0] else 'absent'), \
             patch.object(install, '_stop_service', side_effect=lambda _: active.__setitem__(0, False)), \
             patch.object(install, '_start_service', side_effect=start), \
             patch.object(install, '_ready', side_effect=[False, True]):
            with self.assertRaises(RuntimeError) as failed:
                install.install(*args)
        self.assertEqual((failed.exception.exit_code, failed.exception.code), (4, 'GATEWAY_OPERATION_RESTORED'))
        self.assertTrue(active[0])
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.service(), before)
        self.assertEqual(len(starts), 2)

    def test_a_failed_record_write_restores_the_files_and_the_prior_service(self):
        args, original = self.installation_fixture()
        before = self.service()
        active = [True]
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install, '_service_state', side_effect=lambda _: 'running' if active[0] else 'absent'), \
             patch.object(install, '_stop_service', side_effect=lambda _: active.__setitem__(0, False)), \
             patch.object(install, '_start_service', side_effect=lambda *_: active.__setitem__(0, True)), \
             patch.object(install, '_ready', return_value=True), \
             patch.object(install, '_record', side_effect=OSError('record is unwritable')):
            with self.assertRaises(RuntimeError) as failed:
                install.install(*args)
        self.assertEqual(failed.exception.exit_code, 4)
        self.assertTrue(active[0])
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.service(), before)

    def test_a_failed_first_registration_restores_the_empty_state_and_the_record(self):
        for existing_empty_state in (False, True):
            for failure in ('probe', 'bootstrap', 'readiness', 'record'):
                with self.subTest(existing_empty_state=existing_empty_state, failure=failure):
                    args, _ = self.installation_fixture(service=False)
                    reg = self.reg
                    if not existing_empty_state:
                        import shutil
                        shutil.rmtree(reg.state_dir)
                    paths = set(reg.root.rglob('*'))
                    original = self.snapshot()
                    record_before = installed_payload.path(reg.ghost).read_bytes()
                    active = [False]

                    def service(_domain):
                        if failure == 'probe':
                            raise RuntimeError('injected probe failure')
                        return 'running' if active[0] else 'absent'

                    def start(*_args):
                        if failure == 'bootstrap':
                            raise RuntimeError('injected bootstrap failure')
                        active[0] = True

                    def record(section):
                        if failure == 'record':
                            raise OSError('injected record failure')
                        return install._record_service(section)

                    with patch.object(install.sys, 'platform', 'darwin'), \
                         patch.object(install, '_service_state', side_effect=service), \
                         patch.object(install, '_start_service', side_effect=start), \
                         patch.object(install, '_stop_service', side_effect=lambda _: active.__setitem__(0, False)), \
                         patch.object(install, '_ready', return_value=failure != 'readiness'), \
                         patch.object(install, '_record', side_effect=record):
                        with self.assertRaises((RuntimeError, OSError)):
                            install.install(*args)
                    self.assertFalse(active[0])
                    self.assertEqual(set(reg.root.rglob('*')), paths)
                    self.assertEqual(self.snapshot(), original)
                    self.assertEqual(installed_payload.path(reg.ghost).read_bytes(), record_before)
                    self.assertIsNone(self.service())
                    self.assertEqual(self.mocked_install(args)['status'], 'installed')

    def test_a_first_registration_preserves_an_unowned_live_service(self):
        args, original = self.installation_fixture(service=False)
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install, '_service_state', return_value='running'), \
             patch.object(install, '_stop_service') as stop, patch.object(install, '_start_service') as start, \
             patch.object(install, '_write') as write:
            with self.assertRaises(ValueError):
                install.install(*args)
        stop.assert_not_called(); start.assert_not_called(); write.assert_not_called()
        self.assertEqual(self.snapshot(), original)

    def test_a_dry_run_prints_the_plan_and_changes_nothing(self):
        args, original = self.installation_fixture()
        result = self.mocked_install(args, running=True, confirm=False)
        self.assertEqual((result['status'], result['action']), ('dry-run', 'would-install'))
        self.assertEqual([step['phase'] for step in result['plan']['steps']],
                         ['prepare', 'stop', 'verify-absence', 'stage', 'bootstrap', 'readiness', 'commit', 'retire'])
        self.assertEqual(self.snapshot(), original)

    def test_bootstrap_failure_restores_prior_artifacts_and_service(self):
        args, original = self.installation_fixture()
        active, boots = [True], []

        def run(command, **_kwargs):
            operation = command[1]
            if operation == 'print':
                code = 0 if active[0] else 113
            elif operation == 'bootout':
                active[0], code = False, 0
            else:
                boots.append(self.reg.state_path.read_bytes())
                code = 5 if len(boots) == 1 else 0
                active[0] = not code
            output = f'path = {self.reg.agent}\n'.encode() if operation == 'print' and code == 0 else b''
            return subprocess.CompletedProcess(command, code, output, b'')

        with patch.object(install.sys, 'platform', 'darwin'), patch.object(install.subprocess, 'run', run), \
             patch.object(install, '_ready', return_value=True), \
             patch.object(install.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"status":"ready"}')):
            with self.assertRaises(RuntimeError):
                install.install(*args)
        self.assertEqual(self.snapshot(), original)
        self.assertTrue(active[0])
        self.assertEqual(len(boots), 2)
        self.assertEqual(boots[1], original[self.reg.state_path])

    def test_failed_stop_prevents_file_replacement_and_start(self):
        args, original = self.installation_fixture()
        starts = []

        def run(command, **_kwargs):
            code = 5 if command[1] == 'bootout' else 0
            if command[1] == 'bootstrap':
                starts.append(command)
            output = f'path = {self.reg.agent}\n'.encode() if command[1] == 'print' and code == 0 else b''
            return subprocess.CompletedProcess(command, code, output, b'')

        with patch.object(install.sys, 'platform', 'darwin'), patch.object(install.subprocess, 'run', run), \
             patch.object(install, '_ready', return_value=True):
            with self.assertRaises(RuntimeError) as failed:
                install.install(*args)
        self.assertEqual(failed.exception.exit_code, 3)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(starts, [])

    def test_stop_waits_for_observed_absence_before_returning(self):
        with patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(install, '_service_state', side_effect=['running', 'absent']) as observe, \
             patch.object(install.time, 'sleep'):
            install._stop_service('gui/fixture')
        self.assertEqual(observe.call_count, 2)

    # Capability and coordinates.

    def test_python_311_is_rejected_before_gateway_side_effects(self):
        args, original = self.installation_fixture()
        self.capability_patch.stop()
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install.sys, 'version_info', (3, 11, 0)), \
             patch.object(install, '_service_state') as service:
            with self.assertRaisesRegex(RuntimeError, 'Python 3.14'):
                install.install(*args)
        service.assert_not_called()
        self.assertEqual(self.snapshot(), original)

    def test_missing_zstd_is_rejected_before_service_or_file_mutations(self):
        args, original = self.installation_fixture()
        self.capability_patch.stop()
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install.sys, 'version_info', (3, 14, 0)), \
             patch.dict('sys.modules', {'compression': None}), \
             patch.object(install, '_service_state') as service:
            with self.assertRaisesRegex(RuntimeError, 'compression.zstd'):
                install.install(*args)
        service.assert_not_called()
        self.assertEqual(self.snapshot(), original)

    @unittest.skipUnless(sys.version_info >= (3, 14), 'real Python 3.14 codec capability')
    def test_actual_python_314_supports_required_request_codec(self):
        install._require_gateway_runtime()

    def test_a_state_dir_inside_the_runtime_root_is_rejected_before_any_change(self):
        args, original = self.installation_fixture(service=False)
        nested = self.reg.runtime_root / 'gateway-state'
        with patch.object(install, '_service_state') as service:
            with self.assertRaisesRegex(ValueError, 'outside the Core runtime'):
                self.mocked_install((*args[:2], nested, args[3]))
        service.assert_not_called()
        self.assertFalse(nested.exists())
        self.assertEqual(self.snapshot(), original)

    def test_a_state_dir_that_links_into_the_runtime_root_is_rejected_before_any_change(self):
        # The registration writes through a link, so the containment check follows it; the link itself stays.
        for shape in ('linked-state-dir', 'linked-ancestor'):
            with self.subTest(shape=shape):
                args, original = self.installation_fixture(service=False)
                inside = self.reg.runtime_root / 'gateway-state'
                if shape == 'linked-state-dir':
                    inside.mkdir()
                    link = self.reg.root / 'linked-state'
                    link.symlink_to(inside, target_is_directory=True)
                    state_dir = link
                else:
                    link = self.reg.root / 'linked-runtime'
                    link.symlink_to(self.reg.runtime_root, target_is_directory=True)
                    state_dir = link / 'gateway-state'
                original = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'outside the Core runtime'):
                    self.mocked_install((*args[:2], state_dir, args[3]))
                self.assertEqual(self.snapshot(), original)
                self.assertTrue(link.is_symlink())
                self.assertEqual(list(inside.iterdir()) if inside.exists() else [], [])
                self.assertFalse(self.reg.agent.exists())

    def test_the_registration_runs_the_runtime_the_latest_publish_wrote(self):
        # A moved runtime (GHOST_ALICE_RUNTIME_SHARED_DIR) leaves the earlier runtime target in the record; the
        # registration follows the latest publish, whatever the record's key order.
        import content_digest
        for name, stamp, latest in (('.a-earlier-runtime', '2026-10-19T00:00:00+00:00', False),
                                    ('zz-later-runtime', '2026-10-21T00:00:00+00:00', True)):
            with self.subTest(other=name):
                args, _ = self.installation_fixture(service=False)
                other = self.reg.root / name
                (other / '_shared').mkdir(parents=True)
                (other / '_shared' / 'context_gateway.py').write_text('other runtime\n', encoding='utf-8')
                installed_payload.update(self.reg.ghost, {'targets': {str(other / '_shared'): {
                    'name': '_shared', 'kind': 'runtime', 'mode': 'copy', 'provider': 'core',
                    'source': '/src/_shared', 'source_commit': 'c0ffee', 'used_by': ['claude', 'codex'],
                    'files': content_digest.tree(other / '_shared'), 'installed_at': stamp, 'baseline': 'recorded'}}})
                self.assertEqual(self.mocked_install(args)['status'], 'installed')
                runtime = other if latest else self.reg.runtime_root
                plist = plistlib.loads(self.reg.agent.read_bytes())
                self.assertEqual(plist['ProgramArguments'][2], str(runtime / '_shared' / 'context_gateway.py'))
                self.assertEqual(self.service()['runtime_root'], str(runtime))

    def test_the_optional_claude_dir_and_strict_log_root_enter_the_state_file(self):
        args, _ = self.installation_fixture()
        self.mocked_install(args)
        state = json.loads(self.reg.state_path.read_text())
        self.assertNotIn('strict_log_root', state)
        self.assertNotIn('claude_dir', state)
        strict = self.reg.root / 'strict'
        settings = self.reg.claude / 'settings.json'
        settings.write_text('{"env":{"original":"preserved"}}')
        self.mocked_install(args, claude_dir=self.reg.claude, strict_log_root=strict)
        state = json.loads(self.reg.state_path.read_text())
        self.assertEqual((state['claude_dir'], state['strict_log_root']), (str(self.reg.claude), str(strict)))
        self.assertEqual(settings.read_text(), '{"env":{"original":"preserved"}}')
        self.assertEqual(self.service()['state_digest'], digest(self.reg.state_path.read_bytes()))

    def test_relative_coordinates_are_rejected_before_service_or_file_changes(self):
        args, original = self.installation_fixture()
        for kwargs in ({'claude_dir': Path('relative-claude')}, {'strict_log_root': Path('relative')}):
            with self.subTest(kwargs=kwargs), patch.object(install, '_service_state') as service:
                with self.assertRaises(ValueError):
                    self.mocked_install(args, **kwargs)
                service.assert_not_called()
        self.assertEqual(self.snapshot(), original)

    def test_cli_forwards_the_coordinates_and_prints_the_result(self):
        args, _ = self.installation_fixture()
        explicit = self.reg.root / 'strict'
        cli = ['--codex-dir', str(args[0]), '--ledger-root', str(args[1]), '--state-dir', str(args[2]),
               '--launch-agent', str(args[3]), '--claude-dir', str(self.reg.claude), '--strict-log-root', str(explicit)]
        stdout = io.StringIO()
        with patch.object(install, 'install', return_value=dict(status='installed', service=LABEL,
                                                                listener_ready=True)) as run, \
             contextlib.redirect_stdout(stdout):
            self.assertEqual(install.main(cli), 0)
        self.assertEqual(run.call_args.args, (args[0], args[1], args[2], args[3]))
        self.assertEqual(run.call_args.kwargs, dict(claude_dir=self.reg.claude, strict_log_root=explicit, confirm=True))
        self.assertEqual(json.loads(stdout.getvalue()), dict(status='installed', service=LABEL, listener_ready=True))

    # The provider blocks.

    def test_the_provider_block_fingerprint_covers_the_two_managed_blocks(self):
        text = planning.render_provider('model = "native-model"\n[features]\nhooks = true\n', 12345, 'local-scope')
        self.assertEqual(planning.provider_blocks(text), gateway_testing.provider_blocks(text))
        # The blocks alone: the same fingerprint whatever surrounds them.
        self.assertEqual(planning.provider_blocks(text),
                         planning.provider_blocks(planning.render_provider('', 12345, 'local-scope')))
        self.assertEqual(planning.provider_blocks('model = "native-model"\n'), '')
        self.assertEqual(planning.provider_blocks(text.replace(install.TABLE_END, '# removed')),
                         planning.provider_blocks(text).split(install.TABLE_BEGIN)[0])

    def test_adds_supported_provider_and_preserves_unrelated_settings(self):
        original = 'model = "native-model"\n[features]\nhooks = true\n[projects."/my/project"]\ntrust_level = "trusted"\n'
        result = install.render_provider(original, 12345, 'local-scope')
        config = tomllib.loads(result)
        self.assertEqual(config['model_provider'], install.PROVIDER)
        provider = config['model_providers'][install.PROVIDER]
        self.assertTrue(provider['requires_openai_auth'])
        self.assertFalse(provider['supports_websockets'])
        self.assertEqual(provider['base_url'], 'http://127.0.0.1:12345/codex')
        self.assertEqual(config['features'], {'hooks': True})
        self.assertEqual(config['projects']['/my/project'], {'trust_level': 'trusted'})
        self.assertNotIn('env_key', provider)
        self.assertEqual(install.remove_provider(result), original)

    def test_refresh_replaces_owned_blocks_without_duplicate_tables(self):
        original = 'model = "native-model"\n'
        once = install.render_provider(original, 12345, 'first')
        twice = install.render_provider(once, 12346, 'second')
        config = tomllib.loads(twice)
        self.assertEqual(config['model_providers'][install.PROVIDER]['http_headers'], {'X-Ghost-Context-Scope': 'second'})
        self.assertEqual(install.remove_provider(twice), original)

    def test_unrelated_provider_and_unowned_table_require_preservation(self):
        for original in ('model_provider = "other"\n', '[model_providers.ghost_alice_context]\nname="User definition"\n'):
            with self.subTest(original=original), self.assertRaises(ValueError):
                install.render_provider(original, 12345, 'scope')

    def test_provider_removal_preserves_later_unrelated_edit(self):
        result = install.render_provider('model="native"\n', 12345, 'scope')
        changed = result + '\n[features]\nhooks=true\n'
        self.assertEqual(tomllib.loads(install.remove_provider(changed)), {'model': 'native', 'features': {'hooks': True}})

    def test_invalid_coordinates_are_not_interpolated(self):
        for port, scope in ((0, 'scope'), (True, 'scope'), (70000, 'scope'), (12345, '')):
            with self.subTest(port=port), self.assertRaises(ValueError):
                install.render_provider('', port, scope)


if __name__ == '__main__':
    unittest.main()
