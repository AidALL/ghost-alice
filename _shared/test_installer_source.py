"""installer.source: source health, git hook path, source identity, the install source check, --update-source.

Expected texts come from installer_lib/source_health.sh and installer_lib/install.sh:538-551.
"""
from __future__ import annotations

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
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installed_payload  # noqa: E402
from installer import report  # noqa: E402
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


class SuccessfulSourceVersionsTests(SourceCase):
    def setUp(self):
        super().setUp()
        self.repo = init_repo(self.root / 'repo')
        self.ctx = make_context(self.root / 'home', 'claude')
        self.versions = self.root / 'home/.ghost-alice/versions'

    def save(self, platform='claude', root=None, addons=()):
        self.ctx.platform = platform
        self.ctx.addon_sources = list(map(str, addons))
        with use_source(root or self.repo):
            lines = source.preserve_successful_source(self.ctx)
        folders = sorted(self.versions.iterdir()) if self.versions.exists() else []
        return folders, lines

    def test_saved_bytes_include_local_changes_and_repeat_hosts_share_one_version(self):
        (self.repo / '.gitignore').write_text('private.txt\n')
        (self.repo / 'private.txt').write_text('private')
        (self.repo / 'tracked.txt').write_text('local change')
        (self.repo / 'new.txt').write_text('untracked code')
        before = (self.repo / '.git/index').read_bytes()
        folders, lines = self.save()
        self.assertEqual(len(folders), 1)
        kept = folders[0] / 'core'
        self.assertEqual((kept / 'tracked.txt').read_text(), 'local change')
        self.assertEqual((kept / 'new.txt').read_text(), 'untracked code')
        self.assertFalse((kept / 'private.txt').exists())
        self.assertFalse((kept / '.git').exists())
        self.assertIn('--adopt-source', '\n'.join(lines))
        self.assertEqual(self.save('codex')[0], folders)
        self.assertEqual((self.repo / '.git/index').read_bytes(), before)

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
        with mock.patch.object(source.installed_payload, 'read', return_value=record):
            for number in range(1, 5):
                (self.repo / 'tracked.txt').write_text(str(number))
                folders, _ = self.save()
        self.assertEqual(len(folders), 4)
        self.assertIn(first, folders)
        self.assertTrue(link.exists())
        self.assertEqual(sorted((folder / 'core/tracked.txt').read_text() for folder in folders), ['2', '3', '4', 'one\n'])

    def test_source_changed_after_placement_does_not_become_a_successful_version(self):
        installed = self.root / 'installed.py'
        installed.write_text('placed bytes')
        (self.repo / 'tracked.txt').write_text('changed after placement')
        record = {'targets': {str(installed): {'source': str(self.repo / 'tracked.txt'),
                                              'used_by': ['claude'], 'mode': 'copy'}}}
        with mock.patch.object(source.installed_payload, 'read', return_value=record):
            with self.assertRaisesRegex(ValueError, 'Preserved source differs from installed code'):
                self.save()
        self.assertEqual(list(self.versions.iterdir()), [])

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

    def test_all_live_versions_survive_with_named_references_when_more_than_three_are_needed(self):
        record = {'targets': {}}
        with mock.patch.object(source.installed_payload, 'read', return_value=record):
            for number in range(4):
                (self.repo / 'tracked.txt').write_text(str(number))
                folders, lines = self.save()
                latest = next(folder for folder in folders if (folder / 'core/tracked.txt').read_text() == str(number))
                record['targets'][str(number)] = {'source': str(latest / 'core'), 'used_by': ['claude']}
        self.assertEqual(len(folders), 4)
        extra = next(folder for folder in folders if (folder / 'core/tracked.txt').read_text() == '0')
        self.assertIn(str(extra), '\n'.join(lines))
        self.assertIn('claude', '\n'.join(lines))

    def test_a_changed_archive_and_old_successes_survive_a_failed_new_copy(self):
        folders, _ = self.save()
        first = folders[0]
        (first / 'core/tracked.txt').write_text('user edit to preserved source')
        for number in range(4):
            (self.repo / 'tracked.txt').write_text(str(number))
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
            (self.repo / 'tracked.txt').write_text(str(number))
            self.save()
        (self.repo / 'tracked.txt').write_text('latest')
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
        (self.repo / 'code').mkdir()
        (self.repo / 'code/file.py').write_text('source')
        (self.repo / 'linked-code').symlink_to(self.repo / 'code', target_is_directory=True)
        git(self.repo, 'add', 'linked-code')
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

    def test_windows_reinstall_command_selects_supported_powershell(self):
        with mock.patch.object(source, 'os', wraps=os) as windows:
            windows.name = 'nt'
            _, lines = self.save('codex')
        self.assertIn('Reinstall this code: pwsh -NoProfile -ExecutionPolicy Bypass -File ', '\n'.join(lines))
        self.assertIn('core/install.ps1', '\n'.join(lines))

    def test_archive_pruning_uses_whole_copy_rules_for_caches_links_and_extra_content(self):
        folder = self.save()[0][0]
        self.assertTrue(source._unchanged_version(folder))
        cache = folder / 'core/__pycache__'
        cache.mkdir()
        (cache / 'generated.pyc').write_bytes(b'generated cache')
        self.assertTrue(source._unchanged_version(folder))
        link = cache / 'user-link'
        link.symlink_to(self.repo / 'tracked.txt')
        self.assertFalse(source._unchanged_version(folder))
        link.unlink()
        extra = folder / 'personal.txt'
        extra.write_text('user content')
        self.assertFalse(source._unchanged_version(folder))
        extra.unlink()
        (folder / 'core/tracked.txt').unlink()
        self.assertFalse(source._unchanged_version(folder))

    def test_service_and_pending_merge_consumers_keep_their_source_versions(self):
        service_version = self.save()[0][0]
        (self.repo / 'tracked.txt').write_text('pending version')
        pending_version = next(folder for folder in self.save()[0] if folder != service_version)
        pending = self.versions.parent / 'pending-merges/claude/manifest.json'
        pending.parent.mkdir(parents=True)
        pending.write_text(json.dumps({'entries': [{'backup_path': str(pending_version / 'core/tracked.txt'), 'decided': False}]}))
        record = {'service': {'program': str(service_version / 'core/tracked.txt')}}
        with mock.patch.object(source.installed_payload, 'read', return_value=record):
            for number in range(4):
                (self.repo / 'tracked.txt').write_text(str(number))
                folders, _ = self.save()
        self.assertEqual(len(folders), 5)
        self.assertIn(service_version, folders)
        self.assertIn(pending_version, folders)
        self.assertEqual(sorted((folder / 'core/tracked.txt').read_text() for folder in folders),
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
                                         '--platform', platform, '--adopt-source', *skills], cwd=package, env=env,
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
            self.assertEqual(target.resolve(), first / 'core/task-router')
            self.assertEqual((target / 'SKILL.md').read_bytes(), original)
            self.assertIn('keep-me', settings.read_text())
            self.assertEqual(len(list(versions.iterdir())), 2)
            self.assertIn('Reinstall this code:', output)
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
                      "before installing. If the human user decided to install from this local source, rerun with --skip-source-health.", out)
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


