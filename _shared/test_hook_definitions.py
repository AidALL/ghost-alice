"""The definitions both the installer and the runtime read live in one module, and neither layer imports the other."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_definitions

SHARED = Path(__file__).resolve().parent
RUNTIME_MODULES = (
    "hook_profile_gate", "session_intent_analyzer_hook", "io_trace_hook", "pending_merge_precheck_hook",
    "claude_stop_verification_hook", "native_completion", "stop_notice", "strict_session_log",
    "agent_visibility_policy", "work_impact_projection", "completion_check_validator",
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
    "codex_config_toml": {"_vendor"},
    "hook_definitions": set(),
    "install_lock": {"file_lock"},
    "content_digest": set(),
    "installed_payload": {"content_digest", "install_lock"},
    "install_destination": set(),
    "atomic_write": {"install_destination"},
    "file_lock": set(),
    "pending_manifest": {"atomic_write", "file_lock", "install_destination"},
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

    def test_installer_package_shadows_no_standard_library_name(self):
        # `python _shared/installer/cli.py` puts the package directory first on sys.path, so a module named like
        # a standard-library module (an earlier `inspect.py`) would replace it for every import in that process.
        names = {path.stem for path in INSTALLER_PACKAGE_FILES}
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
                path = SHARED / 'codex_config_toml' / '__init__.py' if name == 'codex_config_toml' else SHARED / f'{name}.py'
                imported = {target for target, _ in _imported_modules(path)}
                self.assertEqual(imported - set(sys.stdlib_module_names) - {"__future__"}, allowed)

    def test_codex_syntax_package_imports_only_its_relative_vendor(self):
        tree = ast.parse((SHARED / 'codex_config_toml' / '__init__.py').read_text())
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual(len(imports), 1)
        self.assertIsInstance(imports[0], ast.ImportFrom)
        self.assertEqual((imports[0].level, imports[0].module), (1, '_vendor.tomli'))

    def test_runtime_configuration_does_not_import_installer_modules(self):
        imported = {target for target, _ in _imported_modules(SHARED / 'runtime_config.py')}
        self.assertEqual(imported & set(INSTALLER_MODULES), set())
        self.assertIn('codex_config_toml', imported)

    def test_no_module_is_both_shared_and_in_a_layer(self):
        self.assertEqual(set(SHARED_MODULES) & (set(RUNTIME_MODULES) | set(INSTALLER_MODULES)), set())


class HookNameTests(unittest.TestCase):
    def test_platform_reader_normalizes_supported_names_and_handles_invalid_values(self):
        for supplied, expected in (("claude", "claude"), (" CoDeX ", "codex")):
            with self.subTest(platform=supplied):
                self.assertEqual(hook_definitions.read_platform(supplied), expected)
                self.assertEqual(hook_definitions.read_platform(supplied, reject_unknown=True), expected)
        for supplied in (None, "", " ", "future-host", "../claude", 1, {"name": "claude"}):
            with self.subTest(platform=supplied):
                self.assertEqual(hook_definitions.read_platform(supplied), "unknown")
                with self.assertRaises(ValueError):
                    hook_definitions.read_platform(supplied, reject_unknown=True)

    def test_normalize_hook_id_lowercases_and_keeps_the_last_namespace_part(self):
        self.assertEqual(hook_profile_gate_free_normalize("Session_Intent"), "session-intent")
        self.assertEqual(hook_profile_gate_free_normalize("addon:demo:Stop Hook"), "stop-hook")
        self.assertEqual(hook_profile_gate_free_normalize("  --Tool  Checkpoint-- "), "tool-checkpoint")
        self.assertEqual(hook_profile_gate_free_normalize(""), "")

    def test_only_claude_code_has_a_failed_tool_call_event(self):
        self.assertEqual(hook_definitions.resolve_hook_event("post_tool_use_failure", "claude"), "PostToolUseFailure")
        with self.assertRaises(ValueError):
            hook_definitions.resolve_hook_event("post_tool_use_failure", "codex")

    def test_resolve_hook_event_maps_the_shared_intents_for_both_hosts(self):
        expected = {
            "on_prompt_submit": "UserPromptSubmit",
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


class CoreHostCliTests(unittest.TestCase):
    def test_cli_host_choices_preserve_acceptance_rejection_and_display_order(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.txt"
            source.write_text("original source", encoding="utf-8")
            environment = {key: value for key, value in os.environ.items()
                if not key.startswith(("GHOST_ALICE_", "GIT_")) and key not in {
                    "CODEX_THREAD_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_HOME",
                    "CLAUDE_CONFIG_DIR", "PYTHONPATH"}}
            environment.update(HOME=folder, USERPROFILE=folder, TMPDIR=folder)
            cases = (
                ("claude_stop_verification_hook", [], "claude,codex", 0, "stdout", '"continue": true'),
                ("source_lookup", [str(source)], "codex,claude", 1, "stdout", "A declared use requires"),
                ("context_lifecycle", ["--root", folder, "--native-dir", folder, "--session-id", "",
                                       "--locate-text", "original"], "codex,claude", 1, "stderr",
                 "The exact native session is required"),
            )
            for module, arguments, order, status, channel, expected in cases:
                for platform in ("claude", "codex", "unknown-host"):
                    with self.subTest(module=module, platform=platform):
                        result = subprocess.run([sys.executable, str(SHARED / f"{module}.py"),
                            *arguments, "--platform", platform], input="{}", text=True, capture_output=True,
                            cwd=folder, env=environment)
                        if platform == "unknown-host":
                            self.assertEqual(result.returncode, 2, result.stderr)
                            self.assertIn("invalid choice: 'unknown-host'", result.stderr)
                            self.assertIn("{" + order + "}", result.stderr)
                        else:
                            self.assertEqual(result.returncode, status, result.stderr)
                            self.assertIn(expected, getattr(result, channel))


def hook_profile_gate_free_normalize(value):
    return hook_definitions.normalize_hook_id(value)


if __name__ == "__main__":
    unittest.main()
