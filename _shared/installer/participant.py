"""The participant interface of a publish.

A publish names the install locations it replaces; a participant is an object the transaction calls at
four points around that replacement: `before_publish` once the staged copies are ready and before any
install location changes, `after_publish` once the files and the install record are published,
`before_restore` before a failed publish puts the originals back, and `after_restore` once they are back.
Each point receives the same `PublishContext`. `install_transaction.publish` calls the participants it is
given and knows nothing else about them; the participants decide from the context whether the publish
concerns them. `NullParticipant` does nothing at every point.

Dependencies: Python standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PublishContext:
    """What one publish tells its participants: the record root, the host, the transaction id and the
    install locations the publish replaces (links the publish only records are not among them)."""

    ghost_root: Path
    host: str
    txid: str
    destinations: tuple[Path, ...]


@runtime_checkable
class Participant(Protocol):
    def before_publish(self, ctx: PublishContext) -> None: ...

    def after_publish(self, ctx: PublishContext) -> None: ...

    def before_restore(self, ctx: PublishContext) -> None: ...

    def after_restore(self, ctx: PublishContext) -> None: ...


class NullParticipant:
    """A participant with nothing to do."""

    def before_publish(self, ctx: PublishContext) -> None:
        return None

    def after_publish(self, ctx: PublishContext) -> None:
        return None

    def before_restore(self, ctx: PublishContext) -> None:
        return None

    def after_restore(self, ctx: PublishContext) -> None:
        return None
