"""Tests for completion_check_validator.

Covers structural control parsing and compact full-control compatibility.
Declared criteria constrain claim references; partial controls require declared
unresolved criteria. These parser tests do not establish native proof acceptance,
which also requires the owning Core assessment.
"""

import unittest
from unittest import mock

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

    def test_retired_printed_blocks_after_completion_check_are_ordinary_text(self):
        # Decisions 7 and 14 retired the printed [io-trace] and [gate-state] blocks; neither has a placement rule.
        self.assertIsNone(v.validate_completion_text(_final_text("completion", "summary", "gate", "io")))

    def test_control_fields_follow_only_printed_control_blocks(self):
        # The contract prints only these blocks; their nested fields are evidence data.
        self.assertEqual(v._CONTROL_BLOCKS, {"boundary-contract", "completion-check", "partial-check"})

    def test_routine_text_without_completion_check_is_unchanged(self):
        text = "[boundary-contract]\n- phase: discovery\n\nRoutine explanation without a final claim."
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
        'Example: "Sample payload."\n```\n[completion-check]\n```\n',
        "    [completion-check]\n    Sample payload.\n",
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

    def test_shorter_fence_does_not_end_a_long_fence(self):
        text = "````text\n```\nSample payload.\n[completion-check]\n````\n"
        self.assertFalse(v.looks_like_completion_claim(text))

    def test_an_unclosed_fence_does_not_hide_a_later_control(self):
        # A summary with a stray fence must not take the completion block after it out of the Stop comparison.
        for opening in ("```\ncode\n", "````text\n```\ncode\n", "- item\n\n  ```\n  code\n",
                        "[boundary-contract]\n- id: fix\n\n```\ncode\n"):
            text = "Summary.\n\n" + opening + "\n" + _final_text("completion", "summary", "io")
            with self.subTest(opening=opening):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))
                self.assertTrue(v.extract_original_proof(text).startswith("[completion-check]"))

    def test_an_unclosed_fence_with_no_control_after_it_is_no_claim(self):
        self.assertFalse(v.looks_like_completion_claim("Summary.\n```\ncode\n"))

    def test_a_control_inside_a_fence_that_never_closes_counts_as_a_control(self):
        # Code cannot tell a sample from a real block here. A wrong notice costs less than a claim Stop never compares.
        self.assertTrue(v.looks_like_completion_claim("Format:\n```\n[completion-check]\n- unverified:\n"))

    def test_an_unclosed_fence_after_a_completion_block_stays_hidden(self):
        # The block before it already keeps the answer in the Stop comparison.
        block = _final_text("completion", "summary", "io")
        text = block + "\n\nFormat:\n```\n[completion-check]\n- unverified:\n"
        self.assertTrue(v.looks_like_completion_claim(text))
        self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))
        self.assertTrue(v.extract_original_proof(text).startswith("[completion-check]"))
        # Text after the fence cannot turn the prose before the block into a quoted example.
        quoted = "'tis done.\n" + block + "\n```\nthe users' code\n"
        self.assertTrue(v.looks_like_completion_claim(quoted))
        # A fence inside the block hides the fields after it from the check of the block.
        split = block.replace("- unverified:", "```\n- unverified:")
        self.assertIsNotNone(v.validate_completion_text(split, require_completion_check=True))

    def test_an_unclosed_fence_after_a_partial_block_stays_hidden(self):
        block = _final_text("completion", "summary", "io").replace(
            "[completion-check]", "[partial-check]\n- scope: partial").replace(
            "- unverified:\n  - none", "- unverified:\n  - c2: not checked")
        stripped = v._strip_block_examples(block + "\n\nFormat:\n```\n[partial-check]\n- unverified:\n")
        self.assertEqual(stripped.count("[partial-check]"), 1)

    def test_equal_fences_that_never_close_take_two_passes(self):
        # A closing line for a later opener would also have closed the first one, so the later ones are not rescanned.
        text = "```a\n" * 5000 + "\n" + _final_text("completion", "summary", "io")
        with mock.patch.object(v, "_mask_block_lines", wraps=v._mask_block_lines) as passes:
            stripped = v._strip_block_examples(text)
        self.assertEqual(passes.call_count, 2)
        self.assertEqual(stripped.count("[completion-check]"), 1)
        self.assertEqual(len(v._split_lines(stripped)), len(v._split_lines(text)))

    def test_a_fence_that_can_still_close_stays_a_fence_after_one_that_never_closes(self):
        for name, text in (("another character", "~~~\n````\nsample\n````\n"),
                           ("a shorter opening", "````\n```   \nsample\n```\n"),
                           ("another list indent", "```\n- ````\n   sample\nx\n")):
            with self.subTest(name):
                self.assertNotIn("sample", v._strip_block_examples(text))

    def test_a_fence_opened_on_the_last_line_keeps_the_line_count(self):
        self.assertEqual(v._strip_block_examples("Summary.\n```"), "Summary.\n")

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
            text = f'The command returned "{literal}"\n\n' + _final_text("completion", "summary") + '\n\n"[boundary-contract]"'
            with self.subTest(text=text):
                self.assertTrue(v.looks_like_completion_claim(text))
                self.assertIsNone(v.validate_completion_text(text, require_completion_check=True))

    def test_inline_quotes_cannot_hide_a_malformed_unquoted_control_block(self):
        text = 'The command returned "OK."\n\n[completion-check]\n- claim-evidence-map:\n\n"[boundary-contract]"'
        self.assertTrue(v.looks_like_completion_claim(text))
        self.assertIsNotNone(v.validate_completion_text(text, require_completion_check=True))


