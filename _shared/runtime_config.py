#!/usr/bin/env python3
"""Runtime preference config for Ghost-ALICE hooks."""

from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
from pathlib import Path
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
    base = Path(home) if home is not None else Path.home()
    return base / ".ghost-alice" / "config.json"


def canonical_agent_visibility_profile(value: str | None) -> str:
    if value is None:
        return "strict"
    profile = str(value).strip().lower().replace("_", "-")
    if profile in VALID_AGENT_VISIBILITY_PROFILES:
        return profile
    return "strict"


def canonical_profile(value: str | None) -> str:
    """Alias of `canonical_agent_visibility_profile`."""
    return canonical_agent_visibility_profile(value)


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
    config["schema_version"] = "ghost-alice-config.v1"
    config["agent_visibility"]["profile"] = canonical_agent_visibility_profile(
        config["agent_visibility"].get("profile")
    )
    config["agent_visibility"].pop("enabled", None)
    config["strict_session_log"] = {"mode": "always"}
    _apply_env_overrides(config, source_env)
    return config


def _resolve_symlink_target(path: Path) -> Path:
    path = Path(path)
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise OSError(f"Cannot resolve atomic write symlink {path}: {error}") from error


def _atomic_write_destination(path: Path) -> Path:
    """Return the filesystem entry replaced by an atomic write.

    Existing symlinks remain in place: their fully resolved target is replaced
    in the target directory. A broken link follows direct-write behavior only
    when its resolved target parent already exists; otherwise it fails without
    changing the link.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_symlink():
        return path
    destination = _resolve_symlink_target(path)
    if not destination.parent.is_dir():
        raise FileNotFoundError(
            f"Cannot write through broken symlink {path}: "
            f"target parent does not exist: {destination.parent}"
        )
    return destination


def _atomic_write_bytes(path: Path, contents: bytes) -> None:
    destination = _atomic_write_destination(path)
    try:
        existing_mode = stat.S_IMODE(destination.stat().st_mode)
    except FileNotFoundError:
        existing_mode = None

    descriptor, temp_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temp_path, existing_mode)
        os.replace(temp_path, destination)
    except BaseException as primary_error:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError as close_error:
                primary_error.add_note(
                    f"Failed to close temporary file descriptor for {temp_path}: "
                    f"{close_error}"
                )
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup_error:
            primary_error.add_note(
                f"Failed to clean up temporary file {temp_path}: {cleanup_error}"
            )
        raise


def _atomic_write_json(path: Path, data: Any) -> None:
    contents = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, contents)


def save_config(config: dict[str, Any], home: Path | None = None) -> Path:
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = load_config(env={}, home=home)
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
    _atomic_write_json(path, normalized)
    return path
