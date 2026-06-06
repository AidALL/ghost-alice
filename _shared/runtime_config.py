#!/usr/bin/env python3
"""Runtime preference config for Ghost-ALICE hooks."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

VALID_AGENT_VISIBILITY_PROFILES = {"strict", "dynamic", "minimal"}
DEFAULT_CONFIG = {
    "schema_version": "ghost-alice-config.v1",
    "agent_visibility": {"profile": "dynamic"},
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
    except (OSError, json.JSONDecodeError):
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
    config["schema_version"] = "ghost-alice-config.v1"
    config["agent_visibility"]["profile"] = canonical_agent_visibility_profile(
        config["agent_visibility"].get("profile")
    )
    config["agent_visibility"].pop("enabled", None)
    config["strict_session_log"] = {"mode": "always"}
    _apply_env_overrides(config, source_env)
    return config


def save_config(config: dict[str, Any], home: Path | None = None) -> Path:
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _default_config()
    agent_visibility = config.get("agent_visibility")
    if isinstance(agent_visibility, dict):
        normalized["agent_visibility"].update(agent_visibility)
    strict_session_log = config.get("strict_session_log")
    if isinstance(strict_session_log, dict):
        normalized["strict_session_log"].update(strict_session_log)
    normalized["agent_visibility"]["profile"] = canonical_agent_visibility_profile(
        normalized["agent_visibility"].get("profile")
    )
    normalized["agent_visibility"].pop("enabled", None)
    normalized["strict_session_log"] = {"mode": "always"}
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
