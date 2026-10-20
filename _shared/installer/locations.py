"""Host locations the installer reads and writes.

Port of `installer_lib/platform.sh:23-80`. `install_hooks.py` keeps its own
copy of the home rules until both move into the host adapters.
"""

from __future__ import annotations

from pathlib import Path
import os

PLATFORMS = ("claude", "codex")


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
    """`claude` → `<claude_home>/skills`; `codex` → `~/.agents/skills` (`install.sh:74, 455-457`)."""
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
    """`$GHOST_ALICE_GHOST_ROOT` or `~/.ghost-alice` (`targets.sh:373`)."""
    value = os.environ.get("GHOST_ALICE_GHOST_ROOT")
    return Path(value) if value else home() / ".ghost-alice"


def install_state_path(platform: str) -> Path:
    """`~/.ghost-alice/install-state/<platform>.json`."""
    return home() / ".ghost-alice" / "install-state" / f"{platform}.json"


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


def install_log_root() -> Path:
    """`~/.ghost-alice/install`."""
    return home() / ".ghost-alice" / "install"


def record_root() -> Path:
    """`~/.ghost-alice` under HOME: the folder of the install lock, the install record
    (`installed-payload.json`) and the publish rollbacks (`rollbacks/<txid>/`). It does not
    follow `GHOST_ALICE_GHOST_ROOT`, so the record stays next to the lock its writers hold."""
    return home() / ".ghost-alice"


def install_lock_path() -> Path:
    """`~/.ghost-alice/install.lock`."""
    return record_root() / "install.lock"


def addon_source_cache_root() -> Path:
    """`$GHOST_ALICE_ADDON_SOURCE_CACHE_DIR` or `~/.ghost-alice/addon-source-cache` (`targets.sh:71`)."""
    value = os.environ.get("GHOST_ALICE_ADDON_SOURCE_CACHE_DIR")
    return Path(value) if value else home() / ".ghost-alice" / "addon-source-cache"


def deprecated_skill_backup_root() -> Path:
    """`~/.ghost-alice/deprecated-skill-backups`."""
    return home() / ".ghost-alice" / "deprecated-skill-backups"


def prefers_copy_install(platform: str) -> bool:
    """Codex installs copies so its user edits can be protected; Claude links (`platform.sh:43-57`)."""
    return platform == "codex"


def detect_present_platforms() -> list[str]:
    """`claude` when its home exists, then `codex` (`platform.sh:63-66`)."""
    found = []
    if claude_home().is_dir():
        found.append("claude")
    if codex_home().is_dir():
        found.append("codex")
    return found


def detect_uninstall_platforms() -> list[str]:
    """Present homes, then platforms with an install-state file, each once, in that order (`platform.sh:68-80`)."""
    found = detect_present_platforms()
    for platform in PLATFORMS:
        if platform not in found and install_state_path(platform).is_file():
            found.append(platform)
    return found


def environment_overrides() -> dict[str, str]:
    """The environment variables this module reads, with their current values (for reports and tests)."""
    return {k: v for k, v in os.environ.items()
            if k in ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_RUNTIME_SHARED_DIR",
                     "GHOST_ALICE_GHOST_ROOT", "GHOST_ALICE_ADDON_SOURCE_CACHE_DIR")}
