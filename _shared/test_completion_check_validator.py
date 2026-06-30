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


class TestSkillsLoadedForms(unittest.TestCase):
    """skills-loaded must be accepted in inline / flow-list / nested forms.

    Regression guard for the over-narrow `[...]`-only matcher that produced
    false negatives on equivalent inline and nested serializations.
    """

    def _final(self, skills_block):
        completion_check = "\n".join(
            [
                "[completion-check]",
                "- verification-before-completion: done",
                "- skill-call: verification-before-completion (this turn)",
                "- claim-evidence-map:",
                "  - claim: did it",
                "    evidence: ran X",
                "    verdict: pass",
                "- unverified:",
                "  - none",
            ]
        )
        return completion_check + "\n\n[io-trace]\n" + skills_block

    def test_inline_form_accepted(self):
        self.assertIsNone(self._validate("- skills-loaded: verification-before-completion"))

    def test_inline_with_paren_accepted(self):
        self.assertIsNone(self._validate("- skills-loaded: verification-before-completion (this turn)"))

    def test_inline_csv_accepted(self):
        self.assertIsNone(self._validate("- skills-loaded: task-router, verification-before-completion"))

    def test_flow_list_accepted(self):
        self.assertIsNone(self._validate("- skills-loaded: [verification-before-completion]"))

    def test_nested_list_accepted(self):
        self.assertIsNone(self._validate("- skills-loaded:\n  - verification-before-completion"))

    def test_nested_list_with_following_field_accepted(self):
        block = "- skills-loaded:\n  - task-router\n  - verification-before-completion\n- commands-run: [x]"
        self.assertIsNone(self._validate(block))

    def test_missing_skill_rejected(self):
        result = self._validate("- skills-loaded: task-router")
        self.assertIsNotNone(result)
        self.assertIn("skills-loaded", result)

    def _validate(self, skills_block):
        return v.validate_completion_text(self._final(skills_block))


class TestExecutedWorkDetectorFalseBlocks(unittest.TestCase):
    """requires_completion_check must not fire on non-committal phrasing.

    Regression guard for false blocks on illustrative code, quoted text,
    conditional / goal framing, and negated closure verbs, while still firing
    on genuine executed-work claims.
    """

    def test_fenced_passing_output_is_not_a_claim(self):
        text = "Example of a passing run:\n```\n===== 12 passed =====\nAll tests passed\n```\nThat is the shape."
        self.assertFalse(v.requires_completion_check(text))

    def test_real_tests_pass_claim_still_detected(self):
        self.assertTrue(v.requires_completion_check("I ran it and all tests pass."))

    def test_quoted_completion_phrase_is_not_a_claim(self):
        self.assertFalse(
            v.requires_completion_check("You said 'the task is complete' but the build still fails.")
        )

    def test_conditional_lead_is_not_a_claim(self):
        self.assertFalse(v.requires_completion_check("Once the task is complete, deploy."))

    def test_goal_lead_with_separate_negation_is_not_a_claim(self):
        self.assertFalse(
            v.requires_completion_check(
                "Acceptance criteria: all tests pass and lint is clean; we are not there yet."
            )
        )

    def test_negated_closure_is_not_a_claim(self):
        self.assertFalse(v.requires_completion_check("The task is not complete yet."))

    def test_negation_does_not_hide_later_success_claim(self):
        self.assertTrue(v.requires_completion_check("The build is not clean but tests passed."))

    def test_plain_completion_still_detected(self):
        self.assertTrue(v.requires_completion_check("The task is complete."))


class TestCompletionMarkerIsHeaderOnly(unittest.TestCase):
    """looks_like_completion_claim must require a real block header."""

    def test_prose_mention_is_not_a_claim(self):
        prose = "The [completion-check] block lists claim-evidence-map and unverified sections."
        self.assertFalse(v.looks_like_completion_claim(prose))
        self.assertIsNone(v.validate_completion_text(prose, require_completion_check=True))

    def test_header_line_is_a_claim(self):
        text = "[completion-check]\n- claim-evidence-map:\n  - claim: x"
        self.assertTrue(v.looks_like_completion_claim(text))


