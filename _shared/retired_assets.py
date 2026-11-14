"""Remove known managed retired assets from discovery while preserving recovery.

Dependencies: Python 3.11+ standard library and existing installer classification
and merge-companion manifest helpers. Unknown ownership is preserved.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from datetime import datetime, timezone
import uuid

from installer_assets import _content_hashes, classify_skill_root, OWNERSHIP_GHOST_ALICE_MANAGED, OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED
from install_destination import assert_mutation_path, ghost_root
from install_transaction import _rename_path
from install_state_writer import read_state
import atomic_write
import pending_manifest
import state_retention

RETIRED_SKILLS = ('harness-security-scan', 'session-intent-guard', 'jailbreak-detector')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _pending(entry, root, platform):
    """Commit this operation's review once, including when it was already decided."""
    manifest = Path(root)/'pending-merges'/platform/'manifest.json'
    assert_mutation_path(manifest)
    def change(data):
        matches = [item for item in data['entries'] if item.get('id') == entry['id']]
        if matches:
            if len(matches) != 1 or any(matches[0].get(key) != entry[key] for key in
                    ('platform', 'source_path', 'backup_path', 'current_hash', 'change_kind')):
                raise ValueError(f'conflicting retirement review: {entry["id"]}')
            return False
        data['entries'].append(entry)
        return True
    return pending_manifest.update_manifest(manifest, _read_pending, change)


def _read_pending(path):
    try:
        data = pending_manifest.read_json(path)
    except FileNotFoundError:
        return {'version': 1, 'entries': []}
    if (not isinstance(data, dict) or not isinstance(data.get('entries'), list)
            or any(not isinstance(item, dict) for item in data['entries'])):
        raise ValueError(f'invalid retirement pending manifest: {path}')
    return data


def content_identity(path):
    """Hash every entry's bytes/type/link text, without exclusions or following links."""
    path = Path(path)
    rows = []
    def visit(entry, relative):
        mode = entry.lstat().st_mode
        if stat.S_ISLNK(mode):
            rows.append((relative, 'link', os.readlink(entry)))
        elif getattr(entry, 'is_junction', lambda: False)():
            raise ValueError(f'retirement junction needs review: {entry}')
        elif stat.S_ISDIR(mode):
            rows.append((relative, 'directory'))
            for child in sorted(entry.iterdir()):
                visit(child, f'{relative}/{child.name}')
        elif stat.S_ISREG(mode):
            rows.append((relative, 'file', hashlib.sha256(entry.read_bytes()).hexdigest()))
        else:
            raise ValueError(f'retirement special entry needs review: {entry}')
    visit(path, '.')
    return _digest(rows)


def _present(path):
    return path.exists() or path.is_symlink()


def _entry_identity(path):
    info = path.lstat()
    return [info.st_dev, info.st_ino]


def _work_for(path, backup_root, platform, kind):
    root = Path(backup_root)
    if root.is_symlink():
        raise ValueError(f'retirement backup root is a link: {root}')
    if not root.exists():
        return None
    matches = []
    for folder in sorted(root.iterdir()):
        work_path = folder/'work.json'
        if folder.is_symlink() or not folder.is_dir() or not _present(work_path):
            continue
        if work_path.is_symlink():
            raise ValueError(f'retirement work record is a link: {work_path}')
        work = json.loads(work_path.read_text(encoding='utf-8'))
        if not isinstance(work, dict):
            raise ValueError(f'invalid retirement work: {work_path}')
        if work.get('source_path') != str(path) or work.get('platform') != platform:
            continue
        if (work.get('schema_version') != 1 or work.get('id') != folder.name
                or re.fullmatch(r'[0-9a-f]{32}', folder.name) is None
                or work.get('backup_path') != str(folder/path.name)
                or work.get('provider') != 'core' or work.get('kind') != kind
                or work.get('phase') not in {'prepared', 'records-started', 'complete'}
                or work.get('ownership') not in {OWNERSHIP_GHOST_ALICE_MANAGED, OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED}
                or not isinstance(work.get('content_identity'), str)
                or re.fullmatch(r'[0-9a-f]{64}', work['content_identity']) is None
                or not isinstance(work.get('entry_identity'), list) or len(work['entry_identity']) != 2
                or any(type(value) is not int for value in work['entry_identity'])
                or work.get('pending_id') != 'retired-'+folder.name):
            raise ValueError(f'invalid retirement work: {work_path}')
        matches.append((work_path, work))
    if len(matches) > 1:
        raise ValueError(f'multiple retirement operations for {path}')
    return matches[0] if matches else None


