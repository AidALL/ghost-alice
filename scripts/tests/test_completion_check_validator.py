"""Tests for completion_check_validator.

These cover the deny conditions and ordering of `validate_completion_text`: the
claim-evidence-map honesty core (claim + evidence + verdict, unverified=none) is
always required, while the separate acceptance-criteria enumeration is optional.
"""

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = REPO_ROOT / "_shared" / "completion_check_validator.py"


def _load_validator():
    if not VALIDATOR.exists():
        raise AssertionError("_shared/completion_check_validator.py must exist")
    spec = importlib.util.spec_from_file_location("completion_check_validator_under_test", VALIDATOR)
    if spec is None or spec.loader is None:
        raise AssertionError("completion_check_validator.py must be importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MOD = _load_validator()


# A fully-valid completion-check response. Used as the baseline that returns
# None, and mutated per-test to trigger one deny condition at a time.
VALID_TEXT = """\
The change is complete and all tests pass.

[completion-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- acceptance-criteria:
  - C1: validator enforces the completion contract [source: user-explicit]
  - C2: stdlib only [source: user-explicit]
- claim-evidence-map:
  - claim: the validator enforces the completion contract
    criterion: C1
    evidence: ran scripts/tests/test_completion_check_validator.py -> OK
    verdict: pass
  - claim: no new dependencies were added
    criterion: C2
    evidence: module imports only re from the standard library
    verdict: pass
- unverified:
  - none
- evidence: ran python3 scripts/tests/test_completion_check_validator.py -v

[io-trace]
- files-read: [/Users/x/ghost-alice/_shared/ghost-alice-hook.mjs]
- files-written: [/Users/x/ghost-alice/_shared/completion_check_validator.py]
- skills-loaded: [verification-before-completion]
"""


class LooksLikeCompletionClaimTest(unittest.TestCase):
    def test_completion_check_marker_is_a_claim(self) -> None:
        self.assertTrue(MOD.looks_like_completion_claim("[completion-check]\n- x"))

    def test_keyword_in_prose_without_marker_is_not_a_claim(self) -> None:
        # Claim detection is marker-only: prose completion keywords and generic
        # success-criteria phrases do NOT make a claim. Only the explicit
        # [completion-check] marker does. The model declares its own claims.
        self.assertFalse(MOD.looks_like_completion_claim("The build is done."))
        self.assertFalse(MOD.looks_like_completion_claim("work complete"))
        self.assertFalse(MOD.looks_like_completion_claim("I recommend option A."))
        self.assertFalse(MOD.looks_like_completion_claim("acceptance criteria: concrete success criteria"))

    def test_plain_non_claim_text(self) -> None:
        self.assertFalse(MOD.looks_like_completion_claim("Here is some neutral text about cats."))

    def test_done_only_inside_control_blocks_is_not_a_claim(self) -> None:
        # No [completion-check] marker -> not a claim (marker-only detection). The "done"
        # words inside other control blocks are irrelevant; only the marker counts.
        text = (
            "[tool-checkpoint]\n"
            "- verification-before-completion: done\n"
            "- status: done\n"
            "\n"
            "[io-trace]\n"
            "- skills-loaded: [verification-before-completion]\n"
            "- note: done\n"
        )
        self.assertFalse(MOD.looks_like_completion_claim(text))

    def test_strip_control_blocks_ends_on_blank_or_triple_dash(self) -> None:
        text = (
            "[gate-state]\n"
            "- task-router: done\n"
            "---\n"
            "kept-after-dash\n"
            "[io-trace]\n"
            "- skills-loaded: [x]\n"
            "\n"
            "kept-after-blank\n"
        )
        stripped = MOD.strip_control_blocks(text)
        self.assertIn("kept-after-dash", stripped)
        self.assertIn("kept-after-blank", stripped)
        self.assertNotIn("task-router: done", stripped)
        self.assertNotIn("skills-loaded", stripped)


class ExtractControlBlockTest(unittest.TestCase):
    def test_extracts_named_block_until_next_header(self) -> None:
        text = (
            "prose\n"
            "[completion-check]\n"
            "- a: 1\n"
            "- b: 2\n"
            "[io-trace]\n"
            "- skills-loaded: [x]\n"
        )
        block = MOD.extract_control_block(text, "completion-check")
        self.assertIn("- a: 1", block)
        self.assertIn("- b: 2", block)
        self.assertNotIn("skills-loaded", block)

    def test_missing_block_returns_empty_string(self) -> None:
        self.assertEqual(MOD.extract_control_block("no blocks here", "completion-check"), "")


class FieldSectionTest(unittest.TestCase):
    def test_extract_top_level_field_section_stops_at_next_top_level(self) -> None:
        block = (
            "- acceptance-criteria:\n"
            "  - C1: foo\n"
            "  - C2: bar\n"
            "- unverified:\n"
            "  - none\n"
        )
        section = MOD.extract_top_level_field_section(block, "acceptance-criteria")
        self.assertIn("- C1: foo", section)
        self.assertIn("- C2: bar", section)
        self.assertNotIn("none", section)

    def test_section_is_none_true_only_for_none_lines(self) -> None:
        self.assertTrue(MOD.section_is_none("- none"))
        self.assertTrue(MOD.section_is_none("  - none\n  - none"))
        self.assertFalse(MOD.section_is_none(""))  # empty -> False
        self.assertFalse(MOD.section_is_none("- C1: something unresolved"))

    def test_extract_acceptance_criteria_ids_skips_placeholders(self) -> None:
        block = (
            "- acceptance-criteria:\n"
            "  - C1: real id\n"
            "  - <criterion-id>: placeholder\n"
            "  - C2: another\n"
        )
        ids = MOD.extract_acceptance_criteria_ids(block)
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
        entries = MOD.extract_claim_evidence_entries(block)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["claim"], "thing one")
        self.assertEqual(entries[0]["criterion"], "C1")
        self.assertEqual(entries[0]["evidence"], "ran a command")
        self.assertEqual(entries[0]["verdict"], "pass")
        self.assertEqual(entries[1]["verdict"], "fail")


