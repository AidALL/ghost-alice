import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PS1 = REPO_ROOT / "install.ps1"
INSTALL_CMD = REPO_ROOT / "install.cmd"
INSTALL_SH = REPO_ROOT / "install.sh"
GLOBAL_RULE_BLOCKS = REPO_ROOT / "_shared" / "global_rule_blocks.py"
UTF8_BOM = b"\xef\xbb\xbf"


class InstallPs1EncodingTest(unittest.TestCase):
    def test_install_ps1_uses_utf8_bom(self) -> None:
        raw = INSTALL_PS1.read_bytes()
        self.assertTrue(raw.startswith(UTF8_BOM))

    def test_install_ps1_reads_utf8_files_with_explicit_encoding(self) -> None:
        ps1 = installer_ps1_source()
        block_helper = GLOBAL_RULE_BLOCKS.read_text(encoding="utf-8")

        self.assertIn("Get-Content -LiteralPath $CodexBootstrapSource -Raw -Encoding UTF8", ps1)
        self.assertIn("global_rule_blocks.py", ps1)
        self.assertIn('encoding="utf-8-sig"', block_helper)
        self.assertIn('encoding="utf-8"', block_helper)
        self.assertIn("Get-Content -LiteralPath $SkillMdPath -TotalCount 8 -Encoding UTF8", ps1)

    def test_windows_powershell_5_can_parse_install_ps1(self) -> None:
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("powershell.exe is not available on this machine")

        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-Command",
                (
                    "$parseErrors = $null; "
                    "$tokens = $null; "
                    "[void][System.Management.Automation.Language.Parser]::ParseFile("
                    f"'{INSTALL_PS1}', [ref]$tokens, [ref]$parseErrors"
                    "); "
                    "if ($parseErrors.Count -gt 0) { "
                    "$parseErrors | ForEach-Object { $_.Message }; "
                    "exit 1 "
                    "}"
                ),
            ],
            capture_output=True,
        )

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        combined = "\n".join(filter(None, [stdout, stderr]))
        self.assertEqual(result.returncode, 0, msg=combined)
        self.assertNotIn("ParserError", combined)
        self.assertNotIn("Unexpected token", combined)

    def test_installers_force_python_utf8_mode(self) -> None:
        ps1 = INSTALL_PS1.read_text(encoding="utf-8-sig")
        cmd = INSTALL_CMD.read_text(encoding="utf-8")
        sh = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn('$env:PYTHONUTF8 = "1"', ps1)
        self.assertIn('$env:PYTHONIOENCODING = "utf-8"', ps1)
        self.assertIn('set "PYTHONUTF8=1"', cmd)
        self.assertIn('set "PYTHONIOENCODING=utf-8"', cmd)
        self.assertIn("export PYTHONUTF8=1", sh)
        self.assertIn("export PYTHONIOENCODING=utf-8", sh)


if __name__ == "__main__":
    unittest.main()
