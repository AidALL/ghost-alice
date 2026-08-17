#!/usr/bin/env python3
"""Behavior tests for repository-local child process runtime isolation."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = Path(__file__).with_name("project_runtime.py")
TEMP_KEYS = ("TEMP", "TMP", "TMPDIR")


def load_project_runtime():
    if not MODULE_PATH.is_file():
        return None
    return importlib.import_module("_shared.project_runtime")


class ProjectRuntimePresenceTest(unittest.TestCase):
    def test_shared_project_runtime_module_exists(self):
        self.assertIsNotNone(load_project_runtime())


@unittest.skipUnless(MODULE_PATH.is_file(), "project_runtime.py is not implemented")
class ProjectRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = load_project_runtime()

    def setUp(self):
        self.repo = REPO_ROOT / ".tmp" / "test-project-runtime" / uuid.uuid4().hex
        self.repo.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_hostile_parent_temp_values_are_replaced_only_in_child_env(self):
        hostile = r"C:\Users\Public\Documents\ESTsoft\CreatorTemp"
        reports: list[str] = []
        with mock.patch.dict(
            os.environ,
            {**{key: hostile for key in TEMP_KEYS}, "PYTHONPYCACHEPREFIX": hostile},
            clear=False,
        ):
            parent_before = {
                key: os.environ.get(key)
                for key in (*TEMP_KEYS, "PYTHONPYCACHEPREFIX")
            }
            with self.runtime.project_runtime(
                self.repo,
                "hostile-env",
                reporter=reports.append,
            ) as run:
                child = run.child_env()
                runtime_root = run.root
                for key in TEMP_KEYS:
                    self.assertEqual(Path(child[key]), run.temp_dir)
                self.assertEqual(Path(child["PYTHONPYCACHEPREFIX"]), run.pycache_dir)
                self.assertTrue(runtime_root.is_relative_to(self.repo / ".tmp"))
                self.assertEqual(
                    {
                        key: os.environ.get(key)
                        for key in (*TEMP_KEYS, "PYTHONPYCACHEPREFIX")
                    },
                    parent_before,
                )

        self.assertFalse(runtime_root.exists())
        self.assertEqual(reports, [])

    def test_runs_are_unique_and_success_removes_each_run_root(self):
        roots: list[Path] = []
        for _ in range(2):
            with self.runtime.project_runtime(self.repo, "unique") as run:
                roots.append(run.root)
                self.assertTrue(run.root.is_dir())
        self.assertNotEqual(roots[0], roots[1])
        self.assertTrue(all(not root.exists() for root in roots))

    def test_success_cleanup_retries_transient_permission_error(self):
        reports: list[str] = []
        real_rmtree = shutil.rmtree
        attempts = 0

        def flaky_rmtree(path):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError(13, "temporarily locked")
            return real_rmtree(path)

        with (
            mock.patch.object(self.runtime.shutil, "rmtree", side_effect=flaky_rmtree),
            mock.patch.object(self.runtime.time, "sleep") as sleep,
        ):
            with self.runtime.project_runtime(
                self.repo,
                "transient-cleanup-lock",
                reporter=reports.append,
            ) as run:
                runtime_root = run.root

        self.assertEqual(attempts, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(runtime_root.exists())
        self.assertEqual(reports, [])

    def test_success_cleanup_retries_transient_windows_directory_not_empty(self):
        reports: list[str] = []
        real_rmtree = shutil.rmtree
        attempts = 0

        def flaky_rmtree(path):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                error = OSError(0, "directory not empty")
                error.winerror = 145
                raise error
            return real_rmtree(path)

        with (
            mock.patch.object(self.runtime.shutil, "rmtree", side_effect=flaky_rmtree),
            mock.patch.object(self.runtime.time, "sleep") as sleep,
        ):
            with self.runtime.project_runtime(
                self.repo,
                "transient-directory-not-empty",
                reporter=reports.append,
            ) as run:
                runtime_root = run.root

        self.assertEqual(attempts, 2)
        sleep.assert_called_once()
        self.assertFalse(runtime_root.exists())
        self.assertEqual(reports, [])

    def test_persistent_cleanup_permission_error_preserves_without_replacing_success(self):
        reports: list[str] = []
        with (
            mock.patch.object(
                self.runtime.shutil,
                "rmtree",
                side_effect=PermissionError(13, "still locked"),
            ),
            mock.patch.object(self.runtime.time, "sleep") as sleep,
        ):
            with self.runtime.project_runtime(
                self.repo,
                "persistent-cleanup-lock",
                reporter=reports.append,
            ) as run:
                runtime_root = run.root
                result = "successful-child-result"

        self.assertEqual(result, "successful-child-result")
        self.assertEqual(sleep.call_count, len(self.runtime.CLEANUP_RETRY_DELAYS))
        self.assertTrue(runtime_root.is_dir())
        self.assertEqual(len(reports), 1)
        self.assertIn("cleanup-permission-error", reports[0])
        self.assertIn(str(runtime_root), reports[0])

    def test_successful_system_exit_codes_remove_run_root_without_report(self):
        for code in (None, 0, False):
            with self.subTest(code=code):
                reports: list[str] = []

                with self.assertRaises(SystemExit) as caught:
                    with self.runtime.project_runtime(
                        self.repo,
                        "system-exit-success",
                        reporter=reports.append,
                    ) as run:
                        runtime_root = run.root
                        raise SystemExit(code)

                self.assertEqual(caught.exception.code, code)
                self.assertFalse(runtime_root.exists())
                self.assertEqual(reports, [])

    def test_unsuccessful_system_exit_codes_preserve_and_report_run_root(self):
        for code in (7, True, "failure", 0.0):
            with self.subTest(code=code):
                reports: list[str] = []

                with self.assertRaises(SystemExit) as caught:
                    with self.runtime.project_runtime(
                        self.repo,
                        "system-exit-failure",
                        reporter=reports.append,
                    ) as run:
                        runtime_root = run.root
                        raise SystemExit(code)

                self.assertEqual(caught.exception.code, code)
                self.assertTrue(runtime_root.is_dir())
                self.assertEqual(len(reports), 1)
                self.assertIn("exception:SystemExit", reports[0])
                self.assertIn(str(runtime_root), reports[0])

    def test_nonzero_timeout_and_exception_preserve_and_report_run_root(self):
        scenarios = (
            (
                "nonzero",
                lambda run: run.run(
                    [sys.executable, "-c", "raise SystemExit(7)"],
                    cwd=run.work_dir,
                    capture_output=True,
                    text=True,
                ),
            ),
            (
                "timeout",
                lambda run: run.run(
                    ["subject-agent"],
                    cwd=run.work_dir,
                    runner=lambda command, **kwargs: (_ for _ in ()).throw(
                        subprocess.TimeoutExpired(command, kwargs.get("timeout", 1))
                    ),
                    timeout=1,
                ),
            ),
            (
                "exception",
                lambda run: run.run(
                    ["subject-agent"],
                    cwd=run.work_dir,
                    runner=lambda command, **kwargs: (_ for _ in ()).throw(
                        OSError("launch failed")
                    ),
                ),
            ),
        )

        for name, action in scenarios:
            with self.subTest(name=name):
                reports: list[str] = []
                root: Path | None = None
                try:
                    with self.runtime.project_runtime(
                        self.repo,
                        name,
                        reporter=reports.append,
                    ) as run:
                        root = run.root
                        result = action(run)
                        if name == "nonzero":
                            self.assertEqual(result.returncode, 7)
                except (OSError, subprocess.TimeoutExpired):
                    pass

                self.assertIsNotNone(root)
                self.assertTrue(root.is_dir())
                self.assertEqual(len(reports), 1)
                self.assertIn(str(root), reports[0])
                shutil.rmtree(root)

    def test_real_subprocess_receives_requested_cwd_and_repo_local_temp_env(self):
        with self.runtime.project_runtime(self.repo, "child-propagation") as run:
            runtime_root = run.root
            completed = run.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json, os; "
                        "print(json.dumps({"
                        "'cwd': os.getcwd(), 'TEMP': os.environ.get('TEMP'), "
                        "'TMP': os.environ.get('TMP'), "
                        "'TMPDIR': os.environ.get('TMPDIR'), "
                        "'PYTHONPYCACHEPREFIX': os.environ.get('PYTHONPYCACHEPREFIX')}))"
                    ),
                ],
                cwd=run.work_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(Path(payload["cwd"]).resolve(), run.work_dir.resolve())
            for key in TEMP_KEYS:
                self.assertEqual(Path(payload[key]).resolve(), run.temp_dir.resolve())
            self.assertEqual(
                Path(payload["PYTHONPYCACHEPREFIX"]).resolve(),
                run.pycache_dir.resolve(),
            )

        self.assertFalse(runtime_root.exists())


if __name__ == "__main__":
    unittest.main()
