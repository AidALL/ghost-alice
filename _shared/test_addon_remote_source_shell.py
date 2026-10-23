"""Shell e2e: remote git addon sources clone, install, run, and uninstall.

The zsh launcher case matters because users often run the installer from a zsh
terminal. install.sh still owns a bash runtime contract, but zsh invocation must
re-exec bash before the installer reaches bash-only syntax.

Run: python3 -m pytest _shared/test_addon_remote_source_shell.py -q
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

from _shared.test_addon_installer import _find_test_bash

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "_shared") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "_shared"))
from installer_testing import checkout_copy  # noqa: E402


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


def _write_remote_hook_addon(work: Path) -> None:
    skill = work / "addons" / "remote-demo" / "skill"
    hooks = work / "addons" / "remote-demo" / "hooks"
    hooks.mkdir(parents=True)
    skill.mkdir(parents=True)
    (work / "addons-manifest.json").write_text(
        json.dumps({
            "manifest_version": 1,
            "addons": [
                {
                    "id": "remote-demo",
                    "path": "addons/remote-demo",
                    "min_core_version": "0.1.0",
                }
            ],
        }),
        encoding="utf-8",
    )
    (work / "addons" / "remote-demo" / "addon.json").write_text(
        json.dumps({
            "addon_version": "0.1.0",
            "addon_id": "remote-demo",
            "skills": [{"name": "remote-demo", "source": "skill", "skill_dir": "skill"}],
            "platforms": ["claude"],
            "depends_on_core": ["task-router", "verification-before-completion"],
            "secrets": [],
            "hooks": [{"id": "watch", "event": "post_tool_use", "script": "hooks/watch.py"}],
        }),
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: remote-demo
            description: "Use for remote addon source install smoke tests."
            compatibility:
              - "Python 3.11+ standard library"
            ---

            # remote-demo

            ## Critical Rules

            - Test fixture only.
            """
        ),
        encoding="utf-8",
    )
    (hooks / "watch.py").write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import sys

            print(json.dumps({"continue": True, "systemMessage": "remote-clone-hook-smoke"}))
            """
        ),
        encoding="utf-8",
    )


def _make_bare_remote(root: Path) -> str:
    work = root / "addon-work"
    bare = root / "addon.git"
    work.mkdir()
    _write_remote_hook_addon(work)
    subprocess.run(["git", "init"], cwd=work, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "checkout", "-b", "remote-hook-fixture"], cwd=work, check=True, capture_output=True, text=True)
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
    def test_git_url_addon_source_installs_runs_and_uninstalls_its_hook(self):
        bash = _find_test_bash()
        if not bash:
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

            source = checkout_copy(self)

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [bash, str(source / "install.sh"), *args],
                    cwd=source,
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
                "--addon-tag", "remote-hook-fixture",
                "--skip-source-health",
                "task-router",
            )
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)

            cache = home / ".ghost-alice" / "addon-source-cache"
            cloned = [p for p in cache.glob("*") if (p / "addons-manifest.json").is_file()]
            self.assertEqual(len(cloned), 1, msg=f"expected one cloned addon source under {cache}")
            self.assertTrue((claude / "skills" / "remote-demo" / "SKILL.md").exists())

            commands = _hook_commands(claude / "settings.json")
            hook_commands = [cmd for cmd in commands if "[addon:remote-demo] watch" in cmd]
            self.assertEqual(len(hook_commands), 1, msg="\n".join(commands))

            hook = subprocess.run(
                hook_commands[0],
                cwd=project,
                env=env,
                input=json.dumps({"hook_event_name":"PostToolUse", "session_id":"remote-addon-smoke", "cwd":str(project)}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(hook.returncode, 0, msg=hook.stderr + hook.stdout)
            self.assertIn("remote-clone-hook-smoke", hook.stdout)

            per_addon = run("--platform", "claude", "--uninstall", "--addon", "remote-demo")
            self.assertEqual(per_addon.returncode, 0, msg=per_addon.stderr + per_addon.stdout)
            self.assertFalse((claude / "skills" / "remote-demo").exists())
            commands_after = _hook_commands(claude / "settings.json")
            self.assertFalse(any("[addon:remote-demo]" in cmd for cmd in commands_after))
            self.assertTrue(any("tool-checkpoint" in cmd for cmd in commands_after), msg="core hooks should remain")

            status = run("--platform", "claude", "--status")
            self.assertEqual(status.returncode, 0, msg=status.stderr + status.stdout)
            self.assertNotIn("[addon:remote-demo]", status.stdout + status.stderr)

    def test_zsh_launcher_reexecutes_bash_for_remote_addon_lifecycle(self):
        zsh = shutil.which("zsh")
        if not zsh:
            self.skipTest("zsh required")
        if not _find_test_bash():
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

            source = checkout_copy(self)

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [zsh, str(source / "install.sh"), *args],
                    cwd=source,
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
                "--addon-tag", "remote-hook-fixture",
                "--skip-source-health",
                "task-router",
            )
            self.assertEqual(install.returncode, 0, msg=install.stderr + install.stdout)

            cache = home / ".ghost-alice" / "addon-source-cache"
            cloned = [p for p in cache.glob("*") if (p / "addons-manifest.json").is_file()]
            self.assertEqual(len(cloned), 1, msg=f"expected one cloned addon source under {cache}")
            self.assertTrue((claude / "skills" / "remote-demo" / "SKILL.md").exists())

            commands = _hook_commands(claude / "settings.json")
            hook_commands = [cmd for cmd in commands if "[addon:remote-demo] watch" in cmd]
            self.assertEqual(len(hook_commands), 1, msg="\n".join(commands))

            hook = subprocess.run(
                hook_commands[0],
                cwd=project,
                env=env,
                input=json.dumps({"hook_event_name":"Stop", "session_id":"remote-addon-zsh-smoke", "cwd":str(project)}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(hook.returncode, 0, msg=hook.stderr + hook.stdout)
            self.assertIn("remote-clone-hook-smoke", hook.stdout)

            per_addon = run("--platform", "claude", "--uninstall", "--addon", "remote-demo")
            self.assertEqual(per_addon.returncode, 0, msg=per_addon.stderr + per_addon.stdout)
            commands_after = _hook_commands(claude / "settings.json")
            self.assertFalse(any("[addon:remote-demo]" in cmd for cmd in commands_after))
            self.assertTrue(any("tool-checkpoint" in cmd for cmd in commands_after), msg="core hooks should remain")

            status = run("--platform", "claude", "--status")
            self.assertEqual(status.returncode, 0, msg=status.stderr + status.stdout)
            self.assertNotIn("[addon:remote-demo]", status.stdout + status.stderr)

    def test_bad_git_ref_fails_before_installing_remote_addon_targets(self):
        bash = _find_test_bash()
        if not bash:
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

            source = checkout_copy(self)
            install = subprocess.run(
                [
                    bash,
                    str(source / "install.sh"),
                    "--platform", "claude",
                    "--addon-source", remote_url,
                    "--addon-tag", "missing-branch",
                    "--skip-source-health",
                    "task-router",
                ],
                cwd=source,
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
            self.assertFalse((claude / "skills" / "remote-demo").exists())
            self.assertFalse((claude / "settings.json").exists())

    def test_local_addon_source_rejects_addon_tag_without_install_side_effects(self):
        bash = _find_test_bash()
        if not bash:
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
            _write_remote_hook_addon(addon)
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(claude),
                "GHOST_ALICE_INSTALL_PROGRESS": "off",
            })

            source = checkout_copy(self)
            install = subprocess.run(
                [
                    bash,
                    str(source / "install.sh"),
                    "--platform", "claude",
                    "--addon-source", str(addon),
                    "--addon-tag", "remote-hook-fixture",
                    "--skip-source-health",
                    "task-router",
                ],
                cwd=source,
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
            self.assertFalse((claude / "skills" / "remote-demo").exists())
            self.assertFalse((claude / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
