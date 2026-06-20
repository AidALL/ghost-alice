"""TDD for plan task C-THREAD-1: copy-mode ownership markers carry addon
attribution end-to-end (CLI --addon-target + install.ps1 threading).

A copy-mode addon skill must receive a marker whose owner=addon and
addon_id=<id>, so classify_skill_root(expected_addon_id=<id>) (plan task T2.9)
can prove the addon owns its own copied skill instead of failing closed.

Run: python3 -m pytest scripts/tests/test_addon_marker_threading.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_ps1_source  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER_CLI = REPO_ROOT / "_shared" / "installer_assets_cli.py"
# Append (do not insert at 0): keep scripts/tests at sys.path[0] so unittest
# discover resolves the scripts/tests copy of a basename that also exists under
# _shared (e.g. test_completion_check_validator.py). Inserting _shared at 0
# shadows that copy and breaks `unittest discover -s scripts/tests` in CI.
sys.path.append(str(REPO_ROOT / "_shared"))

from installer_assets import (  # noqa: E402
    OWNERSHIP_CONFLICT,
    OWNERSHIP_GHOST_ALICE_MANAGED,
    classify_skill_root,
)


class AddonTargetCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.skills = Path(self.tmp.name) / ".agents" / "skills"
        self.skills.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _skill(self, name: str) -> Path:
        d = self.skills / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# body\n", encoding="utf-8")
        return d

    def _run_cli(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(MARKER_CLI), "--platform", "codex",
             "--source-repo", "/repo", "--source-commit", "abc", *extra],
            cwd=REPO_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False)

    def test_addon_target_writes_addon_attributed_marker(self) -> None:
        dest = self._skill("noop")
        result = self._run_cli("--addon-target", "noop", str(dest), "copy", "noop")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        marker = json.loads((dest / ".ghost-alice-install.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["owner"], "addon")
        self.assertEqual(marker["addon_id"], "noop")
        self.assertEqual(marker["provided_kind"], "skill")
        # plan task T2.9: the addon can now prove ownership of its own copied skill.
        self.assertEqual(
            classify_skill_root(dest, expected_addon_id="noop").ownership,
            OWNERSHIP_GHOST_ALICE_MANAGED)
        # a different addon_id must fail closed.
        self.assertEqual(
            classify_skill_root(dest, expected_addon_id="other").ownership,
            OWNERSHIP_CONFLICT)

    def test_core_target_marker_stays_core_owned(self) -> None:
        dest = self._skill("task-router")
        result = self._run_cli("--target", "task-router", str(dest), "copy")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        marker = json.loads((dest / ".ghost-alice-install.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["owner"], "ghost-alice")
        self.assertIsNone(marker["addon_id"])

    def test_addon_target_symlink_mode_skips_marker(self) -> None:
        # symlink-mode addon targets get no marker (classify uses the link path).
        dest = self._skill("noop")
        result = self._run_cli("--addon-target", "noop", str(dest), "symlink", "noop")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse((dest / ".ghost-alice-install.json").exists())


class InstallerThreadsAddonAttributionTest(unittest.TestCase):
    """PowerShell parity: the Windows installer is only assertable by source on
    this platform. The bash path is proven end-to-end by the shell e2e."""

    def test_ps1_threads_addon_id_into_marker_writer(self) -> None:
        self.assertIn("--addon-target", installer_ps1_source())

    def test_ps1_selected_uninstall_checks_addon_dependents_before_removal(self) -> None:
        source = installer_ps1_source()

        self.assertIn("[switch]$Force", source)
        self.assertIn("Test-AddonDependentsForSkill", source)
        self.assertIn("--dependents", source)
        uninstall_body = source.split("function Invoke-Uninstall", 1)[1].split("function Invoke-UninstallCleanup", 1)[0]
        self.assertLess(
            uninstall_body.index("Test-AddonDependentsForSkill"),
            uninstall_body.index("Remove-InstalledTarget"),
        )


if __name__ == "__main__":
    unittest.main()
