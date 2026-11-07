"""Session identity from host payloads and the environment, and its file-name form. Python standard library."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_identity


class SafeComponentTests(unittest.TestCase):
    def test_keeps_the_ledger_rule(self):
        self.assertEqual(session_identity.safe_component('a=b.c_d-e'), 'a=b.c_d-e')
        self.assertEqual(session_identity.safe_component(' ./x y/z. '), 'x-y-z')
        self.assertEqual(session_identity.safe_component('x' * 130), 'x' * 120)
        self.assertEqual(session_identity.safe_component(''), 'unknown')
        self.assertEqual(session_identity.safe_component(None, ''), '')
        self.assertEqual(session_identity.safe_component('--', 'run'), 'run')

    def test_an_exact_identity_is_unchanged_by_the_rule_and_within_the_limit(self):
        self.assertTrue(session_identity.is_exact('thread=1.2_3-4'))
        self.assertTrue(session_identity.is_exact('x' * 120))
        self.assertFalse(session_identity.is_exact('x' * 121))
        self.assertFalse(session_identity.is_exact('a b'))
        self.assertFalse(session_identity.is_exact('.a'))
        self.assertFalse(session_identity.is_exact(''))
        self.assertFalse(session_identity.is_exact(None))
        self.assertFalse(session_identity.is_exact(5))


class SessionCandidateTests(unittest.TestCase):
    def test_payload_keys_come_in_the_union_order_before_the_environment(self):
        payload = dict(threadId='t4', thread_id='t3', conversation_id='c2', sessionId='s1', session_id='s0')
        env = dict(CODEX_THREAD_ID='e1', GHOST_ALICE_SESSION_ID='e2')
        self.assertEqual(session_identity.session_candidates('codex', payload, env), ['s0', 's1', 'c2', 't3', 't4', 'e1', 'e2'])
        self.assertEqual(session_identity.session_candidates('claude', payload, env), ['s0', 's1', 'c2', 't3', 't4', 'e2'])

    def test_empty_values_are_skipped_and_values_stay_as_the_host_wrote_them(self):
        payload = dict(session_id='', sessionId=None, conversation_id='a b', thread_id=5)
        self.assertEqual(session_identity.session_candidates('claude', payload, {}), ['a b', 5])
        self.assertEqual(session_identity.session_candidates('claude', {}, None), [])

    def test_bound_session_takes_the_first_candidate_only_when_it_is_exact(self):
        self.assertEqual(session_identity.bound_session('claude', dict(session_id='abc'), {}), 'abc')
        self.assertEqual(session_identity.bound_session('claude', {}, dict(GHOST_ALICE_SESSION_ID='env-1')), 'env-1')
        self.assertIsNone(session_identity.bound_session('claude', dict(session_id='a b', sessionId='fine'), {}))
        self.assertIsNone(session_identity.bound_session('claude', dict(session_id='x' * 121), {}))
        self.assertIsNone(session_identity.bound_session('claude', {}, {}))
        self.assertIsNone(session_identity.bound_session('claude', dict(session_id=5), {}))

    def test_host_bound_session_reads_the_codex_thread_before_the_ghost_alice_session(self):
        self.assertEqual(session_identity.host_bound_session('codex', dict(CODEX_THREAD_ID='t', GHOST_ALICE_SESSION_ID='g')), 't')
        self.assertEqual(session_identity.host_bound_session('codex', dict(GHOST_ALICE_SESSION_ID='g')), 'g')
        self.assertEqual(session_identity.host_bound_session('claude', dict(CODEX_THREAD_ID='t', GHOST_ALICE_SESSION_ID='g')), 'g')
        self.assertEqual(session_identity.host_bound_session('claude', {}), '')


if __name__ == '__main__':
    unittest.main()
