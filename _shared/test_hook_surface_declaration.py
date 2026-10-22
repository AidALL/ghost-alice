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
        host = gate("session-intent", declared("session-intent-analyzer: routine notice"), profile="strict")
        self.assertEqual(host["systemMessage"], "session-intent-analyzer: routine notice")
        self.assertNotIn(KEY, host)

    def test_undecided_pending_merge_entry_forces_only_its_owner_notice(self):
        with tempfile.TemporaryDirectory() as home:
            manifest = Path(home) / ".ghost-alice" / "pending-merges" / "claude" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"entries": [{"skill": "task-router", "decided": False}]}), encoding="utf-8")
            for hook_id in ("session-intent", "completion", "session-start"):
                with self.subTest(hook_id=hook_id):
                    host = gate(hook_id, declared("routine notice", context=CONTEXT), home=home)
                    self.assertEqual("systemMessage" in host, hook_id == "session-start")
                    self.assertEqual(host["hookSpecificOutput"]["additionalContext"], CONTEXT)
                    self.assertNotIn(KEY, host)

    def test_nonzero_exit_forces_the_notice_despite_a_routine_declaration(self):
        host = gate("session-intent", declared("session-intent-analyzer: routine notice"), exit_code=3)
        self.assertEqual(host["systemMessage"], "session-intent-analyzer: routine notice")

    def test_undeclared_output_keeps_the_previous_classification(self):
        host = gate("session-intent", json.dumps({"continue": True, "systemMessage": CONTEXT}))
        self.assertEqual(host["systemMessage"], CONTEXT)

    def test_focused_notice_does_not_repeat_its_label(self):
        message = "io-trace: the tool call was recorded."
        host = gate("io-trace", json.dumps({"continue": True, "systemMessage": message}))
        self.assertEqual(host["systemMessage"], message)


class HooksDeclareOnlyRoutineStateTests(unittest.TestCase):
    def test_pending_merge_check_declares_routine_only_when_clean(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            clean = json.loads(pending_merge_precheck_hook._json_payload("claude"))
            manifest = Path(home) / ".ghost-alice" / "pending-merges" / "claude" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"entries": [{"skill": "x", "decided": False}]}), encoding="utf-8")
            pending = json.loads(pending_merge_precheck_hook._json_payload("claude"))
        self.assertEqual(clean.get(KEY), "routine")
        self.assertNotIn(KEY, pending)

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

    def test_installed_intake_keeps_context_without_retired_search_notice(self):
        import install_destination

        source_shared = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = install_destination.InstallDestination(
                mode="normal", owned_root=str(root), platform_dir=str(root / "platform"),
                skills_dir=str(root / "skills"), ghost_root=str(root / "ghost"),
                ledger_root=str(root / "ledger"), project_root=str(source_shared.parent),
                core_source_root=str(source_shared.parent), runtime_shared_dir=str(source_shared))
            with install_destination.use(destination):
                command = install_hooks._entry_command(
                    install_hooks._platform_session_intent_entry("codex", "UserPromptSubmit"))
            env = install_destination.binding_environment(destination, {
                **os.environ, "HOME": temp, "GHOST_ALICE_AGENT_VISIBILITY": "dynamic"})
            out = subprocess.run(
                shlex.split(command, comments=True),
                input=json.dumps({"session_id": "s-installed-surface", "prompt": "current"}),
                env=env, capture_output=True, text=True, check=True).stdout
            self.assertTrue((Path(destination.ledger_root) / "ghost-state.sqlite3").is_file())
        payload = json.loads(out)
        self.assertNotIn("systemMessage", payload)
        self.assertNotIn(KEY, payload)
        context = payload["hookSpecificOutput"]["additionalContext"]
        receipt = json.loads(context.split("[session-intent-receipt]\n", 1)[1]
                             .split("\n[/session-intent-receipt]", 1)[0])
        self.assertEqual(receipt["intake_status"], "observed")
        self.assertEqual(receipt["session_id"], "s-installed-surface")
        self.assertEqual(Path(receipt["ledger_root"]), Path(destination.ledger_root).resolve())
        self.assertNotIn("web-search-first", out)


if __name__ == "__main__":
    unittest.main()
