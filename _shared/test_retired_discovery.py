"""Managed retirement preserves user changes and unknown assets.

Dependencies: Python 3.11+ standard library, pytest.
"""
import json
import errno
from pathlib import Path
import subprocess
import sys
import hashlib
import os
from unittest import mock
import pytest
from retired_assets import retire_skill, retire_command
from install_destination import InstallDestination, use

SHARED = Path(__file__).resolve().parent


@pytest.mark.parametrize('entrypoint', ('sync', 'retirement-claude', 'retirement-codex'))
def test_command_retirement_preserves_unknown_assets_at_installer_entrypoints(tmp_path, entrypoint):
    commands = tmp_path/'commands'
    commands.mkdir()
    retired = commands/'jailbreak-detector.md'
    retired.write_text('@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\n')
    unknown = commands/'local-skill.md'
    unknown.write_text('User-owned command')
    ghost = tmp_path/'ghost'
    if entrypoint == 'sync':
        catalog = tmp_path/'catalog.json'
        catalog.write_text(json.dumps({'skills':[]}))
        argv = [sys.executable, '-B', str(SHARED/'commands_sync.py'),
            str(catalog), str(commands), 'fix', 'claude']
    else:
        argv = [sys.executable, '-B', str(SHARED/'retired_assets.py'),
            '--skills-root', str(tmp_path/'skills'), '--source-root', str(SHARED.parent),
            '--ghost-root', str(ghost), '--platform', entrypoint.removeprefix('retirement-'),
            '--commands-root', str(commands)]
    result = subprocess.run(argv, text=True, capture_output=True,
        env={**os.environ, 'GHOST_ALICE_GHOST_ROOT': str(ghost)})
    assert result.returncode == 0, result.stdout+result.stderr
    assert unknown.read_text() == 'User-owned command'
    backups = list(commands.parent.glob('retired-command-backups/*/jailbreak-detector.md'))
    if entrypoint == 'retirement-codex':
        assert retired.read_text() == '@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\n'
        assert backups == []
    else:
        assert not retired.exists()
        assert len(backups) == 1
        assert backups[0].read_text() == '@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\n'


@pytest.mark.parametrize('kind', ('managed', 'modified', 'unknown', 'foreign-marker'))
def test_skill_retirement_classifies_preserves_and_removes_only_owned_assets(tmp_path, kind):
    target = tmp_path/'skills/jailbreak-detector'
    target.mkdir(parents=True)
    original = '# Source fixture\n'
    (target/'SKILL.md').write_text(original)
    if kind != 'unknown':
        marker = dict(managed_by='Ghost-ALICE', asset_id='foreign' if kind == 'foreign-marker' else 'jailbreak-detector',
            content_hashes={'SKILL.md':hashlib.sha256(original.encode()).hexdigest()})
        (target/'.ghost-alice-install.json').write_text(json.dumps(marker))
    if kind == 'modified':
        (target/'SKILL.md').write_text(original+'User correction retained\n')
    content = (target/'SKILL.md').read_bytes()
    ghost = tmp_path/'ghost'
    pending = ghost/'pending-merges/codex/manifest.json'
    pending.parent.mkdir(parents=True)
    pending.write_text(json.dumps({'version':1,'platform':'codex','entries':[{'id':'existing','decided':False}]}))
    result = retire_skill(target, platform='codex', source_root=SHARED.parent, pending_root=ghost)
    data = json.loads(pending.read_text())
    assert data['entries'][0]['id'] == 'existing'
    if kind in ('unknown','foreign-marker'):
        assert result['action'] == 'preserved'
        assert (target/'SKILL.md').read_bytes() == content
    else:
        assert result['action'] == 'retired-managed'
        assert not target.exists()
        backup = Path(result['backup_path'])
        assert (backup/'SKILL.md').read_bytes() == content
        if kind == 'modified':
            entry = data['entries'][1]
            assert entry['ownership'] == 'user-modified-managed'
            assert entry['decided'] is False
            assert entry['backup_path'] == str(backup)
            assert entry['source_path'] == str(target)
        else:
            assert len(data['entries']) == 1


def test_modified_retired_wrapper_has_backup_and_undecided_pending_entry(tmp_path):
    command = tmp_path/'commands/jailbreak-detector.md'
    command.parent.mkdir()
    content = '@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\nUser correction\n'
    command.write_text(content)
    ghost = tmp_path/'ghost'
    result = retire_command(command, source_root=SHARED.parent, pending_root=ghost)
    assert not command.exists()
    assert Path(result['backup_path']).read_text() == content
    entry = json.loads((ghost/'pending-merges/claude/manifest.json').read_text())['entries'][0]
    assert entry['decided'] is False
    assert entry['current_hash'] == hashlib.sha256(content.encode()).hexdigest()


