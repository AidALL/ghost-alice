"""Shell e2e: `--repair` is the only mutating reconciliation path.

It calls the ownership classifier before replacing anything.

- A managed target that went MISSING is re-provisioned (mutation).
- A target replaced after install (not cleanly managed; writer unknown) is LEFT UNTOUCHED (classify
  before replace) -- repair never clobbers unmanaged or domain content.

Run: python3 -m pytest _shared/test_repair_shell.py -q
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
from unittest import mock

from _shared.test_addon_installer import _find_test_bash
from _shared.addon_uninstall import _is_reparse_point, _symlink_safe_remove
from _shared.install_transaction import _create_directory_link

REPO_ROOT = Path(__file__).resolve().parents[1]
RICH = REPO_ROOT / "_shared" / "tests" / "fixtures" / "rich-addon"
if str(REPO_ROOT / "_shared") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "_shared"))
from installer_testing import checkout_copy, installer_env  # noqa: E402
import installed_payload  # noqa: E402
from installer import locations  # noqa: E402


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

    def _run(self, env, *args, package=None):
        package = package or self.source
        return subprocess.run(
            [self.bash, str(package / "install.sh"), *args], cwd=self.source,
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, check=False)

    def _line(self, env, folder, line):
        """Run a printed command as printed, from `folder`."""
        shell = ['pwsh', '-NoProfile', '-Command'] if os.name == 'nt' else [self.bash, '-c']
        return subprocess.run(
            [*shell, line], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180, check=False)

    def test_repair_restores_only_the_active_version(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_LOCALE"] = "en"
            ghost = Path(home) / ".ghost-alice"
            def run(option, code):
                source_option = [] if option == "--repair" else ["--local-source"]
                result = self._run(env, "--platform", "codex", "--skip-source-health", *source_option, option)
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
            skill = Path(home) / ".agents/skills/task-router"
            metadata = active.parent / "version.json"
            saved_metadata = metadata.read_bytes()
            metadata.unlink()
            for package in (self.source, active):
                result = self._run(env, "--repair", "--platform", "codex", package=package)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn(str(metadata), output)
                self.assertIn(locations.package_command(self.source, "update"), output)
            metadata.write_bytes(saved_metadata)
            for state in ("intact", "missing"):
                if state == "missing":
                    shutil.rmtree(active)
                shutil.rmtree(skill)
                before = record.read_bytes()
                recorded = installed_payload.read(ghost)
                version.write_bytes(version_bytes + b"\n")
                with self.subTest(active=state, source="different"):
                    if state == "intact":
                        # The active installer runs the repair, so the files of this checkout are not compared.
                        run("--repair", 0)
                        self.assertTrue((skill / "SKILL.md").is_file())
                        after = installed_payload.read(ghost)
                        for section in ("installer", "source"):
                            self.assertEqual(after[section], recorded[section])
                        self.assertEqual(sorted(after["targets"]), sorted(recorded["targets"]))
                        shutil.rmtree(skill)
                    else:
                        output = run("--repair", 1)
                        self.assertFalse(active.exists())
                        self.assertFalse(skill.exists())
                        self.assertEqual(record.read_bytes(), before)
                        self.assertIn(str(active), output)
                        self.assertNotIn(str(ghost / "bin/ghost-alice"), output)
                version.write_bytes(version_bytes)
                with self.subTest(active=state, source="matching"):
                    run("--repair", 0)
                    self.assertTrue((skill / "SKILL.md").is_file())
                    self.assertEqual(entry.read_bytes(), source_bytes)
                    self.assertEqual(Path(installed_payload.read(ghost)["installer"]["active_root"]), active)
            # An install of the same files restores the saved version too.
            shutil.rmtree(active)
            run("task-router", 0)
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
            self.assertIn(locations.package_command(self.source, "update"), output)
            self.assertEqual(record.read_bytes(), before)
            run("task-router", 0)
            self.assertEqual(entry.read_bytes(), source_bytes)

    def test_an_install_from_a_changed_checkout_replaces_an_incomplete_active_version(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            args = ("--platform", "codex", "--local-source", "--skip-source-health", "task-router")
            result = self._run(env, *args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            ghost = Path(home) / ".ghost-alice"
            version = self.source / "VERSION"
            for lost in ("_shared/installer/runner.py", "_shared", "."):
                with self.subTest(lost=lost):
                    active = Path(installed_payload.read(ghost)["installer"]["active_root"])
                    versions = set(active.parent.parent.iterdir())
                    path = active / lost
                    if path.is_file():
                        path.unlink()
                    else:
                        shutil.rmtree(path)
                    version.write_bytes(version.read_bytes() + b"\n")
                    result = self._run(env, *args)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    replacement = Path(installed_payload.read(ghost)["installer"]["active_root"])
                    self.assertEqual(set(active.parent.parent.iterdir()), versions | {replacement.parent})
                    self.assertNotEqual(replacement, active)
                    self.assertEqual((replacement / "VERSION").read_bytes(), version.read_bytes())
                    self.assertFalse(path.exists())
                    status = self._run(env, "--platform", "codex", "--status")
                    self.assertEqual(status.returncode, 0, status.stdout + status.stderr)

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
            installed = installed_payload.read(Path(home) / ".ghost-alice")
            self.assertEqual({entry["name"] for entry in installed["targets"].values()
                              if entry["kind"] == "skill" and "claude" in entry["used_by"]}, {"task-router"})
            self.assertFalse((skill.parent / "writing-plans").exists())

            # 2) the test replaced the managed slot with another dir -> repair must NOT clobber it
            _symlink_safe_remove(skill)
            skill.mkdir()
            (skill / "UNMANAGED_FILE").write_text("mine\n", encoding="utf-8")
            repair2 = self._run(env, "--platform", "claude", "--repair")
            self.assertTrue((skill / "UNMANAGED_FILE").exists(),
                            msg="repair clobbered an unclaimed slot: " + repair2.stderr + repair2.stdout)
            self.assertFalse(skill.is_symlink() or _is_reparse_point(skill))  # still the replacing dir, not re-symlinked

    def test_repair_preserves_the_installed_source_when_identical_files_have_another_commit(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            install = self._run(env, "--platform", "claude", "task-router", "writing-plans")
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            ghost = Path(home) / ".ghost-alice"
            before = installed_payload.read(ghost)
            skill = Path(home) / ".claude/skills/task-router"
            leaf = skill.parent / "writing-plans"
            shared = skill.parent / "_shared"
            inventory = ghost / "install-state/claude.json"
            original_inventory = inventory.read_bytes()
            _symlink_safe_remove(skill)
            _symlink_safe_remove(shared)
            _symlink_safe_remove(leaf)
            subprocess.run(["git", "commit", "--quiet", "--allow-empty", "-m", "Same package files"],
                           cwd=self.source, env=env, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.source, env=env, text=True).strip()
            self.assertNotEqual(head, before["source"]["commit"])
            repair = self._run(env, "--platform", "claude", "--repair")
            self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
            self.assertTrue((skill / "SKILL.md").is_file())
            after = installed_payload.read(ghost)
            self.assertEqual(after["source"], before["source"])
            self.assertEqual(after["installer"], before["installer"])
            self.assertEqual(after["targets"][skill.as_posix()]["source_commit"], before["source"]["commit"])
            package = Path(before["installer"]["active_root"])
            for target, original in ((skill, "task-router"), (shared, "_shared"), (leaf, "coding-convention/writing-plans")):
                self.assertEqual(Path(after["targets"][target.as_posix()]["source"]), package / original)
                marker = json.loads((target / ".ghost-alice-install.json").read_text(encoding="utf-8"))
                self.assertEqual(marker["source_repo"], str(package))
            self.assertEqual(inventory.read_bytes(), original_inventory)

    def test_a_repair_that_cannot_restore_the_active_package_names_the_update(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_LOCALE"] = "en"
            ghost = Path(home) / ".ghost-alice"
            skills = Path(home) / ".agents/skills"

            def run(code, *args):
                result = self._run(env, "--platform", "codex", "--skip-source-health", *args)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, code, output)
                return output

            run(0, "--local-source", "task-router")
            active = Path(installed_payload.read(ghost)["installer"]["active_root"])
            installed = sorted(path.name for path in skills.iterdir())
            # A file of the active package is lost, and the checkout holds another release.
            (active / "VERSION").unlink()
            version = self.source / "VERSION"
            version.write_bytes(version.read_bytes() + b"\n")
            shutil.rmtree(skills / "task-router")
            output = run(1, "--repair")
            printed = locations.package_command(self.source, "update")
            self.assertIn(printed, output)
            self.assertNotIn("--local-source", output)
            self.assertFalse((skills / "task-router").exists())
            # The printed command runs from another folder and installs the newest release for the installed skills only.
            update = self._line(env, home, printed)
            self.assertEqual(update.returncode, 0, update.stdout + update.stderr)
            self.assertEqual(sorted(path.name for path in skills.iterdir()), installed)
            self.assertTrue((Path(installed_payload.read(ghost)["installer"]["active_root"]) / "VERSION").is_file())

    def test_the_active_installer_names_what_restores_a_file_that_it_lost(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_LOCALE"] = "en"
            ghost = Path(home) / ".ghost-alice"
            with mock.patch.dict(os.environ, env):
                manage = locations.management_command("--repair", "--platform", "claude")

            def run(code, *args):
                result = self._run(env, "--platform", "claude", "--skip-source-health", *args)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, code, output)
                return output

            def managed(code):
                result = self._line(env, home, manage)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, code, output)
                return output

            run(0, "--local-source", "--addon-source", str(RICH), "task-router")
            active = Path(installed_payload.read(ghost)["installer"]["active_root"])
            with mock.patch.dict(os.environ, env):
                unknown = locations.management_command("--unknown-option")
                help_command = locations.management_command("--help")
            hint = self._line(env, home, unknown)
            self.assertEqual(hint.returncode, 1)
            self.assertIn("See " + help_command, hint.stdout)
            help_result = self._line(env, home, help_command)
            self.assertEqual(help_result.returncode, 0, help_result.stdout + help_result.stderr)
            self.assertIn("Usage:", help_result.stdout)
            # A Core file: the active installer has only its own package, so it names the repair of a checkout.
            lost = active / "task-router" / "SKILL.md"
            lost.unlink()
            output = managed(1)
            self.assertIn(str(lost), output)
            self.assertNotIn("Errno", output)
            # The printed repair of the registered checkout runs from another folder and restores the file.
            printed = locations.package_command(self.source, "--repair", "--platform", "claude")
            self.assertIn(printed, output)
            repair = self._line(env, home, printed)
            self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
            self.assertTrue(lost.is_file())
            # Without the installer of the registered checkout the message names no folder.
            lost.unlink()
            script = locations.package_script(self.source)
            script.rename(script.with_suffix(".away"))
            try:
                output = managed(1)
            finally:
                script.with_suffix(".away").rename(script)
            self.assertIn("checkout containing the same Core files", output)
            self.assertNotIn(str(self.source), output.replace(str(lost), ""))
            run(0, "--repair")
            self.assertTrue(lost.is_file())
            # An addon file: a repair that names no addon source has only the package's own addon files.
            lost = next(iter(sorted((active.parent / "addons").rglob("SKILL.md"))))
            lost.unlink()
            for output in (managed(1), run(1, "--repair")):
                self.assertIn(str(lost), output)
                self.assertIn("--addon-source", output)
                self.assertNotIn("Errno", output)
            run(0, "--repair", "--addon-source", str(RICH))
            self.assertTrue(lost.is_file())
            # The update that the active installer prints runs from another folder and replaces the damaged version.
            (active / "task-router" / "SKILL.md").unlink()
            printed = locations.package_command(active, "update")
            self.assertIn(printed, managed(1))
            update = self._line(env, home, printed)
            self.assertEqual(update.returncode, 0, update.stdout + update.stderr)
            replaced = Path(installed_payload.read(ghost)["installer"]["active_root"])
            self.assertNotEqual(replaced, active)
            self.assertTrue((replaced / "task-router" / "SKILL.md").is_file())
            self.assertTrue((Path(home) / ".claude/skills/task-router/SKILL.md").is_file())

    def test_an_installer_that_lost_its_own_file_names_the_repair_of_a_checkout(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_LOCALE"] = "en"
            ghost = Path(home) / ".ghost-alice"
            with mock.patch.dict(os.environ, env):
                manage = locations.management_command("--repair", "--platform", "claude")
            install = self._run(env, "--platform", "claude", "--skip-source-health", "--local-source", "task-router")
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            active = Path(installed_payload.read(ghost)["installer"]["active_root"])
            # The file that the shell starts, and a module that this file loads.
            for name in ("_shared/installer/cli.py", "_shared/installer/terminal.py"):
                with self.subTest(lost=name):
                    lost = active / name
                    lost.unlink()
                    result = self._line(env, home, manage)
                    output = result.stdout + result.stderr
                    self.assertEqual(result.returncode, 1, output)
                    self.assertIn("files are incomplete", output)
                    self.assertIn("checkout that holds the same Core files", output)
                    self.assertNotIn("Traceback", output)
                    # The repair of the checkout with the same files restores the file.
                    repair = self._run(env, "--platform", "claude", "--repair")
                    self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
                    self.assertTrue(lost.is_file())

    def test_repair_without_an_install_record_follows_the_inventory(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)

            def run(code, *args):
                result = self._run(env, "--platform", "claude", *args)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)

            run(0, "--local-source", "task-router")
            ghost = Path(home) / ".ghost-alice"
            inventory = ghost / "install-state/claude.json"
            skill = Path(home) / ".claude/skills/task-router"
            recorded = inventory.read_bytes()
            _symlink_safe_remove(skill)
            for name in installed_payload.RECORD_FILENAMES:
                (ghost / name).unlink(missing_ok=True)
            inventory.write_text("{", encoding="utf-8")
            run(1, "--repair")
            self.assertIsNone(installed_payload.read(ghost))
            inventory.write_bytes(recorded)
            run(0, "--repair")
            self.assertTrue((skill / "SKILL.md").is_file())
            # The record names no saved package as the install source, so the checkout stays the one that repairs and installs.
            run(0, "--repair")
            run(0, "--local-source", "task-router")
            self.assertEqual(installed_payload.read(ghost)["source"]["root"], str(self.source))

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

    def test_occupied_links_are_compared_with_the_package_used_for_repair(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            installed = self._run(env, "--local-source", "--platform", "claude", "task-router")
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            active = Path(installed_payload.read(Path(home) / ".ghost-alice")["installer"]["active_root"])
            version = active.parent / "version.json"
            saved = json.loads(version.read_text())
            saved["active_runs"] = []  # This version lets the checkout do the repair.
            version.write_text(json.dumps(saved))
            skills = Path(home) / ".claude/skills"
            for name in ("_shared", "task-router"):
                _symlink_safe_remove(skills / name)
                _create_directory_link(active / name, skills / name)
            for package in (active, self.source):
                result = self._run(env, "--repair", "--platform", "claude", package=package)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                self.assertIn("0 re-provisioned, 0 left untouched", output)

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
            self.assertIn("3 re-provisioned", repair.stdout)

            sidecar = Path(home) / ".ghost-alice/addons/claude/rich.json"
            record = json.loads(sidecar.read_text())
            command_entry = next(entry for entry in record["provided"] if entry["kind"] == "command")
            command_entry["target"] = str(Path(home) / "elsewhere.md")
            sidecar.write_text(json.dumps(record))
            broken = self._run(env, "--platform", "claude", "--repair")
            output = broken.stdout + broken.stderr
            self.assertEqual(broken.returncode, 1, output)
            for detail in ("rich", command_entry["target"], "command-target-mismatch"):
                self.assertIn(detail, output)
            self.assertNotIn("Repair complete", output)

    def test_repair_with_an_addon_source_restores_from_a_package_that_an_update_saved(self):
        with tempfile.TemporaryDirectory() as home:
            env = installer_env(home)
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"

            def run(*args):
                result = self._run(env, "--platform", "claude", "--skip-source-health", *args)
                self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

            run("--addon-source", str(RICH), "task-router")
            # An update saves Core only, so the active package holds no addon file.
            run("update")
            skill = Path(home) / ".claude" / "skills" / "task-router"
            addon_skill = Path(home) / ".claude" / "skills" / "richskill"
            _symlink_safe_remove(skill)
            addon_skill.unlink()
            run("--repair", "--addon-source", str(RICH))
            self.assertTrue((skill / "SKILL.md").is_file())
            self.assertTrue(os.path.lexists(addon_skill))
            # A lost Core file of that package comes back from the checkout's same files; the addon source is not compared.
            record = installed_payload.read(Path(home) / ".ghost-alice")
            lost = Path(record["installer"]["active_root"]) / "task-router" / "SKILL.md"
            lost.unlink()
            run("--repair", "--addon-source", str(RICH))
            self.assertTrue(lost.is_file())


if __name__ == "__main__":
    unittest.main()
