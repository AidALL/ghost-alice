"""Removal must retain positional trust and active user/Core configuration."""
import builtins
import copy
import errno
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest import mock

import install_destination
import install_hooks
import runtime_config


class CodexHookRemovalTrustTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.native = self.root / 'native'
        self.native.mkdir()
        binding = dict(mode='isolated', owned_root=str(self.root),
            platform_dir=str(self.native), skills_dir=str(self.root / 'skills'),
            ghost_root=str(self.root / 'ghost'), ledger_root=str(self.root / 'ledger'),
            project_root=str(self.root / 'project'), core_source_root=str(Path(__file__).parents[1]),
            runtime_shared_dir=str(self.root / 'ghost/runtime/current/_shared'))
        context = install_destination.use(binding)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.hooks = self.native / 'hooks.json'
        self.config = self.native / 'config.toml'

    def addon(self, name):
        marker = f'[addon:demo] {name}'
        runner = install_hooks._managed_addon_runner_id('demo', name)
        script = self.root / 'skills/demo/hooks' / f'{name}.py'
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text('# isolated addon hook fixture\n')
        entry = install_hooks._hook_runner_command_entry(runner, install_hooks._hook_python_command(script, payload=True), marker, platform_key='codex')
        return marker, entry

    def seed(self, groups):
        settings = {'hooks': {'Stop': groups}, 'user': 'preserve'}
        self.hooks.write_text(json.dumps(settings))
        commands = {('Stop', h['command']) for g in groups for h in g['hooks'] if h.get('type') == 'command'}
        entries = install_hooks._codex_trusted_hook_state_entries(self.hooks, settings, verified_addon_commands=commands)
        content = install_hooks._merge_codex_hook_trust_state('[user]\nkeep = "precious"\n', entries)[0]
        self.config.write_text(content)
        self.config.chmod(0o600)
        return settings, entries

    def key(self, group, handler=0):
        return install_hooks._codex_hook_state_key(self.hooks, 'Stop', group, handler)

    def state(self):
        return tomllib.loads(self.config.read_text()).get('hooks', {}).get('state', {})

    def test_sequential_addon_hook_removal_leaves_no_original_trust_rows(self):
        first, a = self.addon('first')
        second, b = self.addon('second')
        self.seed([a, b])
        self.assertEqual(install_hooks.remove_addon_hook(first, platform_key='codex'), 1)
        self.assertEqual(install_hooks.remove_addon_hook(second, platform_key='codex'), 1)
        self.assertEqual(json.loads(self.hooks.read_text())['hooks']['Stop'], [])
        self.assertEqual(self.state(), {})
        self.assertEqual(tomllib.loads(self.config.read_text())['user'], {'keep': 'precious'})

    def test_addon_hook_before_core_relocates_whole_trust_table_preserving_user_metadata(self):
        marker, addon = self.addon('continue')
        core = install_hooks._hook_runner_command_entry('completion', 'echo core', '[completion-reminder] AGENTS.md', platform_key='codex')
        _, entries = self.seed([addon, core])
        self.config.write_text(self.config.read_text() + '# keep this explanation\nuser_note = "preserve"\n')
        install_hooks.remove_addon_hook(marker, platform_key='codex')
        self.assertEqual(self.state(), {self.key(0): {'trusted_hash': entries[self.key(1)], 'user_note': 'preserve'}})
        self.assertIn('# keep this explanation\nuser_note = "preserve"', self.config.read_text())
        self.assertEqual(json.loads(self.hooks.read_text())['hooks']['Stop'], [core])

    def test_same_group_handler_indices_and_duplicate_survivors_keep_occurrence_metadata(self):
        marker, addon = self.addon('continue')
        user = {'type': 'command', 'command': 'echo user'}
        group = {'matcher': '', 'hooks': [addon['hooks'][0], copy.deepcopy(user), copy.deepcopy(user)]}
        _, entries = self.seed([group])
        lines = self.config.read_text().splitlines(keepends=True)
        for old_index, label in ((2, 'second'), (1, 'first')):
            header = f'[hooks.state.{install_hooks._toml_basic_string(self.key(0, old_index))}]\n'
            pos = lines.index(header)
            lines.insert(pos + 2, f'user_note = "{label}"\n')
        self.config.write_text(''.join(lines))
        install_hooks.remove_addon_hook(marker, platform_key='codex')
        self.assertEqual(self.state(), {
            self.key(0, 0): {'trusted_hash': entries[self.key(0, 1)], 'user_note': 'first'},
            self.key(0, 1): {'trusted_hash': entries[self.key(0, 2)], 'user_note': 'second'}})
        self.assertEqual(json.loads(self.hooks.read_text())['hooks']['Stop'][0]['hooks'], [user, user])

    def test_removed_handler_table_goes_with_its_metadata(self):
        # The note annotates the removed addon hook's position; the survivor's table moves into it.
        marker, addon = self.addon('continue')
        user = {'hooks': [{'type': 'command', 'command': 'echo user'}]}
        _, entries = self.seed([addon, user])
        content = self.config.read_text()
        header = f'[hooks.state.{install_hooks._toml_basic_string(self.key(0))}]\n'
        self.config.write_text(content.replace(header, header + 'user_note = "precious"\n'))
        self.assertEqual(install_hooks.remove_addon_hook(marker, platform_key='codex'), 1)
        self.assertEqual(self.state(), {self.key(0): {'trusted_hash': entries[self.key(1)]}})
        self.assertEqual(json.loads(self.hooks.read_text())['hooks']['Stop'], [user])

    def test_survivor_table_moves_without_granting_trust(self):
        # Moving a table grants no trust: a changed hash still asks the user.
        marker, addon = self.addon('continue')
        user = {'hooks': [{'type': 'command', 'command': 'echo user'}]}
        _, entries = self.seed([addon, user])
        self.config.write_text(self.config.read_text().replace(entries[self.key(1)], 'user-changed-hash'))
        self.assertEqual(install_hooks.remove_addon_hook(marker, platform_key='codex'), 1)
        self.assertEqual(self.state(), {self.key(0): {'trusted_hash': 'user-changed-hash'}})
        self.assertEqual(json.loads(self.hooks.read_text())['hooks']['Stop'], [user])

    def test_removal_drops_orphan_tables_of_this_hooks_file_only(self):
        marker, addon = self.addon('continue')
        self.seed([addon])
        orphan = f'[hooks.state.{install_hooks._toml_basic_string(self.key(9))}]\ntrusted_hash = "orphan"\nenabled = true\n'
        other = '[hooks.state."/elsewhere/hooks.json:stop:0:0"]\ntrusted_hash = "other-file"\n'
        self.config.write_text(self.config.read_text() + orphan + other)
        install_hooks.remove_addon_hook(marker, platform_key='codex')
        self.assertEqual(self.state(), {'/elsewhere/hooks.json:stop:0:0': {'trusted_hash': 'other-file'}})
        self.assertEqual(tomllib.loads(self.config.read_text())['user'], {'keep': 'precious'})

    def test_partial_config_write_failure_preserves_active_config_and_hooks(self):
        marker, addon = self.addon('continue')
        self.seed([addon])
        before = self.config.read_bytes(), self.hooks.read_bytes(), self.config.stat().st_mtime_ns
        original_open, original_fdopen = builtins.open, os.fdopen
        class PartialWriter:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): self.handle.close()
            def write(self, value):
                self.handle.write(value[:max(1, len(value)//2)])
                self.handle.flush()
                raise OSError(errno.ENOSPC, 'injected partial write')
        def opened(path, mode='r', *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            return PartialWriter(handle) if Path(path) == self.config and mode == 'w' else handle
        def fdopened(descriptor, mode='r', *args, **kwargs):
            return PartialWriter(original_fdopen(descriptor, mode, *args, **kwargs))
        with mock.patch('builtins.open', opened), mock.patch.object(runtime_config.os, 'fdopen', fdopened):
            with self.assertRaises(OSError):
                install_hooks.remove_addon_hook(marker, platform_key='codex')
        self.assertEqual((self.config.read_bytes(), self.hooks.read_bytes(), self.config.stat().st_mtime_ns), before)
        self.assertEqual(list(self.native.glob('.*.tmp')), [])

    def test_settings_write_failure_restores_config_bytes_mode_and_original_mtime(self):
        marker, addon = self.addon('continue')
        self.seed([addon])
        before = self.config.read_bytes(), self.hooks.read_bytes(), self.config.stat().st_mtime_ns
        with mock.patch.object(install_hooks, '_write_settings', side_effect=OSError('hooks-write-failure')):
            with self.assertRaises(OSError):
                install_hooks.remove_addon_hook(marker, platform_key='codex')
        self.assertEqual((self.config.read_bytes(), self.hooks.read_bytes(), self.config.stat().st_mtime_ns), before)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_config_symlink_preserved_and_target_updated_atomically(self):
        marker, addon = self.addon('continue')
        self.seed([addon])
        target = self.root / 'user-config.toml'
        self.config.rename(target)
        self.config.symlink_to(target)
        link = self.config.lstat()
        install_hooks.remove_addon_hook(marker, platform_key='codex')
        self.assertTrue(self.config.is_symlink())
        self.assertEqual((self.config.lstat().st_ino, self.config.lstat().st_mtime_ns), (link.st_ino, link.st_mtime_ns))
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state(), {})

    def test_dry_run_leaves_trust_and_settings_unchanged(self):
        marker, addon = self.addon('continue')
        self.seed([addon])
        before = self.config.read_bytes(), self.hooks.read_bytes()
        self.assertEqual(install_hooks.remove_addon_hook(marker, platform_key='codex', dry_run=True), 1)
        self.assertEqual((self.config.read_bytes(), self.hooks.read_bytes()), before)


if __name__ == '__main__':
    unittest.main()
