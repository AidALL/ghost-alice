#!/usr/bin/env python3
"""The hook runner hands an add-on Stop hook the file that holds this Stop's notice message."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


SHARED = Path(__file__).resolve().parent
sys.path.insert(0, str(SHARED))
import stop_hook_testing as evidence
import test_stop_notice as notice_tests
RUNNER = SHARED / "hook_profile_gate.py"
STOP_SCRIPT = SHARED / "claude_stop_verification_hook.py"
ANSWER = "Plain answer without a completion block."
SESSION = "handoff-fixture"
# The add-on script reports which file the runner named and what that file held when the script ran.
ADDON = ("import json, os; path = os.environ.get('GHOST_ALICE_STOP_NOTICE_FILE'); "
         "body = open(path, encoding='utf-8').read() if path else ''; "
         "print(json.dumps({'continue': True, 'decision': 'block', 'reason': 'after stop', "
         "'systemMessage': json.dumps({'path': path, 'body': body})}))")


class StopNoticeHandoffTests(unittest.TestCase):
    def test_unencodable_partial_answer_reaches_host_addon_and_existing_hook_log(self):
        answer = notice_tests.PARTIAL.replace('the checker is not installed', 'pending \ud800')
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            host = self.core(home, answer=answer)
            self.assertTrue(host['continue'])
            self.assertIn('Ghost-ALICE Stop notice:', host['systemMessage'])
            message = json.loads(self.notice(home).read_text(encoding='utf-8'))
            self.assertIsNone(message['body_digest'])
            self.assertIsNone(message['control'])
            self.assertEqual(message['issues'][0]['code'], 'response-unbound')
            self.assertIn('UTF-8', message['issues'][0]['detail'])
            _, seen = self.addon(home, answer=answer)
            self.assertEqual(json.loads(seen['body']), message)
            rows = [json.loads(line) for line in self.notice(home).with_name('strict-hook-output.jsonl').read_text().splitlines()]
            observed = json.loads(rows[0]['stdout'])
            self.assertEqual(observed['stop_notice'], message)
            self.assertNotIn('\ud800', json.dumps(observed, ensure_ascii=False))

    def run_hook(self, home, hook_id, command, *, event="Stop", platform="codex", answer=ANSWER, inherited=None, ledger=None):
        wrapped = base64.urlsafe_b64encode(command.encode()).decode()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("GHOST_ALICE_") and key not in {"CODEX_THREAD_ID", "CLAUDE_CODE_ENTRYPOINT"}}
        env.update(ledger or {})
        env.update(HOME=str(home), GHOST_ALICE_PLATFORM=platform)
        if inherited is not None:
            env["GHOST_ALICE_STOP_NOTICE_FILE"] = inherited
        result = subprocess.run(
            [sys.executable, "-B", str(RUNNER), "run", hook_id, platform, wrapped],
            input=json.dumps({"session_id": SESSION, "hook_event_name": event, "last_assistant_message": answer}),
            text=True, capture_output=True, env=env, check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def addon(self, home, **options):
        command = f"{sys.executable.replace(chr(92), '/')} -c {shlex.quote(ADDON)}"
        # The runner treats a hook id outside CORE_HOOK_IDS, here "after-stop", as an add-on hook.
        host = self.run_hook(home, "after-stop", command, **options)
        return host, json.loads(host["systemMessage"].split("\n")[0])

    def core(self, home, **options):
        command = f"{sys.executable.replace(chr(92), '/')} -B {shlex.quote(str(STOP_SCRIPT))} --platform codex"
        return self.run_hook(home, "completion", command, **options)

    @staticmethod
    def notice(home, platform="codex"):
        return Path(home) / ".ghost-alice/session-logs" / platform / SESSION / "stop-notice.json"

    def test_addon_stop_hook_reads_this_stops_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            host, seen = self.addon(home)
            self.assertEqual(seen["path"], str(self.notice(home)))
            message = json.loads(seen["body"])
            self.assertEqual(message["schema_version"], "stop-notice.v1")
            self.assertEqual(message["body_digest"], "sha256:" + hashlib.sha256(ANSWER.encode()).hexdigest())
            self.assertIs(host["continue"], True)
            self.assertNotIn("decision", host, "The runner still removes an add-on's block control")

    def test_addon_reuses_the_message_the_core_stop_script_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            core = self.core(home)
            self.assertIs(core["continue"], True)
            recorded = self.notice(home).read_text(encoding="utf-8")
            written = self.notice(home).stat().st_mtime_ns
            _, seen = self.addon(home)
            self.assertEqual(seen["body"], recorded)
            self.assertEqual(self.notice(home).stat().st_mtime_ns, written)

    def test_a_previous_stops_message_is_replaced_before_the_addon_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.addon(home, answer="An earlier answer.")
            _, seen = self.addon(home)
            self.assertEqual(json.loads(seen["body"])["body_digest"],
                             "sha256:" + hashlib.sha256(ANSWER.encode()).hexdigest())

    def test_an_inherited_notice_file_never_reaches_a_hook(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            stale = home / "elsewhere.json"
            stale.write_text("{}", encoding="utf-8")
            _, seen = self.addon(home, event="PreToolUse", inherited=str(stale))
            self.assertEqual((seen["path"], seen["body"]), (None, ""))
            _, seen = self.addon(home, inherited=str(stale))
            self.assertEqual(seen["path"], str(self.notice(home)))

    def test_with_a_recorded_review_the_addon_reads_the_message_the_core_stop_script_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            ledger = {}
            evidence.review_format_fixture(
                home, "codex", notice_tests.COMPLETE, ledger, session_id=SESSION, claim_verdicts=["supported", "unsupported"])
            self.core(home, answer=notice_tests.COMPLETE, ledger=ledger)
            recorded = self.notice(home).read_text(encoding="utf-8")
            written = self.notice(home).stat().st_mtime_ns
            self.assertEqual(json.loads(recorded)["state"], "notice")
            _, seen = self.addon(home, answer=notice_tests.COMPLETE, ledger=ledger)
            self.assertEqual(seen["body"], recorded)
            self.assertEqual(self.notice(home).stat().st_mtime_ns, written)

    def test_a_core_stop_hook_gets_the_path_without_the_runner_building_the_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            probe = ("import json, os; path = os.environ.get('GHOST_ALICE_STOP_NOTICE_FILE'); "
                     "print(json.dumps({'continue': True, 'systemMessage': json.dumps({'path': path, 'exists': os.path.exists(path or '')})}))")
            host = self.run_hook(home, "completion", f"{sys.executable.replace(chr(92), '/')} -c {shlex.quote(probe)}")
            seen = json.loads(host["systemMessage"].split("\n")[0])
            self.assertEqual((seen["path"], seen["exists"]), (str(self.notice(home)), False))

    def test_a_failed_notice_file_is_logged_and_the_addon_runs_without_the_variable(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.notice(home).mkdir(parents=True)
            _, seen = self.addon(home)
            self.assertEqual((seen["path"], seen["body"]), (None, ""))
            log = self.notice(home).parent / "strict-hook-output.jsonl"
            row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
            self.assertIn("IsADirectoryError", row["surface_item"]["stop_notice_failure"])

    def test_other_events_name_no_notice_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            _, seen = self.addon(home, event="PreToolUse")
            self.assertEqual((seen["path"], seen["body"]), (None, ""))
            self.assertFalse(self.notice(home).exists())


if __name__ == "__main__":
    unittest.main()
