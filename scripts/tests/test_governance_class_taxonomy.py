import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


# These are allowed addon skills, but must not be checked into the core catalog
# or install roots. Addon installation uses --addon-source.
ADDON_SKILLS_FORBIDDEN_IN_CORE_CATALOG = {
    "cardnews-automation",
    "company-profile",
    "company-profile-injector",
    "design-library-normalizer",
    "document-to-markdown",
    "edge-detector-architecture",
    "edge-detector-deployment",
    "fullstack-webapp",
    "gov-proposal-writer",
    "headless-browser-render",
    "hwpx",
    "jira-issue-writer",
    "logical-writing",
    "ml-model-builder",
    "robot-control-system",
}


class GovernanceClassTaxonomyTest(unittest.TestCase):
    def test_catalog_schema_defines_governance_class(self) -> None:
        schema = json.loads((ROOT / "skill-catalog/schema.json").read_text())
        props = schema["$defs"]["skill"]["properties"]

        self.assertIn("governance_class", props)
        enum_values = set(props["governance_class"]["enum"])
        self.assertTrue(
            {
                "core-gate",
                "intent-state-producer",
                "boundary-gate",
                "governance-subskill",
                "completion-gate",
                "non-governance-domain",
            }.issubset(enum_values)
        )

    def test_checked_in_catalog_has_governance_classes(self) -> None:
        catalog = json.loads((ROOT / "skill-catalog/skills.json").read_text())
        by_name = {skill["name"]: skill for skill in catalog["skills"]}

        self.assertEqual(
            by_name["jailbreak-detector"]["governance_class"],
            "governance-subskill",
        )
        self.assertEqual(
            by_name["skill-evolution"]["governance_class"],
            "governance-subskill",
        )
        self.assertNotEqual(
            by_name["jailbreak-detector"]["family"],
            by_name["jailbreak-detector"]["governance_class"],
        )

    def test_addon_skills_are_not_checked_into_core_catalog(self) -> None:
        catalog = json.loads((ROOT / "skill-catalog/skills.json").read_text())
        skill_names = {skill["name"] for skill in catalog["skills"]}
        install_roots = set(catalog["install_roots"])

        self.assertFalse(ADDON_SKILLS_FORBIDDEN_IN_CORE_CATALOG & skill_names)
        self.assertFalse(ADDON_SKILLS_FORBIDDEN_IN_CORE_CATALOG & install_roots)


if __name__ == "__main__":
    unittest.main()
