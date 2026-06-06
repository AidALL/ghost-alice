#!/usr/bin/env python3
"""Tests for Ghost-ALICE strict hook output session logs."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict_session_log


class TestStrictSessionLogPath(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)

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


if __name__ == "__main__":
    unittest.main()