def test_failed_pending_record_restores_active_bytes(tmp_path, monkeypatch):
    import retired_assets
    command = tmp_path/'commands/jailbreak-detector.md'
    command.parent.mkdir()
    content = '@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\nUser correction\n'
    command.write_text(content)
    def failed(*args, **kwargs):
        raise OSError('fixture pending storage unavailable')
    monkeypatch.setattr(retired_assets,'_pending',failed)
    with pytest.raises(OSError,match='fixture pending storage unavailable'):
        retire_command(command,source_root=SHARED.parent,pending_root=tmp_path/'ghost')
    assert command.read_text() == content


def test_failed_audit_never_leaves_pending_backup_reference(tmp_path):
    command = tmp_path/'commands/jailbreak-detector.md'
    command.parent.mkdir()
    content = '@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\nUser correction\n'
    command.write_text(content)
    ghost = tmp_path/'ghost'
    ghost.mkdir()
    (ghost/'install-state').write_text('fixture audit destination is not a directory')
    with pytest.raises(OSError):
        retire_command(command, source_root=SHARED.parent, pending_root=ghost)
    assert command.read_text() == content
    assert not (ghost/'pending-merges/claude/manifest.json').exists()


def _collision_fixture(tmp_path, asset_kind):
    if asset_kind == 'command':
        path = tmp_path/'commands/jailbreak-detector.md'
        path.parent.mkdir()
        content = b'@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\nUser correction\n'
        path.write_bytes(content)
        return path, content, lambda: retire_command(path, source_root=SHARED.parent, pending_root=tmp_path/'ghost')
    path = tmp_path/'skills/jailbreak-detector'
    path.parent.mkdir()
    if asset_kind == 'skill-link':
        target = SHARED.parent/'jailbreak-detector'
        path.symlink_to(target, target_is_directory=True)
        content = str(target)
    else:
        path.mkdir()
        original = b'# Managed source fixture\n'
        (path/'SKILL.md').write_bytes(original+b'User correction\n')
        (path/'.ghost-alice-install.json').write_text(json.dumps(dict(managed_by='Ghost-ALICE',
            asset_id='jailbreak-detector', content_hashes={'SKILL.md':hashlib.sha256(original).hexdigest()})))
        content = {item.name:item.read_bytes() for item in path.iterdir()}
    return path, content, lambda: retire_skill(path, platform='codex', source_root=SHARED.parent, pending_root=tmp_path/'ghost')


def _replace_active_slot(path, kind, root):
    if kind == 'file':
        path.write_bytes(b'New user-owned active entry\n')
    elif kind == 'directory':
        path.mkdir()  # An empty replacement directory is still a distinct entry.
    else:
        target = root/'new-link-target'
        if kind == 'link':
            target.write_bytes(b'New target bytes\n')
        path.symlink_to(target, target_is_directory=True)
    return path.lstat().st_ino


@pytest.mark.parametrize('failure_kind', ('audit', 'pending'))
@pytest.mark.parametrize('asset_kind', ('command', 'skill-directory'))
@pytest.mark.parametrize('replacement_kind', ('file', 'directory', 'link', 'broken-link'))
def test_failed_retirement_preserves_replacement_and_original_backup(tmp_path, monkeypatch, failure_kind, asset_kind, replacement_kind):
    import retired_assets
    path, original, retire = _collision_fixture(tmp_path, asset_kind)
    failure = OSError(f'fixture {failure_kind} persistence unavailable')
    backup_paths = []
    new_inode = []
    phases = []
    real_audit = retired_assets._audit
    def audit(event, result, phase):
        phases.append(phase)
        real_audit(event, result, phase)
        if failure_kind == 'audit' and phase == 'moved':
            backup_paths.append(Path(result['backup_path']))
            new_inode.append(_replace_active_slot(path, replacement_kind, tmp_path))
            raise failure
    def pending(entry, *args):
        backup_paths.append(Path(entry['backup_path']))
        new_inode.append(_replace_active_slot(path, replacement_kind, tmp_path))
        raise failure
    monkeypatch.setattr(retired_assets, '_audit', audit)
    if failure_kind == 'pending':
        monkeypatch.setattr(retired_assets, '_pending', pending)
    location = InstallDestination(mode='isolated', owned_root=str(tmp_path),
        platform_dir=str(tmp_path/'platform'), skills_dir=str(tmp_path/'skills'),
        ghost_root=str(tmp_path/'ghost'), ledger_root=str(tmp_path/'ledger'),
        project_root=str(tmp_path/'project'), core_source_root=str(SHARED.parent))
    with use(location), pytest.raises(RuntimeError) as caught:
        retire()
    backup = backup_paths[0]
    assert path.lstat().st_ino == new_inode[0]
    if replacement_kind == 'file':
        assert path.read_bytes() == b'New user-owned active entry\n'
    elif replacement_kind.endswith('link'):
        assert path.is_symlink() and path.readlink() == tmp_path/'new-link-target'
    else:
        assert path.is_dir() and list(path.iterdir()) == []
    if asset_kind == 'command':
        assert backup.read_bytes() == original
    else:
        assert {item.name:item.read_bytes() for item in backup.iterdir()} == original
    assert caught.value.__cause__ is failure
    assert str(path) in str(caught.value) and str(backup) in str(caught.value)
    assert 'rollback' in str(caught.value)
    assert phases == ['moved', 'rollback-failed']
    events = tmp_path/'ghost/install-state'/f'{"claude" if asset_kind == "command" else "codex"}-events.jsonl'
    last_event = json.loads(events.read_text().splitlines()[-1])
    assert last_event['path'] == str(path) and last_event['backup_path'] == str(backup)
    assert last_event['rollback_status'] == 'collision'
    assert not list((tmp_path/'ghost/pending-merges').glob('*/manifest.json'))


