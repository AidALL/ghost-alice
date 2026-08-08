#!/usr/bin/env python3
"""Tests for Ghost-ALICE runtime configuration."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_config


class TestRuntimeConfigDefaults(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)

    def test_default_config_is_dynamic_agent_visibility(self):
        config = runtime_config.load_config(env={}, home=self.home)

        self.assertEqual(config["schema_version"], "ghost-alice-config.v1")
        self.assertEqual(config["agent_visibility"]["profile"], "dynamic")
        self.assertEqual(config["strict_session_log"]["mode"], "always")
        self.assertNotIn("ui_exposure", config)
        self.assertNotIn("enabled", config["agent_visibility"])
        self.assertNotIn("enabled", config["strict_session_log"])

    def test_agent_visibility_profiles_are_canonicalized(self):
        self.assertEqual(runtime_config.canonical_agent_visibility_profile("strict"), "strict")
        self.assertEqual(runtime_config.canonical_agent_visibility_profile("dynamic"), "dynamic")
        self.assertEqual(runtime_config.canonical_agent_visibility_profile("minimal"), "minimal")
        self.assertEqual(runtime_config.canonical_agent_visibility_profile("guided"), "strict")
        self.assertEqual(runtime_config.canonical_agent_visibility_profile("focused"), "strict")
        self.assertEqual(runtime_config.canonical_agent_visibility_profile("quiet"), "strict")
        self.assertEqual(runtime_config.canonical_agent_visibility_profile("quite"), "strict")

    def test_agent_visibility_env_controls_profile(self):
        env = {
            "GHOST_ALICE_HOOK_PROFILE": "minimal",
            "GHOST_ALICE_UI_PROFILE": "minimal",
            "GHOST_ALICE_AGENT_VISIBILITY": "dynamic",
        }

        config = runtime_config.load_config(env=env, home=self.home)

        self.assertEqual(config["agent_visibility"]["profile"], "dynamic")

    def test_legacy_visibility_envs_are_ignored(self):
        config = runtime_config.load_config(
            env={
                "GHOST_ALICE_HOOK_PROFILE": "minimal",
                "GHOST_ALICE_UI_PROFILE": "dynamic",
            },
            home=self.home,
        )

        self.assertEqual(config["agent_visibility"]["profile"], "dynamic")

    def test_disable_like_env_vars_do_not_turn_off_governance_or_strict_log(self):
        config = runtime_config.load_config(
            env={
                "GHOST_ALICE_UI_EXPOSURE": "off",
                "GHOST_ALICE_STRICT_SESSION_LOG": "false",
            },
            home=self.home,
        )

        self.assertEqual(config["agent_visibility"]["profile"], "dynamic")
        self.assertEqual(config["strict_session_log"]["mode"], "always")
        self.assertNotIn("ui_exposure", config)
        self.assertNotIn("enabled", config["agent_visibility"])
        self.assertNotIn("enabled", config["strict_session_log"])

    def test_save_config_uses_runtime_config_path_not_install_state(self):
        path = runtime_config.save_config(
            {
                "agent_visibility": {"enabled": False, "profile": "minimal"},
                "ui_exposure": {"enabled": False, "profile": "dynamic"},
                "strict_session_log": {"enabled": False},
            },
            home=self.home,
        )

        self.assertEqual(path, self.home / ".ghost-alice" / "config.json")
        self.assertNotIn("install-state", str(path))

        row = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(row["agent_visibility"]["profile"], "minimal")
        self.assertNotIn("ui_exposure", row)
        self.assertNotIn("enabled", row["agent_visibility"])
        self.assertEqual(row["strict_session_log"]["mode"], "always")
        self.assertNotIn("enabled", row["strict_session_log"])

        loaded = runtime_config.load_config(env={}, home=self.home)
        self.assertEqual(loaded["agent_visibility"]["profile"], "minimal")
        self.assertEqual(loaded["strict_session_log"]["mode"], "always")

    def test_agent_visibility_cli_sets_profile_in_runtime_config(self):
        import agent_visibility_cli

        result = agent_visibility_cli.main(["set", "dynamic", "--home", str(self.home)])

        self.assertEqual(result, 0)
        config = runtime_config.load_config(env={}, home=self.home)
        self.assertEqual(config["agent_visibility"]["profile"], "dynamic")

    def test_agent_visibility_cli_shows_current_profile(self):
        import agent_visibility_cli

        runtime_config.save_config({"agent_visibility": {"profile": "minimal"}}, home=self.home)

        with self.assertLogs("agent_visibility_cli", level="INFO") as cm:
            result = agent_visibility_cli.main(["show", "--home", str(self.home)])

        self.assertEqual(result, 0)
        self.assertIn("profile=minimal", "\n".join(cm.output))


class TestRuntimeConfigNodeRegistrations(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)
        self.node_registrations = {
            "claude": "/usr/local/bin/node",
            "codex": r"C:\Program Files\nodejs\node.exe",
        }

    def test_default_hook_runtime_node_is_empty(self):
        config = runtime_config.load_config(env={}, home=self.home)

        self.assertEqual(config["hook_runtime"]["node"], {})

    def test_two_platform_node_registrations_round_trip(self):
        runtime_config.save_config(
            {"hook_runtime": {"node": self.node_registrations}},
            home=self.home,
        )

        config = runtime_config.load_config(env={}, home=self.home)

        self.assertEqual(config["hook_runtime"]["node"], self.node_registrations)

    def test_visibility_only_save_preserves_node_registrations(self):
        runtime_config.save_config(
            {"hook_runtime": {"node": self.node_registrations}},
            home=self.home,
        )

        runtime_config.save_config(
            {"agent_visibility": {"profile": "minimal"}},
            home=self.home,
        )

        config = runtime_config.load_config(env={}, home=self.home)
        self.assertEqual(config["agent_visibility"]["profile"], "minimal")
        self.assertEqual(config["hook_runtime"]["node"], self.node_registrations)

    def test_updating_one_platform_preserves_other_registration(self):
        runtime_config.save_config(
            {"hook_runtime": {"node": self.node_registrations}},
            home=self.home,
        )

        runtime_config.save_config(
            {"hook_runtime": {"node": {"codex": r"D:\nodejs\node.exe"}}},
            home=self.home,
        )

        config = runtime_config.load_config(env={}, home=self.home)
        self.assertEqual(
            config["hook_runtime"]["node"],
            {
                "claude": "/usr/local/bin/node",
                "codex": r"D:\nodejs\node.exe",
            },
        )

    def test_malformed_node_registrations_do_not_survive_normalization(self):
        path = runtime_config.config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "hook_runtime": {
                        "node": {
                            "codex": r"C:\Program Files\nodejs\node.exe",
                            "": "/empty/platform",
                            "   ": "/blank/platform",
                            "claude": "",
                            "gemini": "   ",
                            "cursor": None,
                            "windsurf": 17,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        runtime_config.save_config(
            {"agent_visibility": {"profile": "minimal"}},
            home=self.home,
        )

        config = runtime_config.load_config(env={}, home=self.home)
        self.assertEqual(
            config["hook_runtime"]["node"],
            {"codex": r"C:\Program Files\nodejs\node.exe"},
        )


if __name__ == "__main__":
    unittest.main()
