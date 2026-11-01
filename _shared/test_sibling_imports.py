#!/usr/bin/env python3
"""Shared modules load each sibling under one name in package-style and direct runs.

Dependencies: Python 3.11+ standard library.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parent
REPO_ROOT = SHARED_DIR.parent
ENTRY_MODULES = ("blind_behavior", "fresh_agent_session", "live_agent_smoke")
SIBLING_MODULES = ENTRY_MODULES + ("project_runtime", "text_lines")


def _python(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)


class SiblingImportTests(unittest.TestCase):
    def test_package_load_keeps_one_name_per_sibling(self) -> None:
        for entry in ENTRY_MODULES:
            with self.subTest(entry=entry):
                code = (
                    "import importlib, sys\n"
                    f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
                    f"module = importlib.import_module('_shared.{entry}')\n"
                    "package_siblings = sorted(name for name in sys.modules\n"
                    f"                          if name.startswith('_shared.') and name != '_shared.{entry}')\n"
                    "assert not package_siblings, package_siblings\n"
                    f"sys.path.insert(0, {str(SHARED_DIR)!r})\n"
                    "import project_runtime\n"
                    "assert module.project_runtime is project_runtime.project_runtime\n"
                )
                with tempfile.TemporaryDirectory() as cwd:
                    result = _python(["-c", code], Path(cwd))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_test_process_keeps_one_module_per_name(self) -> None:
        code = (
            "import sys\n"
            f"sys.path[:0] = [{str(SHARED_DIR)!r}, {str(REPO_ROOT)!r}]\n"
            "import install_doctor, live_agent_smoke\n"
            "import test_blind_behavior, test_fresh_agent_session, test_project_runtime\n"
            "blind = test_blind_behavior.load_blind_behavior()\n"
            "fresh = test_fresh_agent_session.load_fresh_agent_session()\n"
            "runtime = test_project_runtime.load_project_runtime()\n"
            "assert blind.FreshAgentSessionError is fresh.FreshAgentSessionError\n"
            "assert blind.project_runtime is runtime.project_runtime is live_agent_smoke.project_runtime\n"
            "assert install_doctor.project_runtime is runtime.project_runtime\n"
            f"doubled = sorted(name for name in {SIBLING_MODULES!r}\n"
            "                 if name in sys.modules and '_shared.' + name in sys.modules)\n"
            "assert not doubled, doubled\n"
        )
        result = _python(["-c", code], REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_direct_run_finds_siblings(self) -> None:
        for entry in ENTRY_MODULES:
            with self.subTest(entry=entry):
                with tempfile.TemporaryDirectory() as cwd:
                    result = _python([str(SHARED_DIR / f"{entry}.py"), "--help"], Path(cwd))
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
