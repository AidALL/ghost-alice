#!/usr/bin/env python3
"""CLI for Ghost-ALICE runtime agent visibility preferences."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Sequence

import runtime_config


LOG = logging.getLogger("agent_visibility_cli")
PROFILE_CHOICES = tuple(sorted(runtime_config.VALID_AGENT_VISIBILITY_PROFILES))


def _home_from_text(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser()


def _message_payload(message: str, *, as_json: bool) -> str:
    if as_json:
        return json.dumps({"message": message}, ensure_ascii=False)
    return message


def current_profile(*, home: Path | None = None) -> str:
    config = runtime_config.load_config(env={}, home=home)
    return str(config["agent_visibility"]["profile"])


def set_profile(profile: str, *, home: Path | None = None) -> str:
    normalized = runtime_config.canonical_agent_visibility_profile(profile)
    if normalized != str(profile).strip().lower().replace("_", "-"):
        raise ValueError(
            f"unknown agent visibility profile: {profile} "
            f"(expected: {', '.join(PROFILE_CHOICES)})"
        )
    runtime_config.save_config({"agent_visibility": {"profile": normalized}}, home=home)
    return normalized


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show or set the Ghost-ALICE agent visibility profile.",
    )
    parser.add_argument("tokens", nargs="*", help="show | set <profile> | <profile>")
    parser.add_argument("--home", help="Override the home directory that stores ~/.ghost-alice/config.json")
    parser.add_argument("--json", action="store_true", help="Emit a JSON message payload")
    args = parser.parse_args(list(argv) if argv is not None else None)

    home = _home_from_text(args.home)
    tokens = list(args.tokens)
    if not tokens or tokens[0] in {"show", "status", "current"}:
        profile = current_profile(home=home)
        LOG.info(
            _message_payload(
                f"Ghost-ALICE agent visibility profile is {profile} (profile={profile}).",
                as_json=args.json,
            )
        )
        return 0

    if tokens[0] == "set":
        tokens = tokens[1:]
    if len(tokens) != 1:
        LOG.error(
            "usage: agent_visibility_cli.py [show|status|set <profile>|<profile>] "
            f"where profile is one of: {', '.join(PROFILE_CHOICES)}"
        )
        return 2

    try:
        profile = set_profile(tokens[0], home=home)
    except ValueError as exc:
        LOG.error(str(exc))
        return 2

    LOG.info(
        _message_payload(
            "Ghost-ALICE agent visibility profile set to "
            f"{profile}. Hook execution and strict session logging remain unchanged.",
            as_json=args.json,
        )
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
