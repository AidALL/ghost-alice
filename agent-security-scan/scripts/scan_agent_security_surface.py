#!/usr/bin/env python3
"""Static scanner for Ghost-ALICE security surfaces.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*=\s*['\"]?[A-Za-z0-9._-]{20,}"),
]
EXTERNAL_FETCH_RE = re.compile(r"(?i)\b(curl|wget|fetch|httpie)\b[^\n]*https?://")
URL_RE = re.compile(r"(?i)https?://")
REMOTE_INSTRUCTION_RE = re.compile(r"(?i)\b(read|fetch|open|load|visit|download|retrieve)\b[^\n]{0,200}https?://")
# Preserve quoting until command-list boundaries have been identified. This
# covers simple commands, not substitutions, redirections, or shell control flow.
SHELL_TOKEN_RE = re.compile(
    r'''\#[^\n]*|(?:[^\s'"\\;&|]+|\\[\s\S]|'[^']*'|"(?:\\[\s\S]|[^"\\])*")+|[;&|]+|\n'''
)

CREDENTIAL_ACCESS_RE = re.compile(
    r"(?is)\b(print|reveal|dump|exfiltrate|read|cat|show)\b.{0,140}"
    r"\b(~/?\.ghost-alice/secrets\.env|secrets\.env|api\s+keys?|tokens?|passwords?|credentials?)\b"
)
WRITE_SIDE_EFFECT_COMMAND_RE = re.compile(
    r"(?i)\b(rm\s+(?:-[a-z]+\s+)*(?:-[a-z]*[rf][a-z]*|--(?:recursive|force))"
    r"|mv\s+|cp\s+|chmod\s+|chown\s+|install\.sh|install\.ps1)\b"
)
MCP_PACKAGE_MANAGER_RE = re.compile(
    r"(?i)\b(uvx|pipx|bunx|pnpm\s+dlx|yarn\s+dlx|npm\s+(install|i|exec)|"
    r"pip\s+install|python\s+-m\s+pip\s+install|cargo\s+install|go\s+install)\b"
)
# This skill runs without _shared, so it keeps a copy of the installer's current
# Ghost-ALICE hook markers; a _shared test keeps the two lists equal.
GHOST_ALICE_HOOK_MARKERS = (
    "[session-intent-analyzer]",
    "[tool-checkpoint] pre-tool-check",
    "[completion-reminder] AGENTS.md",
    "[merge-companion] session-check",
    "[session-intent-receipt] session-start",
    "[io-trace] audit",
)
SHELL_COMMANDS = {"bash", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "sh", "zsh"}
ENV_COMMANDS = {"env"}
INLINE_INTERPRETER_COMMANDS = {"node", "osascript", "perl", "php", "python", "python3", "ruby"}
DIRECT_RUN_PACKAGE_MANAGERS = {"bunx", "npx", "pipx", "uvx"}
CONTAINER_COMMANDS = {"docker", "podman"}
CONTAINER_RUN_ACTIONS = {"build", "compose", "pull", "run"}
DOCKER_SOCKET_PATHS = {"/private/var/run/docker.sock", "/run/docker.sock", "/var/run/docker.sock"}
SEVERITY_CREDENTIAL = "critical - credential exposure or secret access"
SEVERITY_COMMAND = "high - command can run without protective gate"
SEVERITY_WRITE = "high - write, install, permission, or destructive side effect"
SEVERITY_MCP = "medium - package manager or broad MCP exposure"
SEVERITY_REMOTE = "medium - remote instruction surface"
SEVERITY_HYGIENE = "low - hygiene or configuration issue"


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    path: str
    message: str
    mitigation: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "severity": self.severity,
            "rule": self.rule,
            "path": self.path,
            "message": self.message,
            "mitigation": self.mitigation,
        }
        if self.details:
            row["details"] = self.details
        return row


def iter_json_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_json_strings(item)


def iter_command_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "command" and isinstance(item, str):
                yield item
            yield from iter_command_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_command_strings(item)


def hook_command_vectors(command: str) -> Iterable[list[str]]:
    """Yield simple-command argv without treating quoted separators as syntax."""
    tokens: list[str] = []
    for match in SHELL_TOKEN_RE.finditer(command):
        token = match.group()
        if token in {";", "&&", "||", "|", "&", "\n"}:
            if tokens:
                yield command_words(" ".join(tokens))
                tokens = []
        elif not token.startswith("#"):
            if not tokens and re.match(r"[A-Za-z_]\w*=", token):
                continue
            tokens.append(token)
    if tokens:
        yield command_words(" ".join(tokens))


def is_shell_command(command: str) -> bool:
    for words in hook_command_vectors(command):
        if not words:
            continue
        name, args = effective_argv(words)
        if name in {"curl", "wget"}:
            return True
        if name in SHELL_COMMANDS:
            for arg in args:
                if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg) or arg.lower() in {"/c", "-command"}:
                    return True
                if not arg.startswith("-"):
                    break
    return False


def is_profiled(command: str) -> bool:
    lowered = command.lower()
    return "profile-gate" in lowered or "hook_profile_gate.py" in lowered or "ghost-alice-hook.mjs" in lowered


def has_ghost_alice_marker(command: str) -> bool:
    return any(marker in command for marker in GHOST_ALICE_HOOK_MARKERS)


def is_unmanaged_hook_command(command: str) -> bool:
    if has_ghost_alice_marker(command):
        return False
    if is_profiled(command) and not URL_RE.search(command):
        return False
    return (
        is_shell_command(command)
        or has_inline_interpreter_command(command)
        or EXTERNAL_FETCH_RE.search(command) is not None
        or URL_RE.search(command) is not None
    )


def has_secret_like_value(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SECRET_PATTERNS)
    if isinstance(value, dict):
        return any(has_secret_like_value(item) for item in value.values())
    if isinstance(value, list):
        return any(has_secret_like_value(item) for item in value)
    return False


def redact_if_sensitive(value: Any) -> str:
    text = str(value)
    if has_secret_like_value(text):
        return "<redacted-sensitive-value>"
    if len(text) > 160:
        return text[:157] + "..."
    return text


def command_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def normalized_arg_list(args: Any) -> list[str]:
    if isinstance(args, list):
        return [str(item) for item in args]
    if isinstance(args, str):
        return command_words(args)
    return []


def scan_json_commands(path: Path, data: Any) -> list[Finding]:
    findings: list[Finding] = []
    for command in iter_command_strings(data):
        if has_ghost_alice_marker(command) and not is_profiled(command):
            findings.append(
                Finding(
                    severity=SEVERITY_COMMAND,
                    rule="ghost-alice-hook-missing-profile-gate",
                    path=str(path),
                    message="Ghost-ALICE hook marker is present but command is not wrapped by the profile gate",
                    mitigation="mitigate",
                )
            )
        if is_unmanaged_hook_command(command):
            findings.append(
                Finding(
                    severity=SEVERITY_COMMAND,
                    rule="unmanaged-hook-command",
                    path=str(path),
                    message="hook command executes shell or network behavior outside Ghost-ALICE-managed wrappers",
                    mitigation="mitigate",
                )
            )
        if is_shell_command(command) and not is_profiled(command):
            findings.append(
                Finding(
                    severity=SEVERITY_COMMAND,
                    rule="unprofiled-shell-command",
                    path=str(path),
                    message="hook command executes through shell without Ghost-ALICE profile gate",
                    mitigation="mitigate",
                )
            )
        if WRITE_SIDE_EFFECT_COMMAND_RE.search(command):
            findings.append(
                Finding(
                    severity=SEVERITY_WRITE,
                    rule="write-side-effect-command",
                    path=str(path),
                    message="hook command contains write, install, permission, or destructive side effect",
                    mitigation="reject",
                )
            )
    return findings


def has_mcp_auto_install(data: Any) -> bool:
    rendered_strings = list(iter_json_strings(data))
    joined = " ".join(rendered_strings).lower()
    command_values = [value.strip().lower() for value in iter_command_strings(data)]
    has_npx_command = "npx" in command_values or re.search(r"\bnpx\s+-y\b", joined) is not None
    has_npx_auto_yes = "-y" in rendered_strings or " --yes " in f" {joined} "
    if has_npx_command and has_npx_auto_yes:
        return True
    return MCP_PACKAGE_MANAGER_RE.search(joined) is not None


def mcp_servers(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = data.get("servers")
    return servers if isinstance(servers, dict) else {}


def command_text(command: Any, args: Any) -> str:
    parts: list[str] = []
    if isinstance(command, str):
        parts.append(command)
    if isinstance(args, list):
        parts.extend(str(item) for item in args)
    elif isinstance(args, str):
        parts.append(args)
    return " ".join(parts)


def command_name(command: Any) -> str:
    if not isinstance(command, str):
        return ""
    return Path(command).name.lower()


def command_family(command: str) -> str:
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", command):
        return "python"
    if re.fullmatch(r"pip(?:\d+(?:\.\d+)*)?", command):
        return "pip"
    return command


def is_inline_exec_arg(arg: str) -> bool:
    return arg in {"-c", "-e", "--eval"} or arg.startswith(("-c", "-e", "--eval="))


def has_inline_interpreter_args(command: str, args: list[str]) -> bool:
    return command_family(command) in INLINE_INTERPRETER_COMMANDS and any(is_inline_exec_arg(arg) for arg in args)


def has_inline_interpreter_command(command: str) -> bool:
    for words in hook_command_vectors(command):
        if not words:
            continue
        name, args = effective_argv(words)
        if has_inline_interpreter_args(name, args):
            return True
    return False


def effective_command_and_args(command: Any, args: Any) -> tuple[str, list[str]]:
    # A recognized leading launcher supports legacy command-plus-argument text.
    # Otherwise the native command remains one argv item, including its spaces.
    command_parts: list[str] = []
    if isinstance(command, str):
        command_parts = command_words(command)
        if len(command_parts) > 1:
            first_name = command_family(command_name(command_parts[0]))
            known_launcher = first_name in (
                SHELL_COMMANDS | ENV_COMMANDS | INLINE_INTERPRETER_COMMANDS
                | DIRECT_RUN_PACKAGE_MANAGERS | CONTAINER_COMMANDS
            )
            package_action = MCP_PACKAGE_MANAGER_RE.match(" ".join([first_name, *command_parts[1:]]))
            if not known_launcher and not package_action:
                command_parts = [command]
    return effective_argv([*(command_parts or [""]), *normalized_arg_list(args)])


def effective_argv(words: list[str]) -> tuple[str, list[str]]:
    """Classify a literal argv after known launchers consume their own options."""
    command = command_family(command_name(words[0])) if words else ""
    normalized_args = words[1:]
    wrapper_values = {
        'exec': {'-a'}, 'nohup': set(),
        'sudo': {'-u', '--user', '-g', '--group', '-h', '--host', '-p', '--prompt', '-C', '--close-from', '-T', '--command-timeout', '-R', '--chroot', '-D', '--chdir'},
        'timeout': {'-k', '--kill-after', '-s', '--signal'},
        'nice': {'-n', '--adjustment'}, 'command': set(),
        'time': {'-f', '--format', '-o', '--output'},
        'stdbuf': {'-i', '--input', '-o', '--output', '-e', '--error'},
        'xargs': {'-a', '--arg-file', '-d', '--delimiter', '-E', '-I', '-L', '-n', '--max-args', '-P', '--max-procs', '-s', '--max-chars'},
    }
    if command in wrapper_values:
        if command == 'command' and any(arg in {'-v', '-V'} for arg in normalized_args):
            return command, normalized_args
        index = 0
        while index < len(normalized_args):
            arg = normalized_args[index]
            if arg == '--':
                index += 1
                break
            if not arg.startswith('-'):
                break
            index += 2 if arg in wrapper_values[command] else 1
        if command == 'timeout':
            index += 1  # timeout consumes a duration before the command.
        return effective_argv(normalized_args[index:]) if index < len(normalized_args) else (command, normalized_args)
    if command not in ENV_COMMANDS:
        return command, normalized_args

    expanded_args: list[str] = []
    index = 0
    while index < len(normalized_args):
        arg = normalized_args[index]
        if arg == "-S" and index + 1 < len(normalized_args):
            expanded_args.extend(command_words(normalized_args[index + 1]))
            expanded_args.extend(normalized_args[index + 2 :])
            break
        expanded_args.append(arg)
        index += 1

    for index, arg in enumerate(expanded_args):
        if not arg or arg.startswith("-") or ("=" in arg and not arg.startswith(("/", "."))):
            continue
        return effective_argv(expanded_args[index:])
    return command, expanded_args


def mcp_details(server: str, config: dict[str, Any], home: Path) -> dict[str, Any]:
    args = config.get("args")
    env = config.get("env")
    details: dict[str, Any] = {
        "server": server,
        "command": redact_if_sensitive(config.get("command", "")),
        "args": [redact_if_sensitive(item) for item in normalized_arg_list(args)],
        "env_keys": sorted(env.keys()) if isinstance(env, dict) else [],
    }
    roots = broad_filesystem_roots(config, home)
    if roots:
        details["filesystem_roots"] = roots
    return details


def server_has_mcp_auto_install(config: dict[str, Any]) -> bool:
    command, args = effective_command_and_args(config.get("command"), config.get("args"))
    rendered = " ".join([command, *args]).lower()
    if command in DIRECT_RUN_PACKAGE_MANAGERS:
        return True
    if command in CONTAINER_COMMANDS and any(arg in CONTAINER_RUN_ACTIONS for arg in args):
        return True
    return MCP_PACKAGE_MANAGER_RE.search(rendered) is not None


def is_mcp_shell_command(config: dict[str, Any]) -> bool:
    command, args = effective_command_and_args(config.get("command"), config.get("args"))
    return command in SHELL_COMMANDS or has_inline_interpreter_args(command, args)


def resolve_config_path(text: str, home: Path) -> Path | None:
    home_resolved = home.expanduser().resolve(strict=False)
    if text in {"~", "$HOME", "${HOME}"}:
        return home_resolved
    for prefix in ("~/", "$HOME/", "${HOME}/"):
        if text.startswith(prefix):
            suffix = text[len(prefix) :]
            return (home_resolved / suffix).resolve(strict=False)
    candidate = Path(text).expanduser()
    if text.startswith("/") or candidate.is_absolute():
        return candidate.resolve(strict=False)
    return None


def broad_filesystem_roots(config: dict[str, Any], home: Path) -> list[str]:
    _, args = effective_command_and_args(config.get("command"), config.get("args"))
    home_resolved = home.expanduser().resolve()
    broad: list[str] = []
    for index, item in enumerate(args):
        text = str(item)
        volume_source = mount_source_path(args[index + 1]) if text == "--mount" and index + 1 < len(args) else ""
        if not volume_source:
            volume_source = volume_source_path(text)
        if volume_source:
            text = volume_source
        if text in DOCKER_SOCKET_PATHS:
            broad.append(text)
            continue
        resolved = resolve_config_path(text, home)
        if resolved is None:
            continue
        if (
            resolved == home_resolved
            or resolved == home_resolved.parent
            or resolved.parent == resolved
            or str(resolved) in DOCKER_SOCKET_PATHS
        ):
            broad.append(str(resolved))
    return broad


def volume_source_path(text: str) -> str:
    if text.startswith("--volume="):
        text = text.removeprefix("--volume=")
    if text.startswith("--mount"):
        return mount_source_path(text.removeprefix("--mount="))
    if ":" not in text:
        return ""
    source = text.split(":", 1)[0]
    if source.startswith(("/", "~", "$HOME", "${HOME}")):
        return source
    return ""


def mount_source_path(text: str) -> str:
    for part in str(text).split(","):
        if part.startswith(("source=", "src=")):
            return part.split("=", 1)[1]
    return ""


def scan_mcp(path: Path, data: Any, home: Path) -> list[Finding]:
    findings: list[Finding] = []
    servers = mcp_servers(data)
    for server, raw_config in servers.items():
        if not isinstance(raw_config, dict):
            continue
        details = mcp_details(server, raw_config, home)
        if server_has_mcp_auto_install(raw_config):
            findings.append(
                Finding(
                    severity=SEVERITY_MCP,
                    rule="mcp-auto-install",
                    path=str(path),
                    message="MCP server can auto-install package manager dependencies",
                    mitigation="mitigate",
                    details=details,
                )
            )
        if broad_filesystem_roots(raw_config, home):
            findings.append(
                Finding(
                    severity=SEVERITY_MCP,
                    rule="mcp-broad-filesystem-root",
                    path=str(path),
                    message="filesystem MCP server exposes a broad local root",
                    mitigation="mitigate",
                    details=details,
                )
            )
        if is_mcp_shell_command(raw_config):
            findings.append(
                Finding(
                    severity=SEVERITY_COMMAND,
                    rule="mcp-shell-command",
                    path=str(path),
                    message="MCP server command uses a shell or inline interpreter wrapper",
                    mitigation="reject",
                    details=details,
                )
            )
        if (
            has_secret_like_value(raw_config.get("command"))
            or has_secret_like_value(raw_config.get("args"))
            or has_secret_like_value(raw_config.get("env"))
        ):
            findings.append(
                Finding(
                    severity=SEVERITY_CREDENTIAL,
                    rule="mcp-secret-inline",
                    path=str(path),
                    message="MCP server command, args, or env contain a credential-like value; raw value redacted",
                    mitigation="reject",
                    details=details,
                )
            )
    if not servers and has_mcp_auto_install(data):
        findings.append(
            Finding(
                severity=SEVERITY_MCP,
                rule="mcp-auto-install",
                path=str(path),
                message="MCP config can auto-install package manager dependencies",
                mitigation="mitigate",
            )
        )
    return findings


def scan_codex_config(path: Path, data: Any) -> list[Finding]:
    features = data.get("features") if isinstance(data, dict) else None
    hooks_enabled = isinstance(features, dict) and features.get("hooks") is True
    if hooks_enabled:
        return []
    return [
        Finding(
            severity=SEVERITY_HYGIENE,
            rule="codex-hooks-disabled",
            path=str(path),
            message="Codex hooks config exists but does not enable hooks=true",
            mitigation="mitigate",
        )
    ]


def scan_skill(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    if EXTERNAL_FETCH_RE.search(text) or REMOTE_INSTRUCTION_RE.search(text):
        findings.append(
            Finding(
                severity=SEVERITY_REMOTE,
                rule="external-fetch-in-skill",
                path=str(path),
                message="skill text instructs external URL fetch or remote script retrieval",
                mitigation="mitigate",
            )
        )
    if CREDENTIAL_ACCESS_RE.search(text):
        findings.append(
            Finding(
                severity=SEVERITY_CREDENTIAL,
                rule="credential-access-in-skill",
                path=str(path),
                message="skill text instructs reading or revealing credential material",
                mitigation="reject",
            )
        )
    return findings


def scan_secret_file(path: Path, text: str) -> list[Finding]:
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        return [
            Finding(
                severity=SEVERITY_CREDENTIAL,
                rule="secret-pattern",
                path=str(path),
                message="file contains a credential-like value; raw value redacted",
                mitigation="reject",
            )
        ]
    return []


def existing_unique(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if path.exists() and resolved not in seen:
            seen.add(resolved)
            result.append(path)
    return result


def iter_skill_paths(root: Path, home: Path) -> Iterable[Path]:
    skill_roots = [
        root,
        home / ".claude" / "skills",
        home / ".agents" / "skills",
        home / ".codex" / "skills",
    ]
    for skill_root in skill_roots:
        if not skill_root.exists():
            continue
        yield from sorted(skill_root.glob("*/SKILL.md"))


def candidate_paths(root: Path, home: Path) -> dict[str, list[Path]]:
    paths = {
        "settings": [
            home / ".claude" / "settings.json",
            root / ".claude" / "settings.json",
            home / ".codex" / "hooks.json",
        ],
        "codex_config": [home / ".codex" / "config.toml"],
        "skills": list(iter_skill_paths(root, home)),
        "mcp": [root / ".mcp.json", home / ".mcp.json"],
        "secrets": [root / ".env", home / ".env"],
    }
    return {kind: existing_unique(items) for kind, items in paths.items()}


def scan(root: Path, home: Path) -> dict[str, Any]:
    """Report read/parse coverage for selected files separately from findings."""
    findings: list[Finding] = []
    coverage: list[dict[str, str]] = []
    for surface, paths in candidate_paths(root, home).items():
        for path in paths:
            observation = {"path": str(path), "surface": surface, "status": "unobserved"}
            coverage.append(observation)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                observation["reason"] = "read-error"
                continue
            try:
                if surface == "codex_config":
                    data = tomllib.loads(text)
                elif surface in {"settings", "mcp"}:
                    data = json.loads(text)
            except (json.JSONDecodeError, tomllib.TOMLDecodeError):
                observation["reason"] = "parse-error"
                continue
            observation["status"] = "observed"
            if surface == "settings":
                findings.extend(scan_json_commands(path, data))
            elif surface == "codex_config":
                findings.extend(scan_codex_config(path, data))
                findings.extend(scan_mcp(path, {"servers": data.get("mcp_servers", {})}, home))
            elif surface == "skills":
                findings.extend(scan_skill(path, text))
            elif surface == "mcp":
                findings.extend(scan_mcp(path, data, home))
            elif surface == "secrets":
                findings.extend(scan_secret_file(path, text))

    rows = [finding.to_dict() for finding in findings]
    rows.sort(key=lambda item: (item["severity"], item["rule"], item["path"]))
    return {
        "root": str(root),
        "home": str(home),
        "finding_count": len(rows),
        "findings": rows,
        "coverage": coverage,
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [f"finding_count: {report['finding_count']}"]
    for observation in report["coverage"]:
        reason = f" [{observation['reason']}]" if "reason" in observation else ""
        lines.append(f"coverage: {observation['status']} {observation['path']}{reason}")
    for finding in report["findings"]:
        lines.append(
            f"- {finding['severity']} {finding['rule']} {finding['path']} [{finding['mitigation']}]"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan local agent security surfaces.")
    parser.add_argument("--root", default=".", help="repo root to inspect")
    parser.add_argument("--home", default=str(Path.home()), help="HOME-like directory to inspect")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = scan(Path(args.root).expanduser().resolve(), Path(args.home).expanduser().resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
