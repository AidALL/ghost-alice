import tempfile
import unittest
from pathlib import Path

from scripts.validate_entrypoints import (
    Finding,
    check_platform_ports,
    check_task_router_body,
    check_using_cc_body,
)


class EntrypointBodyContractTest(unittest.TestCase):
    def test_platform_port_parity_includes_claude_global_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            codex_port = repo / "platforms" / "codex" / "AGENTS.md"
            claude_port = repo / "platforms" / "claude" / "CLAUDE.md"
            codex_port.parent.mkdir(parents=True)
            claude_port.parent.mkdir(parents=True)
            ssot_rules = "\n".join(f"### {number}. Rule {number}" for number in range(13))
            (repo / "AGENTS.md").write_text(ssot_rules + "\n", encoding="utf-8")
            codex_port.write_text(ssot_rules + "\ntask-router\n", encoding="utf-8")
            claude_port.write_text(
                "\n".join(f"### {number}. Rule {number}" for number in range(12))
                + "\ntask-router\n",
                encoding="utf-8",
            )

            findings: list[Finding] = []
            check_platform_ports(repo, findings)

            self.assertTrue(
                any(
                    finding.check == "parity-missing"
                    and "platforms/claude/CLAUDE.md" in finding.message
                    and "rule 12" in finding.message
                    for finding in findings
                ),
                [finding.to_dict() for finding in findings],
            )

    def test_task_router_accepts_quality_rationale_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = repo / "task-router" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "\n".join(
                    [
                        "---",
                        "name: task-router",
                        "calls:",
                        '  - "meta:*"',
                        "---",
                        "<QUALITY-RATIONALE>",
                        "quality-maintenance procedure.",
                        "</QUALITY-RATIONALE>",
                        "<ROUTING-CONTRACT>",
                        "runs before downstream work.",
                        "</ROUTING-CONTRACT>",
                    ]
                ),
                encoding="utf-8",
            )

            findings: list[Finding] = []
            check_task_router_body(repo, findings)

            self.assertEqual([], [f.to_dict() for f in findings])

    def test_using_coding_convention_accepts_quality_rationale_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = repo / "coding-convention" / "using-coding-convention" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "\n".join(
                    [
                        "---",
                        "name: using-coding-convention",
                        "---",
                        "<QUALITY-RATIONALE>",
                        "quality-maintenance device.",
                        "</QUALITY-RATIONALE>",
                        "<USE-CONTRACT>",
                        "check first when there is even a 1% chance.",
                        "</USE-CONTRACT>",
                    ]
                ),
                encoding="utf-8",
            )

            findings: list[Finding] = []
            check_using_cc_body(repo, findings)

            self.assertEqual([], [f.to_dict() for f in findings])


if __name__ == "__main__":
    unittest.main()
