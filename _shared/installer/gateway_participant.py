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
from context_gateway_model import GatewayBlocked, GatewayFailure, GatewayServiceError
from context_gateway_plan import valid_state
from context_gateway_transaction import owned_service_state
from installer import locations
from installer.participant import PublishContext

SERVICE_FILE_KEYS = ("launch_agent", "state_path", "config_path")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host(platform: str | None) -> str:
    """The operating system, read when the judgment runs."""
    return sys.platform if platform is None else platform


def _listener(path: Path) -> tuple[bytes, dict]:
    """Read listener content, including a linked state file, without granting replacement eligibility."""
    raw = path.read_bytes()
    state = json.loads(raw)
    if not isinstance(state, dict):
        raise ValueError('The listener state is not an object')
    return raw, valid_state(state)


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


def recovery_participants(ctx: PublishContext, states: dict | None, original_record: dict | None) -> list:
    """Reconstruct only the gateway whose prior running state the publish saved before its effects.

    An older affected journal cannot establish that state and waits for review. A new
    journal without a gateway checkpoint has not reached a gateway service effect.
    """
    service = (original_record or {}).get("service")
    if states is None:
        if isinstance(service, dict) and affects(service, ctx.destinations):
            raise GatewayFailure("The journal has no prior gateway running state; review the service before recovery",
                                 phase="rollback")
        return []
    if not isinstance(states, dict):
        raise GatewayFailure("The journal's participant recovery facts are unreadable", phase="rollback")
    if "gateway" not in states:
        return []
    saved = states["gateway"]
    # Readiness telemetry can be saved just before an interrupted journal reaches done.
    telemetry = {'restarted_at', 'listener_ready'}
    if (not isinstance(saved, dict) or type(saved.get("was_running")) is not bool
            or not isinstance(service, dict) or not isinstance(saved.get('service'), dict)
            or {key: value for key, value in saved['service'].items() if key not in telemetry}
               != {key: value for key, value in service.items() if key not in telemetry}
            or not affects(service, ctx.destinations)):
        raise GatewayFailure("The journal cannot establish the gateway's prior running state and ownership",
                             phase="rollback")
    chosen = GatewayParticipant(ctx.ghost_root, service)
    chosen.affected = chosen.recovering = True
    chosen.was_running = chosen.stopped = saved["was_running"]
    return [chosen]


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
        self.effects = gateway._effects(self.ghost_root.parent) if effects is None else effects
        self.platform = _host(platform)
        self.domain = gateway_platform.service_domain(self.platform)
        self.agent = Path(service["launch_agent"])
        self.affected = self.was_running = self.stopped = self.started = False
        self.stop_attempted = self.recovering = False

    def prepare_ledger_retarget(self, old_root: Path, new_root: Path) -> tuple[dict[Path, bytes], dict]:
        """Prepare this registered listener's new ledger binding without service effects."""
        from context_gateway_model import digest, encoded
        path = Path(self.service['state_path'])
        raw, state = _listener(path)
        if digest(raw) != self.service['state_digest']:
            raise GatewayBlocked('Gateway state changed outside the installer; preserve it before ledger migration')
        if Path(state['ledger_root']).resolve() not in (old_root, new_root):
            return {}, dict(self.service)
        state.update(ledger_root=str(new_root), ghost_root=str(self.ghost_root))
        raw = encoded(state)
        return {path: raw}, dict(self.service, state_digest=digest(raw))

    def before_publish(self, ctx: PublishContext) -> None:
        self.affected = affects(self.service, ctx.destinations)
        if not self.affected:
            return
        if self.platform != "darwin":
            raise GatewayBlocked("The registered gateway service needs its macOS launchctl owner to stop and start it")
        try:
            self.was_running = owned_service_state(self.effects, self.domain, self.agent) == "running"
        except Exception as exc:
            raise GatewayServiceError("Service state is unavailable; no installed artifacts changed") from exc
        if ctx.checkpoint is None:
            raise GatewayBlocked("Gateway publish needs a transaction recovery checkpoint before service effects")
        ctx.checkpoint("gateway", {"service": dict(self.service), "was_running": self.was_running})
        if self.was_running:
            self.stop_attempted = True
            try:
                self.effects.stop(self.domain)
            except Exception as exc:
                raise GatewayServiceError("Gateway service stop/absence is unconfirmed; no active files changed; "
                                          "retain Core for review", phase="stop") from exc
        try:
            absent = owned_service_state(self.effects, self.domain, self.agent) == "absent"
        except Exception:
            absent = False
        if not absent:
            raise GatewayServiceError("Gateway service absence is unconfirmed; no active files changed; retain Core "
                                      "for review", phase="verify-absence")
        self.stopped = self.was_running

    def after_publish(self, ctx: PublishContext) -> None:
        if not (self.affected and self.stopped):
            return
        self.started = True
        try:
            if self.recovering and self.platform != 'darwin':
                raise RuntimeError('Gateway recovery needs its macOS launchctl owner')
            if not self.recovering or owned_service_state(self.effects, self.domain, self.agent) == 'absent':
                self.effects.start(self.domain, self.agent)
            ready = self.effects.ready(_listener(Path(self.service['state_path']))[1])
        except Exception as exc:
            raise GatewayFailure(f"Service start failed after the publish: {exc}", phase="bootstrap",
                                 restored=not self.recovering) from exc
        if not ready:
            raise GatewayFailure("Candidate service is not ready after the publish", phase="readiness",
                                 restored=not self.recovering)
        section = dict(self.service, restarted_at=_now(), listener_ready=True)
        installed_payload.update(self.ghost_root, {"service": section})
        self.service = section

    def before_restore(self, ctx: PublishContext) -> None:
        if not (self.started or self.recovering or self.stop_attempted):
            return
        try:
            if self.platform != "darwin":
                raise RuntimeError("Gateway recovery needs its macOS launchctl owner")
            if owned_service_state(self.effects, self.domain, self.agent) == "running":
                if not (self.started or self.recovering or self.stopped):
                    # A failed stop left the prior service running and no files changed.
                    self.stopped = False
                    return
                if self.recovering and not self.was_running:
                    raise RuntimeError("The previously stopped service was started outside this publish")
                self.effects.stop(self.domain)
            if owned_service_state(self.effects, self.domain, self.agent) != "absent":
                raise RuntimeError("Candidate stop is unconfirmed")
        except Exception as exc:
            raise GatewayFailure(f"Candidate service stop is unconfirmed: {exc}", phase="rollback") from exc
        self.started = False
        self.stopped = self.was_running

    def after_restore(self, ctx: PublishContext) -> None:
        if not (self.was_running and self.stopped):
            return
        try:
            self.effects.start(self.domain, self.agent)
            ready = self.effects.ready(_listener(Path(self.service['state_path']))[1])
        except Exception as exc:
            raise GatewayFailure(f"Restored service did not start: {exc}", phase="rollback") from exc
        if not ready:
            raise GatewayFailure("Restored service is not ready", phase="rollback")
        self.stopped = False


