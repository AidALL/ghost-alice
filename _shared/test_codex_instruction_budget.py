"""Check that Codex's project_doc_max_bytes budget covers the whole repository AGENTS.md."""

from __future__ import annotations

from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".codex" / "config.toml"
DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024


class CodexInstructionBudgetTests(unittest.TestCase):
    def test_project_budget_covers_complete_repository_instructions(self) -> None:
        config = tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        budget = config.get("project_doc_max_bytes", DEFAULT_PROJECT_DOC_MAX_BYTES)
        self.assertGreaterEqual(budget, (ROOT / "AGENTS.md").stat().st_size)


if __name__ == "__main__":
    unittest.main()
