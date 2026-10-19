#!/usr/bin/env python3
"""Atomic installer lock with stale lock recovery."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


DEFAULT_STALE_SECONDS = 30 * 60


class InstallLockError(RuntimeError):
    pass


def _metadata(owner: str | None) -> dict[str, object]:
    return {
        "owner": owner or "unknown",
        "pid": os.getpid(),
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_lock(fd: int, owner: str | None) -> None:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(_metadata(owner), fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def acquire_lock(lock_path: Path | str, *, stale_seconds: int = DEFAULT_STALE_SECONDS, owner: str | None = None) -> None:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stale_seconds = max(1, int(stale_seconds))

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        try:
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            return acquire_lock(path, stale_seconds=stale_seconds, owner=owner)

        if age >= stale_seconds:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as unlink_exc:
                raise InstallLockError(f"stale install lock could not be removed: {path}") from unlink_exc
            return acquire_lock(path, stale_seconds=stale_seconds, owner=owner)

        raise InstallLockError(f"install lock is already held: {path}") from exc

    _write_lock(fd, owner)


def release_lock(lock_path: Path | str) -> None:
    path = Path(lock_path)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def held_by_process_tree(lock_path: Path | str) -> bool:
    """True when the lock file names this process or the process that started it.

    The installer acquires the lock in its own process and runs its tools as child
    processes, so a tool inherits the lock of its parent. The owner text is a label
    and grants nothing. An absent lock, a lock without a pid and another process's
    lock are not held; a lock that is not a regular file or whose metadata is not
    a readable JSON object raises InstallLockError.
    """
    path = Path(lock_path)
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise InstallLockError("Installer lock is not a regular owned file")
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError) as exc:
        raise InstallLockError(f"Installer lock metadata is unreadable: {exc}") from exc
    try:
        holder = json.loads(raw)
    except ValueError as exc:
        raise InstallLockError("Installer lock metadata is unreadable") from exc
    if not isinstance(holder, dict):
        raise InstallLockError("Installer lock metadata is not an object")
    pid = holder.get("pid")
    return isinstance(pid, int) and not isinstance(pid, bool) and pid in (os.getpid(), os.getppid())


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Acquire or release the Ghost-ALICE installer lock.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire = subparsers.add_parser("acquire")
    acquire.add_argument("--lock", required=True, type=Path)
    acquire.add_argument("--stale-seconds", type=int, default=DEFAULT_STALE_SECONDS)
    acquire.add_argument("--owner", default=None)

    release = subparsers.add_parser("release")
    release.add_argument("--lock", required=True, type=Path)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "acquire":
            acquire_lock(args.lock, stale_seconds=args.stale_seconds, owner=args.owner)
        elif args.command == "release":
            release_lock(args.lock)
        else:
            raise AssertionError(args.command)
    except InstallLockError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
