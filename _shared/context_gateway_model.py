"""Installer values and effects contract; no filesystem or native operations."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping, Protocol

LABEL = 'org.ghost-alice.context-gateway'
TOP_BEGIN = '# Ghost-ALICE managed context selection begin'
TOP_END = '# Ghost-ALICE managed context selection end'
TABLE_BEGIN = '# Ghost-ALICE managed context provider begin'
TABLE_END = '# Ghost-ALICE managed context provider end'


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, indent=2).encode()


class GatewayBlocked(ValueError):
    def __init__(self, detail, *, code='GATEWAY_PRECONDITION', exit_code=2):
        super().__init__(detail)
        self.code, self.exit_code, self.phase = code, exit_code, 'preflight'
        self.outcome = 'held' if exit_code == 3 else 'blocked'


class GatewayFailure(RuntimeError):
    def __init__(self, detail, *, phase, restored=False):
        super().__init__(detail)
        self.phase = phase
        self.outcome = 'restored' if restored else 'held'
        self.code = 'GATEWAY_OPERATION_RESTORED' if restored else 'GATEWAY_RECOVERY_UNCONFIRMED'
        self.exit_code = 4 if restored else 5


class GatewayServiceError(RuntimeError):
    code = 'GATEWAY_SERVICE_UNCONFIRMED'
    exit_code = 3
    outcome = 'held'

    def __init__(self, detail, *, phase='preflight'):
        super().__init__(detail)
        self.phase = phase


@dataclass(frozen=True)
class Artifact:
    path: Path
    resolved: Path
    kind: str
    raw: bytes | None = None
    mode: int | None = None
    identity: tuple[int, int] | None = None

    @property
    def exists(self):
        return self.kind != 'missing'


@dataclass(frozen=True)
class ServiceObservation:
    presence: str
    agent: Path | None = None
    returncode: int | None = None
    detail: str = ''


@dataclass(frozen=True)
class Request:
    """One registration (`install`) or unregistration (`detach`) of the gateway service.

    `runtime_root` is the folder the install record names for the runtime `_shared` the service runs
    from; the registration writes nothing under it.
    """
    operation: str
    home: Path
    platform: str
    runtime_root: Path | None = None
    codex: Path | None = None
    ledger: Path | None = None
    state_dir: Path | None = None
    agent: Path | None = None
    claude: Path | None = None
    strict_log: Path | None = None

    @property
    def program(self):
        return self.runtime_root / '_shared' / 'context_gateway.py'

    @property
    def config(self):
        return self.codex / 'config.toml'

    @property
    def state_path(self):
        return self.state_dir / 'state.json'


@dataclass(frozen=True)
class Inventory:
    files: Mapping[Path, Artifact]
    prerequisites: frozenset[Path] = frozenset()
    state_entries: tuple[Path, ...] = ()

    def file(self, path):
        return self.files[Path(path)]


@dataclass(frozen=True)
class Mutation:
    before: Artifact
    after: bytes | None
    mode: int = 0o600


@dataclass(frozen=True)
class Preparation:
    path: Path
    raw: bytes
    mutable: bool = False


@dataclass(frozen=True)
class Plan:
    """The ordered effects of one operation.

    `record_change` says the commit phase writes the install record's `service` section:
    `record_after` (None deletes it), and a restore puts `record_before` back.
    """
    operation: str
    domain: str
    state_dir: Path
    agent: Path
    guards: tuple[Artifact, ...]
    staged: tuple[Mutation, ...]
    committed: tuple[Mutation, ...]
    retired: tuple[Mutation, ...]
    preparation: tuple[Preparation, ...]
    prior_state: Mapping | None
    candidate: Mapping | None
    result: Mapping
    archive: Path | None = None
    record_change: bool = False
    record_before: Mapping | None = None
    record_after: Mapping | None = None


class Effects(Protocol):
    """The transaction receives effects, never imports a concrete adapter."""
    state: Callable[[str], str | ServiceObservation]
    stop: Callable[[str], None]
    start: Callable[[str, Path], None]
    ready: Callable[[Mapping], bool]
    write: Callable[[Path, bytes, int], None]
    record: Callable[[Mapping | None], None]

    def snapshot(self, path: Path, *, content: bool = True) -> Artifact: ...
    def observe(self, domain: str) -> ServiceObservation: ...
    def mkdir(self, path: Path, mode: int = 0o777) -> None: ...
    def children(self, path: Path) -> tuple[Path, ...]: ...
    def unlink(self, path: Path) -> None: ...
    def rmdir(self, path: Path) -> None: ...
