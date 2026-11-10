"""Codex TOML edits preserve user text while restoring recorded managed keys."""

import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
import install_hooks
import uninstall_cleanup
from installer_testing import installer_env


class CodexTomlRestoreTest(unittest.TestCase):
    def test_feature_writer_preserves_valid_tables_without_an_editable_header(self):
        for content in (
            'features.hooks = false\n',
            'features = { hooks = false, unrelated = true }\n',
            'features = {}\n',
        ):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                config = Path(temporary) / '.codex' / 'config.toml'
                config.parent.mkdir()
                config.write_text(content, encoding='utf-8')
                expected = tomllib.loads(content)
                expected['features']['hooks'] = True
                with patch.dict(os.environ, installer_env(temporary), clear=True):
                    self.assertTrue(install_hooks._ensure_codex_hook_feature_enabled())
                    self.assertTrue(install_hooks._codex_hook_feature_change_path().exists())
                self.assertEqual(tomllib.loads(config.read_text()), expected)
                self.assertEqual(config.with_suffix('.toml.bak').read_text(), content)

    def test_semantic_readers_accept_enabled_dotted_and_inline_tables(self):
        for content in ('features.hooks = true\n', 'features = { hooks = true, unrelated = true }\n'):
            with self.subTest(content=content):
                self.assertEqual(install_hooks._codex_hook_feature_state_in_content(content), 'true')
                self.assertEqual(install_hooks._merge_codex_hook_feature_flag(content), (content, False))
        for content in ('projects."/work".trust_level = "trusted"\n',
                        'projects = { "/work" = { trust_level = "trusted", other = 1 } }\n'):
            with self.subTest(content=content):
                self.assertEqual(install_hooks._codex_project_trust_state_in_content(content, '/work'), 'trusted')
                self.assertEqual(install_hooks._merge_codex_project_trust(content, '/work'), (content, False, 'trusted'))

    def test_project_and_trust_writers_preserve_unsupported_valid_table_forms(self):
        project = 'projects = { "/work" = { trust_level = "untrusted", other = 1 } }\n'
        updated, changed, before = install_hooks._merge_codex_project_trust(project, '/work')
        self.assertTrue(changed)
        self.assertEqual(tomllib.loads(updated)['projects']['/work'], {'trust_level': 'trusted', 'other': 1})
        hook_key = '/work/hooks.json:stop:0:0'
        trust = f'hooks.state."{hook_key}".trusted_hash = "old"\n'
        updated, changed = install_hooks._merge_codex_hook_trust_state(trust, {hook_key: 'new'})
        self.assertTrue(changed)
        self.assertEqual(tomllib.loads(updated)['hooks']['state'][hook_key]['trusted_hash'], 'new')
        restored, changed = uninstall_cleanup._restore_codex_project_trust_content(project, project_path='/work', before_state='missing')
        self.assertTrue(changed)
        self.assertEqual(tomllib.loads(restored)['projects']['/work'], {'other': 1})

    def test_feature_writer_and_confirm_restore_preserve_multiline_assignment_text(self):
        for quote in ('"""', "'''"):
            for before in ("missing", "false"):
                with self.subTest(quote=quote, before=before), tempfile.TemporaryDirectory() as temporary:
                    config = Path(temporary) / ".codex" / "config.toml"
                    config.parent.mkdir()
                    note = f'note = {quote}\nhooks = false\ncodex_hooks = true\nunicode:\u2028kept\u0085\n{quote} # note comment\n'
                    original = ('[features] # flags\n' + note
                                + ('"hooks" = false # managed key note\n' if before == "false" else '')
                                + '[user] # adjacent\nname = "retained"\n')
                    expected = tomllib.loads(original)
                    config.write_text(original, encoding="utf-8")
                    with patch.dict(os.environ, installer_env(temporary), clear=True):
                        self.assertTrue(install_hooks._ensure_codex_hook_feature_enabled())
                        installed = config.read_text(encoding="utf-8")
                        parsed = tomllib.loads(installed)
                        self.assertIs(parsed["features"].get("hooks"), True)
                        self.assertEqual(parsed["features"]["note"], expected["features"]["note"])
                        self.assertEqual(parsed["user"], expected["user"])
                        self.assertIn(note, installed)
                        self.assertFalse(install_hooks._ensure_codex_hook_feature_enabled())
                        change = json.loads(install_hooks._codex_hook_feature_change_path().read_text(encoding="utf-8"))
                        self.assertEqual(change["before_state"], before)
                        restored = uninstall_cleanup._codex_hook_feature_item_from_change(change, apply_changes=True)
                    self.assertEqual(restored["action"], "restored-codex-hooks-feature-flag")
                    self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8")), expected)
                    self.assertIn(note, config.read_text(encoding="utf-8"))

    def test_project_writer_and_confirm_restore_preserve_multiline_assignment_text(self):
        project = "/work/project"
        for quote in ('"""', "'''"):
            for before in ("missing", "untrusted"):
                with self.subTest(quote=quote, before=before), tempfile.TemporaryDirectory() as temporary:
                    config = Path(temporary) / ".codex" / "config.toml"
                    config.parent.mkdir()
                    note = f'note = {quote}\ntrust_level = "untrusted"\n{quote} # note comment\n'
                    original = (f"[projects.'{project}'] # project\n" + note
                                + ('"trust_level" = """\nuntrusted""" # managed key note\n' if before == "untrusted" else '')
                                + '[user] # adjacent\nname = "retained"\n')
                    expected = tomllib.loads(original)
                    with patch.dict(os.environ, installer_env(temporary), clear=True):
                        installed, changed, recorded = install_hooks._merge_codex_project_trust(original, project)
                        self.assertTrue(changed)
                        self.assertEqual(recorded, before)
                        install_hooks._write_text_file(config, installed)
                        parsed = tomllib.loads(config.read_text(encoding="utf-8"))
                        self.assertEqual(parsed["projects"][project]["trust_level"], "trusted")
                        self.assertEqual(parsed["projects"][project]["note"], expected["projects"][project]["note"])
                        self.assertIn(note, installed)
                        change = {"path": str(config), "project_path": project,
                                  "before_state": before, "after_state": "trusted"}
                        restored = uninstall_cleanup._codex_project_trust_item_from_change(change, apply_changes=True)
                    self.assertEqual(restored["action"], "removed-codex-project-trust" if before == "missing" else "restored-codex-project-trust")
                    self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8")), expected)
                    self.assertIn(note, config.read_text(encoding="utf-8"))

    def test_hook_trust_replaces_the_actual_multiline_assignment_span(self):
        key = "/work/hooks.json:stop:0:0"
        note = "note = '''\ntrusted_hash = 'decoy'\n''' # note comment\n"
        content = (f"[hooks.state.'{key}'] # hook\n" + note
                   + '"trusted_hash" = """\nold hash""" # managed key note\n'
                   + '[user] # adjacent\nname = "retained"\n')
        expected = tomllib.loads(content)
        expected["hooks"]["state"][key]["trusted_hash"] = "new hash"
        updated, changed = install_hooks._merge_codex_hook_trust_state(content, {key: "new hash"})
        self.assertTrue(changed)
        self.assertEqual(tomllib.loads(updated), expected)
        self.assertIn(note, updated)
        self.assertIn("# managed key note", updated)
        self.assertEqual(install_hooks._merge_codex_hook_trust_state(updated, {key: "new hash"}), (updated, False))

    def test_confirm_restore_uses_actual_assignment_spans_in_both_tables(self):
        project = "/work/project"
        for kind, key, current, prior in (("feature", "hooks", "true", "false"),
                                          ("project", "trust_level", '"""\ntrusted"""', "untrusted")):
            for before in ("missing", prior):
                with self.subTest(kind=kind, before=before), tempfile.TemporaryDirectory() as temporary:
                    config = Path(temporary) / ".codex" / "config.toml"
                    config.parent.mkdir()
                    header = "[features]" if kind == "feature" else f"[projects.'{project}']"
                    note = f"note = '''\n{key} = 'decoy'\n''' # note comment\n"
                    original = header + "\n" + note + f'"{key}" = {current} # managed key note\n'
                    expected = tomllib.loads(original)
                    table = expected["features"] if kind == "feature" else expected["projects"][project]
                    if before == "missing":
                        del table[key]
                    else:
                        table[key] = False if kind == "feature" else before
                    config.write_text(original, encoding="utf-8")
                    change = {"path": str(config), "project_path": project, "before_state": before,
                              "after_state": "true" if kind == "feature" else "trusted"}
                    restore = (uninstall_cleanup._codex_hook_feature_item_from_change if kind == "feature"
                               else uninstall_cleanup._codex_project_trust_item_from_change)
                    with patch.dict(os.environ, installer_env(temporary), clear=True):
                        result = restore(change, apply_changes=True)
                    self.assertIn(result["action"].split("-")[0], {"restored", "removed"})
                    restored = config.read_text(encoding="utf-8")
                    self.assertEqual(tomllib.loads(restored), expected)
                    self.assertIn(note, restored)
                    self.assertIn("# managed key note", restored)

    def test_deprecated_feature_removal_uses_the_whole_assignment_span(self):
        note = 'note = """\ncodex_hooks = true\n""" # note comment\n'
        content = '[features]\n' + note + "'codex_hooks' = [\n true,\n] # legacy flag\nhooks = true\n[user]\nname = 'retained'\n"
        expected = tomllib.loads(content)
        del expected["features"]["codex_hooks"]
        updated, changed = install_hooks._merge_codex_hook_feature_flag(content)
        self.assertTrue(changed)
        self.assertEqual(tomllib.loads(updated), expected)
        self.assertIn(note, updated)
        self.assertIn("# legacy flag", updated)

    def test_feature_install_and_restore_preserve_commented_table_boundaries(self):
        for header in ("[features]", "[features] # feature flags", '[ "features" ] # quoted'):
            for before in ("missing", "false"):
                with self.subTest(header=header, before=before):
                    original = (
                        f'{header}\n# retain feature note\nweb_search = true\n'
                        + ('hooks = false\n' if before == "false" else '')
                        + '[[profiles]] # adjacent array table\nname = "personal"\nhooks = false\n'
                    )
                    installed, changed = install_hooks._merge_codex_hook_feature_flag(original)
                    self.assertTrue(changed)
                    self.assertTrue(tomllib.loads(installed)["features"]["hooks"])
                    self.assertEqual(install_hooks._merge_codex_hook_feature_flag(installed), (installed, False))
                    restored, changed = uninstall_cleanup._restore_codex_hooks_feature_content(installed, before)
                    self.assertTrue(changed)
                    self.assertEqual(tomllib.loads(restored), tomllib.loads(original))
                    self.assertIn(header + "\n# retain feature note\nweb_search = true\n", restored)
                    self.assertTrue(restored.endswith('[[profiles]] # adjacent array table\nname = "personal"\nhooks = false\n'))

    def test_project_install_and_restore_match_quoted_paths_and_preserve_other_tables(self):
        project = '/work/space # bracket] "quote" \\project'
        for header in (f"[projects.{json.dumps(project)}]", f"[ projects . '{project}' ] # project note"):
            for before in ("missing", "untrusted"):
                with self.subTest(header=header, before=before):
                    original = (
                        f'{header}\n# retain project note\ncustom = "keep"\n'
                        + ('trust_level = "untrusted"\n' if before == "untrusted" else '')
                        + '[projects.other] # adjacent project\ntrust_level = "untrusted"\n'
                    )
                    installed, changed, recorded = install_hooks._merge_codex_project_trust(original, project)
                    self.assertTrue(changed)
                    self.assertEqual(recorded, before)
                    self.assertEqual(tomllib.loads(installed)["projects"][project]["trust_level"], "trusted")
                    self.assertEqual(install_hooks._merge_codex_project_trust(installed, project), (installed, False, "trusted"))
                    restored, changed = uninstall_cleanup._restore_codex_project_trust_content(
                        installed, project_path=project, before_state=before)
                    self.assertTrue(changed)
                    self.assertEqual(tomllib.loads(restored), tomllib.loads(original))
                    self.assertIn(header + '\n# retain project note\ncustom = "keep"\n', restored)
                    self.assertTrue(restored.endswith('[projects.other] # adjacent project\ntrust_level = "untrusted"\n'))

    def test_header_text_inside_multiline_string_is_not_a_table(self):
        content = 'note = """\n[features]\nhooks = false\n"""\n[features] # actual table\nweb_search = true\n'
        installed, changed = install_hooks._merge_codex_hook_feature_flag(content)
        self.assertTrue(changed)
        parsed = tomllib.loads(installed)
        self.assertEqual(parsed["note"], tomllib.loads(content)["note"])
        self.assertEqual(parsed["features"], {"web_search": True, "hooks": True})

    def test_confirm_restores_file_and_dry_run_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / ".codex"
            codex_home.mkdir()
            config = codex_home / "config.toml"
            project = "/work/user # project]"
            original = (
                '[features] # flags\nhooks = true\nweb_search = false\n'
                f"[projects.'{project}'] # trusted project\ntrust_level = \"trusted\"\ncustom = 7\n"
                '[user] # adjacent\nname = "retained"\n'
            )
            feature = {"path": str(config), "before_state": "false", "after_state": "true"}
            trust = {"path": str(config), "project_path": project, "before_state": "untrusted", "after_state": "trusted"}
            with patch.dict(os.environ, {"HOME": temporary, "CODEX_HOME": str(codex_home)}):
                config.write_text(original, encoding="utf-8")
                for change, restore in ((feature, uninstall_cleanup._codex_hook_feature_item_from_change),
                                        (trust, uninstall_cleanup._codex_project_trust_item_from_change)):
                    before = config.read_bytes()
                    self.assertTrue(restore(change, apply_changes=False)["action"].startswith("would-restore"))
                    self.assertEqual(config.read_bytes(), before)
                    self.assertTrue(restore(change, apply_changes=True)["action"].startswith("restored"))
                    tomllib.loads(config.read_text(encoding="utf-8"))
                restored = config.read_text(encoding="utf-8")
                parsed = tomllib.loads(restored)
                self.assertEqual(parsed["features"], {"hooks": False, "web_search": False})
                self.assertEqual(parsed["projects"][project], {"trust_level": "untrusted", "custom": 7})
                self.assertEqual(parsed["user"], {"name": "retained"})
                for header in ("[features] # flags", f"[projects.'{project}'] # trusted project", "[user] # adjacent"):
                    self.assertIn(header, restored)

    def test_restore_missing_project_removes_only_its_empty_table(self):
        content = "[projects.'/work/project'] # managed\ntrust_level = 'trusted'\n[projects.other] # keep\ntrust_level = 'untrusted'\n"
        restored, changed = uninstall_cleanup._restore_codex_project_trust_content(
            content, project_path="/work/project", before_state="missing")
        self.assertTrue(changed)
        self.assertEqual(restored, "[projects.other] # keep\ntrust_level = 'untrusted'\n")

    def test_trust_path_variant_pruning_preserves_adjacent_commented_table(self):
        key = "/work/hooks.json:stop:0:0"
        old_key = key.replace("/", "\\")
        content = f"[hooks.state.'{old_key}'] # old path\ntrusted_hash = 'old'\n[user] # keep\nname = 'retained'\n"
        merged, changed = install_hooks._merge_codex_hook_trust_state(content, {key: "new"})
        self.assertTrue(changed)
        self.assertEqual(tomllib.loads(merged), {"hooks": {"state": {key: {"trusted_hash": "new"}}}, "user": {"name": "retained"}})
        self.assertIn("[user] # keep\nname = 'retained'\n", merged)

    def test_trust_reconciliation_moves_commented_header_with_handler(self):
        settings_file = Path("/work/hooks.json")
        old = "/work/hooks.json:stop:1:0"
        new = "/work/hooks.json:stop:0:0"
        content = f"[hooks.state.'{old}'] # retained trust note\ntrusted_hash = 'stale'\n[user] # keep\nname = 'retained'\n"
        settings = {"hooks": {"Stop": [{"hooks": [{"command": "echo retained"}]}]}}
        reconciled = install_hooks._reconcile_codex_hook_trust(content, settings_file, settings, {old: new})
        self.assertEqual(tomllib.loads(reconciled), {"hooks": {"state": {new: {"trusted_hash": "stale"}}}, "user": {"name": "retained"}})
        self.assertIn(" # retained trust note\ntrusted_hash = 'stale'\n", reconciled)
        self.assertTrue(reconciled.endswith("[user] # keep\nname = 'retained'\n"))


if __name__ == "__main__":
    unittest.main()
