"""The install lock around every command that changes the installation.

`install_lock` owns the lock file and default stale window; this module owns the owner string and the environment
override, and restores a publish that stopped halfway before the command does anything else.
"""

from __future__ import annotations

from pathlib import Path
import os

import install_lock
import install_transaction
from installer import gateway_participant, locations, report
from installer.runner import InstallerExit

def stale_seconds() -> int:
    """`$GHOST_ALICE_INSTALL_LOCK_STALE_SECONDS` or 1800."""
    value = os.environ.get("GHOST_ALICE_INSTALL_LOCK_STALE_SECONDS")
    try:
        return int(value) if value else install_lock.DEFAULT_STALE_SECONDS
    except ValueError:
        return install_lock.DEFAULT_STALE_SECONDS


def owner(command: str, platform: str, pid: int | None = None) -> str:
    """`installer:<command>:<platform>:<pid>`."""
    return f"installer:{command}:{platform}:{os.getpid() if pid is None else pid}"


def acquire(command: str, platform: str, lock_path: Path | None = None) -> tuple[Path, str]:
    """Acquire `~/.ghost-alice/install.lock`, then restore unfinished publishes; on failure print
    `Another install is already running; aborting <command>: <path>` and raise `InstallerExit(1)`."""
    path = Path(lock_path) if lock_path is not None else locations.install_lock_path()
    try:
        generation = install_lock.acquire_lock(path, stale_seconds=stale_seconds(), owner=owner(command, platform))
    except install_lock.InstallLockError:
        report.error(f"Another install is already running; aborting {command}: {path}")
        raise InstallerExit(1)
    try:
        restore_unfinished_publishes()
    except InstallerExit:
        release((path, generation))
        raise
    return path, generation


def restore_unfinished_publishes() -> None:
    """Restore unfinished publishes under the lock, newest first, using the gateway recovery factory.
    If recovery or staged-file cleanup raises an error, print the transaction's diagnostic
    and stop the command with exit 1; acquire() releases the lock before propagating that exit.
    """
    root = locations.record_root()
    for txid in install_transaction.unfinished(root):
        try:
            for line in install_transaction.recover(root, txid,
                    participant_factory=gateway_participant.recovery_participants):
                print(line)
        except install_transaction.InstallTransactionError as exc:
            report.error(install_transaction.failure_message(exc))
            raise InstallerExit(1) from exc


def release(handle: tuple[Path, str] | None) -> None:
    """Release a held lock; a missing file is not an error."""
    if handle is None:
        return
    install_lock.release_lock(*handle)
