"""secret_patterns: the one list of context-free secret patterns, and the copies that must equal it."""
import importlib.util
from pathlib import Path
import sys
import unittest

import secret_patterns

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SecretPatternsTest(unittest.TestCase):
    def test_the_four_patterns_match_secret_like_values_and_not_plain_text(self):
        for value in ('sk-' + 'a' * 24, 'ghp_' + 'b' * 24, 'AKIA' + 'C' * 16, '-----BEGIN RSA PRIVATE KEY-----'):
            with self.subTest(value=value):
                self.assertTrue(any(pattern.search(value) for pattern in secret_patterns.SECRET_PATTERNS))
        self.assertFalse(any(pattern.search('token abc') for pattern in secret_patterns.SECRET_PATTERNS))
        self.assertEqual(len(secret_patterns.SECRET_PATTERNS), 4)

    def test_security_scan_copy_matches_the_owner(self):
        # The scanner skill runs without _shared, so it keeps a copy; the copy must not drift.
        scanner = _load('scan_agent_security_surface_under_test', 'agent-security-scan/scripts/scan_agent_security_surface.py')
        self.addCleanup(sys.modules.pop, 'scan_agent_security_surface_under_test', None)
        self.assertEqual([(p.pattern, p.flags) for p in scanner.SECRET_PATTERNS[:4]],
                         [(p.pattern, p.flags) for p in secret_patterns.SECRET_PATTERNS])

    def test_compact_handoff_uses_the_owner_and_adds_only_its_own_pattern(self):
        handoff = _load('compact_handoff_under_test', 'compact-handoff/scripts/compact_handoff.py')
        self.addCleanup(sys.modules.pop, 'compact_handoff_under_test', None)
        self.assertEqual(handoff.SECRET_PATTERNS[:4], list(secret_patterns.SECRET_PATTERNS))
        self.assertEqual(len(handoff.SECRET_PATTERNS), 5)
        for value in ('API_KEY: ' + 'a' * 20, 'token="' + 'b' * 20, "secret = '" + 'c' * 20):
            with self.subTest(value=value):
                self.assertFalse(any(pattern.search(value) for pattern in secret_patterns.SECRET_PATTERNS))
                self.assertTrue(handoff.contains_secret_like_value(value))
        for value in ('token: ' + 'a' * 19, 'secret=' + 'b' * 19,
                      'token ' + 'c' * 20, 'password: ' + 'd' * 20):
            with self.subTest(value=value):
                self.assertFalse(handoff.contains_secret_like_value(value))


if __name__ == '__main__':
    unittest.main()
