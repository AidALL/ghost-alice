"""Behavioral contracts for the authoritative store, independent of exports.

Dependencies: Python standard library only.
"""
import importlib.util
from contextlib import closing
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("sqlite_ledger_under_test", Path(__file__).with_name("session_intent_ledger.py"))
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


class SQLiteAuthorityTests(unittest.TestCase):
    def moved_source_fixture(self, name):
        source, current = (self.root / name / part for part in ('A', 'B'))
        ledger.record_turn(root=source, platform='codex', session_id='native', raw_input='original input')
        ledger.write_current_session_pointer(source, 'codex', 'native')
        (source / 'original.txt').write_bytes(b'original companion\x00')
        (source / 'original-link').symlink_to('original.txt')
        with ledger.storage_transaction(source) as db:
            ledger._shared_module('context_lifecycle')._schema(db)
            uses = ledger._shared_module('response_context_use')
            uses._schema(db)
            uses._judgment_schema(db)
            ledger._shared_module('completion_assessment')._schema(db)
            db.execute("INSERT INTO core_context_dispositions VALUES ('old-context', ?)",
                       (json.dumps(dict(input_binding=dict(platform='codex', session_id='native', input_event_id='original'))),))
            db.execute("INSERT INTO core_context_current VALUES ('codex', 'native', 'old-context')")
            db.execute("INSERT INTO core_response_context_uses VALUES ('use', 'codex', 'native', 'original use')")
            db.execute("INSERT INTO core_response_use_judgments VALUES ('old-judgment', 'codex', 'native', 'use', 'original judgment')")
            db.execute("INSERT INTO core_response_use_current VALUES ('codex', 'native', 'use', 'old-judgment')")
            db.execute("INSERT INTO core_completion_candidates VALUES ('candidate', 'key', 'original proof')")
            db.execute('CREATE TABLE unknown_history (body BLOB)')
            db.executemany('INSERT INTO unknown_history VALUES (?)', [(b'original\x00',)] * 2)
        self.assertTrue(ledger._shared_module('session_check_cache').record_delivery(
            source, 'codex', 'native', 'receipt', 'original receipt', create=True))
        ledger.snapshot_roots(source, self.root / 'absent', current, writers_stopped=True)
        return source, current

    def test_moved_source_verification_preserves_originals_while_current_heads_advance(self):
        source, current = self.moved_source_fixture('continued')
        ledger.record_turn(root=current, platform='codex', session_id='native', raw_input='later input')
        ledger.record_turn(root=current, platform='codex', session_id='other', raw_input='another session')
        ledger.write_current_session_pointer(current, 'codex', 'other')
        with ledger.storage_transaction(current) as db:
            db.execute("INSERT INTO core_context_dispositions VALUES ('new-context', ?)",
                       (json.dumps(dict(input_binding=dict(platform='codex', session_id='native', input_event_id='later'))),))
            db.execute("UPDATE core_context_current SET reference='new-context'")
            db.execute("INSERT INTO core_response_use_judgments VALUES ('new-judgment', 'codex', 'native', 'use', 'later judgment')")
            db.execute("UPDATE core_response_use_current SET reference='new-judgment'")
            db.execute("INSERT INTO unknown_history VALUES ('later original')")
        self.assertTrue(ledger._shared_module('session_check_cache').record_delivery(
            current, 'codex', 'native', 'receipt', 'later receipt', create=False))
        before = [ledger._snapshot_files(root) for root in (source, current)]
        self.assertTrue(hasattr(ledger, 'verify_moved_source'), 'The ledger owner must verify retained migration sources')
        baseline = ledger.verify_moved_source(source, current)
        self.assertEqual(baseline, ledger._shared_module('content_digest').state(source, include_generated=True))
        self.assertEqual([ledger._snapshot_files(root) for root in (source, current)], before)

    def test_moved_source_verification_rejects_lost_or_changed_originals(self):
        for case in ('event', 'same-revision-state', 'source-only-session', 'original', 'unknown-row',
                     'duplicate-row', 'companion', 'nested-pointer', 'link', 'cache-audit',
                     'missing-head-original', 'unknown-head-schema', 'storage-type', 'foreign-context-head'):
            with self.subTest(case=case):
                source, current = self.moved_source_fixture(case)
                if case == 'source-only-session':
                    ledger.record_turn(root=source, platform='codex', session_id='only-a', raw_input='not copied')
                elif case == 'companion':
                    (source / 'original.txt').write_bytes(b'changed after migration')
                elif case == 'nested-pointer':
                    for root in (source, current):
                        path = root / 'codex/evidence/current-session.json'
                        path.parent.mkdir(parents=True)
                        path.write_text(root.name)
                elif case == 'link':
                    (current / 'original-link').unlink()
                    (current / 'original-link').symlink_to('elsewhere')
                elif case == 'cache-audit':
                    with sqlite3.connect(current / 'session-checks.sqlite3') as db:
                        db.execute('DELETE FROM check_events')
                elif case == 'storage-type':
                    for root in (source, current):
                        with sqlite3.connect(root / 'ghost-state.sqlite3') as db:
                            db.execute('UPDATE unknown_history SET body=?', (1,))
                    with sqlite3.connect(current / 'ghost-state.sqlite3') as db:
                        db.execute('UPDATE unknown_history SET body=? WHERE rowid=1', (1.0,))
                        self.assertEqual(db.execute('SELECT typeof(body) FROM unknown_history ORDER BY rowid').fetchall(),
                                         [('real',), ('integer',)])
                else:
                    with sqlite3.connect(current / 'ghost-state.sqlite3') as db:
                        if case == 'event':
                            db.execute('DELETE FROM intent_events')
                        elif case == 'same-revision-state':
                            state = json.loads(db.execute('SELECT state_json FROM sessions').fetchone()[0])
                            state['current_goal'] = 'different state without history'
                            db.execute('UPDATE sessions SET state_json=?', (json.dumps(state),))
                        elif case == 'original':
                            db.execute('DROP TABLE core_completion_candidates')
                        elif case == 'unknown-row':
                            db.execute('DELETE FROM unknown_history')
                        elif case == 'duplicate-row':
                            db.execute('DELETE FROM unknown_history WHERE rowid=1')
                        elif case == 'missing-head-original':
                            db.execute('DROP TRIGGER immutable_context_delete')
                            db.execute('DELETE FROM core_context_dispositions')
                        elif case == 'unknown-head-schema':
                            db.execute('ALTER TABLE core_context_current ADD COLUMN original TEXT')
                        elif case == 'foreign-context-head':
                            db.execute("INSERT INTO core_context_dispositions VALUES ('foreign-context', ?)",
                                       (json.dumps(dict(input_binding=dict(platform='codex', session_id='other'))),))
                            db.execute("UPDATE core_context_current SET reference='foreign-context'")
                before = [ledger._snapshot_files(root) for root in (source, current)]
                with self.assertRaises(ValueError):
                    ledger.verify_moved_source(source, current)
                self.assertEqual([ledger._snapshot_files(root) for root in (source, current)], before)

    def test_moved_source_verification_preserves_legacy_bytes_after_import_normalization(self):
        origin, source, current = (self.root / name for name in ('origin', 'A', 'B'))
        identity = dict(platform='codex', session_id='native')
        ledger.record_turn(root=origin, **identity, raw_input='legacy input')
        ledger.export_session(root=origin, **identity, destination=source)
        ledger.snapshot_roots(source, self.root / 'absent', current, writers_stopped=True)
        ledger.record_turn(root=current, **identity, raw_input='current input')
        ledger.verify_moved_source(source, current)
        ledger.session_paths(current, **identity)['state'].unlink()
        with self.assertRaises(ValueError):
            ledger.verify_moved_source(source, current)

    def test_moved_source_verification_detects_source_change_during_comparison(self):
        source, current = self.moved_source_fixture('changed-during-check')
        snapshot = ledger._snapshot_files
        def changed_after_reading_current(root):
            result = snapshot(root)
            if root.resolve() == current.resolve():
                (source / 'original.txt').write_text('changed during comparison')
            return result
        with mock.patch.object(ledger, '_snapshot_files', side_effect=changed_after_reading_current):
            with self.assertRaisesRegex(ValueError, 'source changed'):
                ledger.verify_moved_source(source, current)

    def moved_link_fixture(self, name):
        root = (self.root / name).resolve()
        source, current = root / 'old/A', root / 'new/deeper/B'
        (source / 'nested').mkdir(parents=True)
        (source / 'proof.txt').write_text('retained original')
        external = root / 'external.txt'
        external.write_text('external original')
        indirect = root / 'external-tree/child'
        indirect.mkdir(parents=True)
        (indirect.parent / 'proof.txt').write_text('external original')
        links = {
            'absolute': str(source / 'proof.txt'),
            'alias': str(external),
            'alias-chain': str(source / 'alias'),
            'relative': './proof.txt',
            'indirect': str(indirect),
            'nested/internal': '../proof.txt',
            'external-absolute': str(external),
            'external-parent': 'indirect/../proof.txt',
            'nested/external-relative': os.path.relpath(external, source / 'nested'),
        }
        expected = dict(links, absolute='proof.txt', relative='proof.txt')
        expected['alias-chain'] = 'alias'
        expected['external-parent'] = os.path.relpath(indirect.parent / 'proof.txt', current)
        expected['nested/external-relative'] = os.path.relpath(external, current / 'nested')
        for relative, target in links.items():
            (source / relative).symlink_to(target)
        return source, current, links, expected

    def test_snapshot_relocates_links_for_final_ledger_without_changing_inputs(self):
        source, current, links, expected = self.moved_link_fixture('new-links')
        current.mkdir(parents=True)
        (current / 'existing-link').symlink_to(source / 'proof.txt')
        (current / 'alias').symlink_to(links['alias'])
        (current / 'alias-chain').symlink_to('alias')
        expected['existing-link'] = 'proof.txt'
        staged = self.root / 'temporary/stage'
        before = [ledger._snapshot_files(root) for root in (source, current)]
        ledger.snapshot_roots(source, current, staged, writers_stopped=True)
        self.assertEqual([ledger._snapshot_files(root) for root in (source, current)], before)
        self.assertEqual({name: os.readlink(staged / name) for name in expected}, expected)
        shutil.rmtree(current)
        staged.rename(current)
        shutil.rmtree(source)
        for name in expected:
            if name == 'indirect':
                self.assertTrue((current / name).is_dir())
                continue
            self.assertEqual((current / name).read_text(),
                             'external original' if 'external' in name or 'alias' in name else 'retained original')
        (current / 'alias').unlink()
        (current / 'alias').symlink_to('proof.txt')
        self.assertEqual((current / 'alias-chain').read_text(), 'retained original')

    def test_snapshot_rejects_equal_relative_link_text_with_different_external_targets(self):
        source, current, staged = (self.root / 'link-conflict' / name for name in ('old/A', 'new/B', 'stage'))
        for root in (source, current):
            root.mkdir(parents=True)
            (root.parent / 'outside.txt').write_text(root.name)
            (root / 'proof-link').symlink_to('../outside.txt')
        before = [ledger._snapshot_files(root) for root in (source, current)]
        with self.assertRaisesRegex(ledger.LedgerSnapshotConflict, 'proof-link'):
            ledger.snapshot_roots(source, current, staged, writers_stopped=True)
        self.assertFalse(staged.exists())
        self.assertEqual([ledger._snapshot_files(root) for root in (source, current)], before)

    def test_older_moved_links_relocate_atomically_and_retry_without_changing_source(self):
        source, current, links, expected = self.moved_link_fixture('older-links')
        shutil.copytree(source, current, symlinks=True)
        links['existing-link'] = str(source / 'proof.txt')
        expected['existing-link'] = 'proof.txt'
        (current / 'existing-link').symlink_to(links['existing-link'])
        before = ledger._snapshot_files(source)
        copied = ledger._snapshot_files(current)
        with self.assertRaisesRegex(ValueError, 'nested/external-relative'):
            ledger.verify_moved_source(source, current)
        self.assertEqual(ledger._snapshot_files(current), copied)
        self.assertEqual(ledger._snapshot_files(source), before)
        # A journal-free copy cannot establish ownership of this ambiguous B
        # pointer. Only the separately corrected fixture proceeds to relocation.
        (current / 'nested/external-relative').unlink()
        (current / 'nested/external-relative').symlink_to(expected['nested/external-relative'])
        links['nested/external-relative'] = expected['nested/external-relative']
        ledger.verify_moved_source(source, current)
        self.assertEqual({name: os.readlink(current / name) for name in links}, links)
        replace = os.replace
        changes = []
        def interrupted(src, dst):
            if changes:
                raise OSError('interrupted link replacement')
            changes.append(Path(dst))
            return replace(src, dst)
        self.assertTrue(hasattr(ledger, 'relocate_moved_links'), 'The ledger owner must relocate copied links before source cleanup')
        with mock.patch.object(ledger.os, 'replace', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interrupted link replacement'):
                ledger.relocate_moved_links(source, current)
        self.assertEqual(len(changes), 1)
        self.assertEqual(ledger._snapshot_files(source), before)
        changes[0].unlink()
        changes[0].symlink_to(current / 'proof.txt')
        ledger.verify_moved_source(source, current)
        ledger.relocate_moved_links(source, current)
        self.assertEqual({name: os.readlink(current / name) for name in links}, expected)
        self.assertEqual(ledger._snapshot_files(source), before)
        ledger.verify_moved_source(source, current)
        placed = ledger._snapshot_files(current)
        ledger.relocate_moved_links(source, current)
        self.assertEqual(ledger._snapshot_files(current), placed)
        shutil.rmtree(source)
        for name in expected:
            if name == 'indirect':
                self.assertTrue((current / name).is_dir())
                continue
            self.assertEqual((current / name).read_text(),
                             'external original' if 'external' in name or 'alias' in name else 'retained original')

    def test_snapshot_keeps_selection_when_discovery_pointer_bytes_differ(self):
        host, session = 'future-host', 'same-session'
        origin = self.root / 'origin'
        ledger.record_turn(root=origin, platform=host, session_id=session, raw_input='retained input')
        state = ledger.read_session_state(root=origin, platform=host, session_id=session)
        events = ledger.read_session_events(root=origin, platform=host, session_id=session)
        for storage in ('legacy', 'sqlite'):
            with self.subTest(storage=storage):
                source, existing, staged = (self.root / storage / name for name in ('A', 'B', 'stage'))
                for root in (source, existing):
                    if storage == 'legacy':
                        ledger.export_session(root=origin, platform=host, session_id=session, destination=root)
                    else:
                        ledger.snapshot_roots(origin, self.root / 'absent', root, writers_stopped=True)
                    # DB discovery shadows these different stale JSON selections.
                    pointer_session = session if storage == 'legacy' else 'stale-' + root.name
                    ledger.write_current_session_pointer(root, host, pointer_session)
                    self.assertEqual(ledger.read_current_session_pointer(root, host), session)
                before = [ledger._snapshot_files(root) for root in (source, existing)]
                pointer = ledger.current_session_pointer_path(source, host).read_bytes()
                ledger.snapshot_roots(source, existing, staged, writers_stopped=True)
                self.assertEqual(ledger.read_current_session_pointer(staged, host), session)
                # Import preserves history without promoting an exported intake to a current binding.
                expected = dict(state, canonical_input_event_id='') if storage == 'legacy' else state
                self.assertEqual(ledger.read_session_state(root=staged, platform=host, session_id=session), expected)
                self.assertEqual(ledger.read_session_events(root=staged, platform=host, session_id=session), events)
                self.assertEqual(ledger.current_session_pointer_path(staged, host).read_bytes(), pointer)
                self.assertEqual([ledger._snapshot_files(root) for root in (source, existing)], before)

    def test_snapshot_keeps_conflicts_outside_equivalent_discovery_files(self):
        for case in ('different-session', 'invalid-schema', 'nested-evidence', 'noncanonical-host', 'symlink'):
            with self.subTest(case=case):
                source, existing, staged = (self.root / case / name for name in ('A', 'B', 'stage'))
                relative = Path('future-host/current-session.json')
                if case == 'nested-evidence':
                    relative = Path('future-host/evidence/current-session.json')
                elif case == 'noncanonical-host':
                    relative = Path('future host/current-session.json')
                for root in (source, existing):
                    path = root / relative
                    path.parent.mkdir(parents=True)
                    data = dict(schema_version=ledger.CURRENT_SESSION_SCHEMA,
                                session_id=root.name if case == 'different-session' else 'same-session',
                                state_path=str(root / 'state.json'))
                    if case == 'invalid-schema':
                        data['schema_version'] = 'unsupported'
                    if case == 'symlink':
                        target = root.parent / (root.name + '-pointer.json')
                        target.write_text(json.dumps(data))
                        path.symlink_to(target)
                    else:
                        path.write_text(json.dumps(data))
                before = [ledger._snapshot_files(root) for root in (source, existing)]
                with self.assertRaisesRegex(ledger.LedgerSnapshotConflict, 'Ledger migration conflict:'):
                    ledger.snapshot_roots(source, existing, staged, writers_stopped=True)
                self.assertEqual([ledger._snapshot_files(root) for root in (source, existing)], before)
                self.assertFalse(staged.exists())

    def test_snapshot_detects_pointer_mutation_after_equivalent_selection_check(self):
        source, existing, staged = (self.root / name for name in ('A', 'B', 'stage'))
        for root in (source, existing):
            ledger.write_current_session_pointer(root, 'future-host', 'same-session')
        pointer = ledger.current_session_pointer_path(source, 'future-host')
        other = ledger.current_session_pointer_path(existing, 'future-host')
        original = pointer.read_bytes()
        other_original = other.read_bytes()
        copy = shutil.copy2
        def copy_then_change_input(src, dest, *args, **kwargs):
            result = copy(src, dest, *args, **kwargs)
            if src == pointer.resolve():
                other.write_bytes(other.read_bytes() + b' ')
            return result
        with mock.patch.object(ledger.shutil, 'copy2', side_effect=copy_then_change_input):
            with self.assertRaisesRegex(ValueError, 'Ledger input changed during snapshot'):
                ledger.snapshot_roots(source, existing, staged, writers_stopped=True)
        self.assertEqual(pointer.read_bytes(), original)
        self.assertEqual(other.read_bytes(), other_original + b' ')
        self.assertEqual(ledger.read_current_session_pointer(existing, 'future-host'), 'same-session')

    def test_snapshot_rejects_source_change_during_baseline_capture(self):
        source, existing, staged = (self.root / name for name in ('A', 'B', 'stage'))
        ledger.record_turn(root=source, platform='codex', session_id='native', raw_input='original input')
        companion = source / 'original.txt'
        companion.write_text('original companion')
        digest = ledger._shared_module('content_digest')
        capture = digest.state

        def changed_before_capture(root, **kwargs):
            companion.write_text('not preserved in the snapshot')
            return capture(root, **kwargs)

        with mock.patch.object(digest, 'state', side_effect=changed_before_capture):
            with self.assertRaisesRegex(ValueError, 'Ledger input changed during snapshot'):
                ledger.snapshot_roots(source, existing, staged, writers_stopped=True)
        self.assertEqual(companion.read_text(), 'not preserved in the snapshot')
        self.assertEqual((staged / 'original.txt').read_text(), 'original companion')

    def test_snapshot_returns_baseline_before_final_input_validation(self):
        source, existing, staged = ((self.root / name).resolve() for name in ('A', 'B', 'stage'))
        ledger.record_turn(root=source, platform='codex', session_id='native', raw_input='original input')
        companion = source / 'original.txt'
        companion.write_text('original companion')
        (source / 'original-link').symlink_to(companion)
        # Stabilize SQLite's read sidecars before describing the original physical state.
        ledger._snapshot_files(source)
        expected = ledger._shared_module('content_digest').state(source, include_generated=True)
        snapshot = ledger._snapshot_files

        def changed_after_source_validation(root):
            result = snapshot(root)
            if root == existing.resolve() and staged.exists():
                companion.write_text('later source content')
            return result

        with mock.patch.object(ledger, '_snapshot_files', side_effect=changed_after_source_validation):
            baselines = ledger.snapshot_roots(source, existing, staged, writers_stopped=True)
        self.assertEqual(baselines, (expected, None))
        self.assertEqual(companion.read_text(), 'later source content')
        self.assertEqual((staged / 'original.txt').read_text(), 'original companion')
        self.assertEqual(os.readlink(staged / 'original-link'), 'original.txt')

    def test_snapshot_returns_existing_baseline_before_final_input_validation(self):
        source, existing, staged = ((self.root / name).resolve() for name in ('A', 'B', 'stage'))
        ledger.record_turn(root=source, platform='codex', session_id='native', raw_input='original input')
        existing.mkdir()
        companion = existing / 'original-b.txt'
        companion.write_text('original B content')
        expected = ledger._shared_module('content_digest').state(existing, include_generated=True)
        snapshot = ledger._snapshot_files

        def changed_after_validation(root):
            result = snapshot(root)
            if root == existing and staged.exists():
                companion.write_text('later B content')
            return result

        with mock.patch.object(ledger, '_snapshot_files', side_effect=changed_after_validation):
            baselines = ledger.snapshot_roots(source, existing, staged, writers_stopped=True)
        self.assertEqual(baselines[1], expected)
        self.assertEqual(companion.read_text(), 'later B content')
        self.assertEqual((staged / 'original-b.txt').read_text(), 'original B content')

    def test_snapshot_rejects_link_directory_collision_before_changing_either_input(self):
        source, existing, staged = self.root / 'A', self.root / 'B', self.root / 'stage'
        (source / 'target').mkdir(parents=True)
        (source / 'target/original.txt').write_text('original')
        (source / 'nested').symlink_to(source / 'target', target_is_directory=True)
        (existing / 'nested').mkdir(parents=True)
        (existing / 'nested/new.txt').write_text('from B')
        before = [ledger._snapshot_files(path) for path in (source, existing)]
        with self.assertRaises(ValueError):
            ledger.snapshot_roots(source, existing, staged, writers_stopped=True)
        self.assertEqual([ledger._snapshot_files(path) for path in (source, existing)], before)
        self.assertFalse((source / 'target/new.txt').exists())
        self.assertFalse(staged.exists())

    def test_migration_snapshot_preserves_full_databases_and_disjoint_legacy_sources(self):
        source, existing, staged = self.root / 'A', self.root / 'B', self.root / 'stage'
        ledger.record_turn(root=source, platform='codex', session_id='native-a', raw_input='original')
        with ledger.storage_transaction(source) as db:
            db.execute('CREATE TABLE retained_originals (body BLOB)')
            db.execute('INSERT INTO retained_originals VALUES (?)', (b'original body\x00',))
        with sqlite3.connect(source / 'session-checks.sqlite3') as db:
            db.execute('CREATE TABLE checks (stamp TEXT)')
            db.execute("INSERT INTO checks VALUES ('original receipt stamp')")
        state = ledger.default_state('claude', 'native-b')
        paths = ledger.session_paths(existing, 'claude', 'native-b')
        paths['dir'].mkdir(parents=True)
        paths['state'].write_text(json.dumps(state))
        raw = paths['state'].read_bytes()
        with self.assertRaisesRegex(ValueError, 'stopped'):
            ledger.snapshot_roots(source, existing, staged, writers_stopped=False)
        self.assertFalse(staged.exists())
        ledger.snapshot_roots(source, existing, staged, writers_stopped=True)
        self.assertEqual(ledger.read_session_state(root=staged, platform='codex', session_id='native-a'),
                         ledger.read_session_state(root=source, platform='codex', session_id='native-a'))
        self.assertEqual(ledger.read_session_state(root=staged, platform='claude', session_id='native-b'), state)
        self.assertEqual(ledger.session_paths(staged, 'claude', 'native-b')['state'].read_bytes(), raw)
        self.assertEqual(paths['state'].read_bytes(), raw)
        with sqlite3.connect(staged / 'ghost-state.sqlite3') as db:
            self.assertEqual(db.execute('SELECT body FROM retained_originals').fetchone()[0], b'original body\x00')
            self.assertEqual(db.execute('SELECT count(*) FROM legacy_migrations').fetchone()[0], 1)
        with sqlite3.connect(staged / 'session-checks.sqlite3') as db:
            self.assertEqual(db.execute('SELECT stamp FROM checks').fetchone()[0], 'original receipt stamp')

    def test_migration_snapshot_deduplicates_identical_databases_but_never_merges_distinct_ones(self):
        source, existing = self.root / 'A', self.root / 'B'
        ledger.record_turn(root=source, platform='codex', session_id='native', raw_input='original')
        ledger.snapshot_roots(source, existing, self.root / 'initial', writers_stopped=True)
        (self.root / 'initial').rename(existing)
        ledger.snapshot_roots(source, existing, self.root / 'same', writers_stopped=True)
        ledger.record_turn(root=existing, platform='codex', session_id='native', raw_input='different history')
        before = ledger.read_session_state(root=existing, platform='codex', session_id='native')
        with self.assertRaisesRegex(ledger.LedgerSnapshotConflict, 'conflict'):
            ledger.snapshot_roots(source, existing, self.root / 'conflicting', writers_stopped=True)
        self.assertEqual(ledger.read_session_state(root=existing, platform='codex', session_id='native'), before)

    def test_migration_snapshot_rejects_same_session_conflict_across_sqlite_and_legacy_storage(self):
        source, existing, staged = (self.root / name for name in ('A', 'B', 'stage'))
        identity = dict(platform='codex', session_id='native')
        ledger.record_turn(root=source, **identity, raw_input='original history')
        ledger.export_session(root=source, **identity, destination=existing)
        ledger.record_turn(root=source, **identity, raw_input='new history only in SQLite')
        self.assertNotEqual(ledger.read_session_state(root=source, **identity)['latest_input_digest'],
                            ledger.read_session_state(root=existing, **identity)['latest_input_digest'])
        before = [ledger._snapshot_files(root) for root in (source, existing)]
        self.assertTrue(before[0].keys().isdisjoint(before[1]), 'No file conflict may mask the session comparison')
        with self.assertRaisesRegex(ledger.LedgerSnapshotConflict, '^Ledger migration conflict: session codex/native$'):
            ledger.snapshot_roots(source, existing, staged, writers_stopped=True)
        self.assertEqual([ledger._snapshot_files(root) for root in (source, existing)], before)
        self.assertFalse(staged.exists())

    def test_retired_root_rejects_writes_without_recreation_and_other_roots_remain_writable(self):
        original = self.seed()
        ghost = self.root.parent / (self.root.name + '-installation')
        self.addCleanup(lambda: shutil.rmtree(ghost, ignore_errors=True))
        ghost.mkdir()
        (ghost / 'config.json').write_text(json.dumps({'retired_ledger_roots': [str(self.root)]}))
        with mock.patch.dict(os.environ, {'GHOST_ALICE_GHOST_ROOT': str(ghost)}):
            for present in (True, False):
                with self.subTest(present=present):
                    if not present:
                        shutil.rmtree(self.root)
                    with self.assertRaisesRegex(ValueError, 'retired'):
                        ledger.record_turn(**self.args, intent_delta={'current_goal': 'must not write'},
                                           expected_input_event_id=original['latest_input_event_id'])
                    self.assertEqual(self.root.exists(), present)
            for path in (ghost / 'session-intent', ghost / 'independent-development'):
                ledger.record_turn(root=path, platform='codex', session_id='session-a', raw_input='new request')
                self.assertEqual(ledger.read_session_state(root=path, platform='codex', session_id='session-a')['ledger_revision'], 1)

    def test_retirement_also_rejects_a_previously_opened_transaction(self):
        ghost = self.root / 'installation'
        ghost.mkdir()
        with ledger.storage_transaction(self.root) as connection:
            (ghost / 'config.json').write_text(json.dumps({'retired_ledger_roots': [str(self.root)]}))
            with mock.patch.dict(os.environ, {'GHOST_ALICE_GHOST_ROOT': str(ghost)}):
                with self.assertRaisesRegex(ValueError, 'retired'):
                    ledger.record_turn(**self.args, raw_input='must not write', transaction=connection)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(mock.patch.dict(os.environ, {
            'HOME': str(self.root), 'GHOST_ALICE_GHOST_ROOT': str(self.root / '.ghost-alice'),
            'CODEX_HOME': str(self.root / '.codex'), 'CLAUDE_CONFIG_DIR': str(self.root / '.claude'),
        }))
        self.args = dict(root=self.root, platform="codex", session_id="session-a")

    def seed(self):
        ledger.record_turn(**self.args, raw_input="first request")
        return ledger.read_session_state(**self.args)

    def test_first_wal_transition_retries_busy_before_any_mutation(self):
        connect = sqlite3.connect
        attempts = []

        class ContendedConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "PRAGMA journal_mode = WAL":
                    attempts.append(sql)
                    if len(attempts) < 3:
                        failure = sqlite3.OperationalError("database is locked")
                        failure.sqlite_errorcode = sqlite3.SQLITE_BUSY
                        raise failure
                return super().execute(sql, *args)

        def contended(*args, **kwargs):
            return connect(*args, **kwargs, factory=ContendedConnection)

        with mock.patch.object(ledger.sqlite3, "connect", side_effect=contended):
            state = self.seed()
        self.assertEqual(len(attempts), 3)
        self.assertEqual(state["ledger_revision"], 1)
        self.assertEqual(len(ledger.read_session_events(**self.args)), 1)

    def test_wal_initialization_does_not_retry_non_contention_errors(self):
        connect = sqlite3.connect
        attempts = []

        class BrokenConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "PRAGMA journal_mode = WAL":
                    attempts.append(sql)
                    failure = sqlite3.OperationalError("read-only database")
                    failure.sqlite_errorcode = sqlite3.SQLITE_READONLY
                    raise failure
                return super().execute(sql, *args)

        def broken(*args, **kwargs):
            return connect(*args, **kwargs, factory=BrokenConnection)

        with mock.patch.object(ledger.sqlite3, "connect", side_effect=broken):
            with self.assertRaisesRegex(sqlite3.OperationalError, "read-only"):
                self.seed()
        self.assertEqual(len(attempts), 1)

    def test_database_is_authority_when_export_is_missing_or_poisoned(self):
        original = self.seed()
        self.assertTrue((self.root / "ghost-state.sqlite3").is_file(), "mutations must create SQLite authority")
        path = ledger.session_paths(**self.args)["state"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**original, "current_goal": "poisoned export"}))
        self.assertEqual(ledger.read_session_state(**self.args), original)
        path.unlink()
        self.assertEqual(ledger.read_session_state(**self.args), original)

    def test_outer_transaction_rolls_back_state_and_event(self):
        self.assertTrue(hasattr(ledger, "storage_transaction"), "shared transactional API required")
        old = self.seed()
        events = ledger.read_session_events(**self.args)
        with self.assertRaisesRegex(RuntimeError, "injected interruption"):
            with ledger.storage_transaction(self.root) as transaction:
                ledger.record_turn(**self.args, intent_delta={"current_goal": "uncommitted"},
                    expected_input_event_id=old["latest_input_event_id"], transaction=transaction)
                raise RuntimeError("injected interruption")
        self.assertEqual(ledger.read_session_state(**self.args), old)
        self.assertEqual(ledger.read_session_events(**self.args), events)

    def test_wrong_database_transaction_cannot_cross_session_root(self):
        self.assertTrue(hasattr(ledger, "storage_transaction"), "shared transactional API required")
        self.seed()
        other = self.root / "other"
        with ledger.storage_transaction(other) as transaction:
            with self.assertRaisesRegex(ValueError, "root|database"):
                ledger.record_turn(**self.args, raw_input="wrong root", transaction=transaction)

    def test_migration_preserves_source_and_history_and_is_idempotent(self):
        self.assertTrue(hasattr(ledger, "migrate_session"), "legacy import API required")
        paths = ledger.session_paths(**self.args)
        paths["dir"].mkdir(parents=True)
        state = ledger.default_state("codex", "session-a")
        state.update(current_goal="legacy goal", ledger_revision=1)
        event = dict(event="intent-updated", platform="codex", session_id="session-a", ledger_revision=1, mutation_id="old-mutation")
        paths["state"].write_text(json.dumps(state))
        paths["events"].write_text(json.dumps(event) + "\n")
        originals = {key: paths[key].read_bytes() for key in ("state", "events")}
        self.assertEqual(ledger.migrate_session(**self.args), state)
        self.assertEqual(ledger.migrate_session(**self.args), state)
        self.assertEqual(ledger.read_session_events(**self.args), [event])
        self.assertEqual({key: paths[key].read_bytes() for key in originals}, originals)

    def test_v1_input_history_survives_read_import_and_v2_write(self):
        for anchored in (False, True):
            with self.subTest(anchored=anchored):
                root = self.root / ("anchored" if anchored else "without-anchor")
                args = dict(self.args, root=root)
                paths = ledger.session_paths(**args)
                paths["dir"].mkdir(parents=True)
                digest = ledger.text_digest("legacy input")
                event_id = ledger.json_digest({"event": "user-input-observed", "input_digest": digest})
                state = ledger.default_state("codex", "session-a")
                state.update(schema_version="session-intent-ledger.v1", ledger_revision=1,
                             latest_input_event_id=event_id, latest_input_digest=digest,
                             latest_input_char_count=len("legacy input"))
                stored = dict(state)
                if not anchored:
                    for key in ledger.INPUT_ANCHOR_FIELDS:
                        stored.pop(key)
                    state["ledger_revision"] = 0
                event = dict(event="user-input-observed", platform="codex", session_id="session-a",
                             event_id=event_id, input_event_id=event_id, input_digest=digest,
                             input_char_count=len("legacy input"), ledger_revision=1,
                             mutation_id="original-mutation", source="agent", ts="2026-01-01T00:00:00Z")
                paths["state"].write_text(json.dumps(stored))
                paths["events"].write_text(json.dumps(event) + "\n")
                originals = {key: paths[key].read_bytes() for key in ("state", "events")}
                self.assertEqual(ledger.read_session_state(**args), stored)
                self.assertFalse(ledger.storage_database_path(root).exists())
                self.assertEqual(ledger.migrate_session(**args), state)
                with ledger.storage_transaction(root) as transaction:
                    self.assertEqual(ledger._current_observation_sequence(transaction, state), 1)
                ledger.record_turn(**args, expected_input_event_id=event_id,
                                   intent_delta={"current_goal": "Continue the existing work"})
                current = ledger.read_session_state(**args)
                self.assertEqual(current["schema_version"], "session-intent-ledger.v2")
                self.assertEqual(current["latest_input_event_id"], event_id)
                self.assertEqual(ledger.read_session_events(**args)[0], event)
                self.assertEqual({key: paths[key].read_bytes() for key in originals}, originals)

    def test_corrupt_database_never_revives_legacy_export(self):
        self.seed()
        database = self.root / "ghost-state.sqlite3"
        # Close per-call connections before replacing the test database.
        database.write_bytes(b"not a SQLite database")
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            ledger.read_session_state(**self.args)

    def test_deleted_database_cannot_silently_reimport_stale_export(self):
        original = self.seed()
        path = ledger.session_paths(**self.args)["state"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**original, "current_goal": "stale export"}))
        (self.root / "ghost-state.sqlite3").unlink()
        with self.assertRaisesRegex(ValueError, "missing|restore"):
            ledger.read_session_state(**self.args)
        with self.assertRaisesRegex(ValueError, "missing|restore"):
            ledger.record_turn(**self.args, raw_input="must not reset history")

    def test_invalid_migration_does_not_create_partial_session(self):
        self.assertTrue(hasattr(ledger, "migrate_session"), "legacy import API required")
        paths = ledger.session_paths(**self.args)
        paths["dir"].mkdir(parents=True)
        paths["state"].write_text(json.dumps(ledger.default_state("codex", "session-a")))
        paths["events"].write_text(json.dumps(dict(platform="claude", session_id="other")))
        with self.assertRaisesRegex(ValueError, "identity"):
            ledger.migrate_session(**self.args)
        with closing(sqlite3.connect(self.root / "ghost-state.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)

    def test_reporting_consumers_read_without_exports(self):
        old = self.seed()
        ledger.record_turn(**self.args, expected_input_event_id=old["latest_input_event_id"], intent_delta={
            "current_goal": "current DB goal", "conduct_feedback": [{"id": "feedback", "source": "user-said",
            "corrective_rule": "Respect the revised condition", "status": "open"}]})
        root = Path(__file__).resolve().parents[2]
        def load(relative):
            entry = importlib.util.spec_from_file_location("consumer_" + Path(relative).stem, root / relative)
            module = importlib.util.module_from_spec(entry)
            sys.modules[entry.name] = module
            entry.loader.exec_module(module)
            return module
        path = ledger.session_paths(**self.args)["state"]
        analysis = load("skill-evolution/scripts/analyze_io_trace.py")
        aggregate = load("skill-evolution/scripts/aggregate_recommendations.py")
        self.assertEqual(analysis.load_intent_context(path)["current_goal"], "current DB goal")
        report = aggregate.aggregate(self.root)
        self.assertEqual(report["session_files"], 1)
        self.assertEqual(report["recommendations"][0]["id"], "feedback")


if __name__ == "__main__":
    unittest.main()