def removal(ghost_root: Path | str, removing, *, remove_codex: bool = False, apply_changes: bool = True, effects=None,
            platform: str | None = None) -> dict:
    """Judge a removal of `removing` and detach the registered service when it is touched.

    The caller's apply_changes selects detachment over a preview; the installer records no person's confirmation.
    Returns the gateway item of the removal report: `missing` (no service), `unchanged` (independent),
    `would-detach` (a preview) or `detached`. Raises GatewayBlocked, GatewayServiceError or GatewayFailure
    with the exit code of the cause.
    """
    ghost_root = Path(ghost_root)
    io = gateway._effects(ghost_root.parent) if effects is None else effects
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
    result = gateway.detach(apply_changes=apply_changes, home=ghost_root.parent, io=io, host_platform=platform)
    return {"kind": "context-gateway", **result}


@dataclass(frozen=True)
class LoadedService:
    """What launchd says about a service with the gateway's label: `absent` (also off macOS), `inconclusive`
    (launchd gave no clear answer), `unreadable` (its plist names no absolute program) or `loaded` with the
    `program` its plist runs. `detail` says why an answer is not clear."""

    state: str
    program: Path | None = None
    detail: str = ""
    state_path: Path | None = None
    pid: int | None = None

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
    io = gateway._effects(Path.home()) if effects is None else effects
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
    state_path = (Path(argv[4]) if argv[3] == '--config' and isinstance(argv[4], str)
                  and Path(argv[4]).is_absolute() and Path(argv[2]).name == 'context_gateway.py' else None)
    return LoadedService("loaded", program=Path(argv[2]), state_path=state_path, pid=observed.pid)


