"""Gateway detachment precedes every dependent Core cleanup mutation.

The full uninstall asks the gateway participant's judgment with the removal roots it computed: a registered
service whose runtime (or a service file) is among them, or a Codex full removal, is detached first; a
removal that leaves the service's code alone proceeds without touching it. Without a `service` section the
judgment asks launchd and holds only for a loaded service whose program is inside a removal root. The
`detach` command unregisters the service the record names.
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_gateway_install as gateway  # noqa: E402
import gateway_testing  # noqa: E402
import installed_payload  # noqa: E402
import uninstall_cleanup as cleanup  # noqa: E402
from context_gateway_model import GatewayBlocked, LABEL  # noqa: E402
from hook_definitions import PROVIDER  # noqa: E402


class RegisteredHomeCase(unittest.TestCase):
    """A home whose install record names a registered gateway; the native service is a flag."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        home = patch.object(gateway.Path, 'home', return_value=self.root)
        home.start(); self.addCleanup(home.stop)
        self.reg = gateway_testing.registered_home(self, self.root)
        self.active = True
        self.stops = 0
        self.patchers = [
            patch.object(cleanup, '_user_home', return_value=self.root),
            patch.object(cleanup, '_codex_home', return_value=self.reg.codex),
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

    def unregister(self):
        """The record without its service section: the gateway is not registered in the record."""
        installed_payload.update(self.reg.ghost, {"service": None})


class GatewayUninstallTests(RegisteredHomeCase):
    def setUp(self):
        super().setUp()
        self.targets = []

    def report(self, *, platform='codex', confirm=True, target=None):
        manifest = self.root / '.ghost-alice/install-state' / (platform + '.json')
        manifest.parent.mkdir(parents=True, exist_ok=True)
        target = self.reg.shared if target is None else target
        manifest.write_text(json.dumps(dict(platform=platform, targets=[dict(target_name='_shared', dest_path=str(target))])))
        args = argparse.Namespace(platform=platform, confirm=confirm, install_state_manifest=manifest)

        def remove(*_args, **_kwargs):
            if not _kwargs.get('confirm'):
                return dict(action='would-remove', path=str(target))
            self.targets.append(dict(active=self.active, config=self.reg.config.read_text()))
            return dict(action='unchanged')

        with patch.object(cleanup, '_hook_item', return_value=dict(action='unchanged')) as hooks, \
             patch.object(cleanup, '_global_rule_items', return_value=[]), \
             patch.object(cleanup, '_target_item', side_effect=remove), \
             patch.object(cleanup, '_support_artifact_items', return_value=[]):
            result = cleanup.build_report(args)
        return result, hooks.call_count

    def test_the_codex_full_removal_detaches_before_core_targets_and_preserves_adjacent_edits(self):
        config = self.reg.config
        config.write_text(config.read_text() + '\n[features]\nhooks=true\n')
        installed_payload.update(self.reg.ghost, {"service": gateway_testing.service_section(self.reg)})
        result, _ = self.report()
        self.assertFalse(self.targets[0]['active'])
        self.assertNotIn('model_provider = "ghost_alice_context"', self.targets[0]['config'])
        self.assertIn('[features]\nhooks=true', config.read_text())
        self.assertFalse(self.reg.agent.exists())
        self.assertIsNone(self.reg.service())
        self.assertTrue(self.reg.state_path.is_file())
        self.assertTrue((self.reg.state_dir / 'usage.jsonl').is_file())
        item = next(item for item in result['items'] if item.get('kind') == 'context-gateway')
        self.assertEqual(item['action'], 'detached')

    def test_uncertain_service_stop_aborts_all_dependent_core_mutations(self):
        with patch.object(gateway, '_stop_service', side_effect=RuntimeError('stop unconfirmed')):
            result, hooks = self.report()
        self.assertEqual(self.targets, [])
        self.assertEqual(hooks, 0)
        self.assertEqual(result['items'][0]['action'], 'manual-review')
        self.assertTrue(self.reg.agent.exists())
        self.assertIn('ghost_alice_context', self.reg.config.read_text())
        self.assertIsNotNone(self.reg.service())

    def test_stop_after_actual_bootout_is_reported_uncertain_without_file_deletion(self):
        def uncertain(domain):
            self.stop(domain)
            raise RuntimeError('native absence check failed')
        with patch.object(gateway, '_stop_service', side_effect=uncertain):
            result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertIn('stop/absence is unconfirmed', result['items'][0]['detail'])
        self.assertTrue(self.reg.agent.exists())
        self.assertIn('ghost_alice_context', self.reg.config.read_text())

    def test_failed_second_absence_observation_restores_certified_stopped_service(self):
        with patch.object(gateway, '_service_state', side_effect=['running', RuntimeError('observation failed')]):
            result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertTrue(self.active)
        self.assertIn('prior configuration/service retained', result['items'][0]['detail'])
        self.assertIsNotNone(self.reg.service())

    def test_user_modified_provider_blocks_are_preserved_and_block_removal(self):
        config = self.reg.config
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
        result, _ = self.report(platform='claude', target=self.root / '.claude/skills/_shared')
        self.assertTrue(self.active)
        self.assertEqual(self.stops, 0)
        self.assertTrue(self.reg.agent.exists())
        self.assertEqual(len(self.targets), 1)
        item = next(item for item in result['items'] if item.get('kind') == 'context-gateway')
        self.assertEqual((item['action'], item['reason']), ('unchanged', 'independent-gateway-runtime'))

    def test_dry_run_keeps_provider_service_and_record(self):
        result, _ = self.report(confirm=False)
        self.assertEqual(self.stops, 0)
        self.assertTrue(self.reg.agent.exists())
        self.assertIn('ghost_alice_context', self.reg.config.read_text())
        self.assertIsNotNone(self.reg.service())
        item = next(item for item in result['items'] if item.get('kind') == 'context-gateway')
        self.assertEqual(item['action'], 'would-detach')

    def test_failed_config_write_restores_service_and_blocks_core_cleanup(self):
        config = self.reg.config
        original = config.read_bytes()
        with patch.object(gateway, '_write', side_effect=OSError('config is unwritable')):
            result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertEqual(config.read_bytes(), original)
        self.assertTrue(self.active, 'the unchanged provider must retain its running service')
        self.assertTrue(self.reg.agent.exists())
        self.assertIsNotNone(self.reg.service())
        self.assertEqual(result['items'][0]['action'], 'manual-review')

    def test_claude_removal_of_the_runtime_detaches_first(self):
        state = self.root / '.ghost-alice/install-state/codex.json'
        state.parent.mkdir(parents=True, exist_ok=True); state.write_text('{}')
        self.report(platform='claude')
        self.assertEqual(self.stops, 1)
        self.assertFalse(self.targets[0]['active'])

    def test_a_loaded_service_without_a_record_entry_holds_when_its_program_is_removed(self):
        import subprocess
        self.unregister()
        observed = subprocess.CompletedProcess([], 0, f'path = {self.reg.agent}\n'.encode(), b'')
        with patch.object(gateway.subprocess, 'run', return_value=observed):
            result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertEqual(result['items'][0]['action'], 'manual-review')
        self.assertIn('install record', result['items'][0]['detail'])
        self.assertEqual(self.stops, 0)
        self.assertTrue(result['blocked'])

    def test_changed_launch_agent_is_preserved_before_any_detachment(self):
        self.reg.agent.write_bytes(b'user changed launch agent')
        result, hooks = self.report()
        self.assertEqual((hooks, self.targets), (0, []))
        self.assertEqual(self.stops, 0)
        self.assertEqual(self.reg.agent.read_bytes(), b'user changed launch agent')
        self.assertEqual(result['items'][0]['action'], 'manual-review')
        self.assertIsNotNone(self.reg.service())

    def test_symlinked_codex_config_is_rejected_before_detach_side_effects(self):
        self._assert_symlink_config_rejected(dangling=False)

    def test_dangling_codex_config_is_rejected_before_detach_side_effects(self):
        self._assert_symlink_config_rejected(dangling=True)

    def _assert_symlink_config_rejected(self, *, dangling):
        config = self.reg.config
        target = self.reg.codex / 'user-managed.toml'
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
        artifacts = [self.reg.agent, self.reg.state_path, installed_payload.path(self.reg.ghost)]
        originals = {path: path.read_bytes() for path in artifacts}
        with patch.object(gateway, '_service_state', return_value='absent') as service, \
             patch.object(gateway, '_write', wraps=gateway._write) as write:
            with self.assertRaisesRegex(ValueError, '[Ss]ymlinked Codex config'):
                gateway.detach(confirm=True)
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
        self.unregister()
        observed = subprocess.CompletedProcess([], 0, f'path = {self.reg.agent}\n'.encode(), b'')
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
        self.unregister()
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

    def test_unregistered_dotfile_config_link_is_judged_by_native_discovery_alone(self):
        import subprocess
        self.unregister()
        self.reg.agent.unlink()
        config = self.reg.config
        config.unlink()
        target = self.root / 'dotfiles/config.toml'
        target.parent.mkdir()
        try:
            config.symlink_to(target)
        except OSError as exc:
            self.skipTest(f'symlink creation unavailable: {exc}')
        for text, code, blocked in [('model="native"\n', 77, True), ('model="native"\n', 113, False),
                                    ('model_provider="ghost_alice_context"\n', 113, False)]:
            with self.subTest(text=text, native_exit=code):
                target.write_text(text)
                self.targets.clear()
                observed = subprocess.CompletedProcess([], code, b'', b'')
                with patch.object(gateway.subprocess, 'run', return_value=observed) as native:
                    result, hooks = self.report()
                self.assertEqual(bool(result.get('blocked')), blocked, result)
                self.assertEqual(hooks, 0 if blocked else 1)
                self.assertEqual(native.call_count, 1)
                self.assertTrue(config.is_symlink())
                self.assertEqual(target.read_text(), text)

    def test_unregistered_service_with_unknown_arguments_blocks_cleanup(self):
        import subprocess
        self.unregister()
        observed = subprocess.CompletedProcess([], 0, f'path = {self.reg.agent}\n'.encode(), b'')
        plist = plistlib.loads(self.reg.agent.read_bytes())
        plist['ProgramArguments'] = []
        self.reg.agent.write_bytes(plistlib.dumps(plist))
        with patch.object(cleanup, '_codex_home', return_value=self.root / 'unrelated-codex'), \
             patch.object(gateway.subprocess, 'run', return_value=observed):
            result, hooks = self.report(platform='claude', target=self.root / 'unrelated-runtime')
        self.assertEqual((self.targets, hooks, self.stops), ([], 0, 0))
        self.assertEqual(result['items'][0]['action'], 'manual-review')

    def test_linked_service_files_block_affected_cleanup(self):
        for name in ('state', 'agent'):
            with self.subTest(name=name):
                target = self.reg.state_path if name == 'state' else self.reg.agent
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

    def test_a_target_preserved_for_codex_is_not_a_removal_root_and_leaves_the_gateway_alone(self):
        shared = self.root / '.claude/skills/_shared'
        shared.mkdir(parents=True)
        (shared / 'completion_assessment.py').write_text('installed Core')
        manifests = self.root / '.ghost-alice/install-state'
        manifests.mkdir(parents=True, exist_ok=True)
        body = dict(targets=[dict(target_name='_shared', dest_path=str(shared))])
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
        self.assertTrue(shared.exists())
        self.assertTrue(any(item.get('reason') == 'shared-with-other-platform' for item in result['items']))
        gateway_item = next(item for item in result['items'] if item.get('kind') == 'context-gateway')
        self.assertEqual(gateway_item['action'], 'unchanged')
        self.assertIsNotNone(self.reg.service())


class DetachCommandTests(RegisteredHomeCase):
    """`context_gateway_install.py detach`: the explicit unregistration of the recorded service."""

    def test_detach_unregisters_the_service_and_keeps_its_state_dir(self):
        config = self.reg.config
        config.write_text(config.read_text(encoding='utf-8') + '\n[features]\nhooks=true\n', encoding='utf-8')
        installed_payload.update(self.reg.ghost, {"service": gateway_testing.service_section(self.reg)})
        result = gateway.detach(confirm=True)
        self.assertEqual(result['action'], 'detached')
        self.assertEqual(self.stops, 1)
        self.assertFalse(self.active)
        self.assertFalse(self.reg.agent.exists())
        self.assertNotIn(PROVIDER, config.read_text(encoding='utf-8'))
        self.assertEqual(tomllib.loads(config.read_text(encoding='utf-8')), {'model': 'native', 'features': {'hooks': True}})
        self.assertIsNone(self.reg.service())
        self.assertTrue(self.reg.state_path.is_file())
        self.assertTrue((self.reg.state_dir / 'usage.jsonl').is_file())

    def test_detach_without_a_registered_service_changes_nothing(self):
        self.unregister()
        before = {p: p.read_bytes() for p in (self.reg.agent, self.reg.config, installed_payload.path(self.reg.ghost))}
        with patch.object(gateway, '_service_state') as service:
            result = gateway.detach(confirm=True)
        service.assert_not_called()
        self.assertEqual((result['action'], result['reason']), ('missing', 'gateway-not-registered'))
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_detach_preview_changes_nothing(self):
        before = {p: p.read_bytes() for p in (self.reg.agent, self.reg.config, installed_payload.path(self.reg.ghost))}
        result = gateway.detach(confirm=False)
        self.assertEqual((result['status'], result['action']), ('dry-run', 'would-detach'))
        self.assertEqual([step['phase'] for step in result['plan']['steps']],
                         ['prepare', 'stop', 'verify-absence', 'stage', 'commit', 'retire'])
        self.assertIn(str(self.reg.state_dir), result['plan']['preserved'])
        self.assertEqual(result['plan']['steps'][-2]['record'], dict(section='service', action='delete'))
        self.assertEqual(self.stops, 0)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_detach_cli(self):
        stdout = io.StringIO()
        with patch.object(gateway, 'detach', return_value=dict(action='detached')) as run, \
             contextlib.redirect_stdout(stdout):
            self.assertEqual(gateway.main(['detach', '--confirm']), 0)
        self.assertEqual(run.call_args.kwargs, dict(confirm=True))
        self.assertEqual(json.loads(stdout.getvalue()), dict(action='detached'))
        with patch.object(gateway, 'detach', return_value=dict(action='missing')) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gateway.main(['detach', '--user-home', str(self.root)]), 0)
        self.assertEqual(run.call_args.kwargs, dict(confirm=False, home=self.root))
        with patch.object(gateway, 'detach', side_effect=GatewayBlocked('changed')), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gateway.main(['detach', '--confirm']), 2)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
            gateway.main(['detach', '--root', '/x'])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn('unrecognized arguments', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
