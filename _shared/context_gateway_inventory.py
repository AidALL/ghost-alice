"""Read one fixed installer inventory without choosing or applying changes."""
from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from context_gateway_model import Inventory


class _Collector:
    def __init__(self, io):
        self.io, self.files = io, {}

    def file(self, path, *, content=True):
        path = Path(path)
        if path not in self.files:
            self.files[path] = self.io.snapshot(path, content=content)
        return self.files[path]

    def finish(self, **kwargs):
        return Inventory(MappingProxyType(dict(self.files)), **kwargs)


def installation(request, io):
    """The service files, the state directory's entries, the ledger prerequisite and the runtime program
    a registration reads; the registration writes no module, so the runtime is read only for presence."""
    c = _Collector(io)
    for path in (request.config, request.agent, request.state_path, request.state_dir / 'installed-files.json'):
        c.file(path)
    for name in ('usage.jsonl', 'service.log'):
        c.file(request.state_dir / name, content=False)
    c.file(request.program, content=False)
    prerequisites = {request.ledger / 'ghost-state.sqlite3'}
    # The registration writes through links, so its containment check also compares the resolved paths.
    resolved = {path: path.resolve() for path in (request.state_dir, request.runtime_root)}
    return c.finish(prerequisites=frozenset(p for p in prerequisites if p.is_file()),
                    state_entries=io.children(request.state_dir), resolved=MappingProxyType(resolved))


def registration(service, io):
    """The three service files the install record's `service` section names, for a detach."""
    c = _Collector(io)
    for key in ('config_path', 'launch_agent', 'state_path'):
        c.file(Path(service[key]))
    return c.finish()
