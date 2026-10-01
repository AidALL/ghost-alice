"""Keep repository instructions complete at Codex's actual input boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".codex" / "config.toml"
DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024


def text_parts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from text_parts(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from text_parts(item)


class CodexInstructionBudgetTests(unittest.TestCase):
    def test_project_budget_covers_complete_repository_instructions(self) -> None:
        config = tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        budget = config.get("project_doc_max_bytes", DEFAULT_PROJECT_DOC_MAX_BYTES)
        self.assertGreaterEqual(budget, (ROOT / "AGENTS.md").stat().st_size)

    @unittest.skipUnless(shutil.which("codex"), "Native Codex CLI is not installed")
    def test_native_codex_delivers_complete_repository_instructions(self) -> None:
        # The debugger renders actual prompt inputs without calling a model.
        source = (ROOT / "AGENTS.md").read_text()
        with tempfile.TemporaryDirectory(prefix="codex-instruction-contract-") as tmp:
            fixture = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q", str(fixture)], check=True,
                           capture_output=True)
            (fixture / "AGENTS.md").write_text(source)
            if CONFIG.exists():
                (fixture / ".codex").mkdir()
                shutil.copyfile(CONFIG, fixture / ".codex" / "config.toml")
            codex_home = fixture / ".runtime-codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                f"[projects.{json.dumps(str(fixture))}]\ntrust_level = \"trusted\"\n"
            )
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("GHOST_ALICE_") and key != "CODEX_THREAD_ID"}
            env["CODEX_HOME"] = str(codex_home)
            capability = subprocess.run(
                ["codex", "-C", str(fixture), "debug", "prompt-input", "--help"],
                env=env, capture_output=True, text=True, timeout=30, check=False,
            )
            if capability.returncode and re.search(
                r"unrecognized subcommand ['\"](?:debug|prompt-input)['\"]",
                capability.stderr,
            ):
                self.skipTest("Installed Codex CLI lacks the prompt-input diagnostic")
            self.assertEqual(capability.returncode, 0, capability.stderr)
            result = subprocess.run(
                ["codex", "-C", str(fixture), "debug", "prompt-input",
                 "Instruction delivery fixture"],
                env=env,
                capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            inputs = json.loads(result.stdout)
            self.assertTrue(
                any(source in part for part in text_parts(inputs)),
                "Native Codex did not receive the complete repository AGENTS.md",
            )


if __name__ == "__main__":
    unittest.main()
