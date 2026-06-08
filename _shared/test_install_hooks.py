#!/usr/bin/env python3
"""
test_install_hooks.py - unit tests for install_hooks.py

Covered behavior:
  - install: add hooks to empty settings
  - idempotency: skip when the same hook already exists
  - uninstall: remove exactly marker-managed hooks
  - empty uninstall: return not_found when no hooks exist
  - status: return installed / missing accurately
  - preserve existing settings keys
  - preserve other hooks without managed markers
  - skip uninstalled frameworks when directories are absent
  - recover corrupt JSON by backing up and starting from empty settings
  - dry-run: leave files unchanged

Usage:

  python test_install_hooks.py              # Run unittest directly
"""

import base64
import concurrent.futures
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hook_profile_gate
import install_hooks


def _run_hook_command(
    command: str,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    hook_env = env
    if hook_env is None:
        hook_env = os.environ.copy()
        hook_env["GHOST_ALICE_AGENT_VISIBILITY"] = "strict"
    candidates = [
        shutil.which("bash"),
        shutil.which("bash.exe"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    bash = None
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        normalized = path.as_posix().lower()
        if normalized.endswith("/windows/system32/bash.exe") or normalized.endswith("/appdata/local/microsoft/windowsapps/bash.exe"):
            continue
        probe = subprocess.run(
            [str(path), "-lc", "printf '%s\\n' ok"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            bash = str(path)
            break
    if not bash:
        raise unittest.SkipTest("A working Git Bash/POSIX shell is required to execute generated hook commands")
    return subprocess.run(
        [bash, "-lc", command],
        input=input_text,
        env=hook_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _run_hook_command_via_cmd(
    command: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    if os.name != "nt":
        raise unittest.SkipTest("cmd.exe is only available on Windows")
    return subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


def _strict_hook_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["GHOST_ALICE_AGENT_VISIBILITY"] = "strict"
    if extra:
        env.update(extra)
    return env


def _write_session_intent_preflight(
    home: str | Path,
    platform: str,
    session_id: str,
    *,
    semantic_delta: bool = True,
    event_id: str = "evt-current",
    input_digest: str = "sha256:test",
    input_char_count: int = 4,
    session_intent_root: str | Path | None = None,
) -> None:
    root = Path(session_intent_root) if session_intent_root is not None else Path(home) / ".ghost-alice" / "session-intent"
    session_dir = root / platform / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    state_path = session_dir / "intent-state.json"
    current_goal = "semantic intent recorded" if semantic_delta else ""
    user_intent_summary = "agent semantic delta recorded" if semantic_delta else ""
    acceptance_criteria = [
        {
            "id": "test-semantic-delta",
            "summary": "semantic delta is recorded for this turn",
            "source": "inferred",
        }
    ] if semantic_delta else []
    state_path.write_text(
        json.dumps({
            "schema_version": "session-intent-ledger.v1",
            "platform": platform,
            "session_id": session_id,
            "current_goal": current_goal,
            "user_intent_summary": user_intent_summary,
            "constraints": [],
            "non_goals": [],
            "open_questions": [],
            "acceptance_criteria": acceptance_criteria,
            "decisions": [],
            "risk_flags": [],
            "consumer_hints": {},
            "intake_status": "observed",
            "last_semantic_delta_status": "provided" if semantic_delta else "not-provided",
        }, indent=2)
        + "\n",
        encoding="utf-8",
    )
    events_path = session_dir / "intent-events.jsonl"
    events_path.write_text(
        json.dumps({
            "ts": "2026-01-01T00:00:00Z",
            "event": "user-input-observed",
            "platform": platform,
            "session_id": session_id,
            "source": "hook",
            "event_id": event_id,
            "input_digest": input_digest,
            "input_char_count": input_char_count,
        })
        + "\n",
        encoding="utf-8",
    )
    if semantic_delta:
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({
                    "ts": "2026-01-01T00:00:01Z",
                    "event": "semantic-delta-recorded",
                    "platform": platform,
                    "session_id": session_id,
                    "source": "agent",
                    "intent_delta_digest": "sha256:semantic-delta",
                    "delta_keys": ["current_goal", "user_intent_summary"],
                })
                + "\n"
            )
    pointer_path = root / platform / "current-session.json"
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(
        json.dumps({
            "schema_version": "session-intent-current.v1",
            "platform": platform,
            "session_id": session_id,
            "state_path": str(state_path),
            "updated_at": "2026-01-01T00:00:00Z",
        }, indent=2)
        + "\n",
        encoding="utf-8",
    )


def _write_session_intent_digest_only_preflight(
    home: str | Path,
    platform: str,
    session_id: str,
    **kwargs: Any,
) -> None:
    _write_session_intent_preflight(home, platform, session_id, semantic_delta=False, **kwargs)


def _write_downstream_gate(
    home: str | Path,
    platform: str,
    session_id: str,
    decision: str,
    *,
    event_id: str = "evt-current",
    input_digest: str = "sha256:test",
    session_intent_root: str | Path | None = None,
) -> None:
    root = Path(session_intent_root) if session_intent_root is not None else Path(home) / ".ghost-alice" / "session-intent"
    session_dir = root / platform / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir.joinpath("downstream-gates.json").write_text(
        json.dumps({
            "schema_version": "downstream-gates.v1",
            "platform": platform,
            "session_id": session_id,
            "gate": "jailbreak-detector",
            "decision": decision,
            "opened": decision == "allow",
            "input_event_id": event_id,
            "input_digest": input_digest,
            "rules": ["instruction-hierarchy-override"] if decision == "block" else [],
            "state_goal_digest": "sha256:test",
            "evidence_summary": "test gate state",
            "updated_at": "2026-01-01T00:00:00Z",
        }, indent=2)
        + "\n",
        encoding="utf-8",
    )


def _minimal_visibility_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"
    if extra:
        env.update(extra)
    return env


def _assert_contains_any(testcase: unittest.TestCase, text: str, *terms: str) -> None:
    testcase.assertTrue(
        any(term in text for term in terms),
        f"expected one of {terms!r} in {text!r}",
    )


def _visible_and_runner_payload_text(command: str) -> str:
    """Return visible command text plus one decoded hook_profile_gate payload."""
    texts = [command]
    try:
        parts = shlex.split(command)
    except ValueError:
        return "\n".join(texts)
    if "run" in parts:
        run_index = parts.index("run")
        payload_indexes = (run_index + 2, run_index + 3)
        for payload_index in payload_indexes:
            if len(parts) <= payload_index:
                continue
            try:
                decoded = base64.urlsafe_b64decode(parts[payload_index].encode("ascii")).decode("utf-8")
            except Exception:
                continue
            if decoded:
                texts.append(decoded)
                break
    return "\n".join(texts)


VERSIONED_HOMEBREW_PYTHON = "/opt/homebrew/opt/python@3.14/bin/python3.14"
VERSIONED_HOMEBREW_PYTHON_RE = re.compile(
    r"/opt/homebrew/(?:opt/python@\d+\.\d+/bin/python\d+\.\d+|bin/python\d+\.\d+)"
)


def _expected_ghost_alice_skill_names() -> list[str]:
    names = install_hooks._ghost_alice_installed_skill_names()
    if not names:
        raise AssertionError("Ghost-ALICE skill catalog discovery returned no skills")
    return names


class TestHookProfileGateWindowsCommandCompatibility(unittest.TestCase):
    def test_wrapped_command_validation_strips_marker_comment(self):
        command = f'"{sys.executable}" -c "import sys; sys.exit(0)" # [hook-reminder] AGENTS.md'

        argv = hook_profile_gate._validate_shell_command(command)

        self.assertNotIn("#", argv)
        self.assertNotIn("[hook-reminder]", argv)
        self.assertNotIn("AGENTS.md", argv)

    def test_main_ignores_outer_marker_args_from_windows_shell(self):
        command = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
        payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")

        with self.assertRaises(SystemExit) as cm:
            hook_profile_gate.main([
                "run",
                "prompt",
                payload,
                "#",
                "[hook-reminder]",
                "AGENTS.md",
            ])

        self.assertEqual(cm.exception.code, 0)


class TestMessageLanguage(unittest.TestCase):
    def assert_no_pretool_deny(self, payload: dict[str, object]) -> None:
        output = payload.get("hookSpecificOutput", {})
        if isinstance(output, dict):
            self.assertNotEqual(output.get("permissionDecision"), "deny")
            self.assertNotIn("permissionDecision", output)
        self.assertNotEqual(payload.get("decision"), "deny")

    def run_codex_pretool(
        self,
        request: dict[str, object],
        *,
        downstream_decision: str = "allow",
    ) -> subprocess.CompletedProcess:
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            session_id = str(request.get("session_id") or "s-codex-pretool")
            env = _strict_hook_env({"HOME": temp_home})
            _write_session_intent_preflight(temp_home, "codex", session_id)
            _write_downstream_gate(temp_home, "codex", session_id, downstream_decision)
            return subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

    def test_codex_dispatcher_session_intent_ignores_no_prompt_payload(self):
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home) / "session-intent"
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "BeforeAgent",
                    "--hook",
                    "session-intent",
                    "--session-intent-root",
                    str(root),
                ],
                input=json.dumps({"sessionId": "s-empty-payload"}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_strict_hook_env({"HOME": temp_home}),
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["continue"], True)
            session_dir = root / "codex" / "s-empty-payload"
            self.assertFalse((session_dir / "intent-events.jsonl").exists())
            self.assertFalse((session_dir / "intent-state.json").exists())
            self.assertFalse((root / "codex" / "current-session.json").exists())

    def test_dispatcher_does_not_own_session_intent_or_io_trace_writers(self):
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        source = dispatcher.read_text(encoding="utf-8")

        self.assertNotIn("function recordSessionIntent", source)
        self.assertNotIn("recordSessionIntent(", source)
        self.assertNotIn("function recordIoTrace", source)
        self.assertNotIn("recordIoTrace(", source)
        self.assertNotIn("ioTraceLogPath", source)

    def test_t_returns_english(self):
        self.assertEqual(install_hooks._t("Localized", "English"), "English")

    def test_generated_hook_messages_use_polite_or_neutral_tone(self):
        bad_terms = [
            "call-do-it",
            "check-do-it",
            "verify-do-it",
            "do-not-miss-it",
            "input-do-it",
            "send-it",
        ]
        commands = [
            install_hooks.HOOK_COMMAND,
            install_hooks.SESSION_INTENT_COMMAND,
            install_hooks.STOP_HOOK_COMMAND,
            install_hooks.SESSION_START_COMMAND,
        ]

        for command in commands:
            result = _run_hook_command(command)
            self.assertEqual(result.returncode, 0)
            for term in bad_terms:
                with self.subTest(term=term):
                    self.assertNotIn(term, result.stdout)
        result = _run_hook_command(
            install_hooks.WEB_SEARCH_FIRST_COMMAND,
            env=_strict_hook_env(),
        )
        self.assertEqual(result.returncode, 0)
        for term in bad_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, result.stdout)

    def test_generated_hook_messages_use_bridge_labels(self):
        for command, env in (
            (install_hooks.HOOK_COMMAND, None),
            (install_hooks.SESSION_INTENT_COMMAND, None),
            (install_hooks.WEB_SEARCH_FIRST_COMMAND, _strict_hook_env()),
            (install_hooks.SESSION_START_COMMAND, None),
        ):
            result = _run_hook_command(command, env=env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("Internal instruction:", result.stdout)
            self.assertIn("User:", result.stdout)
            self.assertIn("Tech:", result.stdout)
            self.assertNotIn("legacy non-developer label", result.stdout)
            self.assertNotIn("legacy developer label", result.stdout)
            self.assertNotIn("Non-developer note", result.stdout)
            self.assertNotIn("Developer note", result.stdout)

    def test_session_intent_hook_command_uses_repo_tmp_root(self):
        command = install_hooks._session_intent_analyzer_command(platform="codex", output_format="json")
        parts = shlex.split(command)

        self.assertIn("--root", parts)
        root = Path(parts[parts.index("--root") + 1])
        self.assertEqual(root, install_hooks._repo_root_from_this_file() / ".tmp" / "session-intent")

    def test_claude_codex_tool_checkpoint_entries_use_dispatcher(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                command = _visible_and_runner_payload_text(
                    install_hooks._entry_command(
                        install_hooks._platform_tool_checkpoint_entry(platform, "PreToolUse")
                    )
                )
                self.assertIn("ghost-alice-hook.mjs", command)
                self.assertIn("--event PreToolUse", command)
                self.assertIn(f"--platform {platform}", command)

    def test_codex_tool_checkpoint_legacy_json_constants_removed(self):
        self.assertFalse(hasattr(install_hooks, "TOOL_CHECKPOINT_COMMAND_CODEX"))
        self.assertFalse(hasattr(install_hooks, "TOOL_CHECKPOINT_ENTRY_CODEX"))

    def test_hook_reminder_treats_questions_and_opinions_as_user_input(self):
        self.assertIn("simple question", install_hooks.HOOK_INTERNAL)
        self.assertIn("opinion", install_hooks.HOOK_INTERNAL)
        self.assertIn("user input", install_hooks.HOOK_INTERNAL)
        self.assertIn("task-router", install_hooks.HOOK_INTERNAL)
        self.assertIn("task-router waits until session-intent preflight exists", install_hooks.HOOK_INTERNAL)
        self.assertIn("Absent downstream-gates.json means silent allow", install_hooks.HOOK_INTERNAL)
        self.assertIn("atomic meaning units", install_hooks.HOOK_INTERNAL)
        self.assertIn("focus-layer", install_hooks.HOOK_INTERNAL)
        self.assertIn("scope-reopen", install_hooks.HOOK_INTERNAL)
        self.assertIn("performs routing decisions", install_hooks.HOOK_INTERNAL)
        self.assertIn("does not infer raw user intent", install_hooks.HOOK_INTERNAL)
        self.assertIn("task-router consumes session-intent and jailbreak gate context", install_hooks.HOOK_INTERNAL)
        self.assertLessEqual(len(install_hooks.HOOK_INTERNAL), 900)
        self.assertNotIn("[tool-checkpoint]", install_hooks.HOOK_INTERNAL)
        self.assertNotIn("hook-stage: PreToolUse", install_hooks.HOOK_INTERNAL)
        self.assertNotIn("Before the first tool call", install_hooks.HOOK_INTERNAL)
        self.assertNotIn("run task-router first", install_hooks.HOOK_INTERNAL)

    def test_session_intent_reminder_has_conduct_feedback_capture_trigger(self):
        self.assertIn("conduct_feedback", install_hooks.SESSION_INTENT_INTERNAL)
        self.assertIn("Capture trigger", install_hooks.SESSION_INTENT_INTERNAL)
        self.assertIn("session_intent_ledger.py", install_hooks.SESSION_INTENT_INTERNAL)
        # the correction judgment must name its evidentiary basis, not be a vibe
        self.assertIn("Basis for the correction judgment", install_hooks.SESSION_INTENT_INTERNAL)
        self.assertIn("acceptance_criteria", install_hooks.SESSION_INTENT_INTERNAL)

    def test_codex_hook_reminder_command_uses_task_router_gate_helper(self):
        command = _visible_and_runner_payload_text(
            install_hooks._entry_command(
                install_hooks._platform_hook_entry("codex", "UserPromptSubmit")
            )
        )

        self.assertIn("task_router_reminder_hook.py", command)
        self.assertIn("--root", command)
        self.assertIn(
            str(install_hooks._repo_root_from_this_file() / ".tmp" / "session-intent").replace("\\", "/"),
            command.replace("\\", "/"),
        )

    def test_codex_task_router_reminder_releases_after_intent_preflight_and_ignores_legacy_allow_gate(self):
        script = Path(install_hooks.__file__).with_name("task_router_reminder_hook.py")
        self.assertTrue(script.exists(), f"missing reminder helper: {script}")

        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home) / ".ghost-alice" / "session-intent"
            _write_session_intent_preflight(temp_home, "codex", "s-codex-router")

            first = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--platform",
                    "codex",
                    "--format",
                    "json",
                    "--root",
                    str(root),
                ],
                input=json.dumps({"session_id": "s-codex-router"}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(first.returncode, 0, msg=first.stderr)
            first_payload = json.loads(first.stdout)
            first_message = first_payload["systemMessage"]
            self.assertIn("gate-opened: jailbreak-detector silent allow", first_message)
            self.assertIn("task-router-step", first_message)
            self.assertNotIn("task-router withheld", first_message)

            _write_downstream_gate(temp_home, "codex", "s-codex-router", "allow")
            second = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--platform",
                    "codex",
                    "--format",
                    "json",
                    "--root",
                    str(root),
                ],
                input=json.dumps({"session_id": "s-codex-router"}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(second.returncode, 0, msg=second.stderr)
            second_payload = json.loads(second.stdout)
            message = second_payload["systemMessage"]
            self.assertIn("gate-opened: jailbreak-detector silent allow", message)
            self.assertNotIn("decision=allow", message)
            self.assertIn("intent-ledger: read", message)
            self.assertIn("task-router-step", message)
            self.assertIn("atomic meaning decomposition", message)
            self.assertIn("focus-layer/scope-reopen", message)
            self.assertIn("skill assignment", message)

    def test_codex_task_router_reminder_releases_on_absent_gate_after_intent_preflight(self):
        script = Path(install_hooks.__file__).with_name("task_router_reminder_hook.py")
        self.assertTrue(script.exists(), f"missing reminder helper: {script}")

        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home) / ".ghost-alice" / "session-intent"
            _write_session_intent_preflight(temp_home, "codex", "s-codex-silent-allow")

            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--platform",
                    "codex",
                    "--format",
                    "json",
                    "--root",
                    str(root),
                ],
                input=json.dumps({"session_id": "s-codex-silent-allow"}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            message = payload["systemMessage"]
            self.assertIn("gate-opened: jailbreak-detector silent allow", message)
            self.assertIn("task-router-step", message)
            self.assertNotIn("task-router withheld", message)

    def test_completion_reminder_requests_observed_timing_when_available(self):
        self.assertIn("[observed-timing]", install_hooks.STOP_HOOK_INTERNAL)
        self.assertIn("observable durations only", install_hooks.STOP_HOOK_INTERNAL)
        self.assertIn("when available", install_hooks.STOP_HOOK_INTERNAL.lower())
        self.assertIn("Do not infer hidden reasoning time", install_hooks.STOP_HOOK_INTERNAL)
        self.assertIn("unavailable", install_hooks.STOP_HOOK_INTERNAL)

    def test_claude_stop_hook_blocks_without_actual_verification_skill_load(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text(
                "\n".join([
                    json.dumps({
                        "type": "user",
                        "message": {"role": "user", "content": "Tell me the current status."},
                    }),
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{
                                "type": "text",
                                "text": "[completion-check]\n- verification-before-completion: done\n- skill-call: verification-before-completion (this turn)",
                            }],
                        },
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            result = _run_hook_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn('"skill": "verification-before-completion"', payload["reason"])
        self.assertIn("Do not ask the user", payload["reason"])
        self.assertIn("always-on completion lifecycle gate", payload["reason"])

    def test_claude_stop_hook_retry_reason_requires_standalone_final_answer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text(
                "\n".join([
                    json.dumps({
                        "type": "user",
                        "message": {"role": "user", "content": "Summarize two papers."},
                    }),
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{
                                "type": "text",
                                "text": (
                                    "Paper A summary.\n\n"
                                    "[completion-check]\n"
                                    "- verification-before-completion: done\n"
                                    "- skill-call: verification-before-completion (this turn)\n"
                                    "- acceptance-criteria:\n"
                                    "  - c1: cite two papers [source: user-explicit]\n"
                                    "- claim-evidence-map:\n"
                                    "  - claim: two papers cited\n"
                                    "    criterion: c1\n"
                                    "    evidence: search result\n"
                                    "    verdict: pass\n"
                                    "- unverified:\n"
                                    "  - none\n"
                                    "- evidence: search result\n"
                                    "[io-trace]\n"
                                    "- web-accessed: [query]\n"
                                    "- skills-loaded: [verification-before-completion]"
                                ),
                            }],
                        },
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            result = _run_hook_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("complete standalone final answer", payload["reason"])
        self.assertIn("Do not refer to a previous answer", payload["reason"])
        self.assertIn("Begin with the user's requested answer", payload["reason"])
        self.assertIn("Do not begin with verification process notes", payload["reason"])

    def test_claude_stop_hook_allows_actual_verification_skill_load(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text(
                "\n".join([
                    json.dumps({
                        "type": "user",
                        "message": {"role": "user", "content": "Tell me the current status."},
                    }),
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{
                                "type": "tool_use",
                                "name": "Skill",
                                "input": {"skill": "verification-before-completion"},
                            }],
                        },
                    }),
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{
                                "type": "text",
                                "text": (
                                    "[completion-check]\n"
                                    "- verification-before-completion: done\n"
                                    "- skill-call: verification-before-completion (this turn)\n"
                                    "- acceptance-criteria:\n"
                                    "  - crit-a: aligned work [source: system-doc]\n"
                                    "- claim-evidence-map:\n"
                                    "  - claim: did the thing\n"
                                    "    criterion: crit-a\n"
                                    "    evidence: ran cmd 3/3 OK\n"
                                    "    verdict: pass\n"
                                    "- unverified:\n"
                                    "  - none\n"
                                    "- evidence: ran cmd\n"
                                    "[io-trace]\n"
                                    "- skills-loaded: [verification-before-completion]"
                                ),
                            }],
                        },
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            result = _run_hook_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("continue"), True)
        self.assertNotIn("decision", payload)

    def test_claude_stop_hook_blocks_completion_claim_with_invalid_body(self):
        # Completion-body-validation invariant: verification skill loaded, but the [completion-check] body is
        # incomplete (no [io-trace]/acceptance-criteria/claim-evidence-map).
        # The strengthened Claude Stop gate must block, matching the .mjs validator.
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text(
                "\n".join([
                    json.dumps({
                        "type": "user",
                        "message": {"role": "user", "content": "Tell me the current status."},
                    }),
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{
                                "type": "tool_use",
                                "name": "Skill",
                                "input": {"skill": "verification-before-completion"},
                            }],
                        },
                    }),
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{
                                "type": "text",
                                "text": "Work complete.\n[completion-check]\n- verification-before-completion: done\n- skill-call: verification-before-completion (this turn)",
                            }],
                        },
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            result = _run_hook_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("io-trace", payload["reason"])

    def test_claude_stop_hook_validates_only_final_response(self):
        # Regression: AskUserQuestion answers / tool results are NOT real user prompts,
        # so the turn boundary does not reset on them. The hook must validate only the
        # FINAL response, not concatenate completion-checks from earlier responses in
        # the span (which made it re-extract a stale/invalid earlier block and block).
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text(
                "\n".join([
                    json.dumps({"type": "user", "message": {"role": "user", "content": "Do it."}}),
                    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "name": "Skill",
                         "input": {"skill": "verification-before-completion"}}]}}),
                    # earlier response carries an INVALID completion-check (missing verification-done)
                    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "text", "text": (
                            "[completion-check]\n"
                            "- skill-call: verification-before-completion (this turn)\n"
                            "- acceptance-criteria:\n"
                            "  - c1: stale [source: system-doc]\n"
                            "- claim-evidence-map:\n"
                            "  - claim: stale\n"
                            "    criterion: c1\n"
                            "    evidence: stale\n"
                            "    verdict: pass\n"
                            "- unverified:\n"
                            "  - none\n"
                            "[io-trace]\n"
                            "- skills-loaded: [verification-before-completion]")}]}}),
                    # an AskUserQuestion answer returns as a tool_result user entry (NOT a real prompt)
                    json.dumps({"type": "user", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": "abc", "content": "option A"}]}}),
                    # FINAL response: a fully valid completion-check
                    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "text", "text": (
                            "[completion-check]\n"
                            "- verification-before-completion: done\n"
                            "- skill-call: verification-before-completion (this turn)\n"
                            "- acceptance-criteria:\n"
                            "  - crit-a: aligned [source: system-doc]\n"
                            "- claim-evidence-map:\n"
                            "  - claim: did the thing\n"
                            "    criterion: crit-a\n"
                            "    evidence: ran cmd 3/3 OK\n"
                            "    verdict: pass\n"
                            "- unverified:\n"
                            "  - none\n"
                            "- evidence: ran cmd\n"
                            "[io-trace]\n"
                            "- skills-loaded: [verification-before-completion]")}]}}),
                ]) + "\n",
                encoding="utf-8",
            )
            result = _run_hook_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("continue"), True, msg=payload)
        self.assertNotIn("decision", payload)

    def test_claude_stop_hook_allows_explanatory_final_response_without_completion_check(self):
        # Stop hooks keep the completion gate alive without turning every routine
        # explanatory final response into a completion-check retry loop.
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text(
                "\n".join([
                    json.dumps({"type": "user", "message": {"role": "user", "content": "Explain both options."}}),
                    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "text", "text": "Here are the two options. The first is gate wiring, and the second is install safety. Choose the direction."}]}}),
                ]) + "\n",
                encoding="utf-8",
            )
            result = _run_hook_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("continue"), True, msg=payload)
        self.assertNotIn("decision", payload)

    def test_claude_stop_hook_blocks_completion_claim_without_completion_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text(
                "\n".join([
                    json.dumps({"type": "user", "message": {"role": "user", "content": "Fix it."}}),
                    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "text", "text": "The requested change is complete and tests pass."}]}}),
                ]) + "\n",
                encoding="utf-8",
            )
            result = _run_hook_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block", msg=payload)
        self.assertIn("[completion-check]", payload["reason"])

    def test_internal_instruction_lines_are_english_only(self):
        for command in (
            install_hooks.HOOK_COMMAND,
            install_hooks.SESSION_INTENT_COMMAND,
            install_hooks.SESSION_START_COMMAND,
        ):
            result = _run_hook_command(command)
            self.assertEqual(result.returncode, 0)
            internal_lines = [
                line for line in result.stdout.splitlines()
                if line.startswith("Internal instruction:")
            ]
            self.assertTrue(internal_lines)
            for line in internal_lines:
                self.assertIsNone(re.search(r"[\uac00-\ud7a3]", line))
        result = _run_hook_command(
            install_hooks.WEB_SEARCH_FIRST_COMMAND,
            env=_strict_hook_env(),
        )
        self.assertEqual(result.returncode, 0)
        internal_lines = [
            line for line in result.stdout.splitlines()
            if line.startswith("Internal instruction:")
        ]
        self.assertTrue(internal_lines)
        for line in internal_lines:
            self.assertIsNone(re.search(r"[\uac00-\ud7a3]", line))

    def test_tool_checkpoint_runtime_hook_uses_concise_enforced_instruction(self):
        """Installed tool-checkpoint instruction keeps the concise decision surface."""
        self.assertIn("hook-enforced", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("Surface a visible [tool-checkpoint]", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("same session input lineage", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("continuing to check every tool call", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("why", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("procedure", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("when it changes the next work decision", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("Add localized-human-note, rejected-alternatives, unverified-premises, and failure-mode-if-wrong only when", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("recovery-action", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertNotIn("recovery-cost", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertNotIn("recovery-note", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("focus-layer", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("scope-reopen", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        # Tiered contract: intent and why always; procedure is the normal
        # operational summary. Analytical fields expand only on the
        # observable tool kind and recorded boundary/forced state, never on a
        # self-judgment that the call is safe, and the gate itself is never skipped.
        self.assertIn("read-only", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("boundary-contract", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertIn("never skip the gate", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertNotIn("Required fields: intent, why, procedure", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertNotIn("[tool-checkpoint:batch]", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertNotIn("[tool-checkpoint:continuation]", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertNotIn("previous full gate", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertNotIn("semantic safety classifier", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertNotIn("compact schema", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        self.assertNotIn("compact " + "tool-checkpoint", install_hooks.TOOL_CHECKPOINT_INTERNAL)
        opaque_risk_code_re = re.compile(r"(?<![A-Za-z0-9_])R[0-9][A-Z]?(?![A-Za-z0-9_])")
        self.assertIsNone(opaque_risk_code_re.search(install_hooks.TOOL_CHECKPOINT_INTERNAL))
        # ASCII boundaries (not \b) so a Korean-adjacent opaque code is still caught.
        self.assertIsNotNone(opaque_risk_code_re.search("R1-suffix"))

        result = _run_hook_command(
            install_hooks.TOOL_CHECKPOINT_COMMAND,
            env=_strict_hook_env(),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hook-enforced", result.stdout)
        self.assertIn("Surface a visible [tool-checkpoint]", result.stdout)
        self.assertIn("same session input lineage", result.stdout)
        self.assertIn("continuing to check every tool call", result.stdout)
        self.assertIn("hook-stage: PreToolUse", result.stdout)
        self.assertIn("tool-call retry checkpoint, not user-input intake", result.stdout)
        self.assertIn("when it changes the next work decision", result.stdout)
        self.assertIn("Add localized-human-note, rejected-alternatives, unverified-premises, and failure-mode-if-wrong only when", result.stdout)
        self.assertIn("recovery-action", result.stdout)
        self.assertIn("read-only", result.stdout)
        self.assertIn("never skip the gate", result.stdout)
        self.assertNotIn("recovery-cost", result.stdout)
        self.assertNotIn("recovery-note", result.stdout)
        self.assertNotIn("Required fields: intent, why, procedure", result.stdout)
        self.assertNotIn("[tool-checkpoint:batch]", result.stdout)
        self.assertNotIn("[tool-checkpoint:continuation]", result.stdout)
        self.assertNotIn("semantic safety classifier", result.stdout)
        self.assertNotIn("compact schema", result.stdout)
        self.assertNotIn("compact " + "tool-checkpoint", result.stdout)
        self.assertIsNone(opaque_risk_code_re.search(result.stdout))

    def test_codex_dispatcher_tool_checkpoint_returns_pre_tool_use_deny_shape_for_blocked_gate(self):
        """Codex PreToolUse tool-checkpoint denies only when downstream gate blocks."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            _write_session_intent_preflight(temp_home, "codex", "s-codex-tool-checkpoint")
            _write_downstream_gate(temp_home, "codex", "s-codex-tool-checkpoint", "block")
            request = json.dumps({
                "session_id": "s-codex-tool-checkpoint",
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"},
            })
            command = [
                node,
                str(dispatcher),
                "--platform",
                "codex",
                "--event",
                "PreToolUse",
                "--hook",
                "tool-checkpoint",
                "--marker",
                install_hooks.TOOL_CHECKPOINT_MARKER,
            ]

            first = subprocess.run(
                command,
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            second = subprocess.run(
                command,
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(first.returncode, 0, msg=first.stderr)
        first_payload = json.loads(first.stdout)
        output = first_payload["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("jailbreak-detector blocked downstream tool execution", output["permissionDecisionReason"])
        self.assertNotIn("continue", first_payload)
        self.assertNotIn("systemMessage", first_payload)

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assertEqual(second_payload["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_codex_dispatcher_tool_checkpoint_surfaces_once_per_input_lineage(self):
        """Open downstream gate keeps checking each tool call but surfaces once per user input."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            session_id = "s-codex-tool-checkpoint-surface-batch"
            _write_session_intent_preflight(
                temp_home,
                "codex",
                session_id,
                event_id="evt-a",
                input_digest="sha256:a",
            )
            _write_downstream_gate(
                temp_home,
                "codex",
                session_id,
                "allow",
                event_id="evt-a",
                input_digest="sha256:a",
            )
            request = json.dumps({
                "session_id": session_id,
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"},
            })
            command = [
                node,
                str(dispatcher),
                "--platform",
                "codex",
                "--event",
                "PreToolUse",
                "--hook",
                "tool-checkpoint",
                "--marker",
                install_hooks.TOOL_CHECKPOINT_MARKER,
            ]

            first = subprocess.run(
                command,
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            second = subprocess.run(
                command,
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            _write_session_intent_preflight(
                temp_home,
                "codex",
                session_id,
                event_id="evt-b",
                input_digest="sha256:b",
            )
            _write_downstream_gate(
                temp_home,
                "codex",
                session_id,
                "allow",
                event_id="evt-b",
                input_digest="sha256:b",
            )
            third = subprocess.run(
                command,
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(first.returncode, 0, msg=first.stderr)
        first_payload = json.loads(first.stdout)
        self.assert_no_pretool_deny(first_payload)
        self.assertIn("additionalContext", first_payload.get("hookSpecificOutput", {}))

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assert_no_pretool_deny(second_payload)
        self.assertNotIn("additionalContext", second_payload.get("hookSpecificOutput", {}))

        self.assertEqual(third.returncode, 0, msg=third.stderr)
        third_payload = json.loads(third.stdout)
        self.assert_no_pretool_deny(third_payload)
        self.assertIn("additionalContext", third_payload.get("hookSpecificOutput", {}))

    def test_claude_dispatcher_tool_checkpoint_fallback_does_not_inject_answer_context(self):
        """Claude print mode can miss intent lineage; allow checkpoints still avoid model-context text."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            session_id = "s-claude-tool-checkpoint-missing-lineage"
            command = [
                node,
                str(dispatcher),
                "--platform",
                "claude",
                "--event",
                "PreToolUse",
                "--hook",
                "tool-checkpoint",
                "--marker",
                install_hooks.TOOL_CHECKPOINT_MARKER,
            ]

            def run_tool(tool_name: str) -> subprocess.CompletedProcess:
                return subprocess.run(
                    command,
                    input=json.dumps({
                        "session_id": session_id,
                        "hook_event_name": "PreToolUse",
                        "tool_name": tool_name,
                        "tool_input": {"command": "echo smoke"},
                    }),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=False,
                )

            first = run_tool("PowerShell")
            second = run_tool("WebSearch")

        self.assertEqual(first.returncode, 0, msg=first.stderr)
        first_payload = json.loads(first.stdout)
        self.assert_no_pretool_deny(first_payload)
        self.assertNotIn("additionalContext", first_payload.get("hookSpecificOutput", {}))
        self.assertNotIn("[tool-checkpoint]", first.stdout)

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assert_no_pretool_deny(second_payload)
        self.assertNotIn("additionalContext", second_payload.get("hookSpecificOutput", {}))
        self.assertNotIn("[tool-checkpoint]", second.stdout)

    def test_claude_dispatcher_tool_checkpoint_allow_does_not_inject_answer_surface_context(self):
        """Claude allow checkpoints must not enter model context as answer-shaped text."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "claude",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-claude-tool-checkpoint-no-answer-leak",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)
        output = payload.get("hookSpecificOutput", {})
        self.assertNotIn("additionalContext", output)
        self.assertNotIn("[tool-checkpoint]", result.stdout)
        self.assertNotIn("Surface a visible", result.stdout)

    def test_claude_dispatcher_tool_checkpoint_transcript_fallback_avoids_answer_context(self):
        """Claude transcript fallback never injects answer-shaped checkpoint text."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            session_id = "s-claude-tool-checkpoint-transcript-fallback"
            transcript = Path(temp_home) / "transcript.jsonl"
            transcript.write_text(
                json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": "first prompt"},
                })
                + "\n",
                encoding="utf-8",
            )
            command = [
                node,
                str(dispatcher),
                "--platform",
                "claude",
                "--event",
                "PreToolUse",
                "--hook",
                "tool-checkpoint",
                "--marker",
                install_hooks.TOOL_CHECKPOINT_MARKER,
            ]

            def run_tool() -> subprocess.CompletedProcess:
                return subprocess.run(
                    command,
                    input=json.dumps({
                        "session_id": session_id,
                        "transcript_path": str(transcript),
                        "hook_event_name": "PreToolUse",
                        "tool_name": "WebSearch",
                        "tool_input": {"query": "neuromorphic AI"},
                    }),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=False,
                )

            first = run_tool()
            second = run_tool()
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": [
                        {"type": "tool_result", "content": "ignored tool result"},
                    ]},
                }) + "\n")
                handle.write(json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": "second prompt"},
                }) + "\n")
            third = run_tool()

        self.assertEqual(first.returncode, 0, msg=first.stderr)
        first_payload = json.loads(first.stdout)
        self.assertNotIn("additionalContext", first_payload.get("hookSpecificOutput", {}))
        self.assertNotIn("[tool-checkpoint]", first.stdout)

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assertNotIn("additionalContext", second_payload.get("hookSpecificOutput", {}))
        self.assertNotIn("[tool-checkpoint]", second.stdout)

        self.assertEqual(third.returncode, 0, msg=third.stderr)
        third_payload = json.loads(third.stdout)
        self.assertNotIn("additionalContext", third_payload.get("hookSpecificOutput", {}))
        self.assertNotIn("[tool-checkpoint]", third.stdout)

    def test_codex_dispatcher_tool_checkpoint_parallel_calls_share_input_surface(self):
        """Parallel tool calls in one input lineage share one user-facing checkpoint surface."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            session_id = "s-codex-tool-checkpoint-parallel-surface"
            _write_session_intent_preflight(
                temp_home,
                "codex",
                session_id,
                event_id="evt-parallel",
                input_digest="sha256:parallel",
            )
            _write_downstream_gate(
                temp_home,
                "codex",
                session_id,
                "allow",
                event_id="evt-parallel",
                input_digest="sha256:parallel",
            )
            command = [
                node,
                str(dispatcher),
                "--platform",
                "codex",
                "--event",
                "PreToolUse",
                "--hook",
                "tool-checkpoint",
                "--marker",
                install_hooks.TOOL_CHECKPOINT_MARKER,
            ]

            def run_one(index: int) -> subprocess.CompletedProcess:
                return subprocess.run(
                    command,
                    input=json.dumps({
                        "session_id": session_id,
                        "hook_event_name": "PreToolUse",
                        "tool_name": "shell",
                        "tool_input": {"cmd": f"echo parallel-{index}"},
                    }),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=False,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(run_one, range(8)))

        payloads = []
        for result in results:
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assert_no_pretool_deny(payload)
            payloads.append(payload)

        surfaced_count = sum(
            1
            for payload in payloads
            if payload.get("hookSpecificOutput", {}).get("additionalContext")
        )
        self.assertEqual(surfaced_count, 1)

    def test_dispatcher_has_no_tool_checkpoint_replay_or_identity_decision_helpers(self):
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        source = dispatcher.read_text(encoding="utf-8")
        decision_body = re.search(
            r"function toolCheckpointDecision[\s\S]*?\n}\n\ntry",
            source,
        )

        self.assertLess(len(source.splitlines()), 880)  # bumped from 850 for expanded task-router reminder text (no new decision helpers)
        self.assertNotIn("function buildAuditRef(", source)
        self.assertNotIn("function completionReminderDecision", source)
        self.assertNotIn("function validateCompletionResponse", source)
        self.assertNotIn("COMPLETION_REMINDER_RETRY_TTL", source)
        self.assertIsNotNone(decision_body)
        self.assertNotIn("auditRef", decision_body.group(0))
        self.assertNotIn("buildAuditRef", decision_body.group(0))
        forbidden = [
            "function toolCheckpointReplayTtlMs",
            "stableToolCheckpointInputKey",
            "stableToolCheckpointSessionKey",
            "stableToolKey",
            "replay" + "Allowed",
            "denied" + "Sessions",
            "tool-checkpoint-state" + ".json",
            "fullToolCheckpointReason",
            "isRoutineNativeInspectionToolCall",
            "currentIntentGateDecision",
            "normalizedToolCategory",
            "mutationKeys",
            "shellCommandText",
        ]
        for needle in forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, source)

    def test_dispatcher_has_no_capability_fence_or_payload_content_gate(self):
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        source = dispatcher.read_text(encoding="utf-8")
        shared_dir = Path(install_hooks.__file__).parent

        forbidden_source = [
            "capability_fence",
            "capabilityFence",
            "admit_scope",
            "admitted-scope",
            "deny_substrings",
            "JSON.stringify(ti)",
        ]
        for needle in forbidden_source:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, source)

        forbidden_files = [
            "capability_fence.mjs",
            "admit_scope.mjs",
            "test_capability_fence.mjs",
            "test_admit_scope.mjs",
            "test_capability_fence_hook.py",
        ]
        for filename in forbidden_files:
            with self.subTest(filename=filename):
                self.assertFalse((shared_dir / filename).exists())

    def test_dispatcher_install_copies_companion_modules(self):
        # Dispatcher companion-module invariant: the dispatcher imports a relative .mjs module; the installer
        # must copy it alongside, or the installed enforcer fails ERR_MODULE_NOT_FOUND.
        prev_home = os.environ.get("HOME")
        with tempfile.TemporaryDirectory() as temp_home:
            os.environ["HOME"] = temp_home
            try:
                install_hooks._ensure_hook_dispatcher_installed()
                hooks_dir = Path(temp_home) / ".ghost-alice" / "hooks"
                self.assertTrue((hooks_dir / "ghost-alice-hook.mjs").exists())
                self.assertTrue(
                    (hooks_dir / "derive_downstream_gate.mjs").exists(),
                    "dispatcher companion module must be installed alongside the dispatcher",
                )
                self.assertTrue(
                    (hooks_dir / "reminder_texts.json").exists(),
                    "dispatcher reminder text data must be installed alongside the dispatcher",
                )
            finally:
                if prev_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = prev_home

    def test_codex_dispatcher_tool_checkpoint_does_not_deny_by_input_event_or_tool_input(self):
        """Codex tool-checkpoint ignores input event/tool identity when downstream gate is open."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            _write_session_intent_preflight(temp_home, "codex", "s-codex-tool-checkpoint-changing-input")
            command = [
                node,
                str(dispatcher),
                "--platform",
                "codex",
                "--event",
                "PreToolUse",
                "--hook",
                "tool-checkpoint",
                "--marker",
                install_hooks.TOOL_CHECKPOINT_MARKER,
            ]
            first = subprocess.run(
                command,
                input=json.dumps({
                    "session_id": "s-codex-tool-checkpoint-changing-input",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "apply_patch",
                    "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            second = subprocess.run(
                command,
                input=json.dumps({
                    "session_id": "s-codex-tool-checkpoint-changing-input",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "write",
                    "tool_input": {"path": "README.md", "content": "updated"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            replay = subprocess.run(
                command,
                input=json.dumps({
                    "session_id": "s-codex-tool-checkpoint-changing-input",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "write",
                    "tool_input": {"path": "README.md", "content": "updated"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(first.returncode, 0, msg=first.stderr)
        first_payload = json.loads(first.stdout)
        self.assert_no_pretool_deny(first_payload)

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assert_no_pretool_deny(second_payload)

        self.assertEqual(replay.returncode, 0, msg=replay.stderr)
        replay_payload = json.loads(replay.stdout)
        self.assert_no_pretool_deny(replay_payload)

    def test_codex_dispatcher_zero_deny_for_100_distinct_shell_commands_with_open_gate(self):
        """Open downstream gate means shell command text never creates a checkpoint denial."""
        deny_count = 0
        for index in range(100):
            result = self.run_codex_pretool({
                "session_id": "s-codex-100-shell-commands",
                "hook_event_name": "PreToolUse",
                "tool_name": "shell",
                "tool_input": {"cmd": f"echo distinct-command-{index}"},
            })
            payload = json.loads(result.stdout)
            output = payload.get("hookSpecificOutput", {})
            if isinstance(output, dict) and output.get("permissionDecision") == "deny":
                deny_count += 1

        self.assertEqual(deny_count, 0)

    def test_codex_dispatcher_zero_deny_for_same_command_different_args_with_open_gate(self):
        """Command arguments are not a checkpoint identity or mutation classifier."""
        for index in range(50):
            result = self.run_codex_pretool({
                "session_id": "s-codex-same-command-different-args",
                "hook_event_name": "PreToolUse",
                "tool_name": "shell",
                "tool_input": {"cmd": f"ls -la /tmp/ghost-alice-{index}"},
            })
            self.assert_no_pretool_deny(json.loads(result.stdout))

    def test_codex_dispatcher_zero_deny_for_different_tool_input_payload_keys_with_open_gate(self):
        """tool_input key names are not used as checkpoint decision signals."""
        payload_variants = [
            {"command": "echo command"},
            {"cmd": "echo cmd"},
            {"script": "echo script"},
            {"text": "echo text"},
            {"content": "echo content"},
            {"input": "echo input"},
            {"file_path": "/tmp/ghost-alice"},
        ]
        for tool_input in payload_variants:
            with self.subTest(tool_input=tool_input):
                result = self.run_codex_pretool({
                    "session_id": "s-codex-payload-key-variants",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "shell",
                    "tool_input": tool_input,
                })
                self.assert_no_pretool_deny(json.loads(result.stdout))

    def test_codex_dispatcher_block_gate_denies_regardless_of_tool_shape(self):
        """Closed downstream gate is the only checkpoint denial source."""
        requests = [
            {"tool_name": "shell", "tool_input": {"cmd": "echo shell"}},
            {"tool_name": "read", "tool_input": {"file_path": "README.md"}},
            {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"}},
            {"tool_name": "unknown-tool", "tool_input": {"anything": "value"}},
        ]
        for request in requests:
            with self.subTest(tool_name=request["tool_name"]):
                result = self.run_codex_pretool({
                    "session_id": "s-codex-block-regardless-tool-shape",
                    "hook_event_name": "PreToolUse",
                    **request,
                }, downstream_decision="block")
                payload = json.loads(result.stdout)
                self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("jailbreak-detector blocked downstream tool execution", payload["hookSpecificOutput"]["permissionDecisionReason"])

    def test_codex_dispatcher_tool_checkpoint_does_not_deny_missing_session_intent_preflight(self):
        """Missing session-intent preflight evidence is not a first-entry deny reason."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-missing-intent-preflight",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "shell",
                    "tool_input": {"cmd": "pwd"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)

    def test_codex_dispatcher_tool_checkpoint_allows_read_only_bootstrap_batch_first_try(self):
        """A first-entry read-only bootstrap batch must not hit tool-checkpoint retry."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            session_id = f"s-codex-read-only-bootstrap-batch-{Path(temp_home).name}"
            command_text = (
                "printf 'pending-merges\\n'; "
                "sed -n '1,40p' \"$HOME/.agents/skills/task-router/SKILL.md\"; "
                "sed -n '1,40p' \"$HOME/.agents/skills/session-intent-analyzer/SKILL.md\"; "
                "sed -n '1,40p' \"$HOME/.agents/skills/using-coding-convention/SKILL.md\"; "
                "rg --files \"$HOME/ghost-alice\" | rg 'session_intent|ghost-alice-hook'"
            )
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": session_id,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "functions.exec_command",
                    "tool_input": {"cmd": command_text},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)

    def test_codex_dispatcher_tool_checkpoint_does_not_deny_digest_only_session_intent(self):
        """Digest-only session-intent evidence is an intake state, not a tool-scope deny reason."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            _write_session_intent_digest_only_preflight(temp_home, "codex", "s-codex-digest-only")
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-digest-only",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "shell",
                    "tool_input": {"cmd": "pwd"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)

    def test_codex_dispatcher_tool_checkpoint_allows_session_intent_semantic_update_recovery(self):
        """The semantic ledger update command stays open so digest-only preflight cannot deadlock."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        ledger_script = Path(install_hooks.__file__).parents[1] / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py"
        self.assertTrue(ledger_script.exists(), f"missing ledger script: {ledger_script}")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            session_root = Path(temp_home) / ".ghost-alice" / "session-intent"
            _write_session_intent_digest_only_preflight(temp_home, "codex", "s-codex-semantic-recovery")
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-semantic-recovery",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "functions.exec_command",
                    "tool_input": {
                        "cmd": (
                            f"python3 {ledger_script} --root {session_root} --platform codex "
                            "--delta-json '{\"current_goal\":\"record semantic intent\"}' --snapshot"
                        ),
                    },
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))

    def test_codex_dispatcher_tool_checkpoint_allows_session_intent_ledger_help_before_preflight(self):
        """A fresh agent can discover the session-intent recovery CLI before preflight exists."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        ledger_script = Path(install_hooks.__file__).parents[1] / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py"
        self.assertTrue(ledger_script.exists(), f"missing ledger script: {ledger_script}")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-ledger-help-before-preflight",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "functions.exec_command",
                    "tool_input": {"cmd": f"python3 {ledger_script} --help"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))

    def test_codex_dispatcher_tool_checkpoint_allows_pending_merge_manifest_precheck_before_preflight(self):
        """The pre-routing pending-merge manifest check must not depend on session-intent preflight."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            manifest_path = Path(temp_home) / ".ghost-alice" / "pending-merges" / "codex" / "manifest.json"
            command_text = f"test -f {manifest_path} && sed -n '1,160p' {manifest_path} || true"
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-pending-merge-before-preflight",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "functions.exec_command",
                    "tool_input": {"cmd": command_text},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))

    def test_codex_dispatcher_tool_checkpoint_allows_shell_content_after_open_gate(self):
        """Codex shell command text is not parsed once downstream gate is open."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        command = [
            node,
            str(dispatcher),
            "--platform",
            "codex",
            "--event",
            "PreToolUse",
            "--hook",
            "tool-checkpoint",
            "--marker",
            install_hooks.TOOL_CHECKPOINT_MARKER,
        ]
        routine_inspection_commands = [
            "pwd",
            "sed -n '1,20p' README.md",
            "git diff --check -- install.sh",
            "bash -n install.sh",
            "zsh -n install.sh",
            "pwsh -NoLogo -NoProfile -Command \"[System.Management.Automation.Language.Parser]::ParseFile('install.ps1',[ref]$null,[ref]$null)\"",
            "cmd /c type README.md",
        ]

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            for index, shell_command in enumerate(routine_inspection_commands):
                with self.subTest(shell_command=shell_command):
                    session_id = f"s-codex-routine-inspection-tool-checkpoint-{index}"
                    _write_session_intent_preflight(temp_home, "codex", session_id)
                    result = subprocess.run(
                        command,
                        input=json.dumps({
                            "session_id": session_id,
                            "hook_event_name": "PreToolUse",
                            "tool_name": "shell",
                            "tool_input": {"cmd": shell_command},
                        }),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, msg=result.stderr)
                    payload = json.loads(result.stdout)
                    self.assert_no_pretool_deny(payload)
                    self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))

    def test_codex_dispatcher_tool_checkpoint_allows_functions_exec_command_inspection_after_intent_preflight(self):
        """Codex app developer-tool shell reads follow the same open-gate state policy."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            _write_session_intent_preflight(temp_home, "codex", "s-codex-functions-exec-command")
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-functions-exec-command",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "functions.exec_command",
                    "tool_input": {"cmd": "rg -n session-intent-analyzer AGENTS.md"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))

    def test_codex_dispatcher_ignores_old_block_gate_for_newer_intent_event(self):
        with tempfile.TemporaryDirectory() as temp_home:
            session_id = "s-old-block-is-stale"
            _write_session_intent_preflight(
                temp_home,
                "codex",
                session_id,
                event_id="evt-new",
                input_digest="sha256:new",
            )
            _write_downstream_gate(
                temp_home,
                "codex",
                session_id,
                "block",
                event_id="evt-old",
                input_digest="sha256:old",
            )

            dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
            node = shutil.which("node") or shutil.which("node.exe")
            if not node:
                self.skipTest("node is required to execute the hook dispatcher")
            env = _strict_hook_env({"HOME": temp_home})
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": session_id,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "shell",
                    "tool_input": {"cmd": "opaque shell text"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assert_no_pretool_deny(json.loads(result.stdout))

    def test_codex_dispatcher_does_not_parse_shell_command_content(self):
        for command in [
            "rg -n 'hook|gate' AGENTS.md",
            "sed -n '1,20p' README.md",
            "test -f README.md && wc -l README.md || true",
            "git diff --check -- install.sh",
            "shasum -a 256 README.md",
        ]:
            with self.subTest(command=command):
                result = self.run_codex_pretool({
                    "session_id": "s-shell-read-only-content",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "shell",
                    "tool_input": {"cmd": command},
                })
                payload = json.loads(result.stdout)
                self.assert_no_pretool_deny(payload)
                self.assertNotIn(command, json.dumps(payload, ensure_ascii=False))

    def test_codex_dispatcher_tool_checkpoint_does_not_deny_unknown_shell_after_open_gate(self):
        result = self.run_codex_pretool({
            "session_id": "s-shell-unknown-content",
            "hook_event_name": "PreToolUse",
            "tool_name": "shell",
            "tool_input": {"cmd": "opaque shell text with no known read-only command"},
        })

        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)
        self.assertNotIn("opaque shell text", json.dumps(payload, ensure_ascii=False))

    def test_codex_dispatcher_still_respects_downstream_gate_for_shell(self):
        result = self.run_codex_pretool({
            "session_id": "s-shell-downstream-closed",
            "hook_event_name": "PreToolUse",
            "tool_name": "shell",
            "tool_input": {"cmd": "opaque shell text"},
        }, downstream_decision="block")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("downstream", payload["hookSpecificOutput"]["permissionDecisionReason"])

    def test_codex_dispatcher_tool_checkpoint_does_not_parse_compound_shell_mutation(self):
        """Compound shell content is not parsed as mutation when downstream gate is open."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            _write_session_intent_preflight(temp_home, "codex", "s-codex-compound-mutation")
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-compound-mutation",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "shell",
                    "tool_input": {"cmd": "sed -n '1p' README.md && rm -f README.md"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)
        self.assertNotIn("rm -f README.md", json.dumps(payload, ensure_ascii=False))

    def test_codex_dispatcher_tool_checkpoint_honors_jailbreak_block_before_tool_shape(self):
        """jailbreak-detector gate state controls downstream access before tool-shape review."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            _write_session_intent_preflight(temp_home, "codex", "s-codex-jailbreak-block")
            _write_downstream_gate(temp_home, "codex", "s-codex-jailbreak-block", "block")
            env = _strict_hook_env({"HOME": temp_home})
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-jailbreak-block",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "shell",
                    "tool_input": {"cmd": "pwd"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("jailbreak-detector", output["permissionDecisionReason"])
        self.assertIn("decision=block", output["permissionDecisionReason"])

    def test_codex_dispatcher_tool_checkpoint_blocks_session_intent_recovery_when_gate_blocks(self):
        """No command-shape exception bypasses a blocked downstream gate."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            _write_session_intent_preflight(temp_home, "codex", "s-codex-recovery-before-jailbreak")
            _write_downstream_gate(temp_home, "codex", "s-codex-recovery-before-jailbreak", "block")
            env = _strict_hook_env({"HOME": temp_home})
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-recovery-before-jailbreak",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "shell",
                    "tool_input": {
                        "cmd": "python3 /tmp/session_intent_ledger.py --delta-json '{}'",
                    },
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("jailbreak-detector", payload["hookSpecificOutput"]["permissionDecisionReason"])

    def test_codex_dispatcher_tool_checkpoint_allows_governance_skill_doc_bootstrap_before_semantic_intent(self):
        """Codex has no native Skill tool, so reading governance SKILL.md is a bootstrap lane."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            _write_session_intent_preflight(
                temp_home,
                "codex",
                "s-codex-governance-skill-doc-bootstrap",
                semantic_delta=False,
            )
            env = _strict_hook_env({"HOME": temp_home})
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "codex",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-codex-governance-skill-doc-bootstrap",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "functions.exec_command",
                    "tool_input": {
                        "cmd": "sed -n '1,120p' /Users/test/.agents/skills/task-router/SKILL.md",
                    },
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)

    def test_codex_dispatcher_tool_checkpoint_blocks_jailbreak_detector_bootstrap_when_gate_blocks(self):
        """A blocked downstream gate applies to bootstrap-looking tool calls too."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        command = [
            node,
            str(dispatcher),
            "--platform",
            "codex",
            "--event",
            "PreToolUse",
            "--hook",
            "tool-checkpoint",
            "--marker",
            install_hooks.TOOL_CHECKPOINT_MARKER,
        ]

        with tempfile.TemporaryDirectory() as temp_home:
            _write_session_intent_preflight(temp_home, "codex", "s-codex-jailbreak-bootstrap")
            _write_downstream_gate(temp_home, "codex", "s-codex-jailbreak-bootstrap", "block")
            env = _strict_hook_env({"HOME": temp_home})
            skill_call = subprocess.run(
                command,
                input=json.dumps({
                    "session_id": "s-codex-jailbreak-bootstrap",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "skill",
                    "tool_input": {"skill": "jailbreak-detector"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            doc_read = subprocess.run(
                command,
                input=json.dumps({
                    "session_id": "s-codex-jailbreak-bootstrap",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "functions.exec_command",
                    "tool_input": {
                        "cmd": "sed -n '1,120p' /Users/test/.agents/skills/jailbreak-detector/SKILL.md",
                    },
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(skill_call.returncode, 0, msg=skill_call.stderr)
        self.assertEqual(json.loads(skill_call.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(doc_read.returncode, 0, msg=doc_read.stderr)
        self.assertEqual(json.loads(doc_read.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_codex_dispatcher_blocks_mandatory_governance_skill_docs_while_blocked(self):
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")
        command = [
            node,
            str(dispatcher),
            "--platform",
            "codex",
            "--event",
            "PreToolUse",
            "--hook",
            "tool-checkpoint",
            "--marker",
            install_hooks.TOOL_CHECKPOINT_MARKER,
        ]

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            trusted_roots = [
                Path(temp_home) / ".agents" / "skills",
                Path(temp_home) / ".claude" / "skills",
                Path(temp_home) / ".codex" / "skills",
                Path(temp_home) / ".codex" / "skills" / ".system",
            ]
            for skill in [
                "merge-companion",
                "session-intent-analyzer",
                "jailbreak-detector",
                "task-router",
                "boundary-contract",
                "using-coding-convention",
                "verification-before-completion",
            ]:
                for root_index, trusted_root in enumerate(trusted_roots):
                    with self.subTest(skill=skill, trusted_root=str(trusted_root)):
                        session_id = f"s-governance-bootstrap-{skill}-{root_index}"
                        _write_session_intent_preflight(temp_home, "codex", session_id)
                        _write_downstream_gate(temp_home, "codex", session_id, "block")
                        doc_path = trusted_root / skill / "SKILL.md"
                        result = subprocess.run(
                            command,
                            input=json.dumps({
                                "session_id": session_id,
                                "hook_event_name": "PreToolUse",
                                "tool_name": "functions.exec_command",
                                "tool_input": {
                                    "cmd": f"sed -n '1,120p' {doc_path}",
                                },
                            }),
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            env=env,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 0, msg=result.stderr)
                        payload = json.loads(result.stdout)
                        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_codex_dispatcher_does_not_bootstrap_untrusted_governance_named_skill_doc(self):
        result = self.run_codex_pretool({
            "session_id": "s-untrusted-bootstrap",
            "hook_event_name": "PreToolUse",
            "tool_name": "functions.exec_command",
            "tool_input": {
                "cmd": "sed -n '1,120p' /tmp/session-intent-analyzer/SKILL.md",
            },
        }, downstream_decision="block")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("jailbreak-detector", payload["hookSpecificOutput"]["permissionDecisionReason"])

    def test_claude_dispatcher_tool_checkpoint_blocks_governance_skill_bootstrap_when_gate_blocks(self):
        """Claude Skill-looking calls do not bypass a blocked downstream gate."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            _write_session_intent_preflight(temp_home, "claude", "s-claude-skill-bootstrap")
            _write_downstream_gate(temp_home, "claude", "s-claude-skill-bootstrap", "block")
            env = _strict_hook_env({"HOME": temp_home})
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "claude",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-claude-skill-bootstrap",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Skill",
                    "tool_input": {"skill": "task-router"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_claude_dispatcher_tool_checkpoint_returns_pre_tool_use_deny_shape_for_blocked_gate(self):
        """Claude PreToolUse tool-checkpoint denies only when downstream gate blocks."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            _write_session_intent_preflight(temp_home, "claude", "s-claude-tool-checkpoint")
            _write_downstream_gate(temp_home, "claude", "s-claude-tool-checkpoint", "block")
            request = json.dumps({
                "session_id": "s-claude-tool-checkpoint",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -f README.md"},
            })
            command = [
                node,
                str(dispatcher),
                "--platform",
                "claude",
                "--event",
                "PreToolUse",
                "--hook",
                "tool-checkpoint",
                "--marker",
                install_hooks.TOOL_CHECKPOINT_MARKER,
            ]

            first = subprocess.run(
                command,
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            second = subprocess.run(
                command,
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(first.returncode, 0, msg=first.stderr)
        first_payload = json.loads(first.stdout)
        output = first_payload["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("jailbreak-detector blocked downstream tool execution", output["permissionDecisionReason"])
        self.assertNotIn("continue", first_payload)
        self.assertNotIn("systemMessage", first_payload)

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assertEqual(second_payload["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_claude_dispatcher_tool_checkpoint_does_not_deny_by_input_event_or_tool_input(self):
        """Claude tool-checkpoint matches Codex: open gate means tool identity is ignored."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            _write_session_intent_preflight(temp_home, "claude", "s-claude-tool-checkpoint-changing-input")
            command = [
                node,
                str(dispatcher),
                "--platform",
                "claude",
                "--event",
                "PreToolUse",
                "--hook",
                "tool-checkpoint",
                "--marker",
                install_hooks.TOOL_CHECKPOINT_MARKER,
            ]
            first = subprocess.run(
                command,
                input=json.dumps({
                    "session_id": "s-claude-tool-checkpoint-changing-input",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "rm -f README.md"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            second = subprocess.run(
                command,
                input=json.dumps({
                    "session_id": "s-claude-tool-checkpoint-changing-input",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Write",
                    "tool_input": {"file_path": "README.md", "content": "changed"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            replay = subprocess.run(
                command,
                input=json.dumps({
                    "session_id": "s-claude-tool-checkpoint-changing-input",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Write",
                    "tool_input": {"file_path": "README.md", "content": "changed"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(first.returncode, 0, msg=first.stderr)
        first_payload = json.loads(first.stdout)
        self.assert_no_pretool_deny(first_payload)

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assert_no_pretool_deny(second_payload)

        self.assertEqual(replay.returncode, 0, msg=replay.stderr)
        replay_payload = json.loads(replay.stdout)
        self.assert_no_pretool_deny(replay_payload)

    def test_claude_dispatcher_tool_checkpoint_does_not_parse_bash_inspection(self):
        """Claude Bash command content is not parsed for read/write classification."""
        dispatcher = Path(install_hooks.__file__).with_name("ghost-alice-hook.mjs")
        self.assertTrue(dispatcher.exists(), f"missing dispatcher: {dispatcher}")

        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")

        with tempfile.TemporaryDirectory() as temp_home:
            env = _strict_hook_env({"HOME": temp_home})
            _write_session_intent_preflight(temp_home, "claude", "s-claude-routine-inspection-tool-checkpoint")
            result = subprocess.run(
                [
                    node,
                    str(dispatcher),
                    "--platform",
                    "claude",
                    "--event",
                    "PreToolUse",
                    "--hook",
                    "tool-checkpoint",
                    "--marker",
                    install_hooks.TOOL_CHECKPOINT_MARKER,
                ],
                input=json.dumps({
                    "session_id": "s-claude-routine-inspection-tool-checkpoint",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "bash -n install.sh"},
                }),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assert_no_pretool_deny(payload)
        self.assertNotIn("bash -n install.sh", json.dumps(payload, ensure_ascii=False))


class TempHomeTestCase(unittest.TestCase):
    """Base class that uses a temporary home directory for each test."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.fake_home = Path(self.temp_dir)
        self._patcher = patch.object(install_hooks, "_home", return_value=self.fake_home)
        self._patcher.start()
        # Platform detect functions use the patched _home, so no separate patch is needed.

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_platform_dir(self, platform: str) -> Path:
        """Create a test platform directory."""
        config_dir = self.fake_home / f".{platform}"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    def _config_file(self, platform: str) -> Path:
        """Return the hook config file path for a platform."""
        return install_hooks.PLATFORMS[platform]["hook_file"]()

    def _codex_config_toml(self) -> Path:
        return self.fake_home / ".codex" / "config.toml"

    def _write_settings(self, platform: str, data: dict) -> Path:
        """Write a test hook config file."""
        config_dir = self._create_platform_dir(platform)
        settings_file = self._config_file(platform)
        config_dir = settings_file.parent
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return settings_file

    def _read_settings(self, platform: str) -> dict:
        """Read and return the hook config file for a platform."""
        settings_file = self._config_file(platform)
        with open(settings_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _create_runtime_shared_dir(self, shared: Path | None = None) -> Path:
        """Create an installed _shared fixture referenced by hook commands."""
        if shared is None:
            shared = self.fake_home / ".agents" / "skills" / "_shared"
        shared.mkdir(parents=True, exist_ok=True)
        for name in (
            "pending_merge_precheck_hook.py",
            "session_intent_analyzer_hook.py",
            "task_router_reminder_hook.py",
            "claude_stop_verification_hook.py",
            "hook_profile_gate.py",
            "io_trace_hook.py",
        ):
            (shared / name).write_text("# fixture\n", encoding="utf-8")
        return shared


class TestInstallHook(TempHomeTestCase):
    """Install behavior tests."""

    def test_install_into_empty_settings(self):
        """Install hooks into an empty settings file."""
        self._write_settings("claude", {})
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        settings = self._read_settings("claude")
        hooks = settings["hooks"]["UserPromptSubmit"]
        # Four entries: prompt pending-merge + session-intent-analyzer + task-router-reminder + web-search-first.
        self.assertEqual(len(hooks), 4)
        cmds = [h["command"] for entry in hooks for h in entry["hooks"]]
        self.assertTrue(any(install_hooks.PROMPT_PENDING_MERGE_MARKER in c for c in cmds))
        self.assertTrue(any(install_hooks.HOOK_MARKER in c for c in cmds))
        self.assertTrue(any(install_hooks.SESSION_INTENT_MARKER in c for c in cmds))
        self.assertTrue(any(install_hooks.WEB_SEARCH_FIRST_MARKER in c for c in cmds))
        allow = settings["permissions"]["allow"]
        for skill_name in _expected_ghost_alice_skill_names():
            with self.subTest(skill_name=skill_name):
                self.assertIn(f"Skill({skill_name})", allow)

    def test_user_prompt_governance_hooks_are_ordered_before_task_router_reminder(self):
        """pending-merge and session-intent preflight come before the task-router reminder."""
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                self._write_settings(platform, {})
                result = install_hooks.install_hook(platform)
                self.assertEqual(result, "installed")

                settings = self._read_settings(platform)
                event = install_hooks._resolve_hook_event("on_user_prompt", platform)
                hooks = settings["hooks"][event]
                marker_order = []
                for entry in hooks:
                    command = " ".join(hook.get("command", "") for hook in entry.get("hooks", []))
                    if install_hooks.PROMPT_PENDING_MERGE_MARKER in command:
                        marker_order.append("pending-merge")
                    elif install_hooks.SESSION_INTENT_MARKER in command:
                        marker_order.append("session-intent")
                    elif install_hooks.HOOK_MARKER in command:
                        marker_order.append("task-router-reminder")
                    elif install_hooks.WEB_SEARCH_FIRST_MARKER in command:
                        marker_order.append("web-search-first")

                self.assertEqual(
                    marker_order,
                    ["pending-merge", "session-intent", "task-router-reminder", "web-search-first"],
                )

    def test_install_replaces_legacy_pre_tool_checkpoint_marker(self):
        """Old pre-tool checkpoint entries are removed when the new marker is installed."""
        legacy_marker = f"[{'action'}-{'gate'}] pre-tool-check"
        self._write_settings(
            "codex",
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"legacy command # {legacy_marker}",
                                }
                            ],
                        }
                    ]
                }
            },
        )

        result = install_hooks.install_hook("codex")

        self.assertEqual(result, "installed")
        settings = self._read_settings("codex")
        commands = [
            hook.get("command", "")
            for entry in settings["hooks"]["PreToolUse"]
            for hook in entry.get("hooks", [])
        ]
        self.assertFalse(any(legacy_marker in command for command in commands))
        self.assertEqual(
            sum(install_hooks.TOOL_CHECKPOINT_MARKER in command for command in commands),
            1,
        )

    def test_install_uses_runtime_shared_dir_for_generated_hook_commands(self):
        """Installed hook commands reference runtime _shared, not the source checkout."""
        self._write_settings("claude", {})
        runtime_shared = self._create_runtime_shared_dir()
        source_shared = Path(install_hooks.__file__).resolve().parent.as_posix()

        with patch.dict(os.environ, {"GHOST_ALICE_HOOK_SHARED_DIR": str(runtime_shared)}):
            result = install_hooks.install_hook("claude")

        self.assertEqual(result, "installed")
        settings = self._read_settings("claude")
        commands = [
            hook.get("command", "")
            for entries in settings["hooks"].values()
            for entry in entries
            for hook in entry.get("hooks", [])
        ]
        joined = "\n".join(
            _visible_and_runner_payload_text(command)
            for command in commands
        ).replace("\\", "/")
        self.assertIn(runtime_shared.as_posix(), joined)
        self.assertIn((runtime_shared / "io_trace_hook.py").as_posix(), joined)
        self.assertNotIn(source_shared, joined)

    def test_cli_defaults_to_installed_platform_shared_dir_when_available(self):
        """Direct CLI execution uses installed platform _shared as the expected command path when present."""
        self._write_settings("claude", {})
        runtime_shared = self._create_runtime_shared_dir(
            self.fake_home / ".claude" / "skills" / "_shared"
        )
        source_shared = Path(install_hooks.__file__).resolve().parent.as_posix()

        with patch.dict(os.environ, {install_hooks.HOOK_SHARED_DIR_ENV: ""}):
            with patch.object(sys, "argv", ["install_hooks.py", "--platform", "claude"]):
                result = install_hooks.main()

        self.assertEqual(result, 0)
        settings = self._read_settings("claude")
        commands = [
            hook.get("command", "")
            for entries in settings["hooks"].values()
            for entry in entries
            for hook in entry.get("hooks", [])
        ]
        joined = "\n".join(
            _visible_and_runner_payload_text(command)
            for command in commands
        ).replace("\\", "/")
        self.assertIn(runtime_shared.as_posix(), joined)
        self.assertIn((runtime_shared / "io_trace_hook.py").as_posix(), joined)
        self.assertNotIn(source_shared, joined)

    def test_cli_agent_visibility_status_writes_runtime_config_and_reports_profile(self):
        """--agent-visibility is stored as runtime config separate from hook status."""
        self._create_platform_dir("claude")

        output = io.StringIO()
        with patch.object(sys, "argv", [
            "install_hooks.py",
            "--platform",
            "claude",
            "--status",
            "--agent-visibility",
            "dynamic",
        ]):
            with contextlib.redirect_stdout(output):
                result = install_hooks.main()

        config_path = self.fake_home / ".ghost-alice" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(config["agent_visibility"]["profile"], "dynamic")
        self.assertEqual(config["strict_session_log"]["mode"], "always")
        self.assertIn("Agent visibility: profile=dynamic", output.getvalue())
        self.assertIn("Strict session log: mode=always", output.getvalue())

    def test_cli_visibility_alias_writes_runtime_config_and_reports_profile(self):
        """--visibility is the short spelling for the same runtime preference."""
        self._create_platform_dir("claude")

        output = io.StringIO()
        with patch.object(sys, "argv", [
            "install_hooks.py",
            "--platform",
            "claude",
            "--status",
            "--visibility",
            "minimal",
        ]):
            with contextlib.redirect_stdout(output):
                result = install_hooks.main()

        config_path = self.fake_home / ".ghost-alice" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(config["agent_visibility"]["profile"], "minimal")
        self.assertEqual(config["strict_session_log"]["mode"], "always")
        self.assertIn("Agent visibility: profile=minimal", output.getvalue())
        self.assertIn("Strict session log: mode=always", output.getvalue())

    def test_cli_install_prints_agent_visibility_guidance(self):
        """Install output reports the dynamic default and runtime adjustment method."""
        self._create_platform_dir("codex")

        output = io.StringIO()
        with patch.object(sys, "argv", [
            "install_hooks.py",
            "--platform",
            "codex",
        ]):
            with contextlib.redirect_stdout(output):
                result = install_hooks.main()

        text = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Agent visibility: profile=dynamic", text)
        self.assertIn("Default profile is dynamic unless --visibility overrides it.", text)
        self.assertIn("--agent-visibility remains accepted for compatibility.", text)
        self.assertIn("Profiles adjust user-facing governance message volume only", text)
        self.assertIn("Use /visibility to inspect", text)

    def test_cli_install_fails_when_node_runtime_is_missing(self):
        """Hook-enabled installs fail before writing Node-backed tool-checkpoint commands without node."""
        self._create_platform_dir("claude")
        original_which = shutil.which

        def fake_which(command: str) -> str | None:
            if command in {"node", "node.exe"}:
                return None
            return original_which(command)

        output = io.StringIO()
        with patch.object(install_hooks.shutil, "which", side_effect=fake_which):
            with patch.object(sys, "argv", [
                "install_hooks.py",
                "--platform",
                "claude",
            ]):
                with contextlib.redirect_stdout(output):
                    result = install_hooks.main()

        text = output.getvalue()
        self.assertEqual(result, 1)
        self.assertIn("Node.js runtime is required for Claude Code hook enforcement", text)
        self.assertFalse((self.fake_home / ".claude" / "settings.json").exists())
        self.assertIn("/visibility strict, /visibility dynamic, or /visibility minimal", text)
        self.assertIn("Hook execution, governance gates, and strict session logging remain unchanged.", text)

    def test_agent_visibility_env_does_not_change_generated_hook_command(self):
        hook_key = install_hooks._resolve_hook_event("on_user_prompt", "claude")
        with patch.dict(os.environ, {"GHOST_ALICE_AGENT_VISIBILITY": "strict"}):
            strict_command = install_hooks._entry_command(install_hooks._platform_hook_entry("claude", hook_key))
        with patch.dict(os.environ, {"GHOST_ALICE_AGENT_VISIBILITY": "minimal"}):
            minimal_command = install_hooks._entry_command(install_hooks._platform_hook_entry("claude", hook_key))

        self.assertEqual(strict_command, minimal_command)

    def test_install_into_nonexistent_settings(self):
        """Install hooks when the settings file is absent."""
        self._create_platform_dir("claude")
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        settings = self._read_settings("claude")
        self.assertIn("hooks", settings)
        self.assertIn("UserPromptSubmit", settings["hooks"])

    def test_install_preserves_existing_settings(self):
        """Preserve existing settings keys."""
        self._write_settings("claude", {
            "autoUpdatesChannel": "latest",
            "customKey": "customValue",
            "permissions": {"allow": ["Skill(hwpx)"]},
        })
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        settings = self._read_settings("claude")
        self.assertEqual(settings["autoUpdatesChannel"], "latest")
        self.assertEqual(settings["customKey"], "customValue")
        self.assertIn("Skill(hwpx)", settings["permissions"]["allow"])
        self.assertIn("Skill(verification-before-completion)", settings["permissions"]["allow"])
        self.assertEqual(
            settings["permissions"]["allow"].count("Skill(verification-before-completion)"),
            1,
        )

    def test_install_preserves_non_managed_bash_permissions(self):
        """The installer does not remove unmanaged Bash allow rules."""
        self._write_settings("claude", {
            "permissions": {
                "allow": [
                    "Skill(hwpx)",
                    "Bash(rg ghost-alice README.md)",
                ],
            },
        })

        result = install_hooks.install_hook("claude")

        self.assertEqual(result, "installed")
        allow = self._read_settings("claude")["permissions"]["allow"]
        self.assertIn("Skill(hwpx)", allow)
        self.assertIn("Bash(rg ghost-alice README.md)", allow)

    def test_install_preserves_other_hooks(self):
        """Preserve existing hooks without managed markers."""
        existing_hook = {
            "matcher": "*.py",
            "hooks": [{"type": "command", "command": "echo 'other hook'"}],
        }
        self._write_settings("claude", {
            "hooks": {"UserPromptSubmit": [existing_hook]},
        })
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        settings = self._read_settings("claude")
        hooks = settings["hooks"]["UserPromptSubmit"]
        # Existing 1 + prompt pending-merge + session-intent-analyzer + task-router + web-search-first = 5.
        self.assertEqual(len(hooks), 5)
        self.assertEqual(hooks[0]["matcher"], "*.py")

    def test_install_creates_backup(self):
        """Create a .json.bak backup file during install."""
        self._write_settings("claude", {"existing": True})
        install_hooks.install_hook("claude")

        backup = self.fake_home / ".claude" / "settings.json.bak"
        self.assertTrue(backup.exists())
        with open(backup, "r", encoding="utf-8") as f:
            backup_data = json.load(f)
        self.assertTrue(backup_data["existing"])


class TestIdempotency(TempHomeTestCase):
    """Idempotency tests."""

    def test_skip_when_already_installed(self):
        """Return 'already' and leave files unchanged when all hooks already exist."""
        # First install adds UserPromptSubmit + PostToolUse.
        self._write_settings("claude", {})
        install_hooks.install_hook("claude")
        # Second call returns already because all hooks exist.
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "already")

    def test_double_install_no_duplicate(self):
        """Installing twice does not duplicate hooks."""
        self._write_settings("claude", {})
        install_hooks.install_hook("claude")
        install_hooks.install_hook("claude")

        settings = self._read_settings("claude")
        hooks = settings["hooks"]["UserPromptSubmit"]
        marker_count = sum(
            1 for entry in hooks
            for hook in entry.get("hooks", [])
            if install_hooks.HOOK_MARKER in hook.get("command", "")
        )
        self.assertEqual(marker_count, 1)


class TestUninstallHook(TempHomeTestCase):
    """Uninstall behavior tests."""

    def test_uninstall_removes_hook(self):
        """Remove installed hooks."""
        self._write_settings("claude", {
            "hooks": {
                "UserPromptSubmit": [install_hooks.HOOK_ENTRY],
            },
        })
        result = install_hooks.uninstall_hook("claude")
        self.assertEqual(result, "removed")

        settings = self._read_settings("claude")
        hooks = settings["hooks"]["UserPromptSubmit"]
        self.assertEqual(len(hooks), 0)

    def test_uninstall_preserves_other_hooks(self):
        """Preserve other hooks without managed markers."""
        other_hook = {
            "matcher": "",
            "hooks": [{"type": "command", "command": "echo 'keep me'"}],
        }
        self._write_settings("claude", {
            "hooks": {
                "UserPromptSubmit": [other_hook, install_hooks.HOOK_ENTRY],
            },
        })
        result = install_hooks.uninstall_hook("claude")
        self.assertEqual(result, "removed")

        settings = self._read_settings("claude")
        hooks = settings["hooks"]["UserPromptSubmit"]
        self.assertEqual(len(hooks), 1)
        self.assertIn("keep me", hooks[0]["hooks"][0]["command"])

    def test_uninstall_when_no_hook(self):
        """Return not_found when no hooks exist."""
        self._write_settings("claude", {"hooks": {"UserPromptSubmit": []}})
        result = install_hooks.uninstall_hook("claude")
        self.assertEqual(result, "not_found")

    def test_uninstall_preserves_other_settings(self):
        """Preserve other settings keys during removal."""
        self._write_settings("claude", {
            "autoUpdatesChannel": "latest",
            "hooks": {
                "UserPromptSubmit": [install_hooks.HOOK_ENTRY],
                "SessionStart": [],
            },
        })
        install_hooks.uninstall_hook("claude")

        settings = self._read_settings("claude")
        self.assertEqual(settings["autoUpdatesChannel"], "latest")
        self.assertIn("SessionStart", settings["hooks"])


class TestCheckStatus(TempHomeTestCase):
    """Status check tests."""

    def test_status_installed(self):
        """Return installed when the full hook suite is installed."""
        self._write_settings("claude", {})
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        result = install_hooks.check_status("claude")
        self.assertEqual(result, "installed")

    def test_status_detail_reports_drift_when_marker_exists_but_command_differs(self):
        """Marker presence with command mismatch is command drift, not absence."""
        self._write_settings("claude", {})
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")
        settings = self._read_settings("claude")
        for entry in settings["hooks"]["UserPromptSubmit"]:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if install_hooks.WEB_SEARCH_FIRST_MARKER in command:
                    hook["command"] = command + " --local-user-edit"
        self._write_settings("claude", settings)

        status = install_hooks.check_status_detail("claude")

        self.assertEqual(status.status_token, "HOOK_INSTALLED_DRIFT")
        self.assertEqual(status.legacy_status, "missing")
        self.assertEqual(status.details["drifted"], ["web-search-first"])
        self.assertNotEqual(status.status_label, install_hooks.STATUS_LABELS["missing"])

    def test_install_reports_stale_web_search_hook_as_replaced_not_removed(self):
        """A stale mandatory web-search-first hook is reported as replacement."""
        self._write_settings("claude", {})
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")
        settings = self._read_settings("claude")
        for entry in settings["hooks"]["UserPromptSubmit"]:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if install_hooks.WEB_SEARCH_FIRST_MARKER in command:
                    hook["command"] = command + " --local-user-edit"
        self._write_settings("claude", settings)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = install_hooks.install_hook("claude")

        text = output.getvalue()
        self.assertEqual(result, "installed")
        self.assertIn("Replaced 1 stale web-search-first hook entry(ies)", text)
        self.assertNotIn("Removed 1 stale web-search-first hook entry(ies)", text)
        self.assertIn("web-search-first hook added (rule 10)", text)

    def test_status_detail_reports_codex_feature_flag_disabled(self):
        """Codex hooks.json with feature flag off is config disabled, not missing."""
        self._create_platform_dir("codex")
        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")
        self._codex_config_toml().write_text("[features]\nhooks = false\n", encoding="utf-8")

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            status = install_hooks.check_status_detail("codex")

        self.assertEqual(status.status_token, "HOOK_CONFIG_DISABLED")
        self.assertEqual(status.legacy_status, "missing")
        self.assertIsNone(status.missing_reason)

    def test_status_detail_reports_missing_reason_for_absent_config(self):
        """Absent config file is reported as config_file_absent."""
        self._create_platform_dir("claude")

        status = install_hooks.check_status_detail("claude")

        self.assertEqual(status.status_token, "HOOK_MISSING")
        self.assertEqual(status.legacy_status, "missing")
        self.assertEqual(status.missing_reason, "config_file_absent")

    def test_status_missing_when_only_prompt_hook_is_installed(self):
        """A prompt hook alone does not count as the full hook suite."""
        self._write_settings("claude", {
            "hooks": {"UserPromptSubmit": [install_hooks.HOOK_ENTRY]},
        })
        result = install_hooks.check_status("claude")
        self.assertEqual(result, "missing")

    def test_status_missing_when_web_search_hook_is_missing(self):
        """Missing web-search-first hook is missing."""
        self._write_settings("claude", {
            "hooks": {
                "UserPromptSubmit": [
                    install_hooks.PROMPT_PENDING_MERGE_ENTRY,
                    install_hooks.HOOK_ENTRY,
                    install_hooks.SESSION_INTENT_ENTRY,
                ],
                "Stop": [install_hooks.STOP_HOOK_ENTRY],
                "SessionStart": [install_hooks.SESSION_START_ENTRY],
                "PostToolUse": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": "bash '/tmp/io-trace-hook.sh' # [io-trace] Ghost-ALICE"}],
                    }
                ],
            },
        })
        result = install_hooks.check_status("claude")
        self.assertEqual(result, "missing")

    def test_status_missing_when_session_intent_hook_is_missing(self):
        """Missing session-intent-analyzer hook is missing."""
        self._write_settings("claude", {
            "hooks": {
                "UserPromptSubmit": [
                    install_hooks.PROMPT_PENDING_MERGE_ENTRY,
                    install_hooks.HOOK_ENTRY,
                    install_hooks.WEB_SEARCH_FIRST_ENTRY,
                ],
                "Stop": [install_hooks.STOP_HOOK_ENTRY],
                "SessionStart": [install_hooks.SESSION_START_ENTRY],
                "PostToolUse": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": "bash '/tmp/io-trace-hook.sh' # [io-trace] Ghost-ALICE"}],
                    }
                ],
            },
        })
        result = install_hooks.check_status("claude")
        self.assertEqual(result, "missing")

    def test_status_missing_when_session_start_hook_is_missing(self):
        """Missing SessionStart hook is missing."""
        self._write_settings("claude", {
            "hooks": {
                "UserPromptSubmit": [
                    install_hooks.PROMPT_PENDING_MERGE_ENTRY,
                    install_hooks.HOOK_ENTRY,
                    install_hooks.SESSION_INTENT_ENTRY,
                    install_hooks.WEB_SEARCH_FIRST_ENTRY,
                ],
                "Stop": [install_hooks.STOP_HOOK_ENTRY],
                "PostToolUse": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": "bash '/tmp/io-trace-hook.sh' # [io-trace] Ghost-ALICE"}],
                    }
                ],
            },
        })
        result = install_hooks.check_status("claude")
        self.assertEqual(result, "missing")

    def test_status_missing_when_io_trace_hook_is_missing(self):
        """Missing Claude/Codex PostToolUse io-trace hook is missing."""
        self._write_settings("claude", {
            "hooks": {
                "UserPromptSubmit": [
                    install_hooks.PROMPT_PENDING_MERGE_ENTRY,
                    install_hooks.HOOK_ENTRY,
                    install_hooks.SESSION_INTENT_ENTRY,
                    install_hooks.WEB_SEARCH_FIRST_ENTRY,
                ],
                "Stop": [install_hooks.STOP_HOOK_ENTRY],
                "SessionStart": [install_hooks.SESSION_START_ENTRY],
            },
        })
        result = install_hooks.check_status("claude")
        self.assertEqual(result, "missing")

    def test_status_missing(self):
        """Return missing when no hooks exist."""
        self._write_settings("claude", {})
        result = install_hooks.check_status("claude")
        self.assertEqual(result, "missing")

    def test_status_no_settings_file(self):
        """Return missing when the settings file is absent."""
        self._create_platform_dir("claude")
        result = install_hooks.check_status("claude")
        self.assertEqual(result, "missing")


class TestPlatformSkip(TempHomeTestCase):
    """Framework-not-installed skip tests."""

    def test_install_skips_missing_platform(self):
        """Return skipped when the platform directory is absent."""
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "skipped")

    def test_uninstall_skips_missing_platform(self):
        """Return skipped when the platform directory is absent."""
        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.uninstall_hook("codex")
        self.assertEqual(result, "skipped")

    def test_status_skips_missing_platform(self):
        """Return skipped when the platform directory is absent."""
        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.check_status("codex")
        self.assertEqual(result, "skipped")


class TestCodexUnixHookConfig(TempHomeTestCase):
    """macOS/Linux/WSL Codex hook config tests."""

    def test_install_codex_uses_hooks_json_and_enables_feature_flag(self):
        """Codex installs hooks.json and enables the config.toml feature flag."""
        self._create_platform_dir("codex")

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        hooks_file = self.fake_home / ".codex" / "hooks.json"
        self.assertTrue(hooks_file.exists())
        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
        self.assertIn("UserPromptSubmit", hooks["hooks"])
        self.assertIn("Stop", hooks["hooks"])
        self.assertIn("PostToolUse", hooks["hooks"])
        stop_cmds = [h["command"] for entry in hooks["hooks"]["Stop"] for h in entry["hooks"]]
        self.assertTrue(any(install_hooks.STOP_HOOK_MARKER in cmd for cmd in stop_cmds))
        stop_payloads = []
        for cmd in stop_cmds:
            if install_hooks.STOP_HOOK_MARKER not in cmd:
                continue
            result = _run_hook_command(cmd)
            self.assertEqual(result.returncode, 0)
            stop_payloads.append(json.loads(result.stdout))
        self.assertTrue(any(payload.get("continue") is True for payload in stop_payloads))
        self.assertFalse(any("systemMessage" in payload for payload in stop_payloads))

        prompt_cmds = [h["command"] for entry in hooks["hooks"]["UserPromptSubmit"] for h in entry["hooks"]]
        session_cmds = [h["command"] for entry in hooks["hooks"]["SessionStart"] for h in entry["hooks"]]
        for marker, cmds in (
            (install_hooks.PROMPT_PENDING_MERGE_MARKER, prompt_cmds),
            (install_hooks.HOOK_MARKER, prompt_cmds),
            (install_hooks.SESSION_INTENT_MARKER, prompt_cmds),
            (install_hooks.WEB_SEARCH_FIRST_MARKER, prompt_cmds),
            (install_hooks.SESSION_START_MARKER, session_cmds),
        ):
            matching = [cmd for cmd in cmds if marker in cmd]
            self.assertEqual(len(matching), 1)
            if marker == install_hooks.WEB_SEARCH_FIRST_MARKER:
                minimal_result = _run_hook_command(matching[0], env=_minimal_visibility_env())
                self.assertEqual(minimal_result.returncode, 0)
                self.assertTrue(minimal_result.stdout)
                result = _run_hook_command(matching[0])
            else:
                result = _run_hook_command(matching[0])
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["continue"], True)
            self.assertIn("systemMessage", payload)
            self.assertNotIn("User:", payload["systemMessage"])
            self.assertNotIn("Tech:", payload["systemMessage"])

        config_toml = self._codex_config_toml()
        self.assertTrue(config_toml.exists())
        self.assertIn("[features]", config_toml.read_text(encoding="utf-8"))
        self.assertIn("hooks = true", config_toml.read_text(encoding="utf-8"))
        self.assertNotIn("codex_hooks", config_toml.read_text(encoding="utf-8"))

    def test_install_codex_trusts_installed_ghost_alice_hooks(self):
        """Codex installs trusted hashes for every Ghost-ALICE hook it writes."""
        self._create_platform_dir("codex")

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        settings_file = self.fake_home / ".codex" / "hooks.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        event_labels = {
            "PreToolUse": "pre_tool_use",
            "PostToolUse": "post_tool_use",
            "SessionStart": "session_start",
            "Stop": "stop",
            "UserPromptSubmit": "user_prompt_submit",
        }
        managed_keys = []
        for event, entries in settings["hooks"].items():
            event_key = event_labels[event]
            for group_index, entry in enumerate(entries):
                for handler_index, hook in enumerate(entry.get("hooks", [])):
                    command = hook.get("command", "")
                    if "[hook-runner:" in command or "[io-trace] Ghost-ALICE" in command:
                        managed_keys.append(
                            f"{settings_file.as_posix()}:{event_key}:{group_index}:{handler_index}"
                        )

        config = tomllib.loads(self._codex_config_toml().read_text(encoding="utf-8"))
        hook_state = config["hooks"]["state"]
        self.assertEqual(set(hook_state), set(managed_keys))
        self.assertEqual(len(hook_state), 8)
        for key in managed_keys:
            trusted_hash = hook_state[key]["trusted_hash"]
            self.assertRegex(trusted_hash, r"^sha256:[0-9a-f]{64}$")

    def test_install_codex_trusts_current_project_config_layer(self):
        """Codex install marks the Ghost-ALICE project config layer as trusted."""
        self._create_platform_dir("codex")
        repo = self.fake_home / "repo" / "Ghost-ALICE"
        repo.mkdir(parents=True)

        with (
            patch.object(install_hooks, "_repo_root_from_this_file", return_value=repo),
            patch.object(install_hooks, "_codex_hooks_supported", return_value=True),
        ):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        project_key = str(repo.resolve())
        config = tomllib.loads(self._codex_config_toml().read_text(encoding="utf-8"))
        self.assertEqual(config["projects"][project_key]["trust_level"], "trusted")
        trace = self.fake_home / ".ghost-alice" / "install-state" / "codex-project-trust-change.json"
        self.assertTrue(trace.exists())
        data = json.loads(trace.read_text(encoding="utf-8"))
        self.assertEqual(data["kind"], "codex_project_trust")
        self.assertEqual(data["path"], self._codex_config_toml().as_posix())
        self.assertEqual(data["project_path"], project_key)
        self.assertEqual(data["before_state"], "missing")
        self.assertEqual(data["after_state"], "trusted")
        self.assertNotIn("raw_config", data)
        self.assertNotIn("content", data)

    def test_install_codex_hook_commands_do_not_embed_versioned_homebrew_python(self):
        """Installed hook commands resolve Python at runtime instead of pinning one Homebrew minor."""
        if os.name == "nt":
            self.skipTest("POSIX Homebrew path regression does not apply on Windows")
        self._create_platform_dir("codex")

        with (
            patch.object(install_hooks.sys, "executable", VERSIONED_HOMEBREW_PYTHON),
            patch.object(install_hooks, "_codex_hooks_supported", return_value=True),
        ):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        settings = self._read_settings("codex")
        command_surfaces: list[str] = []
        for groups in settings["hooks"].values():
            for entry in groups:
                for hook in entry.get("hooks", []):
                    command = hook.get("command", "")
                    command_surfaces.append(_visible_and_runner_payload_text(command))

        installed_text = "\n".join(command_surfaces)
        self.assertNotIn(VERSIONED_HOMEBREW_PYTHON, installed_text)
        self.assertIsNone(VERSIONED_HOMEBREW_PYTHON_RE.search(installed_text))

    def test_hook_python_invocation_resolves_compatible_python_from_runtime_path(self):
        if os.name == "nt":
            self.skipTest("POSIX launcher test does not apply on Windows")
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is required for POSIX launcher execution")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            called = temp_path / "called.txt"
            fake_python = bin_dir / "python3"
            fake_python.write_text(
                "\n".join([
                    f"#!{bash}",
                    'if [ "${1:-}" = "-c" ]; then',
                    '  case "${2:-}" in',
                    '    *"sys.version_info >= (3, 11)"*) exit 0 ;;',
                    "  esac",
                    "fi",
                    f'printf "%s\\n" "$@" > "{called}"',
                    "exit 0",
                    "",
                ]),
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            hook_script = temp_path / "hook.py"
            hook_script.write_text("# fixture\n", encoding="utf-8")

            with patch.object(install_hooks.sys, "executable", VERSIONED_HOMEBREW_PYTHON):
                command = install_hooks._hook_python_invocation(hook_script, "--flag")
            result = subprocess.run(
                [bash, "-c", command],
                env={"PATH": str(bin_dir), "HOME": str(temp_path)},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertNotIn(VERSIONED_HOMEBREW_PYTHON, command)
            self.assertEqual(called.read_text(encoding="utf-8").splitlines(), [str(hook_script), "--flag"])

    def test_install_codex_preserves_existing_hook_state_when_trusting_hooks(self):
        """Codex hook auto-trust preserves unrelated hook state."""
        self._create_platform_dir("codex")
        config_toml = self._codex_config_toml()
        config_toml.write_text(
            '\n'.join([
                '[features]',
                'hooks = true',
                'multi_agent = true',
                '',
                '[hooks.state."user-hook:pre_tool_use:0:0"]',
                'enabled = false',
                'trusted_hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
                '',
            ]),
            encoding="utf-8",
        )

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        config = tomllib.loads(config_toml.read_text(encoding="utf-8"))
        self.assertTrue(config["features"]["hooks"])
        self.assertTrue(config["features"]["multi_agent"])
        user_state = config["hooks"]["state"]["user-hook:pre_tool_use:0:0"]
        self.assertFalse(user_state["enabled"])
        self.assertEqual(
            user_state["trusted_hash"],
            "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        ghost_alice_states = [
            key
            for key in config["hooks"]["state"]
            if key.startswith((self.fake_home / ".codex" / "hooks.json").as_posix())
        ]
        self.assertEqual(len(ghost_alice_states), 8)

    def test_codex_hook_hash_omits_matcher_for_prompt_and_stop_events(self):
        hook = {"type": "command", "command": "node /tmp/ghost-alice-hook.mjs", "timeout": 600}
        group_with_matcher = {"matcher": "ignored-by-event", "hooks": [hook]}
        group_without_matcher = {"hooks": [hook]}

        self.assertEqual(
            install_hooks._codex_command_hook_hash("UserPromptSubmit", group_with_matcher, hook),
            install_hooks._codex_command_hook_hash("UserPromptSubmit", group_without_matcher, hook),
        )
        self.assertEqual(
            install_hooks._codex_command_hook_hash("Stop", group_with_matcher, hook),
            install_hooks._codex_command_hook_hash("Stop", group_without_matcher, hook),
        )
        self.assertNotEqual(
            install_hooks._codex_command_hook_hash("PreToolUse", group_with_matcher, hook),
            install_hooks._codex_command_hook_hash("PreToolUse", group_without_matcher, hook),
        )

    def test_install_codex_replaces_legacy_plain_text_hooks(self):
        self._write_settings("codex", {
            "hooks": {
                "UserPromptSubmit": [
                    install_hooks.PROMPT_PENDING_MERGE_ENTRY,
                    install_hooks.HOOK_ENTRY,
                    install_hooks.SESSION_INTENT_ENTRY,
                    install_hooks.WEB_SEARCH_FIRST_ENTRY,
                ],
                "SessionStart": [install_hooks.SESSION_START_ENTRY],
                "Stop": [install_hooks.STOP_HOOK_ENTRY_CODEX],
            },
        })

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        settings = self._read_settings("codex")
        prompt_cmds = [h["command"] for entry in settings["hooks"]["UserPromptSubmit"] for h in entry["hooks"]]
        session_cmds = [h["command"] for entry in settings["hooks"]["SessionStart"] for h in entry["hooks"]]

        for marker, cmds in (
            (install_hooks.PROMPT_PENDING_MERGE_MARKER, prompt_cmds),
            (install_hooks.HOOK_MARKER, prompt_cmds),
            (install_hooks.SESSION_INTENT_MARKER, prompt_cmds),
            (install_hooks.WEB_SEARCH_FIRST_MARKER, prompt_cmds),
            (install_hooks.SESSION_START_MARKER, session_cmds),
        ):
            matching = [cmd for cmd in cmds if marker in cmd]
            self.assertEqual(len(matching), 1)
            if marker == install_hooks.WEB_SEARCH_FIRST_MARKER:
                minimal_result = _run_hook_command(matching[0], env=_minimal_visibility_env())
                self.assertEqual(minimal_result.returncode, 0)
                self.assertTrue(minimal_result.stdout)
                result = _run_hook_command(matching[0])
            else:
                result = _run_hook_command(matching[0])
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["continue"], True)
            self.assertNotIn("Internal instruction:", result.stdout)
            self.assertNotIn("User:", payload["systemMessage"])
            self.assertNotIn("Tech:", payload["systemMessage"])

    def test_install_codex_replaces_markerless_hook_runner_wrappers(self):
        """Older hook-runner wrappers without outer markers are replaced without duplicate execution."""

        def markerless(entry: dict[str, Any]) -> dict[str, Any]:
            cloned = json.loads(json.dumps(entry))
            for hook in cloned.get("hooks", []):
                command = hook.get("command", "")
                hook["command"] = re.sub(r"\s+# .*\[hook-runner:[^\]]+\].*$", "", command)
            return cloned

        user_prompt = install_hooks._resolve_hook_event("on_user_prompt", "codex")
        pre_tool = install_hooks._resolve_hook_event("pre_tool_use", "codex")
        post_tool = install_hooks._resolve_hook_event("post_tool_use", "codex")
        stop = install_hooks._resolve_hook_event("on_agent_stop", "codex")
        session_start = install_hooks._resolve_hook_event("on_session_start", "codex")
        io_trace_entry = install_hooks._platform_io_trace_entry("codex", post_tool)
        self.assertIsNotNone(io_trace_entry)

        self._write_settings("codex", {
            "hooks": {
                user_prompt: [
                    markerless(install_hooks._platform_prompt_pending_merge_entry("codex", user_prompt)),
                    markerless(install_hooks._platform_session_intent_entry("codex", user_prompt)),
                    markerless(install_hooks._platform_hook_entry("codex", user_prompt)),
                    markerless(install_hooks._platform_web_search_entry("codex", user_prompt)),
                ],
                pre_tool: [markerless(install_hooks._platform_tool_checkpoint_entry("codex", pre_tool))],
                post_tool: [markerless(io_trace_entry)],
                stop: [markerless(install_hooks._platform_stop_hook_entry("codex", stop))],
                session_start: [markerless(install_hooks._platform_session_start_entry("codex", session_start))],
            },
        })

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        settings = self._read_settings("codex")
        expected = {
            user_prompt: [
                install_hooks.PROMPT_PENDING_MERGE_MARKER,
                install_hooks.SESSION_INTENT_MARKER,
                install_hooks.HOOK_MARKER,
                install_hooks.WEB_SEARCH_FIRST_MARKER,
            ],
            pre_tool: [install_hooks.TOOL_CHECKPOINT_MARKER],
            post_tool: [install_hooks.IO_TRACE_MARKER],
            stop: [install_hooks.STOP_HOOK_MARKER],
            session_start: [install_hooks.SESSION_START_MARKER],
        }

        for event, markers in expected.items():
            commands = [h["command"] for entry in settings["hooks"][event] for h in entry["hooks"]]
            self.assertEqual(len(commands), len(markers), event)
            self.assertFalse(
                any("hook_profile_gate.py run" in cmd and "[hook-runner:" not in cmd for cmd in commands),
                event,
            )
            for marker in markers:
                self.assertEqual(sum(marker in cmd for cmd in commands), 1, marker)

    def test_status_requires_codex_feature_flag(self):
        """Codex does not count hooks.json alone as installed without the feature flag."""
        self._write_settings("codex", {
            "hooks": {
                "UserPromptSubmit": [install_hooks.HOOK_ENTRY],
                "Stop": [install_hooks.STOP_HOOK_ENTRY],
            },
        })

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.check_status("codex")
        self.assertEqual(result, "missing")

    def test_install_codex_preserves_existing_config_toml(self):
        """Add only hooks=true when config.toml already exists."""
        self._create_platform_dir("codex")
        config_toml = self._codex_config_toml()
        config_toml.write_text('[features]\nmulti_agent = true\n', encoding="utf-8")

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        content = config_toml.read_text(encoding="utf-8")
        self.assertIn("multi_agent = true", content)
        self.assertIn("hooks = true", content)
        self.assertNotIn("codex_hooks", content)

    def test_install_codex_records_hook_feature_flag_change_without_raw_config(self):
        """Record trace-backed rollback metadata when Ghost-ALICE flips hooks=true."""
        self._create_platform_dir("codex")
        config_toml = self._codex_config_toml()
        config_toml.write_text('[features]\nhooks = false\nmulti_agent = true\n', encoding="utf-8")

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        trace = self.fake_home / ".ghost-alice" / "install-state" / "codex-hook-feature-change.json"
        self.assertTrue(trace.exists())
        data = json.loads(trace.read_text(encoding="utf-8"))
        self.assertEqual(data["kind"], "codex_hooks_feature_flag")
        self.assertEqual(data["before_state"], "false")
        self.assertEqual(data["after_state"], "true")
        self.assertEqual(data["path"], config_toml.as_posix())
        self.assertNotIn("raw_config", data)
        self.assertNotIn("content", data)

    def test_install_codex_migrates_deprecated_hook_feature_flag(self):
        """Migrate the deprecated codex_hooks feature flag to hooks=true."""
        self._create_platform_dir("codex")
        config_toml = self._codex_config_toml()
        config_toml.write_text('[features]\ncodex_hooks = true\nmulti_agent = true\n', encoding="utf-8")

        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "installed")

        content = config_toml.read_text(encoding="utf-8")
        self.assertIn("hooks = true", content)
        self.assertIn("multi_agent = true", content)
        self.assertNotIn("codex_hooks", content)


class TestCodexHookSupportGuard(TempHomeTestCase):
    """Codex hook support guard tests."""

    def test_codex_hooks_supported_includes_windows_runtime(self):
        """Windows runtime is not excluded from Codex hook installation."""
        with patch.object(install_hooks, "_running_on_windows", return_value=True):
            self.assertTrue(install_hooks._codex_hooks_supported())

    def test_install_codex_hooks_unsupported_runtime(self):
        """Skip hook installation on unsupported runtimes."""
        self._create_platform_dir("codex")
        with patch.object(install_hooks, "_codex_hooks_supported", return_value=False):
            result = install_hooks.install_hook("codex")
        self.assertEqual(result, "unsupported")
        settings_file = self.fake_home / ".codex" / "hooks.json"
        self.assertFalse(settings_file.exists())

    def test_uninstall_codex_hooks_unsupported_runtime(self):
        """Skip hook removal on unsupported runtimes."""
        self._write_settings("codex", {
            "hooks": {"UserPromptSubmit": [install_hooks.HOOK_ENTRY]},
        })
        original = (self.fake_home / ".codex" / "hooks.json").read_text(encoding="utf-8")
        with patch.object(install_hooks, "_codex_hooks_supported", return_value=False):
            result = install_hooks.uninstall_hook("codex")
        self.assertEqual(result, "unsupported")
        current = (self.fake_home / ".codex" / "hooks.json").read_text(encoding="utf-8")
        self.assertEqual(original, current)

    def test_status_codex_hooks_unsupported_runtime(self):
        """Codex status check returns unsupported on unsupported runtimes."""
        self._create_platform_dir("codex")
        with patch.object(install_hooks, "_codex_hooks_supported", return_value=False):
            result = install_hooks.check_status("codex")
        self.assertEqual(result, "unsupported")


class TestCorruptJson(TempHomeTestCase):
    """Corrupt JSON handling tests."""

    def test_install_recovers_from_corrupt_json(self):
        """Install succeeds even with corrupt settings.json."""
        config_dir = self._create_platform_dir("claude")
        settings_file = config_dir / "settings.json"
        settings_file.write_text("{invalid json", encoding="utf-8")

        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        # Confirm that the corrupt file was backed up.
        corrupt_backup = config_dir / "settings.json.corrupt-bak"
        self.assertTrue(corrupt_backup.exists())


class TestDryRun(TempHomeTestCase):
    """Dry-run tests."""

    def test_dry_run_does_not_modify_file(self):
        """Dry-run mode does not modify files."""
        self._write_settings("claude", {"original": True})
        original_content = (self.fake_home / ".claude" / "settings.json").read_text(encoding="utf-8")

        install_hooks.install_hook("claude", dry_run=True)

        current_content = (self.fake_home / ".claude" / "settings.json").read_text(encoding="utf-8")
        self.assertEqual(original_content, current_content)

    def test_dry_run_uninstall_does_not_modify(self):
        """Dry-run removal does not modify files."""
        self._write_settings("claude", {
            "hooks": {"UserPromptSubmit": [install_hooks.HOOK_ENTRY]},
        })
        original_content = (self.fake_home / ".claude" / "settings.json").read_text(encoding="utf-8")

        install_hooks.uninstall_hook("claude", dry_run=True)

        current_content = (self.fake_home / ".claude" / "settings.json").read_text(encoding="utf-8")
        self.assertEqual(original_content, current_content)


class TestMultiPlatform(TempHomeTestCase):
    """Multi-platform tests."""

    def test_install_to_multiple_platforms(self):
        """Install independently to multiple platforms."""
        for p in ("claude", "codex"):
            self._write_settings(p, {})

        for p in ("claude", "codex"):
            with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
                result = install_hooks.install_hook(p)
            self.assertEqual(result, "installed", f"{p} install failed")

        for p in ("claude", "codex"):
            with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
                result = install_hooks.check_status(p)
            self.assertEqual(result, "installed", f"{p} status check failed")


class TestRoundTrip(TempHomeTestCase):
    """Install -> status -> uninstall -> status round-trip tests."""

    def test_full_lifecycle(self):
        """Full install -> status(installed) -> uninstall -> status(missing) cycle."""
        self._write_settings("claude", {"keepMe": True})

        # Install.
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        # Status check.
        result = install_hooks.check_status("claude")
        self.assertEqual(result, "installed")

        # Reinstall (idempotency).
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "already")

        # Remove.
        result = install_hooks.uninstall_hook("claude")
        self.assertEqual(result, "removed")

        # Status check.
        result = install_hooks.check_status("claude")
        self.assertEqual(result, "missing")

        # Confirm existing settings were preserved.
        settings = self._read_settings("claude")
        self.assertTrue(settings.get("keepMe"))

        # Remove again (idempotency).
        result = install_hooks.uninstall_hook("claude")
        self.assertEqual(result, "not_found")


class TestEnvVarOverride(TempHomeTestCase):
    """Environment variable path override tests."""

    def test_claude_config_dir_override(self):
        """Use CLAUDE_CONFIG_DIR when it is set."""
        custom_dir = Path(self.temp_dir) / "custom-claude"
        custom_dir.mkdir()
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(custom_dir)}):
            resolved = install_hooks._resolve_claude_dir()
            self.assertEqual(resolved, custom_dir)

    def test_claude_default_without_env(self):
        """Use ~/.claude when CLAUDE_CONFIG_DIR is absent."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            resolved = install_hooks._resolve_claude_dir()
            self.assertEqual(resolved, self.fake_home / ".claude")

    def test_codex_home_override(self):
        """Use CODEX_HOME when it is set."""
        custom_dir = Path(self.temp_dir) / "custom-codex"
        custom_dir.mkdir()
        with patch.dict(os.environ, {"CODEX_HOME": str(custom_dir)}):
            resolved = install_hooks._resolve_codex_dir()
            self.assertEqual(resolved, custom_dir)

    def test_codex_default_without_env(self):
        """Use ~/.codex when CODEX_HOME is absent."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEX_HOME", None)
            resolved = install_hooks._resolve_codex_dir()
            self.assertEqual(resolved, self.fake_home / ".codex")

    def test_install_uses_env_var_path(self):
        """Install to the path specified by an environment variable."""
        custom_dir = Path(self.temp_dir) / "env-claude"
        custom_dir.mkdir()
        settings_file = custom_dir / "settings.json"
        settings_file.write_text("{}", encoding="utf-8")

        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(custom_dir)}):
            result = install_hooks.install_hook("claude")
            self.assertEqual(result, "installed")
            # Confirm installation occurred at the environment-variable path.
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertIn("hooks", data)


class TestBackupRotation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name)
        self.path = self.parent / "cfg.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_rotate_keeps_only_last_three(self):
        from install_hooks import _rotate_backups
        for i in range(5):
            bak = self.path.with_name(f"cfg.json.bak.{i}")
            bak.write_text(f"v{i}", encoding="utf-8")
            os.utime(bak, (1000 + i, 1000 + i))
        _rotate_backups(self.path, keep=3)
        remaining = sorted(self.parent.glob("cfg.json.bak*"))
        self.assertEqual(len(remaining), 3)
        names = {p.name for p in remaining}
        self.assertNotIn("cfg.json.bak.0", names)
        self.assertNotIn("cfg.json.bak.1", names)

    def test_rotate_skips_pending_merges_dir(self):
        """The guard protects pending-merges/ descendants even when they match the rotation glob."""
        from install_hooks import _rotate_backups
        pending = self.parent / ".ghost-alice" / "pending-merges" / "codex"
        pending.mkdir(parents=True)
        protected = pending / "cfg.json.bak.protected"
        protected.write_text("must survive", encoding="utf-8")
        for i in range(5):
            bak = self.path.with_name(f"cfg.json.bak.{i}")
            bak.write_text(f"v{i}", encoding="utf-8")
            os.utime(bak, (1000 + i, 1000 + i))
        _rotate_backups(
            self.path,
            keep=3,
            _pending_root=self.parent / ".ghost-alice" / "pending-merges",
        )
        self.assertTrue(protected.exists(), "pending-merges/ descendant .bak files must never be deleted")

    def test_rotate_does_not_unlink_symlinks(self):
        """Symlink .bak files are excluded from rotation to avoid unintended target impact."""
        from install_hooks import _rotate_backups
        target = self.path.with_name("real-target.txt")
        target.write_text("target", encoding="utf-8")
        link = self.path.with_name("cfg.json.bak.linked")
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"Symlink creation is unavailable in this environment: {exc}")
        for i in range(5):
            bak = self.path.with_name(f"cfg.json.bak.{i}")
            bak.write_text(f"v{i}", encoding="utf-8")
            os.utime(bak, (1000 + i, 1000 + i))
        _rotate_backups(self.path, keep=3)
        self.assertTrue(link.is_symlink(), "symlink .bak files are excluded from rotation")
        self.assertTrue(target.exists(), "symlink target is unaffected")


class TestSessionStartHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prev_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp.name
        os.environ["CODEX_HOME"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        if self.prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.prev_home
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.pop("CODEX_HOME", None)

    def test_session_start_hook_installed_for_claude(self):
        from install_hooks import install_hook, SESSION_START_MARKER, _resolve_hook_event
        Path(self.tmp.name).mkdir(parents=True, exist_ok=True)
        result = install_hook("claude")
        self.assertEqual(result, "installed")
        settings = json.loads((Path(self.tmp.name) / "settings.json").read_text(encoding="utf-8"))
        ss_key = _resolve_hook_event("on_session_start", "claude")
        self.assertIn(ss_key, settings["hooks"])
        cmds = [h["command"] for entry in settings["hooks"][ss_key] for h in entry["hooks"]]
        self.assertTrue(any(SESSION_START_MARKER in c for c in cmds))

    def test_user_prompt_hook_uses_hook_verified_pending_merge_precheck(self):
        from install_hooks import PROMPT_PENDING_MERGE_COMMAND
        result = _run_hook_command(PROMPT_PENDING_MERGE_COMMAND)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Internal instruction:", result.stdout)
        self.assertIn("merge-companion prompt-check", result.stdout)
        self.assertIn("do not run an extra shell manifest check", result.stdout)
        self.assertIn("merge-companion", result.stdout)
        _assert_contains_any(self, result.stdout, "current conversation", "current conversation")
        _assert_contains_any(self, result.stdout, "without an extra shell check", "without an extra shell check")
        self.assertNotIn("Check the current platform pending-merge manifest", result.stdout)
        self.assertNotIn("next time Claude/Codex is opened", result.stdout)
        self.assertNotIn("The next time you open Claude/Codex", result.stdout)

    def test_user_prompt_hook_reports_pending_entries_without_agent_shell_probe(self):
        from install_hooks import PROMPT_PENDING_MERGE_COMMAND

        with tempfile.TemporaryDirectory() as temp_home:
            manifest = Path(temp_home) / ".ghost-alice" / "pending-merges" / "claude" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({"entries": [{"decided": False}], "version": 1}),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = temp_home
            result = _run_hook_command(PROMPT_PENDING_MERGE_COMMAND, env=env)

        self.assertEqual(result.returncode, 0)
        self.assertIn("has 1 undecided entry", result.stdout)
        self.assertIn("Surface merge-companion first", result.stdout)
        self.assertIn("user-explicit defer/skip may continue", result.stdout)
        self.assertNotIn("Check the current platform pending-merge manifest", result.stdout)

    def test_claude_prompt_hooks_use_json_payloads_to_avoid_answer_text_leakage(self):
        from install_hooks import (
            _platform_hook_entry,
            _platform_prompt_pending_merge_entry,
            _platform_session_intent_entry,
            _platform_session_start_entry,
            _platform_web_search_entry,
        )

        entries = [
            _platform_prompt_pending_merge_entry("claude", "UserPromptSubmit"),
            _platform_hook_entry("claude", "UserPromptSubmit"),
            _platform_session_intent_entry("claude", "UserPromptSubmit"),
            _platform_web_search_entry("claude", "UserPromptSubmit"),
            _platform_session_start_entry("claude", "SessionStart"),
        ]
        commands = [hook["command"] for entry in entries for hook in entry["hooks"]]

        for command in commands:
            encoded_args = re.findall(r'"([A-Za-z0-9_-]{40,}=*)"', command)
            self.assertTrue(encoded_args, msg=command)
            wrapped_command = base64.urlsafe_b64decode(max(encoded_args, key=len)).decode("utf-8")
            self.assertNotIn("--format text", wrapped_command)
            if "--format" in wrapped_command:
                self.assertIn("--format json", wrapped_command)
            else:
                inline_payload = re.search(r"b64decode\('([^']+)'\)", wrapped_command)
                self.assertIsNotNone(inline_payload, msg=wrapped_command)
                decoded = base64.b64decode(inline_payload.group(1)).decode("utf-8")  # type: ignore[union-attr]
                self.assertIn("systemMessage", json.loads(decoded))

    def test_session_start_command_has_failsafe(self):
        from install_hooks import SESSION_START_COMMAND
        self.assertIn("|| true", SESSION_START_COMMAND)

    def test_session_start_hook_uses_current_session_copy(self):
        from install_hooks import SESSION_START_COMMAND

        result = _run_hook_command(SESSION_START_COMMAND)
        self.assertEqual(result.returncode, 0)
        _assert_contains_any(self, result.stdout, "At session start", "At session start")
        _assert_contains_any(self, result.stdout, "without an extra shell check", "extra shell check")
        self.assertIn("do not run a second shell check just to prove clean", result.stdout)
        self.assertNotIn("next time Claude/Codex is opened", result.stdout)
        self.assertNotIn("The next time you open Claude/Codex", result.stdout)


class TestWebSearchFirstHook(TempHomeTestCase):
    """Rule 10: web-search-first hook for external tool claims (agent governance)."""

    def test_web_search_first_hook_installed_for_claude(self):
        self._create_platform_dir("claude")
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")
        settings = self._read_settings("claude")
        hooks = settings["hooks"]["UserPromptSubmit"]
        cmds = [h["command"] for entry in hooks for h in entry["hooks"]]
        self.assertTrue(any(install_hooks.WEB_SEARCH_FIRST_MARKER in c for c in cmds))

    def test_web_search_first_command_mentions_community_sources(self):
        result = _run_hook_command(install_hooks.WEB_SEARCH_FIRST_COMMAND)
        self.assertEqual(result.returncode, 0)
        self.assertIn("WebSearch", result.stdout)
        self.assertIn("GitHub issues", result.stdout)
        self.assertIn("Reddit", result.stdout)
        self.assertIn("Rule 10", result.stdout)

    def test_user_prompt_hooks_emit_non_json_prefixed_text(self):
        for cmd, env in (
            (install_hooks.HOOK_COMMAND, None),
            (install_hooks.SESSION_INTENT_COMMAND, None),
            (install_hooks.WEB_SEARCH_FIRST_COMMAND, _strict_hook_env()),
        ):
            result = _run_hook_command(cmd, env=env)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.stdout)
            self.assertNotIn(result.stdout.lstrip()[:1], ("[", "{"))

    def test_session_start_hook_emits_non_json_prefixed_text(self):
        result = _run_hook_command(install_hooks.SESSION_START_COMMAND)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout)
        self.assertNotIn(result.stdout.lstrip()[:1], ("[", "{"))

    def test_claude_stop_hook_emits_valid_json(self):
        # With no transcript (empty input) there is no completion claim, so the
        # claim-only gate allows. However, it must still emit valid JSON. The block path's.
        # valid JSON is covered by test_claude_stop_hook_blocks_without_actual_verification_skill_load.
        result = _run_hook_command(install_hooks.STOP_HOOK_COMMAND)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["continue"], True)
        self.assertNotIn("decision", payload)

    def test_codex_stop_hook_emits_valid_json(self):
        result = _run_hook_command(install_hooks.STOP_HOOK_COMMAND_CODEX)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["continue"], True)
        self.assertNotIn("decision", payload)
        self.assertNotIn("systemMessage", payload)

    def test_codex_stop_hook_blocks_completion_claim_missing_completion_check(self):
        result = _run_hook_command(
            install_hooks.STOP_HOOK_COMMAND_CODEX,
            input_text=json.dumps({
                "hook_event_name": "Stop",
                "last_assistant_message": "The requested change is complete and tests pass.",
            }),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block", msg=payload)
        self.assertIn("[completion-check]", payload["reason"])
        self.assertNotIn("systemMessage", payload)

    def test_codex_stop_hook_allows_explanatory_response_without_completion_check(self):
        result = _run_hook_command(
            install_hooks.STOP_HOOK_COMMAND_CODEX,
            input_text=json.dumps({
                "hook_event_name": "Stop",
                "last_assistant_message": "Here is why that hook behavior repeated too much.",
            }),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("continue"), True, msg=payload)
        self.assertNotIn("decision", payload)
        self.assertNotIn("systemMessage", payload)

    def test_codex_stop_hook_allows_valid_completion_check(self):
        result = _run_hook_command(
            install_hooks.STOP_HOOK_COMMAND_CODEX,
            input_text=json.dumps({
                "hook_event_name": "Stop",
                "last_assistant_message": (
                    "[completion-check]\n"
                    "- verification-before-completion: done\n"
                    "- skill-call: verification-before-completion (this turn)\n"
                    "- acceptance-criteria:\n"
                    "  - crit-a: aligned [source: system-doc]\n"
                    "- claim-evidence-map:\n"
                    "  - claim: did the thing\n"
                    "    criterion: crit-a\n"
                    "    evidence: ran cmd 3/3 OK\n"
                    "    verdict: pass\n"
                    "- unverified:\n"
                    "  - none\n"
                    "- evidence: ran cmd\n"
                    "[io-trace]\n"
                    "- skills-loaded: [verification-before-completion]"
                ),
            }),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("continue"), True, msg=payload)
        self.assertNotIn("decision", payload)
        self.assertNotIn("systemMessage", payload)

    def test_codex_stop_hook_uses_validator_command(self):
        command = install_hooks.STOP_HOOK_COMMAND_CODEX

        self.assertIn("claude_stop_verification_hook.py", command)
        self.assertNotIn("completion-reminder: " + "Before " + "final " + "response", command)

    def test_codex_foreground_hooks_emit_valid_json_system_messages(self):
        cases = (
            (install_hooks.PROMPT_PENDING_MERGE_COMMAND_CODEX, "merge-companion prompt-check"),
            (install_hooks.HOOK_COMMAND_CODEX, "hook-reminder"),
            (install_hooks.WEB_SEARCH_FIRST_COMMAND_CODEX, "web-search-first"),
            (install_hooks.SESSION_START_COMMAND_CODEX, "merge-companion session-check"),
        )

        for command, expected in cases:
            with self.subTest(expected=expected):
                if command == install_hooks.WEB_SEARCH_FIRST_COMMAND_CODEX:
                    result = _run_hook_command(command, env=_strict_hook_env())
                else:
                    result = _run_hook_command(command)
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["continue"], True)
                self.assertIn(expected, payload["systemMessage"])
                self.assertIsNone(re.search(r"[\uac00-\ud7a3]", payload["systemMessage"]))
                self.assertNotIn("User:", payload["systemMessage"])
                self.assertNotIn("Tech:", payload["systemMessage"])

    def test_codex_pending_merge_hooks_emit_explicit_hook_verified_clean_contract(self):
        cases = (
            install_hooks.PROMPT_PENDING_MERGE_COMMAND_CODEX,
            install_hooks.SESSION_START_COMMAND_CODEX,
        )

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            for command in cases:
                with self.subTest(command=command):
                    result = _run_hook_command(command, env=env)
                    self.assertEqual(result.returncode, 0, msg=result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertIn(
                        "merge-companion-precheck: clean (hook-verified)",
                        payload["systemMessage"],
                    )
                    self.assertIn(
                        "do not run an extra shell manifest check",
                        payload["systemMessage"],
                    )

    def test_codex_prompt_pending_merge_hook_ignores_other_platform_pending_manifest(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            manifest = Path(temp_home) / ".ghost-alice" / "pending-merges" / "claude" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({
                    "version": 1,
                    "platform": "claude",
                    "entries": [{"decided": False} for _ in range(30)],
                })
                + "\n",
                encoding="utf-8",
            )

            result = _run_hook_command(install_hooks.PROMPT_PENDING_MERGE_COMMAND_CODEX, env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("merge-companion-precheck: clean (hook-verified)", payload["systemMessage"])
        self.assertNotIn("claude", payload["systemMessage"])
        self.assertNotIn("30 undecided", payload["systemMessage"])

    def test_codex_visibility_command_sets_profile_and_blocks_original_prompt(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            result = _run_hook_command(
                install_hooks.PROMPT_PENDING_MERGE_COMMAND_CODEX,
                input_text=json.dumps({"prompt": "/visibility dynamic"}),
                env=env,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["decision"], "block")
            self.assertIn("agent visibility profile set to dynamic", payload["reason"])

            config_path = Path(temp_home) / ".ghost-alice" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["agent_visibility"]["profile"], "dynamic")

    def test_codex_visibility_command_without_profile_reports_current_status(self):
        with tempfile.TemporaryDirectory() as temp_home:
            config_dir = Path(temp_home) / ".ghost-alice"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps({
                    "schema_version": "ghost-alice-config.v1",
                    "agent_visibility": {"profile": "minimal"},
                    "strict_session_log": {"mode": "always"},
                }),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = temp_home
            result = _run_hook_command(
                install_hooks.PROMPT_PENDING_MERGE_COMMAND_CODEX,
                input_text=json.dumps({"prompt": "/visibility"}),
                env=env,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["decision"], "block")
            self.assertIn("agent visibility profile is minimal", payload["reason"])

    def test_foreground_hook_messages_run_under_cmd_exe(self):
        for cmd, env in (
            (install_hooks.HOOK_COMMAND, None),
            (install_hooks.WEB_SEARCH_FIRST_COMMAND, _strict_hook_env()),
            (install_hooks.SESSION_START_COMMAND, None),
        ):
            result = _run_hook_command_via_cmd(cmd, env=env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("Internal instruction:", result.stdout)
            self.assertIn("User:", result.stdout)
            self.assertIn("Tech:", result.stdout)

    def test_claude_stop_hook_runs_under_cmd_exe(self):
        result = _run_hook_command_via_cmd(install_hooks.STOP_HOOK_COMMAND)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["continue"], True)
        self.assertNotIn("decision", payload)
        self.assertNotIn("systemMessage", payload)

    def test_codex_stop_hook_runs_under_cmd_exe(self):
        result = _run_hook_command_via_cmd(install_hooks.STOP_HOOK_COMMAND_CODEX)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["continue"], True)
        self.assertNotIn("decision", payload)
        self.assertNotIn("systemMessage", payload)

    def test_claude_io_trace_hook_does_not_depend_on_bare_bash(self):
        command = install_hooks._entry_command(
            install_hooks._platform_io_trace_entry("claude", "PostToolUse")
        )
        payload_text = _visible_and_runner_payload_text(command)

        self.assertIn("io_trace_hook.py", payload_text)
        self.assertNotIn("bash ", payload_text)
        self.assertNotIn("bash.exe", payload_text.lower())

    def test_web_search_first_hook_removed_on_uninstall(self):
        self._create_platform_dir("claude")
        install_hooks.install_hook("claude")
        result = install_hooks.uninstall_hook("claude")
        self.assertEqual(result, "removed")
        settings = self._read_settings("claude")
        hooks = settings["hooks"].get("UserPromptSubmit", [])
        cmds = [h["command"] for entry in hooks for h in entry["hooks"]]
        self.assertFalse(any(install_hooks.WEB_SEARCH_FIRST_MARKER in c for c in cmds))

    def test_session_intent_hook_removed_on_uninstall(self):
        self._create_platform_dir("claude")
        install_hooks.install_hook("claude")
        result = install_hooks.uninstall_hook("claude")
        self.assertEqual(result, "removed")
        settings = self._read_settings("claude")
        hooks = settings["hooks"].get("UserPromptSubmit", [])
        cmds = [h["command"] for entry in hooks for h in entry["hooks"]]
        self.assertFalse(any(install_hooks.SESSION_INTENT_MARKER in c for c in cmds))


if __name__ == "__main__":
    unittest.main()
