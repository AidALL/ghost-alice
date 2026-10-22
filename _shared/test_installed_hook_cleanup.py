"""A later install removes old Ghost-ALICE registrations and their leftovers.

A registration with a Ghost-ALICE marker, a retired runner id or the managed
runner form belongs to Ghost-ALICE. Hooks of other tools stay unchanged.
"""
import importlib.util
import json
from pathlib import Path
import re
import sys
import tomllib
import unittest
from unittest.mock import patch

import hook_definitions
import install_doctor
import install_hooks
import retired_assets
from installer import hosts, report
from test_install_hooks import TempHomeTestCase


def _commands(settings):
    return [hook.get("command", "") for groups in settings.get("hooks", {}).values()
            for group in groups for hook in group.get("hooks", [])]


def _without_hook_state(text):
    """Return config text without hooks.state tables, keeping every other line."""
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped.startswith("[hooks.state.")
        if not skipping:
            kept.append(line)
    return "".join(kept)


class InstalledHookCleanupTests(TempHomeTestCase):
    def setUp(self):
        super().setUp()
        supported = patch.object(install_hooks, "_codex_hooks_supported", return_value=True)
        supported.start()
        self.addCleanup(supported.stop)

    def retired_forms(self, platform):
        """Every form an older installer wrote for each retired hook."""
        forms = []
        for hook_id, marker in install_hooks.RETIRED_HOOKS:
            forms += [
                install_hooks._hook_runner_command_entry(
                    hook_id, f"python3 /old/_shared/retired_hook.py --internal-b64 eA== # {marker}",
                    marker, platform_key=platform)["hooks"][0],
                {"type": "command", "command":
                    f"python3 '/old/_shared/retired_hook.py' --platform {platform} --internal-b64 eA== # {marker}"},
                {"type": "command", "command": f"echo 'Internal instruction: route the task.' # {marker}"},
                {"type": "command", "command":
                    f"python /foreign/hook_profile_gate.py run {hook_id} {platform} eA== # {marker}"},
            ]
        return forms

    def test_reinstall_removes_every_installed_form_of_retired_hooks(self):
        user = {"type": "command", "command": "echo user-owned"}
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                self._create_platform_dir(platform)
                self.assertEqual(install_hooks.install_hook(platform), "installed")
                settings = self._read_settings(platform)
                forms = self.retired_forms(platform)
                settings["hooks"]["UserPromptSubmit"].append(
                    {"matcher": "preserved", "custom": 7, "hooks": [*forms, user]})
                settings["hooks"]["SessionStart"].append({"hooks": forms[:1]})
                self._write_settings(platform, settings)

                self.assertEqual(install_hooks.install_hook(platform), "installed")
                after = self._read_settings(platform)
                for command in _commands(after):
                    for hook_id, marker in install_hooks.RETIRED_HOOKS:
                        self.assertNotIn(marker, command)
                        self.assertFalse(install_hooks._hook_runner_id_match(command, hook_id), command)
                self.assertIn({"matcher": "preserved", "custom": 7, "hooks": [user]},
                              after["hooks"]["UserPromptSubmit"])
                self.assertTrue(all(group.get("hooks") for groups in after["hooks"].values() for group in groups))
                self.assertEqual(install_hooks.install_hook(platform), "already")

    def test_reinstall_removes_hook_groups_without_hooks_on_both_hosts(self):
        user_stop = {"type": "command", "command": "echo user-stop"}
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                self._write_settings(platform, {"hooks": {
                    "PreToolUse": [{"matcher": "Bash", "hooks": []}],
                    "Stop": [{"hooks": []}, {"matcher": "", "hooks": [user_stop]}, {"matcher": ""}],
                }})
                self.assertEqual(install_hooks.install_hook(platform), "installed")
                hooks = self._read_settings(platform)["hooks"]
                self.assertEqual([(event, group) for event, groups in hooks.items()
                                  for group in groups if not group.get("hooks")], [])
                self.assertIn({"matcher": "", "hooks": [user_stop]}, hooks["Stop"])

    def test_codex_reinstall_moves_trust_with_handlers_and_drops_orphan_tables(self):
        self._create_platform_dir("codex")
        self.assertEqual(install_hooks.install_hook("codex"), "installed")
        hooks_file = self._config_file("codex")
        settings = self._read_settings("codex")
        intake_groups = settings["hooks"]["UserPromptSubmit"]
        user = {"type": "command", "command": "echo user-prompt"}
        retired = install_hooks._hook_runner_command_entry(
            "prompt", "python3 /old/_shared/task_router_reminder_hook.py # " + install_hooks.HOOK_MARKER,
            install_hooks.HOOK_MARKER, platform_key="codex")
        settings["hooks"]["UserPromptSubmit"] = [retired, {"hooks": [user]}, *intake_groups]
        self._write_settings("codex", settings)

        def key(event, group, handler=0):
            return install_hooks._codex_hook_state_key(hooks_file, event, group, handler)

        def table(state_key, *lines):
            return "\n".join([f"[hooks.state.{json.dumps(state_key)}]", *lines]) + "\n"

        user_hash = install_hooks._codex_command_hook_hash("UserPromptSubmit", {"hooks": [user]}, user)
        intake_hash = install_hooks._codex_command_hook_hash(
            "UserPromptSubmit", intake_groups[0], intake_groups[0]["hooks"][0])
        config = self._codex_config_toml()
        config.write_text(_without_hook_state(config.read_text()) + "\n" + "".join([
            table(key("UserPromptSubmit", 0), 'trusted_hash = "retired-reminder"', "enabled = true"),
            table(key("UserPromptSubmit", 1), f'trusted_hash = "{user_hash}"', "enabled = false"),
            table(key("UserPromptSubmit", 2), f'trusted_hash = "{intake_hash}"', "enabled = true"),
            table(key("Stop", 7), 'trusted_hash = "orphan"', "enabled = true"),
            table("/elsewhere/hooks.json:stop:0:0", 'trusted_hash = "other-file"'),
        ]))

        self.assertEqual(install_hooks.install_hook("codex"), "installed")
        after = self._read_settings("codex")
        self.assertEqual(after["hooks"]["UserPromptSubmit"], [{"hooks": [user]}, *intake_groups])
        state = tomllib.loads(config.read_text())["hooks"]["state"]
        self.assertEqual(state[key("UserPromptSubmit", 0)], {"trusted_hash": user_hash, "enabled": False})
        self.assertEqual(state[key("UserPromptSubmit", 1)], {"trusted_hash": intake_hash, "enabled": True})
        self.assertNotIn(key("Stop", 7), state)
        self.assertEqual(state["/elsewhere/hooks.json:stop:0:0"], {"trusted_hash": "other-file"})
        positions = {key(event, g, h) for event, groups in after["hooks"].items()
                     for g, group in enumerate(groups) for h, _ in enumerate(group["hooks"])}
        self.assertLessEqual({k for k in state if k.startswith(hooks_file.as_posix() + ":")}, positions)

    def test_codex_uninstall_drops_ghost_alice_trust_and_moves_user_trust(self):
        self._create_platform_dir("codex")
        self.assertEqual(install_hooks.install_hook("codex"), "installed")
        hooks_file = self._config_file("codex")
        settings = self._read_settings("codex")
        user = {"type": "command", "command": "echo user-prompt"}
        settings["hooks"]["UserPromptSubmit"].append({"hooks": [user]})
        settings["hooks"]["SessionStart"].append({"hooks": self.retired_forms("codex")[:1]})
        self._write_settings("codex", settings)
        user_key = install_hooks._codex_hook_state_key(
            hooks_file, "UserPromptSubmit", len(settings["hooks"]["UserPromptSubmit"]) - 1, 0)
        user_hash = install_hooks._codex_command_hook_hash("UserPromptSubmit", {"hooks": [user]}, user)
        config = self._codex_config_toml()
        config.write_text(config.read_text() + f"\n[hooks.state.{json.dumps(user_key)}]\ntrusted_hash = \"{user_hash}\"\n")

        self.assertEqual(install_hooks.uninstall_hook("codex"), "removed")
        self.assertEqual(_commands(self._read_settings("codex")), ["echo user-prompt"])
        state = tomllib.loads(config.read_text()).get("hooks", {}).get("state", {})
        self.assertEqual(state, {install_hooks._codex_hook_state_key(hooks_file, "UserPromptSubmit", 0, 0):
                                 {"trusted_hash": user_hash}})

    def test_codex_trusts_no_retired_marker_command(self):
        for _, marker in install_hooks.RETIRED_HOOKS:
            with self.subTest(marker=marker):
                settings = {"hooks": {"UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": f"echo old # {marker}"}]}]}}
                self.assertEqual(install_hooks._codex_trusted_hook_state_entries(Path("/x/hooks.json"), settings), {})

    def test_claude_reinstall_drops_retired_skill_permissions(self):
        skills = self.fake_home / ".claude" / "skills"
        for name in retired_assets.RETIRED_SKILLS:
            (skills / name).mkdir(parents=True)
        retired = [f"Skill({name})" for name in retired_assets.RETIRED_SKILLS]
        self._write_settings("claude", {"permissions": {"allow": [*retired, "Skill(user-own)", "Bash(ls:*)"]}})
        self.assertEqual(install_hooks.install_hook("claude"), "installed")
        allow = self._read_settings("claude")["permissions"]["allow"]
        for rule in retired:
            self.assertNotIn(rule, allow)
        self.assertIn("Skill(user-own)", allow)
        self.assertIn("Bash(ls:*)", allow)

    def test_doctor_counts_no_retired_marker_command_as_managed(self):
        shared = self._create_runtime_shared_dir()
        for _, marker in install_hooks.RETIRED_HOOKS:
            with self.subTest(marker=marker):
                self.assertEqual(install_doctor._runtime_hook_config_audit([f"echo old # {marker}"], shared),
                                 [{"status": install_doctor.STATUS_ERROR, "name": "managed-hooks",
                                   "kind": "hook-config", "reason": "none-found"}])

    def test_security_scan_marker_copy_matches_the_current_markers(self):
        # The scanner skill runs without _shared, so it keeps a copy; the copy must not drift.
        path = Path(install_hooks.__file__).parents[1] / "agent-security-scan/scripts/scan_agent_security_surface.py"
        spec = importlib.util.spec_from_file_location("scan_agent_security_surface_under_test", path)
        scanner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = scanner
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(scanner)
        self.assertEqual(set(scanner.GHOST_ALICE_HOOK_MARKERS), set(install_hooks.GHOST_ALICE_HOOK_MARKERS))
        for _, marker in install_hooks.RETIRED_HOOKS:
            self.assertNotIn(marker, install_hooks.GHOST_ALICE_HOOK_MARKERS)

    def test_install_report_lists_the_installed_core_hooks(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                self._create_platform_dir(platform)
                self.assertEqual(install_hooks.install_hook(platform), "installed")
                runners = set(re.findall(r"\[hook-runner:([a-z0-9-]+)\]",
                                         " ".join(_commands(self._read_settings(platform)))))
                self.assertEqual(runners, set(hook_definitions.CORE_HOOK_IDS))
        self.assertEqual(report.hook_suite_label(), ", ".join(hook_definitions.CORE_HOOK_IDS))
        self.assertEqual(hosts.HOOK_ACTION_LABEL, " + ".join(hook_definitions.CORE_HOOK_IDS))


if __name__ == "__main__":
    unittest.main()
