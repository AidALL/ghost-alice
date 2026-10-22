#!/usr/bin/env python3
"""Tests for the Ghost-ALICE hook command runner."""

from __future__ import annotations

import base64
import hashlib
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
import hook_definitions
import hook_profile_gate
import install_hooks
import pending_merge_precheck_hook


def _make_node_executable(node: Path) -> None:
    if os.name != "nt":
        node.chmod(node.stat().st_mode | 0o100)


def _python_payload_command(args: str) -> str:
    executable = sys.executable.replace("\\", "/")
    return f"{executable} {args}"


def _write_node_runtime_config(home: Path, registrations: dict[str, Path]) -> None:
    for runtime in registrations.values():
        _make_node_executable(runtime)
    hook_profile_gate.runtime_config.save_config(
        {
            "hook_runtime": {
                "node": {
                    platform: str(runtime)
                    for platform, runtime in registrations.items()
                }
            }
        },
        home=home,
    )


def _strict_log_path(home: str | Path, platform: str, session_id: str) -> Path:
    return (
        Path(home)
        / ".ghost-alice"
        / "session-logs"
        / platform
        / session_id
        / "strict-hook-output.jsonl"
    )


def _isolated_node_runtime(root: Path, directory: str = "runtime") -> Path | None:
    resolved_node = shutil.which("node") or shutil.which("node.exe")
    if not resolved_node:
        return None
    installed_node = Path(resolved_node).resolve()
    node = root / directory / ("node.exe" if os.name == "nt" else "node")
    node.parent.mkdir(parents=True)
    try:
        os.link(installed_node, node)
    except OSError:
        shutil.copy2(installed_node, node)
    return node


def _write_codex_node_config(config_file: Path, node: Path) -> None:
    _make_node_executable(node)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        "[mcp_servers.node_repl.env]\n"
        f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n",
        encoding="utf-8",
    )


def _node_marker_command(node: Path, marker: Path) -> str:
    code = (
        "require('node:fs').writeFileSync("
        f"{json.dumps(marker.as_posix())}, 'ran')"
    )
    return f'"{node.as_posix()}" -e {shlex.quote(code)} -- --platform codex'


