import hashlib
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from glob import glob
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "_shared"))
import uninstall_cleanup as cleanup
from installer_testing import checkout_copy, destination_fixture, installer_home_effects, installer_env  # noqa: E402
import install_destination
from installer import gateway_participant

UNINSTALL_CLEANUP = REPO_ROOT / "_shared" / "uninstall_cleanup.py"
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
README = REPO_ROOT / "README.md"
UNINSTALL_DOC = REPO_ROOT / "docs" / "getting-started" / "uninstall.md"
REMOVED_BASH_FLAG = "--off" + "board"
REMOVED_BASH_CONFIRM_FLAG = REMOVED_BASH_FLAG + "-confirm"
REMOVED_BASH_REPORT_FLAG = REMOVED_BASH_FLAG + "-report"
REMOVED_PS_FLAG = "-Off" + "board"
REMOVED_PS_CONFIRM_FLAG = REMOVED_PS_FLAG + "Confirm"
REMOVED_PS_REPORT_FLAG = REMOVED_PS_FLAG + "Report"
REMOVED_DOC_STEM = "off" + "boarding"


def _find_test_bash() -> str | None:
    candidates = [
        shutil.which("bash"),
        shutil.which("bash.exe"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        normalized = path.as_posix().lower()
        if sys.platform.startswith("win") and (
            normalized.endswith("/windows/system32/bash.exe")
            or normalized.endswith("/appdata/local/microsoft/windowsapps/bash.exe")
        ):
            continue
        return str(path)
    return None


def _make_directory_link_or_skip(testcase: unittest.TestCase, dest: Path, source: Path) -> str:
    try:
        dest.symlink_to(source, target_is_directory=True)
        return "symlink"
    except OSError as exc:
        symlink_error = exc

    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dest), str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            return "junction"
        testcase.skipTest(
            "directory symlink unavailable and Windows junction fallback failed: "
            f"{symlink_error}; {result.stderr or result.stdout}"
        )

    testcase.skipTest(f"directory symlink unavailable: {symlink_error}")


def _assert_directory_link_exists(testcase: unittest.TestCase, path: Path, link_kind: str) -> None:
    if link_kind == "symlink":
        testcase.assertTrue(path.is_symlink())
    else:
        testcase.assertTrue(path.exists(), msg=f"{link_kind} should still exist: {path}")


def _make_windows_junction_or_skip(testcase: unittest.TestCase, dest: Path, source: Path) -> None:
    if os.name != "nt":
        testcase.skipTest("Windows junction regression test")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(dest), str(source)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        testcase.skipTest(f"Windows junction unavailable: {result.stderr or result.stdout}")


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_managed_skill(root: Path, name: str) -> Path:
    target = root / ".agents" / "skills" / name
    target.mkdir(parents=True)
    skill_file = target / "SKILL.md"
    skill_file.write_text(f"# {name}\n", encoding="utf-8")
    marker = {
        "schema_version": 1,
        "managed_by": "Ghost-ALICE",
        "platform": "codex",
        "asset_id": name,
        "source_repo": "test-repo",
        "source_commit": "abc123",
        "installed_at": "2026-05-16T00:00:00Z",
        "install_mode": "copy",
        "content_hashes": {"SKILL.md": _hash_file(skill_file)},
        "encoding_contract": {"text": "utf-8-strict"},
    }
    (target / ".ghost-alice-install.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def _write_manifest(
    root: Path,
    targets: list[dict],
    *,
    source_root: Path | None = None,
    system_env_changes: list[dict] | None = None,
) -> Path:
    manifest = root / ".ghost-alice" / "install-state" / "codex.json"
    manifest.parent.mkdir(parents=True)
    if source_root is None:
        source_root = root / "repo"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platform": "codex",
                "installed_at": "2026-05-16T00:00:00Z",
                "source_root": source_root.as_posix(),
                "source_head": "abc123",
                "targets": targets,
                "system_env_changes": system_env_changes or [],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


class ConfigurationRecoveryTest(unittest.TestCase):
    def exercise(self, kind, *, failure=None, before_state=None, current_config=None):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            stack.enter_context(mock.patch.dict(os.environ, installer_env(root), clear=True))
            stack.enter_context(install_destination.use(destination_fixture(
                root, platform_dir=str(root / '.codex'), skills_dir=str(root / '.agents/skills'),
                ghost_root=str(root / '.ghost-alice'))))
            stack.enter_context(mock.patch.object(gateway_participant, 'removal',
                side_effect=lambda *args, **kwargs: dict(kind='context-gateway', action='missing')))
            config = root / '.codex/config.toml'
            config.parent.mkdir()
            config.write_text(current_config if current_config is not None else
                              '[features]\nhooks = true\n[projects."/project"]\ntrust_level = "trusted"\n')
            if kind == 'feature':
                change = dict(kind='codex_hooks_feature_flag', path=str(config),
                              before_state=before_state or 'false', after_state='true')
                record_name, owner_kind = 'codex-hook-feature-change.json', 'codex-hook-feature-config'
            elif kind == 'trust':
                change = dict(kind='codex_project_trust', path=str(config), project_path='/project',
                              before_state=before_state or 'untrusted', after_state='trusted')
                record_name, owner_kind = 'codex-project-trust-change.json', 'codex-project-trust-config'
            else:
                change = dict(kind='source_repo_hook_path', repo_root=str(root / 'repo'),
                              before='prior/hooks', before_present=True, after='managed/hooks')
                record_name, owner_kind = 'source-repo-hook-path.json', 'source-repo-hook-config'
                query_rc = 128 if failure == 'query' else 1 if failure == 'absent' else 0
                replies = [subprocess.CompletedProcess([], query_rc, 'managed/hooks\n', 'query denied' if query_rc == 128 else '')]
                if query_rc == 0:
                    replies.append(subprocess.CompletedProcess([], 128, '', 'restore denied'))
                stack.enter_context(mock.patch.object(cleanup.subprocess, 'run', side_effect=replies))
            manifest = _write_manifest(root, [], system_env_changes=[change])
            record = manifest.with_name(record_name)
            record.write_text(json.dumps(change))
            original_manifest, original_record = manifest.read_bytes(), record.read_bytes()
            if failure == 'unicode':
                config.write_bytes(b'\xff')
            elif failure == 'edit':
                config.write_text('[features\nhooks = true\n')
            elif failure == 'foreign':
                (config.parent / 'hooks.json').write_text(json.dumps(dict(hooks={
                    'Stop': [dict(hooks=[dict(type='command', command='echo external')])]})))
            original_config = config.read_bytes()
            original_reader = Path.read_text
            if failure == 'read':
                def read(path, *args, **kwargs):
                    if path == config:
                        raise PermissionError('config read denied')
                    return original_reader(path, *args, **kwargs)
                stack.enter_context(mock.patch.object(Path, 'read_text', read))
            elif failure == 'write':
                stack.enter_context(mock.patch.object(cleanup, '_write_text_file',
                                                       side_effect=PermissionError('config write denied')))
            output = root / 'report.json'
            with contextlib.redirect_stdout(io.StringIO()):
                code = cleanup.main(['--platform', 'codex', '--install-state-manifest', str(manifest),
                                     '--report-path', str(output), '--apply'])
            result = json.loads(output.read_text())
            owner = next(item for item in result['items'] if item.get('kind') == owner_kind)
            self.assertEqual(config.read_bytes(), original_config)
            retained = failure not in {'foreign', 'absent'} and current_config is None
            if retained:
                self.assertEqual(record.read_bytes(), original_record)
                self.assertEqual(manifest.read_bytes(), original_manifest)
            else:
                self.assertFalse(record.exists())
                self.assertFalse(manifest.exists())
            return code, result, owner

    def test_later_config_values_survive_full_removal(self):
        for kind, values in (('feature', ('hooks = false', 'hooks = "external"', '')),
                             ('trust', ('trust_level = "untrusted"', 'trust_level = "external"', ''))):
            header = '[features]' if kind == 'feature' else '[projects."/project"]'
            for value in values:
                with self.subTest(kind=kind, value=value):
                    code, result, owner = self.exercise(kind, before_state='missing',
                        current_config=f'{header}\n{value}\n# external comment\n')
                    self.assertEqual(code, 0)
                    self.assertEqual(owner['action'], 'preserved-existing' if value else 'unchanged')
                    self.assertFalse(result['errors'])

    def test_config_restore_failures_preserve_recovery_records_and_fail(self):
        for kind in ('feature', 'trust'):
            for failure in ('read', 'unicode', 'edit', 'write'):
                with self.subTest(kind=kind, failure=failure):
                    code, result, owner = self.exercise(kind, failure=failure)
                    self.assertEqual(code, 1)
                    self.assertEqual(owner['action'], 'manual-review')
                    self.assertTrue(owner['errors'])
                    self.assertEqual(result['errors'], owner['errors'])

    def test_git_query_and_restore_failures_preserve_recovery_records_and_fail(self):
        for failure in ('query', 'restore'):
            with self.subTest(failure=failure):
                code, result, owner = self.exercise('git', failure=failure)
                self.assertEqual(code, 1)
                self.assertTrue(owner['errors'])
                self.assertEqual(result['errors'], owner['errors'])

    def test_absent_git_key_completes_without_an_error(self):
        code, result, owner = self.exercise('git', failure='absent')
        self.assertEqual(code, 0)
        self.assertEqual(owner['action'], 'unchanged')
        self.assertFalse(result['errors'])

    def test_nonrestorable_prior_trust_keeps_evidence_without_an_io_error(self):
        code, result, owner = self.exercise('trust', before_state='other')
        self.assertEqual(code, 0)
        self.assertEqual(owner['action'], 'manual-review')
        self.assertFalse(result['errors'])

    def test_foreign_hooks_keep_the_feature_without_an_io_error(self):
        code, result, owner = self.exercise('feature', failure='foreign')
        self.assertEqual(code, 0)
        self.assertEqual(owner['action'], 'preserved-existing')
        self.assertFalse(result['errors'])


class SupportArtifactLifecycleTest(unittest.TestCase):
    def test_obsolete_copy_cleanup_removes_confirmed_copy_and_keeps_unmatched_contents(self):
        import content_digest
        from installer_assets import write_ownership_marker
        target = _write_managed_skill(self.root, 'task-router')
        source = self.root/'repo/task-router'
        source.mkdir(parents=True)
        (source/'SKILL.md').write_text('new')
        write_ownership_marker(target, platform='codex', asset_id='task-router',
            source_repo=str(self.root/'repo'), source_commit='old', install_mode='copy')
        old = self.rollbacks/'rollback-task-router-20261001T000000Z-1-abcdef12'
        shutil.copytree(target, old)
        changed = self.rollbacks/'rollback-task-router-20261001T000001Z-1-abcdef13'
        shutil.copytree(target, changed)
        (changed/'SKILL.md').write_text('user change')
        hidden = self.rollbacks/'rollback-task-router-20261001T000002Z-1-abcdef14'
        shutil.copytree(target, hidden)
        (hidden/'attachment').symlink_to('../private-original')
        (target/'SKILL.md').write_text('new')
        self.record.write_text(json.dumps(dict(schema_version=1, targets={str(target): dict(
            name='task-router', kind='skill', provider='core', mode='copy', baseline='recorded',
            used_by=['codex'], files=content_digest.tree(target))})))
        completed = [dict(target_name='task-router', dest_path=str(target), source_path=str(source),
                          source_root=str(self.root/'repo'), provider='core', action='replacement-verified')]
        results = cleanup.cleanup_obsolete_copies(root=self.ghost, platform='codex', completed=completed)
        self.assertFalse(old.exists())
        self.assertEqual((changed/'SKILL.md').read_text(), 'user change')
        self.assertEqual(os.readlink(hidden/'attachment'), '../private-original')
        self.assertEqual((self.rollbacks/'previous-copy.txt').read_text(), 'original')
        self.assertEqual(next(r['action'] for r in results if r['path'] == str(old)), 'removed')
        self.assertTrue((self.publish_rollbacks/'rollback-task-router/SKILL.md').is_file())

    def _original_with_live_reference(self, record_kind, mode, host):
        import content_digest
        import hash_utils
        from installer_assets import write_ownership_marker
        base = self.root / f'{record_kind}-{mode}-{host}'
        ghost = base / 'ghost'
        source = base / 'repo/task-router'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text('new version\n')
        original = ghost / 'install-rollbacks/rollback-task-router-20261001T000000Z-1-abcdef12'
        (original / 'nested').mkdir(parents=True)
        (original / 'SKILL.md').write_text('old version\n')
        (original / 'nested/SKILL.md').write_text('live dependency\n')
        write_ownership_marker(original, platform='codex', asset_id='task-router',
            source_repo=str(source.parent), source_commit='old', install_mode='copy')
        unused = original.with_name('rollback-task-router-20261001T000001Z-1-abcdef13')
        shutil.copytree(original, unused)
        target = base / 'skills/task-router'
        shutil.copytree(source, target)
        live = base / host / 'skills/dependent'
        live.parent.mkdir(parents=True)
        referenced = original / 'nested'
        if mode == 'link':
            live.symlink_to(os.path.relpath(referenced, live.parent), target_is_directory=True)
        else:
            shutil.copytree(referenced, live)
            write_ownership_marker(live, platform=host, asset_id='dependent',
                source_repo=str(original), source_commit='old', install_mode='copy')
        entries = {str(target): dict(name='task-router', kind='skill', provider='core', mode='copy',
            baseline='recorded', used_by=['codex'], files=content_digest.tree(target))}
        if record_kind == 'payload':
            entries[str(live)] = dict(name='dependent', kind='skill', provider='core', mode=mode,
                baseline='recorded', used_by=[host], source=str(referenced),
                **({'link_target': os.readlink(live)} if mode == 'link' else {'files': content_digest.tree(live)}))
        elif record_kind == 'inventory':
            inventory = ghost / 'install-state' / f'{host}.json'
            inventory.parent.mkdir(parents=True)
            inventory.write_text(json.dumps(dict(platform=host, source_root=str(base), targets=[dict(
                target_name='dependent', dest_path=str(live), source_path=str(referenced if mode == 'copy' else source),
                install_mode='copy' if mode == 'copy' else 'symlink')])))
        else:
            sidecar = ghost / 'addons' / host / 'dependent.json'
            sidecar.parent.mkdir(parents=True)
            install_mode = 'copy' if mode == 'copy' else 'symlink'
            sidecar.write_text(json.dumps(dict(schema_version='1.0', addon_id='dependent', addon_version='1',
                source=str(referenced if mode == 'copy' else source), platform=host, owner='addon', origin='local',
                depends_on_core=[], min_core_version='1', installed_at='2026-11-14T00:00:00Z', provided=[dict(
                    kind='skill', name='dependent', target=str(live), ownership='ghost-alice-managed',
                    install_mode=install_mode, content_hash=hash_utils.hash_target(live, install_mode),
                    marker='', metadata={})])))
        (ghost / 'installed-payload.json').write_text(json.dumps(dict(schema_version=1, targets=entries)))
        completed = [dict(target_name='task-router', dest_path=str(target), source_path=str(source),
                          source_root=str(source.parent), provider='core', action='replacement-verified')]
        return ghost, original, unused, live, completed

    def test_obsolete_copy_keeps_live_target_sources_without_changing_exact_target_references(self):
        for record_kind, mode, host in [('payload', 'link', 'codex'), ('payload', 'link', 'claude'),
                ('payload', 'copy', 'claude'), ('inventory', 'link', 'claude'), ('inventory', 'copy', 'claude'),
                ('addon', 'link', 'claude'), ('addon', 'copy', 'claude')]:
            with self.subTest(record=record_kind, mode=mode, host=host):
                ghost, original, unused, live, completed = self._original_with_live_reference(record_kind, mode, host)
                before = (live / 'SKILL.md').read_bytes()
                link = os.readlink(live) if mode == 'link' else None
                self.assertEqual(cleanup._platforms_referencing_target(original,
                    current_manifest=ghost / 'install-state/codex.json', current_platform='codex', include_current=True), [])
                rows = cleanup.cleanup_obsolete_copies(root=ghost, platform='codex', completed=completed)
                self.assertTrue(original.exists())
                self.assertEqual((live / 'SKILL.md').read_bytes(), before)
                if link is not None:
                    self.assertEqual(os.readlink(live), link)
                self.assertFalse(unused.exists())
                self.assertEqual(next(row['action'] for row in rows if row['path'] == str(original)), 'preserved')

    def test_obsolete_copy_ignores_absent_destinations_with_stale_source_records(self):
        for record_kind in ('payload', 'inventory', 'addon'):
            with self.subTest(record=record_kind):
                ghost, original, unused, live, completed = self._original_with_live_reference(record_kind, 'link', 'claude')
                live.unlink()
                cleanup.cleanup_obsolete_copies(root=ghost, platform='codex', completed=completed)
                self.assertFalse(original.exists())
                self.assertFalse(unused.exists())

    def test_completed_retirement_deletes_clean_backup_but_keeps_review_original(self):
        import retired_assets
        from installer_assets import write_ownership_marker
        results = []
        for name, modified in [('harness-security-scan', False), ('jailbreak-detector', True)]:
            target = _write_managed_skill(self.root, name)
            write_ownership_marker(target, platform='codex', asset_id=name,
                source_repo=str(self.root/'repo'), source_commit='old', install_mode='copy')
            if modified:
                (target/'SKILL.md').write_text('user change')
            result = retired_assets.retire_skill(target, platform='codex', source_root=self.root/'repo', pending_root=self.ghost)
            self.assertEqual(result['action'], 'retired-managed')
            results.append(result)
        # The record owner is real: no retirement API is replaced by a call counter.
        cleanup.cleanup_obsolete_copies(root=self.ghost, platform='codex', completed=[])
        self.assertFalse(Path(results[0]['backup_path']).exists())
        self.assertFalse(Path(results[0]['work_path']).exists())
        self.assertEqual((Path(results[1]['backup_path'])/'SKILL.md').read_text(), 'user change')
        self.assertTrue(Path(results[1]['work_path']).exists())

    def test_completed_retirement_keeps_live_dependencies_and_receipt_until_retry(self):
        import retired_assets
        from installer_assets import write_ownership_marker
        for dependency in ('codex', 'claude', 'pending', 'service', 'unreadable-inventory'):
            with self.subTest(dependency=dependency):
                base = self.root / dependency
                ghost = base / 'ghost'
                target = _write_managed_skill(base, 'jailbreak-detector')
                write_ownership_marker(target, platform='codex', asset_id=target.name,
                    source_repo=str(base / 'repo'), source_commit='old', install_mode='copy')
                retired = retired_assets.retire_skill(target, platform='codex', source_root=base / 'repo',
                                                      pending_root=ghost)
                backup, receipt = Path(retired['backup_path']), Path(retired['work_path'])
                before, work = (backup / 'SKILL.md').read_bytes(), receipt.read_bytes()
                live = None
                if dependency in {'codex', 'claude'}:
                    live = base / dependency / 'skills' / target.name
                    live.parent.mkdir(parents=True)
                    live.symlink_to(backup, target_is_directory=True)
                    inventory = ghost / 'install-state' / f'{dependency}.json'
                    inventory.parent.mkdir(parents=True, exist_ok=True)
                    inventory.write_text(json.dumps(dict(platform=dependency, source_root=str(backup.parent), targets=[dict(
                        target_name=target.name, source_path=str(backup), dest_path=str(live), install_mode='symlink')])) )
                    reason = 'live-host-uses-original'
                elif dependency == 'pending':
                    pending = ghost / 'pending-merges/codex/manifest.json'
                    pending.parent.mkdir(parents=True)
                    pending.write_text(json.dumps(dict(entries=[dict(source_path=str(backup), decided=False)])))
                    reason = 'pending-original-review'
                elif dependency == 'service':
                    digest = 'sha256:' + '0' * 64
                    service = dict(label='fixture', runtime_root=str(backup), program=str(backup / 'SKILL.md'),
                        launch_agent=str(base / 'agent.plist'), launch_agent_digest=digest, state_path=str(base / 'state.json'),
                        state_digest=digest, config_path=str(base / 'config.toml'), config_block_digest=None,
                        config_backup=None, registered_at='2026-11-14T00:00:00Z', restarted_at=None, listener_ready=True)
                    (ghost / 'installed-payload.json').write_text(json.dumps(dict(schema_version=1, targets={}, service=service)))
                    reason = 'registered-service-uses-original'
                else:
                    inventory = ghost / 'install-state/claude.json'
                    inventory.parent.mkdir(parents=True, exist_ok=True)
                    inventory.write_text('{broken')
                    reason = 'original-cleanup-failed'
                rows = cleanup.cleanup_obsolete_copies(root=ghost, platform='codex', completed=[])
                row = next(row for row in rows if row['path'] == str(backup))
                self.assertEqual(row['reason'], reason)
                self.assertEqual((backup / 'SKILL.md').read_bytes(), before)
                self.assertEqual(receipt.read_bytes(), work)
                self.assertFalse(target.exists())
                if live is not None:
                    self.assertEqual((live / 'SKILL.md').read_bytes(), before)
                    self.assertEqual(os.readlink(live), str(backup))
                    live.unlink()
                elif dependency == 'pending':
                    pending.write_text(json.dumps(dict(entries=[])))
                elif dependency == 'service':
                    (ghost / 'installed-payload.json').write_text(json.dumps(dict(schema_version=1, targets={})))
                else:
                    inventory.unlink()
                rows = cleanup.cleanup_obsolete_copies(root=ghost, platform='codex', completed=[])
                self.assertEqual(next(row['action'] for row in rows if row['path'] == str(backup)), 'removed')
                self.assertFalse(backup.exists())
                self.assertFalse(receipt.exists())
                self.assertFalse(target.exists())

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.enterContext(mock.patch.dict(os.environ, installer_env(self.root), clear=True))
        self.ghost = self.root / ".ghost-alice"
        self.enterContext(install_destination.use(destination_fixture(
            self.root, platform_dir=str(self.root / '.codex'), skills_dir=str(self.root / '.agents/skills'),
            ghost_root=str(self.ghost))))
        home = mock.patch.object(cleanup.locations, "home", return_value=self.root)
        home.start()
        self.addCleanup(home.stop)
        self.manifest = self.ghost / "install-state" / "codex.json"
        self._create_support()

    def _create_support(self, value="original"):
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text('{"platform":"codex","targets":[]}', encoding="utf-8")
        self.pending = self.ghost / "pending-merges" / "codex"
        self.pending.mkdir(parents=True, exist_ok=True)
        (self.pending / "user-original.txt").write_text(value, encoding="utf-8")
        self.rollbacks = self.ghost / "install-rollbacks"
        self.rollbacks.mkdir(parents=True, exist_ok=True)
        (self.rollbacks / "previous-copy.txt").write_text(value, encoding="utf-8")
        self.dispatcher = self.ghost / "hooks"
        self.dispatcher.mkdir(parents=True, exist_ok=True)
        (self.dispatcher / "dispatcher.mjs").write_text("shared", encoding="utf-8")
        self.record = self.ghost / "installed-payload.json"
        self.record.write_text('{"schema_version": 1, "targets": {}}', encoding="utf-8")
        self.publish_rollbacks = self.ghost / "rollbacks" / "20261020T000000Z-1-aaaaaaaa"
        self.publish_rollbacks.mkdir(parents=True, exist_ok=True)
        (self.publish_rollbacks / "journal.json").write_text('{"phase": "done"}', encoding="utf-8")
        (self.publish_rollbacks / "rollback-task-router").mkdir(exist_ok=True)
        (self.publish_rollbacks / "rollback-task-router" / "SKILL.md").write_text(value, encoding="utf-8")

    def _support(self, apply_changes=True):
        report_path = self.root / 'support-report.json'
        args = [
            "--platform", "codex", "--install-state-manifest", str(self.manifest),
            '--report-path', str(report_path),
            *(["--apply"] if apply_changes else []),
        ]
        with mock.patch.object(gateway_participant, 'removal',
                               side_effect=lambda *args, **kwargs: dict(kind='context-gateway', action='missing')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.cleanup_code = cleanup.main(args)
        self.cleanup_report = json.loads(report_path.read_text())
        return {item['target_name']: item for item in self.cleanup_report['items'] if item.get('kind') == 'support-artifact'}

    def test_other_platform_retains_shared_dispatcher_and_rollbacks(self):
        other = self.manifest.with_name("claude.json")
        other.write_text('{"platform":"claude","targets":[]}', encoding="utf-8")
        original_other = other.read_bytes()
        report = self._support()
        self.assertEqual(self.cleanup_code, 0)
        self.assertFalse(self.cleanup_report['removal_incomplete'])
        self.assertEqual((self.dispatcher / "dispatcher.mjs").read_text(), "shared")
        self.assertEqual((self.rollbacks / "previous-copy.txt").read_text(), "original")
        self.assertEqual(other.read_bytes(), original_other)
        for name in ["hook-dispatcher-assets", "install-rollbacks", "install-record", "publish-rollbacks"]:
            self.assertEqual(report[name]["action"], "manual-review")
            self.assertEqual(report[name]["shared_platforms"], ["claude"])
        # The install record names both hosts, so the last host's removal is the one that removes it.
        self.assertTrue(self.record.exists())
        self.assertTrue((self.publish_rollbacks / "rollback-task-router" / "SKILL.md").exists())

    def test_unreadable_other_host_inventory_keeps_retry_records_until_repaired(self):
        other = self.manifest.with_name('claude.json')
        other.write_text('{broken')
        manifest, record = self.manifest.read_bytes(), self.record.read_bytes()
        pending = (self.pending / 'user-original.txt').read_bytes()
        self._support()
        self.assertEqual(self.cleanup_code, 1)
        self.assertTrue(self.cleanup_report['removal_incomplete'])
        self.assertTrue(self.cleanup_report['hooks_detached'])
        self.assertEqual(self.manifest.read_bytes(), manifest)
        self.assertEqual(self.record.read_bytes(), record)
        self.assertEqual((self.pending / 'user-original.txt').read_bytes(), pending)
        self.assertTrue(self.dispatcher.exists())
        other.write_text(json.dumps(dict(platform='claude', targets=[])))
        self._support()
        self.assertEqual(self.cleanup_code, 0)
        self.assertFalse(self.cleanup_report['removal_incomplete'])
        self.assertFalse(self.manifest.exists())
        self.assertTrue(self.record.exists())
        self.assertTrue(self.dispatcher.exists())

    def test_invalid_current_target_row_keeps_retry_records_until_repaired(self):
        self.manifest.write_text(json.dumps(dict(platform='codex', targets=['invalid row'])))
        manifest, record = self.manifest.read_bytes(), self.record.read_bytes()
        pending = (self.pending / 'user-original.txt').read_bytes()
        self._support()
        self.assertEqual(self.cleanup_code, 1)
        self.assertTrue(self.cleanup_report['removal_incomplete'])
        self.assertTrue(self.cleanup_report['hooks_detached'])
        self.assertEqual(self.manifest.read_bytes(), manifest)
        self.assertEqual(self.record.read_bytes(), record)
        self.assertEqual((self.pending / 'user-original.txt').read_bytes(), pending)
        self.manifest.write_text(json.dumps(dict(platform='codex', targets=[])))
        self._support()
        self.assertEqual(self.cleanup_code, 0)
        self.assertFalse(self.cleanup_report['removal_incomplete'])
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.record.exists())

    def test_unavailable_current_inventory_keeps_retry_records_until_repaired(self):
        for invalid in (None, '{broken', '{"targets": "invalid"}'):
            with self.subTest(invalid=invalid):
                self._create_support()
                if invalid is None:
                    self.manifest.unlink()
                else:
                    self.manifest.write_text(invalid)
                record = self.record.read_bytes()
                pending = (self.pending / 'user-original.txt').read_bytes()
                self._support()
                self.assertEqual(self.cleanup_code, 1)
                self.assertTrue(self.cleanup_report['removal_incomplete'])
                if invalid is not None:
                    self.assertEqual(self.manifest.read_text(), invalid)
                self.assertEqual(self.record.read_bytes(), record)
                self.assertEqual((self.pending / 'user-original.txt').read_bytes(), pending)
                self.manifest.write_text(json.dumps(dict(platform='codex', targets=[])))
                self._support()
                self.assertEqual(self.cleanup_code, 0)
                self.assertFalse(self.cleanup_report['removal_incomplete'])
                self.assertFalse(self.manifest.exists())
                self.assertFalse(self.record.exists())

    def test_payload_host_reference_preserves_shared_support_without_legacy_inventory(self):
        import content_digest
        target = _write_managed_skill(self.root, 'task-router')
        self.record.write_text(json.dumps(dict(schema_version=1, targets={str(target): dict(
            name='task-router', kind='skill', provider='core', mode='copy', baseline='recorded',
            used_by=['claude'], files=content_digest.tree(target))})))
        before = self.record.read_bytes()
        result = self._support()
        self.assertEqual(self.record.read_bytes(), before)
        self.assertTrue(self.dispatcher.exists())
        self.assertEqual(result['install-record']['shared_platforms'], ['claude'])

    def test_core_ownership_change_after_detachment_keeps_retry_records_and_fails(self):
        target = _write_managed_skill(self.root, 'task-router')
        self.manifest.write_text(json.dumps(dict(platform='codex', targets=[dict(
            target_name='task-router', dest_path=str(target), install_mode='copy')])) )
        marker = target/'.ghost-alice-install.json'
        original_marker = marker.read_bytes()
        uninstall_hook = cleanup.uninstall_hook
        def detach(*args, **kwargs):
            result = uninstall_hook(*args, **kwargs)
            if not kwargs.get('dry_run', False):
                marker.unlink()
            return result
        output = self.root/'report.json'
        with mock.patch.object(cleanup, 'uninstall_hook', side_effect=detach), \
                mock.patch.object(gateway_participant, 'removal',
                    side_effect=lambda *a, **kw: dict(kind='context-gateway', action='missing')), \
                contextlib.redirect_stdout(io.StringIO()):
            code = cleanup.main(['--platform', 'codex', '--install-state-manifest', str(self.manifest),
                                 '--report-path', str(output), '--apply'])
        self.assertEqual(code, 1)
        result = json.loads(output.read_text())
        self.assertTrue(result['hooks_detached'])
        self.assertTrue(target.exists())
        for path in (self.pending, self.record, self.manifest):
            self.assertTrue(path.exists(), str(path))
        marker.write_bytes(original_marker)
        self._support()
        self.assertFalse(target.exists())
        self.assertFalse(self.manifest.exists())

    def test_last_platform_removes_the_install_record_and_archives_publish_rollbacks(self):
        report = self._support()
        self.assertFalse(self.record.exists())
        self.assertFalse(self.publish_rollbacks.parent.exists())
        self.assertEqual(report["install-record"]["action"], "removed")
        self.assertEqual(report["publish-rollbacks"]["action"], "quarantined-removed")
        copy = Path(report["publish-rollbacks"]["backup_path"]) / self.publish_rollbacks.name / "rollback-task-router" / "SKILL.md"
        self.assertEqual(copy.read_text(encoding="utf-8"), "original")
        self.assertEqual(cleanup._summary(list(report.values()))["removed"], 6)

    def test_last_platform_discards_completed_managed_originals_and_retries_cleanup_failure(self):
        import install_lock
        import install_transaction
        source = self.root / 'repo'
        targets = []
        for name in ('task-router', 'skill-evolution'):
            skill = source / name
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('old managed code\n')
            targets.append(dict(name=name, kind='skill', provider='core', source=str(skill),
                source_commit='abc123', dest=str(self.root / '.agents/skills' / name), mode='copy'))
        request = dict(schema_version=1, host='codex', ghost_root=str(self.ghost),
            installer=dict(commit='abc123', schema_version=1),
            source=dict(root=str(source), commit='abc123', dirty='clean', hooks_path='hooks'), targets=targets)
        with install_lock.command_lock(self.ghost / 'install.lock', owner='installer:install:codex:test'):
            install_transaction.publish(request)
            for target in targets:
                (Path(target['source']) / 'SKILL.md').write_text('new managed code\n')
            txid = install_transaction.publish(request)
        journal = self.ghost / 'rollbacks' / txid
        originals = [Path(target['rollback']) for target in json.loads((journal / 'journal.json').read_text())['targets']]
        changed = originals[1] / 'SKILL.md'
        changed.write_text('external change\n')
        self.manifest.write_text(json.dumps(dict(platform='codex', source_root=str(source), targets=[dict(
            target_name=target['name'], source_path=target['source'], dest_path=target['dest'], install_mode='copy')
            for target in targets])))
        remove = install_transaction._remove_path
        def deny_original(path):
            if path == originals[0]:
                raise PermissionError('completed original locked')
            return remove(path)
        with mock.patch.object(install_transaction, '_remove_path', side_effect=deny_original):
            report = self._support()
        self.assertEqual(self.cleanup_code, 1)
        self.assertIn('completed original locked', '\n'.join(report['publish-rollbacks']['errors']))
        self.assertTrue(self.cleanup_report['hooks_detached'])
        for target in targets:
            self.assertFalse(Path(target['dest']).exists())
        for path in (originals[0], changed, self.manifest, self.record, self.pending):
            self.assertTrue(path.exists(), str(path))
        report = self._support()
        self.assertEqual(self.cleanup_code, 0)
        archive = Path(report['publish-rollbacks']['backup_path']) / txid
        self.assertFalse((archive / originals[0].name).exists())
        self.assertEqual((archive / originals[1].name / 'SKILL.md').read_text(), 'external change\n')
        self.assertFalse(journal.exists())
        self.assertFalse(self.manifest.exists())

    def test_last_platform_archives_recovery_material_before_removal(self):
        original_times = [(self.pending / "user-original.txt").stat().st_mtime_ns,
                          (self.rollbacks / "previous-copy.txt").stat().st_mtime_ns]
        report = self._support()
        self.assertEqual(self.cleanup_code, 0)
        self.assertFalse(self.cleanup_report['removal_incomplete'])
        self.assertFalse(self.pending.exists())
        self.assertFalse(self.rollbacks.exists())
        self.assertFalse(self.dispatcher.exists())
        self.assertEqual(cleanup._summary(list(report.values()))["removed"], 6)
        for index, (name, filename) in enumerate([
            ("pending-merges", "user-original.txt"),
            ("install-rollbacks", "previous-copy.txt"),
        ]):
            self.assertEqual(report[name]["action"], "quarantined-removed")
            copy = Path(report[name]["backup_path"]) / filename
            self.assertEqual(copy.read_text(), "original")
            self.assertEqual(copy.stat().st_mtime_ns, original_times[index])

    def test_successive_removals_do_not_replace_prior_recovery_copies(self):
        first = self._support()
        first_copy = Path(first["pending-merges"]["backup_path"]) / "user-original.txt"
        self._create_support("next original")
        second = self._support()
        second_copy = Path(second["pending-merges"]["backup_path"]) / "user-original.txt"
        self.assertNotEqual(first_copy, second_copy)
        self.assertEqual(first_copy.read_text(), "original")
        self.assertEqual(second_copy.read_text(), "next original")

    def test_dry_run_keeps_recovery_material_and_creates_no_archive(self):
        before = {p: p.read_bytes() for p in self.ghost.rglob("*") if p.is_file()}
        report = self._support(apply_changes=False)
        self.assertEqual({p: p.read_bytes() for p in self.ghost.rglob("*") if p.is_file()}, before)
        self.assertFalse((self.ghost / "uninstall-backup").exists())
        self.assertEqual(report["pending-merges"]["action"], "would-quarantine-remove")
        self.assertEqual(report["install-rollbacks"]["action"], "would-quarantine-remove")
        self.assertEqual(report["publish-rollbacks"]["action"], "would-quarantine-remove")
        self.assertEqual(report["install-record"]["action"], "would-remove")

    def test_failed_recovery_copy_keeps_original_material(self):
        with mock.patch.object(cleanup.shutil, "copytree", side_effect=OSError("copy interrupted")):
            report = self._support()
        self.assertEqual(report['pending-merges']['action'], 'manual-review')
        self.assertEqual(report['pending-merges']['reason'], 'file-removal-unresolved')
        self.assertFalse(report['pending-merges'].get('errors'))
        for name in ('install-rollbacks', 'publish-rollbacks'):
            self.assertIn('copy interrupted', '\n'.join(report[name]['errors']))
        self.assertEqual(self.cleanup_code, 1)
        self.assertTrue(self.cleanup_report['removal_incomplete'])
        self.assertEqual((self.pending / "user-original.txt").read_text(), "original")
        self.assertEqual((self.rollbacks / "previous-copy.txt").read_text(), "original")
        self.assertTrue(self.manifest.exists())
        self.assertTrue(self.record.exists())

    def test_failed_legacy_copy_removal_keeps_its_live_location_until_retry(self):
        from installer_assets import write_ownership_marker
        target = _write_managed_skill(self.root, 'task-router')
        source = self.root / 'repo/task-router'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text('new checkout version\n')
        write_ownership_marker(target, platform='codex', asset_id='task-router',
            source_repo=str(self.root/'repo'), source_commit='old', install_mode='copy')
        original = self.rollbacks / 'rollback-task-router-20261001T000000Z-1-abcdef12'
        shutil.copytree(target, original)
        original_files = {path.relative_to(original): path.read_bytes()
                          for path in original.rglob('*') if path.is_file()}
        self.manifest.write_text(json.dumps(dict(platform='codex', source_root=str(self.root/'repo'), targets=[dict(
            target_name='task-router', source_path=str(source), dest_path=str(target), install_mode='copy')])))
        remove = cleanup._remove_path

        def fail_original(path):
            if path == original:
                raise PermissionError('injected legacy original removal failure')
            return remove(path)

        with mock.patch.object(cleanup, '_remove_path', side_effect=fail_original):
            report = self._support()
        self.assertEqual(self.cleanup_code, 1)
        self.assertTrue(self.cleanup_report['removal_incomplete'])
        self.assertFalse(target.exists())
        failed = next(item for item in self.cleanup_report['items']
                      if item.get('kind') == 'obsolete-copy' and item['path'] == str(original))
        self.assertEqual((failed['action'], failed['reason']), ('manual-review', 'original-cleanup-failed'))
        self.assertIn('injected legacy original removal failure', '\n'.join(failed['errors']))
        self.assertEqual({path.relative_to(original): path.read_bytes()
                          for path in original.rglob('*') if path.is_file()}, original_files)
        self.assertEqual(report['install-rollbacks']['action'], 'manual-review')
        self.assertEqual(report['install-rollbacks']['reason'], 'recovery-removal-pending')
        for path in (self.pending, self.record, self.manifest):
            self.assertTrue(path.exists(), str(path))
        self._support()
        self.assertEqual(self.cleanup_code, 0, self.cleanup_report)
        self.assertFalse(self.cleanup_report['removal_incomplete'])
        self.assertFalse(original.exists())
        completed = next(item for item in self.cleanup_report['items']
                         if item.get('kind') == 'obsolete-copy' and item['path'] == str(original))
        self.assertEqual(completed['action'], 'removed')
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.record.exists())
        self.assertFalse(any(path.read_bytes() == original_files[Path('SKILL.md')]
                             for path in self.ghost.rglob('SKILL.md')))

    def test_recovery_archive_preserves_links_without_copying_external_targets(self):
        outside = self.root / "user-owned.txt"
        outside.write_text("external original", encoding="utf-8")
        link = self.pending / "source-link"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink capability unavailable: {exc}")
        report = self._support()
        archived_link = Path(report["pending-merges"]["backup_path"]) / link.name
        self.assertTrue(archived_link.is_symlink())
        self.assertEqual(os.readlink(archived_link), str(outside))
        self.assertEqual(outside.read_text(), "external original")

    def test_recovery_root_symlink_preserves_original_link_mtime(self):
        outside = self.root / "original-recovery"
        outside.mkdir()
        (outside / "original.txt").write_text("outside original", encoding="utf-8")
        shutil.rmtree(self.pending)
        try:
            self.pending.symlink_to(outside, target_is_directory=True)
            os.utime(self.pending, ns=(1234567800000000000, 1234567890000000000), follow_symlinks=False)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink timestamp capability unavailable: {exc}")
        original_time = self.pending.lstat().st_mtime_ns
        report = self._support()
        archived = Path(report["pending-merges"]["backup_path"])
        self.assertTrue(archived.is_symlink())
        self.assertEqual(os.readlink(archived), str(outside))
        self.assertEqual(archived.lstat().st_mtime_ns, original_time)
        self.assertEqual((outside / "original.txt").read_text(), "outside original")

    def test_recovery_junction_roots_and_children_remain_for_review(self):
        # Emulate only the Windows junction detector; all archive/removal paths
        # are real temporary files. This is not native Windows evidence.
        for nested in [False, True]:
            with self.subTest(nested=nested):
                target = self.pending / "junction-child" if nested else self.pending
                if nested:
                    target.mkdir()
                with mock.patch.object(cleanup, "_is_junction", side_effect=lambda p: p == target):
                    report = self._support()
                self.assertEqual(report["pending-merges"]["action"], "manual-review")
                self.assertEqual(report["pending-merges"]["reason"], "recovery-junction-needs-review")
                self.assertEqual(report["pending-merges"]["junction_path"], str(target))
                self.assertEqual((self.pending / "user-original.txt").read_text(), "original")
                self.assertTrue(self.manifest.exists())
                self.assertTrue(self.record.exists())
                self.assertEqual(report["install-state-manifest"]["action"], "manual-review")
                self.assertEqual(self.cleanup_code, 1)
                self.assertTrue(self.cleanup_report['removal_incomplete'])
        self._support()
        self.assertEqual(self.cleanup_code, 0, self.cleanup_report)
        self.assertFalse(self.pending.exists())
        self.assertFalse(self.record.exists())
        self.assertFalse(self.manifest.exists())

    def test_unsupported_link_timestamp_copy_keeps_original_and_manifest(self):
        outside = self.root / "original-recovery"
        outside.mkdir()
        shutil.rmtree(self.pending)
        try:
            self.pending.symlink_to(outside, target_is_directory=True)
            os.utime(self.pending, ns=(1234567800000000000, 1234567890000000000), follow_symlinks=False)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink timestamp fixture unavailable: {exc}")
        original_time = self.pending.lstat().st_mtime_ns
        copystat = cleanup.shutil.copystat

        def unsupported_no_follow(src, dest, *, follow_symlinks=True):
            if follow_symlinks:
                return copystat(src, dest, follow_symlinks=follow_symlinks)

        with mock.patch.object(cleanup.shutil, "copystat", side_effect=unsupported_no_follow):
            report = self._support()
        self.assertEqual(report["pending-merges"]["action"], "manual-review")
        self.assertEqual(report["pending-merges"]["reason"], "recovery-link-time-not-preserved")
        self.assertTrue(self.pending.is_symlink())
        self.assertEqual(self.pending.lstat().st_mtime_ns, original_time)
        self.assertTrue(self.manifest.exists())
        self.assertTrue(self.record.exists())
        self.assertEqual(report["install-state-manifest"]["action"], "manual-review")
        self.assertEqual(self.cleanup_code, 1)
        self.assertTrue(self.cleanup_report['removal_incomplete'])
        self._support()
        self.assertEqual(self.cleanup_code, 0, self.cleanup_report)
        self.assertFalse(self.pending.is_symlink())
        self.assertFalse(self.record.exists())
        self.assertFalse(self.manifest.exists())

    def test_unsupported_nested_link_timestamp_copy_keeps_original_tree(self):
        outside = self.root / "user-original.txt"
        outside.write_text("outside original", encoding="utf-8")
        link = self.pending / "nested-link"
        try:
            link.symlink_to(outside)
            os.utime(link, ns=(1234567800000000000, 1234567890000000000), follow_symlinks=False)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink timestamp fixture unavailable: {exc}")
        original_time = link.lstat().st_mtime_ns
        copystat = cleanup.shutil.copystat

        def unsupported_no_follow(src, dest, *, follow_symlinks=True):
            if follow_symlinks:
                return copystat(src, dest, follow_symlinks=follow_symlinks)

        with mock.patch.object(cleanup.shutil, "copystat", side_effect=unsupported_no_follow):
            report = self._support()
        self.assertEqual(report["pending-merges"]["action"], "manual-review")
        self.assertEqual(report["pending-merges"]["reason"], "recovery-link-time-not-preserved")
        self.assertEqual(link.lstat().st_mtime_ns, original_time)
        self.assertEqual((self.pending / "user-original.txt").read_text(), "original")
        self.assertEqual(outside.read_text(), "outside original")
        self.assertTrue(self.manifest.exists())
        self.assertTrue(self.record.exists())
        self.assertEqual(self.cleanup_code, 1)
        self.assertTrue(self.cleanup_report['removal_incomplete'])
        self._support()
        self.assertEqual(self.cleanup_code, 0, self.cleanup_report)
        self.assertFalse(self.pending.exists())
        self.assertFalse(self.record.exists())
        self.assertFalse(self.manifest.exists())


