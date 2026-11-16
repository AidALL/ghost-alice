"""What the installer registers in the host: hooks, managed rule blocks and command wrappers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import ntpath
import os
import re
import shlex
import sys
from typing import TYPE_CHECKING

import hook_definitions
import ledger_access
from installer import locations, report, runner, source

if TYPE_CHECKING:
    from installer.cli import Context

CODEX_BOOTSTRAP_MARKER = "# Ghost-ALICE Codex Bootstrap"
CODEX_MANAGED_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->"
CODEX_MANAGED_BLOCK_END = "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"
CLAUDE_BOOTSTRAP_MARKER = "# Ghost-ALICE Claude Bootstrap"
CLAUDE_MANAGED_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: claude-bootstrap -->"
CLAUDE_MANAGED_BLOCK_END = "<!-- Ghost-ALICE managed block end: claude-bootstrap -->"
HOOK_ACTION_LABEL = " + ".join(hook_definitions.CORE_HOOK_IDS)
GLOBAL_RULE_BLOCKS = "_shared/global_rule_blocks.py"


@dataclass(frozen=True)
class WriterObservation:
    """A host/CLI process observation, not authority to stop a process or move its records."""

    state: str
    detail: str = ''
    pids: tuple[int, ...] = ()


def _process_argv(pid: int, platform: str) -> list[str]:
    """Read actual argument boundaries; ps's displayed command loses spaces and quoting."""
    if platform == 'linux':
        raw = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
        return [os.fsdecode(arg) for arg in raw.rstrip(b'\0').split(b'\0')] if raw else []
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)  # Darwin CTL_KERN, KERN_PROCARGS2.
    size = ctypes.c_size_t()
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), 'Process argument size is unavailable')
    buffer = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), 'Process arguments are unavailable')
    raw = buffer.raw[:size.value]
    count = int.from_bytes(raw[:4], sys.byteorder)
    arguments = raw[raw.index(b'\0', 4) + 1:].lstrip(b'\0').split(b'\0')[:count]
    if count < 1 or len(arguments) != count:
        raise ValueError('Process arguments are incomplete')
    return [os.fsdecode(arg) for arg in arguments]