class ValidateCompletionResponseTest(unittest.TestCase):
    def test_valid_text_returns_none(self) -> None:
        self.assertIsNone(MOD.validate_completion_text(VALID_TEXT))

    def test_non_completion_claim_returns_none(self) -> None:
        self.assertIsNone(MOD.validate_completion_text("Just a neutral sentence about the weather."))

    def test_completion_claim_missing_completion_check_rejected_in_mandatory_final_block_mode(self) -> None:
        reason = MOD.validate_completion_text(
            "The requested change is complete and tests pass.",
            require_completion_check=True,
        )

        self.assertIsNotNone(reason)
        self.assertIn("[completion-check]", reason)

    def test_explanatory_final_response_does_not_require_completion_check(self) -> None:
        reason = MOD.validate_completion_text(
            "Here is what happened: the hook treated a routine answer as closure.",
            require_completion_check=True,
        )

        self.assertIsNone(reason)

    def test_empty_text_returns_none(self) -> None:
        self.assertIsNone(MOD.validate_completion_text(""))

    def test_missing_completion_check_block(self) -> None:
        # Marker present but the [completion-check] block is empty (next header follows
        # immediately). Marker-only detection flags it as a claim; the empty body is rejected.
        reason = MOD.validate_completion_text(
            "I did the work.\n[completion-check]\n[io-trace]\n- skills-loaded: [x]\n"
        )
        self.assertIsNotNone(reason)
        self.assertIn("[completion-check]", reason)

    def test_missing_verification_before_completion_done(self) -> None:
        text = VALID_TEXT.replace("- verification-before-completion: done\n", "")
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("verification-before-completion: done", reason)

    def test_skill_call_missing(self) -> None:
        text = VALID_TEXT.replace(
            "- skill-call: verification-before-completion (this turn)\n", ""
        )
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("verification-reminder", reason)

    def test_skill_call_without_verification_skill(self) -> None:
        text = VALID_TEXT.replace(
            "- skill-call: verification-before-completion (this turn)\n",
            "- skill-call: task-router (this turn)\n",
        )
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("verification-reminder", reason)

    def test_missing_io_trace(self) -> None:
        # Remove the whole [io-trace] block (last block in VALID_TEXT).
        text = VALID_TEXT.split("[io-trace]")[0]
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("[io-trace]", reason)

    def test_io_trace_skills_loaded_missing_verification(self) -> None:
        text = VALID_TEXT.replace(
            "- skills-loaded: [verification-before-completion]\n",
            "- skills-loaded: [task-router]\n",
        )
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("skills-loaded", reason)

    def test_compact_form_without_acceptance_criteria_is_accepted(self) -> None:
        # Compact form: no acceptance-criteria enumeration, but the claim-evidence-map
        # still binds each claim to evidence and a verdict.
        text = VALID_TEXT.replace(
            "  - C1: validator enforces the completion contract [source: user-explicit]\n"
            "  - C2: stdlib only [source: user-explicit]\n",
            "  - <criterion-id>: <placeholder>\n",
        )
        reason = MOD.validate_completion_text(text)
        self.assertIsNone(reason)

    def test_empty_claim_evidence_map(self) -> None:
        text = VALID_TEXT.replace(
            "  - claim: the validator enforces the completion contract\n"
            "    criterion: C1\n"
            "    evidence: ran scripts/tests/test_completion_check_validator.py -> OK\n"
            "    verdict: pass\n"
            "  - claim: no new dependencies were added\n"
            "    criterion: C2\n"
            "    evidence: module imports only re from the standard library\n"
            "    verdict: pass\n",
            "  (none recorded)\n",
        )
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("claim-evidence-map", reason)

    def test_entry_criterion_not_a_known_acceptance_id(self) -> None:
        text = VALID_TEXT.replace("    criterion: C1\n", "    criterion: C9\n")
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("acceptance-criteria criterion id", reason)

    def test_multi_criterion_entry_with_known_ids_is_accepted(self) -> None:
        # A single claim may map to multiple acceptance ids (comma/space separated).
        text = VALID_TEXT.replace("    criterion: C1\n", "    criterion: C1, C2\n", 1)
        self.assertIsNone(MOD.validate_completion_text(text))

    def test_multi_criterion_entry_with_unknown_id_is_rejected(self) -> None:
        # If any criterion in the list is unknown, the entry is still rejected.
        text = VALID_TEXT.replace("    criterion: C1\n", "    criterion: C1, C9\n", 1)
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("acceptance-criteria criterion id", reason)

    def test_entry_missing_evidence(self) -> None:
        text = VALID_TEXT.replace(
            "    evidence: ran scripts/tests/test_completion_check_validator.py -> OK\n",
            "",
        )
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("must include evidence", reason)

    def test_entry_verdict_invalid_value(self) -> None:
        text = VALID_TEXT.replace("    verdict: pass\n", "    verdict: maybe\n", 1)
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("pass | fail | unverified", reason)

    def test_entry_verdict_unverified_blocks(self) -> None:
        text = VALID_TEXT.replace("    verdict: pass\n", "    verdict: unverified\n", 1)
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("cannot contain an 'unverified' verdict", reason)

    def test_missing_unverified_section(self) -> None:
        text = VALID_TEXT.replace("- unverified:\n  - none\n", "")
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("unverified section", reason)

    def test_unverified_section_not_none(self) -> None:
        text = VALID_TEXT.replace(
            "- unverified:\n  - none\n",
            "- unverified:\n  - C2: still checking deps\n",
        )
        reason = MOD.validate_completion_text(text)
        self.assertIsNotNone(reason)
        self.assertIn("requires the unverified section to be 'none'", reason)


if __name__ == "__main__":
    unittest.main()