class TestAllCompletionBlocksValidated(unittest.TestCase):
    """A malformed later [completion-check] block cannot hide behind a valid one."""

    def test_second_malformed_block_rejected(self):
        valid = "\n".join(
            [
                "[completion-check]",
                "- claim-evidence-map:",
                "  - claim: did it",
                "    evidence: ran X",
                "    verdict: pass",
                "- unverified:",
                "  - none",
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
            "- claim-evidence-map:\n"
            "  - claim: x\n"
            "    evidence: y\n"
            "    verdict: pass\n"
            "- unverified:\n"
            "  - none"
        )
        result = v.validate_completion_text(indented, require_completion_check=True)
        self.assertTrue(result is None or isinstance(result, str))


# A fully-valid completion-check response. Used as the baseline that returns None, and mutated per-test to trigger one deny condition at a time.
VALID_TEXT = """\
The change is complete and all tests pass.

[completion-check]
- acceptance-criteria:
  - C1: validator enforces the completion contract [source: user-said]
  - C2: stdlib only [source: user-said]
- claim-evidence-map:
  - claim: the validator enforces the completion contract
    criterion: C1
    evidence: ran _shared/test_completion_check_validator.py -> OK
    verdict: pass
  - claim: no new dependencies were added
    criterion: C2
    evidence: module imports only re from the standard library
    verdict: pass
- unverified:
  - none
- evidence: ran python3 _shared/test_completion_check_validator.py -v
"""


class LooksLikeCompletionClaimTest(unittest.TestCase):
    def test_completion_check_marker_is_a_claim(self) -> None:
        self.assertTrue(v.looks_like_completion_claim("[completion-check]\n- x"))

    def test_keyword_in_prose_without_marker_is_not_a_claim(self) -> None:
        # This marker helper detects explicit completion controls only. Native routing also checks executed-work assertions through requires_completion_check.
        self.assertFalse(v.looks_like_completion_claim("The build is done."))
        self.assertFalse(v.looks_like_completion_claim("work complete"))
        self.assertFalse(v.looks_like_completion_claim("I recommend option A."))
        self.assertFalse(v.looks_like_completion_claim("acceptance criteria: concrete success criteria"))

    def test_plain_non_claim_text(self) -> None:
        self.assertFalse(v.looks_like_completion_claim("Here is some neutral text about cats."))

    def test_done_only_inside_control_blocks_is_not_a_claim(self) -> None:
        # Other control blocks do not count as explicit completion markers for this helper.
        text = (
            "[boundary-contract]\n"
            "- id: done-contract\n"
            "- phase: verification\n"
            "- next-allowed-actions:\n"
            "  - report the done status\n"
        )
        self.assertFalse(v.looks_like_completion_claim(text))


class ExtractControlBlockTest(unittest.TestCase):
    def test_extracts_named_block_until_next_header(self) -> None:
        text = (
            "prose\n"
            "[completion-check]\n"
            "- a: 1\n"
            "- b: 2\n"
            "[boundary-contract]\n"
            "- id: x\n"
        )
        block = v.extract_control_block(text, "completion-check")
        self.assertIn("- a: 1", block)
        self.assertIn("- b: 2", block)
        self.assertNotIn("- id: x", block)

    def test_missing_block_returns_empty_string(self) -> None:
        self.assertEqual(v.extract_control_block("no blocks here", "completion-check"), "")


class FieldSectionTest(unittest.TestCase):
    def test_extract_top_level_field_section_stops_at_next_top_level(self) -> None:
        block = (
            "- acceptance-criteria:\n"
            "  - C1: foo\n"
            "  - C2: bar\n"
            "- unverified:\n"
            "  - none\n"
        )
        section = v.extract_top_level_field_section(block, "acceptance-criteria")
        self.assertIn("- C1: foo", section)
        self.assertIn("- C2: bar", section)
        self.assertNotIn("none", section)

    def test_section_is_none_true_only_for_none_lines(self) -> None:
        self.assertTrue(v.section_is_none("- none"))
        self.assertTrue(v.section_is_none("  - none\n  - none"))
        self.assertFalse(v.section_is_none(""))  # empty -> False
        self.assertFalse(v.section_is_none("- C1: something unresolved"))

    def test_extract_acceptance_criteria_ids_skips_placeholders(self) -> None:
        block = (
            "- acceptance-criteria:\n"
            "  - C1: real id\n"
            "  - <criterion-id>: placeholder\n"
            "  - C2: another\n"
        )
        ids = v.extract_acceptance_criteria_ids(block)
        self.assertEqual(ids, ["C1", "C2"])

    def test_extract_claim_evidence_entries(self) -> None:
        block = (
            "- claim-evidence-map:\n"
            "  - claim: thing one\n"
            "    criterion: C1\n"
            "    evidence: ran a command\n"
            "    verdict: pass\n"
            "  - claim: thing two\n"
            "    criterion: C2\n"
            "    evidence: read a file\n"
            "    verdict: fail\n"
        )
        entries = v.extract_claim_evidence_entries(block)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["claim"], "thing one")
        self.assertEqual(entries[0]["criterion"], "C1")
        self.assertEqual(entries[0]["evidence"], "ran a command")
        self.assertEqual(entries[0]["verdict"], "pass")
        self.assertEqual(entries[1]["verdict"], "fail")


