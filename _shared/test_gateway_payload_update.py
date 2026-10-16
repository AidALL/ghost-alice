"""General installer replacement must coordinate its actual gateway dependencies."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import context_gateway_install as gateway
from context_gateway_model import RETIRED_MODULES
import install_transaction as files
import test_context_gateway_install as fixtures


class GatewayPayloadUpdateTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ContextGatewayInstallTests()
        self.addCleanup(fixture.doCleanups)
        self.args, _ = fixture.installation_fixture()
        fixture.mocked_install(self.args)
        self.root, self.shared = self.args[0].parent, self.args[1][0]
        self.source = self.root / 'replacement'
        shutil.copytree(self.shared, self.source)
        (self.source / 'context_gateway.py').write_bytes(b'updated gateway implementation')
        self.original = {p.relative_to(self.shared): p.read_bytes() for p in self.shared.rglob('*') if p.is_file()}
        self.active, self.stops = True, 0
        self.events = []
        for item in (
            patch.object(gateway.sys, 'platform', 'darwin'),
            patch.object(gateway, '_service_state', side_effect=lambda _: 'running' if self.active else 'absent'),
            patch.object(gateway, '_stop_service', side_effect=self.stop),
            patch.object(gateway, '_start_service', side_effect=self.start),
            patch.object(gateway, '_ready', return_value=True),
        ):
            item.start(); self.addCleanup(item.stop)

    def stop(self, _domain):
        self.events.append('stop')
        self.active = False
        self.stops += 1

    def start(self, *_args):
        self.events.append('start')
        self.active = True

    def test_general_shared_replacement_stops_gateway_before_publish_and_refreshes_binding(self):
        rename = files._rename_path
        def observed(source, destination):
            if source == self.shared:
                self.assertFalse(self.active, 'active dependencies must not be replaced')
            return rename(source, destination)
        with patch.object(files, '_rename_path', side_effect=observed):
            files.staged_copy_replace(self.source, self.shared, rollback_root=self.root / 'rollbacks')
        self.assertEqual((self.stops, self.active), (1, True))
        receipt = self.args[4] / 'installation.json'
        retained = json.loads(receipt.read_bytes())
        self.assertEqual(retained['files'][str(self.shared / 'context_gateway.py')],
                         gateway._digest((self.shared / 'context_gateway.py').read_bytes()))
        binding = json.loads((self.root / '.ghost-alice/context-gateway-binding.json').read_bytes())
        self.assertEqual(binding['installation_digest'], gateway._digest(receipt.read_bytes()))

    def test_readiness_failure_restores_entire_payload_before_prior_service_restart(self):
        def start(*_args):
            if len(self.events) > 2:
                self.assertEqual({p.relative_to(self.shared): p.read_bytes() for p in self.shared.rglob('*') if p.is_file()},
                                 self.original)
            self.start()
        with patch.object(gateway, '_ready', side_effect=[False, True]), \
             patch.object(gateway, '_start_service', side_effect=start):
            with self.assertRaisesRegex(RuntimeError, 'restored'):
                files.staged_copy_replace(self.source, self.shared, rollback_root=self.root / 'rollbacks')
        self.assertTrue(self.active)
        self.assertEqual({p.relative_to(self.shared): p.read_bytes() for p in self.shared.rglob('*') if p.is_file()}, self.original)

    def own_retired_module(self, raw=b'retired gateway module'):
        """An earlier installation owned a module that the current payload no longer ships."""
        path = self.shared / RETIRED_MODULES[0]
        path.write_bytes(raw)
        receipt_path = self.args[4] / 'installation.json'
        receipt = json.loads(receipt_path.read_bytes())
        receipt['files'][str(path)] = gateway._digest(raw)
        receipt_raw = json.dumps(receipt).encode()
        receipt_path.write_bytes(receipt_raw)
        binding_path = self.root / '.ghost-alice/context-gateway-binding.json'
        binding = dict(json.loads(binding_path.read_bytes()), installation_digest=gateway._digest(receipt_raw))
        binding_path.write_bytes(json.dumps(binding).encode())
        return path

    def test_update_retires_an_unchanged_module_the_payload_no_longer_ships(self):
        retired = self.own_retired_module()
        files.staged_copy_replace(self.source, self.shared, rollback_root=self.root / 'rollbacks')
        self.assertEqual((self.stops, self.active), (1, True))
        self.assertFalse(retired.exists())
        receipt = json.loads((self.args[4] / 'installation.json').read_bytes())
        self.assertNotIn(str(retired), receipt['files'])
        self.assertIn(str(self.shared / 'context_gateway.py'), receipt['files'])

    def test_update_preserves_a_changed_retired_module_for_review(self):
        retired = self.own_retired_module()
        retired.write_bytes(b'locally changed retired module')
        with self.assertRaisesRegex(RuntimeError, 'component changed'):
            files.staged_copy_replace(self.source, self.shared, rollback_root=self.root / 'rollbacks')
        self.assertEqual(retired.read_bytes(), b'locally changed retired module')
        self.assertEqual((self.stops, self.active), (0, True))

    def test_independent_payload_keeps_gateway_running_and_ownership_unchanged(self):
        destination = self.root / 'independent-claude'
        receipt = (self.args[4] / 'installation.json').read_bytes()
        files.staged_copy_replace(self.source, destination, rollback_root=self.root / 'rollbacks')
        self.assertEqual((self.stops, self.active), (0, True))
        self.assertEqual((self.args[4] / 'installation.json').read_bytes(), receipt)

    def source_update_fixture(self, *, rename_companion=False, retire=False):
        from installer_source_update import SourceUpdate
        repository, origin = self.root / 'linked-source', self.root / 'origin.git'
        repository.mkdir()
        self.shared.rename(repository / '_shared')
        self.shared.symlink_to(repository / '_shared', target_is_directory=True)
        if rename_companion:
            companion_root = self.root / 'session-intent-analyzer'
            companion_root.rename(repository / companion_root.name)
            companion_root.symlink_to(repository / companion_root.name, target_is_directory=True)
        def git(path, *arguments):
            return subprocess.run(['git', '-c', 'user.name=garlicvread', '-c', 'user.email=ceo@aidall.tech',
                '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', '-C', str(path), *arguments],
                capture_output=True, check=True, text=True)
        origin.mkdir()
        git(origin, 'init', '--bare')
        git(repository, 'init')
        git(repository, 'add', '.')
        git(repository, 'commit', '-m', 'fixture baseline')
        git(repository, 'remote', 'add', 'origin', str(origin))
        git(repository, 'push', '-u', 'origin', 'HEAD')
        upstream = self.root / 'upstream'
        git(self.root, 'clone', str(origin), str(upstream))
        if rename_companion:
            companion = upstream / gateway.COMPANIONS[0]
            companion.rename(companion.with_name('ledger.py'))
        elif retire:
            (upstream / '_shared' / RETIRED_MODULES[0]).unlink()
        else:
            (upstream / '_shared/context_gateway.py').write_bytes(b'new upstream gateway')
        git(upstream, 'add', '.')
        git(upstream, 'commit', '-m', 'fixture candidate')
        git(upstream, 'push', 'origin', 'HEAD')
        operation = SourceUpdate(repository)
        self.addCleanup(operation.cleanup)
        operation.prepare()
        return operation

    def test_managed_source_update_stops_a_gateway_using_a_linked_checkout(self):
        operation = self.source_update_fixture()
        gateway.coordinate_payload_replacement(operation)
        self.assertEqual((self.stops, self.active), (1, True))
        self.assertEqual((self.shared / 'context_gateway.py').read_bytes(), b'new upstream gateway')

    def test_managed_source_update_retires_a_module_removed_upstream(self):
        retired = self.own_retired_module()
        operation = self.source_update_fixture(retire=True)
        gateway.coordinate_payload_replacement(operation)
        self.assertEqual((self.stops, self.active), (1, True))
        self.assertFalse(retired.exists())
        receipt = json.loads((self.args[4] / 'installation.json').read_bytes())
        self.assertNotIn(str(retired), receipt['files'])

    def test_managed_source_update_restores_source_before_restarting_prior_service(self):
        operation = self.source_update_fixture()
        with patch.object(gateway, '_ready', side_effect=[False, True]):
            with self.assertRaisesRegex(RuntimeError, 'restored'):
                gateway.coordinate_payload_replacement(operation)
        self.assertTrue(self.active)
        self.assertEqual({p.relative_to(self.shared): p.read_bytes() for p in self.shared.rglob('*') if p.is_file()}, self.original)


    def test_source_rename_cannot_delete_a_live_companion_dependency(self):
        operation = self.source_update_fixture(rename_companion=True)
        prior = operation.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'removes a required gateway dependency'):
            gateway.coordinate_payload_replacement(operation)
        self.assertEqual(operation.git('rev-parse', 'HEAD'), prior)
        self.assertTrue((self.root / gateway.COMPANIONS[0]).is_file())
        self.assertEqual((self.stops, self.active), (0, True))


class GatewayPortableInstallerTests(unittest.TestCase):
    def test_windows_payload_replacement_and_absent_gateway_removal_need_no_unix_uid(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            source, destination = home / 'source', home / 'installed'
            source.mkdir()
            (source / 'payload').write_bytes(b'portable payload')
            with patch.object(Path, 'home', return_value=home), \
                 patch.object(gateway.sys, 'platform', 'win32'), \
                 patch.object(gateway.platform.os, 'getuid', None, create=True):
                files.staged_copy_replace(source, destination, rollback_root=home / 'rollback')
                result = gateway.detach_for_uninstall(user_home=home, remove_codex=False,
                    removing_paths=[destination], confirm=True)
            self.assertEqual((destination / 'payload').read_bytes(), b'portable payload')
            self.assertEqual(result['action'], 'missing')


if __name__ == '__main__':
    unittest.main()
