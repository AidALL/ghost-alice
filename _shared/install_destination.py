"""Validated installation locations; transport grants no host or session authority.

Dependencies: Python 3.11+ standard library. The owned root is a containment
limit for isolated mutations, independently of native hook/project trust.
"""
from __future__ import annotations
import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path

_ACTIVE = ContextVar('ghost_alice_install_destination', default=None)
FIELDS = frozenset({'mode','owned_root','platform_dir','skills_dir','ghost_root','ledger_root','project_root','core_source_root','runtime_shared_dir'})
COORDINATES = {
    'GHOST_ALICE_GHOST_ROOT':'ghost_root', 'GHOST_ALICE_SESSION_INTENT_ROOT':'ledger_root',
    'GHOST_ALICE_CORE_ROOT':'core_source_root', 'GHOST_ALICE_PROJECT_CWD':'project_root',
    'GHOST_ALICE_PLATFORM_DIR':'platform_dir', 'GHOST_ALICE_SKILLS_DIR':'skills_dir',
}
LEGACY_OVERRIDES = ('GHOST_ALICE_HOOK_SHARED_DIR','GHOST_ALICE_RUNTIME_SHARED_DIR')

@dataclass(frozen=True)
class InstallDestination:
    mode: str
    owned_root: str
    platform_dir: str
    skills_dir: str
    ghost_root: str
    ledger_root: str
    project_root: str
    core_source_root: str
    runtime_shared_dir: str = ""

    def __post_init__(self):
        # Normalize the optional constructor default before instance/asdict use.
        # Supplied dictionary paths still pass the strict parse validation below.
        if self.runtime_shared_dir == "" and isinstance(self.ghost_root, str):
            object.__setattr__(self,"runtime_shared_dir",str(Path(self.ghost_root)/"runtime/current/_shared"))

    @classmethod
    def parse(cls, value):
        if isinstance(value, cls):
            value = asdict(value)
        if isinstance(value,dict):
            value = dict(value)
            value.setdefault("runtime_shared_dir",str(Path(value.get("ghost_root", "/unbound"))/"runtime/current/_shared"))
        if not isinstance(value, dict) or set(value) != FIELDS:
            raise ValueError('destination must contain exactly the documented location fields')
        if value['mode'] not in {'normal','isolated'}:
            raise ValueError('destination mode must be normal or isolated')
        for key in FIELDS - {'mode'}:
            if not isinstance(value[key],str) or not value[key].strip() or not Path(value[key]).is_absolute():
                raise ValueError(f'destination {key} must be an absolute path')
        result = cls(**value)
        if result.mode == 'isolated':
            for key in FIELDS - {'mode','core_source_root'}:
                result.assert_owned(Path(value[key]))
        return result

    def assert_owned(self, path):
        if self.mode == 'isolated':
            try:
                Path(path).resolve().relative_to(Path(self.owned_root).resolve())
            except (ValueError, RuntimeError) as error:
                raise ValueError(f'isolated destination escape: {path}') from error
        return Path(path)

    def binding(self):
        return dict(schema_version='ghost-alice-hook-binding.v1', destination=asdict(self))


def current():
    return _ACTIVE.get()


def ghost_root(home=None, env=None):
    active = current()
    source = os.environ if env is None else env
    value = active.ghost_root if active else source.get('GHOST_ALICE_GHOST_ROOT')
    return Path(value) if value else (Path(home) if home is not None else Path.home()) / '.ghost-alice'


def assert_mutation_path(path, *, env=None):
    active = current()
    if active:
        active.assert_owned(path)
    elif (source := os.environ if env is None else env).get('GHOST_ALICE_OWNED_ROOT'):
        root = Path(source['GHOST_ALICE_OWNED_ROOT']).resolve()
        try:
            Path(path).resolve().relative_to(root)
        except (ValueError, RuntimeError) as error:
            raise ValueError(f'isolated mutation escape: {path}') from error
    return Path(path)


def binding_environment(destination, env):
    destination = InstallDestination.parse(destination)
    result = dict(env)
    for key, field in COORDINATES.items():
        result[key] = getattr(destination,field)
    result['GHOST_ALICE_RUNTIME_SHARED_DIR'] = destination.runtime_shared_dir
    result['GHOST_ALICE_HOOK_SHARED_DIR'] = result['GHOST_ALICE_RUNTIME_SHARED_DIR']
    result['PYTHONDONTWRITEBYTECODE'] = '1'
    result['GHOST_ALICE_GOVERNANCE_EVENTS'] = str(Path(destination.ghost_root)/'governance-events.jsonl')
    result['GHOST_ALICE_AUTOPILOT_CWD'] = destination.project_root
    result.pop('GHOST_ALICE_AUTOPILOT_RUN_DIR',None)
    if destination.mode == 'isolated':
        result['GHOST_ALICE_OWNED_ROOT'] = destination.owned_root
        result['GHOST_ALICE_TEMP_ROOT'] = str(Path(destination.owned_root)/'tmp')
    else:
        result.pop('GHOST_ALICE_OWNED_ROOT',None)
    return result


@contextmanager
def use(destination, *, reject_legacy=False):
    resolved = InstallDestination.parse(destination)
    if reject_legacy and resolved.mode == 'isolated':
        for key in LEGACY_OVERRIDES:
            if os.environ.get(key):
                raise ValueError(f'isolated destination conflicts with legacy override {key}')
    token = _ACTIVE.set(resolved)
    keys = set(COORDINATES) | set(LEGACY_OVERRIDES) | {'GHOST_ALICE_OWNED_ROOT','GHOST_ALICE_TEMP_ROOT','PYTHONDONTWRITEBYTECODE','GHOST_ALICE_AUTOPILOT_CWD','GHOST_ALICE_AUTOPILOT_RUN_DIR','GHOST_ALICE_GOVERNANCE_EVENTS'}
    before = {key:os.environ.get(key) for key in keys}
    replacement = binding_environment(resolved,os.environ)
    for key in keys:
        if key in replacement: os.environ[key] = replacement[key]
        else: os.environ.pop(key,None)
    try:
        yield resolved
    finally:
        _ACTIVE.reset(token)
        for key,value in before.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key] = value


def encode_binding(binding):
    return base64.urlsafe_b64encode(json.dumps(binding,sort_keys=True,separators=(',',':')).encode()).decode()


def decode_binding(value):
    try:
        data = json.loads(base64.urlsafe_b64decode(value).decode())
    except Exception as error:
        raise ValueError('invalid hook binding encoding') from error
    if (not isinstance(data,dict) or set(data)-{'schema_version','destination','adapter'}
            or data.get('schema_version') != 'ghost-alice-hook-binding.v1'):
        raise ValueError('invalid hook binding schema')
    InstallDestination.parse(data.get('destination'))
    return data
