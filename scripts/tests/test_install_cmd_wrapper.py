import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_CMD = REPO_ROOT / "install.cmd"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
INSTALLER_INSTALL_PS1 = REPO_ROOT / "installer_lib" / "install.ps1"
INSTALLER_PYTHON_RUNTIME_PS1 = REPO_ROOT / "installer_lib" / "python_runtime.ps1"
INSTALLER_TARGETS_PS1 = REPO_ROOT / "installer_lib" / "targets.ps1"
INSTALLER_UNINSTALL_PS1 = REPO_ROOT / "installer_lib" / "uninstall.ps1"
README = REPO_ROOT / "README.md"
README_KO = REPO_ROOT / "README_ko.md"
INSTALLATION_DOC = REPO_ROOT / "docs" / "getting-started" / "installation.md"
INSTALLATION_DOC_KO = REPO_ROOT / "docs" / "ko" / "getting-started" / "installation.md"
TROUBLESHOOTING_DOC = REPO_ROOT / "docs" / "getting-started" / "troubleshooting.md"
TROUBLESHOOTING_DOC_KO = REPO_ROOT / "docs" / "ko" / "getting-started" / "troubleshooting.md"
ADDON_FIXTURE = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"


class InstallCmdWrapperTest(unittest.TestCase):
    def test_cmd_wrapper_delegates_to_install_ps1_without_native_hook_logic(self) -> None:
        install_cmd = INSTALL_CMD.read_text(encoding="utf-8")

        self.assertIn('set "PS1=%SCRIPT_DIR%install.ps1"', install_cmd)
        self.assertIn('pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*', install_cmd)
        self.assertIn('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*', install_cmd)
        self.assertNotIn("Set-ExecutionPolicy", install_cmd)
        self.assertNotIn("install_hooks.py", install_cmd)
        self.assertNotIn("Node.js fallback", install_cmd)
        self.assertNotIn("PowerShell native", install_cmd)

    def test_cmd_wrapper_forces_utf8_and_forwards_arguments(self) -> None:
        install_cmd = INSTALL_CMD.read_text(encoding="utf-8")

        self.assertIn("chcp 65001", install_cmd)
        self.assertIn('set "PYTHONUTF8=1"', install_cmd)
        self.assertIn('set "PYTHONIOENCODING=utf-8"', install_cmd)
        self.assertIn("-File \"%PS1%\" %*", install_cmd)

    def test_readme_documents_cmd_as_install_ps1_wrapper_for_python_contract(self) -> None:
        readme = README.read_text(encoding="utf-8")

        self.assertIn("`install.cmd` is a thin wrapper around `install.ps1`", readme)
        self.assertIn("Python 3.11+", readme)
        self.assertIn("UTF-8 console setup", readme)

    def test_docs_prefer_cmd_wrapper_when_powershell_policy_blocks_ps1(self) -> None:
        expectations = {
            README: [
                "Windows PowerShell / CMD:",
                ".\\install.cmd",
                "PowerShell execution policy",
                "`-NoProfile -ExecutionPolicy Bypass`",
                "does not change the user or machine execution policy",
            ],
            README_KO: [
                "Windows PowerShell / CMD:",
                ".\\install.cmd",
                "PowerShell execution policy",
                "`-NoProfile -ExecutionPolicy Bypass`",
                "사용자 또는 머신 execution policy를 변경하지 않는다",
            ],
            INSTALLATION_DOC: [
                ".\\install.cmd",
                "PowerShell execution policy",
                "`-NoProfile -ExecutionPolicy Bypass`",
                "does not change the user or machine execution policy",
            ],
            INSTALLATION_DOC_KO: [
                ".\\install.cmd",
                "PowerShell execution policy",
                "`-NoProfile -ExecutionPolicy Bypass`",
                "사용자 또는 머신 execution policy를 변경하지 않는다",
            ],
            TROUBLESHOOTING_DOC: [
                ".\\install.cmd",
                "cannot be loaded because running scripts is disabled",
                "does not change the user or machine execution policy",
            ],
            TROUBLESHOOTING_DOC_KO: [
                ".\\install.cmd",
                "cannot be loaded because running scripts is disabled",
                "사용자 또는 머신 execution policy를 변경하지 않는다",
            ],
        }

        for path, snippets in expectations.items():
            with self.subTest(path=path.relative_to(REPO_ROOT).as_posix()):
                text = path.read_text(encoding="utf-8-sig")
                for snippet in snippets:
                    self.assertIn(snippet, text)
                self.assertNotIn("```powershell\n.\\install.ps1\n```", text)

    def test_windows_help_and_recovery_text_prefer_cmd_wrapper(self) -> None:
        install_ps1 = INSTALL_PS1.read_text(encoding="utf-8-sig")
        install_runtime = INSTALLER_INSTALL_PS1.read_text(encoding="utf-8-sig")
        python_runtime = INSTALLER_PYTHON_RUNTIME_PS1.read_text(encoding="utf-8-sig")
        uninstall_runtime = INSTALLER_UNINSTALL_PS1.read_text(encoding="utf-8-sig")

        self.assertIn(".\\install.cmd                                  # Install to detected AI tools", install_ps1)
        self.assertIn(".\\install.cmd                          # Install to detected AI tools", uninstall_runtime)
        self.assertNotIn("Run .\\install.ps1 -List", install_runtime)
        self.assertNotIn("rerun install.ps1", install_runtime)
        self.assertNotIn("rerun install.ps1", python_runtime)

    def test_powershell_declares_official_addon_alias_and_source_preparation(self) -> None:
        install_ps1 = INSTALL_PS1.read_text(encoding="utf-8-sig")
        targets_runtime = INSTALLER_TARGETS_PS1.read_text(encoding="utf-8-sig")

        self.assertIn("[string[]]$Addon", install_ps1)
        self.assertIn("Resolve-OfficialAddonShortcuts", targets_runtime)
        self.assertIn("Prepare-AddonSources", targets_runtime)
        self.assertIn("GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE", targets_runtime)
        self.assertIn("addon-source-cache", targets_runtime)

    def test_powershell_addon_alias_lists_official_addon_targets(self) -> None:
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            self.skipTest("PowerShell executable is required for addon alias binding test")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "USERPROFILE": str(home),
                    "APPDATA": str(tmp_path / "AppData" / "Roaming"),
                    "LOCALAPPDATA": str(tmp_path / "AppData" / "Local"),
                    "PYTHONUTF8": "1",
                    "PYTHONIOENCODING": "utf-8",
                    "GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE": str(ADDON_FIXTURE),
                }
            )
            result = subprocess.run(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(INSTALL_PS1),
                    "--addon",
                    "autopilot",
                    "-ListAddons",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=combined)
        self.assertNotIn("parameter name 'addon' is ambiguous", combined)
        self.assertIn("noop (addon:noop)", combined)

    def test_docs_explain_autopilot_addon_installs_from_core_checkout(self) -> None:
        expectations = {
            README: [
                "Official autopilot addon:",
                "cd ~/ghost-alice",
                "bash install.sh --addon autopilot",
                "The autopilot repository is an addon package consumed by the core installer",
            ],
            README_KO: [
                "Official autopilot addon:",
                "cd ~/ghost-alice",
                "bash install.sh --addon autopilot",
                "autopilot repository는 core installer가 소비하는 addon package",
            ],
            INSTALLATION_DOC: [
                "Run this from the Ghost-ALICE core checkout",
                "Normal users do not clone the autopilot addon repository",
                "bash install.sh --addon autopilot",
                "Windows PowerShell/CMD use the same official alias",
            ],
            INSTALLATION_DOC_KO: [
                "Ghost-ALICE core checkout에서 실행한다",
                "일반 사용자는 autopilot addon repository를 직접 clone하지 않는다",
                "bash install.sh --addon autopilot",
                "Windows PowerShell/CMD도 같은 official alias를 사용한다",
            ],
        }

        for path, snippets in expectations.items():
            with self.subTest(path=path.relative_to(REPO_ROOT).as_posix()):
                text = path.read_text(encoding="utf-8-sig")
                for snippet in snippets:
                    self.assertIn(snippet, text)


if __name__ == "__main__":
    unittest.main()
