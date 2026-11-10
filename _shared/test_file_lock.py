"""Tests for `file_lock`: one exclusive lock per file, waited for at most `LOCK_WAIT_S`.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import file_lock  # noqa: E402


class FileLockTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_writer_that_waited_while_the_log_was_rotated_locks_the_new_log(self):
        path = self.root / "io-trace.jsonl"
        path.write_text("old\n", encoding="utf-8")
        locked = []
        opened = []
        old_inode_opened = threading.Event()
        open_file = os.open

        def observe_open(*args, **kwargs):
            descriptor = open_file(*args, **kwargs)
            if threading.current_thread() is thread:
                opened.append(os.fstat(descriptor).st_ino)
                old_inode_opened.set()
            return descriptor

        def writer():
            with file_lock.exclusive(path) as held:
                locked.append(held.st_ino)

        with file_lock.exclusive(path) as old:
            thread = threading.Thread(target=writer)
            self.addCleanup(thread.join, 10)
            with mock.patch.object(file_lock.os, "open", side_effect=observe_open):
                thread.start()
                self.assertTrue(old_inode_opened.wait(5), "the waiter did not open the held log")
                self.assertEqual(opened, [old.st_ino])
                path.rename(path.with_name("io-trace.jsonl.1"))
                path.write_text("new\n", encoding="utf-8")
        thread.join(10)
        self.assertFalse(thread.is_alive(), "the waiter did not finish after rotation")
        self.assertEqual(locked, [path.stat().st_ino])

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_lock_held_past_the_limit_raises_lock_timeout(self):
        # A holder that stopped must not stop the writers behind it.
        path = self.root / "manifest.json.lock"
        with mock.patch.object(file_lock, "LOCK_WAIT_S", 0.2), file_lock.exclusive(path):
            started = time.monotonic()
            with self.assertRaises(file_lock.LockTimeout):
                with file_lock.exclusive(path):
                    pass
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.15)
            self.assertLess(elapsed, 5)

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_caller_limit_replaces_the_default_limit(self):
        path = self.root / "manifest.json.lock"
        with mock.patch.object(file_lock, "LOCK_WAIT_S", 30), file_lock.exclusive(path):
            started = time.monotonic()
            with self.assertRaises(file_lock.LockTimeout):
                with file_lock.exclusive(path, wait=0.2):
                    pass
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.15)
            self.assertLess(elapsed, 5)

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_lock_the_file_system_refuses_goes_on_without_the_lock(self):
        # A network file system can answer flock with an error other than "held"; the holder goes on as without fcntl.
        path = self.root / "log.jsonl"
        with mock.patch.object(file_lock.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")):
            with file_lock.exclusive(path) as held:
                self.assertIsNone(held)

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_caller_that_must_lock_gets_the_refusal(self):
        path = self.root / "log.jsonl"
        with mock.patch.object(file_lock.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")):
            with self.assertRaises(OSError) as caught:
                with file_lock.exclusive(path, must_lock=True):
                    self.fail("the holder went on without the lock")
        self.assertEqual(caught.exception.errno, errno.ENOLCK)

    def test_without_flock_the_lock_yields_none_and_creates_nothing(self):
        path = self.root / "manifest.json.lock"
        with mock.patch.object(file_lock, "fcntl", None), file_lock.exclusive(path) as held:
            self.assertIsNone(held)
        self.assertFalse(path.exists())

    def test_mandatory_lock_without_a_native_primitive_refuses_to_proceed(self):
        path = self.root / 'install.lock.guard'
        with mock.patch.object(file_lock, 'fcntl', None), mock.patch.object(file_lock, 'msvcrt', None, create=True):
            with self.assertRaises(OSError):
                with file_lock.exclusive(path, must_lock=True):
                    self.fail('mandatory locking silently proceeded')
        self.assertFalse(path.exists())

    def test_windows_mandatory_lock_locks_and_unlocks_the_first_byte(self):
        path = self.root / 'install.lock.guard'
        calls = []

        def locking(fd, mode, count):
            calls.append((fd, mode, count, os.lseek(fd, 0, os.SEEK_CUR), os.fstat(fd).st_size))

        windows = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
        with mock.patch.object(file_lock, 'fcntl', None), mock.patch.object(file_lock, 'msvcrt', windows, create=True):
            with self.assertRaisesRegex(RuntimeError, 'body failed'):
                with file_lock.exclusive(path, must_lock=True) as held:
                    self.assertIsNotNone(held)
                    raise RuntimeError('body failed')
        self.assertEqual([call[1:] for call in calls], [(2, 1, 0, 1), (0, 1, 0, 1)])
        with self.assertRaises(OSError):
            os.fstat(calls[0][0])
        self.assertEqual(path.read_bytes(), b'\0')

    def test_windows_contention_times_out_and_other_errors_propagate(self):
        path = self.root / 'install.lock.guard'
        windows = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0,
                                  locking=mock.Mock(side_effect=OSError(errno.EACCES, 'held')))
        with mock.patch.object(file_lock, 'fcntl', None), mock.patch.object(file_lock, 'msvcrt', windows, create=True):
            with self.assertRaises(file_lock.LockTimeout):
                with file_lock.exclusive(path, wait=0, must_lock=True):
                    self.fail('contended lock was granted')
            fd = windows.locking.call_args.args[0]
            with self.assertRaises(OSError):
                os.fstat(fd)
            windows.locking.side_effect = OSError(errno.ENOLCK, 'unsupported')
            with self.assertRaises(OSError) as caught:
                with file_lock.exclusive(path, must_lock=True):
                    self.fail('unsupported mandatory lock was granted')
            self.assertEqual(caught.exception.errno, errno.ENOLCK)


if __name__ == "__main__":
    unittest.main()
