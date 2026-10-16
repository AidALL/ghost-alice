"""Tests for completion_check_validator.

Covers structural control parsing and compact full-control compatibility.
Declared criteria constrain claim references; partial controls require declared
unresolved criteria. These parser tests do not establish native proof acceptance,
which also requires the owning Core assessment.
"""

import unittest

import completion_check_validator as v


def _block(acceptance=True, *, criterion="c1", evidence="ran test X", verdict="pass", unverified="  - none"):
    lines = ["[completion-check]"]
    if acceptance:
        lines += ["- acceptance-criteria:", "  - c1: do the thing [source: user-said]"]
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
            "  - c1: do the thing [source: user-said]",
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


class TestExplicitCompletionRequirement(unittest.TestCase):
    def test_explicit_requirement_rejects_text_without_a_control_block(self):
        self.assertIsNotNone(v.validate_completion_text("Status.", require_completion_check=True))

    def test_explicit_requirement_rejects_empty_text(self):
        self.assertIsNotNone(v.validate_completion_text("", require_completion_check=True))

    def test_scoped_partial_control_satisfies_the_explicit_requirement(self):
        partial = _block(unverified="  - c2: observation unavailable").replace(
            "[completion-check]", "[partial-check]\n- scope: partial").replace(
            "- claim-evidence-map:", "  - c2: remaining condition [source: user-said]\n- claim-evidence-map:")
        self.assertIsNone(v.validate_completion_text(partial, require_completion_check=True))


class TestCompletionTextFinalSurfaceOrder(unittest.TestCase):
    def test_one_diagnostic_reports_all_independent_metadata_defects(self):
        malformed = _block(evidence=None, verdict="pass (9 checks)", unverified="  - unfinished work")
        result = v.validate_completion_text(malformed)
        for expected in ("include evidence",
                         "Invalid verdict value", "unverified section"):
            self.assertIn(expected, result)

    def test_completion_check_rejects_gate_state_after_completion_check(self):
        result = v.validate_completion_text(_final_text("completion", "summary", "gate", "io"))
        self.assertIsNotNone(result)
        self.assertIn("gate-state", result)

    def test_completion_check_allows_optional_trace_before_completion_check(self):
        result = v.validate_completion_text(_final_text("io", "completion", "summary"))
        self.assertIsNone(result)

    def test_completion_check_allows_completion_summary_io_trace_order(self):
        self.assertIsNone(v.validate_completion_text(_final_text("completion", "summary", "io")))

    def test_routine_text_without_completion_check_is_unchanged(self):
        text = "[io-trace]\n- commands-run: [none]\n\nRoutine explanation without a final claim."
        self.assertIsNone(v.validate_completion_text(text))


class TestCompletionMarkerIsHeaderOnly(unittest.TestCase):
    """looks_like_completion_claim must require a real block header."""

    def test_prose_mention_is_not_a_claim(self):
        prose = "The [completion-check] block lists claim-evidence-map and unverified sections."
        self.assertFalse(v.looks_like_completion_claim(prose))
        self.assertIsNotNone(v.validate_completion_text(prose, require_completion_check=True))

    def test_header_line_is_a_claim(self):
        text = "[completion-check]\n- claim-evidence-map:\n  - claim: x"
        self.assertTrue(v.looks_like_completion_claim(text))

    def test_fenced_completion_block_example_is_not_a_control(self):
        text = "Example format:\n```text\n[completion-check]\n- claim-evidence-map:\n```\nUse this shape."
        self.assertFalse(v.looks_like_completion_claim(text))
        self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_tilde_fenced_example_is_not_a_control(self):
        text = "Example:\n~~~text\n[completion-check]\n- claim-evidence-map:\n~~~\nThat is the format."
        self.assertFalse(v.looks_like_completion_claim(text))
        self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))


