"""Installers of different releases meet in disposable Git repositories through the shipped commands."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_shared'))
from installer_testing import checkout_copy, inject_installer_python, installer_env, run_pty
import content_digest
import install_transaction
import installed_payload
from installer.selection import ALL_SKILLS, installed_core_selection, leaf_names

# The installer of this commit starts a newer package from its own process for a plain install and the hook.
EARLIER_COMMIT = (Path(__file__).parent / '_fixtures/earlier-installer-commit.txt').read_text().strip()


class DirectoryLinkUpgradeTests(unittest.TestCase):
    """Upgrade the reporter's installer against a local main and inspect both ends of its skill link."""

    def setUp(self):
        candidate_source = checkout_copy(self)
        fixture = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home, self.repo, self.remote = fixture / 'home', fixture / 'checkout', fixture / 'release'
        (self.home / '.claude').mkdir(parents=True)
        self.repo.mkdir()
        self.env = installer_env(self.home)
        self.env.update(GIT_ALLOW_PROTOCOL='file', GHOST_ALICE_INSTALL_LOCALE='en')
        self.entry = ['cmd.exe', '/d', '/c', 'install.cmd'] if os.name == 'nt' else ['bash', './install.sh']
        if os.name == 'nt':
            # install.ps1 chooses the newest Python it discovers in PATH or in standard install folders.
            python_dir = Path(sys.executable).parent
            paths = [p for p in self.env['PATH'].split(os.pathsep) if p and Path(p).name.lower() != 'windowsapps'
                     and not (Path(p) / 'python.exe').is_file() and not list(Path(p).glob('python3*.exe'))]
            self.env['PATH'] = os.pathsep.join([str(python_dir), *paths])
            for key in ('LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)'):
                self.env[key] = str(self.home / key)
            # CI runners can create symlinks. Reproduce error 1314 so the old installer itself creates a junction.
            inject_installer_python(self.env, '''
import ctypes, sys
if Path(sys.argv[0]).name == 'cli.py':
    with (Path(os.environ['HOME']) / 'installer-python.txt').open('a') as output:
        print(sys.version_info.major, sys.version_info.minor, file=output)
if os.environ.get('DENY_DIRECTORY_SYMLINK') == '1':
    native_symlink = os.symlink
    def symlink(src, dst, target_is_directory=False, **kwargs):
        if target_is_directory:
            raise ctypes.WinError(1314)
        return native_symlink(src, dst, target_is_directory, **kwargs)
    os.symlink = symlink
''')
        # Use the candidate's Git objects only. Git creates both indexes and writes all worktree
        # bytes in the same isolated environment that the installer will use to clone its release.
        self.command('git', 'clone', '--quiet', '--branch', 'main', str(candidate_source), str(self.remote))
        self.command('git', 'clone', '--quiet', '--no-checkout', str(self.remote), str(self.repo))
        old = (ROOT / 'scripts/tests/_fixtures/junction-installer-commit.txt').read_text().strip()
        self.git(self.repo, 'fetch', '--no-tags', '--depth=1', str(ROOT), old)
        self.git(self.repo, 'switch', '--detach', old)
        self.ledger = self.repo / '.tmp/session-intent'
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.ledger_cli = [sys.executable, 'session-intent-analyzer/scripts/session_intent_ledger.py',
                           '--platform', 'claude', '--session-id', 'issue-177-upgrade']
        self.command(*self.ledger_cli, '--root', str(self.ledger), '--input', 'previous work')
        self.input_id = json.loads(self.command(*self.ledger_cli, '--root', str(self.ledger),
                                                '--read-intent'))['input_event_id']
        self.assertTrue(self.input_id)
        self.command(*self.entry, '--platform', 'claude', 'task-router',
                     DENY_DIRECTORY_SYMLINK='1', GHOST_ALICE_SESSION_INTENT_ROOT=str(self.ledger))
        self.skill = self.home / '.claude/skills/task-router'
        if os.name == 'nt':
            self.assertEqual(self.skill.lstat().st_reparse_tag, stat.IO_REPARSE_TAG_MOUNT_POINT)
        else:
            self.assertTrue(self.skill.is_symlink())
        self.source_skill = self.skill.resolve(strict=True)
        self.ghost = self.home / '.ghost-alice'
        self.candidate = self.git(self.remote, 'rev-parse', 'main')

    def command(self, *args, code=0, **env):
        result = subprocess.run(args, cwd=self.repo, env={**self.env, **env}, stdin=subprocess.DEVNULL,
                                capture_output=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout.strip()

    def git(self, root, *args):
        return self.command('git', '-C', str(root), *args)

    def upgrade_and_check(self):
        self.git(self.repo, 'switch', 'main')
        source_file = self.source_skill / 'SKILL.md'
        before = source_file.read_bytes()
        question = 'Move your existing work records to the new location? [Y/n]'
        if os.name == 'nt':
            from winpty import PtyProcess
            process = PtyProcess.spawn(self.entry, cwd=str(self.repo), env=self.env, dimensions=(60, 240))
            output = ''
            answered = False
            try:
                process.fileobj.settimeout(180)
                while True:
                    try:
                        output += process.read()
                    except EOFError:
                        break
                    except TimeoutError:
                        self.fail(f'Windows installer stopped producing terminal output:\n{output}')
                    if not answered and question in output:
                        process.write('y\r')
                        answered = True
            finally:
                process.close(force=True)
            self.assertEqual(process.exitstatus, 0, output)
            self.assertTrue(answered, output)
        else:
            code, output = run_pty(self.entry, cwd=self.repo, env=self.env, responses=[(question, 'y\n')])
            self.assertEqual(code, 0, output)
        self.assertFalse(self.ledger.exists(), output)
        moved = json.loads(self.command(*self.ledger_cli, '--root', str(self.ghost / 'session-intent'), '--read-intent'))
        self.assertEqual(moved['input_event_id'], self.input_id)
        self.command(*self.entry)
        self.assertEqual(installed_payload.read(self.ghost)['source']['commit'], self.candidate)
        self.assertEqual((self.skill / 'SKILL.md').read_bytes(), (self.remote / 'task-router/SKILL.md').read_bytes())
        self.assertFalse(os.path.samefile(self.skill, self.source_skill))
        self.assertEqual(source_file.read_bytes(), before)
        self.assertFalse(list((self.ghost / 'rollbacks').glob('*/rollback-task-router-*')))
        if os.name == 'nt':
            versions = (self.home / 'installer-python.txt').read_text().splitlines()
            self.assertEqual(set(versions), {f'{sys.version_info.major} {sys.version_info.minor}'})

    def test_plain_install_replaces_the_legacy_link_and_moves_work_records(self):
        self.upgrade_and_check()

    def test_retry_after_v0_6_0_left_a_completed_transaction(self):
        release = '737ec399d65e11eb10cfd315d3f60ba3d1eb7237'
        for root in (self.repo, self.remote):
            self.git(root, 'fetch', '--no-tags', '--depth=1', str(ROOT), release)
        self.git(self.repo, 'switch', '--detach', release)
        self.git(self.remote, 'reset', '--hard', release)
        if os.name != 'nt':
            # POSIX symlinks normally clean up successfully. Stop v0.6.0 at the equivalent removal.
            inject_installer_python(self.env, '''
if os.environ.get('FAIL_OLD_LINK_REMOVAL'):
    unlink = Path.unlink
    def fail_old_link(path, *args, **kwargs):
        if path.name.startswith('rollback-task-router-') and path.is_symlink():
            raise PermissionError('fixture: cannot remove old skill link')
        return unlink(path, *args, **kwargs)
    Path.unlink = fail_old_link
''')
        self.command(*self.entry, code=1, FAIL_OLD_LINK_REMOVAL='1')
        self.assertFalse(os.path.samefile(self.skill, self.source_skill))
        saved = list((self.ghost / 'rollbacks').glob('*/rollback-task-router-*'))
        self.assertTrue(saved)
        for path in saved:
            if os.name == 'nt':
                self.assertEqual(path.lstat().st_reparse_tag, stat.IO_REPARSE_TAG_MOUNT_POINT)
            else:
                self.assertTrue(path.is_symlink())
            self.assertEqual(json.loads((path.parent / 'journal.json').read_bytes())['phase'], 'done')
        self.git(self.remote, 'reset', '--hard', self.candidate)
        self.upgrade_and_check()


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
        self.assertEqual(installed_core_selection(installed_payload.read(ghost)), {'claude': ['task-router']})
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
class HandoffCase(unittest.TestCase):
    """A checkout, another repository that also publishes main, and a home that holds Claude Code only."""

    def setUp(self):
        self.repo = checkout_copy(self)
        self.remote = checkout_copy(self)
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (self.home / '.claude').mkdir()
        self.env = installer_env(self.home)
        self.env['GHOST_ALICE_INSTALL_LOCALE'] = 'en'
        self.ghost = self.home / '.ghost-alice'
        self.ledger = self.repo / 'records'
        self.entry = ['bash', str(self.repo / 'install.sh')]
        self.command = ['bash', str(self.ghost / 'bin/ghost-alice')]
        self.options = ['--platform', 'claude', '--skip-source-health', 'task-router']

    def shell(self, argv, *, code=0, **env):
        """The output of a command that ends with `code`; None stands for any failure."""
        result = subprocess.run(argv, cwd=self.home, env={**self.env, **env}, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=180)
        output = result.stdout + result.stderr
        if code is None:
            self.assertNotEqual(result.returncode, 0, output)
        else:
            self.assertEqual(result.returncode, code, output)
        return output

    def git(self, *args, root=None):
        return self.shell(['git', '-C', str(root or self.repo), *args]).strip()

    def commit(self, name, text, *, root=None):
        """One more commit that changes the file `name`."""
        root = root or self.repo
        (root / name).write_text(text)
        self.git('add', name, root=root)
        self.git('commit', '--quiet', '-m', f'Change {name}', root=root)

    def active(self):
        return Path(install_transaction.record_after_recovery(self.ghost)['installer']['active_root'])

    def packages(self):
        return {folder for folder in (self.ghost / 'versions').iterdir() if folder.is_dir()}

    def installation(self):
        """The bytes of every installed file and installation record, the work records and the checkout's Git settings.

        A starting installer adds its own log, its lock file and the package that it saves.
        """
        own = {self.ghost / name for name in ('install', 'install.lock', 'versions')}
        files = [self.repo / '.git/config', *self.active().parent.rglob('*')]
        for folder in (self.home / '.claude', self.home / '.codex', self.home / '.agents', self.ghost, self.ledger):
            files += (path for path in folder.rglob('*') if own.isdisjoint((path, *path.parents)))
        return {path: os.readlink(path) if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest()
                for path in files if path.is_symlink() or path.is_file()}

    def assert_unchanged(self, before):
        after = self.installation()
        self.assertEqual([str(path) for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)], [])


