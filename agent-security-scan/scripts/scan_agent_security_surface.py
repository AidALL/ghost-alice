#!/usr/bin/env python3
"""Static scanner for Ghost-ALICE security surfaces.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
import json
import re
import shlex
import tomllib
from dataclasses import dataclass, field
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


@dataclass
class _ShellContext:
    """The lexical and command-position state of one open shell region."""

    closing: str = ""
    expansions_only: bool = False
    literal_text: bool = False
    words: list[str] = field(default_factory=list)
    word: list[str] = field(default_factory=list)
    quote: str = ""
    case_states: list[str] = field(default_factory=list)
    loop_header: bool = False
    function_header: bool = False
    arithmetic: bool = False
    at_start: bool = True
    redirect: str = ""
    redirect_fd: str = ""
    stdin_heredoc: int | None = None
    heredocs: list[tuple[str, bool, bool, bool]] = field(default_factory=list)

    def finish_word(self) -> None:
        raw = "".join(self.word)
        self.word.clear()
        if not raw or self.expansions_only:
            return
        try:
            decoded = shlex.split(raw)
        except ValueError:
            return  # Malformed quoting supplies no reliable argv boundary.
        if not decoded:
            return
        if self.redirect:
            stdin_redirect = self.redirect.startswith('<') and self.redirect_fd in {'', '0'}
            if stdin_redirect:
                self.stdin_heredoc = None
            if self.redirect in {"<<", "<<-"}:
                if stdin_redirect:
                    self.stdin_heredoc = len(self.heredocs)
                self.heredocs.append((decoded[0], any(c in raw for c in "'\"\\"), self.redirect == "<<-", False))
            self.redirect = ""
            return
        if self.function_header:
            if raw == '{':
                self.function_header = False
            return
        if self.loop_header:
            if self.at_start and raw == "do":
                self.loop_header = False
            else:
                self.at_start = False
            return
        if self.case_states and self.case_states[-1] != "body":
            if raw == "esac":
                self.case_states.pop()
            elif raw == "in" and self.case_states[-1] == "header":
                self.case_states[-1] = "pattern"
            return
        if not self.words:
            if raw in {"if", "then", "elif", "else", "while", "until", "do", "!", "{", "fi", "done", "}", "coproc"}:
                return
            if raw == 'function':
                self.function_header = True
                return
            if raw in {"for", "select"}:
                self.loop_header = True
                self.at_start = False
                return
            if raw == "case":
                self.case_states.append("header")
                return
            if raw == "esac" and self.case_states:
                self.case_states.pop()
                return
            if re.match(r"[A-Za-z_]\w*\+?=", raw):
                return
        self.words.extend(decoded)
        self.at_start = False

    def finish_command(self) -> list[str]:
        self.finish_word()
        words, self.words = self.words, []
        if self.stdin_heredoc is not None:
            name, args = effective_argv(words, stdin_receiver=True)
            delimiter, literal, tabs, _ = self.heredocs[self.stdin_heredoc]
            self.heredocs[self.stdin_heredoc] = (delimiter, literal, tabs, shell_input_mode(name, args) == 'stdin')
            self.stdin_heredoc = None
        self.at_start = True
        self.redirect = ""
        return words


def _shell_command_vectors(command: str) -> Iterable[list[str]]:
    """Read static command positions, never evaluate expansions or run commands.

    Nested regions keep their own quotes and grammar state. An explicit stack
    makes case-pattern parentheses distinct from region endings without using
    Python recursion. Heredoc bodies are source ranges, so nested or missing
    delimiters do not copy and repeatedly search the same remaining suffix.
    """
    sources = [(command, 0, len(command), False, {})]
    while sources:
        text, begin, limit, heredoc_body, line_index = sources.pop()
        contexts = [_ShellContext(expansions_only=heredoc_body, literal_text=heredoc_body)]
        index = begin
        while index < limit:
            context = contexts[-1]
            char = text[index]
            if context.closing and not context.quote and text.startswith(context.closing, index):
                context.finish_word()
                in_pattern = bool(context.case_states and context.case_states[-1] == "pattern")
                if not in_pattern:
                    if (words := context.finish_command()) and not context.arithmetic:
                        yield words
                    index += len(context.closing)
                    contexts.pop()
                    continue
            if context.arithmetic and char == ')' and not context.quote:
                # A lone ')' makes the tentative '((' a nested subshell, not
                # arithmetic: ((command) | command) and $((command) | command).
                if words := context.finish_command():
                    yield words
                context.arithmetic = False
                context.closing = ')'
                index += 1
                continue
            if context.quote == "'":
                context.word.append(char)
                index += 1
                if char == "'":
                    context.quote = ""
                continue
            if text.startswith("$'", index) and not context.quote and not context.literal_text:
                end = index + 2
                literal: list[str] = []
                while end < limit and text[end] != "'":
                    if text[end] == '\\' and end + 1 < limit:
                        escaped = text[end + 1]
                        numeric = re.match(r'\\(?:x([0-9a-fA-F]{1,2})|u([0-9a-fA-F]{1,4})|U([0-9a-fA-F]{1,8})|([0-7]{1,3}))', text[end: end + 10])
                        if numeric:
                            value = next(group for group in numeric.groups() if group is not None)
                            code = int(value, 8 if numeric.group(4) is not None else 16)
                            literal.append(chr(code) if code <= 0x10ffff else '')
                            end += len(numeric.group())
                            continue
                        literal.append({'a': '\a', 'b': '\b', 'e': '\x1b', 'E': '\x1b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v', '\\': '\\', "'": "'", '"': '"'}.get(escaped, '\\' + escaped))
                        end += 2
                    else:
                        literal.append(text[end])
                        end += 1
                context.word.append(shlex.quote(''.join(literal)))
                index = min(end + 1, limit)
                continue
            if char == "\\" and index + 1 < limit:
                if text[index + 1] != "\n":
                    context.word.append(text[index:index + 2])
                index += 2
                continue
            if text.startswith("$((", index):
                context.word.append("$()")
                contexts.append(_ShellContext(closing="))", arithmetic=True))
                index += 3
                continue
            if (text.startswith("$(", index)
                    or (not context.quote and not context.literal_text and text[index:index + 2] in {"<(", ">("})):
                context.word.append("$()")
                contexts.append(_ShellContext(closing=")"))
                index += 2
                continue
            if char == "`":
                context.word.append("$()")
                end = index + 1
                while end < limit and text[end] != '`':
                    end += 2 if text[end] == '\\' and end + 1 < limit else 1
                body = re.sub(r'\\([$`\\])', r'\1', text[index + 1:end])
                sources.append((body, 0, len(body), False, {}))
                index = min(end + 1, limit)
                continue
            if context.literal_text:
                index += 1
                continue
            if char == '"' or (char == "'" and not context.quote):
                context.quote = "" if context.quote == char else char
                context.word.append(char)
                index += 1
                continue
            if context.quote:
                context.word.append(char)
                index += 1
                continue
            if char == "#" and not context.word and not context.expansions_only:
                newline = text.find("\n", index, limit)
                index = limit if newline < 0 else newline
                continue
            if char in " \t\r\n":
                context.finish_word()
                index += 1
                if char == "\n":
                    words = context.finish_command()
                    if words and not context.arithmetic:
                        yield words
                    for delimiter, literal, strip_tabs, shell_stdin in context.heredocs:
                        if not line_index:
                            for match in re.finditer(r'[^\n]*\n|[^\n]+$', text):
                                line = match.group().rstrip('\r\n')
                                for strip in (False, True):
                                    key = (line.lstrip('\t') if strip else line, strip)
                                    line_index.setdefault(key, []).append((match.start(), match.end()))
                        candidates = line_index.get((delimiter, strip_tabs), [])
                        next_line = bisect_left(candidates, (index,))
                        end, after = candidates[next_line] if next_line < len(candidates) else (limit, limit)
                        end, after = min(end, limit), min(after, limit)
                        if not literal or shell_stdin:
                            sources.append((text, index, end, not shell_stdin, line_index))
                        index = after
                    context.heredocs.clear()
                continue
            if char in "<>&;|()":
                redirect_fd = ''
                if char in "<>" and "".join(context.word).isdigit():
                    redirect_fd = ''.join(context.word)
                    context.word.clear()  # Adjacent digits name the redirection's file descriptor.
                context.finish_word()
                in_pattern = bool(context.case_states and context.case_states[-1] == "pattern")
                operator = next((op for op in (";;&", "<<-", "<<<", "&&", "||", ";;", ";&", "<<", ">>", "<&", ">&", "<>", ">|", "&>")
                                 if text.startswith(op, index)), char)
                if in_pattern:
                    if operator == ")":
                        context.case_states[-1] = "body"
                    index += len(operator)
                    continue
                if operator in {"<", ">", "<<", "<<-", "<<<", ">>", "<&", ">&", "<>", ">|", "&>"}:
                    context.redirect = operator
                    context.redirect_fd = redirect_fd
                else:
                    if (words := context.finish_command()) and not context.arithmetic:
                        yield words
                    if operator in {";;", ";&", ";;&"} and context.case_states:
                        context.case_states[-1] = "pattern"
                    if operator == "(":
                        arithmetic = text.startswith("((", index)
                        contexts.append(_ShellContext(closing="))" if arithmetic else ")",
                                                      arithmetic=arithmetic,
                                                      expansions_only=context.arithmetic or context.expansions_only))
                        if arithmetic:
                            index += 1
                index += len(operator)
                continue
            context.word.append(char)
            index += 1
        for context in reversed(contexts):
            if (words := context.finish_command()) and not context.arithmetic:
                yield words


def shell_input_mode(name: str, args: list[str]) -> str:
    """Identify literal command, stdin or file input using this shell's options."""
    if name not in SHELL_COMMANDS:
        return ''
    index = 0
    stdin = False
    while index < len(args):
        arg = args[index]
        lowered = arg.lower()
        if name in {'powershell', 'powershell.exe', 'pwsh', 'pwsh.exe'}:
            if lowered in {'-command', '-c', '-encodedcommand', '-enc', '-e', '-commandwithargs'}:
                return 'command'
            if lowered in {'-file', '-f'} or not arg.startswith('-'):
                return 'file'
            index += 2 if lowered in {'-executionpolicy', '-ep', '-inputformat', '-outputformat', '-windowstyle', '-workingdirectory', '-configurationname', '-version'} else 1
        elif name in {'cmd', 'cmd.exe'}:
            if lowered in {'/c', '/k'}:
                return 'command'
            if not arg.startswith('/'):
                return 'file'
            index += 1
        else:
            if arg == '--' or (name == 'bash' and arg == '-'):
                return 'stdin' if stdin or index + 1 == len(args) else 'file'
            if not arg.startswith(('-', '+')) or arg == '-':
                return 'stdin' if stdin else 'file'
            if arg.startswith('--'):
                index += 2 if arg in {'--rcfile', '--init-file'} else 1
                continue
            if arg.startswith('-') and 'c' in arg[1:]:
                return 'command'
            stdin = stdin or (arg.startswith('-') and 's' in arg[1:])
            index += 2 if arg[-1:] in {'o', 'O'} else 1
    return 'stdin'