class GitHooksTests(SourceCase):
    """The install writes the repository's shared `core.hooksPath` as the absolute path of the install source's
    hooks folder, so a merge in any worktree runs the install source's post-merge hook and no other checkout's hook
    file. The change it returns tells the install-state what the full uninstall restores."""

    def setUp(self):
        super().setUp()
        self.repo = init_repo(self.root / "repo").resolve()
        self.enterContext(use_source(self.repo))
        self.hooks = (self.repo / "hooks").as_posix()

    def shared_value(self, repo: Path | None = None) -> str | None:
        result = subprocess.run(["git", "-C", str(repo or self.repo), "config", "--local", "--get", "core.hooksPath"],
                                capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    def test_concurrent_worktree_adoptions_keep_the_original_hook_path(self):
        git(self.repo, "config", "--local", "core.hooksPath", "user-hooks")
        other = self.root / "other"
        git(self.repo, "worktree", "add", "-q", "-b", "other", str(other))
        script = r'''
import contextlib, io, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import file_lock
from installer import runner, source
runner.REPO_ROOT = Path(sys.argv[2])
role = sys.argv[3]
write = source._write_hook_path_record
git = source._git
lock = file_lock.exclusive
def paused_write(change):
    print("before-record", file=sys.__stdout__, flush=True)
    sys.stdin.readline()
    write(change)
def observed_git(root, *args):
    if args == ("config", "--local", "--get", "core.hooksPath"):
        print("read-current", file=sys.__stdout__, flush=True)
    return git(root, *args)
@contextlib.contextmanager
def observed_lock(*args, **kwargs):
    print("lock-request", file=sys.__stdout__, flush=True)
    with lock(*args, **kwargs) as held:
        yield held
if role == "first":
    source._write_hook_path_record = paused_write
else:
    source._git = observed_git
    file_lock.exclusive = observed_lock
with contextlib.redirect_stdout(io.StringIO()):
    change = source.setup_git_hooks()
print(json.dumps(vars(change)), flush=True)
'''
        args = [sys.executable, "-u", "-c", script, str(Path(__file__).resolve().parent)]
        first = subprocess.Popen([*args, str(self.repo), "first"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = None
        try:
            self.assertEqual(first.stdout.readline().strip(), "before-record")
            second = subprocess.Popen([*args, str(other), "second"], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            boundary = second.stdout.readline().strip()
            self.assertIn(boundary, {"lock-request", "read-current"})
            # Without serialization, let B finish while A has written Git but not its record.
            if boundary == "read-current":
                second_out, second_err = second.communicate(timeout=10)
            first_out, first_err = first.communicate("continue\n", timeout=10)
            if boundary == "lock-request":
                second_out, second_err = second.communicate(timeout=10)
            self.assertEqual(first.returncode, 0, first_err)
            self.assertEqual(second.returncode, 0, second_err)
            self.assertEqual(json.loads(first_out.splitlines()[-1])["before"], "user-hooks")
            self.assertEqual(json.loads(second_out.splitlines()[-1])["before"], "user-hooks")
            record = self.root / "home" / ".ghost-alice" / "install-state" / source.HOOK_PATH_RECORD
            self.assertEqual(json.loads(record.read_text())["before"], "user-hooks")
            self.assertEqual(self.shared_value(), source.hooks_path_value(other))
        finally:
            for process in (first, second):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()

    def setup_hooks(self) -> tuple[source.HookPathChange, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            change = source.setup_git_hooks()
        return change, out.getvalue()

    def record_install_state(self, platform: str, **entry) -> None:
        state = self.root / "home" / ".ghost-alice" / "install-state" / f"{platform}.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        change = {"kind": "source_repo_hook_path", "repo_root": "/earlier/source", **entry}
        state.write_text(json.dumps({"schema_version": 1, "platform": platform, "targets": [],
                                     "system_env_changes": [change]}), encoding="utf-8")

    def test_the_shared_value_names_the_install_sources_hooks_folder_by_absolute_path(self):
        # source_health.sh:91-116, with the absolute path in place of the relative `hooks`
        hook = self.repo / "hooks" / "post-merge"
        hook.parent.mkdir()
        hook.write_text("#!/bin/sh\n", encoding="utf-8")
        hook.chmod(0o644)
        change, out = self.setup_hooks()
        self.assertEqual(change, source.HookPathChange(changed=True, recorded=True, before_present=False, before="",
                                                       after=self.hooks))
        self.assertEqual(self.shared_value(), self.hooks)
        self.assertIn(f"[INFO] Git hook path → {self.hooks} (post-merge auto-refresh enabled)", out)
        self.assertTrue(hook.stat().st_mode & stat.S_IXUSR)

    def test_an_earlier_installers_relative_value_leaves_no_previous_value(self):
        git(self.repo, "config", "--local", "core.hooksPath", "hooks")
        change, _ = self.setup_hooks()
        self.assertEqual(change, source.HookPathChange(changed=True, recorded=True, before_present=False, before="",
                                                       after=self.hooks))
        self.assertEqual(self.shared_value(), self.hooks)

    def test_another_value_is_the_value_to_restore(self):
        git(self.repo, "config", "--local", "core.hooksPath", "elsewhere")
        change, _ = self.setup_hooks()
        self.assertEqual(change, source.HookPathChange(changed=True, recorded=True, before_present=True,
                                                       before="elsewhere", after=self.hooks))

    def test_a_value_an_earlier_install_wrote_keeps_the_recorded_previous_value(self):
        # A reinstall finds its own value in place: the install-state of either host carries what was there before.
        git(self.repo, "config", "--local", "core.hooksPath", self.hooks)
        self.record_install_state("codex", before_present=True, before="custom-hooks", after=self.hooks,
                                  applied_at="2026-10-01T00:00:00Z")
        change, out = self.setup_hooks()
        self.assertEqual(change, source.HookPathChange(changed=False, recorded=True, before_present=True,
                                                       before="custom-hooks", after=self.hooks,
                                                       applied_at="2026-10-01T00:00:00Z"))
        self.assertNotIn("Git hook path", out)
        # An install source that moved inside the repository replaces the earlier source's value, and the value to
        # restore stays the one from before Ghost-ALICE wrote any.
        earlier = "/earlier/source/hooks"
        git(self.repo, "config", "--local", "core.hooksPath", earlier)
        self.record_install_state("codex", before_present=False, before=None, after=earlier,
                                  applied_at="2026-09-01T00:00:00Z")
        change, _ = self.setup_hooks()
        self.assertEqual(change, source.HookPathChange(changed=True, recorded=True, before_present=False, before="",
                                                       after=self.hooks))
        self.assertEqual(self.shared_value(), self.hooks)

    def test_an_earlier_installers_record_of_the_relative_value_keeps_the_value_it_replaced(self):
        # An earlier installer replaced a user's value with the relative `hooks` and recorded it: the value from before
        # Ghost-ALICE is that user's value, for any checkout of the same repository.
        worktree = (self.root / "earlier-worktree").resolve()
        git(self.repo, "worktree", "add", "-q", "-b", "earlier", str(worktree))
        for recorded_root in (self.repo, worktree):
            with self.subTest(recorded_root=recorded_root.name):
                git(self.repo, "config", "--local", "core.hooksPath", "hooks")
                self.record_install_state("claude", repo_root=str(recorded_root), before_present=True,
                                          before="custom-hooks", after="hooks")
                change, _ = self.setup_hooks()
                self.assertEqual(change, source.HookPathChange(changed=True, recorded=True, before_present=True,
                                                               before="custom-hooks", after=self.hooks))
        # The relative value names no folder: a record from another repository says nothing about this one.
        other = init_repo(self.root / "other").resolve()
        git(self.repo, "config", "--local", "core.hooksPath", "hooks")
        self.record_install_state("claude", repo_root=str(other), before_present=True, before="custom-hooks",
                                  after="hooks")
        change, _ = self.setup_hooks()
        self.assertEqual(change, source.HookPathChange(changed=True, recorded=True, before_present=False, before="",
                                                       after=self.hooks))

    def test_an_install_that_stops_after_the_write_leaves_its_record_for_the_retry(self):
        # The git hook step runs before the later steps; an install that stops before its install-state is written
        # still leaves the record of the write it made, written in the same step, so the retry knows the value to
        # restore.
        git(self.repo, "config", "--local", "core.hooksPath", "elsewhere")
        self.setup_hooks()
        record = json.loads((self.root / "home" / ".ghost-alice" / "install-state" / "source-repo-hook-path.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual({key: record[key] for key in ("kind", "repo_root", "before_present", "before", "after")},
                         {"kind": "source_repo_hook_path", "repo_root": self.repo.as_posix(), "before_present": True,
                          "before": "elsewhere", "after": self.hooks})
        self.assertTrue(record["applied_at"])
        retry, out = self.setup_hooks()
        self.assertEqual(retry, source.HookPathChange(changed=False, recorded=True, before_present=True,
                                                      before="elsewhere", after=self.hooks,
                                                      applied_at=record["applied_at"]))
        self.assertEqual(out, "")

    def test_a_value_in_place_that_no_install_state_names_has_no_known_previous_value(self):
        # Another installation (another HOME) or the user wrote this value: the full uninstall leaves it alone.
        git(self.repo, "config", "--local", "core.hooksPath", self.hooks)
        change, out = self.setup_hooks()
        self.assertEqual(change, source.HookPathChange(changed=False, recorded=False, after=self.hooks))
        self.assertEqual(out, "")

    def test_a_linked_worktree_writes_the_shared_value_and_keeps_the_worktree_setting(self):
        worktree = (self.root / "worktree").resolve()
        git(self.repo, "worktree", "add", "-q", "-b", "work", str(worktree))
        git(self.repo, "config", "--local", "extensions.worktreeConfig", "true")
        git(worktree, "config", "--worktree", "core.hooksPath", "/dev/null")
        with use_source(worktree):
            change, _ = self.setup_hooks()
        self.assertEqual(change.after, (worktree / "hooks").as_posix())
        self.assertEqual(self.shared_value(), (worktree / "hooks").as_posix())
        self.assertEqual(git(worktree, "config", "--worktree", "--get", "core.hooksPath"), "/dev/null")

    def test_setup_git_hooks_outside_git_warns(self):
        plain = self.root / "plain"
        plain.mkdir()
        self.enterContext(use_source(plain))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            change = source.setup_git_hooks()
        self.assertFalse(change.changed or change.recorded)
        self.assertIn("[WARN] Failed to set Git hook path. Post-merge hook may not fire", out.getvalue())


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


STOP_HINT = ("Rerun this command from the install source, or add --adopt-source to install from this checkout "
             "and record it as the install source.")


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
        self.assertEqual(replies.call_args.args, ("Replace the install source with this checkout? [y/N]: ",))
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
                "schema_version": 1, "phase": "prepare", "record_copy": installed_payload.FILENAME, "targets": []}),
                encoding="utf-8")
        self.write_record(self.other)
        self.assertEqual(source.match_install_source().state, "same")
        self.assertEqual(self.run_check()[:2], (0, ""))
        # The oldest unfinished publish found no record: after the restore there is none, so nothing is compared.
        (older / installed_payload.FILENAME).unlink()
        (older / "journal.json").write_text(json.dumps({"schema_version": 1, "phase": "prepare", "record_copy": None,
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
