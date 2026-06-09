import re
import shutil
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PS1 = REPO_ROOT / "install.ps1"


def _extract_powershell_function(source: str, name: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"function {name} "):
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


class InstallPs1PwshLtsTest(unittest.TestCase):
    def _script(self) -> str:
        return installer_ps1_source()

    def test_windows_installer_has_pwsh_76_baseline_gate(self) -> None:
        script = self._script()

        self.assertIn('function Test-Windows10OrNewer', script)
        self.assertIn('function Get-InstalledPwshVersion', script)
        self.assertIn('function Resolve-PowerShell76LtsReleaseAsset', script)
        self.assertIn('function Initialize-PwshLtsBaseline', script)
        self.assertIn('$env:GHOST_ALICE_TEST_SKIP_PWSH_LTS_BASELINE -eq "1"', script)
        self.assertIn('$script:PwshLtsBaselineVersion = [version]"7.6.0"', script)
        self.assertIn('$script:PwshLtsReleaseLine = "7.6"', script)
        self.assertRegex(script, r"\$existingVersion\s+-and\s+\$existingVersion\s+-ge\s+\$script:PwshLtsBaselineVersion")

    def test_pwsh_lts_install_uses_github_76_msi_without_default_shell_changes(self) -> None:
        script = self._script()

        self.assertIn('https://api.github.com/repos/PowerShell/PowerShell/releases?per_page=100', script)
        self.assertIn('^v7\\.6\\.\\d+$', script)
        self.assertIn('PowerShell-{0}-win-{1}.msi', script)
        self.assertIn('msiexec.exe', script)
        self.assertIn('/quiet', script)
        self.assertIn('/norestart', script)
        self.assertIn('ADD_PATH=1', script)
        self.assertNotIn('^v7\\.4\\.\\d+$', script)
        self.assertNotIn('ENABLE_PSREMOTING=1', script)
        self.assertNotIn('ADD_EXPLORER_CONTEXT_MENU_OPENPOWERSHELL=1', script)
        self.assertNotIn('ADD_FILE_CONTEXT_MENU_RUNPOWERSHELL=1', script)
        self.assertNotRegex(script, re.compile(r"Set-.*Default.*Shell", re.IGNORECASE))
        self.assertNotRegex(script, re.compile(r"Windows\s+Terminal.*default", re.IGNORECASE))

    def test_pwsh_lts_removes_existing_74_msi_before_installing_76(self) -> None:
        script = self._script()

        self.assertIn('function Get-PowerShell74LtsInstalledProducts', script)
        self.assertIn('function Uninstall-PowerShell74LtsInstallations', script)
        self.assertIn('HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', script)
        self.assertIn('HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', script)
        self.assertIn('HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', script)
        self.assertIn('$version.Major -ne 7 -or $version.Minor -ne 4', script)
        self.assertIn('$productCodePattern = "^\\{[0-9A-Fa-f-]{36}\\}$"', script)
        self.assertIn('"/x", $product.ProductCode', script)
        self.assertIn('/quiet', script)
        self.assertIn('/norestart', script)
        self.assertIn('3010', script)
        self.assertNotRegex(script, re.compile(r"cmd\s+/c\s+.*UninstallString", re.IGNORECASE))
        self.assertNotIn('Uninstall-Package', script)
        self.assertIn('function Test-RunningAsAdministrator', script)

        initializer = script[script.index('function Initialize-PwshLtsBaseline'):]
        self.assertLess(
            initializer.index('Get-InstalledPwshVersion'),
            initializer.index('Test-RunningAsAdministrator'),
        )
        self.assertLess(
            initializer.index('Test-RunningAsAdministrator'),
            initializer.index('Uninstall-PowerShell74LtsInstallations'),
        )
        self.assertLess(
            initializer.index('Get-InstalledPwshVersion'),
            initializer.index('Resolve-PowerShell76LtsReleaseAsset'),
        )
        self.assertLess(
            initializer.index('Uninstall-PowerShell74LtsInstallations'),
            initializer.index('Resolve-PowerShell76LtsReleaseAsset'),
        )

    def test_pwsh_lts_baseline_failure_warns_and_continues(self) -> None:
        script = self._script()
        initializer = _extract_powershell_function(script, "Initialize-PwshLtsBaseline")

        self.assertIn('try {', initializer)
        self.assertLess(
            initializer.index('try {'),
            initializer.index('Uninstall-PowerShell74LtsInstallations'),
        )
        self.assertIn('} catch {', initializer)
        self.assertIn('Write-Warn', initializer)
        self.assertIn('continuing with current PowerShell runtime', initializer)
        self.assertNotIn('PowerShell 7.6 baseline check failed before auto platform installs', script)

    def test_pwsh_lts_baseline_skips_msi_when_shell_is_not_elevated(self) -> None:
        if not sys.platform.startswith("win"):
            self.skipTest("PowerShell elevation check is Windows-specific")
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("PowerShell executable is required for baseline test")

        script = textwrap.dedent(
            """
            $ErrorActionPreference = "Stop"
            $script:GhostAliceRoot = (Get-Location).Path
            $script:PwshLtsBaselineVersion = [version]"7.6.0"
            $script:PwshLtsReleaseLine = "7.6"
            $script:PwshLtsEnsureChecked = $false
            function Write-Warn { param([string]$English, [string]$Korean) Write-Output "WARN:$English" }
            function Write-Info { param([string]$English, [string]$Korean) Write-Output "INFO:$English" }
            function Write-Ok { param([string]$English, [string]$Korean) Write-Output "OK:$English" }
            . (Join-Path $script:GhostAliceRoot "installer_lib/pwsh_runtime.ps1")
            function Test-Windows10OrNewer { return $true }
            function Get-InstalledPwshVersion { return [version]"7.4.16" }
            function Test-RunningAsAdministrator { return $false }
            function Uninstall-PowerShell74LtsInstallations { throw "uninstall-called" }
            function Resolve-PowerShell76LtsReleaseAsset { throw "resolve-called" }
            Initialize-PwshLtsBaseline
            Write-Output "completed"
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
        self.assertIn("PowerShell 7.6.0+ baseline setup skipped", result.stdout)
        self.assertIn("completed", result.stdout)
        self.assertNotIn("uninstall-called", result.stdout)
        self.assertNotIn("resolve-called", result.stdout)

    def test_pwsh_lts_gate_runs_after_install_mutation_paths(self) -> None:
        script = self._script()
        main = script[script.index("# \u2500\u2500 Main"):]

        install_branch = main[main.index("Invoke-WithInstallLock { Invoke-Install"):]

        self.assertRegex(install_branch, r"Invoke-WithInstallLock \{ Invoke-Install[\s\S]{0,160}Initialize-PwshLtsBaseline")

    def test_auto_install_does_not_suppress_child_pwsh_lts_baseline(self) -> None:
        script = self._script()
        main = script[script.index("# \u2500\u2500 Main"):]
        auto_branch = main[main.index("if ($Auto) {"):main.index("if ($PromptPlatform", main.index("if ($Auto) {"))]

        self.assertNotIn('GHOST_ALICE_PWSH_LTS_BASELINE_CHECKED', script)
        self.assertNotIn('Initialize-PwshLtsBaseline', auto_branch)
        self.assertIn('foreach ($plat in $detected)', auto_branch)

    def test_install_ps1_comments_use_english_default_metadata(self) -> None:
        script = self._script()
        korean_comment_lines = [
            (idx, line)
            for idx, line in enumerate(script.splitlines(), start=1)
            if line.lstrip().startswith("#") and re.search(r"[\uac00-\ud7a3]", line)
        ]

        self.assertEqual(korean_comment_lines, [])


if __name__ == "__main__":
    unittest.main()