class DownloadedInstallerTests(HandoffCase):
    """Every installing command starts the installer that it downloaded."""

    PROMPT = 'Downloaded installer ready?'
    # A distinct release asks through its own entry and declines the installation.
    DOWNLOADED = f"""import json, sys
from pathlib import Path
terminal = sys.stdin.isatty()
Path.home().joinpath('received.json').write_text(json.dumps([sys.argv[1:], terminal]))
if terminal:
    print({PROMPT!r}, flush=True)
sys.exit(38 if terminal and input() != 'continue' else 37)
"""

    def received(self):
        file = self.home / 'received.json'
        self.assertTrue(file.is_file(), 'The downloaded installer did not start')
        value = json.loads(file.read_bytes())
        file.unlink()
        return value

    def test_the_downloaded_installer_receives_the_arguments_and_the_terminal_and_its_exit_code_returns(self):
        self.git('remote', 'set-url', 'origin', str(self.remote))
        self.shell([*self.entry, *self.options, '--local-source'])
        self.commit('_shared/installer/cli.py', self.DOWNLOADED, root=self.remote)
        active, other = self.active(), f'{self.remote}/.git'
        before, packages = self.installation(), self.packages()
        # Only the downloaded release can interpret a new adapter name or its options.
        update = ['update', '-p', 'next-adapter', '--remote', other, '--ref', 'main', '--skip-source-health',
                  '--new-adapter-option']
        for name, command, argv, root, remote in (('checkout', self.entry, self.options, self.repo, self.remote),
                                                  ('ghost-alice', self.command, [], active, self.remote),
                                                  ('update', self.command, update, active, other)):
            with self.subTest(entry=name):
                code, output = run_pty([*command, *argv], env=self.env, cwd=self.home,
                                       responses=[(self.PROMPT, 'continue\n')], timeout=60)
                self.assertEqual(code, 37, output)
                self.assertNotIn("older than this installer", output)
                self.assertEqual(self.received(), [['--install-acquired-release', str(root), str(remote), *argv], True])
        with self.subTest(entry='hook'):
            # The hook starts the same command without a terminal.
            self.git('remote', 'set-url', 'origin', str(self.home / 'obsolete-remote'))
            self.shell([*self.command, '--post-merge', '--merged-checkout', str(self.repo)], code=37)
            self.assertEqual(self.received(), [['--install-acquired-release', str(self.repo), str(self.remote),
                                                '--post-merge'], False])
            self.git('remote', 'set-url', 'origin', str(self.remote))
        self.assert_unchanged(before)
        self.assertEqual(self.packages(), packages)


