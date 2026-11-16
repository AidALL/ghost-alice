import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_CLI = REPO_ROOT / "_shared" / "installer" / "cli.py"


class TestInstallTailPendingMessage(unittest.TestCase):
    def assert_install_tail_copy(self, body: str) -> None:
        self.assertIn("For the human user:", body)
        self.assertIn("Tech:", body)
        self.assertIn("the installer backs up external changes it finds in installed targets during updates", body)
        self.assertIn("its records do not hold those changes or identify their writer", body)
        self.assertIn("next time you open Claude/Codex", body)
        self.assertIn("Please review backed-up changes.", body)
        self.assertNotIn("were backed up", body)

    def test_installer_cli_tail_points_to_next_session(self):
        self.assert_install_tail_copy(INSTALLER_CLI.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
