"""The definitions both the installer and the runtime read live in one module, and neither layer imports the other."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_definitions

SHARED = Path(__file__).resolve().parent
RUNTIME_MODULES = (
    "hook_profile_gate", "session_intent_analyzer_hook", "io_trace_hook", "pending_merge_precheck_hook",
    "claude_stop_verification_hook", "native_completion", "strict_session_log",
    "agent_visibility_policy", "work_impact_projection", "governance_events", "completion_check_validator",
    "context_lifecycle", "response_context_use", "claude_context_adapter", "native_transcript", "source_lookup",
    "context_gateway",
)
INSTALLER_MODULES = (
    "install_hooks", "addon_installer", "addon_uninstall", "addon_registry", "addon_migration",
    "install_transaction", "installer_source_update", "installer_assets", "install_state_writer",
    "uninstall_cleanup", "retired_assets", "install_doctor", "context_gateway_install", "context_gateway_plan",
    "context_gateway_inventory", "context_gateway_transaction", "context_gateway_platform",
    "context_gateway_model", "hash_utils", "addon_skill_permissions", "commands_sync", "encoding_guard",
    "pending_manifest_writer", "installer_assets_cli", "agent_visibility_cli",
    "installer", "installer_testing", "gateway_testing",
)
# The gateway modules the installer package may touch only through its one adapter, `gateway_participant`
# (the service participant). `install_transaction` knows only the participant interface.
GATEWAY_MODULES = ("context_gateway_install", "context_gateway_plan", "context_gateway_inventory",
                   "context_gateway_transaction", "context_gateway_platform", "context_gateway_model")
# Shared modules belong to neither layer: the installer, the runtime and the skills may all import them, so a
# shared module imports only the standard library and the shared modules this table names for it.
SHARED_MODULES = {
    "hook_definitions": set(),
    "install_lock": set(),
    "content_digest": set(),
    "installed_payload": {"content_digest", "install_lock"},
}
# The installer package (`_shared/installer/*.py`) is the installer layer too; it runs
# the entry points above as child processes and imports none of the runtime.
INSTALLER_PACKAGE_FILES = tuple(sorted((SHARED / "installer").glob("*.py")))


def _imported_modules(path: Path) -> list[tuple[str, int]]:
    """Every module name a file imports, at module level or inside a function."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8", errors="replace"))):
        if isinstance(node, ast.Import):
            found.extend((alias.name.split(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module.split(".")[0], node.lineno))
    return found


class LayerRuleTests(unittest.TestCase):
    def test_runtime_modules_do_not_import_installer_modules(self):
        violations = [f"{name}:{line} -> {target}" for name in RUNTIME_MODULES if (SHARED / f"{name}.py").is_file()
                      for target, line in _imported_modules(SHARED / f"{name}.py") if target in INSTALLER_MODULES]
        self.assertEqual(violations, [])

    def test_installer_modules_do_not_import_runtime_modules(self):
        files = [SHARED / f"{name}.py" for name in INSTALLER_MODULES if (SHARED / f"{name}.py").is_file()]
        files.extend(INSTALLER_PACKAGE_FILES)
        violations = [f"{path.relative_to(SHARED)}:{line} -> {target}" for path in files
                      for target, line in _imported_modules(path) if target in RUNTIME_MODULES]
        self.assertEqual(violations, [])

    def test_installer_package_has_every_expected_module_and_shadows_no_standard_library_name(self):
        # `python _shared/installer/cli.py` puts the package directory first on sys.path, so a module named like
        # a standard-library module (an earlier `inspect.py`) would replace it for every import in that process.
        names = {path.stem for path in INSTALLER_PACKAGE_FILES}
        expected = {"__init__", "addons", "auto", "cli", "config", "diagnostics", "gateway_participant", "hosts",
                    "lifecycle", "locations", "lock", "participant", "prerequisites", "preservation", "report",
                    "runner", "selection", "source", "transaction"}
        self.assertEqual(names, expected)
        self.assertEqual(sorted((names - {"__init__"}) & set(sys.stdlib_module_names)), [])

    def test_only_the_gateway_participant_imports_the_gateway_modules_in_the_installer_package(self):
        importers = sorted({path.stem for path in INSTALLER_PACKAGE_FILES
                            for target, _ in _imported_modules(path) if target in GATEWAY_MODULES})
        self.assertEqual(importers, ["gateway_participant"])

    def test_install_transaction_imports_no_gateway_module(self):
        imported = {target for target, _ in _imported_modules(SHARED / "install_transaction.py")}
        self.assertEqual(imported & set(GATEWAY_MODULES), set())
        self.assertIn("installer", imported)

    def test_the_participant_interface_imports_only_the_standard_library(self):
        imported = {target for target, _ in _imported_modules(SHARED / "installer" / "participant.py")}
        self.assertEqual(imported - set(sys.stdlib_module_names) - {"__future__"}, set())

    def test_shared_modules_import_only_the_standard_library_and_their_named_shared_modules(self):
        for name, allowed in SHARED_MODULES.items():
            with self.subTest(module=name):
                imported = {target for target, _ in _imported_modules(SHARED / f"{name}.py")}
                self.assertEqual(imported - set(sys.stdlib_module_names) - {"__future__"}, allowed)

    def test_no_module_is_both_shared_and_in_a_layer(self):
        self.assertEqual(set(SHARED_MODULES) & (set(RUNTIME_MODULES) | set(INSTALLER_MODULES)), set())


class HookNameTests(unittest.TestCase):
    def test_normalize_hook_id_lowercases_and_keeps_the_last_namespace_part(self):
        self.assertEqual(hook_profile_gate_free_normalize("Session_Intent"), "session-intent")
        self.assertEqual(hook_profile_gate_free_normalize("adapter:autopilot-mode:Stop Hook"), "stop-hook")
        self.assertEqual(hook_profile_gate_free_normalize("  --Tool  Checkpoint-- "), "tool-checkpoint")
        self.assertEqual(hook_profile_gate_free_normalize(""), "")

    def test_resolve_hook_event_maps_every_intent_for_both_hosts(self):
        expected = {
            "on_user_prompt": "UserPromptSubmit", "pre_tool_use": "PreToolUse", "post_tool_use": "PostToolUse",
            "on_agent_stop": "Stop", "on_session_start": "SessionStart",
        }
        for intent, event in expected.items():
            for platform in ("claude", "codex"):
                self.assertEqual(hook_definitions.resolve_hook_event(intent, platform), event)
        with self.assertRaises(ValueError):
            hook_definitions.resolve_hook_event("on_user_prompt", "unknown-host")
        with self.assertRaises(ValueError):
            hook_definitions.resolve_hook_event("no-such-intent", "claude")

    def test_provider_constant_is_the_gateway_provider_name(self):
        self.assertEqual(hook_definitions.PROVIDER, "ghost_alice_context")


def hook_profile_gate_free_normalize(value):
    return hook_definitions.normalize_hook_id(value)


def _spec(adapter_id="demo", addon_id="demo-addon"):
    return {
        "adapter_id": adapter_id, "allowed_addon_id": addon_id, "expected_skill_name": addon_id,
        "events": ["on_agent_stop"], "script_rel_by_event": {"on_agent_stop": "adapters/stop.py"},
        "hook_id_by_event": {"on_agent_stop": "continue"}, "args_policy": "no_args",
        "marker_template": "[adapter:{adapter_id}] {hook_id}", "runner_namespace": "adapter-{adapter_id}-{hook_id}",
        "source_policy": "expected_skill_source", "hash_policy": "content_hash",
        "uninstall_matcher": "exact_marker_runner",
    }


def _write_allowlist(path: Path, adapters: dict, *, trust_owner: str) -> Path:
    path.write_text(json.dumps({"schema_version": 1, "trust_owner": trust_owner, "adapters": adapters}), encoding="utf-8")
    return path


class PrivilegedAdapterAllowlistTests(unittest.TestCase):
    def test_official_and_user_files_merge_and_duplicates_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            official = _write_allowlist(root / "official.json", {"demo": _spec()}, trust_owner="core")
            user = _write_allowlist(root / "user.json", {"mine": _spec("mine", "mine-addon")}, trust_owner="machine-owner")
            merged = hook_definitions.load_privileged_adapter_allowlist(official_path=official, user_path=user)
            self.assertEqual(sorted(merged), ["demo", "mine"])
            duplicate = _write_allowlist(root / "dup.json", {"demo": _spec()}, trust_owner="machine-owner")
            with self.assertRaises(hook_definitions.AddonManifestError):
                hook_definitions.load_privileged_adapter_allowlist(official_path=official, user_path=duplicate)

    def test_wrong_trust_owner_or_schema_is_rejected_and_missing_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrong_owner = _write_allowlist(root / "wrong.json", {"demo": _spec()}, trust_owner="machine-owner")
            with self.assertRaises(hook_definitions.AddonManifestError):
                hook_definitions.load_privileged_adapter_allowlist(official_path=wrong_owner, user_path=None)
            (root / "schema.json").write_text(json.dumps({"schema_version": 2, "trust_owner": "core", "adapters": {}}))
            with self.assertRaises(hook_definitions.AddonManifestError):
                hook_definitions.load_privileged_adapter_allowlist(official_path=root / "schema.json", user_path=None)
            self.assertEqual(hook_definitions.load_privileged_adapter_allowlist(official_path=root / "absent.json", user_path=None), {})

    def test_user_allowlist_defaults_to_the_ghost_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_allowlist(root / "privileged-adapters.json", {"mine": _spec("mine", "mine-addon")}, trust_owner="machine-owner")
            original = os.environ.get("GHOST_ALICE_GHOST_ROOT")
            os.environ["GHOST_ALICE_GHOST_ROOT"] = str(root)
            try:
                merged = hook_definitions.load_privileged_adapter_allowlist(official_path=None)
            finally:
                if original is None:
                    os.environ.pop("GHOST_ALICE_GHOST_ROOT", None)
                else:
                    os.environ["GHOST_ALICE_GHOST_ROOT"] = original
            self.assertEqual(list(merged), ["mine"])

    def test_runner_id_is_one_rule_with_and_without_a_spec(self):
        self.assertEqual(hook_definitions.privileged_adapter_runner_id("demo", "continue"), "adapter-demo-continue")
        self.assertEqual(hook_definitions.privileged_adapter_runner_id("demo", "continue", _spec()), "adapter-demo-continue")
        bad = dict(_spec(), runner_namespace="other-{adapter_id}")
        with self.assertRaises(hook_definitions.AddonManifestError):
            hook_definitions.privileged_adapter_runner_id("demo", "continue", bad)

    def test_spec_validation_rejects_escaping_scripts_and_unknown_events(self):
        escaping = dict(_spec(), script_rel_by_event={"on_agent_stop": "../outside.py"})
        with self.assertRaises(hook_definitions.AddonManifestError):
            hook_definitions.privileged_adapter_spec("demo", allowlist={"demo": escaping})
        unknown = dict(_spec(), events=["on_coffee"], script_rel_by_event={"on_coffee": "a.py"}, hook_id_by_event={"on_coffee": "x"})
        with self.assertRaises(hook_definitions.AddonManifestError):
            hook_definitions.privileged_adapter_spec("demo", allowlist={"demo": unknown})
        self.assertEqual(hook_definitions.privileged_adapter_spec("demo", allowlist={"demo": _spec()})["adapter_id"], "demo")


if __name__ == "__main__":
    unittest.main()
