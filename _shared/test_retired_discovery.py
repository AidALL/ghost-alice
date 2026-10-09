"""Managed retirement preserves user changes and unknown assets.

Dependencies: Python 3.11+ standard library, pytest.
"""
import json
from pathlib import Path
import subprocess
import sys
import hashlib
import os
import pytest
from retired_assets import retire_skill, retire_command
from install_destination import InstallDestination, use

SHARED = Path(__file__).resolve().parent


def test_sync_removes_owned_retired_wrapper_from_active_discovery(tmp_path):
    catalog = tmp_path/'catalog.json'
    catalog.write_text(json.dumps({'skills':[]}))
    commands = tmp_path/'commands'
    commands.mkdir()
    retired = commands/'jailbreak-detector.md'
    retired.write_text('@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\n')
    unknown = commands/'local-skill.md'
    unknown.write_text('User-owned command')
    result = subprocess.run([sys.executable, '-B', str(SHARED/'commands_sync.py'),
        str(catalog), str(commands), 'fix', 'claude'], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout+result.stderr
    assert not retired.exists()
    assert unknown.read_text() == 'User-owned command'
    backups = list(commands.parent.glob('retired-command-backups/*/jailbreak-detector.md'))
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
def test_installer_cleanup_calls_retirement_with_explicit_owned_ghost_root(tmp_path, platform):
    target = tmp_path/'skills/jailbreak-detector'
    target.mkdir(parents=True)
    content = '# Managed source fixture\n'
    (target/'SKILL.md').write_text(content)
    (target/'.ghost-alice-install.json').write_text(json.dumps(dict(managed_by='Ghost-ALICE',
        asset_id='jailbreak-detector', content_hashes={'SKILL.md':hashlib.sha256(content.encode()).hexdigest()})))
    ghost = tmp_path/'explicit-ghost'
    env = dict(os.environ, SCRIPT_DIR=str(SHARED.parent), PLATFORM=platform,
        GHOST_ALICE_GHOST_ROOT=str(ghost), PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
    command = 'source "$SCRIPT_DIR/installer_lib/python_runtime.sh"\nsource "$SCRIPT_DIR/installer_lib/targets.sh"\nDEPRECATED_INSTALLED_SKILLS=()\ncleanup_deprecated_installed_skills "$1"'
    result = subprocess.run(['/bin/bash','-c',command,'retirement-source-fixture',str(target.parent)],
        env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout+result.stderr
    assert not target.exists()
    backup = next((ghost/'deprecated-skill-backups').glob('*/jailbreak-detector'))
    assert (backup/'SKILL.md').read_text() == content
    assert (ghost/'install-state'/f'{platform}-events.jsonl').is_file()
