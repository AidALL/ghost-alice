#!/usr/bin/env python3
"""Tests for the read-only installer doctor node-runtime check.

The tool-checkpoint PreToolUse gate is dispatched through `node ghost-alice-hook.mjs`.
The installer blocks hook install when node is absent, but node can be removed from
PATH after install. Doctor is the read-only diagnostic that must surface that drift,
because a missing Node runtime prevents the checkpoint hook from executing.
The checkpoint is guidance and audit; the native host owns tool permission.
"""

import contextlib
import inspect
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
        _, findings = install_doctor._addon_registry_audit(self.gar, "claude")
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
        self.assertIn(f"codex-config:{codex_node.resolve()}", detail)
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


class NativeIntakeWaitsTest(unittest.TestCase):
    def test_doctor_lists_waiting_native_inputs_from_the_installed_ledger(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "session-intent-analyzer" / "scripts"))
        import session_intent_ledger as ledger
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_root = root / "intent"
            transcript = root / "waiting.jsonl"
            transcript.write_text("", encoding="utf-8")
            coords = dict(root=ledger_root, platform="claude", session_id="waiting-session")
            ledger.stage_native_input(**coords, raw_input="Session message without a native row",
                                      prompt_id=None, transcript_path=str(transcript))
            ledger.reconcile_native_inputs(**coords, record_wait=True)
            ghost = root / ".ghost-alice"
            ghost.mkdir()
            # The record's hook registration names the ledger root the installed hooks run with.
            (ghost / "installed-payload.json").write_text(json.dumps({"schema_version": 1, "targets": {}, "hooks": {
                "claude": {"destination": {"ledger_root": str(ledger_root)}}}}), encoding="utf-8")
            lines = install_doctor._native_intake_wait_lines(ghost, "claude")
            codex = install_doctor._native_intake_wait_lines(ghost, "codex")
        self.assertEqual(lines[0], "native-intake-waits: 1")
        self.assertIn("waiting-session", lines[1])
        self.assertIn("row-not-found", lines[1])
        self.assertEqual(codex, ["native-intake-waits: unavailable (no installed ledger root)"])

    def test_doctor_lists_recorded_native_intake_failures(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "session-intent-analyzer" / "scripts"))
        import session_intent_ledger as ledger
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_root = root / "intent"
            coords = dict(root=ledger_root, platform="claude", session_id="failure-session")
            for _ in range(2):
                ledger.record_native_intake_failure(**coords, error=sqlite3.OperationalError("database is locked"))
            ghost = root / ".ghost-alice"
            ghost.mkdir()
            (ghost / "installed-payload.json").write_text(json.dumps({"schema_version": 1, "targets": {}, "hooks": {
                "claude": {"destination": {"ledger_root": str(ledger_root)}}}}), encoding="utf-8")
            lines = install_doctor._native_intake_failure_lines(ghost, "claude")
        self.assertEqual(lines[0], "native-intake-failures: 1")
        self.assertIn("claude/failure-session: OperationalError x2", lines[1])

    def test_doctor_reports_an_unreadable_ledger_without_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ghost = Path(tmp) / ".ghost-alice"
            ghost.mkdir()
            broken = Path(tmp) / "intent"
            broken.mkdir()
            (broken / "ghost-state.sqlite3").write_text("not a database", encoding="utf-8")
            # An older installer's config.json copy of the ledger root still counts while the record names none.
            (ghost / "config.json").write_text(json.dumps(
                {"install_locations": {"claude": {"ledger_root": str(broken)}}}), encoding="utf-8")
            lines = install_doctor._native_intake_wait_lines(ghost, "claude")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("native-intake-waits: unavailable ("))


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

    def test_run_prints_native_intake_waits_without_changing_the_result(self) -> None:
        result, output = self._run("claude", path_node=self._runtime())
        self.assertEqual(result, 0, output)
        self.assertIn("native-intake-waits: unavailable (no installed ledger root)", output)
        self.assertIn("native-intake-failures: unavailable (no installed ledger root)", output)

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


