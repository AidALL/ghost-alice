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
        # Loop-found gap: agents lead with a variable assignment, so the verb is not token[0]. The scan must still find `Get-Content` -> op=read + path.
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

    def test_installed_platform_is_not_inferred_as_codex(self):
        with mock.patch.dict(os.environ, {"GHOST_ALICE_PLATFORM": "claude", "CODEX_THREAD_ID": "foreign"}, clear=True):
            row = io_trace_hook._extract({"session_id": "same-id", "platform": "codex"})
        self.assertEqual(row.get("platform"), "claude")
        self.assertEqual(row["session"], "same-id")

    def test_unknown_platform_and_invalid_identity_are_not_guessed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(io_trace_hook._extract({"session_id": "own"}).get("platform"), "unknown")
            self.assertEqual(io_trace_hook._extract({"platform": "codex", "session_id": "../other"})["session"], "unknown")

    def test_codex_apply_patch_records_each_patched_file(self):
        patch = ("*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n"
                 "*** Add File: docs/new.md\n+text\n*** Delete File: old.txt\n*** End Patch\n")
        with mock.patch.dict(os.environ, {"GHOST_ALICE_PLATFORM": "codex"}, clear=True):
            row = io_trace_hook._extract({"session_id": "s-patch", "tool_name": "apply_patch",
                                          "tool_input": {"command": patch}})
        self.assertEqual(row["path"], "src/app.py")
        self.assertEqual(row["pattern"], "src/app.py; docs/new.md; old.txt")
        self.assertNotIn("op", row)

    def test_codex_apply_patch_move_records_source_and_destination(self):
        patch = "*** Begin Patch\n*** Update File: a.py\n*** Move to: b.py\n@@\n-x\n+y\n*** End Patch\n"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_PLATFORM": "codex"}, clear=True):
            row = io_trace_hook._extract({"session_id": "s-move", "tool_name": "apply_patch",
                                          "tool_input": {"command": patch}})
        self.assertEqual((row["path"], row["pattern"]), ("a.py", "a.py; b.py"))

    def test_codex_apply_patch_keeps_every_path_past_200_characters(self):
        paths = [f"src/package_{index:02d}/module_with_a_long_name_{index:02d}.py" for index in range(8)]
        patch = "*** Begin Patch\n" + "".join(f"*** Add File: {path}\n+x\n" for path in paths) + "*** End Patch\n"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_PLATFORM": "codex"}, clear=True):
            row = io_trace_hook._extract({"session_id": "s-long", "tool_name": "apply_patch",
                                          "tool_input": {"command": patch}})
        self.assertGreater(len("; ".join(paths)), 200)
        self.assertEqual((row["path"], row["pattern"]), (paths[0], "; ".join(paths)))

    def test_empty_patch_and_notebook_edit_paths(self):
        with mock.patch.dict(os.environ, {"GHOST_ALICE_PLATFORM": "codex"}, clear=True):
            empty = io_trace_hook._extract({"session_id": "s-empty", "tool_name": "apply_patch",
                                            "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"}})
        with mock.patch.dict(os.environ, {"GHOST_ALICE_PLATFORM": "claude"}, clear=True):
            notebook = io_trace_hook._extract({"session_id": "s-nb", "tool_name": "NotebookEdit",
                                               "tool_input": {"notebook_path": "/work/analysis.ipynb"}})
        self.assertEqual((empty["path"], empty["pattern"]), ("n/a", ""))
        self.assertEqual(notebook["path"], "/work/analysis.ipynb")


class TestIoTraceRetention(unittest.TestCase):
    """io-trace keeps its limit (over 10,000 lines keeps the last 5,000) in the retention table."""

    def test_the_trim_rule_lives_in_the_retention_table(self):
        self.assertFalse(hasattr(io_trace_hook, "_rotate"))
        self.assertFalse(hasattr(io_trace_hook, "MAX_LINES"))

    def test_rows_are_appended_through_the_retention_lock(self):
        with tempfile.TemporaryDirectory() as temp_home:
            environment = {"HOME": temp_home, "GHOST_ALICE_GHOST_ROOT": str(Path(temp_home) / ".ghost-alice")}
            with mock.patch.dict(os.environ, environment, clear=False), \
                    mock.patch.object(io_trace_hook.state_retention, "append") as append:
                io_trace_hook._append_log({"tool": "Read"})
        [call] = append.call_args_list
        self.assertEqual(call.args[:2], ("io-trace", Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"))
        self.assertEqual(json.loads(call.args[2])["tool"], "Read")

    def test_an_append_past_ten_thousand_lines_keeps_the_last_five_thousand(self):
        raw = '{"session_id":"s-1","tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}'
        with tempfile.TemporaryDirectory() as temp_home:
            path = Path(temp_home) / ".ghost-alice" / "io-trace.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("".join(f'{{"n":{index}}}\n' for index in range(10_000)), encoding="utf-8")
            environment = {"HOME": temp_home, "GHOST_ALICE_GHOST_ROOT": str(path.parent)}
            with mock.patch.dict(os.environ, environment, clear=False), \
                    mock.patch.object(sys, "stdin", io.StringIO(raw)):
                io_trace_hook.main()
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 5_000)
        self.assertEqual(lines[0], '{"n":5001}')
        self.assertEqual(json.loads(lines[-1])["tool"], "Read")


if __name__ == "__main__":
    unittest.main()
