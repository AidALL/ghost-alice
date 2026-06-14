import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_catalog import build_catalog  # noqa: E402


def write_skill(repo_root: Path, relative_dir: str, name: str) -> None:
    skill_md = repo_root / relative_dir / "SKILL.md"
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text(
        f"---\nname: {name}\ndescription: \"{name} description\"\n---\n\n# {name}\n",
        encoding="utf-8",
    )


class BuildCatalogFamilyTest(unittest.TestCase):
    def test_root_router_and_boundary_contract_are_system_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            write_skill(repo_root, "task-router", "task-router")
            write_skill(repo_root, "boundary-contract", "boundary-contract")
            write_skill(repo_root, "feature-skill", "feature-skill")
            write_skill(
                repo_root,
                "coding-convention/using-coding-convention",
                "using-coding-convention",
            )

            catalog = build_catalog(repo_root)
            by_name = {skill["name"]: skill for skill in catalog["skills"]}

            self.assertEqual(by_name["task-router"]["family"], "system")
            self.assertEqual(by_name["boundary-contract"]["family"], "system")
            self.assertEqual(by_name["feature-skill"]["family"], "domain")
            self.assertEqual(
                by_name["using-coding-convention"]["family"],
                "coding-convention",
            )

    def test_governance_class_is_separate_from_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            write_skill(repo_root, "task-router", "task-router")
            write_skill(repo_root, "jailbreak-detector", "jailbreak-detector")
            write_skill(repo_root, "sample-domain-skill", "sample-domain-skill")
            write_skill(
                repo_root,
                "coding-convention/verification-before-completion",
                "verification-before-completion",
            )

            catalog = build_catalog(repo_root)
            by_name = {skill["name"]: skill for skill in catalog["skills"]}

            self.assertEqual(by_name["jailbreak-detector"]["family"], "domain")
            self.assertEqual(
                by_name["jailbreak-detector"]["governance_class"],
                "governance-subskill",
            )
            self.assertEqual(
                by_name["sample-domain-skill"]["governance_class"],
                "non-governance-domain",
            )
            self.assertEqual(
                by_name["task-router"]["governance_class"],
                "core-gate",
            )
            self.assertEqual(
                by_name["verification-before-completion"]["governance_class"],
                "completion-gate",
            )


if __name__ == "__main__":
    unittest.main()
