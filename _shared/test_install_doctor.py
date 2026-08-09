#!/usr/bin/env python3
"""Tests for the read-only installer doctor node-runtime check.

The tool-checkpoint PreToolUse gate is dispatched through `node ghost-alice-hook.mjs`.
The installer blocks hook install when node is absent, but node can be removed from
PATH after install. Doctor is the read-only diagnostic that must surface that drift,
because Claude Code treats a non-2 PreToolUse exit (a missing-node crash) as
non-blocking, so the gate would silently fail open.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_doctor
import addon_registry as reg
import hash_utils
import runtime_config


def _make_node_executable(node: Path) -> None:
    if install_doctor.os.name != "nt":
        node.chmod(node.stat().st_mode | 0o100)


def _sidecar(addon_id, target, *, install_mode="copy", content_hash):
    return {
        "schema_version": "1.0", "addon_id": addon_id, "addon_version": "1.0.0",
        "source": f"/s/{addon_id}", "platform": "claude", "owner": "addon",
        "origin": f"addon:{addon_id}", "depends_on_core": [], "min_core_version": "0.0.0",
        "installed_at": "t", "provided": [{
            "kind": "skill", "name": addon_id, "target": str(target), "ownership": "addon",
            "install_mode": install_mode, "content_hash": content_hash, "marker": "", "metadata": {},
        }],
    }


class AddonRegistryAuditTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.gar = Path(self._tmp.name) / ".ghost-alice"
        self.addons = self.gar / "addons" / "claude"
        self.skills = Path(self._tmp.name) / ".claude" / "skills"
        self.skills.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _install(self, addon_id, body="body\n"):
        dest = self.skills / addon_id
        dest.mkdir()
        (dest / "SKILL.md").write_text(body, encoding="utf-8")
        reg.write_record(_sidecar(addon_id, dest, content_hash=hash_utils.hash_target(str(dest), "copy")),
                         addons_dir=self.addons)
        return dest

    def test_intact_addon_target_is_ok(self):
        self._install("noop")
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertEqual([(f["addon_id"], f["status"]) for f in findings], [("noop", "ok")])

    def test_content_hash_tamper_is_error(self):
        dest = self._install("noop")
        (dest / "SKILL.md").write_text("TAMPERED\n", encoding="utf-8")  # bit-rot / edit after install
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(status, install_doctor.STATUS_ERROR)
        self.assertEqual(findings[0]["status"], install_doctor.STATUS_ERROR)
        self.assertIn("hash", findings[0]["reason"])

    def test_missing_target_is_warning(self):
        dest = self._install("noop")
        import shutil
        shutil.rmtree(dest)  # user deleted the installed skill
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(findings[0]["status"], install_doctor.STATUS_WARNING)
        self.assertEqual(status, install_doctor.STATUS_WARNING)

    def test_unreadable_sidecar_is_error(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        (self.addons / "corrupt.json").write_text("{ not json", encoding="utf-8")
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(status, install_doctor.STATUS_ERROR)
        self.assertTrue(any("sidecar" in f["reason"] for f in findings))

    def test_no_addons_dir_is_ok_empty(self):
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertEqual(findings, [])

    def test_platform_scoped_isolation(self):
        # a codex sidecar must not be audited under the claude platform
        self._install("noop")
        codex_dir = self.gar / "addons" / "codex"
        reg.write_record(_sidecar("other", self.skills / "ghost", content_hash="x"),
                         addons_dir=codex_dir)
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual([f["addon_id"] for f in findings], ["noop"])  # codex 'other' excluded


class LiveDirOwnershipTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addons = root / ".ghost-alice" / "addons" / "claude"
        self.skills = root / ".claude" / "skills"
        self.skills.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _skill(self, name, body="x\n"):
        dest = self.skills / name
        dest.mkdir()
        (dest / "SKILL.md").write_text(body, encoding="utf-8")
        return dest

    def test_classifies_core_addon_domain_user(self):
        self._skill("task-router")                 # core (name in core set)
        addon_dest = self._skill("noop")           # addon (sidecar-owned)
        reg.write_record(_sidecar("noop", addon_dest, content_hash="h"), addons_dir=self.addons)
        self._skill("my-domain-skill")             # domain (SKILL.md, unmanaged)
        (self.skills / "random-dir").mkdir()       # user (no SKILL.md)
        (self.skills / "_shared").mkdir()          # support dir -> excluded

        findings = install_doctor._live_dir_ownership(self.skills, {"task-router"}, self.addons)
        owner = {f["name"]: f["owner"] for f in findings}
        self.assertEqual(owner["task-router"], "core")
        self.assertEqual(owner["noop"], "addon")
        self.assertEqual(owner["my-domain-skill"], "domain")
        self.assertEqual(owner["random-dir"], "user")
        self.assertNotIn("_shared", owner)

    def test_absent_skills_dir_is_empty(self):
        missing = self.skills / "nope"
        self.assertEqual(install_doctor._live_dir_ownership(missing, set(), self.addons), [])


class NodeRuntimeStatusTest(unittest.TestCase):
    def test_node_runtime_normalization_uses_shared_usability_predicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp) / "node"
            node.write_text("# fake node\n", encoding="utf-8")

            with mock.patch.object(
                runtime_config,
                "is_usable_node_runtime",
                return_value=False,
                create=True,
            ) as usability:
                normalized = install_doctor._normalized_node_runtime(node)

        self.assertIsNone(normalized)
        usability.assert_called_once_with(node.resolve())

    def test_missing_node_is_warning(self) -> None:
        with mock.patch.object(install_doctor.shutil, "which", return_value=None):
            status, detail = install_doctor._node_runtime_status(
                strict=False,
                codex_config=Path("__missing_codex_config__.toml"),
            )
        self.assertEqual(status, install_doctor.STATUS_WARNING)
        self.assertIn("missing", detail)
        self.assertIn("For full capability, install Node.js:", detail)
        self.assertIn("https://nodejs.org/en/download", detail)

    def test_missing_node_under_strict_is_error(self) -> None:
        with mock.patch.object(install_doctor.shutil, "which", return_value=None):
            status, _detail = install_doctor._node_runtime_status(
                strict=True,
                codex_config=Path("__missing_codex_config__.toml"),
            )
        self.assertEqual(status, install_doctor.STATUS_ERROR)

    def test_present_node_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp) / "node"
            node.write_text("# fake node\n", encoding="utf-8")
            _make_node_executable(node)
            with mock.patch.object(install_doctor.shutil, "which", return_value=str(node)):
                status, detail = install_doctor._node_runtime_status(strict=False)
        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertIn("ok", detail)

    def test_codex_configured_node_is_ok_when_path_node_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp) / "codex-runtime" / "bin" / "node.exe"
            node.parent.mkdir(parents=True)
            node.write_text("# fake node\n", encoding="utf-8")
            _make_node_executable(node)
            config = Path(tmp) / ".codex" / "config.toml"
            config.parent.mkdir()
            config.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n",
                encoding="utf-8",
            )

            with mock.patch.object(install_doctor.shutil, "which", return_value=None):
                status, detail = install_doctor._node_runtime_status(
                    strict=False,
                    codex_config=config,
                )

        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertIn("codex-config", detail)
        self.assertIn("For full capability, install Node.js:", detail)
        self.assertIn("https://nodejs.org/en/download", detail)

    def test_explicit_codex_config_path_is_authoritative_over_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit_non_node = root / "explicit" / "python"
            explicit_non_node.parent.mkdir()
            explicit_non_node.write_text("# not node\n", encoding="utf-8")
            explicit_config = root / "explicit" / "config.toml"
            explicit_config.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(explicit_non_node.as_posix())}\n",
                encoding="utf-8",
            )

            codex_home_node = root / "codex-home-runtime" / "node.exe"
            codex_home_node.parent.mkdir()
            codex_home_node.write_text("# fake node\n", encoding="utf-8")
            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(codex_home_node.as_posix())}\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(install_doctor.shutil, "which", return_value=None),
                mock.patch.dict(
                    install_doctor.os.environ,
                    {
                        "CODEX_HOME": str(codex_home),
                        "GHOST_ALICE_NODE": "",
                        "NODE_REPL_NODE_PATH": "",
                    },
                    clear=False,
                ),
            ):
                status, detail = install_doctor._node_runtime_status(
                    strict=True,
                    codex_config=explicit_config,
                )

        self.assertEqual(status, install_doctor.STATUS_ERROR)
        self.assertIn("missing", detail)
        self.assertNotIn(str(codex_home_node), detail)

    def test_active_registration_precedes_explicit_env_and_codex_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_node = root / "env" / "node"
            codex_node = root / "codex" / "node.exe"
            registered_node = root / "registered" / "node"
            for node in (env_node, codex_node, registered_node):
                node.parent.mkdir(parents=True)
                node.write_text("# fake node\n", encoding="utf-8")
                _make_node_executable(node)
            codex_config = root / ".codex" / "config.toml"
            codex_config.parent.mkdir()
            codex_config.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(codex_node.as_posix())}\n",
                encoding="utf-8",
            )
            ghost_alice_root = root / ".ghost-alice"
            ghost_alice_root.mkdir()
            (ghost_alice_root / "config.json").write_text(
                json.dumps(
                    {"hook_runtime": {"node": {"claude": str(registered_node.resolve())}}}
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(install_doctor.shutil, "which", return_value=None),
                mock.patch.dict(
                    install_doctor.os.environ,
                    {
                        "GHOST_ALICE_NODE": str(env_node),
                        "NODE_REPL_NODE_PATH": "",
                    },
                    clear=False,
                ),
            ):
                status, detail = install_doctor._node_runtime_status(
                    strict=True,
                    codex_config=codex_config,
                    platform="claude",
                    ghost_alice_root=ghost_alice_root,
                )

        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertIn(f"runtime-config:claude:{registered_node.resolve()}", detail)
        self.assertNotIn(str(env_node), detail)
        self.assertNotIn(str(codex_node), detail)

    def test_codex_config_is_used_when_active_registration_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_node = root / "codex" / "node.exe"
            registered_node = root / "registered" / "node"
            for node in (codex_node, registered_node):
                node.parent.mkdir(parents=True)
                node.write_text("# fake node\n", encoding="utf-8")
                _make_node_executable(node)
            codex_config = root / ".codex" / "config.toml"
            codex_config.parent.mkdir()
            codex_config.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(codex_node.as_posix())}\n",
                encoding="utf-8",
            )
            ghost_alice_root = root / ".ghost-alice"
            ghost_alice_root.mkdir()
            (ghost_alice_root / "config.json").write_text(
                json.dumps(
                    {"hook_runtime": {"node": {"claude": str(registered_node.resolve())}}}
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(install_doctor.shutil, "which", return_value=None),
                mock.patch.dict(
                    install_doctor.os.environ,
                    {"GHOST_ALICE_NODE": "", "NODE_REPL_NODE_PATH": ""},
                    clear=False,
                ),
            ):
                status, detail = install_doctor._node_runtime_status(
                    strict=True,
                    codex_config=codex_config,
                    platform="codex",
                    ghost_alice_root=ghost_alice_root,
                )

        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertIn(f"codex-config:{codex_node}", detail)
        self.assertNotIn(str(registered_node), detail)

    def test_existing_non_node_fallback_candidates_never_report_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            non_node = root / "python"
            non_node.write_text("# not node\n", encoding="utf-8")
            missing_codex_config = root / "missing.toml"
            codex_config = root / "config.toml"
            codex_config.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(non_node.as_posix())}\n",
                encoding="utf-8",
            )
            cases = (
                (
                    "path",
                    str(non_node),
                    {"GHOST_ALICE_NODE": "", "NODE_REPL_NODE_PATH": ""},
                    missing_codex_config,
                ),
                (
                    "env",
                    None,
                    {"GHOST_ALICE_NODE": str(non_node), "NODE_REPL_NODE_PATH": ""},
                    missing_codex_config,
                ),
                (
                    "codex",
                    None,
                    {"GHOST_ALICE_NODE": "", "NODE_REPL_NODE_PATH": ""},
                    codex_config,
                ),
            )

            for source, path_value, env, config in cases:
                with (
                    self.subTest(source=source),
                    mock.patch.object(install_doctor.shutil, "which", return_value=path_value),
                    mock.patch.dict(install_doctor.os.environ, env, clear=False),
                ):
                    status, detail = install_doctor._node_runtime_status(
                        strict=True,
                        codex_config=config,
                    )
                    self.assertEqual(status, install_doctor.STATUS_ERROR)
                    self.assertIn("missing", detail)

    def test_invalid_higher_priority_fallback_skips_to_lower_valid_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invalid = root / "python"
            env_node = root / "env" / "node"
            repl_node = root / "repl" / "node.exe"
            codex_node = root / "codex" / "node"
            path_node_exe = root / "path" / "node.exe"
            for candidate in (invalid, env_node, repl_node, codex_node, path_node_exe):
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text("# runtime fixture\n", encoding="utf-8")
                _make_node_executable(candidate)
            codex_config = root / "config.toml"
            codex_config.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(codex_node.as_posix())}\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    install_doctor.shutil,
                    "which",
                    side_effect=lambda name: str(invalid) if name == "node" else None,
                ),
                mock.patch.dict(
                    install_doctor.os.environ,
                    {"GHOST_ALICE_NODE": str(env_node), "NODE_REPL_NODE_PATH": ""},
                    clear=False,
                ),
            ):
                path_to_env_status, path_to_env_detail = install_doctor._node_runtime_status(
                    strict=True,
                    codex_config=codex_config,
                )

            with (
                mock.patch.object(install_doctor.shutil, "which", return_value=None),
                mock.patch.dict(
                    install_doctor.os.environ,
                    {
                        "GHOST_ALICE_NODE": str(invalid),
                        "NODE_REPL_NODE_PATH": str(repl_node),
                    },
                    clear=False,
                ),
            ):
                env_to_env_status, env_to_env_detail = install_doctor._node_runtime_status(
                    strict=True,
                    codex_config=codex_config,
                )

            with (
                mock.patch.object(install_doctor.shutil, "which", return_value=None),
                mock.patch.dict(
                    install_doctor.os.environ,
                    {
                        "GHOST_ALICE_NODE": str(invalid),
                        "NODE_REPL_NODE_PATH": str(invalid),
                    },
                    clear=False,
                ),
            ):
                env_to_codex_status, env_to_codex_detail = install_doctor._node_runtime_status(
                    strict=True,
                    codex_config=codex_config,
                )

            which = mock.Mock(
                side_effect=lambda name: str(invalid) if name == "node" else str(path_node_exe)
            )
            with (
                mock.patch.object(install_doctor.shutil, "which", which),
                mock.patch.dict(
                    install_doctor.os.environ,
                    {"GHOST_ALICE_NODE": "", "NODE_REPL_NODE_PATH": ""},
                    clear=False,
                ),
            ):
                path_to_path_status, _path_to_path_detail = install_doctor._node_runtime_status(
                    strict=True,
                    codex_config=codex_config,
                )

        self.assertEqual(path_to_env_status, install_doctor.STATUS_OK)
        self.assertIn(f"env:{env_node.resolve()}", path_to_env_detail)
        self.assertEqual(env_to_env_status, install_doctor.STATUS_OK)
        self.assertIn(f"env:{repl_node.resolve()}", env_to_env_detail)
        self.assertEqual(env_to_codex_status, install_doctor.STATUS_OK)
        self.assertIn(f"codex-config:{codex_node.resolve()}", env_to_codex_detail)
        self.assertEqual(path_to_path_status, install_doctor.STATUS_OK)
        self.assertEqual(which.call_args_list, [mock.call("node"), mock.call("node.exe")])

    def test_relative_env_node_is_reported_as_resolved_absolute_path_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative_node = Path("runtime") / "node"
            absolute_node = root / relative_node
            absolute_node.parent.mkdir()
            absolute_node.write_text("# fake node\n", encoding="utf-8")
            _make_node_executable(absolute_node)

            with (
                contextlib.chdir(root),
                mock.patch.object(install_doctor.shutil, "which", return_value=None),
                mock.patch.dict(
                    install_doctor.os.environ,
                    {"GHOST_ALICE_NODE": str(relative_node), "NODE_REPL_NODE_PATH": ""},
                    clear=False,
                ),
                mock.patch.object(
                    install_doctor.subprocess,
                    "run",
                    side_effect=AssertionError("node candidates must not be executed"),
                ),
            ):
                status, detail = install_doctor._node_runtime_status(
                    strict=True,
                    codex_config=root / "missing.toml",
                )

        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertIn(f"env:{absolute_node.resolve()}", detail)

    def test_malformed_codex_fallback_is_unavailable_without_raising(self) -> None:
        malformed_documents = (
            b"mcp_servers = []\n",
            b"[mcp_servers]\nnode_repl = []\n",
            b"[mcp_servers.node_repl]\nenv = []\n",
            b"\xffnot-utf8",
        )
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            for document in malformed_documents:
                with self.subTest(document=document):
                    config.write_bytes(document)
                    with (
                        mock.patch.object(install_doctor.shutil, "which", return_value=None),
                        mock.patch.dict(
                            install_doctor.os.environ,
                            {"GHOST_ALICE_NODE": "", "NODE_REPL_NODE_PATH": ""},
                            clear=False,
                        ),
                    ):
                        try:
                            status, detail = install_doctor._node_runtime_status(
                                strict=False,
                                codex_config=config,
                            )
                        except Exception as exc:  # regression assertion: optional fallback never crashes doctor
                            self.fail(f"malformed Codex config raised {exc.__class__.__name__}: {exc}")
                    self.assertEqual(status, install_doctor.STATUS_WARNING)
                    self.assertIn("missing", detail)


class NodeRuntimeRunPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        self.ghost_alice_root = self.root / ".ghost-alice"
        self.config_path = self.ghost_alice_root / "config.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _runtime(self, name: str = "node", *, directory: str = "runtime") -> Path:
        runtime = self.root / directory / name
        runtime.parent.mkdir(exist_ok=True)
        runtime.write_text("# fake node runtime\n", encoding="utf-8")
        _make_node_executable(runtime)
        return runtime.resolve()

    def _write_runtime_config(self, node_registrations: object) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps({"hook_runtime": {"node": node_registrations}}),
            encoding="utf-8",
        )

    def _config_snapshot(self) -> tuple[str, bytes | None]:
        if self.config_path.is_file():
            return "file", self.config_path.read_bytes()
        if self.config_path.is_dir():
            return "directory", None
        return "absent", None

    def _run(
        self,
        platform: str,
        *,
        strict: bool = True,
        path_node: Path | None = None,
        env_node: Path | None = None,
        codex_node: Path | None = None,
    ) -> tuple[int, str]:
        argv = [
            "--platform",
            platform,
            "--repo-root",
            str(self.repo_root),
            "--encoding-root",
            str(self.repo_root),
            "--ghost-alice-root",
            str(self.ghost_alice_root),
        ]
        if strict:
            argv.append("--strict")
        args = install_doctor._parse_args(argv)
        before = self._config_snapshot()
        output = io.StringIO()
        with (
            mock.patch.object(
                install_doctor.shutil,
                "which",
                return_value=str(path_node) if path_node is not None else None,
            ),
            mock.patch.object(
                install_doctor,
                "_configured_node_runtime_from_codex_config",
                return_value=codex_node,
            ),
            mock.patch.dict(
                install_doctor.os.environ,
                {
                    "GHOST_ALICE_NODE": str(env_node) if env_node is not None else "",
                    "NODE_REPL_NODE_PATH": "",
                },
                clear=False,
            ),
            mock.patch("sys.stdout", output),
        ):
            result = install_doctor.run(args)
        after = self._config_snapshot()
        self.assertEqual(after, before, "doctor must not mutate runtime configuration")
        return result, output.getvalue()

    def _run_strict(self, platform: str) -> tuple[int, str]:
        return self._run(platform, strict=True)

    @staticmethod
    def _write_codex_node_config(codex_dir: Path, node: Path) -> Path:
        codex_dir.mkdir(parents=True, exist_ok=True)
        config = codex_dir / "config.toml"
        config.write_text(
            "[mcp_servers.node_repl.env]\n"
            f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n",
            encoding="utf-8",
        )
        return config

    @staticmethod
    def _path_snapshot(path: Path) -> tuple[str, bytes | None]:
        if path.is_file():
            return "file", path.read_bytes()
        if path.is_dir():
            return "directory", None
        return "absent", None

    def _run_with_real_codex_config(
        self,
        platform: str,
        *,
        home: Path,
        codex_home: Path | None,
    ) -> tuple[int, str]:
        argv = [
            "--platform",
            platform,
            "--repo-root",
            str(self.repo_root),
            "--encoding-root",
            str(self.repo_root),
            "--ghost-alice-root",
            str(self.ghost_alice_root),
            "--strict",
        ]
        args = install_doctor._parse_args(argv)
        observed_paths = [self.config_path, home / ".codex" / "config.toml"]
        if codex_home is not None:
            observed_paths.append(codex_home / "config.toml")
        before = {path: self._path_snapshot(path) for path in observed_paths}
        env = {
            "GHOST_ALICE_NODE": "",
            "NODE_REPL_NODE_PATH": "",
        }
        if codex_home is not None:
            env["CODEX_HOME"] = str(codex_home)
        output = io.StringIO()
        with (
            mock.patch.object(install_doctor.shutil, "which", return_value=None),
            mock.patch.object(install_doctor.Path, "home", return_value=home),
            mock.patch.dict(install_doctor.os.environ, env, clear=True),
            mock.patch("sys.stdout", output),
        ):
            result = install_doctor.run(args)
        after = {path: self._path_snapshot(path) for path in observed_paths}
        self.assertEqual(after, before, "doctor must not mutate runtime or Codex config")
        return result, output.getvalue()

    def test_strict_codex_uses_node_from_codex_home_config_after_path_loss(self) -> None:
        home = self.root / "isolated-home"
        node = self._runtime("node.exe", directory="codex-home-runtime")
        codex_home = self.root / "custom-codex-home"
        self._write_codex_node_config(codex_home, node)

        result, output = self._run_with_real_codex_config(
            "codex",
            home=home,
            codex_home=codex_home,
        )

        self.assertEqual(result, 0, output)
        self.assertIn(f"node-runtime: ok (codex-config:{node}", output)

    def test_strict_codex_uses_home_config_when_codex_home_is_absent(self) -> None:
        home = self.root / "isolated-home"
        node = self._runtime("node", directory="home-runtime")
        self._write_codex_node_config(home / ".codex", node)

        result, output = self._run_with_real_codex_config(
            "codex",
            home=home,
            codex_home=None,
        )

        self.assertEqual(result, 0, output)
        self.assertIn(f"node-runtime: ok (codex-config:{node}", output)

    def test_invalid_codex_home_candidate_does_not_fall_back_to_home_config(self) -> None:
        home = self.root / "isolated-home"
        home_node = self._runtime("node", directory="home-runtime")
        self._write_codex_node_config(home / ".codex", home_node)
        codex_home = self.root / "custom-codex-home"
        invalid_candidates = (
            self._runtime("python", directory="codex-home-non-node"),
            (self.root / "codex-home-missing" / "node.exe").resolve(),
        )

        for candidate in invalid_candidates:
            with self.subTest(candidate=candidate):
                self._write_codex_node_config(codex_home, candidate)
                result, output = self._run_with_real_codex_config(
                    "codex",
                    home=home,
                    codex_home=codex_home,
                )

                self.assertEqual(result, 1, output)
                self.assertIn("node-runtime: missing", output)
                self.assertNotIn(str(home_node), output)

    def test_strict_claude_accepts_active_registered_node_after_path_loss(self) -> None:
        node = self._runtime("node")
        other_node = self._runtime("node.exe", directory="other-runtime")
        self._write_runtime_config({"claude": str(node), "codex": str(other_node)})

        result, output = self._run_strict("claude")

        self.assertEqual(result, 0, output)
        self.assertIn(f"node-runtime: ok (runtime-config:claude:{node}", output)
        self.assertNotIn(str(other_node), output)

    def test_strict_codex_accepts_active_registered_node_after_path_loss(self) -> None:
        node = self._runtime("NODE.EXE")
        self._write_runtime_config({"codex": str(node)})

        result, output = self._run_strict("codex")

        self.assertEqual(result, 0, output)
        self.assertIn(f"node-runtime: ok (runtime-config:codex:{node}", output)

    @unittest.skipUnless(
        install_doctor.os.name != "nt",
        "POSIX execute bits are not enforced on Windows",
    )
    def test_posix_non_executable_active_registration_is_invalid(self) -> None:
        registered_node = self._runtime("node", directory="registered-non-executable")
        registered_node.chmod(0o600)
        fallback_node = self._runtime("node", directory="path-executable-fallback")
        fallback_node.chmod(0o700)
        self._write_runtime_config({"claude": str(registered_node)})

        result, output = self._run("claude", path_node=fallback_node)

        self.assertEqual(result, 1, output)
        self.assertIn("node-runtime: invalid (runtime-config:claude", output)
        self.assertNotIn(str(fallback_node), output)

    @unittest.skipUnless(
        install_doctor.os.name != "nt",
        "POSIX execute bits are not enforced on Windows",
    )
    def test_posix_non_executable_env_fallback_is_missing(self) -> None:
        env_node = self._runtime("node", directory="env-non-executable")
        env_node.chmod(0o600)

        result, output = self._run("claude", env_node=env_node)

        self.assertEqual(result, 1, output)
        self.assertIn("node-runtime: missing", output)
        self.assertNotIn(str(env_node), output)

    @unittest.skipUnless(
        install_doctor.os.name != "nt",
        "POSIX execute bits are not enforced on Windows",
    )
    def test_posix_executable_registration_and_env_fallback_are_accepted(self) -> None:
        registered_node = self._runtime("node", directory="registered-executable")
        registered_node.chmod(0o700)
        self._write_runtime_config({"claude": str(registered_node)})

        registered_result, registered_output = self._run_strict("claude")

        self.assertEqual(registered_result, 0, registered_output)
        self.assertIn(
            f"node-runtime: ok (runtime-config:claude:{registered_node}",
            registered_output,
        )

        self.config_path.unlink()
        env_node = self._runtime("node", directory="env-executable")
        env_node.chmod(0o700)

        fallback_result, fallback_output = self._run("claude", env_node=env_node)

        self.assertEqual(fallback_result, 0, fallback_output)
        self.assertIn(f"node-runtime: ok (env:{env_node}", fallback_output)

    def test_strict_other_platform_registration_remains_missing(self) -> None:
        other_node = self._runtime("node.exe")
        self._write_runtime_config({"codex": str(other_node)})

        result, output = self._run_strict("claude")

        self.assertEqual(result, 1, output)
        self.assertIn("node-runtime: missing", output)
        self.assertNotIn(str(other_node), output)

    def test_valid_active_registration_is_authoritative_over_all_fallbacks(self) -> None:
        registered_node = self._runtime("node", directory="registered")
        fallback_nodes = {
            "path": self._runtime("node", directory="path-fallback"),
            "env": self._runtime("node", directory="env-fallback"),
            "codex": self._runtime("node.exe", directory="codex-fallback"),
        }
        self._write_runtime_config({"claude": str(registered_node)})

        for source, fallback_node in fallback_nodes.items():
            kwargs = {f"{source}_node": fallback_node}
            with self.subTest(source=source):
                result, output = self._run("claude", **kwargs)
                self.assertEqual(result, 0, output)
                self.assertIn(
                    f"node-runtime: ok (runtime-config:claude:{registered_node}",
                    output,
                )
                self.assertNotIn(str(fallback_node), output)

    def test_invalid_active_registrations_are_not_masked_by_any_fallback(self) -> None:
        non_node = self._runtime("python")
        missing_node = (self.root / "missing" / "node").resolve()
        node_directory = self.root / "directory" / "node"
        node_directory.mkdir(parents=True)
        relative_node = self.root / "relative" / "node"
        relative_node.parent.mkdir()
        relative_node.write_text("# existing relative node\n", encoding="utf-8")
        invalid_values = {
            "non-node": str(non_node),
            "missing": str(missing_node),
            "directory": str(node_directory.resolve()),
            "existing-relative": str(Path("relative") / "node"),
            "empty": "",
            "whitespace": "   ",
            "integer": 7,
            "list": [],
            "mapping": {},
            "null": None,
        }
        fallback_nodes = {
            "path": self._runtime("node", directory="path-fallback"),
            "env": self._runtime("node", directory="env-fallback"),
            "codex": self._runtime("node.exe", directory="codex-fallback"),
        }

        with contextlib.chdir(self.root):
            self.assertTrue((Path("relative") / "node").is_file())
            for value_name, value in invalid_values.items():
                self._write_runtime_config({"claude": value})
                for source, fallback_node in fallback_nodes.items():
                    kwargs = {f"{source}_node": fallback_node}
                    with self.subTest(value=value_name, fallback=source):
                        result, output = self._run("claude", **kwargs)
                        self.assertEqual(result, 1, output)
                        self.assertIn(
                            "node-runtime: invalid (runtime-config:claude",
                            output,
                        )
                        self.assertNotIn(str(fallback_node), output)

    def test_invalid_active_registration_is_warning_when_not_strict(self) -> None:
        fallback_node = self._runtime("node", directory="path-fallback")
        missing_node = (self.root / "missing" / "node").resolve()
        self._write_runtime_config({"claude": str(missing_node)})

        result, output = self._run(
            "claude",
            strict=False,
            path_node=fallback_node,
        )

        self.assertEqual(result, 0, output)
        self.assertIn("node-runtime: invalid (runtime-config:claude", output)
        self.assertIn("overall: warning", output)

    def test_legacy_fallbacks_remain_when_active_registration_is_absent(self) -> None:
        fallback_nodes = {
            "path": self._runtime("node", directory="path-fallback"),
            "env": self._runtime("node", directory="env-fallback"),
            "codex": self._runtime("node.exe", directory="codex-fallback"),
        }
        cases = (
            ("config-absent", None, "path", "node-runtime: ok"),
            ("empty-registration", {}, "env", "node-runtime: ok (env:"),
            (
                "other-platform-only",
                {"codex": str(self._runtime("node", directory="other-platform"))},
                "codex",
                "node-runtime: ok (codex-config:",
            ),
        )

        for case_name, registrations, source, expected_detail in cases:
            with self.subTest(case=case_name):
                if registrations is None:
                    self.config_path.unlink(missing_ok=True)
                else:
                    self._write_runtime_config(registrations)
                result, output = self._run(
                    "claude",
                    **{f"{source}_node": fallback_nodes[source]},
                )
                self.assertEqual(result, 0, output)
                self.assertIn(expected_detail, output)

    def test_malformed_or_unreadable_config_is_not_masked_by_path(self) -> None:
        fallback_node = self._runtime("node", directory="path-fallback")

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        malformed_documents = (
            "{ malformed",
            json.dumps([]),
            json.dumps({"hook_runtime": None}),
            json.dumps({"hook_runtime": {"node": None}}),
        )
        for document in malformed_documents:
            with self.subTest(document=document):
                self.config_path.write_text(document, encoding="utf-8")
                malformed_result, malformed_output = self._run(
                    "claude",
                    path_node=fallback_node,
                )
                self.assertEqual(malformed_result, 1, malformed_output)
                self.assertIn(
                    "node-runtime: invalid (runtime-config:config",
                    malformed_output,
                )

        self.config_path.unlink()
        self.config_path.mkdir()
        unreadable_result, unreadable_output = self._run(
            "claude",
            path_node=fallback_node,
        )

        self.assertEqual(unreadable_result, 1, unreadable_output)
        self.assertIn("node-runtime: invalid (runtime-config:config", unreadable_output)


class RuntimeCoreAuditTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = self.root / "repo" / "_shared"
        self.runtime = self.root / ".ghost-alice" / "runtime" / "current" / "_shared"
        self.config = self.root / "hooks.json"
        self.source.mkdir(parents=True)
        self.runtime.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_runtime_files(self):
        files = {
            "hook_profile_gate.py": "# runner\n",
            "completion_check_validator.py": (
                "def validate_completion_text(text, *, require_completion_check=False):\n"
                "    return None\n"
            ),
        }
        for name, body in files.items():
            (self.source / name).write_text(body, encoding="utf-8")
            (self.runtime / name).write_text(body, encoding="utf-8")

    def _audit_with_files(self):
        with mock.patch.object(
            install_doctor,
            "RUNTIME_SHARED_FILES",
            ("hook_profile_gate.py", "completion_check_validator.py"),
        ), mock.patch.object(
            install_doctor,
            "_runtime_session_intent_golden_status",
            return_value=(install_doctor.STATUS_OK, "golden-pass"),
        ):
            return install_doctor._runtime_core_audit(self.source, self.runtime, self.config, "codex")

    def test_runtime_core_audit_skips_when_runtime_shared_not_requested(self):
        status, findings = install_doctor._runtime_core_audit(self.source, None, None, "codex")

        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertEqual(findings[0]["reason"], "not-requested")

    def test_runtime_shared_file_audit_covers_runner_import_dependencies(self):
        for dependency in (
            "agent_visibility_policy.py",
            "runtime_config.py",
            "work_impact_projection.py",
            "strict_session_log.py",
            "governance_events.py",
        ):
            self.assertIn(dependency, install_doctor.RUNTIME_SHARED_FILES)

    def test_runtime_shared_file_audit_rejects_missing_runner_dependency(self):
        (self.source / "hook_profile_gate.py").write_text("# runner\n", encoding="utf-8")
        (self.runtime / "hook_profile_gate.py").write_text("# runner\n", encoding="utf-8")
        (self.source / "agent_visibility_policy.py").write_text("# dependency\n", encoding="utf-8")

        with mock.patch.object(
            install_doctor,
            "RUNTIME_SHARED_FILES",
            ("hook_profile_gate.py", "agent_visibility_policy.py"),
        ):
            findings = install_doctor._runtime_shared_file_audit(self.source, self.runtime)

        self.assertTrue(any(f["status"] == install_doctor.STATUS_ERROR for f in findings))
        self.assertTrue(any(f["name"] == "agent_visibility_policy.py" for f in findings))

    def test_runtime_core_audit_accepts_runtime_hook_config(self):
        self._write_runtime_files()
        command = f'"{sys.executable}" "{(self.runtime / "hook_profile_gate.py").as_posix()}" run prompt x # [hook-reminder] AGENTS.md'
        self.config.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"command": command}]}]}}),
                               encoding="utf-8")

        status, findings = self._audit_with_files()

        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertTrue(any(f["reason"] == "runtime-core-referenced" for f in findings))
        self.assertTrue(any(f["reason"] == "golden-pass" for f in findings))

    def test_runtime_core_audit_rejects_platform_shared_hook_config(self):
        self._write_runtime_files()
        stale_runner = self.root / ".agents" / "skills" / "_shared" / "hook_profile_gate.py"
        command = f'"{sys.executable}" "{stale_runner.as_posix()}" run prompt x # [hook-reminder] AGENTS.md'
        self.config.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"command": command}]}]}}),
                               encoding="utf-8")

        status, findings = self._audit_with_files()

        self.assertEqual(status, install_doctor.STATUS_ERROR)
        self.assertTrue(any(f["reason"] == "runner-not-runtime-core" for f in findings))

    def test_session_intent_golden_flags_missing_ledger_dependency_as_warning(self):
        # First-layer install: no intent-audit capability at all must not read
        # as fully healthy — the ledger is the governance input anchor. WARNING
        # (documented degrade), not OK, not ERROR.
        hook = self.runtime / "session_intent_analyzer_hook.py"
        hook.write_text(
            "import json\n"
            "print(json.dumps({"
            "\"continue\": True, "
            "\"systemMessage\": \"Ledger dependency unavailable non-blockingly; continue without raw prompt persistence.\""
            "}))\n",
            encoding="utf-8",
        )

        status, detail = install_doctor._runtime_session_intent_golden_status(self.runtime, "claude")

        self.assertEqual(status, install_doctor.STATUS_WARNING)
        self.assertEqual(detail, "ledger-unavailable-degraded")

    def test_session_intent_golden_rejects_pointerless_write_failure(self):
        hook = self.runtime / "session_intent_analyzer_hook.py"
        hook.write_text(
            "import json\n"
            "print(json.dumps({"
            "\"continue\": True, "
            "\"systemMessage\": \"Ledger write failed non-blockingly; continue without raw prompt persistence.\""
            "}))\n",
            encoding="utf-8",
        )

        status, detail = install_doctor._runtime_session_intent_golden_status(self.runtime, "claude")

        self.assertEqual(status, install_doctor.STATUS_ERROR)
        self.assertEqual(detail, "current-session-not-written")


if __name__ == "__main__":
    unittest.main()
