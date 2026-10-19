"""Run the existing Python entry points as child processes.

One owner for "the installer ends with code N" (`InstallerExit`) and for
"call `_shared/<tool>.py` or `merge-companion/scripts/<tool>.py` with these
arguments" (`run_tool`). The shell did the second with
`"$py" "${SCRIPT_DIR}/<tool>" args` at 23 call sites; the Python port keeps the
arguments and the child's own output and exit code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import sys
from typing import Mapping, Sequence

from installer import report

# `_shared/installer/runner.py` → the checkout root two levels up.
REPO_ROOT = Path(__file__).resolve().parents[2]


class InstallerExit(Exception):
    """The command ends with this exit code; `cli.main` turns it into the process exit."""

    def __init__(self, code: int = 1):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ToolResult:
    returncode: int
    stdout: str | None = None
    stderr: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def python_executable() -> str:
    """The interpreter that runs the child tools: this process's own."""
    return sys.executable


def run_tool(
    script: str,
    *args: str,
    capture: bool = False,
    env: Mapping[str, str] | None = None,
    no_bytecode: bool = False,
    cwd: Path | None = None,
) -> ToolResult:
    """Run `<REPO_ROOT>/<script>` with `args` under this interpreter.

    `script` is repository-relative (`_shared/install_hooks.py`,
    `merge-companion/scripts/snapshot_cli.py`). Without `capture`, the child
    writes to the installer's current output sink (`report.sink()`), which is
    the terminal or the install log in compact mode; stderr follows stdout
    there, like the shell's `>>"$LOG" 2>&1`. With `capture`, stdout and stderr
    are returned as text. `env` adds to the inherited environment.
    `no_bytecode` passes `-B` (the shell did this for `retired_assets.py`).
    """
    argv = [python_executable()]
    if no_bytecode:
        argv.append("-B")
    argv.append(str(REPO_ROOT / script))
    argv.extend(str(a) for a in args)
    return run_command(argv, capture=capture, env=env, cwd=cwd)


def popen(argv: Sequence[str], *, env: Mapping[str, str] | None = None, stdout=None) -> subprocess.Popen:
    """Start a child that keeps running while the caller works (auto mode's per-platform installs).

    `env` adds to the inherited environment; `stdout` is a file object the
    child's stdout and stderr go to (the install log), or None to inherit.
    """
    merged = dict(os.environ)
    if env:
        merged.update(env)
    if stdout is not None:
        stdout.flush()
    return subprocess.Popen([str(a) for a in argv], env=merged, stdin=subprocess.DEVNULL, stdout=stdout,
                            stderr=subprocess.STDOUT if stdout is not None else None)


def run_command(argv: Sequence[str], *, capture: bool = False, env: Mapping[str, str] | None = None,
                cwd: Path | None = None) -> ToolResult:
    """Run any command (git, brew, apt-get ...) with the same sink rules as `run_tool`."""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    kwargs: dict = dict(cwd=cwd, env=merged, stdin=subprocess.DEVNULL)
    sink = report.sink()
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    elif sink is not None:
        sink.flush()
        kwargs.update(stdout=sink, stderr=subprocess.STDOUT)
    else:
        sys.stdout.flush()
    try:
        completed = subprocess.run([str(a) for a in argv], **kwargs)
    except FileNotFoundError as exc:
        return ToolResult(127, "" if capture else None, str(exc) if capture else None)
    return ToolResult(completed.returncode, completed.stdout if capture else None,
                      completed.stderr if capture else None)
