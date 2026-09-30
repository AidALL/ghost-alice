"""Design placement pins for governance surfaces.

- skill-call lines are factual records, so templates must not pre-fill skill names.
- Accumulated-restriction reconciliation is a routing decision rule and stays in the router body.
- verification-before-completion is a rule-enforcement skill; its defenses stay in the body.
- The user-claim rule sits right after verify-or-reuse on the always-on surfaces: verification covers the agent's own work.
"""
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFILLED = "skill-call: session-intent-analyzer (this turn); task-router (this turn)"
PLACEHOLDER = "skill-call: <each skill actually called this turn, as name (this turn)>"
HARD_SEQUENCE = "skill load/call -> decision-relevant fresh verification -> [completion-check]"
HARD_SEQUENCE_EVIDENCE = (
    "A successful result already returned in this turn, such as a write or a test run, is that fresh verification; "
    "the order places the skill before the claim and does not require repeating a successful check."
)
HARD_SEQUENCE_SURFACES = (
    "AGENTS.md",
    "platforms/claude/CLAUDE.md",
    "platforms/codex/AGENTS.md",
    "coding-convention/verification-before-completion/SKILL.md",
    "coding-convention/using-coding-convention/SKILL.md",
    "docs/policies/session-gate-matrix.md",
    "docs/ko/policies/session-gate-matrix.md",
)
FINALIZATION_STEPS = (
    "2. Extract the acceptance criteria and map each intended final claim to fresh evidence from this turn: cite a "
    "successful result already returned, such as a write or a test run, and run a decision-relevant check only for a "
    "claim that no returned result covers.",
    "3. Only after the skill is loaded and every claim has its evidence, write `[completion-check]`; on Codex, "
    "include the `skill-call: verification-before-completion (this turn)` line.",
)
STALE_FINALIZATION_WORDING = (
    "run the decision-relevant fresh verification that can prove or disprove each intended final claim",
    "Only after the skill is loaded and the fresh evidence is read",
)
USER_CLAIM_RULE = (
    "User-supplied facts, results, and wording are the user's claims, not the agent's. When the user asks to record "
    "them, write them as given; do not refuse, soften, re-verify, or add caveats because the agent cannot confirm them, "
    "since that judgment and its consequences belong to the user. Verification duties cover claims about work the agent "
    "itself performed."
)
VERIFY_OR_REUSE_LEAD = "Before re-reading an artifact to support a claim, apply verify-or-reuse:"
USER_CLAIM_SURFACES = ("AGENTS.md", "platforms/claude/CLAUDE.md", "platforms/codex/AGENTS.md")
TEMPLATE_SURFACES = (
    "AGENTS.md",
    "platforms/claude/CLAUDE.md",
    "platforms/codex/AGENTS.md",
    "task-router/SKILL.md",
    "coding-convention/using-coding-convention/SKILL.md",
    "docs/policies/session-gate-matrix.md",
    "docs/ko/policies/session-gate-matrix.md",
)


class GateSurfaceDesignTest(unittest.TestCase):
    def test_skill_call_templates_record_only_actual_calls(self) -> None:
        # A pre-filled example list was copied as a claim without the calls being made.
        for rel in TEMPLATE_SURFACES:
            with self.subTest(surface=rel):
                text = (REPO_ROOT / rel).read_text(encoding="utf-8")
                self.assertNotIn(PREFILLED, text)
                self.assertIn(PLACEHOLDER, text)

    def test_router_body_keeps_accumulated_restriction_reconciliation(self) -> None:
        body = (REPO_ROOT / "task-router" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(
            "A supported explicit user revision resolves only the named exception; other restrictions remain.", body
        )

    def test_hard_sequence_accepts_evidence_already_returned_this_turn(self) -> None:
        # Subjects wrote a file, called the verification skill, then re-read the same file only to
        # satisfy the order; the order places the skill before the claim, not before the evidence.
        for rel in HARD_SEQUENCE_SURFACES:
            with self.subTest(surface=rel):
                text = " ".join((REPO_ROOT / rel).read_text(encoding="utf-8").split())
                self.assertIn(HARD_SEQUENCE, text)
                self.assertIn(HARD_SEQUENCE_EVIDENCE, text)

    def test_finalization_steps_map_claims_to_returned_evidence(self) -> None:
        # Steps 2-3 read as "run and read again after the skill loads", contradicting the hard-sequence clarification.
        body = (REPO_ROOT / "coding-convention" / "verification-before-completion" / "SKILL.md").read_text(encoding="utf-8")
        for step in FINALIZATION_STEPS:
            with self.subTest(step=step[:2]):
                self.assertIn(step, body)
        for stale in STALE_FINALIZATION_WORDING:
            with self.subTest(stale=stale[:30]):
                self.assertNotIn(stale, body)

    def test_user_claim_rule_follows_verify_or_reuse(self) -> None:
        # Subjects refused or rewrote user-dictated lines worded as results; the verification duty covers the
        # agent's own work, so the rule sits next to verify-or-reuse on every always-on surface.
        for rel in USER_CLAIM_SURFACES:
            with self.subTest(surface=rel):
                paragraphs = [p.strip() for p in (REPO_ROOT / rel).read_text(encoding="utf-8").split("\n\n")]
                leads = [i for i, p in enumerate(paragraphs) if p.startswith(VERIFY_OR_REUSE_LEAD)]
                self.assertEqual(len(leads), 1)
                self.assertEqual(paragraphs.count(USER_CLAIM_RULE), 1)
                self.assertEqual(paragraphs[leads[0] + 1], USER_CLAIM_RULE)

    def test_verification_body_keeps_rule_enforcement_defenses(self) -> None:
        body = (REPO_ROOT / "coding-convention" / "verification-before-completion" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for heading in ("## Common Failures", "## Red Flags", "## Rationalization Defense", "## Why It Matters"):
            with self.subTest(heading=heading):
                self.assertIn(heading, body)


if __name__ == "__main__":
    unittest.main()
