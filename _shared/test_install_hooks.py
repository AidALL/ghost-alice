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
import builtins
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hook_profile_gate
import hook_definitions
import install_hooks
from test_completion_skill_evidence import assert_stop_observation


LEGACY_PROMPT_PENDING_MERGE_ENTRY = {
    "matcher": "",
    "hooks": [{"type": "command", "command": "echo legacy # " + install_hooks.PROMPT_PENDING_MERGE_MARKER}],
}
LEGACY_WEB_SEARCH_ENTRY = {
    "matcher": "",
    "hooks": [{"type": "command", "command": "echo old # [web-search-first]"}],
}


class _FailAfterPartialWrite:
    def __init__(self, handle, error: OSError) -> None:
        self._handle = handle
        self._error = error

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._handle.__exit__(exc_type, exc_value, traceback)

    def write(self, value):
        partial_length = max(1, len(value) // 2)
        self._handle.write(value[:partial_length])
        self._handle.flush()
        raise self._error

    def __getattr__(self, name):
        return getattr(self._handle, name)


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str]]:
    snapshot: dict[str, tuple[str, bytes | str]] = {}
    for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(candidate))
        elif candidate.is_dir():
            snapshot[relative] = ("directory", b"")
        else:
            snapshot[relative] = ("file", candidate.read_bytes())
    return snapshot


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


def _run_reviewed_stop_command(command, input_text, env=None, *, platform="claude",
                               response_purpose="completion", record_review=True):
    """Exercise generated commands against explicitly owned synthetic evidence."""
    from claude_stop_verification_hook import _assistant_text_this_turn
    from test_completion_skill_evidence import StopHookSkillEvidenceTests
    payload = json.loads(input_text)
    text = payload.get("last_assistant_message")
    if text is None:
        entries = [json.loads(row) for row in Path(payload["transcript_path"]).read_text().splitlines()]
        text = _assistant_text_this_turn(entries)
    hook_env = {key: value for key, value in (env or os.environ).items()
                if key != "CODEX_THREAD_ID" and not key.startswith("GHOST_ALICE_")}
    hook_env["GHOST_ALICE_AGENT_VISIBILITY"] = (env or {}).get("GHOST_ALICE_AGENT_VISIBILITY", "strict")
    if shared := (env or os.environ).get(install_hooks.HOOK_SHARED_DIR_ENV):
        hook_env[install_hooks.HOOK_SHARED_DIR_ENV] = shared
    payload.setdefault("session_id", "installer-stop")
    with tempfile.TemporaryDirectory() as temporary:
        hook_env['HOME'] = temporary
        hook_env['USERPROFILE'] = temporary
        hook_env['GHOST_ALICE_GHOST_ROOT'] = str(Path(temporary) / '.ghost-alice')
        if payload.get('transcript_path') and platform == 'claude':
            # These synthetic rows need the same explicit owner as the hook.
            entries = [json.loads(row) for row in Path(payload['transcript_path']).read_text().splitlines()]
            transcript = Path(temporary) / 'owned-transcript.jsonl'
            transcript.write_text(''.join(json.dumps(dict(row, sessionId=payload['session_id'])) + '\n'
                for row in entries))
            payload['transcript_path'] = str(transcript)
        StopHookSkillEvidenceTests._review_format_fixture(Path(temporary), platform, text, hook_env,
            session_id=payload["session_id"], response_purpose=response_purpose,
            record_review=record_review)
        if callable(command):
            with patch.dict(os.environ, hook_env, clear=True), patch.object(
                install_hooks, '_home', return_value=Path(temporary)
            ):
                command = command()
        result = _run_hook_command(command, json.dumps(payload), env=hook_env)
        log = Path(temporary) / '.ghost-alice/session-logs' / platform / payload['session_id'] / 'strict-hook-output.jsonl'
        result.stop_observation = None
        if log.exists():
            for line in log.read_text().splitlines():
                raw = json.loads(json.loads(line)['stdout'])
                if 'response_assessment' in raw:
                    result.stop_observation = raw['response_assessment']
        return result


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
            "source": "interpreted-as",
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


def _make_node_executable(node: Path) -> None:
    if os.name != "nt":
        node.chmod(node.stat().st_mode | 0o100)


def _expected_ghost_alice_skill_names() -> list[str]:
    names = install_hooks._ghost_alice_installed_skill_names()
    if not names:
        raise AssertionError("Ghost-ALICE skill catalog discovery returned no skills")
    return names


