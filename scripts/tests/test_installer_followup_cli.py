"""Installer recovery contracts through the real outer CLI and temporary homes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / '_shared'))
from installer_testing import checkout_copy, destination_fixture, installer_env


class InstallerFollowupCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = checkout_copy(self)
        self.home = self.root / 'home'
        self.home.mkdir()
        (self.home / '.claude').mkdir()
        self.env = installer_env(self.home)
        self.settings = self.home / '.claude/settings.json'
        self.settings.write_text(json.dumps({'custom': 'preserved', 'permissions': {'allow': ['Bash(custom)']}}))
        self.receipt = self.home / '.ghost-alice/addon-permissions/claude-v2.json'
        self.manifest = self.home / '.ghost-alice/install-state/claude.json'

    def run_install(self, *args, platform='claude'):
        return subprocess.run(['bash', str(self.source / 'install.sh'), '--platform', platform,
                               '--skip-source-health', *args], env=self.env, text=True,
                              capture_output=True, timeout=90)

    def install(self, *args):
        result = self.run_install('task-router', *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.receipt.is_file())

    def inject(self, source):
        directory = self.root / 'injection'
        directory.mkdir(exist_ok=True)
        (directory / 'sitecustomize.py').write_text('import os\nfrom pathlib import Path\n' + source)
        self.env['PYTHONPATH'] = str(directory)

    def fail_replace(self, path, message, *, after_write=False):
        self.inject(f'''_replace = os.replace
def replace(source, target, *args, **kwargs):
    if Path(target) == Path({str(path)!r}):
        if {after_write!r}:
            _replace(source, target, *args, **kwargs)
        raise PermissionError({message!r})
    return _replace(source, target, *args, **kwargs)
os.replace = replace
''')

    def test_receipt_commit_and_legacy_restore_errors_reach_screen_log_and_json(self):
        original = self.settings.read_bytes()
        legacy = self.receipt.with_name('claude.json')
        legacy.parent.mkdir(parents=True)
        legacy_bytes = json.dumps(dict(schema_version=1, settings_file=str(self.settings.resolve()),
                                      addons={'pilot': {'pilot': True}})).encode()
        legacy.write_bytes(legacy_bytes)
        self.inject(f'''_replace = os.replace
def replace(source, target, *args, **kwargs):
    if Path(target) == Path({str(self.receipt)!r}):
        raise PermissionError('new receipt publish denied')
    if Path(target) == Path({str(legacy)!r}) and Path(source).name.startswith('claude-v1-'):
        raise PermissionError('legacy receipt restore denied')
    return _replace(source, target, *args, **kwargs)
os.replace = replace
''')
        result = self.run_install('task-router')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.settings.read_bytes(), original)
        backups = list(legacy.parent.glob('claude-v1-*.bak'))
        self.assertEqual([path.read_bytes() for path in backups], [legacy_bytes])
        log = '\n'.join(path.read_text() for path in self.home.rglob('*.log'))
        for message in ('new receipt publish denied', 'legacy receipt restore denied', str(backups[0])):
            self.assertIn(message, result.stdout + result.stderr)
            self.assertIn(message, log)
        legacy.write_bytes(legacy_bytes)
        direct = subprocess.run([sys.executable, str(self.source / '_shared/install_hooks.py'),
            '--platform', 'claude', '--format', 'json'], env=self.env, text=True, capture_output=True, timeout=30)
        payload = json.loads(direct.stdout)
        self.assertEqual(payload['returncode'], 1)
        errors = '\n'.join(payload['results']['claude']['details']['errors'])
        for message in ('new receipt publish denied', 'legacy receipt restore denied', 'claude-v1-'):
            self.assertIn(message, errors)

    def test_report_publish_failure_preserves_previous_report_and_creates_no_empty_final(self):
        self.install()
        report = self.root / 'protected-report.json'
        original = b'{"previous":true}\n'
        report.write_bytes(original)
        self.fail_replace(report, 'report publish denied')
        result, _ = self.cleanup(apply=False, report=report)
        self.assertEqual(result.returncode, 1)
        self.assertIn('report publish denied', result.stderr)
        self.assertEqual(report.read_bytes(), original)
        report.unlink()
        result, _ = self.cleanup(apply=False, report=report)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(report.exists())

    def cleanup(self, *, apply=True, report=None):
        path = report or self.root / 'report.json'
        result = subprocess.run([sys.executable, str(self.source / '_shared/uninstall_cleanup.py'),
            '--platform', 'claude', '--install-state-manifest', str(self.manifest), '--report-path', str(path),
            *(['--apply'] if apply else [])], env=self.env, text=True, capture_output=True, timeout=90)
        return result, json.loads(path.read_text()) if path.is_file() else None

    def test_corrupt_or_missing_receipt_with_addon_keeps_dependencies_and_removes_independent_core(self):
        addon = self.source / '_shared/tests/fixtures/dummy-addon'
        for receipt in (b'{', None):
            with self.subTest(receipt=receipt):
                self.install('skill-evolution', '--addon-source', str(addon))
                allow = json.loads(self.settings.read_text())['permissions']['allow']
                sidecar = self.home / '.ghost-alice/addons/claude/noop.json'
                original_sidecar = sidecar.read_bytes()
                if receipt is None:
                    self.receipt.unlink()
                else:
                    self.receipt.write_bytes(receipt)
                    reinstall = self.run_install('task-router')
                    self.assertEqual(reinstall.returncode, 0, reinstall.stdout + reinstall.stderr)
                    self.assertIn('permissions retained for review', reinstall.stdout)
                    self.assertTrue(any(json.loads(self.settings.read_text())['hooks'].values()))
                command = [sys.executable, str(self.source / '_shared/addon_uninstall.py'),
                           '--addons-dir', str(sidecar.parent), '--platform', 'claude',
                           '--skills-dir', str(self.home / '.claude/skills')]
                for action in (['--addon-id', 'noop'], ['--resume-pending']):
                    partial = subprocess.run([*command, *action], env=self.env, text=True,
                                             capture_output=True, timeout=30)
                    self.assertEqual(partial.returncode, 2, partial.stderr)
                    body = json.loads(partial.stdout)
                    self.assertEqual((body['resumed'][0] if '--resume-pending' in action else body)['status'],
                                     'permissions-retained')
                result = self.run_install('--uninstall')
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('partial: permissions retained for review', result.stdout)
                self.assertEqual(sidecar.read_bytes(), original_sidecar)
                self.assertEqual(self.receipt.read_bytes() if self.receipt.exists() else None, receipt)
                for name in ('noop', 'task-router', '_shared'):
                    self.assertTrue((self.home / '.claude/skills' / name).exists(), name)
                self.assertFalse((self.home / '.claude/skills/skill-evolution').exists())
                self.assertTrue((self.home / '.ghost-alice/runtime/current/_shared').is_dir())
                self.assertTrue(self.manifest.is_file())
                self.assertFalse(any(json.loads(self.settings.read_text())['hooks'].values()))
                self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'], allow)
                reports = list((self.home / '.ghost-alice/uninstall-reports').glob('*.json'))
                self.assertTrue(reports)
                report = json.loads(max(reports, key=lambda path: path.stat().st_mtime_ns).read_text())
                self.assertEqual(report['schema_version'], 2)
                self.assertTrue(any(row['kind'] == 'permission' and row['action'] == 'manual-review' for row in report['items']))
                self.assertTrue(any(row.get('addon_id') == 'noop' and row['action'] == 'manual-review' for row in report['items']))

    def test_addon_core_single_backup_keeps_command_start_bytes(self):
        import gateway_testing
        import context_gateway_install as gateway
        import install_destination
        addon = self.source / '_shared/tests/fixtures/dummy-addon'
        self.install('--addon-source', str(addon))
        original = self.settings.read_bytes()
        with mock.patch.dict(os.environ, self.env, clear=True), install_destination.use(destination_fixture(
                self.home, platform_dir=str(self.home / '.claude'), skills_dir=str(self.home / '.claude/skills'),
                ghost_root=str(self.home / '.ghost-alice'))), gateway.platform.operation_lock(self.home):
            registered = gateway_testing.registered_home(self, self.home, lock=False)
        stopped = self.root / 'gateway-stopped'
        self.inject(f'''import json, sys
sys.path.insert(0, {str(self.source / '_shared')!r})
import context_gateway_install as gateway
stopped = Path({str(stopped)!r})
gateway._service_state = lambda domain: 'absent' if stopped.exists() else 'running'
def stop(domain):
    assert Path({str(self.home / '.claude/skills/noop')!r}).exists(), 'addon file preceded gateway stop'
    assert not any(json.loads(Path({str(self.settings)!r}).read_text())['hooks'].values()), 'hook refs still active'
    stopped.write_text('stopped')
gateway._stop_service = stop
gateway._start_service = lambda *args: stopped.unlink(missing_ok=True)
gateway._ready = lambda *args: True
''')
        result = self.run_install('--uninstall')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.settings.with_suffix('.json.bak').read_bytes(), original)
        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings['permissions']['allow'], ['Bash(custom)'])
        self.assertEqual(settings['custom'], 'preserved')
        self.assertFalse((self.home / '.claude/skills/noop').exists())
        # The registered runtime belongs to Codex; removing Claude's discovery
        # files must preserve that other host's gateway and service record.
        self.assertFalse(stopped.exists())
        payload = json.loads((self.home/'.ghost-alice/installed-payload.json').read_text())
        self.assertIn('service', payload)
        self.assertTrue(registered.agent.exists())
        self.assertTrue((self.home / '.ghost-alice/runtime').exists())
        reports = list((self.home / '.ghost-alice/uninstall-reports').glob('*.json'))
        report = json.loads(reports[-1].read_text())
        addon_items = [row for row in report['items']
                       if row.get('target') == str(self.home / '.claude/skills/noop')
                       or row.get('path') == str(self.home / '.claude/skills/noop')]
        self.assertEqual(len(addon_items), 1)
        self.assertEqual(addon_items[0]['action'], 'removed')

    def test_target_drift_remains_unsuccessful_when_permission_evidence_is_also_unknown(self):
        addon = self.source / '_shared/tests/fixtures/rich-addon'
        self.install('skill-evolution', '--addon-source', str(addon))
        self.receipt.write_bytes(b'{')
        command = self.home / '.claude/commands/richcmd.md'
        command.write_text('user command edit\n')
        result, report = self.cleanup()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('partial: addon removal is incomplete', result.stdout)
        self.assertTrue(report['addon_error'])
        self.assertFalse(report['errors'])
        self.assertEqual(command.read_text(), 'user command edit\n')
        self.assertTrue((self.home / '.ghost-alice/addons/claude/rich.json').exists())
        self.assertTrue((self.home / '.claude/skills/task-router').exists())
        self.assertFalse((self.home / '.claude/skills/skill-evolution').exists())

    def test_selected_addons_and_last_core_hook_share_command_start_backup(self):
        for remove_core in (False, True):
            with self.subTest(remove_core=remove_core):
                self.install('--addon-source',
                    str(self.source / '_shared/tests/fixtures/dummy-addon'), '--addon-source',
                    str(self.source / '_shared/tests/fixtures/rich-addon'))
                original = self.settings.read_bytes()
                result = self.run_install('--uninstall', '--addon', 'noop', '--addon', 'rich',
                                          *(['task-router'] if remove_core else []))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.settings.with_suffix('.json.bak').read_bytes(), original)
                for name in ('noop', 'richskill'):
                    self.assertFalse((self.home / '.claude/skills' / name).exists())
                settings = json.loads(self.settings.read_text())
                self.assertNotIn('Skill(noop)', settings['permissions']['allow'])
                self.assertNotIn('Skill(richskill)', settings['permissions']['allow'])
                if remove_core:
                    self.assertFalse(any(settings['hooks'].values()))
                else:
                    self.assertTrue(any(settings['hooks'].values()))

    def test_core_pending_before_finishes_before_addon_changes_in_full_uninstall(self):
        addon = self.source / '_shared/tests/fixtures/dummy-addon'
        self.install('--addon-source', str(addon))
        original = self.settings.read_bytes()
        self.fail_replace(self.settings, 'settings commit denied')
        failed = subprocess.run([sys.executable, str(self.source / '_shared/install_hooks.py'),
            '--platform', 'claude', '--uninstall', '--format', 'json'], env=self.env,
            text=True, capture_output=True, timeout=30)
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertEqual(self.settings.read_bytes(), original)
        pending = json.loads(self.receipt.read_text())['pending_detach']
        self.assertEqual(pending['owner'], 'core')
        self.env.pop('PYTHONPATH')
        result = self.run_install('--uninstall')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.settings.with_suffix('.json.bak').read_bytes(), original)
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'], ['Bash(custom)'])
        self.assertFalse(self.receipt.exists())
        self.assertFalse((self.home / '.ghost-alice/addons/claude/noop.json').exists())

    def test_unreadable_settings_preserve_runtime_manifest_and_retry_evidence(self):
        for failure in ('malformed', 'read-denied'):
            with self.subTest(failure=failure):
                self.install()
                original = self.settings.read_bytes()
                if failure == 'malformed':
                    self.settings.write_bytes(b'{')
                else:
                    self.inject(f'''import builtins
_open = builtins.open
def opened(file, *args, **kwargs):
    if Path(file) == Path({str(self.settings)!r}):
        raise PermissionError('settings reader denied')
    return _open(file, *args, **kwargs)
builtins.open = opened
''')
                paths = (self.settings, self.receipt, self.manifest)
                before = [path.read_bytes() for path in paths]
                result, report = self.cleanup()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(report['schema_version'], 2)
                self.assertTrue(report['partial'])
                self.assertFalse(report['hooks_detached'])
                self.assertEqual([path.read_bytes() for path in paths], before)
                self.assertTrue((self.home / '.ghost-alice/runtime/current/_shared').is_dir())
                self.assertTrue((self.home / '.claude/skills/task-router').exists())
                self.settings.write_bytes(original)
                self.env.pop('PYTHONPATH', None)
                result, _ = self.cleanup()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'], ['Bash(custom)'])

    def test_preview_keeps_receipt_settings_runtime_and_manifest_bytes(self):
        self.install()
        self.receipt.write_bytes(b'{')
        before = {str(path.relative_to(self.home)): (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
                  for path in self.home.rglob('*') if path.is_file() and not path.is_symlink()}
        result, report = self.cleanup(apply=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = {str(path.relative_to(self.home)): (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
                 for path in self.home.rglob('*') if path.is_file() and not path.is_symlink()}
        self.assertEqual(before, after)
        self.assertTrue(any(item['action'] == 'manual-review' for item in report['items']))
        self.assertTrue(any(item['action'] == 'would-remove' for item in report['items']))

    def test_binding_mismatch_preserves_internal_symlink_mode_and_permissions(self):
        self.install()
        target = self.settings.with_name('linked-settings.json')
        self.settings.rename(target)
        target.chmod(0o640)
        self.settings.symlink_to(target.name)
        receipt = self.receipt.read_bytes()
        allow = json.loads(target.read_text())['permissions']['allow']
        result, report = self.cleanup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(os.readlink(self.settings), target.name)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(json.loads(target.read_text())['permissions']['allow'], allow)
        self.assertEqual(self.receipt.read_bytes(), receipt)
        self.assertTrue(report['hooks_detached'])
        self.assertFalse((self.home / '.claude/skills/task-router').exists())

    def test_mismatched_pending_is_preserved_while_hooks_are_detached(self):
        self.install()
        data = json.loads(self.receipt.read_text())
        data['pending_detach'] = dict(owner='core', names=['task-router'], before='0' * 64, after='1' * 64)
        self.receipt.write_text(json.dumps(data))
        receipt = self.receipt.read_bytes()
        allow = json.loads(self.settings.read_text())['permissions']['allow']
        result, report = self.cleanup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.receipt.read_bytes(), receipt)
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'], allow)
        self.assertTrue(report['hooks_detached'])

    def test_settings_commit_failure_before_and_after_write_remains_recoverable(self):
        self.install()
        settings = json.loads(self.settings.read_text())
        settings['permissions']['allow'].append('Skill(task-router)')
        self.settings.write_text(json.dumps(settings))
        original = self.settings.read_bytes()
        # A failed pre-effect write keeps runtime and the original pending fingerprint.
        self.fail_replace(self.settings, 'settings commit denied')
        result, report = self.cleanup()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertFalse(report['hooks_detached'])
        self.assertTrue((self.home / '.ghost-alice/runtime/current/_shared').is_dir())
        self.assertIn('pending_detach', json.loads(self.receipt.read_text()))
        self.fail_replace(self.settings, 'settings committed before error', after_write=True)
        result, report = self.cleanup()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(report['hooks_detached'])
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'], ['Bash(custom)', 'Skill(task-router)'])
        self.assertTrue(self.manifest.exists())
        self.env.pop('PYTHONPATH')
        result, _ = self.cleanup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'], ['Bash(custom)', 'Skill(task-router)'])
        self.assertFalse(self.receipt.exists())

    def test_receipt_finalization_failure_keeps_pending_until_retry(self):
        self.install()
        self.inject(f'''_unlink = Path.unlink
def unlink(path, *args, **kwargs):
    if path == Path({str(self.receipt)!r}):
        raise PermissionError('receipt finalization denied')
    return _unlink(path, *args, **kwargs)
Path.unlink = unlink
''')
        result, report = self.cleanup()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(report['hooks_detached'])
        self.assertIn('pending_detach', json.loads(self.receipt.read_text()))
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'], ['Bash(custom)'])
        self.assertTrue(self.manifest.exists())
        self.env.pop('PYTHONPATH')
        result, _ = self.cleanup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.receipt.exists())

    def test_missing_codex_directory_is_reported_as_skipped(self):
        result = self.run_install('task-router', platform='codex')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.home / '.agents/skills/task-router').exists())
        self.assertIn('Hooks: skipped (platform directory missing)', result.stdout)
        self.assertNotIn('hooks=true', result.stdout)
        self.assertNotIn('Hooks: enabled', result.stdout)


if __name__ == '__main__':
    unittest.main()
