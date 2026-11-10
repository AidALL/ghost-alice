import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate_entrypoints import (
    Finding,
    SSOT_RULES_FILE,
    check_platform_ports,
    check_ssot_imports,
    check_ssot_rules,
    check_task_router_body,
    check_using_cc_body,
)


class EntrypointBodyContractTest(unittest.TestCase):
    def test_loader_requires_agents_import_outside_code(self) -> None:
        cases = (
            ('@AGENTS.md\n', True),
            ('See @AGENTS.md for project instructions.\n', True),
            ('See @./AGENTS.md for project instructions.\n', True),
            ('Read AGENTS.md for project instructions.\n', False),
            ('Use `@AGENTS.md` as an example.\n', False),
            ('Use ``example ` @AGENTS.md `` literally.\n', False),
            ('Use \\` @AGENTS.md \\` as ordinary text.\n', True),
            ('Use \\\\` @AGENTS.md ` as code.\n', False),
            ('A lone `\n\n@AGENTS.md\n\nAnother `\n', True),
            ('A span `\n@AGENTS.md\n` on multiple lines.\n', False),
            ('\\`` @AGENTS.md `\n', False),
            ('# Heading with a lone `\n@AGENTS.md\nAnother lone `\n', True),
            ('A lone `\n~~~text\nexample\n~~~\n@AGENTS.md\nAnother `\n', True),
            ('```text\n@AGENTS.md\n```\n', False),
            ('~~~text\n@AGENTS.md\n~~~\n', False),
            ('```text\n@AGENTS.md\n', False),
            ('@AGENTS.md.backup\n', False),
            ('```text\n@AGENTS.md\n```\nSee @AGENTS.md now.\n', True),
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for text, imports in cases:
                with self.subTest(text=text):
                    (repo / 'CLAUDE.md').write_text(text, encoding='utf-8')
                    findings = []
                    check_ssot_imports(repo, findings)
                    self.assertEqual([f.check for f in findings], [] if imports else ['chain'])

    def copy_owner_contract(self, repo: Path) -> None:
        source = Path(__file__).resolve().parents[2]
        for rel in (
            "AGENTS.md",
            "platforms/common-governance.md",
            "task-router/SKILL.md",
            "coding-convention/using-coding-convention/SKILL.md",
            "boundary-contract/SKILL.md",
            "session-intent-analyzer/SKILL.md",
            "coding-convention/systematic-debugging/SKILL.md",
            "coding-convention/verification-before-completion/SKILL.md",
            "merge-companion/SKILL.md",
            "coding-convention/verification-before-completion/references/response-review.md",
        ):
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, target)

    def test_platform_port_without_the_entry_sections_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for rel in ("platforms/codex/AGENTS.md", "platforms/claude/CLAUDE.md"):
                port = repo / rel
                port.parent.mkdir(parents=True)
                port.write_text("no entry sections\n", encoding="utf-8")

            findings: list[Finding] = []
            check_platform_ports(repo, findings)

            self.assertEqual({finding.check for finding in findings}, {"entry-sections", "mention"})

    def test_current_entrypoints_accept_consolidated_owner_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.copy_owner_contract(repo)

            findings: list[Finding] = []
            check_ssot_rules(repo, findings)
            check_task_router_body(repo, findings)
            check_using_cc_body(repo, findings)

            self.assertEqual([], [f.to_dict() for f in findings])

    def test_missing_owner_reference_is_rejected(self) -> None:
        cases = (
            (SSOT_RULES_FILE, check_ssot_rules, "merge-companion"),
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
        for check, target, heading in ((check_using_cc_body, "coding-convention/systematic-debugging/SKILL.md", "Incident accounting and continuation"),):
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
