"""The gateway participant: the installer's one adapter to the context gateway service.

The judgment `affects` answers whether a publish or a removal touches the service's code: it compares
the install locations the publisher names with the install record's `service.runtime_root` (a location
equal to it, under it or above it) and with the three service files. It never resolves links, and the
data roots the service reads are not inputs. `GatewayParticipant` applies that judgment at the four
points of a publish: it stops the service before the files change, starts it after, confirms readiness
and records the restart. `removal` applies it to the roots a removal deletes and detaches the service
through `context_gateway_install.detach`; without a `service` section it asks launchd and holds only for
a loaded service whose program is inside a removal root. `loaded_service` is that one launchd
observation; the install report asks it too.

This is the only installer package module that imports the gateway modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import plistlib
import sys

import context_gateway_install as gateway
import context_gateway_platform as gateway_platform
import installed_payload
from context_gateway_model import GatewayBlocked, GatewayFailure, GatewayServiceError, ServiceObservation
from installer import locations
from installer.participant import PublishContext

SERVICE_FILE_KEYS = ("launch_agent", "state_path", "config_path")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host(platform: str | None) -> str:
    """The host platform, read when the judgment runs (the tests patch `sys.platform`)."""
    return sys.platform if platform is None else platform


def participants(ghost_root: Path | str, *, effects=None, platform: str | None = None,
                 runtime_root: Path | None = None) -> list:
    """The participants of a publish: the gateway participant when the install record has a `service`
    section, none when the record (or the section) is absent, and a participant that holds a runtime
    publish when the record cannot be read (whether a gateway runs from the runtime is unknown then)."""
    try:
        record = installed_payload.read(ghost_root)
    except installed_payload.RecordError as exc:
        return [UnreadableRecordParticipant(str(exc), locations.runtime_root() if runtime_root is None else runtime_root)]
    service = (record or {}).get("service")
    if not isinstance(service, dict):
        return []
    return [GatewayParticipant(ghost_root, service, effects=effects, platform=_host(platform))]


def _touches(root: Path, locations) -> bool:
    """Whether a location equals `root`, lies under it or holds it."""
    for location in locations:
        location = Path(location)
        if location == root or root in location.parents or location in root.parents:
            return True
    return False


def affects(service: dict, locations, *, remove_codex: bool = False) -> bool:
    """Whether changing or removing `locations` touches the registered service's code or files: a location
    equal to, under or above `runtime_root`, or one that is or holds a service file."""
    if remove_codex:
        return True
    if _touches(Path(service["runtime_root"]), locations):
        return True
    locations = [Path(location) for location in locations]
    return any(file == location or location in file.parents
               for file in (Path(service[key]) for key in SERVICE_FILE_KEYS) for location in locations)


class UnreadableRecordParticipant:
    """Stands in for the gateway participant while the install record cannot be read: a publish that touches
    the runtime is held until the record is reviewed; a publish elsewhere goes on."""

    def __init__(self, reason: str, runtime_root: Path):
        self.reason, self.runtime_root = reason, Path(runtime_root)

    def before_publish(self, ctx: PublishContext) -> None:
        if _touches(self.runtime_root, ctx.destinations):
            raise GatewayBlocked(f"The install record cannot be read ({self.reason}); whether a gateway runs from "
                                 f"{self.runtime_root} is unknown, so its publish waits for the record to be reviewed")

    def after_publish(self, ctx: PublishContext) -> None:
        return None

    def before_restore(self, ctx: PublishContext) -> None:
        return None

    def after_restore(self, ctx: PublishContext) -> None:
        return None


class GatewayParticipant:
    """Stops the registered service around a publish that touches its code, and starts it again."""

    def __init__(self, ghost_root: Path | str, service: dict, *, effects=None, platform: str | None = None):
        self.ghost_root, self.service = Path(ghost_root), dict(service)
        self.effects = gateway._effects() if effects is None else effects
        self.platform = _host(platform)
        self.domain = gateway_platform.service_domain(self.platform)
        self.agent = Path(service["launch_agent"])
        self.affected = self.was_running = self.stopped = self.started = False

    def _state(self) -> str:
        value = self.effects.state(self.domain)
        if isinstance(value, ServiceObservation):
            value = "running" if value.presence == "present" else value.presence
        if value not in ("running", "absent"):
            raise RuntimeError("Native service observation is inconclusive")
        return value

    def _listener(self) -> dict:
        return json.loads(Path(self.service["state_path"]).read_bytes())

    def before_publish(self, ctx: PublishContext) -> None:
        self.affected = affects(self.service, ctx.destinations)
        if not self.affected:
            return
        if self.platform != "darwin":
            raise GatewayBlocked("The registered gateway service needs its macOS launchctl owner to stop and start it")
        try:
            self.was_running = self._state() == "running"
        except Exception as exc:
            raise GatewayServiceError("Service state is unavailable; no installed artifacts changed") from exc
        if self.was_running:
            try:
                self.effects.stop(self.domain)
            except Exception as exc:
                raise GatewayServiceError("Gateway service stop/absence is unconfirmed; no active files changed; "
                                          "retain Core for review", phase="stop") from exc
            self.stopped = True
        try:
            absent = self._state() == "absent"
        except Exception:
            absent = False
        if not absent:
            raise GatewayServiceError("Gateway service absence is unconfirmed; no active files changed; retain Core "
                                      "for review", phase="verify-absence")

    def after_publish(self, ctx: PublishContext) -> None:
        if not (self.affected and self.stopped):
            return
        self.started = True
        try:
            self.effects.start(self.domain, self.agent)
            ready = self.effects.ready(self._listener())
        except Exception as exc:
            raise GatewayFailure(f"Service start failed after the publish: {exc}", phase="bootstrap", restored=True) from exc
        if not ready:
            raise GatewayFailure("Candidate service is not ready after the publish", phase="readiness", restored=True)
        section = dict(self.service, restarted_at=_now(), listener_ready=True)
        installed_payload.update(self.ghost_root, {"service": section})
        self.service = section

    def before_restore(self, ctx: PublishContext) -> None:
        if not self.started:
            return
        try:
            if self._state() == "running":
                self.effects.stop(self.domain)
            if self._state() != "absent":
                raise RuntimeError("Candidate stop is unconfirmed")
        except Exception as exc:
            raise GatewayFailure(f"Candidate service stop is unconfirmed: {exc}", phase="rollback") from exc
        self.started = False

    def after_restore(self, ctx: PublishContext) -> None:
        if not (self.was_running and self.stopped):
            return
        try:
            self.effects.start(self.domain, self.agent)
            ready = self.effects.ready(self._listener())
        except Exception as exc:
            raise GatewayFailure(f"Restored service did not start: {exc}", phase="rollback") from exc
        if not ready:
            raise GatewayFailure("Restored service is not ready", phase="rollback")
        self.stopped = False


def removal(ghost_root: Path | str, removing, *, remove_codex: bool = False, confirm: bool = True, effects=None,
            platform: str | None = None) -> dict:
    """Judge a removal of `removing` and detach the registered service when it is touched.

    Returns the gateway item of the removal report: `missing` (no service), `unchanged` (independent),
    `would-detach` (a preview) or `detached`. Raises GatewayBlocked, GatewayServiceError or GatewayFailure
    with the exit code of the cause.
    """
    ghost_root = Path(ghost_root)
    io = gateway._effects() if effects is None else effects
    platform = _host(platform)
    removing = [Path(path) for path in removing]
    try:
        record = installed_payload.read(ghost_root)
    except installed_payload.RecordError as exc:
        raise GatewayBlocked("The install record cannot be read: " + str(exc)) from exc
    service = (record or {}).get("service")
    if not isinstance(service, dict):
        return _unregistered(io, removing, platform)
    if not affects(service, removing, remove_codex=remove_codex):
        return dict(kind="context-gateway", action="unchanged", reason="independent-gateway-runtime")
    result = gateway.detach(confirm=confirm, home=ghost_root.parent, io=io, host_platform=platform)
    return {"kind": "context-gateway", **result}


@dataclass(frozen=True)
class LoadedService:
    """What launchd says about a service with the gateway's label: `absent` (also off macOS), `inconclusive`
    (launchd gave no clear answer), `unreadable` (its plist names no absolute program) or `loaded` with the
    `program` its plist runs. `detail` says why an answer is not clear."""

    state: str
    program: Path | None = None
    detail: str = ""

    def runs_under(self, root) -> bool:
        """Whether the loaded program is `root` or lies under it."""
        root = Path(root)
        return self.program is not None and (self.program == root or root in self.program.parents)


def loaded_service(*, effects=None, platform: str | None = None) -> LoadedService:
    """Ask launchd once whether a service with the gateway's label is loaded and, when it is, read the program
    its plist runs (`ProgramArguments[2]` of five arguments). A removal without a `service` section and the
    install report both read launchd through this function. Off macOS nothing is asked."""
    platform = _host(platform)
    if platform != "darwin":
        return LoadedService("absent")
    io = gateway._effects() if effects is None else effects
    try:
        observed = io.observe(gateway_platform.service_domain(platform))
    except (OSError, RuntimeError, ValueError) as exc:
        return LoadedService("inconclusive", detail=f"launchd cannot be asked: {exc}")
    if observed.presence == "absent":
        return LoadedService("absent")
    if observed.presence != "present" or observed.agent is None:
        return LoadedService("inconclusive", detail=observed.detail or f"launchd answered {observed.presence}")
    try:
        definition = io.snapshot(observed.agent)
        parsed = plistlib.loads(definition.raw) if definition.kind == "file" else None
    except (OSError, ValueError, plistlib.InvalidFileException):
        parsed = None
    argv = parsed.get("ProgramArguments") if isinstance(parsed, dict) else None
    if not isinstance(argv, list) or len(argv) != 5 or not isinstance(argv[2], str) or not Path(argv[2]).is_absolute():
        return LoadedService("unreadable", detail=f"{observed.agent} names no absolute program")
    return LoadedService("loaded", program=Path(argv[2]))


def _unregistered(io, removing, platform) -> dict:
    """Without a `service` section only launchd can say whether the gateway runs: a loaded service whose
    program is inside a removal root holds the removal; an absent one means nothing to detach."""
    seen = loaded_service(effects=io, platform=platform)
    if seen.state == "absent":
        return dict(kind="context-gateway", action="missing", reason="gateway-not-registered")
    if seen.state == "inconclusive":
        raise GatewayBlocked("Gateway native discovery is inconclusive; retain Core for review",
                             code="GATEWAY_SERVICE_UNCONFIRMED", exit_code=3)
    if seen.state == "unreadable":
        raise GatewayBlocked("Unregistered service arguments are unavailable; retain Core")
    if any(seen.runs_under(root) for root in removing):
        raise GatewayBlocked("A loaded gateway service runs from the selected removal root but has no install record "
                             "entry; retain Core for review", code="GATEWAY_SERVICE_UNCONFIRMED", exit_code=3)
    return dict(kind="context-gateway", action="unchanged", reason="independent-unregistered-gateway-runtime")


def guard(ghost_root: Path | str, removing, *, effects=None, platform: str | None = None) -> int:
    """`removal` as an exit code for the selected removal: 0 to go on, else the cause's code with its
    diagnostic on stderr."""
    try:
        removal(ghost_root, removing, remove_codex=False, confirm=True, effects=effects, platform=platform)
    except (GatewayBlocked, GatewayServiceError, GatewayFailure) as exc:
        print(json.dumps(dict(status=exc.outcome, error_code=exc.code, phase=exc.phase, detail=str(exc))), file=sys.stderr)
        return exc.exit_code
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps(dict(status="blocked", error_code="GATEWAY_PRECONDITION", phase="preflight", detail=str(exc))),
              file=sys.stderr)
        return 2
    return 0
