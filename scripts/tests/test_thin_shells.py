"""The OS entry points only find or install Python and run the installer cli."""
from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import powershell_function  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
INSTALL_CMD = REPO_ROOT / "install.cmd"

# The parameter names of the earlier install.ps1 param block and the cli flag each becomes.
# `-Skills` disappears: the skill names after it pass through as they are.
POWERSHELL_PARAMETER_FLAGS = {
    "-Skills": "",
    "-Platform": "--platform",
    "-PromptPlatform": "--prompt-platform",
    "-Auto": "--auto",
    "-List": "--list",
    "-Status": "--status",
    "-Doctor": "--doctor",
    "-Uninstall": "--uninstall",
    "-Force": "--force",
    "-CleanupPending": "--cleanup-pending",
    "-Addon": "--addon",
    "-AddonSource": "--addon-source",
    "-AddonTag": "--addon-tag",
    "-AddonSkip": "--addon-skip",
    "-ListAddons": "--list-addons",
    "-AgentVisibility": "--visibility",
    "-Visibility": "--visibility",
    "-UpdateSource": "--update-source",
    "-SkipSourceHealth": "--skip-source-health",
    "-DestinationFile": "--destination-file",
    "-Help": "--help",
}
PYTHON_RUNTIME_FUNCTIONS = ("Test-Python311OrNewer", "Get-PythonVersionKey", "Find-PythonExe", "Install-PythonRuntime",
                            "Initialize-PythonRuntimeForInstall")
REQUIREMENT = "PowerShell 7.6 or newer is required"
WINGET_POWERSHELL = "winget install --id Microsoft.PowerShell --exact"