def observe_writers(platforms, *, platform: str | None = None) -> dict[str, WriterObservation]:
    """Observe supported host processes and surviving host ledger/hook CLIs.

    The OS inventory covers the current POSIX user or Windows session. Missing CLI identity remains
    unknown. This snapshot neither tracks process history nor proves that no writer can start after
    the observation; the caller owns the common waiting policy.
    """
    selected = tuple(dict.fromkeys(platforms))
    if not selected:
        return {}
    platform = sys.platform if platform is None else platform
    try:
        if platform == 'win32':
            command = ('$session = (Get-Process -Id $PID).SessionId; '
                       'Get-CimInstance Win32_Process | Where-Object {$_.SessionId -eq $session} | '
                       'Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress')
            result = runner.run_command(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], capture=True)
            if not result.ok:
                raise ValueError(result.stderr or 'Windows process discovery failed')
            rows = json.loads(result.stdout or '')
            rows = [rows] if isinstance(rows, dict) else rows
            processes = [(int(row['ProcessId']),
                          row.get('ExecutablePath') or row['Name'], row.get('CommandLine')) for row in rows]
        elif platform in ('darwin', 'linux'):
            result = runner.run_command(['ps', '-ww', '-U', str(os.getuid()), '-o', 'pid=,comm='], capture=True)
            if not result.ok:
                raise ValueError(result.stderr or 'POSIX process discovery failed')
            processes = []
            for line in (result.stdout or '').splitlines():
                pid, executable = line.strip().split(None, 1)
                processes.append((int(pid), executable, None))
        else:
            raise ValueError(f'Process observation is unavailable on {platform}')
        if not processes:
            raise ValueError('The process inventory is empty')
        if any(not isinstance(executable, str) or not executable for _, executable, _ in processes):
            raise ValueError('A process executable identity is unavailable')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {host: WriterObservation('unknown', str(exc)) for host in selected}

    running = {host: set() for host in selected}
    unknown = []
    for pid, executable, _ in processes:
        name = ntpath.basename(executable).lower().removesuffix('.exe')
        for host in hook_definitions.SUPPORTED_PLATFORMS:
            if name == host or name.startswith((host + ' helper', host + ' (')):
                if host in running:
                    running[host].add(pid)
    for pid, executable, command in processes:
        name = ntpath.basename(executable).lower().removesuffix('.exe')
        if not re.fullmatch(r'python(?:w|3(?:\.[0-9]+)?)?|node', name):
            continue
        try:
            argv = ([arg.strip('"') for arg in shlex.split(command, posix=False)]
                    if platform == 'win32' and command else _process_argv(pid, platform)
                    if platform != 'win32' else [])
            if not argv:
                raise ValueError('Process arguments are unavailable')
        except (FileNotFoundError, ProcessLookupError):
            continue  # This process exited between the inventory and its argument read.
        except (OSError, ValueError) as exc:
            unknown.append(f'PID {pid}: {exc}')
            continue
        args = argv[1:]
        if name != 'node':
            # -W/-X consume the rest of their option group, or the next argument.
            # -c/-m start a different execution form rather than a script path.
            while args and args[0].startswith('-'):
                option = args[0]
                argument_option = re.search('[cmWX]', option[1:]) if not option.startswith('--') else None
                if option == '-' or (argument_option and argument_option.group() in ('c', 'm')):
                    break
                separate_value = (option == '--check-hash-based-pycs' or
                                  (argument_option and argument_option.end() == len(option) - 1))
                args = args[2:] if separate_value else args[1:]
                if option == '--':
                    break
        if not args:
            continue
        script = args[0].replace('\\', '/')
        if name == 'node' and ntpath.basename(script) in hook_definitions.SUPPORTED_PLATFORMS:
            host = ntpath.basename(script)
        elif script.endswith('/@openai/codex/bin/codex.js'):
            host = 'codex'
        elif script.endswith('/@anthropic-ai/claude-code/cli.js'):
            host = 'claude'
        elif ntpath.basename(script) == 'hook_profile_gate.py':
            parsed = hook_definitions.parse_hook_runner_command(shlex.join([argv[0], *args]))
            host = (parsed or {}).get('platform')
        elif ntpath.basename(script) in (ledger_access.LEDGER_PATH.name, 'session_intent_analyzer_hook.py',
                                       'session_check_cache.py', 'claude_stop_verification_hook.py',
                                       'pending_merge_precheck_hook.py', 'io_trace_hook.py',
                                       'response_route.py', 'context_lifecycle.py',
                                       'completion_assessment.py', 'source_lookup.py'):
            # A parent app does not establish the CLI's environment-bound or default record host.
            host = next((arg.split('=', 1)[1] for arg in args[1:] if arg.startswith('--platform=')), None)
            if '--platform' in args:
                index = args.index('--platform') + 1
                host = args[index] if index < len(args) else None
        else:
            continue
        if host in running:
            running[host].add(pid)
        elif host not in hook_definitions.SUPPORTED_PLATFORMS:
            unknown.append(f'PID {pid}: the ledger CLI host binding is unavailable')
    return {host: WriterObservation('running', 'Observed host or ledger CLI processes', tuple(sorted(pids))) if pids
            else WriterObservation('unknown', f'The {host} host adapter is unavailable') if host not in hook_definitions.SUPPORTED_PLATFORMS
            else WriterObservation('unknown', '; '.join(unknown)) if unknown
            else WriterObservation('absent', 'No host or participating ledger CLI process was observed')
            for host, pids in running.items()}


