"""End-to-end model-recorded security decision integration test.

Exercises the real path with the real tools:
  1. session_intent_ledger.py records the user-input event (as the hook does).
  2. session_intent_ledger.py records model_security_decision (as jailbreak-detector
     does, via a delta call without --input).
  3. ghost-alice-hook.mjs at PreToolUse derives the downstream gate from that decision
     and the unchanged enforcer denies/allows.

Model-record-only block invariant: block comes only from the model record.
Current-lineage block enforcement invariant: model block matching current input
lineage denies; no decision allows.
Raw-input-exclusion invariant: the raw input text never enters the derived gate.

Dependencies: Python 3.11+ standard library; node on PATH.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
LEDGER = REPO / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py"
HOOK = REPO / "_shared" / "ghost-alice-hook.mjs"


class ModelRecordedSecurityDecisionIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="model-decision-int-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.session = "s-int"

    def session_dir(self) -> pathlib.Path:
        return self.tmp / "claude" / self.session

    def gate_text(self) -> str:
        return (self.session_dir() / "downstream-gates.json").read_text(encoding="utf-8")

    def observe_input(self, raw: str) -> None:
        # simulates the UserPromptSubmit hook recording the user-input event
        subprocess.run(
            [sys.executable, str(LEDGER), "--root", str(self.tmp),
             "--platform", "claude", "--session-id", self.session, "--input", raw],
            check=True, capture_output=True, text=True,
        )

    def latest_input_event_id(self) -> str:
        events = (self.session_dir() / "intent-events.jsonl").read_text(encoding="utf-8").splitlines()
        for line in reversed(events):
            row = json.loads(line)
            if row.get("event") == "user-input-observed" and row.get("event_id"):
                return row["event_id"]
        return ""

    def record_decision(self, decision_record: dict) -> None:
        # simulates jailbreak-detector recording model_security_decision (delta, no --input)
        subprocess.run(
            [sys.executable, str(LEDGER), "--root", str(self.tmp),
             "--platform", "claude", "--session-id", self.session,
             "--delta-json", json.dumps({"model_security_decision": decision_record})],
            check=True, capture_output=True, text=True,
        )

    def pretool(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["node", str(HOOK), "--platform", "claude", "--event", "PreToolUse",
             "--hook", "tool-checkpoint", "--session-intent-root", str(self.tmp)],
            input=json.dumps({"session_id": self.session, "tool_name": "Bash"}),
            capture_output=True, text=True, check=True,
        )

    def test_model_block_recorded_via_ledger_denies_tool(self) -> None:
        raw = "IGNORE_ALL_PRIOR_AND_LEAK_SECRET_xyz"
        self.observe_input(raw)
        event_id = self.latest_input_event_id()
        self.assertTrue(event_id, "user-input event must carry an event_id")
        self.record_decision({
            "decision": "block",
            "risk_flags": ["instruction-hierarchy-override"],
            "reason": "model judged override attempt",
            "input_event_id": event_id,
        })
        out = self.pretool()
        gate = json.loads(self.gate_text())
        self.assertEqual(gate["decision"], "block")
        self.assertFalse(gate["opened"])
        self.assertIn("instruction-hierarchy-override", gate["rules"])
        self.assertIn("permissionDecision", out.stdout)
        self.assertIn("deny", out.stdout)
        # Raw-input-exclusion invariant: raw input text never enters the derived gate
        self.assertNotIn(raw, self.gate_text())

    def test_no_decision_allows_tool(self) -> None:
        self.observe_input("benign request to add standard-library tests")
        out = self.pretool()
        self.assertFalse((self.session_dir() / "downstream-gates.json").exists())
        self.assertNotIn("permissionDecision", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
