import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from public_text_rules import term_findings  # noqa: E402

CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

BASE_SCAN_PATHS = [
    "AGENTS.md",
    "architecture.md",
    "adversarial-verification",
    "docs",
    "jailbreak-detector",
    "merge-companion",
    "platforms/claude/CLAUDE.md",
    "platforms/codex/AGENTS.md",
    "scripts",
    "skill-catalog",
    "task-router",
    "_shared",
]

SKIP_PARTS = {
    ".git",
    "__pycache__",
    "node_modules",
}

# These files hold the rule tables or fixtures that must contain the forbidden terms.
SKIP_FILES = {
    Path("scripts/public_text_rules.py"),
    Path("scripts/tests/test_terminology_clarity.py"),
}

TEXT_SUFFIXES = {".json", ".md", ".mjs", ".py"}

def local_scan_paths(root: Path):
    skill_roots = {str(path.parent.relative_to(root)) for path in root.glob("*/SKILL.md")}
    skill_roots.update(
        str(path.parent.relative_to(root)) for path in (root / "coding-convention").glob("*/SKILL.md")
    )
    return sorted(set(BASE_SCAN_PATHS) | skill_roots)


def iter_text_files(root: Path, scan_paths):
    for rel in scan_paths:
        path = root / rel
        if not path.exists():
            continue
        if path.is_file():
            candidates = [path]
        else:
            candidates = [p for p in path.rglob("*") if p.is_file()]
        for candidate in candidates:
            rel_candidate = candidate.relative_to(root)
            if rel_candidate in SKIP_FILES:
                continue
            if any(part in SKIP_PARTS for part in rel_candidate.parts):
                continue
            if candidate.suffix not in TEXT_SUFFIXES:
                continue
            yield candidate


def collect_findings(root: Path, scan_paths):
    findings = []
    for path in iter_text_files(root, scan_paths):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(root)
        findings.extend(f"{rel}:{lineno}: {message}" for lineno, message in term_findings(text))
    return findings


class TestTerminologyClarity(unittest.TestCase):
    def test_layer_tokens_in_local_text_are_detected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("sample.md").write_text("L3 expands the fixed reviewer count.\n", encoding="utf-8")
            findings = collect_findings(root, ["sample.md"])
        self.assertEqual(["sample.md:1: bare layer token `L3`"], findings)

    def test_local_text_uses_explicit_terms(self):
        findings = collect_findings(REPO_ROOT, local_scan_paths(REPO_ROOT))
        self.assertEqual([], findings)

    def test_ci_enables_installed_skill_copy_scan(self):
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("Install Codex skills for installed-copy terminology scan", workflow)
        self.assertIn("HOME: ${{ runner.temp }}/ghost-alice-ci-home", workflow)
        self.assertIn("bash ./install.sh --platform codex --skip-source-health", workflow)
        self.assertIn(
            "GHOST_ALICE_INSTALLED_SKILLS_DIR: ${{ runner.temp }}/ghost-alice-ci-home/.agents/skills",
            workflow,
        )

    def test_installed_skill_text_uses_explicit_terms_when_available(self):
        skills_dir = os.environ.get("GHOST_ALICE_INSTALLED_SKILLS_DIR")
        if not skills_dir:
            self.skipTest("set GHOST_ALICE_INSTALLED_SKILLS_DIR to scan installed skill copies")
        root = Path(skills_dir)
        findings = collect_findings(root, ["."])
        self.assertEqual([], findings)
