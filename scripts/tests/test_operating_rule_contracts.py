import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class OperatingRuleContractTest(unittest.TestCase):
    def test_sufficient_change_rule_is_global_bootstrap_contract(self) -> None:
        targets = [
            REPO_ROOT / "AGENTS.md",
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
        targets = [
            REPO_ROOT / "coding-convention" / "test-driven-development" / "SKILL.md",
            REPO_ROOT / "coding-convention" / "using-coding-convention" / "SKILL.md",
        ]
        required = [
            "Verification scope",
            "previously failing test",
            "directly impacted tests",
            "Do not repeat broad passing",
            "risk signal",
        ]

        for target in targets:
            body = target.read_text(encoding="utf-8")
            with self.subTest(path=target):
                for needle in required:
                    self.assertIn(needle, body)


if __name__ == "__main__":
    unittest.main()
