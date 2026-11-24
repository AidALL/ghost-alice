"""installer.source: source health, source identity, the install source check, --update-source.

Expected texts come from installer_lib/source_health.sh and installer_lib/install.sh:538-551.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installed_payload  # noqa: E402
import install_lock  # noqa: E402
import install_transaction  # noqa: E402
from installer import cli, locations, report  # noqa: E402
from installer import runner  # noqa: E402
from installer import source  # noqa: E402
from installer_testing import checkout_copy, installer_env, make_context, use_source  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def init_repo(path: Path, *, upstream: Path | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if upstream is None:
        git(path, "init", "-q", "-b", "main")
        (path / "tracked.txt").write_text("one\n", encoding="utf-8")
        git(path, "add", "tracked.txt")
        git(path, "commit", "-q", "-m", "one")
        return path
    subprocess.run(["git", "clone", "-q", str(upstream), str(path)], check=True, env={**os.environ, **GIT_ENV})
    return path


class SourceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # The CI runner puts the temp root inside the checkout: stop git from finding the checkout above it.
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.root / "home"),
                                                "GIT_CEILING_DIRECTORIES": str(self.root.resolve().parent),
                                                **GIT_ENV}, clear=False)
        self.env.start()
        (self.root / "home").mkdir()
        report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=self.root / "home")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def check(self, repo: Path, *, skip: bool = False) -> tuple[bool, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with use_source(repo):
                result = source.check_source_health(skip=skip)
        return result, out.getvalue()


class InputProvenanceTests(SourceCase):
    def test_external_ledger_relationships_do_not_become_acquisition_metadata(self):
        ctx = make_context(self.root / 'home', 'claude')
        package = self.root / 'home/.ghost-alice/versions/external/core'
        (package / '_shared').mkdir(parents=True)
        (package / '_shared/product.py').write_text('external')
        relations = {str(self.root / 'ledger'): str(self.root / 'other-source')}
        (package.parent / 'version.json').write_text(json.dumps(dict(
            source=dict(root=str(self.root / 'development'), commit='external', ledger_source_roots=relations),
            files={'core/_shared/product.py': 'external-digest'})))
        expected = dict(root=str(self.root / 'development'), commit='external', ref=source.RELEASE_REF)
        with use_source(package):
            self.assertEqual(source.input_record(), expected)
            folder, _ = source.prepare_source(ctx)
        self.assertEqual(json.loads((folder / 'version.json').read_text())['source'], expected)

    def test_only_the_package_request_binds_a_process_to_its_package(self):
        package = self.root / 'package'
        package.mkdir()
        request = dict(package_root=str(package.resolve()), source=dict(root=str(self.root / 'development'), commit='c0ffee'))
        current, earlier = 'GHOST_ALICE_PACKAGE_REQUEST', 'GHOST_ALICE_INSTALL_REQUEST'
        with use_source(package):
            for name, value in (('none', ''), ('valid', json.dumps(request)), ('invalid JSON', '{not JSON')):
                with self.subTest(earlier=name):
                    with mock.patch.dict(os.environ, {current: json.dumps(request), earlier: value}):
                        self.assertEqual(source.install_request(), request)
                        self.assertEqual(source.input_record(), request['source'])
                    with mock.patch.dict(os.environ, {current: '', earlier: value}):
                        self.assertIsNone(source.install_request())
            for name, unfit in (('another package', dict(request, package_root=str(self.root))),
                                ('no source', dict(request, source=None)), ('no object', [request])):
                with self.subTest(request=name), mock.patch.dict(os.environ, {current: json.dumps(unfit), earlier: ''}), \
                        self.assertRaisesRegex(ValueError, 'does not identify this package'):
                    source.install_request()
        self.assertEqual((source.REQUEST_ENV, source.EARLIER_REQUEST_ENV), (current, earlier))


class SuccessfulSourceVersionsTests(SourceCase):
    def setUp(self):
        super().setUp()
        self.repo = init_repo(self.root / 'repo')
        (self.repo / '_shared').mkdir()
        (self.repo / 'tracked.txt').rename(self.repo / '_shared/tracked.txt')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'product files')
        self.ctx = make_context(self.root / 'home', 'claude')
        self.versions = self.root / 'home/.ghost-alice/versions'

    def test_git_and_plain_inputs_include_policies_and_exclude_unrelated_files(self):
        policies = ('docs/policies/evaluator-artifact-contract.md', 'docs/policies/live-smoke-regression.md')
        for name in policies:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        (self.repo / 'secret.env').write_text('unrelated input')
        from_git = source._version_source_files(self.repo)
        shutil.rmtree(self.repo / '.git')
        from_plain = source._version_source_files(self.repo)
        self.assertEqual(from_git, from_plain)
        self.assertTrue(set(policies).issubset(from_plain))
        self.assertNotIn('secret.env', from_plain)

    @contextlib.contextmanager
    def failing(self, point, files, mode):
        """Stop at `point`: before or after the replacement of one of the named `files`, or before a journal is done."""
        real, fired = os.replace, []

        def stop():
            if not fired:
                fired.append(point)
                if mode == 'process-exit':
                    os._exit(75)
                raise OSError('injected failure')

        def replacing(path, dest, *args, **kwargs):
            dest = Path(dest)
            name = next((name for name, file in files.items() if file == dest), None)
            if dest.name == 'journal.json' and json.loads(Path(path).read_text())['phase'] == 'done':
                name = 'done'
            if point == f'{name}-before':
                stop()
            real(path, dest, *args, **kwargs)
            if point == f'{name}-after':
                stop()
        with mock.patch('os.replace', replacing):
            yield

    def stopped(self, step, point, files, mode):
        """Run `step` until `point`, in a process of its own that ends there in the mode `process-exit`."""
        with self.failing(point, files, mode):
            if mode == 'exception':
                with self.assertRaisesRegex(OSError, 'injected failure'):
                    step()
                return
            pid = os.fork()
            if pid == 0:
                try:
                    step()
                finally:
                    os._exit(76)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 75)

    def active(self):
        return (install_transaction.record_after_recovery(self.versions.parent) or {})['installer'].get('active_root')

    def test_one_record_write_changes_the_active_package(self):
        from installer import lock, transaction
        root = self.versions.parent
        package, choices = self.root / 'package', {'claude': ['alpha']}
        (package / '_shared').mkdir(parents=True)
        placed = dict(name='earlier', kind='skill', mode='copy', provider='core', baseline='recorded',
                      used_by=['claude'], source='/earlier-placement', files={})
        for prior in (None, '/previous/core'):
            for mode in ['exception'] + (['process-exit'] if hasattr(os, 'fork') else []):
                for point in ('record-before', 'record-after'):
                    with self.subTest(prior=prior, mode=mode, point=point):
                        (package / '_shared/tracked.txt').write_text(f'{prior}:{mode}:{point}')
                        with use_source(package), install_lock.command_lock(root / 'install.lock', owner='test'):
                            installed_payload.update(root, {'installer': {'active_root': prior}, 'targets': {'/placed': placed}})
                            folder, _ = source.prepare_source(self.ctx, selections=choices)
                        version, record = folder / 'version.json', installed_payload.path(root)
                        saved, before = version.read_bytes(), installed_payload.read(root)
                        after = dict(before, installer=dict(before['installer'], active_root=str(folder / 'core')))
                        with use_source(folder / 'core'):
                            with install_lock.command_lock(root / 'install.lock', owner='test'):
                                self.stopped(lambda: transaction.activate(self.ctx, choices), point, {'record': record}, mode)
                                self.assertEqual(install_transaction.unfinished(root), [])
                                self.assertEqual(self.active(), prior if point == 'record-before' else str(folder / 'core'))
                                self.assertEqual(installed_payload.read(root), before if point == 'record-before' else after)
                                self.assertEqual(version.read_bytes(), saved)
                            handle = lock.acquire('install', 'claude')
                            try:
                                transaction.activate(self.ctx, choices)
                            finally:
                                lock.release(handle)
                        self.assertEqual(installed_payload.read(root), after)
                        self.assertEqual(version.read_bytes(), saved)

    def test_completion_failures_restore_metadata_and_record_and_keep_the_active_package(self):
        from installer import lock
        root = self.versions.parent
        placed = dict(name='earlier', kind='skill', mode='copy', provider='core', baseline='recorded',
                      used_by=['claude'], source='/earlier-placement', files={})
        for mode in ['exception'] + (['process-exit'] if hasattr(os, 'fork') else []):
            for point in ('metadata-before', 'metadata-after', 'record-before', 'record-after', 'done-before'):
                with self.subTest(mode=mode, point=point), use_source(self.repo):
                    (self.repo / '_shared/tracked.txt').write_text(f'{mode}:{point}')
                    with install_lock.command_lock(root / 'install.lock', owner='test'):
                        installed_payload.update(root, {'source': {'root': str(self.repo)}, 'targets': {'/placed': placed}})
                        folder, _ = source.prepare_source(self.ctx)
                        active = str(folder / 'core')
                        installed_payload.update(root, {'installer': {'active_root': active}})
                        version, record = folder / 'version.json', installed_payload.path(root)
                        original = version.read_bytes(), record.read_bytes()
                        original_mtime = version.stat().st_mtime_ns
                        self.stopped(lambda: source.finalize_source(self.ctx, folder, ['claude']), point,
                                     {'metadata': version, 'record': record}, mode)
                        if mode == 'exception':
                            self.assertEqual((version.read_bytes(), record.read_bytes()), original)
                    self.assertEqual(self.active(), active)
                    # The next locked command restores a killed completion before doing new work.
                    handle = lock.acquire('install', 'claude')
                    try:
                        self.assertEqual((version.read_bytes(), record.read_bytes()), original)
                        self.assertEqual(version.stat().st_mtime_ns, original_mtime)
                        self.assertEqual(installed_payload.read(root)['targets']['/placed'], placed)
                        self.assertEqual(self.active(), active)
                        source.finalize_source(self.ctx, folder, ['claude'])
                    finally:
                        lock.release(handle)
                    self.assertEqual(json.loads(version.read_bytes())['status'], 'successful')
                    self.assertEqual(self.active(), active)

    def test_completion_does_not_change_the_active_package(self):
        root = self.versions.parent
        with use_source(self.repo), install_lock.command_lock(root / 'install.lock', owner='test'):
            installed_payload.update(root, {'installer': {'active_root': '/other/core'}, 'source': {'root': str(self.repo)}})
            folder, _ = source.prepare_source(self.ctx)
            source.finalize_source(self.ctx, folder, ['claude'])
        self.assertEqual(json.loads((folder / 'version.json').read_text())['status'], 'successful')
        self.assertEqual(self.active(), '/other/core')

    def test_completion_records_the_ledger_relations_of_the_hosts(self):
        root, repo = self.versions.parent, self.repo.resolve()
        ledger = str(repo / 'ledger')
        with use_source(self.repo), install_lock.command_lock(root / 'install.lock', owner='test'):
            installed_payload.update(root, {'source': {'root': str(repo), 'commit': 'installed', 'ledger_source_roots': {}}})
            (root / 'config.json').write_text(json.dumps({'install_locations': {'claude': {'ledger_root': ledger}}}))
            folder, _ = source.prepare_source(self.ctx)
            source.finalize_source(self.ctx, folder, ['claude'])
        self.assertEqual(installed_payload.read(root)['source'], {
            'root': str(repo), 'commit': 'installed',
            'ledger_source_roots': {ledger: {'source_root': str(repo), 'hosts': ['claude']}}})

    def test_a_package_that_lost_or_changed_a_file_is_not_completed(self):
        for change in ('lost', 'changed'):
            with self.subTest(change=change), use_source(self.repo), \
                    install_lock.command_lock(self.versions.parent / 'install.lock', owner='test'):
                folder, _ = source.prepare_source(self.ctx)
                file = folder / 'core/_shared/tracked.txt'
                content = file.read_bytes()
                if change == 'lost':
                    file.unlink()
                else:
                    file.write_text('changed')
                with self.assertRaisesRegex(ValueError, 'Preserved source version (is incomplete|has changed)') as stopped:
                    source.finalize_source(self.ctx, folder, ['claude'])
                self.assertIn(str(file), str(stopped.exception))
                self.assertEqual(json.loads((folder / 'version.json').read_text())['status'], 'candidate')
                file.write_bytes(content)

    def save(self, platform='claude', root=None, addons=()):
        self.ctx.platform = platform
        self.ctx.addon_sources = list(map(str, addons))
        with use_source(root or self.repo), install_lock.command_lock(self.versions.parent / 'install.lock', owner='test'):
            folder, _ = source.prepare_source(self.ctx)
            installed_payload.update(self.versions.parent, {'installer': {'active_root': str(folder / 'core')}})
            lines = source.finalize_source(self.ctx, folder, [platform])
        folders = sorted(self.versions.iterdir()) if self.versions.exists() else []
        return folders, lines

    def test_saved_bytes_include_local_changes_and_repeat_hosts_share_one_version(self):
        (self.repo / '.gitignore').write_text('private.txt\n')
        (self.repo / 'private.txt').write_text('private')
        (self.repo / '_shared/tracked.txt').write_text('local change')
        (self.repo / '_shared/new.txt').write_text('untracked code')
        before = (self.repo / '.git/index').read_bytes()
        folders, lines = self.save()
        self.assertEqual(len(folders), 1)
        kept = folders[0] / 'core'
        self.assertEqual((kept / '_shared/tracked.txt').read_text(), 'local change')
        self.assertEqual((kept / '_shared/new.txt').read_text(), 'untracked code')
        self.assertFalse((kept / 'private.txt').exists())
        self.assertFalse((kept / '.git').exists())
        self.assertIn('--adopt-source', '\n'.join(lines))
        self.assertEqual(self.save('codex')[0], folders)
        self.assertEqual((self.repo / '.git/index').read_bytes(), before)

    def test_a_saved_package_is_complete_only_with_every_recorded_file_and_digest(self):
        (self.repo / '_shared/other.txt').write_text('other')
        folder = self.save()[0][0].resolve()
        source.check_saved_package(folder)
        (folder / 'core/__pycache__').mkdir()
        (folder / 'core/__pycache__/generated.pyc').write_bytes(b'generated cache')
        source.check_saved_package(folder)
        tracked, version = folder / 'core/_shared/tracked.txt', folder / 'version.json'
        content, saved = tracked.read_bytes(), version.read_bytes()
        changed = json.loads(saved)
        changed['files'].pop('core/_shared/other.txt')
        outside = self.root / 'outside.txt'
        outside.write_bytes(content)

        def link(path, _):
            path.unlink()
            path.symlink_to(outside)
        for label, path, damage in (
                ('changed', tracked, lambda path, _: path.write_bytes(b'changed')),
                ('missing', tracked, lambda path, _: path.unlink()),
                ('a link', tracked, link),
                ('another file list', version, lambda path, _: path.write_text(json.dumps(changed))),
                ('no file list', version, lambda path, _: path.unlink())):
            with self.subTest(damage=label):
                original = path.read_bytes()
                damage(path, original)
                with self.assertRaises(ValueError):
                    source.check_saved_package(folder)
                if path.is_symlink():
                    path.unlink()
                path.write_bytes(original)
                source.check_saved_package(folder)
        shutil.rmtree(folder / 'core/_shared')
        with self.assertRaises(ValueError):
            source.check_saved_package(folder)

    def test_another_release_is_prepared_while_the_active_package_lost_files(self):
        active = self.save()[0][0].resolve()
        lost = active / 'core/_shared/tracked.txt'
        lost.unlink()
        (self.repo / '_shared/tracked.txt').write_text('next')
        root = self.versions.parent
        before = installed_payload.path(root).read_bytes()
        with use_source(self.repo), install_lock.command_lock(root / 'install.lock', owner='test'):
            folder, _ = source.prepare_source(self.ctx)
            self.assertNotEqual(folder, active)
            source.check_saved_package(folder)
            with self.assertRaises(ValueError):
                source.prepare_source(self.ctx, required_version=active)
        self.assertFalse(lost.exists())
        self.assertEqual(installed_payload.path(root).read_bytes(), before)

    def test_an_installer_that_repairs_by_itself_accepts_only_the_files_of_the_active_package(self):
        package = checkout_copy(self)
        folders, _ = self.save(root=package)
        root, version = self.versions.parent, package / 'VERSION'
        original, before = version.read_bytes(), installed_payload.path(root).read_bytes()
        with use_source(package), install_lock.command_lock(root / 'install.lock', owner='test'):
            version.write_bytes(original + b'\n')
            with self.assertRaises(ValueError):
                source.repair_active_source(self.ctx)
            self.assertEqual(sorted(self.versions.iterdir()), folders)
            version.write_bytes(original)
            source.repair_active_source(self.ctx)
        self.assertEqual(installed_payload.path(root).read_bytes(), before)
        self.assertEqual(sorted(self.versions.iterdir()), folders)

    def test_repair_names_a_changed_file_of_the_active_package(self):
        package = checkout_copy(self)
        (folder,), _ = self.save(root=package)
        root, changed = self.versions.parent, folder / 'core/VERSION'
        changed.write_bytes(changed.read_bytes() + b'\n')
        for running in (package, folder / 'core'):
            with self.subTest(running=running.name), use_source(running), \
                    install_lock.command_lock(root / 'install.lock', owner='test'), \
                    self.assertRaisesRegex(ValueError, f'has changed: .*{re.escape(str(changed.resolve()))}'):
                source.repair_active_source(self.ctx)

    def test_complete_package_repair_reads_the_files_without_staging_a_copy(self):
        (folder,), _ = self.save()
        for running in (folder / 'core', self.repo):
            with self.subTest(running=running), use_source(running), mock.patch.object(
                    shutil, 'copy2', side_effect=AssertionError('repair copied an intact package')):
                self.assertEqual(source.repair_active_source(self.ctx), folder.resolve() / 'core')

    def test_lost_core_file_never_calls_the_original_input_checkout_registered(self):
        locations.package_script(self.repo).write_text('original installer')
        (self.repo / '_shared/installer').mkdir()
        (self.repo / '_shared/installer/cli.py').write_text('original entry')
        (folder,), _ = self.save()
        lost = folder / 'core/_shared/tracked.txt'
        lost.unlink()
        with use_source(folder / 'core'), self.assertRaises(ValueError) as cause:
            source.repair_active_source(self.ctx)
        self.assertIn(str(lost), str(cause.exception))
        self.assertIn(source.report.message('restore_from_checkout'), str(cause.exception))
        self.assertNotIn(str(self.repo), str(cause.exception))
        self.assertNotIn(locations.package_command(folder / 'core', 'update'), str(cause.exception))

    def test_a_saved_package_reinstalls_the_installed_choices_and_those_of_its_own_unfinished_installation(self):
        root = self.versions.parent
        own, installed = {'claude': ['alpha'], 'codex': ['alpha', 'beta']}, {'codex': ['beta', 'gamma']}
        both = {'claude': ['alpha'], 'codex': ['alpha', 'beta', 'gamma']}
        with use_source(self.repo), install_lock.command_lock(root / 'install.lock', owner='test'):
            folder, _ = source.prepare_source(self.ctx, selections=own)
            package, version = folder / 'core', folder / 'version.json'
            saved = json.loads(version.read_text())
            self.assertIsNone(source.reinstall_selection(self.repo))
            for status, active, expected in (('candidate', '/earlier/core', installed), ('candidate', str(package), both),
                                             ('successful', '/later/core', installed),
                                             ('successful', str(package), installed)):
                with self.subTest(status=status, active=active):
                    version.write_text(json.dumps(dict(saved, status=status)))
                    installed_payload.update(root, {'installer': {'active_root': active}, 'targets': {
                        f'/{name}': dict(name=name, kind='skill', mode='copy', provider='core', baseline='recorded',
                                         used_by=['codex'], source=f'/{name}', source_commit=name, files={},
                                         installed_at='2026-10-20T00:00:00+00:00') for name in installed['codex']}})
                    self.assertEqual(source.reinstall_selection(package), expected)
                    for name in installed['codex']:
                        installed_payload.release_target(root, destination=f'/{name}', provider='core', platform='codex')
                    # Nothing is installed.
                    self.assertEqual(source.reinstall_selection(package), own)
            version.write_text(json.dumps(dict(saved, selections=None)))
            with self.assertRaises(ValueError):
                source.reinstall_selection(package)

    def test_a_removal_drops_its_skills_from_the_choices_of_the_unfinished_installation(self):
        root = self.versions.parent
        choices = {'claude': ['alpha'], 'codex': ['alpha', 'fam']}
        with use_source(self.repo), install_lock.command_lock(root / 'install.lock', owner='test'):
            skill = self.repo / 'fam/one/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('one')
            (self.repo / 'fam/two').mkdir()
            (self.repo / 'fam/two/SKILL.md').write_text('two')
            folder, _ = source.prepare_source(self.ctx, selections=choices)
            version = folder / 'version.json'
            saved = json.loads(version.read_text())
            for status, active, removals, left in (
                    ('candidate', folder, [('codex', ['alpha', 'one', '_shared'])], {'claude': ['alpha'], 'codex': ['two']}),
                    ('candidate', folder, [('codex', ['alpha']), ('codex', ['one', 'two'])], {'claude': ['alpha']}),
                    ('candidate', folder, [('codex', None)], {'claude': ['alpha']}),
                    ('candidate', folder, [('codex', None), ('claude', None)], {}),
                    ('candidate', folder, [('other', None), ('other', ['alpha'])], choices),
                    ('successful', folder, [('codex', None), ('claude', ['alpha'])], choices),
                    ('candidate', self.versions / 'another', [('codex', None)], choices)):
                with self.subTest(status=status, active=active, removals=removals):
                    version.write_text(json.dumps(dict(saved, status=status)))
                    installed_payload.update(root, {'installer': {'active_root': str(active / 'core')}})
                    before = version.read_bytes()
                    for host, names in removals:
                        source.drop_unfinished_choices(host, names)
                    self.assertEqual(json.loads(version.read_text()), dict(saved, status=status, selections=left))
                    if left == choices:
                        self.assertEqual(version.read_bytes(), before)
            # Without a readable record or package nothing is kept.
            installed_payload.update(root, {'installer': {'active_root': str(folder / 'core')}})
            for damaged, text in ((version, '{'), (version, None), (installed_payload.read_path(root), '{')):
                with self.subTest(damaged=damaged.name, text=text):
                    damaged.unlink(missing_ok=True)
                    if text is not None:
                        damaged.write_text(text)
                    source.drop_unfinished_choices('codex', ['alpha'])
                    self.assertEqual(damaged.read_text() if damaged.exists() else None, text)

    def test_preparing_the_same_files_again_leaves_the_saved_version_as_it_is(self):
        root = self.versions.parent
        other = dict(root=str(self.root / 'other'), remote='second', commit='one', dirty='clean')
        with use_source(self.repo), install_lock.command_lock(root / 'install.lock', owner='test'):
            folder, _ = source.prepare_source(self.ctx, selections={'claude': ['alpha']})
            version = folder / 'version.json'
            saved = json.loads(version.read_text())
            for status in ('candidate', 'successful'):
                with self.subTest(status=status):
                    version.write_text(json.dumps(dict(saved, status=status)))
                    before = version.read_bytes()
                    again, _ = source.prepare_source(self.ctx, provenance=other, selections={'codex': ['beta']})
                    self.assertEqual((again, version.read_bytes()), (folder, before))

    def test_latest_three_versions_remain_beside_an_older_live_version(self):
        folders, _ = self.save()
        first = folders[0]
        link = self.root / 'home/.claude/skills/alpha'
        link.parent.mkdir(parents=True)
        link.symlink_to(first / 'core')
        earlier_source = self.root / 'earlier-checkout'
        record = {'targets': {str(link): {'used_by': ['claude'], 'source': str(earlier_source),
                                         'mode': 'link', 'link_target': str(earlier_source)},
                              str(self.root / 'absent'): {'used_by': ['claude'],
                                                         'source': str(self.versions / 'absent/core')}}}
        with mock.patch.object(source.installed_payload, 'read_file', return_value=record):
            for number in range(1, 5):
                (self.repo / '_shared/tracked.txt').write_text(str(number))
                folders, _ = self.save()
        self.assertEqual(len(folders), 4)
        self.assertIn(first, folders)
        self.assertTrue(link.exists())
        self.assertEqual(sorted((folder / 'core/_shared/tracked.txt').read_text() for folder in folders), ['2', '3', '4', 'one\n'])

    def test_source_changed_after_placement_does_not_become_a_successful_version(self):
        installed = self.root / 'installed.py'
        installed.write_text('placed bytes')
        (self.repo / '_shared/tracked.txt').write_text('changed after placement')
        with use_source(self.repo):
            folder, _ = source.prepare_source(self.ctx)
        record = {'targets': {str(installed): {'source': str(folder / 'core/_shared/tracked.txt'),
                                              'used_by': ['claude'], 'mode': 'copy'}}}
        with mock.patch.object(source.installed_payload, 'read_file', return_value=record):
            with self.assertRaisesRegex(ValueError, 'Preserved source differs from installed code'):
                source.finalize_source(self.ctx, folder, ['claude'])
        self.assertEqual(json.loads((folder / 'version.json').read_text())['status'], 'candidate')

    def test_inventory_only_future_host_keeps_its_older_source_version(self):
        folders, _ = self.save()
        first = folders[0]
        installed = self.root / 'future-host/alpha'
        installed.parent.mkdir()
        installed.symlink_to(first / 'core/_shared', target_is_directory=True)
        inventory = self.versions.parent / 'install-state/future-host.json'
        inventory.parent.mkdir()
        inventory.write_text(json.dumps(dict(platform='future-host', source_root=str(first / 'core'), targets=[
            dict(target_name='alpha', dest_path=str(installed), source_path=str(first / 'core/_shared'), install_mode='symlink')])) )
        for number in range(4):
            (self.repo / '_shared/tracked.txt').write_text(str(number))
            folders, _ = self.save()
        self.assertIn(first, folders)
        self.assertTrue(installed.exists())

    def test_plain_package_uses_catalog_and_saved_source_reuses_its_file_list(self):
        plain = self.root / 'package'
        (plain / 'skill-catalog').mkdir(parents=True)
        (plain / 'skill-catalog/skills.json').write_text(json.dumps({'install_roots': ['alpha']}))
        (plain / 'alpha').mkdir()
        (plain / 'alpha/SKILL.md').write_text('alpha')
        (plain / 'install.sh').write_text('entrypoint')
        (plain / 'private.txt').write_text('unrelated user material')
        folders, _ = self.save(root=plain)
        self.assertEqual(len(folders), 1)
        kept = folders[0] / 'core'
        self.assertTrue((kept / 'alpha/SKILL.md').is_file())
        self.assertFalse((kept / 'private.txt').exists())
        (kept / 'personal.txt').write_text('not source inventory')
        self.assertEqual(self.save(root=kept)[0], folders)

    def test_selected_addon_sources_are_fixed_and_named_in_the_existing_install_command(self):
        addon = self.root / 'addon-package'
        (addon / 'demo/skill').mkdir(parents=True)
        (addon / 'addons-manifest.json').write_text(json.dumps({'manifest_version': 1,
                                                             'addons': [{'id': 'demo', 'path': 'demo'}]}))
        (addon / 'demo/addon.json').write_text('{}')
        (addon / 'demo/skill/SKILL.md').write_text('old addon bytes')
        (addon / 'private.txt').write_text('unrelated material')
        folders, lines = self.save(addons=[addon])
        self.assertEqual(len(folders), 1)
        kept = next((folders[0] / 'addons').iterdir())
        (addon / 'demo/skill/SKILL.md').write_text('new addon bytes')
        self.assertEqual((kept / 'demo/skill/SKILL.md').read_text(), 'old addon bytes')
        self.assertFalse((kept / 'private.txt').exists())
        self.assertIn('--addon-source', '\n'.join(lines))
        self.assertIn(str(kept), '\n'.join(lines))

    def test_recovery_checks_all_surviving_files_and_rejects_linked_paths_before_writing(self):
        (self.repo / 'VERSION').write_bytes(b'original')
        folder = self.save()[0][0]
        missing = folder / 'core/VERSION'
        missing.unlink()
        shared = folder / 'core/_shared'
        tracked = shared / 'tracked.txt'
        original = tracked.read_bytes()
        tracked.write_bytes(b'changed')
        with use_source(self.repo):
            with self.assertRaises(ValueError):
                source.prepare_source(self.ctx)
            self.assertFalse(missing.exists())
            self.assertEqual(tracked.read_bytes(), b'changed')
            tracked.write_bytes(original)
            target = self.root / 'outside'
            shared.rename(target)
            install_transaction._create_directory_link(target, shared)
            with self.assertRaises(ValueError):
                source.prepare_source(self.ctx)
            self.assertFalse(missing.exists())
            self.assertEqual((target / 'tracked.txt').read_bytes(), original)

    def test_retry_preserves_a_file_created_after_recovery_checked_the_destination(self):
        (self.repo / '_shared/a.py').write_bytes(b'first')
        (self.repo / '_shared/b.py').write_bytes(b'second')
        folder = self.save()[0][0].resolve()
        first, second = folder / 'core/_shared/a.py', folder / 'core/_shared/b.py'
        first.unlink()
        second.unlink()
        from retired_assets import _restore_no_replace
        def competing_writer(staged, destination):
            if destination == second:
                second.write_bytes(b'user')
            _restore_no_replace(staged, destination)
        with use_source(self.repo), install_lock.command_lock(self.versions.parent / 'install.lock', owner='test'):
            with mock.patch('retired_assets._restore_no_replace', side_effect=competing_writer), self.assertRaises(FileExistsError):
                source.prepare_source(self.ctx)
            self.assertEqual(first.read_bytes(), b'first')
            self.assertEqual(second.read_bytes(), b'user')
            second.unlink()
            source.prepare_source(self.ctx)
            self.assertEqual(second.read_bytes(), b'second')

    def test_all_live_versions_survive_with_named_references_when_more_than_three_are_needed(self):
        record = {'targets': {}}
        with mock.patch.object(source.installed_payload, 'read_file', return_value=record):
            for number in range(4):
                (self.repo / '_shared/tracked.txt').write_text(str(number))
                folders, lines = self.save()
                latest = next(folder for folder in folders if (folder / 'core/_shared/tracked.txt').read_text() == str(number))
                record['targets'][str(number)] = {'source': str(latest / 'core'), 'used_by': ['claude']}
        self.assertEqual(len(folders), 4)
        extra = next(folder for folder in folders if (folder / 'core/_shared/tracked.txt').read_text() == '0')
        self.assertIn(str(extra), '\n'.join(lines))
        self.assertIn('claude', '\n'.join(lines))

    def test_a_changed_archive_and_old_successes_survive_a_failed_new_copy(self):
        folders, _ = self.save()
        first = folders[0]
        (first / 'core/_shared/tracked.txt').write_text('user edit to preserved source')
        for number in range(4):
            (self.repo / '_shared/tracked.txt').write_text(str(number))
            folders, _ = self.save()
        self.assertIn(first, folders)
        before = set(folders)
        with mock.patch.object(source.shutil, 'copy2', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.save()
        self.assertEqual(set(self.versions.iterdir()), before)

    def test_failed_old_version_deletion_reports_the_saved_new_version(self):
        oldest = self.save()[0][0]
        for number in range(1, 3):
            (self.repo / '_shared/tracked.txt').write_text(str(number))
            self.save()
        (self.repo / '_shared/tracked.txt').write_text('latest')
        remove = shutil.rmtree
        def fail_old_version(path, *args, **kwargs):
            if Path(path).resolve() == oldest.resolve():
                raise PermissionError('old version is locked')
            return remove(path, *args, **kwargs)
        with mock.patch.object(source.shutil, 'rmtree', side_effect=fail_old_version):
            with self.assertRaisesRegex(OSError, 'Source version saved.*previous version cleanup failed'):
                self.save()
        self.assertEqual(len(list(self.versions.iterdir())), 4)

    def test_a_directory_link_cannot_be_silently_omitted_from_the_saved_source(self):
        (self.repo / '_shared/code').mkdir()
        (self.repo / '_shared/code/file.py').write_text('source')
        (self.repo / '_shared/linked-code').symlink_to(self.repo / '_shared/code', target_is_directory=True)
        git(self.repo, 'add', '_shared/linked-code')
        with self.assertRaisesRegex(ValueError, 'Source directory link cannot be preserved'):
            self.save()
        self.assertEqual(list(self.versions.iterdir()), [])

    def test_version_root_link_never_redirects_the_archive_writer(self):
        outside = self.root / 'unrelated'
        outside.mkdir()
        self.versions.parent.mkdir(parents=True)
        self.versions.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Source version root is a link'):
            self.save()
        self.assertEqual(list(outside.iterdir()), [])

    def test_the_reinstall_command_names_no_host(self):
        # The saved package reinstalls the installed selection; a host in the command would narrow it or add every skill.
        self.ctx.options.platform_explicit = True
        for platform in ('claude', 'codex'):
            with self.subTest(platform=platform):
                folders, lines = self.save(platform)
                (command,) = [line for line in lines if '--adopt-source' in line]
                self.assertEqual(command, source.report.message('reinstall_command', command=locations.command_line(
                    'bash', str(folders[0].resolve() / 'core/install.sh'), '--local-source', '--adopt-source')))

    def test_a_repair_compares_the_commands_addon_source_only_for_a_lost_addon_file(self):
        def addon(name):
            folder = self.root / name
            (folder / 'demo/skill').mkdir(parents=True)
            (folder / 'addons-manifest.json').write_text(json.dumps({'manifest_version': 1, 'addons': [{'id': 'demo', 'path': 'demo'}]}))
            (folder / 'demo/addon.json').write_text('{}')
            (folder / 'demo/skill/SKILL.md').write_text(name + ' bytes')
            return folder

        saved, other = addon('saved-addon'), addon('other-addon')
        folders, _ = self.save(addons=[saved])
        active = folders[0].resolve() / 'core'
        kept = next((active.parent / 'addons').iterdir()) / 'demo/skill/SKILL.md'
        core = active / '_shared/tracked.txt'
        self.ctx.addon_sources = [str(other)]
        with install_lock.command_lock(self.versions.parent / 'install.lock', owner='test'):
            # The complete package repairs: its own addon folders are its addon files.
            for running in (active, self.repo):
                with self.subTest(running=running.name), use_source(running):
                    self.assertEqual(source.repair_active_source(self.ctx), active)
            with use_source(self.repo):
                # A lost Core file comes back from the same Core files.
                content = core.read_bytes()
                core.unlink()
                self.assertEqual(source.repair_active_source(self.ctx), active)
                self.assertEqual(core.read_bytes(), content)
                # A lost addon file comes back only from the same addon source.
                kept.unlink()
                with self.assertRaises(ValueError):
                    source.repair_active_source(self.ctx)
                self.ctx.addon_sources = [str(saved)]
                self.assertEqual(source.repair_active_source(self.ctx), active)
                self.assertEqual(kept.read_text(), 'saved-addon bytes')
        self.assertEqual(sorted(self.versions.iterdir()), folders)

    def test_windows_reinstall_command_selects_supported_powershell(self):
        with mock.patch.object(source.locations, 'os', wraps=os) as windows:
            windows.name = 'nt'
            _, lines = self.save('codex')
        self.assertIn(source.report.message('reinstall_command', command='pwsh -NoProfile -ExecutionPolicy Bypass -File '),
                      '\n'.join(lines))
        self.assertIn('core/install.ps1', '\n'.join(lines))

    def test_archive_pruning_uses_whole_copy_rules_for_caches_links_and_extra_content(self):
        folder = self.save()[0][0]
        self.assertTrue(source._unchanged_version(folder))
        cache = folder / 'core/__pycache__'
        cache.mkdir()
        (cache / 'generated.pyc').write_bytes(b'generated cache')
        self.assertTrue(source._unchanged_version(folder))
        link = cache / 'user-link'
        link.symlink_to(self.repo / '_shared/tracked.txt')
        self.assertFalse(source._unchanged_version(folder))
        link.unlink()
        extra = folder / 'personal.txt'
        extra.write_text('user content')
        self.assertFalse(source._unchanged_version(folder))
        extra.unlink()
        (folder / 'core/_shared/tracked.txt').unlink()
        self.assertFalse(source._unchanged_version(folder))

    def test_service_and_pending_merge_consumers_keep_their_source_versions(self):
        service_version = self.save()[0][0]
        (self.repo / '_shared/tracked.txt').write_text('pending version')
        pending_version = next(folder for folder in self.save()[0] if folder != service_version)
        pending = self.versions.parent / 'pending-merges/claude/manifest.json'
        pending.parent.mkdir(parents=True)
        pending.write_text(json.dumps({'entries': [{'backup_path': str(pending_version / 'core/_shared/tracked.txt'), 'decided': False}]}))
        record = {'service': {'program': str(service_version / 'core/_shared/tracked.txt')}}
        with mock.patch.object(source.installed_payload, 'read_file', return_value=record):
            for number in range(4):
                (self.repo / '_shared/tracked.txt').write_text(str(number))
                folders, _ = self.save()
        self.assertEqual(len(folders), 5)
        self.assertIn(service_version, folders)
        self.assertIn(pending_version, folders)
        self.assertEqual(sorted((folder / 'core/_shared/tracked.txt').read_text() for folder in folders),
                         ['1', '2', '3', 'one\n', 'pending version'])


class SuccessfulSourceReinstallTests(unittest.TestCase):
    def test_full_installs_share_a_version_and_the_saved_installer_reinstalls_its_old_bytes(self):
        checkout = checkout_copy(self)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            live, home = root / 'package', root / 'home'
            shutil.copytree(checkout, live, ignore=shutil.ignore_patterns('.git', '__pycache__'))
            (home / '.claude').mkdir(parents=True)
            (home / '.codex').mkdir()
            settings = home / '.claude/settings.json'
            settings.write_text('{"userPreference":"keep-me"}')
            codex_settings = home / '.codex/config.toml'
            codex_settings.write_text('user_preference = "keep-me"\n')
            env = installer_env(home)
            env.update(PYTHONDONTWRITEBYTECODE='1', GIT_CEILING_DIRECTORIES=str(root))
            # Package-manager setup and live service observation are outside this temporary installation.
            entry = ('import sys; sys.path.insert(0, sys.argv.pop(1)); from installer import cli; '
                     'cli.prerequisites.install_prerequisites=lambda: True; '
                     'cli.transaction.report_gateway=lambda ctx: None; raise SystemExit(cli.main(sys.argv[1:]))')
            def install(package, platform, *skills):
                result = subprocess.run([sys.executable, '-B', '-c', entry, str(package / '_shared'),
                                         '--platform', platform, '--adopt-source', '--local-source', *skills], cwd=package, env=env,
                                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout[-6000:])
                return result.stdout
            original = (live / 'task-router/SKILL.md').read_bytes()
            install(live, 'claude')
            versions = home / '.ghost-alice/versions'
            first = next(versions.iterdir())
            install(live, 'codex')
            self.assertEqual(list(versions.iterdir()), [first])
            saved = json.loads((first / 'version.json').read_text())
            self.assertEqual(saved['platforms'], ['claude', 'codex'])
            (live / 'task-router/SKILL.md').write_bytes(original + b'\nChanged working source.\n')
            install(live, 'claude')
            install(live, 'codex')
            codex_settings.write_text('rollback_user_sentinel = "keep-current"\n' + codex_settings.read_text())
            self.assertEqual(len(list(versions.iterdir())), 2)
            output = install(first / 'core', 'claude')
            target = home / '.claude/skills/task-router'
            self.assertFalse(target.is_symlink())
            self.assertEqual((target / 'SKILL.md').read_bytes(), original)
            self.assertIn('keep-me', settings.read_text())
            self.assertEqual(len(list(versions.iterdir())), 2)
            self.assertIn(' --local-source --adopt-source', output)
            install(first / 'core', 'codex')
            copied = home / '.agents/skills/task-router'
            self.assertFalse(copied.is_symlink())
            self.assertEqual((copied / 'SKILL.md').read_bytes(), original)
            self.assertIn('user_preference = "keep-me"', codex_settings.read_text())
            self.assertIn('rollback_user_sentinel = "keep-current"', codex_settings.read_text())
            self.assertEqual(len(list(versions.iterdir())), 2)
            successful_versions = set(versions.iterdir())
            for _ in range(2):
                install(first / 'core', 'codex', 'task-router')
                self.assertEqual(set(versions.iterdir()), successful_versions)
                self.assertEqual(list((home / '.ghost-alice/rollbacks').glob('*/rollback-*')), [])


class SourceHealthTests(SourceCase):
    @unittest.skipUnless(os.name == 'posix', 'requires a POSIX package-manager fixture')
    def test_default_install_bootstraps_git_before_reading_the_release_remote(self):
        remote = init_repo(self.root / 'release')
        checkout = init_repo(self.root / 'development', upstream=remote)
        expected = git(remote, 'rev-parse', 'main')
        tools = self.root / 'tools'
        tools.mkdir()
        brew = tools / 'brew'
        brew.write_text('#!/bin/sh\n[ "$1" = install ] && [ "$2" = git ] && '
                        'exec /bin/ln -s "$FIXTURE_GIT" "$PATH/git"\n')
        brew.chmod(0o755)
        paths = {'PATH': str(tools), 'FIXTURE_GIT': shutil.which('git'), 'GHOST_ALICE_PREREQUISITES_SHOWN': ''}
        run_command, started = runner.run_command, []

        def run(argv, **kwargs):
            if argv[0] != sys.executable:
                return run_command(argv, **kwargs)
            release = Path(argv[1]).parents[2]
            started.append(([str(value) for value in argv], kwargs, release, git(release, 'rev-parse', 'HEAD')))
            return runner.ToolResult(23)

        with mock.patch.dict(os.environ, paths), use_source(checkout), mock.patch.object(runner, 'run_command', side_effect=run), \
                mock.patch.object(cli, 'dispatch', side_effect=AssertionError('the downloaded release installs itself')):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([]), 23, out.getvalue())
        ((argv, kwargs, release, commit),) = started
        self.assertEqual(argv, [sys.executable, str(release / '_shared/installer/cli.py'), '--install-acquired-release',
                                str(checkout), str(remote)])
        self.assertEqual(kwargs, {'inherit_stdin': True, 'env': {
            cli.START_ORIGIN_ENV: json.dumps(dict(source.input_record(checkout), registered=None))}})
        self.assertEqual(commit, expected)
        self.assertFalse(release.exists())
        self.assertLess(out.getvalue().index('automatic consent'), out.getvalue().index('brew install git'))

    def test_the_downloaded_release_keeps_the_named_folder_and_remote_and_identifies_its_own_commit(self):
        checkout = init_repo(self.root / 'development').resolve()
        git(checkout, 'checkout', '-q', '-b', 'feature')
        release = init_repo(self.root / 'release')
        git(release, 'commit', '-q', '--allow-empty', '-m', 'released')
        git(release, 'checkout', '-q', '--detach')
        start = {cli.START_ORIGIN_ENV: json.dumps(dict(source.input_record(checkout), registered=None))}
        with use_source(release), mock.patch.object(cli, 'dispatch', return_value=23) as dispatch, \
                mock.patch.dict(os.environ, start), contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli.main(['--install-acquired-release', str(checkout), 'https://example.invalid/product.git'])
        self.assertEqual(code, 23, out.getvalue())
        arguments = dispatch.call_args.kwargs
        provenance = arguments.get('provenance') or {}
        self.assertEqual({key: provenance.get(key) for key in ('root', 'remote', 'ref', 'branch', 'commit', 'dirty')},
                         dict(root=str(checkout), remote='https://example.invalid/product.git', ref=source.RELEASE_REF,
                              branch='main', commit=git(release, 'rev-parse', 'HEAD'), dirty='clean'))
        self.assertEqual(arguments.get('reinstall_from'), checkout)

    def test_update_acquisition_failure_keeps_development_checkout_unchanged(self):
        repo = init_repo(self.root / 'development')
        (repo / 'tracked.txt').write_text('uncommitted user change')
        before = git(repo, 'status', '--porcelain=v1')
        git(repo, 'branch', '-m', 'feature')
        for remote in (repo, self.root / 'missing-remote'):
            with self.subTest(remote=remote), self.assertRaisesRegex(ValueError, 'Core release'):
                with source.acquire_release(str(remote)):
                    self.fail('failed acquisition must not yield an install input')
        self.assertEqual((repo / 'tracked.txt').read_text(), 'uncommitted user change')
        self.assertEqual(git(repo, 'status', '--porcelain=v1'), before)

    def test_skip_flag_warns_and_passes(self):
        # source_health.sh:6-9
        ok, out = self.check(self.root / "nowhere", skip=True)
        self.assertTrue(ok)
        self.assertIn("[WARN] Skipping source health gate (--skip-source-health)", out)

    def test_missing_git_is_unverified(self):
        # source_health.sh:11-14
        with mock.patch("installer.source.shutil.which", return_value=None):
            ok, out = self.check(init_repo(self.root / "repo"))
        self.assertTrue(ok)
        self.assertIn("[WARN] git not found; source health is unverified (continuing)", out)

    def test_plain_directory_is_unverified(self):
        # source_health.sh:16-19
        plain = self.root / "plain"
        plain.mkdir()
        ok, out = self.check(plain)
        self.assertTrue(ok)
        self.assertIn("[WARN] source is not a git worktree; source health is unverified (continuing)", out)

    def test_clean_branch_without_upstream(self):
        # source_health.sh:25-41; test_source_health_gate.py:268-270
        repo = init_repo(self.root / "repo")
        ok, out = self.check(repo)
        self.assertTrue(ok)
        self.assertRegex(out, r"\[INFO\] Source health: branch=main, head=[0-9a-f]{7,}, upstream=none")
        self.assertNotIn("[WARN]", out)

    def test_detached_head(self):
        # test_source_health_gate.py:279
        repo = init_repo(self.root / "repo")
        git(repo, "checkout", "-q", "--detach")
        ok, out = self.check(repo)
        self.assertTrue(ok)
        self.assertIn("branch=DETACHED", out)

    def test_behind_upstream_refuses(self):
        # source_health.sh:55-60; test_source_health_gate.py:343-345
        upstream = init_repo(self.root / "upstream")
        clone = init_repo(self.root / "clone", upstream=upstream)
        (upstream / "tracked.txt").write_text("two\n", encoding="utf-8")
        git(upstream, "commit", "-q", "-am", "two")
        git(clone, "fetch", "-q")
        ok, out = self.check(clone)
        self.assertFalse(ok)
        self.assertIn("[ERROR] Source branch is behind its upstream; refusing to install from stale source.", out)
        self.assertIn("Run git fetch origin, inspect git status, then fast-forward or otherwise reconcile this checkout "
                      "before installing. If the human user decided to install these local files, rerun with --local-source --skip-source-health.", out)
        self.assertIn("To preserve source-local changes while updating, run bash install.sh --update-source.", out)
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_COMMAND": ".\\install.cmd"}):
            _, out = self.check(clone)
        self.assertIn("To preserve source-local changes while updating, run .\\install.cmd --update-source.", out)
        self.assertIn("If an old checkout is already blocked by local changes during git pull, use the bootstrap "
                      "one-command update in docs/getting-started/troubleshooting.md.", out)

    def test_diverged_warns_and_continues(self):
        # source_health.sh:52-54; test_source_health_gate.py:322-323
        upstream = init_repo(self.root / "upstream")
        clone = init_repo(self.root / "clone", upstream=upstream)
        (upstream / "tracked.txt").write_text("two\n", encoding="utf-8")
        git(upstream, "commit", "-q", "-am", "two")
        (clone / "local.txt").write_text("local\n", encoding="utf-8")
        git(clone, "add", "local.txt")
        git(clone, "commit", "-q", "-m", "local")
        git(clone, "fetch", "-q")
        ok, out = self.check(clone)
        self.assertTrue(ok)
        self.assertIn("[WARN] Source branch has diverged from its upstream; continuing because local commits may be intentional.", out)
        self.assertIn("[WARN] Before release, run git fetch origin, inspect git status, and reconcile this checkout history.", out)

    def test_ahead_warns_and_continues(self):
        # source_health.sh:61-63
        upstream = init_repo(self.root / "upstream")
        clone = init_repo(self.root / "clone", upstream=upstream)
        (clone / "local.txt").write_text("local\n", encoding="utf-8")
        git(clone, "add", "local.txt")
        git(clone, "commit", "-q", "-m", "local")
        ok, out = self.check(clone)
        self.assertTrue(ok)
        self.assertIn("[WARN] Source branch is ahead of its upstream; continuing because local commits may be intentional.", out)

    def test_untracked_and_tracked_changes_warn(self):
        # source_health.sh:69-76; test_source_health_gate.py:289-300
        repo = init_repo(self.root / "repo")
        (repo / "note.txt").write_text("untracked\n", encoding="utf-8")
        ok, out = self.check(repo)
        self.assertTrue(ok)
        self.assertIn("[WARN] Source tree has untracked files; continuing", out)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        ok, out = self.check(repo)
        self.assertTrue(ok)
        self.assertIn("[WARN] Source tree has untracked files; continuing.", out)
        self.assertIn("[WARN] Source tree has tracked local changes; continuing. External changes to installed targets remain "
                      "protected by pending-merges backup and manifest checks.", out)
        self.assertNotIn("tracked files are clean", out)


class SourceIdentityTests(SourceCase):
    def test_resolve_source_in_a_repo(self):
        # installer_lib/install.sh:538-551
        repo = init_repo(self.root / "repo")
        (repo / "sub").mkdir()
        with use_source(repo / "sub"):
            info = source.resolve_source()
        self.assertEqual(info.root, repo.resolve())
        self.assertEqual(info.branch, "main")
        self.assertEqual(info.head, git(repo, "rev-parse", "HEAD"))
        self.assertEqual(info.dirty, "clean")
        (repo / "x.txt").write_text("x", encoding="utf-8")
        with use_source(repo):
            self.assertEqual(source.resolve_source().dirty, "dirty")
        git(repo, "checkout", "-q", "--detach")
        with use_source(repo):
            self.assertEqual(source.resolve_source().branch, "DETACHED")

    def test_resolve_source_outside_git(self):
        plain = self.root / "plain"
        plain.mkdir()
        with use_source(plain):
            info = source.resolve_source()
        self.assertEqual(info, source.SourceInfo(root=plain, branch="DETACHED", head="unknown", dirty="unknown"))

    def test_update_source_calls_the_updater(self):
        # source_health.sh:81-89
        with mock.patch("installer.runner.run_tool", return_value=runner.ToolResult(returncode=3)) as run_tool:
            with use_source(self.root):
                self.assertEqual(source.update_source(), 3)
        run_tool.assert_called_once_with("_shared/installer_source_update.py", "--source-dir", str(self.root))


STOP_HINT = ("Rerun this command from the install source, or add --adopt-source to record this checkout "
             "as the install source.")


class InstallSourceTests(SourceCase):
    """The install record names the checkout the installation came from (`source.root`). Before an install or a
    repair changes anything, the installer compares that folder with the checkout it runs from, both with links
    followed: no record or no source goes on, the same folder goes on, the installer asks at a terminal for another folder and
    stops elsewhere."""

    def setUp(self):
        super().setUp()
        self.here = init_repo(self.root / "here")
        self.enterContext(use_source(self.here))
        self.other = init_repo(self.root / "other")
        self.home = self.root / "home"
        self.record = installed_payload.path(self.home / ".ghost-alice")
        self.ctx = make_context(self.home, "codex")

    def write_record(self, source_root: Path | None, **extra) -> None:
        record = {"schema_version": 1, "targets": {}, **extra}
        if source_root is not None:
            record["source"] = {"root": str(source_root), "commit": "c0ffee", "dirty": "clean", "hooks_path": "hooks"}
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text(json.dumps(record), encoding="utf-8")

    def run_check(self, ctx=None, *, interactive=False, answer=None):
        ctx = self.ctx if ctx is None else ctx
        out = io.StringIO()
        replies = mock.Mock(side_effect=answer if isinstance(answer, BaseException) else [answer])
        with contextlib.redirect_stdout(out), mock.patch("installer.source.interactive", return_value=interactive), \
                mock.patch("builtins.input", replies):
            try:
                source.check_install_source(ctx)
            except runner.InstallerExit as stop:
                return stop.code, out.getvalue(), replies
        return 0, out.getvalue(), replies

    def test_without_a_record_the_first_install_goes_on(self):
        self.assertEqual(source.match_install_source().state, "unrecorded")
        self.assertEqual(self.run_check()[:2], (0, ""))
        # A record that names no source (written before any publish) is the same as no record.
        self.write_record(None)
        self.assertEqual(source.match_install_source().state, "unrecorded")
        self.assertEqual(self.run_check()[:2], (0, ""))

    def test_the_judgment_compares_the_checkout_it_is_given(self):
        # The post-merge step names the checkout the merge ran in; the installer itself runs from the install source.
        self.write_record(self.other)
        merged = source.match_install_source(self.other)
        self.assertEqual((merged.state, merged.here, merged.recorded), ("same", self.other.resolve(), self.other))
        self.assertEqual(source.match_install_source(self.here).state, "other")
        # Without a checkout the judgment compares the checkout the installer runs from.
        self.assertEqual(source.match_install_source().state, "other")

    def test_the_recorded_checkout_goes_on_with_links_followed(self):
        link = self.root / "link-to-here"
        link.symlink_to(self.here, target_is_directory=True)
        self.write_record(link)
        match = source.match_install_source()
        self.assertEqual((match.state, match.here, match.recorded), ("same", self.here.resolve(), link))
        self.assertEqual(self.run_check()[:2], (0, ""))
        # The installer's own location is compared with its links followed too.
        self.write_record(self.here)
        with use_source(link):
            self.assertEqual(source.match_install_source().state, "same")

    def test_another_checkout_stops_a_non_interactive_command_with_exit_2_and_the_command_to_run(self):
        self.write_record(self.other)
        before = self.record.read_bytes()
        match = source.match_install_source()
        self.assertEqual((match.state, match.recorded), ("other", self.other))
        code, out, replies = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn("[ERROR] This checkout is not the install source the install record names.", out)
        self.assertIn(f"[ERROR]   Install source: {self.other}\n", out)
        self.assertIn(f"[ERROR]   This checkout:  {self.here.resolve()}\n", out)
        self.assertIn(f"[ERROR] {STOP_HINT}", out)
        replies.assert_not_called()
        self.assertEqual(self.record.read_bytes(), before)
        self.assertFalse(self.ctx.options.adopt_source)

    def test_a_recorded_checkout_that_no_longer_exists_is_replaced_without_a_question(self):
        # A fresh clone at another path after the old one was removed: nothing else can be the install source.
        gone = self.root / "removed-clone"
        self.write_record(gone)
        for interactive in (False, True):
            with self.subTest(interactive=interactive):
                ctx = make_context(self.home, "codex")
                code, out, replies = self.run_check(ctx, interactive=interactive, answer="n")
                self.assertEqual(code, 0)
                self.assertEqual(out, f"[INFO] Install source: {gone} no longer exists; this checkout replaces it\n")
                replies.assert_not_called()
                # The decision travels with the command, so the auto mode's children do not judge it again.
                self.assertTrue(ctx.options.adopt_source)

    def test_adopt_source_installs_from_another_checkout(self):
        self.write_record(self.other)
        ctx = make_context(self.home, "codex", adopt_source=True)
        code, out, replies = self.run_check(ctx)
        self.assertEqual(code, 0)
        self.assertIn(f"[INFO] Install source: this checkout replaces {self.other}", out)
        replies.assert_not_called()
        self.assertTrue(ctx.options.adopt_source)

    def test_a_terminal_asks_and_the_answer_decides(self):
        self.write_record(self.other)
        code, out, replies = self.run_check(interactive=True, answer="y")
        self.assertEqual(code, 0)
        self.assertIn(f"[WARN]   Install source: {self.other}\n", out)
        self.assertIn(f"[WARN]   This checkout:  {self.here.resolve()}\n", out)
        self.assertIn(f"[INFO] Install source: this checkout replaces {self.other}", out)
        # The yes typed at the installer's terminal question travels as --adopt-source, so the auto mode's children do not ask again. The installer does not check who typed it.
        self.assertTrue(self.ctx.options.adopt_source)
        for answer in ("", "n", "no", "maybe", EOFError()):
            with self.subTest(answer=answer):
                ctx = make_context(self.home, "codex")
                code, out, _ = self.run_check(ctx, interactive=True, answer=answer)
                self.assertEqual(code, 2)
                self.assertIn(f"[ERROR] {STOP_HINT}", out)
                self.assertFalse(ctx.options.adopt_source)
        ctx = make_context(self.home, 'codex')
        with mock.patch.object(source.terminal, '_discard_previous_input', side_effect=OSError('input failed')):
            code, out, replies = self.run_check(ctx, interactive=True, answer='yes')
        self.assertEqual(code, 2)
        self.assertFalse(ctx.options.adopt_source)
        self.assertIn('input failed', out)

    def test_a_publish_that_stopped_halfway_does_not_decide_the_source(self):
        # The check runs before the lock, so before the lock restores unfinished publishes: it reads the record the
        # restore will put back (the copy the oldest unfinished publish saved), not the half-written one.
        self.write_record(self.here)
        older = self.home / ".ghost-alice" / "rollbacks" / "20261021T000000000000Z-1-aaaaaaaa"
        newer = self.home / ".ghost-alice" / "rollbacks" / "20261021T000001000000Z-1-bbbbbbbb"
        for folder in (older, newer):
            folder.mkdir(parents=True)
            shutil.copyfile(self.record, folder / installed_payload.FILENAME)
            (folder / "journal.json").write_text(json.dumps({
                "schema_version": 1, "txid": folder.name, "phase": "prepare", "record_copy": installed_payload.FILENAME, "targets": []}),
                encoding="utf-8")
        self.write_record(self.other)
        self.assertEqual(source.match_install_source().state, "same")
        self.assertEqual(self.run_check()[:2], (0, ""))
        # The oldest unfinished publish found no record: after the restore there is none, so nothing is compared.
        (older / installed_payload.FILENAME).unlink()
        (older / "journal.json").write_text(json.dumps({"schema_version": 1, "txid": older.name, "phase": "prepare", "record_copy": None,
                                                        "targets": []}), encoding="utf-8")
        self.assertEqual(source.match_install_source().state, "unrecorded")
        # A journal that cannot be read leaves the record unknown; the lock's restore reports that journal.
        (older / "journal.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(source.match_install_source().state, "unreadable")

    def test_an_unreadable_record_skips_the_check_with_a_warning(self):
        # The comparison cannot be made; the command goes on as it did before the check existed.
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text("{broken", encoding="utf-8")
        self.assertEqual(source.match_install_source().state, "unreadable")
        code, out, _ = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn(f"[WARN] Install source check skipped: {self.record} is not JSON", out)

    def test_a_command_that_cannot_replace_the_source_never_asks(self):
        # Repair compares like the install but never replaces the install source, not even at a terminal.
        for recorded in (self.other, self.root / "removed-clone"):
            with self.subTest(recorded=recorded.name):
                self.write_record(recorded)
                ctx = make_context(self.home, "claude", adopt_source=True)
                out = io.StringIO()
                replies = mock.Mock(return_value="y")
                with contextlib.redirect_stdout(out), mock.patch("installer.source.interactive", return_value=True), \
                        mock.patch("builtins.input", replies), self.assertRaises(runner.InstallerExit) as stop:
                    source.check_install_source(ctx, can_adopt=False)
                self.assertEqual(stop.exception.code, 2)
                replies.assert_not_called()
                self.assertIn("[ERROR] Repair runs only from the install source; run the install from this checkout "
                              "with --adopt-source to move the installation here.", out.getvalue())
                self.assertNotIn("replaces", out.getvalue())

    def test_interactive_needs_a_terminal_on_both_ends(self):
        for stdin_tty, stdout_tty, expected in ((True, True, True), (True, False, False), (False, True, False)):
            with self.subTest(stdin=stdin_tty, stdout=stdout_tty), \
                    mock.patch("installer.source.sys.stdin") as stdin, mock.patch("installer.source.sys.stdout") as stdout:
                stdin.isatty.return_value, stdout.isatty.return_value = stdin_tty, stdout_tty
                self.assertEqual(source.interactive(), expected)


if __name__ == "__main__":
    unittest.main()
