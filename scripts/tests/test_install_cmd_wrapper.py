import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_CMD = REPO_ROOT / "install.cmd"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
README = REPO_ROOT / "README.md"
README_KO = REPO_ROOT / "README_ko.md"
INSTALLATION_DOC = REPO_ROOT / "docs" / "getting-started" / "installation.md"
INSTALLATION_DOC_KO = REPO_ROOT / "docs" / "ko" / "getting-started" / "installation.md"
TROUBLESHOOTING_DOC = REPO_ROOT / "docs" / "getting-started" / "troubleshooting.md"
TROUBLESHOOTING_DOC_KO = REPO_ROOT / "docs" / "ko" / "getting-started" / "troubleshooting.md"


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

    def test_detailed_guide_documents_windows_native_wrapper_for_python_contract(self) -> None:
        guide = INSTALLATION_DOC.read_text(encoding="utf-8")
        self.assertIn("./docs/getting-started/installation.md", README.read_text(encoding="utf-8"))
        self.assertIn("`install.cmd` keeps the native wrapper path", guide)
        self.assertIn("Python 3.11+", guide)
        self.assertIn("UTF-8 console setup", guide)
        self.assertIn("`-NoProfile -ExecutionPolicy Bypass`", guide)
        for path in (INSTALLATION_DOC, INSTALLATION_DOC_KO):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("PowerShell 7.6", text)
                self.assertIn("Windows PowerShell 5.1", text)
                self.assertIn("winget install --id Microsoft.PowerShell --exact", text)

    def test_docs_prefer_cmd_wrapper_when_powershell_policy_blocks_ps1(self) -> None:
        expectations = {
            README: [
                "bash install.sh",
                "./docs/getting-started/installation.md",
            ],
            README_KO: [
                "bash install.sh",
                "./docs/ko/getting-started/installation.md",
            ],
            INSTALLATION_DOC: [
                ".\\install.cmd",
                "PowerShell execution policy",
                "`-NoProfile -ExecutionPolicy Bypass`",
                "does not change the user or machine execution policy",
            ],
            INSTALLATION_DOC_KO: [
                ".\\install.cmd",
                "PowerShell 실행 정책",
                "`-NoProfile -ExecutionPolicy Bypass`",
                "사용자나 컴퓨터의 실행 정책은 변경하지 않습니다",
            ],
            TROUBLESHOOTING_DOC: [
                ".\\install.cmd",
                "cannot be loaded because running scripts is disabled",
                "does not change the user or machine execution policy",
            ],
            TROUBLESHOOTING_DOC_KO: [
                ".\\install.cmd",
                "cannot be loaded because running scripts is disabled",
                "사용자 또는 컴퓨터의 실행 정책을 변경하지 않습니다",
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

        self.assertIn(".\\install.cmd                                  # Install to detected AI tools", install_ps1)
        self.assertIn("then rerun .\\install.cmd.", install_ps1)
        self.assertIn("Then re-run: .\\install.cmd", install_ps1)
        self.assertNotIn("rerun install.ps1", install_ps1)
        self.assertNotIn("Run .\\install.ps1 -List", install_ps1)


    def test_install_and_uninstall_docs_put_command_choices_before_runtime_detail(self) -> None:
        # Each edition's own headings: quick install, addons, one platform, common commands, runtime reference.
        install_docs = {
            INSTALLATION_DOC: ("## Quick Install", "## Install Addons", "## Install One Platform", "## Common Commands", "## Runtime And Platform Reference"),
            INSTALLATION_DOC_KO: ("## 빠른 설치", "## 애드온 설치", "## 플랫폼 하나만 설치", "## 자주 사용하는 명령", "## 런타임과 플랫폼 참조"),
        }
        for path, (quick, addons, one_platform, common, runtime) in install_docs.items():
            with self.subTest(path=path.relative_to(REPO_ROOT).as_posix()):
                text = path.read_text(encoding="utf-8-sig")
                positions = [text.index(marker) for marker in (quick, addons, runtime)]
                self.assertEqual(positions, sorted(positions))
                self.assertLess(text.index(addons), text.index(one_platform))
                self.assertLess(text.index(common), text.index(runtime))

        # Each edition's removal-scope and source-file headings, then a phrase from its addon-removal text.
        uninstall_docs = {
            REPO_ROOT / "docs" / "getting-started" / "uninstall.md": ("## Choose The Removal Scope", "## Source", "Addon-specific removal"),
            REPO_ROOT / "docs" / "ko" / "getting-started" / "uninstall.md": ("## 제거 범위 선택", "## 기준 파일", "개별 애드온 제거"),
        }
        for path, (scope, source, addon_removal) in uninstall_docs.items():
            with self.subTest(path=path.relative_to(REPO_ROOT).as_posix()):
                text = path.read_text(encoding="utf-8-sig")
                self.assertIn(scope, text)
                self.assertLess(text.index(scope), text.index(source))
                self.assertIn(addon_removal, text)


if __name__ == "__main__":
    unittest.main()
