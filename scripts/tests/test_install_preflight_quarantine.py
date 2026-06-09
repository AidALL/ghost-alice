import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "merge-companion" / "scripts"))
from installer_assets import write_ownership_marker
from file_walker import walk_user_files


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"


def _find_test_bash() -> str | None:
    candidates = [
        shutil.which("bash"),
        shutil.which("bash.exe"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        normalized = path.as_posix().lower()
        if sys.platform.startswith("win") and (
            normalized.endswith("/windows/system32/bash.exe")
            or normalized.endswith("/appdata/local/microsoft/windowsapps/bash.exe")
        ):
            continue
        return str(path)

    return None


def _find_test_powershell() -> str | None:
    candidates = [
        shutil.which("pwsh.exe"),
        shutil.which("pwsh"),
        shutil.which("powershell.exe"),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return str(path)

    return None


class InstallPreflightQuarantineTest(unittest.TestCase):
    def test_file_walker_skips_windows_junction_skill_roots(self) -> None:
        if not sys.platform.startswith("win"):
            self.skipTest("Windows junction behavior is platform-specific")
        powershell = _find_test_powershell()
        if not powershell:
            self.skipTest("No PowerShell executable available for junction test")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_skill = root / "repo" / "task-router"
            repo_skill.mkdir(parents=True)
            (repo_skill / "SKILL.md").write_text("# repo skill\n", encoding="utf-8")
            skills_dir = root / ".claude" / "skills"
            skills_dir.mkdir(parents=True)
            junction = skills_dir / "task-router"
            source = str(repo_skill).replace("'", "''")
            dest = str(junction).replace("'", "''")

            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"New-Item -ItemType Junction -Path '{dest}' -Target '{source}' | Out-Null",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

            self.assertEqual(walk_user_files(skills_dir), [])

    def test_install_sh_backs_up_user_edit_before_copy_replacement(self) -> None:
        bash_exe = _find_test_bash()
        if not bash_exe:
            self.skipTest("No non-WSL bash executable available for install.sh integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"

            cmd = [bash_exe, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", "task-router"]
            first = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(first.returncode, 0, msg=first.stderr + first.stdout)

            installed_skill = temp_home / ".agents" / "skills" / "task-router" / "SKILL.md"
            self.assertTrue(installed_skill.exists())
            installed_skill.write_text("user edit before reinstall\n", encoding="utf-8")

            second = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(second.returncode, 0, msg=second.stderr + second.stdout)

            pending = temp_home / ".ghost-alice" / "pending-merges" / "codex"
            backup_files = list(pending.rglob("*.bak"))
            self.assertEqual(len(backup_files), 1, msg=second.stderr + second.stdout)
            self.assertEqual(backup_files[0].read_text(encoding="utf-8"), "user edit before reinstall\n")

    def test_install_sh_aborts_before_overwrite_when_preflight_fails(self) -> None:
        bash_exe = _find_test_bash()
        if not bash_exe:
            self.skipTest("No non-WSL bash executable available for install.sh integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"

            cmd = [bash_exe, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", "task-router"]
            first = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(first.returncode, 0, msg=first.stderr + first.stdout)

            installed_skill = temp_home / ".agents" / "skills" / "task-router" / "SKILL.md"
            installed_skill.write_text("user edit should survive failed preflight\n", encoding="utf-8")
            snapshot = temp_home / ".ghost-alice" / "pending-merges" / "codex" / "snapshot.json"
            snapshot.write_text("{not valid json", encoding="utf-8")

            second = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertNotEqual(second.returncode, 0, msg=second.stderr + second.stdout)
            self.assertEqual(
                installed_skill.read_text(encoding="utf-8"),
                "user edit should survive failed preflight\n",
            )

    def test_install_sh_quarantines_legacy_target_when_snapshot_is_missing(self) -> None:
        bash_exe = _find_test_bash()
        if not bash_exe:
            self.skipTest("No non-WSL bash executable available for install.sh integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            legacy_skill = temp_home / ".agents" / "skills" / "task-router"
            legacy_skill.mkdir(parents=True)
            (legacy_skill / "SKILL.md").write_text("legacy bash skill edit\n", encoding="utf-8")

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            cmd = [bash_exe, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", "task-router"]

            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

            pending = temp_home / ".ghost-alice" / "pending-merges" / "codex"
            quarantined = list((pending / "legacy-targets").rglob("SKILL.md"))
            self.assertEqual(len(quarantined), 1, msg=result.stderr + result.stdout)
            self.assertEqual(quarantined[0].read_text(encoding="utf-8"), "legacy bash skill edit\n")
            manifest = json.loads((pending / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["entries"][0]["reason"], "legacy-no-baseline")
            self.assertFalse(manifest["entries"][0]["decided"])

    def test_install_sh_does_not_quarantine_clean_managed_target_when_snapshot_is_missing(self) -> None:
        bash_exe = _find_test_bash()
        if not bash_exe:
            self.skipTest("No non-WSL bash executable available for install.sh integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            managed_skill = temp_home / ".agents" / "skills" / "task-router"
            shutil.copytree(REPO_ROOT / "task-router", managed_skill)
            write_ownership_marker(
                managed_skill,
                platform="codex",
                asset_id="task-router",
                source_repo="/old/ghost-alice",
                source_commit="old-head",
                install_mode="copy",
            )

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            cmd = [bash_exe, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", "task-router"]

            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

            pending = temp_home / ".ghost-alice" / "pending-merges" / "codex"
            manifest = pending / "manifest.json"
            entries = json.loads(manifest.read_text(encoding="utf-8"))["entries"] if manifest.exists() else []
            self.assertEqual(entries, [], msg=result.stderr + result.stdout)
            self.assertFalse((pending / "legacy-targets").exists(), msg=result.stderr + result.stdout)

    def test_install_sh_aborts_when_existing_custom_skill_has_invalid_encoding(self) -> None:
        bash_exe = _find_test_bash()
        if not bash_exe:
            self.skipTest("No non-WSL bash executable available for install.sh integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            custom_skill = temp_home / ".agents" / "skills" / "my-local-skill"
            custom_skill.mkdir(parents=True)
            (custom_skill / "SKILL.md").write_bytes(b"\xff")

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            cmd = [bash_exe, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", "task-router"]

            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("invalid-utf8", result.stderr + result.stdout)
            self.assertFalse((temp_home / ".agents" / "skills" / "task-router").exists())
            self.assertEqual((custom_skill / "SKILL.md").read_bytes(), b"\xff")

    def test_install_sh_preserves_existing_custom_skill(self) -> None:
        bash_exe = _find_test_bash()
        if not bash_exe:
            self.skipTest("No non-WSL bash executable available for install.sh integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            custom_skill = temp_home / ".agents" / "skills" / "my-local-skill"
            custom_skill.mkdir(parents=True)
            custom_body = "---\nname: my-local-skill\ndescription: Local skill.\n---\n\n# Keep Me\n"
            (custom_skill / "SKILL.md").write_text(custom_body, encoding="utf-8")

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            cmd = [bash_exe, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", "task-router"]

            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertEqual((custom_skill / "SKILL.md").read_text(encoding="utf-8"), custom_body)
            self.assertTrue((temp_home / ".agents" / "skills" / "task-router" / "SKILL.md").exists())

    def test_install_ps1_backs_up_user_edit_before_copy_replacement(self) -> None:
        powershell = _find_test_powershell()
        if not powershell:
            self.skipTest("No PowerShell executable available for install.ps1 integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            env = os.environ.copy()
            env["HOME"] = str(temp_home)
            env["USERPROFILE"] = str(temp_home)

            cmd = [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALL_PS1),
                "-Platform",
                "codex",
                "-SkipSourceHealth",
                "-Skills",
                "task-router",
            ]
            first = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(first.returncode, 0, msg=first.stderr + first.stdout)

            installed_skill = temp_home / ".agents" / "skills" / "task-router" / "SKILL.md"
            self.assertTrue(installed_skill.exists())
            installed_skill.write_text("powershell user edit before reinstall\n", encoding="utf-8")

            second = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(second.returncode, 0, msg=second.stderr + second.stdout)

            pending = temp_home / ".ghost-alice" / "pending-merges" / "codex"
            backup_files = list(pending.rglob("*.bak"))
            self.assertEqual(len(backup_files), 1, msg=second.stderr + second.stdout)
            self.assertEqual(
                backup_files[0].read_text(encoding="utf-8"),
                "powershell user edit before reinstall\n",
            )

    def test_install_ps1_aborts_before_overwrite_when_preflight_fails(self) -> None:
        powershell = _find_test_powershell()
        if not powershell:
            self.skipTest("No PowerShell executable available for install.ps1 integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            env = os.environ.copy()
            env["HOME"] = str(temp_home)
            env["USERPROFILE"] = str(temp_home)

            cmd = [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALL_PS1),
                "-Platform",
                "codex",
                "-SkipSourceHealth",
                "-Skills",
                "task-router",
            ]
            first = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(first.returncode, 0, msg=first.stderr + first.stdout)

            installed_skill = temp_home / ".agents" / "skills" / "task-router" / "SKILL.md"
            installed_skill.write_text("powershell user edit should survive failed preflight\n", encoding="utf-8")
            snapshot = temp_home / ".ghost-alice" / "pending-merges" / "codex" / "snapshot.json"
            snapshot.write_text("{not valid json", encoding="utf-8")

            second = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertNotEqual(second.returncode, 0, msg=second.stderr + second.stdout)
            self.assertEqual(
                installed_skill.read_text(encoding="utf-8"),
                "powershell user edit should survive failed preflight\n",
            )

    def test_install_ps1_quarantines_legacy_target_when_snapshot_is_missing(self) -> None:
        powershell = _find_test_powershell()
        if not powershell:
            self.skipTest("No PowerShell executable available for install.ps1 integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            legacy_skill = temp_home / ".agents" / "skills" / "task-router"
            legacy_skill.mkdir(parents=True)
            (legacy_skill / "SKILL.md").write_text("legacy powershell skill edit\n", encoding="utf-8")

            env = os.environ.copy()
            env["HOME"] = str(temp_home)
            env["USERPROFILE"] = str(temp_home)
            cmd = [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALL_PS1),
                "-Platform",
                "codex",
                "-SkipSourceHealth",
                "-Skills",
                "task-router",
            ]

            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

            pending = temp_home / ".ghost-alice" / "pending-merges" / "codex"
            quarantined = list((pending / "legacy-targets").rglob("SKILL.md"))
            self.assertEqual(len(quarantined), 1, msg=result.stderr + result.stdout)
            self.assertEqual(quarantined[0].read_text(encoding="utf-8"), "legacy powershell skill edit\n")
            manifest = json.loads((pending / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["entries"][0]["reason"], "legacy-no-baseline")
            self.assertFalse(manifest["entries"][0]["decided"])

    def test_install_ps1_does_not_quarantine_clean_managed_target_when_snapshot_is_missing(self) -> None:
        powershell = _find_test_powershell()
        if not powershell:
            self.skipTest("No PowerShell executable available for install.ps1 integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            managed_skill = temp_home / ".agents" / "skills" / "task-router"
            shutil.copytree(REPO_ROOT / "task-router", managed_skill)
            write_ownership_marker(
                managed_skill,
                platform="codex",
                asset_id="task-router",
                source_repo="/old/ghost-alice",
                source_commit="old-head",
                install_mode="copy",
            )

            env = os.environ.copy()
            env["HOME"] = str(temp_home)
            env["USERPROFILE"] = str(temp_home)
            cmd = [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALL_PS1),
                "-Platform",
                "codex",
                "-SkipSourceHealth",
                "-Skills",
                "task-router",
            ]

            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

            pending = temp_home / ".ghost-alice" / "pending-merges" / "codex"
            manifest = pending / "manifest.json"
            entries = json.loads(manifest.read_text(encoding="utf-8"))["entries"] if manifest.exists() else []
            self.assertEqual(entries, [], msg=result.stderr + result.stdout)
            self.assertFalse((pending / "legacy-targets").exists(), msg=result.stderr + result.stdout)

    def test_install_ps1_aborts_when_existing_custom_skill_has_invalid_encoding(self) -> None:
        powershell = _find_test_powershell()
        if not powershell:
            self.skipTest("No PowerShell executable available for install.ps1 integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            custom_skill = temp_home / ".agents" / "skills" / "my-local-skill"
            custom_skill.mkdir(parents=True)
            (custom_skill / "SKILL.md").write_bytes(b"\xff")

            env = os.environ.copy()
            env["HOME"] = str(temp_home)
            env["USERPROFILE"] = str(temp_home)
            cmd = [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALL_PS1),
                "-Platform",
                "codex",
                "-SkipSourceHealth",
                "-Skills",
                "task-router",
            ]

            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("invalid-utf8", result.stderr + result.stdout)
            self.assertFalse((temp_home / ".agents" / "skills" / "task-router").exists())
            self.assertEqual((custom_skill / "SKILL.md").read_bytes(), b"\xff")

    def test_install_ps1_preserves_existing_custom_skill(self) -> None:
        powershell = _find_test_powershell()
        if not powershell:
            self.skipTest("No PowerShell executable available for install.ps1 integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            custom_skill = temp_home / ".agents" / "skills" / "my-local-skill"
            custom_skill.mkdir(parents=True)
            custom_body = "---\nname: my-local-skill\ndescription: Local skill.\n---\n\n# Keep Me\n"
            (custom_skill / "SKILL.md").write_text(custom_body, encoding="utf-8")

            env = os.environ.copy()
            env["HOME"] = str(temp_home)
            env["USERPROFILE"] = str(temp_home)
            cmd = [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALL_PS1),
                "-Platform",
                "codex",
                "-SkipSourceHealth",
                "-Skills",
                "task-router",
            ]

            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertEqual((custom_skill / "SKILL.md").read_text(encoding="utf-8"), custom_body)
            self.assertTrue((temp_home / ".agents" / "skills" / "task-router" / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
