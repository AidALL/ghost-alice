#!/usr/bin/env python3
"""Routine prompt-hook notices stay off the user screen; real state still surfaces.

A hook declares its own routine state with the private `ghostAliceSurface` key. The runner strips the key before the
host sees the output, keeps model context untouched, and forces visibility only from real state: an undecided
pending-merge entry, a current downstream block, a nonzero exit, or a block/deny decision.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hook_profile_gate
import install_hooks
import pending_merge_precheck_hook
import session_intent_analyzer_hook
import task_router_reminder_hook

KEY = "ghostAliceSurface"
CONTEXT = "session-intent-analyzer: model context stays exactly as written. Never persist secrets."


def gate(hook_id, output, *, profile="dynamic", exit_code=0, home=None):
    """Run `output` through the hook runner as a child hook and return what the host receives."""
    code = f"import sys; sys.stdout.write({json.dumps(output)}); sys.exit({exit_code})"
    command = f"{sys.executable.replace(chr(92), '/')} -c {shlex.quote(code)}"
    payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
    with tempfile.TemporaryDirectory() as temp_home:
        home = home or temp_home
        env = {**os.environ, "HOME": home, "GHOST_ALICE_PLATFORM": "claude", "GHOST_ALICE_SESSION_ID": "s-surface",
               "GHOST_ALICE_AGENT_VISIBILITY": profile}
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"session_id": "s-surface", "prompt": "x"}))), \
                mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", io.StringIO()):
            hook_profile_gate.run(hook_id, payload)
    text = stdout.getvalue().strip()
    return json.loads(text) if text.startswith("{") else text


def declared(message, *, context=None):
    body = {"continue": True, "systemMessage": message, KEY: "routine"}
    if context is not None:
        body["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit", "additionalContext": context}
    return json.dumps(body)


class RunnerHonorsDeclarationTests(unittest.TestCase):
    def test_dynamic_profile_hides_a_declared_routine_notice_and_keeps_model_context(self):
        host = gate("session-intent", declared(CONTEXT, context=CONTEXT))
        self.assertNotIn("systemMessage", host)
        self.assertNotIn(KEY, host)
        self.assertEqual(host["hookSpecificOutput"]["additionalContext"], CONTEXT)

    def test_strict_profile_still_shows_the_notice_without_the_private_key(self):
        host = gate("prompt", declared("hook-reminder: routine release"), profile="strict")
        self.assertEqual(host["systemMessage"], "hook-reminder: routine release")
        self.assertNotIn(KEY, host)

    def test_undecided_pending_merge_entry_forces_the_notice_despite_a_routine_declaration(self):
        with tempfile.TemporaryDirectory() as home:
            manifest = Path(home) / ".ghost-alice" / "pending-merges" / "claude" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"entries": [{"skill": "task-router", "decided": False}]}), encoding="utf-8")
            host = gate("pending-merge-prompt", declared("merge-companion prompt-check: clean"), home=home)
        self.assertIn("systemMessage", host)
        self.assertNotIn(KEY, host)

    def test_nonzero_exit_forces_the_notice_despite_a_routine_declaration(self):
        host = gate("prompt", declared("hook-reminder: routine release"), exit_code=3)
        self.assertEqual(host["systemMessage"], "hook-reminder: routine release")

    def test_undeclared_output_keeps_the_previous_classification(self):
        host = gate("session-intent", json.dumps({"continue": True, "systemMessage": CONTEXT}))
        self.assertEqual(host["systemMessage"], CONTEXT)

    def test_focused_notice_does_not_repeat_its_label(self):
        message = "web-search-first: AGENTS.md Rule 10. Cross-check community sources."
        host = gate("web-search-first", json.dumps({"continue": True, "systemMessage": message}))
        self.assertEqual(host["systemMessage"], message)


class HooksDeclareOnlyRoutineStateTests(unittest.TestCase):
    def test_pending_merge_check_declares_routine_only_when_clean(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            clean = json.loads(pending_merge_precheck_hook._json_payload("claude", "prompt-check"))
            manifest = Path(home) / ".ghost-alice" / "pending-merges" / "claude" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"entries": [{"skill": "x", "decided": False}]}), encoding="utf-8")
            pending = json.loads(pending_merge_precheck_hook._json_payload("claude", "prompt-check"))
        self.assertEqual(clean.get(KEY), "routine")
        self.assertNotIn(KEY, pending)

    def test_task_router_reminder_declares_routine_only_on_release(self):
        released = json.loads(task_router_reminder_hook.render_payload("json", "hook-reminder: released", routine=True))
        withheld = json.loads(task_router_reminder_hook.render_payload("json", "hook-reminder: withheld"))
        self.assertEqual(released.get(KEY), "routine")
        self.assertNotIn(KEY, withheld)
        with tempfile.TemporaryDirectory() as root:
            message, routine = task_router_reminder_hook.reminder_result("base", Path(root), "codex", {})
        self.assertIn("withheld", message)
        self.assertFalse(routine)

    def test_session_intent_declares_routine_only_after_a_recorded_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "ledger"
            ok = io.StringIO()
            with mock.patch.object(session_intent_analyzer_hook, "read_payload",
                                   return_value={"session_id": "s-ok", "prompt": "current"}), \
                    mock.patch.object(session_intent_analyzer_hook.sys, "stdout", ok):
                session_intent_analyzer_hook.main(["--root", str(root), "--format", "json"])
            root.parent.mkdir(parents=True, exist_ok=True)
            broken = Path(temp) / "broken"
            broken.write_text("not a directory", encoding="utf-8")
            failed = io.StringIO()
            with mock.patch.object(session_intent_analyzer_hook, "read_payload",
                                   return_value={"session_id": "s-bad", "prompt": "current"}), \
                    mock.patch.object(session_intent_analyzer_hook.sys, "stdout", failed):
                session_intent_analyzer_hook.main(["--root", str(broken), "--format", "json"])
        recorded, not_recorded = json.loads(ok.getvalue()), json.loads(failed.getvalue())
        self.assertEqual(recorded.get(KEY), "routine")
        self.assertEqual(recorded["hookSpecificOutput"]["additionalContext"], recorded["systemMessage"])
        self.assertNotIn(KEY, not_recorded)

    def test_web_search_first_declares_routine(self):
        command = install_hooks._web_search_first_command(output_format="json")
        out = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out).get(KEY), "routine")


if __name__ == "__main__":
    unittest.main()
