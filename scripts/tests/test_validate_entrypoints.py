import tempfile
import unittest
from pathlib import Path

from scripts.validate_entrypoints import (
    Finding,
    check_task_router_body,
    check_using_cc_body,
)


class EntrypointBodyContractTest(unittest.TestCase):
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