class TestHookProfileGateWindowsCommandCompatibility(unittest.TestCase):
    def test_dispatcher_command_uses_dynamic_node_runtime_sentinel(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            selected_node = root / "versioned-runtime" / ("node.exe" if os.name == "nt" else "node")
            selected_node.parent.mkdir(parents=True, exist_ok=True)
            selected_node.write_text("# selected node\n", encoding="utf-8")
            _make_node_executable(selected_node)

            for platform in ("claude", "codex"):
                with self.subTest(platform=platform):
                    command = install_hooks._dispatcher_hook_command(
                        platform,
                        "PreToolUse",
                        "tool-checkpoint",
                        "[test]",
                        "tool-checkpoint",
                        selected_node,
                    )

                    self.assertTrue(
                        command.startswith(
                            install_hooks._quote_command_arg(
                                install_hooks.runtime_config.HOOK_NODE_SENTINEL
                            )
                        )
                    )
                    self.assertNotIn(selected_node.as_posix(), command)

    def test_dynamic_node_command_requires_the_profile_gate_runner(self):
        command = (
            f'"{install_hooks.runtime_config.HOOK_NODE_SENTINEL}" '
            '"C:/managed/ghost-alice-hook.mjs" --platform codex'
        )

        with (
            patch.object(install_hooks, "_resolve_hook_runner_script", return_value=""),
            self.assertRaisesRegex(RuntimeError, "dynamic Node hook command requires"),
        ):
            install_hooks._hook_runner_command(
                "tool-checkpoint",
                command,
                "[tool-checkpoint] pre-tool-check",
                platform_key="codex",
            )

    def test_installer_prefers_codex_configured_node_over_path_node(self):
        """The generated hook command must use the runtime trusted by the gate."""
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            configured_node = root / "codex-runtime" / ("node.exe" if os.name == "nt" else "node")
            configured_node.parent.mkdir(parents=True, exist_ok=True)
            configured_node.write_text("# configured node\n", encoding="utf-8")
            _make_node_executable(configured_node)
            path_node = root / "path-runtime" / ("node.exe" if os.name == "nt" else "node")
            path_node.parent.mkdir(parents=True, exist_ok=True)
            path_node.write_text("# path node\n", encoding="utf-8")
            _make_node_executable(path_node)
            config_toml = root / ".codex" / "config.toml"
            config_toml.parent.mkdir(parents=True, exist_ok=True)
            config_toml.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(configured_node.as_posix())}\n",
                encoding="utf-8",
            )

            with (
                patch.object(install_hooks, "_home", return_value=root),
                patch.object(install_hooks, "_resolve_codex_config_toml", return_value=config_toml),
                patch.object(install_hooks.shutil, "which", return_value=str(path_node)),
                patch.dict(
                    os.environ,
                    {"GHOST_ALICE_NODE": "", "NODE_REPL_NODE_PATH": ""},
                    clear=False,
                ),
            ):
                selected = install_hooks._resolve_node_runtime("codex")

        self.assertEqual(Path(selected).resolve(), configured_node.resolve())

    def test_installer_preserves_path_node_priority_for_claude(self):
        """Claude hooks stay independent from a concurrently installed Codex runtime."""
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            configured_node = root / "codex-runtime" / ("node.exe" if os.name == "nt" else "node")
            configured_node.parent.mkdir(parents=True, exist_ok=True)
            configured_node.write_text("# configured node\n", encoding="utf-8")
            _make_node_executable(configured_node)
            path_node = root / "path-runtime" / ("node.exe" if os.name == "nt" else "node")
            path_node.parent.mkdir(parents=True, exist_ok=True)
            path_node.write_text("# path node\n", encoding="utf-8")
            _make_node_executable(path_node)
            config_toml = root / ".codex" / "config.toml"
            config_toml.parent.mkdir(parents=True, exist_ok=True)
            config_toml.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(configured_node.as_posix())}\n",
                encoding="utf-8",
            )

            with (
                patch.object(install_hooks, "_home", return_value=root),
                patch.object(install_hooks, "_resolve_codex_config_toml", return_value=config_toml),
                patch.object(install_hooks.shutil, "which", return_value=str(path_node)),
                patch.dict(
                    os.environ,
                    {"GHOST_ALICE_NODE": "", "NODE_REPL_NODE_PATH": ""},
                    clear=False,
                ),
            ):
                selected = install_hooks._resolve_node_runtime("claude")

        self.assertEqual(Path(selected).resolve(), path_node.resolve())

    def test_configured_node_runtime_absolute_path_is_allowed_in_hook_payload(self):
        """Configured Node runtimes are valid hook executables even outside fixed roots."""
        with tempfile.TemporaryDirectory() as temp_home:
            codex_home = Path(temp_home) / ".codex"
            node = codex_home / "runtime" / ("node.exe" if os.name == "nt" else "node")
            node.parent.mkdir(parents=True, exist_ok=True)
            node.write_text("# fake node\n", encoding="utf-8")
            _make_node_executable(node)
            config_toml = codex_home / "config.toml"
            config_toml.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n",
                encoding="utf-8",
            )
            command = f'"{node.as_posix()}" "{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}"'

            with patch.dict(os.environ, {"HOME": temp_home, "CODEX_HOME": str(codex_home)}, clear=False):
                argv = hook_profile_gate._validate_shell_command(command)

        self.assertEqual(Path(argv[0]).resolve(), node.resolve())

    def test_configured_node_runtime_must_be_named_node(self):
        """A configured runtime path is not a generic absolute executable bypass."""
        with tempfile.TemporaryDirectory() as temp_home:
            codex_home = Path(temp_home) / ".codex"
            runtime = codex_home / "runtime" / ("python.exe" if os.name == "nt" else "python")
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_text("# fake runtime\n", encoding="utf-8")
            config_toml = codex_home / "config.toml"
            config_toml.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(runtime.as_posix())}\n",
                encoding="utf-8",
            )
            command = f'"{runtime.as_posix()}" "{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}"'

            with patch.dict(os.environ, {"HOME": temp_home, "CODEX_HOME": str(codex_home)}, clear=False):
                with self.assertRaises(hook_profile_gate.HookCommandRejected):
                    hook_profile_gate._validate_shell_command(command)

    def test_wrapped_command_validation_strips_marker_comment(self):
        command = f'"{sys.executable}" -c "import sys; sys.exit(0)" # [hook-reminder] AGENTS.md'

        argv = hook_profile_gate._validate_shell_command(command)

        self.assertNotIn("#", argv)
        self.assertNotIn("[hook-reminder]", argv)
        self.assertNotIn("AGENTS.md", argv)

    def test_main_ignores_outer_marker_args_from_windows_shell(self):
        with tempfile.TemporaryDirectory() as owned, patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT":str(Path(owned)/"ghost")}):
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

    def test_generated_hook_messages_use_bridge_labels(self):
        for command, env in (
            (install_hooks.HOOK_COMMAND, None),
            (install_hooks.SESSION_INTENT_COMMAND, None),
            (install_hooks.SESSION_START_COMMAND, None),
        ):
            result = _run_hook_command(command, env=env)
            self.assertEqual(result.returncode, 0)
            if command == install_hooks.SESSION_START_COMMAND:
                self.assertIn("merge-companion", result.stdout)
                self.assertNotIn("Internal instruction:", result.stdout)
                continue
            self.assertIn("Internal instruction:", result.stdout)
            self.assertIn("User:", result.stdout)
            self.assertIn("Tech:", result.stdout)
            self.assertNotIn("Non-developer note", result.stdout)
            self.assertNotIn("Developer note", result.stdout)

    def test_session_intent_hook_command_uses_repo_tmp_root(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ,
                {"HOME":home,"CODEX_HOME":home+"/.codex","GHOST_ALICE_SESSION_INTENT_ROOT":""}):
            command = install_hooks._session_intent_analyzer_command(platform="codex", output_format="json")
        parts = shlex.split(command)

        self.assertIn("--root", parts)
        root = Path(parts[parts.index("--root") + 1])
        self.assertEqual(root, install_hooks._repo_root_from_this_file() / ".tmp" / "session-intent")

    def test_default_hook_shared_dir_uses_runtime_core(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env = {
                "HOME": temp_home,
                install_hooks.HOOK_SHARED_DIR_ENV: "",
                "GHOST_ALICE_RUNTIME_SHARED_DIR": "",
            }
            with patch.dict(os.environ, env, clear=False):
                shared = install_hooks._hook_shared_dir_for_platform("codex")

        self.assertEqual(shared, Path(temp_home) / ".ghost-alice" / "runtime" / "current" / "_shared")

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

    def test_hook_reminder_delegates_intake_and_routing_to_canonical_owners(self):
        self.assertIn("after exact session-intent intake", install_hooks.HOOK_INTERNAL)
        self.assertIn("follows task-router", install_hooks.HOOK_INTERNAL)
        self.assertIn("register the Core response-route", install_hooks.HOOK_INTERNAL)
        self.assertIn("common contract's Work Selection section", install_hooks.HOOK_INTERNAL)
        self.assertNotIn("downstream-gates.json", install_hooks.HOOK_INTERNAL)
        self.assertNotIn("[tool-checkpoint]", install_hooks.HOOK_INTERNAL)

    def test_session_intent_reminder_preserves_receipt_and_data_minimization(self):
        self.assertIn("digest-only intake", install_hooks.SESSION_INTENT_INTERNAL)
        self.assertIn("only when it affects a decision or a semantic update", install_hooks.SESSION_INTENT_INTERNAL)
        self.assertIn("using the exact receipt", install_hooks.SESSION_INTENT_INTERNAL)
        self.assertIn("exclude raw prompts, conversation/tool output, system instructions and secrets", install_hooks.SESSION_INTENT_INTERNAL)

    def test_codex_hook_reminder_command_uses_task_router_gate_helper(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ,
                {"HOME":home,"CODEX_HOME":home+"/.codex","GHOST_ALICE_SESSION_INTENT_ROOT":""}):
            entry_command = install_hooks._entry_command(
                install_hooks._platform_hook_entry("codex", "UserPromptSubmit")
            )
        command = _visible_and_runner_payload_text(entry_command)

        self.assertIn("task_router_reminder_hook.py", command)
        self.assertIn("--root", command)
        hook_command = next(
            line for line in command.splitlines()
            if "task_router_reminder_hook.py" in line
        )
        parts = shlex.split(hook_command)
        root = Path(parts[parts.index("--root") + 1])
        self.assertEqual(root, install_hooks._repo_root_from_this_file() / ".tmp" / "session-intent")

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
            self.assertIn("intake-ready: current input recorded", first_message)
            self.assertNotIn(install_hooks.HOOK_INTERNAL, first_message)
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
            self.assertIn("intake-ready: current input recorded", message)
            self.assertNotIn("decision=allow", message)
            import session_intent_ledger
            ledger_command = message.split("intent-ledger: use ", 1)[1].split(
                " after session-intent preflight.", 1)[0]
            ledger_args = shlex.split(ledger_command)
            self.assertEqual(ledger_args, [
                sys.executable, str(Path(session_intent_ledger.__file__).resolve()),
                "--read-intent", "--root", str(root),
                "--platform", "codex", "--session-id", "s-codex-router",
            ])
            self.assertNotIn(install_hooks.HOOK_INTERNAL, message)
            self.assertNotIn("reuse retained instructions, reload if lost", message)
            self.assertNotIn("task-router-step", message)
            self.assertNotIn("atomic meaning decomposition", message)
            self.assertNotIn("downstream-gate:", message)

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
            self.assertIn("intake-ready: current input recorded", message)
            self.assertNotIn(install_hooks.HOOK_INTERNAL, message)
            self.assertNotIn("task-router withheld", message)

    def test_completion_reminder_delegates_evidence_review_and_preserves_original_time(self):
        self.assertIn("Core authenticates", install_hooks.STOP_HOOK_INTERNAL)
        self.assertIn("follows verification-before-completion", install_hooks.STOP_HOOK_INTERNAL)
        self.assertIn("retains unchanged evidence at its original source and time", install_hooks.STOP_HOOK_INTERNAL)
        self.assertIn("Printed skill or trace fields do not establish evidence", install_hooks.STOP_HOOK_INTERNAL)

    def test_claude_stop_records_printed_skill_markers_without_evidence_as_unaccepted(self):
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
            result = _run_reviewed_stop_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        assert_stop_observation(self, payload, issue='claim-evidence-map', repair_target='completion_metadata')

    def test_claude_stop_observes_unreviewed_answer_without_retry_or_answer_replay(self):
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
                                    "- acceptance-criteria:\n"
                                    "  - c1: cite two papers [source: user-said]\n"
                                    "- claim-evidence-map:\n"
                                    "  - claim: two papers cited\n"
                                    "    criterion: c1\n"
                                    "    evidence: search result\n"
                                    "    verdict: pass\n"
                                    "- unverified:\n"
                                    "  - none\n"
                                    "- evidence: search result"
                                ),
                            }],
                        },
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            # Stop records the unreviewed body; it cannot hold or regenerate the host's answer.
            headless_env = {**os.environ, "GHOST_ALICE_AGENT_VISIBILITY": "strict", "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}
            result = _run_reviewed_stop_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
                env=headless_env,
                record_review=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        observed = assert_stop_observation(self, payload, issue='assessment-required')
        self.assertIsNotNone(observed['repair_target'])
        self.assertNotIn('Paper A summary', observed['body_issue'])

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
                                    "- acceptance-criteria:\n"
                                    "  - crit-a: aligned work [source: system-doc]\n"
                                    "- claim-evidence-map:\n"
                                    "  - claim: did the thing\n"
                                    "    criterion: crit-a\n"
                                    "    evidence: ran cmd 3/3 OK\n"
                                    "    verdict: pass\n"
                                    "- unverified:\n"
                                    "  - none\n"
                                    "- evidence: ran cmd"
                                ),
                            }],
                        },
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            result = _run_reviewed_stop_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("continue"), True)
        self.assertNotIn("decision", payload)

    def test_claude_stop_keeps_invalid_completion_body_unaccepted(self):
        # Loading a skill cannot authenticate an incomplete completion body.
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
            result = _run_reviewed_stop_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        assert_stop_observation(self, payload, issue='claim-evidence-map', repair_target='completion_metadata')

    def test_claude_stop_hook_validates_only_final_response(self):
        # AskUserQuestion answers and tool results are not user prompts and do not reset the turn boundary. Validate only the final response in the span; earlier completion-checks cannot establish its closure.
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "transcript.jsonl"
            transcript.write_text(
                "\n".join([
                    json.dumps({"type": "user", "message": {"role": "user", "content": "Do it."}}),
                    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "name": "Skill",
                         "input": {"skill": "verification-before-completion"}}]}}),
                    # earlier response carries an INVALID completion-check (a claim without evidence)
                    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "text", "text": (
                            "[completion-check]\n"
                            "- acceptance-criteria:\n"
                            "  - c1: stale [source: system-doc]\n"
                            "- claim-evidence-map:\n"
                            "  - claim: stale\n"
                            "    criterion: c1\n"
                            "    verdict: pass\n"
                            "- unverified:\n"
                            "  - none")}]}}),
                    # an AskUserQuestion answer returns as a tool_result user entry (NOT a real prompt)
                    json.dumps({"type": "user", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": "abc", "content": "option A"}]}}),
                    # FINAL response: a fully valid completion-check
                    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "text", "text": (
                            "[completion-check]\n"
                            "- acceptance-criteria:\n"
                            "  - crit-a: aligned [source: system-doc]\n"
                            "- claim-evidence-map:\n"
                            "  - claim: did the thing\n"
                            "    criterion: crit-a\n"
                            "    evidence: ran cmd 3/3 OK\n"
                            "    verdict: pass\n"
                            "- unverified:\n"
                            "  - none\n"
                            "- evidence: ran cmd")}]}}),
                ]) + "\n",
                encoding="utf-8",
            )
            result = _run_reviewed_stop_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        assert_stop_observation(self, payload)

    def test_claude_stop_hook_allows_explanatory_final_response_without_completion_check(self):
        # An authenticated explanatory response remains distinct from a completion submission.
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
            result = _run_reviewed_stop_command(
                install_hooks.STOP_HOOK_COMMAND,
                response_purpose="explanation",
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        assert_stop_observation(self, payload)

    def test_claude_stop_keeps_declared_completion_without_proof_unaccepted(self):
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
            result = _run_reviewed_stop_command(
                install_hooks.STOP_HOOK_COMMAND,
                input_text=json.dumps({"transcript_path": str(transcript)}),
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        assert_stop_observation(self, payload, issue='[completion-check]', repair_target='completion_metadata')

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
            if command == install_hooks.SESSION_START_COMMAND:
                self.assertIn("merge-companion", result.stdout)
                self.assertFalse(internal_lines)
                self.assertIsNone(re.search(r"[\uac00-\ud7a3]", result.stdout))
            else:
                self.assertTrue(internal_lines)
            for line in internal_lines:
                self.assertIsNone(re.search(r"[\uac00-\ud7a3]", line))

    def test_tool_checkpoint_runtime_hook_uses_concise_enforced_instruction(self):
        """The hook points to one field owner and still runs through its host command."""
        instruction = install_hooks.TOOL_CHECKPOINT_INTERNAL
        self.assertIn("common contract's io-trace Transparency Rule", instruction)
        self.assertIn('Native checks and strict audit run independently', instruction)
        self.assertNotIn('rejected-alternatives', instruction)
        contract = (Path(__file__).resolve().parents[1] / 'platforms/common-governance.md').read_text()
        for field in ('intent', 'why', 'stage: before-tool', 'contract-ref', 'contract-check'):
            self.assertIn(field, contract)
        result = _run_hook_command(install_hooks.TOOL_CHECKPOINT_COMMAND, env=_strict_hook_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(instruction, result.stdout)

    def test_codex_dispatcher_tool_checkpoint_returns_pre_tool_use_context_shape_with_legacy_block_record(self):
        """Codex PreToolUse preserves context and leaves native permission to its host."""
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
        output = first_payload.get("hookSpecificOutput", {})
        self.assertIn(output.get("hookEventName"), (None, "PreToolUse"))
        self.assertNotIn("permissionDecision", output)

        self.assertNotIn("continue", first_payload)
        self.assertNotIn("systemMessage", first_payload)

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assertNotIn("permissionDecision", second_payload.get("hookSpecificOutput", {}))

    def test_codex_dispatcher_routine_tool_checks_add_no_input_guidance(self):
        """Recorded intake keeps checking each tool call but surfaces once per user input."""
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
        self.assertNotIn("additionalContext", first_payload.get("hookSpecificOutput", {}))

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assert_no_pretool_deny(second_payload)
        self.assertNotIn("additionalContext", second_payload.get("hookSpecificOutput", {}))

        self.assertEqual(third.returncode, 0, msg=third.stderr)
        third_payload = json.loads(third.stdout)
        self.assert_no_pretool_deny(third_payload)
        self.assertNotIn("additionalContext", third_payload.get("hookSpecificOutput", {}))

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

    def test_codex_dispatcher_parallel_routine_checks_add_no_input_guidance(self):
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
        self.assertEqual(surfaced_count, 0)

    def test_dispatcher_install_copies_companion_modules(self):
        # Dispatcher companion-module invariant: the dispatcher imports a relative .mjs module; the installer must copy it alongside, or the installed enforcer fails ERR_MODULE_NOT_FOUND.
        prev_home = os.environ.get("HOME")
        with tempfile.TemporaryDirectory() as temp_home:
            os.environ["HOME"] = temp_home
            try:
                install_hooks._ensure_hook_dispatcher_installed()
                hooks_dir = Path(temp_home) / ".ghost-alice" / "hooks"
                self.assertTrue((hooks_dir / "ghost-alice-hook.mjs").exists())
                self.assertTrue(
                    (hooks_dir / "session_intent_snapshot.mjs").exists(),
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
        """Codex checkpoints leave native permission with the host across input and tool identities."""
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
        """A closed audit record supplies no native permission decision."""
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
                self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))


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
        """Codex checkpoints do not classify shell payloads for native permission."""
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
        """Codex developer-tool shell reads preserve the host native-permission boundary."""
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

    def test_codex_dispatcher_ignores_legacy_gate_permission_for_shell(self):
        result = self.run_codex_pretool({
            "session_id": "s-shell-downstream-closed",
            "hook_event_name": "PreToolUse",
            "tool_name": "shell",
            "tool_input": {"cmd": "opaque shell text"},
        }, downstream_decision="block")
        payload = json.loads(result.stdout)
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))


    def test_codex_dispatcher_tool_checkpoint_does_not_parse_compound_shell_mutation(self):
        """Compound shell payloads do not create a checkpoint permission decision."""
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

    def test_codex_dispatcher_tool_checkpoint_legacy_block_does_not_decide_tool_permission(self):
        """Stored detector state does not grant or deny native tool permission."""
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
        output = payload.get("hookSpecificOutput", {})
        self.assertNotIn("permissionDecision", output)



    def test_codex_dispatcher_tool_checkpoint_legacy_block_does_not_deny_session_intent_recovery(self):
        """Stored gate records do not deny session-intent recovery calls."""
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
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))


    def test_codex_dispatcher_tool_checkpoint_allows_governance_skill_doc_bootstrap_before_semantic_intent(self):
        """Governance instruction reads remain available while semantic intake is pending."""
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

    def test_codex_dispatcher_tool_checkpoint_legacy_block_does_not_deny_historical_doc_reads(self):
        """Stored gate records do not deny instruction calls or document reads."""
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
        self.assertNotIn("permissionDecision", json.loads(skill_call.stdout).get("hookSpecificOutput", {}))
        self.assertEqual(doc_read.returncode, 0, msg=doc_read.stderr)
        self.assertNotIn("permissionDecision", json.loads(doc_read.stdout).get("hookSpecificOutput", {}))

    def test_codex_dispatcher_preserves_host_permission_for_governance_skill_docs(self):
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
                        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))

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
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))


    def test_claude_dispatcher_tool_checkpoint_blocks_governance_skill_bootstrap_when_gate_blocks(self):
        """Claude Skill calls leave native permission with the host."""
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
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))

    def test_claude_dispatcher_tool_checkpoint_returns_pre_tool_use_context_shape_with_legacy_block_record(self):
        """Claude PreToolUse preserves context and leaves native permission to its host."""
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
        output = first_payload.get("hookSpecificOutput", {})
        self.assertIn(output.get("hookEventName"), (None, "PreToolUse"))
        self.assertNotIn("permissionDecision", output)

        self.assertNotIn("continue", first_payload)
        self.assertNotIn("systemMessage", first_payload)

        self.assertEqual(second.returncode, 0, msg=second.stderr)
        second_payload = json.loads(second.stdout)
        self.assertNotIn("permissionDecision", second_payload.get("hookSpecificOutput", {}))

    def test_claude_dispatcher_tool_checkpoint_does_not_deny_by_input_event_or_tool_input(self):
        """Claude and Codex checkpoints leave tool permission to their native hosts."""
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
        self._hook_shared_patcher = patch.dict(
            os.environ,
            {install_hooks.HOOK_SHARED_DIR_ENV: str(Path(install_hooks.__file__).parent)},
            clear=False,
        )
        self._hook_shared_patcher.start()
        # Platform detect functions use the patched _home, so no separate patch is needed.

    def tearDown(self):
        self._hook_shared_patcher.stop()
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

    def _symlink_file_or_skip(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"file symlinks unavailable: {error}")

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

    def _write_codex_node_runtime(self) -> Path:
        """Create a fake Codex-managed node runtime path in config.toml."""
        node = self.fake_home / "codex-runtime" / "bin" / ("node.exe" if os.name == "nt" else "node")
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_text("# fake node\n", encoding="utf-8")
        _make_node_executable(node)
        config_toml = self._codex_config_toml()
        config_toml.parent.mkdir(parents=True, exist_ok=True)
        config_toml.write_text(
            "[mcp_servers.node_repl.env]\n"
            f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n",
            encoding="utf-8",
        )
        return node

    def _create_addon_source(self, skill_name: str = "autopilot-mode") -> Path:
        """Create a manifest-verified addon source with one Claude skill target."""
        source = self.fake_home / "addon-source"
        addon_dir = source / "addons" / skill_name
        skill_dir = addon_dir / "skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {skill_name}\n"
            'description: "Fixture addon skill."\n'
            "---\n",
            encoding="utf-8",
        )
        (source / "addons-manifest.json").write_text(
            json.dumps({
                "manifest_version": 1,
                "addons": [{
                    "id": skill_name,
                    "path": f"addons/{skill_name}",
                    "min_core_version": "0.1.0",
                    "tags": ["test"],
                }],
            }),
            encoding="utf-8",
        )
        (addon_dir / "addon.json").write_text(
            json.dumps({
                "addon_version": "0.1.0",
                "addon_id": skill_name,
                "skills": [{
                    "name": skill_name,
                    "source": "skill",
                    "skill_dir": "skill",
                }],
                "platforms": ["claude"],
                "depends_on_core": [],
                "secrets": [],
            }),
            encoding="utf-8",
        )
        return source


