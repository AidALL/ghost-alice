"""Host locations the installer reads and writes.

`install_hooks.py` keeps its own copy of the home rules.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import shlex
import subprocess

from hook_definitions import SUPPORTED_PLATFORMS as PLATFORMS


# Package entry point for each installed management command.
MANAGEMENT_COMMANDS = {'ghost-alice': 'install.sh', 'ghost-alice.ps1': 'install.ps1', 'ghost-alice.cmd': 'install.cmd'}


def command_line(*words: object) -> str:
    """The command that users type on this operating system."""
    return subprocess.list2cmdline(map(str, words)) if os.name == 'nt' else shlex.join(map(str, words))


def management_command(*arguments: str) -> str:
    """The installed command, with `arguments`, that users invoke for installation and updates."""
    directory = record_root() / 'bin'
    if os.name == 'nt':
        return _powershell_command(directory / 'ghost-alice.ps1', *arguments)
    return command_line(directory / 'ghost-alice', *arguments)


def package_script(package: Path) -> Path:
    """The installer script executed by the supported shell on this platform."""
    return package / ('install.ps1' if os.name == 'nt' else 'install.sh')


def package_command(package: Path, *arguments: str) -> str:
    """The command that starts the installer of `package` with `arguments`."""
    script = package_script(package)
    if os.name == 'nt':
        return _powershell_command(script, *arguments)
    return command_line('bash', script, *arguments)


def _powershell_command(script: Path, *arguments: str) -> str:
    return command_line('pwsh', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script, *arguments)


def home() -> Path:
    """`$HOME`, else the account home."""
    value = os.environ.get("HOME")
    return Path(value) if value else Path.home()


def claude_home() -> Path:
    """`$CLAUDE_CONFIG_DIR` or `~/.claude`."""
    value = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(value) if value else home() / ".claude"


def codex_home() -> Path:
    """`$CODEX_HOME` or `~/.codex`."""
    value = os.environ.get("CODEX_HOME")
    return Path(value) if value else home() / ".codex"


def shared_skills_dir() -> Path:
    """`~/.agents/skills`: the Codex skill root."""
    return home() / ".agents" / "skills"


def skills_dir(platform: str) -> Path:
    """`claude` → `<claude_home>/skills`; `codex` → `~/.agents/skills`."""
    if platform == "claude":
        return claude_home() / "skills"
    if platform == "codex":
        return shared_skills_dir()
    raise ValueError(f"unknown platform: {platform}")


def runtime_shared_dir() -> Path:
    """`$GHOST_ALICE_RUNTIME_SHARED_DIR` or `~/.ghost-alice/runtime/current/_shared`."""
    value = os.environ.get("GHOST_ALICE_RUNTIME_SHARED_DIR")
    return Path(value) if value else home() / ".ghost-alice" / "runtime" / "current" / "_shared"


def runtime_root() -> Path:
    """The folder of the runtime shared dir (`~/.ghost-alice/runtime/current`): the runtime `_shared` and the
    companion scripts the context gateway runs from are published under it."""
    return runtime_shared_dir().parent


def ghost_root() -> Path:
    """`$GHOST_ALICE_GHOST_ROOT` or `~/.ghost-alice`."""
    value = os.environ.get("GHOST_ALICE_GHOST_ROOT")
    return Path(value) if value else home() / ".ghost-alice"


def install_state_dir() -> Path:
    """`~/.ghost-alice/install-state`: the hosts' install-state manifests and the records next to them."""
    return home() / ".ghost-alice" / "install-state"


def install_state_path(platform: str) -> Path:
    """`~/.ghost-alice/install-state/<platform>.json`."""
    return install_state_dir() / f"{platform}.json"


def install_state_manifests(directory: Path) -> list[Path]:
    """Inventory candidates for this and other host adapters, excluding installer sidecars."""
    sidecars = {'source-repo-hook-path.json', 'codex-hook-feature-change.json', 'codex-project-trust-change.json'}
    sidecars.update(f'{host}-registry-health.json' for host in PLATFORMS)
    found = []
    for path in sorted(Path(directory).glob('*.json')):
        if path.name in sidecars:
            continue
        if path.stem not in PLATFORMS and path.is_file() and not path.is_symlink():
            # Keep inventories from another adapter, and unresolved files that may still represent a host.
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
            else:
                if not isinstance(data, dict) or ('targets' not in data and data.get('platform') != path.stem):
                    continue
        found.append(path)
    return found


def events_log(platform: str) -> Path:
    """`~/.ghost-alice/install-state/<platform>-events.jsonl`."""
    return home() / ".ghost-alice" / "install-state" / f"{platform}-events.jsonl"


def pending_root(platform: str) -> Path:
    """`~/.ghost-alice/pending-merges/<platform>`."""
    return home() / ".ghost-alice" / "pending-merges" / platform


def addons_dir(platform: str) -> Path:
    """`~/.ghost-alice/addons/<platform>`."""
    return home() / ".ghost-alice" / "addons" / platform


def resources_dir(platform: str) -> Path:
    """`~/.ghost-alice/resources/<platform>`."""
    return home() / ".ghost-alice" / "resources" / platform


def claude_commands_dir() -> Path:
    """`<claude_home>/commands`."""
    return claude_home() / "commands"


def install_log_root(base: Path | None = None) -> Path:
    """`~/.ghost-alice/install`, under `base` instead of the home when one is given."""
    return (Path(base) if base is not None else home()) / ".ghost-alice" / "install"


def record_root() -> Path:
    """`~/.ghost-alice` under HOME: the folder of the install lock, the install record
    (current detail and legacy incompatibility notice) and the publish rollbacks (`rollbacks/<txid>/`). It does not
    follow `GHOST_ALICE_GHOST_ROOT`, so the record stays next to the lock its writers hold."""
    return home() / ".ghost-alice"


def git_hooks_dir() -> Path:
    """`~/.ghost-alice/git-hooks`: the folder the registered repository's `core.hooksPath` names."""
    return record_root() / "git-hooks"


def install_lock_path() -> Path:
    """`~/.ghost-alice/install.lock`."""
    return record_root() / "install.lock"


def ledger_root() -> Path:
    """The fixed record location owned by the installation beside its lock."""
    return record_root().resolve() / 'session-intent'


def addon_source_cache_root() -> Path:
    """`$GHOST_ALICE_ADDON_SOURCE_CACHE_DIR` or `~/.ghost-alice/addon-source-cache`."""
    value = os.environ.get("GHOST_ALICE_ADDON_SOURCE_CACHE_DIR")
    return Path(value) if value else home() / ".ghost-alice" / "addon-source-cache"


def deprecated_skill_backup_root() -> Path:
    """`~/.ghost-alice/deprecated-skill-backups`."""
    return home() / ".ghost-alice" / "deprecated-skill-backups"


def prefers_copy_install(platform: str) -> bool:
    """Codex installs copies so external changes can be protected; the installer does not record their writer. Claude links."""
    return platform == "codex"


def detect_present_platforms() -> list[str]:
    """`claude` when its home exists, then `codex`."""
    found = []
    if claude_home().is_dir():
        found.append("claude")
    if codex_home().is_dir():
        found.append("codex")
    return found


def detect_uninstall_platforms() -> list[str]:
    """Present homes, then platforms with an install-state file, each once, in that order."""
    found = detect_present_platforms()
    for platform in PLATFORMS:
        if platform not in found and install_state_path(platform).is_file():
            found.append(platform)
    return found
