"""Private hook receipts must not cross the native host JSON boundary."""
import base64
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hook_profile_gate


class PrivateHookOutputTests(unittest.TestCase):
    def test_real_runner_preserves_receipt_in_audit_and_forced_pending_warning(self):
        shared = Path(__file__).resolve().parent
        for platform in ('codex', 'claude'):
            for profile in ('strict', 'dynamic', 'minimal'):
                for pending in (False, True):
                    with self.subTest(platform=platform, profile=profile, pending=pending), tempfile.TemporaryDirectory() as temporary:
                        home = Path(temporary)
                        manifest = home / '.ghost-alice/pending-merges' / platform / 'manifest.json'
                        manifest.parent.mkdir(parents=True)
                        manifest.write_text(json.dumps({'entries': [{'id': 'fixture', 'decided': False}] if pending else []}))
                        env = {k: v for k, v in os.environ.items() if not k.startswith(('GHOST_ALICE_', 'CODEX_', 'CLAUDE_'))}
                        env.update(HOME=str(home), USERPROFILE=str(home), GHOST_ALICE_PLATFORM=platform,
                                   GHOST_ALICE_AGENT_VISIBILITY=profile)
                        command = shlex.join([sys.executable, str(shared / 'pending_merge_precheck_hook.py'), '--platform', platform,
                                              '--format', 'json', '--root', str(home / 'private-state')])
                        encoded = base64.urlsafe_b64encode(command.encode()).decode()
                        result = subprocess.run([sys.executable, str(shared / 'hook_profile_gate.py'), 'run', 'session-start', encoded],
                                                input=json.dumps({'session_id': 'native-receipt-fixture', 'hook_event_name': 'SessionStart'}),
                                                cwd=home, env=env, capture_output=True, text=True, timeout=20)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        wire = json.loads(result.stdout)
                        self.assertNotIn('ghostAlicePendingMergeCheck', wire)
                        self.assertNotIn('ghostAliceSurface', wire)
                        self.assertTrue(set(wire).issubset({'continue', 'stopReason', 'systemMessage', 'suppressOutput', 'hookSpecificOutput'}))
                        log = home / '.ghost-alice/session-logs' / platform / 'native-receipt-fixture/strict-hook-output.jsonl'
                        row = json.loads(log.read_text().splitlines()[0])
                        raw = json.loads(row['stdout'])
                        self.assertEqual(raw['ghostAlicePendingMergeCheck']['undecided_count'], int(pending))
                        if pending:
                            self.assertEqual(row['visible_decision'], 'force_show')
                            self.assertTrue(wire.get('systemMessage'))

    def test_pending_receipt_removed_for_every_output_surface(self):
        receipt = {"platform": "codex", "manifest_path": "/private/home/.ghost-alice/pending-merges/codex/manifest.json",
                   "undecided_count": 1}
        raw = json.dumps({"continue": True, "ghostAlicePendingMergeCheck": receipt,
                          "systemMessage": "A pending merge requires a decision.",
                          "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "Keep the pending decision."}})
        for level in ("hidden", "compact", "focused", "full", "forced"):
            with self.subTest(level=level):
                rendered, _ = hook_profile_gate._render_user_surface({"user_surface": level, "value_key": "pending-merge"}, raw, "")
                payload = json.loads(rendered)
                self.assertNotIn("ghostAlicePendingMergeCheck", payload)
                self.assertTrue(payload["continue"])
                self.assertEqual(payload["hookSpecificOutput"]["additionalContext"], "Keep the pending decision.")
                self.assertIn("ghostAlicePendingMergeCheck", json.loads(raw))

    def test_internal_pending_check_receipt_still_prevents_duplicate_inspection(self):
        env = {"HOME": "/private/home", "GHOST_ALICE_PLATFORM": "codex"}
        receipt = {"platform": "codex", "manifest_path": "/private/home/.ghost-alice/pending-merges/codex/manifest.json", "undecided_count": 1}
        raw = json.dumps({"continue": True, "ghostAlicePendingMergeCheck": receipt})
        with mock.patch.object(hook_profile_gate, "_has_pending_merge_undecided", side_effect=AssertionError("duplicate manifest inspection")):
            context = hook_profile_gate._visibility_context("session-start", raw, "", 0, env=env, declared="routine")
        self.assertTrue(context["pending_merge_undecided"])
        rendered, _ = hook_profile_gate._render_user_surface({"user_surface": "full"}, raw, "")
        self.assertNotIn("ghostAlicePendingMergeCheck", json.loads(rendered))

    def test_unknown_fields_and_non_json_failures_are_not_silently_removed(self):
        for raw in ('broken JSON', '{"unexpectedControl": false}', '[1,2]'):
            with self.subTest(raw=raw):
                rendered, _ = hook_profile_gate._render_user_surface({"user_surface": "full"}, raw, "")
                self.assertEqual(rendered, raw)


if __name__ == '__main__':
    unittest.main()
