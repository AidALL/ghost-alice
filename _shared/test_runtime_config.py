#!/usr/bin/env python3
"""Tests for Ghost-ALICE runtime configuration."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_config


from test_atomic_write import _FailAfterPartialWrite  # noqa: E402


class TestRuntimeConfigDefaults(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)

    def _symlink_file_or_skip(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"file symlinks unavailable: {error}")

    def test_default_config_is_dynamic_agent_visibility(self):
        config = runtime_config.load_config(env={}, home=self.home)

        self.assertEqual(config["schema_version"], "ghost-alice-config.v1")
        self.assertEqual(config["agent_visibility"]["profile"], "dynamic")
        self.assertEqual(config["strict_session_log"]["mode"], "always")
        self.assertNotIn("ui_exposure", config)
        self.assertNotIn("enabled", config["agent_visibility"])
        self.assertNotIn("enabled", config["strict_session_log"])

    def test_invalid_utf8_config_returns_canonical_defaults(self):
        path = runtime_config.config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            b'{"agent_visibility":{"profile":"strict"},'
            b'"hook_runtime":{"node":{"claude":"stale"}}}\xff'
        )

        config = runtime_config.load_config(env={}, home=self.home)

        self.assertEqual(config, runtime_config.DEFAULT_CONFIG)
        self.assertEqual(config["hook_runtime"]["node"], {})

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

    def test_partial_write_failure_preserves_existing_bytes_and_cleans_temp(self):
        path = runtime_config.config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        original_bytes = b'{  "agent_visibility": {"profile": "minimal"}  }\n'
        path.write_bytes(original_bytes)
        write_error = OSError("partial config write failed")
        original_fdopen = os.fdopen

        def failing_write_text(target, data, *args, **kwargs):
            encoded = data.encode(kwargs.get("encoding") or "utf-8")
            target.write_bytes(encoded[: max(1, len(encoded) // 2)])
            raise write_error

        def failing_fdopen(fd, *args, **kwargs):
            return _FailAfterPartialWrite(
                original_fdopen(fd, *args, **kwargs),
                write_error,
            )

        with (
            mock.patch.object(Path, "write_text", new=failing_write_text),
            mock.patch.object(runtime_config.os, "fdopen", side_effect=failing_fdopen),
            self.assertRaises(OSError) as raised,
        ):
            runtime_config.save_config(
                {"agent_visibility": {"profile": "strict"}},
                home=self.home,
            )

        self.assertIs(raised.exception, write_error)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_save_config_preserves_existing_permissions(self):
        path = runtime_config.config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        original_mode = stat.S_IMODE(path.stat().st_mode)

        runtime_config.save_config(
            {"agent_visibility": {"profile": "minimal"}},
            home=self.home,
        )

        self.assertEqual(stat.S_IMODE(path.stat().st_mode), original_mode)

    def test_save_config_preserves_existing_symlink_and_target_permissions(self):
        path = runtime_config.config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = self.home / "runtime-target" / "config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            '{"agent_visibility":{"profile":"dynamic"}}\n',
            encoding="utf-8",
        )
        original_mode = stat.S_IMODE(target.stat().st_mode)
        self._symlink_file_or_skip(path, target)
        original_link = os.readlink(path)

        runtime_config.save_config(
            {"agent_visibility": {"profile": "minimal"}},
            home=self.home,
        )

        self.assertTrue(path.is_symlink())
        self.assertEqual(os.readlink(path), original_link)
        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8"))["agent_visibility"]["profile"],
            "minimal",
        )
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), original_mode)

    def test_symlink_write_failure_preserves_link_target_and_uses_target_directory(self):
        path = runtime_config.config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = self.home / "runtime-target" / "config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        original_bytes = b'{  "agent_visibility": {"profile": "minimal"}  }\n'
        target.write_bytes(original_bytes)
        self._symlink_file_or_skip(path, target)
        original_link = os.readlink(path)
        write_error = OSError("partial symlink target write failed")
        original_fdopen = os.fdopen

        def failing_fdopen(fd, *args, **kwargs):
            return _FailAfterPartialWrite(
                original_fdopen(fd, *args, **kwargs),
                write_error,
            )

        with (
            mock.patch.object(runtime_config.os, "fdopen", side_effect=failing_fdopen),
            mock.patch.object(
                tempfile,
                "mkstemp",
                wraps=tempfile.mkstemp,
            ) as mkstemp,
            self.assertRaises(OSError) as raised,
        ):
            runtime_config.save_config(
                {"agent_visibility": {"profile": "strict"}},
                home=self.home,
            )

        self.assertIs(raised.exception, write_error)
        self.assertTrue(path.is_symlink())
        self.assertEqual(os.readlink(path), original_link)
        self.assertEqual(target.read_bytes(), original_bytes)
        self.assertEqual(Path(mkstemp.call_args.kwargs["dir"]).resolve(), target.parent.resolve())
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_broken_symlink_creates_target_only_when_target_parent_exists(self):
        path = runtime_config.config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = self.home / "runtime-target" / "config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        self._symlink_file_or_skip(path, target)
        original_link = os.readlink(path)

        runtime_config.save_config(
            {"agent_visibility": {"profile": "minimal"}},
            home=self.home,
        )

        self.assertTrue(path.is_symlink())
        self.assertEqual(os.readlink(path), original_link)
        self.assertTrue(target.is_file())

        target.unlink()
        target.parent.rmdir()
        with self.assertRaises(FileNotFoundError):
            runtime_config.save_config(
                {"agent_visibility": {"profile": "strict"}},
                home=self.home,
            )
        self.assertTrue(path.is_symlink())
        self.assertEqual(os.readlink(path), original_link)
        self.assertFalse(target.exists())

    def test_temp_cleanup_failure_is_noted_without_replacing_primary_error(self):
        path = runtime_config.config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        write_error = OSError("primary write failed")
        cleanup_error = OSError("cleanup failed")
        original_fdopen = os.fdopen
        original_unlink = Path.unlink
        cleanup_attempts: list[Path] = []

        def failing_fdopen(fd, *args, **kwargs):
            return _FailAfterPartialWrite(
                original_fdopen(fd, *args, **kwargs),
                write_error,
            )

        def failing_temp_unlink(candidate: Path, *args, **kwargs):
            if candidate.parent == path.parent and candidate.name.endswith(".tmp"):
                cleanup_attempts.append(candidate)
                raise cleanup_error
            return original_unlink(candidate, *args, **kwargs)

        with (
            mock.patch.object(runtime_config.os, "fdopen", side_effect=failing_fdopen),
            mock.patch.object(Path, "unlink", new=failing_temp_unlink),
            self.assertRaises(OSError) as raised,
        ):
            runtime_config.save_config(
                {"agent_visibility": {"profile": "strict"}},
                home=self.home,
            )

        self.assertIs(raised.exception, write_error)
        self.assertEqual(len(cleanup_attempts), 1)
        self.addCleanup(original_unlink, cleanup_attempts[0], missing_ok=True)
        notes = getattr(raised.exception, "__notes__", [])
        self.assertTrue(
            any(
                str(cleanup_attempts[0]) in note and "cleanup failed" in note
                for note in notes
            ),
            notes,
        )


class TestNodeRuntimeUsability(unittest.TestCase):
    def test_shared_predicate_requires_posix_execute_access_but_not_windows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "node"
            runtime.write_text("# fake node runtime\n", encoding="utf-8")

            predicate = getattr(runtime_config, "is_usable_node_runtime", None)
            self.assertIsNotNone(predicate, "shared Node usability predicate is missing")

            posix_os = mock.Mock()
            posix_os.name = "posix"
            posix_os.X_OK = os.X_OK
            posix_os.access.return_value = False
            with mock.patch.object(runtime_config, "os", posix_os):
                self.assertFalse(predicate(runtime))
            posix_os.access.assert_called_once_with(runtime, os.X_OK)

            windows_os = mock.Mock()
            windows_os.name = "nt"
            windows_os.X_OK = os.X_OK
            windows_os.access.return_value = False
            with mock.patch.object(runtime_config, "os", windows_os):
                self.assertTrue(predicate(runtime))
            windows_os.access.assert_not_called()


class TestNodeRuntimeCandidates(unittest.TestCase):
    def test_codex_config_location_preserves_explicit_precedence_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locations = {name: root / name for name in ("platform", "override", "codex-home", ".codex")}
            for location in locations.values():
                location.mkdir()
                node = location / "node"
                node.write_text("node fixture", encoding="utf-8")
                node.chmod(node.stat().st_mode | 0o100)
                (location / "config.toml").write_text(
                    "[mcp_servers.node_repl.env]\n"
                    f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n", encoding="utf-8")
            env = {"HOME": directory, "USERPROFILE": directory, "PATH": "",
                   "GHOST_ALICE_PLATFORM": "codex", "GHOST_ALICE_PLATFORM_DIR": str(locations["platform"]),
                   "GHOST_ALICE_CODEX_DIR": str(locations["override"]), "CODEX_HOME": str(locations["codex-home"])}
            with (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch("shutil.which", return_value=None)):
                for selected, removed in (("platform", None), ("override", "GHOST_ALICE_PLATFORM_DIR"),
                                          ("codex-home", "GHOST_ALICE_CODEX_DIR"), (".codex", "CODEX_HOME")):
                    if removed:
                        env.pop(removed)
                    with self.subTest(selected=selected):
                        self.assertEqual(runtime_config.node_runtime_candidates(env, "codex"),
                                         [(locations[selected] / "node").resolve()])
                env["GHOST_ALICE_PLATFORM_DIR"] = str(root / "missing")
                self.assertEqual(runtime_config.node_runtime_candidates(env, "codex"), [])

    def test_candidates_preserve_platform_order_and_only_first_path_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nodes = {}
            for name in ("override", "repl", "codex", "registered", "path-first", "path-second"):
                node = root / name / ("node.exe" if name == "path-second" else "node")
                node.parent.mkdir()
                node.write_text("node fixture", encoding="utf-8")
                node.chmod(node.stat().st_mode | 0o100)
                nodes[name] = node.resolve()
            codex_home = root / ".codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(nodes['codex'].as_posix())}\n", encoding="utf-8")
            env = {"HOME": directory, "USERPROFILE": directory, "CODEX_HOME": str(codex_home),
                   "PATH": "fixture-path", "GHOST_ALICE_NODE": str(nodes["override"]),
                   "NODE_REPL_NODE_PATH": str(nodes["repl"])}
            runtimes = {"node": str(nodes["path-first"]), "node.exe": str(nodes["path-second"])}
            expected = {
                "codex": ("override", "repl", "codex", "registered", "path-first"),
                "claude": ("override", "repl", "registered", "path-first", "codex"),
                "unknown-host": ("override", "repl", "path-first", "codex"),
                None: ("override", "repl", "path-first", "codex"),
            }
            with (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch("shutil.which", side_effect=lambda name, path=None: runtimes.get(name))):
                runtime_config.save_config({"hook_runtime": {"node": {
                    "claude": str(nodes["registered"]), "codex": str(nodes["registered"]),
                    "unknown-host": str(nodes["registered"])}}}, home=root)
                for platform, order in expected.items():
                    with self.subTest(platform=platform):
                        self.assertEqual(runtime_config.node_runtime_candidates(env, platform),
                                         [nodes[name] for name in order])


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

    def test_node_only_save_does_not_persist_visibility_env_override(self):
        runtime_config.save_config(
            {"agent_visibility": {"profile": "minimal"}},
            home=self.home,
        )

        with mock.patch.dict(
            os.environ,
            {"GHOST_ALICE_AGENT_VISIBILITY": "strict"},
        ):
            path = runtime_config.save_config(
                {"hook_runtime": {"node": {"codex": self.node_registrations["codex"]}}},
                home=self.home,
            )

        stored = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(stored["agent_visibility"]["profile"], "minimal")
        self.assertEqual(
            stored["hook_runtime"]["node"],
            {"codex": self.node_registrations["codex"]},
        )

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

    def test_node_registrations_keep_only_non_empty_string_keys_and_paths(self):
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
            {
                "agent_visibility": {"profile": "minimal"},
                "hook_runtime": {"node": {17: "/numeric/platform"}},
            },
            home=self.home,
        )

        config = runtime_config.load_config(env={}, home=self.home)

        self.assertEqual(
            config["hook_runtime"]["node"],
            {"codex": r"C:\Program Files\nodejs\node.exe"},
        )


if __name__ == "__main__":
    unittest.main()
