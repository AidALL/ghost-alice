"""The install lock around every command that changes the installation.

Port of `installer_lib/install.sh:35-73`, extended to the uninstall, repair,
cleanup-pending and gateway commands. `install_lock` owns the lock file; this
module owns the owner string and the stale window.
"""

from __future__ import annotations

from pathlib import Path
import os

import install_lock
from installer import locations, report
from installer.runner import InstallerExit

DEFAULT_STALE_SECONDS = 1800


def stale_seconds() -> int:
    """`$GHOST_ALICE_INSTALL_LOCK_STALE_SECONDS` or 1800."""
    value = os.environ.get("GHOST_ALICE_INSTALL_LOCK_STALE_SECONDS")
    try:
        return int(value) if value else DEFAULT_STALE_SECONDS
    except ValueError:
        return DEFAULT_STALE_SECONDS


def owner(command: str, platform: str, pid: int | None = None) -> str:
    """`installer:<command>:<platform>:<pid>`."""
    return f"installer:{command}:{platform}:{os.getpid() if pid is None else pid}"


def acquire(command: str, platform: str, lock_path: Path | None = None) -> Path:
    """Acquire `~/.ghost-alice/install.lock`; on failure print
    `Another install is already running; aborting <command>: <path>` and raise `InstallerExit(1)`."""
    path = Path(lock_path) if lock_path is not None else locations.install_lock_path()
    try:
        install_lock.acquire_lock(path, stale_seconds=stale_seconds(), owner=owner(command, platform))
    except install_lock.InstallLockError:
        report.error(f"Another install is already running; aborting {command}: {path}")
        raise InstallerExit(1)
    return path


def release(lock_path: Path | None) -> None:
    """Release a held lock; a missing file is not an error."""
    if lock_path is None:
        return
    install_lock.release_lock(lock_path)