def _write_work(path, work):
    if path.is_symlink():
        raise ValueError(f'retirement work record is a link: {path}')
    atomic_write.write_json(path, work, sort_keys=True, sync_folder=True)


def _audit(event, result, phase, *, must_write=False):
    state_retention.append('install-state-events', event, json.dumps(dict(result, phase=phase, ts=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')), sort_keys=True)+'\n', must_write=must_write)


def _restore_no_replace(source, destination):
    """Restore one owned entry atomically, or leave the backup for recovery."""
    # Guard entry parents, so a broken link remains an entry rather than an
    # attempted mutation of its target. Never check absence then rename.
    assert_mutation_path(source.parent)
    assert_mutation_path(destination.parent)
    if os.name == 'nt':
        # CPython os.rename uses MoveFileExW(flags=0), never REPLACE_EXISTING.
        # Keep the existing Windows retry behavior for transient move failures.
        _rename_path(source, destination)
        return
    try:
        if sys.platform == 'darwin':
            native = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True).renamex_np
            native.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            native.restype = ctypes.c_int
            ctypes.set_errno(0)
            # Darwin sys/stdio.h: RENAME_EXCL = 0x00000004.
            status = native(os.fsencode(source), os.fsencode(destination), 0x4)
            error = ctypes.get_errno()
        elif sys.platform.startswith('linux'):
            native = ctypes.CDLL(None, use_errno=True).renameat2
            native.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            native.restype = ctypes.c_int
            source_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                destination_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    ctypes.set_errno(0)
                    # Linux uapi/linux/fs.h: RENAME_NOREPLACE = (1 << 0).
                    status = native(source_fd, os.fsencode(source.name), destination_fd, os.fsencode(destination.name), 1)
                    error = ctypes.get_errno()
                finally:
                    os.close(destination_fd)
            finally:
                os.close(source_fd)
        else:
            raise OSError(errno.ENOTSUP, 'atomic no-replace restore unavailable on this platform')
    except AttributeError as unavailable:
        raise OSError(errno.ENOTSUP, 'atomic no-replace restore symbol unavailable') from unavailable
    if status != 0:
        code = error or errno.EIO
        raise OSError(code, 'atomic no-replace restore failed: '+os.strerror(code), str(source), None, str(destination))


def _retire(path, *, ownership, backup_root, platform, source_root, pending_root, baseline=None,
            link_target=None, kind='skill', evidence=None):
    from uninstall_cleanup import _platforms_referencing_target, release_target_records

    path = Path(path).absolute()
    pending_root = Path(pending_root)
    previous = _work_for(path, backup_root, platform, kind)
    if previous:
        work_path, work = previous
    else:
        others = _platforms_referencing_target(path,
            current_manifest=pending_root/'install-state'/f'{platform}.json', current_platform=platform)
        if others:
            release_target_records(root=pending_root, platform=platform, destination=path, kind=kind, provider='core')
            return dict(action='retired-incomplete', path=str(path), ownership=ownership,
                        reason='shared-retired-asset-still-discoverable', shared_platforms=others,
                        records_released=True)
        identifier = uuid.uuid4().hex
        backup = Path(backup_root).absolute()/identifier/path.name
        work_path = backup.parent/'work.json'
        entry_identity = _entry_identity(path)
        identity = content_identity(path)
        current_hash = (hashlib.sha256(str(path.readlink()).encode()).hexdigest() if link_target is not None else
                        hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else _digest(_content_hashes(path)))
        work = dict(schema_version=1, id=identifier, phase='prepared', platform=platform, provider='core', kind=kind,
                    source_path=str(path), backup_path=str(backup), ownership=ownership,
                    evidence=evidence or {}, content_identity=identity, entry_identity=entry_identity,
                    current_hash=current_hash,
                    snapshot_hash=_digest(baseline), pending_id='retired-'+identifier,
                    created_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
        if link_target is not None:
            work['link_target'] = link_target
        _write_work(work_path, work)
    backup = Path(work['backup_path'])
    result = dict(action='retired-managed', path=str(path), backup_path=str(backup), work_path=str(work_path),
                  ownership=work['ownership'], platform=platform, provider='core', kind=kind,
                  content_identity=work['content_identity'])
    if 'link_target' in work:
        result.update(link_target=work['link_target'], current_hash=work['current_hash'])
    source_present, backup_present = _present(path), _present(backup)
    if source_present and backup_present:
        return dict(result, action='retired-incomplete', reason='retirement-path-collision')
    if not source_present and not backup_present:
        return dict(result, action='absent' if work['phase'] == 'complete' else 'retired-incomplete',
                    reason='retirement-copy-absent', records_released=work['phase'] == 'complete')
    observed = path if source_present else backup
    if _entry_identity(observed) != work['entry_identity']:
        return dict(result, action='retired-incomplete', reason='retirement-entry-changed')
    if content_identity(observed) != work['content_identity']:
        return dict(result, action='retired-incomplete', reason='retirement-content-changed')
    if source_present:
        if work['phase'] != 'prepared':
            return dict(result, action='retired-incomplete', reason='retirement-original-recreated')
        others = _platforms_referencing_target(path,
            current_manifest=pending_root/'install-state'/f'{platform}.json', current_platform=platform)
        if others:
            release_target_records(root=pending_root, platform=platform, destination=path, kind=kind, provider='core')
            return dict(result, action='retired-incomplete', reason='shared-retired-asset-still-discoverable',
                        shared_platforms=others, records_released=True)
        # Check entry parents: an owned link's source target is never mutated.
        assert_mutation_path(path.parent)
        assert_mutation_path(backup.parent)
        _rename_path(path, backup)
    if work['phase'] == 'complete':
        return dict(result, records_released=True)
    event = pending_root/'install-state'/f'{platform}-events.jsonl'
    review_committed = False
    try:
        if work['phase'] == 'prepared':
            _audit(event, result, 'moved', must_write=True)
            if work['ownership'] == OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED:
                entry = dict(id=work['pending_id'], platform=platform,
                    skill=path.stem if kind == 'command' else path.name,
                    source_path=str(path), backup_path=str(backup), snapshot_hash=work['snapshot_hash'],
                    current_hash=work['current_hash'], change_kind='retired-managed',
                    reason='retired-managed-external-changes-preserved', ownership=work['ownership'],
                    decided=False, decision=None, created_at=work['created_at'])
                result['pending_entry_added'] = _pending(entry, pending_root, platform)
                review_committed = True
            work['phase'] = 'records-started'
            _write_work(work_path, work)
        release_target_records(root=pending_root, platform=platform, destination=path, kind=kind, provider='core')
        work['phase'] = 'complete'
        _write_work(work_path, work)
        return dict(result, records_released=True)
    except Exception as original:
        original.add_note(f'retirement paths: source={path}; backup={backup}; work={work_path}')
        # A review may have committed just before its writer reported failure.
        # Unreadable review state also keeps the backup; it cannot prove restoration safe.
        if work['phase'] != 'prepared' or review_committed:
            raise
        try:
            entries = _read_pending(pending_root/'pending-merges'/platform/'manifest.json')['entries']
            if any(entry.get('id') == work['pending_id'] for entry in entries):
                raise original
        except (OSError, ValueError):
            raise original
        try:
            _restore_no_replace(backup, path)
        except Exception as rollback:
            failure = dict(result, rollback_error=str(rollback),
                           rollback_status='collision' if isinstance(rollback, FileExistsError) else 'failed')
            _audit(event, failure, 'rollback-failed')
            raise RuntimeError(f'retirement failed for active entry {path}; recovery backup remains at {backup}; rollback failed: {rollback}') from original
        _audit(event, result, 'rolled-back')
        raise


def _recorded_source(path, pending_root, platform):
    state_path = Path(pending_root)/'install-state'/f'{platform}.json'
    state = read_state(state_path)
    if state is None:
        return None
    if state.get('platform', platform) != platform:
        raise ValueError(f'install-state platform mismatch: {state_path}')
    matches = [row for row in state['targets'] if Path(row['dest_path']).absolute() == path
               and row['target_name'] == path.name and row.get('owner') != 'addon' and not row.get('addon_id')]
    if len(matches) > 1:
        raise ValueError(f'ambiguous retired asset inventory: {path}')
    return matches[0] if matches else None


def retire_skill(path, *, platform, source_root, pending_root):
    path = Path(path).absolute()
    if path.name not in RETIRED_SKILLS:
        raise ValueError('asset is not a retired Core skill')
    backup_root = Path(pending_root)/'deprecated-skill-backups'
    if _work_for(path, backup_root, platform, 'skill'):
        return _retire(path, ownership=None, backup_root=backup_root, platform=platform,
                       source_root=source_root, pending_root=pending_root)
    if not _present(path):
        return dict(action='absent', path=str(path))
    classification = classify_skill_root(path, expected_asset_id=path.name, repo_root=Path(source_root), whole_copy=True)
    recorded = _recorded_source(path, pending_root, platform)
    evidence = dict(classification_reason=classification.reason)
    if path.is_symlink():
        target = path.resolve(strict=False)
        expected = (Path(source_root)/path.name).resolve(strict=False)
        recorded_source = recorded.get('source_path') if recorded else None
        if target == expected or (isinstance(recorded_source, str) and recorded_source
                                 and target == Path(recorded_source).resolve(strict=False)):
            evidence.update(source_path=recorded_source or str(expected), install_target=recorded)
            return _retire(path, ownership=OWNERSHIP_GHOST_ALICE_MANAGED,
                backup_root=backup_root, platform=platform,
                source_root=source_root, pending_root=pending_root, link_target=str(path.readlink()), evidence=evidence)
        return dict(action='preserved', path=str(path), ownership=classification.ownership, reason='unproven-retired-link')
    if classification.ownership not in {OWNERSHIP_GHOST_ALICE_MANAGED, OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED}:
        return dict(action='preserved', path=str(path), ownership=classification.ownership, reason=classification.reason)
    marker = classification.marker or {}
    if marker.get('owner') != 'ghost-alice' or marker.get('addon_id') or marker.get('provided_kind') != 'skill':
        return dict(action='preserved', path=str(path), reason='unproven-retired-core-marker')
    previous_source = marker.get('source_repo')
    recorded_source = recorded.get('source_path') if recorded else None
    if not isinstance(previous_source, str) or not previous_source:
        return dict(action='preserved', path=str(path), reason='unproven-retired-source')
    if (Path(previous_source).resolve() != Path(source_root).resolve()
            and (not isinstance(recorded_source, str) or not recorded_source
                 or Path(recorded_source).resolve() != (Path(previous_source)/path.name).resolve())):
        return dict(action='preserved', path=str(path), reason='unproven-retired-source')
    if marker.get('platform') != platform and recorded is None:
        return dict(action='preserved', path=str(path), reason='unproven-retired-platform')
    evidence.update(source_repo=previous_source, source_commit=marker.get('source_commit'), install_target=recorded)
    return _retire(path, ownership=classification.ownership,
        backup_root=backup_root, platform=platform,
        source_root=source_root, pending_root=pending_root, baseline=marker, evidence=evidence)


def retire_command(path, *, source_root, pending_root=None):
    path = Path(path).absolute()
    pending_root = Path(pending_root) if pending_root is not None else ghost_root()
    backup_root = path.parent.parent/'retired-command-backups'
    if path.name != 'jailbreak-detector.md':
        return {'action':'preserved', 'path':str(path)}
    if _work_for(path, backup_root, 'claude', 'command'):
        return _retire(path, ownership=None, backup_root=backup_root, platform='claude',
                       source_root=source_root, pending_root=pending_root, kind='command')
    if not _present(path):
        return dict(action='absent', path=str(path))
    if not path.is_file() or path.is_symlink():
        return {'action':'preserved', 'path':str(path), 'reason':'unknown-command-ownership'}
    original = '@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\n'
    content = path.read_text(encoding='utf-8')
    if not content.startswith('@jailbreak-detector/SKILL.md\n') or '$ARGUMENTS' not in content:
        return {'action':'preserved', 'path':str(path), 'reason':'unknown-command-ownership'}
    ownership = OWNERSHIP_GHOST_ALICE_MANAGED if content.rstrip('\n') == original.rstrip('\n') else OWNERSHIP_EXTERNALLY_MODIFIED_MANAGED
    return _retire(path, ownership=ownership, backup_root=backup_root,
        platform='claude', source_root=source_root, pending_root=pending_root, baseline=original, kind='command',
        evidence=dict(classification_reason='retired-command-template'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skills-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--ghost-root', type=Path, required=True)
    parser.add_argument('--platform', choices=('codex','claude'), required=True)
    parser.add_argument('--commands-root', type=Path)
    args = parser.parse_args(argv)
    tasks = [(retire_skill, args.skills_root/name, dict(platform=args.platform,
              source_root=args.source_root, pending_root=args.ghost_root)) for name in RETIRED_SKILLS]
    if args.platform == 'claude' and args.commands_root is not None:
        tasks.append((retire_command, args.commands_root/'jailbreak-detector.md',
                      dict(source_root=args.source_root, pending_root=args.ghost_root)))
    incomplete = False
    for operation, path, kwargs in tasks:
        try:
            result = operation(path, **kwargs)
        except (OSError, ValueError, RuntimeError) as error:
            result = dict(action='retired-incomplete', path=str(path), reason='retirement-failed',
                          source_exists=_present(path), errors=[str(error), *getattr(error, '__notes__', [])])
        incomplete = incomplete or result['action'] == 'retired-incomplete'
        print(json.dumps(result, sort_keys=True))
    return 1 if incomplete else 0


if __name__ == '__main__':
    raise SystemExit(main())
