"""Integration tests for PreToolUse gate derivation.

The covered behavior is write-on-block-only downstream gate derivation from the
model-recorded security decision.

The dispatcher (ghost-alice-hook.mjs) calls deriveDownstreamGateFromDecision
(_shared/derive_downstream_gate.mjs) at PreToolUse. It writes a block gate ONLY
when the model recorded a block matching the current input lineage; it never
overwrites an existing gate on allow / absent / stale. The unchanged enforcer
then reads the gate. Existing block gates are preserved (enforcer staleness
neutralizes prior-turn blocks); absence means silent allow.

Invokes the hook as a subprocess (node), the same way the runtime does.

Dependencies: Python 3.11+ standard library only; node on PATH.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
HOOK = REPO / "_shared" / "ghost-alice-hook.mjs"


class DerivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="mjs-derive-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.session = "s-derive"
        self.session_dir = self.tmp / "claude" / self.session
        self.session_dir.mkdir(parents=True)

    def write_state(self, decision_record: dict | None) -> None:
        state = {
            "schema_version": "session-intent-ledger.v1",
            "platform": "claude",
            "session_id": self.session,
            "current_goal": "g",
        }
        if decision_record is not None:
            state["model_security_decision"] = decision_record
        (self.session_dir / "intent-state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    def write_event(self, event_id: str, digest: str = "sha256:d", char_count: int = 12) -> None:
        (self.session_dir / "intent-events.jsonl").write_text(
            json.dumps({
                "event": "user-input-observed",
                "event_id": event_id,
                "input_digest": digest,
                "input_char_count": char_count,
            }) + "\n",
            encoding="utf-8",
        )

    def write_gate(self, gate: dict) -> None:
        (self.session_dir / "downstream-gates.json").write_text(
            json.dumps(gate, ensure_ascii=False), encoding="utf-8"
        )

    def gate_path(self) -> pathlib.Path:
        return self.session_dir / "downstream-gates.json"

    def run_pretool(self) -> subprocess.CompletedProcess:
        payload = json.dumps({"session_id": self.session, "tool_name": "Bash"})
        return subprocess.run(
            [
                "node", str(HOOK),
                "--platform", "claude",
                "--event", "PreToolUse",
                "--hook", "tool-checkpoint",
                "--session-intent-root", str(self.tmp),
            ],
            input=payload,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_block_decision_matching_lineage_writes_block_and_denies(self) -> None:
        self.write_state({"decision": "block", "risk_flags": ["credential-reveal"], "input_event_id": "sha256:e1"})
        self.write_event("sha256:e1")
        out = self.run_pretool()
        gate = json.loads(self.gate_path().read_text(encoding="utf-8"))
        self.assertEqual(gate["decision"], "block")
        self.assertFalse(gate["opened"])
        self.assertIn("credential-reveal", gate["rules"])
        self.assertIn("permissionDecision", out.stdout)
        self.assertIn("deny", out.stdout)

    def test_allow_decision_does_not_write_gate(self) -> None:
        self.write_state({"decision": "allow", "risk_flags": [], "input_event_id": "sha256:e1"})
        self.write_event("sha256:e1")
        out = self.run_pretool()
        self.assertFalse(self.gate_path().exists())
        self.assertNotIn("permissionDecision", out.stdout)

    def test_absent_decision_does_not_write_gate(self) -> None:
        self.write_state(None)
        self.write_event("sha256:e1")
        out = self.run_pretool()
        self.assertFalse(self.gate_path().exists())
        self.assertNotIn("permissionDecision", out.stdout)

    def test_stale_block_lineage_mismatch_does_not_write_gate(self) -> None:
        # prior-turn block (e1) but latest event is e2 -> no match -> no write.
        self.write_state({"decision": "block", "risk_flags": ["x"], "input_event_id": "sha256:e1"})
        self.write_event("sha256:e2")
        out = self.run_pretool()
        self.assertFalse(self.gate_path().exists())
        self.assertNotIn("permissionDecision", out.stdout)

    def test_existing_block_gate_not_clobbered_and_stale_allows(self) -> None:
        # A prior block gate (e1) is on disk; this turn the model recorded allow and
        # the latest event is e2. derive must NOT clobber the gate; the enforcer must
        # treat the prior block gate as stale -> no deny.
        self.write_state({"decision": "allow", "risk_flags": [], "input_event_id": "sha256:e2"})
        self.write_event("sha256:e2")
        self.write_gate({
            "schema_version": "downstream-gates.v1",
            "platform": "claude",
            "session_id": self.session,
            "gate": "jailbreak-detector",
            "decision": "block",
            "opened": False,
            "rules": ["old-block"],
            "input_event_id": "sha256:e1",
            "input_digest": "sha256:d-old",
        })
        out = self.run_pretool()
        gate = json.loads(self.gate_path().read_text(encoding="utf-8"))
        self.assertEqual(gate["decision"], "block")  # not clobbered
        self.assertEqual(gate["input_event_id"], "sha256:e1")  # untouched
        self.assertNotIn("permissionDecision", out.stdout)  # stale -> enforcer allows


if __name__ == "__main__":
    unittest.main(verbosity=2)
