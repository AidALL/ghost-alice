"""Cross-version installs use disposable Git repositories and the shipped management command."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_shared'))
from installer_testing import checkout_copy, inject_installer_python, installer_env, run_pty
import installed_payload

# The last deployed parent reads installed-payload-v2.json again after its child exits.
V2_COMMIT = (Path(__file__).parent / '_fixtures/v2-manager-commit.txt').read_text().strip()


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'requires the POSIX installer')
class ReleaseSourceTests(unittest.TestCase):
    def test_install_and_update_take_main_even_after_explicit_local_install(self):
        checkout = checkout_copy(self)
        remote = checkout_copy(self)
        home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (home / '.claude').mkdir()
        env = installer_env(home)
        def run(*args, code=0):
            result = subprocess.run(args, env=env, cwd=checkout, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
            return result.stdout.strip()
        run('git', 'config', 'remote.origin.url', str(remote))
        run('git', 'switch', '-qc', 'feature')
        skill = Path('task-router/SKILL.md')
        release = (remote / skill).read_bytes()
        local = release + b'\nLocal development edit.\n'
        (checkout / skill).write_bytes(local)
        entry = ['bash', str(checkout / 'install.sh'), '--platform', 'claude', 'task-router']
        target = home / '.claude/skills' / skill
        run(*entry)
        self.assertEqual(target.read_bytes(), release)
        ghost = home / '.ghost-alice'
        self.assertEqual(installed_payload.read(ghost)['source']['branch'], 'main')
        run(*entry, '--local-source')
        self.assertEqual(target.read_bytes(), local)
        record = installed_payload.read(ghost)
        self.assertEqual(record['source']['branch'], 'feature')
        self.assertEqual(record['source']['ref'], 'refs/heads/main')
        saved = Path(record['installer']['active_root'])
        self.assertEqual((saved / skill).read_bytes(), local)
        # An installation made before the release policy stored its development ref here.
        record['source']['ref'] = 'refs/heads/deleted-feature'
        installed_payload.read_path(ghost).write_text(json.dumps(record))
        run('bash', str(ghost / 'bin/ghost-alice'), 'update')
        self.assertEqual(target.read_bytes(), release)
        self.assertEqual(installed_payload.read(ghost)['source']['ref'], 'refs/heads/main')
        run('bash', str(saved / 'install.sh'), '--local-source')
        self.assertEqual(target.read_bytes(), local)
        run('bash', str(saved / 'install.sh'))
        self.assertEqual(target.read_bytes(), release)
        detail = installed_payload.read_path(ghost)
        before = detail.read_bytes()
        manager = ['bash', str(ghost / 'bin/ghost-alice'), 'update']
        self.assertIn('main', run(*manager, '--ref', 'feature', code=1))
        self.assertIn('main', run(*manager, '--local-source', code=1))
        run('git', '-C', str(remote), 'branch', '-m', 'other')
        self.assertIn('refs/heads/main', run(*entry, code=1))
        self.assertIn('refs/heads/main', run(*manager, code=1))
        self.assertEqual(detail.read_bytes(), before)
        self.assertEqual(target.read_bytes(), release)
        self.assertEqual((checkout / skill).read_bytes(), local)


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'requires the POSIX installer')
class VersionHandoffTests(unittest.TestCase):
    def setUp(self):
        self.repo = checkout_copy(self)
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (self.home / '.claude').mkdir()
        self.env = installer_env(self.home)
        self.env['GHOST_ALICE_INSTALL_LOCALE'] = 'en'
        self.ghost = self.home / '.ghost-alice'
        self.ledger = self.repo / 'records'
        self.command = ['bash', str(self.ghost / 'bin/ghost-alice')]
        self.pending = self.ghost / 'pending-install-source.json'
        self.remote = checkout_copy(self)
        self.git('fetch', '--quiet', '--depth=1', str(ROOT), V2_COMMIT)
        self.git('checkout', '--detach', '--quiet', V2_COMMIT)
        self.options = ['--platform', 'claude', '--skip-source-health', 'task-router']
        self.shell(['bash', str(self.repo / 'install.sh'), *self.options],
                   env=dict(self.env, GHOST_ALICE_SESSION_INTENT_ROOT=str(self.ledger)))
        self.old = Path(installed_payload.read(self.ghost)['installer']['active_root'])
        self.update = [*self.command, 'update', '-p', 'claude', '--remote', str(self.remote),
                       '--ref', 'main', '--skip-source-health']

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], env=self.env, capture_output=True,
                              text=True, check=True).stdout.strip()

    def shell(self, argv, *, env=None, code=0):
        result = subprocess.run(argv, cwd=self.home, env=env or self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_v2_update_retains_hosts_across_interruption_and_package_reuse(self):
        self.git('checkout', '--quiet', 'main')
        # Fail once after the request writer reads the activation record, before the move process starts.
        inject_installer_python(self.env, """
