import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate_entrypoints import (
    Finding,
    SSOT_RULES_FILE,
    check_platform_ports,
    check_ssot_rules,
    check_task_router_body,
    check_using_cc_body,
)


class EntrypointBodyContractTest(unittest.TestCase):
    def copy_owner_contract(self, repo: Path) -> None:
        source = Path(__file__).resolve().parents[2]
        for rel in (
            "AGENTS.md",
            "platforms/common-governance.md",
            "task-router/references/routing-details.md",
            "task-router/SKILL.md",
            "coding-convention/using-coding-convention/SKILL.md",
            "boundary-contract/SKILL.md",
            "session-intent-analyzer/SKILL.md",
            "session-intent-analyzer/references/ledger-schema.md",
            "coding-convention/systematic-debugging/SKILL.md",
            "coding-convention/verification-before-completion/SKILL.md",
            "merge-companion/SKILL.md",
            "coding-convention/verification-before-completion/references/verify-or-reuse.md",
            "coding-convention/verification-before-completion/references/autopilot-publication.md",
        ):
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, target)

    def test_platform_port_parity_includes_claude_global_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            codex_port = repo / "platforms" / "codex" / "AGENTS.md"
            claude_port = repo / "platforms" / "claude" / "CLAUDE.md"
            codex_port.parent.mkdir(parents=True)
            claude_port.parent.mkdir(parents=True)
            ssot_rules = "\n".join(f"### {number}. Rule {number}" for number in range(13))
            (repo / SSOT_RULES_FILE).write_text(ssot_rules + "\n", encoding="utf-8")
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

    def test_current_entrypoints_accept_consolidated_owner_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.copy_owner_contract(repo)

            findings: list[Finding] = []
            check_ssot_rules(repo, findings)
            check_task_router_body(repo, findings)
            check_using_cc_body(repo, findings)

            self.assertEqual([], [f.to_dict() for f in findings])

    def test_entry_sections_require_headings_not_only_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.copy_owner_contract(repo)
            ssot = repo / SSOT_RULES_FILE
            ssot.write_text(
                ssot.read_text(encoding="utf-8").replace("## Work Selection\n", "## Other section\n"),
                encoding="utf-8",
            )

            findings: list[Finding] = []
            check_ssot_rules(repo, findings)

            self.assertTrue(any("Work Selection section" in f.message for f in findings))

    def test_missing_owner_reference_is_rejected(self) -> None:
        cases = (
            (SSOT_RULES_FILE, check_ssot_rules, "merge-companion"),
            ("task-router/SKILL.md", check_task_router_body,
             "../session-intent-analyzer/references/ledger-schema.md#core-response-projection"),
            ("coding-convention/using-coding-convention/SKILL.md", check_using_cc_body,
             "../systematic-debugging/SKILL.md#incident-accounting-and-continuation"),
        )
        for rel, check, reference in cases:
            with self.subTest(entrypoint=rel), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                self.copy_owner_contract(repo)
                entrypoint = repo / rel
                entrypoint.write_text(
                    entrypoint.read_text(encoding="utf-8").replace(reference, "other-owner.md"),
                    encoding="utf-8",
                )

                findings: list[Finding] = []
                check(repo, findings)

                self.assertTrue(
                    any(f.check == "owner-reference" and reference in f.message for f in findings),
                    [f.to_dict() for f in findings],
                )

    def test_missing_owner_file_is_rejected(self) -> None:
        for check, target in ((check_ssot_rules, "merge-companion/SKILL.md"),
                              (check_task_router_body, "session-intent-analyzer/references/ledger-schema.md"),
                              (check_using_cc_body, "coding-convention/systematic-debugging/SKILL.md")):
            with self.subTest(check=check.__name__), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                self.copy_owner_contract(repo)
                (repo / target).unlink()

                findings: list[Finding] = []
                check(repo, findings)

                self.assertTrue(
                    any(f.check == "owner-missing" for f in findings),
                    [f.to_dict() for f in findings],
                )

    def test_missing_owner_heading_is_rejected(self) -> None:
        for check, target, heading in ((check_task_router_body, "session-intent-analyzer/references/ledger-schema.md", "Core Response Projection"),
                                       (check_using_cc_body, "coding-convention/systematic-debugging/SKILL.md", "Incident accounting and continuation")):
            with self.subTest(check=check.__name__), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                self.copy_owner_contract(repo)
                owner = repo / target
                owner.write_text(
                    owner.read_text(encoding="utf-8").replace(
                        "## " + heading, "## Other section"
                    ),
                    encoding="utf-8",
                )

                findings: list[Finding] = []
                check(repo, findings)

                self.assertTrue(
                    any(f.check == "owner-anchor" and heading.lower().replace(" ", "-") in f.message
                        for f in findings),
                    [f.to_dict() for f in findings],
                )


if __name__ == "__main__":
    unittest.main()
