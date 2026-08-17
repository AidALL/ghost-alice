"""Regression tests for the completion lifecycle contract wording.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

HARD_SEQUENCE = "Hard sequence for a new current-turn closure claim: skill load/call -> decision-relevant fresh verification -> [completion-check]"
INVALID_IF_BROKEN = "If any step is missing or out of order, the completion-check is invalid."
ROUTING_NOT_REVERIFY = "Every user input reopens routing; it does not by itself invalidate unchanged evidence or require reverification."
UNCHANGED_EXPLANATION = "Explaining unchanged prior work is not a new closure claim."
UNCERTAINTY_GATE = "Before running a check, name the live uncertainty and the next decision that each possible outcome can change."
NO_DECISION_EFFECT = "If no possible outcome can change the criterion or next decision, do not run the check."
REVERIFY_TRIGGERS = "Reverify when the relevant state, artifact, or criterion changed; a new error, mismatch, contradiction, or instability appeared; or the user explicitly requested a new check."
NO_RECURSION = "Verification output does not create a new obligation to verify the verification."
VERIFICATION_OWNER_POINTER = "That skill owns evidence selection, evidence reuse, stop conditions, and the final completion format; this entrypoint does not duplicate those rules."
LIVE_CHECKPOINT = "A checkpoint runs only while a live uncertainty exists and its possible outcomes can change the next decision."
CHECKPOINT_STOP = "Stop the sequence when a checkpoint produces no relevant state delta or when further checking displaces the primary objective."

SYNCED_SURFACES = [
    "AGENTS.md",
    "platforms/claude/CLAUDE.md",
    "platforms/codex/AGENTS.md",
    "coding-convention/using-coding-convention/SKILL.md",
    "coding-convention/verification-before-completion/SKILL.md",
    "docs/policies/session-gate-matrix.md",
    "docs/ko/policies/session-gate-matrix.md",
]


class CompletionLifecycleContractTests(unittest.TestCase):
    def test_hard_sequence_is_synchronized_across_completion_surfaces(self) -> None:
        for rel_path in SYNCED_SURFACES:
            with self.subTest(path=rel_path):
                text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                self.assertIn(HARD_SEQUENCE, text)

    def test_routing_does_not_force_unchanged_reverification(self) -> None:
        for rel_path in SYNCED_SURFACES:
            with self.subTest(path=rel_path):
                text = " ".join(
                    (REPO_ROOT / rel_path).read_text(encoding="utf-8").split()
                )
                self.assertIn(ROUTING_NOT_REVERIFY, text)
                self.assertIn(UNCHANGED_EXPLANATION, text)
                self.assertIn(REVERIFY_TRIGGERS, text)

    def test_verification_has_decision_value_and_recursion_stop_gates(self) -> None:
        verification_surfaces = [
            "coding-convention/verification-before-completion/SKILL.md",
            "docs/policies/session-gate-matrix.md",
            "docs/ko/policies/session-gate-matrix.md",
        ]
        for rel_path in verification_surfaces:
            with self.subTest(path=rel_path):
                text = " ".join(
                    (REPO_ROOT / rel_path).read_text(encoding="utf-8").split()
                )
                self.assertIn(UNCERTAINTY_GATE, text)
                self.assertIn(NO_DECISION_EFFECT, text)
                self.assertIn(NO_RECURSION, text)

    def test_coding_convention_entrypoint_delegates_verification_details(self) -> None:
        text = " ".join((REPO_ROOT / "coding-convention/using-coding-convention/SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn(VERIFICATION_OWNER_POINTER, text)
        self.assertNotIn(UNCERTAINTY_GATE, text)
        self.assertNotIn(NO_DECISION_EFFECT, text)
        self.assertNotIn(NO_RECURSION, text)

    def test_old_unconditional_reverification_phrases_are_absent(self) -> None:
        forbidden = [
            "Re-check evidence on each user turn.",
            "run the check fresh from the beginning",
            "Do not skip fresh verification merely because the same session just inspected something.",
            "New user input reopens routing and verification.",
            "Even for something just confirmed in the same session, pass the gate again on a new turn.",
            "final response without fresh checks",
            "Your own fresh grep/read for that item",
            "A fresh grep/read this turn",
            "run the relevant check",
            "Which fresh evidence proves each claim?",
            "There is no shortcut. Run the check, read the output, map the claim, then speak.",
        ]
        for rel_path in SYNCED_SURFACES:
            text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
            for fragment in forbidden:
                with self.subTest(path=rel_path, fragment=fragment):
                    self.assertNotIn(fragment, text)

    def test_operating_model_bounds_reverification_sequences(self) -> None:
        surfaces = [
            "AGENTS.md",
            "architecture.md",
            "docs/policies/session-gate-matrix.md",
            "docs/ko/policies/session-gate-matrix.md",
        ]
        forbidden = [
            "treat the re-verification loop itself as the body of the work",
            "Repeated loops such as re-reviewing intermediate state, re-fetching evidence",
            "closed-loop reasoning that repeatedly compares",
            "runtime's re-verification loop itself",
            "re-verification loops belong to procedure and runtime verification",
        ]
        for rel_path in surfaces:
            text = " ".join(
                (REPO_ROOT / rel_path).read_text(encoding="utf-8").split()
            )
            with self.subTest(path=rel_path, required=LIVE_CHECKPOINT):
                self.assertIn(LIVE_CHECKPOINT, text)
            with self.subTest(path=rel_path, required=CHECKPOINT_STOP):
                self.assertIn(CHECKPOINT_STOP, text)
            for fragment in forbidden:
                with self.subTest(path=rel_path, forbidden=fragment):
                    self.assertNotIn(fragment, text)

    def test_verification_skill_names_invalid_out_of_order_completion_check(self) -> None:
        text = (
            REPO_ROOT
            / "coding-convention"
            / "verification-before-completion"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn(INVALID_IF_BROKEN, text)
        self.assertLess(text.index(HARD_SEQUENCE), text.index("[completion-check]"))


if __name__ == "__main__":
    unittest.main()
