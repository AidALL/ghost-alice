#!/usr/bin/env python3
"""Run one fresh Claude or Codex process and emit only its final response."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

try:
    from _shared.project_runtime import project_runtime
except ModuleNotFoundError:  # Direct execution from the installed _shared directory.
    from project_runtime import project_runtime


CODE_OWNER_ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {"claude", "codex"}
FORBIDDEN_SESSION_FLAGS = {"--resume", "--continue", "--session-id"}
CLAUDE_SESSION_SHORT_FLAGS = {"-r", "-c"}
ORDINARY_AUTH_ENV_KEYS = frozenset({
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "LANG",
    "LC_ALL",
    "TERM",
})


class FreshAgentSessionError(RuntimeError):
    """A fresh CLI process did not produce a usable final response."""


def _command(
    value: Sequence[str],
    *,
    forbidden_short: frozenset[str] | set[str] = frozenset(),
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("cli_command must be a sequence")
    command = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("cli_command items must be non-empty strings")
        command.append(item)
    if not command:
        raise ValueError("cli_command must not be empty")
    forbidden_assignment = tuple(f"{flag}=" for flag in FORBIDDEN_SESSION_FLAGS)
    if (FORBIDDEN_SESSION_FLAGS | set(forbidden_short)).intersection(command) or any(
        item.startswith(forbidden_assignment)
        for item in command
    ):
        raise ValueError("persistent session flags are forbidden")
    return command


def _ordinary_cli_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = os.environ if source is None else source
    return {
        key: environment[key]
        for key in ORDINARY_AUTH_ENV_KEYS
        if environment.get(key)
    }


def resolve_cli_command(
    binary: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    platform: str = os.name,
) -> list[str]:
    """Resolve a CLI executable without putting prompt text in argv."""

    requested = Path(binary)
    explicit_path = requested.is_absolute() or requested.parent != Path(".")
    if explicit_path:
        if requested.suffix.lower() == ".ps1":
            pwsh = which("pwsh.exe") or which("pwsh")
            if not pwsh:
                raise FreshAgentSessionError("PowerShell runtime is unavailable")
            return [
                pwsh,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(requested),
            ]
        return [str(requested)]

    if platform == "nt":
        cmd = which(f"{binary}.cmd")
        if cmd:
            return [cmd]
        ps1 = which(f"{binary}.ps1")
        if ps1:
            pwsh = which("pwsh.exe") or which("pwsh")
            if pwsh:
                return [
                    pwsh,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    ps1,
                ]
        executable = which(f"{binary}.exe")
        if executable:
            return [executable]

    resolved = which(binary)
    return [resolved] if resolved else [binary]


def build_claude_command(cli_command: Sequence[str]) -> list[str]:
    command = _command(
        cli_command,
        forbidden_short=CLAUDE_SESSION_SHORT_FLAGS,
    )
    command.extend([
        "-p",
        "--verbose",
        "--output-format",
        "stream-json",
        "--include-hook-events",
        "--no-session-persistence",
    ])
    return command


def build_codex_command(
    cli_command: Sequence[str],
    last_message_path: Path,
) -> list[str]:
    command = _command(cli_command)
    command.extend([
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--output-last-message",
        str(last_message_path),
        "-",
    ])
    return command


def parse_claude_response(stream_text: str) -> str:
    """Extract the final text from Claude stream-json without persisting it."""

    if not isinstance(stream_text, str):
        raise FreshAgentSessionError("Claude output was not text")
    assistant_text: str | None = None
    result_event: dict[str, Any] | None = None
    for line in stream_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "assistant":
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                candidate = "\n".join(parts).strip()
                if candidate:
                    assistant_text = candidate
        if event.get("type") == "result":
            result_event = event

    if result_event is None:
        raise FreshAgentSessionError("Claude output lacked a result event")
    if result_event.get("is_error") is True:
        raise FreshAgentSessionError("Claude reported an unsuccessful result")
    fallback = result_event.get("result")
    response = assistant_text or (fallback.strip() if isinstance(fallback, str) else "")
    if not response:
        raise FreshAgentSessionError("Claude produced no final assistant response")
    return response


def read_codex_response(last_message_path: Path) -> str:
    """Read Codex's run-local final message without consulting its runtime log."""

    try:
        response = last_message_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).strip()
    except OSError as exc:
        raise FreshAgentSessionError("Codex produced no readable final response") from exc
    if not response:
        raise FreshAgentSessionError("Codex produced no final assistant response")
    return response


def run_fresh_agent_session(
    *,
    platform: str,
    prompt: str,
    cli_command: Sequence[str] | None = None,
    repo_root: Path | str | None = None,
    timeout_seconds: float = 600,
    run_process: Callable[..., Any] | None = None,
    reporter: Callable[[str], None] | None = None,
) -> str:
    """Run a one-shot nonpersistent CLI process for one stdin prompt."""

    if platform not in PLATFORMS:
        raise ValueError("platform must be claude or codex")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("stdin prompt must be non-empty text")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive")

    base_command = _command(
        cli_command
        if cli_command is not None
        else resolve_cli_command(platform)
    )
    owning_root = Path(repo_root).resolve() if repo_root else CODE_OWNER_ROOT
    process_runner = run_process or subprocess.run
    with project_runtime(
        owning_root,
        f"fresh-agent-{platform}",
        reporter=reporter,
    ) as runtime:
        last_message_path = runtime.root / "last-message.txt"
        subject_environment = _ordinary_cli_env()
        subject_environment["GIT_CEILING_DIRECTORIES"] = str(runtime.root)
        command = (
            build_claude_command(base_command)
            if platform == "claude"
            else build_codex_command(base_command, last_message_path)
        )
        completed = runtime.run(
            command,
            cwd=runtime.work_dir,
            env=subject_environment,
            runner=process_runner,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise FreshAgentSessionError(
                f"{platform} process exited with code {completed.returncode}"
            )
        if platform == "claude":
            return parse_claude_response(completed.stdout)
        return read_codex_response(last_message_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--timeout-seconds", type=float, default=600)
    return parser


def _write_utf8(stream: TextIO, text: str) -> None:
    output = text if text.endswith("\n") else text + "\n"
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    try:
        stream.write(output)
    except UnicodeEncodeError:
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            raise
        buffer.write(output.encode("utf-8", errors="replace"))


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    run_process: Callable[..., Any] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    prompt = input_stream.read()
    binary = args.claude_bin if args.platform == "claude" else args.codex_bin
    command = resolve_cli_command(binary)
    try:
        response = run_fresh_agent_session(
            platform=args.platform,
            prompt=prompt,
            cli_command=command,
            timeout_seconds=args.timeout_seconds,
            run_process=run_process,
            reporter=lambda message: error_stream.write(message + "\n"),
        )
    except subprocess.TimeoutExpired:
        error_stream.write(f"[fresh-agent-session] {args.platform} timed out\n")
        return 124
    except (FreshAgentSessionError, OSError, ValueError) as exc:
        error_stream.write(f"[fresh-agent-session] {exc}\n")
        return 1
    _write_utf8(output_stream, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
