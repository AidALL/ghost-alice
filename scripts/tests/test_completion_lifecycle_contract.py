"""Regression tests for the completion lifecycle contract wording.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

HARD_SEQUENCE = "Hard sequence: skill load/call -> fresh verification -> [completion-check]"
INVALID_IF_BROKEN = "If any step is missing or out of order, the completion-check is invalid."

SYNCED_SURFACES = [
    "AGENTS.md",
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
