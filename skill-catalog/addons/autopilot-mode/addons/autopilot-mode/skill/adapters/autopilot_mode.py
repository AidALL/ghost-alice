#!/usr/bin/env python3
"""Thin privileged adapter for the official autopilot-mode addon."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _load_autopilot_state():
    env_shared = os.environ.get("GHOST_ALICE_SHARED_DIR")
    candidates: list[Path] = []
    if env_shared:
        candidates.append(Path(env_shared))
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "_shared")

    for candidate in candidates:
        if (candidate / "autopilot_state.py").is_file():
            sys.path.insert(0, str(candidate))
            import autopilot_state  # type: ignore[import-not-found]

            return autopilot_state
    raise RuntimeError("could not locate Ghost-ALICE _shared/autopilot_state.py")


def main() -> int:
    if len(sys.argv) > 1:
        sys.stderr.write("autopilot-mode adapter accepts no arguments\n")
        return 64
    try:
        autopilot_state = _load_autopilot_state()
        payload = autopilot_state.adapter_payload_from_env()
    except Exception as exc:  # pragma: no cover - defensive hook fallback
        sys.stderr.write(f"autopilot-mode adapter fell back to no-op: {exc}\n")
        payload = {"continue": True, "systemMessage": ""}
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