class ChangedOriginTests(HandoffCase):
    def assert_later_installation_survives(self, *, before_activation=False, update=True):
        self.git('remote', 'set-url', 'origin', str(self.remote))
        self.shell([*self.entry, *self.options])
        if before_activation:
            # Same checkout, changed remote. An update names its download remote; a plain command takes the registered one.
            other = checkout_copy(self)
            skill = 'task-router/SKILL.md'
            self.commit(skill, (other / skill).read_text() + '\nInstalled from the new remote\n', root=other)
            second = [*self.command, 'update', '--remote', str(other)]
            first = [*self.command, *(['update', '--remote', str(self.remote)] if update else [])]
            expected_root, expected_remote = self.repo, other
        else:
            # Same remote, changed checkout. The first update used the recorded remote.
            other = checkout_copy(self)
            self.git('remote', 'set-url', 'origin', str(self.remote), root=other)
            second = ['bash', str(other / 'install.sh'), '--adopt-source', *self.options]
            first = [*self.command, *(['update'] if update else [])]
            expected_root, expected_remote = other, self.remote
        record_path = installed_payload.read_path(self.ghost)
        target = self.home / '.claude/skills/task-router/SKILL.md'
        # Run a second real command inside the download, or between the two installer processes that follow it. Both
        # commands use the test home.
        inject_installer_python(self.env, f"""
import json, sys
def switch_source(event, args):
    if event != 'subprocess.Popen' or not (Path.home() / 'switch-once').is_file():
        return
    argv = args[1]
    request = json.loads((args[3] or {{}}).get('GHOST_ALICE_PACKAGE_REQUEST') or '{{}}')
    ready = bool(request.get('package_root') and not request.get('stage')) if {before_activation!r} else argv[-2:] == ['--detach', 'FETCH_HEAD']
    if ready:
        (Path.home() / 'switch-once').unlink()
        subprocess.run({second!r}, stdin=subprocess.DEVNULL, check=True, timeout=120)
        (Path.home() / 'second-record').write_bytes(Path({str(record_path)!r}).read_bytes())
        (Path.home() / 'second-skill').write_bytes(Path({str(target)!r}).read_bytes())
sys.addaudithook(switch_source)
""")
        (self.home / 'switch-once').touch()
        result = subprocess.run(first, cwd=self.home, env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=180)
        self.assertTrue((self.home / 'second-record').is_file(), result.stdout + result.stderr)
        second_record = (self.home / 'second-record').read_bytes()
        origin = json.loads(second_record)['source']
        self.assertEqual((origin['root'], origin['remote']), (str(expected_root), str(expected_remote)))
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('registered checkout or remote changed', output)
        if not before_activation:
            self.assertEqual(output.count('Install source: this checkout replaces'), 1, output)
        self.assertEqual('run the command again without --remote' in output, update, output)
        self.assertEqual(record_path.read_bytes(), second_record)
        self.assertEqual(target.read_bytes(), (self.home / 'second-skill').read_bytes())
        if update:
            # The command that the message names keeps the later installation.
            self.shell([*self.command, 'update'])
            origin = installed_payload.read(self.ghost)['source']
            self.assertEqual((origin['root'], origin['remote']), (str(expected_root), str(expected_remote)))
            self.assertEqual(target.read_bytes(), (self.home / 'second-skill').read_bytes())

    def test_checkout_changed_during_download(self):
        self.assert_later_installation_survives()

    def test_checkout_changed_during_the_download_of_a_plain_command(self):
        self.assert_later_installation_survives(update=False)

    def test_remote_changed_after_the_package_was_prepared(self):
        self.assert_later_installation_survives(before_activation=True)

    def test_remote_changed_after_the_package_of_a_plain_command_was_prepared(self):
        self.assert_later_installation_survives(before_activation=True, update=False)


