#!/usr/bin/env python3
"""Tests for the Ghost-ALICE hook command runner."""

from __future__ import annotations

import base64
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hook_profile_gate
import install_hooks


def _python_payload_command(args: str) -> str:
    executable = sys.executable.replace("\\", "/")
    return f"{executable} {args}"


class TestHookRunnerExecutionGate(unittest.TestCase):
    def test_dynamic_visibility_does_not_disable_tool_checkpoint(self):
        env = {"GHOST_ALICE_AGENT_VISIBILITY": "dynamic"}

        self.assertTrue(hook_profile_gate.is_hook_enabled("prompt", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("completion", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("web-search-first", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("tool-checkpoint", env=env))

    def test_minimal_visibility_does_not_disable_hooks(self):
        env = {"GHOST_ALICE_AGENT_VISIBILITY": "minimal"}

        self.assertTrue(hook_profile_gate.is_hook_enabled("session-start", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("io-trace", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("prompt", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("web-search-first", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("tool-checkpoint", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("completion", env=env))

    def test_legacy_hook_profile_env_does_not_control_visibility_or_execution(self):
        env = {"GHOST_ALICE_HOOK_PROFILE": "quiet"}

        self.assertTrue(hook_profile_gate.is_hook_enabled("prompt", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("web-search-first", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("tool-checkpoint", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("completion", env=env))

    def test_disabled_hooks_accept_event_prefixes(self):
        env = {
            "GHOST_ALICE_AGENT_VISIBILITY": "strict",
            "GHOST_ALICE_DISABLED_HOOKS": "prompt:web-search-first, tool_checkpoint",
        }

        self.assertIn("web-search-first", hook_profile_gate.disabled_hooks(env))
        self.assertIn("tool-checkpoint", hook_profile_gate.disabled_hooks(env))
        self.assertFalse(hook_profile_gate.is_hook_enabled("web-search-first", env=env))
        self.assertFalse(hook_profile_gate.is_hook_enabled("tool-checkpoint", env=env))
        self.assertTrue(hook_profile_gate.is_hook_enabled("session-start", env=env))

    def test_visibility_context_reads_pending_merge_manifest(self):
        with tempfile.TemporaryDirectory() as temp_home:
            manifest = Path(temp_home) / ".ghost-alice" / "pending-merges" / "codex" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({"entries": [{"id": "one", "decided": False}]}) + "\n",
                encoding="utf-8",
            )

            context = hook_profile_gate._visibility_context(
                "prompt",
                "routine clean pass already persisted",
                "",
                0,
                env={"HOME": temp_home, "GHOST_ALICE_PLATFORM": "codex"},
                hook_payload={},
            )

        self.assertTrue(context["pending_merge_undecided"])

    def test_visibility_context_reads_current_downstream_block_gate(self):
        with tempfile.TemporaryDirectory() as temp_home, tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            session_dir = root / "codex" / "s-block"
            session_dir.mkdir(parents=True)
            (root / "codex" / "current-session.json").write_text(
                json.dumps({
                    "schema_version": "session-intent-current.v1",
                    "platform": "codex",
                    "session_id": "s-block",
                    "state_path": str(session_dir / "intent-state.json"),
                })
                + "\n",
                encoding="utf-8",
            )
            (session_dir / "intent-events.jsonl").write_text(
                json.dumps({
                    "event": "user-input-observed",
                    "event_id": "evt-current",
                    "input_digest": "sha256:current",
                })
                + "\n",
                encoding="utf-8",
            )
            (session_dir / "downstream-gates.json").write_text(
                json.dumps({
                    "schema_version": "downstream-gates.v1",
                    "platform": "codex",
                    "session_id": "s-block",
                    "gate": "jailbreak-detector",
                    "decision": "block",
                    "opened": False,
                    "input_event_id": "evt-current",
                    "input_digest": "sha256:current",
                })
                + "\n",
                encoding="utf-8",
            )

            context = hook_profile_gate._visibility_context(
                "prompt",
                "routine clean pass already persisted",
                "",
                0,
                env={
                    "HOME": temp_home,
                    "GHOST_ALICE_PLATFORM": "codex",
                    "GHOST_ALICE_SESSION_INTENT_ROOT": str(root),
                },
                hook_payload={},
            )

        self.assertTrue(context["security_boundary"])

    def test_visibility_context_ignores_stale_downstream_block_gate(self):
        with tempfile.TemporaryDirectory() as temp_home, tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            session_dir = root / "codex" / "s-stale"
            session_dir.mkdir(parents=True)
            (root / "codex" / "current-session.json").write_text(
                json.dumps({
                    "schema_version": "session-intent-current.v1",
                    "platform": "codex",
                    "session_id": "s-stale",
                    "state_path": str(session_dir / "intent-state.json"),
                })
                + "\n",
                encoding="utf-8",
            )
            (session_dir / "intent-events.jsonl").write_text(
                json.dumps({
                    "event": "user-input-observed",
                    "event_id": "evt-new",
                    "input_digest": "sha256:new",
                })
                + "\n",
                encoding="utf-8",
            )
            (session_dir / "downstream-gates.json").write_text(
                json.dumps({
                    "schema_version": "downstream-gates.v1",
                    "platform": "codex",
                    "session_id": "s-stale",
                    "gate": "jailbreak-detector",
                    "decision": "block",
                    "opened": False,
                    "input_event_id": "evt-old",
                    "input_digest": "sha256:old",
                })
                + "\n",
                encoding="utf-8",
            )

            context = hook_profile_gate._visibility_context(
                "prompt",
                "routine clean pass already persisted",
                "",
                0,
                env={
                    "HOME": temp_home,
                    "GHOST_ALICE_PLATFORM": "codex",
                    "GHOST_ALICE_SESSION_INTENT_ROOT": str(root),
                },
                hook_payload={},
            )

        self.assertNotIn("security_boundary", context)

    def test_visibility_context_carries_routing_surface_from_payload(self):
        surface = {
            "intent_relation": "changed",
            "change_depth": "localized",
            "focus_layer": "meso",
            "verification_complexity": "level-2",
            "boundary_contract": "n/a",
            "forced_visibility": "no",
        }

        context = hook_profile_gate._visibility_context(
            "prompt",
            "routine clean pass already persisted",
            "",
            0,
            env={"HOME": "/tmp/ghost-alice-home", "GHOST_ALICE_PLATFORM": "codex"},
            hook_payload={"routing_surface": surface},
        )

        self.assertEqual(context["routing_surface"], surface)

    def test_classify_surface_item_projects_routine_and_forced_values(self):
        routine = hook_profile_gate.classify_surface_item(
            value_key="merge-precheck",
            value_kind="routine",
            exposure_class="routine",
            profile="dynamic",
            strict_log_ref="strict-log#1",
            source_hook="prompt",
            value="routine clean pass already persisted",
        )
        forced = hook_profile_gate.classify_surface_item(
            value_key="downstream-block",
            value_kind="risk",
            exposure_class="forced",
            profile="minimal",
            strict_log_ref="strict-log#2",
            source_hook="tool-checkpoint",
            value="decision=block",
        )

        self.assertEqual(routine["user_surface"], "hidden")
        self.assertEqual(routine["model_surface"], "omitted")
        self.assertEqual(routine["work_impact"], "routine-noise")
        self.assertEqual(routine["strict_log_ref"], "strict-log#1")
        self.assertEqual((forced["user_surface"], forced["model_surface"]), ("forced", "full"))
        self.assertEqual(forced["work_impact"], "interrupts-work")


class TestHookCommandAllowlist(unittest.TestCase):
    def test_allows_system_and_homebrew_binaries(self):
        if os.name == "nt":
            self.skipTest("POSIX absolute executable allowlist does not apply on Windows")
        hook_profile_gate.assert_allowed_command(["/bin/bash", "-lc", "printf ok"], ["/bin", "/usr/bin"])
        hook_profile_gate.assert_allowed_command(["/opt/homebrew/bin/python3"], ["/opt/homebrew"])

    def test_allows_versioned_python_bare_command_without_minor_pin(self):
        hook_profile_gate.assert_allowed_command(["python3.15", "-V"], ["/bin", "/usr/bin"])

    def test_rejects_malformed_versioned_python_bare_command(self):
        with self.assertRaises(hook_profile_gate.HookCommandRejected):
            hook_profile_gate.assert_allowed_command(["python3.evil"], ["/bin", "/usr/bin"])

    def test_rejects_path_traversal_and_arbitrary_executable(self):
        with self.assertRaises(hook_profile_gate.HookCommandRejected):
            hook_profile_gate.assert_allowed_command(["../tmp/evil"], ["/bin", "/usr/bin"])

        with self.assertRaises(hook_profile_gate.HookCommandRejected):
            hook_profile_gate.assert_allowed_command(["/tmp/evil"], ["/bin", "/usr/bin"])

    def test_validate_shell_command_resolves_managed_python_sentinel(self):
        argv = hook_profile_gate._validate_shell_command(
            f"{hook_profile_gate.HOOK_PYTHON_SENTINEL} -c 'import sys; sys.exit(0)'"
        )

        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1:], ["-c", "import sys; sys.exit(0)"])

    def test_cli_rejects_shell_injection_payload(self):
        payload = base64.urlsafe_b64encode(b"/bin/bash -lc 'printf ok'; /tmp/evil").decode("ascii")
        env = os.environ.copy()
        env["GHOST_ALICE_AGENT_VISIBILITY"] = "strict"

        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run", "prompt", "strict", payload],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rejected", result.stderr.lower())

    def test_cli_accepts_current_runner_shape_without_visibility_csv(self):
        executable = sys.executable.replace("\\", "/")
        payload = base64.urlsafe_b64encode(f"{executable} -c 'import sys; sys.exit(0)'".encode("utf-8")).decode("ascii")

        with self.assertRaises(SystemExit) as cm:
            hook_profile_gate.main(["run", "prompt", payload])

        self.assertEqual(cm.exception.code, 0)

    def test_cli_keeps_legacy_runner_shape_for_installed_wrappers(self):
        executable = sys.executable.replace("\\", "/")
        payload = base64.urlsafe_b64encode(f"{executable} -c 'import sys; sys.exit(0)'".encode("utf-8")).decode("ascii")

        with self.assertRaises(SystemExit) as cm:
            hook_profile_gate.main(["run", "prompt", "strict,dynamic,minimal", payload])

        self.assertEqual(cm.exception.code, 0)

    def test_runner_hides_clean_pass_after_strict_log_append(self):
        message = "No pending warning from this hook means merge-companion-precheck is clean."
        code = f"print({message!r})"
        payload = base64.urlsafe_b64encode(_python_payload_command(f"-c {shlex.quote(code)}").encode("utf-8")).decode("ascii")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "codex"
            env["GHOST_ALICE_SESSION_ID"] = "s-hidden"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"

            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run", "prompt", payload],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "codex"
                / "s-hidden"
                / "strict-hook-output.jsonl"
            )
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(row["stdout"].strip(), message)
        self.assertEqual(row["visible_decision"], "hide")
        self.assertIsInstance(row["observed_duration_s"], float)
        self.assertGreaterEqual(row["observed_duration_s"], 0.0)
        self.assertEqual(row["observed_duration_source"], "hook-runner")
        self.assertNotIn("reasoning_duration_s", row)

    def test_runner_records_work_impact_and_omits_model_output_for_hidden_routine(self):
        message = "routine clean pass already persisted"
        code = f"print({message!r})"
        payload = base64.urlsafe_b64encode(_python_payload_command(f"-c {shlex.quote(code)}").encode("utf-8")).decode("ascii")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "codex"
            env["GHOST_ALICE_SESSION_ID"] = "s-model-surface"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "dynamic"

            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run", "prompt", payload],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "codex"
                / "s-model-surface"
                / "strict-hook-output.jsonl"
            )
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(row["stdout"].strip(), message)
        self.assertEqual(row["surface_item"]["value_key"], "merge-precheck")
        self.assertEqual(row["surface_item"]["user_surface"], "hidden")
        self.assertEqual(row["surface_item"]["model_surface"], "omitted")
        self.assertEqual(row["surface_item"]["work_impact"], "routine-noise")
        self.assertEqual(row["model_surface_output"], "")

    def test_runner_materializes_routing_surface_compact_output_from_payload(self):
        message = "routine clean pass already persisted"
        code = f"print({message!r})"
        payload = base64.urlsafe_b64encode(_python_payload_command(f"-c {shlex.quote(code)}").encode("utf-8")).decode("ascii")
        hook_payload = {
            "session_id": "s-routing-surface",
            "hook_event_name": "UserPromptSubmit",
            "routing_surface": {
                "intent_relation": "changed",
                "change_depth": "localized",
                "focus_layer": "meso",
                "verification_complexity": "level-2",
                "boundary_contract": "n/a",
                "forced_visibility": "no",
            },
        }

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "codex"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"

            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run", "prompt", payload],
                input=json.dumps(hook_payload),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "codex"
                / "s-routing-surface"
                / "strict-hook-output.jsonl"
            )
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "routing-surface observed\n")
        self.assertEqual(row["stdout"].strip(), message)
        self.assertEqual(row["visible_decision"], "show")
        self.assertEqual(row["surface_item"]["value_key"], "routing-surface")
        self.assertEqual(row["surface_item"]["user_surface"], "compact")
        self.assertEqual(row["surface_item"]["model_surface"], "digest")
        self.assertEqual(row["user_surface_output"], "routing-surface observed\n")

    def test_render_user_surface_materializes_compact_and_focused(self):
        for surface, expected in (
            ("compact", "routing-surface observed\n"),
            ("focused", "routing-surface: compact summary\n"),
        ):
            with self.subTest(surface=surface):
                user_stdout, user_stderr = hook_profile_gate._render_user_surface(
                    {
                        "user_surface": surface,
                        "value_key": "routing-surface",
                        "value": "compact summary",
                    },
                    "RAW HOOK OUTPUT\nsecond line\n",
                    "",
                )

            self.assertEqual(user_stdout, expected)
            self.assertEqual(user_stderr, "")

    def test_runner_emits_forced_action_denial_after_strict_log_append(self):
        message = '{"decision":"deny","reason":"[tool-checkpoint] required"}'
        code = f"print({message!r})"
        payload = base64.urlsafe_b64encode(_python_payload_command(f"-c {shlex.quote(code)}").encode("utf-8")).decode("ascii")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "codex"
            env["GHOST_ALICE_SESSION_ID"] = "s-forced"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"

            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run", "tool-checkpoint", payload],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "codex"
                / "s-forced"
                / "strict-hook-output.jsonl"
            )
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), message)
        self.assertEqual(row["stdout"].strip(), message)
        self.assertEqual(row["visible_decision"], "force_show")

    def test_runner_force_shows_pending_manifest_after_strict_log_append(self):
        message = "routine clean pass already persisted"
        code = f"print({message!r})"
        payload = base64.urlsafe_b64encode(_python_payload_command(f"-c {shlex.quote(code)}").encode("utf-8")).decode("ascii")

        with tempfile.TemporaryDirectory() as temp_home:
            manifest = Path(temp_home) / ".ghost-alice" / "pending-merges" / "codex" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({"entries": [{"id": "pending", "decided": False}]}) + "\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "codex"
            env["GHOST_ALICE_SESSION_ID"] = "s-pending"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"

            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run", "prompt", payload],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "codex"
                / "s-pending"
                / "strict-hook-output.jsonl"
            )
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), message)
        self.assertEqual(row["visible_decision"], "force_show")
        self.assertEqual(row["stdout"].strip(), message)

    def test_runner_preserves_nonzero_exit_after_strict_log_append(self):
        command = _python_payload_command("-c 'import sys; print(\"bad\", file=sys.stderr); sys.exit(7)'")
        payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "codex"
            env["GHOST_ALICE_SESSION_ID"] = "s-nonzero"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "dynamic"

            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run", "completion", payload],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "codex"
                / "s-nonzero"
                / "strict-hook-output.jsonl"
            )
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(result.returncode, 7)
        self.assertEqual(row["stderr"].strip(), "bad")
        self.assertEqual(row["exit_code"], 7)
        self.assertEqual(row["visible_decision"], "force_show")

    def test_runner_logs_surrogate_stdin_without_crashing(self):
        command = _python_payload_command("-c 'import sys; sys.exit(0)'")
        payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "claude"
            env["GHOST_ALICE_SESSION_ID"] = "s-win"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "strict"

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(sys, "stdin", io.StringIO("payload with bad surrogate \udcec")),
            ):
                return_code = hook_profile_gate.run("completion", payload)

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "claude"
                / "s-win"
                / "strict-hook-output.jsonl"
            )
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(return_code, 0)
        self.assertRegex(row["payload_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(row["hook_id"], "completion")


class TestInstallHooksRunnerIntegration(unittest.TestCase):
    def test_generated_hook_commands_are_wrapped_with_hook_runner(self):
        pending_entry = install_hooks._platform_prompt_pending_merge_entry("claude", "UserPromptSubmit")
        prompt_entry = install_hooks._platform_hook_entry("claude", "UserPromptSubmit")
        session_entry = install_hooks._platform_session_start_entry("claude", "SessionStart")

        pending_command = install_hooks._entry_command(pending_entry)
        prompt_command = install_hooks._entry_command(prompt_entry)
        session_command = install_hooks._entry_command(session_entry)

        self.assertIn("hook_profile_gate.py", pending_command)
        self.assertIn("[hook-runner:pending-merge-prompt]", pending_command)
        self.assertNotIn('"strict,dynamic,minimal"', pending_command)
        self.assertIn(install_hooks.PROMPT_PENDING_MERGE_MARKER, pending_command)
        self.assertIn("hook_profile_gate.py", prompt_command)
        self.assertIn("[hook-runner:prompt]", prompt_command)
        self.assertNotIn('"strict,dynamic,minimal"', prompt_command)
        self.assertIn(install_hooks.HOOK_MARKER, prompt_command)
        self.assertIn("hook_profile_gate.py", session_command)
        self.assertIn("[hook-runner:session-start]", session_command)
        self.assertNotIn('"strict,dynamic,minimal"', session_command)
        self.assertIn(install_hooks.SESSION_START_MARKER, session_command)

    def test_installed_minimal_visibility_runs_prompt_and_session_start(self):
        if os.name == "nt":
            self.skipTest("POSIX shell launcher test does not apply on Windows")
        pending_entry = install_hooks._platform_prompt_pending_merge_entry("claude", "UserPromptSubmit")
        prompt_entry = install_hooks._platform_hook_entry("claude", "UserPromptSubmit")
        session_entry = install_hooks._platform_session_start_entry("claude", "SessionStart")

        env = os.environ.copy()
        env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"

        pending = subprocess.run(
            ["/bin/bash", "-lc", install_hooks._entry_command(pending_entry)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        prompt = subprocess.run(
            ["/bin/bash", "-lc", install_hooks._entry_command(prompt_entry)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        session_start = subprocess.run(
            ["/bin/bash", "-lc", install_hooks._entry_command(session_entry)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )

        self.assertEqual(pending.returncode, 0)
        pending_payload = json.loads(pending.stdout)
        self.assertTrue(pending_payload["continue"])
        self.assertIn("merge-companion prompt-check", pending_payload["systemMessage"])
        self.assertIn("merge-companion-precheck: clean (hook-verified)", pending_payload["systemMessage"])
        self.assertEqual(prompt.returncode, 0)
        self.assertIn("task-router", prompt.stdout)
        self.assertEqual(session_start.returncode, 0)
        self.assertIn("merge-companion", session_start.stdout)


if __name__ == "__main__":
    unittest.main()
