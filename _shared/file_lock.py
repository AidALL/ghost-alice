"""An exclusive lock on one file, waited for within a time limit, so a holder that stopped never stops the writers
behind it.

Dependencies: Python 3.11+ standard library. Without `fcntl` (Windows), or on a file system that refuses `flock`, the
lock is not taken.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import time

try:
    import fcntl
except ImportError:
    fcntl = None

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


@contextmanager
def exclusive(path: Path | str, *, wait: float | None = None, must_lock: bool = False):
    """An exclusive flock on the file at `path`, created when missing; yields the locked file's stat, or None when the
    lock is not taken. A writer that waited while another rotated the file locks the file now at `path`; a lock still
    held after `wait` seconds (`LOCK_WAIT_S` when None) raises LockTimeout. A caller that sets `must_lock` gets the
    OSError of a file system that refuses `flock` instead of going on without the lock."""
    if fcntl is None:
        yield None
        return
    deadline = time.monotonic() + (LOCK_WAIT_S if wait is None else wait)
    while True:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
        try:
            if not _flock(descriptor, path, deadline, must_lock):
                held = None
                break
            held = os.fstat(descriptor)
            try:
                current = os.stat(path)
            except FileNotFoundError:
                current = None
        except BaseException:
            os.close(descriptor)
            raise
        if current is not None and os.path.samestat(held, current):
            break
        os.close(descriptor)  # the file was rotated while this writer waited
    try:
        yield held
    finally:
        os.close(descriptor)