def confirm_project_trust(platforms: list[str]) -> str:
    """Ask once in the interactive parent before changing an existing Codex untrusted project."""
    if 'codex' not in platforms:
        return ''
    from install_hooks import _default_destination, _codex_project_trust_state_in_content, _read_text_file
    project = str(Path(_default_destination('codex', project_root=source.resolve_source().root).project_root).resolve())
    config = locations.codex_home() / 'config.toml'
    if _codex_project_trust_state_in_content(_read_text_file(config), project) != 'untrusted':
        return ''
    report.info(f'Codex project {project} is untrusted in {config}.')
    if source.interactive():
        try:
            if input('Change this project to trusted? [y/N]: ').strip().lower() in ('y', 'yes'):
                return project
        except EOFError:
            pass
    report.info('The installer will keep the untrusted project setting.')
    return ''


def codex_bootstrap_source() -> Path:
    return runner.REPO_ROOT / "platforms" / "codex" / "AGENTS.md"


def codex_loader_source() -> Path:
    return runner.REPO_ROOT / "platforms" / "codex" / "bootstrap.md"


def claude_bootstrap_source() -> Path:
    return runner.REPO_ROOT / "platforms" / "claude" / "CLAUDE.md"


def install_hooks(ctx: "Context", action: str, platform: str, *, backup_sources=None):
    """`install_hooks.py --core-source-root --platform --hook-shared-dir --skills-dir
    [--visibility] [--uninstall|--status] [--addon-source ...]`; install
    defaults visibility to `dynamic`; status and doctor pass no visibility."""
    report.emit("")
    report.info(f"Agent hook {action}... ({HOOK_ACTION_LABEL})")
    from install_hooks import HookStatus, STATUS_LABELS
    def failed(message):
        report.error(message)
        return HookStatus(platform, 'HOOK_ACTION_ERROR', STATUS_LABELS['error'], 'error',
                          {'action': action, 'items': [], 'errors': [message]})
    hook_py = runner.REPO_ROOT / "_shared" / "install_hooks.py"
    if not hook_py.is_file():
        return failed(f"install_hooks.py not found: {hook_py}")
    if action == "uninstall":
        from install_hooks import uninstall_hook
        return uninstall_hook(platform, backup_sources=backup_sources)
    args = ["--platform", platform,
            "--hook-shared-dir", str(locations.runtime_shared_dir()), "--skills-dir", str(ctx.skills_dir)]
    if action == "install":
        args.extend(["--core-source-root", str(runner.REPO_ROOT), "--visibility", ctx.options.visibility or "dynamic"])
    elif action == "status":
        args.append("--status")
    for addon_source in ctx.addon_sources:
        args.extend(["--addon-source", str(addon_source)])
    result = runner.run_tool('_shared/install_hooks.py', *args, '--format', 'json', capture=True,
                             env={'GHOST_ALICE_PROJECT_ROOT': str(source.resolve_source().root)})
    if result.stderr:
        report.emit(result.stderr.rstrip('\n'))
    try:
        payload = json.loads(result.stdout or '')
        row = payload['results'][platform]
        status = HookStatus(**row)
        allowed = {'install': {'installed', 'already', 'skipped', 'error'},
                   'status': {'installed', 'missing', 'skipped', 'error'}}[action]
        if (status.platform != platform or status.legacy_status not in allowed
                or not isinstance(status.details, dict)
                or (action != 'status' and status.details.get('action') != action)
                or not isinstance(status.status_token, str) or not status.status_token
                or not isinstance(status.status_label, str)
                or not isinstance(status.details.get('items', []), list)
                or not isinstance(status.details.get('errors', []), list)
                or type(payload['returncode']) is not int
                or payload['returncode'] != result.returncode
                or (result.returncode == 0) != (status.legacy_status != 'error')):
            raise ValueError('inconsistent hook result')
    except (ValueError, KeyError, TypeError) as error:
        return failed(f'Hook {action} returned an invalid result: {error}')
    return status


def _merge_block(args: list[str], failure: str, proposed_prefix: str, updated: str) -> bool:
    result = runner.run_tool(GLOBAL_RULE_BLOCKS, *args, capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip("\n"))
    if not result.ok:
        report.error(failure)
        return False
    output = (result.stdout or "").strip()
    if output.startswith("proposed:"):
        report.warn(f"{proposed_prefix}: {output[len('proposed:'):]}")
    else:
        report.ok(updated)
    return True


