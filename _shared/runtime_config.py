#!/usr/bin/env python3
"""Runtime preference config for Ghost-ALICE hooks."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tomllib
from collections.abc import Mapping
from pathlib import Path
import atomic_write
import install_destination
import native_transcript
from hook_definitions import SUPPORTED_PLATFORMS
from typing import Any

VALID_AGENT_VISIBILITY_PROFILES = {"strict", "dynamic", "minimal"}
HOOK_NODE_SENTINEL = "__GHOST_ALICE_HOOK_NODE__"
DEFAULT_CONFIG = {
    "schema_version": "ghost-alice-config.v1",
    "agent_visibility": {"profile": "dynamic"},
    "hook_runtime": {"node": {}},
    "strict_session_log": {"mode": "always"},
}


def config_path(home: Path | None = None) -> Path:
    return install_destination.ghost_root(home) / "config.json"


def canonical_agent_visibility_profile(value: str | None) -> str:
    if value is None:
        return "strict"
    profile = str(value).strip().lower().replace("_", "-")
    if profile in VALID_AGENT_VISIBILITY_PROFILES:
        return profile
    return "strict"


def _default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def _normalized_node_registrations(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        platform: path
        for platform, path in value.items()
        if isinstance(platform, str)
        and platform.strip()
        and isinstance(path, str)
        and path.strip()
    }


def is_usable_node_runtime(resolved: Path) -> bool:
    """Return whether an already-resolved Node candidate can be launched."""
    if os.name == "nt":
        return True
    return os.access(resolved, os.X_OK)


def is_node_executable(path: Path) -> bool:
    return path.name.lower() in {"node", "node.exe"}


def _configured_node_runtime_from_value(value: str | None) -> Path | None:
    text = (value or "").strip().strip('"')
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not is_node_executable(candidate) or not candidate.is_file():
        return None
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    return resolved if is_usable_node_runtime(resolved) else None


def _codex_configured_node_runtime_paths(env: dict[str, str]) -> list[Path]:
    if env.get("GHOST_ALICE_PLATFORM_DIR") and env.get("GHOST_ALICE_PLATFORM") == "codex":
        config_file = Path(env["GHOST_ALICE_PLATFORM_DIR"]) / "config.toml"
    else:
        config_file = Path(native_transcript.codex_home(env)).expanduser() / "config.toml"
    try:
        with config_file.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    mcp_servers = data.get("mcp_servers")
    if not isinstance(mcp_servers, Mapping):
        return []
    node_repl = mcp_servers.get("node_repl")
    if not isinstance(node_repl, Mapping):
        return []
    env_config = node_repl.get("env")
    if not isinstance(env_config, Mapping):
        return []
    value = env_config.get("NODE_REPL_NODE_PATH")
    configured = _configured_node_runtime_from_value(value if isinstance(value, str) else None)
    return [configured] if configured is not None else []


def _registered_node_runtime_path(env: dict[str, str], active_platform: str | None) -> Path | None:
    if active_platform not in SUPPORTED_PLATFORMS:
        return None
    config = load_config(env=env, home=install_destination.home_directory(env))
    hook_runtime = config.get("hook_runtime")
    if not isinstance(hook_runtime, dict):
        return None
    registrations = hook_runtime.get("node")
    if not isinstance(registrations, dict):
        return None
    value = registrations.get(active_platform)
    return _configured_node_runtime_from_value(value if isinstance(value, str) else None)


def _path_node_runtime(env: dict[str, str]) -> Path | None:
    for executable in ("node", "node.exe"):
        resolved = shutil.which(executable, path=env.get("PATH"))
        if resolved:
            configured = _configured_node_runtime_from_value(resolved)
            if configured is not None:
                return configured
    return None


def node_runtime_candidates(env: dict[str, str], active_platform: str | None) -> list[Path]:
    """Return configured runtimes and the first usable PATH runtime in launch order.

    The runner separately authorizes exact paths found under either PATH name;
    only the selected PATH runtime supplies same-file trust to other paths.
    """
    candidates = [_configured_node_runtime_from_value(env.get(name))
                  for name in ("GHOST_ALICE_NODE", "NODE_REPL_NODE_PATH")]
    codex_configured = _codex_configured_node_runtime_paths(env)
    if active_platform == "codex":
        candidates.extend(codex_configured)
    candidates.append(_registered_node_runtime_path(env, active_platform))
    candidates.append(_path_node_runtime(env))
    if active_platform != "codex":
        candidates.extend(codex_configured)
    return [candidate for candidate in candidates if candidate is not None]


def _apply_env_overrides(config: dict[str, Any], env: dict[str, str]) -> None:
    profile = env.get("GHOST_ALICE_AGENT_VISIBILITY")
    if profile is not None and profile.strip():
        config["agent_visibility"]["profile"] = canonical_agent_visibility_profile(profile)


def load_config(env: dict[str, str] | None = None, home: Path | None = None) -> dict[str, Any]:
    source_env = env if env is not None else os.environ
    path = config_path(home)
    if not path.exists():
        config = _default_config()
        _apply_env_overrides(config, source_env)
        return config
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    config = _default_config()
    agent_visibility = loaded.get("agent_visibility")
    if isinstance(agent_visibility, dict):
        config["agent_visibility"].update(agent_visibility)
    strict_session_log = loaded.get("strict_session_log")
    if isinstance(strict_session_log, dict):
        config["strict_session_log"].update(strict_session_log)
    hook_runtime = loaded.get("hook_runtime")
    if isinstance(hook_runtime, dict):
        config["hook_runtime"]["node"] = _normalized_node_registrations(
            hook_runtime.get("node")
        )
    if isinstance(loaded.get("install_locations"), dict):
        config["install_locations"] = loaded["install_locations"]
    config["schema_version"] = "ghost-alice-config.v1"
    config["agent_visibility"]["profile"] = canonical_agent_visibility_profile(
        config["agent_visibility"].get("profile")
    )
    config["agent_visibility"].pop("enabled", None)
    config["strict_session_log"] = {"mode": "always"}
    _apply_env_overrides(config, source_env)
    return config


# The installer's hook writer calls these four names; each calls `atomic_write` when it runs.
def _resolve_symlink_target(path: Path) -> Path:
    return atomic_write.link_target(path)


def _atomic_write_destination(path: Path) -> Path:
    return atomic_write.destination(path)


def _atomic_write_bytes(path: Path, contents: bytes) -> None:
    atomic_write.write_bytes(path, contents)


def _atomic_write_json(path: Path, data: Any) -> None:
    atomic_write.write_json(path, data)


def save_config(config: dict[str, Any], home: Path | None = None) -> Path:
    path = config_path(home)
    install_destination.assert_mutation_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = load_config(env={}, home=home)
    if isinstance(config.get("install_locations"),dict):
        normalized.setdefault("install_locations",{}).update(config["install_locations"])
    agent_visibility = config.get("agent_visibility")
    if isinstance(agent_visibility, dict):
        normalized["agent_visibility"].update(agent_visibility)
    strict_session_log = config.get("strict_session_log")
    if isinstance(strict_session_log, dict):
        normalized["strict_session_log"].update(strict_session_log)
    hook_runtime = config.get("hook_runtime")
    if isinstance(hook_runtime, dict) and isinstance(hook_runtime.get("node"), dict):
        normalized["hook_runtime"]["node"].update(
            _normalized_node_registrations(hook_runtime["node"])
        )
    normalized["agent_visibility"]["profile"] = canonical_agent_visibility_profile(
        normalized["agent_visibility"].get("profile")
    )
    normalized["agent_visibility"].pop("enabled", None)
    normalized["strict_session_log"] = {"mode": "always"}
    atomic_write.write_json(path, normalized)
    return path
