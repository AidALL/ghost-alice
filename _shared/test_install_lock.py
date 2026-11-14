"""install_lock.held_by_process_tree: the one answer to "does this process, or the installer that started it, hold the install lock"."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest

import file_lock

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
        self.assertEqual(list(self.lock.parent.iterdir()), [])
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

    def test_a_real_child_inherits_its_parent_lock_and_cannot_release_another_generation(self):
        generation = install_lock.acquire_lock(self.lock, owner='parent')
        before = self.lock.read_bytes()
        result = subprocess.run([sys.executable, '-c',
            'import install_lock, sys; '
            'assert install_lock.held_by_process_tree(sys.argv[1]); '
            'install_lock.release_lock(sys.argv[1], "unowned-generation")', str(self.lock)],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lock.read_bytes(), before)
        install_lock.release_lock(self.lock, generation)

    @unittest.skipIf(file_lock.fcntl is None, 'requires the native POSIX guard')
    def test_another_process_cannot_create_metadata_while_the_guard_is_held(self):
        guard = self.lock.with_name(self.lock.name + '.guard')
        with file_lock.exclusive(guard, must_lock=True):
            result = subprocess.run([sys.executable, '-c',
                'import file_lock, install_lock, sys\n'
                'file_lock.LOCK_WAIT_S = 0.05\n'
                'try:\n'
                '    install_lock.acquire_lock(sys.argv[1])\n'
                'except install_lock.InstallLockError:\n'
                '    print("blocked")\n'
                'else:\n'
                '    raise AssertionError("guard did not exclude a second process")\n', str(self.lock)],
                cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (0, 'blocked\n'), result.stderr)
        self.assertFalse(self.lock.exists())
        identity = guard.stat()
        generation = install_lock.acquire_lock(self.lock)
        install_lock.release_lock(self.lock, generation)
        self.assertTrue(os.path.samestat(identity, guard.stat()))

    def test_a_generation_is_required_even_for_legacy_metadata(self):
        self.write({'pid': os.getpid()})
        before = self.lock.read_bytes()
        for generation in (None, ''):
            with self.subTest(generation=generation), self.assertRaises(install_lock.InstallLockError):
                install_lock.release_lock(self.lock, generation)
        self.assertEqual(self.lock.read_bytes(), before)
        install_lock.release_lock(self.lock, 'not-the-legacy-owner')
        self.assertEqual(self.lock.read_bytes(), before)

    def test_cli_carries_generation_between_acquire_and_release(self):
        command = [sys.executable, str(Path(install_lock.__file__))]
        acquired = subprocess.run([*command, 'acquire', '--lock', str(self.lock)], capture_output=True, text=True)
        self.assertEqual(acquired.returncode, 0, acquired.stderr)
        token = acquired.stdout.strip()
        self.assertEqual(json.loads(self.lock.read_text())['generation'], token)
        rejected = subprocess.run([*command, 'release', '--lock', str(self.lock)], capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertTrue(self.lock.exists())
        released = subprocess.run([*command, 'release', '--lock', str(self.lock), '--generation', token], capture_output=True, text=True)
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertFalse(self.lock.exists())

    def test_command_releases_its_generation_when_the_body_fails(self):
        with self.assertRaisesRegex(ValueError, 'operation failed'):
            with install_lock.command_lock(self.lock, owner='addon-uninstall:claude'):
                self.assertTrue(install_lock.held_by_process_tree(self.lock))
                self.assertEqual(json.loads(self.lock.read_text())['owner'], 'addon-uninstall:claude')
                raise ValueError('operation failed')
        self.assertFalse(self.lock.exists())

    def test_command_reuses_current_or_parent_lock_without_releasing_it(self):
        for pid in (os.getpid(), os.getppid()):
            with self.subTest(pid=pid):
                self.write(dict(pid=pid, generation='existing-generation', owner='installer'))
                before = self.lock.read_bytes()
                with install_lock.command_lock(self.lock, owner='addon-uninstall:claude'):
                    self.assertEqual(self.lock.read_bytes(), before)
                self.assertEqual(self.lock.read_bytes(), before)

    def test_preview_neither_reads_an_existing_lock_nor_creates_a_new_lock_folder(self):
        missing = self.lock.parent / 'missing' / 'install.lock'
        with install_lock.command_lock(missing, owner='preview', enabled=False):
            self.assertFalse(missing.parent.exists())
        self.lock.write_text('invalid metadata')
        with install_lock.command_lock(self.lock, owner='preview', enabled=False):
            self.assertEqual(self.lock.read_text(), 'invalid metadata')
        self.assertEqual(list(self.lock.parent.iterdir()), [self.lock])

    def test_command_preserves_a_generation_that_replaced_the_one_it_acquired(self):
        with install_lock.command_lock(self.lock, owner='addon-uninstall:claude'):
            self.write(dict(pid=424242, generation='replacement-generation', owner='new-installer'))
        self.assertEqual(json.loads(self.lock.read_text())['generation'], 'replacement-generation')

    def test_command_refuses_another_holder_before_entering_the_body(self):
        self.write(dict(pid=424242, generation='other-generation', owner='other'))
        before = self.lock.read_bytes()
        with self.assertRaisesRegex(install_lock.InstallLockError, 'already held'):
            with install_lock.command_lock(self.lock, owner='addon-uninstall:claude'):
                self.fail('the command entered without owning the lock')
        self.assertEqual(self.lock.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
