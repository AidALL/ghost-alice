#!/usr/bin/env python3
"""Tests for the cross-platform io-trace hook."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import io_trace_hook


class TestIoTraceHook(unittest.TestCase):
    def write_digest_only_session(
        self,
        session_root: str,
        *,
        platform: str = "codex",
        session_id: str = "s-digest-only",
        event_count: int = 3,
    ) -> None:
        session_dir = Path(session_root) / platform / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "intent-state.json").write_text(json.dumps({
            "schema_version": "session-intent-ledger.v1",
            "platform": platform,
            "session_id": session_id,
            "last_semantic_delta_status": "not-provided",
        }) + "\n", encoding="utf-8")
        events = [
            {
                "event": "user-input-observed",
                "event_id": f"evt-{index}",
                "input_digest": f"sha256:{index}",
                "intent_delta_status": "not-provided",
            }
            for index in range(1, event_count + 1)
        ]
        (session_dir / "intent-events.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )

    def test_main_logs_payload_with_surrogate_path(self):
        raw = (
            '{"session_id":"s-win","tool_name":"Read",'
            '"tool_input":{"file_path":"C:/tmp/bad \udcec.txt"}}'
        )

        with tempfile.TemporaryDirectory() as temp_home:
            with (
                mock.patch.dict(os.environ, {"HOME": temp_home}, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                exit_code = io_trace_hook.main()

            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(exit_code, 0)
        self.assertEqual(row["session"], "s-win")
        self.assertEqual(row["tool"], "Read")
        self.assertEqual(row["path"], "C:/tmp/bad \\udcec.txt")

    def test_main_emits_semantic_delta_starvation_warning_after_three_digest_only_turns(self):
        raw = json.dumps({
            "session_id": "s-digest-only",
            "platform": "codex",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/example.txt"},
        })

        with tempfile.TemporaryDirectory() as temp_home, tempfile.TemporaryDirectory() as session_root:
            self.write_digest_only_session(session_root)

            with (
                mock.patch.dict(os.environ, {
                    "HOME": temp_home,
                    "GHOST_ALICE_SESSION_INTENT_ROOT": session_root,
                }, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                exit_code = io_trace_hook.main()

            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(exit_code, 0)
        warning_rows = [
            row for row in rows
            if row.get("tool") == "governance-warning"
        ]
        self.assertEqual(len(warning_rows), 1)
        self.assertEqual(
            warning_rows[0]["pattern"],
            "semantic-delta-starvation: 3 consecutive digest-only user inputs",
        )
        self.assertEqual(
            warning_rows[0]["dedup_key"],
            "semantic-delta-starvation:codex:s-digest-only:evt-3",
        )

    def test_main_skips_semantic_delta_warning_for_recovery_command(self):
        raw = json.dumps({
            "session_id": "s-digest-only",
            "platform": "codex",
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 session_intent_ledger.py --delta-json '{}'",
            },
        })

        with tempfile.TemporaryDirectory() as temp_home, tempfile.TemporaryDirectory() as session_root:
            self.write_digest_only_session(session_root)

            with (
                mock.patch.dict(os.environ, {
                    "HOME": temp_home,
                    "GHOST_ALICE_SESSION_INTENT_ROOT": session_root,
                }, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                exit_code = io_trace_hook.main()

            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(exit_code, 0)
        self.assertFalse(any(row.get("tool") == "governance-warning" for row in rows))

    def test_main_deduplicates_semantic_delta_warning_for_same_latest_event(self):
        raw = json.dumps({
            "session_id": "s-digest-only",
            "platform": "codex",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/example.txt"},
        })

        with tempfile.TemporaryDirectory() as temp_home, tempfile.TemporaryDirectory() as session_root:
            self.write_digest_only_session(session_root)

            for _ in range(2):
                with (
                    mock.patch.dict(os.environ, {
                        "HOME": temp_home,
                        "GHOST_ALICE_SESSION_INTENT_ROOT": session_root,
                    }, clear=False),
                    mock.patch.object(sys, "stdin", io.StringIO(raw)),
                ):
                    exit_code = io_trace_hook.main()
                    self.assertEqual(exit_code, 0)

            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]

        warning_rows = [
            row for row in rows
            if row.get("tool") == "governance-warning"
        ]
        self.assertEqual(len(warning_rows), 1)


if __name__ == "__main__":
    unittest.main()
