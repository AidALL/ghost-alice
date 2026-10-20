"""Isolated subprocess installer tests with a private native-service simulator.

The candidate gateway is a real loopback process. Only the launchctl executable
and explicitly selected failure boundaries are simulated. No user's native
service, configuration, credentials or installed files enter these fixtures.
The install record names the runtime the gateway runs from; the registration
records the service in it, and the removal judges from it.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_digest  # noqa: E402
import context_gateway_install as gateway  # noqa: E402
import context_gateway_model as model  # noqa: E402
import install_lock  # noqa: E402
import installed_payload  # noqa: E402
from context_gateway_model import GatewayBlocked  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

NATIVE = r'''
import json, os, pathlib, plistlib, signal, socket, subprocess, sys, time
root = pathlib.Path(os.environ['FAKE_NATIVE_ROOT'])
path = root / 'native.json'
state = json.loads(path.read_text()) if path.exists() else dict(active=False, boots=0, pids=[])
mode = os.environ.get('FAULT_CASE', '')
operation = sys.argv[1]
def save():
    path.write_text(json.dumps(state))
if operation == 'print':
    if mode == 'discovery-error':
        sys.exit(77)
    if not state['active']:
        sys.exit(113)
    if mode == 'malformed-discovery':
        print('service output cannot establish a definition')
    else:
        print('path = ' + (str(root / 'other.plist') if mode == 'different-agent' else state['agent']))
    sys.exit(0)
if operation == 'bootout':
    if mode == 'stop-error' or (mode == 'candidate-stop-error' and state['boots'] >= 2):
        sys.exit(5)
    try:
        os.kill(state['pid'], signal.SIGTERM)
    except ProcessLookupError:
        pass
    state['active'] = False
    save()
    for _ in range(100):
        with socket.socket() as probe:
            probe.settimeout(.05)
            if probe.connect_ex(('127.0.0.1', state['port'])):
                break
        time.sleep(.01)
    if mode == 'concurrent-runtime':
        pathlib.Path(state['runtime']).write_bytes(b'concurrent user runtime edit')
    sys.exit(5 if mode == 'stop-after-kill-error' else 0)
if operation == 'bootstrap':
    state['boots'] += 1
    save()
    if mode == 'bootstrap-error' and state['boots'] == 2:
        sys.exit(5)
    agent = pathlib.Path(sys.argv[3])
    definition = plistlib.loads(agent.read_bytes())
    argv = definition['ProgramArguments']
    config = json.loads(pathlib.Path(argv[-1]).read_bytes())
    with open(definition['StandardErrorPath'], 'ab') as output:
        process = subprocess.Popen(argv, stdout=output, stderr=output, start_new_session=True)
    state.update(active=True, pid=process.pid, agent=str(agent), runtime=argv[2], port=config['port'])
    state['pids'].append(process.pid)
    save()
    if mode == 'unowned-state-content':
        pathlib.Path(argv[-1]).with_name('independent.txt').write_bytes(b'preserve independent user content')
    sys.exit(5 if mode == 'partial-bootstrap-error' and state['boots'] == 2 else 0)
sys.exit(64)
'''

DRIVER = r'''
import os, pathlib, sys
sys.path.insert(0, sys.argv.pop(1))
import context_gateway_install as gateway
gateway.sys.platform = 'darwin'
mode = os.environ.get('FAULT_CASE', '')
real_write, real_ready = gateway._write, gateway._ready
if mode in ('config-write-error', 'receipt-write-error', 'binding-write-error', 'recovery-write-error'):
    names = {'config-write-error': 'config.toml', 'receipt-write-error': 'installation.json',
             'binding-write-error': 'context-gateway-binding.json', 'recovery-write-error': 'config.toml'}
    hit = [False]
    def write(path, raw, permissions=0o600):
        real_write(path, raw, permissions)
        if path.name == names[mode] and (not hit[0] or mode == 'recovery-write-error'):
            hit[0] = True
            raise OSError('injected error after atomic replacement')
    gateway._write = write
if mode == 'record-write-error':
    def record(section):
        raise OSError('injected record write failure')
    gateway._record = record
if mode in ('readiness-error', 'candidate-stop-error', 'unowned-state-content'):
    calls = [0]
    def ready(state):
        calls[0] += 1
        return False if calls[0] == 1 else real_ready(state)
    gateway._ready = ready
sys.exit(gateway.main(sys.argv[1:]))
'''


@unittest.skipUnless(sys.version_info >= (3, 14), 'Subprocess gateway requires Python 3.14 with zstd')
class GatewayInstallerSubprocessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghost-alice-isolated-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(self.stop_private_processes)
        self.home, self.shared, self.codex, self.ledger, self.state = [self.root / n for n in
            ('home', 'installed/_shared', 'home/.codex', 'ledger', 'gateway-state')]
        self.ghost = self.home / '.ghost-alice'
        self.agent = self.root / 'agent.plist'
        for path in (self.home, self.codex, self.ledger, self.root / 'bin'):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / '_shared', self.shared,
                        ignore=shutil.ignore_patterns('__pycache__', 'test_*.py', '.pytest_cache'))
        self.write_record()
        sqlite3.connect(self.ledger / 'ghost-state.sqlite3').close()
        (self.codex / 'config.toml').write_text('model="native"\n')
        (self.codex / 'config.toml').chmod(0o640)
        shim = self.root / 'bin/launchctl'
        shim.write_text('#!' + sys.executable + '\n' + NATIVE)
        shim.chmod(0o700)
        self.environment = dict(os.environ, HOME=str(self.home), PATH=str(shim.parent),
            FAKE_NATIVE_ROOT=str(self.root), FAULT_CASE='', PYTHONDONTWRITEBYTECODE='1')
        self.arguments = ['--source-root', str(ROOT), '--shared-dir', str(self.shared), '--codex-dir', str(self.codex),
            '--ledger-root', str(self.ledger), '--state-dir', str(self.state), '--launch-agent', str(self.agent)]

    def write_record(self, patches=None):
        """The install record the installer's runtime publish would have written, under this process's lock."""
        lock = self.ghost / 'install.lock'
        install_lock.acquire_lock(lock, owner='installer:test:codex:test')
        try:
            if patches is None:
                patches = {'targets': {str(self.shared): dict(
                    name='_shared', kind='runtime', mode='copy', provider='core', source=str(ROOT / '_shared'),
                    source_commit='test', used_by=['codex'], files=content_digest.tree(self.shared),
                    installed_at='2026-10-20T00:00:00+00:00', baseline='recorded')}}
            installed_payload.update(self.ghost, patches)
        finally:
            install_lock.release_lock(lock)

    def stop_private_processes(self):
        path = self.root / 'native.json'
        if path.exists():
            for pid in json.loads(path.read_text()).get('pids', []):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def run_cli(self, *, fault='', arguments=None):
        result = subprocess.run([sys.executable, '-B', '-c', DRIVER, str(ROOT / '_shared'),
            *(self.arguments if arguments is None else arguments)],
            env=dict(self.environment, FAULT_CASE=fault), capture_output=True, text=True, timeout=30)
        body = json.loads((result.stderr if result.returncode else result.stdout).strip().splitlines()[-1])
        print(json.dumps(dict(case=fault or body.get('status', body.get('action')), exit_code=result.returncode,
            error_code=body.get('error_code'), phase=body.get('phase'))), flush=True)
        return result, body

    def seed(self):
        result, body = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body['status'], 'installed')
        self.assertIsNotNone(self.service())
        return self.snapshot_owned()

    def service(self):
        return (installed_payload.read(self.ghost) or {}).get('service')

    def snapshot_owned(self):
        paths = [self.shared / n for n in model.MODULES]
        paths.extend([self.codex / 'config.toml', self.agent, self.state / 'state.json',
                      self.state / 'installation.json', self.home / '.ghost-alice/context-gateway-binding.json',
                      installed_payload.path(self.ghost)])
        return {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in paths}

    def test_registration_records_the_service_in_the_install_record(self):
        self.seed()
        service = self.service()
        self.assertEqual((service['runtime_root'], service['program']),
                         (str(self.shared.parent), str(self.shared / 'context_gateway.py')))
        self.assertEqual((service['launch_agent'], service['state_path'], service['config_path']),
                         (str(self.agent), str(self.state / 'state.json'), str(self.codex / 'config.toml')))
        self.assertEqual(service['launch_agent_digest'], model.digest(self.agent.read_bytes()))
        self.assertTrue(service['listener_ready'])
        self.assertTrue((self.state / 'installation.json').is_file())

    def test_registration_without_a_record_writes_none(self):
        installed_payload.path(self.ghost).unlink()
        result, body = self.run_cli()
        self.assertEqual((result.returncode, body['status']), (0, 'installed'))
        self.assertFalse(installed_payload.path(self.ghost).exists())
        self.assertTrue((self.state / 'installation.json').is_file())

    def test_real_candidate_refresh_and_removal_keep_the_native_configuration_contract(self):
        original = self.seed()
        state = json.loads((self.state / 'state.json').read_bytes())
        result, _ = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        current = json.loads((self.state / 'state.json').read_bytes())
        self.assertEqual((current['port'], current['scope']), (state['port'], state['scope']))
        result, body = self.run_cli(arguments=['detach', '--user-home', str(self.home), '--confirm'])
        self.assertEqual((result.returncode, body['action']), (0, 'detached'))
        self.assertEqual((self.codex / 'config.toml').read_text(), 'model="native"\n')
        self.assertFalse(self.agent.exists())
        self.assertTrue((self.state / 'installation.json').exists())
        self.assertIsNone(self.service())
        self.assertEqual(stat.S_IMODE((self.codex / 'config.toml').stat().st_mode), original[self.codex / 'config.toml'][1])

    def selected_removal(self, names, *, remaining=True, fault=''):
        """Run the installer's selected removal (`--uninstall --force <names>`) for codex against the
        fixture runtime and the native-service simulator; return its exit code."""
        from installer import addons, cli, hosts, lifecycle, selection, transaction
        from installer.runner import InstallerExit
        environment = dict(self.environment, FAULT_CASE=fault,
            PATH=self.environment['PATH'] + os.pathsep + os.environ.get('PATH', ''))
        ctx = cli.Context(options=cli.Options(platform='codex'), platform='codex', skills_dir=self.shared.parent)
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(gateway.sys, 'platform', 'darwin'), \
                mock.patch.object(selection, 'expand_skill_targets',
                    lambda name: [selection.Target(name, Path('fixture'), None)]), \
                mock.patch.object(selection, 'validate_requested_skills', lambda names: None), \
                mock.patch.object(selection, 'has_managed_installs', lambda skills_dir: remaining), \
                mock.patch.object(addons, 'resume_pending_uninstalls', lambda ctx: None), \
                mock.patch.object(transaction, 'migrate_record', lambda ctx: None), \
                mock.patch.object(hosts, 'remove_codex_bootstrap_if_unused', lambda ctx, skills_dir: False), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                return lifecycle.uninstall(ctx, ['--force', *names])
            except InstallerExit as stop:
                return stop.code

    def test_selected_skill_removal_guards_companion_before_deleting_it(self):
        self.seed()
        target = self.shared.parent / 'session-intent-analyzer'
        independent = self.shared.parent / 'writing-plans'
        independent.mkdir()
        for fault, expected in [('stop-error', 3), ('', 0)]:
            with self.subTest(fault=fault):
                code = self.selected_removal(['writing-plans', 'session-intent-analyzer'], fault=fault)
                self.assertEqual(code, expected)
                self.assertEqual(target.exists(), bool(fault))
                self.assertEqual(independent.exists(), bool(fault))
                self.assertEqual(json.loads((self.root / 'native.json').read_text())['active'], bool(fault))
                self.assertEqual(self.service() is not None, bool(fault))

    def test_unconfirmed_shared_cleanup_is_not_reported_as_success(self):
        self.seed()
        independent = self.shared.parent / 'writing-plans'
        independent.mkdir()
        code = self.selected_removal(['writing-plans'], remaining=False, fault='stop-error')
        self.assertEqual(code, 3)
        self.assertTrue(self.shared.is_dir())
        self.assertTrue(json.loads((self.root / 'native.json').read_text())['active'])

    def test_dry_run_prints_a_plan_and_exits_without_applying_it(self):
        original = (self.codex / 'config.toml').read_bytes()
        record = installed_payload.path(self.ghost).read_bytes()
        result, body = self.run_cli(arguments=[*self.arguments, '--dry-run'])
        self.assertEqual((result.returncode, body['status']), (0, 'dry-run'))
        self.assertTrue(body['changes'])
        plan = body['plan']
        self.assertEqual(plan['service_before'], 'absent')
        self.assertEqual([step['phase'] for step in plan['steps']],
            ['prepare', 'verify-absence', 'stage', 'bootstrap', 'readiness', 'commit', 'retire'])
        self.assertTrue(plan['preconditions'])
        self.assertTrue(plan['steps'][0]['files'])
        self.assertIn('preserved', plan)
        self.assertTrue(plan['recovery'])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.agent.exists())
        self.assertEqual((self.codex / 'config.toml').read_bytes(), original)
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), record)
        self.assertFalse((self.root / 'native.json').exists())

    def test_detach_preview_returns_service_file_preservation_and_recovery_plan(self):
        original = self.seed()
        result, body = self.run_cli(arguments=['detach', '--user-home', str(self.home)])
        self.assertEqual((result.returncode, body['status'], body['action']), (0, 'dry-run', 'would-detach'))
        plan = body['plan']
        self.assertEqual(plan['service_before'], 'running')
        self.assertEqual([step['phase'] for step in plan['steps']],
            ['prepare', 'stop', 'verify-absence', 'stage', 'commit', 'retire'])
        self.assertIn(str(self.state), plan['preserved'])
        self.assertEqual(self.snapshot_owned(), original)
        self.assertTrue(json.loads((self.root / 'native.json').read_text())['active'])

    def test_unknown_native_discovery_and_wrong_loaded_agent_exit_3_before_replacement(self):
        original = self.seed()
        for fault in ('discovery-error', 'malformed-discovery', 'different-agent', 'stop-error', 'stop-after-kill-error'):
            with self.subTest(fault=fault):
                # Each failure is independent; restart a certified stopped prior
                # service before exercising the next failure condition.
                native = json.loads((self.root / 'native.json').read_text())
                if not native['active']:
                    result, _ = self.run_cli()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    original = self.snapshot_owned()
                result, body = self.run_cli(fault=fault)
                self.assertEqual((result.returncode, body['error_code']), (3, 'GATEWAY_SERVICE_UNCONFIRMED'))
                self.assertEqual(self.snapshot_owned(), original)

    def test_partial_start_writes_and_readiness_errors_restore_and_exit_4(self):
        self.seed()
        for fault in ('bootstrap-error', 'partial-bootstrap-error', 'readiness-error', 'config-write-error',
                      'receipt-write-error', 'binding-write-error', 'record-write-error'):
            with self.subTest(fault=fault):
                # Native fault counts select the first candidate after the seed.
                native_path = self.root / 'native.json'
                native = json.loads(native_path.read_text()); native['boots'] = 1
                native_path.write_text(json.dumps(native))
                original = self.snapshot_owned()
                result, body = self.run_cli(fault=fault)
                self.assertEqual((result.returncode, body['error_code']), (4, 'GATEWAY_OPERATION_RESTORED'), result.stderr)
                self.assertEqual(self.snapshot_owned(), original)
                self.assertTrue(json.loads(native_path.read_text())['active'])

    def test_concurrent_runtime_is_preserved_without_restarting_it(self):
        self.seed()
        result, body = self.run_cli(fault='concurrent-runtime')
        self.assertEqual((result.returncode, body['error_code']), (5, 'GATEWAY_RECOVERY_UNCONFIRMED'))
        self.assertEqual((self.shared / 'context_gateway.py').read_bytes(), b'concurrent user runtime edit')
        self.assertFalse(json.loads((self.root / 'native.json').read_text())['active'])

    def test_uncertain_candidate_shutdown_preserves_candidate_and_exits_5(self):
        self.seed()
        result, body = self.run_cli(fault='candidate-stop-error')
        self.assertEqual((result.returncode, body['error_code']), (5, 'GATEWAY_RECOVERY_UNCONFIRMED'))
        self.assertTrue(json.loads((self.root / 'native.json').read_text())['active'])
        self.assertTrue(list((self.state / 'backups').glob('*-artifacts/manifest.json')))

    def test_failed_recovery_and_independent_first_install_content_are_preserved(self):
        result, body = self.run_cli(fault='unowned-state-content')
        self.assertEqual((result.returncode, body['error_code']), (5, 'GATEWAY_RECOVERY_UNCONFIRMED'))
        self.assertEqual((self.state / 'independent.txt').read_bytes(), b'preserve independent user content')

    def test_write_failure_during_recovery_reports_uncertainty(self):
        self.seed()
        result, body = self.run_cli(fault='recovery-write-error')
        self.assertEqual((result.returncode, body['error_code']), (5, 'GATEWAY_RECOVERY_UNCONFIRMED'))
        self.assertFalse(json.loads((self.root / 'native.json').read_text())['active'])

    def test_a_loaded_service_without_a_record_entry_is_preserved_and_its_program_removal_is_held(self):
        from installer import gateway_participant
        self.seed()
        self.write_record({'service': None})
        native = (self.root / 'native.json').read_bytes()

        def removal(removing, fault=''):
            environment = dict(self.environment, FAULT_CASE=fault)
            with mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(gateway.sys, 'platform', 'darwin'):
                return gateway_participant.removal(self.ghost, removing, remove_codex=False, confirm=True)

        result = removal([self.root / 'independent-claude'])
        self.assertEqual(result['action'], 'unchanged')
        with self.assertRaises(GatewayBlocked) as held:
            removal([self.shared])
        self.assertEqual((held.exception.exit_code, held.exception.code), (3, 'GATEWAY_SERVICE_UNCONFIRMED'))
        with self.assertRaises(GatewayBlocked) as held:
            removal([self.root / 'independent-claude'], fault='discovery-error')
        self.assertEqual((held.exception.exit_code, held.exception.code), (3, 'GATEWAY_SERVICE_UNCONFIRMED'))
        self.assertEqual((self.root / 'native.json').read_bytes(), native)
        self.assertTrue(self.agent.exists())

    def test_changed_receipt_blocks_before_any_service_stop_and_exits_2(self):
        self.seed()
        receipt = self.state / 'installation.json'
        receipt.write_bytes(receipt.read_bytes() + b'\n')
        original, native = self.snapshot_owned(), (self.root / 'native.json').read_bytes()
        result, body = self.run_cli()
        self.assertEqual((result.returncode, body['error_code']), (2, 'GATEWAY_PRECONDITION'))
        self.assertEqual(self.snapshot_owned(), original)
        self.assertEqual((self.root / 'native.json').read_bytes(), native)


if __name__ == '__main__':
    unittest.main()
