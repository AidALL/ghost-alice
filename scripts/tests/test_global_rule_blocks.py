import importlib.util
import inspect
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
GLOBAL_RULE_BLOCKS = REPO_ROOT / "_shared" / "global_rule_blocks.py"


def _load_global_rule_blocks():
    if not GLOBAL_RULE_BLOCKS.exists():
        raise AssertionError("_shared/global_rule_blocks.py must exist")
    spec = importlib.util.spec_from_file_location("global_rule_blocks_under_test", GLOBAL_RULE_BLOCKS)
    if spec is None or spec.loader is None:
        raise AssertionError("global_rule_blocks.py must be importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GlobalRuleBlockTest(unittest.TestCase):
    def test_claude_bootstrap_is_created_when_destination_is_missing(self) -> None:
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-CLAUDE.md"
            dest = root / "config" / "CLAUDE.md"
            source.write_text("# Ghost-ALICE Claude Bootstrap\n\nmanaged v1\n", encoding="utf-8")

            result = blocks.apply_claude_bootstrap(source, dest)

            self.assertEqual(result.status, "updated")
            body = dest.read_text(encoding="utf-8")
            self.assertTrue(body.startswith(blocks.CLAUDE_BOOTSTRAP_MARKER))
            self.assertIn(blocks.CLAUDE_MANAGED_BLOCK_BEGIN, body)
            self.assertIn("managed v1", body)

    def test_markerless_claude_rules_write_proposed_without_touching_existing(self) -> None:
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-CLAUDE.md"
            dest = root / "CLAUDE.md"
            proposed = root / "CLAUDE.md.ghost-alice-proposed"
            source.write_text("# Ghost-ALICE Claude Bootstrap\n\nmanaged v2\n", encoding="utf-8")
            dest.write_text("# user local rules\n\nkeep me\n", encoding="utf-8")

            result = blocks.apply_claude_bootstrap(source, dest, proposed_path=proposed)

            self.assertEqual(result.status, "proposed")
            self.assertEqual(dest.read_text(encoding="utf-8"), "# user local rules\n\nkeep me\n")
            proposed_body = proposed.read_text(encoding="utf-8")
            self.assertIn(blocks.CLAUDE_MANAGED_BLOCK_BEGIN, proposed_body)
            self.assertIn("managed v2", proposed_body)

    def test_existing_claude_managed_block_is_replaced_and_user_text_is_preserved(self) -> None:
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-CLAUDE.md"
            dest = root / "CLAUDE.md"
            source.write_text("# Ghost-ALICE Claude Bootstrap\n\nmanaged v2\n", encoding="utf-8")
            dest.write_text(
                "# Ghost-ALICE Claude Bootstrap\n"
                f"{blocks.CLAUDE_MANAGED_BLOCK_BEGIN}\n"
                "managed v1\n"
                f"{blocks.CLAUDE_MANAGED_BLOCK_END}\n"
                "\n# user appendix\nkeep me\n",
                encoding="utf-8",
            )

            result = blocks.apply_claude_bootstrap(source, dest)

            self.assertEqual(result.status, "updated")
            body = dest.read_text(encoding="utf-8")
            self.assertIn("managed v2", body)
            self.assertNotIn("managed v1", body)
            self.assertIn("# user appendix\nkeep me\n", body)

    def test_remove_claude_managed_block_preserves_user_text(self) -> None:
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dest = root / "CLAUDE.md"
            dest.write_text(
                "# Ghost-ALICE Claude Bootstrap\n"
                f"{blocks.CLAUDE_MANAGED_BLOCK_BEGIN}\n"
                "managed rules\n"
                f"{blocks.CLAUDE_MANAGED_BLOCK_END}\n"
                "\n# user appendix\nkeep me\n",
                encoding="utf-8",
            )

            result = blocks.remove_claude_bootstrap(dest)

            self.assertEqual(result.status, "updated")
            body = dest.read_text(encoding="utf-8")
            self.assertNotIn(blocks.CLAUDE_MANAGED_BLOCK_BEGIN, body)
            self.assertNotIn("managed rules", body)
            self.assertIn("# user appendix\nkeep me\n", body)

    def test_markerless_codex_agents_writes_proposed_without_touching_existing(self) -> None:
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-AGENTS.md"
            dest = root / "AGENTS.md"
            proposed = root / "AGENTS.md.ghost-alice-proposed"
            source.write_text("# Ghost-ALICE Codex Bootstrap\n\nmanaged v2\n", encoding="utf-8")
            dest.write_text("# user local rules\n\nkeep me\n", encoding="utf-8")

            result = blocks.apply_codex_bootstrap(source, dest, proposed_path=proposed)

            self.assertEqual(result.status, "proposed")
            self.assertEqual(dest.read_text(encoding="utf-8"), "# user local rules\n\nkeep me\n")
            proposed_body = proposed.read_text(encoding="utf-8")
            self.assertIn(blocks.CODEX_MANAGED_BLOCK_BEGIN, proposed_body)
            self.assertIn("managed v2", proposed_body)

    def test_existing_codex_managed_block_is_replaced_and_user_text_is_preserved(self) -> None:
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-AGENTS.md"
            dest = root / "AGENTS.md"
            source.write_text("# Ghost-ALICE Codex Bootstrap\n\nmanaged v2\n", encoding="utf-8")
            dest.write_text(
                "# Ghost-ALICE Codex Bootstrap\n"
                f"{blocks.CODEX_MANAGED_BLOCK_BEGIN}\n"
                "managed v1\n"
                f"{blocks.CODEX_MANAGED_BLOCK_END}\n"
                "\n# user appendix\nkeep me\n",
                encoding="utf-8",
            )

            result = blocks.apply_codex_bootstrap(source, dest)

            self.assertEqual(result.status, "updated")
            body = dest.read_text(encoding="utf-8")
            self.assertIn("managed v2", body)
            self.assertNotIn("managed v1", body)
            self.assertIn("# user appendix\nkeep me\n", body)

    def test_legacy_aidall_codex_managed_block_is_replaced(self) -> None:
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-AGENTS.md"
            dest = root / "AGENTS.md"
            proposed = root / "AGENTS.md.ghost-alice-proposed"
            source.write_text("# Ghost-ALICE Codex Bootstrap\n\nmanaged v2\n", encoding="utf-8")
            dest.write_text(
                "# AidALL Codex Bootstrap\n"
                "<!-- AidALL managed block begin: codex-bootstrap -->\n"
                "managed v1\n"
                "<!-- AidALL managed block end: codex-bootstrap -->\n"
                "\n# user appendix\nkeep me\n",
                encoding="utf-8",
            )

            result = blocks.apply_codex_bootstrap(source, dest, proposed_path=proposed)

            self.assertEqual(result.status, "updated")
            self.assertFalse(proposed.exists())
            body = dest.read_text(encoding="utf-8")
            self.assertTrue(body.startswith(blocks.CODEX_BOOTSTRAP_MARKER))
            self.assertIn(blocks.CODEX_MANAGED_BLOCK_BEGIN, body)
            self.assertIn("managed v2", body)
            self.assertNotIn("AidALL managed block", body)
            self.assertNotIn("managed v1", body)
            self.assertIn("# user appendix\nkeep me\n", body)

    def test_remove_codex_managed_block_preserves_user_text(self) -> None:
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dest = root / "AGENTS.md"
            dest.write_text(
                "# Ghost-ALICE Codex Bootstrap\n"
                f"{blocks.CODEX_MANAGED_BLOCK_BEGIN}\n"
                "managed rules\n"
                f"{blocks.CODEX_MANAGED_BLOCK_END}\n"
                "\n# user appendix\nkeep me\n",
                encoding="utf-8",
            )

            result = blocks.remove_codex_bootstrap(dest)

            self.assertEqual(result.status, "updated")
            body = dest.read_text(encoding="utf-8")
            self.assertNotIn(blocks.CODEX_MANAGED_BLOCK_BEGIN, body)
            self.assertNotIn("managed rules", body)
            self.assertIn("# user appendix\nkeep me\n", body)

    def test_codex_source_owns_one_hookless_fallback(self) -> None:
        blocks = _load_global_rule_blocks()
        source = (REPO_ROOT / "platforms" / "codex" / "AGENTS.md").read_text(
            encoding="utf-8"
        )
        self.assertFalse(hasattr(blocks, "CODEX_HOOKLESS_FALLBACK_BLOCK"))
        for renderer in (
            blocks.render_codex_managed_block,
            blocks.render_codex_bootstrap,
            blocks.merge_codex_bootstrap_text,
            blocks.apply_codex_bootstrap,
        ):
            self.assertNotIn("hookless_fallback", inspect.signature(renderer).parameters)

        body = blocks.render_codex_bootstrap(source)

        self.assertEqual(body.count("## Codex Hookless Fallback"), 1)
        self.assertNotIn("## Codex Hook Enforcement And Hookless Fallback", body)
        self.assertEqual(body.count("If hooks are disabled in the Codex session"), 1)
        self.assertIn("`[tool-checkpoint]`", body)
        self.assertIn("rejected-alternatives", body)
        self.assertIn("unverified-premises", body)
        self.assertIn("failure-mode-if-wrong", body)
        self.assertIn("recovery-action", body)
        self.assertNotIn("recovery-cost", body)
        self.assertNotIn("recovery-note", body)
        self.assertNotIn("compact `[tool-checkpoint]`", body)
        self.assertIn("[tool-checkpoint:batch]", body)
        self.assertIn("[tool-checkpoint:continuation]", body)
        self.assertIn("user-input tool batch", body)
        self.assertIn("same session input lineage", body)
        self.assertIn("new user input, current-lineage block/deny, mismatch", body)
        self.assertIn(
            "Do not infer whether an action is safe from tool-call identity or payload content",
            body,
        )
        self.assertIn(
            "decision depends only on the current-lineage block gate and the silent allow invariant",
            body,
        )

    def test_claude_source_owns_rules_zero_through_twelve_and_one_hookless_fallback(self) -> None:
        blocks = _load_global_rule_blocks()
        ssot = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        source = (REPO_ROOT / "platforms" / "claude" / "CLAUDE.md").read_text(
            encoding="utf-8"
        )

        heading_pattern = re.compile(r"(?m)^###\s*(\d+)\.\s*(.+)$")
        self.assertEqual(
            {int(number): title.strip() for number, title in heading_pattern.findall(ssot)},
            {int(number): title.strip() for number, title in heading_pattern.findall(source)},
        )
        self.assertTrue(source.startswith(blocks.CLAUDE_BOOTSTRAP_MARKER))
        self.assertFalse(hasattr(blocks, "CLAUDE_HOOKLESS_FALLBACK_BLOCK"))
        for renderer in (
            blocks.render_claude_managed_block,
            blocks.render_claude_bootstrap,
            blocks.merge_claude_bootstrap_text,
            blocks.apply_claude_bootstrap,
        ):
            self.assertNotIn("hookless_fallback", inspect.signature(renderer).parameters)

        body = blocks.render_claude_bootstrap(source)

        self.assertEqual(body.count("## Claude Hookless Fallback"), 1)
        self.assertEqual(body.count("If hooks are disabled in the Claude session"), 1)
        self.assertIn("`session-intent-analyzer`", body)
        self.assertIn("`jailbreak-detector`", body)
        self.assertIn("`task-router`", body)
        self.assertIn("`[completion-check]`", body)
        self.assertIn("`[io-trace]`", body)

    def test_installers_route_global_rule_files_through_block_helper(self) -> None:
        sh = installer_bash_source()
        self.assertIn("global_rule_blocks.py", sh)
        self.assertIn("claude-merge", sh)
        self.assertIn("claude-remove", sh)
        self.assertIn("ensure_claude_bootstrap", sh)
        self.assertIn("remove_claude_bootstrap_if_unused", sh)
        self.assertIn('claude) run_logged_if_compact ensure_claude_bootstrap "${SKILLS_DIR}"', sh)
        self.assertNotIn("remove_claude_bootstrap || return 1", sh)
        self.assertIn("codex-merge", sh)
        self.assertIn("codex-remove", sh)
        self.assertNotIn('get_codex_bootstrap_content > "$agents_path"', sh)
        self.assertNotIn("get_codex_bootstrap_content()", sh)
        self.assertNotIn("--hookless-fallback", sh)

        ps1 = installer_ps1_source()
        self.assertIn("global_rule_blocks.py", ps1)
        self.assertIn('"claude-merge"', ps1)
        self.assertIn('"claude-remove"', ps1)
        self.assertIn("function Set-ClaudeBootstrap", ps1)
        self.assertIn("function Remove-ClaudeBootstrapIfUnused", ps1)
        self.assertIn("Invoke-LoggedIfCompact { Set-ClaudeBootstrap }", ps1)
        self.assertIn("Remove-ClaudeBootstrapIfUnused -SkillsRoot $SkillsDir", ps1)
        self.assertNotIn('$Platform -eq "claude" -and -not (Remove-ClaudeBootstrap)', ps1)
        self.assertIn('"codex-merge"', ps1)
        self.assertIn('"codex-remove"', ps1)
        self.assertNotIn("[System.IO.File]::WriteAllText($agentsPath, $content", ps1)
        self.assertNotIn("function Get-CodexBootstrapContent", ps1)
        self.assertNotIn("function Get-CodexHooklessFallbackBlock", ps1)
        self.assertNotIn("--hookless-fallback", ps1)


if __name__ == "__main__":
    unittest.main()