class ChangedSelectionTests(HandoffCase):
    def test_saved_reinstall_keeps_a_skill_removed_after_preparation(self):
        self.shell([*self.entry, '--platform', 'claude', '--local-source', 'task-router', 'boundary-contract'])
        remove = [*self.command, '--platform', 'claude', '--uninstall', 'task-router']
        inject_installer_python(self.env, f"""
import json, sys
def remove_selected(event, args):
    if event != 'subprocess.Popen' or not (Path.home() / 'remove-once').is_file():
        return
    request = json.loads((args[3] or {{}}).get('GHOST_ALICE_PACKAGE_REQUEST') or '{{}}')
    if request.get('package_root') and not request.get('stage'):
        (Path.home() / 'remove-once').unlink()
        subprocess.run({remove!r}, stdin=subprocess.DEVNULL, check=True, timeout=120)
sys.addaudithook(remove_selected)
""")
        (self.home / 'remove-once').touch()
        output = self.shell([*self.command, '--local-source'], code=1)
        self.assertIn('Core selection changed', output)
        self.assertFalse((self.home / '.claude/skills/task-router').exists())
        self.assertEqual(installed_core_selection(installed_payload.read(self.ghost)), {'claude': ['boundary-contract']})
        self.shell([*self.command, '--local-source'])
        self.assertFalse((self.home / '.claude/skills/task-router').exists())


