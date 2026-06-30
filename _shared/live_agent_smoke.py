#!/usr/bin/env python3
"""Run and classify live agent smoke sessions.

This helper is intentionally outside installer doctor. Doctor is a read-only
configuration diagnostic; live smoke starts a real agent session and may be slow.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_INVALID_HARNESS = "invalid-harness"
STATUS_INCONCLUSIVE = "inconclusive"
HOOK_TRUST_FLAG = "--dangerously-bypass-hook-trust"


@dataclass(frozen=True)
class SmokeCase:
    case_id: str
    prompt: str
    required_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class SmokeClassification:
    status: str
    reasons: list[str]


SMOKE_CASES: dict[str, SmokeCase] = {
    "readme-first-lines": SmokeCase(
        case_id="readme-first-lines",
        prompt=(
            "Read the first 10 lines of README.md using a read-only local "
            "command, summarize what Ghost-ALICE OS is in one paragraph, "
            "apply verification-before-completion before any completion "
            "claim, and include [io-trace]. Do not edit files."
        ),
        required_markers=("[gate-state]", "[io-trace]"),
    ),
    "install-doctor-read": SmokeCase(
        case_id="install-doctor-read",
        prompt=(
            "Read _shared/install_doctor.py using a simple read-only full-file "
            "command such as `Get-Content _shared/install_doctor.py -Raw`; avoid "
            "shell-side filtering or formatting for the target file. "
            "Summarize in at most 4 lines what runtime-core doctor drift "
            "detection covers, citing concrete code symbols as evidence. "
            "Do not edit files. Include [io-trace]."
        ),
        required_markers=("[gate-state]", "[io-trace]"),
    ),
    "completion-check-readme": SmokeCase(
        case_id="completion-check-readme",
        prompt=(
            "Read the first 10 lines of README.md, make a brief completion "
            "claim only after fresh verification-before-completion, and "
            "include both [completion-check] and [io-trace]. Do not edit files."
        ),
        required_markers=("[completion-check]", "[io-trace]"),
    ),
}


FAILURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("tool-router-error", re.compile(r"ERROR\s+codex_core::tools::router", re.IGNORECASE)),
    ("runtime-cache-error", re.compile(r"Failed to initialize cache", re.IGNORECASE)),
    ("hook-failure", re.compile(r"hook:\s+[A-Za-z]+\s+Failed", re.IGNORECASE)),
    ("python-traceback", re.compile(r"Traceback \(most recent call last\)|ModuleNotFoundError")),
    ("runtime-panic", re.compile(r"thread '.*' panicked|panic", re.IGNORECASE)),
)


INVALID_HARNESS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "contradictory-tool-constraint",
        # Anchored to the agent's OWN voice (first person) so that prose which
        # merely describes a shell/tool restriction (e.g. summarizing a policy
        # or doctor behaviour) is not misread as an invalid harness. The Korean
        # self-report phrases are already specific enough to stand alone.
        re.compile(
            r"\b(?:i|we)\b[^.\n]{0,100}(?:shell|command|tool)[^.\n]{0,60}"
            r"(?:forbid|forbidden|not allowed|prohibit|disabled)"
            r"|파일 내용을 읽지 않았다"
            r"|shell[^\n]{0,40}(?:금지|불가)"
            r"|명령[^\n]{0,40}금지",
            re.IGNORECASE,
        ),
    ),
    (
        "read-method-unavailable",
        # First-person inability to read/access, or an explicit "no read tool"
        # statement. A third-person mention ("the doctor could not read X") in an
        # otherwise-successful answer is not an invalid harness.
        re.compile(
            r"\b(?:i|we)\b[^.\n]{0,40}"
            r"(?:cannot|can't|could not|couldn't|unable to|not able to|failed to)\s+"
            r"(?:read|access|inspect|open)\b"
            r"|no available (?:read|file) (?:tool|method)",
            re.IGNORECASE,
        ),
    ),
)


# CLI/harness errors (unknown flag, version skew) are not governance failures of
# the agent. They are matched on the runtime log and reported as invalid-harness
# so an operator fixes the harness/CLI, not the agent.
HARNESS_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cli-arg-rejected", re.compile(r"error:\s+(?:unexpected|unrecognized|invalid)\s+(?:argument|option|subcommand)", re.IGNORECASE)),
    ("cli-arg-rejected", re.compile(r"unexpected argument\s+.+\s+found", re.IGNORECASE)),
)


def _contains_pattern(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(text))


def _runtime_diagnostic_log_text(log_text: str) -> str:
    """Keep runtime diagnostics from Codex stdout without agent answer prose."""

    diagnostic_lines: list[str] = []
    timestamped = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+\s+(?:ERROR|WARN|INFO|DEBUG)\s+")
    for line in log_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if timestamped.match(stripped):
            diagnostic_lines.append(stripped)
        elif stripped.startswith("hook:"):
            diagnostic_lines.append(stripped)
        elif stripped.startswith((
            "Error loading config.toml",
            "error:",
            "Failed to initialize cache",
            "thread '",
            "[smoke-timeout]",
        )):
            diagnostic_lines.append(stripped)
        elif stripped.lower().startswith("panic"):
            diagnostic_lines.append(stripped)
    return "\n".join(diagnostic_lines)


def classify_smoke_result(
    *,
    exit_code: int | None,
    timed_out: bool,
    log_text: str,
    output_text: str,
    output_exists: bool,
    required_markers: Iterable[str] = (),
) -> SmokeClassification:
    """Classify a live smoke run without storing raw prompts."""

    combined = f"{log_text}\n{output_text}"
    runtime_log = _runtime_diagnostic_log_text(log_text)
    fail_reasons: list[str] = []

    if timed_out:
        fail_reasons.append("timeout")

    # A CLI/harness error (unknown flag, version skew) is not a governance
    # failure. Detect it on the runtime log and report invalid-harness, before
    # the exit-code/fail logic so a nonzero exit cannot mask it.
    if not timed_out:
        harness_reasons: list[str] = []
        for reason, pattern in HARNESS_ERROR_PATTERNS:
            if _contains_pattern(pattern, runtime_log) and reason not in harness_reasons:
                harness_reasons.append(reason)
        if harness_reasons:
            return SmokeClassification(status=STATUS_INVALID_HARNESS, reasons=harness_reasons)

    if exit_code is None and not timed_out:
        fail_reasons.append("missing-exit-code")
    elif exit_code not in (None, 0):
        fail_reasons.append(f"exit-code:{exit_code}")

    # Runtime-error patterns are scanned against the runtime LOG only, never the
    # agent's own answer text, so an agent that legitimately quotes an error
    # string in its summary is not false-failed.
    for reason, pattern in FAILURE_PATTERNS:
        if _contains_pattern(pattern, runtime_log) and reason not in fail_reasons:
            fail_reasons.append(reason)

    if not output_exists or not output_text.strip():
        fail_reasons.append("missing-output")

    for marker in required_markers:
        if marker not in combined:
            fail_reasons.append(f"missing-marker:{marker}")

    if fail_reasons:
        return SmokeClassification(status=STATUS_FAIL, reasons=fail_reasons)

    invalid_reasons = [
        reason for reason, pattern in INVALID_HARNESS_PATTERNS
        if _contains_pattern(pattern, output_text)
    ]
    if invalid_reasons:
        return SmokeClassification(
            status=STATUS_INVALID_HARNESS,
            reasons=invalid_reasons,
        )

    return SmokeClassification(status=STATUS_PASS, reasons=[])


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _default_log_root() -> Path:
    home = Path.home()
    return home / ".ghost-alice" / "live-smoke"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_codex_command(
    codex_bin: str,
    *,
    which=shutil.which,
    platform: str = os.name,
) -> list[str]:
    """Resolve the Codex executable without relying on Windows PATHEXT order."""

    requested = Path(codex_bin)
    explicit_path = requested.is_absolute() or requested.parent != Path(".")
    if explicit_path:
        if requested.suffix.lower() == ".ps1":
            pwsh = which("pwsh.exe") or which("pwsh")
            if not pwsh:
                return []
            return [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(requested)]
        return [str(requested)]

    if platform == "nt" and codex_bin.lower() == "codex":
        cmd = which("codex.cmd")
        if cmd:
            return [cmd]
        ps1 = which("codex.ps1")
        if ps1:
            pwsh = which("pwsh.exe") or which("pwsh")
            if pwsh:
                return [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1]
        exe = which("codex.exe")
        if exe:
            return [exe]

    resolved = which(codex_bin)
    return [resolved] if resolved else []


def codex_exec_help_text(
    codex_command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int = 15,
) -> str:
    """Return `codex exec --help` output, or empty text if probing fails."""

    try:
        completed = subprocess.run(
            [*codex_command, "exec", "--help"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout or ""


def codex_supports_hook_trust(
    codex_command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int = 15,
) -> bool:
    """Detect whether the installed Codex exec command accepts hook-trust bypass."""

    return HOOK_TRUST_FLAG in codex_exec_help_text(
        codex_command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )


def build_codex_exec_command(
    *,
    codex_command: list[str],
    repo_root: Path,
    output_file: Path,
    prompt: str,
    hook_trust_supported: bool,
) -> list[str]:
    command = [*codex_command, "exec"]
    if hook_trust_supported:
        command.append(HOOK_TRUST_FLAG)
    command.extend(
        [
            "--sandbox",
            "read-only",
            "-C",
            str(repo_root),
            "--output-last-message",
            str(output_file),
            prompt,
        ]
    )
    return command


def run_codex_case(
    *,
    case: SmokeCase,
    repo_root: Path,
    log_root: Path,
    codex_bin: str,
    timeout_seconds: int,
) -> dict[str, object]:
    """Run one Codex exec smoke case and return a raw-free summary."""

    codex_command = resolve_codex_command(codex_bin)
    if not codex_command:
        classification = SmokeClassification(
            status=STATUS_INCONCLUSIVE,
            reasons=["codex-binary-not-found"],
        )
        return {
            "platform": "codex",
            "case": case.case_id,
            "status": classification.status,
            "reasons": classification.reasons,
            "exit_code": None,
            "timed_out": False,
            "log_file": None,
            "output_file": None,
        }

    run_dir = log_root / _timestamp() / f"codex-{case.case_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = run_dir / "agent.log"
    output_file = run_dir / "last-message.txt"
    summary_file = run_dir / "summary.json"

    hook_trust_supported = codex_supports_hook_trust(codex_command, cwd=repo_root)
    command = build_codex_exec_command(
        codex_command=codex_command,
        repo_root=repo_root,
        output_file=output_file,
        prompt=case.prompt,
        hook_trust_supported=hook_trust_supported,
    )

    exit_code: int | None
    timed_out = False
    log_text = ""
    try:
        completed = subprocess.run(
            command,
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        log_text = completed.stdout or ""
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        timed_out = True
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        log_text = partial + f"\n[smoke-timeout] exceeded {timeout_seconds}s\n"

    log_file.write_text(log_text, encoding="utf-8")
    output_text = _read_text(output_file)
    classification = classify_smoke_result(
        exit_code=exit_code,
        timed_out=timed_out,
        log_text=log_text,
        output_text=output_text,
        output_exists=output_file.exists(),
        required_markers=case.required_markers,
    )

    summary: dict[str, object] = {
        "platform": "codex",
        "case": case.case_id,
        "status": classification.status,
        "reasons": classification.reasons,
        "agent_command": codex_command[0],
        "codex_command": codex_command[0],
        "hook_trust_flag": "enabled" if hook_trust_supported else "omitted-unsupported",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "log_file": str(log_file),
        "output_file": str(output_file),
        "summary_file": str(summary_file),
    }
    summary_file.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def classify_existing(args: argparse.Namespace) -> dict[str, object]:
    log_file = Path(args.log_file)
    output_file = Path(args.output_file) if args.output_file else None
    output_exists = bool(output_file and output_file.exists())
    output_text = _read_text(output_file) if output_file else ""
    classification = classify_smoke_result(
        exit_code=args.exit_code,
        timed_out=args.timed_out,
        log_text=_read_text(log_file),
        output_text=output_text,
        output_exists=output_exists,
        required_markers=args.required_marker,
    )
    return {
        "status": classification.status,
        "reasons": classification.reasons,
        "log_file": str(log_file),
        "output_file": str(output_file) if output_file else None,
        "exit_code": args.exit_code,
        "timed_out": args.timed_out,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("codex",), default="codex")
    parser.add_argument("--case", choices=sorted(SMOKE_CASES), default="readme-first-lines")
    parser.add_argument("--repo-root", default=os.getcwd())
    parser.add_argument("--log-root", default=str(_default_log_root()))
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--classify-log")
    parser.add_argument("--output-file")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--timed-out", action="store_true")
    parser.add_argument("--required-marker", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.classify_log:
        args.log_file = args.classify_log
        summary = classify_existing(args)
    else:
        summary = run_codex_case(
            case=SMOKE_CASES[args.case],
            repo_root=Path(args.repo_root).resolve(),
            log_root=Path(args.log_root).resolve(),
            codex_bin=args.codex_bin,
            timeout_seconds=args.timeout,
        )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == STATUS_PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
