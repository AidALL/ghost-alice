import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
KOREAN_RE = re.compile(r"[\uac00-\ud7a3]")


def _skill_frontmatter_paths() -> list[Path]:
    return sorted(REPO_ROOT.glob("*/SKILL.md")) + sorted((REPO_ROOT / "coding-convention").glob("*/SKILL.md"))


def _frontmatter(text: str) -> str:
    match = re.match(r"---\n(.*?)\n---", text, re.S)
    if not match:
        return ""
    return match.group(1)


class AgentMetadataLanguageTest(unittest.TestCase):
    def test_skill_frontmatter_metadata_is_english_default(self) -> None:
        offenders: list[str] = []
        for path in _skill_frontmatter_paths():
            metadata = _frontmatter(path.read_text(encoding="utf-8-sig"))
            for line in metadata.splitlines():
                stripped = line.strip()
                if not (
                    stripped.startswith("description:")
                    or stripped.startswith("compatibility:")
                    or stripped.startswith("- ")
                ):
                    continue
                if KOREAN_RE.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {line}")

        self.assertEqual(offenders, [])

    def test_skill_catalog_json_metadata_is_english_default(self) -> None:
        skills = json.loads((REPO_ROOT / "skill-catalog" / "skills.json").read_text(encoding="utf-8"))
        offenders: list[str] = []
        for entry in skills.get("skills", []):
            for key in ("description", "compatibility"):
                value = entry.get(key)
                if isinstance(value, str) and KOREAN_RE.search(value):
                    offenders.append(f"{entry.get('name')}.{key}: {value}")
            for value in entry.get("compatibility", []) if isinstance(entry.get("compatibility"), list) else []:
                if isinstance(value, str) and KOREAN_RE.search(value):
                    offenders.append(f"{entry.get('name')}.compatibility[]: {value}")

        self.assertEqual(offenders, [])

    def test_skill_catalog_schema_descriptions_are_english_default(self) -> None:
        schema = (REPO_ROOT / "skill-catalog" / "schema.json").read_text(encoding="utf-8")
        self.assertNotRegex(schema, KOREAN_RE)


if __name__ == "__main__":
    unittest.main()
