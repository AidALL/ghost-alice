import os
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _markdown_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root]
    return sorted(
        path
        for path in root.rglob("*.md")
        if ".git" not in path.parts and "__pycache__" not in path.parts
    )


def _wiki_root() -> Path | None:
    env = os.environ.get("GHOST_ALICE_WIKI_DIR")
    if env:
        return Path(env)
    sibling = REPO_ROOT.parent / "ghost-alice.wiki"
    if sibling.exists():
        return sibling
    return None


def _scan_files(paths: list[Path], patterns: dict[str, re.Pattern[str]]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in patterns.items():
                if pattern.search(line):
                    findings.append(f"{path.relative_to(REPO_ROOT.parent)}:{line_number}: {label}: {line.strip()}")
    return findings


class PublicCommandSurfaceTest(unittest.TestCase):
    PATTERNS = {
        "removed offboard flag": re.compile(r"--offboard|--offboard-confirm|-Offboard\b"),
        "unimplemented top-level addon dry-run": re.compile(
            r"install\.sh\s+(?=[^\n]*--addon-source)(?=[^\n]*--dry-run)"
        ),
        "unsupported dry-run preview claim": re.compile(r"\bdry-run previews?\b"),
        "stale Node optional prerequisite": re.compile(r"Node\.js\s+\(optional\)"),
    }

    def test_repo_docs_do_not_publish_removed_or_unimplemented_commands(self) -> None:
        paths = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "README_ko.md",
            *_markdown_files(REPO_ROOT / "docs"),
        ]
        self.assertEqual([], _scan_files(paths, self.PATTERNS))

    def test_evolution_command_matches_aggregate_recommendation_contract(self) -> None:
        command_path = REPO_ROOT / ".claude" / "commands" / "evolution.md"
        if not command_path.exists():
            self.skipTest(".claude command surface is not included in this public export")
        command = command_path.read_text(encoding="utf-8")
        self.assertIn("session_count", command)
        self.assertIn("occurrence_count", command)
        self.assertIn("days_since_last", command)
        self.assertNotIn("priority_score", command)

    def test_wiki_docs_do_not_publish_removed_or_unimplemented_commands_when_available(self) -> None:
        wiki = _wiki_root()
        if wiki is None:
            self.skipTest("set GHOST_ALICE_WIKI_DIR or place ghost-alice.wiki next to the repo")
        self.assertEqual([], _scan_files(_markdown_files(wiki), self.PATTERNS))

    def test_repo_docs_have_ko_counterpart_for_each_english_markdown_file(self) -> None:
        english_root = REPO_ROOT / "docs"
        korean_root = english_root / "ko"
        english = {
            path.relative_to(english_root)
            for path in _markdown_files(english_root)
            if "ko" not in path.relative_to(english_root).parts
        }
        korean = {
            path.relative_to(korean_root)
            for path in _markdown_files(korean_root)
        }
        self.assertEqual(english, korean)

    def test_wiki_docs_have_ko_counterpart_when_available(self) -> None:
        wiki = _wiki_root()
        if wiki is None:
            self.skipTest("set GHOST_ALICE_WIKI_DIR or place ghost-alice.wiki next to the repo")
        special_pages = {"_Sidebar.md", "_Footer.md"}

        def normalized_wiki_key(path: Path) -> str:
            name = path.relative_to(wiki).as_posix()
            if name.endswith("_en.md"):
                return f"{name[:-6]}.md"
            if name.endswith("_ko.md"):
                return f"{name[:-6]}.md"
            return name

        english = {
            normalized_wiki_key(path)
            for path in _markdown_files(wiki)
            if not path.name.endswith("_ko.md") and path.name not in special_pages
        }
        korean = {
            normalized_wiki_key(path)
            for path in _markdown_files(wiki)
            if path.name.endswith("_ko.md")
        }
        self.assertEqual(english, korean)


if __name__ == "__main__":
    unittest.main()
