#!/usr/bin/env python3
"""Run the installer compatibility test matrix."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


PYTHON_RUNTIME_CONTRACT = "sys.version_info >= (3, 11)"
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class TestGroup:
    name: str
    description: str
    command: tuple[str, ...]


def _py(*args: str) -> tuple[str, ...]:
    return (sys.executable, *args)


TEST_GROUPS: tuple[TestGroup, ...] = (
    TestGroup(
        "merge-companion-v2",
        "snapshot v2 and diff collector compatibility",
        _py("-m", "unittest", "discover", "-s", "merge-companion/scripts", "-p", "test_*_v2.py"),
    ),
    TestGroup(
        "merge-companion-core",
        "manifest, snapshot, and diff collector core tests",
        _py("-m", "unittest", "discover", "-s", "merge-companion/scripts"),
    ),
    TestGroup(
        "installer-runtime-detection",
        "Python 3.11+ runtime lookup contracts",
        _py(
            "-m",
            "unittest",
            "scripts.tests.test_install_runtime_detection",
            "scripts.tests.test_installer_default_auto",
            "scripts.tests.test_installer_compat_matrix",
        ),
    ),
    TestGroup(
        "installer-cmd-wrapper",
        "CMD wrapper delegation, UTF-8, and argument forwarding",
        _py("-m", "unittest", "scripts.tests.test_install_cmd_wrapper"),
    ),
    TestGroup(
        "public-surface-contract",
        "README, homepage, and command wrapper parity with the skill catalog",
        _py("-m", "unittest", "scripts.tests.test_validate_public_surfaces"),
    ),
    TestGroup(
        "installer-encoding",
        "UTF-8, PowerShell BOM, shell inline Python, and semantic asset encoding",
        _py(
            "-m",
            "unittest",
            "scripts.tests.test_install_ps1_encoding",
            "scripts.tests.test_install_sh_inline_python_encoding",
            "scripts.tests.test_encoding_guard",
        ),
    ),
    TestGroup(
        "installer-powershell-static",
        "PowerShell parser and optional PSScriptAnalyzer warning budget",
        _py("-m", "unittest", "scripts.tests.test_powershell_static_analysis"),
    ),
    TestGroup(
        "installer-status-contract",
        "asset ownership, global rule, doctor/status, and snapshot freshness",
        _py(
            "-m",
            "unittest",
            "scripts.tests.test_installer_asset_inventory",
            "scripts.tests.test_global_rule_blocks",
            "scripts.tests.test_install_status_contract",
        ),
    ),
    TestGroup(
        "installer-transaction",
        "preflight quarantine, staged replacement, rollback, postflight, and install-state",
        _py(
            "-m",
            "unittest",
            "scripts.tests.test_install_preflight_quarantine",
            "scripts.tests.test_install_transaction",
            "scripts.tests.test_install_postflight_validation",
            "scripts.tests.test_install_state_manifest",
        ),
    ),
    TestGroup(
        "shared-install-hooks",
        "hook install/status suite and merge-companion prompt wording",
        _py(
            "-m",
            "unittest",
            "_shared.test_install_hooks",
            "_shared.test_merge_companion_messages",
        ),
    ),
)


def _check_python_runtime() -> int:
    if sys.version_info >= (3, 11):
        return 0
    print("Python 3.11+ is required; no upper bound is enforced.", file=sys.stderr)
    return 2


def _selected_groups(names: Sequence[str]) -> list[TestGroup]:
    if not names:
        return list(TEST_GROUPS)
    by_name = {group.name: group for group in TEST_GROUPS}
    missing = [name for name in names if name not in by_name]
    if missing:
        known = ", ".join(sorted(by_name))
        raise SystemExit(f"unknown group(s): {', '.join(missing)}; known: {known}")
    return [by_name[name] for name in names]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault(
        "PYTHONPYCACHEPREFIX",
        str(Path(tempfile.gettempdir()) / "ghost-alice-installer-compat-pycache"),
    )
    return env


def _list_groups() -> None:
    for group in TEST_GROUPS:
        print(f"{group.name}: {' '.join(group.command)}")


def run(groups: Sequence[TestGroup]) -> int:
    runtime_rc = _check_python_runtime()
    if runtime_rc:
        return runtime_rc

    env = _env()
    for group in groups:
        print(f"== {group.name}: {group.description}", flush=True)
        result = subprocess.run(group.command, cwd=REPO_ROOT, env=env, check=False)
        if result.returncode != 0:
            print(f"FAILED {group.name}: exit {result.returncode}", file=sys.stderr)
            return result.returncode
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List compatibility test groups and exit.")
    parser.add_argument("--group", action="append", default=[], help="Run only the named group. Repeatable.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.list:
        _list_groups()
        return 0
    return run(_selected_groups(args.group))


if __name__ == "__main__":
    raise SystemExit(main())
