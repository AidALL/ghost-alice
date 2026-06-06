import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source

ROOT = Path(__file__).resolve().parents[2]


def bad_term(*parts: str) -> str:
    return "".join(parts)


class TestInstallTailPendingMessage(unittest.TestCase):
    def assert_install_tail_copy(self, body: str) -> None:
        self.assertIn("User:", body)
        self.assertIn("Tech:", body)
        self.assertIn("when local changes are detected during the agent tool update", body)
        self.assertIn("they are backed up instead of overwritten", body)
        self.assertIn("Next time you open Claude/Codex", body)
        self.assertIn("Please review backed-up changes.", body)
        self.assertNotIn("legacy backup copy", body)
        self.assertNotIn("were backed up", body)
        self.assertNotIn("legacy current conversation copy", body)
        self.assertNotIn("legacy non-developer label", body)
        self.assertNotIn("legacy developer label", body)
        self.assertNotIn("Non-developer note", body)
        self.assertNotIn("Developer note", body)
        self.assertNotIn("legacy review request", body)
        self.assertNotIn("legacy input request", body)

    def test_install_sh_tail_points_to_next_session(self):
        self.assert_install_tail_copy(installer_bash_source())

    def test_install_ps1_tail_points_to_next_session(self):
        self.assert_install_tail_copy(installer_ps1_source())

    def test_install_ps1_is_english_only(self):
        body = installer_ps1_source()

        self.assertIn("Installer messages are English-only", body)
        self.assertNotIn("Test-KoreanInstallLanguage", body)
        self.assertNotIn("CurrentUICulture", body)
        self.assertNotIn("CurrentCulture", body)

    def test_install_sh_is_english_only(self):
        body = installer_bash_source()

        self.assertIn("Installer messages are English-only", body)
        self.assertNotIn("_is_korean_language_value", body)
        self.assertIn("ensure_utf8_locale()", body)
        self.assertLess(body.index("ORIGINAL_LOCALE="), body.index("ensure_utf8_locale()"))

    def test_runtime_messages_use_polite_or_neutral_tone(self):
        bad_terms = [
            "call-do-it",
            "check-do-it",
            "verify-do-it",
            "add-do-it",
            "input-do-it",
            "send-it",
            "do-not-miss-it",
        ]
        runtime_files = [
            ROOT / "install.sh",
            ROOT / "install.ps1",
            ROOT / "_shared" / "install_hooks.py",
            ROOT / "_shared" / "secrets" / "load.py",
            ROOT / "_shared" / "secrets" / "load.sh",
        ]

        for path in runtime_files:
            body = path.read_text(encoding="utf-8")
            for term in bad_terms:
                with self.subTest(path=path.name, term=term):
                    self.assertNotIn(term, body)


if __name__ == "__main__":
    unittest.main()
