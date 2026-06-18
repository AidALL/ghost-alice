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
FIXTURE_ROOT = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"
sys.path.insert(0, str(REPO_ROOT / "_shared"))


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
        if (
            normalized.endswith("/windows/system32/bash.exe")
            or normalized.endswith("/appdata/local/microsoft/windowsapps/bash.exe")
        ):
            continue
        probe = subprocess.run(
            [str(path), "-lc", "printf '%s\\n' ok"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return str(path)
    return None


def _python_311_or_newer() -> str | None:
    candidates = [
        sys.executable,
        shutil.which("python3"),
        shutil.which("python"),
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        result = subprocess.run(
            [
                candidate,
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return candidate
    return None


class AddonInstallerTest(unittest.TestCase):
    def test_fixture_manifest_discovers_noop_skill_target(self) -> None:
        from addon_installer import load_addon_targets

        targets = load_addon_targets(
            [FIXTURE_ROOT],
            core_skill_names={"task-router", "verification-before-completion"},
        )

        self.assertEqual(len(targets), 1)
        target = targets[0]
        self.assertEqual(target.name, "noop")
        self.assertEqual(target.addon_id, "noop")
        self.assertEqual(target.origin, "addon:noop")
        self.assertEqual(target.source, FIXTURE_ROOT / "addons" / "noop" / "skill")
        self.assertEqual(target.tags, ("test",))

    def test_core_skill_name_collision_is_rejected(self) -> None:
        from addon_installer import AddonManifestError, load_addon_targets

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            addon_dir = source / "addons" / "task-router" / "skill"
            addon_dir.mkdir(parents=True)
            addon_dir.joinpath("SKILL.md").write_text(
                "---\nname: task-router\ndescription: collision\n---\n",
                encoding="utf-8",
            )
            source.joinpath("addons-manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 1,
                        "addons": [
                            {
                                "id": "task-router",
                                "path": "addons/task-router",
                                "tags": ["bad"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            source.joinpath("addons/task-router/addon.json").write_text(
                json.dumps(
                    {
                        "addon_version": "0.1.0",
                        "addon_id": "task-router",
                        "skills": [
                            {
                                "name": "task-router",
                                "source": "skill",
                                "skill_dir": "skill",
                            }
                        ],
                        "platforms": ["claude", "codex"],
                        "depends_on_core": ["task-router"],
                        "secrets": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(AddonManifestError, "collides with core skill"):
                load_addon_targets([source], core_skill_names={"task-router"})

    def _write_dep_addon(self, source: Path, depends_on_core: list[str]) -> None:
        skill = source / "addons" / "needs" / "skill"
        skill.mkdir(parents=True)
        skill.joinpath("SKILL.md").write_text(
            "---\nname: needsskill\ndescription: dep test\n---\n", encoding="utf-8")
        source.joinpath("addons-manifest.json").write_text(json.dumps({
            "manifest_version": 1,
            "addons": [{"id": "needs", "path": "addons/needs", "tags": ["t"]}],
        }), encoding="utf-8")
        source.joinpath("addons/needs/addon.json").write_text(json.dumps({
            "addon_version": "0.1.0", "addon_id": "needs",
            "skills": [{"name": "needsskill", "source": "skill", "skill_dir": "skill"}],
            "platforms": ["claude", "codex"],
            "depends_on_core": depends_on_core, "secrets": [],
        }), encoding="utf-8")

    def test_depends_on_core_absent_from_core_set_is_rejected(self) -> None:
        # depends_on_core enforcement: a declared depends_on_core not in the known core skill set
        # must fail closed at load time (decorative contract -> enforced contract).
        from addon_installer import AddonManifestError, load_addon_targets
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            self._write_dep_addon(source, ["definitely-not-installed-core"])
            with self.assertRaisesRegex(AddonManifestError, "depends_on_core"):
                load_addon_targets([source], core_skill_names={"task-router"})

    def test_depends_on_core_satisfied_loads(self) -> None:
        # A dep that IS in the core set loads normally (positive guard).
        from addon_installer import load_addon_targets
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            self._write_dep_addon(source, ["task-router"])
            targets = load_addon_targets([source], core_skill_names={"task-router"})
            self.assertEqual([t.name for t in targets], ["needsskill"])

    def test_depends_on_core_not_enforced_without_core_set(self) -> None:
        # When no core list is provided, the check cannot run -> must not raise
        # (enforcement only fires where the core set is known).
        from addon_installer import load_addon_targets
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            self._write_dep_addon(source, ["definitely-not-installed-core"])
            targets = load_addon_targets([source])  # no core_skill_names
            self.assertEqual([t.name for t in targets], ["needsskill"])

    _RICH_FIXTURE = REPO_ROOT / "_shared" / "tests" / "fixtures" / "rich-addon"

    def test_detect_collisions_flags_unowned_command_extra(self) -> None:
        # Preflight must catch a command/resource extra whose dest already exists
        # and is NOT owned by the installing addon. Otherwise the collision only
        # surfaces at provision time (after hooks/skills are written), orphaning
        # an installed addon skill + hook with no sidecar to uninstall.
        from addon_installer import detect_collisions, load_addon_targets
        targets = load_addon_targets([str(self._RICH_FIXTURE)], platform="claude")
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            commands = tmp / "commands"
            commands.mkdir()
            (commands / "richcmd.md").write_text("pre-existing not-owned file\n", encoding="utf-8")
            collisions = detect_collisions(
                targets, skills_dir=tmp / "skills", addons_dir=tmp / "addons",
                claude_commands_dir=commands, resources_dir=tmp / "resources",
                platform="claude",
            )
            self.assertIn("richcmd", {c["name"] for c in collisions})

    def test_detect_collisions_ignores_absent_command_extra(self) -> None:
        # No false positive: when no extra dest pre-exists, there is no collision.
        from addon_installer import detect_collisions, load_addon_targets
        targets = load_addon_targets([str(self._RICH_FIXTURE)], platform="claude")
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            collisions = detect_collisions(
                targets, skills_dir=tmp / "skills", addons_dir=tmp / "addons",
                claude_commands_dir=tmp / "commands", resources_dir=tmp / "resources",
                platform="claude",
            )
            self.assertEqual([], collisions)

    def test_cli_list_outputs_machine_readable_targets(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "_shared" / "addon_installer.py"),
                "--source",
                str(FIXTURE_ROOT),
                "--core-skill",
                "task-router",
                "--format",
                "json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target_count"], 1)
        self.assertEqual(payload["targets"][0]["name"], "noop")
        self.assertEqual(payload["targets"][0]["origin"], "addon:noop")

    def test_install_hooks_dry_run_lists_addon_targets(self) -> None:
        python = _python_311_or_newer()
        if python is None:
            self.skipTest("install_hooks.py requires Python 3.11+")
        result = subprocess.run(
            [
                python,
                str(REPO_ROOT / "_shared" / "install_hooks.py"),
                "--addon-source",
                str(FIXTURE_ROOT),
                "--list-addons",
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("noop (addon:noop)", result.stdout)

    def test_shell_installs_dummy_addon_into_temp_home(self) -> None:
        bash = _find_test_bash()
        if bash is None:
            self.skipTest("Git Bash is required for install.sh")
        if _python_311_or_newer() is None:
            self.skipTest("install.sh requires Python 3.11+")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
            result = subprocess.run(
                [
                    bash,
                    str(REPO_ROOT / "install.sh"),
                    "--platform",
                    "claude",
                    "--addon-source",
                    str(FIXTURE_ROOT),
                    "--skip-source-health",
                    "task-router",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            installed_skill = Path(temp_home) / ".claude" / "skills" / "noop" / "SKILL.md"
            self.assertTrue(installed_skill.exists(), msg=result.stderr + result.stdout)

            state_path = Path(temp_home) / ".ghost-alice" / "install-state" / "claude.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            target_names = {target["target_name"] for target in state["targets"]}
            self.assertIn("noop", target_names)

            # End-to-end: the install path wrote the per-addon sidecar (T1.3),
            # platform-scoped under addons/<platform>/ (review M5).
            sidecar = Path(temp_home) / ".ghost-alice" / "addons" / "claude" / "noop.json"
            self.assertTrue(sidecar.exists(), msg=result.stderr + result.stdout)
            record = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(record["addon_id"], "noop")
            self.assertEqual(record["origin"], "addon:noop")
            self.assertEqual(record["min_core_version"], "0.1.0")
            provided_names = {p["name"] for p in record["provided"]}
            self.assertIn("noop", provided_names)
            noop_entry = next(p for p in record["provided"] if p["name"] == "noop")
            self.assertEqual(noop_entry["install_mode"], "symlink")
            self.assertTrue(noop_entry["content_hash"])

            # F1 end-to-end: the SAME-run sidecar must be reflected in install-state attribution.
            state_noop = next(t for t in state["targets"] if t["target_name"] == "noop")
            self.assertEqual(state_noop.get("addon_id"), "noop")
            self.assertEqual(state_noop.get("origin"), "addon:noop")
            self.assertEqual(state_noop.get("owner"), "addon")

    def test_shell_install_without_addons_handles_empty_addon_targets(self) -> None:
        bash = _find_test_bash()
        if bash is None:
            self.skipTest("Git Bash is required for install.sh")
        if _python_311_or_newer() is None:
            self.skipTest("install.sh requires Python 3.11+")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
            result = subprocess.run(
                [
                    bash,
                    str(REPO_ROOT / "install.sh"),
                    "--platform",
                    "claude",
                    "--skip-source-health",
                    "task-router",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            installed_skill = Path(temp_home) / ".claude" / "skills" / "task-router" / "SKILL.md"
            self.assertTrue(installed_skill.exists(), msg=result.stderr + result.stdout)

    def test_install_entrypoints_expose_addon_options(self) -> None:
        install_sh = REPO_ROOT.joinpath("install.sh").read_text(encoding="utf-8")
        install_ps1 = REPO_ROOT.joinpath("install.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("--addon-source", install_sh)
        self.assertIn("--addon-skip", install_sh)
        self.assertIn("--list-addons", install_sh)
        self.assertIn("[string[]]$AddonSource", install_ps1)
        self.assertIn("[switch]$AddonSkip", install_ps1)
        self.assertIn("[switch]$ListAddons", install_ps1)

    def test_powershell_addon_install_fails_closed_until_sidecar_parity_exists(self) -> None:
        install_ps1 = (REPO_ROOT / "installer_lib" / "install.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("Assert-PowerShellAddonInstallSupported", install_ps1)
        self.assertLess(
            install_ps1.index("Assert-PowerShellAddonInstallSupported"),
            install_ps1.index("$addonTargets = @(Get-AddonTargets)"),
        )


if __name__ == "__main__":
    unittest.main()
