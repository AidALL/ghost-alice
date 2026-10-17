"""Read one fixed installer inventory without choosing or applying changes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
from types import MappingProxyType

from context_gateway_model import COMPANIONS, Inventory, LABEL, MODULES, ServiceObservation, TOP_BEGIN, TABLE_BEGIN
from hook_definitions import PROVIDER


class _Collector:
    def __init__(self, io):
        self.io, self.files, self.resolved = io, {}, {}

    def path(self, path):
        path = Path(path)
        if path not in self.resolved:
            self.resolved[path] = path.resolve()
        return path

    def file(self, path, *, content=True):
        path = self.path(path)
        if path not in self.files:
            self.files[path] = self.io.snapshot(path, content=content)
        return self.files[path]

    def document(self, path, *, plist=False):
        item = self.file(path)
        if item.kind != 'file':
            return {}
        try:
            parsed = plistlib.loads(item.raw) if plist else json.loads(item.raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, plistlib.InvalidFileException):
            return {}

    def coordinates(self, document):
        for key in ('runtime_shared_dir', 'ledger_root', 'launch_agent', 'config_path', 'state_path',
                    'codex_dir', 'claude_dir', 'strict_log_root', 'usage_log'):
            value = document.get(key)
            if isinstance(value, str) and Path(value).is_absolute():
                self.path(value)
        runtime = document.get('runtime_shared_dir')
        if isinstance(runtime, str) and Path(runtime).is_absolute():
            self.runtime(Path(runtime))

    def runtime(self, runtime):
        self.path(runtime)
        for name in MODULES:
            self.path(runtime / name)
        for relative in COMPANIONS:
            self.path(runtime.parent / relative)

    def finish(self, **kwargs):
        return Inventory(MappingProxyType(dict(self.files)), MappingProxyType(dict(self.resolved)), **kwargs)


def installation(request, io):
    c = _Collector(io)
    for path in (request.home, request.source, request.codex, request.ledger, request.state_dir,
                 request.agent, request.registration.parent / 'runtime', *request.shared):
        c.path(path)
    for path in (request.codex / 'config.toml', request.agent, request.registration,
                 request.state_dir / 'state.json', request.state_dir / 'installation.json',
                 request.state_dir / 'installed-files.json'):
        c.file(path)
    for name in ('usage.jsonl', 'service.log'):
        c.file(request.state_dir / name, content=False)
    prerequisites = {request.ledger / 'ghost-state.sqlite3'}
    for shared in request.shared:
        prerequisites.add(shared / 'completion_assessment.py')
        for name in MODULES:
            c.file(request.source / '_shared' / name)
            c.file(shared / name)
        for relative in COMPANIONS:
            c.file(request.source / relative)
            c.file(shared.parent / relative)
    return c.finish(prerequisites=frozenset(p for p in prerequisites if p.is_file()),
                    state_entries=io.children(request.state_dir))


def removal(request, io):
    c = _Collector(io)
    for path in (*request.removing, request.registration, request.home):
        c.path(path)
    config = c.file(request.codex / 'config.toml')
    registration = c.file(request.registration)
    if registration.kind == 'missing':
        default_agent = c.file(request.home / 'Library/LaunchAgents' / (LABEL + '.plist'))
        # A dotfile link is not a gateway ownership signal. Inspect its target
        # only to classify provider content; neither leaf becomes writable.
        provider = c.file(config.resolved) if config.kind == 'symlink' else config
        text = provider.raw.decode(errors='replace') if provider.raw else ''
        signal = (provider.kind not in ('file', 'missing')
                  or any(x in text for x in (PROVIDER, TOP_BEGIN, TABLE_BEGIN)) or default_agent.exists)
        service = None if signal else (io.observe(f'gui/{os.getuid()}') if request.platform == 'darwin' else ServiceObservation('absent'))
        if service is not None and service.agent is not None:
            definition = c.document(service.agent, plist=True)
            argv = definition.get('ProgramArguments')
            if isinstance(argv, list):
                for value in argv:
                    if isinstance(value, str) and Path(value).is_absolute():
                        c.path(value)
                if len(argv) == 5 and argv[3] == '--config' and isinstance(argv[4], str) and Path(argv[4]).is_absolute():
                    state = c.document(Path(argv[4]))
                    c.coordinates(state)
                    if isinstance(argv[2], str) and Path(argv[2]).is_absolute():
                        c.runtime(Path(argv[2]).parent)
            for key in ('WorkingDirectory', 'StandardOutPath', 'StandardErrorPath'):
                if isinstance(definition.get(key), str) and Path(definition[key]).is_absolute():
                    c.path(definition[key])
        return c.finish(service=service)
    binding = c.document(request.registration)
    path = binding.get('installation_path')
    if not isinstance(path, str) or not Path(path).is_absolute():
        return c.finish()
    receipt = c.document(Path(path))
    c.path(Path(path).parent)
    c.coordinates(receipt)
    for key in ('config_path', 'launch_agent', 'state_path'):
        path = receipt.get(key)
        if isinstance(path, str) and Path(path).is_absolute():
            c.file(Path(path))
    agent_path = receipt.get('launch_agent')
    if isinstance(agent_path, str) and Path(agent_path).is_absolute():
        definition = c.document(Path(agent_path), plist=True)
        argv = definition.get('ProgramArguments')
        if isinstance(argv, list):
            for value in argv:
                if isinstance(value, str) and Path(value).is_absolute():
                    c.path(value)
        for key in ('WorkingDirectory', 'StandardOutPath', 'StandardErrorPath'):
            value = definition.get(key)
            if isinstance(value, str) and Path(value).is_absolute():
                c.path(value)
    state_path = receipt.get('state_path')
    if isinstance(state_path, str) and Path(state_path).is_absolute():
        c.coordinates(c.document(Path(state_path)))
    # Capture current component bytes when available. Their retained receipt
    # baselines remain policy inputs, not newly inferred overwrite authority.
    files = receipt.get('files')
    if isinstance(files, dict):
        for path in files:
            if isinstance(path, str) and Path(path).is_absolute():
                c.file(Path(path))
    return c.finish()


def replacement(request, io, targets):
    """Read candidate files from completed stages, not from moving sources."""
    observed = removal(request, io)
    c = _Collector(io)
    c.files.update(observed.files)
    c.resolved.update(observed.resolved)
    candidates = {}
    binding = c.document(request.registration)
    receipt_path = binding.get('installation_path')
    if not isinstance(receipt_path, str) or not Path(receipt_path).is_absolute():
        return observed, candidates
    receipt = c.document(Path(receipt_path))
    paths = set(receipt.get('files', {})) if isinstance(receipt.get('files'), dict) else set()
    runtime = receipt.get('runtime_shared_dir')
    if isinstance(runtime, str) and Path(runtime).is_absolute():
        shared = receipt.get('shared_dirs', [runtime])
        if isinstance(shared, list) and all(isinstance(p, str) and Path(p).is_absolute() for p in shared):
            for root in map(Path, shared):
                paths.update(str(root / name) for name in MODULES)
                paths.update(str(root.parent / relative) for relative in COMPANIONS)
    for path in paths:
        if not isinstance(path, str) or not Path(path).is_absolute():
            continue
        installed = Path(path)
        c.file(installed)
        for stage, destination in targets:
            destination = c.path(destination)
            pairs = ((installed, destination), (c.resolved[installed], c.resolved[destination]))
            for child, parent in pairs:
                if child == parent or parent in child.parents:
                    candidate = Path(stage) / child.relative_to(parent)
                    candidates[installed] = c.file(candidate)
                    break
            if installed in candidates:
                break
    return c.finish(service=observed.service), candidates
