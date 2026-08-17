#!/usr/bin/env python3
"""Contract tests for one-shot Claude and Codex subject sessions."""

from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = Path(__file__).with_name("fresh_agent_session.py")
FORBIDDEN_SESSION_FLAGS = {"--resume", "--continue", "--session-id"}


def load_fresh_agent_session():
    if not MODULE_PATH.is_file():
        return None
    return importlib.import_module("_shared.fresh_agent_session")


def claude_stream(final_text: str) -> str:
    events = [
        {
            "type": "system",
            "subtype": "hook_response",
            "hook_event": "UserPromptSubmit",
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {"type": "text", "text": final_text},
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": final_text,
        },
    ]
    return "\n".join(json.dumps(event) for event in events) + "\n"


class FreshAgentSessionPresenceTest(unittest.TestCase):
    def test_fresh_agent_session_module_exists(self):
        self.assertIsNotNone(load_fresh_agent_session())


@unittest.skipUnless(MODULE_PATH.is_file(), "fresh_agent_session.py is not implemented")
class FreshAgentSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = load_fresh_agent_session()

    def setUp(self):
        self.fixture = (
            REPO_ROOT
            / ".tmp"
            / "test-fresh-agent-session"
            / uuid.uuid4().hex
        )
        self.fixture.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.fixture, ignore_errors=True)

    def test_claude_uses_stdin_nonpersistent_stream_json_and_clean_runtime(self):
        prompt = "Explain why the first premise changes the implementation scope."
        final_text = "The premise changes the terminal objective."
        calls = []

        def run_process(command, **kwargs):
            calls.append((command, kwargs, tuple(Path(kwargs["cwd"]).iterdir())))
            return subprocess.CompletedProcess(
                command,
                0,
                claude_stream(final_text),
                "",
            )

        hostile = r"C:\Users\Public\Documents\ESTsoft\CreatorTemp"
        environment = {
            "HOME": str(self.fixture / "auth-home"),
            "USERPROFILE": str(self.fixture / "auth-profile"),
            "CLAUDE_CONFIG_DIR": str(self.fixture / "claude-config"),
            "CODEX_HOME": str(self.fixture / "codex-home"),
            "TEMP": hostile,
            "TMP": hostile,
            "TMPDIR": hostile,
            "PYTHONPYCACHEPREFIX": hostile,
            "RUBRIC": "private rubric",
            "PURPOSE": "private purpose",
            "EXPECTED_ANSWER": "private expected answer",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            parent_before = dict(environment)
            response = self.session.run_fresh_agent_session(
                platform="claude",
                prompt=prompt,
                cli_command=["claude-test"],
                timeout_seconds=5,
                run_process=run_process,
            )
            self.assertEqual(
                {key: os.environ[key] for key in environment},
                parent_before,
            )

        self.assertEqual(response, final_text)
        self.assertEqual(len(calls), 1)
        command, kwargs, initial_cwd_entries = calls[0]
        self.assertEqual(command[0], "claude-test")
        self.assertIn("-p", command)
        self.assertIn("--no-session-persistence", command)
        self.assertIn("stream-json", command)
        self.assertIn("--include-hook-events", command)
        self.assertFalse(FORBIDDEN_SESSION_FLAGS.intersection(command))
        self.assertNotIn(prompt, command)
        self.assertEqual(kwargs["input"], prompt)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(initial_cwd_entries, ())
        self.assertEqual(kwargs["env"]["HOME"], environment["HOME"])
        self.assertEqual(kwargs["env"]["USERPROFILE"], environment["USERPROFILE"])
        self.assertEqual(
            kwargs["env"]["CLAUDE_CONFIG_DIR"],
            environment["CLAUDE_CONFIG_DIR"],
        )
        self.assertEqual(kwargs["env"]["CODEX_HOME"], environment["CODEX_HOME"])
        for key in ("RUBRIC", "PURPOSE", "EXPECTED_ANSWER"):
            self.assertNotIn(key, kwargs["env"])
        run_root = Path(kwargs["env"]["TMPDIR"]).parent
        self.assertTrue(run_root.is_relative_to(REPO_ROOT / ".tmp"))
        self.assertTrue(Path(kwargs["cwd"]).is_relative_to(run_root))
        self.assertFalse(run_root.exists())

    def test_codex_uses_ephemeral_stdin_and_removes_last_message_on_success(self):
        prompt = "Assess this behavior without knowing the evaluator rubric."
        final_text = "Keep the conclusion conditional on observed behavior."
        calls = []

        def run_process(command, **kwargs):
            calls.append((command, kwargs, tuple(Path(kwargs["cwd"]).iterdir())))
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text(final_text, encoding="utf-8")
            return subprocess.CompletedProcess(
                command,
                0,
                "hook: UserPromptSubmit\nnon-answer runtime log\n",
                "",
            )

        response = self.session.run_fresh_agent_session(
            platform="codex",
            prompt=prompt,
            cli_command=["codex-test"],
            timeout_seconds=5,
            run_process=run_process,
        )

        self.assertEqual(response, final_text)
        command, kwargs, initial_cwd_entries = calls[0]
        self.assertEqual(command[:2], ["codex-test", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertIn("--output-last-message", command)
        self.assertEqual(command[-1], "-")
        self.assertFalse(FORBIDDEN_SESSION_FLAGS.intersection(command))
        self.assertNotIn(prompt, command)
        self.assertEqual(kwargs["input"], prompt)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(initial_cwd_entries, ())
        run_root = Path(kwargs["env"]["TMPDIR"]).parent
        last_message = Path(command[command.index("--output-last-message") + 1])
        self.assertTrue(last_message.is_relative_to(run_root))
        self.assertFalse(run_root.exists())
        self.assertFalse(last_message.exists())

    def test_subject_environment_cannot_discover_the_parent_repository(self):
        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                discoveries = []

                def run_process(command, **kwargs):
                    discoveries.append(
                        subprocess.run(
                            ["git", "rev-parse", "--show-toplevel"],
                            cwd=kwargs["cwd"],
                            env=kwargs["env"],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                    )
                    if platform == "codex":
                        output = Path(command[command.index("--output-last-message") + 1])
                        output.write_text("General explanation.", encoding="utf-8")
                        stdout = ""
                    else:
                        stdout = claude_stream("General explanation.")
                    return subprocess.CompletedProcess(command, 0, stdout, "")

                self.session.run_fresh_agent_session(
                    platform=platform,
                    prompt="Explain the general mechanism.",
                    cli_command=[f"{platform}-test"],
                    timeout_seconds=5,
                    run_process=run_process,
                )

                self.assertEqual(len(discoveries), 1)
                self.assertNotEqual(discoveries[0].returncode, 0)
                self.assertNotIn(str(REPO_ROOT), discoveries[0].stdout)

    def test_claude_and_codex_parsers_return_the_same_final_text(self):
        expected = "line one\nline two"
        codex_output = self.fixture / "last-message.txt"
        codex_output.write_text(expected, encoding="utf-8")

        self.assertEqual(
            self.session.parse_claude_response(claude_stream(expected)),
            expected,
        )
        self.assertEqual(self.session.read_codex_response(codex_output), expected)

    def test_nonzero_and_timeout_preserve_diagnostic_root_without_prompt_file(self):
        prompt = "This authentic prompt must not be written to a file."
        scenarios = (
            (
                "nonzero",
                lambda command, **kwargs: subprocess.CompletedProcess(
                    command,
                    9,
                    "",
                    "private failure detail",
                ),
                self.session.FreshAgentSessionError,
            ),
            (
                "timeout",
                lambda command, **kwargs: (_ for _ in ()).throw(
                    subprocess.TimeoutExpired(command, kwargs["timeout"])
                ),
                subprocess.TimeoutExpired,
            ),
        )

        for name, run_process, expected_error in scenarios:
            with self.subTest(name=name):
                calls = []
                reports = []

                def capture(command, **kwargs):
                    calls.append((command, kwargs))
                    return run_process(command, **kwargs)

                with self.assertRaises(expected_error) as raised:
                    self.session.run_fresh_agent_session(
                        platform="claude",
                        prompt=prompt,
                        cli_command=["claude-test"],
                        repo_root=self.fixture,
                        timeout_seconds=1,
                        run_process=capture,
                        reporter=reports.append,
                    )

                run_root = Path(calls[0][1]["env"]["TMPDIR"]).parent
                self.assertTrue(run_root.is_dir())
                self.assertEqual(len(reports), 1)
                self.assertIn(str(run_root), reports[0])
                self.assertNotIn(prompt, reports[0])
                self.assertNotIn(prompt, str(raised.exception))
                self.assertEqual(
                    [path for path in run_root.rglob("*") if path.is_file()],
                    [],
                )
                shutil.rmtree(run_root)

    def test_cli_surface_accepts_no_prompt_or_evaluator_private_inputs(self):
        destinations = {
            action.dest
            for action in self.session.build_parser()._actions
        }
        for forbidden in (
            "prompt",
            "rubric",
            "purpose",
            "expected_answer",
            "resume",
            "continue",
            "session_id",
        ):
            self.assertNotIn(forbidden, destinations)

    def test_command_builders_reject_every_persistent_session_flag_form(self):
        long_forms = (
            "--resume",
            "--resume=prior",
            "--continue",
            "--session-id",
            "--session-id=prior",
        )
        for flag in long_forms:
            with self.subTest(flag=flag):
                with self.assertRaises(ValueError):
                    self.session.build_claude_command(["claude", flag])
                with self.assertRaises(ValueError):
                    self.session.build_codex_command(
                        ["codex", flag],
                        self.fixture / "last-message.txt",
                    )
        for flag in ("-r", "-c"):
            with self.subTest(claude_short_flag=flag):
                with self.assertRaises(ValueError):
                    self.session.build_claude_command(["claude", flag])

    def test_codex_short_config_flag_is_not_misclassified_as_continue(self):
        try:
            command = self.session.build_codex_command(
                ["codex", "-c", "model_reasoning_effort=high"],
                self.fixture / "last-message.txt",
            )
        except ValueError as exc:
            self.fail(f"Codex -c was misclassified as a session flag: {exc}")

        self.assertIn("-c", command)
        self.assertIn("model_reasoning_effort=high", command)

    def test_main_writes_only_final_assistant_response_to_stdout(self):
        prompt = "Use the currently observable behavior as evidence."
        final_text = "Current behavior is the relevant evidence."

        def run_process(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                claude_stream(final_text),
                "diagnostic that must not reach stdout",
            )

        stdout = io.StringIO()
        stderr = io.StringIO()
        returncode = self.session.main(
            [
                "--platform",
                "claude",
                "--claude-bin",
                "claude-test",
                "--timeout-seconds",
                "5",
            ],
            stdin=io.StringIO(prompt),
            stdout=stdout,
            stderr=stderr,
            run_process=run_process,
        )

        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.getvalue(), final_text + "\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_main_writes_unicode_response_through_cp949_stdout(self):
        prompt = "Keep the response concise."
        final_text = "Direct answer — 한국어 응답"

        def run_process(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                claude_stream(final_text),
                "",
            )

        raw_stdout = io.BytesIO()
        stdout = io.TextIOWrapper(
            raw_stdout,
            encoding="cp949",
            errors="strict",
        )
        stderr = io.StringIO()
        try:
            try:
                returncode = self.session.main(
                    [
                        "--platform",
                        "claude",
                        "--claude-bin",
                        "claude-test",
                        "--timeout-seconds",
                        "5",
                    ],
                    stdin=io.StringIO(prompt),
                    stdout=stdout,
                    stderr=stderr,
                    run_process=run_process,
                )
            except UnicodeEncodeError as exc:
                self.fail(f"main could not emit the Unicode response: {exc}")
            stdout.flush()
            rendered = raw_stdout.getvalue().decode("utf-8")
        finally:
            stdout.detach()

        self.assertEqual(returncode, 0)
        self.assertEqual(rendered, final_text + os.linesep)
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
