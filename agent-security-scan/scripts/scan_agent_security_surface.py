#!/usr/bin/env python3
"""Static scanner for Ghost-ALICE security surfaces.

Dependencies: Python 3.11+ standard library and bundled Tomli 2.4.0.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
import json
import re
import shlex
import codex_config_toml as tomllib
from dataclasses import dataclass, field, replace
from itertools import chain, islice
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
POSIX_SHELLS = {'sh', 'bash', 'zsh', 'dash', 'ash', 'hush', 'ksh', 'mksh'}
SHELL_COMMANDS = POSIX_SHELLS | {'csh', 'tcsh', 'fish', 'cmd', 'cmd.exe', 'powershell', 'powershell.exe', 'pwsh', 'pwsh.exe'}
DIRECT_RUN_PACKAGE_MANAGERS = {"bunx", "npx", "pipx", "uvx"}
MCP_PACKAGE_ACTIONS = (
    ('pnpm', 'dlx'), ('yarn', 'dlx'), ('npm', 'install'), ('npm', 'i'), ('npm', 'exec'),
    ('pip', 'install'), ('cargo', 'install'), ('go', 'install'),
)
MCP_PACKAGE_MANAGER_RE = re.compile(
    r"(?i)\b(?:" + '|'.join(
        r'\s+'.join(map(re.escape, prefix))
        for prefix in chain(((name,) for name in sorted(DIRECT_RUN_PACKAGE_MANAGERS - {'npx'})), MCP_PACKAGE_ACTIONS)
    ) + r")\b"
)
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


@dataclass(frozen=True)
class ArgvBuffer:
    """One token allocation, with indexes shared by all nested argv views."""

    values: tuple[str, ...]
    reparses: tuple[int, ...]
    semicolons: tuple[int, ...]
    exec_ends: tuple[int, ...]

    @classmethod
    def create(cls, words: Iterable[str]) -> 'ArgvRange':
        values = tuple(words)
        totals, semicolons, ends = [0], [], []
        reserved = {'if', 'then', 'elif', 'else', 'fi', 'for', 'select', 'while', 'until',
                    'do', 'done', 'case', 'in', 'esac', 'function', 'coproc', 'time'}
        for index, value in enumerate(values):
            simple = re.fullmatch(r'[A-Za-z0-9_./:+,@%-]+', value) is not None and value not in reserved
            totals.append(totals[-1] + (not simple))
            if value == ';':
                semicolons.append(index)
                ends.append(index)
            elif value == '+' and index and values[index - 1] == '{}':
                ends.append(index)
        buffer = cls(values, tuple(totals), tuple(semicolons), tuple(ends))
        return ArgvRange(buffer, 0, len(values))


@dataclass(frozen=True)
class ArgvRange:
    buffer: ArgvBuffer
    start: int
    stop: int

    def __post_init__(self) -> None:
        if not 0 <= self.start <= self.stop <= len(self.buffer.values):
            raise ValueError('invalid argv range')

    def __len__(self) -> int:
        return self.stop - self.start

    def __iter__(self):
        for index in range(self.start, self.stop):
            yield self.buffer.values[index]

    def __getitem__(self, index: int) -> str:
        if not isinstance(index, int) or not 0 <= index < len(self):
            raise IndexError(index)
        return self.buffer.values[self.start + index]

    def drop(self, count: int) -> 'ArgvRange':
        if not 0 <= count <= len(self):
            raise ValueError(count)
        return ArgvRange(self.buffer, self.start + count, self.stop)

    def take(self, count: int) -> 'ArgvRange':
        if not 0 <= count <= len(self):
            raise ValueError(count)
        return ArgvRange(self.buffer, self.start, self.start + count)

    def needs_shell_parse(self) -> bool:
        return self.buffer.reparses[self.stop] != self.buffer.reparses[self.start]

    def find_end(self, start: int, *, allow_plus: bool) -> int:
        ends = self.buffer.exec_ends if allow_plus else self.buffer.semicolons
        index = bisect_left(ends, self.start + start)
        return min(ends[index], self.stop) - self.start if index < len(ends) else len(self)


@dataclass(frozen=True)
class CommandVector:
    name: str
    args: ArgvRange
    input_mode: str = ''
    program_operand: str = ''
    stdin_forwarded: bool = True
    stopped: bool = False
    conditions: frozenset[str] = frozenset()
    git_ssh_command: str | None = None


@dataclass(frozen=True)
class OptionSpec:
    short_values: str = ''
    long_values: frozenset[str] = frozenset()
    nonexecuting: frozenset[str] = frozenset({'--help', '--version'})
    fixed_operands: int = 0
    extra_operands: tuple[tuple[str, int], ...] = ()


def option_token(args: ArgvRange, index: int, spec: OptionSpec):
    """Consume one option, including short clusters and attached operands."""
    arg = args[index]
    if arg.startswith('--'):
        key, equals, value = arg.partition('=')
        if key in spec.long_values and not equals:
            extra = next((count for option, count in spec.extra_operands if option == key), 0)
            return min(index + 2 + extra, len(args)), ((key, args[index + 1] if index + 1 < len(args) else ''),)
        return index + 1, ((key, value if equals else None),)
    options = []
    for offset, character in enumerate(arg[1:], 1):
        key = '-' + character
        if character in spec.short_values:
            if offset + 1 < len(arg):
                options.append((key, arg[offset + 1:]))
                return index + 1, options
            options.append((key, args[index + 1] if index + 1 < len(args) else ''))
            return min(index + 2, len(args)), options
        options.append((key, None))
    return index + 1, options


def consume_options(args: ArgvRange, spec: OptionSpec, start: int = 0):
    index, occurrences, stopped = start, [], False
    while index < len(args):
        arg = args[index]
        if arg == '--':
            return index + 1, occurrences, stopped
        if not arg.startswith('-') or arg == '-':
            break
        index, options = option_token(args, index, spec)
        occurrences.extend(options)
        stopped = stopped or any(key in spec.nonexecuting for key, _ in options)
    return index, occurrences, stopped


WRAPPER_SPECS = {
    'builtin': OptionSpec(), 'exec': OptionSpec('a'), 'nohup': OptionSpec(),
    'sudo': OptionSpec('ughpCTRD', frozenset({'--user', '--group', '--host', '--prompt', '--close-from', '--command-timeout', '--chroot', '--chdir'}), frozenset({'-l', '--list', '-v', '--validate', '-V', '--version', '--help'})),
    'timeout': OptionSpec('ks', frozenset({'--kill-after', '--signal'}), fixed_operands=1),
    'nice': OptionSpec('n', frozenset({'--adjustment'})),
    'command': OptionSpec(nonexecuting=frozenset({'-v', '-V'})),
    'time': OptionSpec('fo', frozenset({'--format', '--output'})),
    'stdbuf': OptionSpec('ioe', frozenset({'--input', '--output', '--error'})),
    'xargs': OptionSpec('adEIJLlnPRsS', frozenset({'--arg-file', '--delimiter', '--max-lines', '--max-args', '--max-procs', '--max-chars'})),
    'watch': OptionSpec('n', frozenset({'--interval'}), frozenset({'-h', '--help', '-v', '--version'})),
    'doas': OptionSpec('uC', nonexecuting=frozenset({'-C', '-L'})), 'setsid': OptionSpec(),
    'flock': OptionSpec('Ew', frozenset({'--conflict-exit-code', '--timeout'}), fixed_operands=1),
    'env': OptionSpec('uCaPS', frozenset({'--unset', '--chdir', '--argv0', '--split-string'})),
    'ionice': OptionSpec('cnpPuU', frozenset({'--class', '--classdata', '--pid', '--pgid', '--uid'})),
    'caffeinate': OptionSpec('tw'), 'arch': OptionSpec(),
    'script': OptionSpec('t', fixed_operands=1), 'taskpolicy': OptionSpec('dtcl'),
    'sandbox-exec': OptionSpec('fnpD'), 'chroot': OptionSpec(long_values=frozenset({'--userspec', '--groups'}), fixed_operands=1),
    'unshare': OptionSpec(long_values=frozenset({'--map-user', '--map-group', '--root', '--wd'})),
    'nsenter': OptionSpec('t', frozenset({'--target', '--root', '--wd'})),
    'runuser': OptionSpec('ugG', frozenset({'--user', '--group', '--supp-group'})),
    'taskset': OptionSpec(fixed_operands=1), 'chrt': OptionSpec(fixed_operands=1),
    'strace': OptionSpec('eopPs', frozenset({'--trace', '--output', '--attach', '--string-limit'})),
    'systemd-run': OptionSpec('pE', frozenset({'--property', '--unit', '--description', '--uid', '--gid', '--setenv', '--working-directory'})),
    'screen': OptionSpec('Sct'),
    'wsl': OptionSpec('du', frozenset({'--distribution', '--user', '--cd'})),
    'npm': OptionSpec('pc', frozenset({'--package', '--call'})),
    'uv': OptionSpec('p', frozenset({'--python', '--project', '--directory', '--with'})),
    'poetry': OptionSpec(), 'conda': OptionSpec('np', frozenset({'--name', '--prefix', '--cwd'})),
    'direnv': OptionSpec(fixed_operands=1), 'mise': OptionSpec(), 'bundle': OptionSpec(),
}


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
class _TextSource:
    text: str
    begin: int = 0
    limit: int | None = None
    line_index: dict = field(default_factory=dict)


@dataclass
class _StaticInput:
    source: _TextSource | None = None
    producer: '_ParsedCommand | None' = None


@dataclass
class _ParsedCommand:
    argv: ArgvRange
    stdin: _StaticInput | None
    stdout_redirected: bool
    processes: dict[str, _StaticInput]
    selected: CommandVector | None = None


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
    coproc_header: bool = False
    arithmetic: bool = False
    at_start: bool = True
    redirect: str = ""
    redirect_fd: str = ""
    stdin: _StaticInput | None = None
    pipe_stdin: _StaticInput | None = None
    stdout_redirected: bool = False
    last_command: _ParsedCommand | None = None
    output_complete: bool = True
    output_to: _StaticInput | None = None
    processes: dict[str, _StaticInput] = field(default_factory=dict)
    heredocs: list[tuple[str, bool, bool, _StaticInput]] = field(default_factory=list)

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
            source = _StaticInput()
            if self.redirect in {"<<", "<<-"}:
                self.heredocs.append((decoded[0], any(c in raw for c in "'\"\\"), self.redirect == "<<-", source))
            elif self.redirect == '<<<' and '$' not in decoded[0] and '`' not in decoded[0]:
                source.source = _TextSource(decoded[0] + '\n')
            elif self.redirect == '<' and decoded[0] in self.processes:
                source = self.processes[decoded[0]]
            if stdin_redirect:
                self.stdin = source  # The final fd 0 redirect replaces earlier input.
            if '>' in self.redirect and self.redirect_fd in {'', '1'}:
                self.stdout_redirected = True
            self.redirect = ""
            return
        if self.coproc_header and raw == '{' and len(self.words) == 1:
            self.words.clear()
            self.coproc_header = False
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
            if raw in {"if", "then", "elif", "else", "while", "until", "do", "!", "{", "fi", "done", "}"}:
                return
            if raw == 'coproc':
                self.coproc_header = True
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
            if re.match(r"[A-Za-z_]\w*\+?=", raw) and not raw.startswith(("GIT_SSH_COMMAND=", "GIT_SSH_COMMAND+=")):
                return
        self.words.extend(decoded)
        self.at_start = False

    def finish_command(self) -> _ParsedCommand | None:
        self.finish_word()
        words, self.words = self.words, []
        node = _ParsedCommand(ArgvBuffer.create(words), self.stdin or self.pipe_stdin,
                              self.stdout_redirected, self.processes) if words else None
        if node is not None:
            if self.last_command is not None and self.pipe_stdin is None:
                self.output_complete = False
            self.last_command = node
            self.pipe_stdin = None
        self.stdin = None
        self.stdout_redirected = False
        self.at_start = True
        self.redirect = ""
        self.function_header = False
        self.coproc_header = False
        return node


def _shell_command_vectors(command: str | _TextSource) -> Iterable[_ParsedCommand]:
    """Read static command positions, never evaluate expansions or run commands.

    Nested regions keep their own quotes and grammar state. An explicit stack
    makes case-pattern parentheses distinct from region endings without using
    Python recursion. Heredoc bodies are source ranges, so nested or missing
    delimiters do not copy and repeatedly search the same remaining suffix.
    """
    source = command if isinstance(command, _TextSource) else _TextSource(command)
    sources = [(source.text, source.begin, len(source.text) if source.limit is None else source.limit, False, source.line_index)]
    while sources:
        text, begin, limit, heredoc_body, line_index = sources.pop()
        processes: dict[str, _StaticInput] = {}
        contexts = [_ShellContext(expansions_only=heredoc_body, literal_text=heredoc_body, processes=processes)]
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
                    if context.output_to is not None:
                        if context.output_complete:
                            context.output_to.producer = context.last_command
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
                contexts.append(_ShellContext(closing="))", arithmetic=True, processes=processes))
                index += 3
                continue
            if (text.startswith("$(", index)
                    or (not context.quote and not context.literal_text and text[index:index + 2] in {"<(", ">("})):
                output = None
                if text.startswith('<(', index):
                    marker = '\0process:' + str(len(processes))
                    output = processes[marker] = _StaticInput()
                    context.word.append(shlex.quote(marker))
                else:
                    context.word.append("$()")
                contexts.append(_ShellContext(closing=")", output_to=output, processes=processes))
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
                    for delimiter, literal, strip_tabs, stdin_source in context.heredocs:
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
                        stdin_source.source = _TextSource(text, index, end, line_index)
                        if not literal:
                            sources.append((text, index, end, True, line_index))
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
                    words = context.finish_command()
                    if words and not context.arithmetic:
                        yield words
                    if operator == '|' and words:
                        context.pipe_stdin = _StaticInput(producer=words)
                    if operator in {";;", ";&", ";;&"} and context.case_states:
                        context.case_states[-1] = "pattern"
                    if operator == "(":
                        context.output_complete = False
                        arithmetic = text.startswith("((", index)
                        contexts.append(_ShellContext(closing="))" if arithmetic else ")",
                                                      arithmetic=arithmetic,
                                                      processes=processes,
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


# Ordered MatchSwitch contracts from PowerShell v7.6.0. The legacy Windows
# host shares these documented switches but treats -Version as a value option.
POWERSHELL_COMMAND_OPTIONS = (
    ('command', 'c', 'command'), ('windowstyle', 'w', 'value'), ('file', 'f', 'file'),
    ('outputformat', 'o', 'value'), ('of', 'o', 'value'),
    ('inputformat', 'inp', 'value'), ('if', 'if', 'value'),
    ('executionpolicy', 'ex', 'value'), ('ep', 'ep', 'value'),
    ('encodedcommand', 'e', 'encoded'), ('ec', 'e', 'encoded'),
    ('encodedarguments', 'encodeda', 'value'), ('ea', 'ea', 'value'),
)
POWERSHELL_OPTIONS = (
    ('version', 'v', 'value'), ('help', 'h', 'stop'), ('?', '?', 'stop'),
    ('noexit', 'noe', 'flag'), ('noprofile', 'nop', 'flag'),
    ('nologo', 'nol', 'flag'), ('noninteractive', 'noni', 'flag'),
    ('configurationname', 'config', 'value'),
    *POWERSHELL_COMMAND_OPTIONS,
    ('sta', 'sta', 'flag'), ('mta', 'mta', 'flag'),
)
PWSH_OPTIONS = (
    ('version', 'v', 'stop'), ('help', 'h', 'stop'), ('?', '?', 'stop'),
    ('login', 'l', 'flag'), ('noexit', 'noe', 'flag'), ('noprofile', 'nop', 'flag'),
    ('nologo', 'nol', 'flag'), ('noninteractive', 'noni', 'flag'),
    ('socketservermode', 'so', 'stop'), ('servermode', 's', 'stop'),
    ('namedpipeservermode', 'nam', 'stop'), ('sshservermode', 'sshs', 'stop'),
    ('noprofileloadtime', 'noprofileloadtime', 'flag'), ('interactive', 'i', 'flag'),
    ('configurationfile', 'configurationfile', 'value'), ('configurationname', 'config', 'value'),
    ('custompipename', 'cus', 'value'),
    ('commandwithargs', 'commandwithargs', 'command-args'), ('cwa', 'cwa', 'command-args'),
    *POWERSHELL_COMMAND_OPTIONS,
    ('settingsfile', 'settings', 'value'), ('sta', 'sta', 'flag'), ('mta', 'mta', 'flag'),
    ('workingdirectory', 'wo', 'value'), ('wd', 'wd', 'value'),
)


def shell_program(name: str, args: ArgvRange) -> tuple[str, str, bool, frozenset[str]]:
    """Select the program input and startup conditions without evaluating it."""
    if name not in SHELL_COMMANDS:
        return '', '', False, frozenset()
    index, stdin, encoded = 0, False, None
    while index < len(args):
        arg = args[index]
        if name in {'powershell', 'powershell.exe', 'pwsh', 'pwsh.exe'}:
            modern = name.startswith('pwsh')
            if not arg.startswith(('-', '/')):
                return 'file', arg, False, frozenset()
            key = arg[2:] if arg.startswith('--') else arg[1:]
            key = key.lower()
            mode = next((mode for full, minimum, mode in (PWSH_OPTIONS if modern else POWERSHELL_OPTIONS)
                         if len(key) >= len(minimum) and full.startswith(key)), None)
            if mode is None:
                return 'file', arg, False, frozenset()
            if mode == 'stop':
                return 'none', '', True, frozenset()
            if mode in {'command', 'command-args', 'file', 'encoded'}:
                operand = args[index + 1] if index + 1 < len(args) else ''
                if mode == 'encoded':
                    encoded = operand
                    index += 2
                    continue
                conditions = frozenset({CWA_CONDITION}) if mode == 'command-args' else frozenset()
                selected_mode = 'command' if mode == 'command-args' else mode
                return ('stdin' if operand == '-' else selected_mode), operand, False, conditions
            index += 2 if mode == 'value' else 1
        elif name in {'cmd', 'cmd.exe'}:
            if arg.lower() in {'/c', '/k'}:
                return 'command', args[index + 1] if index + 1 < len(args) else '', False, frozenset()
            if not arg.startswith('/'):
                return 'file', arg, False, frozenset()
            index += 1
        else:
            if arg in {'--', '-'}:
                return ('stdin', '', False, frozenset()) if stdin or index + 1 == len(args) else ('file', args[index + 1], False, frozenset())
            if not arg.startswith(('-', '+')):
                return ('stdin', '', False, frozenset()) if stdin else ('file', arg, False, frozenset())
            if arg.startswith('--'):
                if name == 'fish' and arg == '--command':
                    return 'command', args[index + 1] if index + 1 < len(args) else '', False, frozenset()
                if name == 'bash' and (arg in {'--help', '--version', '--dump-po-strings', '--dump-strings'} or arg not in {
                    '--debug', '--debugger', '--init-file', '--login', '--noediting', '--noprofile',
                    '--norc', '--posix', '--pretty-print', '--rcfile', '--restricted', '--verbose',
                }):
                    return 'none', '', True, frozenset()
                index += 2 if arg in {'--rcfile', '--init-file'} else 1
                continue
            # -o/-O consume their value; c in that value is never an option.
            for offset, option in enumerate(arg[1:], 1):
                if option in {'o', 'O'} and name in POSIX_SHELLS:
                    index += 1 if offset + 1 == len(arg) else 0
                    break
                if arg.startswith('-') and option == 'c':
                    return 'command', args[index + 1] if index + 1 < len(args) else '', False, frozenset()
                if arg.startswith('-') and option == 's':
                    stdin = True
            index += 1
    return ('command', encoded, False, frozenset()) if encoded is not None else ('stdin', '', False, frozenset())


SSH_OPTIONS = OptionSpec('BbcDEeFIiJLlmOo pQRSWw'.replace(' ', ''), nonexecuting=frozenset({'-G', '-N', '-O', '-Q', '-W'}))
CONTAINER_GLOBAL_OPTIONS = OptionSpec('Hcfp', frozenset({'--host', '--context', '--config', '--file', '--project-name', '--project-directory', '--env-file', '--namespace', '--address'}))
CONTAINER_EXEC_OPTIONS = OptionSpec('euw', frozenset({'--env', '--env-file', '--user', '--workdir', '--detach-keys'}))
CONTAINER_RUN_OPTIONS = OptionSpec('aehpuvwlmc', frozenset({
    '--attach', '--cap-add', '--cap-drop', '--entrypoint', '--env', '--env-file', '--gpus',
    '--hostname', '--mount', '--name', '--network', '--publish', '--platform', '--runtime',
    '--security-opt', '--user', '--volume', '--workdir', '--memory', '--pull', '--label',
    '--cpus', '--cpu-shares', '--restart', '--ulimit', '--add-host', '--tmpfs', '--pid', '--shm-size',
}))
CONTAINER_ENTRYPOINT_CONDITION = 'container image or service entrypoint must execute the supplied command argv'
KUBECTL_ARGS_CONDITION = 'image entrypoint must execute the supplied args (kubectl run without --command)'
GIT_SSH_CONDITION = 'Git remote must use SSH transport'
SSH_STDIN_CONDITION = 'remote default shell must read the supplied stdin program'
CWA_CONDITION = 'CommandWithArgs requires PowerShell 7.4 experimental support or 7.5-preview.5 and later'


def pflag_enabled(occurrences, *aliases, default=False):
    """pflag Boolean aliases share one value; the last occurrence sets it."""
    for name, value in reversed(occurrences):
        if name in aliases:
            return value is None or value in {'1', 't', 'T', 'TRUE', 'true', 'True'}
    return default


def container_command(name: str, args: ArgvRange):
    if name not in CONTAINER_COMMANDS | {'docker-compose', 'nerdctl'}:
        return None
    index, _, stopped = consume_options(args, CONTAINER_GLOBAL_OPTIONS)
    if index >= len(args) or stopped:
        return args.take(0), False, '', stopped
    compose = name == 'docker-compose' or args[index] == 'compose'
    if args[index] in {'container', 'compose'}:
        index, _, stopped = consume_options(args, CONTAINER_GLOBAL_OPTIONS, index + 1)
    if stopped or index >= len(args) or args[index] not in {'exec', 'run'}:
        return args.take(0), False, '', stopped
    action = args[index]
    index, options, stopped = consume_options(args, CONTAINER_RUN_OPTIONS if action == 'run' else CONTAINER_EXEC_OPTIONS, index + 1)
    if stopped or index >= len(args):
        return args.take(0), False, '', stopped
    inner = args.drop(index + 1)
    entrypoint = next((value for key, value in reversed(options) if key == '--entrypoint'), None)
    if entrypoint:
        inner = ArgvBuffer.create(chain((entrypoint,), inner))
    condition = CONTAINER_ENTRYPOINT_CONDITION if action == 'run' and not entrypoint else ''
    interactive = pflag_enabled(options, '-i', '--interactive', default=compose)
    detached = pflag_enabled(options, '-d', '--detach')
    if name == 'nerdctl' and not compose and action == 'exec' and detached and interactive:
        return args.take(0), False, '', True
    if detached and (name in {'docker', 'docker-compose'} or name == 'podman' and not compose):
        interactive = False
    return inner, interactive, condition, False


def shell_text(words: ArgvRange):
    # A sufficient fast path: joining these words changes no shell token or
    # command boundary. Anything requiring interpretation uses a new source.
    return ' '.join(words) if words.needs_shell_parse() else words


def rsync_shell_words(text: str) -> Iterable[str]:
    """rsync -e uses spaces, matching quotes and doubled quotes, not escapes."""
    index = 0
    while index < len(text):
        if text[index] == ' ':
            index += 1
            continue
        word, quote = [], ''
        while index < len(text) and (quote or text[index] != ' '):
            character = text[index]
            index += 1
            if quote and character == quote:
                if index < len(text) and text[index] == quote:
                    word.append(quote)
                    index += 1
                else:
                    quote = ''
            elif not quote and character in {'"', "'"}:
                quote = character
            else:
                word.append(character)
        if quote:
            raise ValueError('unclosed rsync remote-shell quote')
        yield ''.join(word)


def static_output(source: _StaticInput | None) -> _TextSource | None:
    """Follow literal producers only; do not evaluate a program or expansion."""
    while source is not None:
        if source.source is not None:
            return source.source
        node = source.producer
        if node is None or node.stdout_redirected:
            return None
        selected = node.selected
        if selected is None or selected.stopped or not selected.stdin_forwarded:
            return None
        name, args = selected.name, selected.args
        if name == 'cat' and not args:
            source = node.stdin
            continue
        if any('$' in word or '`' in word or '\0' in word for word in args):
            return None
        if name == 'echo' and (not args or not args[0].startswith('-')):
            return _TextSource(' '.join(args) + '\n')
        if name == 'printf' and args and args[0] in {'%s', '%s\\n', '%s\n'}:
            separator = '' if args[0] == '%s' else '\n'
            return _TextSource(''.join(value + separator for value in args.drop(1)))
        return None
    return None


def program_source(node: _ParsedCommand):
    """Select explicit POSIX programs or known fd 0/process program sources."""
    selected = node.selected
    if selected is None or selected.stopped:
        return None
    if selected.name in SHELL_COMMANDS - POSIX_SHELLS:
        return None
    mode, operand = selected.input_mode, selected.program_operand
    if mode == 'command' and selected.name in POSIX_SHELLS:
        return _TextSource(operand)
    stdin = node.stdin if selected.stdin_forwarded else None
    source = None
    if mode == 'stdin':
        source = stdin
    elif mode == 'file':
        source = stdin if operand == '/dev/stdin' else node.processes.get(operand)
    return static_output(source)


def command_vectors(command: str | ArgvRange | _ParsedCommand) -> Iterable[CommandVector]:
    """Traverse argv views and actual shell-text boundaries on an explicit stack."""
    pending = [(command, None, {}, frozenset())]
    while pending:
        payload, stdin, processes, conditions = pending.pop()
        # Finish lexical source links before following heredocs and pipes.
        if isinstance(payload, _ParsedCommand):
            nodes = [payload]
        elif isinstance(payload, ArgvRange):
            nodes = [_ParsedCommand(payload, stdin, False, processes)]
        else:
            nodes = list(_shell_command_vectors(payload))
        for node in nodes:
            if node.stdin is None:
                node.stdin = stdin
            words = node.argv
            selected = None
            for stage in effective_argv(words):
                if selected is not None:
                    yield replace(selected, conditions=conditions) if conditions else selected
                selected = stage
            name, args = selected.name, selected.args
            stdin_forwarded, stopped = selected.stdin_forwarded, selected.stopped
            mode, operand, shell_stopped, shell_conditions = shell_program(name, args) if not stopped else ('', '', False, frozenset())
            stopped = stopped or shell_stopped
            current_conditions = conditions | shell_conditions

            def enqueue(child, *, forward_stdin=True, extra=frozenset()):
                pending.append((child, node.stdin if stdin_forwarded and forward_stdin else None,
                                node.processes, current_conditions | extra))

            if stopped:
                node.selected = replace(selected, input_mode=mode, program_operand=operand, stopped=True, conditions=current_conditions)
                continue
            if name in {'source', '.'}:
                mode, operand = 'file', args[0] if args else ''
            if name == 'find':
                index = 0
                while index < len(args):
                    if args[index] in {'-exec', '-execdir', '-ok', '-okdir'}:
                        end = args.find_end(index + 1, allow_plus=args[index] in {'-exec', '-execdir'})
                        enqueue(args.drop(index + 1).take(end - index - 1))
                        index = end
                    index += 1
            elif name in {'fd', 'fdfind'}:
                for index, arg in enumerate(args):
                    if arg in {'-x', '--exec', '-X', '--exec-batch'}:
                        enqueue(args.drop(index + 1))
                        break
            elif name == 'eval':
                inner = args.drop(1) if args and args[0] == '--' else args
                if inner:
                    enqueue(shell_text(inner))
            elif name == 'trap':
                handler = args.drop(1) if args and args[0] == '--' else args
                if len(handler) > 1 and handler[0] and not handler[0].startswith('-'):
                    enqueue(handler[0])
            elif name == 'ssh':
                index, occurrences, stopped = consume_options(args, SSH_OPTIONS)
                stdin_forwarded = stdin_forwarded and '-n' not in dict(occurrences)
                if not stopped:
                    if index + 1 < len(args):
                        enqueue(shell_text(args.drop(index + 1)))
                    elif index + 1 == len(args):
                        mode = 'stdin'
                        current_conditions = current_conditions | {SSH_STDIN_CONDITION}
            elif (container := container_command(name, args)) is not None:
                inner, interactive, condition, stopped = container
                if inner:
                    enqueue(inner, forward_stdin=interactive, extra=frozenset({condition}) if condition else frozenset())
            elif name in {'kubectl', 'oc'}:
                global_spec = OptionSpec('ns', frozenset({'--namespace', '--context', '--kubeconfig', '--server', '--token'}))
                index, _, stopped = consume_options(args, global_spec)
                if not stopped and index < len(args) and args[index] in {'exec', 'run', 'debug'}:
                    action = args[index]
                    offset, options, stopped = index + 1, [], False
                    spec = OptionSpec('nscf', frozenset({'--namespace', '--server', '--container', '--filename', '--image', '--context', '--copy-to', '--target', '--profile', '--env', '--overrides'}))
                    while offset < len(args):
                        if args[offset] == '--':
                            command_flag = pflag_enabled(options, '--command')
                            extra = frozenset({KUBECTL_ARGS_CONDITION}) if action == 'run' and not command_flag else frozenset()
                            if action == 'debug' and pflag_enabled(options, '--arguments-only'):
                                extra = frozenset({'image entrypoint must execute the supplied args (kubectl debug --arguments-only)'})
                            if not stopped:
                                enqueue(args.drop(offset + 1), forward_stdin=pflag_enabled(options, '-i', '--stdin'), extra=extra)
                            break
                        if args[offset].startswith('-'):
                            offset, parsed = option_token(args, offset, spec)
                            options.extend(parsed)
                            stopped = stopped or any(key in {'-h', '--help', '--version'} for key, _ in parsed)
                        else:
                            offset += 1  # target/name operand before the explicit command delimiter
            elif name == 'parallel':
                index, _, stopped = consume_options(args, OptionSpec('jNSI', frozenset({'--jobs', '--max-args', '--sshlogin', '--replace'})))
                end = next((i for i in range(index, len(args)) if args[i] in {':::', '::::', ':::+', '::::+'}), len(args))
                if not stopped and end > index:
                    enqueue(shell_text(args.drop(index).take(end - index)), forward_stdin=False)
            elif name in {'su', 'nix-shell'}:
                # These launchers accept command options after other operands.
                # Only the last explicit command operand supplies shell text.
                index, command_value = 0, None
                if name == 'su':
                    spec = OptionSpec('cs', frozenset({'--command', '--session-command', '--shell'}))
                    command_options = {'-c', '--command', '--session-command'}
                else:
                    spec = OptionSpec('Ip', frozenset({'--run', '--command', '--arg', '--argstr'}), extra_operands=(('--arg', 1), ('--argstr', 1)))
                    command_options = {'--run', '--command'}
                while index < len(args):
                    if args[index].startswith('-') and args[index] != '-':
                        index, options = option_token(args, index, spec)
                        stopped = stopped or any(key in spec.nonexecuting for key, _ in options)
                        for key, value in options:
                            if key in command_options:
                                command_value = value
                    else:
                        index += 1
                if command_value is not None and not stopped:
                    enqueue(command_value)
            elif name == 'tmux':
                index, _, stopped = consume_options(args, OptionSpec('LSf'))
                if not stopped and index < len(args) and args[index] in {'new-session', 'new', 'new-window', 'split-window'}:
                    index, _, stopped = consume_options(args, OptionSpec('stnxycefF'), index + 1)
                    inner = args.drop(index)
                    if inner and not stopped:
                        enqueue(inner[0] if len(inner) == 1 else inner, forward_stdin=False)
            elif name == 'rsync':
                index, options, stopped = consume_options(args, OptionSpec('efB', frozenset({'--rsh', '--filter', '--block-size'})))
                remote_shell = next((value for key, value in reversed(options) if key in {'-e', '--rsh'}), None)
                if not stopped and remote_shell and any(':' in arg and not arg.startswith('/') for arg in args.drop(index)):
                    # rsync splits its remote-shell argv itself; ';' and '|'
                    # remain ordinary arguments, not local shell operators.
                    try:
                        enqueue(ArgvBuffer.create(rsync_shell_words(remote_shell)), forward_stdin=False)
                    except ValueError:
                        pass
            elif name == 'git':
                index, options, stopped = consume_options(args, OptionSpec('cC', frozenset({'--git-dir', '--work-tree', '--namespace', '--exec-path', '--config-env'})))
                if not stopped and index < len(args) and args[index] in {'fetch', 'push', 'pull', 'clone', 'ls-remote', 'submodule'}:
                    ssh_command = selected.git_ssh_command
                    if ssh_command is None:
                        for key, value in options:
                            if key == '-c' and value and value.lower().startswith('core.sshcommand='):
                                ssh_command = value.partition('=')[2]
                    if ssh_command:
                        enqueue(ssh_command, forward_stdin=False, extra=frozenset({GIT_SSH_CONDITION}))
            node.selected = replace(selected, input_mode=mode, program_operand=operand, stopped=stopped, conditions=current_conditions)
        # Every producer in this source has a selected result before delivery.
        for node in nodes:
            yield node.selected
            source = program_source(node)
            if source is not None:
                pending.append((source, None, {}, node.selected.conditions))


def analyze_hook_command(command: str):
    shell, inline, profiled, conditions = False, False, False, set()
    for vector in command_vectors(command):
        program = interpreter_program(vector.name, vector.args)
        is_shell = not vector.stopped and (vector.name in {'curl', 'wget'} or vector.input_mode == 'command')
        is_inline = not vector.stopped and program[0] == 'command'
        shell = shell or is_shell
        inline = inline or is_inline
        profiled = profiled or (not vector.stopped and is_profiled(vector, program))
        if is_shell or is_inline:
            conditions.update(vector.conditions)
    return shell, inline, profiled, sorted(conditions)


def is_profiled(vector: CommandVector, program: tuple[str, str, ArgvRange | None]) -> bool:
    if vector.name in {'profile-gate', 'hook_profile_gate.py', 'ghost-alice-hook.mjs'}:
        return True
    scripts = {'python': 'hook_profile_gate.py', 'node': 'ghost-alice-hook.mjs'}
    return program[0] == 'file' and command_name(program[1]) == scripts.get(vector.name)


def has_ghost_alice_marker(command: str) -> bool:
    return any(marker in command for marker in GHOST_ALICE_HOOK_MARKERS)


def has_secret_like_value(value: Any) -> bool:
    return any(pattern.search(text) for text in iter_json_strings(value) for pattern in SECRET_PATTERNS)


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
        shell, inline, profiled, conditions = analyze_hook_command(command)
        details = {'execution_conditions': conditions} if conditions else None
        has_marker = has_ghost_alice_marker(command)
        has_url = URL_RE.search(command) is not None
        if has_marker and not profiled:
            findings.append(
                Finding(
                    severity=SEVERITY_COMMAND,
                    rule="ghost-alice-hook-missing-profile-gate",
                    path=str(path),
                    message="Ghost-ALICE hook marker is present but command is not wrapped by the profile gate",
                    mitigation="mitigate",
                )
            )
        if (not has_marker
                and not (profiled and not has_url)
                and (shell or inline or has_url)):
            findings.append(
                Finding(
                    severity=SEVERITY_COMMAND,
                    rule="unmanaged-hook-command",
                    path=str(path),
                    message="hook command defines a shell or network execution path outside Ghost-ALICE-managed wrappers",
                    mitigation="mitigate",
                    details=details,
                )
            )
        if shell and not profiled:
            findings.append(
                Finding(
                    severity=SEVERITY_COMMAND,
                    rule="unprofiled-shell-command",
                    path=str(path),
                    message="hook command defines a shell execution path without Ghost-ALICE profile gate",
                    mitigation="mitigate",
                    details=details,
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


def command_name(command: Any) -> str:
    if not isinstance(command, str):
        return ""
    if command == '.':
        return command
    if re.match(r'^[A-Za-z]:[\\/]', command) or command.startswith('\\\\'):
        command = command.replace('\\', '/')
    command = command.rsplit('/', 1)[-1].lower()
    if family := re.fullmatch(r"(python|pip)(?:\d+(?:\.\d+)*)?", command):
        return family[1]
    return command


INTERPRETER_SPECS = {
    'python': (
        OptionSpec('cmWX', frozenset({'--check-hash-based-pycs'}), frozenset({'-h', '-?', '-V', '--help', '--help-env', '--help-xoptions', '--help-all', '--version'})),
        {'-c'}, {'-m': 'module'}, frozenset('-' + flag for flag in 'bBdEiIOPqRsSuvx'),
    ),
    'node': (
        OptionSpec('eprC', frozenset({'--eval', '--print', '--require', '--import', '--loader', '--experimental-loader', '--input-type', '--conditions', '--title'}), frozenset({'-c', '--check', '-h', '-v', '--help', '--version'})),
        {'-e', '-p', '--eval', '--print'}, {}, frozenset({'-i', '--interactive', '--no-warnings', '--trace-warnings'}),
    ),
    'ruby': (
        OptionSpec('eIrEFCKX', frozenset({'--encoding', '--external-encoding', '--internal-encoding'}), frozenset({'-h', '--help', '--version', '--copyright'})),
        {'-e'}, {}, frozenset('-' + flag for flag in 'acdlnpsSuvw') | {'--debug', '--verbose', '--disable-gems'},
    ),
    'perl': (
        OptionSpec('eEIMmF', nonexecuting=frozenset({'-h', '-v', '-V', '--help', '--version'})),
        {'-e', '-E'}, {}, frozenset('-' + flag for flag in 'acdflnpsStTuUwWX'),
    ),
    'php': (
        OptionSpec('rcdfBRFEzSt', frozenset({'--run', '--file', '--php-ini', '--define', '--zend-extension'}), frozenset({'-h', '-i', '-m', '-v', '--help', '--version', '--info', '--modules', '--ini'})),
        {'-r', '--run', '-B', '-R', '-E'}, {'-f': 'file', '--file': 'file'}, frozenset({'-a', '-e', '-H', '-l', '-n', '-s', '-w'}),
    ),
    'osascript': (OptionSpec('els'), {'-e'}, {}, frozenset({'-i'})),
}


def interpreter_program(command: str, args: Iterable[str]) -> tuple[str, str, ArgvRange | None]:
    """Select startup code or a script and preserve its remaining argument boundary."""
    if command not in INTERPRETER_SPECS:
        return '', '', None
    spec, inline_options, selectors, flags = INTERPRETER_SPECS[command]
    args = args if isinstance(args, ArgvRange) else ArgvBuffer.create(args)
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--':
            return (('file', args[index + 1], args.drop(index + 2)) if index + 1 < len(args)
                    else ('stdin', '', args.take(0)))
        if arg == '-':
            return 'stdin', '', args.drop(index + 1)
        if not arg.startswith('-'):
            return 'file', arg, args.drop(index + 1)
        if (command == 'ruby' and re.fullmatch(r'-W(?:[012]|:(?:no-)?(?:deprecated|experimental|performance))?', arg)
                or command == 'perl' and re.fullmatch(r'-dt?:.+', arg)):
            # These optional startup operands are attached and consume no following word.
            index += 1
            continue
        index, options = option_token(args, index, spec)
        for option, value in options:
            if option in spec.nonexecuting:
                return 'none', '', None
            if option in inline_options:
                return 'command', value or '', args.drop(index)
            if option in selectors:
                return selectors[option], value or '', args.drop(index)
            if option not in flags and option not in spec.long_values and option[1:] not in spec.short_values:
                # An unregistered operand arity cannot establish a script position.
                return 'unknown', '', None
    return 'stdin', '', args.take(0)


def has_inline_interpreter_args(command: str, args: Iterable[str]) -> bool:
    return interpreter_program(command, args)[0] == 'command'


def mcp_command_node(command: Any, args: Any) -> _ParsedCommand:
    # A recognized leading launcher supports legacy command-plus-argument text.
    # Otherwise the native command remains one argv item, including its spaces.
    command_parts: list[str] = []
    if isinstance(command, str):
        command_parts = command_words(command)
        if len(command_parts) > 1:
            first_name = command_name(command_parts[0])
            known_launcher = first_name in (
                SHELL_COMMANDS | {'env'} | set(INTERPRETER_SPECS)
                | DIRECT_RUN_PACKAGE_MANAGERS | CONTAINER_COMMANDS
            )
            if not known_launcher and not server_has_mcp_auto_install(first_name, command_parts[1:]):
                command_parts = [command]
    argv = ArgvBuffer.create([*(command_parts or [""]), *normalized_arg_list(args)])
    return _ParsedCommand(argv, None, False, {})


def effective_argv(words: Iterable[str]) -> Iterable[CommandVector]:
    """Yield each invocation once; only split-string boundaries allocate tokens."""
    words = words if isinstance(words, ArgvRange) else ArgvBuffer.create(words)
    stdin_forwarded = True
    git_ssh_command = None
    git_ssh_known = False

    def invocation(command, args, *, stopped=False):
        return CommandVector(command, args, stdin_forwarded=stdin_forwarded, stopped=stopped, git_ssh_command=git_ssh_command)

    while words:
        command = command_name(words[0])
        args = words.drop(1)
        if re.match(r'[A-Za-z_]\w*\+?=', words[0]):
            if words[0].startswith('GIT_SSH_COMMAND='):
                git_ssh_command = words[0].partition('=')[2]
                git_ssh_known = True
            elif words[0].startswith('GIT_SSH_COMMAND+='):
                # Only a known command-local prefix can produce a known append.
                # An unknown inherited value is still present and disables fallback.
                git_ssh_command = git_ssh_command + words[0].partition('=')[2] if git_ssh_known else ''
            words = args
            continue
        if command == 'xargs':
            stdin_forwarded = False
        if command == 'busybox' and args and not args[0].startswith('-'):
            yield invocation(command, args)
            words = args
            continue
        if command not in WRAPPER_SPECS:
            yield invocation(command, args)
            return
        spec = WRAPPER_SPECS[command]
        invocation_args = args
        if command in {'npm', 'uv', 'poetry', 'conda', 'direnv', 'mise', 'bundle'}:
            expected = 'run' if command in {'uv', 'poetry', 'conda'} else 'exec'
            if not args or args[0] != expected:
                yield invocation(command, args)
                return
            args = args.drop(1)
        if command not in {'env', 'flock'}:
            index, options, stopped = consume_options(args, spec)
        else:
            # These options can introduce a new argv or shell-text boundary
            # before the usual first-operand stopping point.
            index, options, stopped = 0, [], False
            while index < len(args):
                arg = args[index]
                if arg == '--':
                    index += 1
                    break
                if arg == '-' and command == 'env':
                    git_ssh_command = None
                    git_ssh_known = False
                    index += 1
                    continue
                if arg == '-' or not arg.startswith('-'):
                    break
                if command == 'flock' and arg in {'-c', '--command'}:
                    yield invocation(command, invocation_args)
                    yield invocation('sh', ArgvBuffer.create(chain(('-c',), args.drop(index + 1))))
                    return
                next_index, parsed = option_token(args, index, spec)
                options.extend(parsed)
                if command == 'env':
                    for key, value in parsed:
                        if key in {'-i', '--ignore-environment'} or (key in {'-u', '--unset'} and value == 'GIT_SSH_COMMAND'):
                            git_ssh_command = None
                            git_ssh_known = False
                stopped = any(key in spec.nonexecuting for key, _ in parsed)
                if stopped:
                    break
                split = next((value for key, value in parsed if command == 'env' and key in {'-S', '--split-string'}), None)
                if split is not None:
                    # env -S splits argv; it never evaluates shell operators.
                    try:
                        split_words = shlex.split(split)
                    except ValueError:
                        yield invocation(command, invocation_args, stopped=True)
                        return
                    args = ArgvBuffer.create(chain(split_words, args.drop(next_index)))
                    index = 0
                    continue
                index = next_index
        if stopped:
            yield invocation(command, invocation_args, stopped=True)
            return
        yield invocation(command, invocation_args)
        index += spec.fixed_operands
        if command == 'flock' and index < len(args) and args[index] in {'-c', '--command'}:
            yield invocation('sh', ArgvBuffer.create(chain(('-c',), args.drop(index + 1))))
            return
        if command in {'env', 'sudo'}:
            # env owns NAME=VALUE operands; '+' in NAME is not a shell append.
            while index < len(args) and ('=' in args[index] if command == 'env' else re.match(r'[A-Za-z_]\w*=', args[index])):
                if args[index].startswith('GIT_SSH_COMMAND='):
                    git_ssh_command = args[index].partition('=')[2]
                    git_ssh_known = True
                index += 1
        if command == 'watch' and not ({'-x', '--exec'} & dict(options).keys()) and index < len(args):
            yield invocation('sh', ArgvBuffer.create(('-c', ' '.join(args.drop(index)))))
            return
        if index >= len(args):
            return
        words = args.drop(index)
    yield invocation('', words)


def mcp_details(server: str, config: dict[str, Any], roots: list[str]) -> dict[str, Any]:
    args = config.get("args")
    env = config.get("env")
    details: dict[str, Any] = {
        "server": server,
        "command": redact_if_sensitive(config.get("command", "")),
        "args": [redact_if_sensitive(item) for item in normalized_arg_list(args)],
        "env_keys": sorted(env.keys()) if isinstance(env, dict) else [],
    }
    if roots:
        details["filesystem_roots"] = roots
    return details


def server_has_mcp_auto_install(command: str, args: ArgvRange | list[str]) -> bool:
    if command in DIRECT_RUN_PACKAGE_MANAGERS:
        return True
    if command == 'python':
        mode, module, tail = interpreter_program(command, args)
        if mode != 'module' or module.lower() != 'pip':
            return False
        command, args = 'pip', tail
    width = max(map(len, MCP_PACKAGE_ACTIONS))
    prefix = (command, *(arg.lower() for arg in islice(args, width - 1)))
    return any(prefix[:len(action)] == action for action in MCP_PACKAGE_ACTIONS)


def resolve_config_path(text: str, home_resolved: Path) -> Path | None:
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


def broad_filesystem_roots(args: ArgvRange, home: Path) -> list[str]:
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
        resolved = resolve_config_path(text, home_resolved)
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
        auto_install = False
        conditions: set[str] = set()
        root = mcp_command_node(raw_config.get("command"), raw_config.get("args"))
        for vector in command_vectors(root):
            if not vector.stopped and server_has_mcp_auto_install(vector.name, vector.args):
                auto_install = True
                conditions.update(vector.conditions)
        selected = root.selected
        command, args = selected.name, selected.args
        if not selected.stopped and command in CONTAINER_COMMANDS and any(arg in CONTAINER_RUN_ACTIONS for arg in args):
            auto_install = True
            conditions.clear()
        roots = [] if selected.stopped else broad_filesystem_roots(args, home)
        details = mcp_details(server, raw_config, roots)
        if auto_install:
            findings.append(
                Finding(
                    severity=SEVERITY_MCP,
                    rule="mcp-auto-install",
                    path=str(path),
                    message="MCP server can auto-install package manager dependencies",
                    mitigation="mitigate",
                    details={**details, 'execution_conditions': sorted(conditions)} if conditions else details,
                )
            )
        if roots:
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
        if not selected.stopped and (command in SHELL_COMMANDS or has_inline_interpreter_args(command, args)):
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
    if has_secret_like_value(text):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan local agent security surfaces.")
    parser.add_argument("--root", default=".", help="repo root to inspect")
    parser.add_argument("--home", default=str(Path.home()), help="HOME-like directory to inspect")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = scan(Path(args.root).expanduser().resolve(), Path(args.home).expanduser().resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
