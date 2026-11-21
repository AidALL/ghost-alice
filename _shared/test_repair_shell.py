"""Shell e2e: `--repair` is the only mutating reconciliation path.

It calls the ownership classifier before replacing anything.

- A managed target that went MISSING is re-provisioned (mutation).
- A target replaced after install (not cleanly managed; writer unknown) is LEFT UNTOUCHED (classify
  before replace) -- repair never clobbers unmanaged or domain content.

Run: python3 -m pytest _shared/test_repair_shell.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _shared.test_addon_installer import _find_test_bash
from _shared.addon_uninstall import _is_reparse_point, _symlink_safe_remove
from _shared.install_transaction import _create_directory_link

REPO_ROOT = Path(__file__).resolve().parents[1]
RICH = REPO_ROOT / "_shared" / "tests" / "fixtures" / "rich-addon"
if str(REPO_ROOT / "_shared") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "_shared"))
from installer_testing import checkout_copy, installer_env  # noqa: E402
import installed_payload  # noqa: E402


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


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.bash = _find_test_bash()
        if not self.bash:
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")
        self.source = checkout_copy(self)

    def _run(self, env, *args):
        return subprocess.run(
            [self.bash, str(self.source / "install.sh"), *args], cwd=self.source,
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, check=False)

    def test_source_reinstall_and_repair_recover_only_the_matching_manager(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_LOCALE"] = "en"
            ghost = Path(home) / ".ghost-alice"
            def run(option, code):
                result = self._run(env, "--platform", "codex", "--skip-source-health", option)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, code, output)
                return output
            run("task-router", 0)
            record = installed_payload.read_path(ghost)
            active = Path(installed_payload.read(ghost)["installer"]["active_root"])
            entry = active / "_shared/installer/cli.py"
            source_bytes = (self.source / "_shared/installer/cli.py").read_bytes()
            version = self.source / "VERSION"
            version_bytes = version.read_bytes()
            for command in ("task-router", "--repair"):
                before = record.read_bytes()
                if active.exists():
                    shutil.rmtree(active)
                version.write_bytes(version_bytes + b"\n")
                with self.subTest(command=command, source="different"):
                    output = run(command, 1)
                    self.assertFalse(active.exists())
                    self.assertEqual(record.read_bytes(), before)
                    self.assertIn(str(active), output)
                    self.assertNotIn(str(ghost / "bin/ghost-alice"), output)
                version.write_bytes(version_bytes)
                with self.subTest(command=command, source="matching"):
                    run(command, 0)
                    self.assertEqual(entry.read_bytes(), source_bytes)
                    self.assertEqual(Path(installed_payload.read(ghost)["installer"]["active_root"]), active)
            entry.unlink()
            (active / "VERSION").write_bytes(b"modified archive")
            for command in ("task-router", "--repair"):
                before = record.read_bytes()
                run(command, 1)
                self.assertEqual(record.read_bytes(), before)
                self.assertFalse(entry.exists())
                self.assertEqual((active / "VERSION").read_bytes(), b"modified archive")
            # The record still identifies this content hash when the entire version was lost.
            shutil.rmtree(active.parent)
            before = record.read_bytes()
            output = run("--repair", 1)
            self.assertIn("entire saved version", output)
            self.assertIn("install.sh", output)
            self.assertEqual(record.read_bytes(), before)
            run("task-router", 0)
            self.assertEqual(entry.read_bytes(), source_bytes)

    def test_changed_checkout_requires_the_incomplete_active_manager_to_load(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_LOCALE"] = "en"
            args = ("--platform", "codex", "--skip-source-health", "task-router")
            result = self._run(env, *args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            ghost = Path(home) / ".ghost-alice"
            detail = installed_payload.read_path(ghost)
            original = detail.read_bytes()
            active = Path(installed_payload.read(ghost)["installer"]["active_root"])
            versions = set(active.parent.parent.iterdir())
            dependency = active / "_shared/installer/runner.py"
            dependency.unlink()
            version = self.source / "VERSION"
            version.write_bytes(version.read_bytes() + b"\n")
            refused = self._run(env, *args)
            output = refused.stdout + refused.stderr
            self.assertEqual(refused.returncode, 1, output)
            self.assertIn(str(active), output)
            self.assertFalse(dependency.exists())
            self.assertEqual(detail.read_bytes(), original)
            self.assertEqual(set(active.parent.parent.iterdir()), versions)
            shutil.copy2(self.source / "_shared/installer/runner.py", dependency)
            (active / "_shared/context_lifecycle.md").unlink()
            updated = self._run(env, *args)
            self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
            self.assertNotEqual(installed_payload.read(ghost)["installer"]["active_root"], str(active))

    def test_repair_reprovisions_missing_and_preserves_unclaimed_slot(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"

            install = self._run(env, "--platform", "claude", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            skill = Path(home) / ".claude" / "skills" / "task-router"
            self.assertTrue(skill.is_dir())
            self.assertFalse(skill.is_symlink() or _is_reparse_point(skill))

            # 1) target went missing -> repair must re-provision it
            _symlink_safe_remove(skill)
            self.assertFalse(os.path.lexists(skill))
            repair = self._run(env, "--platform", "claude", "--repair")
            self.assertEqual(repair.returncode, 0, msg=repair.stderr + repair.stdout)
            self.assertTrue(os.path.lexists(skill), msg="repair did not re-provision missing target: "
                            + repair.stderr + repair.stdout)

            # 2) the test replaced the managed slot with another dir -> repair must NOT clobber it
            _symlink_safe_remove(skill)
            skill.mkdir()
            (skill / "UNMANAGED_FILE").write_text("mine\n", encoding="utf-8")
            repair2 = self._run(env, "--platform", "claude", "--repair")
            self.assertTrue((skill / "UNMANAGED_FILE").exists(),
                            msg="repair clobbered an unclaimed slot: " + repair2.stderr + repair2.stdout)
            self.assertFalse(skill.is_symlink() or _is_reparse_point(skill))  # still the replacing dir, not re-symlinked

    def test_repair_fixes_dangling_shared(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
            install = self._run(env, "--platform", "claude", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            shared = Path(home) / ".claude" / "skills" / "_shared"
            # a DANGLING _shared symlink is functionally missing (every skill resolves shared modules through it) -> repair must restore it, not silently skip.
            _symlink_safe_remove(shared)
            missing_target = Path(home) / "nonexistent-target"
            missing_target.mkdir()
            _create_directory_link(missing_target, shared)
            shutil.rmtree(missing_target)
            self.assertFalse(shared.exists())  # dangling
            repair = self._run(env, "--platform", "claude", "--repair")
            self.assertEqual(repair.returncode, 0, msg=repair.stderr + repair.stdout)
            self.assertTrue(shared.exists(), msg="repair left _shared dangling: " + repair.stdout)
            self.assertTrue((shared / "hash_utils.py").exists())  # resolves into the repo again

    def test_repair_reprovisions_missing_addon_targets(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
            install = self._run(env, "--platform", "claude", "--addon-source", str(RICH),
                                "--skip-source-health", "task-router")
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            skill = Path(home) / ".claude" / "skills" / "richskill"
            command = Path(home) / ".claude" / "commands" / "richcmd.md"
            resource = Path(home) / ".ghost-alice" / "resources" / "claude" / "rich" / "ref.txt"
            self.assertTrue(os.path.lexists(skill))
            self.assertTrue(command.exists())
            self.assertTrue(resource.exists())

            skill.unlink()
            command.unlink()
            resource.unlink()
            active = Path(installed_payload.read(Path(home) / ".ghost-alice")["installer"]["active_root"])
            entry = active / "_shared/installer/cli.py"
            entry.unlink()

            repair = self._run(env, "--platform", "claude", "--repair")

            self.assertEqual(repair.returncode, 0, msg=repair.stderr + repair.stdout)
            self.assertEqual(entry.read_bytes(), (self.source / "_shared/installer/cli.py").read_bytes())
            self.assertTrue(os.path.lexists(skill), msg="repair left addon skill missing: " + repair.stdout)
            self.assertTrue(command.exists(), msg="repair left addon command missing: " + repair.stdout)
            self.assertTrue(resource.exists(), msg="repair left addon resource missing: " + repair.stdout)


if __name__ == "__main__":
    unittest.main()
