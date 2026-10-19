"""The gateway coordination reuses the lock of the installer that called it and never adopts another holder."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_gateway_platform as platform  # noqa: E402
from context_gateway_model import GatewayBlocked  # noqa: E402


def _write_lock(home: Path, *, pid: int, owner: str) -> Path:
    path = home / ".ghost-alice" / "install.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"owner": owner, "pid": pid, "acquired_at": "2026-10-19T00:00:00+00:00"}),
                    encoding="utf-8")
    return path


class OperationLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_reuses_the_calling_python_installers_lock(self):
        # `installer.lock.acquire` runs inside the installer process, so the lock names that pid: this process's parent.
        path = _write_lock(self.home, pid=os.getppid(), owner=f"installer:install:claude:{os.getppid()}")
        before = path.read_text(encoding="utf-8")
        with platform.operation_lock(self.home):
            self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_reuses_its_own_lock(self):
        path = _write_lock(self.home, pid=os.getpid(), owner=f"installer:gateway:codex:{os.getpid()}")
        with platform.operation_lock(self.home):
            pass
        self.assertTrue(path.exists())

    def test_an_owner_string_naming_the_parent_grants_nothing(self):
        # Only the lock file's pid identifies the holder; a shell-style owner string that ends with the
        # parent's pid is an unrelated holder when the pid itself is another process.
        for owner in (f"install.ps1:codex:{os.getppid()}", f"install.sh:claude:{os.getppid()}"):
            with self.subTest(owner=owner):
                path = _write_lock(self.home, pid=424242, owner=owner)
                before = path.read_text(encoding="utf-8")
                with self.assertRaises(GatewayBlocked) as blocked:
                    with platform.operation_lock(self.home):
                        pass
                self.assertEqual(str(blocked.exception), "Another installer holds the operation lock")
                self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_refuses_an_unrelated_holder(self):
        _write_lock(self.home, pid=424242, owner="installer:install:claude:424242")
        with self.assertRaises(GatewayBlocked) as blocked:
            with platform.operation_lock(self.home):
                pass
        self.assertEqual(str(blocked.exception), "Another installer holds the operation lock")

    def test_takes_and_releases_its_own_lock_when_no_installer_runs(self):
        path = self.home / ".ghost-alice" / "install.lock"
        with platform.operation_lock(self.home):
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["owner"], "context-gateway")
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
