#!/usr/bin/env python3
"""Tests for Ghost-ALICE strict hook output session logs."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import file_lock
import strict_session_log


class TestStrictSessionLogPath(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)
        environment = mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(self.home / ".ghost-alice")})
        environment.start()
        self.addCleanup(environment.stop)

    def test_log_path_uses_platform_and_session_id(self):
        path = strict_session_log.log_path(
            home=self.home,
            platform="codex",
            session_id="s-123",
        )

        self.assertEqual(
            path,
            self.home
            / ".ghost-alice"
            / "session-logs"
            / "codex"
            / "s-123"
            / "strict-hook-output.jsonl",
        )

    def test_session_id_from_payload_uses_safe_component(self):
        session_id = strict_session_log.session_id_from_payload({"session_id": "../s 123"})

        self.assertEqual(session_id, "s-123")

    def test_session_id_from_payload_reads_every_host_session_key(self):
        self.assertEqual(strict_session_log.session_id_from_payload({"threadId": "thread=1"}), "thread-1")
        self.assertEqual(strict_session_log.session_id_from_payload({"conversation_id": "c-1"}), "c-1")

    def test_native_codex_log_identity_preserves_payload_and_platform_precedence(self):
        env = {"GHOST_ALICE_PLATFORM": "codex", "CODEX_THREAD_ID": "native", "GHOST_ALICE_SESSION_ID": "generic"}
        self.assertEqual(strict_session_log.session_id_from_payload({}, env), "native")
        self.assertEqual(strict_session_log.session_id_from_payload({"session_id": "payload"}, env), "payload")
        self.assertEqual(strict_session_log.session_id_from_payload({"thread_id": "thread"}, env), "thread")
        self.assertEqual(strict_session_log.session_id_from_payload({}, {**env, "GHOST_ALICE_PLATFORM": "claude"}), "generic")
        without_platform = {key: value for key, value in env.items() if key != "GHOST_ALICE_PLATFORM"}
        self.assertEqual(strict_session_log.session_id_from_payload({}, without_platform, platform="codex"), "native")
        self.assertEqual(strict_session_log.session_id_from_payload({}, without_platform, platform="claude"), "generic")

    def test_append_event_records_digest_without_raw_payload(self):
        path = strict_session_log.append_event(
            home=self.home,
            platform="codex",
            session_id="s-123",
            event={
                "hook_id": "prompt",
                "event": "UserPromptSubmit",
                "stdin": "raw user prompt text",
                "stdout": "hook-reminder",
                "stderr": "",
                "exit_code": 0,
                "agent_visibility_profile": "dynamic",
                "visible_decision": "show",
                "observed_duration_s": 1.236,
                "observed_duration_source": "hook-runner",
            },
        )

        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        serialized = json.dumps(row, ensure_ascii=False)
        self.assertNotIn("stdin", row)
        self.assertNotIn("raw user prompt text", serialized)
        self.assertEqual(row["schema_version"], "strict-hook-output.v1")
        self.assertEqual(row["platform"], "codex")
        self.assertEqual(row["session_id"], "s-123")
        self.assertEqual(row["hook_id"], "prompt")
        self.assertEqual(row["stdout"], "hook-reminder")
        self.assertEqual(row["stderr"], "")
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["agent_visibility_profile"], "dynamic")
        self.assertEqual(row["observed_duration_s"], 1.24)
        self.assertEqual(row["observed_duration_source"], "hook-runner")
        self.assertNotIn("reasoning_duration_s", row)
        self.assertNotIn("semantic_quality", row)
        self.assertNotIn("profile", row)
        self.assertNotIn("ui_exposure_enabled", row)
        self.assertEqual(row["visible_decision"], "show")
        self.assertRegex(row["payload_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_append_event_handles_surrogate_payload_text(self):
        path = strict_session_log.append_event(
            home=self.home,
            platform="claude",
            session_id="s-win",
            event={
                "hook_id": "stop",
                "event": "Stop",
                "stdin": "payload with bad surrogate \udcec",
                "stdout": "visible text \udcec",
                "stderr": "traceback text \udcec",
                "exit_code": 2,
                "agent_visibility_profile": "strict",
                "visible_decision": "show",
            },
        )

        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertRegex(row["payload_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(row["stdout"], "visible text \\udcec")
        self.assertEqual(row["stderr"], "traceback text \\udcec")

    def test_append_event_normalizes_unknown_visible_decision_to_show(self):
        path = strict_session_log.append_event(
            home=self.home,
            platform="codex",
            session_id="s-123",
            event={
                "hook_id": "prompt",
                "stdout": "hook-reminder",
                "visible_decision": "rewritten",
            },
        )

        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["visible_decision"], "show")

    def test_append_event_defaults_unknown_agent_visibility_to_strict(self):
        path = strict_session_log.append_event(
            home=self.home,
            platform="codex",
            session_id="s-123",
            event={
                "hook_id": "prompt",
                "stdout": "hook-reminder",
                "agent_visibility_profile": "quiet",
            },
        )

        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["agent_visibility_profile"], "strict")

    def test_append_event_preserves_work_impact_projection_fields(self):
        path = strict_session_log.append_event(
            home=self.home,
            platform="codex",
            session_id="s-123",
            event={
                "hook_id": "prompt",
                "stdout": "routine clean pass already persisted",
                "agent_visibility_profile": "dynamic",
                "visible_decision": "hide",
                "surface_item": {
                    "source_hook": "prompt",
                    "value_key": "merge-precheck",
                    "value_kind": "routine",
                    "exposure_class": "routine",
                    "work_impact": "routine-noise",
                    "value": "routine clean pass already persisted",
                    "user_surface": "hidden",
                    "model_surface": "omitted",
                    "strict_log_ref": "strict-hook-output.jsonl#1",
                },
                "model_surface_output": "",
            },
        )

        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["surface_item"]["value_key"], "merge-precheck")
        self.assertEqual(row["surface_item"]["work_impact"], "routine-noise")
        self.assertEqual(row["surface_item"]["model_surface"], "omitted")
        self.assertEqual(row["model_surface_output"], "")


class TestSessionLogRetention(unittest.TestCase):
    """The first row of a new session removes session folders whose log is older than 14 days."""

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.home = Path(temp_dir.name)
        environment = mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(self.home / ".ghost-alice")})
        environment.start()
        self.addCleanup(environment.stop)

    def old_session(self, platform: str, session_id: str, days: int) -> Path:
        path = strict_session_log.append_event(self.home, platform, session_id, {"hook_id": "prompt"})
        moment = time.time() - days * 86400
        for item in (path, path.parent):
            os.utime(item, (moment, moment))
        return path.parent

    def test_first_row_of_a_new_session_removes_old_folders_of_every_host(self):
        old_codex = self.old_session("codex", "old-codex", 20)
        old_unknown = self.old_session("unknown", "old-unknown", 40)
        recent = self.old_session("claude", "recent", 2)
        path = strict_session_log.append_event(self.home, "claude", "new", {"hook_id": "prompt"})
        self.assertFalse(old_codex.exists())
        self.assertFalse(old_unknown.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(path.exists())

    def test_a_later_row_of_an_existing_session_removes_nothing(self):
        strict_session_log.append_event(self.home, "claude", "running", {"hook_id": "prompt"})
        old = self.old_session("codex", "old-codex", 20)
        strict_session_log.append_event(self.home, "claude", "running", {"hook_id": "stop"})
        self.assertTrue(old.exists())

    def test_a_removal_failure_keeps_the_row_and_is_recorded(self):
        self.old_session("codex", "old-codex", 20)
        with mock.patch("state_retention._remove", side_effect=PermissionError("denied")):
            path = strict_session_log.append_event(self.home, "claude", "new", {"hook_id": "prompt"})
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
        failures = (self.home / ".ghost-alice" / "state-retention-failures.jsonl").read_text(encoding="utf-8")
        self.assertIn('"policy": "session-logs"', failures)


class StrictSessionLogLockTests(unittest.TestCase):
    """Hooks of one session append to one log; each row is one write under the log's lock."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)
        environment = mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(self.home / ".ghost-alice")})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("GHOST_ALICE_OWNED_ROOT", None)
        self.path = strict_session_log.log_path(home=self.home, platform="claude", session_id="locked")
        self.path.parent.mkdir(parents=True)

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_row_waits_for_the_log_lock(self):
        written = threading.Event()

        def writer():
            strict_session_log.append_event(self.home, "claude", "locked", {"hook_id": "prompt"})
            written.set()

        with file_lock.exclusive(self.path):
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertFalse(written.wait(0.3))
            self.assertEqual(self.path.read_bytes(), b"")
        thread.join(10)
        self.assertTrue(written.is_set())
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 1)

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_lock_held_past_the_limit_still_appends_the_row(self):
        # A hook that stopped while holding the lock must not cost the next hook its audit row.
        with mock.patch.object(file_lock, "LOCK_WAIT_S", 0.2), file_lock.exclusive(self.path):
            strict_session_log.append_event(self.home, "claude", "locked", {"hook_id": "prompt"})
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["hook_id"] for row in rows], ["prompt"])

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_lock_held_past_the_limit_is_recorded_as_a_retention_failure(self):
        # A holder that stopped makes every later row wait; the retention failure record says why.
        with mock.patch.object(file_lock, "LOCK_WAIT_S", 0.2), file_lock.exclusive(self.path):
            strict_session_log.append_event(self.home, "claude", "locked", {"hook_id": "prompt"})
        record = self.home / ".ghost-alice" / "state-retention-failures.jsonl"
        rows = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()] if record.exists() else []
        self.assertEqual([(row["policy"], row["error_type"], row["path"], row["error"]) for row in rows],
                         [("session-logs", "LockTimeout", str(self.path),
                           "lock held past 0.2s; appended without the lock")])

    def test_a_row_under_the_lock_leaves_the_retention_failure_record_empty(self):
        # The log's retention rule runs on the sessions folder at a session's first row, never on the log itself.
        for _ in range(2):
            strict_session_log.append_event(self.home, "claude", "locked", {"hook_id": "prompt"})
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 2)
        self.assertFalse((self.home / ".ghost-alice" / "state-retention-failures.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