class OlderReleaseTests(HandoffCase):
    """The main branch of the remote holds an earlier release, which does not know this installer's request."""

    def test_a_release_that_does_not_know_the_request_ends_with_the_cause_and_changes_nothing(self):
        self.git('fetch', '--quiet', '--depth=1', str(ROOT), EARLIER_COMMIT, root=self.remote)
        self.git('checkout', '--quiet', '-B', 'main', EARLIER_COMMIT, root=self.remote)
        self.git('remote', 'set-url', 'origin', str(self.remote))
        def stopped(command):
            output = self.shell([*self.entry, *command], code=1)
            self.assertIn("did not accept this installer's request", output)
            self.assertIn(str(self.remote), output)
            self.assertIn('--local-source', output)

        stopped(self.options)
        self.assertFalse((self.ghost / 'versions').exists())
        self.assertFalse((self.home / '.claude/skills').exists())
        # The files of this checkout install with the option that the message names.
        self.shell([*self.entry, *self.options, '--local-source'])
        self.assertTrue((self.home / '.claude/skills/task-router/SKILL.md').is_file())
        # An update from that remote ends the same way and keeps the installation.
        before = self.installation()
        stopped(['update'])
        self.assert_unchanged(before)


class OfficialReleaseUpgradeTests(HandoffCase):
    """In a shallow clone, fetch the fixture with `git fetch --depth=1 origin tag v0.5.1`."""

    @unittest.skipUnless(shutil.which('node'), 'the full release installation requires Node.js')
    def test_plain_install_after_pulling_main_from_v0_5_1(self):
        # Publish the candidate working tree as the next commit even when CI has a shallow checkout.
        self.env['GIT_ALLOW_PROTOCOL'] = 'file'
        tag = 'v0.5.1'
        self.assertEqual(self.git('tag', '--list', tag, root=ROOT), tag,
                         f'Fetch the official upgrade fixture with: git fetch --depth=1 origin tag {tag}')
        self.git('fetch', '--no-tags', '--depth=1', str(ROOT), 'tag', tag, root=self.remote)
        previous = self.git('rev-parse', f'{tag}^{{commit}}', root=self.remote)
        self.assertEqual(previous, '400621d7a8e3559120efba537dbb8afb247cb8c4')
        candidate = self.git('commit-tree', 'main^{tree}', '-p', previous, '-m', 'Release candidate', root=self.remote)
        self.git('reset', '--hard', previous, root=self.remote)
        self.git('remote', 'set-url', 'origin', str(self.remote))
        self.git('fetch', '--depth=1', 'origin', '+refs/heads/main:refs/remotes/origin/main')
        self.git('switch', '-C', 'main', '--track', 'origin/main')
        self.assertEqual(self.git('rev-parse', 'HEAD'), previous)

        (self.home / '.codex').mkdir()
        claude_settings = self.home / '.claude/settings.json'
        codex_config = self.home / '.codex/config.toml'
        claude_settings.write_text('{"permissions":{"deny":["Bash(example)"]}}')
        codex_config.write_text('model = "user-selected-model"\n')
        self.shell(self.entry)
        skill = Path('task-router/SKILL.md')
        hosts = (self.home / '.claude/skills', self.home / '.agents/skills')
        previous_skill = (self.repo / skill).read_bytes()
        for folder in hosts:
            self.assertEqual((folder / skill).read_bytes(), previous_skill)
        backups = list((self.ghost / 'install-rollbacks').glob('rollback-_shared-*'))
        self.assertEqual(len(backups), 1, backups)
        backup = backups[0]
        self.assertFalse((backup / content_digest.MARKER_FILENAME).exists())
        self.assertEqual((backup / 'encoding_guard.py').read_bytes(),
                         (self.repo / '_shared/encoding_guard.py').read_bytes())
        original = content_digest.state(backup, include_generated=True)

        self.git('reset', '--hard', candidate, root=self.remote)
        self.git('pull', '--ff-only')
        self.assertEqual(self.git('rev-parse', 'HEAD'), candidate)
        self.shell(self.entry)
        self.assertEqual(content_digest.state(backup, include_generated=True), original)
        record = installed_payload.read(self.ghost)
        self.assertEqual(record['source']['commit'], candidate)
        self.assertEqual(installed_core_selection(record), {host: leaf_names(ALL_SKILLS) for host in ('claude', 'codex')})
        candidate_skill = (self.repo / skill).read_bytes()
        self.assertNotEqual(candidate_skill, previous_skill)
        for folder in hosts:
            self.assertEqual((folder / skill).read_bytes(), candidate_skill)
        claude = json.loads(claude_settings.read_text())
        codex = json.loads((self.home / '.codex/hooks.json').read_text())
        for settings in (claude, codex):
            self.assertIn('[hook-runner:session-receipt]', json.dumps(settings['hooks']['SessionStart']))
        self.assertIn('Bash(example)', claude['permissions']['deny'])
        config = tomllib.loads(codex_config.read_text())
        self.assertEqual(config['model'], 'user-selected-model')
        self.assertTrue(config['features']['hooks'])
        self.shell([*self.command, 'update'])
        self.assertEqual(content_digest.state(backup, include_generated=True), original)


