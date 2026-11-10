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
from unittest import mock


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
ANALYZER = SCRIPT_DIR / "analyze_io_trace.py"
sys.path.insert(0, str(SCRIPT_DIR))
import analyze_io_trace


class AnalyzeIoTraceTests(unittest.TestCase):
    def test_sqlite_context_reads_authority_and_keeps_report_fields(self) -> None:
        ledger = analyze_io_trace.load_ledger()
        root = self.write_trace([]).parent / "ledger"
        paths = ledger.record_turn(root=root, platform="codex", session_id="report", intent_delta={
            "current_goal": "authoritative report", "constraints": ["read only"],
            "decisions": [{"id": "active", "summary": "use the ledger"}],
            "latest_scope": {"allowed": ["read"]},
        })
        paths["state"].parent.mkdir(parents=True)
        paths["state"].write_text('{"current_goal":"stale portable copy"}', encoding="utf-8")
        before = {item: item.read_bytes() for item in root.rglob("*") if item.is_file()}
        context = analyze_io_trace.load_intent_context(paths["state"])
        self.assertEqual(set(context), {
            "schema_version", "current_goal", "constraints", "non_goals", "open_questions",
            "decision_count", "active_decisions", "latest_scope", "conduct_feedback",
        })
        self.assertEqual(context["current_goal"], "authoritative report")
        self.assertEqual(context["decision_count"], 1)
        self.assertEqual(context["active_decisions"][0]["id"], "active")
        self.assertEqual(context["latest_scope"], {"allowed": ["read"]})
        self.assertEqual({item: item.read_bytes() for item in before}, before)

    def test_portable_context_keeps_its_contract_without_ledger_or_writes(self) -> None:
        state = {
            "schema_version": "portable.old", "current_goal": "offline report",
            "constraints": ["read only"], "non_goals": ["install"], "open_questions": ["next?"],
            "decisions": [None, {"id": "old", "superseded": True}, {"id": "active"}],
            "latest_scope": {"allowed": ["read"]},
            "conduct_feedback": [None, "not an object", {"id": "feedback"}],
            "pending_native_inputs": "not a list", "acceptance_criteria": None,
        }
        path = self.write_intent_state(state)
        before = {item.name: item.read_bytes() for item in path.parent.iterdir()}
        with mock.patch.object(analyze_io_trace, "load_ledger", side_effect=ImportError("ledger unavailable")) as loader:
            context = analyze_io_trace.load_intent_context(path)
        self.assertEqual(context, {
            "schema_version": "portable.old", "current_goal": "offline report",
            "constraints": ["read only"], "non_goals": ["install"], "open_questions": ["next?"],
            "decision_count": 1, "active_decisions": [{"id": "active"}],
            "latest_scope": {"allowed": ["read"]}, "conduct_feedback": [{"id": "feedback"}],
        })
        self.assertEqual({item.name: item.read_bytes() for item in path.parent.iterdir()}, before)

    def test_jsonl_waits_for_the_lf_after_an_incomplete_utf8_tail(self) -> None:
        trace = self.write_trace([{"tool": "Read"}])
        character = "가".encode("utf-8")
        with trace.open("ab") as handle:
            handle.write(b'{"tool":"' + character[:2])
        self.assertEqual(analyze_io_trace.load_jsonl(trace, 0), [{"tool": "Read"}])
        with trace.open("ab") as handle:
            handle.write(character[2:] + b'"}\n')
        self.assertEqual(analyze_io_trace.load_jsonl(trace, 0), [{"tool": "Read"}, {"tool": "가"}])

    def test_jsonl_filters_committed_rows_before_applying_window(self) -> None:
        first = {"tool": "Read", "pattern": "a\u2028b\u2029c\u0085d"}
        second = {"tool": "Edit"}
        tail = {"tool": "Bash"}
        trace = self.write_trace([])
        trace.write_bytes((
            json.dumps(first, ensure_ascii=False) + "\r\n\nnot json\n[]\n42\n"
            + '{"tool":"CR alone"}\r{"tool":"not a new line"}\n'
            + "\u00a0" + json.dumps(second) + "\u00a0\n" + json.dumps(tail)
        ).encode("utf-8"))
        self.assertEqual(analyze_io_trace.load_jsonl(trace, 0), [first, second])
        self.assertEqual(analyze_io_trace.load_jsonl(trace, -1), [first, second])
        self.assertEqual(analyze_io_trace.load_jsonl(trace, 1), [second])
        with trace.open("ab") as handle:
            handle.write(b"\n")
        self.assertEqual(analyze_io_trace.load_jsonl(trace, 1), [tail])

    # The fixtures stand in for the response model's recorded source claims, decisions and scope.
    # user-said does not authenticate a sender; allowed/prohibited do not establish human authorization.
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

    def test_default_trace_and_ledger_loading_come_from_their_owners(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("analyze_io_trace_under_test", ANALYZER)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # the module's dataclass resolves its annotations through sys.modules
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        with mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": "/ghost"}):
            self.assertEqual(module.default_trace(), pathlib.Path("/ghost") / "io-trace.jsonl")
        self.assertEqual(module.load_ledger.__module__, "ledger_access")
        self.assertFalse(hasattr(module, "_ledger_module"))
        self.assertFalse(hasattr(module, "DEFAULT_TRACE"))

    def test_conduct_feedback_emitted_as_candidate(self) -> None:
        intent = self.write_intent_state({
            "schema_version": "session-intent-ledger.v1",
            "current_goal": "fix agent conduct",
            "conduct_feedback": [
                {
                    "id": "scope-narrowing",
                    "failure_pattern": "covered only key spots of a full-set objective",
                    "corrective_rule": "cover the whole committed scope",
                    "source": "user-said",
                    "status": "open",
                },
                {
                    "id": "already-encoded",
                    "failure_pattern": "x",
                    "corrective_rule": "y",
                    "source": "user-said",
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
        self.assertEqual(candidate["suggested_next_step"], "necessity-gate")
        self.assertNotIn("decision", candidate)
        self.assertEqual(candidate["domain"], "conduct")
        self.assertIn("cover the whole committed scope", candidate["trigger"])
        self.assertGreaterEqual(report["quality_summary"]["review"], 1)

    def test_legacy_missing_sources_stay_unknown_in_reports(self) -> None:
        entries = [{"id": "missing", "corrective_rule": "Recorded correction"}]
        entries.extend({"id": str(index), "source": source, "corrective_rule": "Recorded correction"}
                       for index, source in enumerate((None, "", " ", "user-explicit", "inferred", "legacy-value")))
        intent = self.write_intent_state({"conduct_feedback": entries})
        original = intent.read_bytes()
        reports = {item["id"]: item for item in self.run_analyzer([], "--intent-ledger", str(intent))["instincts"]}
        for entry_id in ("missing", "0", "1", "2"):
            item = reports["conduct:" + entry_id]
            self.assertEqual(item["source"], "unknown")
            self.assertIn("source-unknown", item["quality_reasons"])
            self.assertEqual(item["evidence"], "recorded conduct feedback")
        self.assertEqual(reports["conduct:3"]["source"], "user-said")
        self.assertEqual(reports["conduct:4"]["source"], "interpreted-as")
        self.assertEqual(reports["conduct:5"]["source"], "legacy-value")
        self.assertEqual(intent.read_bytes(), original)

    def test_open_recommendations_retrievable_standalone(self) -> None:
        intent = self.write_intent_state({
            "schema_version": "session-intent-ledger.v1",
            "conduct_feedback": [
                {
                    "id": "intent-vs-skill-gap",
                    "failure_pattern": "skill behaves narrower than the user's stated intent",
                    "corrective_rule": "widen the skill to match intent",
                    "source": "interpreted-as",
                    "status": "open",
                },
                {
                    "id": "already-applied",
                    "failure_pattern": "x",
                    "corrective_rule": "y",
                    "source": "user-said",
                    "status": "encoded",
                },
            ],
        })
        report = self.run_analyzer([], "--intent-ledger", str(intent))
        self.assertEqual(report["event_count"], 0)
        conduct = [i for i in report["instincts"] if str(i["id"]).startswith("conduct:")]
        self.assertEqual(len(conduct), 1)
        self.assertEqual(conduct[0]["id"], "conduct:intent-vs-skill-gap")
        self.assertEqual(conduct[0]["source"], "interpreted-as")
        self.assertEqual(conduct[0]["suggested_next_step"], "necessity-gate")

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
        self.assertEqual(sequence["suggested_next_step"], "necessity-gate")
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
        self.assertEqual(loop["suggested_next_step"], "route-to-systematic-debugging")
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
        })

        result = self.run_analyzer(rows, "--min-count", "1", "--intent-ledger", str(intent_state))
        instincts = {item["id"]: item for item in result["instincts"]}

        self.assertEqual(result["intent_context"]["current_goal"], "implement session intent guard")
        self.assertEqual(instincts["tool:read"]["suggested_next_step"], "observe-more")
        self.assertNotIn("events", json.dumps(result, ensure_ascii=False))

    def test_corrected_scope_reaches_report_without_changing_triage(self) -> None:
        rows = [{"session": "scope-correction", "tool": "Read", "path": "/repo/config.json"}]
        state = {
            "current_goal": "Repair a diagnosed configuration issue",
            "constraints": ["Keep unrelated files unchanged"],
            "non_goals": ["Edit configuration during diagnosis", "Publish externally"],
            "decisions": [
                {"id": "diagnosis-only", "summary": "Inspect only", "superseded": True},
                {"id": "repair-approved", "source": "user-said", "summary": "Repair is now authorized"},
            ],
        }
        baseline = self.run_analyzer(rows, "--min-count", "1", "--intent-ledger", str(self.write_intent_state(state)))
        state["latest_scope"] = {"allowed": ["Repair configuration"], "prohibited": ["Publish externally"]}
        path = self.write_intent_state(state)
        before = path.read_bytes()
        report = self.run_analyzer(rows, "--min-count", "1", "--intent-ledger", str(path))
        context = report["intent_context"]
        self.assertIn("active_decisions", context)
        self.assertEqual(context["active_decisions"], [state["decisions"][1]])
        self.assertEqual(context["latest_scope"], state["latest_scope"])
        self.assertEqual(context["decision_count"], 1)
        self.assertEqual(context["constraints"], state["constraints"])
        self.assertEqual(context["non_goals"], state["non_goals"])
        self.assertEqual([(i["id"], i["suggested_next_step"]) for i in report["instincts"]],
                         [(i["id"], i["suggested_next_step"]) for i in baseline["instincts"]])
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_intent_context_does_not_invent_decisions_or_scope(self) -> None:
        for scope in (None, "not a scope object"):
            report = self.run_analyzer([], "--intent-ledger", str(self.write_intent_state({"latest_scope": scope})))
            self.assertEqual(report["intent_context"].get("active_decisions"), [])
            self.assertEqual(report["intent_context"].get("latest_scope"), {})

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
        self.assertEqual(instincts["tool:read"]["suggested_next_step"], "observe-more")


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    unittest.main(verbosity=2)
