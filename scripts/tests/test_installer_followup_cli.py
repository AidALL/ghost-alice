"""Installer recovery contracts through the real outer CLI and temporary homes."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / '_shared'))
from installer_testing import checkout_copy, destination_fixture, inject_installer_python, installer_env, run_pty
from installer.source import DEFAULT_REMOTE


class InstallerFollowupCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = checkout_copy(self)
        self.home = self.root / 'home'
        self.home.mkdir()
        (self.home / '.claude').mkdir()
        self.env = installer_env(self.home)
        # An archive fixture must not discover the repository enclosing the test runner's TMPDIR.
        self.env['GIT_CEILING_DIRECTORIES'] = str(self.root)
        # Archives without a Git origin fetch the default release from this test's main branch.
        subprocess.run(['git', 'config', '--global', f'url.{self.source.as_uri()}.insteadOf', DEFAULT_REMOTE],
                       env=self.env, check=True)
        self.env['GHOST_ALICE_INSTALL_LOCALE'] = 'en_US.UTF-8'
        self.settings = self.home / '.claude/settings.json'
        self.settings.write_text(json.dumps({'custom': 'preserved', 'permissions': {'allow': ['Bash(custom)']}}))
        self.receipt = self.home / '.ghost-alice/addon-permissions/claude-v2.json'
        self.manifest = self.home / '.ghost-alice/install-state/claude.json'

    def run_install(self, *args, platform='claude'):
        return subprocess.run(['bash', str(self.source / 'install.sh'), *(['--platform', platform] if platform else []),
                               '--skip-source-health', *args], env=self.env, text=True,
                              capture_output=True, timeout=90)

    def install(self, *args):
        result = self.run_install('task-router', *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.receipt.is_file())

    def test_final_codex_skill_restores_settings_and_archives_host_queue_before_full_retry(self):
        codex = self.home / '.codex'
        codex.mkdir()
        config = codex / 'config.toml'
        original = '# my model\nmodel = "fixture"\n[features]\nhooks = false\n'
        config.write_text(original)
        installed = self.run_install('task-router', platform='codex')
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        ghost = self.home / '.ghost-alice'
        pending = ghost / 'pending-merges/codex'
        personal = ghost / 'hooks/personal.mjs'
        personal.write_text('// personal hook')
        (pending / 'review.bak').write_text('original edit')
        for args in [('--uninstall', 'task-router'), ('--uninstall',), ('--uninstall',)]:
            result = self.run_install(*args, platform='codex')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(tomllib.loads(config.read_text()), tomllib.loads(original))
            self.assertIn('# my model', config.read_text())
            self.assertFalse((ghost / 'install-state/codex.json').exists())
            self.assertFalse(pending.exists())
            self.assertFalse(list((ghost / 'install-state').glob('codex-*-change.json')))
            self.assertEqual(personal.read_text(), '// personal hook')
            archive, = (ghost / 'uninstall-backup/codex/support-state').glob('pending-merges-*/original/review.bak')
            self.assertEqual(archive.read_text(), 'original edit')

    def test_full_removal_uses_old_sidecars_after_final_skill_already_retired_inventory(self):
        codex = self.home / '.codex'
        codex.mkdir()
        config = codex / 'config.toml'
        config.write_text('model = "my model"\n')
        installed = self.run_install('task-router', platform='codex')
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        ghost = self.home / '.ghost-alice'
        pending = ghost / 'pending-merges/codex'
        dispatcher = ghost / 'hooks/ghost-alice-hook.mjs'
        state = ghost / 'install-state'
        sidecars = [*state.glob('codex-*-change.json'), state / 'source-repo-hook-path.json']
        self.assertEqual(len(sidecars), 3)
        receipts = {path: path.read_bytes() for path in [*sidecars, dispatcher]}
        queue_manifest = (pending / 'manifest.json').read_bytes()
        selected = self.run_install('--uninstall', 'task-router', platform='codex')
        self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
        self.assertFalse((state / 'codex.json').exists())
        # Older selected removal retired the inventory but left the change records, active queue and dispatcher.
        for path, content in receipts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        pending.mkdir(parents=True, exist_ok=True)
        (pending / 'manifest.json').write_bytes(queue_manifest)
        (pending / 'review.bak').write_text('preserved')
        config.write_text('model = "my model"\n[features]\nhooks = true\n'
                          f'[projects.{json.dumps(str(self.source))}]\ntrust_level = "trusted"\n')
        personal = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo mine'}]}]}}
        (codex / 'hooks.json').write_text(json.dumps(personal))
        for _ in range(2):
            full = self.run_install('--uninstall', platform='codex')
            self.assertEqual(full.returncode, 0, full.stdout + full.stderr)
            self.assertEqual(tomllib.loads(config.read_text()), {'model': 'my model', 'features': {'hooks': True}})
            self.assertEqual(json.loads((codex / 'hooks.json').read_text()), personal)
            self.assertFalse(any(path.exists() for path in receipts))
            self.assertFalse(pending.exists())
        archive, = (ghost / 'uninstall-backup/codex/support-state').glob('pending-merges-*/original/review.bak')
        self.assertEqual(archive.read_text(), 'preserved')

    def test_full_removal_retires_only_the_installed_dispatcher(self):
        self.install()
        hooks = self.home / '.ghost-alice/hooks'
        personal = hooks / 'personal.mjs'
        personal.write_text('// my hook')
        dispatcher = hooks / 'ghost-alice-hook.mjs'
        installed = dispatcher.read_bytes()
        (self.source / '_shared/ghost-alice-hook.mjs').write_text('// newer checkout')
        dispatcher.write_text('// local edit')
        held = self.run_install('--uninstall')
        self.assertEqual(held.returncode, 1, held.stdout + held.stderr)
        self.assertIn('dispatcher-ownership-unresolved', held.stdout)
        self.assertEqual(dispatcher.read_text(), '// local edit')
        self.assertTrue(self.manifest.is_file())
        dispatcher.write_bytes(installed)
        result = self.run_install('--uninstall')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(personal.is_file())
        self.assertEqual(personal.read_text(), '// my hook')
        self.assertFalse(dispatcher.exists())

    def test_other_host_personal_hook_keeps_dispatcher_after_checkout_changes(self):
        (self.home / '.codex').mkdir()
        installed = self.run_install('task-router', platform='codex')
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        dispatcher = self.home / '.ghost-alice/hooks/ghost-alice-hook.mjs'
        original = dispatcher.read_bytes()
        (self.source / '_shared/ghost-alice-hook.mjs').write_text('// newer checkout')
        personal = {'hooks': {'Stop': [{'hooks': [{'command': f'node "{dispatcher.as_posix()}"'}]}]}}
        self.settings.write_text(json.dumps(personal))
        selected = self.run_install('--uninstall', 'task-router', platform='codex')
        self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
        self.assertTrue(dispatcher.is_file())
        self.assertEqual(dispatcher.read_bytes(), original)
        self.assertEqual(json.loads(self.settings.read_text()), personal)

    def test_full_removal_restores_the_feature_from_its_sidecar_when_omitted_from_inventory(self):
        codex = self.home / '.codex'
        codex.mkdir()
        config = codex / 'config.toml'
        config.write_text('[features]\nhooks = false\n')
        installed = self.run_install('task-router', platform='codex')
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        manifest = self.manifest.with_name('codex.json')
        inventory = json.loads(manifest.read_text())
        inventory['system_env_changes'] = [entry for entry in inventory['system_env_changes']
                                            if entry['kind'] != 'codex_hooks_feature_flag']
        manifest.write_text(json.dumps(inventory))
        self.assertTrue(tomllib.loads(config.read_text())['features']['hooks'])
        full = self.run_install('--uninstall', platform='codex')
        self.assertEqual(full.returncode, 0, full.stdout + full.stderr)
        self.assertFalse(tomllib.loads(config.read_text())['features']['hooks'])
        self.assertFalse(manifest.with_name('codex-hook-feature-change.json').exists())

    def test_final_skill_recognizes_recorded_relative_git_hooks_for_the_same_repository(self):
        self.install()
        receipt = self.manifest.with_name('source-repo-hook-path.json')
        inventory = json.loads(self.manifest.read_text())
        change = json.loads(receipt.read_text())
        change.update(after='hooks', before_present=True, before='personal/hooks')
        receipt.write_text(json.dumps(change))
        for entry in inventory['system_env_changes']:
            if entry['kind'] == change['kind']:
                entry.update(change)
        self.manifest.write_text(json.dumps(inventory))
        git = ['git', '-C', str(self.source), 'config', '--local']
        subprocess.run([*git, 'core.hooksPath', 'hooks'], env=self.env, check=True)
        result = self.run_install('--uninstall', 'task-router')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        restored = subprocess.check_output([*git, '--get', 'core.hooksPath'], env=self.env, text=True)
        self.assertEqual(restored.strip(), 'personal/hooks')
        self.assertFalse(receipt.exists())
        self.assertFalse(self.manifest.exists())

    def test_unreadable_record_config_allows_known_fixed_hooks_to_detach_and_retains_retry_records(self):
        self.install()
        config = self.home / '.ghost-alice/config.json'
        original = config.read_bytes()
        config.write_bytes(b'{broken')
        result = self.run_install('--uninstall')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('could not read the existing work record locations', result.stdout)
        settings = json.loads(self.settings.read_text())
        self.assertFalse(any(settings['hooks'].values()))
        self.assertEqual(settings['custom'], 'preserved')
        self.assertEqual(settings['permissions']['allow'], ['Bash(custom)'])
        self.assertEqual(config.read_bytes(), b'{broken')
        self.assertFalse((self.home / '.claude/skills/task-router').exists())
        self.assertFalse((self.home / '.claude/skills/_shared').exists())
        self.assertTrue(self.manifest.is_file())
        report_path = next((self.home / '.ghost-alice/uninstall-reports').glob('*.json'))
        outcome = json.loads(report_path.read_text())
        self.assertTrue(outcome['partial'])
        self.assertTrue(outcome['hooks_detached'])
        self.assertTrue(any(item.get('reason') == 'record-source-reference-retention-failed'
                            and item.get('errors') for item in outcome['items']))
        config.write_bytes(original)
        retry = self.run_install('--uninstall')
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        settings = json.loads(self.settings.read_text())
        self.assertFalse(any(settings['hooks'].values()))
        self.assertEqual(settings['custom'], 'preserved')
        self.assertEqual(settings['permissions']['allow'], ['Bash(custom)'])
        self.assertFalse((self.home / '.claude/skills/_shared').exists())
        self.assertFalse(self.manifest.exists())

    def test_unreadable_notice_config_does_not_block_last_skill_removal(self):
        self.install()
        config = self.home / '.ghost-alice/config.json'
        original = config.read_bytes()
        payload = self.home / '.ghost-alice/installed-payload-v2.json'
        support = {path for path, entry in json.loads(payload.read_text())['targets'].items()
                   if entry['kind'] in {'command', 'runtime'}}
        config.write_bytes(b'{broken')
        result = self.run_install('--uninstall', 'task-router')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('could not read the existing work record locations', result.stdout)
        self.assertFalse(any(json.loads(self.settings.read_text())['hooks'].values()))
        self.assertFalse((self.home / '.claude/skills/task-router').exists())
        self.assertEqual(config.read_bytes(), b'{broken')
        for path in support:
            self.assertTrue(Path(path).exists(), path)
            self.assertIn(path, result.stdout)
            self.assertIn('claude', json.loads(payload.read_text())['targets'][path]['used_by'])
        self.assertIn(str(self.manifest), result.stdout)
        self.assertIn('core.hooksPath', result.stdout)
        hook = subprocess.run(['git', '-C', str(self.source), 'config', '--local', '--get', 'core.hooksPath'],
                              env=self.env, text=True, capture_output=True)
        self.assertEqual(hook.returncode, 0)
        self.assertIn('run the same removal command again', result.stdout)
        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings['custom'], 'preserved')
        self.assertEqual(settings['permissions']['allow'], ['Bash(custom)'])
        config.write_bytes(original)
        retry = self.run_install('--uninstall', 'task-router')
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertFalse(self.manifest.exists())
        self.assertEqual(json.loads(payload.read_text())['targets'], {})
        self.assertIsNone(json.loads(payload.read_text())['installer']['active_root'])
        for path in support:
            self.assertFalse(Path(path).exists(), path)
        self.assertEqual(json.loads(self.settings.read_text()), settings)
        hook = subprocess.run(['git', '-C', str(self.source), 'config', '--local', '--get', 'core.hooksPath'],
                              env=self.env, text=True, capture_output=True)
        self.assertEqual(hook.returncode, 1)

    def _non_hook_hold_then_other_host_then_retry(self, failure):
        self.install()
        (self.home / '.codex').mkdir()
        installed = self.run_install('task-router', platform='codex')
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        ghost = self.home / '.ghost-alice'
        payload = ghost / 'installed-payload-v2.json'
        support = {path for path, entry in json.loads(payload.read_text())['targets'].items()
                   if entry['kind'] in {'command', 'runtime'}}
        if failure == 'retirement':
            group = 'a' * 32
            folder = ghost / 'deprecated-skill-backups' / group
            folder.mkdir(parents=True)
            retired = self.home / '.claude/skills/retired-fixture-skill'
            (folder / 'work.json').write_text(json.dumps(dict(schema_version=1, id=group,
                source_path=str(retired), platform='claude', backup_path=str(folder / retired.name),
                provider='core', kind='skill', phase='prepared', ownership='ghost-alice-managed',
                content_identity='0' * 64, entry_identity=[1, 2], pending_id='retired-' + group)))
        elif failure == 'manifest-delete':
            self.inject(f'''_unlink = Path.unlink
def unlink(path, *args, **kwargs):
    if path == Path({str(self.manifest)!r}):
        raise PermissionError('final inventory removal denied')
    return _unlink(path, *args, **kwargs)
Path.unlink = unlink
''')
        elif failure == 'record-write':
            self.inject(f'''import sys
sys.path.insert(0, {str(self.source / '_shared')!r})
import installed_payload
_release = installed_payload.release_target
def release(root, *, destination, provider, platform):
    if platform == 'claude' and Path(destination).name == 'ghost-alice':
        raise PermissionError('later command reference write denied')
    return _release(root, destination=destination, provider=provider, platform=platform)
installed_payload.release_target = release
''')
        else:
            pending = ghost / 'pending-merges/claude'
            pending.mkdir(parents=True, exist_ok=True)
            (pending / 'original.txt').write_text('user original')
            self.inject(f'''import shutil
_copytree = shutil.copytree
def copytree(source, *args, **kwargs):
    if Path(source) == Path({str(pending)!r}):
        raise PermissionError('pending original copy denied')
    return _copytree(source, *args, **kwargs)
shutil.copytree = copytree
''')
        held, outcome = self.cleanup()
        self.assertEqual(held.returncode, 1, held.stdout + held.stderr)
        self.assertTrue(outcome['hooks_detached'])
        self.assertFalse((self.home / '.claude/skills/task-router').exists())
        entries = json.loads(payload.read_text())['targets']
        for path in support:
            self.assertTrue(Path(path).exists(), path)
            self.assertIn(path, entries, 'A retained file must keep a recorded owner')
            self.assertEqual(entries[path]['used_by'], ['claude', 'codex'])
        independent = self.run_install('--uninstall', platform='codex')
        self.assertEqual(independent.returncode, 0, independent.stdout + independent.stderr)
        self.assertFalse((ghost / 'install-state/codex.json').exists())
        entries = json.loads(payload.read_text())['targets']
        for path in support:
            self.assertTrue(Path(path).exists(), path)
            self.assertEqual(entries[path]['used_by'], ['claude'])
        if failure == 'retirement':
            shutil.rmtree(folder)  # Resolve only this synthetic receipt; not a product recovery assertion.
        else:
            inject_installer_python(self.env)
        retry = self.run_install('--uninstall')
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertFalse(self.manifest.exists())
        self.assertFalse(payload.exists())
        for path in support:
            self.assertFalse(Path(path).exists(), path)
        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings['custom'], 'preserved')
        self.assertEqual(settings['permissions']['allow'], ['Bash(custom)'])

    def test_retirement_hold_then_other_host_removal_then_retry_has_no_orphans(self):
        self._non_hook_hold_then_other_host_then_retry('retirement')

    def test_pending_copy_failure_then_other_host_removal_then_retry_has_no_orphans(self):
        self._non_hook_hold_then_other_host_then_retry('pending-copy')

    def test_later_reference_write_failure_then_other_host_removal_then_retry_has_no_orphans(self):
        self._non_hook_hold_then_other_host_then_retry('record-write')

    def test_final_inventory_failure_keeps_shared_retry_dependency_set(self):
        self._non_hook_hold_then_other_host_then_retry('manifest-delete')

    @unittest.skipUnless(sys.platform == 'darwin' or os.name == 'nt', 'requires a native immutable or read-only file')
    def test_locked_inventory_restores_commands_and_allows_both_host_removals(self):
        import installed_payload
        # Only Codex owns the later wrappers. Removing it reaches the physical
        # command boundary while the other installation still uses the fixed entry.
        for name in ('install.cmd', 'install.ps1'):
            (self.source / name).rename(self.root / name)
        self.install('--local-source')
        for name in ('install.cmd', 'install.ps1'):
            (self.root / name).rename(self.source / name)
        (self.home / '.codex').mkdir()
        result = self.run_install('task-router', '--local-source', platform='codex')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ghost = self.home / '.ghost-alice'
        inventory = ghost / 'install-state/codex.json'
        payload = installed_payload.path(ghost)
        entry = ghost / 'bin/ghost-alice'
        argv = ['bash', str(entry), '--platform', 'codex', '--skip-source-health', '--uninstall']
        def remove(command):
            return subprocess.run(command, env=self.env, cwd=self.root, capture_output=True, text=True, timeout=90)

        # Let skill, hook and inventory-row removal finish before locking the
        # now-empty inventory so the next failure occurs at the final boundary.
        self.inject(f'''_unlink = Path.unlink
def unlink(path, *args, **kwargs):
    if path == Path({str(entry.with_suffix('.cmd'))!r}):
        raise PermissionError('hold final command')
    return _unlink(path, *args, **kwargs)
Path.unlink = unlink
''')
        preparation = remove(argv)
        self.assertEqual(preparation.returncode, 1, preparation.stdout + preparation.stderr)
        self.assertIn('hold final command', preparation.stdout)
        inject_installer_python(self.env)
        self.assertEqual(json.loads(inventory.read_text())['targets'], [])
        original = payload.read_bytes()

        def set_lock(file, locked):
            if os.name == 'nt':
                file.chmod(stat.S_IREAD if locked else stat.S_IREAD | stat.S_IWRITE)
            else:
                flags = file.stat().st_flags
                os.chflags(file, flags | stat.UF_IMMUTABLE if locked else flags & ~stat.UF_IMMUTABLE)
        def unlock_fixture():
            for file in (inventory, *(ghost / 'rollbacks').glob('*/metadata-*')):
                if file.is_file():
                    set_lock(file, False)
        self.addCleanup(unlock_fixture)
        set_lock(inventory, True)
        failed = remove(argv)
        with self.subTest(stage='locked'):
            self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
            self.assertIn(str(inventory), failed.stdout)
            self.assertIn('saved files and records were restored', failed.stdout)
            self.assertEqual(payload.read_bytes(), original)
            self.assertTrue(all(entry.with_suffix(suffix).is_file() for suffix in ('.cmd', '.ps1')))

        set_lock(inventory, False)
        retried = remove(argv)
        with self.subTest(stage='same command after unlocking'):
            self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
            self.assertFalse(inventory.exists())
            self.assertFalse(any(entry.with_suffix(suffix).exists() for suffix in ('.cmd', '.ps1')))
            self.assertEqual(json.loads(payload.read_text())['targets'][str(entry)]['used_by'], ['claude'])

        other = remove([part if part != 'codex' else 'claude' for part in argv])
        self.assertEqual(other.returncode, 0, other.stdout + other.stderr)
        self.assertFalse(self.manifest.exists())
        self.assertFalse(entry.exists())

    def test_same_removal_retries_only_its_committed_metadata_cleanup_without_manifest(self):
        self._check_completed_metadata_retry('cleanup-api')

    def test_same_removal_retries_after_journal_unlink_fails_after_backup_copies_are_deleted(self):
        self._check_completed_metadata_retry('journal-unlink')

    def test_same_outer_uninstall_retries_after_completed_journal_directory_removal_fails(self):
        self._check_completed_metadata_retry('directory-rmdir')

    def _check_completed_metadata_retry(self, failure):
        import content_digest
        import install_transaction
        self.install()
        (self.home / '.codex').mkdir()
        installed = self.run_install('task-router', platform='codex')
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        ghost = self.home / '.ghost-alice'
        self.inject(f'''import sys
sys.path.insert(0, {str(self.source / '_shared')!r})
import install_transaction
_cleanup = install_transaction.cleanup_completed
def cleanup(*args, **kwargs):
    if kwargs.get('txids') is not None:
        failure = {failure!r}
        if failure == 'cleanup-api':
            raise OSError('completed metadata cleanup denied')
        method = 'unlink' if failure == 'journal-unlink' else 'rmdir'
        original = getattr(Path, method)
        def denied(path, *values, **options):
            selected = (path.name == 'journal.json' and path.parent.name in kwargs['txids']
                        if failure == 'journal-unlink' else path.name in kwargs['txids'])
            if selected:
                raise PermissionError('completed metadata deletion denied')
            return original(path, *values, **options)
        setattr(Path, method, denied)
        try:
            return _cleanup(*args, **kwargs)
        finally:
            setattr(Path, method, original)
    return _cleanup(*args, **kwargs)
install_transaction.cleanup_completed = cleanup
''')
        def remove():
            if failure != 'directory-rmdir':
                return self.cleanup()
            result = self.run_install('--uninstall')
            reports = list((ghost / 'uninstall-reports').glob('*.json'))
            return result, json.loads(max(reports, key=lambda path: path.stat().st_mtime_ns).read_text())
        first, report = remove()
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertFalse(self.manifest.exists())
        row = next(item for item in report['items'] if item.get('reason') == 'completed-reference-journal-cleanup-failed')
        journal = Path(row['path'])
        saved = json.loads((journal / 'journal.json').read_text())
        self.assertEqual(saved['phase'], 'done')
        if failure != 'cleanup-api':
            self.assertTrue(saved['metadata_records'])
            self.assertTrue(all(not (journal / item['copy']).exists() for item in saved['metadata_records'] if item['copy']))
        references = [item for item in report['items'] if item.get('target_name') in {'management-command', 'runtime-core'}]
        self.assertTrue(references)
        self.assertTrue(all(item['action'] == 'reference-released' for item in references))
        payload = ghost / 'installed-payload-v2.json'
        committed = payload.read_bytes()
        source, destination = self.root / 'old-records', self.root / 'new-records'
        source.mkdir()
        (source / 'user-history').write_text('preserved original records')
        shutil.copytree(source, destination)
        unrelated = install_transaction.Journal.begin(dict(ghost_root=str(ghost), host='installer'), [])
        unrelated.data['source_cleanup'] = dict(path=str(source), destination=str(destination),
            baseline=content_digest.state(source, include_generated=True))
        unrelated.mark('done')
        preserved = {path: path.read_bytes() for root in (source, unrelated.folder)
                     for path in root.rglob('*') if path.is_file()}
        dry, preview = self.cleanup(apply=False)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertTrue(journal.exists())
        self.assertTrue(any(item['action'] == 'would-remove' and item.get('path') == str(journal)
                            for item in preview['items']))
        failed, failure_report = remove()
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertTrue(journal.exists())
        self.assertTrue(failure_report['removal_incomplete'])
        inject_installer_python(self.env)
        retry, result = remove()
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertFalse(journal.exists())
        self.assertTrue(any(item['action'] == 'removed' and item.get('path') == str(journal)
                            for item in result['items']))
        self.assertFalse(result['removal_incomplete'])
        self.assertEqual(payload.read_bytes(), committed)
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)
        self.assertFalse(self.manifest.exists())
        self.assertTrue((ghost / 'install-state/codex.json').exists())

    def test_addon_permission_commit_remains_reported_when_receipt_finalization_fails(self):
        self.install('--addon-source', str(self.source / '_shared/tests/fixtures/dummy-addon'))
        self.inject(f'''import sys
sys.path.insert(0, {str(self.source / '_shared')!r})
import addon_skill_permissions
_save = addon_skill_permissions._save
def save(data):
    if 'noop' not in data.get('addons', {{}}) and 'pending_detach' not in data:
        raise PermissionError('addon receipt finalization denied')
    return _save(data)
addon_skill_permissions._save = save
''')
        result, outcome = self.cleanup()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn('Skill(noop)', json.loads(self.settings.read_text())['permissions']['allow'])
        permission = next(row for row in outcome['items'] if row.get('addon_id') == 'noop'
                          and row.get('name') == 'Skill(noop)')
        self.assertEqual(permission['action'], 'removed')
        self.assertTrue((self.home / '.claude/skills/noop').exists())
        self.assertTrue((self.home / '.ghost-alice/addons/claude/noop.json').exists())

    def test_addon_permission_effect_remains_reported_when_later_hook_step_fails(self):
        self.install('--addon-source', str(self.source / '_shared/tests/fixtures/dummy-addon'))
        self.inject(f'''import sys
sys.path.insert(0, {str(self.source / '_shared')!r})
import addon_uninstall
_hooks = addon_uninstall._addon_hook_items
def hooks(*args, **kwargs):
    if kwargs.get('apply_changes'):
        raise PermissionError('later addon hook failed')
    return _hooks(*args, **kwargs)
addon_uninstall._addon_hook_items = hooks
''')
        result, outcome = self.cleanup()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn('Skill(noop)', json.loads(self.settings.read_text())['permissions']['allow'])
        permission = next(row for row in outcome['items'] if row.get('addon_id') == 'noop'
                          and row.get('name') == 'Skill(noop)')
        self.assertEqual(permission['action'], 'removed')
        self.assertTrue((self.home / '.claude/skills/noop').exists())
        self.assertTrue((self.home / '.ghost-alice/addons/claude/noop.json').exists())

    def test_other_host_hold_does_not_fail_independent_removal_or_lose_shared_retry_ownership(self):
        self.install()
        (self.home / '.codex').mkdir()
        result = self.run_install('task-router', platform='codex')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ghost = self.home / '.ghost-alice'
        source = self.home / 'old-records'
        source.mkdir()
        (source / 'history.txt').write_text('retired work records')
        config = ghost / 'config.json'
        saved = json.loads(config.read_text())
        from install_hooks import install_hook, installed_hook_ledger_roots
        bound = dict(saved['install_locations']['claude'], mode='isolated', ledger_root=str(source),
                     project_root=str(self.home))
        with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            installed = install_hook('claude', destination=bound)
            self.assertIn(installed.legacy_status, ('installed', 'already'))
            self.assertEqual(set(installed_hook_ledger_roots('claude')), {str(source)})
        saved['retired_ledger_roots'] = [str(source)]
        config.write_text(json.dumps(saved))
        payload = ghost / 'installed-payload-v2.json'
        support = {path for path, entry in json.loads(payload.read_text())['targets'].items()
                   if entry['kind'] in {'command', 'runtime'}}
        held = self.run_install('--uninstall')
        self.assertEqual(held.returncode, 1, held.stdout + held.stderr)
        self.assertTrue(any(json.loads(self.settings.read_text())['hooks'].values()))
        self.assertIn(str(source), held.stdout)
        for path in support:
            self.assertTrue(Path(path).exists(), path)
            self.assertEqual(json.loads(payload.read_text())['targets'][path]['used_by'], ['claude', 'codex'])
        independent = self.run_install('--uninstall', platform='codex')
        self.assertEqual(independent.returncode, 0, independent.stdout + independent.stderr)
        self.assertFalse((ghost / 'install-state/codex.json').exists())
        self.assertTrue(self.manifest.exists())
        for path in support:
            self.assertEqual(json.loads(payload.read_text())['targets'][path]['used_by'], ['claude'])
        # The saved config now preserves the relationship that was previously hook-only.
        saved['install_locations']['claude']['ledger_root'] = str(source)
        config.write_text(json.dumps(saved))
        retry = self.run_install('--uninstall')
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertFalse(self.manifest.exists())
        self.assertFalse(payload.exists())
        for path in support:
            self.assertFalse(Path(path).exists(), path)
        self.assertEqual((source / 'history.txt').read_text(), 'retired work records')

    def inject(self, source):
        inject_installer_python(self.env, source)

    def test_reinstall_reports_corrupt_core_skill_hosts_without_changing_installed_files(self):
        import content_digest
        import installed_payload
        self.install()
        ghost = self.home / '.ghost-alice'
        detail = installed_payload.read_path(ghost)
        record = json.loads(detail.read_bytes())
        target = self.home / '.claude/skills/task-router'
        for locale, invalid, expected in [('en', None, 'list of host names'), ('ko', 'claude', '호스트 이름의 목록')]:
            with self.subTest(locale=locale):
                record['targets'][str(target)]['used_by'] = invalid
                detail.write_text(json.dumps(record))
                before = content_digest.state(self.home, include_generated=True)
                self.env['GHOST_ALICE_INSTALL_LOCALE'] = locale
                result = self.run_install('task-router')
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                for text in (str(detail), str(target), 'used_by', expected):
                    self.assertIn(text, output)
                self.assertNotIn('Traceback', output)
                self.assertEqual(content_digest.state(self.home, include_generated=True), before)

    def test_reinstall_keeps_latest_success_log_and_a_failed_retry_preserves_it(self):
        logs = self.home / '.ghost-alice/install'
        self.install()
        first = next(logs.glob('*.log'))
        self.install()
        kept = list(logs.glob('*.log'))
        self.assertEqual(len(kept), 1, 'Repeated success from unchanged code must not accumulate detail logs')
        successful = kept[0]
        self.assertNotEqual(successful, first)
        result = json.loads(successful.with_suffix('.events.jsonl').read_text().splitlines()[-1])
        active = json.loads((self.home / '.ghost-alice/installed-payload-v2.json').read_text())['installer']['active_root']
        self.assertEqual(result, dict(type='install-result', source_digest=Path(active).parent.name, returncode=0))
        previous_bytes = successful.read_bytes()
        self.inject('''import sys
entry = Path(sys.argv[0]).resolve()
if entry.name == 'cli.py' and entry.parent.name == 'installer':
    sys.path.insert(0, str(entry.parent.parent))
    from installer import transaction
    def fail(*args, **kwargs):
        raise OSError('fixture installation failure')
    transaction.sync_runtime_shared = fail
''')
        failed = self.run_install('task-router')
        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self.assertEqual(successful.read_bytes(), previous_bytes)
        remaining = set(logs.glob('*.log')) - {successful}
        self.assertEqual(len(remaining), 1)
        failed_log = remaining.pop()
        outcome = json.loads(failed_log.with_suffix('.events.jsonl').read_text().splitlines()[-1])
        self.assertEqual(outcome['returncode'], failed.returncode)
        self.assertIn('fixture installation failure', failed_log.read_text())

    def test_auto_reinstall_retains_both_hosts_in_one_latest_success_log(self):
        (self.home / '.codex').mkdir()
        logs = self.home / '.ghost-alice/install'
        first = None
        for _ in range(2):
            result = self.run_install('task-router', platform=None)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            kept = list(logs.glob('*.log'))
            self.assertEqual(len(kept), 1)
            self.assertNotEqual(kept[0], first)
            first = kept[0]
            self.assertIn(str(first), result.stdout)
            detail = first.read_text()
            for host in ('claude', 'codex'):
                self.assertIn(f'Starting install for {host}', detail)
            events = [json.loads(line) for line in first.with_suffix('.events.jsonl').read_text().splitlines()]
            self.assertEqual({row['platform'] for row in events if row['type'] == 'platform-result'}, {'claude', 'codex'})
            self.assertEqual([row['returncode'] for row in events if row['type'] == 'install-result'], [0])

    @unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'normal POSIX termination through the terminal installer')
    def test_platform_uses_fresh_selection_with_terminal_stdin_and_piped_stdout(self):
        (self.home / '.codex').mkdir()
        self.inject('''import select, sys
entry = Path(sys.argv[0]).resolve()
if entry.name == 'cli.py' and entry.parent.name == 'installer' and not os.environ.get('GHOST_ALICE_INSTALL_REQUEST'):
    print('BEFORE_PLATFORM_QUESTION', flush=True)
    assert select.select([sys.stdin], [], [], 5)[0], 'No queued input received'
''')
        captured = self.root / 'redirected-output.txt'
        command = ['bash', '-o', 'pipefail', '-c',
                   'bash "$1" --prompt-platform --skip-source-health task-router | tee "$2"',
                   'fixture', str(self.source / 'install.sh'), str(captured)]
        code, output = run_pty(command, env=self.env, cwd=self.home,
                              responses=[('BEFORE_PLATFORM_QUESTION', '1\n'), ('Enter number or name:', '2\n')])
        self.assertEqual(code, 0, output)
        self.assertTrue((self.home / '.ghost-alice/install-state/codex.json').is_file())
        self.assertFalse(self.manifest.exists())
        self.assertIn('Enter number or name:', captured.read_text())

    def test_source_replacement_uses_fresh_refusal_and_preserves_the_record(self):
        self.install()
        record = self.home / '.ghost-alice/installed-payload-v2.json'
        before = record.read_bytes()
        other = self.root / 'other-source'
        shutil.copytree(self.source, other, ignore=shutil.ignore_patterns('.git', '.tmp'))
        self.inject('''import select, sys
entry = Path(sys.argv[0]).resolve()
if entry.name == 'cli.py' and entry.parent.name == 'installer' and not os.environ.get('GHOST_ALICE_INSTALL_REQUEST'):
    print('BEFORE_SOURCE_QUESTION', flush=True)
    assert select.select([sys.stdin], [], [], 5)[0], 'No queued input received'
''')
        code, output = run_pty(['bash', str(other / 'install.sh'), '--platform', 'claude', '--skip-source-health', 'task-router'],
                              env=self.env, cwd=self.home,
                              responses=[('BEFORE_SOURCE_QUESTION', 'y\n'), ('Replace the install source with this checkout?', 'n\n')])
        self.assertEqual(code, 2, output)
        self.assertEqual(record.read_bytes(), before)

    def test_trust_question_uses_fresh_refusal_and_keeps_untrusted_configuration(self):
        import tomllib
        codex = self.home / '.codex'
        codex.mkdir()
        config = codex / 'config.toml'
        project = str(self.source.resolve())
        config.write_text(f'[projects.{json.dumps(project)}]\ntrust_level = "untrusted"\n')
        self.inject('''import select, sys
entry = Path(sys.argv[0]).resolve()
if entry.name == 'cli.py' and entry.parent.name == 'installer' and not os.environ.get('GHOST_ALICE_INSTALL_REQUEST'):
    print('BEFORE_TRUST_QUESTION', flush=True)
    assert select.select([sys.stdin], [], [], 5)[0], 'No queued input received'
''')
        code, output = run_pty(['bash', str(self.source / 'install.sh'), '--platform', 'codex', '--skip-source-health', 'task-router'],
                              env=self.env, cwd=self.home,
                              responses=[('BEFORE_TRUST_QUESTION', 'y\n'), ('Change this project to trusted?', 'n\n')])
        self.assertEqual(code, 0, output)
        self.assertEqual(tomllib.loads(config.read_text())['projects'][project]['trust_level'], 'untrusted')
        self.assertTrue((self.home / '.ghost-alice/install-state/codex.json').is_file())

    @unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX terminal-owning ancestor observation')
    def test_terminal_owning_writer_ancestor_blocks_installation_without_being_stopped(self):
        parent = self.root / 'session_intent_ledger.py'
        survived = self.root / 'ancestor-returned'
        terminated = self.root / 'ancestor-terminated'
        parent.write_text('import fcntl, os, signal, subprocess, sys, termios\nfrom pathlib import Path\n'
                          'fcntl.ioctl(sys.stdin.fileno(), termios.TIOCSCTTY, 0)\n'
                          'os.environ["FIXTURE_TERMINAL_PARENT"] = str(os.getpid())\n'
                          f'def stop(*args):\n    Path({str(terminated)!r}).touch()\n    sys.exit(90)\n'
                          'signal.signal(signal.SIGTERM, stop)\n'
                          f'result = subprocess.run(["bash", {str(self.source / "install.sh")!r}, "--platform", "claude", '
                          '"--skip-source-health", "task-router"])\n'
                          f'Path({str(survived)!r}).write_text(str(result.returncode))\n')
        self.inject('''import sys
entry = Path(sys.argv[0]).resolve()
if entry.name == 'cli.py' and entry.parent.name == 'installer':
    sys.path.insert(0, str(entry.parent.parent))
    from installer import hosts, runner
    original = runner.run_command
    def inspect_own_ancestry(argv, **kwargs):
        if argv[:3] != ['ps', '-ww', '-U']:
            return original(argv, **kwargs)
        ancestor = int(os.environ['FIXTURE_TERMINAL_PARENT'])
        pid = os.getpid()
        pids = []
        while pid and pid not in pids:
            pids.append(pid)
            if pid == ancestor:
                break
            pid = int(_run(['ps', '-p', str(pid), '-o', 'ppid='], capture_output=True, text=True, check=True).stdout)
        assert pids[-1] == ancestor, 'Fixture parent must be an actual installer ancestor'
        result = _run(['ps', '-ww', '-p', ','.join(map(str, pids)), '-o', 'pid=,ppid=,comm='], capture_output=True, text=True)
        return runner.ToolResult(result.returncode, result.stdout, result.stderr)
    hosts.runner.run_command = inspect_own_ancestry
''')
        code, output = run_pty([sys.executable, str(parent), '--platform', 'claude'], env=self.env,
                              cwd=self.home, responses=[])
        self.assertEqual(code, 0, output)
        self.assertEqual(survived.read_text(), '1')
        self.assertFalse(terminated.exists())
        self.assertIn('independent terminal', output)
        self.assertIn('session_intent_ledger.py', output)
        self.assertNotIn('[y/N]', output)
        self.assertFalse((self.home / '.ghost-alice/versions').exists())
        self.assertFalse(self.manifest.exists())

    @unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'normal POSIX termination through the terminal installer')
    def test_terminal_confirmation_closes_a_fixture_writer_before_placing_the_candidate(self):
        script = self.root / 'writer.py'
        saved, exited = self.root / 'saved', self.root / 'exited'
        versions = self.home / '.ghost-alice/versions'
        script.write_text('import signal, sys\nfrom pathlib import Path\n'
                          'def stop(signum, frame):\n'
                          f'    Path({str(saved)!r}).write_text(str(Path({str(versions)!r}).exists()))\n'
                          '    sys.exit(0)\n'
                          'signal.signal(signal.SIGTERM, stop)\nprint("ready", flush=True)\nsys.stdin.read()\n')
        process = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)
        self.assertEqual(process.stdout.readline().strip(), 'ready')
        def reap():
            process.wait()
            exited.touch()
        reaper = threading.Thread(target=reap, daemon=True)
        reaper.start()
        self.inject(f'''import sys
entry = Path(sys.argv[0]).resolve()
if entry.name == 'cli.py' and entry.parent.name == 'installer':
    sys.path.insert(0, str(entry.parent.parent))
    from installer import hosts
    hosts.observe_writers = lambda platforms, **kwargs: {{host: hosts.WriterObservation(
        'absent' if Path({str(exited)!r}).exists() else 'running', pids=() if Path({str(exited)!r}).exists() else ({process.pid},))
        for host in platforms}}
    if not os.environ.get('GHOST_ALICE_INSTALL_REQUEST'):
        import select
        print('BEFORE_SHUTDOWN_QUESTION', flush=True)
        assert select.select([sys.stdin], [], [], 5)[0], 'No queued input received'
''')
        command = ['bash', str(self.source / 'install.sh'), '--platform', 'claude', '--skip-source-health', 'task-router']
        question = 'May the installer close the programs listed above before installation? [y/N]'
        try:
            code, output = run_pty(command, env=self.env, cwd=self.home,
                                  responses=[('BEFORE_SHUTDOWN_QUESTION', 'y\n'), (question, 'n\n')])
            self.assertEqual(code, 1, output)
            self.assertFalse(versions.exists())
            self.assertFalse(self.manifest.exists())
            self.assertIsNone(process.poll())
            self.assertIn(str(process.pid), output)
            stopped_log = next((self.home / '.ghost-alice/install').glob('*.log'))
            self.assertIn('Installation was cancelled', stopped_log.read_text())
            self.assertFalse(stopped_log.with_suffix('.events.jsonl').exists())
            code, output = run_pty(command, env=self.env, cwd=self.home,
                                  responses=[('BEFORE_SHUTDOWN_QUESTION', '\n'), (question, 'y\n')])
            self.assertEqual(code, 0, output)
            self.assertEqual(saved.read_text(), 'False', 'Candidate files must not precede the close confirmation')
            self.assertEqual(process.returncode, 0)
            self.assertTrue(self.manifest.is_file())
            self.assertEqual(output.count(question), 1)
            successful_log = (set((self.home / '.ghost-alice/install').glob('*.log')) - {stopped_log}).pop()
            self.assertIn(str(process.pid), successful_log.read_text(), 'Shutdown observation and installed detail must share one log')
            self.assertIn(str(successful_log), output)
            events = [json.loads(line) for line in successful_log.with_suffix('.events.jsonl').read_text().splitlines()]
            self.assertTrue(any(row['type'] == 'platform-result' for row in events))
            self.assertEqual(events[-1]['type'], 'install-result')
            self.assertEqual(events[-1]['returncode'], 0)
        finally:
            process.stdin.close()
            reaper.join(timeout=5)
            process.stdout.close()

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

    def source_records(self):
        """Use the installer to bind old source-local records, then remove the one-time override."""
        import ledger_access
        self.ledger = ledger_access.load_ledger()
        self.a = self.source / '.tmp/session-intent'
        self.b = self.home / '.ghost-alice/session-intent'
        self.config = self.home / '.ghost-alice/config.json'
        with mock.patch.dict(os.environ, self.env, clear=True):
            self.ledger.record_turn(root=self.a, platform='claude', session_id='followup-session', raw_input='before move')
            self.before_move = self.ledger.read_session_state(root=self.a, platform='claude', session_id='followup-session')
        self.env['GHOST_ALICE_SESSION_INTENT_ROOT'] = str(self.a)
        try:
            self.install()
        finally:
            self.env.pop('GHOST_ALICE_SESSION_INTENT_ROOT')
        self.assertEqual(json.loads(self.config.read_text())['install_locations']['claude']['ledger_root'], str(self.a))

    def controlled_writers(self, fault=''):
        """Only external writer observations are controlled; the bound active CLI performs the move."""
        self.inject('''import json, sys
entry = Path(sys.argv[0]).resolve()
request = json.loads(os.environ.get('GHOST_ALICE_INSTALL_REQUEST') or '{}')
if entry.name == 'cli.py' and entry.parent.name == 'installer' and request.get('stage') == 'record-move':
    sys.path.insert(0, str(entry.parent.parent))
    from installer import hosts, gateway_participant
    hosts.observe_writers = lambda platforms, **kwargs: {host: hosts.WriterObservation('absent') for host in platforms}
    gateway_participant.listener_binding = lambda seen, roots, *, effects=None: gateway_participant.ListenerBinding('absent')
''' + ''.join('    ' + line + '\n' for line in fault.splitlines()))

    def test_source_change_cancel_then_fixed_command_moves_records_and_next_receipt(self):
        import hook_definitions
        self.source_records()
        old_source = self.source
        self.source = self.root / '설치 원본 2'
        shutil.copytree(old_source, self.source, ignore=shutil.ignore_patterns('.git', '.tmp'))
        self.controlled_writers('''import select
print('BEFORE_MOVE_QUESTION', flush=True)
assert select.select([sys.stdin], [], [], 5)[0], 'No queued input received'
''')
        question = 'Move your existing work records to the new location? [Y/n]'
        code, output = run_pty(['bash', str(self.source / 'install.sh'), '--adopt-source', '--skip-source-health'],
                              env=self.env, cwd=self.home,
                              responses=[('BEFORE_MOVE_QUESTION', '\n'), (question, 'n\n')])
        self.assertEqual(code, 0, output)
        self.assertEqual(output.count(question), 1)
        self.assertIn(str(old_source), output)
        self.assertIn(str(self.a), output)
        self.assertIn('will ask again', output)
        self.assertEqual(json.loads(self.config.read_text())['install_locations']['claude']['ledger_root'], str(self.a))
        import installed_payload
        record = installed_payload.read(self.home / '.ghost-alice')
        self.assertEqual(record['source']['root'], str(self.source))
        self.assertEqual(record['source']['ledger_source_roots'],
                         {str(self.a): {'source_root': str(old_source), 'hosts': ['claude']}})
        self.controlled_writers()
        code, output = run_pty(['bash', str(self.home / '.ghost-alice/bin/ghost-alice'), '--skip-source-health'],
                              env=self.env, cwd=self.home, responses=[(question, '\n')])
        self.assertEqual(code, 0, output)
        self.assertEqual(output.count(question), 1)
        self.assertIn(f'Your existing work records from {self.a} are now at {self.b}', output)
        self.assertEqual(json.loads(self.config.read_text())['install_locations']['claude']['ledger_root'], str(self.b))
        self.assertFalse(self.a.exists(), 'The installed follow-up must remove the migrated source folder')
        with mock.patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(self.ledger.read_session_state(root=self.b, platform='claude', session_id='followup-session'),
                             self.before_move)
        settings = json.loads(self.settings.read_text())
        command = next(handler['command'] for group in settings['hooks']['SessionStart'] for handler in group['hooks']
                       if hook_definitions.parse_hook_runner_command(handler['command'])['hook_id'] == 'session-receipt')
        result = subprocess.run(command, shell=True, env=self.env, capture_output=True, text=True, timeout=30,
                                input=json.dumps(dict(session_id='followup-session', source='startup', hook_event_name='SessionStart')))
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        receipt = json.loads(context.split('[session-intent-receipt]', 1)[1].split('[/session-intent-receipt]', 1)[0])
        self.assertEqual((receipt['ledger_root'], receipt['session_id']), (str(self.b), 'followup-session'))

    def test_original_locale_survives_shell_utf8_repair_to_the_followup_question(self):
        self.source_records()
        question = '기존 작업 기록을 새 위치로 옮길까요? [Y/n]'
        for locale in ({'LANG': 'ko_KR'}, {'LC_MESSAGES': 'ko_KR'}):
            with self.subTest(locale=locale):
                env = self.env.copy()
                for name in ('GHOST_ALICE_INSTALL_LOCALE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
                    env.pop(name, None)
                env.update(locale)
                code, output = run_pty(['bash', str(self.source / 'install.sh'), '--skip-source-health'],
                                      env=env, cwd=self.home, responses=[(question, 'n\n')])
                self.assertEqual(code, 0, output)
                self.assertEqual(output.count('Ghost-ALICE 설치를 완료했습니다.'), 1)
                self.assertIn(f'기존 작업 기록은 그대로 {self.a}에 있습니다.', output)
                self.assertNotIn('Move your existing work records', output)

    def test_post_merge_install_does_not_ask_even_with_terminal_input(self):
        self.source_records()
        code, output = run_pty(['bash', str(self.source / 'install.sh'), '--post-merge', '--merged-checkout', str(self.source)],
                              env=self.env, cwd=self.source, responses=[], timeout=90)
        self.assertEqual(code, 0, output)
        self.assertIn(f'Your existing work records remain at {self.a}', output)
        self.assertNotIn('[Y/n]', output)
        self.assertEqual(json.loads(self.config.read_text())['install_locations']['claude']['ledger_root'], str(self.a))

    def test_move_write_failure_restores_records_and_keeps_install_success_distinct(self):
        self.source_records()
        logs = self.home / '.ghost-alice/install'
        previous = {path: path.read_bytes() for path in logs.iterdir()}
        self.controlled_writers(f'''import install_transaction
rename = install_transaction._rename_path
failed = False
def fail_config(source, target):
    global failed
    if Path(target) == Path({str(self.config)!r}) and not failed:
        failed = True
        raise PermissionError('record move config write denied')
    return rename(source, target)
install_transaction._rename_path = fail_config''')
        code, output = run_pty(['bash', str(self.source / 'install.sh'), '--skip-source-health'], env=self.env, cwd=self.home,
                              responses=[('Move your existing work records to the new location? [Y/n]', '\n')])
        self.assertEqual(code, 1, output)
        self.assertIn('Ghost-ALICE installation is complete.', output)
        self.assertIn('restored the previous state', output)
        self.assertIn('record move config write denied', output)
        self.assertNotIn('Candidate install did not finish', output)
        events = set(logs.glob('*.events.jsonl')) - previous.keys()
        self.assertEqual(len(events), 1)
        event_file = events.pop()
        outcome = json.loads(event_file.read_text().splitlines()[-1])
        self.assertEqual((outcome['type'], outcome['returncode']), ('install-result', 1))
        detail = event_file.with_suffix('').with_suffix('.log').read_text()
        self.assertIn('record move config write denied', detail)
        for path, content in previous.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(json.loads(self.config.read_text())['install_locations']['claude']['ledger_root'], str(self.a))
        with mock.patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(self.ledger.read_session_state(root=self.a, platform='claude', session_id='followup-session'),
                             self.before_move)

    def test_committed_move_is_kept_until_normal_install_retries_completion(self):
        import install_transaction
        self.source_records()
        self.controlled_writers('''import install_transaction
mark = install_transaction.Journal.mark
def fail_completion(journal, phase):
    if phase == 'done':
        raise OSError('move completion record denied')
    return mark(journal, phase)
install_transaction.Journal.mark = fail_completion''')
        code, output = run_pty(['bash', str(self.source / 'install.sh'), '--skip-source-health'], env=self.env, cwd=self.home,
                              responses=[('Move your existing work records to the new location? [Y/n]', '\n')])
        self.assertEqual(code, 1, output)
        self.assertIn(f'Your existing work records from {self.a} are at {self.b}', output)
        self.assertIn('Saving the completion record', output)
        self.assertIn('installation preparation checks', output)
        self.assertNotIn('Candidate install did not finish', output)
        self.assertEqual(json.loads(self.config.read_text())['install_locations']['claude']['ledger_root'], str(self.b))
        inject_installer_python(self.env)
        pending = install_transaction.unfinished(self.home / '.ghost-alice')
        self.assertTrue(pending)
        result = self.run_install('--doctor')
        self.assertEqual(install_transaction.unfinished(self.home / '.ghost-alice'), pending, result.stdout + result.stderr)
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(install_transaction.unfinished(self.home / '.ghost-alice'), [])
        self.assertEqual(json.loads(self.config.read_text())['install_locations']['claude']['ledger_root'], str(self.b))

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
        payload = json.loads((self.home/'.ghost-alice/installed-payload-v2.json').read_text())
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
        hook_path = subprocess.run(['git', '-C', str(self.source), 'config', '--local', '--get', 'core.hooksPath'],
                                  env=self.env, capture_output=True, text=True, check=True).stdout
        result, report = self.cleanup()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('partial: addon removal is incomplete', result.stdout)
        self.assertTrue(report['addon_error'])
        self.assertFalse(report['errors'])
        self.assertEqual(command.read_text(), 'user command edit\n')
        self.assertTrue((self.home / '.ghost-alice/addons/claude/rich.json').exists())
        self.assertTrue((self.home / '.claude/skills/task-router').exists())
        self.assertFalse((self.home / '.claude/skills/skill-evolution').exists())
        retained_hook = subprocess.run(['git', '-C', str(self.source), 'config', '--local', '--get', 'core.hooksPath'],
                                      env=self.env, capture_output=True, text=True)
        self.assertEqual(retained_hook.returncode, 0, retained_hook.stderr)
        self.assertEqual(retained_hook.stdout, hook_path)

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
        inject_installer_python(self.env)
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
                inject_installer_python(self.env)
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
        inject_installer_python(self.env)
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
        inject_installer_python(self.env)
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
