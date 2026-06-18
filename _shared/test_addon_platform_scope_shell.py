"""Shell e2e for review finding M5: addon sidecars must be platform-scoped so
installing the SAME addon to claude and codex keeps BOTH records (the last
platform must not overwrite the first).

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_platform_scope_shell.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"


def _python_311() -> bool:
    candidates = [sys.executable, shutil.which("python3"), "/opt/homebrew/bin/python3",
                  "/usr/local/bin/python3", "/usr/bin/python3"]
    for candidate in candidates:
        if candidate and subprocess.run(
            [candidate, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
            capture_output=True,
        ).returncode == 0:
            return True
    return False


class PlatformScopedSidecarTest(unittest.TestCase):
    def test_same_addon_two_platforms_keeps_both_sidecars(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"

            def run(*args):
                return subprocess.run(
                    [bash, str(REPO_ROOT / "install.sh"), *args], cwd=REPO_ROOT, env=env,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=180, check=False)

            claude = run("--platform", "claude", "--addon-source", str(FIXTURE),
                         "--skip-source-health", "task-router")
            self.assertEqual(claude.returncode, 0, msg=claude.stderr + claude.stdout)
            codex = run("--platform", "codex", "--addon-source", str(FIXTURE),
                        "--skip-source-health", "task-router")
            self.assertEqual(codex.returncode, 0, msg=codex.stderr + codex.stdout)

            addons = Path(home) / ".ghost-alice" / "addons"
            claude_sc = addons / "claude" / "noop.json"
            codex_sc = addons / "codex" / "noop.json"
            self.assertTrue(claude_sc.exists(), msg="claude sidecar lost: " + codex.stderr + codex.stdout)
            self.assertTrue(codex_sc.exists(), msg="codex sidecar missing")

            claude_rec = json.loads(claude_sc.read_text(encoding="utf-8"))
            codex_rec = json.loads(codex_sc.read_text(encoding="utf-8"))
            self.assertEqual(claude_rec["platform"], "claude")
            self.assertEqual(codex_rec["platform"], "codex")
            # Each record points at its own platform's installed target.
            self.assertIn(".claude/skills/noop", claude_rec["provided"][0]["target"])
            self.assertIn(".agents/skills/noop", codex_rec["provided"][0]["target"])
            # Both platform skills are actually present on disk.
            self.assertTrue(os.path.lexists(Path(home) / ".claude" / "skills" / "noop"))
            self.assertTrue(os.path.lexists(Path(home) / ".agents" / "skills" / "noop"))


if __name__ == "__main__":
    unittest.main()
