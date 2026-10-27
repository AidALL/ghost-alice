import importlib.util
import inspect
import re
import sys
import tempfile
import unittest
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parents[2]
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
    def test_project_loader_does_not_reinject_common_contract(self):
        body = (REPO_ROOT / 'AGENTS.md').read_text()
        self.assertFalse('## First-Entry Contract' in body, 'project loader duplicates the common procedure')
        self.assertIn('installed common contract', body)

    def test_platform_install_composes_one_common_source_and_preserves_user_rules(self):
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / 'platforms/common-governance.md'
            common.parent.mkdir()
            common.write_text('# Ghost-ALICE Common Governance\n\nOne current procedure.\n')
            for platform, name, apply, marker, render in (
                    ('codex', 'AGENTS.md', blocks.apply_codex_bootstrap, blocks.CODEX_BOOTSTRAP_MARKER, blocks.render_codex_bootstrap),
                    ('claude', 'CLAUDE.md', blocks.apply_claude_bootstrap, blocks.CLAUDE_BOOTSTRAP_MARKER, blocks.render_claude_bootstrap)):
                source = common.parent / platform / name
                source.parent.mkdir()
                source.write_text(marker + '\n\n{{COMMON_GOVERNANCE}}\n\nHost-specific tools.\n')
                dest = root / platform / name
                dest.parent.mkdir()
                dest.write_text(render(marker + '\n\nOld contract.\n') + '\nUser-owned rule.\n')
                result = apply(source, dest, proposed_path=root / (platform + '.proposed'))
                rendered = result.path.read_text()
                self.assertEqual(rendered.count('One current procedure.'), 1)
                self.assertFalse('{{COMMON_GOVERNANCE}}' in rendered, 'installer left an unresolved common source')
                self.assertIn('User-owned rule.', rendered)
                common.unlink()
                with self.assertRaises(blocks.GlobalRuleBlockError):
                    apply(source, dest)
                common.write_text('# Ghost-ALICE Common Governance\n\nOne current procedure.\n')

    def assert_intake_routing_authority(self, body: str) -> None:
        self.assertIn("`task-router` owns this rule and this section carries it, so applying it loads no skill", body)
        self.assertIn("`session-intent-analyzer`: reading or writing the intent record", body)
        self.assertIn("The native host executes tools and enforces permission", body)
        self.assertNotIn("jailbreak-detector", body)

    def test_rendered_fallback_rejects_classifier_or_audit_authority(self) -> None:
        blocks = _load_global_rule_blocks()
        for rel, render in (("platforms/codex/AGENTS.md", blocks.render_codex_bootstrap),
                            ("platforms/claude/CLAUDE.md", blocks.render_claude_bootstrap)):
            body = render(blocks.platform_contract_text(REPO_ROOT / rel))
            for old, new in (("`session-intent-analyzer`: reading or writing the intent record", "Session intake requires jailbreak-detector permission before task-router"),
                             ("The native host executes tools and enforces permission", "Audit records grant execution permission")):
                with self.subTest(path=rel, change=old), self.assertRaises(AssertionError):
                    self.assert_intake_routing_authority(body.replace(old, new))

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
        source = blocks.platform_contract_text(REPO_ROOT / "platforms/codex/AGENTS.md")
        self.assertFalse(hasattr(blocks, "CODEX_HOOKLESS_FALLBACK_BLOCK"))
        for renderer in (
            blocks.render_codex_managed_block,
            blocks.render_codex_bootstrap,
            blocks.merge_codex_bootstrap_text,
            blocks.apply_codex_bootstrap,
        ):
            self.assertNotIn("hookless_fallback", inspect.signature(renderer).parameters)

        body = blocks.render_codex_bootstrap(source)

        self.assert_intake_routing_authority(body)
        self.assertEqual(body.count("When this session's context carries no Ghost-ALICE session receipt"), 1)
        self.assertIn("does not invent a receipt, and does not claim intake, routing, Stop or audit records", body)
        self.assertIn("Repeated healthy tool calls and new input receipts alone need no checkpoint", body)
        self.assertIn("Before a tool call that changes an authority boundary, the next work decision or a required verification", body)
        self.assertIn("strict audit", body)

    def test_claude_source_owns_the_common_contract_and_one_hookless_fallback(self) -> None:
        blocks = _load_global_rule_blocks()
        ssot = (REPO_ROOT / "platforms/common-governance.md").read_text(encoding="utf-8")
        source = blocks.platform_contract_text(REPO_ROOT / "platforms/claude/CLAUDE.md")

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

        self.assertEqual(body.count("When this session's context carries no Ghost-ALICE session receipt"), 1)
        self.assertIn("does not claim intake, routing, Stop or audit records", body)
        self.assert_intake_routing_authority(body)
        self.assertIn("task-router", body)
        self.assertIn("verification-before-completion", body)
        self.assertIn("## io-trace Transparency Rule", body)

    def test_installers_route_global_rule_files_through_block_helper(self) -> None:
        hosts_py = (REPO_ROOT / "_shared" / "installer" / "hosts.py").read_text(encoding="utf-8")
        self.assertIn("global_rule_blocks.py", hosts_py)
        self.assertIn("claude-merge", hosts_py)
        self.assertIn("claude-remove", hosts_py)
        self.assertIn("def ensure_claude_bootstrap", hosts_py)
        self.assertIn("def remove_claude_bootstrap_if_unused", hosts_py)
        self.assertIn("codex-merge", hosts_py)
        self.assertIn("codex-remove", hosts_py)
        self.assertNotIn("get_codex_bootstrap_content", hosts_py)
        self.assertNotIn("--hookless-fallback", hosts_py)


if __name__ == "__main__":
    unittest.main()