@pytest.mark.parametrize('replacement_kind', ('file', 'directory', 'link', 'broken-link'))
def test_failed_retired_skill_link_preserves_both_link_entries(tmp_path, monkeypatch, replacement_kind):
    import retired_assets
    path, original_target, retire = _collision_fixture(tmp_path, 'skill-link')
    failure = OSError('fixture audit unavailable')
    observed = {}
    phases = []
    def audit(event, result, phase):
        phases.append(phase)
        if phase == 'moved':
            observed['backup'] = Path(result['backup_path'])
            observed['inode'] = _replace_active_slot(path, replacement_kind, tmp_path)
            raise failure
    monkeypatch.setattr(retired_assets, '_audit', audit)
    with pytest.raises(RuntimeError) as caught:
        retire()
    assert path.lstat().st_ino == observed['inode']
    assert observed['backup'].is_symlink()
    assert str(observed['backup'].readlink()) == original_target
    assert caught.value.__cause__ is failure
    assert str(path) in str(caught.value) and str(observed['backup']) in str(caught.value)
    assert 'rolled-back' not in phases


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin native no-replace collision boundary')
def test_replacement_at_native_restore_call_is_not_overwritten(tmp_path, monkeypatch):
    import ctypes
    import retired_assets
    path, original, retire = _collision_fixture(tmp_path, 'command')
    failure = OSError('fixture pending unavailable')
    observed = {}
    def pending(entry, *args):
        observed['backup'] = Path(entry['backup_path'])
        raise failure
    real_library = ctypes.CDLL(None, use_errno=True)
    native = real_library.renamex_np
    native.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    native.restype = ctypes.c_int
    def at_native_window(source, destination, flags):
        observed['inode'] = _replace_active_slot(path, 'file', tmp_path)
        return native(source, destination, flags)
    class Library:
        renamex_np = staticmethod(at_native_window)
    monkeypatch.setattr(retired_assets, '_pending', pending)
    monkeypatch.setattr(ctypes, 'CDLL', lambda *args, **kwargs: Library())
    with pytest.raises(RuntimeError) as caught:
        retire()
    assert path.read_bytes() == b'New user-owned active entry\n'
    assert path.lstat().st_ino == observed['inode']
    assert observed['backup'].read_bytes() == original
    assert caught.value.__cause__ is failure


@pytest.mark.skipif(os.name == 'nt', reason='POSIX native symbol unavailable boundary')
def test_unavailable_native_no_replace_preserves_backup_without_fallback(tmp_path, monkeypatch):
    import ctypes
    import retired_assets
    path, original, retire = _collision_fixture(tmp_path, 'command')
    failure = OSError('fixture pending unavailable')
    observed = {}
    def pending(entry, *args):
        observed['backup'] = Path(entry['backup_path'])
        raise failure
    monkeypatch.setattr(retired_assets, '_pending', pending)
    monkeypatch.setattr(ctypes, 'CDLL', lambda *args, **kwargs: object())
    with pytest.raises(RuntimeError) as caught:
        retire()
    assert not path.exists()
    assert observed['backup'].read_bytes() == original
    assert caught.value.__cause__ is failure
    assert str(path) in str(caught.value) and str(observed['backup']) in str(caught.value)


