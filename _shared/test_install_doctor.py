#!/usr/bin/env python3
"""Tests for the read-only installer doctor node-runtime check.

The tool-checkpoint PreToolUse gate is dispatched through `node ghost-alice-hook.mjs`.
The installer blocks hook install when node is absent, but node can be removed from
PATH after install. Doctor is the read-only diagnostic that must surface that drift,
because Claude Code treats a non-2 PreToolUse exit (a missing-node crash) as
non-blocking, so the gate would silently fail open.
"""

import unittest
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_doctor


class NodeRuntimeStatusTest(unittest.TestCase):
    def test_missing_node_is_warning(self) -> None:
        with mock.patch.object(install_doctor.shutil, "which", return_value=None):
            status, detail = install_doctor._node_runtime_status(strict=False)
        self.assertEqual(status, install_doctor.STATUS_WARNING)
        self.assertIn("missing", detail)

    def test_missing_node_under_strict_is_error(self) -> None:
        with mock.patch.object(install_doctor.shutil, "which", return_value=None):
            status, _detail = install_doctor._node_runtime_status(strict=True)
        self.assertEqual(status, install_doctor.STATUS_ERROR)

    def test_present_node_is_ok(self) -> None:
        with mock.patch.object(install_doctor.shutil, "which", return_value="/usr/bin/node"):
            status, detail = install_doctor._node_runtime_status(strict=False)
        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertIn("ok", detail)


if __name__ == "__main__":
    unittest.main()
