"""Tests for `atomic_write`: one file replaced so readers see its old or its new contents, never a part.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_write  # noqa: E402


class _FailAfterPartialWrite:
    def __init__(self, handle, error: OSError) -> None:
        self._handle = handle
        self._error = error

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._handle.__exit__(exc_type, exc_value, traceback)

    def write(self, value):
        partial_length = max(1, len(value) // 2)
        self._handle.write(value[:partial_length])
        self._handle.flush()
        raise self._error

    def __getattr__(self, name):
        return getattr(self._handle, name)


class AtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("GHOST_ALICE_OWNED_ROOT", None)

    def test_a_write_creates_the_folder_replaces_the_file_and_leaves_no_temporary_file(self):
        path = self.root / "state" / "record.json"
        atomic_write.write_bytes(path, b"first")
        atomic_write.write_bytes(path, b"second")
        self.assertEqual(path.read_bytes(), b"second")
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_a_new_file_is_owner_only_and_a_replaced_file_keeps_its_mode(self):
        path = self.root / "record.json"
        atomic_write.write_bytes(path, b"first")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        os.chmod(path, 0o640)
        atomic_write.write_bytes(path, b"second")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_json_has_a_two_space_indent_unescaped_text_and_a_final_newline(self):
        path = self.root / "record.json"
        atomic_write.write_json(path, {"b": "한글", "a": 1})
        self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "b": "한글",\n  "a": 1\n}\n')
        atomic_write.write_json(path, {"b": "한글", "a": 1}, sort_keys=True)
        self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "a": 1,\n  "b": "한글"\n}\n')

    @unittest.skipIf(os.name == "nt", "Windows cannot flush a folder")
    def test_sync_folder_also_flushes_the_folder_entry_after_the_rename(self):
        path = self.root / "record.json"
        flushed: list[bool] = []
        real_fsync = os.fsync

        def observe(descriptor):
            flushed.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
            return real_fsync(descriptor)

        with mock.patch.object(atomic_write.os, "fsync", side_effect=observe):
            atomic_write.write_bytes(path, b"plain")
        self.assertEqual(flushed, [False])
        flushed.clear()
        with mock.patch.object(atomic_write.os, "fsync", side_effect=observe):
            atomic_write.write_bytes(path, b"durable", sync_folder=True)
        self.assertEqual(flushed, [False, True])

    def test_a_path_outside_the_owned_root_is_refused_before_anything_is_written(self):
        owned = self.root / "owned"
        owned.mkdir()
        outside = self.root / "outside" / "record.json"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_OWNED_ROOT": str(owned)}), self.assertRaises(ValueError):
            atomic_write.write_bytes(outside, b"escape")
        self.assertFalse(outside.parent.exists())

    def test_the_owned_root_check_reads_the_environment_the_caller_gives(self):
        owned = self.root / "owned"
        owned.mkdir()
        outside = self.root / "outside" / "record.json"
        with self.assertRaises(ValueError):
            atomic_write.write_bytes(outside, b"escape", env={"GHOST_ALICE_OWNED_ROOT": str(owned)})
        self.assertFalse(outside.parent.exists())

    def test_atomic_write_replaces_resolved_symlink_destination(self):
        path = self.root / "config.json"
        path.write_bytes(b"symlink entry sentinel")
        target = self.root / "runtime-target" / "config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"old target")
        original_mode = stat.S_IMODE(target.stat().st_mode)
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def simulated_is_symlink(candidate: Path) -> bool:
            if candidate == path:
                return True
            return original_is_symlink(candidate)

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == path:
                return target
            return original_resolve(candidate, *args, **kwargs)

        with (
            mock.patch.object(Path, "is_symlink", new=simulated_is_symlink),
            mock.patch.object(Path, "resolve", new=simulated_resolve),
        ):
            atomic_write.write_bytes(path, b"new target")

        self.assertEqual(path.read_bytes(), b"symlink entry sentinel")
        self.assertEqual(target.read_bytes(), b"new target")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), original_mode)

    def test_resolved_symlink_failure_keeps_entry_and_target_bytes(self):
        path = self.root / "config.json"
        path.write_bytes(b"symlink entry sentinel")
        target = self.root / "runtime-target" / "config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"old target")
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve
        original_fdopen = os.fdopen
        write_error = OSError("target temp write failed")

        def simulated_is_symlink(candidate: Path) -> bool:
            if candidate == path:
                return True
            return original_is_symlink(candidate)

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == path:
                return target
            return original_resolve(candidate, *args, **kwargs)

        def failing_fdopen(fd, *args, **kwargs):
            return _FailAfterPartialWrite(
                original_fdopen(fd, *args, **kwargs),
                write_error,
            )

        with (
            mock.patch.object(Path, "is_symlink", new=simulated_is_symlink),
            mock.patch.object(Path, "resolve", new=simulated_resolve),
            mock.patch.object(atomic_write.os, "fdopen", side_effect=failing_fdopen),
            mock.patch.object(
                atomic_write.tempfile,
                "mkstemp",
                wraps=tempfile.mkstemp,
            ) as mkstemp,
            self.assertRaises(OSError) as raised,
        ):
            atomic_write.write_bytes(path, b"new target")

        self.assertIs(raised.exception, write_error)
        self.assertEqual(path.read_bytes(), b"symlink entry sentinel")
        self.assertEqual(target.read_bytes(), b"old target")
        self.assertEqual(Path(mkstemp.call_args.kwargs["dir"]), target.parent)
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_broken_symlink_with_missing_target_parent_fails_safely(self):
        path = self.root / "config.json"
        path.write_bytes(b"symlink entry sentinel")
        target = self.root / "missing-target-parent" / "config.json"
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def simulated_is_symlink(candidate: Path) -> bool:
            if candidate == path:
                return True
            return original_is_symlink(candidate)

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == path:
                return target
            return original_resolve(candidate, *args, **kwargs)

        with (
            mock.patch.object(Path, "is_symlink", new=simulated_is_symlink),
            mock.patch.object(Path, "resolve", new=simulated_resolve),
            self.assertRaises(FileNotFoundError),
        ):
            atomic_write.write_bytes(path, b"new target")

        self.assertEqual(path.read_bytes(), b"symlink entry sentinel")
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