@pytest.mark.parametrize('asset_kind,failure_kind', (('command','audit'), ('command','pending'),
    ('skill-directory','audit'), ('skill-directory','pending'), ('skill-link','audit')))
def test_empty_slot_rollback_preserves_original_entry_and_exception(tmp_path, monkeypatch, asset_kind, failure_kind):
    import retired_assets
    path, original, retire = _collision_fixture(tmp_path, asset_kind)
    failure = OSError(f'fixture {failure_kind} unavailable')
    observed = {}
    phases = []
    real_audit = retired_assets._audit
    def audit(event, result, phase):
        phases.append(phase)
        if phase == 'moved' and failure_kind == 'audit':
            observed['backup'] = Path(result['backup_path'])
            raise failure
        real_audit(event, result, phase)
    def pending(entry, *args):
        observed['backup'] = Path(entry['backup_path'])
        raise failure
    monkeypatch.setattr(retired_assets, '_audit', audit)
    monkeypatch.setattr(retired_assets, '_pending', pending)
    with pytest.raises(OSError) as caught:
        retire()
    assert caught.value is failure
    assert 'rolled-back' in phases and 'rollback-failed' not in phases
    assert not observed['backup'].exists() and not observed['backup'].is_symlink()
    if asset_kind == 'command':
        assert path.read_bytes() == original
    elif asset_kind == 'skill-link':
        assert str(path.readlink()) == original
    else:
        assert {item.name:item.read_bytes() for item in path.iterdir()} == original


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin native unsupported-flag failure boundary')
@pytest.mark.parametrize('code', (errno.ENOTSUP, errno.EINVAL))
def test_native_restore_failure_keeps_backup_and_records_failed_rollback(tmp_path, monkeypatch, code):
    import ctypes
    import retired_assets
    path, original, retire = _collision_fixture(tmp_path, 'command')
    failure = OSError('fixture pending unavailable')
    observed = {}
    def pending(entry, *args):
        observed['backup'] = Path(entry['backup_path'])
        raise failure
    def failed_native(*args):
        ctypes.set_errno(code)
        return -1  # Failure injection; this is not native support proof.
    class Library:
        renamex_np = staticmethod(failed_native)
    monkeypatch.setattr(retired_assets, '_pending', pending)
    monkeypatch.setattr(ctypes, 'CDLL', lambda *args, **kwargs: Library())
    with pytest.raises(RuntimeError) as caught:
        retire()
    assert not path.exists() and not path.is_symlink()
    assert observed['backup'].read_bytes() == original
    assert caught.value.__cause__ is failure
    event = tmp_path/'ghost/install-state/claude-events.jsonl'
    records = [json.loads(line) for line in event.read_text().splitlines()]
    assert [row['phase'] for row in records] == ['moved', 'rollback-failed']
    assert records[-1]['rollback_status'] == 'failed'
    assert str(code) in records[-1]['rollback_error']


def test_committed_pending_entry_keeps_recovery_backup_on_collision(tmp_path, monkeypatch):
    import retired_assets
    path, original, retire = _collision_fixture(tmp_path, 'command')
    failure = OSError('fixture failure after pending persistence')
    real_pending = retired_assets._pending
    observed = {}
    def pending(entry, *args):
        real_pending(entry, *args)
        observed['backup'] = Path(entry['backup_path'])
        _replace_active_slot(path, 'file', tmp_path)
        raise failure
    monkeypatch.setattr(retired_assets, '_pending', pending)
    with pytest.raises(RuntimeError) as caught:
        retire()
    assert caught.value.__cause__ is failure
    assert path.read_bytes() == b'New user-owned active entry\n'
    manifest = tmp_path/'ghost/pending-merges/claude/manifest.json'
    entry = json.loads(manifest.read_text())['entries'][0]
    assert Path(entry['backup_path']) == observed['backup']
    assert Path(entry['backup_path']).read_bytes() == original
    assert entry['decided'] is False


@pytest.mark.skipif(os.name == 'nt', reason='Unknown POSIX platform fallback boundary')
def test_unknown_platform_keeps_backup_without_unsafe_restore(tmp_path, monkeypatch):
    import retired_assets
    from types import SimpleNamespace
    path, original, retire = _collision_fixture(tmp_path, 'command')
    failure = OSError('fixture pending unavailable')
    observed = {}
    def pending(entry, *args):
        observed['backup'] = Path(entry['backup_path'])
        raise failure
    monkeypatch.setattr(retired_assets, '_pending', pending)
    monkeypatch.setattr(retired_assets, 'sys', SimpleNamespace(platform='unknown-fixture-platform'))
    with pytest.raises(RuntimeError) as caught:
        retire()
    assert caught.value.__cause__ is failure
    assert not path.exists() and observed['backup'].read_bytes() == original
    assert 'unavailable on this platform' in str(caught.value)


