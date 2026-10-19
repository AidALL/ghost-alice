"""installer.locations and installer.config: host paths, platform detection and the visibility default.

Expected values come from installer_lib/platform.sh and installer_lib/report.sh:13-21.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import config  # noqa: E402
from installer import locations  # noqa: E402


class LocationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_RUNTIME_SHARED_DIR", "GHOST_ALICE_GHOST_ROOT",
                    "GHOST_ALICE_ADDON_SOURCE_CACHE_DIR"):
            os.environ.pop(key, None)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()


class PathTests(LocationCase):
    def test_homes_and_overrides(self):
        # platform.sh:23-29
        self.assertEqual(locations.home(), self.home)
        self.assertEqual(locations.claude_home(), self.home / ".claude")
        self.assertEqual(locations.codex_home(), self.home / ".codex")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home / "cc"), "CODEX_HOME": str(self.home / "cx")}):
            self.assertEqual(locations.claude_home(), self.home / "cc")
            self.assertEqual(locations.codex_home(), self.home / "cx")
            self.assertEqual(locations.skills_dir("claude"), self.home / "cc" / "skills")
            self.assertEqual(locations.claude_commands_dir(), self.home / "cc" / "commands")

    def test_skill_and_runtime_dirs(self):
        # platform.sh:31-41; install.sh:74, 455-457
        self.assertEqual(locations.shared_skills_dir(), self.home / ".agents" / "skills")
        self.assertEqual(locations.skills_dir("codex"), self.home / ".agents" / "skills")
        self.assertEqual(locations.skills_dir("claude"), self.home / ".claude" / "skills")
        self.assertEqual(locations.runtime_shared_dir(), self.home / ".ghost-alice" / "runtime" / "current" / "_shared")
        with mock.patch.dict(os.environ, {"GHOST_ALICE_RUNTIME_SHARED_DIR": str(self.home / "rt")}):
            self.assertEqual(locations.runtime_shared_dir(), self.home / "rt")
        with self.assertRaises(ValueError):
            locations.skills_dir("windows")

    def test_ghost_root_and_state_paths(self):
        # targets.sh:373; install.sh:36, 88-89, 279, 629-632; report.sh:84
        ghost = self.home / ".ghost-alice"
        self.assertEqual(locations.ghost_root(), ghost)
        self.assertEqual(locations.install_state_path("codex"), ghost / "install-state" / "codex.json")
        self.assertEqual(locations.events_log("claude"), ghost / "install-state" / "claude-events.jsonl")
        self.assertEqual(locations.record_root(), ghost)
        # A publish keeps what it replaced in rollbacks/<txid>/ under the record root, not in install-rollbacks.
        self.assertFalse(hasattr(locations, "rollback_root"))
        self.assertEqual(locations.pending_root("claude"), ghost / "pending-merges" / "claude")
        self.assertEqual(locations.addons_dir("codex"), ghost / "addons" / "codex")
        self.assertEqual(locations.resources_dir("codex"), ghost / "resources" / "codex")
        self.assertEqual(locations.install_log_root(), ghost / "install")
        self.assertEqual(locations.install_lock_path(), ghost / "install.lock")
        self.assertEqual(locations.addon_source_cache_root(), ghost / "addon-source-cache")
        self.assertEqual(locations.deprecated_skill_backup_root(), ghost / "deprecated-skill-backups")
        with mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(self.home / "g"),
                                          "GHOST_ALICE_ADDON_SOURCE_CACHE_DIR": str(self.home / "cache")}):
            self.assertEqual(locations.ghost_root(), self.home / "g")
            self.assertEqual(locations.addon_source_cache_root(), self.home / "cache")
            # The install lock, the install record and the publish rollbacks stay together under HOME.
            self.assertEqual(locations.record_root(), ghost)
            self.assertEqual(locations.install_lock_path(), ghost / "install.lock")

    def test_copy_preference(self):
        # platform.sh:43-57
        self.assertTrue(locations.prefers_copy_install("codex"))
        self.assertFalse(locations.prefers_copy_install("claude"))


class DetectionTests(LocationCase):
    def test_present_platforms_follow_home_directories(self):
        # platform.sh:63-66
        self.assertEqual(locations.detect_present_platforms(), [])
        (self.home / ".codex").mkdir()
        self.assertEqual(locations.detect_present_platforms(), ["codex"])
        (self.home / ".claude").mkdir()
        self.assertEqual(locations.detect_present_platforms(), ["claude", "codex"])

    def test_uninstall_platforms_add_install_state_files_once(self):
        # platform.sh:68-80
        self.assertEqual(locations.detect_uninstall_platforms(), [])
        state = self.home / ".ghost-alice" / "install-state"
        state.mkdir(parents=True)
        (state / "codex.json").write_text("{}", encoding="utf-8")
        self.assertEqual(locations.detect_uninstall_platforms(), ["codex"])
        (self.home / ".codex").mkdir()
        self.assertEqual(locations.detect_uninstall_platforms(), ["codex"])
        (self.home / ".claude").mkdir()
        self.assertEqual(locations.detect_uninstall_platforms(), ["claude", "codex"])
        (state / "claude.json").write_text("{}", encoding="utf-8")
        self.assertEqual(locations.detect_uninstall_platforms(), ["claude", "codex"])


class VisibilityTests(LocationCase):
    def test_flag_wins_and_the_default_is_dynamic(self):
        # report.sh:13-21
        self.assertEqual(config.resolve_effective_visibility(None), "dynamic")
        self.assertEqual(config.resolve_effective_visibility(""), "dynamic")
        self.assertEqual(config.resolve_effective_visibility("strict"), "strict")
        self.assertEqual(config.resolve_effective_visibility("minimal"), "minimal")

    def test_existing_runtime_config_is_not_a_default(self):
        # report.sh:14-16: a plain install repairs stale profiles; test_installer_default_auto.py:957-989
        cfg = self.home / ".ghost-alice" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"agent_visibility": {"profile": "minimal"}}), encoding="utf-8")
        self.assertEqual(config.resolve_effective_visibility(None), "dynamic")
        self.assertEqual(config.resolve_effective_visibility("strict"), "strict")


if __name__ == "__main__":
    unittest.main()