class ValidateCompletionResponseTest(unittest.TestCase):
    def test_valid_text_returns_none(self) -> None:
        self.assertIsNone(v.validate_completion_text(VALID_TEXT))

    def test_non_completion_claim_returns_none(self) -> None:
        self.assertIsNone(v.validate_completion_text("Just a neutral sentence about the weather."))

    def test_completion_claim_missing_completion_check_rejected_in_mandatory_final_block_mode(self) -> None:
        reason = v.validate_completion_text(
            "The requested change is complete and tests pass.",
            require_completion_check=True,
        )

        self.assertIsNotNone(reason)
        self.assertIn("[completion-check]", reason)

    def test_explanatory_final_response_does_not_require_completion_check(self) -> None:
        reason = v.validate_completion_text(
            "Here is what happened: the hook treated a routine answer as closure.",
            require_completion_check=False,
        )

        self.assertIsNone(reason)

    def test_empty_text_returns_none(self) -> None:
        self.assertIsNone(v.validate_completion_text(""))

    def test_missing_completion_check_block(self) -> None:
        # Marker present but the [completion-check] block is empty (next header follows immediately). Marker-only detection flags it as a claim; the empty body is rejected.
        reason = v.validate_completion_text(
            "I did the work.\n[completion-check]\n[boundary-contract]\n- id: x\n"
        )
        self.assertIsNotNone(reason)
        self.assertIn("[completion-check]", reason)

    def test_compact_form_without_acceptance_criteria_is_accepted(self) -> None:
        # Compact form: no acceptance-criteria enumeration, but the claim-evidence-map still binds each claim to evidence and a verdict.
        text = VALID_TEXT.replace(
            "  - C1: validator enforces the completion contract [source: user-said]\n"
            "  - C2: stdlib only [source: user-said]\n",
            "  - <criterion-id>: <placeholder>\n",
        )
        reason = v.validate_completion_text(text)
        self.assertIsNone(reason)

    def test_multi_criterion_entry_with_known_ids_is_accepted(self) -> None:
        # A single claim may map to multiple acceptance ids (comma/space separated).
        text = VALID_TEXT.replace("    criterion: C1\n", "    criterion: C1, C2\n", 1)
        self.assertIsNone(v.validate_completion_text(text))

    def test_multi_criterion_entry_with_unknown_id_is_rejected(self) -> None:
        # If any criterion in the list is unknown, the entry is still rejected.
        text = VALID_TEXT.replace("    criterion: C1\n", "    criterion: C1, C9\n", 1)
        reason = v.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("acceptance-criteria criterion id", reason)

    def test_entry_verdict_invalid_value(self) -> None:
        text = VALID_TEXT.replace("    verdict: pass\n", "    verdict: maybe\n", 1)
        reason = v.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("Invalid verdict value", reason)
        self.assertIn("without punctuation", reason)

    def test_missing_unverified_section(self) -> None:
        text = VALID_TEXT.replace("- unverified:\n  - none\n", "")
        reason = v.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("unverified section", reason)



if __name__ == "__main__":
    unittest.main()
