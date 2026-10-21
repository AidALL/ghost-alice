"""The git hook paths of a checkout's repository, read for the checks around test runs.

The installer writes the shared `core.hooksPath` of the repository it runs from, which every worktree of that
repository inherits. The test runner and the checkout copies the end-to-end tests install from compare these values
before and after a run, so a test that installs from the developer's checkout fails instead of changing it.

Dependencies: Python standard library and git.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """git without the variables a git process exports to the hooks it runs, so `-C` finds the named repository."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="surrogateescape",
                          env=env, check=check)


def hook_paths(checkout: Path | str) -> dict[str, list[str]]:
    """The `core.hooksPath` values of the repository that holds `checkout`, by configuration file: the shared value
    every worktree inherits and the checkout's own per-worktree value. Empty outside git."""
    found = git("-C", str(checkout), "rev-parse", "--path-format=absolute", "--git-common-dir",
                "--git-path", "config.worktree")
    lines = (found.stdout or "").splitlines()
    if found.returncode != 0 or len(lines) != 2:
        return {}
    values: dict[str, list[str]] = {}
    for config in (Path(lines[0]) / "config", Path(lines[1])):
        if config.is_file():
            read = git("config", "--file", str(config), "--get-all", "core.hooksPath")
            values[str(config)] = read.stdout.splitlines() if read.returncode == 0 else []
    return values
