"""install_lock.held_by_process_tree: the one answer to "does this process, or the installer that started it, hold the install lock"."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_lock  # noqa: E402


class HeldByProcessTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lock = Path(self.temp.name) / ".ghost-alice" / "install.lock"
        self.lock.parent.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, metadata) -> None:
        self.lock.write_text(json.dumps(metadata), encoding="utf-8")

    def test_this_process_holds_the_lock_it_acquired(self):
        install_lock.acquire_lock(self.lock, owner="installer:install:claude:1")
        self.assertTrue(install_lock.held_by_process_tree(self.lock))

    def test_a_child_inherits_the_lock_of_the_installer_that_started_it(self):
        # `install_transaction.py` runs as a child of the installer process that holds the lock.
        self.write({"owner": "installer:install:codex:1", "pid": os.getppid()})
        self.assertTrue(install_lock.held_by_process_tree(self.lock))

    def test_another_process_holds_nothing_for_this_one(self):
        self.write({"owner": f"installer:install:claude:{os.getppid()}", "pid": 424242})
        self.assertFalse(install_lock.held_by_process_tree(self.lock))

    def test_no_lock_or_a_lock_without_a_pid_is_not_held(self):
        self.assertFalse(install_lock.held_by_process_tree(self.lock))
        self.write({"owner": "old"})
        self.assertFalse(install_lock.held_by_process_tree(self.lock))
        self.write({"owner": "old", "pid": str(os.getpid())})
        self.assertFalse(install_lock.held_by_process_tree(self.lock))

    def test_an_unreadable_lock_is_an_error_not_an_answer(self):
        self.lock.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(install_lock.InstallLockError, "unreadable"):
            install_lock.held_by_process_tree(self.lock)
        self.write(["not", "an", "object"])
        with self.assertRaisesRegex(install_lock.InstallLockError, "not an object"):
            install_lock.held_by_process_tree(self.lock)
        self.lock.unlink()
        self.lock.mkdir()
        with self.assertRaisesRegex(install_lock.InstallLockError, "not a regular"):
            install_lock.held_by_process_tree(self.lock)


if __name__ == "__main__":
    unittest.main()
