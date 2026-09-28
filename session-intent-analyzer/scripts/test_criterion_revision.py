"""Completion evidence must follow the condition it actually verified.

Dependencies: Python 3.11+ standard library only.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def ledger_module():
    spec = importlib.util.spec_from_file_location(
        "criterion_revision_ledger", Path(__file__).with_name("session_intent_ledger.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CriterionRevisionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = ledger_module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.criteria = [
            {"id": "report", "summary": "Check the approved report revision 1", "source": "user-explicit"},
            {"id": "readonly", "summary": "Keep source data unchanged", "source": "user-explicit"},
        ]
        self.write(self.criteria)
        for criterion in self.criteria:
            self.ledger.mark_acceptance_criterion_met(
                root=self.root, platform="codex", session_id="test-session",
                criterion_id=criterion["id"], completion_check_digest="a" * 64,
                expected_criterion=dict(criterion, admitted=True))

    def write(self, criteria):
        self.paths = self.ledger.record_turn(
            root=self.root, platform="codex", session_id="test-session",
            intent_delta={"acceptance_criteria": criteria})

    def snapshot(self):
        return {c["id"]: c for c in self.ledger.consumer_snapshot(self.paths["state"])["acceptance_criteria"]}

    def test_changed_condition_reopens_only_affected_criterion(self):
        before = self.snapshot()
        self.write([dict(self.criteria[0], summary="Check the approved report revision 2")])
        after = self.snapshot()
        self.assertEqual(after["report"]["status"], "unmet")
        self.assertNotIn("met_completion_check_digest", after["report"])
        self.assertNotIn("met_at", after["report"])
        self.assertEqual(after["readonly"], before["readonly"])

    def test_repeating_identical_condition_preserves_evidence(self):
        before = self.snapshot()
        self.write([dict(self.criteria[0])])
        self.assertEqual(self.snapshot(), before)

    def test_incoming_met_cannot_reuse_proof_for_changed_condition(self):
        self.write([dict(self.criteria[0], summary="Verify the final revision 3", status="met",
                         met_completion_check_digest="b" * 64)])
        self.assertEqual(self.snapshot()["report"]["status"], "unmet")
        self.assertNotIn("met_completion_check_digest", self.snapshot()["report"])

    def test_withdrawn_admission_cannot_remain_complete(self):
        self.write([dict(self.criteria[0], admitted=False)])
        self.assertFalse(self.snapshot()["report"]["admitted"])
        self.assertEqual(self.snapshot()["report"]["status"], "unmet")

    def test_old_proof_remains_historical_after_correction(self):
        self.write([dict(self.criteria[0], summary="Verify the revised condition")])
        events = self.ledger.read_session_events(root=self.root, platform="codex", session_id="test-session")
        old = [e for e in events if e.get("event") == "acceptance-criterion-met" and e.get("criterion_id") == "report"]
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0]["completion_check_digest"], "a" * 64)
        self.assertEqual(self.snapshot()["report"]["status"], "unmet")


if __name__ == "__main__":
    unittest.main()
