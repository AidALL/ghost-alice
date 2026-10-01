#!/usr/bin/env python3
"""Tests for task_router_reminder_hook freshness fail-closed behavior."""

from __future__ import annotations

import json
import base64
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task_router_reminder_hook as trh


class DegradedLedgerFailClosedTests(unittest.TestCase):
    'H5: a degraded-ledger marker must withhold routing release instead of\n    letting the silent-allow invariant ride a stale lineage anchor.'

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.platform = "codex"
        self.session = "s-run"
        self.payload = {"session_id": self.session}
        self.session_dir = trh.session_dir(self.root, self.platform, self.session)
        self.session_dir.mkdir(parents=True)
        (self.session_dir / 'intent-state.json').write_text(json.dumps({
            'schema_version': 'session-intent-ledger.v1',
            'platform': self.platform, 'session_id': self.session,
        }))
        # A prior turn's observed event: the stale anchor.
        (self.session_dir / "intent-events.jsonl").write_text(
            json.dumps({
                "event": "user-input-observed",
                "platform": self.platform,
                "session_id": self.session,
                "event_id": "evt-old",
                "input_digest": "sha256:old",
            }) + "\n",
            encoding="utf-8",
        )

    def test_degrade_marker_withholds_routing(self) -> None:
        (self.session_dir / "ledger-degraded.json").write_text(
            '{"schema_version": "session-intent-degrade.v1", "reason": "ledger-broken"}\n',
            encoding="utf-8",
        )
        message = trh.reminder_message("base", self.root, self.platform, self.payload)
        self.assertIn("withheld", message)
        self.assertIn("ledger is degraded", message)
        self.assertIn("ledger-broken", message)
        self.assertNotIn("silent allow", message)

    def test_without_marker_stale_anchor_still_releases_as_before(self) -> None:
        # Control: pre-fix behavior preserved when no degrade marker exists.
        message = trh.reminder_message("base", self.root, self.platform, self.payload)
        self.assertIn("silent allow", message)

    def test_unreadable_marker_still_withholds(self) -> None:
        (self.session_dir / "ledger-degraded.json").write_text("{broken", encoding="utf-8")
        message = trh.reminder_message("base", self.root, self.platform, self.payload)
        self.assertIn("withheld", message)
        self.assertIn("unreadable-marker", message)

    def test_native_identity_degrade_marker_withholds_despite_foreign_pointer(self) -> None:
        import session_intent_analyzer_hook as analyzer
        pointer = self.root / "codex/current-session.json"
        pointer.write_text(json.dumps({"schema_version": "session-intent-current.v1", "session_id": "foreign"}))
        payload = {"prompt": "native input without payload session id"}
        with patch.dict(os.environ, {"CODEX_THREAD_ID": self.session, "GHOST_ALICE_SESSION_ID": "stale"}):
            analyzer._write_degrade_marker(self.root, "codex", payload, "ledger-write-failed")
            self.assertTrue((self.session_dir / "ledger-degraded.json").exists())
            message = trh.reminder_message("base", self.root, "codex", payload)
        self.assertIn("ledger is degraded", message)
        self.assertNotIn("silent allow", message)

    def test_producer_marker_path_matches_consumer_lookup(self) -> None:
        # Cross-module seam: the analyzer hook WRITES the marker with its own session-key derivation; this consumer LOOKS IT UP with resolve_session_id. If the two ever diverge, fail-closed silently stops working — the exact drift class that produced the N2 intent-root divergence.
        import shutil
        import subprocess
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        home = base / "home"
        home.mkdir()
        broken = home / ".claude" / "skills" / "session-intent-analyzer" / "scripts"
        broken.mkdir(parents=True)
        (broken / "session_intent_ledger.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        shared = base / "_shared"
        shared.mkdir()
        analyzer_src = Path(trh.__file__).resolve().with_name("session_intent_analyzer_hook.py")
        shutil.copy2(analyzer_src, shared / "session_intent_analyzer_hook.py")
        root = base / "root"
        payload = {"session_id": "s-xchain", "prompt": "hello"}
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("GHOST_ALICE_SESSION_ID", None)
        proc = subprocess.run(
            [sys.executable, str(shared / "session_intent_analyzer_hook.py"),
             "--platform", "codex", "--format", "json", "--root", str(root)],
            input=json.dumps(payload), capture_output=True, text=True, env=env, check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        message = trh.reminder_message("base", root, "codex", payload)
        self.assertIn("withheld", message)
        self.assertIn("ledger is degraded", message)

    def test_producer_marker_path_matches_consumer_lookup_with_equals_session_id(self) -> None:
        # Session ids may contain platform-produced separators. Producer and consumer sanitizers must agree, or the degraded-ledger marker is written under one directory and looked up under another.
        import shutil
        import subprocess
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        home = base / "home"
        home.mkdir()
        broken = home / ".claude" / "skills" / "session-intent-analyzer" / "scripts"
        broken.mkdir(parents=True)
        (broken / "session_intent_ledger.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        shared = base / "_shared"
        shared.mkdir()
        analyzer_src = Path(trh.__file__).resolve().with_name("session_intent_analyzer_hook.py")
        shutil.copy2(analyzer_src, shared / "session_intent_analyzer_hook.py")
        root = base / "root"
        payload = {"session_id": "s=eq", "prompt": "hello"}
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("GHOST_ALICE_SESSION_ID", None)
        proc = subprocess.run(
            [sys.executable, str(shared / "session_intent_analyzer_hook.py"),
             "--platform", "codex", "--format", "json", "--root", str(root)],
            input=json.dumps(payload), capture_output=True, text=True, env=env, check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        message = trh.reminder_message("base", root, "codex", payload)
        self.assertIn("withheld", message)
        self.assertIn("ledger is degraded", message)


class CurrentSessionReaderTests(unittest.TestCase):
    """Current routing follows one exact identity and committed input anchor."""

    def test_invalid_state_with_old_gate_still_withholds_routing(self):
        self.state['session_id'] = 'foreign'
        self.save_state()
        (self.directory / 'downstream-gates.json').write_text(json.dumps({
            'schema_version': 'downstream-gates.v1', 'gate': 'jailbreak-detector',
            'platform': 'codex', 'session_id': 'bound-session',
            'decision': 'block', 'opened': False, 'input_event_id': 'new-input',
        }))
        message = self.message()
        self.assertIn('withheld', message)
        self.assertNotIn('Continue intake/routing', message)

    def test_degraded_intake_does_not_expose_previous_input_as_current(self):
        (self.directory / 'intent-state.json').write_text(json.dumps(self.state))
        (self.directory / 'ledger-degraded.json').write_text('{"reason":"new input write failed"}')
        material = trh.session_material(self.root, 'codex', 'bound-session')
        self.assertEqual(material['latest_input'], {})
        self.assertTrue(material['degraded'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / 'codex' / 'bound-session'
        self.directory.mkdir(parents=True)
        self.state = {
            'schema_version': 'session-intent-ledger.v1',
            'platform': 'codex', 'session_id': 'bound-session',
            'ledger_revision': 2, 'latest_input_event_id': 'new-input',
            'latest_input_digest': 'sha256:new', 'latest_input_char_count': 4,
        }
        self.save_state()

    def save_state(self):
        (self.directory / 'intent-state.json').write_text(json.dumps(self.state))

    def message(self, payload=None):
        with patch.dict(os.environ, {'CODEX_THREAD_ID': '', 'GHOST_ALICE_SESSION_ID': ''}):
            return trh.reminder_message('base', self.root, 'codex',
                                        {'session_id': 'bound-session'} if payload is None else payload)

    def test_shared_pointer_cannot_select_a_current_session(self):
        (self.root / 'codex/current-session.json').write_text(json.dumps({
            'schema_version': 'session-intent-current.v1', 'session_id': 'bound-session'}))
        (self.directory / 'intent-events.jsonl').write_text(json.dumps({
            'event': 'user-input-observed', 'event_id': 'new-input',
            'platform': 'codex', 'session_id': 'bound-session'}) + '\n')
        self.assertIn('withheld', self.message({}))
        self.assertNotIn('gate-opened', self.message({}))

    def test_invalid_identity_is_not_aliased_to_an_existing_session(self):
        (self.directory / 'intent-events.jsonl').write_text(json.dumps({
            'event': 'user-input-observed', 'event_id': 'new-input'}) + '\n')
        self.assertIn('withheld', self.message({'session_id': 'bound/session'}))

    def test_atomic_state_anchor_works_before_audit_projection_is_visible(self):
        self.assertIn('gate-opened', self.message())

    def test_old_audit_cannot_make_a_current_block_stale(self):
        (self.directory / 'intent-events.jsonl').write_text(json.dumps({
            'event': 'user-input-observed', 'event_id': 'old-input',
            'platform': 'codex', 'session_id': 'bound-session'}) + '\n')
        (self.directory / 'downstream-gates.json').write_text(json.dumps({
            'schema_version': 'downstream-gates.v1', 'gate': 'jailbreak-detector',
            'platform': 'codex', 'session_id': 'bound-session',
            'decision': 'block', 'opened': False, 'input_event_id': 'new-input',
        }))
        self.assertIn('current-lineage block', self.message())

    def test_foreign_state_or_legacy_event_is_not_current_preflight(self):
        self.state['session_id'] = 'foreign'
        self.save_state()
        (self.directory / 'intent-events.jsonl').write_text(json.dumps({
            'event': 'user-input-observed', 'event_id': 'new-input',
            'platform': 'codex', 'session_id': 'bound-session'}) + '\n')
        self.assertNotIn('gate-opened', self.message())
        (self.directory / 'intent-state.json').unlink()
        (self.directory / 'intent-events.jsonl').write_text(json.dumps({
            'event': 'user-input-observed', 'event_id': 'new-input',
            'platform': 'claude', 'session_id': 'foreign'}) + '\n')
        self.assertNotIn('gate-opened', self.message())

    def test_pointer_path_cannot_redirect_bound_context(self):
        (self.root / 'codex/current-session.json').write_text(json.dumps({
            'schema_version': 'session-intent-current.v1', 'session_id': 'bound-session',
            'state_path': '/unrelated/foreign/intent-state.json'}))
        self.assertEqual(trh.ledger_state_path(self.root, 'codex', 'bound-session'),
                         str(self.directory / 'intent-state.json'))

    def test_missing_identity_cannot_write_foreign_degrade_marker(self):
        import session_intent_analyzer_hook as analyzer
        (self.root / 'codex/current-session.json').write_text(json.dumps({
            'schema_version': 'session-intent-current.v1', 'session_id': 'bound-session'}))
        with patch.dict(os.environ, {'CODEX_THREAD_ID': '', 'GHOST_ALICE_SESSION_ID': ''}):
            analyzer._write_degrade_marker(self.root, 'codex', {}, 'identity-missing')
        self.assertFalse((self.directory / 'ledger-degraded.json').exists())

    def test_known_current_model_block_is_not_reported_as_absent(self):
        self.state['model_security_decision'] = {'decision': 'block', 'input_event_id': 'new-input'}
        self.save_state()
        self.assertIn('current-lineage block', self.message())

    def test_released_context_preserves_admitted_unfinished_work(self):
        self.state['acceptance_criteria'] = [
            {'id': 'runtime-fix', 'admitted': True, 'status': 'unmet'},
            {'id': 'already-done', 'admitted': True, 'status': 'met'},
            {'id': 'proposal-only', 'admitted': False, 'status': 'unmet'},
        ]
        self.save_state()
        message = self.message()
        self.assertIn('unresolved-work: ["runtime-fix"]', message)
        self.assertNotIn('already-done', message)
        self.assertNotIn('proposal-only', message)
        self.assertIn('Reconcile the current input', message)
        self.assertIn('commentary', message)
        self.assertIn('does not authorize', message)

    def test_completed_or_unadmitted_criteria_do_not_start_work(self):
        self.state['acceptance_criteria'] = [
            {'id': 'done', 'admitted': True, 'status': 'met'},
            {'id': 'draft', 'admitted': False, 'status': 'unmet'},
        ]
        self.save_state()
        self.assertNotIn('unresolved-work:', self.message())

    def test_pending_work_never_overrides_a_current_block(self):
        self.state['acceptance_criteria'] = [
            {'id': 'runtime-fix', 'admitted': True, 'status': 'unmet'},
        ]
        self.state['model_security_decision'] = {'decision': 'block', 'input_event_id': 'new-input'}
        self.save_state()
        message = self.message()
        self.assertIn('withheld', message)
        self.assertNotIn('unresolved-work:', message)

    def test_identity_helper_matches_writer_identity_selection(self):
        import session_intent_analyzer_hook as analyzer
        for platform in ('codex', 'claude', 'agent-runtime'):
            for identity in ('valid=1', 'a'*120, 'a'*121, 'a/b', ' spaced ', '.prefix', 'suffix.', '', None, 5):
                payload = {'session_id': identity}
                env = {'CODEX_THREAD_ID': 'native', 'GHOST_ALICE_SESSION_ID': 'generic'}
                with self.subTest(platform=platform, identity=identity):
                    try:
                        writer = analyzer.resolve_session_id(root=self.root, platform=platform,
                                                             payload=payload, env=env, for_write=True)
                    except ValueError:
                        writer = None
                    self.assertEqual(analyzer.bound_session_identity(platform, payload, env), writer)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for cross-language reader parity')
    def test_python_and_javascript_share_input_lineage_rules(self):
        module = Path(trh.__file__).with_name('derive_downstream_gate.mjs').as_uri()
        script = f'''import {{readSessionIntentSnapshot, inputLineageMatches}} from {json.dumps(module)};
const [directory, records] = JSON.parse(process.argv[1]);
const snapshot = readSessionIntentSnapshot(directory, 'codex', 'bound-session');
console.log(JSON.stringify({{event:snapshot?.latestInput?.event_id || '', matches:records.map(r=>inputLineageMatches(r,snapshot?.latestInput))}}));'''
        legacy = {k: self.state[k] for k in ('schema_version', 'platform', 'session_id')}
        own_event = {'event': 'user-input-observed', 'event_id': 'new-input',
                     'input_digest': 'sha256:new', 'platform': 'codex', 'session_id': 'bound-session'}
        records = [
            {'input_event_id': 'new-input'}, {'input_event_id': 'old-input', 'input_digest': 'sha256:new'},
            {'input_digest': 'sha256:new'}, {'input_event_id': 'new-input', 'input_digest': 'sha256:wrong'},
            {},
        ]
        fixtures = [
            ('state-before-audit', self.state, []),
            ('old-audit', self.state, [dict(own_event, event_id='old-input')]),
            ('revision-only', dict(legacy, ledger_revision=2), [own_event]),
            ('missing-count', {k:v for k,v in self.state.items() if k != 'latest_input_char_count'}, [own_event]),
            ('foreign-state', dict(self.state, session_id='foreign'), [own_event]),
            ('legacy', legacy, [own_event]),
            ('legacy-digest', legacy, [{k:v for k,v in own_event.items() if k != 'event_id'}]),
            ('legacy-id-only', legacy, [{k:v for k,v in own_event.items() if k != 'input_digest'}]),
            ('unsafe-revision', dict(self.state, ledger_revision=2**53), [own_event]),
            ('unsafe-count', dict(self.state, latest_input_char_count=2**53), [own_event]),
            ('foreign-tail', legacy, [own_event, dict(own_event, session_id='foreign')]),
        ]
        for name, state, rows in fixtures:
            with self.subTest(fixture=name):
                (self.directory / 'intent-state.json').write_text(json.dumps(state))
                (self.directory / 'intent-events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
                observed = subprocess.run(['node', '--input-type=module', '-e', script,
                                           json.dumps([str(self.directory), records])],
                                          capture_output=True, text=True, check=True)
                js = json.loads(observed.stdout)
                py_input = trh.latest_intent_event(self.root, 'codex', 'bound-session')
                self.assertEqual(py_input.get('event_id', ''), js['event'])
                self.assertEqual([trh.downstream_gate_matches_latest_event(r, py_input)['ok'] for r in records], js['matches'])


class RoutingDeliveryTests(unittest.TestCase):
    """Exercise authoritative storage and both platform wire formats, not only a policy string."""

    def test_ledger_hint_reads_exact_session_from_unrelated_cwd(self):
        import install_hooks
        ledger = trh.importlib.import_module('session_intent_ledger')
        shared = Path(trh.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'ledger root with spaces'
            unrelated = Path(temporary) / 'unrelated cwd'
            unrelated.mkdir()
            env = dict(os.environ)
            env.pop('CODEX_THREAD_ID', None)
            env.pop('GHOST_ALICE_SESSION_ID', None)
            for platform in ('codex', 'claude'):
                session_id = f'{platform}-hint-session'
                ledger.record_turn(
                    root=root, platform=platform, session_id=session_id,
                    raw_user_input='status question',
                    intent_delta={'current_goal': f'{platform} active work'},
                )
                with patch.object(install_hooks, '_session_intent_root', return_value=root), \
                     patch.object(install_hooks, '_hook_shared_dir', return_value=shared):
                    native_command = install_hooks._entry_command(
                        install_hooks._platform_hook_entry(platform, 'UserPromptSubmit'))
                self.assertIn('hook_profile_gate.py', native_command)
                for profile in ('strict', 'dynamic', 'minimal'):
                    with self.subTest(platform=platform, profile=profile):
                        home = Path(temporary) / platform / profile
                        home.mkdir(parents=True)
                        profile_env = dict(env, HOME=str(home), USERPROFILE=str(home),
                                           GHOST_ALICE_AGENT_VISIBILITY=profile)
                        result = subprocess.run(
                            native_command, shell=True,
                            input=json.dumps({'session_id': session_id,
                                              'hook_event_name': 'UserPromptSubmit'}),
                            cwd=unrelated, text=True, capture_output=True,
                            env=profile_env, check=True,
                        )
                        host = json.loads(result.stdout)
                        self.assertTrue(host['continue'])
                        context = host['hookSpecificOutput']['additionalContext']
                        command = context.split('intent-ledger: use ', 1)[1].split(
                            ' after session-intent preflight.', 1)[0]
                        args = shlex.split(command)
                        self.assertTrue(Path(args[0]).is_absolute(), command)
                        self.assertTrue(Path(args[0]).is_file(), command)
                        self.assertTrue(Path(args[1]).is_absolute(), command)
                        self.assertTrue(Path(args[1]).is_file(), command)
                        observed = subprocess.run(
                            args, cwd=unrelated, text=True, capture_output=True,
                            env=profile_env, check=True,
                        )
                        state = json.loads(observed.stdout)
                        self.assertEqual(platform, state['platform'])
                        self.assertEqual(session_id, state['session_id'])
                        self.assertEqual(f'{platform} active work', state['current_goal'])

    def test_unfinished_criteria_reach_platform_context_from_sqlite(self):
        ledger = trh.importlib.import_module('session_intent_ledger')
        script = Path(trh.__file__)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for platform in ('codex', 'claude'):
                with self.subTest(platform=platform):
                    ledger.record_turn(
                        root=root, platform=platform, session_id='active-work',
                        raw_user_input='status question',
                        intent_delta={'acceptance_criteria': [{
                            'id': 'order-fix', 'summary': 'Preserve first-seen order.',
                            'source': 'user-explicit', 'admitted': True, 'status': 'unmet',
                        }]},
                    )
                    # Once SQLite owns the state, an old export cannot erase the work.
                    exported = trh.session_dir(root, platform, 'active-work') / 'intent-state.json'
                    exported.parent.mkdir(parents=True, exist_ok=True)
                    exported.write_text('{"acceptance_criteria": []}', encoding='utf-8')
                    env = dict(os.environ)
                    env.pop('CODEX_THREAD_ID', None)
                    env.pop('GHOST_ALICE_SESSION_ID', None)
                    result = subprocess.run(
                        [sys.executable, str(script), '--platform', platform,
                         '--format', 'json' if platform == 'codex' else 'text',
                         '--root', str(root)],
                        input=json.dumps({'session_id': 'active-work'}),
                        text=True, capture_output=True, env=env, check=True,
                    )
                    if platform == 'codex':
                        payload = json.loads(result.stdout)
                        self.assertTrue(payload['continue'])
                        context = payload['systemMessage']
                    else:
                        context = result.stdout
                    self.assertIn('unresolved-work: ["order-fix"]', context)
                    self.assertIn('Respect an explicit stop', context)

    def test_installer_reminder_keeps_model_context_through_visibility_wrapper(self):
        import install_hooks
        ledger = trh.importlib.import_module('session_intent_ledger')
        shared = Path(trh.__file__).parent
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'intent'
            for platform in ('codex', 'claude'):
                ledger.record_turn(
                    root=root, platform=platform, session_id='active-work',
                    raw_user_input='status question',
                    intent_delta={'acceptance_criteria': [{
                        'id': 'order-fix', 'summary': 'Preserve first-seen order.',
                        'source': 'user-explicit', 'admitted': True, 'status': 'unmet',
                    }]},
                )
                with patch.object(install_hooks, '_session_intent_root', return_value=root), \
                     patch.object(install_hooks, '_resolve_task_router_reminder_hook_script',
                                  return_value=str(shared / 'task_router_reminder_hook.py')):
                    command = install_hooks._hook_reminder_command(
                        platform=platform, output_format='json', payload_mode=True)
                encoded = base64.urlsafe_b64encode(command.encode()).decode()
                for profile in ('strict', 'dynamic', 'minimal'):
                    with self.subTest(platform=platform, profile=profile):
                        home = Path(temporary) / platform / profile
                        home.mkdir(parents=True)
                        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                                   GHOST_ALICE_AGENT_VISIBILITY=profile)
                        env.pop('CODEX_THREAD_ID', None)
                        env.pop('GHOST_ALICE_SESSION_ID', None)
                        result = subprocess.run(
                            [sys.executable, str(shared / 'hook_profile_gate.py'),
                             'run', 'prompt', platform, encoded],
                            input=json.dumps({'session_id': 'active-work',
                                              'hook_event_name': 'UserPromptSubmit'}),
                            text=True, capture_output=True, env=env, check=True,
                        )
                        host = json.loads(result.stdout)
                        self.assertTrue(host['continue'])
                        context = host['hookSpecificOutput']['additionalContext']
                        self.assertIn('unresolved-work: ["order-fix"]', context)
                        self.assertIn('task-router consumes session-intent', context)
                        if profile != 'strict':
                            self.assertNotIn('systemMessage', host)


if __name__ == "__main__":
    unittest.main(verbosity=2)