class TestMarkdownExampleBoundaries(unittest.TestCase):
    EXAMPLES = (
        "````text\n```\n[completion-check]\nSample payload.\n```\n````\n",
        "~~~~text\n~~~\n[completion-check]\nSample payload.\n~~~\n~~~~\n",
        "Example:\n\n    [completion-check]\n    Sample payload.\n",
        "\t[completion-check]\n\tSample payload.\n",
        "> This paragraph quotes an example:\nSample payload.\n[completion-check]\n",
    )

    def test_markdown_examples_do_not_supply_required_control_markers(self):
        for example in self.EXAMPLES:
            with self.subTest(example=example):
                self.assertFalse(v.looks_like_completion_claim(example))
                self.assertIsNotNone(v.validate_completion_text(example, require_completion_check=True))

    def test_valid_actual_completion_after_example_preserves_control_fields(self):
        for example in self.EXAMPLES:
            text = example + "\n" + _final_text("completion", "summary", "io")
            with self.subTest(example=example):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_shorter_fence_does_not_end_an_unclosed_long_fence(self):
        text = "````text\n```\nSample payload.\n[completion-check]\n"
        self.assertFalse(v.looks_like_completion_claim(text))

    def test_closing_fence_with_text_is_still_inside_example(self):
        text = "````text\n```` extra text\n[completion-check]\nSample payload.\n````\n"
        self.assertFalse(v.looks_like_completion_claim(text))

    def test_long_fence_in_list_is_still_an_example(self):
        text = "- Example:\n\n  ````text\n  ```\n  [completion-check]\n  Sample payload.\n  ```\n  ````\n"
        self.assertFalse(v.looks_like_completion_claim(text))

    def test_deeply_nested_list_fence_is_still_an_example(self):
        text = "- Outer:\n  - Example:\n\n    ````text\n    ```\n    [completion-check]\n    Sample payload.\n    ```\n    ````\n"
        self.assertFalse(v.looks_like_completion_claim(text))

    def test_quoted_list_paragraphs_can_continue_lazily(self):
        for prefix in ("> - ", "> 1. ", "> 2) ", "> 1. > "):
            text = prefix + "Example output:\nSample payload.\n[completion-check]\n"
            with self.subTest(text=text):
                self.assertFalse(v.looks_like_completion_claim(text))
                self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_actual_control_fields_after_quoted_list_are_preserved(self):
        text = "> 1. Example output:\nquoted continuation.\n\n" + _final_text("completion", "summary", "io")
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_explicit_indented_quote_paragraph_continuation_keeps_laziness(self):
        for first in ("> - Example output:", "> Example output:"):
            text = first + "\n>     continued example\nSample payload.\n[completion-check]\n"
            with self.subTest(text=text):
                self.assertFalse(v.looks_like_completion_claim(text))

    def nested_quote_fence(self, explicit=False):
        tail = "> Sample payload.\n> [completion-check]\n" if explicit else "Sample payload.\n[completion-check]\n"
        return "> - > ```\n>   > code\n>   > ```\n>\n> A quoted paragraph.\n" + tail

    def test_closed_nested_container_fence_allows_equivalent_lazy_quote_paragraph(self):
        for explicit in (False, True):
            text = self.nested_quote_fence(explicit)
            with self.subTest(explicit=explicit):
                self.assertFalse(v.looks_like_completion_claim(text))
                self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_nested_fence_literal_quote_or_list_markers_do_not_close_it(self):
        for literal in ("> ```", "- ```", "- > ```"):
            text = "> - > ```\n>   > " + literal + "\n>   > another code line\nSample payload.\n[completion-check]\n"
            with self.subTest(literal=literal):
                self.assertTrue(v.looks_like_completion_claim(text))

    def test_unclosed_nested_container_fence_keeps_unquoted_control_visible(self):
        text = "> - > ```\n>   > code\nSample payload.\n[completion-check]\n"
        self.assertTrue(v.looks_like_completion_claim(text))

    def test_changed_container_does_not_act_as_a_matching_fence_close(self):
        text = "> - > ```\n>   > code\n> ```\nSample payload.\n[completion-check]\n"
        self.assertTrue(v.looks_like_completion_claim(text))


class TestExplicitMultilineQuotedExamples(unittest.TestCase):
    def example(self, opening, closing):
        return ("A sample response is:\n" + opening + "Sample payload.\n"
                "[completion-check]\n- claim-evidence-map:\n" + closing)

    def test_balanced_multiline_example_does_not_supply_a_control_marker(self):
        for opening, closing in (("“", "”"), ('"', '"'), ("‘", "’"), ("'", "'")):
            text = self.example(opening, closing)
            with self.subTest(text=text):
                self.assertFalse(v.looks_like_completion_claim(text))
                self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_actual_control_block_after_quotation_remains_valid(self):
        for opening, closing in (("“", "”"), ('"', '"')):
            text = self.example(opening, closing) + "\n\n" + _final_text("completion", "summary", "io")
            with self.subTest(text=text):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_unclosed_or_mismatched_quote_cannot_hide_actual_controls(self):
        for opening, closing in (("“", ""), ('"', ""), ("“", '"'), ("‘", "”")):
            text = self.example(opening, closing)
            with self.subTest(text=text):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))

    def test_inline_quoted_evidence_in_actual_block_is_preserved(self):
        text = _final_text("completion", "summary", "io").replace("evidence: ran test X", 'evidence: "ran test X"')
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_unclosed_quote_does_not_pair_with_later_inline_evidence_quotes(self):
        text = ('Unclosed sample:\n"Sample payload.\n[completion-check]\n'
                'Actual evidence: "command"\n')
        self.assertTrue(v.looks_like_completion_claim(text))

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


class TestIndentedCompletionHeader(unittest.TestCase):
    """Structural validation accepts indented real control headers."""

    def test_indented_completion_header_does_not_crash(self):
        # An indented header returns a deny-reason string or None without raising IndexError.
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


class TestGateStatePlacement(unittest.TestCase):
    # Stop validation receives the final assistant message, not necessarily the opening commentary where [gate-state] is surfaced. Enforce placement only when the block is present in the validated text.

    def test_completion_surface_without_gate_state_is_allowed_in_hook_mode(self):
        text = _final_text("completion", "summary", "io")
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_completion_surface_with_gate_state_before_completion_check_is_allowed(self):
        text = _final_text("gate", "completion", "summary", "io")
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_gate_state_does_not_require_completion_controls(self):
        self.assertIsNone(v.validate_completion_text("[gate-state]\n- task-router: done"))


if __name__ == "__main__":
    unittest.main()
