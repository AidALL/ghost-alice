from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/skill-validation.yml",
)


class GithubWorkflowNodeVersionTest(unittest.TestCase):
    def test_ci_workflows_pin_node_24(self) -> None:
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow):
                text = (REPO_ROOT / workflow).read_text(encoding="utf-8")
                # Keep the action and its input in the same step of these block-style workflows.
                steps = re.split(r'(?m)^\s*- (?=name:|uses:)', text)
                node_steps = [step for step in steps if 'actions/setup-node@' in step]
                self.assertTrue(node_steps, 'workflow must contain a setup-node step')
                for step in node_steps:
                    self.assertRegex(step, r'(?m)^\s*uses: actions/setup-node@v7\s*$')
                    self.assertRegex(step, r'''(?m)^\s*with:\s*\n\s+node-version: ['"]24['"]\s*$''')


if __name__ == "__main__":
    unittest.main()