class UninstallCleanupTest(unittest.TestCase):
    def test_apply_flag_and_legacy_alias_keep_the_preview_default(self):
        parser = cleanup.build_parser()
        base = ["--platform", "codex", "--install-state-manifest", "/isolated/manifest.json"]
        self.assertFalse(parser.parse_args(base).apply_changes)
        for flags in (["--apply"], ["--confirm"], ["--apply", "--confirm"]):
            with self.subTest(flags=flags):
                self.assertTrue(parser.parse_args([*base, *flags]).apply_changes)

    def test_global_rule_preview_uses_the_same_owned_blocks_as_removal(self):
        from global_rule_blocks import CLAUDE_SPEC, CODEX_SPEC

        for platform, spec, filename in (("claude", CLAUDE_SPEC, "CLAUDE.md"),
                                         ("codex", CODEX_SPEC, "AGENTS.md")):
            managed = f"{spec.marker}\n{spec.begin}\nmanaged\n{spec.end}\n"
            lookalike = "# Ghost-ALICE notes\nuser rules\n"
            incomplete = f"user rules\n{spec.begin}\nunclosed user example\n"
            for body, removed, remaining in ((lookalike, False, lookalike),
                                              (incomplete, False, incomplete),
                                              (managed, True, None),
                                              (managed + "user rules\n", True, "user rules\n")):
                with self.subTest(platform=platform, body=body):
                    with tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        path = root / filename
                        path.write_text(body, encoding="utf-8")
                        before = (path.read_bytes(), path.stat().st_mtime_ns)
                        with mock.patch.object(cleanup, "_" + platform + "_home", return_value=root):
                            preview = cleanup._global_rule_item(platform, apply_changes=False)
                            self.assertEqual(preview["action"], "would-remove-global-rule" if removed else "unchanged")
                            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
                            applied = cleanup._global_rule_item(platform, apply_changes=True)
                        self.assertEqual(applied["action"], "removed-global-rule" if removed else "unchanged")
                        if remaining is None:
                            self.assertFalse(path.exists())
                        else:
                            self.assertEqual(path.read_text(encoding="utf-8"), remaining)

    def test_claude_global_rule_cleanup_has_one_reported_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            claude_home = root / "custom-claude"
            rule_file = claude_home / "CLAUDE.md"
            claude_home.mkdir(parents=True)
            rule_file.write_text("# Ghost-ALICE Claude Bootstrap\n<!-- Ghost-ALICE managed block begin: claude-bootstrap -->\nmanaged\n<!-- Ghost-ALICE managed block end: claude-bootstrap -->\nuser rule\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"HOME": str(root), "CLAUDE_CONFIG_DIR": str(claude_home)}):
                dry_run = cleanup._global_rule_item("claude", apply_changes=False)
                self.assertIsNotNone(dry_run)
                self.assertEqual(dry_run["action"], "would-remove-global-rule")
                confirmed = cleanup._global_rule_item("claude", apply_changes=True)
            self.assertEqual(confirmed["action"], "removed-global-rule")
            self.assertEqual(rule_file.read_text(encoding="utf-8").strip(), "user rule")

        self.assertNotIn("remove_claude_bootstrap || return 1", installer_bash_source())

    def run_cleanup(
        self,
        root: Path,
        manifest: Path,
        report: Path,
        *,
        apply_changes: bool = False,
        ) -> subprocess.CompletedProcess[str]:
        env = installer_env(str(root))
        env["USERPROFILE"] = str(root)
        args = [
            sys.executable,
            str(UNINSTALL_CLEANUP),
            "--platform",
            "codex",
            "--install-state-manifest",
            str(manifest),
            "--report-path",
            str(report),
        ]
        if apply_changes:
            args.append("--apply")
        return subprocess.run(
            args,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_default_report_and_scope_use_env_home_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )

            env = installer_env(str(root))
            env["USERPROFILE"] = str(root)
            env["CODEX_HOME"] = str(root / ".codex")
            result = subprocess.run(
                [
                    sys.executable,
                    str(UNINSTALL_CLEANUP),
                    "--platform",
                    "codex",
                    "--install-state-manifest",
                    str(manifest),
                    "--apply",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(managed.exists(), msg=result.stdout)
            temp_report_root = root / ".ghost-alice" / "uninstall-reports"
            self.assertIn(str(temp_report_root), result.stdout)
            self.assertNotIn(str(Path.home() / ".ghost-alice" / "uninstall-reports"), result.stdout)
            reports = glob(str(temp_report_root / "codex-apply-*.json"))
            self.assertEqual(len(reports), 1, msg=result.stdout)

    def test_the_ghost_alice_root_comes_from_the_installer_locations(self) -> None:
        with mock.patch.object(cleanup.locations, "record_root", return_value=Path("/configured/.ghost-alice")):
            self.assertEqual(cleanup._ghost_alice_root(), Path("/configured/.ghost-alice"))

    def test_a_new_default_report_keeps_the_last_thirty_reports_before_it(self) -> None:
        # The reports folder keeps 30 reports; the backup copies stay for the user.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports = root / ".ghost-alice" / "uninstall-reports"
            reports.mkdir(parents=True)
            old = []
            for index in range(32):
                path = reports / f"codex-dry-run-202610{index // 24 + 1:02d}T{index % 24:02d}0000Z.json"
                path.write_text("{}\n", encoding="utf-8")
                os.utime(path, (1_000_000 + index, 1_000_000 + index))
                old.append(path)
            kept_copy = root / ".ghost-alice" / "uninstall-backup" / "codex" / "support-state" / "x-1" / "original"
            kept_copy.mkdir(parents=True)
            manifest = _write_manifest(root, [])
            env = installer_env(root)
            env.update(HOME=str(root), USERPROFILE=str(root), CODEX_HOME=str(root / ".codex"),
                       GHOST_ALICE_GHOST_ROOT=str(root / ".ghost-alice"))
            result = subprocess.run(
                [sys.executable, str(UNINSTALL_CLEANUP), "--platform", "codex", "--install-state-manifest",
                 str(manifest)],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            remaining = sorted(reports.iterdir())
            self.assertEqual(len(remaining), 31)
            self.assertFalse(old[0].exists())
            self.assertFalse(old[1].exists())
            self.assertTrue(old[2].exists())
            self.assertTrue(kept_copy.is_dir())

    def test_dry_run_reports_managed_targets_without_deleting_unclaimed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            unclaimed_skill = root / ".agents" / "skills" / "custom-local-skill"
            unclaimed_skill.mkdir(parents=True)
            (unclaimed_skill / "SKILL.md").write_text("# user owned\n", encoding="utf-8")
            manifest = _write_manifest(
                root,
                [
                    {"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"},
                    {"target_name": "custom-local-skill", "dest_path": unclaimed_skill.as_posix(), "install_mode": "copy"},
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(managed.exists())
            self.assertTrue(unclaimed_skill.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "dry-run")
            actions = {item["target_name"]: item["action"] for item in data["items"] if "target_name" in item}
            self.assertEqual(actions["task-router"], "would-remove")
            self.assertEqual(actions["custom-local-skill"], "manual-review")

    def test_apply_removes_only_ghost_alice_managed_targets_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            unclaimed_skill = root / ".agents" / "skills" / "custom-local-skill"
            unclaimed_skill.mkdir(parents=True)
            (unclaimed_skill / "SKILL.md").write_text("# user owned\n", encoding="utf-8")
            manifest = _write_manifest(
                root,
                [
                    {"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"},
                    {"target_name": "custom-local-skill", "dest_path": unclaimed_skill.as_posix(), "install_mode": "copy"},
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 1, msg=result.stderr + result.stdout)
            self.assertFalse(managed.exists())
            self.assertTrue(unclaimed_skill.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "apply")
            self.assertEqual(data["schema_version"], 2)
            install_actions = {
                item["target_name"]: item["action"]
                for item in data["items"]
                if item.get("kind") == "install-target"
            }
            self.assertEqual(install_actions["task-router"], "removed")
            self.assertEqual(install_actions["custom-local-skill"], "manual-review")

    def test_apply_unlinks_manifest_symlink_without_removing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "repo" / "_shared"
            source.mkdir(parents=True)
            (source / "helper.py").write_text("# helper\n", encoding="utf-8")
            dest = root / ".agents" / "skills" / "_shared"
            dest.parent.mkdir(parents=True)
            link_kind = _make_directory_link_or_skip(self, dest, source)
            manifest = _write_manifest(
                root,
                [{"target_name": "_shared", "dest_path": dest.as_posix(), "install_mode": link_kind}],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(dest.exists() or dest.is_symlink())
            self.assertTrue(source.exists())

    def test_apply_refuses_symlink_inside_platform_root_when_target_is_outside_source_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            source = root / "personal-source"
            source.mkdir()
            (source / "SKILL.md").write_text("# personal\n", encoding="utf-8")
            dest = root / ".agents" / "skills" / "personal-link"
            dest.parent.mkdir(parents=True)
            link_kind = _make_directory_link_or_skip(self, dest, source)
            manifest = _write_manifest(
                root,
                [
                    {
                        "target_name": "personal-link",
                        "source_path": (repo / "personal-link").as_posix(),
                        "dest_path": dest.as_posix(),
                        "install_mode": link_kind,
                    }
                ],
                source_root=repo,
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 1, msg=result.stderr + result.stdout)
            _assert_directory_link_exists(self, dest, link_kind)
            self.assertTrue(source.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            item = next(item for item in data["items"] if item.get("target_name") == "personal-link")
            self.assertEqual(item["action"], "manual-review")
            self.assertEqual(item["ownership"], "unclaimed")
            self.assertEqual(item["reason"], f"{link_kind}-outside-repo")

    def test_apply_refuses_manifest_targets_outside_platform_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "repo" / "_shared"
            source.mkdir(parents=True)
            outside = root / "outside-link"
            link_kind = _make_directory_link_or_skip(self, outside, source)
            manifest = _write_manifest(
                root,
                [{"target_name": "_shared", "dest_path": outside.as_posix(), "install_mode": link_kind}],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 1, msg=result.stderr + result.stdout)
            _assert_directory_link_exists(self, outside, link_kind)
            data = json.loads(report.read_text(encoding="utf-8"))
            item = next(item for item in data["items"] if item.get("target_name") == "_shared")
            self.assertEqual(item["action"], "manual-review")
            self.assertEqual(item["reason"], "outside-allowed-roots")

    def test_apply_removes_manifest_junction_without_removing_source_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "repo" / "_shared"
            source.mkdir(parents=True)
            (source / "helper.py").write_text("# helper\n", encoding="utf-8")
            dest = root / ".agents" / "skills" / "_shared"
            dest.parent.mkdir(parents=True)
            _make_windows_junction_or_skip(self, dest, source)
            manifest = _write_manifest(
                root,
                [{"target_name": "_shared", "dest_path": dest.as_posix(), "install_mode": "junction"}],
                source_root=root / "repo",
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(dest.exists())
            self.assertTrue(source.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            item = next(item for item in data["items"] if item.get("target_name") == "_shared")
            self.assertEqual(item["action"], "removed")
            self.assertEqual(item["ownership"], "ghost-alice-managed")
            self.assertEqual(item["reason"], "junction-to-repo")

    def test_apply_checks_report_path_before_removing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            report = root / "report-as-directory"
            report.mkdir()

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(managed.exists())

    def test_apply_removes_trace_backed_support_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            pending = root / ".ghost-alice" / "pending-merges" / "codex"
            pending.mkdir(parents=True)
            (pending / "snapshot.json").write_text('{"version": 2, "files": {}}\n', encoding="utf-8")
            (pending / "manifest.json").write_text('{"entries": []}\n', encoding="utf-8")
            events = root / ".ghost-alice" / "install-state" / "codex-events.jsonl"
            events.write_text('{"event":"copy_replace_failure"}\n', encoding="utf-8")
            feature_change = root / ".ghost-alice" / "install-state" / "codex-hook-feature-change.json"
            feature_change.write_text('{"kind":"codex_hooks_feature_flag"}\n', encoding="utf-8")
            hooks_dir = root / ".ghost-alice" / "hooks"
            hooks_dir.mkdir(parents=True)
            (hooks_dir / "ghost-alice-hook.mjs").write_text("// managed dispatcher\n", encoding="utf-8")
            rollback_dir = root / ".ghost-alice" / "install-rollbacks"
            rollback_dir.mkdir(parents=True)
            (rollback_dir / "rollback-task-router").write_text("previous copy\n", encoding="utf-8")
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(managed.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(events.exists())
            self.assertFalse(feature_change.exists())
            self.assertFalse(pending.exists())
            self.assertFalse(hooks_dir.exists())
            self.assertFalse(rollback_dir.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            support = {item["target_name"]: item for item in data["items"] if item.get("kind") == "support-artifact"}
            self.assertEqual(support["install-state-manifest"]["action"], "removed")
            self.assertEqual(support["install-state-events"]["action"], "removed")
            self.assertEqual(support["codex-hook-feature-change"]["action"], "removed")
            self.assertEqual(support["pending-merges"]["action"], "quarantined-removed")
            self.assertEqual(support["hook-dispatcher-assets"]["action"], "removed")
            self.assertEqual(support["install-rollbacks"]["action"], "quarantined-removed")
            pending_copy = Path(support["pending-merges"]["backup_path"])
            rollback_copy = Path(support["install-rollbacks"]["backup_path"])
            self.assertEqual((pending_copy / "manifest.json").read_text(), '{"entries": []}\n')
            self.assertEqual((rollback_copy / "rollback-task-router").read_text(), "previous copy\n")

    def test_apply_removes_addon_sidecars(self) -> None:
        # Full uninstall must clean the per-addon sidecar registry; otherwise orphan ~/.ghost-alice/addons/*.json survive a full uninstall.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            # Platform-scoped cleanup uses platform "codex".
            addons_dir = root / ".ghost-alice" / "addons" / "codex"
            addons_dir.mkdir(parents=True)
            (addons_dir / "noop.json").write_text(json.dumps(dict(
                schema_version='1.0', addon_id='noop', addon_version='1.0.0', source='/source/noop',
                platform='codex', owner='addon', origin='addon:noop', depends_on_core=[],
                min_core_version='0.0.0', installed_at='fixture', provided=[])), encoding='utf-8')
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(addons_dir.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            support = {item["target_name"]: item for item in data["items"] if item.get("kind") == "support-artifact"}
            self.assertEqual(support["addon-sidecars"]["action"], "removed")

    def test_apply_reports_hook_uninstall_before_target_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            data = json.loads(report.read_text(encoding="utf-8"))
            kinds = [item["kind"] for item in data["items"]]
            self.assertLess(kinds.index("hook-config"), kinds.index("install-target"))

    def _hook_path_repo(self, root: Path, value: str | None) -> tuple[str, Path]:
        git = shutil.which("git")
        if not git:
            self.skipTest("git executable is required for source repo hook path tests")
        repo = root / "repo"
        repo.mkdir()
        subprocess.run([git, "init"], cwd=repo, check=True, capture_output=True, text=True)
        if value is not None:
            subprocess.run([git, "config", "--local", "core.hooksPath", value], cwd=repo, check=True)
        return git, repo

    @staticmethod
    def _hook_path(git: str, repo: Path) -> str | None:
        current = subprocess.run([git, "config", "--local", "--get", "core.hooksPath"], cwd=repo,
                                 capture_output=True, text=True)
        return current.stdout.strip() if current.returncode == 0 else None

    @staticmethod
    def _hook_change(repo: Path, *, before: str | None) -> dict:
        return {
            "kind": "source_repo_hook_path",
            "repo_root": repo.as_posix(),
            "before_present": before is not None,
            "before": before,
            "after": (repo / "hooks").as_posix(),
            "applied_at": "2026-10-21T00:00:00Z",
        }

    def _source_hook_item(self, report: Path) -> dict:
        data = json.loads(report.read_text(encoding="utf-8"))
        return next(item for item in data["items"] if item.get("kind") == "source-repo-hook-config")

    def test_apply_leaves_a_source_repo_hook_path_no_change_record_names(self) -> None:
        # A value of `hooks` alone does not say this install wrote it: another installation or the user may have.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            git, repo = self._hook_path_repo(root, "hooks")
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                source_root=repo,
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertEqual(self._hook_path(git, repo), "hooks")
            source_hook = self._source_hook_item(report)
            self.assertEqual((source_hook["action"], source_hook["reason"]),
                             ("unchanged", "source-repo-hook-path-unrecorded"))

    def test_apply_unsets_the_hook_path_the_install_wrote_over_no_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            git, repo = self._hook_path_repo(root, None)
            subprocess.run([git, "config", "--local", "core.hooksPath", (repo / "hooks").as_posix()], cwd=repo,
                           check=True)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                source_root=repo,
                system_env_changes=[self._hook_change(repo, before=None)],
            )
            report = root / "uninstall-report.json"

            dry = self.run_cleanup(root, manifest, report)
            self.assertEqual(dry.returncode, 0, msg=dry.stderr + dry.stdout)
            self.assertEqual(self._source_hook_item(report)["action"], "would-remove-source-repo-hook-path")
            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIsNone(self._hook_path(git, repo))
            self.assertEqual(self._source_hook_item(report)["action"], "removed-source-repo-hook-path")

    def test_apply_restores_previous_source_repo_hook_path_from_change_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            git, repo = self._hook_path_repo(root, None)
            subprocess.run([git, "config", "--local", "core.hooksPath", (repo / "hooks").as_posix()], cwd=repo,
                           check=True)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                source_root=repo,
                system_env_changes=[self._hook_change(repo, before="custom-hooks")],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertEqual(self._hook_path(git, repo), "custom-hooks")
            source_hook = self._source_hook_item(report)
            self.assertEqual(source_hook["action"], "restored-source-repo-hook-path")
            self.assertEqual(source_hook["restored_to"], "custom-hooks")

    def test_apply_leaves_a_hook_path_that_changed_after_the_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            git, repo = self._hook_path_repo(root, "user-hooks")
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                source_root=repo,
                system_env_changes=[self._hook_change(repo, before=None)],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertEqual(self._hook_path(git, repo), "user-hooks")
            self.assertEqual(self._source_hook_item(report)["action"], "unchanged")

    def test_a_removal_that_is_not_the_last_host_leaves_the_hook_path(self) -> None:
        # Every host installs from the one install source, so the post-merge hook serves the hosts that remain.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            git, repo = self._hook_path_repo(root, None)
            hooks = (repo / "hooks").as_posix()
            subprocess.run([git, "config", "--local", "core.hooksPath", hooks], cwd=repo, check=True)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                source_root=repo,
                system_env_changes=[self._hook_change(repo, before=None)],
            )
            (manifest.parent / "claude.json").write_text(json.dumps({"schema_version": 1, "platform": "claude"}),
                                                         encoding="utf-8")
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertEqual(self._hook_path(git, repo), hooks)
            source_hook = self._source_hook_item(report)
            self.assertEqual((source_hook["action"], source_hook["reason"], source_hook["shared_platforms"]),
                             ("unchanged", "source-repo-hook-path-in-use-by-other-platform", ["claude"]))

    def test_the_record_of_the_hook_path_write_goes_with_the_last_host(self) -> None:
        # The install writes one record of its hook path write for the whole installation, next to the hosts'
        # install-state manifests; a host's removal keeps it while another host remains.
        for other_host in (True, False):
            with self.subTest(other_host=other_host), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                managed = _write_managed_skill(root, "task-router")
                manifest = _write_manifest(
                    root,
                    [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                )
                record = manifest.parent / "source-repo-hook-path.json"
                record.write_text(json.dumps({"kind": "source_repo_hook_path", "repo_root": (root / "repo").as_posix(),
                                              "before_present": False, "before": None,
                                              "after": (root / "repo" / "hooks").as_posix(),
                                              "applied_at": "2026-10-21T00:00:00Z"}), encoding="utf-8")
                if other_host:
                    (manifest.parent / "claude.json").write_text(
                        json.dumps({"schema_version": 1, "platform": "claude"}), encoding="utf-8")
                report = root / "uninstall-report.json"

                result = self.run_cleanup(root, manifest, report, apply_changes=True)

                self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
                self.assertEqual(record.exists(), other_host)
                data = json.loads(report.read_text(encoding="utf-8"))
                item = next(item for item in data["items"] if item.get("target_name") == "source-repo-hook-path-record")
                self.assertEqual(item["action"], "manual-review" if other_host else "removed")

    def test_apply_restores_codex_hooks_feature_flag_from_change_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_dir = root / ".codex"
            codex_dir.mkdir()
            config_toml = codex_dir / "config.toml"
            config_toml.write_text("[features]\nhooks = true\nmulti_agent = true\n", encoding="utf-8")
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                system_env_changes=[
                    {
                        "kind": "codex_hooks_feature_flag",
                        "path": config_toml.as_posix(),
                        "before_state": "false",
                        "after_state": "true",
                        "applied_at": "2026-06-04T00:00:00Z",
                    }
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            content = config_toml.read_text(encoding="utf-8")
            self.assertIn("hooks = false", content)
            self.assertIn("multi_agent = true", content)
            data = json.loads(report.read_text(encoding="utf-8"))
            feature = next(item for item in data["items"] if item.get("kind") == "codex-hook-feature-config")
            self.assertEqual(feature["action"], "restored-codex-hooks-feature-flag")
            self.assertEqual(feature["restored_to"], "false")

    def test_codex_hooks_feature_flag_rollback_preserves_unmanaged_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_dir = root / ".codex"
            codex_dir.mkdir()
            config_toml = codex_dir / "config.toml"
            config_toml.write_text("[features]\nhooks = true\n", encoding="utf-8")
            (codex_dir / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [
                                {
                                    "matcher": "",
                                    "hooks": [{"type": "command", "command": "echo user-hook"}],
                                }
                            ]
                        }
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                system_env_changes=[
                    {
                        "kind": "codex_hooks_feature_flag",
                        "path": config_toml.as_posix(),
                        "before_state": "false",
                        "after_state": "true",
                    }
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("hooks = true", config_toml.read_text(encoding="utf-8"))
            data = json.loads(report.read_text(encoding="utf-8"))
            feature = next(item for item in data["items"] if item.get("kind") == "codex-hook-feature-config")
            self.assertEqual(feature["action"], "preserved-existing")
            self.assertEqual(feature["reason"], "codex-hooks-feature-required-by-non-ghost-hooks")

    def test_apply_restores_codex_project_trust_from_change_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_dir = root / ".codex"
            codex_dir.mkdir()
            config_toml = codex_dir / "config.toml"
            project = root / "repo"
            project.mkdir()
            project_key = str(project.resolve())
            config_toml.write_text(
                f"[projects.{json.dumps(project_key)}]\ntrust_level = \"trusted\"\n",
                encoding="utf-8",
            )
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                system_env_changes=[
                    {
                        "kind": "codex_project_trust",
                        "path": config_toml.as_posix(),
                        "project_path": project_key,
                        "before_state": "untrusted",
                        "after_state": "trusted",
                    }
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn('trust_level = "untrusted"', config_toml.read_text(encoding="utf-8"))
            data = json.loads(report.read_text(encoding="utf-8"))
            trust = next(item for item in data["items"] if item.get("kind") == "codex-project-trust-config")
            self.assertEqual(trust["action"], "restored-codex-project-trust")
            self.assertEqual(trust["restored_to"], "untrusted")

    def test_apply_removes_codex_project_trust_when_change_record_was_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_dir = root / ".codex"
            codex_dir.mkdir()
            config_toml = codex_dir / "config.toml"
            project = root / "repo"
            project.mkdir()
            project_key = str(project.resolve())
            header = f"[projects.{json.dumps(project_key)}]"
            config_toml.write_text(f"{header}\ntrust_level = \"trusted\"\n", encoding="utf-8")
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                system_env_changes=[
                    {
                        "kind": "codex_project_trust",
                        "path": config_toml.as_posix(),
                        "project_path": project_key,
                        "before_state": "missing",
                        "after_state": "trusted",
                    }
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            content = config_toml.read_text(encoding="utf-8")
            self.assertNotIn(header, content)
            self.assertNotIn("trust_level", content)
            data = json.loads(report.read_text(encoding="utf-8"))
            trust = next(item for item in data["items"] if item.get("kind") == "codex-project-trust-config")
            self.assertEqual(trust["action"], "removed-codex-project-trust")
            self.assertEqual(trust["restored_to"], "missing")

    def test_apply_surfaces_codex_project_trust_when_before_state_was_other(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_dir = root / ".codex"
            codex_dir.mkdir()
            config_toml = codex_dir / "config.toml"
            project = root / "repo"
            project.mkdir()
            project_key = str(project.resolve())
            config_toml.write_text(
                f"[projects.{json.dumps(project_key)}]\ntrust_level = \"trusted\"\n",
                encoding="utf-8",
            )
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                system_env_changes=[
                    {
                        "kind": "codex_project_trust",
                        "path": config_toml.as_posix(),
                        "project_path": project_key,
                        "before_state": "other",
                        "after_state": "trusted",
                    }
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn('trust_level = "trusted"', config_toml.read_text(encoding="utf-8"))
            data = json.loads(report.read_text(encoding="utf-8"))
            trust = next(item for item in data["items"] if item.get("kind") == "codex-project-trust-config")
            self.assertEqual(trust["action"], "manual-review")
            self.assertEqual(trust["reason"], "codex-project-trust-before-state-not-restorable")
            self.assertEqual(trust["before_state"], "other")
            self.assertEqual(trust["after_state"], "trusted")

    def test_apply_defers_target_still_referenced_by_another_platform_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            other_manifest = root / ".ghost-alice" / "install-state" / "claude.json"
            other_manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "platform": "claude",
                        "source_root": (root / "repo").as_posix(),
                        "targets": [
                            {"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}
                        ],
                        "system_env_changes": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(managed.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            item = next(item for item in data["items"] if item.get("target_name") == "task-router")
            self.assertEqual(item["action"], "reference-released")
            self.assertEqual(item["reason"], "shared-with-other-platform")
            self.assertEqual(item["shared_platforms"], ["claude"])

    def test_public_uninstall_surface_has_no_removed_entrypoints(self) -> None:
        sh = "\n".join((REPO_ROOT / "_shared" / "installer" / name).read_text(encoding="utf-8")
                       for name in ("cli.py", "lifecycle.py"))
        ps1 = installer_ps1_source()
        readme = README.read_text(encoding="utf-8")
        legacy_doc = REPO_ROOT / "docs" / f"{REMOVED_DOC_STEM}.md"

        for body in (sh, ps1, readme):
            with self.subTest(surface=body[:20]):
                self.assertNotIn(REMOVED_BASH_FLAG, body)
                self.assertNotIn(REMOVED_BASH_CONFIRM_FLAG, body)
                self.assertNotIn(REMOVED_BASH_REPORT_FLAG, body)
                self.assertNotIn(REMOVED_PS_FLAG, body)
                self.assertNotIn(REMOVED_PS_CONFIRM_FLAG, body)
                self.assertNotIn(REMOVED_PS_REPORT_FLAG, body)
                self.assertNotIn(REMOVED_DOC_STEM, body.lower())
                self.assertNotIn("Advanced uninstall preview", body)

        self.assertIn("Full uninstall", sh)
        self.assertIn("_shared/uninstall_cleanup.py", sh)
        self.assertIn("bash install.sh --uninstall", readme)
        self.assertIn(".\\install.cmd --uninstall", readme)
        self.assertFalse(legacy_doc.exists())
        self.assertTrue(UNINSTALL_DOC.exists())

    def test_bash_removed_preview_flags_are_rejected_as_unknown_options(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh argument test")

        cases = (
            [REMOVED_BASH_FLAG],
            [REMOVED_BASH_CONFIRM_FLAG],
            [REMOVED_BASH_REPORT_FLAG, "report.json"],
            ["--apply"],
        )
        for args in cases:
            with self.subTest(args=args):
                result = subprocess.run(
                    [bash, str(INSTALL_SH), *args],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unknown option", result.stdout + result.stderr)

    def test_powershell_removed_preview_flags_are_rejected_as_unknown_options(self) -> None:
        # install.ps1 passes unknown PowerShell names to the cli, which rejects any unknown `-` token.
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("PowerShell 7.6+ (pwsh) is required for install.ps1 argument test")

        cases = (
            [REMOVED_PS_FLAG],
            [REMOVED_PS_CONFIRM_FLAG],
            [REMOVED_PS_REPORT_FLAG, "report.json"],
        )
        for args in cases:
            with self.subTest(args=args), tempfile.TemporaryDirectory() as temp_dir:
                env = installer_env(temp_dir)
                env["USERPROFILE"] = temp_dir
                env["GHOST_ALICE_LANG"] = "en"

                result = subprocess.run(
                    [pwsh, "-NoLogo", "-NoProfile", "-File", str(INSTALL_PS1), *args],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"Unknown option: {args[0]}", result.stdout + result.stderr)
                # pwsh writes its own state under HOME; the installer wrote nothing.
                self.assertEqual(installer_home_effects(Path(temp_dir)), [])

    def test_bash_uninstall_without_skill_args_runs_full_apply_cleanup(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh uninstall test")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".codex").mkdir()
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            env = installer_env(str(root))
            env["GHOST_ALICE_LANG"] = "en"

            source = checkout_copy(self)
            result = subprocess.run(
                [bash, str(source / "install.sh"), "--platform", "codex", "--uninstall"],
                cwd=source,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(managed.exists())
            self.assertFalse(manifest.exists())
            reports = glob(str(root / ".ghost-alice" / "uninstall-reports" / "codex-apply-*.json"))
            self.assertEqual(len(reports), 1)
            data = json.loads(Path(reports[0]).read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "apply")

    def test_bash_plain_uninstall_detects_install_state_manifest_platforms(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh uninstall test")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            env = installer_env(str(root))
            env["GHOST_ALICE_LANG"] = "en"

            source = checkout_copy(self)
            result = subprocess.run(
                [bash, str(source / "install.sh"), "--uninstall"],
                cwd=source,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(managed.exists())
            self.assertFalse(manifest.exists())


class UninstallExternalChangeQuarantineTest(unittest.TestCase):
    def _run(self, root, manifest, report, *, apply_changes=False, purge_modified=False):
        env = installer_env(str(root))
        env["USERPROFILE"] = str(root)
        args = [
            sys.executable,
            str(UNINSTALL_CLEANUP),
            "--platform",
            "codex",
            "--install-state-manifest",
            str(manifest),
            "--report-path",
            str(report),
        ]
        if apply_changes:
            args.append("--apply")
        if purge_modified:
            args.append("--purge-modified")
        return subprocess.run(
            args,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _externally_changed_skill(self, root: Path, name: str = "task-router") -> Path:
        target = _write_managed_skill(root, name)
        # Mutate content so the recorded hash no longer matches -> EXTERNALLY_MODIFIED_MANAGED.
        (target / "SKILL.md").write_text("# task-router\nUSER EDIT KEEP ME\n", encoding="utf-8")
        return target

    def _actions(self, report: Path) -> dict:
        data = json.loads(report.read_text(encoding="utf-8"))
        return {i["target_name"]: i["action"] for i in data["items"] if "target_name" in i}

    def test_apply_quarantines_externally_changed_skill_before_remove(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = self._externally_changed_skill(root)
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": target.as_posix(), "install_mode": "copy"}],
            )
            report = root / "report.json"

            result = self._run(root, manifest, report, apply_changes=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            backup = root / ".ghost-alice" / "uninstall-backup" / "codex" / "task-router" / "SKILL.md"
            self.assertTrue(backup.exists(), "skill with an external change must be backed up before removal")
            self.assertIn("USER EDIT KEEP ME", backup.read_text(encoding="utf-8"))
            self.assertFalse(target.exists(), "footprint removed after backup")
            self.assertEqual(self._actions(report)["task-router"], "quarantined-removed")

    def test_dry_run_externally_changed_skill_would_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = self._externally_changed_skill(root)
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": target.as_posix(), "install_mode": "copy"}],
            )
            report = root / "report.json"

            result = self._run(root, manifest, report)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(target.exists())
            self.assertEqual(self._actions(report)["task-router"], "would-quarantine-remove")

    def test_purge_modified_removes_externally_changed_skill_without_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = self._externally_changed_skill(root)
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": target.as_posix(), "install_mode": "copy"}],
            )
            report = root / "report.json"

            result = self._run(root, manifest, report, apply_changes=True, purge_modified=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(target.exists())
            backup = root / ".ghost-alice" / "uninstall-backup" / "codex" / "task-router"
            self.assertFalse(backup.exists(), "--purge-modified must not leave a backup")
            self.assertEqual(self._actions(report)["task-router"], "removed")


if __name__ == "__main__":
    unittest.main()
