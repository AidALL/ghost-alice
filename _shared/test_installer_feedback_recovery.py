"""Interrupted publication and unreadable settings retain installation ownership and recovery evidence."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import install_hooks
import installer_assets
import install_transaction
import uninstall_cleanup
from installer import report as installer_report
from installer_testing import installer_env
from test_install_transaction_publish import PublishCase


class PublishedOwnershipTests(PublishCase):
    def test_collector_adopts_only_an_explicit_valid_host_list(self):
        scripts = Path(__file__).resolve().parents[1] / 'merge-companion/scripts'
        with mock.patch.object(sys, 'path', [str(scripts), *sys.path]):
            import snapshot
            import diff_collector_cli
        dest = self.codex / 'alpha'
        request = self.request('codex', self.target('alpha', dest, 'copy'))
        install_transaction.publish(request)
        baseline = self.ghost / 'snapshot.json'
        snapshot.capture_snapshot(baseline, [dest / 'SKILL.md'], 'codex', self.codex)
        (self.src / 'alpha/new.md').write_text('new published content')
        install_transaction.publish(request)
        recorded = self.record()
        for index, (hosts, expected_changes) in enumerate((
            (['codex'], 0), (['claude', 'codex'], 0), (['claude'], 1),
            ('not-codex', 1), (None, 1), (['codex', None], 1), (['codex', 'not-a-host'], 1),
        )):
            with self.subTest(hosts=hosts):
                recorded['targets'][str(dest)]['used_by'] = hosts
                (self.ghost / 'installed-payload.json').write_text(json.dumps(recorded))
                pending = self.ghost / f'pending-{index}'
                manifest = pending / 'manifest.json'
                argv = ['collector', '--snapshot', str(baseline), '--pending', str(pending),
                        '--manifest', str(manifest), '--platform', 'codex', '--skills-dir', str(self.codex),
                        '--target', 'alpha', '--install-record-root', str(self.ghost)]
                with mock.patch.object(sys, 'argv', argv):
                    self.assertEqual(diff_collector_cli.main(), 0)
                entries = json.loads(manifest.read_text()).get('entries', []) if manifest.exists() else []
                self.assertEqual(len(entries), expected_changes)

    def test_retry_after_source_adds_and_deletes_files_uses_published_baseline(self):
        scripts = Path(__file__).resolve().parents[1] / 'merge-companion/scripts'
        with mock.patch.object(sys, 'path', [str(scripts), *sys.path]):
            import snapshot
            import diff_collector
        dest = self.codex / 'alpha'
        obsolete = self.src / 'alpha/obsolete.md'
        obsolete.write_text('old source content')
        request = self.request('codex', self.target('alpha', dest, 'copy'))
        install_transaction.publish(request)
        baseline = self.ghost / 'pending-merges/codex/snapshot.json'
        snapshot.capture_snapshot(baseline, list(dest.glob('*.md')), 'codex', self.codex)
        before = baseline.read_bytes()
        obsolete.unlink()
        (self.src / 'alpha/new.md').write_text('new source content')
        install_transaction.publish(request)
        recorded = self.record()['targets']
        self.assertEqual(diff_collector.collect_external_changes(baseline, list(dest.glob('*.md')), self.codex,
                                                               published_targets=recorded), [])
        self.assertEqual(baseline.read_bytes(), before)
        (dest / 'new.md').write_text('external edit after interrupted install')
        (dest / 'user.md').write_text('external addition')
        marker = dest / '.ghost-alice-install.json'
        marker_data = json.loads(marker.read_text())
        marker_data['content_hashes']['new.md'] = snapshot.file_hash(dest / 'new.md')
        marker.write_text(json.dumps(marker_data))
        changes = diff_collector.collect_external_changes(baseline, list(dest.glob('*.md')), self.codex,
                                                          published_targets=recorded)
        self.assertEqual({Path(row['path']).name for row in changes}, {'new.md', 'user.md'})

    def test_copy_owns_its_files_before_the_next_install_stage(self):
        dest = self.codex / 'alpha'
        request = self.request('codex', self.target('alpha', dest, 'copy'))
        install_transaction.publish(request)
        result = installer_assets.classify_skill_root(dest, expected_asset_id='alpha', repo_root=self.src)
        self.assertEqual(result.ownership, installer_assets.OWNERSHIP_GHOST_ALICE_MANAGED)
        self.assertFalse((self.ghost / 'install-state/codex.json').exists())
        self.assertFalse((self.src / 'alpha/.ghost-alice-install.json').exists())

    def test_retry_after_publish_does_not_invent_external_changes(self):
        scripts = Path(__file__).resolve().parents[1] / 'merge-companion/scripts'
        with mock.patch.object(sys, 'path', [str(scripts), *sys.path]):
            import snapshot
            import diff_collector
        dest = self.codex / 'alpha'
        request = self.request('codex', self.target('alpha', dest, 'copy'))
        install_transaction.publish(request)
        installer_assets.write_ownership_marker(dest, platform='codex', asset_id='alpha',
            source_repo=str(self.src), source_commit='c0ffee', install_mode='copy')
        baseline = self.ghost / 'pending-merges/codex/snapshot.json'
        snapshot.capture_snapshot(baseline, [dest / 'SKILL.md'], 'codex', self.codex)
        install_transaction.publish(request)
        self.assertEqual(diff_collector.collect_external_changes(baseline, [dest / 'SKILL.md'], self.codex), [])
        (dest / 'SKILL.md').write_text('external change\n')
        self.assertEqual(len(diff_collector.collect_external_changes(baseline, [dest / 'SKILL.md'], self.codex)), 1)


class UnreadableRecoveryTests(unittest.TestCase):
    def test_unreadable_manifest_preserves_records_for_unfinished_restoration(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            state = home / '.ghost-alice/install-state'
            state.mkdir(parents=True)
            files = [state / name for name in ('codex.json', 'codex-hook-feature-change.json',
                     'codex-project-trust-change.json', 'source-repo-hook-path.json')]
            for path in files:
                path.write_text('{broken')
            args = argparse.Namespace(platform='codex', install_state_manifest=files[0], apply_changes=True)
            with mock.patch.dict(os.environ, installer_env(home), clear=True):
                report = uninstall_cleanup.build_report(args)
            self.assertTrue(any(row['action'] == 'manual-review' for row in report['items']))
            self.assertEqual({p.name: p.read_text() for p in files}, {p.name: '{broken' for p in files})

    def test_corrupt_settings_recovery_is_visible_in_default_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'settings.json'
            path.write_text('{broken')
            output = io.StringIO()
            with mock.patch.dict(os.environ, installer_env(temporary), clear=True), contextlib.redirect_stdout(output):
                installer_report.configure(home=Path(temporary), tty=False)
                settings = installer_report.run_logged(install_hooks._read_settings, path)
            self.assertEqual(settings, {})
            self.assertIn(str(path), output.getvalue())
            self.assertIn(str(path.with_suffix('.json.corrupt-bak')), output.getvalue())
            self.assertIn('Continuing with empty settings', output.getvalue())
            self.assertEqual(path.with_suffix('.json.corrupt-bak').read_text(), '{broken')
