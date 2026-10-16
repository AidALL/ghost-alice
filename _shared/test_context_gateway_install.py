"""Provider configuration and installation boundary contracts."""
import json
import io
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import context_gateway_install as install


class ContextGatewayInstallTests(unittest.TestCase):
    new_dependencies = ('response_context_use.py', 'claude_context_adapter.py', 'source_lookup.py')

    def installation_fixture(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        home = patch.object(install.Path, 'home', return_value=root)
        home.start(); self.addCleanup(home.stop)
        # These lifecycle fixtures simulate a capable gateway interpreter as
        # well as macOS/launchctl. Capability rejection has separate coverage.
        capability = patch.object(install, '_require_gateway_runtime', return_value=None, create=True)
        capability.start(); self.addCleanup(capability.stop)
        self.capability_patch = capability
        source, shared, codex, ledger, state = [root / n for n in ('source', 'shared', 'codex', 'ledger', 'state')]
        for directory in (source / '_shared', shared, codex, ledger, state):
            directory.mkdir(parents=True, exist_ok=True)
        (shared / 'completion_assessment.py').write_text('installed Core')
        (ledger / 'ghost-state.sqlite3').touch()
        manifest = {}
        for name in install.MODULES:
            (source / '_shared' / name).write_text('new ' + name)
            (shared / name).write_text('prior ' + name)
            manifest[str(shared / name)] = install._digest((shared / name).read_bytes())
        companions = []
        for relative in getattr(install, 'COMPANIONS', ()):
            source_path, target = source / relative, shared.parent / relative
            source_path.parent.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text('new ' + relative)
            target.write_text('prior ' + relative)
            manifest[str(target)] = install._digest(target.read_bytes())
            companions.append(target)
        (state / 'state.json').write_text(json.dumps(dict(schema_version='context-gateway.v1',
            port=12345, scope='prior-scope', ledger_root=str(ledger), codex_dir=str(codex),
            usage_log=str(state / 'usage.jsonl'))))
        (codex / 'config.toml').write_text(install.render_provider('model="native"\n', 12345, 'prior-scope'))
        agent = root / 'agent.plist'
        agent.write_bytes(b'prior owned service')
        receipt = state / 'installation.json'
        receipt.write_text(json.dumps(dict(schema_version='context-gateway-installation.v1', status='installed',
            provider=install.PROVIDER, service=install.LABEL, config_path=str(codex / 'config.toml'),
            state_path=str(state / 'state.json'), state_digest=install._digest((state / 'state.json').read_bytes()),
            launch_agent=str(agent), launch_agent_digest=install._digest(agent.read_bytes()),
            runtime_shared_dir=str(shared), ledger_root=str(ledger), files=manifest)))
        registration = root / '.ghost-alice/context-gateway-binding.json'
        registration.parent.mkdir()
        registration.write_text(json.dumps(dict(schema_version='context-gateway-binding.v1',
            installation_path=str(receipt), installation_digest=install._digest(receipt.read_bytes()))))
        paths = [shared / name for name in install.MODULES] + companions + [state / 'state.json', codex / 'config.toml', agent, receipt, registration]
        return (source, [shared], codex, ledger, state, agent), {p: p.read_bytes() for p in paths}

    def prior_files(self, args):
        return json.loads((args[4] / 'installation.json').read_bytes())['files']

    def bind_prior_files(self, args, files):
        receipt_path = args[4] / 'installation.json'
        receipt = json.loads(receipt_path.read_bytes())
        receipt['files'] = files
        receipt_path.write_text(json.dumps(receipt))
        registration = args[0].parent / '.ghost-alice/context-gateway-binding.json'
        binding = json.loads(registration.read_bytes())
        binding['installation_digest'] = install._digest(receipt_path.read_bytes())
        registration.write_text(json.dumps(binding))

    def test_edited_legacy_manifest_cannot_authorize_overwriting_local_components(self):
        args, _ = self.installation_fixture()
        target = args[1][0] / 'context_gateway.py'
        target.write_bytes(b'locally edited component must stay')
        legacy = args[4] / 'installed-files.json'
        manifest = self.prior_files(args)
        manifest[str(target)] = install._digest(target.read_bytes())
        legacy.write_text(json.dumps(manifest))
        paths = set(args[0].parent.rglob('*'))
        original = {p: (p.read_bytes(), p.stat()) for p in paths if p.is_file()}
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install, '_service_state', return_value='absent') as service, \
             patch.object(install, '_start_service'), patch.object(install, '_ready', return_value=True), \
             patch.object(install, '_write', wraps=install._write) as write, \
             patch.object(install.socket, 'socket') as listener:
            with self.assertRaises(ValueError):
                install.install(*args)
        service.assert_not_called(); write.assert_not_called(); listener.assert_not_called()
        self.assertEqual(set(args[0].parent.rglob('*')), paths)
        for path, (raw, metadata) in original.items():
            observed = path.stat()
            self.assertEqual((path.read_bytes(), observed.st_ino, observed.st_mode, observed.st_mtime_ns),
                             (raw, metadata.st_ino, metadata.st_mode, metadata.st_mtime_ns))

    def test_legacy_manifest_is_removed_without_consuming_or_generating_it(self):
        args, _ = self.installation_fixture()
        legacy = args[4] / 'installed-files.json'
        legacy.write_bytes(b'preserve arbitrary legacy content without interpreting it')
        self.assertEqual(self.mocked_install(args)['status'], 'installed')
        self.assertFalse(legacy.exists())
        clean = self.clean_installation_fixture()
        self.assertEqual(self.mocked_install(clean)['status'], 'installed')
        self.assertFalse((clean[4] / 'installed-files.json').exists())

    def test_linked_component_and_companion_files_are_preserved_before_all_changes(self):
        for kind in ('module', 'companion'):
            for contents in ('prior', 'source', 'dangling'):
                with self.subTest(kind=kind, contents=contents):
                    args, _ = self.installation_fixture()
                    relative = install.COMPANIONS[0] if kind == 'companion' else '_shared/context_gateway.py'
                    target = (args[1][0].parent / relative if kind == 'companion'
                              else args[1][0] / 'context_gateway.py')
                    destination = args[0].parent / 'user-managed-linked-file'
                    if contents != 'dangling':
                        destination.write_bytes(target.read_bytes() if contents == 'prior'
                                                else (args[0] / relative).read_bytes())
                    target.unlink(); target.symlink_to(destination)
                    paths = set(args[0].parent.rglob('*'))
                    metadata = target.lstat()
                    original = {p: (p.read_bytes(), p.stat()) for p in paths
                                if p.is_file() and not p.is_symlink()}
                    with patch.object(install.sys, 'platform', 'darwin'), \
                         patch.object(install, '_service_state', return_value='absent') as service, \
                         patch.object(install, '_start_service'), patch.object(install, '_ready', return_value=True), \
                         patch.object(install, '_write', wraps=install._write) as write:
                        with self.assertRaisesRegex(ValueError, '[Ss]ymlink'):
                            install.install(*args)
                    service.assert_not_called(); write.assert_not_called()
                    observed = target.lstat()
                    self.assertEqual((target.readlink(), observed.st_ino, observed.st_mode, observed.st_mtime_ns),
                                     (destination, metadata.st_ino, metadata.st_mode, metadata.st_mtime_ns))
                    self.assertEqual(set(args[0].parent.rglob('*')), paths)
                    for path, (raw, before) in original.items():
                        after = path.stat()
                        self.assertEqual((path.read_bytes(), after.st_ino, after.st_mode, after.st_mtime_ns),
                                         (raw, before.st_ino, before.st_mode, before.st_mtime_ns))

    def test_linked_state_and_ownership_files_are_preserved_before_all_changes(self):
        for name in ('state.json', 'installation.json', 'registration', 'usage.jsonl', 'service.log'):
            with self.subTest(name=name):
                args, _ = self.installation_fixture()
                self.mocked_install(args)
                target = (args[0].parent / '.ghost-alice/context-gateway-binding.json'
                          if name == 'registration' else args[4] / name)
                destination = args[0].parent / 'user-owned-metadata'
                destination.write_bytes(target.read_bytes())
                target.unlink(); target.symlink_to(destination)
                paths = set(args[0].parent.rglob('*'))
                before = target.lstat()
                raw = destination.read_bytes()
                with patch.object(install.sys, 'platform', 'darwin'), \
                     patch.object(install, '_service_state', return_value='absent') as service, \
                     patch.object(install, '_start_service'), patch.object(install, '_ready', return_value=True), \
                     patch.object(install, '_write', wraps=install._write) as write:
                    with self.assertRaisesRegex(ValueError, '[Ss]ymlink'):
                        install.install(*args)
                service.assert_not_called(); write.assert_not_called()
                after = target.lstat()
                self.assertEqual((target.readlink(), after.st_ino, after.st_mode, after.st_mtime_ns),
                                 (destination, before.st_ino, before.st_mode, before.st_mtime_ns))
                self.assertEqual(destination.read_bytes(), raw)
                self.assertEqual(set(args[0].parent.rglob('*')), paths)

    def test_refresh_preserves_modified_or_partial_provider_before_all_side_effects(self):
        mutations = (
            lambda text: text.replace('wire_api = "responses"', 'wire_api = "user-choice"'),
            lambda text: text.replace('model_provider = "ghost_alice_context"', 'model_provider = "user-provider"'),
            lambda text: text.replace('127.0.0.1:12345', '127.0.0.1:54321'),
            lambda text: text.replace(install.TABLE_END, '# user removed the closing marker'),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                args, _ = self.installation_fixture()
                config = args[2] / 'config.toml'
                config.write_text(mutation(config.read_text()))
                metadata = config.stat()
                before = (config.read_bytes(), metadata.st_ino, metadata.st_mode, metadata.st_mtime_ns)
                paths = set(args[0].parent.rglob('*'))
                with patch.object(install.sys, 'platform', 'darwin'), \
                     patch.object(install, '_service_state', return_value='absent') as service, \
                     patch.object(install, '_start_service'), patch.object(install, '_ready', return_value=True), \
                     patch.object(install, '_write', wraps=install._write) as write:
                    with self.assertRaises(ValueError):
                        install.install(*args)
                service.assert_not_called(); write.assert_not_called()
                metadata = config.stat()
                self.assertEqual((config.read_bytes(), metadata.st_ino, metadata.st_mode, metadata.st_mtime_ns), before)
                self.assertEqual(set(args[0].parent.rglob('*')), paths)

    def test_markers_without_prior_binding_do_not_authorize_refresh_or_create_state(self):
        import shutil
        args, _ = self.installation_fixture()
        self.mocked_install(args)
        shutil.rmtree(args[4])
        (args[0].parent / '.ghost-alice/context-gateway-binding.json').unlink()
        original = (args[2] / 'config.toml').read_bytes()
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install, '_service_state') as service, patch.object(install, '_write') as write, \
             patch.object(install.socket, 'socket') as listener:
            with self.assertRaises(ValueError):
                install.install(*args)
        service.assert_not_called(); write.assert_not_called(); listener.assert_not_called()
        self.assertFalse(args[4].exists())
        self.assertEqual((args[2] / 'config.toml').read_bytes(), original)

    def test_registered_refresh_preserves_changed_or_unowned_launch_agent_before_all_side_effects(self):
        for marked_provider in (True, False):
            for change in ('edited', 'different-path', 'missing', 'missing-digest', 'symlink', 'dangling-symlink'):
                with self.subTest(marked_provider=marked_provider, change=change):
                    args, _ = self.installation_fixture()
                    config, agent = args[2] / 'config.toml', args[5]
                    if not marked_provider:
                        config.write_text(install.remove_provider(config.read_text()))
                    if change == 'edited':
                        agent.write_bytes(b'user-edited service must stay')
                    elif change == 'different-path':
                        other = agent.with_name('other-agent.plist')
                        other.write_bytes(agent.read_bytes())
                        args = (*args[:5], other)
                    elif change == 'missing':
                        agent.unlink()
                    elif change == 'missing-digest':
                        receipt_path = args[4] / 'installation.json'
                        receipt = json.loads(receipt_path.read_bytes())
                        receipt.pop('launch_agent_digest')
                        receipt_path.write_text(json.dumps(receipt))
                        registration = args[0].parent / '.ghost-alice/context-gateway-binding.json'
                        binding = json.loads(registration.read_bytes())
                        binding['installation_digest'] = install._digest(receipt_path.read_bytes())
                        registration.write_text(json.dumps(binding))
                    else:
                        target = agent.with_name('user-service.plist')
                        if change == 'symlink':
                            target.write_bytes(agent.read_bytes())
                        agent.unlink()
                        agent.symlink_to(target.name)
                    paths = set(args[0].parent.rglob('*'))
                    originals = {path: (path.read_bytes(), path.stat()) for path in paths
                                 if path.is_file() and not path.is_symlink()}
                    links = {path: (path.readlink(), path.lstat()) for path in paths if path.is_symlink()}
                    with patch.object(install.sys, 'platform', 'darwin'), \
                         patch.object(install, '_service_state', return_value='absent') as service, \
                         patch.object(install, '_start_service'), patch.object(install, '_ready', return_value=True), \
                         patch.object(install, '_write', wraps=install._write) as write, \
                         patch.object(install.socket, 'socket') as listener:
                        with self.assertRaises(ValueError):
                            install.install(*args)
                    service.assert_not_called(); write.assert_not_called(); listener.assert_not_called()
                    self.assertEqual(set(args[0].parent.rglob('*')), paths)
                    for path, (raw, metadata) in originals.items():
                        observed = path.stat()
                        self.assertEqual((path.read_bytes(), observed.st_ino, observed.st_mode, observed.st_mtime_ns),
                                         (raw, metadata.st_ino, metadata.st_mode, metadata.st_mtime_ns))
                    for path, (target, metadata) in links.items():
                        observed = path.lstat()
                        self.assertEqual((path.readlink(), observed.st_ino, observed.st_mode, observed.st_mtime_ns),
                                         (target, metadata.st_ino, metadata.st_mode, metadata.st_mtime_ns))

    def test_unchanged_registered_agent_refreshes_without_provider_markers(self):
        args, _ = self.installation_fixture()
        config = args[2] / 'config.toml'
        original = install.remove_provider(config.read_text())
        config.write_text(original)
        result = self.mocked_install(args)
        self.assertEqual(result['status'], 'installed')
        self.assertEqual(install.remove_provider(config.read_text()), original)
        self.assertEqual(result['launch_agent'], str(args[5]))
        self.assertEqual(result['launch_agent_digest'], install._digest(args[5].read_bytes()))

    def test_existing_agent_without_registration_is_preserved_even_with_retained_state(self):
        for retained_receipt in (True, False):
            for edited_agent in (True, False):
                with self.subTest(retained_receipt=retained_receipt, edited_agent=edited_agent):
                    args, _ = self.installation_fixture()
                    root = args[0].parent
                    config = args[2] / 'config.toml'
                    config.write_text(install.remove_provider(config.read_text()))
                    (root / '.ghost-alice/context-gateway-binding.json').unlink()
                    if not retained_receipt:
                        (args[4] / 'installation.json').unlink()
                    if edited_agent:
                        args[5].write_bytes(b'unrelated user-managed launch agent')
                    paths = set(root.rglob('*'))
                    originals = {path: (path.read_bytes(), path.stat()) for path in paths if path.is_file()}
                    with patch.object(install.sys, 'platform', 'darwin'), \
                         patch.object(install, '_service_state', return_value='absent') as service, \
                         patch.object(install, '_start_service') as start, \
                         patch.object(install, '_ready', return_value=True), \
                         patch.object(install, '_write', wraps=install._write) as write, \
                         patch.object(install.socket, 'socket') as listener:
                        with self.assertRaisesRegex(ValueError, 'unowned launch agent'):
                            install.install(*args)
                    service.assert_not_called(); start.assert_not_called()
                    write.assert_not_called(); listener.assert_not_called()
                    self.assertEqual(set(root.rglob('*')), paths)
                    for path, (raw, metadata) in originals.items():
                        observed = path.stat()
                        self.assertEqual((path.read_bytes(), observed.st_ino, observed.st_mode, observed.st_mtime_ns),
                                         (raw, metadata.st_ino, metadata.st_mode, metadata.st_mtime_ns))

    def test_unchanged_owned_refresh_preserves_adjacent_user_settings(self):
        args, _ = self.installation_fixture()
        config = args[2] / 'config.toml'
        before = config.read_text() + '\n[features]\nhooks=true\n'
        config.write_text(before)
        self.mocked_install(args)
        self.assertEqual(install.remove_provider(config.read_text()), install.remove_provider(before))
        self.assertEqual(tomllib.loads(config.read_text())['features'], {'hooks': True})

    def test_retained_state_requires_authenticated_unchanged_ownership_without_provider_markers(self):
        for change in ('unregistered', 'unregistered-no-receipt', 'orphan-manifest',
                       'edited-state', 'missing-state', 'wrong-state-path', 'missing-state-digest'):
            with self.subTest(change=change):
                args, _ = self.installation_fixture()
                root, state = args[0].parent, args[4]
                config = args[2] / 'config.toml'
                config.write_text(install.remove_provider(config.read_text()))
                registration = root / '.ghost-alice/context-gateway-binding.json'
                if change in ('unregistered', 'unregistered-no-receipt', 'orphan-manifest'):
                    registration.unlink(); args[5].unlink()
                    if change != 'unregistered':
                        (state / 'installation.json').unlink()
                    if change == 'orphan-manifest':
                        (state / 'state.json').unlink()
                        (state / 'installed-files.json').write_text('{}')
                elif change == 'edited-state':
                    value = json.loads((state / 'state.json').read_bytes())
                    value['scope'] = 'user-edited-state'
                    (state / 'state.json').write_text(json.dumps(value))
                elif change == 'missing-state':
                    (state / 'state.json').unlink()
                else:
                    receipt_path = state / 'installation.json'
                    receipt = json.loads(receipt_path.read_bytes())
                    if change == 'wrong-state-path':
                        receipt['state_path'] = str(state / 'different.json')
                    else:
                        receipt.pop('state_digest')
                    receipt_path.write_text(json.dumps(receipt))
                    binding = json.loads(registration.read_bytes())
                    binding['installation_digest'] = install._digest(receipt_path.read_bytes())
                    registration.write_text(json.dumps(binding))
                paths = set(root.rglob('*'))
                originals = {path: (path.read_bytes(), path.stat()) for path in paths if path.is_file()}
                with patch.object(install.sys, 'platform', 'darwin'), \
                     patch.object(install, '_service_state', return_value='absent') as service, \
                     patch.object(install, '_start_service') as start, \
                     patch.object(install, '_ready', return_value=True), \
                     patch.object(install, '_write', wraps=install._write) as write, \
                     patch.object(install.socket, 'socket') as listener:
                    with self.assertRaises(ValueError):
                        install.install(*args)
                service.assert_not_called(); start.assert_not_called()
                write.assert_not_called(); listener.assert_not_called()
                self.assertEqual(set(root.rglob('*')), paths)
                for path, (raw, metadata) in originals.items():
                    observed = path.stat()
                    self.assertEqual((path.read_bytes(), observed.st_ino, observed.st_mode, observed.st_mtime_ns),
                                     (raw, metadata.st_ino, metadata.st_mode, metadata.st_mtime_ns))

    def clean_installation_fixture(self):
        import shutil
        args, _ = self.installation_fixture()
        for name in install.MODULES:
            (args[1][0] / name).write_bytes((args[0] / '_shared' / name).read_bytes())
        for relative in install.COMPANIONS:
            (args[1][0].parent / relative).write_bytes((args[0] / relative).read_bytes())
        shutil.rmtree(args[4]); args[5].unlink()
        (args[0].parent / '.ghost-alice/context-gateway-binding.json').unlink()
        (args[2] / 'config.toml').write_text('model="native"\n')
        return args

    def test_clean_first_install_needs_no_prior_gateway_ownership(self):
        args = self.clean_installation_fixture()
        result = self.mocked_install(args)
        self.assertEqual(result['status'], 'installed')
        self.assertEqual(tomllib.loads((args[2] / 'config.toml').read_text())['model_provider'], install.PROVIDER)

    def test_failed_first_install_restores_empty_state_and_allows_a_new_attempt(self):
        for existing_empty_state in (False, True):
            for failure in ('probe', 'backup', 'archive', 'bootstrap', 'readiness'):
                with self.subTest(existing_empty_state=existing_empty_state, failure=failure):
                    args = self.clean_installation_fixture()
                    if existing_empty_state:
                        args[4].mkdir()
                    else:
                        args = (*args[:4], args[0].parent / 'new-parent/state', args[5])
                    root = args[0].parent
                    paths = set(root.rglob('*'))
                    original = {p: (p.read_bytes(), p.stat().st_mode) for p in paths if p.is_file()}
                    active = [False]
                    real_write = install._write
                    def write(path, raw, mode=0o600):
                        real_write(path, raw, mode)
                        if ((failure == 'backup' and path.name.endswith('-config.toml'))
                                or (failure == 'archive' and path.name == 'manifest.json'
                                    and path.parent.name.endswith('-artifacts'))):
                            raise OSError('injected state preparation failure')
                    def service(_domain):
                        if failure == 'probe':
                            raise RuntimeError('injected probe failure')
                        return 'running' if active[0] else 'absent'
                    def start(*_args):
                        if failure == 'bootstrap':
                            raise RuntimeError('injected bootstrap failure')
                        active[0] = True
                        for name in ('service.log', 'usage.jsonl'):
                            with (args[4] / name).open('ab') as stream:
                                stream.write(b'owned candidate service output\n')
                    with patch.object(install.sys, 'platform', 'darwin'), \
                         patch.object(install, '_service_state', side_effect=service), \
                         patch.object(install, '_start_service', side_effect=start), \
                         patch.object(install, '_stop_service', side_effect=lambda _: active.__setitem__(0, False)), \
                         patch.object(install, '_ready', return_value=False), \
                         patch.object(install, '_write', side_effect=write):
                        with self.assertRaises((RuntimeError, OSError)):
                            install.install(*args)
                    self.assertFalse(active[0])
                    self.assertEqual(set(root.rglob('*')), paths)
                    self.assertEqual({p: (p.read_bytes(), p.stat().st_mode) for p in original}, original)
                    self.assertEqual(self.mocked_install(args)['status'], 'installed')

    def test_first_install_preserves_unowned_live_service_before_state_writes(self):
        args = self.clean_installation_fixture()
        paths = set(args[0].parent.rglob('*'))
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install, '_service_state', return_value='running'), \
             patch.object(install, '_stop_service') as stop, patch.object(install, '_start_service') as start, \
             patch.object(install, '_write', wraps=install._write) as write:
            with self.assertRaises(ValueError):
                install.install(*args)
        stop.assert_not_called(); start.assert_not_called(); write.assert_not_called()
        self.assertEqual(set(args[0].parent.rglob('*')), paths)

    def test_failed_first_install_preserves_uncertain_service_and_concurrent_artifacts(self):
        for change in ('uncertain-stop', 'user-file', 'changed-backup', 'replaced-log'):
            with self.subTest(change=change):
                args = self.clean_installation_fixture()
                active = [False]
                changed = []
                def start(*_args):
                    active[0] = True
                    if change == 'user-file':
                        path = args[4] / 'user-note.txt'
                        path.write_bytes(b'preserve independent user content')
                        changed.append(path)
                    elif change == 'changed-backup':
                        path = next((args[4] / 'backups').glob('*-config.toml'))
                        path.write_bytes(b'preserve concurrently changed recovery file')
                        changed.append(path)
                    elif change == 'replaced-log':
                        path = args[4] / 'service.log'
                        replacement = args[4] / 'replacement'
                        replacement.write_bytes(b'preserve replacement user log')
                        replacement.replace(path)
                        changed.append(path)
                def stop(_domain):
                    if change == 'uncertain-stop':
                        raise RuntimeError('candidate stop is unconfirmed')
                    active[0] = False
                with patch.object(install.sys, 'platform', 'darwin'), \
                     patch.object(install, '_service_state', side_effect=lambda _: 'running' if active[0] else 'absent'), \
                     patch.object(install, '_start_service', side_effect=start), \
                     patch.object(install, '_stop_service', side_effect=stop), \
                     patch.object(install, '_ready', return_value=False):
                    with self.assertRaisesRegex(RuntimeError, 'recovery failed'):
                        install.install(*args)
                self.assertEqual(active[0], change == 'uncertain-stop')
                self.assertTrue(list((args[4] / 'backups').glob('*-artifacts/manifest.json')))
                self.assertTrue((args[4] / 'service.log').is_file())
                for path in changed:
                    self.assertIn(b'preserve', path.read_bytes())

    def test_python_311_is_rejected_before_gateway_side_effects(self):
        args, original = self.installation_fixture()
        self.capability_patch.stop()
        before = set(args[0].parent.rglob('*'))
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install.sys, 'version_info', (3, 11, 0)), \
             patch.object(install, '_service_state', return_value='absent') as service, \
             patch.object(install, '_start_service'), patch.object(install, '_ready', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'Python 3.14'):
                install.install(*args)
        service.assert_not_called()
        self.assertEqual(set(args[0].parent.rglob('*')), before)
        self.assertEqual({p: p.read_bytes() for p in original}, original)

    def test_missing_zstd_is_rejected_before_service_or_file_mutations(self):
        args, original = self.installation_fixture()
        self.capability_patch.stop()
        before = set(args[0].parent.rglob('*'))
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install.sys, 'version_info', (3, 14, 0)), \
             patch.dict('sys.modules', {'compression': None}), \
             patch.object(install, '_service_state') as service:
            with self.assertRaisesRegex(RuntimeError, 'compression.zstd'):
                install.install(*args)
        service.assert_not_called()
        self.assertEqual(set(args[0].parent.rglob('*')), before)
        self.assertEqual({p: p.read_bytes() for p in original}, original)

    @unittest.skipUnless(__import__('sys').version_info >= (3, 14), 'real Python 3.14 codec capability')
    def test_actual_python_314_supports_required_request_codec(self):
        install._require_gateway_runtime()

    def test_successful_custom_install_has_durable_discovery_binding(self):
        args, _ = self.installation_fixture()
        result = self.mocked_install(args)
        binding = json.loads((args[0].parent / '.ghost-alice/context-gateway-binding.json').read_text())
        self.assertEqual(binding['installation_path'], str(args[4] / 'installation.json'))
        self.assertEqual(result['runtime_shared_dir'], str(args[1][-1]))
        self.assertEqual(result['launch_agent_digest'], install._digest(args[5].read_bytes()))

    def test_nested_core_runtime_state_is_rejected_before_installation_writes(self):
        args, original = self.installation_fixture()
        nested = args[0].parent / '.ghost-alice/runtime/gateway-state'
        unsafe = (*args[:4], nested, args[5])
        with patch.object(install, '_service_state') as service:
            with self.assertRaisesRegex(ValueError, 'outside Core payload'):
                self.mocked_install(unsafe)
        service.assert_not_called()
        self.assertFalse(nested.exists())
        self.assertEqual({p: p.read_bytes() for p in original}, original)

    def test_symlinked_codex_config_is_rejected_before_install_side_effects(self):
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                args, original = self.installation_fixture()
                config = args[2] / 'config.toml'
                target = args[2] / 'user-managed.toml'
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
                files_before = {path: raw for path, raw in original.items() if path != config}
                with patch.object(install.sys, 'platform', 'darwin'), \
                     patch.object(install, '_service_state', return_value='absent') as service, \
                     patch.object(install, '_start_service'), patch.object(install, '_ready', return_value=True), \
                     patch.object(install, '_write', wraps=install._write) as write:
                    with self.assertRaisesRegex(ValueError, '[Ss]ymlinked Codex config'):
                        install.install(*args)
                service.assert_not_called()
                write.assert_not_called()
                link = config.lstat()
                self.assertEqual((config.readlink(), link.st_ino, link.st_mode, link.st_mtime_ns), link_before)
                self.assertEqual(((target.read_bytes(), target.stat().st_ino, target.stat().st_mode,
                                   target.stat().st_mtime_ns) if target.exists() else None), target_before)
                self.assertEqual({path: path.read_bytes() for path in files_before}, files_before)

    def test_new_core_dependencies_require_exact_retained_bases_and_join_same_rollback(self):
        import inspect
        self.assertIn('dependency_bases', inspect.signature(install.install).parameters,
                      'The coherent installer cannot safely adopt its required native intake dependencies')
        args, original = self.installation_fixture()
        targets = [args[1][0] / 'native_transcript.py'] + [args[1][0].parent / rel for rel in install.COMPANIONS]
        manifest = self.prior_files(args)
        bases = {str(p): manifest.pop(str(p)) for p in targets}
        self.bind_prior_files(args, manifest)
        with self.assertRaises(ValueError): self.mocked_install(args)
        self.mocked_install(args, dependency_bases=bases)
        current = self.prior_files(args)
        self.assertTrue(all(current[str(p)] == install._digest(p.read_bytes()) for p in targets))

    def test_dependency_base_cannot_overwrite_new_local_change(self):
        import inspect
        self.assertIn('dependency_bases', inspect.signature(install.install).parameters)
        args, _ = self.installation_fixture()
        target = args[1][0] / 'native_transcript.py'
        manifest = self.prior_files(args)
        baseline = manifest.pop(str(target));self.bind_prior_files(args, manifest)
        target.write_text('new user change must stay')
        with self.assertRaises(ValueError): self.mocked_install(args, dependency_bases={str(target):baseline})
        self.assertEqual(target.read_text(), 'new user change must stay')

    def test_bootstrap_failure_restores_prior_artifacts_and_service(self):
        args, original = self.installation_fixture()
        active, boots = [True], []
        def run(command, **_kwargs):
            operation = command[1]
            if operation == 'print': code = 0 if active[0] else 113
            elif operation == 'bootout': active[0], code = False, 0
            else:
                boots.append({'state': (args[4] / 'state.json').read_bytes(),
                              'code': (args[1][0] / install.MODULES[0]).read_bytes()})
                code = 5 if len(boots) == 1 else 0
                active[0] = not code
            output = f'path = {args[5]}\n'.encode() if operation == 'print' and code == 0 else b''
            return subprocess.CompletedProcess(command, code, output, b'')
        with patch.object(install.sys, 'platform', 'darwin'), patch.object(install.subprocess, 'run', run), \
             patch.object(install, '_ready', return_value=True, create=True), \
             patch.object(install.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"status":"ready"}')):
            with self.assertRaises(RuntimeError): install.install(*args)
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertTrue(active[0])
        self.assertEqual(len(boots), 2)
        self.assertNotEqual(boots[0]['code'], boots[1]['code'])
        self.assertEqual(boots[1]['state'], original[args[4] / 'state.json'])

    def test_change_during_service_stop_is_preserved_before_any_replacement(self):
        args, original = self.installation_fixture()
        target = args[1][0] / 'context_gateway.py'
        active = [True]
        def stop(_domain):
            active[0] = False
            target.write_bytes(b'concurrent user edit')
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install, '_service_state', side_effect=lambda _: 'running' if active[0] else 'absent'), \
             patch.object(install, '_stop_service', side_effect=stop), \
             patch.object(install, '_start_service', side_effect=lambda *_: active.__setitem__(0, True)), \
             patch.object(install, '_ready', return_value=True):
            with self.assertRaisesRegex(RuntimeError, '[Cc]oncurrent'):
                install.install(*args)
        self.assertEqual(target.read_bytes(), b'concurrent user edit')
        self.assertEqual({p: p.read_bytes() for p in original if p != target},
                         {p: raw for p, raw in original.items() if p != target})
        self.assertFalse(active[0], 'changed runtime cannot be restarted as a certified prior service')

    def test_failed_stop_prevents_file_replacement_and_start(self):
        args, original = self.installation_fixture()
        starts = []
        def run(command, **_kwargs):
            code = 5 if command[1] == 'bootout' else 0
            if command[1] == 'bootstrap': starts.append(command)
            output = f'path = {args[5]}\n'.encode() if command[1] == 'print' and code == 0 else b''
            return subprocess.CompletedProcess(command, code, output, b'')
        with patch.object(install.sys, 'platform', 'darwin'), patch.object(install.subprocess, 'run', run), \
             patch.object(install, '_ready', return_value=True, create=True), \
             patch.object(install.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"status":"ready"}')):
            with self.assertRaises(RuntimeError): install.install(*args)
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertEqual(starts, [])

    def test_update_preserves_the_existing_native_listener_binding(self):
        args, original = self.installation_fixture()
        active = [True]
        def run(command, **_kwargs):
            if command[1] == 'print': code = 0 if active[0] else 113
            else: active[0], code = command[1] == 'bootstrap', 0
            output = f'path = {args[5]}\n'.encode() if command[1] == 'print' and code == 0 else b''
            return subprocess.CompletedProcess(command, code, output, b'')
        with patch.object(install.sys, 'platform', 'darwin'), patch.object(install.subprocess, 'run', run), \
             patch.object(install, '_ready', return_value=True):
            install.install(*args)
        current = json.loads((args[4] / 'state.json').read_text())
        prior = json.loads(original[args[4] / 'state.json'])
        self.assertEqual((current['port'], current['scope']), (prior['port'], prior['scope']))

    def test_readiness_failure_restores_artifacts_before_restoring_service(self):
        args, original = self.installation_fixture()
        active, starts = [True], []
        def run(command, **_kwargs):
            if command[1] == 'print': code = 0 if active[0] else 113
            else:
                active[0], code = command[1] == 'bootstrap', 0
                if active[0]: starts.append((args[1][0] / install.MODULES[0]).read_bytes())
            output = f'path = {args[5]}\n'.encode() if command[1] == 'print' and code == 0 else b''
            return subprocess.CompletedProcess(command, code, output, b'')
        with patch.object(install.sys, 'platform', 'darwin'), patch.object(install.subprocess, 'run', run), \
             patch.object(install, '_ready', side_effect=[False, True]):
            with self.assertRaises(RuntimeError): install.install(*args)
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertTrue(active[0])
        self.assertEqual(starts[-1], original[args[1][0] / install.MODULES[0]])

    def test_stop_waits_for_observed_absence_before_returning(self):
        with patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(install, '_service_state', side_effect=['running', 'absent']) as observe, \
             patch.object(install.time, 'sleep'):
            install._stop_service('gui/fixture')
        self.assertEqual(observe.call_count, 2)


    def test_adds_supported_provider_and_preserves_unrelated_settings(self):
        original = 'model = "native-model"\n[features]\nhooks = true\n[projects."/my/project"]\ntrust_level = "trusted"\n'
        result = install.render_provider(original, 12345, 'local-scope')
        config = tomllib.loads(result)
        self.assertEqual(config['model_provider'], install.PROVIDER)
        provider = config['model_providers'][install.PROVIDER]
        self.assertTrue(provider['requires_openai_auth'])
        self.assertFalse(provider['supports_websockets'])
        self.assertEqual(provider['base_url'], 'http://127.0.0.1:12345/codex')
        self.assertEqual(config['features'], {'hooks':True})
        self.assertEqual(config['projects']['/my/project'], {'trust_level':'trusted'})
        self.assertNotIn('env_key', provider)
        self.assertEqual(install.remove_provider(result), original)

    def test_refresh_replaces_owned_blocks_without_duplicate_tables(self):
        original = 'model = "native-model"\n'
        once = install.render_provider(original, 12345, 'first')
        twice = install.render_provider(once, 12346, 'second')
        config = tomllib.loads(twice)
        self.assertEqual(config['model_providers'][install.PROVIDER]['http_headers'],
                         {'X-Ghost-Context-Scope':'second'})
        self.assertEqual(install.remove_provider(twice), original)

    def test_unrelated_provider_and_unowned_table_require_preservation(self):
        for original in ('model_provider = "other"\n',
                         '[model_providers.ghost_alice_context]\nname="User definition"\n'):
            with self.subTest(original=original), self.assertRaises(ValueError):
                install.render_provider(original, 12345, 'scope')

    def test_provider_removal_preserves_later_unrelated_edit(self):
        result = install.render_provider('model="native"\n', 12345, 'scope')
        changed = result + '\n[features]\nhooks=true\n'
        self.assertEqual(tomllib.loads(install.remove_provider(changed)),
                         {'model':'native', 'features':{'hooks':True}})

    def test_invalid_coordinates_are_not_interpolated(self):
        for port, scope in ((0, 'scope'), (True, 'scope'), (70000, 'scope'), (12345, '')):
            with self.subTest(port=port), self.assertRaises(ValueError):
                install.render_provider('', port, scope)

    def fixture_dependencies(self, args):
        for name in self.new_dependencies:
            (args[0] / '_shared' / name).write_text('new ' + name)

    def mocked_install(self, args, **kwargs):
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(install, '_service_state', return_value='absent'), \
             patch.object(install, '_start_service'), patch.object(install, '_stop_service'), \
             patch.object(install, '_ready', return_value=True):
            return install.install(*args, **kwargs)

    def test_installer_declares_all_context_use_dependencies(self):
        self.assertTrue(set(self.new_dependencies).issubset(install.MODULES))

    def test_optional_strict_log_root_is_explicit_and_backward_compatible(self):
        args, _ = self.installation_fixture()
        self.mocked_install(args)
        self.assertNotIn('strict_log_root', json.loads((args[4] / 'state.json').read_text()))
        explicit = args[4].parent / 'strict'
        self.mocked_install(args, strict_log_root=explicit)
        self.assertEqual(json.loads((args[4] / 'state.json').read_text())['strict_log_root'], str(explicit))

    def test_relative_strict_root_rejected_before_service_or_source_changes(self):
        args, original = self.installation_fixture()
        with patch.object(install, '_service_state') as service:
            with self.assertRaises(ValueError):
                self.mocked_install(args, strict_log_root=Path('relative'))
            service.assert_not_called()
        self.assertEqual({p: p.read_bytes() for p in original}, original)

    def test_cli_forwards_explicit_optional_strict_root(self):
        args, _ = self.installation_fixture()
        explicit = args[4].parent / 'strict'
        cli = ['--source-root', str(args[0]), '--shared-dir', str(args[1][0]), '--codex-dir', str(args[2]),
               '--ledger-root', str(args[3]), '--state-dir', str(args[4]), '--launch-agent', str(args[5]),
               '--strict-log-root', str(explicit)]
        with patch.object(install, 'install', return_value=dict(status='installed', service='fixture',
                  listener_ready=True, files=[], existing_native_process_adoption='unverified')) as run, \
             patch('sys.stdout', new=io.StringIO()):
            install.main(cli)
        self.assertEqual(run.call_args.kwargs['strict_log_root'], explicit)

    def test_absent_new_dependencies_are_installed_and_enter_authenticated_receipt(self):
        args, _ = self.installation_fixture()
        self.fixture_dependencies(args)
        previous = self.prior_files(args)
        for name in self.new_dependencies:
            target = args[1][0] / name
            target.unlink(missing_ok=True)
            previous.pop(str(target), None)
        self.bind_prior_files(args, previous)
        self.mocked_install(args)
        manifest = self.prior_files(args)
        for name in self.new_dependencies:
            target = args[1][0] / name
            self.assertEqual(target.read_bytes(), (args[0] / '_shared' / name).read_bytes())
            self.assertEqual(manifest[str(target)], install._digest(target.read_bytes()))

    def test_exact_source_new_dependencies_can_enter_receipt_without_prior_component_ownership(self):
        args, _ = self.installation_fixture()
        self.fixture_dependencies(args)
        previous = self.prior_files(args)
        for name in self.new_dependencies:
            target = args[1][0] / name
            target.write_bytes((args[0] / '_shared' / name).read_bytes())
            previous.pop(str(target), None)
        self.bind_prior_files(args, previous)
        self.mocked_install(args)
        manifest = self.prior_files(args)
        self.assertTrue(all(str(args[1][0] / name) in manifest for name in self.new_dependencies))

    def test_unmanaged_differing_new_dependency_is_preserved_before_service_changes(self):
        for name in self.new_dependencies:
            with self.subTest(name=name):
                args, original = self.installation_fixture()
                self.fixture_dependencies(args)
                target = args[1][0] / name
                target.write_text('unmanaged local change')
                previous = self.prior_files(args)
                previous.pop(str(target), None)
                self.bind_prior_files(args, previous)
                config_before = (args[2] / 'config.toml').read_bytes()
                with patch.object(install, '_service_state') as service:
                    with self.assertRaises(ValueError):
                        self.mocked_install(args)
                    service.assert_not_called()
                self.assertEqual(target.read_text(), 'unmanaged local change')
                self.assertEqual((args[2] / 'config.toml').read_bytes(), config_before)

    def test_missing_source_dependency_prevents_installation(self):
        args, original = self.installation_fixture()
        self.fixture_dependencies(args)
        (args[0] / '_shared' / 'source_lookup.py').unlink()
        with self.assertRaises(FileNotFoundError):
            self.mocked_install(args)
        self.assertEqual({path: path.read_bytes() for path in original}, original)

    def test_explicit_claude_home_is_stored_without_claude_settings_changes(self):
        args, _ = self.installation_fixture()
        self.fixture_dependencies(args)
        claude = args[0].parent / 'claude'
        claude.mkdir()
        settings = claude / 'settings.json'
        settings.write_text('{"env":{"original":"preserved"}}')
        before = settings.read_bytes()
        self.mocked_install(args, claude_dir=claude)
        state = json.loads((args[4] / 'state.json').read_text())
        self.assertEqual(state['claude_dir'], str(claude))
        self.assertEqual(settings.read_bytes(), before)
        config = tomllib.loads((args[2] / 'config.toml').read_text())
        self.assertTrue(config['model_providers'][install.PROVIDER]['requires_openai_auth'])

    def test_relative_claude_home_is_rejected(self):
        args, _ = self.installation_fixture()
        with self.assertRaises(ValueError):
            self.mocked_install(args, claude_dir=Path('relative-claude'))

    def test_source_dependency_bytes_are_snapshotted_once_for_all_targets(self):
        args, _ = self.installation_fixture()
        self.fixture_dependencies(args)
        second = args[0].parent / 'shared-second'
        second.mkdir()
        (second / 'completion_assessment.py').write_text('installed Core')
        args[1].append(second)
        original_read = Path.read_bytes
        reads = {}
        def read(path):
            raw = original_read(path)
            if path.parent == args[0] / '_shared':
                reads[path] = reads.get(path, 0) + 1
                if reads[path] > 1:
                    return raw + b' source changed between targets'
            return raw
        with patch.object(Path, 'read_bytes', read):
            self.mocked_install(args)
        for name in install.MODULES:
            self.assertEqual((args[1][0] / name).read_bytes(), (second / name).read_bytes())
            self.assertEqual(reads[args[0] / '_shared' / name], 1)


if __name__ == '__main__':
    unittest.main()
