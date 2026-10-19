import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_CLI = REPO_ROOT / "_shared" / "installer" / "cli.py"
INSTALLER_REPORT = REPO_ROOT / "_shared" / "installer" / "report.py"


class TestInstallTailPendingMessage(unittest.TestCase):
    def assert_install_tail_copy(self, body: str) -> None:
        self.assertIn("User:", body)
        self.assertIn("Tech:", body)
        self.assertIn("when local changes are detected during the agent tool update", body)
        self.assertIn("they are backed up instead of overwritten", body)
        self.assertIn("Next time you open Claude/Codex", body)
        self.assertIn("Please review backed-up changes.", body)
        self.assertNotIn("were backed up", body)
        self.assertNotIn("Non-developer note", body)
        self.assertNotIn("Developer note", body)

    def test_installer_cli_tail_points_to_next_session(self):
        self.assert_install_tail_copy(INSTALLER_CLI.read_text(encoding="utf-8"))

    def test_installer_messages_are_english_only(self):
        body = INSTALLER_REPORT.read_text(encoding="utf-8")

        self.assertIn("Installer messages are English-only", body)
        self.assertNotIn("_is_korean_language_value", body)

    def test_install_sh_sets_a_utf8_locale_before_python_runs(self):
        body = installer_bash_source()

        self.assertIn("ensure_utf8_locale()", body)
        self.assertLess(body.index("\nensure_utf8_locale\n"), body.index("export PYTHONUTF8=1"))


if __name__ == "__main__":
    unittest.main()
