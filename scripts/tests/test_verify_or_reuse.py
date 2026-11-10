"""Contract tests for the shared verify-or-reuse decision used by Claude and Codex.

Dependencies: Python 3.11+ standard library only. The hook-path tests also use
Node.js when it is available and skip otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "coding-convention" / "verification-before-completion"
HOOK_DISPATCHER = REPO_ROOT / "_shared" / "ghost-alice-hook.mjs"
RULE_BLOCKS = REPO_ROOT / "_shared" / "global_rule_blocks.py"
sys.path.insert(0, str(REPO_ROOT / "_shared"))
import global_rule_blocks as blocks

VERIFY_OR_REUSE_PRECHECK = "Reuse unchanged evidence with its original source and verification time."


class RuntimeSurfaceTests(unittest.TestCase):
    def _run_pretool(self, platform: str) -> dict:
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")
        with tempfile.TemporaryDirectory() as temp_home:
            env = {key: value for key, value in os.environ.items() if not key.startswith("GHOST_ALICE_")}
            env.update({"HOME": temp_home, "USERPROFILE": temp_home})
            result = subprocess.run(
                [node, str(HOOK_DISPATCHER), "--platform", platform, "--event", "PreToolUse"],
                input=json.dumps({"session_id": f"s-{platform}-verify-or-reuse", "hook_event_name": "PreToolUse",
                                  "tool_name": "Read", "tool_input": {"file_path": "plan.md"}}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_healthy_codex_checkpoint_keeps_shared_contract_out_of_tool_context(self) -> None:
        payload = self._run_pretool("codex")
        context = payload.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertEqual(context, '')
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))

    def test_claude_pretool_allow_carries_no_context(self) -> None:
        self.assertEqual(self._run_pretool("claude"), {})

    def _merge(self, command: str, source: Path, dest: Path) -> str:
        result = subprocess.run(
            [sys.executable, "-B", str(RULE_BLOCKS), command, "--source", str(source), "--dest", str(dest)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("updated:"), result.stdout)
        return " ".join(dest.read_text(encoding="utf-8").split())

    def test_installer_rule_block_merge_carries_contract_for_both_platforms(self) -> None:
        targets = {
            "claude-merge": (REPO_ROOT / "platforms" / "claude" / "CLAUDE.md", blocks.CLAUDE_SPEC),
            "codex-merge": (REPO_ROOT / "platforms" / "codex" / "AGENTS.md", blocks.CODEX_SPEC),
        }
        common = " ".join((REPO_ROOT / "platforms" / "common-governance.md").read_text(encoding="utf-8").split())
        for command, (source, block_spec) in targets.items():
            with self.subTest(command=command, case="fresh-install"), tempfile.TemporaryDirectory() as temp_dir:
                merged = self._merge(command, source, Path(temp_dir) / "rules.md")
                self.assertEqual(merged.count(common), 1)
            with self.subTest(command=command, case="refresh-managed-block"), tempfile.TemporaryDirectory() as temp_dir:
                dest = Path(temp_dir) / "rules.md"
                dest.write_text(
                    f"User-owned note stays.\n{block_spec.begin}\nstale managed text\n{block_spec.end}\n",
                    encoding="utf-8",
                )
                merged = self._merge(command, source, dest)
                self.assertIn("User-owned note stays.", merged)
                self.assertNotIn("stale managed text", merged)
                self.assertEqual(merged.count(common), 1)


class InstalledCopyTests(unittest.TestCase):
    """Isolated-install parity; set the variables to installer outputs under a temporary HOME."""

    FILES = ("SKILL.md", "references/verify-or-reuse.md")

    def test_ci_installs_claude_skills_for_the_claude_copy_comparison(self) -> None:
        # Without GHOST_ALICE_INSTALLED_CLAUDE_DIR the Claude copy test below skips in CI;
        # without the Claude install it fails.
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("bash ./install.sh --platform claude --skip-source-health", workflow)
        self.assertIn("GHOST_ALICE_INSTALLED_CLAUDE_DIR: ${{ runner.temp }}/ghost-alice-ci-home/.claude", workflow)

    def _check_installed_skill(self, skills_dir: Path) -> None:
        installed = skills_dir / "verification-before-completion"
        for rel in self.FILES:
            with self.subTest(file=rel):
                self.assertEqual((installed / rel).read_bytes(), (SKILL_DIR / rel).read_bytes())

    def test_installed_codex_copy_matches_repository_ssot(self) -> None:
        skills_dir = os.environ.get("GHOST_ALICE_INSTALLED_SKILLS_DIR")
        if not skills_dir:
            self.skipTest("set GHOST_ALICE_INSTALLED_SKILLS_DIR to an isolated Codex install")
        skills_path = Path(skills_dir)
        self._check_installed_skill(skills_path)
        agents = skills_path.parents[1] / ".codex" / "AGENTS.md"
        self.assertTrue(agents.is_file())
        loader = agents.read_text(encoding="utf-8")
        companion = agents.with_name("ghost-alice-governance.md")
        self.assertIn("Common Ghost-ALICE governance comes from", loader)
        self.assertIn("Project instruction files supply project-specific rules", loader)
        self.assertIn(str(companion), loader)
        self.assertTrue(companion.is_file())
        self.assertIn(VERIFY_OR_REUSE_PRECHECK, " ".join(companion.read_text(encoding="utf-8").split()))

    def test_installed_claude_copy_matches_repository_ssot(self) -> None:
        claude_dir = os.environ.get("GHOST_ALICE_INSTALLED_CLAUDE_DIR")
        if not claude_dir:
            self.skipTest("set GHOST_ALICE_INSTALLED_CLAUDE_DIR to an isolated Claude install")
        claude_path = Path(claude_dir)
        self._check_installed_skill(claude_path / "skills")
        self.assertIn(VERIFY_OR_REUSE_PRECHECK, " ".join((claude_path / "CLAUDE.md").read_text(encoding="utf-8").split()))


if __name__ == "__main__":
    unittest.main()
