"""Installer lock generations with stale recovery and serialized metadata changes."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid

import file_lock


DEFAULT_STALE_SECONDS = 30 * 60
_UNSET = object()
_held_generations = {}


class InstallLockError(RuntimeError):
    pass


@contextmanager
def _guard(path: Path):
    # Never unlink this guard: all contenders must lock the same inode across generations.
    try:
        with file_lock.exclusive(path.with_name(path.name + '.guard'), must_lock=True):
            yield
    except (OSError, file_lock.LockTimeout) as exc:
        raise InstallLockError(f'Installer lock coordination failed: {path}: {exc}') from exc


def _write_lock(fd: int, owner: str | None, generation: str) -> None:
    metadata = {
        'owner': owner or 'unknown',
        'pid': os.getpid(),
        'generation': generation,
        'acquired_at': datetime.now(timezone.utc).isoformat(),
    }
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write('\n')


def acquire_lock(lock_path: Path | str, *, stale_seconds: int = DEFAULT_STALE_SECONDS, owner: str | None = None) -> str:
    """Create a generation and return the token its holder must supply to release it."""
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stale_seconds = max(1, int(stale_seconds))
    with _guard(path):
        try:
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            pass
        else:
            if age < stale_seconds:
                raise InstallLockError(f'install lock is already held: {path}')
            path.unlink()
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        generation = uuid.uuid4().hex
        try:
            _write_lock(fd, owner, generation)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        _held_generations[str(path.absolute())] = (os.getpid(), generation)
        return generation


def _read_metadata(path: Path) -> dict | None:
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise InstallLockError('Installer lock is not a regular owned file')
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise InstallLockError(f'Installer lock metadata is unreadable: {exc}') from exc
    try:
        holder = json.loads(raw)
    except ValueError as exc:
        raise InstallLockError('Installer lock metadata is unreadable') from exc
    if not isinstance(holder, dict):
        raise InstallLockError('Installer lock metadata is not an object')
    return holder


def release_lock(lock_path: Path | str, generation=_UNSET) -> None:
    """Remove only the supplied generation, under the same guard used for takeover."""
    path = Path(lock_path)
    key = str(path.absolute())
    if generation is _UNSET:
        holder_pid, token = _held_generations.get(key, (None, None))
        generation = token if holder_pid == os.getpid() else None
    if not isinstance(generation, str) or not generation:
        raise InstallLockError('Installer lock generation is required')
    if not path.exists() and not path.is_symlink():
        return
    with _guard(path):
        holder = _read_metadata(path)
        if holder is not None and holder.get('generation') == generation:
            path.unlink()
            _held_generations.pop(key, None)


def held_by_process_tree(lock_path: Path | str) -> bool:
    """True when the lock names this process or its calling installer; owner text grants nothing.

    Child tools inherit their parent's install lock. This PID check permits reuse, while only the
    generation returned by acquire_lock permits release. Missing metadata grants no lock; malformed
    metadata raises InstallLockError.
    """
    path = Path(lock_path)
    if not path.exists() and not path.is_symlink():
        return False
    with _guard(path):
        holder = _read_metadata(path)
    if holder is None:
        return False
    pid = holder.get('pid')
    return isinstance(pid, int) and not isinstance(pid, bool) and pid in (os.getpid(), os.getppid())


def main(argv=None) -> int:
    """Keep the standalone lock interface, carrying the acquired generation between processes."""
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="Acquire or release the Ghost-ALICE installer lock.")
    commands = parser.add_subparsers(dest="command", required=True)
    acquire = commands.add_parser("acquire")
    acquire.add_argument("--lock", required=True, type=Path)
    acquire.add_argument("--stale-seconds", type=int, default=DEFAULT_STALE_SECONDS)
    acquire.add_argument("--owner")
    release = commands.add_parser("release")
    release.add_argument("--lock", required=True, type=Path)
    release.add_argument("--generation", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "acquire":
            print(acquire_lock(args.lock, stale_seconds=args.stale_seconds, owner=args.owner))
        else:
            release_lock(args.lock, args.generation)
    except (InstallLockError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
