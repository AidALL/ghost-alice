from __future__ import annotations

import subprocess
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"


def _extract_function(source: str, name: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"{name}() {{"):
            start = idx
            break

    if start is None:
        raise AssertionError(f"Function not found: {name}")

    body = []
    depth = 0
    for line in lines[start:]:
        body.append(line)
        depth += line.count("{")
        depth -= line.count("}")
        if depth == 0:
            return "".join(body)

    raise AssertionError(f"Function did not terminate: {name}")


def _find_test_bash() -> str | None:
    candidates = [
        shutil.which("bash"),
        shutil.which("bash.exe"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]

    for candidate in candidates:
        if not candidate:
            continue

        path = Path(candidate)
        if not path.exists():
            continue

        normalized = path.as_posix().lower()
        if sys.platform.startswith("win") and (
            normalized.endswith("/windows/system32/bash.exe")
            or normalized.endswith("/appdata/local/microsoft/windowsapps/bash.exe")
        ):
            continue

        return str(path)

    return None


def _bash_path(path: Path) -> str:
    path_text = path.resolve().as_posix()
    if len(path_text) >= 3 and path_text[1:3] == ":/":
        return f"/{path_text[0].lower()}{path_text[2:]}"
    return path_text


class InstallRuntimeDetectionTest(unittest.TestCase):
    def test_find_runtime_skips_python3_store_stub(self) -> None:
        bash_exe = _find_test_bash()
        if not bash_exe:
            self.skipTest("No non-WSL bash executable available for install.sh runtime test")

        source = installer_bash_source()
        function_bundle = "\n".join(
            _extract_function(source, name)
            for name in ("_is_working_python", "_find_python_runtime", "_find_runtime")
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = Path(temp_dir) / "runner.sh"
            runner.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -euo pipefail

                    python3() {{
                      echo Python
                      return 49
                    }}

                    python() {{
                      if [ "${{1:-}}" = "-c" ]; then
                        return 0
                      fi
                      echo real-python
                    }}

                    {function_bundle}

                    _find_runtime
                    """
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [bash_exe, str(runner)],
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "python:python")

    def test_find_runtime_uses_versioned_python_311_or_newer_without_hardcoded_list(self) -> None:
        bash_exe = _find_test_bash()
        if not bash_exe:
            self.skipTest("No non-WSL bash executable available for install.sh runtime test")

        source = installer_bash_source()
        function_bundle = "\n".join(
            _extract_function(source, name)
            for name in ("_is_working_python", "_python_version_key", "_find_python_runtime", "_find_runtime")
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            bash_bin_dir = _bash_path(bin_dir)
            versioned_python = bin_dir / "python3.14"
            bash_versioned_python = _bash_path(versioned_python)
            versioned_python.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    if [ "${1:-}" = "-c" ]; then
                      case "${2:-}" in
                        *"sys.version_info >= (3, 11)"*) exit 0 ;;
                        *"version_info.major"*) printf '003.014.002\\n'; exit 0 ;;
                      esac
                    fi
                    exit 1
                    """
                ),
                encoding="utf-8",
            )
            versioned_python.chmod(0o755)

            runner = Path(temp_dir) / "runner.sh"
            runner.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    PATH="{bash_bin_dir}"
                    GHOST_ALICE_TEST_SKIP_COMMON_PYTHON_PATHS=1

                    python3() {{ return 1; }}
                    python() {{ return 1; }}

                    {function_bundle}

                    _find_runtime
                    """
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [bash_exe, str(runner)],
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), f"python:{bash_versioned_python}")

    def test_install_sh_bootstraps_python_for_default_install_only(self) -> None:
        install_sh = installer_bash_source()

        self.assertIn("_try_install_python_runtime()", install_sh)
        self.assertIn("_ensure_python_runtime_for_install()", install_sh)
        self.assertIn("_ensure_python_runtime_for_install || exit 1", install_sh)
        self.assertIn("brew install python3", install_sh)
        self.assertIn("apt-get install -y python3", install_sh)
        self.assertIn("winget.exe install --id Python.Python.3 --exact", install_sh)
        self.assertNotIn("Python.Python.3.13", install_sh)
        self.assertNotIn("Python.Python.3.14", install_sh)

        default_branch = install_sh.split("*)", 1)[-1]
        self.assertIn("_ensure_python_runtime_for_install || exit 1", default_branch)
        self.assertNotIn("_ensure_python_runtime_for_install || exit 1\n    list_skills", install_sh)
        self.assertNotIn("_ensure_python_runtime_for_install || exit 1\n    check_status", install_sh)
        self.assertNotIn("_ensure_python_runtime_for_install || exit 1\n    run_doctor", install_sh)

    def test_install_sh_python_bootstrap_rechecks_runtime_after_package_manager(self) -> None:
        bash_exe = _find_test_bash()
        if not bash_exe:
            self.skipTest("No non-WSL bash executable available for install.sh runtime test")

        source = installer_bash_source()
        function_bundle = "\n".join(
            _extract_function(source, name)
            for name in (
                "_is_working_python",
                "_python_version_key",
                "_find_python_runtime",
                "_try_install_python_runtime",
                "_python_required_notice",
                "_ensure_python_runtime_for_install",
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            marker = Path(temp_dir) / "brew-called"
            bash_bin_dir = _bash_path(bin_dir)
            bash_marker = _bash_path(marker)
            chmod_exe = shutil.which("chmod") or "/bin/chmod"
            fake_python = bin_dir / "python3"
            bash_fake_python = _bash_path(fake_python)
            fake_python.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    [ -f "{bash_marker}" ] || exit 1
                    if [ "${1:-}" = "-c" ]; then
                      case "${2:-}" in
                        *"sys.version_info >= (3, 11)"*) exit 0 ;;
                        *"version_info.major"*) printf '003.011.009\\n'; exit 0 ;;
                      esac
                    fi
                    exit 1
                    """
                ),
                encoding="utf-8",
            )

            brew = bin_dir / "brew"
            brew.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    [ "${{1:-}}" = "install" ] || exit 2
                    [ "${{2:-}}" = "python3" ] || exit 3
                    : > "{bash_marker}"
                    "{chmod_exe}" +x "{bash_fake_python}"
                    exit 0
                    """
                ),
                encoding="utf-8",
            )
            brew.chmod(0o755)

            runner = Path(temp_dir) / "runner.sh"
            runner.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    PATH="{bash_bin_dir}"
                    GHOST_ALICE_TEST_SKIP_COMMON_PYTHON_PATHS=1
                    t() {{ printf '%s' "$1"; }}
                    info() {{ :; }}
                    warn() {{ :; }}
                    ok() {{ :; }}
                    error() {{ :; }}

                    {function_bundle}

                    _ensure_python_runtime_for_install
                    test -f "{bash_marker}"
                    _find_python_runtime
                    """
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [bash_exe, str(runner)],
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "python3")

    def test_install_sh_reuses_verified_python_detection(self) -> None:
        install_sh = installer_bash_source()

        self.assertIn("_is_working_python()", install_sh)
        self.assertIn("_python_version_key()", install_sh)
        self.assertIn("sys.version_info >= (3, 11)", install_sh)
        self.assertIn("Python 3.11+ is required", install_sh)
        self.assertIn("_find_python_runtime()", install_sh)
        self.assertIn('py_cmd="$(_find_python_runtime || true)"', install_sh)
        self.assertIn('py="$(_find_python_runtime || true)"', install_sh)

    def test_install_ps1_requires_python_311_or_newer_without_upper_bound(self) -> None:
        install_ps1 = installer_ps1_source()

        self.assertIn("sys.version_info >= (3, 11)", install_ps1)
        self.assertIn("python3*", install_ps1)
        self.assertIn("Install-PythonRuntime", install_ps1)
        self.assertIn("Initialize-PythonRuntimeForInstall", install_ps1)
        self.assertIn("Python.Python.3", install_ps1)
        self.assertIn("LOCALAPPDATA", install_ps1)
        self.assertIn("ProgramFiles", install_ps1)
        self.assertNotIn("3, 12", install_ps1)
        self.assertNotIn("3, 13", install_ps1)
        self.assertNotIn("3, 14", install_ps1)
        self.assertNotIn("Python.Python.3.13", install_ps1)
        self.assertNotIn("Python.Python.3.14", install_ps1)

    def test_install_ps1_python_version_key_runs_under_powershell(self) -> None:
        if not sys.platform.startswith("win"):
            self.skipTest("PowerShell native argument regression is Windows-specific")
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if not powershell:
            self.skipTest("PowerShell executable is required for python_runtime.ps1 test")

        python_exe = sys.executable.replace("'", "''")
        script = textwrap.dedent(
            f"""
            $ErrorActionPreference = "Stop"
            $script:GhostAliceRoot = (Get-Location).Path
            . (Join-Path $script:GhostAliceRoot "installer_lib/python_runtime.ps1")
            $key = Get-PythonVersionKey '{python_exe}'
            if (-not $key) {{
                Write-Output "KEY_MISSING"
                exit 1
            }}
            Write-Output "KEY=$key"
            """
        )

        result = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertRegex(result.stdout, r"KEY=\d{3}\.\d{3}\.\d{3}")

    def test_install_ps1_avoids_pwsh_only_join_path_overload(self) -> None:
        install_ps1 = installer_ps1_source()

        self.assertNotRegex(
            install_ps1,
            r"Join-Path\s+\$[A-Za-z0-9_:]+\s+['\"][^'\"]+['\"]\s+['\"][^'\"]+['\"]",
        )

    def test_install_sh_has_no_node_or_bash_hook_fallback(self) -> None:
        install_sh = installer_bash_source()

        self.assertNotIn("Node.js fallback", install_sh)
        self.assertNotIn("pure bash fallback", install_sh)
        self.assertNotIn("_install_hook_bash_native", install_sh)
        self.assertNotIn("prompt + completion + io-trace", install_sh)
        self.assertIn("pending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace", install_sh)

    def test_install_ps1_has_no_partial_native_hook_fallback(self) -> None:
        install_ps1 = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8-sig")

        self.assertNotIn("Invoke-InstallHooksNative", install_ps1)
        self.assertNotIn("PowerShell native", install_ps1)
        self.assertNotIn("PowerShell native", install_ps1)
        self.assertNotIn("prompt + completion", install_ps1)


if __name__ == "__main__":
    unittest.main()