@dataclass(frozen=True)
class ListenerBinding:
    """The loaded listener's relation to the selected ledgers; independent of native service presence."""

    state: str
    ledger_root: Path | None = None
    detail: str = ''


def listener_binding(seen: LoadedService, roots, *, effects=None) -> ListenerBinding:
    """Correlate the live listener's startup binding with its native PID; files supply contact coordinates."""
    if seen.state == 'absent':
        return ListenerBinding('absent')
    if seen.state != 'loaded' or seen.state_path is None:
        return ListenerBinding('unknown', detail=seen.detail or 'The native program/state binding is unavailable')
    if type(seen.pid) is not int or seen.pid <= 0:
        return ListenerBinding('unknown', detail='The native service process identity is unavailable')
    try:
        _, state = _listener(seen.state_path)
        io = gateway._effects(Path.home()) if effects is None else effects
        live = io.listener_health(state, inspect_binding=True)
        if (not isinstance(live, dict) or live.get('status') != 'ready' or type(live.get('pid')) is not int
                or live['pid'] != seen.pid or not isinstance(live.get('ledger_root'), str)
                or not Path(live['ledger_root']).is_absolute()):
            return ListenerBinding('unknown', detail='The live listener binding does not identify the native service process')
        ledger_root = Path(live['ledger_root']).resolve()
        related = ledger_root in {Path(root).resolve() for root in roots}
    except (OSError, RuntimeError, ValueError, GatewayBlocked) as exc:
        return ListenerBinding('unknown', detail=f'Live listener binding cannot be observed: {exc}')
    return ListenerBinding('related' if related else 'unrelated', ledger_root)


def ledger_migration_participants(ghost_root: Path | str, old_root: Path, new_root: Path, *, effects=None,
                                  platform: str | None = None) -> tuple[list, str | None]:
    """Select registered participants for the actual A/B listener, or return a reason to hold migration.

    An unrelated loaded listener stays untouched. An absent registered listener keeps its participant
    so the next start uses the new binding. Existing participants own all control and recovery.
    """
    ghost_root, old_root, new_root = Path(ghost_root), Path(old_root).resolve(), Path(new_root).resolve()
    registered = participants(ghost_root, effects=effects, platform=platform)
    if registered and isinstance(registered[0], UnreadableRecordParticipant):
        return [], f'The install record cannot be read: {registered[0].reason}'
    participant = registered[0] if registered else None
    platform = participant.platform if platform is None and participant is not None else _host(platform)
    if effects is not None:
        io = effects
    else:
        io = participant.effects if participant is not None else gateway._effects(ghost_root.parent)
    seen = loaded_service(effects=io, platform=platform)
    binding = listener_binding(seen, (old_root, new_root), effects=io)
    if binding.state == 'absent':
        return registered, None
    if binding.state == 'unrelated':
        return [], None
    if binding.state == 'unknown':
        return [], binding.detail
    if participant is None:
        return [], 'The running service has no installation ownership record'
    try:
        changes, service = participant.prepare_ledger_retarget(old_root, new_root)
        if not changes:
            return [], 'The registered listener is unrelated, but the actual service uses a ledger being moved'
        if (seen.program != Path(service['program'])
                or seen.state_path.resolve() not in {Path(path).resolve() for path in changes}):
            return [], 'The actual service program/state binding differs from the registered listener being retargeted'
        owned_service_state(participant.effects, participant.domain, participant.agent)
    except (OSError, RuntimeError, ValueError, GatewayBlocked) as exc:
        return [], str(exc) or type(exc).__name__
    return registered, None


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
        removal(ghost_root, removing, remove_codex=False, apply_changes=True, effects=effects, platform=platform)
    except (GatewayBlocked, GatewayServiceError, GatewayFailure) as exc:
        print(json.dumps(dict(status=exc.outcome, error_code=exc.code, phase=exc.phase, detail=str(exc))), file=sys.stderr)
        return exc.exit_code
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps(dict(status="blocked", error_code="GATEWAY_PRECONDITION", phase="preflight", detail=str(exc))),
              file=sys.stderr)
        return 2
    return 0
