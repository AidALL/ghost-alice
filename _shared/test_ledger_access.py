"""Loading the session-intent ledger once and checking the coordinates a caller supplies. Python standard library."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger_access


class LoadLedgerTests(unittest.TestCase):
    def test_the_sibling_ledger_loads_once_and_is_the_module_python_knows_by_its_name(self):
        first = ledger_access.load_ledger()
        second = ledger_access.load_ledger()
        self.assertIs(first, second)
        self.assertEqual(Path(first.__file__).resolve(), ledger_access.LEDGER_PATH)
        self.assertIs(sys.modules[first.__name__], first)
        self.assertTrue(callable(first.validate_identity))

    def test_an_absent_ledger_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ledger_access.LedgerUnavailable) as caught:
                ledger_access.load_ledger(Path(folder) / 'session_intent_ledger.py')
        self.assertIsInstance(caught.exception, ImportError)

    def test_a_broken_ledger_raises_its_own_import_error_and_leaves_no_module_behind(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session_intent_ledger.py'
            path.write_text('import ghost_alice_missing_dependency_for_test\n', encoding='utf-8')
            before = set(sys.modules)
            with self.assertRaises(ImportError) as caught:
                ledger_access.load_ledger(path)
        self.assertNotIsInstance(caught.exception, ledger_access.LedgerUnavailable)
        self.assertEqual(caught.exception.name, 'ghost_alice_missing_dependency_for_test')
        self.assertEqual(set(sys.modules) - before, set())


class CoordinatesTests(unittest.TestCase):
    def test_valid_coordinates_return_the_ledger_the_resolved_root_and_the_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            for platform in ('claude', 'codex'):
                with self.subTest(platform=platform):
                    core, root, owner = ledger_access.coordinates(ledger_access.source_environment(
                        platform, 'session-1', root=folder))
                    self.assertIs(core, ledger_access.load_ledger())
                    self.assertEqual(root, Path(folder).resolve())
                    self.assertEqual(owner, dict(platform=platform, session_id='session-1'))

    def test_each_rejection_carries_its_reason(self):
        cases = {
            'ledger-root-unavailable': dict(GHOST_ALICE_PLATFORM='claude', GHOST_ALICE_SESSION_ID='s'),
            'owner-mismatch': dict(GHOST_ALICE_SESSION_INTENT_ROOT='/tmp', GHOST_ALICE_PLATFORM='agent', GHOST_ALICE_SESSION_ID='s'),
        }
        cases['owner-mismatch (empty session)'] = dict(GHOST_ALICE_SESSION_INTENT_ROOT='/tmp', GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='')
        cases['owner-mismatch (host conflict)'] = dict(GHOST_ALICE_SESSION_INTENT_ROOT='/tmp', GHOST_ALICE_PLATFORM='codex',
                                                       GHOST_ALICE_SESSION_ID='s', CODEX_THREAD_ID='other')
        for name, source in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ledger_access.CoordinateError) as caught:
                    ledger_access.coordinates(source)
                self.assertEqual(caught.exception.reason, name.split(' ')[0])

    def test_an_empty_or_equal_codex_thread_is_not_a_conflict(self):
        for thread in ('', 's'):
            with self.subTest(thread=repr(thread)):
                source = dict(GHOST_ALICE_SESSION_INTENT_ROOT='/tmp', GHOST_ALICE_PLATFORM='codex',
                              GHOST_ALICE_SESSION_ID='s', CODEX_THREAD_ID=thread)
                self.assertEqual(ledger_access.coordinates(source)[2], dict(platform='codex', session_id='s'))

    def test_an_inexact_identity_is_rejected_by_the_ledger_rule(self):
        source = dict(GHOST_ALICE_SESSION_INTENT_ROOT='/tmp', GHOST_ALICE_PLATFORM='claude', GHOST_ALICE_SESSION_ID='a b')
        with self.assertRaisesRegex(ValueError, 'invalid ledger identity') as caught:
            ledger_access.coordinates(source)
        self.assertNotIsInstance(caught.exception, ledger_access.CoordinateError)


class SourceEnvironmentTests(unittest.TestCase):
    def test_three_keys_alone_by_default(self):
        self.assertEqual(ledger_access.source_environment('codex', 's', root='/r'),
                         dict(GHOST_ALICE_SESSION_INTENT_ROOT='/r', GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='s'))

    def test_the_native_dir_key_follows_the_platform_and_the_environment_is_copied(self):
        base = dict(HOME='/home/x', GHOST_ALICE_SESSION_ID='old')
        source = ledger_access.source_environment('claude', 's', root=Path('/r'), native_dir=Path('/n'), environment=base)
        self.assertEqual(source, dict(HOME='/home/x', GHOST_ALICE_SESSION_INTENT_ROOT='/r', GHOST_ALICE_PLATFORM='claude',
                                      GHOST_ALICE_SESSION_ID='s', GHOST_ALICE_CLAUDE_DIR='/n'))
        self.assertEqual(base['GHOST_ALICE_SESSION_ID'], 'old')
        self.assertIn('GHOST_ALICE_CODEX_DIR', ledger_access.source_environment('codex', 's', root='/r', native_dir='/n'))

    def test_without_a_root_the_environment_keeps_its_own(self):
        source = ledger_access.source_environment('claude', 's', environment=dict(GHOST_ALICE_SESSION_INTENT_ROOT='/kept'))
        self.assertEqual(source['GHOST_ALICE_SESSION_INTENT_ROOT'], '/kept')
        self.assertNotIn('GHOST_ALICE_SESSION_INTENT_ROOT', ledger_access.source_environment('claude', 's'))


if __name__ == '__main__':
    unittest.main()