class RetentionFailureDoctorTest(NodeRuntimeRunPathTest):
    """Doctor reads the retention failure record that `state_retention` writes, at the path it writes it."""

    def setUp(self) -> None:
        super().setUp()
        environment = mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(self.ghost_alice_root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.record = self.ghost_alice_root / "state-retention-failures.jsonl"

    def _record(self, *rows: dict) -> None:
        self.ghost_alice_root.mkdir(parents=True, exist_ok=True)
        self.record.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_doctor_reads_the_record_where_retention_writes_it(self):
        # A Ghost-ALICE root other than ~/.ghost-alice moves the record for both the writer and doctor.
        import state_retention
        elsewhere = self.root / "elsewhere"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(elsewhere)}):
            state_retention.apply("no-such-entry", self.root)
            _, output = self._run("claude", strict=False)
        self.assertTrue((elsewhere / "state-retention-failures.jsonl").exists())
        self.assertIn("state-retention: 1 failures", output)

    def test_without_failures_the_retention_line_is_ok(self):
        self.assertEqual(install_doctor._retention_failure_line(self.record), "ok")
        _, output = self._run("claude", strict=False)
        self.assertIn("state-retention: ok", output)

    def test_recorded_failures_are_listed_without_changing_overall(self):
        _, clean = self._run("claude", strict=False)
        self._record({"ts": "2026-10-21T00:00:00Z", "policy": "session-logs", "path": "/a", "error_type": "OSError",
                      "error": "busy"},
                     {"ts": "2026-10-22T01:02:03Z", "policy": "gateway-usage", "path": "/b",
                      "error_type": "PermissionError", "error": "denied"})
        self.assertEqual(install_doctor._retention_failure_line(self.record),
                         "2 failures; last 2026-10-22T01:02:03Z gateway-usage PermissionError")
        _, output = self._run("claude", strict=False)
        self.assertIn("state-retention: 2 failures; last 2026-10-22T01:02:03Z gateway-usage PermissionError", output)
        # A retention failure is Ghost-ALICE's own and needs no repair by the user: doctor reports it and
        # overall stays what it was without the record.
        overall = lambda text: [line for line in text.splitlines() if line.startswith("overall:")]
        self.assertEqual(overall(output), overall(clean))

    def test_an_unreadable_record_is_reported(self):
        self.ghost_alice_root.mkdir(parents=True, exist_ok=True)
        self.record.write_bytes(b"\xff\xfe not json\n")
        self.assertEqual(install_doctor._retention_failure_line(self.record), "record unreadable")


