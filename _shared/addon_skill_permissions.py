"""Own only Core and addon Skill grants that this installer added to Claude settings.

The installer preserves preexisting allow rules and does not record who added them. Unknown legacy ownership is
reported for the human user to review, never converted into removal authority. This module stores
names, booleans and settings fingerprints, not settings content or credentials. Dependencies: standard library.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

import addon_registry
import install_destination


def _state_path():
    ghost = install_destination.ghost_root()
    path = ghost / 'addon-permissions/claude-v2.json'
    if not path.parent.resolve().is_relative_to(ghost.resolve()):
        raise ValueError('addon permission ownership escapes Ghost-ALICE root')
    install_destination.assert_mutation_path(path)
    return path


def _read(settings_file):
    path = _state_path()
    expected = str(Path(settings_file).resolve())
    if not os.path.lexists(path):
        legacy = path.with_name("claude.json")
        if os.path.lexists(legacy):
            path = legacy
    if not os.path.lexists(path):
        return {'schema_version': 2, 'settings_file': expected, 'addons': {}}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
        raise ValueError('invalid addon permission ownership file')
    data = json.loads(path.read_text())
    fields = {'schema_version', 'settings_file', 'addons'}
    if (not isinstance(data, dict) or not fields <= set(data) <= fields | {'pending_detach', 'core'}
            or type(data['schema_version']) is not int or data['schema_version'] not in (1, 2)
            or data['settings_file'] != expected or not isinstance(data['addons'], dict)):
        raise ValueError('invalid addon permission ownership binding')
    for addon_id, skills in [*data['addons'].items(), (None, data.get('core', {}))]:
        if addon_id is not None:
            addon_registry.validate_addon_id(addon_id)
        if not isinstance(skills, dict):
            raise ValueError('invalid addon permission skill ownership')
        for name, added in skills.items():
            addon_registry.validate_addon_id(name)
            if not isinstance(added, bool):
                raise ValueError('invalid addon permission addition evidence')
    if 'pending_detach' in data:
        pending = data['pending_detach']
        if (not isinstance(pending, dict)
                or set(pending) not in ({'addon_id', 'names', 'before', 'after'}, {'owner', 'names', 'before', 'after'})
                or ('owner' in pending and pending['owner'] != 'core')
                or ('addon_id' in pending and not isinstance(pending['addon_id'], str))
                or not isinstance(pending['names'], list) or not pending['names']
                or not all(isinstance(name, str) for name in pending['names'])
                or len(set(pending['names'])) != len(pending['names'])):
            raise ValueError('invalid pending addon permission detach')
        if 'addon_id' in pending:
            addon_registry.validate_addon_id(pending['addon_id'])
        ownership = data.get('core', {}) if pending.get('owner') == 'core' else data['addons'].get(pending['addon_id'], {})
        for name in pending['names']:
            addon_registry.validate_addon_id(name)
            if ownership.get(name) is not True:
                raise ValueError('pending addon permission detach lacks ownership')
        for key in ('before', 'after'):
            fingerprint = pending[key]
            if (not isinstance(fingerprint, str) or len(fingerprint) != 64
                    or any(char not in '0123456789abcdef' for char in fingerprint)):
                raise ValueError('invalid pending addon permission fingerprint')
        if pending['before'] == pending['after']:
            raise ValueError('pending addon permission detach has no effect')
    return data


def _save(data):
    """Prepare migration before committing the active receipt, restoring legacy on a failed commit."""
    path = _state_path()
    legacy = path.with_name('claude.json')
    data = dict(data, schema_version=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    saved_legacy = None
    try:
        if data['addons'] or data.get('core') or 'pending_detach' in data:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, encoding='utf-8') as out:
                temporary = Path(out.name)
                json.dump(data, out, sort_keys=True, indent=2)
                out.write('\n')
                out.flush()
                os.fsync(out.fileno())
        if os.path.lexists(legacy):
            descriptor, saved = tempfile.mkstemp(prefix='claude-v1-', suffix='.bak', dir=path.parent)
            os.close(descriptor)
            try:
                os.replace(legacy, saved)
            except Exception:
                Path(saved).unlink(missing_ok=True)
                raise
            saved_legacy = Path(saved)
        # This is the final fallible operation: callers can restore settings on
        # failure without a newly published receipt retaining removal authority.
        if temporary is not None:
            os.replace(temporary, path)
            temporary = None
        else:
            path.unlink(missing_ok=True)
    except Exception as error:
        if saved_legacy is not None:
            try:
                os.replace(saved_legacy, legacy)
            except Exception as restoration_error:
                raise ExceptionGroup(
                    f'Permission receipt commit failed; legacy receipt retained at {saved_legacy}',
                    [error, restoration_error],
                ) from error
        raise
    finally:
        if temporary is not None:
            primary_error = sys.exception()
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(f'Temporary permission receipt cleanup failed at {temporary}: {cleanup_error}')


def record_install(settings_file, targets, before_allow, after_allow, *, core_names=()):
    """After settings commit, retain earliest attribution across Core updates."""
    targets = list(targets)
    core_names = list(core_names)
    if not targets and not core_names:
        return
    data = _read(settings_file)
    if 'pending_detach' in data:
        raise ValueError('pending addon permission detach requires review or resume before install')
    before = set(x for x in before_allow if isinstance(x, str))
    after = set(x for x in after_allow if isinstance(x, str))
    grants = [(target.addon_id, target.name) for target in targets] + [(None, name) for name in core_names]
    for addon_id, name in grants:
        if addon_id is not None:
            addon_registry.validate_addon_id(addon_id)
        addon_registry.validate_addon_id(name)
        rule = f'Skill({name})'
        if rule not in after:
            continue
        skills = data.setdefault('core', {}) if addon_id is None else data['addons'].setdefault(addon_id, {})
        if rule not in before:
            skills[name] = True
        else:
            skills.setdefault(name, False)
    _save(data)


def _settings_fingerprint(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def detach(record, settings_file, settings, core_names, write_settings, *, dry_run=False):
    """Remove one installer-added occurrence; preserve all preexisting grants."""
    names = {e['name'] for e in record.get('provided', [])
             if isinstance(e, dict) and e.get('kind') == 'skill'
             and e.get('ownership') == 'addon' and isinstance(e.get('name'), str)}
    addon_id = addon_registry.validate_addon_id(record['addon_id'])
    return _detach(addon_id, names, settings_file, settings, core_names, write_settings, dry_run=dry_run)


def detach_core(names, settings_file, settings, write_settings, *, dry_run=False):
    """Remove recorded Core additions, including retired names; leave unknown legacy grants for review."""
    names = set(names) | set(_read(settings_file).get('core', {}))
    return _detach(None, names, settings_file, settings, set(), write_settings, dry_run=dry_run)


def _detach(addon_id, names, settings_file, settings, core_names, write_settings, *, dry_run=False):
    if not names:
        return []
    data = _read(settings_file)
    owners = data if addon_id is None else data['addons']
    owner_key = 'core' if addon_id is None else addon_id
    ownership = owners.get(owner_key, {})
    permissions = settings.get('permissions', {})
    if not isinstance(permissions, dict):
        raise ValueError('Claude permissions must be an object')
    allow = permissions.get('allow', [])
    if not isinstance(allow, list):
        raise ValueError('Claude permission allow is not a list')
    completed = set()
    pending = data.get('pending_detach')
    if pending is not None:
        # Read the effect itself: a writer may have committed before raising, and
        # the caller may still hold its old in-memory settings after that error.
        path = Path(settings_file)
        current = json.loads(path.read_text()) if path.exists() else {}
        fingerprint = _settings_fingerprint(current)
        if (pending.get('addon_id') != addon_id or not set(pending['names']).issubset(names)
                or fingerprint != _settings_fingerprint(settings)
                or fingerprint not in (pending['before'], pending['after'])):
            raise ValueError('pending addon permission detach requires review of current settings and selection')
        if fingerprint == pending['after']:
            completed.update(pending['names'])
            if not dry_run:
                for name in completed:
                    ownership.pop(name)
                if not ownership:
                    owners.pop(owner_key)
                data.pop('pending_detach')
                _save(data)
        elif set(pending['names']) & core_names:
            raise ValueError('pending addon permission detach requires review of Core grants')
    updated = list(allow)
    rows = []
    retired = set()
    removed = set()
    for name in sorted(names):
        addon_registry.validate_addon_id(name)
        rule = f'Skill({name})'
        if name not in completed and rule not in updated and name not in ownership:
            continue
        action = 'missing'
        if name in completed:
            action = 'removed'
        elif rule in updated:
            if name in core_names:
                action = 'refused'
            elif name not in ownership:
                action = 'manual-review'
            elif ownership[name] is False:
                action = 'preserved-existing'
            elif dry_run:
                action = 'would-remove'
            else:
                updated.remove(rule)
                removed.add(name)
                action = 'removed'
        rows.append({'kind': 'permission', 'name': rule, 'action': action})
        if action in {'removed', 'missing', 'preserved-existing'} and name in ownership:
            retired.add(name)
    if dry_run:
        return rows
    if updated != allow:
        updated_settings = dict(settings, permissions=dict(permissions, allow=updated))
        data['pending_detach'] = {
            **({'owner': 'core'} if addon_id is None else {'addon_id': addon_id}), 'names': sorted(removed),
            'before': _settings_fingerprint(settings),
            'after': _settings_fingerprint(updated_settings),
        }
        # Persist the recovery decision before the effect. Until finalization,
        # no consumer may treat the booleans alone as fresh removal authority.
        _save(data)
        write_settings(settings_file, updated_settings, dry_run=False)
        settings['permissions'] = updated_settings['permissions']
    if retired:
        for name in retired:
            ownership.pop(name, None)
        if not ownership:
            owners.pop(owner_key, None)
        data.pop('pending_detach', None)
        _save(data)
    return rows