class TestHookRunnerExecutionGate(unittest.TestCase):
    def test_claude_wrapper_reconciles_before_pretool_or_stop_child_only(self):
        import session_intent_ledger as ledger
        import install_destination
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = install_destination.InstallDestination(mode='normal', owned_root=str(root),
                platform_dir=str(root / '.claude'), skills_dir=str(root / 'skills'),
                ghost_root=str(root / '.ghost-alice'), ledger_root=str(root / 'intent'),
                project_root=str(root), core_source_root=str(Path(__file__).resolve().parents[1]))
            for event in ('PreToolUse', 'Stop', 'UserPromptSubmit'):
                with self.subTest(event=event):
                    session = 'native-' + event
                    coords = dict(root=root / 'intent', platform='claude', session_id=session)
                    transcript = root / (session + '.jsonl')
                    transcript.write_text('')
                    ledger.stage_native_input(**coords, raw_input='Human request',
                        prompt_id='grain', transcript_path=str(transcript))
                    transcript.write_text(json.dumps(dict(type='user', sessionId=session,
                        promptId='grain', origin=dict(kind='human'), promptSource='sdk', turnOrigin='human',
                        message=dict(role='user', content='Human request'))) + '\n')
                    observed = []
                    def child(*args, **kwargs):
                        observed.append(ledger.read_session_state(**coords))
                        return subprocess.CompletedProcess(args[0], 0, '{}\n', '')
                    command = base64.urlsafe_b64encode(shlex.join([sys.executable, '-c', 'print(1)']).encode()).decode()
                    with (mock.patch.dict(os.environ, {'HOME': directory}, clear=True),
                          mock.patch.object(hook_profile_gate, '_read_stdin', return_value=json.dumps(
                              dict(session_id=session, hook_event_name=event, transcript_path=str(transcript)))),
                          mock.patch.object(hook_profile_gate.subprocess, 'run', side_effect=child),
                          mock.patch.object(sys, 'stdout', io.StringIO())):
                        hook_profile_gate._run('tool-checkpoint' if event == 'PreToolUse' else 'completion',
                            command, platform='claude', binding=destination.binding())
                    self.assertEqual(len(observed), 1)
                    self.assertEqual(bool(observed[0]['latest_input_event_id']), event != 'UserPromptSubmit')
                    self.assertEqual(bool(observed[0]['pending_native_inputs']), event == 'UserPromptSubmit')

    def test_claude_wrapper_warns_once_per_session_and_cause_for_a_waiting_input(self):
        import session_intent_ledger as ledger
        import install_destination
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = install_destination.InstallDestination(mode='normal', owned_root=str(root),
                platform_dir=str(root / '.claude'), skills_dir=str(root / 'skills'),
                ghost_root=str(root / '.ghost-alice'), ledger_root=str(root / 'intent'),
                project_root=str(root), core_source_root=str(Path(__file__).resolve().parents[1]))
            session = 'waiting-notice'
            coords = dict(root=root / 'intent', platform='claude', session_id=session)
            transcript = root / (session + '.jsonl')
            transcript.write_text('')
            ledger.stage_native_input(**coords, raw_input='Session message without a native row',
                                      prompt_id=None, transcript_path=str(transcript))
            command = base64.urlsafe_b64encode(shlex.join([sys.executable, '-c', 'print(1)']).encode()).decode()
            decision = {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask',
                                               'permissionDecisionReason': 'inner decision'}}
            outputs = []
            for event, child_stdout in (('PreToolUse', json.dumps(decision) + '\n'), ('Stop', ''), ('PreToolUse', '{}\n')):
                stdout = io.StringIO()
                with (mock.patch.dict(os.environ, {'HOME': directory, 'GHOST_ALICE_AGENT_VISIBILITY': 'minimal'},
                                      clear=True),
                      mock.patch.object(hook_profile_gate, '_read_stdin', return_value=json.dumps(
                          dict(session_id=session, hook_event_name=event, transcript_path=str(transcript)))),
                      mock.patch.object(hook_profile_gate.subprocess, 'run',
                                        return_value=subprocess.CompletedProcess([], 0, child_stdout, '')),
                      mock.patch.object(sys, 'stdout', stdout)):
                    hook_profile_gate._run('tool-checkpoint' if event == 'PreToolUse' else 'completion',
                        command, platform='claude', binding=destination.binding())
                outputs.append(stdout.getvalue())
            first = json.loads(outputs[0])
            self.assertIn('row-not-found', first['systemMessage'])
            self.assertEqual(first['hookSpecificOutput'], decision['hookSpecificOutput'])
            # The minimal profile renders an empty Stop envelope as plain text; neither later output warns.
            self.assertNotIn('Ghost-ALICE intake', outputs[1] + outputs[2])
            recorded = [event['event'] for event in ledger.read_session_events(**coords)
                        if event['event'] in ('native-input-waiting', 'native-input-notice')]
            self.assertEqual(recorded, ['native-input-waiting', 'native-input-notice'])

    def test_rejection_audit_uses_known_platform_without_platform_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, HOME=temporary, CODEX_THREAD_ID="native", GHOST_ALICE_SESSION_ID="generic")
            env.pop("GHOST_ALICE_PLATFORM", None)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(hook_profile_gate, "_read_stdin", return_value="{}"):
                hook_profile_gate._append_rejection_audit("tool-checkpoint", "rejected command", platform="codex")
            log = _strict_log_path(temporary, "codex", "native")
            row = json.loads(log.read_text().splitlines()[0])
            self.assertEqual(row["session_id"], "native")
            self.assertEqual(row["platform"], "codex")
            self.assertFalse(_strict_log_path(temporary, "codex", "generic").exists())

    def test_runner_normalizes_child_stdio_to_utf8(self):
        code = (
            "import hashlib, json, sys; "
            "prompt = json.load(sys.stdin)['prompt']; "
            "print(hashlib.sha256(prompt.encode('utf-8')).hexdigest())"
        )
        command = _python_payload_command(f"-c {shlex.quote(code)}")
        payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
        stdin_text = json.dumps(
            {"session_id": "s-child-utf8", "prompt": "상태 확인"},
            ensure_ascii=False,
        )

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "codex"
            env["GHOST_ALICE_SESSION_ID"] = "s-child-utf8"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "strict"
            env["PYTHONIOENCODING"] = "cp949:surrogateescape"

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(sys, "stdin", io.StringIO(stdin_text)),
                mock.patch.object(sys, "stdout", io.StringIO()),
            ):
                return_code = hook_profile_gate.run("prompt", payload)

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "codex"
                / "s-child-utf8"
                / "strict-hook-output.jsonl"
            )
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(return_code, 0, msg=row["stderr"])
        expected_digest = hashlib.sha256("상태 확인".encode("utf-8")).hexdigest()
        self.assertEqual(row["stdout"].strip(), expected_digest)

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

    def test_visibility_context_reads_pending_merge_manifest_only_for_its_owner(self):
        with tempfile.TemporaryDirectory() as temp_home:
            manifest = Path(temp_home) / ".ghost-alice" / "pending-merges" / "codex" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({"entries": [{"id": "one", "decided": False}]}) + "\n",
                encoding="utf-8",
            )

            for hook_id in ("session-intent", "completion", "session-start"):
                with self.subTest(hook_id=hook_id):
                    context = hook_profile_gate._visibility_context(
                        hook_id, "routine clean pass already persisted", "", 0,
                        env={"HOME": temp_home, "GHOST_ALICE_PLATFORM": "codex"}, hook_payload={},
                    )
                    self.assertEqual(bool(context.get("pending_merge_undecided")), hook_id == "session-start")

    def test_pending_merge_hook_result_reuses_completed_manifest_check(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env = {"HOME": temp_home, "GHOST_ALICE_PLATFORM": "codex"}
            manifest = Path(temp_home) / ".ghost-alice/pending-merges/codex/manifest.json"
            for count in (0, 1):
                receipt = {"platform": "codex", "manifest_path": str(manifest), "undecided_count": count}
                stdout = json.dumps({"continue": True, "ghostAlicePendingMergeCheck": receipt})
                with mock.patch.object(hook_profile_gate, "_has_pending_merge_undecided", side_effect=AssertionError("duplicate manifest read")):
                    context = hook_profile_gate._visibility_context("session-start", stdout, "", 0, env=env, declared="routine")
                self.assertEqual(bool(context.get("pending_merge_undecided")), count > 0)

    def test_invalid_merge_owner_result_keeps_manifest_fallback(self):
        env = {"HOME": "/isolated", "GHOST_ALICE_PLATFORM": "codex"}
        valid = {"platform": "codex", "manifest_path": "/isolated/.ghost-alice/pending-merges/codex/manifest.json", "undecided_count": 0}
        cases = [("session-start", {**valid, "platform": "claude"}, 0),
                 ("session-start", {**valid, "manifest_path": "/foreign/manifest.json"}, 0),
                 ("session-start", {**valid, "undecided_count": False}, 0),
                 ("session-start", {**valid, "undecided_count": -1}, 0),
                 ("session-start", valid, 1)]
        for hook, receipt, exit_code in cases:
            with self.subTest(hook=hook, receipt=receipt, exit_code=exit_code):
                with mock.patch.object(hook_profile_gate, "_has_pending_merge_undecided", return_value=True) as probe:
                    context = hook_profile_gate._visibility_context(hook, json.dumps({"ghostAlicePendingMergeCheck": receipt}), "", exit_code, env=env, declared="routine")
                probe.assert_called_once()
                self.assertTrue(context["pending_merge_undecided"])
        with mock.patch.object(hook_profile_gate, "_has_pending_merge_undecided") as probe:
            context = hook_profile_gate._visibility_context("session-intent", json.dumps({"ghostAlicePendingMergeCheck": valid}), "", 0, env=env, declared="routine")
        probe.assert_not_called()
        self.assertNotIn("pending_merge_undecided", context)

    def test_clean_merge_payload_does_not_repeat_workflow_boilerplate(self):
        with mock.patch.object(pending_merge_precheck_hook, "_pending_entries", return_value=[]):
            payload = json.loads(pending_merge_precheck_hook._json_payload("codex", "LONG WORKFLOW INSTRUCTION"))
        self.assertNotIn("LONG WORKFLOW INSTRUCTION", payload["systemMessage"])
        self.assertEqual(payload["ghostAlicePendingMergeCheck"]["undecided_count"], 0)

    def test_visibility_context_ignores_current_downstream_block_gate(self):
        with tempfile.TemporaryDirectory() as temp_home, tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            session_dir = root / "codex" / "s-block"
            session_dir.mkdir(parents=True)
            (session_dir / "intent-state.json").write_text(json.dumps({
                "schema_version": "session-intent-ledger.v1",
                "platform": "codex", "session_id": "s-block", "ledger_revision": 1,
                "latest_input_event_id": "evt-current", "latest_input_digest": "sha256:current",
                "latest_input_char_count": 1,
            }))
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
                hook_payload={"session_id": "s-block"},
            )

        self.assertNotIn("security_boundary", context)

    def test_visibility_context_ignores_stale_downstream_block_gate(self):
        with tempfile.TemporaryDirectory() as temp_home, tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            session_dir = root / "codex" / "s-stale"
            session_dir.mkdir(parents=True)
            (session_dir / "intent-state.json").write_text(json.dumps({
                "schema_version": "session-intent-ledger.v1",
                "platform": "codex", "session_id": "s-stale", "ledger_revision": 1,
                "latest_input_event_id": "evt-new", "latest_input_digest": "sha256:new",
                "latest_input_char_count": 1,
            }))
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
                hook_payload={"session_id": "s-stale"},
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
    def test_stale_adapter_hash_uses_only_current_exact_registration(self):
        import install_destination
        import addon_installer
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'adapters/demo.py'
            script.parent.mkdir()
            script.write_text('print("{}")\n')
            destination = install_destination.InstallDestination(mode='normal', owned_root=str(root),
                platform_dir=str(root / '.codex'), skills_dir=str(root / 'skills'),
                ghost_root=str(root / '.ghost-alice'), ledger_root=str(root / 'intent'),
                project_root=str(root), core_source_root=str(Path(__file__).resolve().parents[1]))
            binding = destination.binding()
            runner = 'adapter-demo-continue'
            binding['adapter'] = dict(adapter_id='demo', runner_id=runner, script=str(script),
                event='Stop', content_hash='0' * 64)
            current = json.loads(json.dumps(binding))
            current['adapter']['content_hash'] = hashlib.sha256(script.read_bytes()).hexdigest()
            payload = base64.urlsafe_b64encode(shlex.join([sys.executable, str(script)]).encode()).decode()
            command = shlex.join([sys.executable, str(Path(hook_profile_gate.__file__)), 'run',
                runner, 'codex', payload, '--binding-b64', install_destination.encode_binding(current)])
            config = root / '.codex/hooks.json'; config.parent.mkdir()
            config.write_text(json.dumps({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': command}]}]}}))
            spec = dict(args_policy='no_args', hook_id_by_event={'on_agent_stop': 'continue'},
                script_rel_by_event={'on_agent_stop': 'adapters/demo.py'})
            with (mock.patch.dict(os.environ, {'HOME': directory}, clear=True),
                  mock.patch.object(hook_definitions, 'load_privileged_adapter_allowlist', return_value={'demo': spec}),
                  mock.patch.object(hook_profile_gate, '_read_stdin', return_value=json.dumps(
                      dict(session_id='current', hook_event_name='Stop'))),
                  mock.patch.object(hook_profile_gate, '_validate_shell_command', return_value=[sys.executable, str(script)])):
                self.assertEqual(hook_profile_gate._run(runner, payload, platform='codex', binding=binding), 0)
                script.write_text('print("unregistered replacement")\n')
                with self.assertRaises(hook_profile_gate.HookCommandRejected):
                    hook_profile_gate._run(runner, payload, platform='codex', binding=binding)

    def setUp(self):
        # These synthetic sessions must not inherit the test runner's native Codex ID.
        native_identity = mock.patch.dict(os.environ, {"CODEX_THREAD_ID": ""})
        native_identity.start()
        self.addCleanup(native_identity.stop)

    def test_configured_node_runtime_uses_shared_usability_predicate(self):
        with tempfile.TemporaryDirectory() as temp_home:
            node = Path(temp_home) / ("node.exe" if os.name == "nt" else "node")
            node.write_text("# fake node runtime\n", encoding="utf-8")

            with mock.patch.object(
                hook_profile_gate.runtime_config,
                "is_usable_node_runtime",
                return_value=False,
                create=True,
            ) as usability:
                configured = hook_profile_gate._configured_node_runtime_from_value(str(node))

        self.assertIsNone(configured)
        usability.assert_called_once_with(node.resolve())

    def test_dynamic_node_runtime_rebinds_stale_codex_registration_to_current_config(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            stale_node = root / "codex-runtime" / "stale" / ("node.exe" if os.name == "nt" else "node")
            current_node = root / "codex-runtime" / "current" / ("node.exe" if os.name == "nt" else "node")
            path_node = root / "path-runtime" / ("node.exe" if os.name == "nt" else "node")
            current_node.parent.mkdir(parents=True, exist_ok=True)
            path_node.parent.mkdir(parents=True, exist_ok=True)
            current_node.write_text("# current Codex node\n", encoding="utf-8")
            path_node.write_text("# competing PATH node\n", encoding="utf-8")
            _make_node_executable(current_node)
            _make_node_executable(path_node)

            hook_profile_gate.runtime_config.save_config(
                {"hook_runtime": {"node": {"codex": str(stale_node)}}},
                home=root,
            )
            codex_home = root / ".codex"
            codex_home.mkdir(parents=True, exist_ok=True)
            (codex_home / "config.toml").write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(current_node.as_posix())}\n",
                encoding="utf-8",
            )
            command = (
                f'"{hook_profile_gate.runtime_config.HOOK_NODE_SENTINEL}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform codex --event PreToolUse --hook tool-checkpoint"
            )
            env = {
                "HOME": str(root),
                "USERPROFILE": str(root),
                "CODEX_HOME": str(codex_home),
                "PATH": str(path_node.parent),
                "GHOST_ALICE_PLATFORM": "codex",
            }

            with mock.patch.object(hook_profile_gate.shutil, "which", return_value=str(path_node)):
                argv = hook_profile_gate._validate_shell_command(command, env=env)

        self.assertEqual(Path(argv[0]).resolve(), current_node.resolve())

    def test_missing_legacy_managed_node_rebinds_to_current_codex_runtime(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            stale_node = (
                root
                / "AppData"
                / "Local"
                / "OpenAI"
                / "Codex"
                / "runtimes"
                / "cua_node"
                / "stale"
                / "bin"
                / ("node.exe" if os.name == "nt" else "node")
            )
            current_node = root / "codex-runtime" / ("node.exe" if os.name == "nt" else "node")
            current_node.parent.mkdir(parents=True, exist_ok=True)
            current_node.write_text("# current Codex node\n", encoding="utf-8")
            _make_node_executable(current_node)
            dispatcher = root / ".ghost-alice" / "hooks" / "ghost-alice-hook.mjs"
            dispatcher.parent.mkdir(parents=True, exist_ok=True)
            dispatcher.write_text("// managed dispatcher\n", encoding="utf-8")
            codex_home = root / ".codex"
            codex_home.mkdir(parents=True, exist_ok=True)
            (codex_home / "config.toml").write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(current_node.as_posix())}\n",
                encoding="utf-8",
            )
            session_intent_root = root / "project" / ".tmp" / "session-intent"
            command = (
                f'"{stale_node.as_posix()}" "{dispatcher.as_posix()}" '
                "--platform codex --event PreToolUse --hook tool-checkpoint "
                "--marker \"[tool-checkpoint] pre-tool-check\" "
                f'--session-intent-root "{session_intent_root.as_posix()}"'
            )
            env = {
                "HOME": str(root),
                "USERPROFILE": str(root),
                "CODEX_HOME": str(codex_home),
                "GHOST_ALICE_PLATFORM": "codex",
                "PATH": "",
            }

            argv = hook_profile_gate._validate_shell_command(command, env=env)
            unmanaged_dispatcher = root / "other" / "ghost-alice-hook.mjs"
            unmanaged_dispatcher.parent.mkdir(parents=True, exist_ok=True)
            unmanaged_dispatcher.write_text("// unmanaged dispatcher\n", encoding="utf-8")
            tampered_commands = {
                "dispatcher": command.replace(
                    dispatcher.as_posix(),
                    unmanaged_dispatcher.as_posix(),
                ),
                "marker": command.replace(
                    "[tool-checkpoint] pre-tool-check",
                    "[tool-checkpoint] altered",
                ),
            }
            for field, tampered in tampered_commands.items():
                with self.subTest(field=field), self.assertRaises(
                    hook_profile_gate.HookCommandRejected
                ):
                    hook_profile_gate._validate_shell_command(tampered, env=env)
            with self.subTest(field="outer-platform"), self.assertRaisesRegex(
                hook_profile_gate.HookCommandRejected,
                "hook platform mismatch",
            ):
                hook_profile_gate._validate_shell_command(
                    command,
                    env={**env, "GHOST_ALICE_PLATFORM": "claude"},
                )

        self.assertEqual(Path(argv[0]).resolve(), current_node.resolve())

    def test_dynamic_node_runtime_preserves_registered_claude_priority(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            claude_node = root / "claude-runtime" / ("node.exe" if os.name == "nt" else "node")
            path_node = root / "path-runtime" / claude_node.name
            codex_node = root / "codex-runtime" / claude_node.name
            for node, content in (
                (claude_node, "# registered Claude node\n"),
                (path_node, "# competing PATH node\n"),
                (codex_node, "# Codex fallback node\n"),
            ):
                node.parent.mkdir(parents=True, exist_ok=True)
                node.write_text(content, encoding="utf-8")
                _make_node_executable(node)
            _write_node_runtime_config(root, {"claude": claude_node})
            codex_home = root / ".codex"
            codex_home.mkdir(parents=True, exist_ok=True)
            (codex_home / "config.toml").write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(codex_node.as_posix())}\n",
                encoding="utf-8",
            )
            command = (
                f'"{hook_profile_gate.runtime_config.HOOK_NODE_SENTINEL}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform claude --event PreToolUse --hook tool-checkpoint"
            )
            env = {
                "HOME": str(root),
                "USERPROFILE": str(root),
                "CODEX_HOME": str(codex_home),
                "PATH": str(path_node.parent),
                "GHOST_ALICE_PLATFORM": "claude",
            }

            with mock.patch.object(hook_profile_gate.shutil, "which", return_value=str(path_node)):
                argv = hook_profile_gate._validate_shell_command(command, env=env)

        self.assertEqual(Path(argv[0]).resolve(), claude_node.resolve())

    def test_dynamic_node_runtime_rejects_outer_inner_platform_mismatch(self):
        command = (
            f'"{hook_profile_gate.runtime_config.HOOK_NODE_SENTINEL}" '
            f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
            "--platform claude --event PreToolUse --hook tool-checkpoint"
        )

        with self.assertRaisesRegex(
            hook_profile_gate.HookCommandRejected,
            "hook platform mismatch",
        ):
            hook_profile_gate._validate_shell_command(
                command,
                env={"GHOST_ALICE_PLATFORM": "codex", "PATH": ""},
            )

    def test_cached_node_requires_same_underlying_trusted_runtime_file(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            current_node = root / "codex-runtime" / "current" / ("node.exe" if os.name == "nt" else "node")
            cached_node = root / "codex-runtime" / "cached" / current_node.name
            copied_node = root / "codex-runtime" / "copied" / current_node.name
            current_node.parent.mkdir(parents=True, exist_ok=True)
            cached_node.parent.mkdir(parents=True, exist_ok=True)
            copied_node.parent.mkdir(parents=True, exist_ok=True)
            current_node.write_text("# trusted Node bytes\n", encoding="utf-8")
            _make_node_executable(current_node)
            os.link(current_node, cached_node)
            copied_node.write_bytes(current_node.read_bytes())
            _make_node_executable(copied_node)
            codex_home = root / ".codex"
            codex_home.mkdir(parents=True, exist_ok=True)
            (codex_home / "config.toml").write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(current_node.as_posix())}\n",
                encoding="utf-8",
            )
            env = {
                "HOME": str(root),
                "USERPROFILE": str(root),
                "CODEX_HOME": str(codex_home),
                "PATH": "",
            }
            suffix = (
                f' "{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform codex --event PreToolUse --hook tool-checkpoint"
            )

            cached_argv = hook_profile_gate._validate_shell_command(
                f'"{cached_node.as_posix()}"{suffix}',
                env=env,
            )
            with self.assertRaises(hook_profile_gate.HookCommandRejected):
                hook_profile_gate._validate_shell_command(
                    f'"{copied_node.as_posix()}"{suffix}',
                    env=env,
                )

        self.assertEqual(Path(cached_argv[0]).resolve(), cached_node.resolve())

    @unittest.skipUnless(os.name != "nt", "POSIX execute bits are not enforced on Windows")
    def test_posix_non_executable_configured_node_sources_are_rejected(self):
        for source in ("env", "codex-config", "registration"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temp_home:
                root = Path(temp_home)
                node = root / source / "node"
                node.parent.mkdir()
                node.write_text("# fake node runtime\n", encoding="utf-8")
                node.chmod(0o600)
                platform = "codex" if source == "codex-config" else "claude"
                env = {"HOME": str(root), "USERPROFILE": str(root), "PATH": ""}
                if source == "env":
                    env["GHOST_ALICE_NODE"] = str(node)
                elif source == "codex-config":
                    codex_home = root / ".codex"
                    env["CODEX_HOME"] = str(codex_home)
                    config_file = codex_home / "config.toml"
                    config_file.parent.mkdir(parents=True)
                    config_file.write_text(
                        "[mcp_servers.node_repl.env]\n"
                        f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n",
                        encoding="utf-8",
                    )
                else:
                    hook_profile_gate.runtime_config.save_config(
                        {"hook_runtime": {"node": {platform: str(node.resolve())}}},
                        home=root,
                    )

                argv = [str(node), "ghost-alice-hook.mjs", "--platform", platform]
                with (
                    mock.patch.dict(os.environ, env, clear=True),
                    mock.patch.object(hook_profile_gate.shutil, "which", return_value=None),
                    self.assertRaises(hook_profile_gate.HookCommandRejected),
                ):
                    hook_profile_gate.assert_allowed_command(argv, [])

    @unittest.skipUnless(os.name != "nt", "POSIX execute bits are not enforced on Windows")
    def test_posix_executable_configured_node_sources_are_accepted(self):
        for source in ("env", "codex-config", "registration"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temp_home:
                root = Path(temp_home)
                node = root / source / "node"
                node.parent.mkdir()
                node.write_text("# fake node runtime\n", encoding="utf-8")
                node.chmod(0o700)
                platform = "codex" if source == "codex-config" else "claude"
                env = {"HOME": str(root), "USERPROFILE": str(root), "PATH": ""}
                if source == "env":
                    env["GHOST_ALICE_NODE"] = str(node)
                elif source == "codex-config":
                    codex_home = root / ".codex"
                    env["CODEX_HOME"] = str(codex_home)
                    config_file = codex_home / "config.toml"
                    config_file.parent.mkdir(parents=True)
                    config_file.write_text(
                        "[mcp_servers.node_repl.env]\n"
                        f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n",
                        encoding="utf-8",
                    )
                else:
                    hook_profile_gate.runtime_config.save_config(
                        {"hook_runtime": {"node": {platform: str(node.resolve())}}},
                        home=root,
                    )

                argv = [str(node), "ghost-alice-hook.mjs", "--platform", platform]
                with (
                    mock.patch.dict(os.environ, env, clear=True),
                    mock.patch.object(hook_profile_gate.shutil, "which", return_value=None),
                ):
                    hook_profile_gate.assert_allowed_command(argv, [])

    def test_allows_node_runtime_resolved_from_current_path(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = root / ("node.exe" if os.name == "nt" else "node")
            node.write_text("# path node\n", encoding="utf-8")
            _make_node_executable(node)
            command = f'"{node.as_posix()}" "{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}"'
            env = {
                "PATH": str(root),
                "PATHEXT": ".EXE",
                "GHOST_ALICE_PLATFORM": "claude",
                "GHOST_ALICE_AGENT_VISIBILITY": "strict",
            }

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("shutil.which", return_value=str(node)),
            ):
                try:
                    argv = hook_profile_gate._validate_shell_command(command)
                except hook_profile_gate.HookCommandRejected as exc:
                    self.fail(f"PATH-resolved Node runtime was rejected: {exc}")

        self.assertEqual(Path(argv[0]).resolve(), node.resolve())

    def test_userprofile_locates_configured_node_when_home_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            codex_home = root / ".codex"
            node = root / "codex-runtime" / ("node.exe" if os.name == "nt" else "node")
            node.parent.mkdir(parents=True, exist_ok=True)
            node.write_text("# configured node\n", encoding="utf-8")
            _make_node_executable(node)
            codex_home.mkdir(parents=True, exist_ok=True)
            (codex_home / "config.toml").write_text(
                "[mcp_servers.node_repl.env]\n"
                f"NODE_REPL_NODE_PATH = {json.dumps(node.as_posix())}\n",
                encoding="utf-8",
            )
            command = f'"{node.as_posix()}" "{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}"'
            env = {
                "USERPROFILE": str(root),
                "GHOST_ALICE_PLATFORM": "codex",
                "GHOST_ALICE_AGENT_VISIBILITY": "strict",
            }

            with mock.patch.dict(os.environ, env, clear=True):
                argv = hook_profile_gate._validate_shell_command(command)

        self.assertEqual(Path(argv[0]).resolve(), node.resolve())

    def test_persisted_node_registration_uses_inner_platform_when_outer_env_conflicts(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = root / "claude-runtime" / ("node.exe" if os.name == "nt" else "node")
            node.parent.mkdir(parents=True)
            node.write_text("# registered claude node\n", encoding="utf-8")
            path_node = root / "path-runtime" / ("node.exe" if os.name == "nt" else "node")
            path_node.parent.mkdir(parents=True)
            path_node.write_text("# different PATH node\n", encoding="utf-8")
            _write_node_runtime_config(root, {"claude": node})
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform claude"
            )
            env = {
                "HOME": str(root),
                "GHOST_ALICE_PLATFORM": "codex",
                "PATH": str(path_node.parent),
            }

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("shutil.which", return_value=str(path_node)),
            ):
                try:
                    argv = hook_profile_gate._validate_shell_command(command)
                except hook_profile_gate.HookCommandRejected as exc:
                    self.fail(f"active-platform registered Node runtime was rejected: {exc}")

        self.assertEqual(Path(argv[0]).resolve(), node.resolve())

    def test_persisted_node_registration_uses_inner_platform_when_outer_env_is_absent(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = root / "codex-runtime" / ("node.exe" if os.name == "nt" else "node")
            node.parent.mkdir(parents=True)
            node.write_text("# registered codex node\n", encoding="utf-8")
            _write_node_runtime_config(root, {"codex": node})
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform codex"
            )
            env = {"HOME": str(root), "PATH": ""}

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("shutil.which", return_value=None),
            ):
                argv = hook_profile_gate._validate_shell_command(command)

        self.assertEqual(Path(argv[0]).resolve(), node.resolve())

    def test_persisted_node_registration_does_not_authorize_other_platform(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = root / "claude-runtime" / ("node.exe" if os.name == "nt" else "node")
            node.parent.mkdir(parents=True)
            node.write_text("# registered claude node\n", encoding="utf-8")
            _write_node_runtime_config(root, {"claude": node})
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform codex"
            )
            env = {
                "HOME": str(root),
                "GHOST_ALICE_PLATFORM": "claude",
                "PATH": "",
            }

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("shutil.which", return_value=None),
                self.assertRaises(hook_profile_gate.HookCommandRejected),
            ):
                hook_profile_gate._validate_shell_command(command)

    def test_outer_platform_does_not_authorize_persisted_node_without_inner_platform(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = root / "claude-runtime" / ("node.exe" if os.name == "nt" else "node")
            node.parent.mkdir(parents=True)
            node.write_text("# registered claude node\n", encoding="utf-8")
            _write_node_runtime_config(root, {"claude": node})
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}"'
            )
            env = {
                "HOME": str(root),
                "GHOST_ALICE_PLATFORM": "claude",
                "PATH": "",
            }

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("shutil.which", return_value=None),
                self.assertRaises(hook_profile_gate.HookCommandRejected),
            ):
                hook_profile_gate._validate_shell_command(command)

    def test_explicit_global_node_sources_remain_platform_independent(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = root / ("node.exe" if os.name == "nt" else "node")
            node.write_text("# globally configured node\n", encoding="utf-8")
            _make_node_executable(node)
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform codex"
            )

            for env_name in ("GHOST_ALICE_NODE", "NODE_REPL_NODE_PATH"):
                with self.subTest(env_name=env_name):
                    env = {
                        env_name: str(node),
                        "GHOST_ALICE_PLATFORM": "claude",
                        "PATH": "",
                    }
                    with (
                        mock.patch.dict(os.environ, env, clear=True),
                        mock.patch("shutil.which", return_value=None),
                    ):
                        argv = hook_profile_gate._validate_shell_command(command)

                    self.assertEqual(Path(argv[0]).resolve(), node.resolve())

    def test_duplicate_inner_platform_arguments_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = root / ("node.exe" if os.name == "nt" else "node")
            node.write_text("# globally configured node\n", encoding="utf-8")
            _make_node_executable(node)
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform claude --platform claude"
            )
            env = {"GHOST_ALICE_NODE": str(node), "PATH": ""}

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("shutil.which", return_value=None),
                self.assertRaisesRegex(
                    hook_profile_gate.HookCommandRejected,
                    "duplicate hook platform",
                ),
            ):
                hook_profile_gate._validate_shell_command(command)

    def test_invalid_inner_platform_argument_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = root / ("node.exe" if os.name == "nt" else "node")
            node.write_text("# globally configured node\n", encoding="utf-8")
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform other"
            )
            env = {"GHOST_ALICE_NODE": str(node), "PATH": ""}

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("shutil.which", return_value=None),
                self.assertRaisesRegex(
                    hook_profile_gate.HookCommandRejected,
                    "invalid hook platform",
                ),
            ):
                hook_profile_gate._validate_shell_command(command)

    def test_equals_and_mixed_platform_forms_fail_closed_with_audit(self):
        forms = (
            "--platform=claude",
            "--platform=codex",
            "--platform claude --platform=codex",
            "--platform=claude --platform codex",
        )
        for index, platform_args in enumerate(forms):
            with self.subTest(platform_args=platform_args), tempfile.TemporaryDirectory() as temp_home:
                root = Path(temp_home)
                node = root / ("node.exe" if os.name == "nt" else "node")
                node.write_text("# globally configured node\n", encoding="utf-8")
                command = (
                    f'"{node.as_posix()}" '
                    f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                    f"{platform_args}"
                )
                payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
                session_id = f"s-equals-platform-{index}"
                stdin_text = json.dumps(
                    {"session_id": session_id, "hook_event_name": "PreToolUse"}
                )
                env = {
                    "HOME": temp_home,
                    "USERPROFILE": temp_home,
                    "GHOST_ALICE_PLATFORM": "codex",
                    "GHOST_ALICE_NODE": str(node),
                    "PATH": "",
                }
                stderr = io.StringIO()

                with (
                    mock.patch.dict(os.environ, env, clear=True),
                    mock.patch.object(sys, "stdin", io.StringIO(stdin_text)),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        hook_profile_gate.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], 0, "", ""),
                    ) as child_run,
                    self.assertRaises(SystemExit) as cm,
                ):
                    hook_profile_gate.main(["run", "tool-checkpoint", payload])

                self.assertEqual(cm.exception.code, 126)
                self.assertIn("hook command rejected:", stderr.getvalue())
                child_run.assert_not_called()
                log_path = _strict_log_path(root, "unknown", session_id)
                self.assertTrue(log_path.is_file(), "equals-form rejection was not audited")
                row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(row["exit_code"], 126)
                self.assertEqual(row["visible_decision"], "force_show")

    def test_rejection_audit_uses_validated_inner_platform(self):
        cases = (
            ("claude", "codex"),
            ("codex", None),
        )
        for inner_platform, outer_platform in cases:
            with self.subTest(
                inner_platform=inner_platform,
                outer_platform=outer_platform,
            ), tempfile.TemporaryDirectory() as temp_home:
                root = Path(temp_home)
                node = root / ("node.exe" if os.name == "nt" else "node")
                node.write_text("# unregistered node\n", encoding="utf-8")
                command = (
                    f'"{node.as_posix()}" '
                    f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                    f"--platform {inner_platform}"
                )
                payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
                session_id = f"s-inner-audit-{inner_platform}"
                env = {
                    "HOME": temp_home,
                    "USERPROFILE": temp_home,
                    "PATH": "",
                }
                if outer_platform is not None:
                    env["GHOST_ALICE_PLATFORM"] = outer_platform

                with (
                    mock.patch.dict(os.environ, env, clear=True),
                    mock.patch.object(
                        sys,
                        "stdin",
                        io.StringIO(json.dumps({"session_id": session_id})),
                    ),
                    mock.patch.object(sys, "stderr", io.StringIO()),
                    self.assertRaises(SystemExit) as cm,
                ):
                    hook_profile_gate.main(["run", "tool-checkpoint", payload])

                self.assertEqual(cm.exception.code, 126)
                log_path = _strict_log_path(root, inner_platform, session_id)
                self.assertTrue(log_path.is_file(), "inner-platform audit log was not created")
                row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(row["platform"], inner_platform)

    def test_malformed_payload_with_unsupported_outer_platform_audits_under_unknown(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            session_id = "s-unknown-audit-platform"
            env = {
                "HOME": temp_home,
                "USERPROFILE": temp_home,
                "GHOST_ALICE_PLATFORM": "unsupported",
                "GHOST_ALICE_SESSION_ID": session_id,
            }
            stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(sys, "stdin", io.StringIO("")),
                mock.patch.object(sys, "stderr", stderr),
                self.assertRaises(SystemExit) as cm,
            ):
                hook_profile_gate.main(["run", "tool-checkpoint", "not-base64!"])

            self.assertEqual(cm.exception.code, 126)
            log_path = _strict_log_path(root, "unknown", session_id)
            self.assertTrue(log_path.is_file(), "unknown-platform audit log was not created")
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["platform"], "unknown")

    def test_malformed_payload_rejection_uses_positional_platform_for_audit(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            session_id = "s-positional-audit-platform"
            env = {
                "HOME": temp_home,
                "USERPROFILE": temp_home,
                "GHOST_ALICE_SESSION_ID": session_id,
            }
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(sys, "stdin", io.StringIO("")),
                mock.patch.object(sys, "stderr", io.StringIO()),
                self.assertRaises(SystemExit) as cm,
            ):
                hook_profile_gate.main(
                    ["run", "tool-checkpoint", "codex", "not-base64!"]
                )

            self.assertEqual(cm.exception.code, 126)
            log_path = _strict_log_path(root, "codex", session_id)
            self.assertTrue(log_path.is_file(), "positional-platform audit log was not created")
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["platform"], "codex")

    def test_unregistered_node_outside_current_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = root / ("node.exe" if os.name == "nt" else "node")
            node.write_text("# unregistered node\n", encoding="utf-8")
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform claude"
            )

            with (
                mock.patch.dict(os.environ, {"PATH": ""}, clear=True),
                mock.patch("shutil.which", return_value=None),
                self.assertRaises(hook_profile_gate.HookCommandRejected),
            ):
                hook_profile_gate._validate_shell_command(command)

    def test_configured_runtime_with_non_node_basename_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            runtime = root / ("node-wrapper.exe" if os.name == "nt" else "node-wrapper")
            runtime.write_text("# not node\n", encoding="utf-8")
            command = (
                f'"{runtime.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform claude"
            )

            with (
                mock.patch.dict(
                    os.environ,
                    {"GHOST_ALICE_NODE": str(runtime), "PATH": ""},
                    clear=True,
                ),
                mock.patch("shutil.which", return_value=None),
                self.assertRaises(hook_profile_gate.HookCommandRejected),
            ):
                hook_profile_gate._validate_shell_command(command)

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
        with tempfile.TemporaryDirectory() as temp_home:
            session_id = f"s-shell-injection-{Path(temp_home).name}"
            real_log_path = _strict_log_path(Path.home(), "unknown", session_id)
            self.assertFalse(real_log_path.exists())
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["USERPROFILE"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "codex"
            env["GHOST_ALICE_SESSION_ID"] = session_id
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "strict"

            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run", "prompt", "strict", payload],
                input=json.dumps({"session_id": session_id}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            isolated_log_path = _strict_log_path(temp_home, "unknown", session_id)
            self.assertTrue(isolated_log_path.is_file())
            self.assertFalse(real_log_path.exists())

        self.assertEqual(result.returncode, 126)
        self.assertIn("rejected", result.stderr.lower())

    def test_cli_rejects_unterminated_quote_cleanly_without_running_child(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            marker = root / "child-ran.txt"
            code = (
                "from pathlib import Path; "
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')"
            )
            command = f"{_python_payload_command(f'-c {shlex.quote(code)}')} '"
            payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
            session_id = f"s-unterminated-quote-{root.name}"
            stdin_text = json.dumps(
                {
                    "session_id": session_id,
                    "hook_event_name": "PreToolUse",
                    "tool_input": {"secret": "must-not-be-stored"},
                }
            )
            real_log_path = _strict_log_path(Path.home(), "claude", session_id)
            self.assertFalse(real_log_path.exists())
            env = os.environ.copy()
            env.update({
                "HOME": temp_home,
                "USERPROFILE": temp_home,
                "GHOST_ALICE_PLATFORM": "claude",
                "GHOST_ALICE_SESSION_ID": session_id,
                "GHOST_ALICE_AGENT_VISIBILITY": "strict",
            })
            env.pop("GHOST_ALICE_DISABLED_HOOKS", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("hook_profile_gate.py")),
                    "run",
                    "tool-checkpoint",
                    payload,
                ],
                input=stdin_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            self.assertEqual(
                result.stderr,
                "hook command rejected: hook command parse failed: No closing quotation\n",
            )
            self.assertEqual(result.returncode, 126)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(marker.exists(), "unterminated command unexpectedly ran its child")
            log_path = _strict_log_path(root, "claude", session_id)
            self.assertTrue(log_path.is_file(), "unterminated command rejection was not audited")
            row_text = log_path.read_text(encoding="utf-8").splitlines()[0]
            row = json.loads(row_text)
            self.assertEqual(row["platform"], "claude")
            self.assertEqual(row["exit_code"], 126)
            self.assertEqual(row["stdout"], "")
            self.assertEqual(row["visible_decision"], "force_show")
            self.assertNotIn("stdin", row)
            self.assertNotIn("must-not-be-stored", row_text)
            self.assertFalse(real_log_path.exists())

    def test_codex_home_config_is_exclusive_over_default_home_config(self):
        if not (shutil.which("node") or shutil.which("node.exe")):
            self.skipTest("Node runtime is required for the CODEX_HOME isolation probe")

        for config_label, config_bytes in (
            ("empty", b""),
            ("invalid-utf8", b"\xff\xfe\x80"),
        ):
            with self.subTest(config=config_label), tempfile.TemporaryDirectory() as temp_home:
                root = Path(temp_home)
                node = _isolated_node_runtime(root, "inactive-home-runtime")
                if node is None:
                    self.skipTest("Node runtime disappeared during the isolation probe")
                _write_codex_node_config(root / ".codex" / "config.toml", node)
                codex_home = root / "active-codex-home"
                codex_home.mkdir()
                (codex_home / "config.toml").write_bytes(config_bytes)
                marker = root / f"inactive-home-node-ran-{config_label}.txt"
                payload = base64.urlsafe_b64encode(
                    _node_marker_command(node, marker).encode("utf-8")
                ).decode("ascii")
                session_id = f"s-codex-home-exclusive-{config_label}-{root.name}"
                stdin_text = json.dumps(
                    {
                        "session_id": session_id,
                        "hook_event_name": "PreToolUse",
                        "tool_input": {"secret": "must-not-be-stored"},
                    }
                )
                real_log_path = _strict_log_path(Path.home(), "codex", session_id)
                self.assertFalse(real_log_path.exists())
                env = os.environ.copy()
                env.update({
                    "HOME": temp_home,
                    "USERPROFILE": temp_home,
                    "CODEX_HOME": str(codex_home),
                    "GHOST_ALICE_PLATFORM": "claude",
                    "GHOST_ALICE_SESSION_ID": session_id,
                    "GHOST_ALICE_AGENT_VISIBILITY": "strict",
                    "PATH": "",
                })
                env.pop("GHOST_ALICE_DISABLED_HOOKS", None)
                env.pop("GHOST_ALICE_NODE", None)
                env.pop("NODE_REPL_NODE_PATH", None)

                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("hook_profile_gate.py")),
                        "run",
                        "tool-checkpoint",
                        payload,
                    ],
                    input=stdin_text,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=False,
                )

                self.assertEqual(result.returncode, 126, msg=result.stderr)
                self.assertEqual(
                    result.stderr,
                    f"hook command rejected: hook executable outside allowlist: {node.resolve()}\n",
                )
                self.assertFalse(marker.exists(), "inactive HOME config authorized its Node")
                log_path = _strict_log_path(root, "codex", session_id)
                self.assertTrue(log_path.is_file(), "CODEX_HOME isolation rejection was not audited")
                row_text = log_path.read_text(encoding="utf-8").splitlines()[0]
                row = json.loads(row_text)
                self.assertEqual(row["exit_code"], 126)
                self.assertEqual(row["stdout"], "")
                self.assertEqual(row["visible_decision"], "force_show")
                self.assertNotIn("stdin", row)
                self.assertNotIn("must-not-be-stored", row_text)
                self.assertFalse(real_log_path.exists())

    def test_valid_codex_home_config_authorizes_its_node(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            node = _isolated_node_runtime(root, "codex-home-runtime")
            if node is None:
                self.skipTest("Node runtime is required for the CODEX_HOME positive probe")
            codex_home = root / "active-codex-home"
            _write_codex_node_config(codex_home / "config.toml", node)
            marker = root / "codex-home-node-ran.txt"
            payload = base64.urlsafe_b64encode(
                _node_marker_command(node, marker).encode("utf-8")
            ).decode("ascii")
            session_id = f"s-valid-codex-home-{root.name}"
            real_log_path = _strict_log_path(Path.home(), "codex", session_id)
            self.assertFalse(real_log_path.exists())
            env = os.environ.copy()
            env.update({
                "HOME": temp_home,
                "USERPROFILE": temp_home,
                "CODEX_HOME": str(codex_home),
                "GHOST_ALICE_PLATFORM": "codex",
                "GHOST_ALICE_SESSION_ID": session_id,
                "GHOST_ALICE_AGENT_VISIBILITY": "strict",
                "PATH": "",
            })
            env.pop("GHOST_ALICE_DISABLED_HOOKS", None)
            env.pop("GHOST_ALICE_NODE", None)
            env.pop("NODE_REPL_NODE_PATH", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("hook_profile_gate.py")),
                    "run",
                    "tool-checkpoint",
                    payload,
                ],
                input=json.dumps({"session_id": session_id}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(marker.is_file(), "valid CODEX_HOME config did not authorize its Node")
            self.assertFalse(real_log_path.exists())

    def test_default_home_codex_config_is_used_when_codex_home_is_absent_or_empty(self):
        for codex_home_value in (None, ""):
            with self.subTest(codex_home=codex_home_value), tempfile.TemporaryDirectory() as temp_home:
                root = Path(temp_home)
                node = _isolated_node_runtime(root, "default-home-runtime")
                if node is None:
                    self.skipTest("Node runtime is required for the default-home positive probe")
                _write_codex_node_config(root / ".codex" / "config.toml", node)
                marker = root / "default-home-node-ran.txt"
                payload = base64.urlsafe_b64encode(
                    _node_marker_command(node, marker).encode("utf-8")
                ).decode("ascii")
                session_id = f"s-default-codex-home-{codex_home_value!r}-{root.name}"
                real_log_path = _strict_log_path(Path.home(), "codex", session_id)
                self.assertFalse(real_log_path.exists())
                env = os.environ.copy()
                env.update({
                    "HOME": temp_home,
                    "USERPROFILE": temp_home,
                    "GHOST_ALICE_PLATFORM": "codex",
                    "GHOST_ALICE_SESSION_ID": session_id,
                    "GHOST_ALICE_AGENT_VISIBILITY": "strict",
                    "PATH": "",
                })
                if codex_home_value is None:
                    env.pop("CODEX_HOME", None)
                else:
                    env["CODEX_HOME"] = codex_home_value
                env.pop("GHOST_ALICE_DISABLED_HOOKS", None)
                env.pop("GHOST_ALICE_NODE", None)
                env.pop("NODE_REPL_NODE_PATH", None)

                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("hook_profile_gate.py")),
                        "run",
                        "tool-checkpoint",
                        payload,
                    ],
                    input=json.dumps({"session_id": session_id}),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertTrue(marker.is_file(), "default HOME config did not authorize its Node")
                self.assertFalse(real_log_path.exists())

    def test_invalid_utf8_codex_toml_does_not_block_independent_node_sources(self):
        resolved_node = shutil.which("node") or shutil.which("node.exe")
        if not resolved_node:
            self.skipTest("Node runtime is required for the subprocess authorization probe")
        installed_node = Path(resolved_node).resolve()

        for source in ("global", "path"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temp_home:
                root = Path(temp_home)
                codex_home = root / ".codex"
                codex_home.mkdir()
                (codex_home / "config.toml").write_bytes(b"\xff\xfe\x80")
                node = root / "runtime" / ("node.exe" if os.name == "nt" else "node")
                node.parent.mkdir()
                try:
                    os.link(installed_node, node)
                except OSError:
                    shutil.copy2(installed_node, node)
                marker = root / f"{source}-node-ran.txt"
                code = (
                    "require('node:fs').writeFileSync("
                    f"{json.dumps(marker.as_posix())}, 'ran')"
                )
                command = (
                    f'"{node.as_posix()}" -e {shlex.quote(code)} '
                    "-- --platform codex"
                )
                payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
                session_id = f"s-invalid-utf8-codex-toml-{source}-{root.name}"
                real_log_path = _strict_log_path(Path.home(), "codex", session_id)
                self.assertFalse(real_log_path.exists())
                env = os.environ.copy()
                env.update({
                    "HOME": temp_home,
                    "USERPROFILE": temp_home,
                    "GHOST_ALICE_PLATFORM": "codex",
                    "GHOST_ALICE_SESSION_ID": session_id,
                    "GHOST_ALICE_AGENT_VISIBILITY": "strict",
                })
                env.pop("CODEX_HOME", None)
                env.pop("GHOST_ALICE_DISABLED_HOOKS", None)
                env.pop("GHOST_ALICE_NODE", None)
                env.pop("NODE_REPL_NODE_PATH", None)
                if source == "global":
                    env["GHOST_ALICE_NODE"] = str(node)
                    env["PATH"] = ""
                else:
                    env["PATH"] = str(node.parent)

                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("hook_profile_gate.py")),
                        "run",
                        "tool-checkpoint",
                        payload,
                    ],
                    input=json.dumps({"session_id": session_id}),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertTrue(marker.is_file(), f"{source} Node source was not executed")
                self.assertFalse(real_log_path.exists())

    def test_invalid_utf8_codex_toml_rejects_unregistered_node_cleanly_and_audits(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            codex_home = root / ".codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_bytes(b"\xff\xfe\x80")
            node = root / "unregistered" / ("node.exe" if os.name == "nt" else "node")
            node.parent.mkdir()
            node.write_text("# unregistered node\n", encoding="utf-8")
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform codex"
            )
            payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
            session_id = f"s-invalid-utf8-codex-toml-reject-{root.name}"
            stdin_text = json.dumps(
                {
                    "session_id": session_id,
                    "hook_event_name": "PreToolUse",
                    "tool_input": {"secret": "must-not-be-stored"},
                }
            )
            real_log_path = _strict_log_path(Path.home(), "codex", session_id)
            self.assertFalse(real_log_path.exists())
            env = os.environ.copy()
            env.update({
                "HOME": temp_home,
                "USERPROFILE": temp_home,
                "GHOST_ALICE_PLATFORM": "claude",
                "GHOST_ALICE_SESSION_ID": session_id,
                "GHOST_ALICE_AGENT_VISIBILITY": "strict",
                "PATH": "",
            })
            env.pop("CODEX_HOME", None)
            env.pop("GHOST_ALICE_DISABLED_HOOKS", None)
            env.pop("GHOST_ALICE_NODE", None)
            env.pop("NODE_REPL_NODE_PATH", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("hook_profile_gate.py")),
                    "run",
                    "tool-checkpoint",
                    payload,
                ],
                input=stdin_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 126, msg=result.stderr)
            self.assertEqual(
                result.stderr,
                f"hook command rejected: hook executable outside allowlist: {node.resolve()}\n",
            )
            self.assertNotIn("Traceback", result.stderr)
            log_path = _strict_log_path(root, "codex", session_id)
            self.assertTrue(log_path.is_file(), "unregistered Node rejection was not audited")
            row_text = log_path.read_text(encoding="utf-8").splitlines()[0]
            row = json.loads(row_text)
            self.assertEqual(row["platform"], "codex")
            self.assertEqual(row["exit_code"], 126)
            self.assertEqual(row["stdout"], "")
            self.assertEqual(row["visible_decision"], "force_show")
            self.assertNotIn("stdin", row)
            self.assertNotIn("must-not-be-stored", row_text)
            self.assertFalse(real_log_path.exists())

    def test_malformed_codex_toml_shapes_reject_cleanly_and_are_audited(self):
        malformed_shapes = (
            'mcp_servers = "not-a-table"\n',
            '[mcp_servers]\nnode_repl = "not-a-table"\n',
            '[mcp_servers.node_repl]\nenv = "not-a-table"\n',
        )
        for index, config_text in enumerate(malformed_shapes):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp_home:
                root = Path(temp_home)
                codex_home = root / ".codex"
                codex_home.mkdir()
                (codex_home / "config.toml").write_text(config_text, encoding="utf-8")
                node = root / ("node.exe" if os.name == "nt" else "node")
                node.write_text("# unregistered node\n", encoding="utf-8")
                command = (
                    f'"{node.as_posix()}" '
                    f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                    "--platform codex"
                )
                payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
                session_id = f"s-malformed-toml-{index}-{root.name}"
                real_log_path = _strict_log_path(Path.home(), "codex", session_id)
                self.assertFalse(real_log_path.exists())
                env = os.environ.copy()
                env.update({
                    "HOME": temp_home,
                    "USERPROFILE": temp_home,
                    "GHOST_ALICE_PLATFORM": "codex",
                    "GHOST_ALICE_SESSION_ID": session_id,
                    "PATH": "",
                })
                env.pop("GHOST_ALICE_NODE", None)
                env.pop("NODE_REPL_NODE_PATH", None)
                env.pop("CODEX_HOME", None)

                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("hook_profile_gate.py")),
                        "run",
                        "tool-checkpoint",
                        payload,
                    ],
                    input=json.dumps({"session_id": session_id}),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=False,
                )

                self.assertEqual(result.returncode, 126, msg=result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                log_path = _strict_log_path(root, "codex", session_id)
                self.assertTrue(log_path.is_file(), "malformed TOML rejection was not audited")
                self.assertFalse(real_log_path.exists())

    def test_malformed_codex_toml_does_not_block_explicit_global_node(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            codex_home = root / ".codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                'mcp_servers = "not-a-table"\n',
                encoding="utf-8",
            )
            node = root / ("node.exe" if os.name == "nt" else "node")
            node.write_text("# globally configured node\n", encoding="utf-8")
            _make_node_executable(node)
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform codex"
            )
            env = {
                "HOME": temp_home,
                "USERPROFILE": temp_home,
                "GHOST_ALICE_NODE": str(node),
                "PATH": "",
            }

            with mock.patch.dict(os.environ, env, clear=True):
                try:
                    argv = hook_profile_gate._validate_shell_command(command)
                except (AttributeError, hook_profile_gate.HookCommandRejected) as exc:
                    self.fail(f"malformed unrelated Codex config blocked global Node: {exc}")

        self.assertEqual(Path(argv[0]).resolve(), node.resolve())

    def test_invalid_utf8_runtime_config_rejects_cleanly_and_is_audited(self):
        with tempfile.TemporaryDirectory() as temp_home:
            root = Path(temp_home)
            config_path = root / ".ghost-alice" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_bytes(b"\xff\xfe\x80")
            node = root / ("node.exe" if os.name == "nt" else "node")
            node.write_text("# only corrupt registration could authorize this\n", encoding="utf-8")
            command = (
                f'"{node.as_posix()}" '
                f'"{Path(__file__).with_name("ghost-alice-hook.mjs").as_posix()}" '
                "--platform codex"
            )
            payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
            session_id = f"s-invalid-utf8-runtime-config-{root.name}"
            real_log_path = _strict_log_path(Path.home(), "codex", session_id)
            self.assertFalse(real_log_path.exists())
            env = os.environ.copy()
            env.update({
                "HOME": temp_home,
                "USERPROFILE": temp_home,
                "GHOST_ALICE_PLATFORM": "claude",
                "GHOST_ALICE_SESSION_ID": session_id,
                "PATH": "",
            })
            env.pop("GHOST_ALICE_NODE", None)
            env.pop("NODE_REPL_NODE_PATH", None)
            env.pop("CODEX_HOME", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("hook_profile_gate.py")),
                    "run",
                    "tool-checkpoint",
                    payload,
                ],
                input=json.dumps({"session_id": session_id}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 126, msg=result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertTrue(result.stderr.startswith("hook command rejected:"))
            log_path = _strict_log_path(root, "codex", session_id)
            self.assertTrue(log_path.is_file(), "invalid UTF-8 rejection was not audited")
            self.assertFalse(real_log_path.exists())
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["exit_code"], 126)
            self.assertEqual(row["visible_decision"], "force_show")

    def test_malformed_payload_rejection_is_sanitized_in_strict_log(self):
        stdin_text = json.dumps(
            {
                "session_id": "s-malformed-rejection",
                "hook_event_name": "PreToolUse",
                "tool_input": {"secret": "must-not-be-stored"},
            }
        )

        with tempfile.TemporaryDirectory() as temp_home:
            env = {
                "HOME": temp_home,
                "GHOST_ALICE_PLATFORM": "codex",
                "GHOST_ALICE_SESSION_ID": "outer-session",
            }
            stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(sys, "stdin", io.StringIO(stdin_text)),
                mock.patch.object(sys, "stderr", stderr),
                self.assertRaises(SystemExit) as cm,
            ):
                hook_profile_gate.main(["run", "tool-checkpoint", "not-base64!"])

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "codex"
                / "s-malformed-rejection"
                / "strict-hook-output.jsonl"
            )
            self.assertTrue(log_path.is_file(), "rejection audit log was not created")
            row_text = log_path.read_text(encoding="utf-8").splitlines()[0]
            row = json.loads(row_text)

        self.assertEqual(cm.exception.code, 126)
        self.assertEqual(
            stderr.getvalue(),
            "hook command rejected: hook payload decode failed\n",
        )
        self.assertEqual(row["hook_id"], "tool-checkpoint")
        self.assertEqual(row["event"], "PreToolUse")
        self.assertEqual(row["exit_code"], 126)
        self.assertEqual(row["stdout"], "")
        self.assertEqual(row["visible_decision"], "force_show")
        self.assertEqual(row["surface_item"]["value_key"], "hook-command-rejection")
        self.assertNotIn("stdin", row)
        self.assertNotIn("must-not-be-stored", row_text)
        raw_stdin_digest = "sha256:" + hashlib.sha256(stdin_text.encode("utf-8")).hexdigest()
        self.assertNotEqual(row["payload_digest"], raw_stdin_digest)

    def test_command_rejection_is_sanitized_in_strict_log(self):
        command = "/bin/bash -lc 'printf ok'; /tmp/evil"
        payload = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
        stdin_text = json.dumps(
            {
                "session_id": "s-command-rejection",
                "hook_event_name": "PreToolUse",
                "tool_input": {"secret": "must-not-be-stored"},
            }
        )

        with tempfile.TemporaryDirectory() as temp_home:
            env = {
                "HOME": temp_home,
                "GHOST_ALICE_PLATFORM": "claude",
            }
            stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(sys, "stdin", io.StringIO(stdin_text)),
                mock.patch.object(sys, "stderr", stderr),
                self.assertRaises(SystemExit) as cm,
            ):
                hook_profile_gate.main(["run", "tool-checkpoint", payload])

            log_path = (
                Path(temp_home)
                / ".ghost-alice"
                / "session-logs"
                / "unknown"
                / "s-command-rejection"
                / "strict-hook-output.jsonl"
            )
            self.assertTrue(log_path.is_file(), "rejection audit log was not created")
            row_text = log_path.read_text(encoding="utf-8").splitlines()[0]
            row = json.loads(row_text)

        self.assertEqual(cm.exception.code, 126)
        self.assertTrue(
            stderr.getvalue().startswith("hook command rejected: shell control operator rejected\n")
        )
        self.assertEqual(row["exit_code"], 126)
        self.assertEqual(row["stdout"], "")
        self.assertEqual(row["stderr"], stderr.getvalue())
        self.assertEqual(row["visible_decision"], "force_show")
        self.assertEqual(row["surface_item"]["value_key"], "hook-command-rejection")
        self.assertNotIn("stdin", row)
        self.assertNotIn("must-not-be-stored", row_text)

    def test_consumed_adapter_payload_rejection_audits_original_session_once(self):
        import addon_installer
        import install_destination
        for mismatch in ("hash", "event", "source"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as temp_home:
                root = Path(temp_home) / "isolated"
                root.mkdir()
                script = root / "adapters" / "demo.py"
                script.parent.mkdir()
                script.write_text("# original adapter bytes\n", encoding="utf-8")
                actual_hash = hashlib.sha256(script.read_bytes()).hexdigest()
                destination = install_destination.InstallDestination(
                    mode="isolated", owned_root=str(root), platform_dir=str(root / ".codex"),
                    skills_dir=str(root / "skills"), ghost_root=str(root / ".ghost-alice"),
                    ledger_root=str(root / "intent"), project_root=str(root),
                    core_source_root=str(Path(__file__).resolve().parents[1]))
                binding = destination.binding()
                runner = addon_installer.privileged_adapter_runner_id("demo", "continue")
                binding["adapter"] = dict(adapter_id="demo", runner_id=runner,
                    script=str(script), event="Stop", content_hash="0" * 64 if mismatch == "hash" else actual_hash)
                spec = dict(args_policy="no_args", hook_id_by_event={"on_agent_stop": "continue"},
                    script_rel_by_event={"on_agent_stop": "adapters/other.py" if mismatch == "source" else "adapters/demo.py"})
                native_event = "PreToolUse" if mismatch == "event" else "Stop"
                stdin_text = json.dumps(dict(session_id="actual-native-session", hook_event_name=native_event,
                    tool_input=dict(secret="must-not-be-stored")))
                command = shlex.join([sys.executable, str(script)])
                payload = base64.urlsafe_b64encode(command.encode()).decode()
                stdout, stderr = io.StringIO(), io.StringIO()
                with (mock.patch.dict(os.environ, {"HOME": temp_home}, clear=True),
                      mock.patch.object(sys, "stdin", io.StringIO(stdin_text)),
                      mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr),
                      mock.patch.object(hook_profile_gate, "_read_stdin", wraps=hook_profile_gate._read_stdin) as read_stdin,
                      mock.patch.object(hook_profile_gate, "_validate_shell_command", return_value=[sys.executable, str(script)]),
                      mock.patch.object(hook_definitions, "load_privileged_adapter_allowlist", return_value={"demo": spec}),
                      mock.patch.object(hook_profile_gate.subprocess, "run") as child,
                      self.assertRaises(SystemExit) as rejected):
                    hook_profile_gate.main(["run", runner, "codex", payload,
                        "--binding-b64", install_destination.encode_binding(binding)])
                self.assertEqual(rejected.exception.code, 126)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn(f"registered adapter {mismatch} mismatch", stderr.getvalue())
                self.assertIn("Adapter was not executed", stderr.getvalue())
                if mismatch == "hash":
                    self.assertIn("Check the installed adapter and current registration", stderr.getvalue())
                    self.assertIn("refresh the host's loaded hook commands", stderr.getvalue())
                    self.assertIn("Do not bypass hash validation", stderr.getvalue())
                    self.assertIn("expected=" + "0" * 64, stderr.getvalue())
                    self.assertIn("actual=" + actual_hash, stderr.getvalue())
                else:
                    self.assertIn("Repair the registered adapter event/source", stderr.getvalue())
                child.assert_not_called()
                expected = _strict_log_path(root, "codex", "actual-native-session")
                self.assertTrue(expected.is_file(), "consumed native session was lost from rejection audit")
                self.assertFalse((Path(temp_home) / ".ghost-alice").exists(),
                                 "bound rejection wrote to the ambient home")
                self.assertEqual(read_stdin.call_count, 1)
                logs = list((root / ".ghost-alice/session-logs").rglob("strict-hook-output.jsonl"))
                self.assertEqual(logs, [expected])
                rows = expected.read_text().splitlines()
                self.assertEqual(len(rows), 1)
                row = json.loads(rows[0])
                self.assertEqual(row["event"], native_event)
                self.assertEqual(row["exit_code"], 126)
                self.assertNotIn("must-not-be-stored", rows[0])
                details = row["surface_item"]["rejection_diagnostic"]
                self.assertEqual(details["mismatch"], mismatch)
                if mismatch == "hash":
                    self.assertEqual(details["expected_hash"], "0" * 64)
                    self.assertEqual(details["actual_hash"], actual_hash)

    def test_rejection_audit_failure_keeps_original_error_and_exit_code(self):
        stdin_text = json.dumps(
            {
                "session_id": "s-audit-failure",
                "hook_event_name": "PreToolUse",
                "tool_input": {"secret": "must-not-be-stored"},
            }
        )
        stderr = io.StringIO()

        with (
            mock.patch.dict(
                os.environ,
                {"HOME": tempfile.gettempdir(), "GHOST_ALICE_PLATFORM": "codex"},
                clear=True,
            ),
            mock.patch.object(sys, "stdin", io.StringIO(stdin_text)),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.object(
                hook_profile_gate.strict_session_log,
                "append_event",
                side_effect=OSError("disk full"),
            ) as append_event,
            self.assertRaises(SystemExit) as cm,
        ):
            hook_profile_gate.main(["run", "tool-checkpoint", "not-base64!"])

        self.assertEqual(cm.exception.code, 126)
        self.assertEqual(
            stderr.getvalue().splitlines(),
            [
                "hook command rejected: hook payload decode failed",
                "hook rejection audit failed: disk full",
            ],
        )
        event = append_event.call_args.kwargs["event"]
        self.assertNotIn("stdin", event)
        self.assertNotIn("must-not-be-stored", json.dumps(event))

    def test_cli_accepts_current_runner_shape_without_visibility_csv(self):
        with tempfile.TemporaryDirectory() as owned, mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT":str(Path(owned)/"ghost")}):
            executable = sys.executable.replace("\\", "/")
            payload = base64.urlsafe_b64encode(f"{executable} -c 'import sys; sys.exit(0)'".encode("utf-8")).decode("ascii")

            with self.assertRaises(SystemExit) as cm:
                hook_profile_gate.main(["run", "prompt", payload])

            self.assertEqual(cm.exception.code, 0)


    def test_cli_keeps_legacy_runner_shape_for_installed_wrappers(self):
        with tempfile.TemporaryDirectory() as owned, mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT":str(Path(owned)/"ghost")}):
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

    def test_empty_noop_object_remains_parseable_on_reduced_surfaces(self):
        message = "{}\n"

        for surface in ("hidden", "compact", "focused"):
            with self.subTest(surface=surface):
                self.assertEqual(
                    hook_profile_gate._render_user_surface(
                        {"user_surface": surface, "value_key": "tool-checkpoint"},
                        message,
                        "debug-only diagnostic\n",
                    ),
                    (message, ""),
                )

    def test_explicit_continue_noop_remains_parseable_on_reduced_surfaces(self):
        message = '{"continue": true, "systemMessage": ""}\n'

        for surface in ("hidden", "compact", "focused"):
            with self.subTest(surface=surface):
                self.assertEqual(
                    hook_profile_gate._render_user_surface(
                        {"user_surface": surface, "value_key": "adapter-autopilot-mode-continue"},
                        message,
                        "debug-only diagnostic\n",
                    ),
                    (message, ""),
                )

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

    def test_runner_preserves_protocol_block_json_under_minimal_visibility(self):
        message = '{"decision":"block","reason":"completion-check required"}'
        code = f"print({message!r})"
        payload = base64.urlsafe_b64encode(_python_payload_command(f"-c {shlex.quote(code)}").encode("utf-8")).decode("ascii")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "claude"
            env["GHOST_ALICE_SESSION_ID"] = "s-protocol-block"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"

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
                / "claude"
                / "s-protocol-block"
                / "strict-hook-output.jsonl"
            )
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, message + "\n")
        self.assertEqual(row["stdout"].strip(), message)
        self.assertEqual(row["visible_decision"], "force_show")

    def test_runner_preserves_protocol_noop_json_under_minimal_visibility(self):
        message = "{}"
        code = f"print({message!r})"
        payload = base64.urlsafe_b64encode(
            _python_payload_command(f"-c {shlex.quote(code)}").encode("utf-8")
        ).decode("ascii")

        with tempfile.TemporaryDirectory() as temp_home:
            env = os.environ.copy()
            env["HOME"] = temp_home
            env["GHOST_ALICE_PLATFORM"] = "codex"
            env["GHOST_ALICE_SESSION_ID"] = "s-protocol-noop"
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("hook_profile_gate.py")),
                    "run",
                    "tool-checkpoint",
                    payload,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, message + "\n")
        self.assertEqual(result.stderr, "")

    def test_runner_platform_argument_reaches_child_environment(self):
        with tempfile.TemporaryDirectory() as owned, mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT":str(Path(owned)/"ghost")}):
            code = (
                "import json, os; "
                "print(json.dumps({'continue': True, 'systemMessage': "
                "os.environ.get('GHOST_ALICE_PLATFORM', '')}))"
            )
            payload = base64.urlsafe_b64encode(
                _python_payload_command(f"-c {shlex.quote(code)}").encode("utf-8")
            ).decode("ascii")
            env = os.environ.copy()
            env.pop("GHOST_ALICE_PLATFORM", None)
            env["GHOST_ALICE_AGENT_VISIBILITY"] = "strict"

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("hook_profile_gate.py")),
                    "run",
                    "completion",
                    "codex",
                    payload,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["systemMessage"], "codex")


    def test_runner_does_not_attach_pending_manifest_to_unrelated_routine_output(self):
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
        self.assertEqual(result.stdout.strip(), "")
        self.assertEqual(row["visible_decision"], "hide")
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

    def test_core_hook_exit_2_does_not_block_the_host(self):
        # Core informs and stops; only a registered Autopilot adapter may block. A usage error or a
        # removed script behind an old registration must not become a host block (exit 2).
        gone = Path(__file__).with_name("retired_hook_that_is_gone.py").as_posix()
        for hook_id, args in (("session-intent", "-c 'import sys; sys.exit(2)'"), ("prompt", f"'{gone}'")):
            with self.subTest(hook_id=hook_id), tempfile.TemporaryDirectory() as temp_home:
                payload = base64.urlsafe_b64encode(_python_payload_command(args).encode("utf-8")).decode("ascii")
                env = os.environ.copy()
                env.update(HOME=temp_home, GHOST_ALICE_PLATFORM="claude", GHOST_ALICE_SESSION_ID="s-exit2",
                           GHOST_ALICE_AGENT_VISIBILITY="dynamic")
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run", hook_id, payload],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, check=False)
                log_path = Path(temp_home) / ".ghost-alice/session-logs/claude/s-exit2/strict-hook-output.jsonl"
                row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(row["exit_code"], 2)

    def test_runner_usage_error_does_not_block_the_host(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")), "run"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("usage:", result.stderr)

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
        cases = (
            (install_hooks._platform_session_receipt_entry("claude", "SessionStart"),
             "session-receipt", install_hooks.SESSION_RECEIPT_MARKER),
            (install_hooks._platform_session_start_entry("claude", "SessionStart"),
             "session-start", install_hooks.SESSION_START_MARKER),
        )
        for entry, hook_id, marker in cases:
            with self.subTest(hook_id=hook_id):
                command = install_hooks._entry_command(entry)
                self.assertIn("hook_profile_gate.py", command)
                self.assertIn(f"[hook-runner:{hook_id}]", command)
                self.assertNotIn('"strict,dynamic,minimal"', command)
                self.assertIn(marker, command)

    def test_installed_minimal_visibility_runs_session_start(self):
        if os.name == "nt":
            self.skipTest("POSIX shell launcher test does not apply on Windows")

        env = os.environ.copy()
        env["GHOST_ALICE_AGENT_VISIBILITY"] = "minimal"
        # An isolated home keeps the clean-pass assertion independent of the real machine's pending merges.
        isolated_home = tempfile.TemporaryDirectory()
        self.addCleanup(isolated_home.cleanup)
        env["HOME"] = isolated_home.name
        with mock.patch.dict(os.environ,{"HOME":isolated_home.name}):
            session_entry = install_hooks._platform_session_start_entry("claude", "SessionStart")

        def run(entry):
            return subprocess.run(
                ["/bin/bash", "-lc", install_hooks._entry_command(entry)],
                input=json.dumps({"prompt": "Explain the router."}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

        session_start = run(session_entry)

        # A clean merge check stays off the minimal screen.
        self.assertEqual(session_start.returncode, 0)
        session_payload = json.loads(session_start.stdout)
        self.assertTrue(session_payload["continue"])
        self.assertNotIn("systemMessage", session_payload)


class TestHookProtocolSurface(unittest.TestCase):
    def test_pretool_recording_metadata_stays_in_raw_audit_not_native_envelope(self):
        for platform in ('codex', 'claude'):
            for denied in (False, True):
                with self.subTest(platform=platform, denied=denied), tempfile.TemporaryDirectory() as home:
                    protocol = {'continue': not denied, 'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse', 'permissionDecision': 'deny' if denied else 'allow',
                        'permissionDecisionReason': 'Current gate is blocked.' if denied else 'Current gate is open.',
                        'additionalContext': 'Preserve the original supported evidence.',
                    }, 'completion_recording': {'status': 'recording-unavailable' if denied else 'prepared',
                        'receipt_token': 'audit-only-original-receipt'}}
                    if denied:
                        protocol.update(systemMessage='Recording failed; the current gate remains blocked.',
                                        stopReason='Current gate is blocked.')
                    raw = json.dumps(protocol)
                    exit_code = 2 if denied else 0
                    code = f'import sys; print({raw!r}); sys.exit({exit_code})'
                    command = _python_payload_command(f'-c {shlex.quote(code)}')
                    payload = base64.urlsafe_b64encode(command.encode()).decode()
                    env = {**os.environ, 'HOME': home, 'CODEX_THREAD_ID': '',
                        'GHOST_ALICE_PLATFORM': platform, 'GHOST_ALICE_AGENT_VISIBILITY': 'minimal'}
                    result = subprocess.run(
                        [sys.executable, str(Path(__file__).with_name('hook_profile_gate.py')),
                         'run', 'tool-checkpoint', payload],
                        input=json.dumps({'session_id': 'pretool-audit', 'hook_event_name': 'PreToolUse'}),
                        text=True, capture_output=True, env=env, check=False)
                    # Only a registered Autopilot adapter may block; a Core hook's exit 2 reaches the host as 1.
                    self.assertEqual(result.returncode, 1 if denied else 0, result.stderr)
                    emitted = json.loads(result.stdout)
                    self.assertNotIn('completion_recording', emitted)
                    self.assertEqual(emitted['continue'], protocol['continue'])
                    self.assertEqual(emitted['hookSpecificOutput'], protocol['hookSpecificOutput'])
                    if denied:
                        self.assertEqual(emitted['systemMessage'], protocol['systemMessage'])
                        self.assertEqual(emitted['stopReason'], protocol['stopReason'])
                    row = json.loads(_strict_log_path(home, platform, 'pretool-audit').read_text().splitlines()[0])
                    self.assertEqual(json.loads(row['stdout']), protocol)
                    self.assertEqual(row['exit_code'], exit_code)

    def test_visibility_preserves_model_context_and_control_fields(self):
        protocols = [
            {"continue": True, "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "[session-intent-receipt] session-a [/session-intent-receipt]",
            }},
            {"decision": "block", "reason": "Continue the approved work."},
            {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "The current gate is blocked.",
            }},
            {"continue": False, "stopReason": "Stop requested."},
        ]
        for protocol in protocols:
            for surface in ("hidden", "compact", "focused", "full", "forced"):
                with self.subTest(protocol=protocol, surface=surface):
                    original = {**protocol, "systemMessage": "Routine user notice."}
                    output, _ = hook_profile_gate._render_user_surface(
                        {"user_surface": surface, "value_key": "test-hook"},
                        json.dumps(original) + "\n", "",
                    )
                    decoded = json.loads(output)
                    decoded.pop("systemMessage", None)
                    self.assertEqual(decoded, protocol)

    def test_visibility_reduces_only_protocol_user_warning(self):
        for surface, expected in (
            ("hidden", None),
            ("compact", "test-hook observed"),
            ("focused", "test-hook: First line. Second line."),
        ):
            with self.subTest(surface=surface):
                output, error = hook_profile_gate._render_user_surface(
                    {"user_surface": surface, "value_key": "test-hook"},
                    json.dumps({"continue": True, "systemMessage": "First line.\nSecond line."}),
                    "debug-only diagnostic\n",
                )
                decoded = json.loads(output)
                self.assertIs(decoded["continue"], True)
                self.assertEqual(decoded.get("systemMessage"), expected)
                self.assertEqual(error, "")

    def test_runner_preserves_additional_context_across_visibility_profiles(self):
        hook_output = {
            "continue": True,
            "systemMessage": "routine clean pass already persisted",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "Use the observed session-a ledger receipt.",
            },
        }
        raw = json.dumps(hook_output)
        command = _python_payload_command(f"-c {shlex.quote(f'print({raw!r})')}")
        payload = base64.urlsafe_b64encode(command.encode()).decode()
        for profile in ("strict", "dynamic", "minimal"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as home:
                env = {**os.environ, "HOME": home, "GHOST_ALICE_PLATFORM": "codex",
                       "GHOST_ALICE_AGENT_VISIBILITY": profile}
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("hook_profile_gate.py")),
                     "run", "prompt", payload],
                    input=json.dumps({"session_id": "session-a", "hook_event_name": "UserPromptSubmit"}),
                    text=True, capture_output=True, env=env, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"], hook_output["hookSpecificOutput"])
                row = json.loads(_strict_log_path(home, "codex", "session-a").read_text().splitlines()[0])
                self.assertEqual(json.loads(row["stdout"]), hook_output)


if __name__ == "__main__":
    unittest.main()