def ensure_claude_bootstrap(ctx: "Context") -> bool:
    """`global_rule_blocks.py claude-merge --source --dest --proposed`; `proposed:` → warning."""
    claude_home = locations.claude_home()
    rules = claude_home / "CLAUDE.md"
    claude_home.mkdir(parents=True, exist_ok=True)
    return _merge_block(["claude-merge", "--source", str(claude_bootstrap_source()),
                         "--dest", str(rules), "--proposed", f"{rules}.ghost-alice-proposed"],
                        "Claude CLAUDE.md block merge failed; aborting install",
                        "Claude CLAUDE.md has no Ghost-ALICE block; proposed file available",
                        "Claude CLAUDE.md bootstrap block updated")


def ensure_codex_bootstrap(ctx: "Context") -> bool:
    """`global_rule_blocks.py codex-merge --source --loader --dest --proposed`."""
    codex_home = locations.codex_home()
    agents = codex_home / "AGENTS.md"
    codex_home.mkdir(parents=True, exist_ok=True)
    return _merge_block(["codex-merge", "--source", str(codex_bootstrap_source()),
                         "--loader", str(codex_loader_source()),
                         "--dest", str(agents), "--proposed", f"{agents}.ghost-alice-proposed"],
                        "Codex AGENTS.md block merge failed; aborting install",
                        "Codex AGENTS.md has no Ghost-ALICE block; proposed file available",
                        "Codex AGENTS.md bootstrap block updated")


def _remove_block(args: list[str], failure: str, removed: str, updated: str, require_change: bool) -> bool:
    result = runner.run_tool(GLOBAL_RULE_BLOCKS, *args, capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip("\n"))
    if not result.ok:
        report.warn(failure)
        return False
    output = (result.stdout or "").strip()
    if output.startswith("removed:"):
        report.ok(removed)
        return True
    if output.startswith("updated:"):
        report.ok(updated)
        return True
    return not require_change


def remove_claude_bootstrap(ctx: "Context", require_change: bool = False) -> bool:
    """`global_rule_blocks.py claude-remove --dest`; result prefixes decide the message."""
    rules = locations.claude_home() / "CLAUDE.md"
    if not rules.is_file():
        return not require_change
    return _remove_block(["claude-remove", "--dest", str(rules)], "Claude CLAUDE.md block removal failed",
                         "Claude CLAUDE.md bootstrap removed",
                         "Claude CLAUDE.md Ghost-ALICE block removed; content outside the block preserved", require_change)


def remove_claude_bootstrap_if_unused(ctx: "Context", skills_root: Path) -> bool:
    from installer import selection

    if selection.has_managed_installs(skills_root, ctx.platform):
        return False
    return remove_claude_bootstrap(ctx, True)


def remove_codex_bootstrap_if_unused(ctx: "Context", skills_root: Path) -> bool:
    from installer import selection

    if selection.has_managed_installs(skills_root, ctx.platform):
        return False
    codex_home = locations.codex_home()
    agents = codex_home / "AGENTS.md"
    if not agents.is_file() and not (codex_home / "ghost-alice-governance.md").is_file():
        return False
    return _remove_block(["codex-remove", "--dest", str(agents)], "Codex AGENTS.md block removal failed",
                         "Codex AGENTS.md bootstrap removed",
                         "Codex AGENTS.md Ghost-ALICE block removed; content outside the block preserved", True)


def sync_commands(ctx: "Context", mode: str = "sync", target: str = "all") -> int:
    """`commands_sync.py <catalog> <checkout .claude/commands> <mode> <target>`; a missing catalog is a warning."""
    catalog = runner.REPO_ROOT / "skill-catalog" / "skills.json"
    if not catalog.is_file():
        report.warn(f"skills.json not found: {catalog}")
        return 1
    claude_dir = runner.REPO_ROOT / ".claude" / "commands"
    return runner.run_tool("_shared/commands_sync.py", str(catalog), str(claude_dir), mode, target).returncode