def hook_command_vectors(command: str) -> Iterable[list[str]]:
    """Inspect command operands of known launchers without treating data as code."""
    pending = [command]
    while pending:
        for words in _shell_command_vectors(pending.pop()):
            name, args = effective_argv(words)
            yield [name, *args]
            if name == 'find':
                index = 0
                while index < len(args):
                    if args[index] in {'-exec', '-execdir', '-ok', '-okdir'}:
                        end = index + 1
                        while end < len(args):
                            if args[end] == ';' or (args[index] in {'-exec', '-execdir'}
                                                    and args[end] == '+' and args[end - 1] == '{}'):
                                break
                            end += 1
                        pending.append(shlex.join(args[index + 1:end]))
                        index = end
                    index += 1
            elif name == 'eval':
                pending.append(' '.join(args[1:] if args[:1] == ['--'] else args))
            elif name == 'trap':
                handler = args[1:] if args[:1] == ['--'] else args
                if len(handler) > 1 and handler[0] and not handler[0].startswith('-'):
                    pending.append(handler[0])
            elif name == 'ssh':
                index = 0
                while index < len(args) and args[index].startswith('-'):
                    option = args[index]
                    if option == '--':
                        index += 1
                        break
                    index += 2 if option in {'-B', '-b', '-c', '-D', '-E', '-e', '-F', '-I', '-i', '-J', '-L', '-l', '-m', '-O', '-o', '-p', '-Q', '-R', '-S', '-W', '-w'} else 1
                # ssh joins the remote command operands and the remote shell parses them.
                if index + 1 < len(args):
                    pending.append(' '.join(args[index + 1:]))
            elif name in CONTAINER_COMMANDS and args[:1] in (['run'], ['exec']):
                index = 1
                entrypoint = ''
                while index < len(args) and args[index].startswith('-'):
                    option = args[index]
                    if option == '--':
                        index += 1
                        break
                    if args[0] == 'run':
                        if option.startswith('--entrypoint='):
                            entrypoint = option.partition('=')[2]
                        elif option == '--entrypoint' and index + 1 < len(args):
                            entrypoint = args[index + 1]
                    index += 2 if option in {'-a', '--attach', '--cap-add', '--cap-drop', '--entrypoint', '-e', '--env', '--env-file', '--gpus', '--hostname', '-h', '--mount', '--name', '--network', '-p', '--publish', '--platform', '--runtime', '--security-opt', '-u', '--user', '-v', '--volume', '-w', '--workdir'} else 1
                inner = ([entrypoint] if entrypoint else []) + args[index + 1:]
                if inner:
                    pending.append(shlex.join(inner))


