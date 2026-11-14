"""Retired assets leave discovery without losing review or interrupted-work evidence."""
from __future__ import annotations

import json
import contextlib
import io
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_destination
from installer_assets import write_ownership_marker
from installer_testing import destination_fixture, installer_env
import retired_assets
import uninstall_cleanup


class Interrupted(BaseException):
    pass


class RetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ghost = self.root / 'ghost'
        self.skills = self.root / 'skills'
        self.skills.mkdir()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.enterContext(mock.patch.dict(os.environ, installer_env(self.root), clear=True))
        self.enterContext(install_destination.use(destination_fixture(self.root)))
        self.release = self.enterContext(mock.patch.object(uninstall_cleanup, 'release_target_records',
                                                          create=True, return_value=None))
        self.references = self.enterContext(mock.patch.object(uninstall_cleanup, '_platforms_referencing_target',
                                                             return_value=[]))

    def asset(self, name='jailbreak-detector', *, modified=False, source=None):
        path = self.skills / name
        path.mkdir()
        (path / 'SKILL.md').write_text('original\n')
        write_ownership_marker(path, platform='codex', asset_id=name, source_repo=str(source or self.source),
                               source_commit='5de7e59', install_mode='copy')
        if modified:
            (path / 'SKILL.md').write_text('user change\n')
        return path

    def retire(self, path):
        return retired_assets.retire_skill(path, platform='codex', source_root=self.source,
                                           pending_root=self.ghost)

    def entries(self):
        path = self.ghost / 'pending-merges/codex/manifest.json'
        return json.loads(path.read_text())['entries'] if path.exists() else []

    def works(self):
        return list((self.ghost / 'deprecated-skill-backups').glob('*/work.json'))

    def test_all_retired_names_use_ownership_and_release_current_records(self):
        for name in ('harness-security-scan', 'session-intent-guard', 'jailbreak-detector'):
            with self.subTest(name=name):
                asset = self.asset(name)
                result = self.retire(asset)
                self.assertEqual(result['action'], 'retired-managed')
                self.assertFalse(asset.exists())
                self.assertEqual((Path(result['backup_path']) / 'SKILL.md').read_text(), 'original\n')
                self.assertEqual(json.loads(Path(result['work_path']).read_text())['phase'], 'complete')
                self.release.assert_called_with(root=self.ghost, platform='codex', destination=asset,
                                                kind='skill', provider='core')

    def test_unknown_same_name_is_preserved_and_absence_is_not_preservation(self):
        asset = self.skills / 'harness-security-scan'
        asset.mkdir()
        (asset / 'mine.txt').write_text('private\n')
        result = self.retire(asset)
        self.assertEqual(result['action'], 'preserved')
        self.assertEqual((asset / 'mine.txt').read_text(), 'private\n')
        self.assertEqual(self.retire(self.skills / 'session-intent-guard')['action'], 'absent')
        self.release.assert_not_called()

    def test_old_checkout_link_requires_recorded_source_and_preserves_link_string(self):
        old = self.root / 'old-checkout/jailbreak-detector'
        old.parent.mkdir()
        asset = self.skills / 'jailbreak-detector'
        asset.symlink_to(old)
        self.assertEqual(self.retire(asset)['action'], 'preserved')
        manifest = self.ghost / 'install-state/codex.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps(dict(platform='codex', targets=[dict(target_name=asset.name,
            source_path=str(old), dest_path=str(asset), install_mode='symlink')])))
        result = self.retire(asset)
        self.assertEqual(result['action'], 'retired-managed')
        self.assertEqual(os.readlink(result['backup_path']), str(old))
        self.assertFalse(old.exists())

    def test_old_checkout_marker_needs_its_recorded_source(self):
        old = self.root / 'old-checkout'
        asset = self.asset(source=old)
        self.assertEqual(self.retire(asset)['action'], 'preserved')
        manifest = self.ghost / 'install-state/codex.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps(dict(platform='codex', targets=[dict(target_name=asset.name,
            source_path=str(old / asset.name), dest_path=str(asset), install_mode='copy')])))
        self.assertEqual(self.retire(asset)['action'], 'retired-managed')

    def test_relative_retired_link_is_preserved_as_an_entry_and_resumes(self):
        asset = self.skills / 'jailbreak-detector'
        target = '../source/jailbreak-detector'
        asset.symlink_to(target)
        first = self.retire(asset)
        self.assertEqual(os.readlink(first['backup_path']), target)
        self.assertEqual(self.retire(asset)['backup_path'], first['backup_path'])
        self.assertFalse((self.source / asset.name).exists())

    def test_pending_review_is_committed_before_record_release_and_not_duplicated(self):
        asset = self.asset(modified=True)
        def release(**kwargs):
            self.assertEqual(len(self.entries()), 1)
            self.assertTrue(Path(self.entries()[0]['backup_path']).exists())
            raise OSError('record write denied')
        self.release.side_effect = release
        with self.assertRaisesRegex(OSError, 'record write denied'):
            self.retire(asset)
        self.assertFalse(asset.exists())
        first = self.entries()[0]
        self.release.side_effect = None
        result = self.retire(asset)
        self.assertEqual(result['action'], 'retired-managed')
        self.assertEqual(self.entries(), [first])
        self.assertEqual((Path(first['backup_path']) / 'SKILL.md').read_text(), 'user change\n')
        self.assertEqual(json.loads(Path(result['work_path']).read_text())['phase'], 'complete')

    def test_interruption_after_move_resumes_without_audit_log(self):
        asset = self.asset(modified=True)
        original = retired_assets._rename_path
        def moved_then_stop(source, destination):
            original(source, destination)
            raise Interrupted()
        with mock.patch.object(retired_assets, '_rename_path', side_effect=moved_then_stop):
            with self.assertRaises(Interrupted):
                self.retire(asset)
        self.assertFalse(asset.exists())
        self.assertEqual(len(self.works()), 1)
        self.assertFalse((self.ghost / 'install-state/codex-events.jsonl').exists())
        result = self.retire(asset)
        self.assertEqual(result['action'], 'retired-managed')
        self.assertEqual(len(self.entries()), 1)

    def test_interruption_before_move_resumes_only_unchanged_original(self):
        asset = self.asset()
        with mock.patch.object(retired_assets, '_rename_path', side_effect=Interrupted):
            with self.assertRaises(Interrupted):
                self.retire(asset)
        self.assertEqual(len(self.works()), 1)
        (asset / 'SKILL.md').write_text('new unrelated contents\n')
        result = self.retire(asset)
        self.assertEqual(result['action'], 'retired-incomplete')
        self.assertIn('changed', result['reason'])
        self.assertEqual((asset / 'SKILL.md').read_text(), 'new unrelated contents\n')
        self.release.assert_not_called()

    def test_recreated_identical_original_is_not_the_recorded_entry(self):
        asset = self.asset()
        with mock.patch.object(retired_assets, '_rename_path', side_effect=Interrupted):
            with self.assertRaises(Interrupted):
                self.retire(asset)
        saved = self.root / 'user-kept-original'
        asset.rename(saved)
        shutil.copytree(saved, asset)
        self.assertEqual((saved / 'SKILL.md').read_bytes(), (asset / 'SKILL.md').read_bytes())
        result = self.retire(asset)
        self.assertEqual(result['action'], 'retired-incomplete')
        self.assertIn('entry-changed', result['reason'])
        self.assertTrue(asset.exists())
        self.assertTrue(saved.exists())
        self.release.assert_not_called()

    def test_recreated_original_is_never_overwritten_after_move(self):
        asset = self.asset(modified=True)
        original = retired_assets._rename_path
        def moved_then_stop(source, destination):
            original(source, destination)
            raise Interrupted()
        with mock.patch.object(retired_assets, '_rename_path', side_effect=moved_then_stop):
            with self.assertRaises(Interrupted):
                self.retire(asset)
        asset.mkdir()
        (asset / 'SKILL.md').write_text('replacement\n')
        result = self.retire(asset)
        self.assertEqual(result['action'], 'retired-incomplete')
        self.assertIn('collision', result['reason'])
        self.assertEqual((asset / 'SKILL.md').read_text(), 'replacement\n')

    def test_full_identity_detects_link_and_excluded_file_changes_before_resume(self):
        asset = self.asset()
        (asset / '__pycache__').mkdir()
        (asset / '__pycache__/private.txt').write_text('keep\n')
        (asset / 'external').symlink_to(self.root / 'not-owned')
        original = retired_assets._rename_path
        def moved_then_stop(source, destination):
            original(source, destination)
            raise Interrupted()
        with mock.patch.object(retired_assets, '_rename_path', side_effect=moved_then_stop):
            with self.assertRaises(Interrupted):
                self.retire(asset)
        work = json.loads(self.works()[0].read_text())
        backup = Path(work['backup_path'])
        (backup / '__pycache__/private.txt').write_text('changed\n')
        result = self.retire(asset)
        self.assertEqual(result['action'], 'retired-incomplete')
        self.assertIn('changed', result['reason'])
        self.assertEqual(os.readlink(backup / 'external'), str(self.root / 'not-owned'))
        self.release.assert_not_called()

    def test_shared_discovery_path_keeps_files_and_reports_incomplete_retirement(self):
        asset = self.asset()
        self.references.return_value = ['claude']
        result = self.retire(asset)
        self.assertEqual(result['action'], 'retired-incomplete')
        self.assertEqual(result['shared_platforms'], ['claude'])
        self.assertEqual((asset / 'SKILL.md').read_text(), 'original\n')
        self.assertTrue(result['records_released'])
        self.assertEqual(self.works(), [])

    def test_unknown_references_stop_before_moving_or_releasing(self):
        asset = self.asset()
        self.references.side_effect = ValueError('contradictory host inventory')
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            self.retire(asset)
        self.assertTrue(asset.exists())
        self.release.assert_not_called()

    def test_pending_failure_before_record_release_restores_original(self):
        asset = self.asset(modified=True)
        with mock.patch.object(retired_assets, '_pending', side_effect=OSError('pending denied')):
            with self.assertRaisesRegex(OSError, 'pending denied'):
                self.retire(asset)
        self.assertEqual((asset / 'SKILL.md').read_text(), 'user change\n')
        self.release.assert_not_called()
        self.assertEqual(self.retire(asset)['action'], 'retired-managed')
        self.assertEqual(len(self.entries()), 1)

    def test_restore_collision_keeps_both_original_replacement_and_backup(self):
        asset = self.asset(modified=True)
        def collide(*args):
            asset.mkdir()
            (asset / 'SKILL.md').write_text('replacement\n')
            raise OSError('review write denied')
        with mock.patch.object(retired_assets, '_pending', side_effect=collide):
            with self.assertRaisesRegex(RuntimeError, 'recovery backup remains'):
                self.retire(asset)
        work = json.loads(self.works()[0].read_text())
        self.assertEqual((asset / 'SKILL.md').read_text(), 'replacement\n')
        self.assertEqual((Path(work['backup_path']) / 'SKILL.md').read_text(), 'user change\n')
        self.release.assert_not_called()

    def test_committed_review_then_writer_failure_never_restores_or_duplicates_a_decision(self):
        asset = self.asset(modified=True)
        original = retired_assets._pending
        def committed_then_fail(*args):
            original(*args)
            raise OSError('reply lost after review commit')
        with mock.patch.object(retired_assets, '_pending', side_effect=committed_then_fail):
            with self.assertRaisesRegex(OSError, 'reply lost'):
                self.retire(asset)
        self.assertFalse(asset.exists())
        manifest = self.ghost / 'pending-merges/codex/manifest.json'
        data = json.loads(manifest.read_text())
        data['entries'][0].update(decided=True, decision='merged')
        manifest.write_text(json.dumps(data))
        self.assertEqual(self.retire(asset)['action'], 'retired-managed')
        self.assertEqual(self.entries(), data['entries'])

    def test_record_boundary_write_failure_after_review_keeps_backup_for_retry(self):
        asset = self.asset(modified=True)
        original = retired_assets._write_work
        def fail_boundary(path, work):
            if work['phase'] == 'records-started':
                raise OSError('work write denied')
            original(path, work)
        with mock.patch.object(retired_assets, '_write_work', side_effect=fail_boundary):
            with self.assertRaisesRegex(OSError, 'work write denied'):
                self.retire(asset)
        self.assertFalse(asset.exists())
        self.assertEqual(len(self.entries()), 1)
        self.release.assert_not_called()
        self.assertEqual(self.retire(asset)['action'], 'retired-managed')
        self.assertEqual(len(self.entries()), 1)

    def test_clean_record_release_failure_keeps_backup_and_resumes(self):
        asset = self.asset()
        self.release.side_effect = OSError('snapshot denied')
        with self.assertRaisesRegex(OSError, 'snapshot denied'):
            self.retire(asset)
        self.assertFalse(asset.exists())
        self.assertEqual(self.entries(), [])
        self.release.side_effect = None
        self.assertEqual(self.retire(asset)['action'], 'retired-managed')

    def test_cli_incomplete_result_has_nonzero_exit_and_keeps_independent_progress(self):
        first = self.asset('harness-security-scan')
        second = self.asset('jailbreak-detector')
        self.references.side_effect = lambda path, **kwargs: ['claude'] if path == first else []
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = retired_assets.main(['--skills-root', str(self.skills), '--source-root', str(self.source),
                                        '--ghost-root', str(self.ghost), '--platform', 'codex'])
        self.assertEqual(code, 1)
        items = {Path(item['path']).name: item for item in map(json.loads, output.getvalue().splitlines())}
        self.assertEqual(items[first.name]['action'], 'retired-incomplete')
        self.assertTrue(first.exists())
        self.assertEqual(items[second.name]['action'], 'retired-managed')
        self.assertFalse(second.exists())

    def test_cli_record_failure_names_backup_and_work_while_original_is_absent(self):
        asset = self.asset()
        self.release.side_effect = OSError('record denied')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = retired_assets.main(['--skills-root', str(self.skills), '--source-root', str(self.source),
                                        '--ghost-root', str(self.ghost), '--platform', 'codex'])
        self.assertEqual(code, 1)
        item = next(item for item in map(json.loads, output.getvalue().splitlines()) if item['path'] == str(asset))
        self.assertFalse(asset.exists())
        work_path = self.works()[0]
        work = json.loads(work_path.read_text())
        self.assertIn(work['backup_path'], '\n'.join(item['errors']))
        self.assertIn(str(work_path), '\n'.join(item['errors']))

    def test_completed_backup_stays_available_for_final_cleanup_owner(self):
        asset = self.asset()
        first = self.retire(asset)
        again = self.retire(asset)
        self.assertEqual(again['backup_path'], first['backup_path'])
        self.assertEqual(len(self.works()), 1)
        self.assertTrue(Path(first['backup_path']).exists())

    def test_marker_exclusions_remain_review_originals_after_retirement(self):
        cases = [('harness-security-scan', '.git/history'),
                 ('session-intent-guard', 'node_modules/private.txt'),
                 ('jailbreak-detector', '__pycache__/private-link')]
        for name, relative in cases:
            with self.subTest(relative=relative):
                asset = self.asset(name)
                private = asset / relative
                private.parent.mkdir(parents=True)
                if private.name == 'private-link':
                    private.symlink_to('../private-document')
                else:
                    private.write_bytes(b'only user copy\n')
                result = self.retire(asset)
                backup = Path(result['backup_path'])
                uninstall_cleanup.cleanup_obsolete_copies(root=self.ghost, platform='codex', completed=[])
                self.assertTrue(backup.exists())
                self.assertEqual(result['ownership'], 'externally-modified-managed')
                self.assertTrue(any(entry['backup_path'] == str(backup) for entry in self.entries()))
                if private.name == 'private-link':
                    self.assertEqual(os.readlink(backup / relative), '../private-document')
                else:
                    self.assertEqual((backup / relative).read_bytes(), b'only user copy\n')

    def test_earlier_complete_receipt_does_not_prove_ownership_of_excluded_content(self):
        asset = self.asset()
        (asset / '.git').mkdir()
        (asset / '.git/history').write_bytes(b'only user history\n')
        result = self.retire(asset)
        work_path = Path(result['work_path'])
        work = json.loads(work_path.read_text())
        # Earlier retirement wrote this ownership from the marker's partial hash walk.
        work['ownership'] = 'ghost-alice-managed'
        work_path.write_text(json.dumps(work))
        rows = uninstall_cleanup.cleanup_obsolete_copies(root=self.ghost, platform='codex', completed=[])
        self.assertEqual((Path(result['backup_path']) / '.git/history').read_bytes(), b'only user history\n')
        self.assertEqual(rows[0]['action'], 'preserved')

    def test_command_modified_content_keeps_review_and_releases_command_baseline(self):
        command = self.root / 'platform/commands/jailbreak-detector.md'
        command.parent.mkdir(parents=True)
        command.write_text('@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\nuser addition\n')
        result = retired_assets.retire_command(command, source_root=self.source, pending_root=self.ghost)
        self.assertEqual(result['action'], 'retired-managed')
        self.release.assert_called_with(root=self.ghost, platform='claude', destination=command,
                                        kind='command', provider='core')
        entry = json.loads((self.ghost / 'pending-merges/claude/manifest.json').read_text())['entries'][0]
        self.assertEqual(entry['skill'], 'jailbreak-detector')
        self.assertIn('user addition', Path(entry['backup_path']).read_text())

    def test_clean_command_retires_and_unknown_command_stays(self):
        command = self.root / 'platform/commands/jailbreak-detector.md'
        command.parent.mkdir(parents=True)
        command.write_text('my command\n')
        result = retired_assets.retire_command(command, source_root=self.source, pending_root=self.ghost)
        self.assertEqual(result['action'], 'preserved')
        self.assertEqual(command.read_text(), 'my command\n')
        command.write_text('@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\n')
        result = retired_assets.retire_command(command, source_root=self.source, pending_root=self.ghost)
        self.assertEqual(result['action'], 'retired-managed')
        self.assertFalse(command.exists())
        self.assertFalse((self.ghost / 'pending-merges/claude/manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