import json, sys
def stop_at_handoff(event, args):
    if event == 'subprocess.Popen' and args[3]:
        request = json.loads(args[3].get('GHOST_ALICE_INSTALL_REQUEST') or '{}')
        if request.get('stage') == 'record-move' and (Path.home() / 'interrupt').exists():
            (Path.home() / 'interrupt').unlink()
            raise OSError('fixture interrupted before record move')
sys.addaudithook(stop_at_handoff)
""")
        (self.home / 'interrupt').touch()
        self.assertIn('fixture interrupted', self.shell(self.update, code=1))
        record = installed_payload.read(self.ghost)
        self.assertEqual(record['schema_version'], 2)
        self.assertEqual(record['source']['ledger_source_roots'], {str(self.ledger): str(self.repo)})
        active = Path(record['installer']['active_root'])
        self.assertNotEqual(active, self.old)
        self.assertTrue(self.pending.is_file(), 'The pending hosts must belong to the install record')
        self.assertNotIn('pending_record_source', json.loads((active.parent / 'version.json').read_bytes()))
        # The same captured package is reused. Its last installation never reached record-move.
        (self.home / '.codex').mkdir(exist_ok=True)
        self.shell([*self.command, 'update'])
        from installer.selection import installed_core_selection
        self.assertEqual(installed_core_selection(installed_payload.read(self.ghost)), {'claude': ['task-router']})
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'],
                         {str(self.ledger): {'hosts': ['claude'], 'source_root': str(self.repo)}})
        self.assertFalse(self.pending.exists())
        self.assertEqual(Path(installed_payload.read(self.ghost)['installer']['active_root']), active)

    def test_new_checkout_can_read_v3_immediately_with_the_v2_manager_installed(self):
        self.git('checkout', '--quiet', 'main')
        inject_installer_python(self.env, """
import json, sys
def observe_handoff(event, args):
    if event == 'subprocess.Popen' and args[3]:
        request = json.loads(args[3].get('GHOST_ALICE_INSTALL_REQUEST') or '{}')
        if request.get('stage') == 'record-move':
            (Path.home() / 'at-handoff.json').write_text(json.dumps(request['source']))
