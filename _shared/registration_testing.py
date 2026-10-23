"""Test helper: the hook registration an older installer wrote.

Before the launcher, every registration carried the Python discovery loop, the runner
path, the inner command and the destination binding in one command. Tests that need
such a registration build it here; no production code writes this form any more.
"""
from __future__ import annotations

import base64

import install_destination
import install_hooks
from hook_definitions import normalize_hook_id


def long_runner_command(hook_id: str, inner_command: str, marker: str, *, platform: str, destination,
                        adapter: dict | None = None) -> str:
    """The long runner form for `hook_id` on `platform`, bound to `destination` (a dict or an
    InstallDestination); `adapter` adds the retired adapter key an older installer wrote."""
    resolved = install_destination.InstallDestination.parse(destination)
    binding = resolved.binding()
    if adapter is not None:
        binding["adapter"] = dict(adapter)
    normalized = normalize_hook_id(hook_id)
    payload = base64.urlsafe_b64encode(inner_command.encode("utf-8")).decode("ascii")
    runner = str(resolved.runtime_shared_dir).replace("\\", "/") + "/hook_profile_gate.py"
    args = ["run", normalized, platform, payload, "--binding-b64", install_destination.encode_binding(binding)]
    return f"{install_hooks._hook_python_invocation(runner, *args)} # {marker} [hook-runner:{normalized}]"


def long_runner_entry(hook_id: str, inner_command: str, marker: str, *, platform: str, destination,
                      adapter: dict | None = None) -> dict:
    """The hook settings entry of `long_runner_command`."""
    return {"matcher": "", "hooks": [{"type": "command", "command": long_runner_command(
        hook_id, inner_command, marker, platform=platform, destination=destination, adapter=adapter)}]}
