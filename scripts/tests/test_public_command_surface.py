import re
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from public_text_rules import POWERSHELL_COMMAND_PATTERNS, REMOVED_COMMAND_PATTERNS, pattern_findings  # noqa: E402


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


def _scan_files(paths: list[Path], patterns: dict[str, re.Pattern[str]]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        findings.extend(f"{path.relative_to(REPO_ROOT.parent)}:{line_number}: {label}: {line}"
                        for line_number, label, line in pattern_findings(text, patterns))
    return findings


class PublicCommandSurfaceTest(unittest.TestCase):
    PRIMARY_INSTALL_GUIDES = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "README_ko.md",
        REPO_ROOT / "docs" / "getting-started" / "installation.md",
        REPO_ROOT / "docs" / "ko" / "getting-started" / "installation.md",
    ]
    PUBLIC_DOC_SURFACES = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "README_ko.md",
        REPO_ROOT / "index.html",
        REPO_ROOT / "index_ko.html",
        *_markdown_files(REPO_ROOT / "docs"),
    ]
    WINDOWS_NATIVE_WRAPPER_REQUIREMENTS = {
        REPO_ROOT / "docs" / "getting-started" / "installation.md": (
            r".\install.cmd"
        ),
        REPO_ROOT / "docs" / "ko" / "getting-started" / "installation.md": (
            r".\install.cmd"
        ),
    }

    PATTERNS = REMOVED_COMMAND_PATTERNS

    # The primary install guides must not name install.ps1 at all; POWERSHELL_COMMAND_PATTERNS forbids only a call of it.
    PUBLIC_INSTALL_FORBIDDEN_PATTERNS = {
        **POWERSHELL_COMMAND_PATTERNS,
        "PowerShell installer entrypoint": re.compile(r"install\.ps1", re.IGNORECASE),
    }

    PUBLIC_DOC_FORBIDDEN_PATTERNS = POWERSHELL_COMMAND_PATTERNS

    def test_primary_install_guides_avoid_powershell_style_public_surface(self) -> None:
        self.assertEqual([], _scan_files(self.PRIMARY_INSTALL_GUIDES, self.PUBLIC_INSTALL_FORBIDDEN_PATTERNS))

    def test_primary_install_guides_include_windows_native_wrapper_examples(self) -> None:
        missing: list[str] = []
        for path, phrase in self.WINDOWS_NATIVE_WRAPPER_REQUIREMENTS.items():
            if phrase not in path.read_text(encoding="utf-8"):
                missing.append(f"{path.relative_to(REPO_ROOT.parent)} missing {phrase!r}")
        self.assertEqual([], missing)
        for readme, guide in (
            (REPO_ROOT / "README.md", "./docs/getting-started/installation.md"),
            (REPO_ROOT / "README_ko.md", "./docs/ko/getting-started/installation.md"),
        ):
            text = readme.read_text(encoding="utf-8")
            with self.subTest(readme=readme.name):
                self.assertIn(guide, text)
                self.assertEqual("bash install.sh",
                                 text.split("```bash\n", 1)[1].split("```", 1)[0].strip())

    def test_public_docs_avoid_powershell_style_installer_command_surface(self) -> None:
        self.assertEqual([], _scan_files(self.PUBLIC_DOC_SURFACES, self.PUBLIC_DOC_FORBIDDEN_PATTERNS))

    def test_repo_docs_do_not_publish_removed_or_unimplemented_commands(self) -> None:
        self.assertEqual([], _scan_files(self.PUBLIC_DOC_SURFACES, self.PATTERNS))

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


if __name__ == "__main__":
    unittest.main()
