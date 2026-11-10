"""An exclusive lock on one file, waited for within a time limit, so a holder that stopped never stops the writers
behind it.

Dependencies: Python 3.11+ standard library. Optional locking skips unsupported platforms and file systems;
mandatory locking uses `fcntl` on POSIX or `msvcrt` on Windows and refuses to proceed without a lock.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

LOCK_WAIT_S = 2.0  # the longest a writer waits for a lock unless its caller gives another limit


class LockTimeout(Exception):
    """A lock stayed held past its time limit."""


def _flock(descriptor: int, path: Path | str, deadline: float, must_lock: bool) -> bool:
    """Take the lock on `descriptor`: True when it is held, False when the file system refuses it and the caller can
    go on without it."""
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeout(str(path))
            time.sleep(0.01)
        except OSError:
            if must_lock:
                raise
            return False


def _windows_lock(descriptor: int, path: Path | str, deadline: float) -> None:
    while True:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if time.monotonic() >= deadline:
                raise LockTimeout(str(path)) from exc
            time.sleep(0.01)


@contextmanager
def exclusive(path: Path | str, *, wait: float | None = None, must_lock: bool = False):
    """An exclusive lock on the file at `path`, created when missing; yields the locked file's stat, or None when the
    lock is not taken. A writer that waited while another rotated the file locks the file now at `path`; a lock still
    held after `wait` seconds (`LOCK_WAIT_S` when None) raises LockTimeout. A caller that sets `must_lock` gets the
    OSError of a platform or file system that refuses locking instead of going on without the lock."""
    if fcntl is None:
        if not must_lock:
            yield None
            return
        if msvcrt is None:
            raise OSError(errno.ENOSYS, 'mandatory file locking is unavailable')
    deadline = time.monotonic() + (LOCK_WAIT_S if wait is None else wait)
    while True:
        flags = os.O_RDWR | os.O_CREAT if fcntl is None else os.O_WRONLY | os.O_APPEND | os.O_CREAT
        descriptor = os.open(path, flags, 0o666)
        locked = False
        try:
            if fcntl is None:
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b'\0')
                _windows_lock(descriptor, path, deadline)
                locked = True
            else:
                locked = _flock(descriptor, path, deadline, must_lock)
            if not locked:
                yield None
                return
            held = os.fstat(descriptor)
            try:
                current = os.stat(path)
            except FileNotFoundError:
                current = None
            if current is not None and os.path.samestat(held, current):
                yield held
                return
        finally:
            try:
                if fcntl is None and locked:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(descriptor)
