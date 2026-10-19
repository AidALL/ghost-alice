"""The OS entry points only find or install Python and run the installer cli."""
from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"


class ThinInstallShTests(unittest.TestCase):
    def setUp(self):
        self.text = INSTALL_SH.read_text(encoding="utf-8")

    def test_no_bash_library_remains(self):
        self.assertNotIn("installer_lib", self.text)
        self.assertEqual(sorted((REPO_ROOT / "installer_lib").glob("*.sh")), [])

    def test_runs_the_python_cli(self):
        self.assertIn('_shared/installer/cli.py', self.text)
        self.assertRegex(self.text, r'exec "\$py" "\$\{SCRIPT_DIR\}/_shared/installer/cli\.py" "\$@"')

    def test_keeps_the_bash_guard_and_runtime_detection(self):
        # install.sh:19-25; installer_lib/python_runtime.sh functions move here by name
        self.assertIn('if [ -z "${BASH_VERSION:-}" ]; then', self.text)
        for name in ("ensure_utf8_locale", "_is_working_python", "_python_version_key", "_find_python_runtime",
                     "_try_install_python_runtime", "_ensure_python_runtime_for_install", "_python_required_notice"):
            self.assertRegex(self.text, rf"(?m)^{re.escape(name)}\(\) \{{", msg=name)
        self.assertIn("export PYTHONUTF8=1", self.text)
        self.assertIn("export PYTHONIOENCODING=utf-8", self.text)
        self.assertIn('"$cmd" -c \'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)\'', self.text)

    def test_no_installer_logic_in_the_shell(self):
        for name in ("install", "uninstall", "check_source_health", "run_doctor", "_run_install_hooks",
                     "collect_install_targets", "print_install_report_tail"):
            self.assertNotRegex(self.text, rf"(?m)^{re.escape(name)}\(\)", msg=name)
        for token in ("ALL_SKILLS=(", "install_lock.py", "install_transaction.py", 'case "${ARGS[0]:-}" in'):
            self.assertNotIn(token, self.text, msg=token)
        self.assertLess(self.text.count("\n"), 220)

    @unittest.skipUnless(shutil.which("bash"), "bash is required")
    def test_help_goes_through_the_cli(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home, "GHOST_ALICE_INSTALL_PROGRESS": "off"}
            result = subprocess.run(["bash", str(INSTALL_SH), "--help"], cwd=REPO_ROOT, env=env,
                                    capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("  bash install.sh                              # Install to detected AI tools", result.stdout)

    @unittest.skipUnless(shutil.which("zsh") and shutil.which("bash"), "zsh and bash are required")
    def test_zsh_invocation_re_executes_under_bash(self):
        result = subprocess.run(["zsh", str(INSTALL_SH), "--list"], cwd=REPO_ROOT,
                                env={**os.environ, "GHOST_ALICE_INSTALL_PROGRESS": "off"},
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Available skills:", result.stdout)
        self.assertNotIn("BASH_SOURCE", result.stderr)


if __name__ == "__main__":
    unittest.main()
