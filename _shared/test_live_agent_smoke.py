#!/usr/bin/env python3
"""Tests for live agent smoke result classification."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_agent_smoke


class LiveAgentSmokeClassificationTest(unittest.TestCase):
    def test_windows_resolver_prefers_cmd_for_bare_codex(self):
        def fake_which(name):
            return {
                "codex.cmd": r"C:\Users\try2q\AppData\Roaming\npm\codex.cmd",
                "codex": r"C:\Users\try2q\AppData\Roaming\npm\codex.CMD",
                "codex.exe": r"C:\Users\try2q\AppData\Local\Microsoft\WinGet\codex.exe",
            }.get(name)

        command = live_agent_smoke.resolve_codex_command(
            "codex",
            which=fake_which,
            platform="nt",
        )

        self.assertEqual(
            command,
            [r"C:\Users\try2q\AppData\Roaming\npm\codex.cmd"],
        )

    def test_timeout_and_router_error_are_failures(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=124,
            timed_out=True,
            log_text=(
                "2026-06-30T09:24:06Z ERROR codex_core::tools::router: "
                "error=Exit code: 1\n"
                "error: Failed to initialize cache at "
                "`C:\\Users\\try2q\\AppData\\Local\\uv\\cache`\n"
            ),
            output_text="",
            output_exists=False,
        )

        self.assertEqual(result.status, "fail")
        self.assertIn("timeout", result.reasons)
        self.assertIn("tool-router-error", result.reasons)
        self.assertIn("missing-output", result.reasons)

    def test_cli_unknown_flag_is_invalid_harness_not_runtime_failure(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=2,
            timed_out=False,
            log_text=(
                "error: unexpected argument '--dangerously-bypass-hook-trust' found\n"
                "tip: a similar argument exists: '--dangerously-bypass-approvals-and-sandbox'\n"
            ),
            output_text="",
            output_exists=False,
        )

        self.assertEqual(result.status, "invalid-harness")
        self.assertEqual(result.reasons, ["cli-arg-rejected"])

    def test_runtime_error_text_in_agent_output_does_not_false_fail(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text="hook: UserPromptSubmit Completed\nhook: Stop Completed\n",
            output_text=(
                "The previous log contained ERROR codex_core::tools::router, "
                "but this answer is quoting it as evidence.\n"
                "[gate-state]\n- task-router: done\n[io-trace]\n- files-read: [agent.log]\n"
            ),
            output_exists=True,
            required_markers=("[gate-state]", "[io-trace]"),
        )

        self.assertEqual(result.status, "pass")

    def test_runtime_error_text_in_codex_stdout_answer_does_not_false_fail(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text=(
                "2026-06-30T11:50:39Z WARN codex_core::shell_snapshot: harmless warning\n"
                "hook: UserPromptSubmit Completed\n"
                "codex\n"
                "| log contains `ERROR codex_core::tools::router`, hook failure, "
                "traceback, panic, or cache initialization error | runtime smoke failure |\n"
                "tokens used\n"
                "123\n"
            ),
            output_text=(
                "[gate-state]\n- task-router: done\n"
                "[completion-check]\n- verification-before-completion: done\n"
                "[io-trace]\n- files-read: [policy.md]\n"
            ),
            output_exists=True,
            required_markers=("[gate-state]", "[completion-check]", "[io-trace]"),
        )

        self.assertEqual(result.status, "pass")

    def test_git_warning_in_codex_stdout_log_does_not_make_invalid_harness(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text=(
                "hook: UserPromptSubmit Completed\n"
                "warning: unable to access 'C:/Users/try2q/.config/git/ignore': Permission denied\n"
                "hook: Stop Completed\n"
            ),
            output_text=(
                "[gate-state]\n- task-router: done\n"
                "[completion-check]\n- verification-before-completion: done\n"
                "[io-trace]\n- files-read: [README.md]\n"
            ),
            output_exists=True,
            required_markers=("[gate-state]", "[completion-check]", "[io-trace]"),
        )

        self.assertEqual(result.status, "pass")

    def test_hook_trust_flag_is_omitted_when_help_lacks_support(self):
        command = live_agent_smoke.build_codex_exec_command(
            codex_command=["codex.cmd"],
            repo_root=Path(r"C:\repo"),
            output_file=Path(r"C:\tmp\last.txt"),
            prompt="hello",
            hook_trust_supported=False,
        )

        self.assertNotIn("--dangerously-bypass-hook-trust", command)
        self.assertIn("--sandbox", command)

    def test_hook_trust_flag_is_included_when_supported(self):
        command = live_agent_smoke.build_codex_exec_command(
            codex_command=["codex.cmd"],
            repo_root=Path(r"C:\repo"),
            output_file=Path(r"C:\tmp\last.txt"),
            prompt="hello",
            hook_trust_supported=True,
        )

        self.assertIn("--dangerously-bypass-hook-trust", command)

    def test_contradictory_read_prompt_is_invalid_harness(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text="hook: UserPromptSubmit Completed\nhook: Stop Completed\n",
            output_text=(
                "요청 조건상 현재 사용 가능한 로컬 파일 읽기 수단은 shell 명령뿐인데, "
                "사용자가 shell 명령을 금지했으므로 파일 내용을 읽지 않았다.\n"
                "[io-trace]\n- commands-run: none\n"
            ),
            output_exists=True,
        )

        self.assertEqual(result.status, "invalid-harness")
        self.assertIn("contradictory-tool-constraint", result.reasons)

    def test_clean_observed_session_passes(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text=(
                "hook: SessionStart Completed\n"
                "hook: UserPromptSubmit Completed\n"
                "hook: PreToolUse Completed\n"
                "hook: Stop Completed\n"
            ),
            output_text=(
                "[gate-state]\n"
                "- task-router: done\n"
                "[completion-check]\n"
                "- verification-before-completion: done\n"
                "[io-trace]\n"
                "- files-read: [C:\\Users\\try2q\\ghost-alice\\README.md]\n"
            ),
            output_exists=True,
        )

        self.assertEqual(result.status, "pass")
        self.assertEqual(result.reasons, [])

    def test_gate_state_can_be_observed_in_agent_log(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text=(
                "hook: UserPromptSubmit Completed\n"
                "codex\n"
                "[gate-state]\n"
                "- task-router: done\n"
            ),
            output_text="[io-trace]\n- files-read: [README.md]\n",
            output_exists=True,
            required_markers=("[gate-state]", "[io-trace]"),
        )

        self.assertEqual(result.status, "pass")

    def test_third_person_could_not_read_in_answer_is_not_invalid_harness(self):
        # A successful answer that incidentally describes some tool's behaviour
        # ("the doctor could not read X") must not be flagged invalid-harness.
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text="hook: UserPromptSubmit Completed\nhook: Stop Completed\n",
            output_text=(
                "[gate-state]\n- task-router: done\n"
                "Summary: install_doctor flags runs where it could not read the runtime file.\n"
                "[io-trace]\n- files-read: [install_doctor.py]\n"
            ),
            output_exists=True,
            required_markers=("[gate-state]", "[io-trace]"),
        )
        self.assertEqual(result.status, "pass")

    def test_describing_shell_forbidden_policy_is_not_invalid_harness(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text="hook: Stop Completed\n",
            output_text=(
                "[gate-state]\n- task-router: done\n"
                "The sandbox forbids shell commands in read-only mode, per policy.\n"
                "[io-trace]\n- files-read: [policy.md]\n"
            ),
            output_exists=True,
            required_markers=("[gate-state]", "[io-trace]"),
        )
        self.assertEqual(result.status, "pass")

    def test_first_person_unable_to_read_is_invalid_harness(self):
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text="hook: Stop Completed\n",
            output_text="I was unable to read the file because no read tool is available.\n",
            output_exists=True,
        )
        self.assertEqual(result.status, "invalid-harness")
        self.assertIn("read-method-unavailable", result.reasons)

    def test_first_person_was_not_able_or_failed_to_read_is_invalid_harness(self):
        # First-person genuine inability in varied phrasing must still be caught
        # (anchored to the agent's voice, so third-person prose stays a pass).
        for phrase in (
            "I was not able to read the file in this sandbox.",
            "I failed to read the file because the shell tool was unavailable.",
        ):
            result = live_agent_smoke.classify_smoke_result(
                exit_code=0,
                timed_out=False,
                log_text="hook: Stop Completed\n",
                output_text=phrase + "\n",
                output_exists=True,
            )
            self.assertEqual(result.status, "invalid-harness", phrase)

    def test_install_doctor_read_case_does_not_require_python(self):
        prompt = live_agent_smoke.SMOKE_CASES["install-doctor-read"].prompt

        self.assertNotIn("Python", prompt)
        self.assertNotIn("ast.parse", prompt)

    def test_install_doctor_read_case_uses_simple_full_file_read(self):
        prompt = live_agent_smoke.SMOKE_CASES["install-doctor-read"].prompt

        self.assertIn("Get-Content _shared/install_doctor.py -Raw", prompt)
        self.assertIn("full-file", prompt)

    def test_recovered_runtime_error_with_full_surface_passes(self):
        # exit 0 + all required markers + non-empty output = completed governed
        # task; a recovered (timestamped) tool-router-error in the runtime log
        # must not false-fail it (the agent.log still preserves the line).
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text=(
                "2026-06-30T22:24:14Z ERROR codex_core::tools::router: transient retry\n"
                "hook: Stop Completed\n"
            ),
            output_text=(
                "[gate-state]\n- task-router: done\n"
                "Summary: completed the multi-step task.\n"
                "[completion-check]\n- verification-before-completion: done\n"
                "[io-trace]\n- files-read: [README.md]\n"
            ),
            output_exists=True,
            required_markers=("[gate-state]", "[completion-check]", "[io-trace]"),
        )
        self.assertEqual(result.status, "pass")

    def test_runtime_error_without_full_surface_still_fails(self):
        # Same router error but the required surface is MISSING -> still a failure.
        result = live_agent_smoke.classify_smoke_result(
            exit_code=0,
            timed_out=False,
            log_text=(
                "2026-06-30T22:24:14Z ERROR codex_core::tools::router: boom\n"
                "hook: Stop Completed\n"
            ),
            output_text="partial output, no governance surface\n",
            output_exists=True,
            required_markers=("[gate-state]", "[io-trace]"),
        )
        self.assertEqual(result.status, "fail")
        self.assertIn("tool-router-error", result.reasons)


if __name__ == "__main__":
    unittest.main()
