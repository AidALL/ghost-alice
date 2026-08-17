import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class OperatingRuleContractTest(unittest.TestCase):
    def test_clarification_only_route_asks_before_tools_or_governance_ceremony(self) -> None:
        task_router = (REPO_ROOT / "task-router" / "SKILL.md").read_text(encoding="utf-8")
        for needle in [
            "clarification-only",
            "minimum decisive information",
            "Do not inspect files, repositories, manifests, tools, credentials, or external state",
            "the content already resolves the question",
            "Intake and routing still run internally",
        ]:
            self.assertIn(needle, task_router)
        using_coding = (REPO_ROOT / "coding-convention" / "using-coding-convention" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("exits before a development turn begins", using_coding)
        self.assertIn("The next actionable user input runs routing again", using_coding)

    def test_direct_response_route_answers_settled_content_without_work_ceremony(self) -> None:
        task_router = (REPO_ROOT / "task-router" / "SKILL.md").read_text(encoding="utf-8")
        for needle in [
            "direct-response",
            "resolved-intent-first",
            "Acknowledge and preserve the settled part first",
            "Do not inspect files, repositories, manifests, tools, credentials, or external state",
            "stable, low-risk, non-current general guidance",
        ]:
            self.assertIn(needle, task_router)
        using_coding = (REPO_ROOT / "coding-convention" / "using-coding-convention" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("no-work terminal route", using_coding)
        self.assertIn("direct-response", using_coding)

    def test_direct_response_does_not_treat_ambient_repo_as_user_evidence(self) -> None:
        required = "Ambient working directory, opened project, and available tools are not user-provided referents or inspection authority."
        premise_rule = "Do not validate or rebut that premise before explaining."
        precedence_rule = "Route classification precedes evidence planning."
        for relative in [
            "task-router/SKILL.md",
            "AGENTS.md",
            "platforms/codex/AGENTS.md",
            "platforms/claude/CLAUDE.md",
        ]:
            with self.subTest(relative=relative):
                body = (REPO_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn(required, body)
                self.assertIn(premise_rule, body)
                self.assertIn(precedence_rule, body)
        task_router = (REPO_ROOT / "task-router" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("### General Past-Cause Explanation", task_router)

    def test_adversarial_rounds_stop_on_decision_relevant_convergence(self) -> None:
        skill = (REPO_ROOT / "adversarial-verification" / "SKILL.md").read_text(encoding="utf-8")
        round_protocol = (REPO_ROOT / "adversarial-verification" / "references" / "round-protocol.md").read_text(encoding="utf-8")
        convergence = (REPO_ROOT / "adversarial-verification" / "references" / "convergence-rules.md").read_text(encoding="utf-8")
        architecture = (REPO_ROOT / "architecture.md").read_text(encoding="utf-8")
        bodies = [skill, round_protocol, convergence, architecture]
        for body in bodies:
            self.assertIn("decision-relevant uncertainty", body)
            self.assertIn("relevant state delta", body)
            self.assertIn("Do not continue only to satisfy a round-count minimum", body)
        normalized_bodies = [" ".join(body.lower().split()) for body in bodies]
        for forbidden in [
            "do not check convergence before round 5",
            "rounds 1-4 are mandatory",
            "from round 5, apply convergence rules",
            "every 5 rounds",
            "only after round >= 5",
            "starting from round >= 5",
            "round >= 5",
            "minimum of 5",
            "do not use fewer than 5 rounds",
            "immediate convergence at round 5",
            "convergence judgment after round >= 5",
        ]:
            for body in normalized_bodies:
                self.assertNotIn(forbidden, body)
        self.assertIn("3-5 independent agents", skill)
        self.assertIn("finite safety cap of 50 rounds", skill)
        self.assertIn("Unresolved disagreement fails closed", skill)
        self.assertIn("Every agent independently writes its own-axis attack plus a meta attack", round_protocol)
        self.assertIn("unanimous", convergence)
        self.assertIn("finite safety cap of 50 rounds", convergence)
        self.assertIn("Unresolved disagreement fails closed", convergence)

    def test_sufficient_change_rule_is_global_bootstrap_contract(self) -> None:
        targets = [
            REPO_ROOT / "AGENTS.md",
            REPO_ROOT / "platforms" / "claude" / "CLAUDE.md",
            REPO_ROOT / "platforms" / "codex" / "AGENTS.md",
        ]
        required = [
            "Sufficient Change Principle",
            "minimal patch",
            "problem cause, structure, and impact surface",
            "sufficient-change-depth",
            "temporary patch",
            "survives the most relevant targeted tests",
            "smaller diff alone",
        ]

        for target in targets:
            body = target.read_text(encoding="utf-8")
            with self.subTest(path=target):
                for needle in required:
                    self.assertIn(needle, body)

        task_router = (REPO_ROOT / "task-router" / "SKILL.md").read_text(encoding="utf-8")
        english_required = [
            "Sufficient Change Principle",
            "minimal patch",
            "problem cause",
            "structure",
            "impact surface",
            "sufficient-change-depth",
            "Temporary patch",
            "survives targeted tests",
            "smaller diff alone",
        ]
        for needle in english_required:
            self.assertIn(needle, task_router)

    def test_source_locator_contract_is_required_for_external_evidence(self) -> None:
        targets = [
            REPO_ROOT / "AGENTS.md",
            REPO_ROOT / "platforms" / "claude" / "CLAUDE.md",
            REPO_ROOT / "platforms" / "codex" / "AGENTS.md",
            REPO_ROOT / "coding-convention" / "verification-before-completion" / "SKILL.md",
            REPO_ROOT / "adversarial-verification" / "SKILL.md",
        ]
        required = [
            "source-locator",
            "accessible_url",
            "file_path",
            "page",
            "region",
            "top | middle | bottom",
        ]

        for target in targets:
            body = target.read_text(encoding="utf-8")
            with self.subTest(path=target):
                for needle in required:
                    self.assertIn(needle, body)

    def test_work_impact_projection_contract_is_documented(self) -> None:
        targets = [
            REPO_ROOT / "AGENTS.md",
            REPO_ROOT / "platforms" / "claude" / "CLAUDE.md",
            REPO_ROOT / "platforms" / "codex" / "AGENTS.md",
            REPO_ROOT / "docs" / "policies" / "session-gate-matrix.md",
        ]
        required = [
            "Work-Impact Projection",
            "change the work boundary, focus layer, verification burden, or recovery",
            "Hook execution and the strict audit log are never reduced",
            "`agent_visibility.profile` selects",
            "Forced/risk/gate",
            "Routine/debug values",
            "Token reduction is a consequence",
        ]

        for target in targets:
            body = target.read_text(encoding="utf-8")
            with self.subTest(path=target):
                for needle in required:
                    self.assertIn(needle, body)

    def test_tdd_applies_only_after_behavior_contract_is_known(self) -> None:
        targets = [
            REPO_ROOT / "coding-convention" / "test-driven-development" / "SKILL.md",
            REPO_ROOT / "coding-convention" / "using-coding-convention" / "SKILL.md",
        ]
        required = [
            "behavior contract",
            "premise or target behavior is unknown",
            "bounded discovery or a proof",
            "regression coverage",
        ]
        forbidden = [
            "Throw away the exploration and start with TDD",
        ]

        for target in targets:
            body = target.read_text(encoding="utf-8")
            with self.subTest(path=target):
                for needle in required:
                    self.assertIn(needle, body)
                for needle in forbidden:
                    self.assertNotIn(needle, body)

    def test_tdd_verification_scope_prefers_direct_evidence(self) -> None:
        tdd = (REPO_ROOT / "coding-convention" / "test-driven-development" / "SKILL.md").read_text(encoding="utf-8")
        required = [
            "Verification scope",
            "previously failing test",
            "directly impacted tests",
            "Do not repeat broad passing",
            "risk signal",
        ]
        for needle in required:
            self.assertIn(needle, tdd)

        entrypoint = (REPO_ROOT / "coding-convention" / "using-coding-convention" / "SKILL.md").read_text(encoding="utf-8")
        for needle in [
            "Material Recommendations And Choices",
            "verification-routing signal",
            "verification-before-completion",
            "does not duplicate those rules",
        ]:
            self.assertIn(needle, entrypoint)

        verification_owner = (REPO_ROOT / "coding-convention" / "verification-before-completion" / "SKILL.md").read_text(encoding="utf-8")
        for needle in [
            "Evidence Selection And Stop Gate",
            "default direct evidence for semantic claims",
            "smallest decision-relevant check",
            "Stop when a repeated check produces no relevant state delta",
        ]:
            self.assertIn(needle, verification_owner)


if __name__ == "__main__":
    unittest.main()
