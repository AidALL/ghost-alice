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


def _python_311_or_newer() -> str | None:
    candidates = [
        sys.executable,
        shutil.which("python3.14"),
        shutil.which("python3.13"),
        shutil.which("python3.12"),
        shutil.which("python3.11"),
        "/opt/homebrew/bin/python3",
        "/opt/homebrew/bin/python3.14",
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
        if shutil.which("bash") is None:
            self.skipTest("bash is required for install.sh")
        if _python_311_or_newer() is None:
            self.skipTest("install.sh requires Python 3.11+")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
            result = subprocess.run(
                [
                    "bash",
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

    def test_shell_install_without_addons_handles_empty_addon_targets(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("bash is required for install.sh")
        if _python_311_or_newer() is None:
            self.skipTest("install.sh requires Python 3.11+")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"
            result = subprocess.run(
                [
                    "bash",
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


if __name__ == "__main__":
    unittest.main()
