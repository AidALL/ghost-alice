#!/usr/bin/env python3
"""Repository-local scratch space and environment for child processes."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


TEMP_ENV_KEYS = ("TEMP", "TMP", "TMPDIR")
CLEANUP_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)
RETRYABLE_WINDOWS_CLEANUP_ERRORS = frozenset({5, 32, 145})


def _default_reporter(message: str) -> None:
    print(message, file=sys.stderr)


def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.")
    return cleaned or "run"


def _is_successful_system_exit(exception: BaseException | None) -> bool:
    if not isinstance(exception, SystemExit):
        return False
    code = exception.code
    return code is None or (isinstance(code, int) and code == 0)


def _is_retryable_cleanup_error(exception: OSError) -> bool:
    return isinstance(exception, PermissionError) or getattr(exception, "winerror", None) in RETRYABLE_WINDOWS_CLEANUP_ERRORS


class ProjectRuntime:
    """Own one unique repository-local run root and its child environment."""

    def __init__(
        self,
        repo_root: Path | str,
        label: str,
        *,
        reporter: Callable[[str], None] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        runtime_parent = self.repo_root / ".tmp" / "runs"
        runtime_parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(
            tempfile.mkdtemp(prefix=f"{_safe_label(label)}-", dir=runtime_parent)
        ).resolve()
        self.temp_dir = self.root / "tmp"
        self.pycache_dir = self.root / "pycache"
        self.work_dir = self.root / "work"
        for path in (self.temp_dir, self.pycache_dir, self.work_dir):
            path.mkdir()
        self._reporter = reporter or _default_reporter
        self._preserve_reason: str | None = None
        self._reported = False

    def __enter__(self) -> "ProjectRuntime":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is not None and not _is_successful_system_exit(exc):
            self.preserve(f"exception:{exc_type.__name__}")
        if self._preserve_reason is None:
            for retry_delay in (*CLEANUP_RETRY_DELAYS, None):
                try:
                    shutil.rmtree(self.root)
                    break
                except FileNotFoundError:
                    break
                except OSError as cleanup_error:
                    if not _is_retryable_cleanup_error(cleanup_error):
                        raise
                    if retry_delay is None:
                        winerror = getattr(cleanup_error, "winerror", None)
                        reason = "cleanup-permission-error" if isinstance(cleanup_error, PermissionError) else f"cleanup-winerror-{winerror}"
                        self.preserve(reason)
                        break
                    time.sleep(retry_delay)
        return False

    def child_env(self, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return a copy for a child process without mutating the parent."""

        env = dict(os.environ if base_env is None else base_env)
        for key in TEMP_ENV_KEYS:
            env[key] = str(self.temp_dir)
        env["PYTHONPYCACHEPREFIX"] = str(self.pycache_dir)
        return env

    def preserve(self, reason: str) -> None:
        """Keep this run root for diagnosis and report its exact path once."""

        if self._preserve_reason is None:
            self._preserve_reason = reason
        if not self._reported:
            self._reporter(f"[project-runtime] preserved run at {self.root} ({self._preserve_reason})")
            self._reported = True

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | str | None,
        env: Mapping[str, str] | None = None,
        runner: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run a child with local temp paths and preserve failures."""

        process_runner = runner or subprocess.run
        try:
            completed = process_runner(
                list(command),
                cwd=cwd,
                env=self.child_env(env),
                **kwargs,
            )
        except subprocess.TimeoutExpired:
            self.preserve("timeout")
            raise
        except BaseException as exc:
            self.preserve(f"exception:{type(exc).__name__}")
            raise
        returncode = getattr(completed, "returncode", None)
        if returncode not in (None, 0):
            self.preserve(f"exit-code:{returncode}")
        return completed


def project_runtime(
    repo_root: Path | str,
    label: str,
    *,
    reporter: Callable[[str], None] | None = None,
) -> ProjectRuntime:
    """Create a context-managed repository-local child-process runtime."""

    return ProjectRuntime(repo_root, label, reporter=reporter)
