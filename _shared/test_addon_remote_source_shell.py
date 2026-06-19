"""Shell e2e: remote git addon sources clone, install, run, and uninstall.

The zsh launcher case matters because users often run the installer from a zsh
terminal. install.sh still owns a bash runtime contract, but zsh invocation must
re-exec bash before the installer reaches bash-only syntax.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_remote_source_shell.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _python_311() -> bool:
    candidates = [
        sys.executable,
        shutil.which("python3"),
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ]
    for candidate in candidates:
        if candidate and subprocess.run(
            [candidate, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
            capture_output=True,
            check=False,
        ).returncode == 0:
            return True
    return False


def _write_remote_autopilot_addon(work: Path) -> None:
    skill = work / "addons" / "autopilot-mode" / "skill"
    adapters = skill / "adapters"
    adapters.mkdir(parents=True)
    (work / "addons-manifest.json").write_text(
        json.dumps({
            "manifest_version": 1,
            "addons": [
                {
                    "id": "autopilot-mode",
                    "path": "addons/autopilot-mode",
                    "min_core_version": "0.1.0",
                }
            ],
        }),
        encoding="utf-8",
    )
    (work / "addons" / "autopilot-mode" / "addon.json").write_text(
        json.dumps({
            "addon_version": "0.1.0",
            "addon_id": "autopilot-mode",
            "skills": [{"name": "autopilot-mode", "source": "skill", "skill_dir": "skill"}],
            "platforms": ["claude"],
            "depends_on_core": ["task-router", "verification-before-completion"],
            "secrets": [],
            "privileged_adapters": ["autopilot-mode"],
        }),
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: autopilot-mode
            description: "Use for remote addon source install smoke tests."
            compatibility:
              - "Python 3.11+ standard library"
            ---

            # autopilot-mode

            ## Critical Rules

            - Test fixture only.
            """
        ),
        encoding="utf-8",
    )
    (adapters / "autopilot_mode.py").write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import sys

            if len(sys.argv) > 1:
                raise SystemExit(64)
            print(json.dumps({"continue": True, "systemMessage": "remote-clone-adapter-smoke"}))
            """
        ),
        encoding="utf-8",
    )


def _make_bare_remote(root: Path) -> str:
    work = root / "addon-work"
    bare = root / "addon.git"
    work.mkdir()
    _write_remote_autopilot_addon(work)
    subprocess.run(["git", "init"], cwd=work, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "checkout", "-b", "p6-privileged-adapter"], cwd=work, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-m", "remote addon fixture"], cwd=work, check=True, capture_output=True, text=True)
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)], cwd=root, check=True, capture_output=True, text=True)
    return bare.as_uri()


def _hook_commands(settings_path: Path) -> list[str]:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    return [
        hook.get("command", "")
        for event in settings.get("hooks", {}).values()
        if isinstance(event, list)
        for entry in event
        for hook in entry.get("hooks", [])
    ]


class RemoteAddonSourceLifecycleTest(unittest.TestCase):
    def test_git_url_addon_source_installs_runs_and_uninstalls_adapter_hook(self):
        if not shutil.which("bash"):
            self.skipTest("bash required")
        if not shutil.which("git"):
            self.skipTest("git required")
        if not _python_311():
            self.skipTest("python 3.11+ required")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            claude = root / "claude"
            project = root / "project"
            home.mkdir()
            claude.mkdir()
            project.mkdir()
            remote_url = _make_bare_remote(root)
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(claude),
                "GHOST_ALICE_INSTALL_PROGRESS": "off",
            })

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [shutil.which("bash"), str(REPO_ROOT / "install.sh"), *args],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=240,
                    check=False,
                )

            install = run(
                "--platform", "claude",
                "--addon-source", remote_url,
                "--addon-tag", "p6-privileged-adapter",
                "--skip-source-health",
                "task-router",
            )
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)

            cache = home / ".ghost-alice" / "addon-source-cache"
            cloned = [p for p in cache.glob("*") if (p / "addons-manifest.json").is_file()]
            self.assertEqual(len(cloned), 1, msg=f"expected one cloned addon source under {cache}")
            self.assertTrue((claude / "skills" / "autopilot-mode" / "SKILL.md").exists())

            commands = _hook_commands(claude / "settings.json")
            adapter_commands = [cmd for cmd in commands if "[adapter:autopilot-mode] continue" in cmd]
            self.assertEqual(len(adapter_commands), 1, msg="\n".join(commands))

            hook = subprocess.run(
                adapter_commands[0],
                cwd=project,
                env=env,
                input="{}\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(hook.returncode, 0, msg=hook.stderr + hook.stdout)
            self.assertIn("remote-clone-adapter-smoke", hook.stdout)

            per_addon = run("--platform", "claude", "--uninstall", "--addon", "autopilot-mode")
            self.assertEqual(per_addon.returncode, 0, msg=per_addon.stderr + per_addon.stdout)
            self.assertFalse((claude / "skills" / "autopilot-mode").exists())
            commands_after = _hook_commands(claude / "settings.json")
            self.assertFalse(any("[adapter:autopilot-mode]" in cmd for cmd in commands_after))
            self.assertTrue(any("tool-checkpoint" in cmd for cmd in commands_after), msg="core hooks should remain")

            status = run("--platform", "claude", "--status")
            self.assertEqual(status.returncode, 0, msg=status.stderr + status.stdout)
            self.assertNotIn("[adapter:autopilot-mode]", status.stdout + status.stderr)

    def test_zsh_launcher_reexecutes_bash_for_remote_addon_lifecycle(self):
        zsh = shutil.which("zsh")
        if not zsh:
            self.skipTest("zsh required")
        if not shutil.which("bash"):
            self.skipTest("bash required")
        if not shutil.which("git"):
            self.skipTest("git required")
        if not _python_311():
            self.skipTest("python 3.11+ required")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            claude = root / "claude"
            project = root / "project"
            home.mkdir()
            claude.mkdir()
            project.mkdir()
            remote_url = _make_bare_remote(root)
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(claude),
                "GHOST_ALICE_INSTALL_PROGRESS": "off",
            })

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [zsh, str(REPO_ROOT / "install.sh"), *args],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=240,
                    check=False,
                )

            install = run(
                "--platform", "claude",
                "--addon-source", remote_url,
                "--addon-tag", "p6-privileged-adapter",
                "--skip-source-health",
                "task-router",
            )
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)

            cache = home / ".ghost-alice" / "addon-source-cache"
            cloned = [p for p in cache.glob("*") if (p / "addons-manifest.json").is_file()]
            self.assertEqual(len(cloned), 1, msg=f"expected one cloned addon source under {cache}")
            self.assertTrue((claude / "skills" / "autopilot-mode" / "SKILL.md").exists())

            commands = _hook_commands(claude / "settings.json")
            adapter_commands = [cmd for cmd in commands if "[adapter:autopilot-mode] continue" in cmd]
            self.assertEqual(len(adapter_commands), 1, msg="\n".join(commands))

            hook = subprocess.run(
                adapter_commands[0],
                cwd=project,
                env=env,
                input="{}\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(hook.returncode, 0, msg=hook.stderr + hook.stdout)
            self.assertIn("remote-clone-adapter-smoke", hook.stdout)

            per_addon = run("--platform", "claude", "--uninstall", "--addon", "autopilot-mode")
            self.assertEqual(per_addon.returncode, 0, msg=per_addon.stderr + per_addon.stdout)
            commands_after = _hook_commands(claude / "settings.json")
            self.assertFalse(any("[adapter:autopilot-mode]" in cmd for cmd in commands_after))
            self.assertTrue(any("tool-checkpoint" in cmd for cmd in commands_after), msg="core hooks should remain")

            status = run("--platform", "claude", "--status")
            self.assertEqual(status.returncode, 0, msg=status.stderr + status.stdout)
            self.assertNotIn("[adapter:autopilot-mode]", status.stdout + status.stderr)

    def test_bad_git_ref_fails_before_installing_remote_addon_targets(self):
        if not shutil.which("bash"):
            self.skipTest("bash required")
        if not shutil.which("git"):
            self.skipTest("git required")
        if not _python_311():
            self.skipTest("python 3.11+ required")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            claude = root / "claude"
            home.mkdir()
            claude.mkdir()
            remote_url = _make_bare_remote(root)
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(claude),
                "GHOST_ALICE_INSTALL_PROGRESS": "off",
            })

            install = subprocess.run(
                [
                    shutil.which("bash"),
                    str(REPO_ROOT / "install.sh"),
                    "--platform", "claude",
                    "--addon-source", remote_url,
                    "--addon-tag", "missing-branch",
                    "--skip-source-health",
                    "task-router",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                check=False,
            )
            self.assertNotEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            self.assertIn("Addon source clone failed", install.stderr + install.stdout)
            self.assertFalse((claude / "skills" / "autopilot-mode").exists())
            self.assertFalse((claude / "settings.json").exists())

    def test_local_addon_source_rejects_addon_tag_without_install_side_effects(self):
        if not shutil.which("bash"):
            self.skipTest("bash required")
        if not _python_311():
            self.skipTest("python 3.11+ required")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            claude = root / "claude"
            addon = root / "addon-local"
            home.mkdir()
            claude.mkdir()
            addon.mkdir()
            _write_remote_autopilot_addon(addon)
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(claude),
                "GHOST_ALICE_INSTALL_PROGRESS": "off",
            })

            install = subprocess.run(
                [
                    shutil.which("bash"),
                    str(REPO_ROOT / "install.sh"),
                    "--platform", "claude",
                    "--addon-source", str(addon),
                    "--addon-tag", "p6-privileged-adapter",
                    "--skip-source-health",
                    "task-router",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                check=False,
            )
            self.assertNotEqual(install.returncode, 0, msg=install.stderr + install.stdout)
            self.assertIn("--addon-tag can only be used with git URL addon sources", install.stderr + install.stdout)
            self.assertFalse((claude / "skills" / "autopilot-mode").exists())
            self.assertFalse((claude / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
