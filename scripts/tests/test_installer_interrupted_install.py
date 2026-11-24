"""An installation that stops or fails after it started, with the real installer in a temporary HOME.

A stop ends the installer's whole process group at one point, as a power loss does. The release that activated itself
owns the installation from then on: the next command starts that release, and that release finishes or removes the
installation. The install source is a copy of this checkout in its own repository (`installer_testing.checkout_copy`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_shared'))
from installer import selection  # noqa: E402
from installer_testing import checkout_copy, inject_installer_python, installer_env  # noqa: E402
import install_transaction  # noqa: E402
import installed_payload  # noqa: E402

# Every Python process of the installer runs this. The file `stop-at` in the HOME names one point and is used once:
# `unactivated` and `record:0` (before and after the first record replacement outside a publish), `activated` (before
# the first host child), `staged:N` (before publish N moves an original), `original:N`, `target:N` and `record:N`
# (after publish N moved an original, placed a target or replaced the record) and `fail:HOST` (the first target in the
# folders of HOST is refused).
STOP = '''
import json, signal, sys
_home = Path(os.environ['HOME'])
_ghost, _stop_file = _home / '.ghost-alice', _home / 'stop-at'

def _at(point):
    try:
        wanted = _stop_file.read_text()
    except FileNotFoundError:
        return False
    if wanted == point:
        _stop_file.unlink()
        return True
    return False

def _stop_at(point):
    if _at(point):
        if os.getpgrp() == TEST_GROUP:
            raise SystemExit('The installer of this test needs its own process group')
        os.killpg(os.getpgrp(), signal.SIGKILL)
        signal.pause()

def _open_publish():
    journals = [json.loads(path.read_text()) for path in (_ghost / 'rollbacks').glob('*/journal.json')]
    journals = [journal for journal in journals if journal['targets']]
    for journal in journals:
        if journal['phase'] == 'prepare':
            return len(journals), journal['host'], [target['dest'] for target in journal['targets']]
    return 0, None, ()

_replace, _rename = os.replace, os.rename

def replace(source, dest, *args, **kwargs):
    if not _stop_file.exists() or Path(dest).parent != _ghost or not Path(dest).name.startswith('installed-payload-v'):
        return _replace(source, dest, *args, **kwargs)
    number, _, _ = _open_publish()
    if not number:
        _stop_at('unactivated')
    result = _replace(source, dest, *args, **kwargs)
    _stop_at(f'record:{number}')
    return result

def rename(source, dest, *args, **kwargs):
    if not _stop_file.exists():
        return _rename(source, dest, *args, **kwargs)
    number, host, destinations = _open_publish()
    if str(dest) in destinations and not Path(dest).is_relative_to(_ghost) and _at(f'fail:{host}'):
        raise OSError('the fixture refuses this target')
    if str(source) in destinations:
        _stop_at(f'staged:{number}')
    result = _rename(source, dest, *args, **kwargs)
    if str(source) in destinations:
        _stop_at(f'original:{number}')
    if str(dest) in destinations:
        _stop_at(f'target:{number}')
    return result

def _host_child(event, args):
    if event == 'subprocess.Popen' and args[3] and args[3].get('GHOST_ALICE_INSTALL_HOST_CHILD') == '1':
        _stop_at('activated')

os.replace, os.rename = replace, rename
sys.addaudithook(_host_child)
'''
# The earlier package keeps the management entry that its command starts, and refuses every other command.
MANAGEMENT_ENTRY_ONLY = '''if __name__ == "__main__" and sys.argv[1:2] != ['--management-entry']:
    (Path.home() / 'earlier-package-ran').write_text(' '.join(sys.argv[1:]))
    raise SystemExit(97)

'''
OPTIONS = ['--local-source', '--skip-source-health']
DISPATCHER = 'ghost-alice-hook.mjs'
# The parts of the record root that an installation may change before its first publish.
PREPARATION = ('versions', 'git-hooks', 'install', 'install.lock', 'install.lock.guard')
STAGED_RECORD = '.installed-payload'


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash') and shutil.which('git'), 'requires the POSIX installer')
class InterruptedInstallTests(unittest.TestCase):
    def setUp(self):
        self.source = checkout_copy(self)

    def new_home(self):
        """An empty HOME with one host, and the install source as it was committed."""
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.ghost = self.home / '.ghost-alice'
        self.command = ['bash', str(self.ghost / 'bin/ghost-alice')]
        self.entry = ['bash', str(self.source / 'install.sh'), *OPTIONS]
        (self.home / '.claude').mkdir()
        self.env = installer_env(self.home)
        self.env['GHOST_ALICE_INSTALL_LOCALE'] = 'en'
        inject_installer_python(self.env, STOP.replace('TEST_GROUP', str(os.getpgrp())))
        self.git('checkout', '-q', '--', '.')
        self.git('config', '--local', '--unset-all', 'core.hooksPath')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.source), *args], env=self.env, capture_output=True, text=True).stdout.strip()

    def hook_path(self):
        return self.git('config', '--local', '--get', 'core.hooksPath')

    def shell(self, argv, *, code=0):
        result = subprocess.run(argv, cwd=self.home, env=self.env, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=300, start_new_session=True)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, code, output)
        return output

    def stop(self, argv, point):
        """Run `argv` until `point`, and let the lock of the ended process become stale."""
        (self.home / 'stop-at').write_text(point)
        self.shell(argv, code=-int(signal.SIGKILL))
        self.assertFalse((self.home / 'stop-at').exists())
        os.utime(self.ghost / 'install.lock', (1, 1))

    def versions(self) -> set[Path]:
        root = self.ghost / 'versions'
        return {path for path in root.iterdir() if not path.name.startswith('.')} if root.is_dir() else set()

    def saved(self, package: Path) -> dict:
        return json.loads((package.parent / 'version.json').read_text())

    def active(self) -> str | None:
        """The package that the management command starts."""
        return (installed_payload.read(self.ghost) or {}).get('installer', {}).get('active_root')

    def files(self, root: Path | None = None) -> dict[str, object]:
        """Every file and link of `root`, else of the host folders and of the record root without PREPARATION."""
        roots = [root] if root else [self.home / '.claude', self.home / '.codex', self.home / '.agents',
                                     *(path for path in self.ghost.glob('*') if path.name not in PREPARATION
                                       and not path.name.startswith(STAGED_RECORD))]
        paths = [path for folder in roots for path in (folder.rglob('*') if folder.is_dir() else [folder])]
        return {path.relative_to(self.home).as_posix(): os.readlink(path) if path.is_symlink() else path.read_bytes()
                for path in paths if path.is_symlink() or path.is_file()}

    def changed(self, before: dict, root: Path | None = None) -> list[str]:
        after = self.files(root)
        return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))

    def change_source(self, text: str):
        """Another release: the skill, the hook dispatcher and the runtime differ from the installed ones."""
        for name, line in (('task-router/SKILL.md', f'\n{text}\n'), (f'_shared/{DISPATCHER}', f'\n// {text}\n')):
            with open(self.source / name, 'a', encoding='utf-8') as file:
                file.write(line)

    def change_journal_form(self):
        """The later release writes journals that the installed release does not read."""
        module, line = self.source / '_shared/install_transaction.py', 'JOURNAL_SCHEMA_VERSION = {}\n'
        text, form = module.read_text(encoding='utf-8'), install_transaction.JOURNAL_SCHEMA_VERSION
        self.assertEqual(text.count(line.format(form)), 1)
        module.write_text(text.replace(line.format(form), line.format(form + 1)), encoding='utf-8')

    def install_earlier(self) -> Path:
        """A completed installation of the committed source. Its package then runs only its management entry."""
        self.shell([*self.entry, 'task-router'])
        package = Path(self.active())
        self.assert_complete(package)
        cli = package / '_shared/installer/cli.py'
        text = cli.read_text(encoding='utf-8')
        start = 'if __name__ == "__main__" and not __package__:\n'
        self.assertEqual(text.count(start), 1)
        cli.write_text(text.replace(start, MANAGEMENT_ENTRY_ONLY + start), encoding='utf-8')
        return package

    def stopped_candidate(self, argv, point) -> Path:
        before = self.versions()
        self.stop(argv, point)
        added, = self.versions() - before
        self.assertEqual(self.saved(added / 'core')['status'], 'candidate')
        return added / 'core'

    def assert_complete(self, package: Path, hosts=('claude',)):
        record = installed_payload.read(self.ghost)
        self.assertEqual(record['installer']['active_root'], str(package))
        self.assertEqual(self.saved(package)['status'], 'successful')
        self.assertEqual(selection.installed_core_selection(record), {host: ['task-router'] for host in hosts})
        self.assertEqual(install_transaction.unfinished(self.ghost), [])
        for host, skills in (('claude', '.claude/skills'), ('codex', '.agents/skills')):
            skill = self.home / skills / 'task-router/SKILL.md'
            self.assertEqual(skill.is_file(), host in hosts)
            if host in hosts:
                self.assertEqual(skill.read_bytes(), (self.source / 'task-router/SKILL.md').read_bytes())
        self.assertEqual((self.ghost / 'hooks' / DISPATCHER).read_bytes(), (self.source / '_shared' / DISPATCHER).read_bytes())
        self.assertIn(str(package), (self.ghost / 'bin/ghost-alice').read_text())
        self.assertFalse((self.ghost / installed_payload.PENDING_SOURCE_FILENAME).exists())
        self.assertFalse((self.home / 'earlier-package-ran').exists())

    def finish(self, package: Path):
        """The plain rerun of the active command, else of the stopped package, installs the saved selection."""
        codex = self.home / '.codex'
        codex.mkdir()  # A newly detected host is not a saved selection.
        command = self.command if Path(self.command[1]).is_file() and self.active() else ['bash', str(package / 'install.sh')]
        self.shell([*command, *OPTIONS])
        self.assertEqual(list(codex.iterdir()), [])
        self.assert_complete(package)

    def test_a_stop_before_the_activation_changes_only_the_prepared_package(self):
        self.new_home()
        earlier = self.install_earlier()
        self.change_source('Later release.')
        before, archive = self.files(), self.files(earlier.parent)
        candidate = self.stopped_candidate([*self.entry, 'task-router'], 'unactivated')
        self.assertEqual(self.active(), str(earlier))
        self.assertEqual(self.changed(before), [])
        self.assertEqual(self.changed(archive, earlier.parent), [])
        self.shell([*self.entry, 'task-router'])
        self.assert_complete(candidate)

    def test_an_upgrade_that_stops_is_finished_by_the_release_that_activated_itself(self):
        for point in ('record:0', 'staged:1', 'original:1', 'target:1', 'record:1', 'activated', 'target:2'):
            with self.subTest(point=point):
                self.new_home()
                earlier = self.install_earlier()
                self.change_source('Later release.')
                self.change_journal_form()
                record, before, archive = installed_payload.read(self.ghost), self.files(), self.files(earlier.parent)
                candidate = self.stopped_candidate([*self.entry, 'task-router'], point)
                self.assertEqual(self.active(), str(candidate))
                self.assertEqual(self.changed(archive, earlier.parent), [])
                self.assertEqual(len(install_transaction.unfinished(self.ghost)),
                                 0 if point in ('record:0', 'activated') else 1)
                if point == 'record:0':
                    self.assertEqual(installed_payload.read(self.ghost),
                                     dict(record, installer=dict(record['installer'], active_root=str(candidate))))
                    self.assertEqual(self.changed(before),
                                     [installed_payload.read_path(self.ghost).relative_to(self.home).as_posix()])
                if point == 'activated':
                    self.assertIn(str(candidate), Path(self.command[1]).read_text())
                    self.assertFalse([name for name in self.changed(before) if not name.startswith('.ghost-alice/')])
                # The journal holds the original of the command while the replacement moves into place.
                if point != 'original:1':
                    self.assertTrue(Path(self.command[1]).is_file())
                self.finish(candidate)

    def test_a_first_installation_that_stops_is_finished_by_its_own_package(self):
        for point in ('activated', 'target:1', 'record:1', 'record:2'):
            with self.subTest(point=point):
                self.new_home()
                candidate = self.stopped_candidate([*self.entry, 'task-router'], point)
                # The first publish creates the record, and the publish of the runtime creates the command.
                self.assertEqual(self.active(), str(candidate) if point in ('record:1', 'record:2') else None)
                self.assertEqual(Path(self.command[1]).is_file(), point == 'record:2')
                if point == 'activated':
                    self.assertEqual(self.changed({}), [])
                self.assertEqual(Path(self.hook_path()), self.ghost / 'git-hooks')
                self.finish(candidate)

    def test_a_refused_first_placement_changes_no_skill_and_the_same_command_finishes_the_installation(self):
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade):
                self.new_home()
                if upgrade:
                    self.install_earlier()
                    self.change_source('Later release.')
                before, versions = self.files(), self.versions()
                (self.home / 'stop-at').write_text('fail:claude')
                output = self.shell([*self.entry, 'task-router'], code=1)
                self.assertFalse((self.home / 'stop-at').exists())
                added, = self.versions() - versions
                candidate = added / 'core'
                self.assertEqual(self.saved(candidate)['status'], 'candidate')
                self.assertEqual(self.active(), str(candidate) if upgrade else None)
                self.assertEqual(install_transaction.unfinished(self.ghost), [])
                changed = self.changed(before)
                for name in ('.claude/skills/task-router/SKILL.md', '.ghost-alice/install-state/claude.json'):
                    self.assertNotIn(name, changed)
                self.assertEqual('.ghost-alice/bin/ghost-alice' in changed, upgrade)
                self.assertIn('run the same command again', output)
                self.assertEqual(f'The active installer is {candidate}.' in output, upgrade, output)
                self.shell([*self.entry, 'task-router'])
                self.assert_complete(candidate)

    def test_a_first_installation_whose_second_host_fails_is_finished_by_the_same_command(self):
        self.new_home()
        (self.home / '.codex').mkdir()
        (self.home / 'stop-at').write_text('fail:codex')
        output = self.shell([*self.entry, 'task-router'], code=1)
        self.assertFalse((self.home / 'stop-at').exists())
        added, = self.versions()
        candidate = added / 'core'
        self.assertEqual(self.active(), str(candidate))
        self.assertEqual(self.saved(candidate)['status'], 'candidate')
        self.assertIn('run the same command again', output)
        self.assertIn(f'The active installer is {candidate}.', output)
        self.assertTrue((self.home / '.claude/skills/task-router/SKILL.md').is_file())
        self.assertFalse((self.home / '.agents/skills/task-router').exists())
        self.shell([*self.entry, 'task-router'])
        self.assert_complete(candidate, hosts=('claude', 'codex'))

    def test_a_host_whose_first_placement_fails_does_not_stop_the_removal_of_the_installation(self):
        for case, host in (('first installation', 'claude'), ('added host', 'codex')):
            with self.subTest(case=case):
                self.new_home()
                if case == 'added host':
                    self.shell([*self.entry, 'task-router'])
                    self.change_source('Later release.')
                    (self.home / '.codex').mkdir()
                (self.home / 'stop-at').write_text(f'fail:{host}')
                self.shell([*self.entry, '--platform', host, 'task-router'], code=1)
                self.assertFalse((self.home / 'stop-at').exists())
                targets = (installed_payload.read(self.ghost) or {}).get('targets', {}).values()
                self.assertEqual({user for entry in targets for user in entry['used_by']},
                                 {'claude'} if case == 'added host' else set())
                # The event log of a host without an inventory stays for review.
                self.shell(['bash', str(self.source / 'install.sh'), '--uninstall'], code=1)
                self.assertIsNone(installed_payload.read(self.ghost))
                self.assertFalse(os.path.lexists(self.command[1]))
                self.assertFalse((self.ghost / 'hooks' / DISPATCHER).exists())
                self.assertEqual(self.hook_path(), '')

    def test_the_next_installation_leaves_out_what_a_removal_took_after_a_failed_installation(self):
        one, two = ['task-router'], ['necessity-gate', 'task-router']
        for case, hosts, installed, requested, refused, removal, code, left in (
                ('installed host', ('.claude', '.codex'), one, one, 'claude', ['claude', '--uninstall'], 0, {'codex': one}),
                ('installed skill', ('.claude',), two, two, 'claude', ['claude', '--uninstall', 'necessity-gate'], 0,
                 {'claude': one}),
                # The event log of a host without an inventory stays for review.
                ('added host', ('.claude',), one, one, 'codex', ['codex', '--uninstall'], 1, {'claude': one}),
                ('added skill', ('.claude', '.codex'), one, two, 'codex', ['codex', '--uninstall', 'necessity-gate'], 0,
                 {'claude': two, 'codex': one})):
            with self.subTest(case=case):
                self.new_home()
                for host in hosts:
                    (self.home / host).mkdir(exist_ok=True)
                self.shell([*self.entry, *installed])
                self.change_source('Later release.')
                (self.home / f'.{refused}').mkdir(exist_ok=True)
                (self.home / 'stop-at').write_text(f'fail:{refused}')
                self.shell([*self.entry, *requested], code=1)
                self.assertFalse((self.home / 'stop-at').exists())
                self.shell([*self.command, '--platform', *removal], code=code)
                self.shell([*self.command, *OPTIONS])
                self.assertEqual(selection.installed_core_selection(installed_payload.read(self.ghost)), left)
                self.assertEqual(self.saved(Path(self.active()))['status'], 'successful')

    def test_a_removal_after_the_activation_takes_what_the_removal_of_a_completed_installation_takes(self):
        completed: set[str] = set()
        for case in ('completed', 'first installation', 'upgrade', 'return to an earlier version'):
            with self.subTest(case=case):
                self.new_home()
                if case in ('upgrade', 'return to an earlier version'):
                    self.shell([*self.entry, 'task-router'])
                    self.change_source('Later release.')
                if case == 'return to an earlier version':
                    self.shell([*self.entry, 'task-router'])
                    self.git('checkout', '-q', '--', '.')
                if case == 'completed':
                    self.shell([*self.entry, 'task-router'])
                else:
                    self.stop([*self.entry, 'task-router'], 'activated')
                if case == 'first installation':
                    self.assertIsNone(self.active())
                else:
                    self.assertEqual((Path(self.active()) / 'task-router/SKILL.md').read_bytes(),
                                     (self.source / 'task-router/SKILL.md').read_bytes())
                self.assertEqual(Path(self.hook_path()), self.ghost / 'git-hooks')
                command = self.command if Path(self.command[1]).is_file() else ['bash', str(self.source / 'install.sh')]
                self.shell([*command, '--uninstall'])
                for report in (self.ghost / 'uninstall-reports').glob('*-apply-*.json'):
                    self.assertNotIn('manual-review', [item.get('action') for item in json.loads(report.read_text())['items']])
                self.assertIsNone(installed_payload.read(self.ghost))
                self.assertFalse((self.ghost / 'hooks' / DISPATCHER).exists())
                self.assertEqual(self.hook_path(), '')
                left = {name for name in self.files() if not name.startswith('.ghost-alice/uninstall-')}
                if case == 'completed':
                    completed = left
                self.assertEqual(left - completed, set())


if __name__ == '__main__':
    unittest.main()
