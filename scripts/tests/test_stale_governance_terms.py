"""Regression tests for stale Ghost-ALICE governance terminology.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

ALLOWED_PATHS = set()

LEGACY_TERM = "har" + "ness"

STALE_PATTERNS = [
    LEGACY_TERM + "-security-scan",
    LEGACY_TERM + "-adapters",
    "validate_" + LEGACY_TERM + "_adapters",
    "scan_" + LEGACY_TERM + "_surface",
    LEGACY_TERM + "-improvement",
    "test " + LEGACY_TERM,
]

# Legacy skill names intentionally referenced for deprecated-install cleanup,
# NOT stale current usages. install.sh keeps DEPRECATED_INSTALLED_SKILLS to remove
# old installs by their original directory name; install.ps1 mirrors that list; and
# test_installer_default_auto.py verifies the cleanup. These references must keep the
# old name to find and remove old installs, so they are exempt term-by-term here.
# (Built from LEGACY_TERM so this file does not trip its own scan.)
ALLOWED_TERM_OCCURRENCES = {
    "install.sh": {LEGACY_TERM + "-security-scan"},
    "install.ps1": {LEGACY_TERM + "-security-scan"},
    "scripts/tests/test_installer_default_auto.py": {LEGACY_TERM + "-security-scan"},
}

EXCLUDED_FALLBACK_DIRS = {
    ".git",
    ".tmp",
    ".worktrees",
    "__pycache__",
}

EXCLUDED_FALLBACK_FILES = {
    ".claude/settings.json",
    ".claude/settings.local.json",
}


def _source_files_from_worktree(root: Path) -> list[str]:
    paths: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if any(part in EXCLUDED_FALLBACK_DIRS for part in path.relative_to(root).parts):
            continue
        if rel in EXCLUDED_FALLBACK_FILES:
            continue
        if path.is_file():
            paths.append(rel)
    return paths


def source_files(root: Path = ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.splitlines()
    return _source_files_from_worktree(root)


class SourceFileDiscoveryTests(unittest.TestCase):
    def test_source_files_works_without_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("# public snapshot\n", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "check.py").write_text("print('ok')\n", encoding="utf-8")
            (root / ".tmp").mkdir()
            (root / ".tmp" / "local.md").write_text("private state\n", encoding="utf-8")

            self.assertEqual(source_files(root), ["README.md", "scripts/check.py"])


class StaleGovernanceTerminologyTests(unittest.TestCase):
    def test_stale_governance_terms_are_absent(self) -> None:
        offenders: list[str] = []
        for rel_path in source_files():
            if rel_path in ALLOWED_PATHS:
                continue
            path = ROOT / rel_path
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # Skip files that are not decodable text, or that are tracked in
                # the index but absent on disk (e.g. a deletion not yet staged).
                continue
            allowed_terms = ALLOWED_TERM_OCCURRENCES.get(rel_path, set())
            for pattern in STALE_PATTERNS:
                if pattern in allowed_terms:
                    continue
                if pattern in text or pattern in rel_path:
                    offenders.append(f"{rel_path}: {pattern}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