sys.addaudithook(observe_handoff)
""")
        self.shell(['bash', str(self.repo / 'install.sh'), *self.options])
        record = installed_payload.read(self.ghost)
        self.assertEqual(json.loads((self.home / 'at-handoff.json').read_bytes()), record['source'])
        self.assertEqual(record['source']['ledger_source_roots'],
                         {str(self.ledger): {'hosts': ['claude'], 'source_root': str(self.repo)}})
        self.assertFalse(self.pending.exists())

    def test_fetched_update_entry_receives_original_arguments_terminal_and_returns_its_exit_code(self):
        self.git('checkout', '--quiet', 'main')
        self.shell(['bash', str(self.repo / 'install.sh'), *self.options])
        # A distinct downstream version asks via its own entry and deliberately declines installation.
        entry = self.remote / '_shared/installer/cli.py'
        entry.write_text("import json, sys\nfrom pathlib import Path\n"
                         "Path.home().joinpath('received.json').write_text(json.dumps(sys.argv[1:]))\n"
                         "print('Downstream installer ready?', flush=True)\n"
                         "sys.exit(37 if input() == 'continue' else 38)\n")
        subprocess.run(['git', '-C', str(self.remote), '-c', 'user.email=t@example.invalid', '-c', 'user.name=t',
                        'commit', '-qam', 'Next installer'], env=self.env, check=True)
        # Only the downloaded version can interpret a new adapter name or its options.
        argv = list(self.update)
        argv[argv.index('-p') + 1] = 'next-adapter'
        argv.append('--new-adapter-option')
        code, output = run_pty(argv, env=self.env, cwd=self.home,
                               responses=[('Downstream installer ready?', 'continue\n')], timeout=30)
        self.assertEqual(code, 37, output)
        self.assertEqual(json.loads((self.home / 'received.json').read_bytes()),
                         ['--install-acquired-update', str(self.repo), str(self.remote), 'main',
                          *argv[len(self.command):]])

    def test_v2_locked_command_recovers_interrupted_activation_and_first_v3_publication(self):
        # Exit the writing process immediately after the actual rename, bypassing Python rollback.
        inject_installer_python(self.env, """
_replace = os.replace
def interrupt_write(src, dst, *args, **kwargs):
    result = _replace(src, dst, *args, **kwargs)
    arm = Path.home() / 'interrupt'
    if arm.exists() and Path(dst) == Path.home() / '.ghost-alice' / arm.read_text():
        arm.unlink()
        os._exit(73)
    return result
os.replace = interrupt_write
""")
        for name in (self.pending.name, installed_payload.ATTRIBUTED_FILENAME):
            with self.subTest(interrupted_file=name):
                (self.home / 'interrupt').write_text(name)
                self.shell(self.update, code=73)
                self.assertTrue(self.pending.is_file())
                self.assertTrue((self.ghost / name).is_file())
                lock = self.ghost / 'install.lock'
                os.utime(lock, (1, 1))  # Model the stale interval after the process exited.
                self.shell(['bash', str(self.old / 'install.sh'), '--platform', 'claude', '--cleanup-pending'])
                record = installed_payload.read(self.ghost)
                self.assertEqual(record['schema_version'], 2)
                self.assertFalse((self.ghost / installed_payload.ATTRIBUTED_FILENAME).exists())
                self.assertEqual(self.pending.exists(), name == installed_payload.ATTRIBUTED_FILENAME)
                self.assertEqual(Path(record['installer']['active_root']) == self.old, not self.pending.exists())
        self.shell(self.update)
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'],
                         {str(self.ledger): {'hosts': ['claude'], 'source_root': str(self.repo)}})
        self.assertFalse(self.pending.exists())

    def test_v2_hook_can_read_the_last_record_even_when_the_active_manager_already_reads_v3(self):
        self.git('checkout', '--quiet', 'main')
        self.shell(['bash', str(self.repo / 'install.sh'), *self.options],
                   env=dict(self.env, GHOST_ALICE_SESSION_INTENT_ROOT=str(self.ghost / 'session-intent')))
        record = installed_payload.read(self.ghost)
        self.assertNotEqual(Path(record['installer']['active_root']), self.old)
        self.assertEqual(record['schema_version'], 2)
        config = self.ghost / 'config.json'
        data = json.loads(config.read_bytes())
        data['install_locations']['claude']['ledger_root'] = str(self.ledger)
        config.write_text(json.dumps(data))
        # A checkout can still invoke a hook supplied by the deployed v2 package.
        self.shell(['bash', str(self.old / 'install.sh'), '--post-merge', '--merged-checkout', str(self.repo)])
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'],
                         {str(self.ledger): {'hosts': ['claude'], 'source_root': str(self.repo)}})