class TestInstallHook(TempHomeTestCase):
    def test_reinstall_replaces_an_old_intake_registration_with_the_current_command(self):
        # 0.5.0 wrote the instruction text into the registration; it is an old form, not a caller policy.
        from install_destination import InstallDestination, use
        for platform in ('codex', 'claude'):
            with self.subTest(platform=platform):
                destination = InstallDestination(mode='isolated', owned_root=str(self.fake_home),
                    platform_dir=str(self.fake_home / platform), skills_dir=str(self.fake_home / 'skills'),
                    ghost_root=str(self.fake_home / 'ghost'), ledger_root=str(self.fake_home / 'ledger'),
                    project_root=str(self.fake_home / 'project'),
                    core_source_root=str(Path(install_hooks.__file__).resolve().parents[1]))
                path = Path(destination.platform_dir) / ('hooks.json' if platform == 'codex' else 'settings.json')

                def intake_commands():
                    return [h['command'] for r in json.loads(path.read_text())['hooks']['UserPromptSubmit']
                            for h in r['hooks'] if '[hook-runner:session-intent]' in h['command']]

                with use(destination):
                    install_hooks.install_hook(platform, destination=destination)
                    current = intake_commands()
                    command = install_hooks._session_intent_analyzer_command(platform=platform, output_format='json', payload_mode=True)
                    entry = install_hooks._hook_runner_command_entry('session-intent',
                        command + ' --internal-b64 ' + base64.urlsafe_b64encode(b'Old instruction').decode(),
                        install_hooks.SESSION_INTENT_MARKER, platform_key=platform)
                    settings = json.loads(path.read_text())
                    for row in settings['hooks']['UserPromptSubmit']:
                        row['hooks'] = [h for h in row['hooks'] if '[hook-runner:session-intent]' not in h['command']]
                    settings['hooks']['UserPromptSubmit'].append(entry)
                    path.write_text(json.dumps(settings))
                    install_hooks.install_hook(platform, destination=destination)
                self.assertEqual(intake_commands(), current)

    def test_legacy_managed_removal_preserves_custom_sibling(self):
        marker = install_hooks.LEGACY_SESSION_INTENT_MARKERS[0]
        rows = [{'matcher': 'keep-matcher', 'custom': 'keep-metadata', 'hooks': [
            {'type': 'command', 'command': 'echo retired # ' + marker},
            {'type': 'command', 'command': 'echo custom-sibling'}]}]
        self.assertEqual(install_hooks._remove_hook_entries(rows, marker), 1)
        self.assertEqual(rows, [{'matcher': 'keep-matcher', 'custom': 'keep-metadata', 'hooks': [
            {'type': 'command', 'command': 'echo custom-sibling'}]}])

    def test_managed_prompt_reinstall_preserves_custom_sibling_commands_and_settings(self):
        from install_destination import InstallDestination
        for platform in ('codex', 'claude'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                owned = Path(temporary)
                destination = InstallDestination(mode='isolated', owned_root=str(owned),
                    platform_dir=str(owned / platform), skills_dir=str(owned / 'skills'),
                    ghost_root=str(owned / 'ghost'), ledger_root=str(owned / 'exact-ledger'),
                    project_root=str(owned / 'exact-project'),
                    core_source_root=str(Path(install_hooks.__file__).resolve().parents[1]))
                # Existing installed managed commands contain the prior generated text.
                with patch.dict(os.environ, {install_hooks.HOOK_SHARED_DIR_ENV: '',
                                             'GHOST_ALICE_RUNTIME_SHARED_DIR': ''}), \
                     patch.object(install_hooks, 'HOOK_INTERNAL', 'previous generated router'), \
                     patch.object(install_hooks, 'SESSION_INTENT_INTERNAL', 'previous generated intake'):
                    self.assertEqual(install_hooks.install_hook(platform, destination=destination), 'installed')
                config = owned / platform / ('hooks.json' if platform == 'codex' else 'settings.json')
                before = json.loads(config.read_text())
                before['customSetting'] = {'preserve': ['value']}
                for row in before['hooks']['UserPromptSubmit']:
                    command = install_hooks._entry_command(row)
                    if any(marker in command for marker in (install_hooks.HOOK_MARKER, install_hooks.SESSION_INTENT_MARKER)):
                        row['customEntryMetadata'] = 'preserve'
                        row['hooks'].append({'type': 'command', 'command': 'echo custom-sibling'})
                config.write_text(json.dumps(before))
                with patch.dict(os.environ, {install_hooks.HOOK_SHARED_DIR_ENV: '',
                                             'GHOST_ALICE_RUNTIME_SHARED_DIR': ''}):
                    self.assertEqual(install_hooks.install_hook(platform, destination=destination), 'already')
                after = json.loads(config.read_text())
                self.assertEqual(after['customSetting'], before['customSetting'])
                custom = [row for row in after['hooks']['UserPromptSubmit']
                          if row.get('customEntryMetadata') == 'preserve']
                self.assertEqual(len(custom), 1)
                self.assertEqual(custom, [row for row in before['hooks']['UserPromptSubmit']
                                          if row.get('customEntryMetadata') == 'preserve'])
                for event in set(before['hooks']) - {'UserPromptSubmit'}:
                    self.assertEqual(after['hooks'][event], before['hooks'][event])
                commands = [hook['command'] for row in after['hooks']['UserPromptSubmit'] for hook in row['hooks']]
                self.assertEqual(sum(install_hooks.SESSION_INTENT_MARKER in command for command in commands), 1)
                self.assertFalse(any(install_hooks.HOOK_MARKER in command for command in commands))
                for command in commands:
                    decoded = _visible_and_runner_payload_text(command)
                    if 'session_intent_analyzer_hook.py' in decoded or 'task_router_reminder_hook.py' in decoded:
                        self.assertIn(str(owned / 'exact-ledger'), decoded)

    """Install behavior tests."""

    def _fake_node(self, directory: str) -> Path:
        node = self.fake_home / directory / ("node.exe" if os.name == "nt" else "node")
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_text("# fake node\n", encoding="utf-8")
        _make_node_executable(node)
        return node

    def _resolve_isolated_node_source(self, source: str, node: Path) -> str | None:
        codex_config = self._codex_config_toml()
        codex_config.unlink(missing_ok=True)
        runtime_config_file = install_hooks.runtime_config.config_path(self.fake_home)
        runtime_config_file.unlink(missing_ok=True)
        env = {
            "CODEX_HOME": str(codex_config.parent),
            "GHOST_ALICE_NODE": "",
            "NODE_REPL_NODE_PATH": "",
        }
        platform = "claude"
        if source == "env":
            env["GHOST_ALICE_NODE"] = str(node)
        elif source == "codex-config":
            platform = "codex"
            codex_config.parent.mkdir(parents=True, exist_ok=True)
            codex_config.write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n",
                encoding="utf-8",
            )
        elif source == "registration":
            install_hooks.runtime_config.save_config(
                {"hook_runtime": {"node": {platform: str(node.resolve())}}},
                home=self.fake_home,
            )
        else:
            self.fail(f"unknown Node source fixture: {source}")

        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(install_hooks.shutil, "which", return_value=None),
        ):
            return install_hooks._resolve_node_runtime(platform)

    def test_node_runtime_normalization_uses_shared_usability_predicate(self):
        node = self._fake_node("shared-usability")

        with patch.object(
            install_hooks.runtime_config,
            "is_usable_node_runtime",
            return_value=False,
            create=True,
        ) as usability:
            normalized = install_hooks._normalized_node_runtime(node)

        self.assertIsNone(normalized)
        usability.assert_called_once_with(node.resolve())

    @unittest.skipUnless(os.name != "nt", "POSIX execute bits are not enforced on Windows")
    def test_posix_non_executable_explicit_node_sources_are_rejected(self):
        for source in ("env", "codex-config", "registration"):
            with self.subTest(source=source):
                node = self._fake_node(f"non-executable-{source}")
                node.chmod(0o600)

                self.assertIsNone(self._resolve_isolated_node_source(source, node))

    @unittest.skipUnless(os.name != "nt", "POSIX execute bits are not enforced on Windows")
    def test_posix_executable_explicit_node_sources_are_accepted(self):
        for source in ("env", "codex-config", "registration"):
            with self.subTest(source=source):
                node = self._fake_node(f"executable-{source}")
                node.chmod(0o700)

                resolved = self._resolve_isolated_node_source(source, node)

                self.assertEqual(Path(resolved).resolve(), node.resolve())

    def test_node_runtime_normalization_resolves_before_validating_identity(self):
        real_node = self._fake_node("real-runtime")
        runtime_alias = self.fake_home / "runtime-alias"
        runtime_alias.write_text("simulated link entry\n", encoding="utf-8")
        resolved_node = real_node.resolve()
        original_resolve = Path.resolve

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == runtime_alias:
                return resolved_node
            return original_resolve(candidate, *args, **kwargs)

        with patch.object(Path, "resolve", new=simulated_resolve):
            normalized = install_hooks._normalized_node_runtime(runtime_alias)
            renormalized = install_hooks._normalized_node_runtime(normalized)

        self.assertEqual(normalized, str(resolved_node))
        self.assertEqual(renormalized, normalized)

    def test_node_named_alias_to_different_runtime_is_rejected(self):
        other_runtime = self.fake_home / "other-runtime" / (
            "python.exe" if os.name == "nt" else "python"
        )
        other_runtime.parent.mkdir(parents=True, exist_ok=True)
        other_runtime.write_text("# not node\n", encoding="utf-8")
        node_alias = self.fake_home / "alias" / (
            "node.exe" if os.name == "nt" else "node"
        )
        node_alias.parent.mkdir(parents=True, exist_ok=True)
        node_alias.write_text("simulated link entry\n", encoding="utf-8")
        resolved_other_runtime = other_runtime.resolve()
        original_resolve = Path.resolve

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == node_alias:
                return resolved_other_runtime
            return original_resolve(candidate, *args, **kwargs)

        with patch.object(Path, "resolve", new=simulated_resolve):
            self.assertIsNone(install_hooks._normalized_node_runtime(node_alias))

    def test_codex_config_wrong_nested_shapes_fall_back_to_registered_runtime(self):
        registered_node = self._fake_node("registered")
        path_node = self._fake_node("path")
        install_hooks.runtime_config.save_config(
            {"hook_runtime": {"node": {"codex": str(registered_node)}}},
            home=self.fake_home,
        )
        config_toml = self._codex_config_toml()
        config_toml.parent.mkdir(parents=True, exist_ok=True)
        wrong_shapes = {
            "mcp_servers": 'mcp_servers = "not-a-table"\n',
            "node_repl": '[mcp_servers]\nnode_repl = "not-a-table"\n',
            "env": '[mcp_servers.node_repl]\nenv = "not-a-table"\n',
        }
        node_env = {
            "CODEX_HOME": str(config_toml.parent),
            "GHOST_ALICE_NODE": "",
            "NODE_REPL_NODE_PATH": "",
        }

        with (
            patch.dict(os.environ, node_env, clear=False),
            patch.object(install_hooks.shutil, "which", return_value=str(path_node)),
        ):
            for level, config_text in wrong_shapes.items():
                with self.subTest(level=level):
                    config_toml.write_text(config_text, encoding="utf-8")
                    self.assertIsNone(
                        install_hooks._configured_node_runtime_from_codex_config()
                    )
                    self.assertEqual(
                        Path(install_hooks._resolve_node_runtime("codex")).resolve(),
                        registered_node.resolve(),
                    )

    def test_codex_config_invalid_utf8_falls_back_to_path(self):
        path_node = self._fake_node("path")
        config_toml = self._codex_config_toml()
        config_toml.parent.mkdir(parents=True, exist_ok=True)
        config_toml.write_bytes(b"\xff")
        node_env = {
            "CODEX_HOME": str(config_toml.parent),
            "GHOST_ALICE_NODE": "",
            "NODE_REPL_NODE_PATH": "",
        }

        with (
            patch.dict(os.environ, node_env, clear=False),
            patch.object(install_hooks.shutil, "which", return_value=str(path_node)),
        ):
            self.assertIsNone(
                install_hooks._configured_node_runtime_from_codex_config()
            )
            resolved = install_hooks._resolve_node_runtime("codex")

        self.assertEqual(Path(resolved).resolve(), path_node.resolve())

    def test_explicit_node_environment_remains_independent_of_malformed_codex_config(self):
        explicit_node = self._fake_node("explicit")
        path_node = self._fake_node("path")
        config_toml = self._codex_config_toml()
        config_toml.parent.mkdir(parents=True, exist_ok=True)
        config_toml.write_bytes(b"\xff")
        node_env = {
            "CODEX_HOME": str(config_toml.parent),
            "GHOST_ALICE_NODE": str(explicit_node),
            "NODE_REPL_NODE_PATH": "",
        }

        with (
            patch.dict(os.environ, node_env, clear=False),
            patch.object(install_hooks.shutil, "which", return_value=str(path_node)),
        ):
            resolved = install_hooks._resolve_node_runtime("codex")

        self.assertEqual(Path(resolved).resolve(), explicit_node.resolve())

    def test_node_runtime_command_uses_sentinel_without_runtime_lookup(self):
        selected_node = self._fake_node("selected")

        with patch.object(
            install_hooks,
            "_resolve_node_runtime",
            return_value=str(selected_node),
        ) as resolver:
            omitted = install_hooks._node_runtime_command("claude")
            explicit_none = install_hooks._node_runtime_command("claude", None)

        self.assertEqual(
            omitted,
            install_hooks._quote_command_arg(install_hooks.runtime_config.HOOK_NODE_SENTINEL),
        )
        self.assertEqual(
            explicit_none,
            install_hooks._quote_command_arg(install_hooks.runtime_config.HOOK_NODE_SENTINEL),
        )
        resolver.assert_not_called()

        with self.assertRaisesRegex(RuntimeError, "Invalid Node.js runtime"):
            install_hooks._node_runtime_command(
                "claude",
                selected_node.with_name("missing-node"),
            )

    def test_cli_resolves_node_once_and_persists_the_command_runtime(self):
        self._create_platform_dir("claude")
        selected_node = self._fake_node("selected")
        changed_path_node = self._fake_node("changed-path")
        output = io.StringIO()

        with (
            patch.object(sys, "argv", ["install_hooks.py", "--platform", "claude"]),
            patch.object(
                install_hooks,
                "_resolve_node_runtime",
                side_effect=[
                    str(selected_node),
                    str(changed_path_node),
                    str(changed_path_node),
                ],
            ) as resolver,
            patch.object(install_hooks, "_path_node_runtime_available", return_value=False),
            contextlib.redirect_stdout(output),
        ):
            result = install_hooks.main()

        settings = self._read_settings("claude")
        command = _visible_and_runner_payload_text(
            settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        ).replace("\\", "/")
        config_path = self.fake_home / ".ghost-alice" / "config.json"

        self.assertEqual(result, 0, msg=output.getvalue())
        self.assertEqual(resolver.call_count, 1)
        self.assertIn(install_hooks.runtime_config.HOOK_NODE_SENTINEL, command)
        self.assertNotIn(selected_node.as_posix(), command)
        self.assertNotIn(changed_path_node.as_posix(), command)
        self.assertTrue(config_path.exists())
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            Path(config["hook_runtime"]["node"]["claude"]).resolve(),
            selected_node.resolve(),
        )
        self.assertIn(f"Current hook install can use configured Node runtime: {selected_node}", output.getvalue())

    def test_cli_status_resolves_missing_node_once_and_reuses_snapshot(self):
        self._write_settings("claude", {})
        output = io.StringIO()

        with (
            patch.object(
                sys,
                "argv",
                ["install_hooks.py", "--platform", "claude", "--status"],
            ),
            patch.object(
                install_hooks,
                "_resolve_node_runtime",
                return_value=None,
            ) as resolver,
            patch.object(install_hooks, "_path_node_runtime_available", return_value=False),
            contextlib.redirect_stdout(output),
        ):
            result = install_hooks.main()

        self.assertEqual(result, 0, msg=output.getvalue())
        resolver.assert_called_once_with("claude")
        self.assertIn("Node.js guidance:", output.getvalue())

    def test_install_registers_runtime_when_current_settings_need_no_write(self):
        self._create_platform_dir("claude")
        selected_node = self._fake_node("selected")
        config_path = self.fake_home / ".ghost-alice" / "config.json"
        node_env = {
            "GHOST_ALICE_NODE": str(selected_node),
            "NODE_REPL_NODE_PATH": "",
        }

        with patch.dict(os.environ, node_env, clear=False):
            self.assertEqual(install_hooks.install_hook("claude"), "installed")
            config_path.unlink(missing_ok=True)
            result = install_hooks.install_hook("claude")

        self.assertEqual(result, "already")
        self.assertTrue(config_path.exists())
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            Path(config["hook_runtime"]["node"]["claude"]).resolve(),
            selected_node.resolve(),
        )

    def test_failed_settings_write_does_not_register_runtime(self):
        self._create_platform_dir("claude")
        selected_node = self._fake_node("selected")
        node_env = {
            "GHOST_ALICE_NODE": str(selected_node),
            "NODE_REPL_NODE_PATH": "",
        }

        with (
            patch.dict(os.environ, node_env, clear=False),
            patch.object(install_hooks, "_write_settings", side_effect=OSError("write failed")),
            patch.object(install_hooks.runtime_config, "save_config") as save_config,
            self.assertRaisesRegex(OSError, "write failed"),
        ):
            install_hooks.install_hook("claude")

        save_config.assert_not_called()
        self.assertFalse((self.fake_home / ".ghost-alice" / "config.json").exists())

    def test_successful_settings_write_reports_exact_saved_path(self):
        settings_file = self._config_file("claude")
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            install_hooks._write_settings(settings_file, {"updated": True})

        self.assertEqual(
            output.getvalue(),
            f"  [install_hooks] Saved: {settings_file}\n",
        )

    def test_settings_write_preserves_existing_symlink_and_target_permissions(self):
        settings_file = self._config_file("claude")
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        target = self.fake_home / "settings-target" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"keep": true}\n', encoding="utf-8")
        original_mode = stat.S_IMODE(target.stat().st_mode)
        self._symlink_file_or_skip(settings_file, target)
        original_link = os.readlink(settings_file)

        install_hooks._write_settings(settings_file, {"updated": True})

        self.assertTrue(settings_file.is_symlink())
        self.assertEqual(os.readlink(settings_file), original_link)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"updated": True})
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), original_mode)

    def test_settings_write_uses_runtime_atomic_resolved_destination(self):
        settings_file = self._config_file("claude")
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        link_entry_bytes = b'{"simulated_link_entry": true}\n'
        settings_file.write_bytes(link_entry_bytes)
        target = self.fake_home / "settings-target" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'{"old_target": true}\n')
        original_mode = stat.S_IMODE(target.stat().st_mode)
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def simulated_is_symlink(candidate: Path) -> bool:
            if candidate == settings_file:
                return True
            return original_is_symlink(candidate)

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == settings_file:
                return target
            return original_resolve(candidate, *args, **kwargs)

        with (
            patch.object(Path, "is_symlink", new=simulated_is_symlink),
            patch.object(Path, "resolve", new=simulated_resolve),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            install_hooks._write_settings(settings_file, {"updated": True})

        self.assertEqual(settings_file.read_bytes(), link_entry_bytes)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"updated": True})
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), original_mode)

    def test_partial_settings_write_failure_preserves_existing_bytes_and_cleans_temp(self):
        settings_file = self._write_settings("claude", {"keep": "exact"})
        original_settings = b'{  "keep": "exact"  }\n'
        settings_file.write_bytes(original_settings)
        write_error = OSError("partial settings write failed")
        original_open = builtins.open
        original_fdopen = os.fdopen

        def failing_open(file, mode="r", *args, **kwargs):
            handle = original_open(file, mode, *args, **kwargs)
            if "w" in mode and Path(file) == settings_file:
                return _FailAfterPartialWrite(handle, write_error)
            return handle

        def failing_fdopen(fd, *args, **kwargs):
            return _FailAfterPartialWrite(
                original_fdopen(fd, *args, **kwargs),
                write_error,
            )

        with (
            patch.object(builtins, "open", side_effect=failing_open),
            patch.object(
                install_hooks.runtime_config.os,
                "fdopen",
                side_effect=failing_fdopen,
            ),
            self.assertRaises(OSError) as raised,
        ):
            install_hooks._write_settings(settings_file, {"changed": True})

        self.assertIs(raised.exception, write_error)
        self.assertEqual(settings_file.read_bytes(), original_settings)
        self.assertEqual(
            list(settings_file.parent.glob(f".{settings_file.name}.*.tmp")),
            [],
        )

    def test_symlink_settings_write_failure_preserves_link_and_target(self):
        settings_file = self._config_file("claude")
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        target = self.fake_home / "settings-target" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        original_settings = b'{  "keep": "exact"  }\n'
        target.write_bytes(original_settings)
        self._symlink_file_or_skip(settings_file, target)
        original_link = os.readlink(settings_file)
        write_error = OSError("partial symlink settings write failed")
        original_fdopen = os.fdopen

        def failing_fdopen(fd, *args, **kwargs):
            return _FailAfterPartialWrite(
                original_fdopen(fd, *args, **kwargs),
                write_error,
            )

        with (
            patch.object(
                install_hooks.runtime_config.os,
                "fdopen",
                side_effect=failing_fdopen,
            ),
            patch.object(
                install_hooks.runtime_config.tempfile,
                "mkstemp",
                wraps=tempfile.mkstemp,
            ) as mkstemp,
            self.assertRaises(OSError) as raised,
        ):
            install_hooks._write_settings(settings_file, {"changed": True})

        self.assertIs(raised.exception, write_error)
        self.assertTrue(settings_file.is_symlink())
        self.assertEqual(os.readlink(settings_file), original_link)
        self.assertEqual(target.read_bytes(), original_settings)
        # macOS exposes the same temporary directory through /var and /private/var.
        self.assertEqual(Path(mkstemp.call_args.kwargs["dir"]).resolve(), target.parent.resolve())
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_resolved_settings_target_failure_preserves_entry_and_target_bytes(self):
        settings_file = self._config_file("claude")
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        link_entry_bytes = b'{"simulated_link_entry": true}\n'
        settings_file.write_bytes(link_entry_bytes)
        target = self.fake_home / "settings-target" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target_bytes = b'{"old_target": true}\n'
        target.write_bytes(target_bytes)
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve
        original_fdopen = os.fdopen
        write_error = OSError("resolved settings temp write failed")

        def simulated_is_symlink(candidate: Path) -> bool:
            if candidate == settings_file:
                return True
            return original_is_symlink(candidate)

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == settings_file:
                return target
            return original_resolve(candidate, *args, **kwargs)

        def failing_fdopen(fd, *args, **kwargs):
            return _FailAfterPartialWrite(
                original_fdopen(fd, *args, **kwargs),
                write_error,
            )

        with (
            patch.object(Path, "is_symlink", new=simulated_is_symlink),
            patch.object(Path, "resolve", new=simulated_resolve),
            patch.object(
                install_hooks.runtime_config.os,
                "fdopen",
                side_effect=failing_fdopen,
            ),
            self.assertRaises(OSError) as raised,
        ):
            install_hooks._write_settings(settings_file, {"changed": True})

        self.assertIs(raised.exception, write_error)
        self.assertEqual(settings_file.read_bytes(), link_entry_bytes)
        self.assertEqual(target.read_bytes(), target_bytes)

    def test_status_uses_registered_runtime_after_path_changes(self):
        self._create_platform_dir("claude")
        installed_node = self._fake_node("installed")
        changed_path_node = self._fake_node("changed-path")
        node_env = {
            "GHOST_ALICE_NODE": str(installed_node),
            "NODE_REPL_NODE_PATH": "",
        }
        with patch.dict(os.environ, node_env, clear=False):
            self.assertEqual(install_hooks.install_hook("claude"), "installed")

        original_which = shutil.which

        def changed_which(command: str, *args, **kwargs) -> str | None:
            if command in {"node", "node.exe"}:
                return str(changed_path_node)
            return original_which(command, *args, **kwargs)

        with (
            patch.dict(
                os.environ,
                {"GHOST_ALICE_NODE": "", "NODE_REPL_NODE_PATH": ""},
                clear=False,
            ),
            patch.object(install_hooks.shutil, "which", side_effect=changed_which),
        ):
            status = install_hooks.check_status("claude")

        self.assertEqual(status, "installed")

    def test_status_resolves_missing_node_runtime_exactly_once(self):
        self._create_platform_dir("claude")
        selected_node = self._fake_node("installed")
        with (
            patch.dict(
                os.environ,
                {
                    "GHOST_ALICE_NODE": str(selected_node),
                    "NODE_REPL_NODE_PATH": "",
                },
                clear=False,
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(install_hooks.install_hook("claude"), "installed")

        with patch.object(
            install_hooks,
            "_resolve_node_runtime",
            return_value=None,
        ) as resolver:
            install_hooks.check_status_detail("claude")

        resolver.assert_called_once_with("claude")

    def test_claude_node_runtime_precedence_keeps_overrides_then_registration(self):
        ghost_node = self._fake_node("ghost-override")
        repl_node = self._fake_node("repl-override")
        registered_node = self._fake_node("registered")
        path_node = self._fake_node("path")
        install_hooks.runtime_config.save_config(
            {"hook_runtime": {"node": {"claude": str(registered_node)}}},
            home=self.fake_home,
        )

        cases = (
            (str(ghost_node), str(repl_node), ghost_node),
            ("", str(repl_node), repl_node),
            ("", "", registered_node),
        )
        with patch.object(install_hooks.shutil, "which", return_value=str(path_node)):
            for ghost_value, repl_value, expected in cases:
                with self.subTest(expected=expected):
                    with patch.dict(
                        os.environ,
                        {
                            "GHOST_ALICE_NODE": ghost_value,
                            "NODE_REPL_NODE_PATH": repl_value,
                        },
                        clear=False,
                    ):
                        resolved = install_hooks._resolve_node_runtime("claude")
                    self.assertEqual(Path(resolved).resolve(), expected.resolve())

    def test_runtime_persistence_failure_restores_existing_settings(self):
        settings_file = self._write_settings("claude", {"keep": "exact"})
        settings_file.write_bytes(b'{  "keep": "exact" }\n')
        original_settings = settings_file.read_bytes()
        config_file = install_hooks.runtime_config.config_path(self.fake_home)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        original_config = b'{  "agent_visibility": {"profile": "minimal"}  }\n'
        config_file.write_bytes(original_config)
        selected_node = self._fake_node("selected")
        persistence_error = OSError("runtime persistence failed")
        events: list[str] = []
        output = io.StringIO()
        original_write_settings = install_hooks._write_settings

        def recording_write_settings(*args, **kwargs):
            events.append("settings")
            return original_write_settings(*args, **kwargs)

        def failing_save_config(*args, **kwargs):
            events.append("runtime")
            config_file.write_bytes(b'{"hook_runtime":')
            raise persistence_error

        with (
            patch.dict(
                os.environ,
                {
                    "GHOST_ALICE_NODE": str(selected_node),
                    "NODE_REPL_NODE_PATH": "",
                },
                clear=False,
            ),
            patch.object(install_hooks, "_write_settings", side_effect=recording_write_settings),
            patch.object(install_hooks.runtime_config, "save_config", side_effect=failing_save_config),
            contextlib.redirect_stdout(output),
            self.assertRaises(OSError) as raised,
        ):
            install_hooks.install_hook("claude")

        self.assertIs(raised.exception, persistence_error)
        self.assertEqual(events, ["settings", "runtime"])
        self.assertEqual(settings_file.read_bytes(), original_settings)
        self.assertEqual(config_file.read_bytes(), original_config)
        self.assertNotIn("Saved:", output.getvalue())

    def test_broken_symlink_snapshot_is_restored_when_target_parent_is_already_absent(self):
        settings_file = self._config_file("claude")
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        lexical_entry = b"simulated broken symlink entry"
        settings_file.write_bytes(lexical_entry)
        settings_target = self.fake_home / "missing-target-parent" / "settings.json"
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def simulated_is_symlink(candidate: Path) -> bool:
            if candidate == settings_file:
                return True
            return original_is_symlink(candidate)

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == settings_file:
                return settings_target
            return original_resolve(candidate, *args, **kwargs)

        with (
            patch.object(Path, "is_symlink", new=simulated_is_symlink),
            patch.object(Path, "resolve", new=simulated_resolve),
        ):
            install_hooks._restore_settings_snapshot(settings_file, False, None)

        self.assertEqual(settings_file.read_bytes(), lexical_entry)
        self.assertFalse(settings_target.exists())
        self.assertFalse(settings_target.parent.exists())

    def test_broken_symlink_snapshot_removes_target_created_during_write(self):
        settings_file = self._config_file("claude")
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        lexical_entry = b"simulated broken symlink entry"
        settings_file.write_bytes(lexical_entry)
        settings_target = self.fake_home / "created-target" / "settings.json"
        settings_target.parent.mkdir(parents=True, exist_ok=True)
        settings_target.write_bytes(b"created during settings write")
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def simulated_is_symlink(candidate: Path) -> bool:
            if candidate == settings_file:
                return True
            return original_is_symlink(candidate)

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == settings_file:
                return settings_target
            return original_resolve(candidate, *args, **kwargs)

        with (
            patch.object(Path, "is_symlink", new=simulated_is_symlink),
            patch.object(Path, "resolve", new=simulated_resolve),
        ):
            install_hooks._restore_settings_snapshot(settings_file, False, None)

        self.assertEqual(settings_file.read_bytes(), lexical_entry)
        self.assertFalse(settings_target.exists())
        self.assertTrue(settings_target.parent.is_dir())

    def test_persistence_failure_after_broken_symlink_target_disappears_reraises_original(self):
        self._create_platform_dir("claude")
        settings_file = self._config_file("claude")
        lexical_entry = b"simulated broken symlink entry"
        settings_file.write_bytes(lexical_entry)
        settings_target = self.fake_home / "transient-target" / "settings.json"
        settings_target.parent.mkdir(parents=True, exist_ok=True)
        selected_node = self._fake_node("selected")
        persistence_error = OSError("runtime persistence failed")
        output = io.StringIO()
        original_exists = Path.exists
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def simulated_exists(candidate: Path) -> bool:
            if candidate == settings_file:
                return False
            return original_exists(candidate)

        def simulated_is_symlink(candidate: Path) -> bool:
            if candidate == settings_file:
                return True
            return original_is_symlink(candidate)

        def simulated_resolve(candidate: Path, *args, **kwargs) -> Path:
            if candidate == settings_file:
                return settings_target
            return original_resolve(candidate, *args, **kwargs)

        def failing_save_config(*args, **kwargs):
            self.assertTrue(settings_target.is_file())
            settings_target.unlink()
            settings_target.parent.rmdir()
            raise persistence_error

        with (
            patch.dict(
                os.environ,
                {
                    "GHOST_ALICE_NODE": str(selected_node),
                    "NODE_REPL_NODE_PATH": "",
                },
                clear=False,
            ),
            patch.object(Path, "exists", new=simulated_exists),
            patch.object(Path, "is_symlink", new=simulated_is_symlink),
            patch.object(Path, "resolve", new=simulated_resolve),
            patch.object(
                install_hooks.runtime_config,
                "save_config",
                side_effect=failing_save_config,
            ),
            contextlib.redirect_stdout(output),
            self.assertRaises(BaseException) as raised,
        ):
            install_hooks.install_hook("claude")

        self.assertIs(raised.exception, persistence_error)
        self.assertEqual(settings_file.read_bytes(), lexical_entry)
        self.assertFalse(settings_target.exists())
        self.assertFalse(settings_target.parent.exists())
        self.assertNotIn(f"Saved: {settings_file}", output.getvalue())

    def test_runtime_persistence_failure_restores_symlink_targets_and_topology(self):
        self._create_platform_dir("claude")
        settings_file = self._config_file("claude")
        settings_target = self.fake_home / "settings-target" / "settings.json"
        settings_target.parent.mkdir(parents=True, exist_ok=True)
        original_settings = b'{  "keep": "exact"  }\n'
        settings_target.write_bytes(original_settings)
        self._symlink_file_or_skip(settings_file, settings_target)
        original_settings_link = os.readlink(settings_file)

        config_file = install_hooks.runtime_config.config_path(self.fake_home)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_target = self.fake_home / "runtime-target" / "config.json"
        config_target.parent.mkdir(parents=True, exist_ok=True)
        original_config = b'{  "agent_visibility": {"profile": "minimal"}  }\n'
        config_target.write_bytes(original_config)
        self._symlink_file_or_skip(config_file, config_target)
        original_config_link = os.readlink(config_file)

        selected_node = self._fake_node("selected")
        persistence_error = OSError("runtime persistence failed")

        def failing_save_config(*args, **kwargs):
            config_file.write_bytes(b'{"hook_runtime":')
            raise persistence_error

        with (
            patch.dict(
                os.environ,
                {
                    "GHOST_ALICE_NODE": str(selected_node),
                    "NODE_REPL_NODE_PATH": "",
                },
                clear=False,
            ),
            patch.object(
                install_hooks.runtime_config,
                "save_config",
                side_effect=failing_save_config,
            ),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(OSError) as raised,
        ):
            install_hooks.install_hook("claude")

        self.assertIs(raised.exception, persistence_error)
        self.assertTrue(settings_file.is_symlink())
        self.assertEqual(os.readlink(settings_file), original_settings_link)
        self.assertEqual(settings_target.read_bytes(), original_settings)
        self.assertTrue(config_file.is_symlink())
        self.assertEqual(os.readlink(config_file), original_config_link)
        self.assertEqual(config_target.read_bytes(), original_config)

    def test_saved_log_failure_is_nonfatal_after_both_files_commit(self):
        self._create_platform_dir("claude")
        settings_file = self._config_file("claude")
        selected_node = self._fake_node("selected")
        saved_log_error = OSError("saved log failed")
        original_log = install_hooks._log

        def failing_saved_log(message: str) -> None:
            if message == f"Saved: {settings_file}":
                raise saved_log_error
            original_log(message)

        with (
            patch.dict(
                os.environ,
                {
                    "GHOST_ALICE_NODE": str(selected_node),
                    "NODE_REPL_NODE_PATH": "",
                },
                clear=False,
            ),
            patch.object(install_hooks, "_log", side_effect=failing_saved_log),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = install_hooks.install_hook("claude")

        self.assertEqual(result, "installed")
        self.assertIn("hooks", self._read_settings("claude"))
        config = install_hooks.runtime_config.load_config(env={}, home=self.fake_home)
        self.assertEqual(
            config["hook_runtime"]["node"]["claude"],
            str(selected_node.resolve()),
        )

    def test_runtime_persistence_failure_removes_new_settings_file(self):
        self._create_platform_dir("claude")
        settings_file = self._config_file("claude")
        config_file = install_hooks.runtime_config.config_path(self.fake_home)
        selected_node = self._fake_node("selected")
        persistence_error = OSError("runtime persistence failed")

        def failing_save_config(*args, **kwargs):
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_bytes(b'{"hook_runtime":')
            raise persistence_error

        with (
            patch.dict(
                os.environ,
                {
                    "GHOST_ALICE_NODE": str(selected_node),
                    "NODE_REPL_NODE_PATH": "",
                },
                clear=False,
            ),
            patch.object(
                install_hooks.runtime_config,
                "save_config",
                side_effect=failing_save_config,
            ),
            self.assertRaises(OSError) as raised,
        ):
            install_hooks.install_hook("claude")

        self.assertIs(raised.exception, persistence_error)
        self.assertFalse(settings_file.exists())
        self.assertFalse(config_file.exists())

    def test_runtime_persistence_and_settings_rollback_failures_are_both_exposed(self):
        settings_file = self._write_settings("claude", {"keep": "exact"})
        selected_node = self._fake_node("selected")
        persistence_error = OSError("runtime persistence failed")
        rollback_error = OSError("settings rollback failed")
        original_atomic_write_bytes = install_hooks.runtime_config._atomic_write_bytes
        settings_write_count = 0

        def failing_settings_rollback(path, contents):
            nonlocal settings_write_count
            if Path(path) == settings_file:
                settings_write_count += 1
                if settings_write_count == 2:
                    raise rollback_error
            return original_atomic_write_bytes(path, contents)

        with (
            patch.dict(
                os.environ,
                {
                    "GHOST_ALICE_NODE": str(selected_node),
                    "NODE_REPL_NODE_PATH": "",
                },
                clear=False,
            ),
            patch.object(
                install_hooks.runtime_config,
                "save_config",
                side_effect=persistence_error,
            ),
            patch.object(
                install_hooks.runtime_config,
                "_atomic_write_bytes",
                side_effect=failing_settings_rollback,
            ),
            self.assertRaises(ExceptionGroup) as raised,
        ):
            install_hooks.install_hook("claude")

        self.assertTrue(settings_file.exists())
        self.assertEqual(len(raised.exception.exceptions), 2)
        self.assertIs(raised.exception.exceptions[0], persistence_error)
        self.assertIs(raised.exception.exceptions[1], rollback_error)

    def test_install_into_empty_settings(self):
        """Install hooks into an empty settings file."""
        self._write_settings("claude", {})
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        settings = self._read_settings("claude")
        hooks = settings["hooks"]["UserPromptSubmit"]
        # Intake is the only Claude input hook; merge-companion re-checks before skill changes.
        self.assertEqual(len(hooks), 1)
        cmds = [h["command"] for entry in hooks for h in entry["hooks"]]
        self.assertFalse(any(install_hooks.PROMPT_PENDING_MERGE_MARKER in c for c in cmds))
        self.assertFalse(any(install_hooks.HOOK_MARKER in c for c in cmds))
        self.assertTrue(any(install_hooks.SESSION_INTENT_MARKER in c for c in cmds))
        self.assertFalse(any(install_hooks.WEB_SEARCH_FIRST_MARKER in c for c in cmds))
        allow = settings["permissions"]["allow"]
        for skill_name in _expected_ghost_alice_skill_names():
            with self.subTest(skill_name=skill_name):
                self.assertIn(f"Skill({skill_name})", allow)

    def test_user_prompt_routing_has_one_owner(self):
        """Intake owns routing; no retired input hook is installed beside it."""
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                self._write_settings(platform, {})
                result = install_hooks.install_hook(platform)
                self.assertEqual(result, "installed")

                settings = self._read_settings(platform)
                event = hook_definitions.resolve_hook_event("on_user_prompt", platform)
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

                expected = ["session-intent"]
                self.assertEqual(marker_order, expected)
                self.assertEqual(install_hooks.check_status_detail(platform).legacy_status, 'installed')

    def test_install_replaces_legacy_pre_tool_checkpoint_marker(self):
        """Replace managed pre-tool checkpoint entries with the configured marker."""
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

    def test_cli_defaults_to_runtime_shared_dir_when_platform_shared_is_available(self):
        """Direct CLI execution uses the shared runtime core even when platform _shared exists."""
        self._write_settings("claude", {})
        self._write_codex_node_runtime()
        platform_shared = self._create_runtime_shared_dir(
            self.fake_home / ".claude" / "skills" / "_shared"
        )
        runtime_shared = self._create_runtime_shared_dir(
            self.fake_home / ".ghost-alice" / "runtime" / "current" / "_shared"
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
        self.assertNotIn(platform_shared.as_posix(), joined)
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
        self._write_codex_node_runtime()

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
        # Codex has no /visibility command; S71 and S72 retired its input hook.
        self.assertNotIn("in trusted Codex sessions", text)
        self.assertIn("Claude Code: /visibility strict|dynamic|minimal", text)

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
        self.assertIn("For full capability, install Node.js:", text)
        self.assertIn("https://nodejs.org/en/download", text)
        self.assertFalse((self.fake_home / ".claude" / "settings.json").exists())
        self.assertIn("Claude Code: /visibility strict|dynamic|minimal", text)
        self.assertIn("Hook execution, governance gates, and strict session logging remain unchanged.", text)

    def test_cli_install_uses_codex_configured_node_when_path_node_is_missing(self):
        """Hook-enabled installs can use the Codex-managed node runtime when PATH lacks node."""
        configured_node = self._write_codex_node_runtime()
        original_which = shutil.which

        def fake_which(command: str) -> str | None:
            if command in {"node", "node.exe"}:
                return None
            return original_which(command)

        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                self._create_platform_dir(platform)
                output = io.StringIO()
                with patch.object(install_hooks.shutil, "which", side_effect=fake_which):
                    with patch.object(sys, "argv", [
                        "install_hooks.py",
                        "--platform",
                        platform,
                    ]):
                        with contextlib.redirect_stdout(output):
                            result = install_hooks.main()

                self.assertEqual(result, 0, msg=output.getvalue())
                settings = self._read_settings(platform)
                pre_tool_key = hook_definitions.resolve_hook_event("pre_tool_use", platform)
                command = _visible_and_runner_payload_text(
                    settings["hooks"][pre_tool_key][0]["hooks"][0]["command"]
                )
                self.assertIn(install_hooks.runtime_config.HOOK_NODE_SENTINEL, command)
                self.assertNotIn(configured_node.as_posix(), command)
                runtime = install_hooks.runtime_config.load_config(env={}, home=self.fake_home)
                self.assertEqual(
                    Path(runtime["hook_runtime"]["node"][platform]).resolve(),
                    configured_node.resolve(),
                )
                self.assertIn("For full capability, install Node.js:", output.getvalue())
                self.assertIn("https://nodejs.org/en/download", output.getvalue())

    @unittest.skipUnless(os.name == "nt", "System32 hook smoke is Windows-specific")
    def test_installed_tool_checkpoint_hooks_run_from_system32_for_both_platforms(self):
        node = shutil.which("node.exe") or shutil.which("node")
        if not node:
            self.skipTest("node is required to execute the installed hook dispatcher")
        system32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
        if not system32.is_dir():
            self.skipTest(f"System32 directory is unavailable: {system32}")

        intent_root = self.fake_home / "session-intent"
        source_shared = Path(install_hooks.__file__).parent
        hook_env = os.environ.copy()
        hook_env.update({
            "HOME": str(self.fake_home),
            "USERPROFILE": str(self.fake_home),
            "CODEX_HOME": str(self.fake_home / ".codex"),
            "CLAUDE_CONFIG_DIR": str(self.fake_home / ".claude"),
            install_hooks.HOOK_SHARED_DIR_ENV: str(source_shared),
            "GHOST_ALICE_AGENT_VISIBILITY": "minimal",
            "GHOST_ALICE_NODE": "",
            "NODE_REPL_NODE_PATH": "",
        })
        hook_env.pop("GHOST_ALICE_PLATFORM", None)

        with (
            patch.dict(os.environ, hook_env, clear=True),
            patch.object(install_hooks, "_session_intent_root", return_value=intent_root),
            patch.object(install_hooks, "_codex_hooks_supported", return_value=True),
        ):
            for platform in ("claude", "codex"):
                with self.subTest(platform=platform):
                    self._create_platform_dir(platform)
                    self.assertEqual(
                        install_hooks.install_hook(platform, node_runtime=node),
                        "installed",
                    )
                    settings = self._read_settings(platform)
                    pre_tool = hook_definitions.resolve_hook_event("pre_tool_use", platform)
                    command = next(
                        hook["command"]
                        for entry in settings["hooks"][pre_tool]
                        for hook in entry.get("hooks", [])
                        if install_hooks.TOOL_CHECKPOINT_MARKER in hook.get("command", "")
                    )
                    session_id = f"s-system32-{platform}"
                    _write_session_intent_preflight(
                        self.fake_home,
                        platform,
                        session_id,
                        session_intent_root=intent_root,
                    )
                    _write_downstream_gate(
                        self.fake_home,
                        platform,
                        session_id,
                        "allow",
                        session_intent_root=intent_root,
                    )
                    request = json.dumps({
                        "session_id": session_id,
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Read",
                        "tool_input": {"file_path": "README.md"},
                        "cwd": str(system32),
                    })

                    results = [
                        subprocess.run(
                            command,
                            shell=True,
                            cwd=system32,
                            input=request,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            env=hook_env,
                            check=False,
                        )
                        for _ in range(2)
                    ]

                    for result in results:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        json.loads(result.stdout)
                        self.assertEqual(result.stderr, "")
                    self.assertEqual(json.loads(results[-1].stdout), {})

    def test_agent_visibility_env_does_not_change_generated_hook_command(self):
        hook_key = hook_definitions.resolve_hook_event("on_user_prompt", "claude")
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
            "permissions": {"allow": ["Skill(custom-local-skill)"]},
        })
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        settings = self._read_settings("claude")
        self.assertEqual(settings["autoUpdatesChannel"], "latest")
        self.assertEqual(settings["customKey"], "customValue")
        self.assertIn("Skill(custom-local-skill)", settings["permissions"]["allow"])
        self.assertIn("Skill(verification-before-completion)", settings["permissions"]["allow"])
        self.assertEqual(
            settings["permissions"]["allow"].count("Skill(verification-before-completion)"),
            1,
        )

    def test_install_adds_only_manifest_verified_addon_skill_permissions(self):
        """Claude permissions include verified addon targets, not arbitrary installed dirs."""
        addon_source = self._create_addon_source()
        unverified_skill = self.fake_home / ".agents" / "skills" / "unverified-addon"
        unverified_skill.mkdir(parents=True)
        (unverified_skill / "SKILL.md").write_text("# unverified\n", encoding="utf-8")
        self._write_settings("claude", {
            "permissions": {"allow": ["Skill(custom-local-skill)"]},
        })

        result = install_hooks.install_hook("claude", addon_sources=[addon_source])

        self.assertEqual(result, "installed")
        allow = self._read_settings("claude")["permissions"]["allow"]
        self.assertIn("Skill(verification-before-completion)", allow)
        self.assertIn("Skill(autopilot-mode)", allow)
        self.assertIn("Skill(custom-local-skill)", allow)
        self.assertNotIn("Skill(unverified-addon)", allow)
        self.assertEqual(allow.count("Skill(autopilot-mode)"), 1)

        self.assertEqual(
            install_hooks.install_hook("claude", addon_sources=[addon_source]),
            "already",
        )
        status = install_hooks.check_status_detail(
            "claude",
            addon_sources=[addon_source],
        )
        self.assertEqual(status.status_token, "HOOK_INSTALLED_OK")
        self.assertIn(
            "skill-permission:autopilot-mode",
            status.details["installed"],
        )

    def test_permission_discovery_does_not_trust_unverified_installed_tree(self):
        """An installed directory cannot become a managed permission without a manifest."""
        addon_source = self._create_addon_source()
        unverified_skill = self.fake_home / ".agents" / "skills" / "unverified-addon"
        unverified_skill.mkdir(parents=True)
        (unverified_skill / "SKILL.md").write_text("# unverified\n", encoding="utf-8")

        with patch.object(
            install_hooks,
            "_discover_ghost_alice_skill_names_from_catalog",
            return_value=[],
        ):
            permissions = install_hooks._claude_ghost_alice_skill_permissions(
                [addon_source]
            )

        self.assertEqual(permissions, ["Skill(autopilot-mode)"])

    def test_install_preserves_non_managed_bash_permissions(self):
        """The installer does not remove unmanaged Bash allow rules."""
        self._write_settings("claude", {
            "permissions": {
                "allow": [
                    "Skill(custom-local-skill)",
                    "Bash(rg ghost-alice README.md)",
                ],
            },
        })

        result = install_hooks.install_hook("claude")

        self.assertEqual(result, "installed")
        allow = self._read_settings("claude")["permissions"]["allow"]
        self.assertIn("Skill(custom-local-skill)", allow)
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
        # Existing 1 + session-intent-analyzer = 2.
        self.assertEqual(len(hooks), 2)
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
        self.assertEqual(marker_count, 0)


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


class TestAddonHookRemovalWithoutHooks(TempHomeTestCase):
    def test_missing_hook_object_is_idempotent_and_preserves_settings(self):
        removers = ((install_hooks.remove_addon_hook, '[addon:hookaddon] obs'),
                    (install_hooks.remove_adapter_hook, '[adapter:autopilot-mode] stop'))
        for platform in ('claude', 'codex'):
            for settings in ({}, {'theme': 'dark', 'permissions': {'allow': ['Read']}}):
                for remove, marker in removers:
                    for dry_run in (False, True):
                        with self.subTest(platform=platform, settings=settings, remove=remove.__name__, dry_run=dry_run):
                            path = self._write_settings(platform, settings)
                            before = _tree_snapshot(self.fake_home)
                            with patch.object(install_hooks, '_write_removed_hook_settings') as write:
                                self.assertEqual(remove(marker, platform_key=platform, dry_run=dry_run), 0)
                                self.assertEqual(remove(marker, platform_key=platform, dry_run=dry_run), 0)
                            write.assert_not_called()
                            self.assertEqual(json.loads(path.read_text()), settings)
                            self.assertEqual(_tree_snapshot(self.fake_home), before)


class TestCheckStatus(TempHomeTestCase):
    """Status check tests."""

    def test_status_installed(self):
        """Return installed when the full hook suite is installed."""
        self._write_settings("claude", {})
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        result = install_hooks.check_status("claude")
        self.assertEqual(result, "installed")

    def test_status_reports_missing_verified_addon_skill_permission(self):
        """Status reports drift when a managed addon Skill permission is removed."""
        addon_source = self._create_addon_source()
        self._write_settings("claude", {})
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        status = install_hooks.check_status_detail(
            "claude",
            addon_sources=[addon_source],
        )

        self.assertEqual(status.status_token, "HOOK_MISSING")
        self.assertEqual(status.legacy_status, "missing")
        self.assertIn("skill-permission:autopilot-mode", status.details["missing"])

    def test_status_detail_reports_drift_when_marker_exists_but_command_differs(self):
        """Marker presence with command mismatch is command drift, not absence."""
        self._write_settings("claude", {})
        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")
        settings = self._read_settings("claude")
        for entry in settings["hooks"]["UserPromptSubmit"]:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if install_hooks.SESSION_INTENT_MARKER in command:
                    hook["command"] = command + " --local-user-edit"
        self._write_settings("claude", settings)

        status = install_hooks.check_status_detail("claude")

        self.assertEqual(status.status_token, "HOOK_INSTALLED_DRIFT")
        self.assertEqual(status.legacy_status, "missing")
        self.assertEqual(status.details["drifted"], ["session-intent"])
        self.assertNotEqual(status.status_label, install_hooks.STATUS_LABELS["missing"])

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

    def test_status_installed_for_codex_without_web_search_hook(self):
        """External evidence guidance is not a required input hook."""
        self._create_platform_dir("codex")
        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            self.assertEqual(install_hooks.install_hook("codex"), "installed")
            settings = self._read_settings("codex")
            settings["hooks"]["UserPromptSubmit"] = [
                entry for entry in settings["hooks"]["UserPromptSubmit"]
                if not any(install_hooks.WEB_SEARCH_FIRST_MARKER in h.get("command", "") for h in entry["hooks"])
            ]
            self._write_settings("codex", settings)
            result = install_hooks.check_status("codex")
        self.assertEqual(result, "installed")

    def test_status_installed_for_claude_without_web_search_hook(self):
        """Claude Code does not install or require the web-search-first hook."""
        self._create_platform_dir("claude")
        self.assertEqual(install_hooks.install_hook("claude"), "installed")
        settings = self._read_settings("claude")
        commands = [h["command"] for entry in settings["hooks"]["UserPromptSubmit"] for h in entry["hooks"]]
        self.assertFalse(any(install_hooks.WEB_SEARCH_FIRST_MARKER in c for c in commands))
        self.assertEqual(install_hooks.check_status("claude"), "installed")

    def test_status_missing_when_session_intent_hook_is_missing(self):
        """Missing session-intent-analyzer hook is missing."""
        self._write_settings("claude", {
            "hooks": {
                "UserPromptSubmit": [
                    install_hooks.HOOK_ENTRY,
                    LEGACY_WEB_SEARCH_ENTRY,
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
                    LEGACY_PROMPT_PENDING_MERGE_ENTRY,
                    install_hooks.HOOK_ENTRY,
                    install_hooks.SESSION_INTENT_ENTRY,
                    LEGACY_WEB_SEARCH_ENTRY,
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
                    LEGACY_PROMPT_PENDING_MERGE_ENTRY,
                    install_hooks.HOOK_ENTRY,
                    install_hooks.SESSION_INTENT_ENTRY,
                    LEGACY_WEB_SEARCH_ENTRY,
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
        self.assertFalse(any(install_hooks.PROMPT_PENDING_MERGE_MARKER in cmd for cmd in prompt_cmds))
        for marker, cmds in (
            (install_hooks.SESSION_INTENT_MARKER, prompt_cmds),
            (install_hooks.SESSION_START_MARKER, session_cmds),
            (install_hooks.SESSION_RECEIPT_MARKER, session_cmds),
        ):
            matching = [cmd for cmd in cmds if marker in cmd]
            self.assertEqual(len(matching), 1)
            result = _run_hook_command(matching[0])
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["continue"], True)
            if marker == install_hooks.SESSION_RECEIPT_MARKER:
                # A session start without host identity adds no message.
                self.assertNotIn("systemMessage", payload)
                continue
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
        for key in managed_keys:
            trusted_hash = hook_state[key]["trusted_hash"]
            self.assertRegex(trusted_hash, r"^sha256:[0-9a-f]{64}$")

    def test_install_codex_trusts_verified_addons_without_trusting_marker_lookalikes(self):
        from test_addon_hooks import _make_addon
        from test_addon_hooks_install import _make_autopilot_adapter_addon

        self._create_platform_dir("codex")
        adapter = _make_autopilot_adapter_addon(self.fake_home)
        observer = _make_addon(self.fake_home, [{
            "id": "obs", "event": "post_tool_use", "script": "hooks/obs.py",
        }])
        user_commands = {
            "Stop": "echo user # [adapter:autopilot-mode] continue [hook-runner:adapter-autopilot-mode-continue]",
            "PostToolUse": "echo user # [addon:hookaddon] obs [hook-runner:obs]",
        }
        settings_file = self._write_settings("codex", {"hooks": {
            event: [{"hooks": [{"type": "command", "command": command}]}]
            for event, command in user_commands.items()
        }})
        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            install_hooks.install_hook("codex", addon_sources=[str(adapter), str(observer)])
        settings = self._read_settings("codex")
        state = tomllib.loads(self._codex_config_toml().read_text())["hooks"]["state"]
        verified_count = 0
        for event, label in (("Stop", "stop"), ("PostToolUse", "post_tool_use")):
            for group_index, group in enumerate(settings["hooks"][event]):
                for handler_index, hook in enumerate(group["hooks"]):
                    key = f"{settings_file.as_posix()}:{label}:{group_index}:{handler_index}"
                    command = hook["command"]
                    if command == user_commands[event]:
                        self.assertNotIn(key, state, "marker text is not installation authority")
                    elif "[adapter:" in command or "[addon:" in command:
                        verified_count += 1
                        self.assertIn(key, state, "installed addon hook must be runnable without trust bypass")
                        self.assertEqual(state[key]["trusted_hash"],
                                         install_hooks._codex_command_hook_hash(event, group, hook))
        self.assertEqual(verified_count, 2)

    def test_codex_addon_reinstall_repairs_missing_trust_without_rewriting_hooks(self):
        from test_addon_hooks_install import _make_autopilot_adapter_addon

        self._create_platform_dir("codex")
        adapter = _make_autopilot_adapter_addon(self.fake_home)
        with patch.object(install_hooks, "_codex_hooks_supported", return_value=True):
            install_hooks.install_hook("codex", addon_sources=[str(adapter)])
            hooks_file = self._config_file("codex")
            settings_bytes = hooks_file.read_bytes()
            key = f"{hooks_file.as_posix()}:stop:1:0"
            config_file = self._codex_config_toml()
            header = f"[hooks.state.{json.dumps(key)}]"
            config_file.write_text(re.sub(
                rf"(?ms)^{re.escape(header)}\n.*?(?=^\[|\Z)", "", config_file.read_text(),
            ))
            self.assertEqual(install_hooks.install_hook("codex", addon_sources=[str(adapter)]), "installed")
            self.assertIn(key, tomllib.loads(config_file.read_text())["hooks"]["state"])
            self.assertEqual(hooks_file.read_bytes(), settings_bytes)
            config_bytes = config_file.read_bytes()
            self.assertEqual(install_hooks.install_hook("codex", addon_sources=[str(adapter)]), "already")
            self.assertEqual(config_file.read_bytes(), config_bytes)

    def test_install_codex_trusts_current_project_config_layer(self):
        """Codex install marks the Ghost-ALICE project config layer as trusted."""
        self._create_platform_dir("codex")
        repo = self.fake_home / "repo" / "Ghost-ALICE"
        repo.mkdir(parents=True)

        with (
            patch.object(install_hooks, "_repo_root_from_this_file", return_value=repo),
            patch.object(install_hooks, "_codex_hooks_supported", return_value=True),
        ):
            destination = install_hooks._default_destination("codex")
            destination = __import__('dataclasses').replace(destination,
                core_source_root=str(Path(install_hooks.__file__).resolve().parents[1]),project_root=str(repo))
            result = install_hooks.install_hook("codex",destination=destination)
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

    def test_install_codex_hook_commands_keep_discovery_before_install_python_fallback(self):
        """A captured Homebrew minor is only a fallback, not a permanent runtime pin."""
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

        for command in command_surfaces:
            self.assertIn(VERSIONED_HOMEBREW_PYTHON, command)
            self.assertLess(command.index("${GHOST_ALICE_PYTHON:-}"), command.index(VERSIONED_HOMEBREW_PYTHON))
            self.assertLess(command.index("/bin/python3.[0-9]*"), command.index(VERSIONED_HOMEBREW_PYTHON))

    @unittest.skipIf(os.name == "nt", "POSIX launcher only")
    def test_hook_python_invocation_retains_install_runtime_outside_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # Shell metacharacters are valid filename bytes, not instructions.
            runtime = root / "python with 'quotes' $HOME $(touch injected)"
            runtime.symlink_to(sys.executable)
            hook_script = root / "hook.py"
            hook_script.write_text("print('fallback-probe')\n", encoding="utf-8")
            with patch.object(install_hooks.sys, "executable", str(runtime)):
                command = install_hooks._hook_python_invocation(hook_script)
            result = subprocess.run(
                ["/bin/sh", "-c", command],
                cwd=root,
                env={"PATH": "/usr/bin:/bin", "HOME": str(root)},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(result.stdout, "fallback-probe\n")
            launcher = shlex.split(command)[2]
            self.assertIn(install_hooks._quote_posix_arg(str(runtime)), launcher)
            self.assertFalse((root / "injected").exists())

    @unittest.skipIf(os.name == "nt", "POSIX launcher only")
    def test_hook_python_override_still_precedes_install_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            override = root / "override-python"
            override.write_text(
                "#!/bin/sh\nexport GHOST_OVERRIDE_PROBE=selected\n"
                f"exec {shlex.quote(sys.executable)} \"$@\"\n", encoding="utf-8")
            override.chmod(0o700)
            hook_script = root / "hook.py"
            hook_script.write_text("import os; print(os.environ.get('GHOST_OVERRIDE_PROBE', 'not-selected'))\n", encoding="utf-8")
            command = install_hooks._hook_python_invocation(hook_script)
            result = subprocess.run(
                ["/bin/sh", "-c", command],
                env={"PATH": "/usr/bin:/bin", "HOME": str(root), "GHOST_ALICE_PYTHON": str(override)},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(result.stdout.strip(), "selected")

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
        self.assertTrue(ghost_alice_states)

    def test_install_codex_prunes_legacy_path_variant_hook_state(self):
        """Codex hook auto-trust removes duplicate Ghost-ALICE path variants."""
        self._create_platform_dir("codex")
        config_toml = self._codex_config_toml()
        hooks_file = self.fake_home / ".codex" / "hooks.json"
        legacy_settings_path = hooks_file.as_posix().replace("/", "\\")
        legacy_key = f"{legacy_settings_path}:pre_tool_use:0:0"
        unrelated_key = "user-hook:pre_tool_use:0:0"
        config_toml.write_text(
            '\n'.join([
                '[features]',
                'hooks = true',
                'multi_agent = true',
                '',
                f'[hooks.state.{json.dumps(legacy_key)}]',
                'trusted_hash = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
                '',
                f'[hooks.state.{json.dumps(unrelated_key)}]',
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
        hook_state = config["hooks"]["state"]
        managed_prefix = hooks_file.as_posix()
        managed_states = [
            key for key in hook_state
            if key.startswith(managed_prefix)
        ]

        self.assertTrue(managed_states)
        self.assertNotIn(legacy_key, hook_state)
        self.assertFalse(any(key.startswith(legacy_settings_path) for key in hook_state))
        self.assertIn(unrelated_key, hook_state)
        self.assertFalse(hook_state[unrelated_key]["enabled"])

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
                    LEGACY_PROMPT_PENDING_MERGE_ENTRY,
                    install_hooks.HOOK_ENTRY,
                    install_hooks.SESSION_INTENT_ENTRY,
                    LEGACY_WEB_SEARCH_ENTRY,
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

        self.assertFalse(any(install_hooks.PROMPT_PENDING_MERGE_MARKER in cmd for cmd in prompt_cmds))
        for marker, cmds in (
            (install_hooks.SESSION_INTENT_MARKER, prompt_cmds),
            (install_hooks.SESSION_START_MARKER, session_cmds),
            (install_hooks.SESSION_RECEIPT_MARKER, session_cmds),
        ):
            matching = [cmd for cmd in cmds if marker in cmd]
            self.assertEqual(len(matching), 1)
            result = _run_hook_command(matching[0])
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["continue"], True)
            self.assertNotIn("Internal instruction:", result.stdout)
            self.assertNotIn("User:", payload.get("systemMessage", ""))
            self.assertNotIn("Tech:", payload.get("systemMessage", ""))

    def test_install_codex_replaces_markerless_hook_runner_wrappers(self):
        """Hook-runner wrappers without outer markers are replaced without duplicate execution."""

        def markerless(entry: dict[str, Any]) -> dict[str, Any]:
            cloned = json.loads(json.dumps(entry))
            for hook in cloned.get("hooks", []):
                command = hook.get("command", "")
                hook["command"] = re.sub(r"\s+# .*\[hook-runner:[^\]]+\].*$", "", command)
            return cloned

        user_prompt = hook_definitions.resolve_hook_event("on_user_prompt", "codex")
        pre_tool = hook_definitions.resolve_hook_event("pre_tool_use", "codex")
        post_tool = hook_definitions.resolve_hook_event("post_tool_use", "codex")
        stop = hook_definitions.resolve_hook_event("on_agent_stop", "codex")
        session_start = hook_definitions.resolve_hook_event("on_session_start", "codex")
        io_trace_entry = install_hooks._platform_io_trace_entry("codex", post_tool)
        self.assertIsNotNone(io_trace_entry)

        self._write_settings("codex", {
            "hooks": {
                user_prompt: [
                    markerless(install_hooks._platform_session_intent_entry("codex", user_prompt)),
                    markerless(install_hooks._hook_runner_command_entry(
                        "web-search-first", "echo old", install_hooks.WEB_SEARCH_FIRST_MARKER, platform_key="codex")),
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
                install_hooks.SESSION_INTENT_MARKER,
            ],
            pre_tool: [install_hooks.TOOL_CHECKPOINT_MARKER],
            post_tool: [install_hooks.IO_TRACE_MARKER],
            stop: [install_hooks.STOP_HOOK_MARKER],
            session_start: [install_hooks.SESSION_START_MARKER, install_hooks.SESSION_RECEIPT_MARKER],
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
        """Normalize the codex_hooks feature setting to hooks=true."""
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
        corrupt_bytes = b"{invalid json"
        settings_file.write_bytes(corrupt_bytes)

        result = install_hooks.install_hook("claude")
        self.assertEqual(result, "installed")

        # Confirm that the corrupt file was backed up.
        corrupt_backup = config_dir / "settings.json.corrupt-bak"
        self.assertTrue(corrupt_backup.exists())
        self.assertEqual(corrupt_backup.read_bytes(), corrupt_bytes)


class TestDryRun(TempHomeTestCase):
    """Dry-run tests."""

    def test_dry_run_does_not_register_selected_node_runtime(self):
        self._write_settings("claude", {"original": True})
        node = self.fake_home / "selected" / ("node.exe" if os.name == "nt" else "node")
        node.parent.mkdir(parents=True)
        node.write_text("# fake node\n", encoding="utf-8")
        _make_node_executable(node)

        with patch.dict(
            os.environ,
            {"GHOST_ALICE_NODE": str(node), "NODE_REPL_NODE_PATH": ""},
            clear=False,
        ):
            install_hooks.install_hook("claude", dry_run=True)

        self.assertFalse((self.fake_home / ".ghost-alice" / "config.json").exists())

    def test_dry_run_with_corrupt_settings_preserves_entire_home_tree(self):
        config_dir = self._create_platform_dir("claude")
        settings_file = config_dir / "settings.json"
        settings_file.write_bytes(b"{invalid json")
        node = self.fake_home / "selected" / ("node.exe" if os.name == "nt" else "node")
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_text("# fake node\n", encoding="utf-8")
        _make_node_executable(node)
        before = _tree_snapshot(self.fake_home)

        with (
            patch.dict(
                os.environ,
                {"GHOST_ALICE_NODE": str(node), "NODE_REPL_NODE_PATH": ""},
                clear=False,
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = install_hooks.install_hook("claude", dry_run=True)

        self.assertEqual(result, "installed")
        self.assertEqual(_tree_snapshot(self.fake_home), before)
        self.assertFalse((config_dir / "settings.json.corrupt-bak").exists())

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
        from install_hooks import install_hook, SESSION_START_MARKER
        from hook_definitions import resolve_hook_event
        Path(self.tmp.name).mkdir(parents=True, exist_ok=True)
        result = install_hook("claude")
        self.assertEqual(result, "installed")
        settings = json.loads((Path(self.tmp.name) / "settings.json").read_text(encoding="utf-8"))
        ss_key = resolve_hook_event("on_session_start", "claude")
        self.assertIn(ss_key, settings["hooks"])
        cmds = [h["command"] for entry in settings["hooks"][ss_key] for h in entry["hooks"]]
        self.assertTrue(any(SESSION_START_MARKER in c for c in cmds))

    def test_session_receipt_hook_installed_once_on_session_start(self):
        from install_hooks import (
            SESSION_RECEIPT_MARKER,
            _platform_session_receipt_entry,
            install_hook,
        )
        from hook_definitions import resolve_hook_event
        Path(self.tmp.name).mkdir(parents=True, exist_ok=True)
        self.assertEqual(install_hook("claude"), "installed")
        install_hook("claude")
        settings = json.loads((Path(self.tmp.name) / "settings.json").read_text(encoding="utf-8"))
        ss_key = resolve_hook_event("on_session_start", "claude")
        cmds = [h["command"] for entry in settings["hooks"][ss_key] for h in entry["hooks"]]
        self.assertEqual(sum(SESSION_RECEIPT_MARKER in c for c in cmds), 1)
        for platform in ("claude", "codex"):
            command = _platform_session_receipt_entry(platform, "SessionStart")["hooks"][0]["command"]
            encoded_args = re.findall(r'"([A-Za-z0-9_-]{40,}=*)"', command)
            self.assertTrue(encoded_args, msg=command)
            wrapped = base64.urlsafe_b64decode(encoded_args[0]).decode("utf-8")
            self.assertIn("session_intent_analyzer_hook.py", wrapped)
            self.assertIn(f"--platform {platform}", wrapped)
            self.assertIn("--context session_start", wrapped)
            self.assertIn("--format json", wrapped)

    def test_session_start_hook_uses_hook_verified_pending_merge_precheck(self):
        from install_hooks import SESSION_START_COMMAND
        result = _run_hook_command(SESSION_START_COMMAND)
        self.assertEqual(result.returncode, 0)
        self.assertIn("merge-companion-precheck: clean (hook-verified)", result.stdout)
        self.assertNotIn("Internal instruction:", result.stdout)
        self.assertIn("re-check it before changing an installed skill", result.stdout)
        self.assertIn("merge-companion", result.stdout)
        self.assertIn("pending-merges/claude/manifest.json", result.stdout)
        self.assertNotIn("Check the current platform pending-merge manifest", result.stdout)
        self.assertNotIn("next time Claude/Codex is opened", result.stdout)
        self.assertNotIn("The next time you open Claude/Codex", result.stdout)

    def test_session_start_hook_reports_pending_entries_without_agent_shell_probe(self):
        from install_hooks import SESSION_START_COMMAND

        with tempfile.TemporaryDirectory() as temp_home:
            manifest = Path(temp_home) / ".ghost-alice" / "pending-merges" / "claude" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({"entries": [{"decided": False}], "version": 1}),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = temp_home
            result = _run_hook_command(SESSION_START_COMMAND, env=env)

        self.assertEqual(result.returncode, 0)
        self.assertIn("has 1 undecided entry", result.stdout)
        self.assertIn("Unrelated read-only work may continue", result.stdout)
        self.assertIn("an explicit defer leaves entries undecided", result.stdout)
        self.assertNotIn("Check the current platform pending-merge manifest", result.stdout)

    def test_claude_prompt_hooks_use_json_payloads_to_avoid_answer_text_leakage(self):
        from install_hooks import (
            _platform_hook_entry,
            _platform_session_intent_entry,
            _platform_session_receipt_entry,
            _platform_session_start_entry,
        )

        entries = [
            _platform_session_receipt_entry("claude", "SessionStart"),
            _platform_hook_entry("claude", "UserPromptSubmit"),
            _platform_session_intent_entry("claude", "UserPromptSubmit"),
            _platform_session_start_entry("claude", "SessionStart"),
        ]
        commands = [hook["command"] for entry in entries for hook in entry["hooks"]]

        for command in commands:
            encoded_args = re.findall(r'"([A-Za-z0-9_-]{40,}=*)"', command)
            self.assertTrue(encoded_args, msg=command)
            wrapped_command = base64.urlsafe_b64decode(encoded_args[0]).decode("utf-8")
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
        self.assertIn("merge-companion-precheck: clean (hook-verified)", result.stdout)
        self.assertIn("re-check it before changing an installed skill", result.stdout)
        self.assertIn("pending-merges/claude/manifest.json", result.stdout)
        self.assertNotIn("next time Claude/Codex is opened", result.stdout)
        self.assertNotIn("The next time you open Claude/Codex", result.stdout)


class TestExternalEvidenceHookRemoval(TempHomeTestCase):
    """External evidence is selected for claims, without an unconditional input hook."""

    def test_prompt_hooks_do_not_install_an_external_search_reminder(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                self._create_platform_dir(platform)
                self.assertEqual(install_hooks.install_hook(platform), "installed")
                commands = [hook["command"]
                            for entry in self._read_settings(platform)["hooks"]["UserPromptSubmit"]
                            for hook in entry["hooks"]]
                self.assertFalse(any("[web-search-first]" in command for command in commands))
                self.assertTrue(any(install_hooks.SESSION_INTENT_MARKER in command for command in commands))
                self.assertEqual(install_hooks.check_status(platform), "installed")

    def test_reinstall_removes_search_reminder_and_preserves_other_commands(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                self._create_platform_dir(platform)
                self.assertEqual(install_hooks.install_hook(platform), "installed")
                settings = self._read_settings(platform)
                settings["hooks"]["UserPromptSubmit"].append({
                    "matcher": "preserved", "custom": 7, "hooks": [
                        {"type": "command", "command": "echo old # [web-search-first]"},
                        {"type": "command", "command": "echo user-owned"},
                    ],
                })
                self._write_settings(platform, settings)
                self.assertEqual(install_hooks.install_hook(platform), "installed")
                hooks = self._read_settings(platform)["hooks"]["UserPromptSubmit"]
                self.assertFalse(any("[web-search-first]" in h["command"] for entry in hooks for h in entry["hooks"]))
                preserved = next(entry for entry in hooks if entry.get("matcher") == "preserved")
                self.assertEqual(preserved, {"matcher": "preserved", "custom": 7,
                                           "hooks": [{"type": "command", "command": "echo user-owned"}]})
                self.assertEqual(install_hooks.check_status(platform), "installed")
                self.assertEqual(install_hooks.install_hook(platform), "already")


class TestForegroundHookPayloads(TempHomeTestCase):
    """Installed foreground hooks preserve their platform payload contracts."""

    def test_user_prompt_hooks_emit_non_json_prefixed_text(self):
        for cmd, env in (
            (install_hooks.HOOK_COMMAND, None),
            (install_hooks.SESSION_INTENT_COMMAND, None),
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
        # With no response body, Stop has no assessment and returns only the host envelope.
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

    def test_codex_wrapped_stop_hook_preserves_bare_continue_json_under_minimal_visibility(self):
        env = os.environ.copy()
        env["HOME"] = str(self.fake_home)
        env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"
        command = lambda: install_hooks._entry_command(
            install_hooks._platform_stop_hook_entry("codex", "Stop")
        )

        result = _run_reviewed_stop_command(
            command,
            platform="codex",
            response_purpose="explanation",
            input_text=json.dumps({
                "session_id": "s-codex-wrapped-stop",
                "hook_event_name": "Stop",
                "last_assistant_message": "Here is why the hook emitted that message.",
            }),
            env=env,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.lstrip()[:1], "{", msg=result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload, {"continue": True})
        self.assertIsNotNone(result.stop_observation)
        assert_stop_observation(self, {'continue': True, 'response_assessment': result.stop_observation})

    def test_codex_stop_keeps_declared_completion_without_proof_unaccepted(self):
        result = _run_reviewed_stop_command(
            install_hooks.STOP_HOOK_COMMAND_CODEX,
            platform="codex",
            input_text=json.dumps({
                "hook_event_name": "Stop",
                "last_assistant_message": "The requested change is complete and tests pass.",
            }),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        assert_stop_observation(self, payload, issue='[completion-check]', repair_target='completion_metadata')
        self.assertNotIn("systemMessage", payload)

    def test_wrapped_stop_logs_unaccepted_body_without_native_continuation(self):
        for platform in ('claude', 'codex'):
            with self.subTest(platform=platform):
                command = lambda: install_hooks._entry_command(
                    install_hooks._platform_stop_hook_entry(platform, 'Stop'))
                result = _run_reviewed_stop_command(command, platform=platform,
                    response_purpose='explanation', record_review=False,
                    input_text=json.dumps(dict(session_id='unreviewed-' + platform,
                        hook_event_name='Stop', last_assistant_message='The source contains two options.')))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {'continue': True})
                self.assertIsNotNone(result.stop_observation)
                assert_stop_observation(self,
                    {'continue': True, 'response_assessment': result.stop_observation},
                    issue='response-assessment-required', repair_target='response_review')

    def test_codex_stop_hook_allows_explanatory_response_without_completion_check(self):
        result = _run_reviewed_stop_command(
            install_hooks.STOP_HOOK_COMMAND_CODEX,
            platform="codex",
            response_purpose="explanation",
            input_text=json.dumps({
                "hook_event_name": "Stop",
                "last_assistant_message": "Here is why that hook behavior repeated too much.",
            }),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        assert_stop_observation(self, payload)
        self.assertNotIn("systemMessage", payload)

    def test_codex_stop_hook_allows_valid_completion_check(self):
        result = _run_reviewed_stop_command(
            install_hooks.STOP_HOOK_COMMAND_CODEX,
            platform="codex",
            input_text=json.dumps({
                "hook_event_name": "Stop",
                "last_assistant_message": (
                    "[completion-check]\n"
                    "- acceptance-criteria:\n"
                    "  - crit-a: aligned [source: system-doc]\n"
                    "- claim-evidence-map:\n"
                    "  - claim: did the thing\n"
                    "    criterion: crit-a\n"
                    "    evidence: ran cmd 3/3 OK\n"
                    "    verdict: pass\n"
                    "- unverified:\n"
                    "  - none\n"
                    "- evidence: ran cmd"
                ),
            }),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        assert_stop_observation(self, payload)
        self.assertNotIn("systemMessage", payload)

    def test_codex_stop_hook_uses_validator_command(self):
        command = install_hooks.STOP_HOOK_COMMAND_CODEX

        self.assertIn("claude_stop_verification_hook.py", command)
        self.assertNotIn("completion-reminder: " + "Before " + "final " + "response", command)

    def test_codex_foreground_hooks_emit_valid_json_system_messages(self):
        cases = (
            (install_hooks.HOOK_COMMAND_CODEX, "hook-reminder"),
            (install_hooks.SESSION_START_COMMAND_CODEX, "merge-companion-precheck: clean (hook-verified)"),
        )

        for command, expected in cases:
            with self.subTest(expected=expected):
                result = _run_hook_command(command, env=_strict_hook_env({"HOME": str(self.fake_home)}))
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["continue"], True)
                self.assertIn(expected, payload["systemMessage"])
                if command == install_hooks.SESSION_START_COMMAND_CODEX:
                    receipt = payload["ghostAlicePendingMergeCheck"]
                    self.assertEqual(receipt["platform"], "codex")
                    self.assertEqual(receipt["undecided_count"], 0)
                    self.assertEqual(Path(receipt["manifest_path"]), self.fake_home / ".ghost-alice/pending-merges/codex/manifest.json")
                self.assertIsNone(re.search(r"[\uac00-\ud7a3]", payload["systemMessage"]))
                self.assertNotIn("User:", payload["systemMessage"])
                self.assertNotIn("Tech:", payload["systemMessage"])

    def test_codex_pending_merge_hooks_emit_explicit_hook_verified_clean_contract(self):
        cases = (install_hooks.SESSION_START_COMMAND_CODEX,)

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
                        "re-check it before changing an installed skill",
                        payload["systemMessage"],
                    )

    def test_codex_session_start_pending_merge_hook_ignores_other_platform_pending_manifest(self):
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

            result = _run_hook_command(install_hooks.SESSION_START_COMMAND_CODEX, env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("merge-companion-precheck: clean (hook-verified)", payload["systemMessage"])
        self.assertNotIn("claude", payload["systemMessage"])
        self.assertNotIn("30 undecided", payload["systemMessage"])

    def test_foreground_hook_messages_run_under_cmd_exe(self):
        for cmd, env in (
            (install_hooks.HOOK_COMMAND, None),
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
        settings = self._read_settings("claude")
        settings["hooks"]["UserPromptSubmit"].append(LEGACY_WEB_SEARCH_ENTRY)  # install fixture with codex_hooks enabled
        self._write_settings("claude", settings)
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
