"""Read one fixed installer inventory without choosing or applying changes."""
from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from context_gateway_model import COMPANIONS, Inventory, MODULES


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


def registration(service, io):
    """The three service files the install record's `service` section names, for a detach."""
    c = _Collector(io)
    for key in ('config_path', 'launch_agent', 'state_path'):
        c.file(Path(service[key]))
    return c.finish()
