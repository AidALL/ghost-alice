"""Fixtures for the gateway participant tests: a home with a registered gateway in its install record, and a
platform double whose service is a flag instead of launchd.

The registered home holds everything the participant and the registration command read: the runtime
`_shared` and its companion script under `~/.ghost-alice/runtime/current`, the three service files
(launch agent, `state.json`, Codex `config.toml` with the provider blocks) and the install record with a
`kind=runtime` target and the `service` section. The fixture computes the service fingerprints the way the
registration command defines them, independently of the production code, so the tests have an oracle.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import plistlib
import re
import sys

import content_digest
import context_gateway_platform as platform
import install_lock
import installed_payload
from context_gateway_model import LABEL, ServiceObservation, TABLE_BEGIN, TABLE_END, TOP_BEGIN, TOP_END, digest
from context_gateway_plan import render_provider

COMPANION = "session-intent-analyzer/scripts/session_intent_ledger.py"
PORT, SCOPE = 12345, "prior-scope"


def provider_blocks(text: str) -> str:
    """The two managed provider blocks of a Codex `config.toml`, as the fingerprint covers them."""
    found = []
    for begin, end in ((TOP_BEGIN, TOP_END), (TABLE_BEGIN, TABLE_END)):
        found.extend(re.findall(re.escape(begin) + r"\n.*?\n" + re.escape(end) + r"\n", text, flags=re.S))
    return "".join(found)


def hold_lock(testcase, root: Path) -> Path:
    """Take `<root>/.ghost-alice/install.lock` for this process until the test ends."""
    lock = root / ".ghost-alice" / "install.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    install_lock.acquire_lock(lock, owner="installer:test:claude:test")
    testcase.addCleanup(install_lock.release_lock, lock)
    return lock


@dataclass
class Registered:
    """The paths of one registered home."""

    root: Path
    ghost: Path
    runtime_root: Path
    shared: Path
    companion: Path
    codex: Path
    config: Path
    ledger: Path
    state_dir: Path
    state_path: Path
    agent: Path
    claude: Path

    @property
    def program(self) -> Path:
        return self.shared / "context_gateway.py"

    def record(self) -> dict:
        return installed_payload.read(self.ghost) or {}

    def service(self) -> dict | None:
        return self.record().get("service")


def service_section(reg: Registered, *, registered_at="2026-10-20T00:00:00+00:00") -> dict:
    """The `service` section for the service files as they are on disk now (the oracle)."""
    return {
        "label": LABEL, "runtime_root": str(reg.runtime_root), "program": str(reg.program),
        "launch_agent": str(reg.agent), "launch_agent_digest": digest(reg.agent.read_bytes()),
        "state_path": str(reg.state_path), "state_digest": digest(reg.state_path.read_bytes()),
        "config_path": str(reg.config),
        "config_block_digest": digest(provider_blocks(reg.config.read_text(encoding="utf-8")).encode("utf-8")),
        "config_backup": str(reg.state_dir / "backups" / "20261020T000000000000Z-config.toml"),
        "registered_at": registered_at, "restarted_at": registered_at, "listener_ready": True,
    }


def write_runtime(reg: Registered, *, stamp: str = "prior") -> None:
    reg.shared.mkdir(parents=True, exist_ok=True)
    for name in ("context_gateway.py", "context_lifecycle.py", "completion_assessment.py", "hook_definitions.py"):
        (reg.shared / name).write_text(f"{stamp} {name}\n", encoding="utf-8")
    reg.companion.parent.mkdir(parents=True, exist_ok=True)
    reg.companion.write_text(f"{stamp} {COMPANION}\n", encoding="utf-8")


def registered_home(testcase, root: Path, *, service: bool = True, lock: bool = True,
                    hosts=("claude", "codex"), interpreter: str | None = None) -> Registered:
    """A home whose install record names the runtime `_shared` (and its companion) and, with `service`,
    the registered gateway; the three service files exist and agree with the record."""
    ghost = root / ".ghost-alice"
    runtime_root = ghost / "runtime" / "current"
    shared = runtime_root / "_shared"
    codex, ledger, claude = root / ".codex", root / "ledger", root / ".claude"
    state_dir = ghost / "context-gateway"
    agent = root / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    reg = Registered(root=root, ghost=ghost, runtime_root=runtime_root, shared=shared,
                     companion=runtime_root / COMPANION, codex=codex, config=codex / "config.toml", ledger=ledger,
                     state_dir=state_dir, state_path=state_dir / "state.json", agent=agent, claude=claude)
    for directory in (ghost, codex, ledger, claude, state_dir, agent.parent):
        directory.mkdir(parents=True, exist_ok=True)
    write_runtime(reg)
    (ledger / "ghost-state.sqlite3").touch()
    if lock:
        hold_lock(testcase, root)
    targets = {
        str(shared): {"name": "_shared", "kind": "runtime", "mode": "copy", "provider": "core",
                      "source": "/src/_shared", "source_commit": "c0ffee", "used_by": list(hosts),
                      "files": content_digest.tree(shared), "installed_at": "2026-10-20T00:00:00+00:00",
                      "baseline": "recorded"},
        str(reg.companion): {"name": COMPANION, "kind": "runtime", "mode": "copy", "provider": "core",
                             "source": "/src/" + COMPANION, "source_commit": "c0ffee", "used_by": list(hosts),
                             "files": content_digest.tree(reg.companion), "installed_at": "2026-10-20T00:00:00+00:00",
                             "baseline": "recorded"},
    }
    installed_payload.update(ghost, {"targets": targets, "installer": {"commit": "c0ffee", "schema_version": 1}})
    if not service:
        reg.config.write_text('model="native"\n', encoding="utf-8")
        return reg
    state = {"schema_version": "context-gateway.v1", "port": PORT, "scope": SCOPE, "ledger_root": str(ledger),
             "codex_dir": str(codex), "usage_log": str(state_dir / "usage.jsonl")}
    reg.state_path.write_bytes(json.dumps(state, indent=2).encode())
    (state_dir / "usage.jsonl").touch()
    (state_dir / "service.log").touch()
    plist = {"Label": LABEL, "ProgramArguments": [interpreter or sys.executable, "-B", str(reg.program),
                                                  "--config", str(reg.state_path)],
             "RunAtLoad": True, "StandardOutPath": str(state_dir / "service.log"),
             "StandardErrorPath": str(state_dir / "service.log"), "WorkingDirectory": str(state_dir)}
    agent.write_bytes(plistlib.dumps(plist))
    reg.config.write_text(render_provider('model="native"\n', PORT, SCOPE), encoding="utf-8")
    installed_payload.update(ghost, {"service": service_section(reg)})
    return reg


class FakeEffects:
    """The platform the participant and the registration command call: real files, a fake launchd.

    `running` says whether the service is loaded; `stop`/`start` flip it and every service call lands in
    `calls`. `ready` answers from `ready_answers` (the last answer repeats). `record` writes the record's
    service section like the platform does.
    """

    snapshot = staticmethod(platform.snapshot)
    children = staticmethod(platform.Platform.children)
    mkdir = staticmethod(platform.Platform.mkdir)
    unlink = staticmethod(platform.Platform.unlink)
    rmdir = staticmethod(platform.Platform.rmdir)

    def __init__(self, home: Path, *, running: bool = False, agent: Path | None = None, ready=True,
                 stop_error: Exception | None = None, start_error: Exception | None = None,
                 record_error: Exception | None = None):
        self.home, self.running, self.agent = Path(home), running, agent
        self.ready_answers = list(ready) if isinstance(ready, (list, tuple)) else [ready]
        self.stop_error, self.start_error, self.record_error = stop_error, start_error, record_error
        self.calls: list[tuple] = []

    def observe(self, domain):
        if self.running:
            return ServiceObservation("present", self.agent)
        return ServiceObservation("absent", returncode=113)

    def state(self, domain):
        self.calls.append(("state", domain))
        return self.observe(domain)

    def stop(self, domain):
        self.calls.append(("stop", domain))
        if self.stop_error is not None:
            raise self.stop_error
        self.running = False

    def start(self, domain, agent):
        self.calls.append(("start", Path(agent)))
        if self.start_error is not None:
            raise self.start_error
        self.running, self.agent = True, Path(agent)

    def ready(self, state):
        answer = self.ready_answers.pop(0) if len(self.ready_answers) > 1 else self.ready_answers[0]
        self.calls.append(("ready", state["port"], answer))
        return answer

    def write(self, path, raw, mode=0o600):
        self.calls.append(("write", Path(path)))
        platform.write(path, raw, mode)

    def record(self, section):
        self.calls.append(("record", None if section is None else "service"))
        if self.record_error is not None:
            raise self.record_error
        installed_payload.update(self.home / ".ghost-alice", {"service": section})

    def service_calls(self) -> list[str]:
        return [call[0] for call in self.calls if call[0] in ("stop", "start", "ready")]


def service_environment(home: Path) -> dict[str, str]:
    """`HOME` set to the fixture home, with the host variables that would move it removed."""
    env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
    env["HOME"] = str(home)
    return env
