"""Pending initialization must preserve a concurrent real manifest append."""
import json
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import unittest
from unittest import mock

import file_lock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "merge-companion/scripts"))
import manifest_io

WRITER = Path(__file__).with_name("pending_manifest_writer.py")


class PendingManifestWriterTest(unittest.TestCase):
    def test_initializes_missing_manifest_and_preserves_existing_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pending" / "manifest.json"
            with mock.patch.object(sys, "argv", [str(WRITER), str(path), "codex"]):
                runpy.run_path(str(WRITER), run_name="__main__")
            self.assertEqual(json.loads(path.read_text()), {"version": 1, "platform": "codex", "entries": []})
            original = b'{"entries": [{"id":"existing","decided":true}]}\n'
            path.write_bytes(original)
            with mock.patch.object(sys, "argv", [str(WRITER), str(path), "codex"]):
                runpy.run_path(str(WRITER), run_name="__main__")
            self.assertEqual(path.read_bytes(), original)

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_initialization_keeps_concurrent_append(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            observed, release, append_done = threading.Event(), threading.Event(), threading.Event()
            errors = []
            original_exists = Path.exists

            def pause_missing_check(candidate, *args, **kwargs):
                result = original_exists(candidate, *args, **kwargs)
                if candidate == path and threading.current_thread().name == "initializer":
                    observed.set()
                    if not release.wait(5):
                        raise TimeoutError("initializer was not released")
                return result

            def initialize():
                try:
                    runpy.run_path(str(WRITER), run_name="__main__")
                except Exception as error:
                    errors.append(error)

            def append():
                try:
                    manifest_io.append_entry(path, {"id": "concurrent", "decided": False})
                except Exception as error:
                    errors.append(error)
                finally:
                    append_done.set()

            with mock.patch.object(sys, "argv", [str(WRITER), str(path), "codex"]), mock.patch.object(Path, "exists", pause_missing_check):
                initializer = threading.Thread(target=initialize, name="initializer")
                initializer.start()
                try:
                    self.assertTrue(observed.wait(5))
                    appender = threading.Thread(target=append)
                    appender.start()
                    append_done.wait(0.2)
                finally:
                    release.set()
                    initializer.join(5)
                appender.join(5)
            self.assertFalse(initializer.is_alive())
            self.assertFalse(appender.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(json.loads(path.read_text())["entries"], [{"id": "concurrent", "decided": False}])
