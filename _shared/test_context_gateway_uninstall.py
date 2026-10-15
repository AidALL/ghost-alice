"""Gateway detachment precedes every dependent Core cleanup mutation."""
import argparse
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import context_gateway_install as gateway
import uninstall_cleanup as cleanup


class GatewayUninstallTests(unittest.TestCase):
    def setUp(self):
        from test_context_gateway_install import ContextGatewayInstallTests
        fixture = ContextGatewayInstallTests()
        self.addCleanup(fixture.doCleanups)
        self.args, _ = fixture.installation_fixture()
        fixture.mocked_install(self.args)
        self.root = self.args[0].parent
        self.active = True
        self.stops = 0
        self.targets = []
        self.patchers = [
            patch.object(cleanup, '_user_home', return_value=self.root),
            patch.object(cleanup, '_codex_home', return_value=self.args[2]),
            patch.object(gateway.sys, 'platform', 'darwin'),
            patch.object(gateway, '_service_state', side_effect=lambda _: 'running' if self.active else 'absent'),
            patch.object(gateway, '_stop_service', side_effect=self.stop),
            patch.object(gateway, '_start_service', side_effect=lambda *_: setattr(self, 'active', True)),
            patch.object(gateway, '_ready', return_value=True),
        ]
        for patcher in self.patchers:
            patcher.start(); self.addCleanup(patcher.stop)

    def stop(self, _domain):
        self.active = False
        self.stops += 1

    def report(self, *, platform='codex', confirm=True, target=None):
        manifest = self.root / '.ghost-alice/install-state' / (platform + '.json')
        manifest.parent.mkdir(parents=True, exist_ok=True)
        target = self.args[1][-1] if target is None else target
        manifest.write_text(json.dumps(dict(platform=platform, targets=[dict(target_name='_shared', dest_path=str(target))])))
        args = argparse.Namespace(platform=platform, confirm=confirm, install_state_manifest=manifest)
        def remove(*_args, **_kwargs):
            if not _kwargs.get('confirm'):
                return dict(action='would-remove', path=str(target))
            self.targets.append(dict(active=self.active, config=(self.args[2] / 'config.toml').read_text()))
            return dict(action='unchanged')
        with patch.object(cleanup, '_hook_item', return_value=dict(action='unchanged')) as hooks, \
             patch.object(cleanup, '_global_rule_items', return_value=[]), \
             patch.object(cleanup, '_target_item', side_effect=remove), \
             patch.object(cleanup, '_support_artifact_items', return_value=[]):
            result = cleanup.build_report(args)
        return result, hooks.call_count

    def test_custom_gateway_detaches_before_core_targets_and_preserves_adjacent_edits(self):
        config = self.args[2] / 'config.toml'
        config.write_text(config.read_text() + '\n[features]\nhooks=true\n')
        result, _ = self.report()
        self.assertFalse(self.targets[0]['active'])
        self.assertNotIn('model_provider = "ghost_alice_context"', self.targets[0]['config'])
        self.assertIn('[features]\nhooks=true', config.read_text())
        self.assertFalse(self.args[5].exists())
        self.assertFalse((self.root / '.ghost-alice/context-gateway-binding.json').exists())
        self.assertTrue((self.args[4] / 'installation.json').is_file())
        self.assertTrue(any(item.get('kind') == 'context-gateway' for item in result['items']))

    def test_uncertain_service_stop_aborts_all_dependent_core_mutations(self):
        with patch.object(gateway, '_stop_service', side_effect=RuntimeError('stop unconfirmed')):
            result, hooks = self.report()
        self.assertEqual(self.targets, [])
        self.assertEqual(hooks, 0)
        self.assertEqual(result['items'][0]['action'], 'manual-review')
        self.assertTrue(self.args[5].exists())
        self.assertIn('ghost_alice_context', (self.args[2] / 'config.toml').read_text())

    def test_stop_after_actual_bootout_is_reported_uncertain_without_file_deletion(self):
        def uncertain(domain):
            self.stop(domain)
            raise RuntimeError('native absence check failed')
        with patch.object(gateway, '_stop_service', side_effect=uncertain):
            result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertIn('stop/absence is unconfirmed', result['items'][0]['detail'])
        self.assertTrue(self.args[5].exists())
        self.assertIn('ghost_alice_context', (self.args[2] / 'config.toml').read_text())

    def test_failed_second_absence_observation_restores_certified_stopped_service(self):
        with patch.object(gateway, '_service_state', side_effect=['running', RuntimeError('observation failed')]):
            result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertTrue(self.active)
        self.assertIn('prior configuration/service retained', result['items'][0]['detail'])

    def test_user_modified_provider_blocks_are_preserved_and_block_removal(self):
        config = self.args[2] / 'config.toml'
        changed = config.read_text().replace('wire_api = "responses"', 'wire_api = "user-choice"')
        config.write_text(changed)
        result, hooks = self.report()
        self.assertEqual(config.read_text(), changed)
        self.assertEqual(self.stops, 0)
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertEqual(result['items'][0]['action'], 'manual-review')

    def test_independent_claude_platform_removal_preserves_codex_gateway(self):
        state = self.root / '.ghost-alice/install-state/codex.json'
        state.parent.mkdir(parents=True, exist_ok=True); state.write_text('{}')
        self.report(platform='claude', target=self.root / 'independent-claude-shared')
        self.assertTrue(self.active)
        self.assertEqual(self.stops, 0)
        self.assertTrue(self.args[5].exists())

    def test_dry_run_keeps_provider_service_and_discovery_record(self):
        self.report(confirm=False)
        self.assertEqual(self.stops, 0)
        self.assertTrue(self.args[5].exists())
        self.assertIn('ghost_alice_context', (self.args[2] / 'config.toml').read_text())

    def test_failed_config_write_restores_service_and_blocks_core_cleanup(self):
        config = self.args[2] / 'config.toml'
        original = config.read_bytes()
        with patch.object(gateway, '_write', side_effect=OSError('config is unwritable')):
            result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertEqual(config.read_bytes(), original)
        self.assertTrue(self.active, 'the unchanged provider must retain its running service')
        self.assertTrue(self.args[5].exists())
        self.assertEqual(result['items'][0]['action'], 'manual-review')

    def test_claude_removal_of_actual_gateway_runtime_detaches_first(self):
        state = self.root / '.ghost-alice/install-state/codex.json'
        state.parent.mkdir(parents=True, exist_ok=True); state.write_text('{}')
        self.report(platform='claude')
        self.assertEqual(self.stops, 1)
        self.assertFalse(self.targets[0]['active'])

    def test_missing_binding_with_active_provider_blocks_core_cleanup(self):
        (self.root / '.ghost-alice/context-gateway-binding.json').unlink()
        result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertIn('discovery binding is missing', result['items'][0]['detail'])
        self.assertEqual(self.stops, 0)

    def test_changed_launch_agent_is_preserved_before_any_detachment(self):
        self.args[5].write_bytes(b'user changed launch agent')
        result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertEqual(self.stops, 0)
        self.assertEqual(self.args[5].read_bytes(), b'user changed launch agent')
        self.assertEqual(result['items'][0]['action'], 'manual-review')

    def test_symlinked_codex_config_is_rejected_before_detach_side_effects(self):
        self._assert_symlink_config_rejected(dangling=False)

    def test_dangling_codex_config_is_rejected_before_detach_side_effects(self):
        self._assert_symlink_config_rejected(dangling=True)

    def _assert_symlink_config_rejected(self, *, dangling):
        config = self.args[2] / 'config.toml'
        target = self.args[2] / 'user-managed.toml'
        config.rename(target)
        if dangling:
            target.unlink()
        try:
            config.symlink_to(target.name)
        except OSError as exc:
            self.skipTest(f'symlink creation unavailable: {exc}')
        link = config.lstat()
        link_before = (config.readlink(), link.st_ino, link.st_mode, link.st_mtime_ns)
        target_before = ((target.read_bytes(), target.stat().st_ino, target.stat().st_mode,
                          target.stat().st_mtime_ns) if target.exists() else None)
        artifacts = [self.args[5], self.args[4] / 'state.json', self.args[4] / 'installation.json',
                     self.root / '.ghost-alice/context-gateway-binding.json']
        originals = {path: path.read_bytes() for path in artifacts}
        with patch.object(gateway, '_service_state', return_value='absent') as service, \
             patch.object(gateway, '_write', wraps=gateway._write) as write:
            with self.assertRaisesRegex(ValueError, '[Ss]ymlinked Codex config'):
                gateway.detach_for_uninstall(user_home=self.root, remove_codex=True,
                    removing_paths=self.args[1], confirm=True, codex_dir=self.args[2])
        service.assert_not_called()
        write.assert_not_called()
        self.assertEqual(self.stops, 0)
        self.assertTrue(self.active)
        link = config.lstat()
        self.assertEqual((config.readlink(), link.st_ino, link.st_mode, link.st_mtime_ns), link_before)
        self.assertEqual(((target.read_bytes(), target.stat().st_ino, target.stat().st_mode,
                           target.stat().st_mtime_ns) if target.exists() else None), target_before)
        self.assertEqual({path: path.read_bytes() for path in artifacts}, originals)

    def test_unregistered_service_is_scoped_to_its_actual_runtime(self):
        import subprocess
        (self.root / '.ghost-alice/context-gateway-binding.json').unlink()
        observed = subprocess.CompletedProcess([], 0, f'path = {self.args[5]}\n'.encode(), b'')
        with patch.object(cleanup, '_codex_home', return_value=self.root / 'unrelated-codex'), \
             patch.object(gateway.subprocess, 'run', return_value=observed):
            self.report(platform='claude', target=self.root / 'unrelated-runtime')
            self.assertEqual(len(self.targets), 1)
            self.targets.clear()
            result, hooks = self.report(platform='claude')
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertEqual(result['items'][0]['action'], 'manual-review')
        self.assertEqual(self.stops, 0)

    def test_unregistered_service_discovery_requires_confirmed_absence_or_dependency_proof(self):
        import subprocess
        (self.root / '.ghost-alice/context-gateway-binding.json').unlink()
        for code, output in ((1, b'permission denied'), (0, b''), (0, b'changed native output'),
                             (0, b'path = relative.plist\n')):
            with self.subTest(code=code, output=output):
                self.targets.clear()
                observed = subprocess.CompletedProcess([], code, output, b'')
                with patch.object(cleanup, '_codex_home', return_value=self.root / 'unrelated-codex'), \
                     patch.object(gateway.subprocess, 'run', return_value=observed):
                    result, hooks = self.report(platform='claude')
                self.assertEqual((self.targets, hooks, self.stops), ([], 0, 0))
                self.assertEqual(result['items'][0]['action'], 'manual-review')
        self.targets.clear()
        absent = subprocess.CompletedProcess([], 113, b'', b'')
        with patch.object(cleanup, '_codex_home', return_value=self.root / 'unrelated-codex'), \
             patch.object(gateway.subprocess, 'run', return_value=absent):
            self.report(platform='claude')
        self.assertEqual(len(self.targets), 1)

    def test_unregistered_dotfile_config_link_uses_provider_content_and_native_discovery(self):
        import subprocess
        (self.root / '.ghost-alice/context-gateway-binding.json').unlink()
        self.args[5].unlink()
        config = self.args[2] / 'config.toml'
        config.unlink()
        target = self.root / 'dotfiles/config.toml'
        target.parent.mkdir()
        try:
            config.symlink_to(target)
        except OSError as exc:
            self.skipTest(f'symlink creation unavailable: {exc}')
        for text, code, blocked in [('model="native"\n', 77, True),
                                    ('model="native"\n', 113, False),
                                    ('model_provider="ghost_alice_context"\n', 113, True)]:
            with self.subTest(text=text, native_exit=code):
                target.write_text(text)
                self.targets.clear()
                observed = subprocess.CompletedProcess([], code, b'', b'')
                with patch.object(gateway.subprocess, 'run', return_value=observed) as native:
                    result, hooks = self.report()
                self.assertEqual(bool(result.get('blocked')), blocked, result)
                self.assertEqual(hooks, 0 if blocked else 1)
                self.assertEqual(native.call_count, 0 if 'ghost_alice_context' in text else 1)
                self.assertTrue(config.is_symlink())
                self.assertEqual(target.read_text(), text)

    def test_unregistered_service_ledger_dependency_and_unknown_arguments_block_cleanup(self):
        import subprocess
        import plistlib
        (self.root / '.ghost-alice/context-gateway-binding.json').unlink()
        observed = subprocess.CompletedProcess([], 0, f'path = {self.args[5]}\n'.encode(), b'')
        original = self.args[5].read_bytes()
        for malformed in (False, True):
            with self.subTest(malformed=malformed):
                self.targets.clear()
                plist = plistlib.loads(original)
                if malformed:
                    plist['ProgramArguments'] = []
                self.args[5].write_bytes(plistlib.dumps(plist))
                target = self.root / 'unrelated-runtime' if malformed else self.args[3]
                with patch.object(cleanup, '_codex_home', return_value=self.root / 'unrelated-codex'), \
                     patch.object(gateway.subprocess, 'run', return_value=observed):
                    result, hooks = self.report(platform='claude', target=target)
                self.assertEqual((self.targets, hooks, self.stops), ([], 0, 0))
                self.assertEqual(result['items'][0]['action'], 'manual-review')

    def test_linked_service_state_and_ownership_files_block_affected_cleanup(self):
        for name in ('state.json', 'installation.json', 'registration', 'agent'):
            with self.subTest(name=name):
                target = (self.root / '.ghost-alice/context-gateway-binding.json' if name == 'registration'
                          else self.args[5] if name == 'agent' else self.args[4] / name)
                raw = target.read_bytes()
                destination = self.root / ('linked-user-' + name)
                destination.write_bytes(raw)
                target.unlink(); target.symlink_to(destination)
                before = target.lstat()
                result, hooks = self.report()
                self.assertEqual((self.targets, hooks, self.stops), ([], 0, 0))
                self.assertEqual(result['items'][0]['action'], 'manual-review')
                self.assertIn('Symlinked', result['items'][0]['detail'])
                after = target.lstat()
                self.assertEqual((target.readlink(), after.st_ino, after.st_mode, after.st_mtime_ns),
                                 (destination, before.st_ino, before.st_mode, before.st_mtime_ns))
                self.assertEqual(destination.read_bytes(), raw)
                target.unlink(); target.write_bytes(raw)

    def test_existing_nested_recovery_blocks_real_runtime_sweep(self):
        import shutil
        from test_context_gateway_install import ContextGatewayInstallTests
        nested = self.root / '.ghost-alice/runtime/gateway-state'
        shutil.copytree(self.args[4], nested)
        (nested / 'usage.jsonl').write_text('original usage evidence')
        # Retarget the authenticated historical fixture to its unsafe location.
        # Current installation must not adopt orphaned state to create it.
        receipt_path = nested / 'installation.json'
        receipt = json.loads(receipt_path.read_bytes())
        receipt['state_path'] = str(nested / 'state.json')
        receipt_path.write_text(json.dumps(receipt))
        registration = self.root / '.ghost-alice/context-gateway-binding.json'
        binding = json.loads(registration.read_bytes())
        binding.update(installation_path=str(receipt_path), installation_digest=gateway._digest(receipt_path.read_bytes()))
        registration.write_text(json.dumps(binding))
        config = self.args[2] / 'config.toml'
        config.write_text(gateway.remove_provider(config.read_text()))
        fixture = ContextGatewayInstallTests(); self.addCleanup(fixture.doCleanups)
        # Represent a previously supported unsafe placement, then exercise the
        # current remover with its actual support-artifact runtime sweep.
        with patch.object(gateway.planning, 'affected', return_value=False):
            fixture.mocked_install((*self.args[:4], nested, self.args[5]))
        manifest = self.root / '.ghost-alice/install-state/codex.json'
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"platform":"codex","targets":[]}')
        args = argparse.Namespace(platform='codex', confirm=True, install_state_manifest=manifest)
        with patch.object(cleanup, '_hook_item') as hooks:
            result = cleanup.build_report(args)
        self.assertTrue(result['blocked'])
        hooks.assert_not_called()
        self.assertEqual((nested / 'usage.jsonl').read_text(), 'original usage evidence')
        self.assertTrue((nested / 'installation.json').is_file())
        self.assertEqual(self.stops, 0)

    def test_shared_target_preserved_for_codex_does_not_detach_its_gateway(self):
        from test_context_gateway_install import ContextGatewayInstallTests
        target = self.root / '.claude/skills/shared-core'
        runtime = target / '_shared'
        runtime.mkdir(parents=True)
        (runtime / 'completion_assessment.py').write_text('installed Core')
        self.args[1][:] = [runtime]
        ContextGatewayInstallTests().mocked_install(self.args)
        manifests = self.root / '.ghost-alice/install-state'
        manifests.mkdir(parents=True, exist_ok=True)
        body = dict(targets=[dict(target_name='_shared', dest_path=str(target))])
        for platform in ('codex', 'claude'):
            (manifests / (platform + '.json')).write_text(json.dumps(dict(body, platform=platform)))
        args = argparse.Namespace(platform='claude', confirm=True, install_state_manifest=manifests / 'claude.json')
        with patch.object(cleanup, '_claude_home', return_value=self.root / '.claude'), \
             patch.object(cleanup, '_hook_item', return_value=dict(action='unchanged')), \
             patch.object(cleanup, '_global_rule_items', return_value=[]), \
             patch.object(cleanup, '_support_artifact_items', return_value=[]):
            result = cleanup.build_report(args)
        self.assertEqual(self.stops, 0)
        self.assertTrue(self.active)
        self.assertTrue(runtime.exists())
        self.assertTrue(any(item.get('reason') == 'shared-with-other-platform' for item in result['items']))


if __name__ == '__main__':
    unittest.main()