@pytest.mark.parametrize('owned', (True, False))
def test_retired_broken_link_entry_requires_exact_supplied_source(tmp_path, owned):
    source = tmp_path/'source'
    source.mkdir()
    target = tmp_path/'skills/jailbreak-detector'
    target.parent.mkdir()
    link_target = source/'jailbreak-detector' if owned else tmp_path/'foreign/jailbreak-detector'
    target.symlink_to(link_target, target_is_directory=True)
    result = retire_skill(target, platform='codex', source_root=source, pending_root=tmp_path/'ghost')
    if owned:
        assert result['action'] == 'retired-managed'
        assert not target.is_symlink()
        backup = Path(result['backup_path'])
        assert backup.is_symlink()
        assert backup.readlink() == link_target
    else:
        assert result['action'] == 'preserved'
        assert target.readlink() == link_target


def test_exact_source_link_entry_respects_owned_parent_guard(tmp_path):
    owned = tmp_path/'owned'
    skills = owned/'skills'
    skills.mkdir(parents=True)
    target = skills/'jailbreak-detector'
    target.symlink_to(SHARED.parent/'jailbreak-detector', target_is_directory=True)
    destination = InstallDestination(mode='isolated', owned_root=str(owned),
        platform_dir=str(owned/'platform'), skills_dir=str(skills), ghost_root=str(owned/'ghost'),
        ledger_root=str(owned/'ledger'), project_root=str(owned/'project'), core_source_root=str(SHARED.parent))
    with use(destination):
        result = retire_skill(target, platform='codex', source_root=SHARED.parent, pending_root=owned/'ghost')
        outside = tmp_path/'outside/jailbreak-detector'
        outside.parent.mkdir()
        outside.symlink_to(SHARED.parent/'jailbreak-detector', target_is_directory=True)
        with pytest.raises(ValueError, match='escape'):
            retire_skill(outside, platform='codex', source_root=SHARED.parent, pending_root=owned/'ghost')
    assert Path(result['backup_path']).is_symlink()
    assert not target.is_symlink()
    assert outside.is_symlink()


@pytest.mark.parametrize('platform', ('codex', 'claude'))
def test_installer_cleanup_calls_retirement_with_explicit_owned_ghost_root(tmp_path, platform, capfd):
    target = tmp_path/'skills/jailbreak-detector'
    target.mkdir(parents=True)
    content = '# Managed source fixture\n'
    (target/'SKILL.md').write_text(content)
    (target/'.ghost-alice-install.json').write_text(json.dumps(dict(managed_by='Ghost-ALICE',
        asset_id='jailbreak-detector', content_hashes={'SKILL.md':hashlib.sha256(content.encode()).hexdigest()})))
    ghost = tmp_path/'explicit-ghost'
    claude = tmp_path/'Configured Claude'
    commands = claude/'commands'; commands.mkdir(parents=True)
    wrapper = commands/'jailbreak-detector.md'
    original = '@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\n'
    wrapper.write_text(original)
    unknown = commands/'local-skill.md'; unknown.write_text('User-owned command')
    env = {key:value for key,value in os.environ.items() if not key.startswith('GHOST_ALICE_')}
    env.update(HOME=str(tmp_path/'home'), CLAUDE_CONFIG_DIR=str(claude), GHOST_ALICE_GHOST_ROOT=str(ghost),
        PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
    from installer import cli as installer_cli, selection
    ctx = installer_cli.Context(options=installer_cli.Options(platform=platform),
        platform=platform, skills_dir=target.parent)
    # The installer's own deprecated list is empty here: only the retirement discovery runs.
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(selection, 'DEPRECATED_INSTALLED_SKILLS', ()):
        completed = selection.cleanup_deprecated_installed_skills(ctx, target.parent)
    out, err = capfd.readouterr()
    assert completed, out+err
    assert err == ''
    assert not target.exists()
    backup = next((ghost/'deprecated-skill-backups').glob('*/jailbreak-detector'))
    assert (backup/'SKILL.md').read_text() == content
    assert (ghost/'install-state'/f'{platform}-events.jsonl').is_file()
    assert unknown.read_text() == 'User-owned command'
    if platform == 'claude':
        assert not wrapper.exists()
        saved = next((claude/'retired-command-backups').glob('*/jailbreak-detector.md'))
        assert saved.read_text() == original
    else:
        assert wrapper.read_text() == original
