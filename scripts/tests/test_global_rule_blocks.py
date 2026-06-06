import importlib.util
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

    def test_codex_hookless_fallback_preserves_concise_tool_checkpoint_surface(self) -> None:
        blocks = _load_global_rule_blocks()
        body = blocks.CODEX_HOOKLESS_FALLBACK_BLOCK

        self.assertIn("hook-enforced", body)
        self.assertIn("`[tool-checkpoint]`", body)
        self.assertIn("Hook timing enforcement does not weaken the semantic gate", body)
        self.assertIn("rejected-alternatives", body)
        self.assertIn("unverified-premises", body)
        self.assertIn("failure-mode-if-wrong", body)
        self.assertIn("recovery-action", body)
        self.assertNotIn("recovery-cost", body)
        self.assertNotIn("recovery-note", body)
        self.assertNotIn("compact `[tool-checkpoint]`", body)
        self.assertIn("hookless/manual fallback", body)
        self.assertIn("[tool-checkpoint:batch]", body)
        self.assertIn("[tool-checkpoint:continuation]", body)
        self.assertIn("full gate", body)
        self.assertIn("full `[tool-checkpoint]`", body)
        self.assertIn("connects acceptance criteria to fresh evidence", body)
        self.assertIn("same process/session/tool-call id", body)
        self.assertIn("Switching to a new command, input, timeout, interruption, or ref", body)
        self.assertIn("Do not infer whether an action is safe from tool-call identity or payload content", body)
        self.assertIn("session-intent-analyzer digest/ledger/current-session pointer", body)
        self.assertIn("current-lineage block only carried to downstream-gates.json", body)
        self.assertIn("decision depends only on the current-lineage block gate and the silent allow invariant", body)

    def test_installers_route_global_rule_files_through_block_helper(self) -> None:
        sh = installer_bash_source()
        self.assertIn("global_rule_blocks.py", sh)
        self.assertIn("codex-merge", sh)
        self.assertIn("codex-remove", sh)
        self.assertNotIn('get_codex_bootstrap_content > "$agents_path"', sh)

        ps1 = installer_ps1_source()
        self.assertIn("global_rule_blocks.py", ps1)
        self.assertIn('"codex-merge"', ps1)
        self.assertIn('"codex-remove"', ps1)
        self.assertNotIn("[System.IO.File]::WriteAllText($agentsPath, $content", ps1)


if __name__ == "__main__":
    unittest.main()