class EarlierInstallerTests(HandoffCase):
    """The installer of an earlier commit made the installation; the checkout then holds main."""

    def setUp(self):
        super().setUp()
        self.git('fetch', '--quiet', '--depth=1', str(ROOT), EARLIER_COMMIT)
        self.git('checkout', '--quiet', '--detach', EARLIER_COMMIT)
        self.shell([*self.entry, *self.options, '--local-source'], GHOST_ALICE_SESSION_INTENT_ROOT=str(self.ledger))
        self.git('checkout', '--quiet', 'main')
        self.main = self.git('rev-parse', 'HEAD')
        self.old = self.active()

    def test_a_repair_from_this_checkout_does_not_start_the_earlier_installer(self):
        # The earlier installer restores every skill of its release. This checkout stops and names the update.
        shutil.rmtree(self.home / '.claude/skills/task-router')
        before = self.installation()
        output = self.shell([*self.entry, '--repair', '--platform', 'claude'], code=1)
        self.assertIn('install.sh update', output)
        self.assert_unchanged(before)

    def record_without_hosts(self):
        """The installation record files in the form whose ledger relations name a path only."""
        record = installed_payload.read(self.ghost)
        record.update(schema_version=2, source=dict(record['source'], ledger_source_roots={str(self.ledger): str(self.repo)}))
        installed_payload.read_path(self.ghost).unlink()
        notice = dict(schema_version=2, record_file='installed-payload-v2.json')
        for name, value in (('installed-payload-v2.json', record), ('installed-payload.json', notice)):
            (self.ghost / name).write_bytes(installed_payload._encode(value))
        self.assertEqual(installed_payload.read(self.ghost), record)

    def assert_stopped(self, argv, *, code=1):
        """The downloaded package stops before any change and names its own install file, which this returns."""
        before = self.installation()
        output = self.shell(argv, code=code)
        (package,) = self.packages() - {self.old.parent}
        command = package / 'core/install.sh'
        # The earlier installer adds its own line about the failed candidate.
        named = [line for line in output.splitlines()
                 if str(command) in line and 'Candidate install did not finish' not in line]
        self.assertEqual(len(named), 1, output)
        self.assert_unchanged(before)
        return command

    def assert_installed(self):
        record = installed_payload.read(self.ghost)
        self.assertNotEqual(Path(record['installer']['active_root']), self.old)
        self.assertEqual((record['source']['root'], record['source']['commit']), (str(self.repo), self.main))
        self.assertEqual(installed_core_selection(record), {'claude': ['task-router']})
        self.assertEqual(record['source']['ledger_source_roots'],
                         {str(self.ledger): {'hosts': ['claude'], 'source_root': str(self.repo)}})

    def assert_installs(self, command):
        # The installed selection decides although another host is present now.
        (self.home / '.codex').mkdir(exist_ok=True)
        self.shell(['bash', str(command)])
        self.assert_installed()

    def test_the_new_checkout_records_the_hosts_before_it_moves_work_records(self):
        self.record_without_hosts()
        inject_installer_python(self.env, """
import json, sys
def observe_followup(event, args):
    if event == 'subprocess.Popen' and args[3]:
        request = json.loads(args[3].get('GHOST_ALICE_PACKAGE_REQUEST') or '{}')
        record = Path.home() / '.ghost-alice/installed-payload-v3.json'
        if request.get('stage') == 'record-move' and record.exists():
            (Path.home() / 'at-followup.json').write_bytes(record.read_bytes())
sys.addaudithook(observe_followup)
""")
        self.shell([*self.entry, *self.options])
        self.assert_installed()
        observed = self.home / 'at-followup.json'
        self.assertTrue(observed.is_file(), 'The record move starts after the installation record names the hosts')
        self.assertEqual(json.loads(observed.read_bytes())['source'], installed_payload.read(self.ghost)['source'])
        self.assertFalse((self.ghost / 'pending-install-source.json').exists())

    def test_the_new_package_stays_the_installer_after_an_interruption_and_finishes(self):
        self.record_without_hosts()
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
        arm, started = self.home / 'interrupt', set()
        for name, command in (('installed-payload-v2.json', [*self.entry, *self.options]),
                              ('installed-payload-v3.json', self.command)):
            arm.write_text(name)
            output = self.shell(command, code=None)
            self.assertFalse(arm.exists(), f'No process replaced {name}\n{output}')
            self.assertNotEqual(self.active(), self.old, f'{name}\n{output}')
            started.add(self.active())
            lock = self.ghost / 'install.lock'
            if lock.exists():
                os.utime(lock, (1, 1))  # Model the stale interval after the process exited.
        self.shell(self.command)
        self.assert_installed()
        self.assertEqual({self.active()}, started)

    def test_install_and_hook_stop_before_any_change_and_the_named_command_installs(self):
        self.git('checkout', '--quiet', '-b', 'topic')
        self.commit('topic.txt', 'topic\n')
        self.git('checkout', '--quiet', 'main')
        # The merge runs the hook of the earlier package, which asks for every skill of every host, and succeeds.
        (self.home / '.codex').mkdir()
        merge = ['git', '-C', str(self.repo), 'merge', '--quiet', '--ff-only', 'topic']
        command = self.assert_stopped(merge, code=0)
        self.assertEqual(self.assert_stopped(self.command), command)
        self.assert_installs(command)

    def test_the_named_command_installs_another_host_with_the_arguments_of_the_stopped_command(self):
        (self.home / '.codex').mkdir()
        options = ['--platform', 'codex', '--skip-source-health']
        self.git('checkout', '--quiet', '--detach', EARLIER_COMMIT)
        command = self.assert_stopped([*self.entry, *options])
        self.shell(['bash', str(command), *options])
        self.assertEqual(installed_core_selection(installed_payload.read(self.ghost)),
                         {'claude': ['task-router'], 'codex': leaf_names(ALL_SKILLS)})

    def test_update_installs_the_downloaded_release(self):
        self.shell([*self.command, 'update'])
        self.assert_installed()


if __name__ == '__main__':
    unittest.main()
