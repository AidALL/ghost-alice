import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
COMMANDS_SYNC = REPO_ROOT / "_shared" / "commands_sync.py"


class InstallShInlinePythonEncodingTest(unittest.TestCase):
    def test_sync_commands_python_reconfigures_stdio_for_windows_locales(self) -> None:
        source = COMMANDS_SYNC.read_text(encoding="utf-8")

        self.assertIn('sys.stdout.reconfigure(encoding="utf-8", errors="replace")', source)
        self.assertIn('sys.stderr.reconfigure(encoding="utf-8", errors="replace")', source)

    def test_sync_commands_python_uses_utf8_for_catalog_and_wrappers(self) -> None:
        source = COMMANDS_SYNC.read_text(encoding="utf-8")

        self.assertIn('with open(catalog_path, encoding="utf-8") as catalog_fh:', source)
        self.assertIn('with open(cmd_file, encoding="utf-8") as cf:', source)
        self.assertIn(
            'with open(os.path.join(commands_dir, f"{name}{ext}"), "w", encoding="utf-8") as wf:',
            source,
        )


if __name__ == "__main__":
    unittest.main()
