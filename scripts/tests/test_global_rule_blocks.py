import importlib.util
import inspect
import os
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
    def test_repeated_proposal_reuses_identical_file_without_rewriting_it(self):
        blocks = _load_global_rule_blocks()
        for apply, marker, render in (
                (blocks.apply_claude_bootstrap, blocks.CLAUDE_BOOTSTRAP_MARKER, blocks.render_claude_bootstrap),
                (blocks.apply_codex_bootstrap, blocks.CODEX_BOOTSTRAP_MARKER, blocks.render_codex_bootstrap)):
            with self.subTest(apply=apply.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, dest = root / "source.md", root / "rules.md"
                source_body = marker + "\n\nmanaged rules\n"
                source.write_text(source_body, encoding="utf-8")
                dest.write_bytes(b"personal rules\n")

                first = apply(source, dest)
                before = first.path.stat()
                paths = set(root.iterdir())
                again = apply(source, dest)

                self.assertEqual((again.status, again.path), ("proposed", first.path))
                self.assertEqual(again.path.read_bytes(), render(source_body).replace("\n", os.linesep).encode("utf-8"))
                after = again.path.stat()
                self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
                self.assertEqual(set(root.iterdir()), paths)
                self.assertEqual(dest.read_bytes(), b"personal rules\n")

    def test_existing_suffixed_proposal_is_reused_with_or_without_base_file(self):
        blocks = _load_global_rule_blocks()
        for apply, marker in ((blocks.apply_claude_bootstrap, blocks.CLAUDE_BOOTSTRAP_MARKER),
                              (blocks.apply_codex_bootstrap, blocks.CODEX_BOOTSTRAP_MARKER)):
            for base_exists in (False, True):
                with self.subTest(apply=apply.__name__, base_exists=base_exists), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    source, dest = root / "source.md", root / "rules.md"
                    proposed = root / "custom[proposal]"
                    source.write_text(marker + "\n\nmanaged rules\n", encoding="utf-8")
                    dest.write_bytes(b"personal rules\n")
                    first = apply(source, dest, proposed_path=proposed)
                    saved = proposed.with_name(proposed.name + ".saved")
                    first.path.rename(saved)
                    if base_exists:
                        proposed.write_bytes(b"older proposal\n")
                    conflict = proposed.with_name(proposed.name + ".conflict")
                    conflict.write_bytes(b"\xffpersonal proposal\n")
                    link = proposed.with_name(proposed.name + ".link")
                    link.symlink_to(saved)
                    unrelated = proposed.with_name(proposed.name + "-unrelated")
                    unrelated.write_bytes(saved.read_bytes())
                    before = saved.stat()
                    paths = set(root.iterdir())

                    result = apply(source, dest, proposed_path=proposed)

                    self.assertEqual((result.status, result.path), ("proposed", saved))
                    after = saved.stat()
                    self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
                    self.assertEqual(set(root.iterdir()), paths)
                    self.assertEqual(conflict.read_bytes(), b"\xffpersonal proposal\n")
                    self.assertEqual(link.readlink(), saved)
                    self.assertEqual(dest.read_bytes(), b"personal rules\n")
                    if base_exists:
                        self.assertEqual(proposed.read_bytes(), b"older proposal\n")

    def test_changed_source_creates_new_proposal_and_reuses_each_version(self):
        blocks = _load_global_rule_blocks()
        for apply, marker in ((blocks.apply_claude_bootstrap, blocks.CLAUDE_BOOTSTRAP_MARKER),
                              (blocks.apply_codex_bootstrap, blocks.CODEX_BOOTSTRAP_MARKER)):
            with self.subTest(apply=apply.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, dest = root / "source.md", root / "rules.md"
                source.write_text(marker + "\n\nmanaged v1\n", encoding="utf-8")
                dest.write_bytes(b"personal rules\n")
                first = apply(source, dest)
                original = first.path.read_bytes()

                source.write_text(marker + "\n\nmanaged v2\n", encoding="utf-8")
                second = apply(source, dest)
                self.assertNotEqual(second.path, first.path)
                self.assertIn(b"managed v2", second.path.read_bytes())
                self.assertEqual(first.path.read_bytes(), original)
                self.assertEqual(apply(source, dest).path, second.path)

                source.write_text(marker + "\n\nmanaged v1\n", encoding="utf-8")
                self.assertEqual(apply(source, dest).path, first.path)
                self.assertEqual(dest.read_bytes(), b"personal rules\n")

    def test_edited_proposals_are_preserved_and_not_reused(self):
        blocks = _load_global_rule_blocks()
        for apply, marker in ((blocks.apply_claude_bootstrap, blocks.CLAUDE_BOOTSTRAP_MARKER),
                              (blocks.apply_codex_bootstrap, blocks.CODEX_BOOTSTRAP_MARKER)):
            with self.subTest(apply=apply.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, dest = root / "source.md", root / "rules.md"
                source.write_text(marker + "\n\nmanaged rules\n", encoding="utf-8")
                dest.write_bytes(b"personal rules\n")
                first = apply(source, dest)
                first.path.write_bytes(b"edited base proposal\n")
                second = apply(source, dest)
                second.path.write_bytes(b"edited suffixed proposal\n")

                third = apply(source, dest)

                self.assertNotIn(third.path, (first.path, second.path))
                self.assertIn(b"managed rules", third.path.read_bytes())
                self.assertEqual(first.path.read_bytes(), b"edited base proposal\n")
                self.assertEqual(second.path.read_bytes(), b"edited suffixed proposal\n")
                self.assertEqual(apply(source, dest).path, third.path)
                self.assertEqual(dest.read_bytes(), b"personal rules\n")

    def test_proposal_creation_preserves_existing_user_files_and_links(self):
        blocks = _load_global_rule_blocks()
        for apply, marker in ((blocks.apply_claude_bootstrap, blocks.CLAUDE_BOOTSTRAP_MARKER),
                              (blocks.apply_codex_bootstrap, blocks.CODEX_BOOTSTRAP_MARKER)):
            for existing_rules in (b"user rules\n", b"\xffuser rules\n"):
                kinds = ["file", "non-utf8", "directory", "link", "matching-link", "dangling-link"]
                if hasattr(os, "mkfifo"):
                    kinds.append("fifo")
                for proposal_kind in kinds:
                    with self.subTest(apply=apply.__name__, rules=existing_rules, proposal_kind=proposal_kind):
                        with tempfile.TemporaryDirectory() as tmp:
                            root = Path(tmp)
                            source, dest = root / "source.md", root / "rules.md"
                            proposed = dest.with_name(dest.name + ".ghost-alice-proposed")
                            target = root / "personal.md"
                            source.write_text(marker + "\n\nmanaged rules\n", encoding="utf-8")
                            dest.write_bytes(existing_rules)
                            if proposal_kind == "file":
                                proposed.write_bytes(b"personal proposal\n")
                            elif proposal_kind == "non-utf8":
                                proposed.write_bytes(b"\xffpersonal proposal\n")
                            elif proposal_kind == "directory":
                                proposed.mkdir()
                            elif proposal_kind == "fifo":
                                os.mkfifo(proposed)
                            else:
                                if proposal_kind == "link":
                                    target.write_bytes(b"personal target\n")
                                elif proposal_kind == "matching-link":
                                    apply(source, dest, proposed_path=target)
                                proposed.symlink_to(target)
                            before = proposed.lstat()
                            result = apply(source, dest)
                            self.assertEqual(result.status, "proposed")
                            self.assertNotEqual(result.path, proposed)
                            self.assertIn("managed rules", result.path.read_text())
                            again = apply(source, dest)
                            self.assertEqual(again.status, "proposed")
                            self.assertEqual(again.path, result.path)
                            self.assertEqual(dest.read_bytes(), existing_rules)
                            after = proposed.lstat()
                            self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
                            if proposal_kind == "file":
                                self.assertEqual(proposed.read_bytes(), b"personal proposal\n")
                            elif proposal_kind == "non-utf8":
                                self.assertEqual(proposed.read_bytes(), b"\xffpersonal proposal\n")
                            elif "link" in proposal_kind:
                                self.assertEqual(proposed.readlink(), target)
                                if proposal_kind == "link":
                                    self.assertEqual(target.read_bytes(), b"personal target\n")
                                elif proposal_kind == "matching-link":
                                    self.assertEqual(target.read_bytes(), result.path.read_bytes())
                                else:
                                    self.assertFalse(target.exists())

    def test_platform_install_composes_one_common_source_and_preserves_rules_outside_managed_block(self):
        blocks = _load_global_rule_blocks()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / 'platforms/common-governance.md'
            common.parent.mkdir()
            common.write_text('# Ghost-ALICE Common Contract\n\nOne current procedure.\n')
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
                common.write_text('# Ghost-ALICE Common Contract\n\nOne current procedure.\n')

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

    def test_existing_claude_managed_block_is_replaced_and_text_outside_it_is_preserved(self) -> None:
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

    def test_remove_claude_managed_block_preserves_text_outside_it(self) -> None:
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

    def test_existing_codex_managed_block_is_replaced_and_text_outside_it_is_preserved(self) -> None:
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

    def test_remove_codex_managed_block_preserves_text_outside_it(self) -> None:
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
        for renderer in (
            blocks.render_codex_managed_block,
            blocks.render_codex_bootstrap,
            blocks.merge_codex_bootstrap_text,
            blocks.apply_codex_bootstrap,
        ):
            self.assertNotIn("hookless_fallback", inspect.signature(renderer).parameters)

        body = blocks.render_codex_bootstrap(source)

        self.assertEqual(body.count("When this session's context carries no Ghost-ALICE session metadata"), 1)
        self.assertIn("does not invent session metadata, and does not claim input, response route, Stop notice, hook log or io-trace records", body)

    def test_claude_source_owns_one_hookless_fallback(self) -> None:
        blocks = _load_global_rule_blocks()
        source = blocks.platform_contract_text(REPO_ROOT / "platforms/claude/CLAUDE.md")

        self.assertTrue(source.startswith(blocks.CLAUDE_BOOTSTRAP_MARKER))
        for renderer in (
            blocks.render_claude_managed_block,
            blocks.render_claude_bootstrap,
            blocks.merge_claude_bootstrap_text,
            blocks.apply_claude_bootstrap,
        ):
            self.assertNotIn("hookless_fallback", inspect.signature(renderer).parameters)

        body = blocks.render_claude_bootstrap(source)

        self.assertEqual(body.count("When this session's context carries no Ghost-ALICE session metadata"), 1)
        self.assertIn("does not claim input, response route, Stop notice, hook log or io-trace records", body)

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
