"""Tests for `file_lock`: one exclusive lock per file, waited for at most `LOCK_WAIT_S`.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import errno
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
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

        def writer():
            with file_lock.exclusive(path) as held:
                locked.append(held.st_ino)

        with file_lock.exclusive(path):
            thread = threading.Thread(target=writer)
            thread.start()
            time.sleep(0.1)  # the writer now waits for the lock on the old file
            path.rename(path.with_name("io-trace.jsonl.1"))
            path.write_text("new\n", encoding="utf-8")
        thread.join(10)
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
            self.assertLess(time.monotonic() - started, 5)

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_a_caller_limit_replaces_the_default_limit(self):
        path = self.root / "manifest.json.lock"
        with mock.patch.object(file_lock, "LOCK_WAIT_S", 30), file_lock.exclusive(path):
            started = time.monotonic()
            with self.assertRaises(file_lock.LockTimeout):
                with file_lock.exclusive(path, wait=0.2):
                    pass
            self.assertLess(time.monotonic() - started, 5)

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


if __name__ == "__main__":
    unittest.main()