class HookRejectionDoctorTest(NodeRuntimeRunPathTest):
    """Doctor reads the hook runner's command rejections from the strict session logs the runner writes them to."""

    def setUp(self) -> None:
        super().setUp()
        environment = mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(self.ghost_alice_root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.session_logs = self.ghost_alice_root / "session-logs"
        self.now = datetime.now(timezone.utc)

    def _stamp(self, *, days_ago: float = 0, seconds_ago: float = 0) -> str:
        return (self.now - timedelta(days=days_ago, seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _printed(stamp: str) -> str:
        return stamp[:19] + "Z"

    def _row(self, platform: str, session: str, *, ts: str, rejected: bool = True,
             hook_id: str = "tool-checkpoint", reason: str = "hook payload decode failed") -> dict:
        """A strict log row as `strict_session_log.append_event` writes it. A rejection carries the runner's mark
        (`surface_item.value_key`); the ordinary row quotes the mark's words inside a value, which is not the mark."""
        stderr = f"hook command rejected: {reason}\n" if rejected else ""
        item = {"source_hook": hook_id, "value_key": "hook-command-rejection" if rejected else hook_id,
                "value_kind": "gate" if rejected else "routine", "exposure_class": "forced" if rejected else "routine",
                "value": " ".join(stderr.split()), "strict_log_ref": "/dev/null"}
        return {"schema_version": "strict-hook-output.v1", "ts": ts, "platform": platform, "session_id": session,
                "hook_id": hook_id, "event": "PreToolUse", "agent_visibility_profile": "strict",
                "visible_decision": "force_show" if rejected else "show", "exit_code": 126 if rejected else 0,
                "stdout": "" if rejected else 'saw "value_key": "hook-command-rejection" in a value',
                "stderr": stderr, "payload_digest": "sha256:0", "surface_item": item}

    def _log(self, platform: str, session: str, rows: list[dict], *, age_days: float = 0) -> Path:
        log = self.session_logs / platform / session / "strict-hook-output.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                       encoding="utf-8")
        if age_days:
            moment = (self.now - timedelta(days=age_days)).timestamp()
            os.utime(log, (moment, moment))
        return log

    def test_without_session_logs_the_line_is_zero(self):
        self.assertEqual(install_doctor._hook_rejection_lines(self.session_logs), ["hook-rejections: 0 in 14 days"])
        _, output = self._run("claude", strict=False)
        self.assertIn("hook-rejections: 0 in 14 days", output)

    def test_rejections_are_listed_per_session_newest_first_without_changing_overall(self):
        _, clean = self._run("claude", strict=False)
        mismatch = ("registered adapter hash mismatch. Adapter was not executed. expected=0 actual=1. "
                    "Check the installed adapter and current registration.")
        older_last = self._stamp(days_ago=1)
        self._log("claude", "s-older", [
            self._row("claude", "s-older", ts=self._stamp(days_ago=2), hook_id="adapter-demo-prepare-origin",
                      reason=mismatch),
            self._row("claude", "s-older", ts=self._stamp(days_ago=1, seconds_ago=10), rejected=False),
            self._row("claude", "s-older", ts=older_last, hook_id="adapter-demo-prepare-origin",
                      reason=mismatch),
        ])
        newer_last = self._stamp(seconds_ago=30)
        self._log("codex", "s-newer", [self._row("codex", "s-newer", ts=newer_last)])
        lines = install_doctor._hook_rejection_lines(self.session_logs)
        self.assertEqual(lines, [
            "hook-rejections: 3 in 14 days (2 sessions)",
            f"  codex/s-newer: 1, last {self._printed(newer_last)} tool-checkpoint: hook payload decode failed",
            f"  claude/s-older: 2, last {self._printed(older_last)} adapter-demo-prepare-origin: "
            "registered adapter hash mismatch",
        ])
        # Doctor for one platform lists every host folder: the audit platform of a rejection can be `unknown`.
        _, output = self._run("claude", strict=False)
        for line in lines:
            self.assertIn(line, output)
        # A rejection is Ghost-ALICE's own refusal to run a hook command: doctor reports it and overall stays
        # what it was without the rows.
        overall = lambda text: [line for line in text.splitlines() if line.startswith("overall:")]
        self.assertEqual(overall(output), overall(clean))

    def test_the_listing_shows_the_newest_five_sessions(self):
        for index in range(7):
            self._log("claude", f"s-{index}", [self._row("claude", f"s-{index}", ts=self._stamp(days_ago=index))])
        lines = install_doctor._hook_rejection_lines(self.session_logs)
        self.assertEqual(lines[0], "hook-rejections: 7 in 14 days (7 sessions, newest 5 shown)")
        self.assertEqual([line.split(":")[0].strip() for line in lines[1:]], [f"claude/s-{index}" for index in range(5)])

    def test_sessions_and_rows_older_than_the_retention_window_are_left_out(self):
        # The folder whose log last changed 15 days ago is one the session-logs retention removes; doctor does not
        # read it. A row older than 14 days inside a live log is not counted either.
        self._log("claude", "s-gone", [self._row("claude", "s-gone", ts=self._stamp(days_ago=1))], age_days=15)
        self._log("claude", "s-live", [self._row("claude", "s-live", ts=self._stamp(days_ago=20)),
                                       self._row("claude", "s-live", ts=self._stamp(days_ago=3))])
        lines = install_doctor._hook_rejection_lines(self.session_logs)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "hook-rejections: 1 in 14 days (1 sessions)")
        self.assertTrue(lines[1].startswith("  claude/s-live: 1, last "), lines[1])

    def test_an_unreadable_log_is_reported(self):
        (self.session_logs / "claude" / "s-dir" / "strict-hook-output.jsonl").mkdir(parents=True)
        self.assertEqual(install_doctor._hook_rejection_lines(self.session_logs),
                         ["hook-rejections: unavailable (IsADirectoryError claude/s-dir)"])
        _, output = self._run("claude", strict=False)
        self.assertIn("hook-rejections: unavailable (IsADirectoryError claude/s-dir)", output)

    def test_a_log_whose_marked_row_is_not_json_is_counted_as_unreadable_beside_the_readable_ones(self):
        self._log("claude", "s-ok", [self._row("claude", "s-ok", ts=self._stamp(days_ago=1))])
        broken = self.session_logs / "codex" / "s-bad" / "strict-hook-output.jsonl"
        broken.parent.mkdir(parents=True)
        broken.write_bytes(b'{"surface_item": {"value_key": "hook-command-rejection"}, broken\n')
        lines = install_doctor._hook_rejection_lines(self.session_logs)
        self.assertEqual(lines[0],
                         "hook-rejections: 1 in 14 days (1 sessions; 1 logs unreadable: JSONDecodeError codex/s-bad)")
        self.assertEqual(len(lines), 2)

    def test_doctor_reads_the_row_the_runner_writes(self):
        # The runner's own writer, at the path it chooses from the same Ghost-ALICE root.
        import hook_profile_gate
        env = dict(os.environ, HOME=str(self.root), GHOST_ALICE_PLATFORM="claude", GHOST_ALICE_SESSION_ID="s-real")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(hook_profile_gate, "_read_stdin", return_value="{}"):
            hook_profile_gate._append_rejection_audit(
                "tool-checkpoint", "hook command rejected: hook payload decode failed\n", platform="claude")
        self.assertTrue((self.session_logs / "claude" / "s-real" / "strict-hook-output.jsonl").is_file())
        lines = install_doctor._hook_rejection_lines(self.session_logs)
        self.assertEqual(lines[0], "hook-rejections: 1 in 14 days (1 sessions)")
        self.assertTrue(lines[1].startswith("  claude/s-real: 1, last "), lines[1])
        self.assertTrue(lines[1].endswith(" tool-checkpoint: hook payload decode failed"), lines[1])


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

    def _bound_session_intent(self, platform="claude", *, inner=None, **locations):
        """The installed session-intent registration and the record it runs through: `(command, record)`."""
        import hook_launcher
        import install_destination
        import install_hooks

        source_root = Path(__file__).resolve().parents[1]
        destination = install_destination.InstallDestination(**{
            "mode": "normal", "owned_root": str(self.root),
            "platform_dir": str(self.root / "platform"), "skills_dir": str(self.root / "skills"),
            "ghost_root": str(self.root / "ghost"), "ledger_root": str(self.root / "ledger"),
            "project_root": str(source_root), "core_source_root": str(source_root),
            "runtime_shared_dir": str(self.runtime), **locations,
        })
        for shared in (self.runtime, Path(destination.runtime_shared_dir)):
            shared.mkdir(parents=True, exist_ok=True)
            for name in ("session_intent_analyzer_hook.py", "hook_profile_gate.py"):
                (shared / name).write_text("# bound hook fixture\n", encoding="utf-8")
        with install_destination.use(destination):
            command = install_hooks._entry_command(
                install_hooks._platform_session_intent_entry(platform, "UserPromptSubmit"))
            intake = inner or install_hooks._core_registration(platform, "session-intent").command
        record = {"schema_version": 1, "targets": {}, "hooks": {platform: hook_launcher.host_section(
            hook_file=Path(destination.platform_dir) / "settings.json", destination=destination,
            entries={"session-intent": {"event": "UserPromptSubmit", "marker": install_hooks.SESSION_INTENT_MARKER,
                                        "command": intake, "provider": "core"}})}}
        return command, record

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
            "text_lines.py",
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
        command = f'"{sys.executable}" "{(self.runtime / "hook_profile_gate.py").as_posix()}" run session-intent x # [session-intent-analyzer]'
        self.config.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"command": command}]}]}}),
                               encoding="utf-8")

        status, findings = self._audit_with_files()

        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertTrue(any(f["reason"] == "runtime-core-referenced" for f in findings))
        self.assertTrue(any(f["reason"] == "golden-pass" for f in findings))

    def test_runtime_core_audit_rejects_platform_shared_hook_config(self):
        self._write_runtime_files()
        stale_runner = self.root / ".agents" / "skills" / "_shared" / "hook_profile_gate.py"
        command = f'"{sys.executable}" "{stale_runner.as_posix()}" run session-intent x # [session-intent-analyzer]'
        self.config.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"command": command}]}]}}),
                               encoding="utf-8")

        status, findings = self._audit_with_files()

        self.assertEqual(status, install_doctor.STATUS_ERROR)
        self.assertTrue(any(f["reason"] == "runner-not-runtime-core" for f in findings))

    def test_session_intent_golden_flags_missing_ledger_dependency_as_warning(self):
        # First-layer install: no intent-audit capability at all must not read as fully healthy — the ledger is the governance input anchor. WARNING (documented degrade), not OK, not ERROR.
        source_shared = Path(__file__).resolve().parent
        import shutil
        shutil.copytree(source_shared, self.runtime, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("test_*", "tests", "__pycache__"))
        status, detail = install_doctor._runtime_session_intent_golden_status(self.runtime, "claude")

        self.assertEqual(status, install_doctor.STATUS_WARNING)
        self.assertEqual(detail, "ledger-unavailable-degraded")

    def test_session_intent_golden_reconciles_real_claude_human_observation(self):
        source_shared = Path(__file__).resolve().parent
        result = install_doctor._runtime_session_intent_golden_status(
            source_shared, "claude", repo_root=source_shared.parent)
        self.assertEqual(result, (install_doctor.STATUS_OK, "golden-pass"))

    def test_session_intent_golden_uses_installed_binding_without_ambient_provider(self):
        import shutil
        import install_destination
        import install_hooks

        source_shared = Path(__file__).resolve().parent
        shutil.copytree(source_shared, self.runtime, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("test_*", "tests", "__pycache__"))
        destination = install_destination.InstallDestination(
            mode="normal", owned_root=str(self.root), platform_dir=str(self.root / "platform"),
            skills_dir=str(self.root / "skills"), ghost_root=str(self.root / "ghost"),
            ledger_root=str(self.root / "installed-ledger"), project_root=str(source_shared.parent),
            core_source_root=str(source_shared.parent), runtime_shared_dir=str(self.runtime))
        import installed_payload
        # The install writes the launcher registration into the host file and its inner command into the record.
        Path(destination.platform_dir).mkdir(parents=True, exist_ok=True)
        self.assertEqual(install_hooks.install_hook("claude", destination=destination), "installed")
        record = installed_payload.read(destination.ghost_root)
        with mock.patch.dict(os.environ, {"HOME": str(self.root),
                                          "GHOST_ALICE_CORE_ROOT": str(self.root / "foreign-provider")}):
            status, findings = install_doctor._runtime_core_audit(
                source_shared, self.runtime, Path(destination.platform_dir) / "settings.json", "claude",
                record=record)
        self.assertEqual(status, install_doctor.STATUS_OK, findings)
        self.assertFalse(Path(destination.ledger_root).exists(), "golden wrote the installed ledger")
        # Without the record the same host file is a registration the launcher would reject.
        _, findings = install_doctor._runtime_core_audit(
            source_shared, self.runtime, Path(destination.platform_dir) / "settings.json", "claude")
        self.assertIn("registration-missing", {finding["reason"] for finding in findings})
        self.assertIn("session-intent-registration-missing", {finding["reason"] for finding in findings})

    def test_session_intent_golden_rejects_binding_mismatch(self):
        for overrides, reason in (
            ({"platform": "codex"}, "session-intent-registration-missing"),
            ({"runtime_shared_dir": str(self.root / "foreign-runtime")}, "session-intent-binding-mismatch"),
            ({"core_source_root": str(self.root / "foreign-provider")}, "session-intent-binding-mismatch"),
        ):
            with self.subTest(overrides=overrides):
                command, record = self._bound_session_intent(**overrides)
                self.assertEqual(
                    install_doctor._runtime_session_intent_golden_status(
                        self.runtime, "claude", hook_commands=[command], record=record),
                    (install_doctor.STATUS_ERROR, reason))

    def test_session_intent_golden_rejects_a_registration_the_record_does_not_carry(self):
        command, _ = self._bound_session_intent()
        self.assertEqual(
            install_doctor._runtime_session_intent_golden_status(
                self.runtime, "claude", hook_commands=[command]),
            (install_doctor.STATUS_ERROR, "session-intent-registration-missing"))

    def test_session_intent_golden_rejects_runner_without_valid_intake_executor(self):
        command, record = self._bound_session_intent(inner="echo intake-lookalike")
        self.assertEqual(
            install_doctor._runtime_session_intent_golden_status(
                self.runtime, "claude", hook_commands=[command], record=record),
            (install_doctor.STATUS_ERROR, "session-intent-registration-invalid"))

    def test_session_intent_golden_uses_repo_local_child_temp(self):
        source_shared = Path(__file__).resolve().parent
        calls = []
        real_run = install_doctor.subprocess.run

        def run_process(command, **kwargs):
            calls.append((command, kwargs))
            return real_run(command, **kwargs)

        repo_root = Path(__file__).resolve().parents[1]
        hostile = r"C:\Users\Public\Documents\ESTsoft\CreatorTemp"
        with mock.patch.dict(
            os.environ,
            {
                "TEMP": hostile,
                "TMP": hostile,
                "TMPDIR": hostile,
                "PYTHONPYCACHEPREFIX": hostile,
            },
            clear=False,
        ), mock.patch.object(install_doctor.subprocess, "run", run_process):
            parent_before = {
                key: os.environ[key]
                for key in ("TEMP", "TMP", "TMPDIR", "PYTHONPYCACHEPREFIX")
            }
            self.assertIn(
                "repo_root",
                inspect.signature(
                    install_doctor._runtime_session_intent_golden_status
                ).parameters,
            )
            status, detail = install_doctor._runtime_session_intent_golden_status(
                source_shared,
                "claude",
                repo_root=repo_root,
            )
            self.assertEqual(
                {
                    key: os.environ[key]
                    for key in ("TEMP", "TMP", "TMPDIR", "PYTHONPYCACHEPREFIX")
                },
                parent_before,
            )

        self.assertEqual((status, detail), (install_doctor.STATUS_OK, "golden-pass"))
        self.assertEqual(len(calls), 2)
        command, kwargs = calls[0]
        self.assertEqual(Path(kwargs["cwd"]).resolve(), repo_root.resolve())
        run_root = Path(kwargs["env"]["TMPDIR"]).parent
        self.assertTrue(run_root.is_relative_to(repo_root / ".tmp"))
        self.assertTrue(
            Path(command[command.index("--root") + 1]).is_relative_to(run_root)
        )
        self.assertFalse(run_root.exists())
        for _, child_kwargs in calls:
            self.assertEqual(child_kwargs["env"]["TMPDIR"], kwargs["env"]["TMPDIR"])
            self.assertEqual(child_kwargs["env"]["GHOST_ALICE_SESSION_INTENT_ROOT"],
                             command[command.index("--root") + 1])

    def test_session_intent_golden_verifies_database_without_pointer_export(self):
        source_shared = Path(__file__).resolve().parent
        real_run = install_doctor.subprocess.run
        removed_pointers = []

        def run_process(command, **kwargs):
            result = real_run(command, **kwargs)
            if "--root" in command:
                root = Path(command[command.index("--root") + 1])
                pointer = root / "codex" / "current-session.json"
                self.assertTrue((root / "ghost-state.sqlite3").is_file())
                pointer.unlink(missing_ok=True)
                removed_pointers.append(pointer)
            else:
                self.assertTrue(removed_pointers)
                self.assertFalse(removed_pointers[0].exists())
            return result

        with mock.patch.object(install_doctor.subprocess, "run", run_process):
            result = install_doctor._runtime_session_intent_golden_status(source_shared, "codex")
        self.assertEqual(result, (install_doctor.STATUS_OK, "golden-pass"))

    def test_session_intent_golden_rejects_pointerless_write_failure(self):
        source_shared = Path(__file__).resolve().parent
        real_run = install_doctor.subprocess.run

        def run_process(command, **kwargs):
            if "--root" in command:
                return install_doctor.subprocess.CompletedProcess(command, 0, json.dumps({"continue": True}), "")
            return real_run(command, **kwargs)

        with mock.patch.object(install_doctor.subprocess, "run", run_process):
            status, detail = install_doctor._runtime_session_intent_golden_status(source_shared, "claude")

        self.assertEqual(status, install_doctor.STATUS_ERROR)
        self.assertEqual(detail, "session-intent-not-committed")


class RuntimeValidatorGoldenSampleTest(unittest.TestCase):
    def test_golden_sample_carries_no_retired_printed_fields(self):
        # Decisions 6, 7 and 14 retired the printed [gate-state] block and its skill-call and
        # skills-loaded fields, so the doctor's golden answer must not depend on them.
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            seen = runtime / "sample.txt"
            (runtime / "completion_check_validator.py").write_text(
                "from pathlib import Path\n"
                f"SEEN = Path({str(seen)!r})\n"
                "def validate_completion_text(text, require_completion_check=False):\n"
                "    SEEN.write_text(text, encoding='utf-8')\n"
                "    return None\n",
                encoding="utf-8",
            )
            status = install_doctor._runtime_validator_golden_status(runtime)
            sample = seen.read_text(encoding="utf-8")

        self.assertEqual(status, (install_doctor.STATUS_OK, "golden-pass"))
        self.assertIn("[completion-check]", sample)
        for retired in ("[gate-state]", "skill-call", "skills-loaded"):
            self.assertNotIn(retired, sample)


if __name__ == "__main__":
    unittest.main()
