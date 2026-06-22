"""Tests for completion_check_validator.

Covers the compact completion-check contract: the claim-evidence-map honesty
core (claim + evidence + verdict, unverified=none) is always required, while the
separate acceptance-criteria enumeration is optional. Full forms that include
acceptance-criteria keep their stricter criterion-binding check.
"""

import unittest

import completion_check_validator as v


def _block(acceptance=True, *, criterion="c1", evidence="ran test X", verdict="pass", unverified="  - none"):
    lines = ["[completion-check]"]
    if acceptance:
        lines += ["- acceptance-criteria:", "  - c1: do the thing [source: user-explicit]"]
    lines += ["- claim-evidence-map:", "  - claim: did the thing"]
    if criterion is not None:
        lines.append(f"    criterion: {criterion}")
    if evidence is not None:
        lines.append(f"    evidence: {evidence}")
    if verdict is not None:
        lines.append(f"    verdict: {verdict}")
    lines += ["- unverified:", unverified]
    return "\n".join(lines)


class TestCompletionEvidenceMap(unittest.TestCase):
    def test_full_form_valid(self):
        self.assertIsNone(v.validate_completion_evidence_map(_block(acceptance=True)))

    def test_compact_form_without_acceptance_criteria_valid(self):
        # No acceptance-criteria block; claim-evidence-map is self-contained.
        block = _block(acceptance=False, criterion=None)
        self.assertIsNone(v.validate_completion_evidence_map(block))

    def test_compact_form_missing_evidence_invalid(self):
        block = _block(acceptance=False, criterion=None, evidence=None)
        result = v.validate_completion_evidence_map(block)
        self.assertIsNotNone(result)
        self.assertIn("evidence", result)

    def test_compact_form_unverified_verdict_invalid(self):
        block = _block(acceptance=False, criterion=None, verdict="unverified")
        result = v.validate_completion_evidence_map(block)
        self.assertIsNotNone(result)
        self.assertIn("unverified", result.lower())

    def test_empty_claim_evidence_map_invalid(self):
        block = "\n".join(["[completion-check]", "- claim-evidence-map:", "- unverified:", "  - none"])
        result = v.validate_completion_evidence_map(block)
        self.assertIsNotNone(result)
        self.assertIn("claim-evidence-map", result)

    def test_full_form_bad_criterion_reference_invalid(self):
        # acceptance-criteria present but entry references an unknown id -> strict check stays.
        block = _block(acceptance=True, criterion="c2")
        result = v.validate_completion_evidence_map(block)
        self.assertIsNotNone(result)
        self.assertIn("criterion", result)

    def test_compact_form_unverified_section_not_none_invalid(self):
        block = _block(acceptance=False, criterion=None, unverified="  - something remains")
        result = v.validate_completion_evidence_map(block)
        self.assertIsNotNone(result)


def _final_text(*parts):
    completion_check = "\n".join(
        [
            "[completion-check]",
            "- verification-before-completion: done",
            "- skill-call: verification-before-completion (this turn)",
            "- acceptance-criteria:",
            "  - c1: do the thing [source: user-explicit]",
            "- claim-evidence-map:",
            "  - claim: did the thing",
            "    criterion: c1",
            "    evidence: ran test X",
            "    verdict: pass",
            "- unverified:",
            "  - none",
        ]
    )
    io_trace = "\n".join(
        [
            "[io-trace]",
            "- commands-run: [python -m unittest _shared.test_completion_check_validator]",
            "- skills-loaded: [verification-before-completion]",
        ]
    )
    gate_state = "\n".join(["[gate-state]", "- task-router: done"])
    available = {
        "completion": completion_check,
        "gate": gate_state,
        "io": io_trace,
        "summary": "Short summary.",
    }
    return "\n\n".join(available[part] for part in parts)


class TestCompletionTextFinalSurfaceOrder(unittest.TestCase):
    def test_completion_check_rejects_gate_state_after_completion_check(self):
        result = v.validate_completion_text(_final_text("completion", "summary", "gate", "io"))
        self.assertIsNotNone(result)
        self.assertIn("gate-state", result)

    def test_completion_check_rejects_io_trace_before_completion_check(self):
        result = v.validate_completion_text(_final_text("io", "completion", "summary"))
        self.assertIsNotNone(result)
        self.assertIn("io-trace", result)

    def test_completion_check_allows_completion_summary_io_trace_order(self):
        self.assertIsNone(v.validate_completion_text(_final_text("completion", "summary", "io")))

    def test_routine_text_without_completion_check_is_unchanged(self):
        text = "[io-trace]\n- commands-run: [none]\n\nRoutine explanation without a final claim."
        self.assertIsNone(v.validate_completion_text(text))


if __name__ == "__main__":
    unittest.main()
