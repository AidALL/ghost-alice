"""The install lock around every command that changes the installation.

`install_lock` owns the lock file and default stale window; this module owns the owner string and the environment
override, and restores a publish that stopped halfway before the command does anything else.
"""

from __future__ import annotations

from pathlib import Path
import os

import install_lock
import install_transaction
from installer import locations, report, runner
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
    """`install_transaction.py recover --ghost-root <root> --txid <id>` for every publish whose journal
    says neither done nor restored, newest first. A publish that cannot be restored stops the command
    with exit 1 and names its folder: placing files over a half-finished publish would hide which
    files are the originals."""
    root = locations.record_root()
    for txid in install_transaction.unfinished(root):
        result = runner.run_tool("_shared/install_transaction.py", "recover", "--ghost-root", str(root),
                                 "--txid", txid)
        if not result.ok:
            report.error(f"An unfinished publish could not be restored; review "
                         f"{install_transaction.rollbacks_root(root) / txid} and run the installer again")
            raise InstallerExit(1)


def release(handle: tuple[Path, str] | None) -> None:
    """Release a held lock; a missing file is not an error."""
    if handle is None:
        return
    install_lock.release_lock(*handle)
