import os
import re
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

BASE_SCAN_PATHS = [
    "AGENTS.md",
    "architecture.md",
    "adversarial-verification",
    "docs",
    "jailbreak-detector",
    "merge-companion",
    "platforms/codex/AGENTS.md",
    "scripts",
    "skill-catalog",
    "task-router",
    "_shared",
]

SKIP_PARTS = {
    ".git",
    "__pycache__",
    "design-library",
    "node_modules",
}

SKIP_FILES = {
    Path("scripts/tests/test_terminology_clarity.py"),
}

TEXT_SUFFIXES = {".json", ".md", ".mjs", ".py"}

LINE_ALLOWLIST = [
    # External model identifiers are API literals; clarify nearby prose instead.
    re.compile(r"korean-MiniLM-L6-v2"),
]

FORBIDDEN_EXACT = [
    "SG1",
    "cNoHardcode",
    "cModelDecision",
    "cInvariant",
    "cInjectionResistant",
    "cEnforce",
    "P1",
    "P2",
    "P3",
    "P4",
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "D6",
    "D7",
    "D8",
    "D9",
    "D10",
    "D11",
    "D12",
    "EN1",
    "IN1",
    "IN2",
    "IN3",
]

BARE_LAYER_RE = re.compile(r"(?<![A-Za-z0-9_])L[1-8](?![A-Za-z0-9_])")


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
    exact_patterns = [
        (term, re.compile(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])"))
        for term in FORBIDDEN_EXACT
    ]
    for path in iter_text_files(root, scan_paths):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(root)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in LINE_ALLOWLIST):
                continue
            for term, pattern in exact_patterns:
                if pattern.search(line):
                    findings.append(f"{rel}:{lineno}: opaque term `{term}`")
            for match in BARE_LAYER_RE.finditer(line):
                findings.append(f"{rel}:{lineno}: bare layer token `{match.group(0)}`")
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

    def test_addon_text_uses_explicit_terms_when_available(self):
        addon_dir = os.environ.get("GHOST_ALICE_ADDON_DIR")
        if not addon_dir:
            self.skipTest("set GHOST_ALICE_ADDON_DIR to scan addon skill source")
        root = Path(addon_dir)
        findings = collect_findings(root, ["addons"])
        self.assertEqual([], findings)

    def test_installed_skill_text_uses_explicit_terms_when_available(self):
        skills_dir = os.environ.get("GHOST_ALICE_INSTALLED_SKILLS_DIR")
        if not skills_dir:
            self.skipTest("set GHOST_ALICE_INSTALLED_SKILLS_DIR to scan installed skill copies")
        root = Path(skills_dir)
        findings = collect_findings(root, ["."])
        self.assertEqual([], findings)

    def test_wiki_governance_text_uses_explicit_terms_when_available(self):
        wiki_dir = os.environ.get("GHOST_ALICE_WIKI_DIR")
        if not wiki_dir:
            self.skipTest("set GHOST_ALICE_WIKI_DIR to scan the remote wiki checkout")
        root = Path(wiki_dir)
        findings = collect_findings(root, ["."])
        self.assertEqual([], findings)