class TestSkillsLoadedGateExactName(unittest.TestCase):
    """io-trace skills-loaded gate matches the canonical name, not a substring."""

    def _final(self, skills_block):
        completion_check = "\n".join(
            [
                "[completion-check]",
                "- verification-before-completion: done",
                "- skill-call: verification-before-completion (this turn)",
                "- claim-evidence-map:",
                "  - claim: did it",
                "    evidence: ran X",
                "    verdict: pass",
                "- unverified:",
                "  - none",
            ]
        )
        return completion_check + "\n\n[io-trace]\n" + skills_block

    def test_v2_suffix_is_rejected(self):
        result = v.validate_completion_text(self._final("- skills-loaded: [verification-before-completion-v2]"))
        self.assertIsNotNone(result)
        self.assertIn("skills-loaded", result)

    def test_paren_annotation_still_accepted(self):
        self.assertIsNone(
            v.validate_completion_text(self._final("- skills-loaded: verification-before-completion (this turn)"))
        )

    def test_nested_blank_gap_accepted(self):
        self.assertIsNone(
            v.validate_completion_text(self._final("- skills-loaded:\n\n  - verification-before-completion"))
        )

    def test_nested_prose_line_accepted(self):
        self.assertIsNone(
            v.validate_completion_text(
                self._final("- skills-loaded:\n  loaded the following:\n  - verification-before-completion")
            )
        )


class TestAllCompletionBlocksValidated(unittest.TestCase):
    """A malformed later [completion-check] block cannot hide behind a valid one."""

    def test_second_malformed_block_rejected(self):
        valid = "\n".join(
            [
                "[completion-check]",
                "- verification-before-completion: done",
                "- skill-call: verification-before-completion (this turn)",
                "- claim-evidence-map:",
                "  - claim: did it",
                "    evidence: ran X",
                "    verdict: pass",
                "- unverified:",
                "  - none",
                "",
                "[io-trace]",
                "- skills-loaded: [verification-before-completion]",
                "",
                "[completion-check]",
                "- claim-evidence-map:",
                "- unverified:",
                "  - none",
            ]
        )
        self.assertIsNotNone(v.validate_completion_text(valid))


class TestDetectorEdgeRegressions(unittest.TestCase):
    """Guards for false-passes/crash found by end-to-end hook verification."""

    def test_contraction_apostrophes_do_not_swallow_claim(self):
        # Contraction apostrophes must not act as quote delimiters (BUG #1).
        self.assertTrue(
            v.requires_completion_check("I won't lie: the work is complete; we don't ship yet.")
        )
        self.assertTrue(
            v.requires_completion_check("y'all the task is complete, ain't kidding around")
        )

    def test_real_single_quote_quotation_still_stripped(self):
        self.assertFalse(
            v.requires_completion_check("You said 'the task is complete' but the build still fails.")
        )

    def test_hypothetical_lead_does_not_exempt_asserted_main_clause(self):
        # A conditional preamble before a comma must not exempt the asserted
        # claim that follows it (BUG #2).
        self.assertTrue(
            v.requires_completion_check("If you're curious, I fixed the bug and all tests pass.")
        )
        self.assertTrue(
            v.requires_completion_check("After much effort the task is complete and all tests pass.")
        )

    def test_genuine_conditionals_still_allowed(self):
        self.assertFalse(v.requires_completion_check("Once the task is complete, deploy."))
        self.assertFalse(v.requires_completion_check("When the task is complete, notify me."))
        self.assertFalse(
            v.requires_completion_check(
                "Acceptance criteria: all tests pass and lint is clean; we are not there yet."
            )
        )

    def test_goal_lead_governs_comma_separated_list(self):
        # A goal/enumeration lead governs the whole sentence, so a comma-separated
        # goal list does not block on a later list item.
        self.assertFalse(
            v.requires_completion_check("Goal: all tests pass, lint is clean, docs updated.")
        )
        self.assertFalse(
            v.requires_completion_check("Next steps: fix the bug, run the tests.")
        )

    def test_goal_governance_stops_at_sentence_boundary(self):
        # A real claim in a new sentence after a goal line is still caught.
        self.assertTrue(
            v.requires_completion_check("Goal: ship it. The task is complete and all tests pass.")
        )

    def test_negated_status_with_later_claim_still_requires_completion_check(self):
        self.assertTrue(
            v.requires_completion_check("The task is not complete but the install doctor tests passed.")
        )

    def test_indented_completion_header_does_not_crash(self):
        # An indented header must not raise IndexError (BUG #3); it returns a
        # deny-reason string or None, never an exception.
        indented = (
            "   [completion-check]\n"
            "- verification-before-completion: done\n"
            "- skill-call: verification-before-completion (this turn)\n"
            "- claim-evidence-map:\n"
            "  - claim: x\n"
            "    evidence: y\n"
            "    verdict: pass\n"
            "- unverified:\n"
            "  - none"
        )
        result = v.validate_completion_text(indented, require_completion_check=True)
        self.assertTrue(result is None or isinstance(result, str))


if __name__ == "__main__":
    unittest.main()
