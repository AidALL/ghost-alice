"""Own only addon Skill grants that this installer added to Claude settings.

Preexisting grants remain user/previous-owner data. Unknown legacy ownership is
reported for review, never converted into removal authority. This module stores
names and booleans, not prompts or credentials. Dependencies: standard library.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

import addon_registry
import install_destination


def _state_path():
    ghost = install_destination.ghost_root()
    path = ghost / 'addon-permissions/claude.json'
    if not path.parent.resolve().is_relative_to(ghost.resolve()):
        raise ValueError('addon permission ownership escapes Ghost-ALICE root')
    install_destination.assert_mutation_path(path)
    return path


def _read(settings_file):
    path = _state_path()
    expected = str(Path(settings_file).resolve())
    if not os.path.lexists(path):
        return {'schema_version': 1, 'settings_file': expected, 'addons': {}}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
        raise ValueError('invalid addon permission ownership file')
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'settings_file', 'addons'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or data['settings_file'] != expected or not isinstance(data['addons'], dict)):
        raise ValueError('invalid addon permission ownership binding')
    for addon_id, skills in data['addons'].items():
        addon_registry.validate_addon_id(addon_id)
        if not isinstance(skills, dict):
            raise ValueError('invalid addon permission skill ownership')
        for name, added in skills.items():
            addon_registry.validate_addon_id(name)
            if not isinstance(added, bool):
                raise ValueError('invalid addon permission addition evidence')
    return data


def _save(data):
    path = _state_path()
    if not data['addons']:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, encoding='utf-8') as out:
            temporary = Path(out.name)
            json.dump(data, out, sort_keys=True, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_install(settings_file, targets, before_allow, after_allow):
    """After settings commit, retain earliest attribution across Core updates."""
    targets = list(targets)
    if not targets:
        return
    data = _read(settings_file)
    before = set(x for x in before_allow if isinstance(x, str))
    after = set(x for x in after_allow if isinstance(x, str))
    for target in targets:
        addon_registry.validate_addon_id(target.addon_id)
        addon_registry.validate_addon_id(target.name)
        rule = f'Skill({target.name})'
        if rule not in after:
            continue
        skills = data['addons'].setdefault(target.addon_id, {})
        if rule not in before:
            skills[target.name] = True
        else:
            skills.setdefault(target.name, False)
    _save(data)


def detach(record, settings_file, settings, core_names, write_settings, *, dry_run=False):
    """Remove one installer-added occurrence; preserve all preexisting grants."""
    names = {e['name'] for e in record.get('provided', [])
             if isinstance(e, dict) and e.get('kind') == 'skill'
             and e.get('ownership') == 'addon' and isinstance(e.get('name'), str)}
    if not names:
        return []
    addon_id = addon_registry.validate_addon_id(record['addon_id'])
    data = _read(settings_file)
    ownership = data['addons'].get(addon_id, {})
    permissions = settings.get('permissions', {})
    if not isinstance(permissions, dict):
        raise ValueError('Claude permissions must be an object')
    allow = permissions.get('allow', [])
    if not isinstance(allow, list):
        raise ValueError('Claude permission allow is not a list')
    updated = list(allow)
    rows = []
    for name in sorted(names):
        addon_registry.validate_addon_id(name)
        rule = f'Skill({name})'
        if rule not in updated and name not in ownership:
            continue
        action = 'missing'
        if rule in updated:
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
                action = 'removed'
        rows.append({'kind': 'permission', 'name': rule, 'action': action})
    if dry_run:
        return rows
    if updated != allow:
        permissions['allow'] = updated
        write_settings(settings_file, settings, dry_run=False)
    if addon_id in data['addons'] and not any(row['action'] in {'manual-review', 'refused'} for row in rows):
        data['addons'].pop(addon_id, None)
        _save(data)
    return rows
