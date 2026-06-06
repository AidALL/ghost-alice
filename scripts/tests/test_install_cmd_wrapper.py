import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_CMD = REPO_ROOT / "install.cmd"
README = REPO_ROOT / "README.md"


class InstallCmdWrapperTest(unittest.TestCase):
    def test_cmd_wrapper_delegates_to_install_ps1_without_native_hook_logic(self) -> None:
        install_cmd = INSTALL_CMD.read_text(encoding="utf-8")

        self.assertIn('set "PS1=%SCRIPT_DIR%install.ps1"', install_cmd)
        self.assertIn('pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*', install_cmd)
        self.assertIn('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*', install_cmd)
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


if __name__ == "__main__":
    unittest.main()
