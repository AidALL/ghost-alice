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

    def test_fenced_completion_block_example_is_not_an_executed_claim(self):
        text = "Example format:\n```text\n[completion-check]\n- claim-evidence-map:\n```\nUse this shape."
        self.assertFalse(v.looks_like_completion_claim(text))
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_explanation_label_does_not_excuse_unsupported_actual_closure(self):
        text = "[task-router]\n- work-kind: nonexecution\n\nExplanation: the task is complete."
        self.assertTrue(v.requires_completion_check(text))
        self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_tilde_fenced_example_is_not_an_executed_claim(self):
        text = "Example:\n~~~text\n[completion-check]\n- claim-evidence-map:\n~~~\nThat is the format."
        self.assertFalse(v.looks_like_completion_claim(text))
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))


class TestMarkdownExampleBoundaries(unittest.TestCase):
    EXAMPLES = (
        "````text\n```\n[completion-check]\nThe task is complete.\n```\n````\n",
        "~~~~text\n~~~\n[completion-check]\nThe task is complete.\n~~~\n~~~~\n",
        "Example:\n\n    [completion-check]\n    The task is complete.\n",
        "\t[completion-check]\n\tThe task is complete.\n",
        "> This paragraph quotes an example:\nThe task is complete.\n[completion-check]\n",
    )

    def test_actual_markdown_examples_are_neither_markers_nor_closure_claims(self):
        for example in self.EXAMPLES:
            with self.subTest(example=example):
                self.assertFalse(v.looks_like_completion_claim(example))
                self.assertFalse(v.requires_completion_check(example))
                self.assertIsNone(v.validate_completion_text(example, require_completion_check=True))

    def test_real_closure_outside_each_example_still_requires_evidence(self):
        for example in self.EXAMPLES:
            for text in (example + "\nThe task is complete.", "The task is complete.\n\n" + example):
                with self.subTest(text=text):
                    self.assertFalse(v.looks_like_completion_claim(text))
                    self.assertTrue(v.requires_completion_check(text))
                    self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_valid_actual_completion_after_example_preserves_control_fields(self):
        for example in self.EXAMPLES:
            text = example + "\n" + _final_text("completion", "summary", "io")
            with self.subTest(example=example):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_shorter_fence_does_not_end_an_unclosed_long_fence(self):
        text = "````text\n```\nThe task is complete.\n[completion-check]\n"
        self.assertFalse(v.looks_like_completion_claim(text))
        self.assertFalse(v.requires_completion_check(text))

    def test_indentation_cannot_hide_real_paragraph_continuation(self):
        self.assertTrue(v.requires_completion_check("Result:\n    The task is complete."))

    def test_closing_fence_with_text_is_still_inside_example(self):
        text = "````text\n```` extra text\n[completion-check]\nThe task is complete.\n````\n"
        self.assertFalse(v.looks_like_completion_claim(text))
        self.assertFalse(v.requires_completion_check(text))

    def test_list_paragraph_indentation_does_not_hide_a_real_closure(self):
        for text in ("- Result:\n    The task is complete.",
                     "- Result:\n\n    The task is complete."):
            with self.subTest(text=text):
                self.assertTrue(v.requires_completion_check(text))

    def test_long_fence_in_list_is_still_an_example(self):
        text = "- Example:\n\n  ````text\n  ```\n  [completion-check]\n  The task is complete.\n  ```\n  ````\n"
        self.assertFalse(v.looks_like_completion_claim(text))
        self.assertFalse(v.requires_completion_check(text))

    def test_deeply_nested_list_fence_is_still_an_example(self):
        text = "- Outer:\n  - Example:\n\n    ````text\n    ```\n    [completion-check]\n    The task is complete.\n    ```\n    ````\n"
        self.assertFalse(v.looks_like_completion_claim(text))
        self.assertFalse(v.requires_completion_check(text))

    def test_quoted_list_paragraphs_can_continue_lazily(self):
        for prefix in ("> - ", "> 1. ", "> 2) ", "> 1. > "):
            text = prefix + "Example output:\nThe task is complete.\n[completion-check]\n"
            with self.subTest(text=text):
                self.assertFalse(v.looks_like_completion_claim(text))
                self.assertFalse(v.requires_completion_check(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_quoted_list_paragraphs_end_at_blank_or_block_interruption(self):
        for interruption in ("\n", "# Outside\n", "---\n", "- Outside\n"):
            text = "> - Example output:\n" + interruption + "The task is complete."
            with self.subTest(text=text):
                self.assertTrue(v.requires_completion_check(text))
                self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_quoted_nonparagraph_blocks_do_not_acquire_lazy_paragraphs(self):
        for first in ("> ```text", "> - ```text", "> # Heading", "> - # Heading", ">     code", "> -     code", "> ---"):
            text = first + "\nThe task is complete."
            with self.subTest(text=text):
                self.assertTrue(v.requires_completion_check(text))

    def test_actual_control_fields_after_quoted_list_are_preserved(self):
        text = "> 1. Example output:\nquoted continuation.\n\n" + _final_text("completion", "summary", "io")
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_quoted_fence_body_does_not_enable_laziness_for_outside_claim(self):
        for text in ("> ```text\n> example body\nThe task is complete.",
                     "> - ```text\n>   example body\nThe task is complete."):
            with self.subTest(text=text):
                self.assertTrue(v.requires_completion_check(text))

    def test_explicit_indented_quote_paragraph_continuation_keeps_laziness(self):
        for first in ("> - Example output:", "> Example output:"):
            text = first + "\n>     continued example\nThe task is complete.\n[completion-check]\n"
            with self.subTest(text=text):
                self.assertFalse(v.looks_like_completion_claim(text))
                self.assertFalse(v.requires_completion_check(text))

    def test_quoted_list_heading_and_code_leaves_still_end_paragraph_laziness(self):
        for continuation in (">     # Quoted heading", ">\n>       code"):
            text = "> - Example output:\n" + continuation + "\nThe task is complete."
            with self.subTest(text=text):
                self.assertTrue(v.requires_completion_check(text))

    def nested_quote_fence(self, explicit=False):
        tail = "> The task is complete.\n> [completion-check]\n" if explicit else "The task is complete.\n[completion-check]\n"
        return "> - > ```\n>   > code\n>   > ```\n>\n> A quoted paragraph.\n" + tail

    def test_closed_nested_container_fence_allows_equivalent_lazy_quote_paragraph(self):
        for explicit in (False, True):
            text = self.nested_quote_fence(explicit)
            with self.subTest(explicit=explicit):
                self.assertFalse(v.looks_like_completion_claim(text))
                self.assertFalse(v.requires_completion_check(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_outside_interruptions_after_nested_fence_keep_real_claims(self):
        for interruption in ("\n", "# Outside\n", "---\n", "- Outside\n"):
            text = self.nested_quote_fence().split("The task is complete.")[0] + interruption + "The task is complete."
            with self.subTest(interruption=interruption):
                self.assertTrue(v.requires_completion_check(text))

    def test_nested_fence_literal_quote_or_list_markers_do_not_close_it(self):
        for literal in ("> ```", "- ```", "- > ```"):
            text = "> - > ```\n>   > " + literal + "\n>   > another code line\nThe task is complete.\n[completion-check]\n"
            with self.subTest(literal=literal):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertTrue(v.requires_completion_check(text))

    def test_unclosed_nested_container_fence_keeps_unquoted_claim_visible(self):
        text = "> - > ```\n>   > code\nThe task is complete.\n[completion-check]\n"
        self.assertTrue(v.looks_like_completion_claim(text))
        self.assertTrue(v.requires_completion_check(text))

    def test_changed_container_does_not_act_as_a_matching_fence_close(self):
        text = "> - > ```\n>   > code\n> ```\nThe task is complete.\n[completion-check]\n"
        self.assertTrue(v.looks_like_completion_claim(text))
        self.assertTrue(v.requires_completion_check(text))


class TestExplicitMultilineQuotedExamples(unittest.TestCase):
    def example(self, opening, closing):
        return ("A sample response is:\n" + opening + "The task is complete.\n"
                "[completion-check]\n- claim-evidence-map:\n" + closing)

    def test_balanced_multiline_example_is_neither_marker_nor_closure(self):
        for opening, closing in (("“", "”"), ('"', '"'), ("‘", "’"), ("'", "'")):
            text = self.example(opening, closing)
            with self.subTest(text=text):
                self.assertFalse(v.looks_like_completion_claim(text))
                self.assertFalse(v.requires_completion_check(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_outside_claim_is_preserved_before_or_after_balanced_quote(self):
        for opening, closing in (("“", "”"), ('"', '"')):
            example = self.example(opening, closing)
            for text in ("The task is complete.\n\n" + example, example + "\n\nThe task is complete."):
                with self.subTest(text=text):
                    self.assertFalse(v.looks_like_completion_claim(text))
                    self.assertTrue(v.requires_completion_check(text))
                    self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_actual_control_block_after_quotation_remains_valid(self):
        for opening, closing in (("“", "”"), ('"', '"')):
            text = self.example(opening, closing) + "\n\n" + _final_text("completion", "summary", "io")
            with self.subTest(text=text):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_unclosed_or_mismatched_quote_cannot_hide_actual_claims(self):
        for opening, closing in (("“", ""), ('"', ""), ("“", '"'), ("‘", "”")):
            text = self.example(opening, closing)
            with self.subTest(text=text):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertTrue(v.requires_completion_check(text))
                self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_apostrophes_across_lines_cannot_swallow_real_closure(self):
        text = "I won't lie:\nThe task is complete.\nWe don't ship yet."
        self.assertTrue(v.requires_completion_check(text))

    def test_inline_quoted_evidence_in_actual_block_is_preserved(self):
        text = _final_text("completion", "summary", "io").replace("evidence: ran test X", 'evidence: "ran test X"')
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_outside_prose_on_closing_quote_line_is_preserved(self):
        text = self.example("“", "”") + " The task is complete."
        self.assertFalse(v.looks_like_completion_claim(text))
        self.assertTrue(v.requires_completion_check(text))

    def test_unclosed_quote_does_not_pair_with_later_inline_evidence_quotes(self):
        text = ('Unclosed sample:\n"The task is complete.\n[completion-check]\n'
                'Actual evidence: "command"\n')
        self.assertTrue(v.looks_like_completion_claim(text))
        self.assertTrue(v.requires_completion_check(text))

    def test_complete_inline_quote_boundaries_cannot_hide_outside_closure(self):
        for literal in ("OK.", "pending.", "(Next section)"):
            text = f'The command returned "{literal}"\n\nThe task is complete.\n\n"[io-trace]"'
            with self.subTest(text=text):
                self.assertTrue(v.requires_completion_check(text))
                self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_inline_quotes_cannot_hide_a_real_unquoted_control_block(self):
        for literal in ("OK.", "pending.", "(Next section)"):
            text = f'The command returned "{literal}"\n\n' + _final_text("completion", "summary", "io") + '\n\n"[io-trace]"'
            with self.subTest(text=text):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_inline_quotes_cannot_hide_a_malformed_unquoted_control_block(self):
        text = 'The command returned "OK."\n\n[completion-check]\n- claim-evidence-map:\n\n"[io-trace]"'
        self.assertTrue(v.looks_like_completion_claim(text))
        self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))


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
        # A conditional preamble before a comma must not exempt the asserted claim that follows it (BUG #2).
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
        # A goal/enumeration lead governs the whole sentence, so a comma-separated goal list does not block on a later list item.
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
        # An indented header must not raise IndexError (BUG #3); it returns a deny-reason string or None, never an exception.
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


class TestKoreanNegatedStatusNotABlock(unittest.TestCase):
    "Korean negation is post-verbal; stripping only the negation particle left\n    the closure word intact and false-blocked an honest 'not complete' report."

    def test_korean_post_verbal_negation_is_not_a_claim(self):
        for phrase in (
            "작업이 완료되지 않았다.",
            "테스트가 통과하지 못했다.",
            "작업이 아직 완료 안 됐다.",
        ):
            self.assertFalse(v.requires_completion_check(phrase), phrase)

    def test_korean_prefixed_incomplete_status_is_not_a_claim(self):
        for phrase in (
            "수정 작업은 아직 미완료입니다.",
            "같은 종료 훅이 계속 재시도하고 있습니다. 현재 실행을 중지한 뒤 "
            "Autopilot 훅을 수동으로 임시 해제해야 합니다. 수정 작업은 아직 미완료입니다.",
            "작업 미완료",
            "작업이 미완료된 상태입니다.",
            "설치본 적용과 Claude PTY 검증은 확인되지 않았습니다.",
        ):
            with self.subTest(phrase=phrase):
                self.assertFalse(v.requires_completion_check(phrase))
                self.assertIsNone(v.validate_completion_text(phrase, require_completion_check=True))

    def test_incomplete_status_does_not_hide_a_real_claim(self):
        for phrase in (
            "수정했습니다. 테스트가 통과했습니다.",
            "빌드는 미완료지만 테스트가 통과했습니다.",
            "테스트가 통과했고 빌드는 미완료입니다.",
            "수정 작업은 아직 미완료입니다. 테스트가 통과했습니다.",
        ):
            with self.subTest(phrase=phrase):
                self.assertTrue(v.requires_completion_check(phrase))
                self.assertIsNotNone(v.validate_completion_text(phrase, require_completion_check=True))

    def test_korean_real_completion_still_detected(self):
        self.assertTrue(v.requires_completion_check("작업 완료했습니다."))
        self.assertTrue(v.requires_completion_check("테스트 통과했다."))
        for phrase in (
            "완료했습니다.",
            "수정했습니다.",
            "구현했습니다.",
            "해결했습니다.",
            "반영했습니다.",
            "끝냈습니다.",
        ):
            self.assertTrue(v.requires_completion_check(phrase), phrase)


class TestHonestNegativeStatusNotABlock(unittest.TestCase):
    def test_korean_honest_negative_verification_status_is_not_a_claim(self):
        for phrase in (
            "테스트가 통과했다고 말할 수는 없습니다.",
            "구현 완료 여부는 확인하지 못했습니다.",
            "작업이 완료라고 보기 어렵습니다.",
            "작업이 완료된 것은 아닙니다.",
            "테스트가 통과한 것은 아닙니다.",
            "작업을 완료했다고 볼 수 없습니다.",
        ):
            self.assertFalse(v.requires_completion_check(phrase), phrase)
            self.assertIsNone(v.validate_completion_text(phrase, require_completion_check=True), phrase)

    def test_english_honest_negative_verification_status_is_not_a_claim(self):
        for phrase in (
            "I haven't verified that the tests pass.",
            "I don't think the tests passed.",
        ):
            self.assertFalse(v.requires_completion_check(phrase), phrase)
            self.assertIsNone(v.validate_completion_text(phrase, require_completion_check=True), phrase)

    def test_honest_negative_does_not_hide_later_real_claim(self):
        self.assertTrue(
            v.requires_completion_check(
                "I haven't verified that the tests pass, but the implementation is complete."
            )
        )

    def test_korean_honest_negative_does_not_hide_separate_real_claim(self):
        self.assertTrue(v.requires_completion_check("작업 완료했고 원인은 확인하지 못했습니다."))


class TestGateStatePlacement(unittest.TestCase):
    # Stop validation receives the final assistant message, not necessarily the opening commentary where [gate-state] is surfaced. Enforce placement only when the block is present in the validated text.

    def test_completion_surface_without_gate_state_is_allowed_in_hook_mode(self):
        text = _final_text("completion", "summary", "io")
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_completion_surface_with_gate_state_before_completion_check_is_allowed(self):
        text = _final_text("gate", "completion", "summary", "io")
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_routine_explanation_is_unaffected_by_gate_state_placement(self):
        self.assertIsNone(
            v.validate_completion_text("이 함수는 경로를 정규화한다.", require_completion_check=True)
        )


if __name__ == "__main__":
    unittest.main()
