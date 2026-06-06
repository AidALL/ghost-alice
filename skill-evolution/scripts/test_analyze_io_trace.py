"""Tests for the skill-evolution io-trace analyzer.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
ANALYZER = SCRIPT_DIR / "analyze_io_trace.py"


class AnalyzeIoTraceTests(unittest.TestCase):
    def write_trace(self, rows: list[dict[str, str]]) -> pathlib.Path:
        tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="skill-evolution-test-"))
        path = tmpdir / "io-trace.jsonl"
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        return path

    def run_analyzer(self, rows: list[dict[str, str]], *args: str) -> dict[str, object]:
        trace = self.write_trace(rows)
        completed = subprocess.run(
            [sys.executable, str(ANALYZER), str(trace), "--json", *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return json.loads(completed.stdout)

    def write_intent_state(self, data: dict[str, object]) -> pathlib.Path:
        tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="skill-evolution-intent-test-"))
        path = tmpdir / "intent-state.json"
        path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        return path

    def test_conduct_feedback_emitted_as_candidate(self) -> None:
        intent = self.write_intent_state({
            "schema_version": "session-intent-ledger.v1",
            "current_goal": "fix agent conduct",
            "conduct_feedback": [
                {
                    "id": "scope-narrowing",
                    "failure_pattern": "covered only key spots of a full-set objective",
                    "corrective_rule": "cover the whole committed scope",
                    "source": "user-explicit",
                    "status": "open",
                },
                {
                    "id": "already-encoded",
                    "failure_pattern": "x",
                    "corrective_rule": "y",
                    "source": "user-explicit",
                    "status": "encoded",
                },
            ],
        })
        rows = [{"ts": "2026-05-13T00:00:00Z", "tool": "Read", "path": "/x/a.py"}]
        report = self.run_analyzer(rows, "--intent-ledger", str(intent))
        conduct = [i for i in report["instincts"] if str(i["id"]).startswith("conduct:")]
        self.assertEqual(len(conduct), 1)
        candidate = conduct[0]
        self.assertEqual(candidate["id"], "conduct:scope-narrowing")
        self.assertEqual(candidate["quality"], "review")
        self.assertEqual(candidate["decision"], "necessity-gate")
        self.assertEqual(candidate["domain"], "conduct")
        self.assertIn("cover the whole committed scope", candidate["trigger"])
        self.assertGreaterEqual(report["quality_summary"]["review"], 1)

    def test_open_recommendations_retrievable_standalone(self) -> None:
        intent = self.write_intent_state({
            "schema_version": "session-intent-ledger.v1",
            "conduct_feedback": [
                {
                    "id": "intent-vs-skill-gap",
                    "failure_pattern": "skill behaves narrower than the user's stated intent",
                    "corrective_rule": "widen the skill to match intent",
                    "source": "inferred",
                    "status": "open",
                },
                {
                    "id": "already-applied",
                    "failure_pattern": "x",
                    "corrective_rule": "y",
                    "source": "user-explicit",
                    "status": "encoded",
                },
            ],
        })
        report = self.run_analyzer([], "--intent-ledger", str(intent))
        self.assertEqual(report["event_count"], 0)
        conduct = [i for i in report["instincts"] if str(i["id"]).startswith("conduct:")]
        self.assertEqual(len(conduct), 1)
        self.assertEqual(conduct[0]["id"], "conduct:intent-vs-skill-gap")
        self.assertEqual(conduct[0]["source"], "inferred")
        self.assertEqual(conduct[0]["decision"], "necessity-gate")

    def test_generates_tool_workflow_and_sequence_instincts(self) -> None:
        rows = [
            {
                "ts": "2026-05-13T00:00:00Z",
                "session": "s1",
                "tool": "Read",
                "path": "/repo/a.py",
                "pattern": "",
            },
            {
                "ts": "2026-05-13T00:00:01Z",
                "session": "s1",
                "tool": "Edit",
                "path": "/repo/a.py",
                "pattern": "",
            },
            {
                "ts": "2026-05-13T00:00:02Z",
                "session": "s1",
                "tool": "Bash",
                "path": "n/a",
                "pattern": "pytest tests/test_a.py",
            },
        ]
        result = self.run_analyzer(rows, "--min-count", "1")
        instincts = {item["id"]: item for item in result["instincts"]}

        self.assertEqual(result["event_count"], 3)
        self.assertIn("tool:read", instincts)
        self.assertIn("workflow:read:python", instincts)
        self.assertIn("sequence:read-edit-bash", instincts)
        self.assertEqual(instincts["sequence:read-edit-bash"]["scope"], "repo")
        self.assertIn("Read -> Edit -> Bash", instincts["sequence:read-edit-bash"]["trigger"])

    def test_project_scope_uses_path_or_cwd_without_hardcoded_user_paths(self) -> None:
        rows = [
            {
                "ts": "2026-05-13T00:00:00Z",
                "session": "s2",
                "tool": "Read",
                "cwd": "/tmp/project-alpha",
                "path": "/tmp/project-alpha/SKILL.md",
                "pattern": "",
            },
            {
                "ts": "2026-05-13T00:00:01Z",
                "session": "s2",
                "tool": "Read",
                "cwd": "/tmp/project-alpha",
                "path": "/tmp/project-alpha/README.md",
                "pattern": "",
            },
        ]
        result = self.run_analyzer(rows, "--min-count", "1")
        instincts = {item["id"]: item for item in result["instincts"]}

        self.assertEqual(instincts["tool:read"]["scope"], "project-alpha")
        source = ANALYZER.read_text(encoding="utf-8")
        self.assertNotIn("/Users/aidall", source)
        self.assertNotIn("/psyco-neu-vision", source)

    def test_accepts_ghost_alice_hook_trace_shape(self) -> None:
        rows = [
            {
                "ts": "2026-05-13T00:00:00Z",
                "session_id": "s3",
                "tool_name": "functions.exec_command",
                "cwd": "/work/example",
                "tool_input": {"cmd": "pytest tests", "workdir": "/work/example"},
            },
            {
                "ts": "2026-05-13T00:00:01Z",
                "session_id": "s3",
                "tool_name": "functions.exec_command",
                "cwd": "/work/example",
                "tool_input": {"cmd": "pytest tests", "workdir": "/work/example"},
            },
        ]
        result = self.run_analyzer(rows, "--min-count", "1")
        ids = {item["id"] for item in result["instincts"]}

        self.assertIn("tool:functions.exec-command", ids)
        self.assertIn("workflow:functions.exec-command:python", ids)

    def test_quality_gate_marks_cross_session_sequence_for_review(self) -> None:
        rows = [
            {
                "ts": "2026-05-13T00:00:00Z",
                "session": "s4",
                "tool": "Read",
                "path": "/repo/a.py",
                "pattern": "",
            },
            {
                "ts": "2026-05-13T00:00:01Z",
                "session": "s4",
                "tool": "Edit",
                "path": "/repo/a.py",
                "pattern": "",
            },
            {
                "ts": "2026-05-13T00:00:02Z",
                "session": "s4",
                "tool": "Bash",
                "path": "n/a",
                "pattern": "python scripts/build_catalog.py",
            },
            {
                "ts": "2026-05-13T00:01:00Z",
                "session": "s5",
                "tool": "Read",
                "path": "/repo/b.py",
                "pattern": "",
            },
            {
                "ts": "2026-05-13T00:01:01Z",
                "session": "s5",
                "tool": "Edit",
                "path": "/repo/b.py",
                "pattern": "",
            },
            {
                "ts": "2026-05-13T00:01:02Z",
                "session": "s5",
                "tool": "Bash",
                "path": "n/a",
                "pattern": "python scripts/build_catalog.py",
            },
        ]
        result = self.run_analyzer(rows, "--min-count", "2")
        instincts = {item["id"]: item for item in result["instincts"]}
        sequence = instincts["sequence:read-edit-bash"]

        self.assertEqual(sequence["quality"], "review")
        self.assertEqual(sequence["decision"], "necessity-gate")
        self.assertEqual(sequence["session_count"], 2)
        self.assertIn("cross-session-evidence", sequence["quality_reasons"])
        self.assertGreaterEqual(result["quality_summary"]["review"], 1)

    def test_quality_gate_rejects_single_session_test_loops(self) -> None:
        rows = [
            {
                "ts": f"2026-05-13T00:00:0{index}Z",
                "session": "s6",
                "tool": "Bash",
                "path": "n/a",
                "pattern": "pytest tests/test_a.py",
            }
            for index in range(4)
        ]
        result = self.run_analyzer(rows, "--min-count", "2")
        instincts = {item["id"]: item for item in result["instincts"]}
        loop = instincts["sequence:bash-bash-bash"]

        self.assertEqual(loop["quality"], "reject")
        self.assertEqual(loop["decision"], "route-to-systematic-debugging")
        self.assertEqual(loop["session_count"], 1)
        self.assertIn("single-session-evidence", loop["quality_reasons"])
        self.assertIn("debugging-or-test-loop", loop["quality_reasons"])
        self.assertGreaterEqual(result["quality_summary"]["reject"], 1)

    def test_intent_ledger_enriches_instincts_without_changing_report_only_mode(self) -> None:
        rows = [
            {
                "ts": "2026-05-13T00:00:00Z",
                "session": "s7",
                "tool": "Read",
                "path": "/repo/SKILL.md",
                "pattern": "",
            },
            {
                "ts": "2026-05-13T00:00:01Z",
                "session": "s7",
                "tool": "Read",
                "path": "/repo/README.md",
                "pattern": "",
            },
        ]
        intent_state = self.write_intent_state({
            "current_goal": "implement session intent guard",
            "constraints": ["do not store raw prompts"],
            "non_goals": ["automatic skill creation"],
            "decisions": [{"id": "report-only", "summary": "skill-evolution only proposes"}],
            "risk_flags": ["none"],
        })

        result = self.run_analyzer(rows, "--min-count", "1", "--intent-ledger", str(intent_state))
        instincts = {item["id"]: item for item in result["instincts"]}
        context = instincts["tool:read"]["intent_context"]

        self.assertEqual(result["intent_context"]["current_goal"], "implement session intent guard")
        self.assertEqual(context["decision_count"], 1)
        self.assertIn("do not store raw prompts", context["constraints"])
        self.assertEqual(instincts["tool:read"]["decision"], "observe-more")
        self.assertNotIn("events", json.dumps(result, ensure_ascii=False))

    def test_missing_intent_ledger_keeps_io_trace_only_output(self) -> None:
        rows = [
            {
                "ts": "2026-05-13T00:00:00Z",
                "session": "s8",
                "tool": "Read",
                "path": "/repo/SKILL.md",
                "pattern": "",
            },
            {
                "ts": "2026-05-13T00:00:01Z",
                "session": "s8",
                "tool": "Read",
                "path": "/repo/README.md",
                "pattern": "",
            },
        ]

        result = self.run_analyzer(rows, "--min-count", "1")
        instincts = {item["id"]: item for item in result["instincts"]}

        self.assertNotIn("intent_context", result)
        self.assertNotIn("intent_context", instincts["tool:read"])
        self.assertEqual(instincts["tool:read"]["decision"], "observe-more")


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    unittest.main(verbosity=2)
