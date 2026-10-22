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
import sys
from datetime import datetime, timezone
import uuid

from installer_assets import _content_hashes, classify_skill_root, OWNERSHIP_GHOST_ALICE_MANAGED, OWNERSHIP_USER_MODIFIED_MANAGED
from install_destination import assert_mutation_path, ghost_root
from install_transaction import _rename_path
import state_retention

RETIRED_SKILLS = ('jailbreak-detector',)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _pending(entry, root, platform, source_root):
    helpers = str(Path(source_root)/'merge-companion/scripts')
    sys.path.insert(0, helpers)
    try:
        from manifest_io import append_entry_if_absent
        manifest = Path(root)/'pending-merges'/platform/'manifest.json'
        assert_mutation_path(manifest)
        return append_entry_if_absent(manifest, entry, ['platform', 'source_path', 'backup_path', 'current_hash', 'change_kind'])
    finally:
        sys.path.remove(helpers)


def _audit(event, result, phase):
    state_retention.append('install-state-events', event, json.dumps(dict(result, phase=phase, ts=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')), sort_keys=True)+'\n')


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


def _retire(path, *, ownership, backup_root, platform, source_root, pending_root, baseline=None, link_target=None):
    # Renaming an owned link entry mutates its parent, never its source target.
    assert_mutation_path(path.parent if link_target is not None else path)
    backup = Path(backup_root)/uuid.uuid4().hex/path.name
    assert_mutation_path(backup)
    backup.parent.mkdir(parents=True)
    # A same-filesystem rename preserves all bytes and links. Failure leaves the
    # active asset untouched; no copy/delete or purge fallback exists.
    content_hash = hashlib.sha256(str(path.readlink()).encode()).hexdigest() if link_target is not None else hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else _digest(_content_hashes(path))
    asset_name = path.stem if path.is_file() else path.name
    result = dict(action='retired-managed', path=str(path), backup_path=str(backup), ownership=ownership)
    if link_target is not None:
        result['link_target'] = link_target
        result['current_hash'] = content_hash
    event = Path(pending_root)/'install-state'/f'{platform}-events.jsonl'
    _rename_path(path, backup)
    try:
        _audit(event, result, 'moved')
        if ownership == OWNERSHIP_USER_MODIFIED_MANAGED:
            entry = dict(id='retired-'+uuid.uuid4().hex, platform=platform, skill=asset_name,
                source_path=str(path), backup_path=str(backup), snapshot_hash=_digest(baseline),
                current_hash=content_hash,
                change_kind='retired-managed', reason='retired-managed-user-changes-preserved',
                ownership=ownership, decided=False, decision=None,
                created_at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
            # Last fallible persistence step: a committed entry always points to
            # the preserved backup, with no later restore that could orphan it.
            result['pending_entry_added'] = _pending(entry, pending_root, platform, source_root)
        return result
    except Exception as original:
        try:
            _restore_no_replace(backup, path)
        except Exception as rollback:
            failure = dict(result, rollback_error=str(rollback),
                           rollback_status='collision' if isinstance(rollback, FileExistsError) else 'failed')
            try:
                _audit(event, failure, 'rollback-failed')
            except Exception:
                pass  # Logging cannot replace the original persistence cause.
            raise RuntimeError(f'retirement failed for active entry {path}; recovery backup remains at {backup}; rollback failed: {rollback}') from original
        try:
            _audit(event, result, 'rolled-back')
        except Exception:
            pass  # Preserve the original audit failure after restoring bytes.
        raise


def retire_skill(path, *, platform, source_root, pending_root):
    path = Path(path)
    if path.name not in RETIRED_SKILLS:
        raise ValueError('asset is not a retired Core skill')
    classification = classify_skill_root(path, expected_asset_id=path.name, repo_root=Path(source_root))
    if path.is_symlink():
        # A source removal turns a previously managed link into a broken link.
        # Only its exact supplied Core target proves this retired entry owned.
        target = path.resolve(strict=False)
        expected = (Path(source_root)/path.name).resolve(strict=False)
        if target == expected:
            return _retire(path, ownership=OWNERSHIP_GHOST_ALICE_MANAGED,
                backup_root=Path(pending_root)/'deprecated-skill-backups', platform=platform,
                source_root=source_root, pending_root=pending_root, link_target=str(path.readlink()))
        return dict(action='preserved', path=str(path), ownership=classification.ownership, reason='unproven-retired-link')
    if classification.ownership not in {OWNERSHIP_GHOST_ALICE_MANAGED, OWNERSHIP_USER_MODIFIED_MANAGED}:
        return dict(action='preserved', path=str(path), ownership=classification.ownership, reason=classification.reason)
    return _retire(path, ownership=classification.ownership,
        backup_root=Path(pending_root)/'deprecated-skill-backups', platform=platform,
        source_root=source_root, pending_root=pending_root, baseline=classification.marker)


def retire_command(path, *, source_root, pending_root=None):
    path = Path(path)
    if path.name != 'jailbreak-detector.md' or not path.is_file() or path.is_symlink():
        return {'action':'preserved', 'path':str(path)}
    original = '@jailbreak-detector/SKILL.md\n\n$ARGUMENTS\n'
    content = path.read_text(encoding='utf-8')
    if not content.startswith('@jailbreak-detector/SKILL.md\n') or '$ARGUMENTS' not in content:
        return {'action':'preserved', 'path':str(path), 'reason':'unknown-command-ownership'}
    ownership = OWNERSHIP_GHOST_ALICE_MANAGED if content.rstrip('\n') == original.rstrip('\n') else OWNERSHIP_USER_MODIFIED_MANAGED
    return _retire(path, ownership=ownership, backup_root=path.parent.parent/'retired-command-backups',
        platform='claude', source_root=source_root, pending_root=pending_root or ghost_root(), baseline=original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skills-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--ghost-root', type=Path, required=True)
    parser.add_argument('--platform', choices=('codex','claude'), required=True)
    parser.add_argument('--commands-root', type=Path)
    args = parser.parse_args()
    for name in RETIRED_SKILLS:
        print(json.dumps(retire_skill(args.skills_root/name, platform=args.platform,
            source_root=args.source_root, pending_root=args.ghost_root), sort_keys=True))
    if args.platform == 'claude' and args.commands_root is not None:
        print(json.dumps(retire_command(args.commands_root/'jailbreak-detector.md',
            source_root=args.source_root, pending_root=args.ghost_root), sort_keys=True))


if __name__ == '__main__':
    main()