def is_shell_command(command: str) -> bool:
    return any(words[0] in {'curl', 'wget'} or shell_input_mode(words[0], words[1:]) == 'command'
               for words in hook_command_vectors(command))


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
        if has_inline_interpreter_args(words[0], words[1:]):
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


def effective_argv(words: list[str], *, stdin_receiver: bool = False) -> tuple[str, list[str]]:
    """Resolve literal argv, stopping at xargs when identifying the input receiver."""
    wrapper_values = {
        'exec': {'-a'}, 'nohup': set(),
        'sudo': {'-u', '--user', '-g', '--group', '-h', '--host', '-p', '--prompt', '-C', '--close-from', '-T', '--command-timeout', '-R', '--chroot', '-D', '--chdir'},
        'timeout': {'-k', '--kill-after', '-s', '--signal'},
        'nice': {'-n', '--adjustment'}, 'command': set(),
        'time': {'-f', '--format', '-o', '--output'},
        'stdbuf': {'-i', '--input', '-o', '--output', '-e', '--error'},
        'xargs': {'-a', '--arg-file', '-d', '--delimiter', '-E', '-I', '-L', '-n', '--max-args', '-P', '--max-procs', '-s', '--max-chars'},
        'watch': {'-n', '--interval'},
        'doas': {'-u', '-C'}, 'setsid': set(),
        'flock': {'-E', '--conflict-exit-code', '-w', '--timeout'},
        'env': {'-u', '--unset', '-C', '--chdir', '--argv0'},
    }
    start = 0
    while start < len(words):
        command = command_family(command_name(words[start]))
        index = start + 1
        if stdin_receiver and command == 'xargs':
            # xargs reads input into child arguments, not child shell source.
            return command, words[index:]
        if command == 'busybox':
            if index < len(words) and not words[index].startswith('-'):
                start = index
                continue
            return command, words[index:]
        if command not in wrapper_values:
            return command, words[index:]
        watch_exec = False
        while index < len(words):
            arg = words[index]
            if arg == '--':
                index += 1
                break
            if arg == '-' and command == 'env':
                index += 1
                continue
            if arg == '-' or not arg.startswith('-'):
                break
            if command == 'command' and arg in {'-v', '-V'}:
                return command, words[start + 1:]
            if command == 'env' and (arg in {'-S', '--split-string'} or arg.startswith('--split-string=')):
                split = arg.partition('=')[2] if '=' in arg else (words[index + 1] if index + 1 < len(words) else '')
                consumed = 1 if '=' in arg else 2
                words = words[:index] + command_words(split) + words[index + consumed:]
                continue
            if command == 'flock' and arg in {'-c', '--command'}:
                return 'sh', ['-c', *words[index + 1:]]
            if command == 'watch':
                if arg in {'-h', '--help', '-v', '--version'}:
                    return command, words[start + 1:]
                if arg == '--exec':
                    watch_exec = True
                if not arg.startswith('--'):
                    consumes_next = False
                    for offset, option in enumerate(arg[1:], start=1):
                        if option in {'h', 'v'}:
                            return command, words[start + 1:]
                        if option == 'x':
                            watch_exec = True
                        if option == 'n':
                            consumes_next = offset == len(arg) - 1
                            break
                    index += 2 if consumes_next else 1
                    continue
            index += 2 if arg in wrapper_values[command] else 1
        if command in {'timeout', 'flock'}:
            index += 1  # Consume the duration or lock file/file descriptor.
        if command == 'flock' and index < len(words) and words[index] in {'-c', '--command'}:
            return 'sh', ['-c', *words[index + 1:]]
        if command in {'env', 'sudo'}:
            while index < len(words) and re.match(r'[A-Za-z_]\w*=', words[index]):
                index += 1
        if command == 'watch' and not watch_exec and index < len(words):
            # watch's default mode reconstructs a shell command; -x executes argv.
            return 'sh', ['-c', ' '.join(words[index:])]
        if index >= len(words):
            return command, words[start + 1:]
        start = index
    return '', []


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
