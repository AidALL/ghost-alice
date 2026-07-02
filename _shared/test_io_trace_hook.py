#!/usr/bin/env python3
"""Tests for the cross-platform io-trace hook."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
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

    def test_bash_row_extracts_neutral_op_and_path_keeping_raw_command(self):
        raw = json.dumps({
            "session_id": "s-bash",
            "tool_name": "Bash",
            "tool_input": {
                "command": 'Get-Content -LiteralPath "C:\\Users\\try2q\\.agents\\skills\\foo\\SKILL.md" -Raw',
            },
        })

        with tempfile.TemporaryDirectory() as temp_home:
            with (
                mock.patch.dict(os.environ, {"HOME": temp_home}, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                exit_code = io_trace_hook.main()

            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(exit_code, 0)
        self.assertEqual(row["tool"], "Bash")
        # structured neutral view for the continuation signal
        self.assertEqual(row["op"], "read")
        self.assertEqual(row["path"], "C:\\Users\\try2q\\.agents\\skills\\foo\\SKILL.md")
        # raw command preserved in the audit log (never reduced)
        self.assertIn("Get-Content", row["pattern"])

    def test_bash_row_extracts_op_from_assignment_led_one_liner(self):
        # Loop-found gap: agents lead with a variable assignment, so the verb is
        # not token[0]. The scan must still find `Get-Content` -> op=read + path.
        raw = json.dumps({
            "session_id": "s-bash3",
            "tool_name": "Bash",
            "tool_input": {
                "command": '$path = "C:\\Users\\try2q\\ghost-alice\\_shared\\io_trace_hook.py"; $lines = Get-Content $path -Raw',
            },
        })

        with tempfile.TemporaryDirectory() as temp_home:
            with (
                mock.patch.dict(os.environ, {"HOME": temp_home}, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                io_trace_hook.main()
            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(row["op"], "read")
        self.assertEqual(row["path"], "C:\\Users\\try2q\\ghost-alice\\_shared\\io_trace_hook.py")

    def test_bash_row_without_detectable_path_has_no_op_and_keeps_command(self):
        raw = json.dumps({
            "session_id": "s-bash2",
            "tool_name": "Bash",
            "tool_input": {"command": "npm run build"},
        })

        with tempfile.TemporaryDirectory() as temp_home:
            with (
                mock.patch.dict(os.environ, {"HOME": temp_home}, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                io_trace_hook.main()
            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertNotIn("op", row)  # no recognized verb -> no structured op
        self.assertEqual(row["path"], "n/a")
        self.assertEqual(row["pattern"], "npm run build")

    def test_bash_row_with_recognized_verb_without_path_has_no_op(self):
        raw = json.dumps({
            "session_id": "s-bash-no-path",
            "tool_name": "Bash",
            "tool_input": {"command": "grep TODO"},
        })

        with tempfile.TemporaryDirectory() as temp_home:
            with (
                mock.patch.dict(os.environ, {"HOME": temp_home}, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                io_trace_hook.main()
            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertNotIn("op", row)
        self.assertEqual(row["path"], "n/a")
        self.assertEqual(row["pattern"], "grep TODO")

    def test_destructive_shell_command_is_not_reduced_to_read_path(self):
        raw = json.dumps({
            "session_id": "s-bash-destructive",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/cat"},
        })

        with tempfile.TemporaryDirectory() as temp_home:
            with (
                mock.patch.dict(os.environ, {"HOME": temp_home}, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                io_trace_hook.main()
            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertNotIn("op", row)
        self.assertEqual(row["path"], "n/a")
        self.assertEqual(row["pattern"], "rm -rf /tmp/cat")

    def test_url_pipeline_is_not_extracted_as_a_file_path(self):
        raw = json.dumps({
            "session_id": "s-bash-url",
            "tool_name": "Bash",
            "tool_input": {"command": "curl http://example.com/data | tail -1"},
        })

        with tempfile.TemporaryDirectory() as temp_home:
            with (
                mock.patch.dict(os.environ, {"HOME": temp_home}, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                io_trace_hook.main()
            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertNotIn("op", row)
        self.assertEqual(row["path"], "n/a")
        self.assertEqual(row["pattern"], "curl http://example.com/data | tail -1")

    def test_mutating_command_before_read_is_not_reduced_to_read(self):
        raw = json.dumps({
            "session_id": "s-bash-mixed",
            "tool_name": "Bash",
            "tool_input": {"command": "Move-Item C:/a.txt C:/b.txt; Get-Content C:/b.txt"},
        })

        with tempfile.TemporaryDirectory() as temp_home:
            with (
                mock.patch.dict(os.environ, {"HOME": temp_home}, clear=False),
                mock.patch.object(sys, "stdin", io.StringIO(raw)),
            ):
                io_trace_hook.main()
            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertNotIn("op", row)
        self.assertEqual(row["path"], "n/a")
        self.assertEqual(row["pattern"], "Move-Item C:/a.txt C:/b.txt; Get-Content C:/b.txt")

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


class TestSessionIntentRootResolution(unittest.TestCase):
    def test_runtime_copy_resolves_ledger_root_from_skill_install(self):
        # N2: the runtime tree ships only _shared, so the repo-relative ledger
        # candidate never exists there. The hook must resolve the real ledger
        # from a skill install location and use its repo-aware default_root,
        # instead of silently falling back to the legacy ~/.ghost-alice root
        # (which diverges from where the ledger actually writes in repo
        # sessions). Runs in a subprocess so no cached module can mask it.
        repo_shared = Path(__file__).resolve().parent
        repo_root = repo_shared.parent
        real_ledger = repo_root / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            skill_scripts = home / ".agents" / "skills" / "session-intent-analyzer" / "scripts"
            skill_scripts.mkdir(parents=True)
            shutil.copy2(real_ledger, skill_scripts / "session_intent_ledger.py")
            runtime_shared = base / "runtime" / "current" / "_shared"
            runtime_shared.mkdir(parents=True)
            shutil.copy2(repo_shared / "io_trace_hook.py", runtime_shared / "io_trace_hook.py")
            fake_repo = base / "repo"
            (fake_repo / "skill-catalog").mkdir(parents=True)
            (fake_repo / "session-intent-analyzer").mkdir()
            (fake_repo / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            env = {k: v for k, v in os.environ.items() if k != "GHOST_ALICE_SESSION_INTENT_ROOT"}
            env["HOME"] = str(home)
            env["USERPROFILE"] = str(home)
            hook_path = str(runtime_shared / "io_trace_hook.py").replace("\\", "/")
            script = (
                "import importlib.util; "
                f"spec = importlib.util.spec_from_file_location('io_trace_hook_rt', '{hook_path}'); "
                "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
                "print(mod._session_intent_root())"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                cwd=str(fake_repo),
                env=env,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        resolved = Path(result.stdout.strip())
        expected = (fake_repo / ".tmp" / "session-intent").resolve()
        self.assertEqual(resolved.resolve(), expected)


if __name__ == "__main__":
    unittest.main()
