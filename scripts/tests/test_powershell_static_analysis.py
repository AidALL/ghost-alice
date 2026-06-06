import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PS1 = REPO_ROOT / "install.ps1"
ANALYZER_SETTINGS = REPO_ROOT / "PSScriptAnalyzerSettings.psd1"


def _vscode_powershell_modules_dir() -> Path | None:
    extensions_root = Path.home() / ".vscode" / "extensions"
    if not extensions_root.exists():
        return None

    manifests = sorted(
        extensions_root.glob("ms-vscode.powershell-*/modules/PSScriptAnalyzer/*/PSScriptAnalyzer.psd1")
    )
    if not manifests:
        return None

    return manifests[-1].parents[2]


def _powershell_env_with_extension_modules() -> dict[str, str]:
    env = os.environ.copy()
    modules_dir = _vscode_powershell_modules_dir()
    if modules_dir is not None:
        existing = env.get("PSModulePath", "")
        env["PSModulePath"] = (
            f"{modules_dir}{os.pathsep}{existing}" if existing else modules_dir.as_posix()
        )
    return env


class PowerShellStaticAnalysisTest(unittest.TestCase):
    def _pwsh(self) -> str:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh is not available")
        return pwsh

    def _require_psscriptanalyzer(self) -> dict[str, str]:
        pwsh = self._pwsh()
        env = _powershell_env_with_extension_modules()
        probe = subprocess.run(
            [
                pwsh,
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "Import-Module PSScriptAnalyzer -ErrorAction Stop",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if probe.returncode != 0:
            self.skipTest("PSScriptAnalyzer module is not available")
        return env

    def test_powershell_parser_accepts_installer(self) -> None:
        pwsh = self._pwsh()

        result = subprocess.run(
            [
                pwsh,
                "-NoLogo",
                "-NoProfile",
                "-Command",
                (
                    "$tokens = $null; $errors = $null; "
                    "[System.Management.Automation.Language.Parser]::ParseFile("
                    f"'{INSTALL_PS1.as_posix()}', [ref]$tokens, [ref]$errors) > $null; "
                    "if ($errors.Count -gt 0) { $errors | Format-List; exit 1 }"
                ),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

    def test_psscriptanalyzer_settings_are_repo_owned(self) -> None:
        self.assertTrue(ANALYZER_SETTINGS.exists())
        settings = ANALYZER_SETTINGS.read_text(encoding="utf-8")
        self.assertIn("ExcludeRules", settings)
        self.assertIn("PSAvoidUsingWriteHost", settings)
        self.assertIn("PSUseApprovedVerbs", settings)
        self.assertIn("PSUseDeclaredVarsMoreThanAssignments", settings)

    def test_psscriptanalyzer_settings_enable_pwsh5_syntax_layer(self) -> None:
        settings = ANALYZER_SETTINGS.read_text(encoding="utf-8")

        self.assertIn("PSUseCompatibleSyntax", settings)
        self.assertIn("TargetVersions", settings)
        self.assertIn("'5.1'", settings)

    def test_psscriptanalyzer_warning_budget_is_zero_with_repo_settings(self) -> None:
        pwsh = self._pwsh()
        env = self._require_psscriptanalyzer()

        command = (
            "Import-Module PSScriptAnalyzer -ErrorAction Stop; "
            "$results = Invoke-ScriptAnalyzer "
            f"-Path '{INSTALL_PS1.as_posix()}' "
            f"-Settings '{ANALYZER_SETTINGS.as_posix()}' "
            "-Severity Warning,Error; "
            "if ($results) { $results | Format-Table -AutoSize | Out-String -Width 240; exit 1 }"
        )
        result = subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-Command", command],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

    def test_pwsh5_compatibility_layer_catches_ps7_only_syntax(self) -> None:
        pwsh = self._pwsh()
        env = self._require_psscriptanalyzer()

        with tempfile.TemporaryDirectory() as temp_dir:
            probe = Path(temp_dir) / "ps7_only_syntax.ps1"
            probe.write_text("$x = $null\n$y = $x ?? 'fallback'\n", encoding="utf-8")

            command = (
                "Import-Module PSScriptAnalyzer -ErrorAction Stop; "
                "$results = Invoke-ScriptAnalyzer "
                f"-Path '{probe.as_posix()}' "
                f"-Settings '{ANALYZER_SETTINGS.as_posix()}' "
                "-Severity Warning,Error; "
                "$results | ConvertTo-Json -Depth 4"
            )
            result = subprocess.run(
                [pwsh, "-NoLogo", "-NoProfile", "-Command", command],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("PSUseCompatibleSyntax", result.stdout)


if __name__ == "__main__":
    unittest.main()
