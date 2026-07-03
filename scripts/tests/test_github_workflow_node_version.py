from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/skill-gate-contract.yml",
    ".github/workflows/skill-validation.yml",
)


class GithubWorkflowNodeVersionTest(unittest.TestCase):
    def test_ci_workflows_pin_node_24(self) -> None:
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow):
                text = (REPO_ROOT / workflow).read_text(encoding="utf-8")
                self.assertIn("actions/setup-node@v4", text)
                self.assertIn('node-version: "24"', text)


if __name__ == "__main__":
    unittest.main()
