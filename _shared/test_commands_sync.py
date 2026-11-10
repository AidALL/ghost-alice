"""The installer writes canonical command bodies and preserves installed trailing-newline tolerance."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CommandsSyncTest(unittest.TestCase):
    def test_sync_writes_the_catalog_target_and_accepts_installed_newline_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"skills": [{"name": "alpha", "path": "스킬/alpha/SKILL.md"}]}), encoding="utf-8")
            commands = root / "commands"
            script = Path(__file__).with_name("commands_sync.py")
            command = [sys.executable, str(script), str(catalog), str(commands), "sync", "claude"]
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            wrapper = commands / "alpha.md"
            expected = "@스킬/alpha/SKILL.md\n\n$ARGUMENTS\n"
            self.assertEqual(wrapper.read_text(encoding="utf-8"), expected)
            self.assertIn("agent_visibility_cli.py $ARGUMENTS", (commands / "visibility.md").read_text())
            for ending in ("", "\n\n"):
                with self.subTest(ending=ending):
                    wrapper.write_text(expected.rstrip("\n") + ending, encoding="utf-8")
                    command[-2] = "check"
                    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            wrapper.write_text("@wrong/SKILL.md\n\n$ARGUMENTS\n", encoding="utf-8")
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
            self.assertIn("drift: alpha", result.stdout)
