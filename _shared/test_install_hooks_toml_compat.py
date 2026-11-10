"""TOML edits preserve valid dotted and inline settings and report invalid input."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import tomllib
import install_hooks
import uninstall_cleanup
from installer_testing import installer_env


class TomlCompatibilityTest(unittest.TestCase):
    def test_dotted_and_inline_feature_tables_remain_editable(self):
        for original in ('features.hooks = false\nfeatures.other = true\n',
                         'features.other = true\nfeatures.hooks = false\n',
                         'features = { hooks = false, other = true } # keep\n'):
            with self.subTest(original=original):
                content, changed = install_hooks._merge_codex_hook_feature_flag(original)
                self.assertTrue(changed)
                self.assertEqual(tomllib.loads(content)['features'], {'hooks': True, 'other': True})
                restored, changed = uninstall_cleanup._restore_codex_hooks_feature_content(content, 'missing')
                self.assertTrue(changed)
                self.assertEqual(tomllib.loads(restored)['features'], {'other': True})

    def test_project_inline_table_preserves_other_settings(self):
        original = '[projects]\n"/tmp/project" = { trust_level = "untrusted", other = 5 }\n'
        content, changed, before = install_hooks._merge_codex_project_trust(original, '/tmp/project')
        self.assertTrue(changed)
        self.assertEqual(before, 'untrusted')
        self.assertEqual(tomllib.loads(content)['projects']['/tmp/project'], {'trust_level': 'trusted', 'other': 5})

    def test_inline_values_preserve_dates_arrays_and_quoted_names(self):
        original = ('features = { hooks = false, date = 2026-11-10, ratio = inf, '
                    '"other.name" = [1, { note = "keep" }] } # retained\n')
        expected = tomllib.loads(original)
        expected['features']['hooks'] = True
        installed, changed = install_hooks._merge_codex_hook_feature_flag(original)
        self.assertTrue(changed)
        self.assertEqual(tomllib.loads(installed), expected)
        self.assertIn('# retained', installed)

    def test_invalid_config_is_reported_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / '.codex/config.toml'
            config.parent.mkdir()
            config.write_text('[features\nhooks = true\n')
            before = config.read_bytes()
            with mock.patch.dict(os.environ, installer_env(temporary), clear=True):
                item = uninstall_cleanup._codex_hook_feature_item_from_change(
                    dict(path=str(config), before_state='false', after_state='true'), apply_changes=True)
            self.assertEqual(item['action'], 'manual-review')
            self.assertEqual(item['reason'], 'codex-config-edit-failed')
            self.assertEqual(config.read_bytes(), before)