class ThinInstallShTests(unittest.TestCase):
    def setUp(self):
        self.text = INSTALL_SH.read_text(encoding="utf-8")

    def test_no_bash_library_remains(self):
        self.assertNotIn("installer_lib", self.text)
        self.assertEqual(sorted((REPO_ROOT / "installer_lib").glob("*.sh")), [])

    def test_runs_the_python_cli(self):
        self.assertIn('_shared/installer/cli.py', self.text)
        self.assertRegex(self.text, r'exec "\$py" "\$\{SCRIPT_DIR\}/_shared/installer/cli\.py" "\$@"')

    def test_keeps_the_bash_guard_and_runtime_detection(self):
        # install.sh:19-25; installer_lib/python_runtime.sh functions move here by name
        self.assertIn('if [ -z "${BASH_VERSION:-}" ]; then', self.text)
        for name in ("ensure_utf8_locale", "_is_working_python", "_python_version_key", "_find_python_runtime",
                     "_try_install_python_runtime", "_ensure_python_runtime_for_install", "_python_required_notice"):
            self.assertRegex(self.text, rf"(?m)^{re.escape(name)}\(\) \{{", msg=name)
        self.assertIn("export PYTHONUTF8=1", self.text)
        self.assertIn("export PYTHONIOENCODING=utf-8", self.text)
        self.assertIn('"$cmd" -c \'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)\'', self.text)

    def test_no_installer_logic_in_the_shell(self):
        for name in ("install", "uninstall", "check_source_health", "run_doctor", "_run_install_hooks",
                     "collect_install_targets", "print_install_report_tail"):
            self.assertNotRegex(self.text, rf"(?m)^{re.escape(name)}\(\)", msg=name)
        for token in ("ALL_SKILLS=(", "install_lock.py", "install_transaction.py", 'case "${ARGS[0]:-}" in'):
            self.assertNotIn(token, self.text, msg=token)
        self.assertLess(self.text.count("\n"), 220)

    @unittest.skipUnless(shutil.which("bash"), "bash is required")
    def test_help_goes_through_the_cli(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home, "GHOST_ALICE_INSTALL_PROGRESS": "off"}
            result = subprocess.run(["bash", str(INSTALL_SH), "--help"], cwd=REPO_ROOT, env=env,
                                    capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("  bash install.sh                              # Install to detected AI tools", result.stdout)

    @unittest.skipUnless(shutil.which("zsh") and shutil.which("bash"), "zsh and bash are required")
    def test_zsh_invocation_re_executes_under_bash(self):
        result = subprocess.run(["zsh", str(INSTALL_SH), "--list"], cwd=REPO_ROOT,
                                env={**os.environ, "GHOST_ALICE_INSTALL_PROGRESS": "off"},
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Available skills:", result.stdout)
        self.assertNotIn("BASH_SOURCE", result.stderr)


class ThinInstallPs1Tests(unittest.TestCase):
    """install.ps1 keeps the PowerShell 7.6 guard, the UTF-8 console, the Python 3.11+
    detection, the earlier parameter names and one cli call; nothing else."""

    def setUp(self):
        self.text = INSTALL_PS1.read_text(encoding="utf-8-sig")

    def test_no_powershell_library_remains(self):
        self.assertNotIn("installer_lib", self.text)
        self.assertFalse((REPO_ROOT / "installer_lib").exists())

    def test_has_no_script_parameter_block(self):
        # PowerShell would bind, position and reorder the arguments; the cli owns every option.
        self.assertNotRegex(self.text, r"(?m)^param\s*\(")
        self.assertNotIn("[CmdletBinding", self.text)

    def test_version_guard_is_the_first_statement(self):
        statements = [line for line in self.text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        guard = powershell_function(self.text, "Assert-PowerShellBaseline")
        self.assertEqual(statements[0], "function Assert-PowerShellBaseline {")
        guard_lines = [line for line in guard.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(statements[len(guard_lines)], "Assert-PowerShellBaseline -Version $PSVersionTable.PSVersion")
        self.assertIn(REQUIREMENT, guard)
        self.assertIn(WINGET_POWERSHELL, guard)
        self.assertIn("exit 1", guard)
        self.assertIn("[Console]::Error.WriteLine", guard)
        self.assertRegex(guard, r"\$Version\.Major -eq 7 -and \$Version\.Minor -ge 6")

    def test_sets_utf8_console_and_python_io_after_the_guard(self):
        for line in ("$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)", "$OutputEncoding = $Utf8NoBom",
                     "[Console]::OutputEncoding = $Utf8NoBom", "[Console]::InputEncoding = $Utf8NoBom",
                     '$env:PYTHONUTF8 = "1"', '$env:PYTHONIOENCODING = "utf-8"',
                     '$env:GHOST_ALICE_INSTALL_COMMAND = ".\\install.cmd"'):
            self.assertIn(line, self.text)
            self.assertLess(self.text.index("Assert-PowerShellBaseline -Version"), self.text.index(line), msg=line)
            self.assertLess(self.text.index(line), self.text.index("$python = Initialize-PythonRuntimeForInstall"), msg=line)
        self.assertNotIn("chcp", self.text)

    def test_keeps_the_python_runtime_detection_by_name(self):
        # installer_lib/python_runtime.ps1 functions move here by name.
        for name in PYTHON_RUNTIME_FUNCTIONS:
            self.assertRegex(self.text, rf"(?m)^function {re.escape(name)} \{{", msg=name)
        self.assertIn("sys.version_info >= (3, 11)", self.text)
        for location in ('"python3*"', "$env:LOCALAPPDATA", "$env:ProgramFiles", "${env:ProgramFiles(x86)}"):
            self.assertIn(location, powershell_function(self.text, "Find-PythonExe"), msg=location)
        installer = powershell_function(self.text, "Install-PythonRuntime")
        self.assertLess(installer.index("winget"), installer.index("choco"))
        self.assertLess(installer.index("choco"), installer.index("scoop"))
        self.assertIn("Python.Python.3 --exact", installer)

    def test_maps_only_the_earlier_powershell_parameter_names(self):
        table = self.text[self.text.index("$PowerShellParameterFlags = @{"):]
        table = table[:table.index("}") + 1]
        self.assertEqual(dict(re.findall(r'"(-[A-Za-z]+)"\s*=\s*"([^"]*)"', table)), POWERSHELL_PARAMETER_FLAGS)
        convert = powershell_function(self.text, "ConvertTo-InstallerArgument")
        self.assertIn('$text.StartsWith("--")', convert)
        self.assertIn('TrimEnd(":")', convert)

    def test_runs_the_python_cli_once_and_exits_with_its_code(self):
        self.assertEqual(self.text.count("_shared/installer/cli.py"), 1)
        self.assertRegex(
            self.text,
            r'\$installerArguments = ConvertTo-InstallerArgument -Arguments \$args\n'
            r'& \$python \(Join-Path -Path \$PSScriptRoot -ChildPath "_shared/installer/cli\.py"\) @installerArguments\n'
            r'exit \$LASTEXITCODE\n?$')

    def test_no_installer_logic_in_the_shell(self):
        for token in ("install_hooks.py", "install_lock.py", "install_transaction.py", "$AllSkills", "Invoke-Install",
                      "Get-DetectedPlatforms", "PwshLts", "msiexec", "Remove-Item", "session-gates.json",
                      "Unknown argument"):
            self.assertNotIn(token, self.text, msg=token)
        self.assertLess(self.text.count("\n"), 200)

    def test_messages_and_comments_are_english(self):
        self.assertEqual([line for line in self.text.splitlines() if re.search(r"[가-힣]", line)], [])
        for token in ("CurrentUICulture", "CurrentCulture", "Test-KoreanInstallLanguage"):
            self.assertNotIn(token, self.text)


def _pwsh() -> str | None:
    return shutil.which("pwsh")


@unittest.skipUnless(_pwsh(), "pwsh is required")
class ThinInstallPs1RuntimeTests(unittest.TestCase):
    """Run install.ps1 under PowerShell 7.6+ (the CI runner's pwsh)."""

    def run_ps1(self, *args: str, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run([_pwsh(), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INSTALL_PS1),
                               *args], cwd=REPO_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)

    def test_guard_refuses_powershell_before_7_6(self):
        guard = powershell_function(INSTALL_PS1.read_text(encoding="utf-8-sig"), "Assert-PowerShellBaseline")
        with tempfile.TemporaryDirectory() as temp:
            for version, code in (("5.1.22621.4391", 1), ("7.5.4", 1), ("6.2.7", 1), ("7.6.0", 0), ("8.0.1", 0)):
                with self.subTest(version=version):
                    script = Path(temp) / "guard.ps1"
                    script.write_text(guard + f"\nAssert-PowerShellBaseline -Version ([version]'{version}')\nexit 0\n",
                                      encoding="utf-8-sig")
                    result = subprocess.run([_pwsh(), "-NoLogo", "-NoProfile", "-File", str(script)], capture_output=True,
                                            text=True, encoding="utf-8", errors="replace", timeout=60)
                    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                    if code:
                        self.assertIn(REQUIREMENT, result.stderr)
                        self.assertIn(WINGET_POWERSHELL, result.stderr)
                        self.assertIn(version, result.stderr)
                    else:
                        self.assertEqual(result.stderr, "")

    @unittest.skipUnless(os.name == "posix", "the recording python is a POSIX shell script")
    def test_maps_the_earlier_names_keeps_the_rest_in_order_and_returns_the_cli_code(self):
        cases = (
            (["-Platform", "codex", "-SkipSourceHealth", "-Skills", "task-router", "merge-companion"],
             ["--platform", "codex", "--skip-source-health", "task-router", "merge-companion"]),
            (["--platform", "claude", "--verbose", "--repair"], ["--platform", "claude", "--verbose", "--repair"]),
            (["-Uninstall", "-Addon", "autopilot-mode", "-Force", "-platform:codex"],
             ["--uninstall", "--addon", "autopilot-mode", "--force", "--platform", "codex"]),
            (["gateway", "detach", "--root", "/x"], ["gateway", "detach", "--root", "/x"]),
            (["-ListAddons", "-AddonSource", "/a b", "-AddonTag", "v1", "-AddonSkip", "-AgentVisibility", "strict",
              "-Visibility", "minimal"],
             ["--list-addons", "--addon-source", "/a b", "--addon-tag", "v1", "--addon-skip", "--visibility", "strict",
              "--visibility", "minimal"]),
            (["-List", "-Status", "-Doctor", "-CleanupPending", "-UpdateSource", "-PromptPlatform", "-Auto", "-Help"],
             ["--list", "--status", "--doctor", "--cleanup-pending", "--update-source", "--prompt-platform", "--auto",
              "--help"]),
            (["-DestinationFile", "x.json", "-Offboard", "-u"], ["--destination-file", "x.json", "-Offboard", "-u"]),
            ([], []),
        )
        with tempfile.TemporaryDirectory() as temp:
            bin_dir = Path(temp) / "bin"
            bin_dir.mkdir()
            recorded = Path(temp) / "args.json"
            fake = bin_dir / "python3"
            fake.write_text(
                "#!/bin/sh\n"
                f'if [ "$1" = "-c" ]; then exec "{sys.executable}" "$@"; fi\n'
                f'"{sys.executable}" -c \'import json, sys; open(sys.argv[1], "w", encoding="utf-8").write('
                'json.dumps(sys.argv[2:]))\' "$GHOST_ALICE_TEST_ARGS_FILE" "$@"\n'
                "exit 7\n", encoding="utf-8")
            fake.chmod(0o755)
            env = {key: value for key, value in os.environ.items()
                   if key not in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)")}
            env.update(PATH=str(bin_dir), GHOST_ALICE_TEST_ARGS_FILE=str(recorded))
            for argv, expected in cases:
                with self.subTest(argv=argv):
                    recorded.unlink(missing_ok=True)
                    result = self.run_ps1(*argv, env=env)
                    self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
                    forwarded = json.loads(recorded.read_text(encoding="utf-8"))
                    self.assertEqual(Path(forwarded[0]), REPO_ROOT / "_shared" / "installer" / "cli.py")
                    self.assertEqual(forwarded[1:], expected)

    def test_missing_python_stops_with_the_requirement(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {key: value for key, value in os.environ.items()
                   if key not in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)")}
            env["PATH"] = temp
            result = self.run_ps1("--help", env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("[WARN] No supported Python installer was found.", result.stdout)
        self.assertIn("[ERROR] Python 3.11+ is required.", result.stderr)
        self.assertNotIn("Usage:", result.stdout)

    def test_help_comes_from_the_cli_and_names_the_windows_command(self):
        for flag in ("--help", "-Help"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as home:
                result = self.run_ps1(flag, env={**os.environ, "HOME": home, "GHOST_ALICE_INSTALL_PROGRESS": "off"})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("  .\\install.cmd" + " " * 32 + "# Install to detected AI tools", result.stdout)
                self.assertNotIn("bash install.sh", result.stdout)
                self.assertIn("  --repair               Re-provision missing managed targets", result.stdout)

    def test_install_through_powershell_names(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / ".codex").mkdir()
            env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(home / ".codex"),
                   "GHOST_ALICE_INSTALL_PROGRESS": "off"}
            env.pop("CLAUDE_CONFIG_DIR", None)
            result = self.run_ps1("-Platform", "codex", "-SkipSourceHealth", "-Skills", "coding-convention", env=env,
                                  timeout=600)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Ghost-ALICE OS installation Process Report", result.stdout)
            self.assertIn("  Platform: codex", result.stdout)
            self.assertFalse((home / ".agents" / "skills" / "task-router").exists())
            self.assertTrue((home / ".agents" / "skills" / "verification-before-completion" / "SKILL.md").is_file())


class ThinInstallCmdTests(unittest.TestCase):
    def test_wrapper_runs_install_ps1_and_leaves_the_requirement_to_it(self):
        # Without pwsh.exe the wrapper still runs install.ps1 under powershell.exe (Windows PowerShell 5.1);
        # the install.ps1 guard then prints the 7.6 requirement and exits 1, and the wrapper returns that code.
        text = INSTALL_CMD.read_text(encoding="utf-8")
        pwsh_line = 'pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*'
        fallback_line = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*'
        self.assertLess(text.index("where pwsh.exe"), text.index(pwsh_line))
        self.assertLess(text.index(pwsh_line), text.index(fallback_line))
        self.assertEqual(text.count("exit /b %ERRORLEVEL%"), 2)
        for token in ("7.6", "PowerShell 7", "winget", "_shared/installer", "python"):
            self.assertNotIn(token, text, msg=token)
        self.assertIn(REQUIREMENT, INSTALL_PS1.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
