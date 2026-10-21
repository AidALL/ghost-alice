"""Place the selected targets through publish requests, and write the earlier records after placement.

Port of `installer_lib/install.sh:75-223, 411-610, 640-653` and
`installer_lib/targets.sh:248-261, 275-338`. Every placement builds one publish
request and calls `install_transaction.publish` in this process with the
participants the install record names (the gateway participant when a gateway is
registered); the publish places the targets and writes their install record
entries in the same step. This module decides what each request names. The
markers, the snapshot, the sidecars and the install-state are still written after
placement until their readers move to the install record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import sys
from typing import TYPE_CHECKING

import install_transaction
import installed_payload
from installer import gateway_participant, locations, report, runner, source
from installer.runner import ToolResult

if TYPE_CHECKING:
    from installer.cli import Context
    from installer.selection import Target
    from installer.source import HookPathChange

# The runtime companion scripts the context gateway runs beside the runtime `_shared`, relative to the
# checkout and to the runtime root; the runtime publish carries them with `_shared`.
RUNTIME_COMPANIONS = ("session-intent-analyzer/scripts/session_intent_ledger.py",)


@dataclass
class CopyBatch:
    """Targets a copy-only install publishes in one request."""

    pairs: list[tuple[Path, Path]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)

    def add(self, src: Path, dest: Path, label: str, kind: str, status: str, provider: str = "core") -> None:
        self.pairs.append((Path(src), Path(dest)))
        self.labels.append(label)
        self.kinds.append(kind)
        self.statuses.append(status)
        self.providers.append(provider)


@dataclass
class SyncCounts:
    installed: int = 0
    updated: int = 0
    skipped: int = 0


def _targets(ctx: "Context", skills: list[str]) -> "list[Target]":
    from installer import selection

    return selection.iter_install_targets(skills, ctx.addon_targets)


def record_source(ctx: "Context") -> dict:
    """The `source` section every publish of this command records: the checkout's root,
    commit, dirty state and git hook path, read once per command."""
    if ctx.record_source is None:
        info = source.resolve_source()
        ctx.record_source = {"root": str(info.root), "commit": info.head, "dirty": info.dirty,
                             "hooks_path": source.configured_hooks_path()}
    return ctx.record_source


def _request_target(ctx: "Context", name: str, source_path: Path, dest: Path, *, kind: str, mode: str,
                    provider: str = "core", status: str | None = None) -> dict:
    """One target of a publish request; an addon target records the commit of its own checkout."""
    commit = record_source(ctx)["commit"] if provider == "core" else source.commit_of(Path(source_path))
    target = {"name": name, "kind": kind, "provider": provider, "source": str(source_path),
              "source_commit": commit, "dest": str(dest), "mode": mode}
    if status:
        target["status"] = status
    return target


def publish(ctx: "Context", targets: list[dict], progress: dict | None = None) -> ToolResult:
    """Build one publish request for `targets` and call `install_transaction.publish` in this process
    with the participants the install record names.

    The request names the host, the record root, the failure event log, the installer
    and source sections and the targets. A refused publish prints its cause on stderr
    (the structured gateway cause when it has one) and returns its exit code.
    """
    checkout = record_source(ctx)
    request = {"schema_version": install_transaction.REQUEST_SCHEMA_VERSION, "host": ctx.platform,
               "ghost_root": str(locations.record_root()), "event_log": str(locations.events_log(ctx.platform)),
               "installer": {"commit": checkout["commit"], "schema_version": installed_payload.SCHEMA_VERSION},
               "source": dict(checkout), "targets": targets}
    if progress:
        request["progress"] = progress
    participants = gateway_participant.participants(locations.record_root())
    try:
        install_transaction.publish(request, participants)
    except install_transaction.InstallTransactionError as exc:
        if hasattr(exc, "code"):
            print(json.dumps(dict(status=exc.outcome, error_code=exc.code, phase=exc.phase, detail=str(exc))),
                  file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return ToolResult(getattr(exc, "exit_code", 1))
    return ToolResult(0)


def copy_target(ctx: "Context", target: "Target", dest: Path, kind: str = "skill") -> bool:
    """One copy publish of `target` at `dest`; `Staged copy replace failed: <dest>` on failure (`install.sh:75-93`)."""
    request = _request_target(ctx, target.name, target.path, dest, kind=kind, mode="copy", provider=target.provider)
    if not publish(ctx, [request]).ok:
        report.error(f"Staged copy replace failed: {dest}")
        return False
    return True


def copy_targets_batch(ctx: "Context", batch: CopyBatch, *, progress_label: str | None = None) -> bool:
    """Every target of the batch in one publish request, with the progress label and the
    event file when they are set (`install.sh:105-223`)."""
    if not batch.pairs:
        return True
    targets = [_request_target(ctx, label, src, dest, kind=kind, mode="copy", provider=provider, status=status)
               for (src, dest), label, kind, status, provider
               in zip(batch.pairs, batch.labels, batch.kinds, batch.statuses, batch.providers)]
    progress = {}
    if progress_label:
        progress["label"] = progress_label
    event_file = report.state().event_file
    if event_file is not None:
        progress["event_file"] = str(event_file)
    if not publish(ctx, targets, progress).ok:
        report.error("Staged copy batch replace failed")
        return False
    return True


def link_or_copy(ctx: "Context", target: "Target", dest: Path, kind: str = "skill") -> int:
    """A link publish; only exit code 1 falls back to a copy publish, other codes are
    returned as they are (`install.sh:640-653`)."""
    request = _request_target(ctx, target.name, target.path, dest, kind=kind, mode="link", provider=target.provider)
    result = publish(ctx, [request])
    if result.ok:
        return 0
    if result.returncode != 1:
        return result.returncode
    return 0 if copy_target(ctx, target, dest, kind) else 1


def record_current_links(ctx: "Context", links: list[tuple["Target", Path, str]]) -> None:
    """One publish request for the `(target, install location, kind)` links that placement found
    already pointing at their source: the publish records them and replaces nothing. A failure is
    reported and the install goes on."""
    if not links:
        return
    targets = [_request_target(ctx, target.name, target.path, dest, kind=kind, mode="link", provider=target.provider,
                               status="current") for target, dest, kind in links]
    if not publish(ctx, targets).ok:
        report.warn("The install record could not record the targets that were already installed")


def _shared_target() -> "Target":
    from installer.selection import Target

    return Target("_shared", runner.REPO_ROOT / "_shared")


def install_shared(ctx: "Context", skills_dir: Path, copy_only: bool) -> int:
    """`_shared` placement: copy in copy mode, skip when already linked to the
    source, else link-or-copy (`targets.sh:248-261`)."""
    shared = _shared_target()
    shared_dest = Path(skills_dir) / "_shared"
    if not shared.path.is_dir():
        return 0
    if copy_only:
        return 0 if copy_target(ctx, shared, shared_dest, "support") else 1
    if shared_dest.is_symlink() and os.readlink(shared_dest) == str(shared.path):
        report.warn("_shared. Already installed (skipped)")
        return 0
    return link_or_copy(ctx, shared, shared_dest, "support")


def sync_runtime_shared(ctx: "Context") -> bool:
    """Publish `<checkout>/_shared` and the runtime companions under the runtime root in one request
    (`install.sh:95-103`); one publish, so a registered gateway stops and starts once."""
    shared = _shared_target()
    if not shared.path.is_dir():
        return True
    runtime_root = locations.runtime_root()
    targets = [_request_target(ctx, shared.name, shared.path, runtime_root / "_shared", kind="runtime", mode="copy")]
    for relative in RUNTIME_COMPANIONS:
        companion = runner.REPO_ROOT / relative
        if companion.is_file():
            targets.append(_request_target(ctx, relative, companion, runtime_root / relative, kind="runtime", mode="copy"))
    if not publish(ctx, targets).ok:
        report.error(f"Staged copy replace failed: {runtime_root / '_shared'}")
        return False
    report.detail_ok("runtime _shared -> copied")
    return True


GATEWAY_SERVICE_LABEL = "org.ghost-alice.context-gateway"


def _gateway_service_running() -> bool:
    """`launchctl print gui/<uid>/<label>` exits 0 while the service is loaded."""
    domain = f"gui/{os.getuid()}" if hasattr(os, "getuid") else ""
    return runner.run_command(["launchctl", "print", f"{domain}/{GATEWAY_SERVICE_LABEL}"], capture=True).ok


def report_gateway(ctx: "Context") -> tuple[str, bool] | None:
    """One line about the context gateway after the runtime publish of an install and at the end of a repair,
    read-only, and the same line returned as `(summary, running)` for the report tail.

    With a `service` section: INFO with the service's runtime root while the service runs, WARN when it does
    not; the gateway participant already stopped and started it around the runtime publish. Without one:
    WARN when launchd has loaded a gateway service whose program lies under the runtime root
    (`gateway_participant.loaded_service`, the removal's own observation). The installer has no record of
    that service's files, so it neither stops nor starts it; the line names the registration command, which
    records the service and restarts it. An observation that is not clear counts as none and leaves one
    detail line. WARN when the record cannot be read.
    """
    root = locations.record_root()
    try:
        record = installed_payload.read(root)
    except installed_payload.RecordError:
        summary = f"Context gateway: the install record cannot be read: {installed_payload.path(root)}"
        report.warn(summary)
        return summary, False
    service = (record or {}).get("service")
    if not isinstance(service, dict):
        return _report_unrecorded_service()
    summary = f"Context gateway: registered, runtime {service.get('runtime_root')}"
    if _gateway_service_running():
        summary += ", service running"
        report.info(summary)
        return summary, True
    summary += ", service not running"
    report.warn(summary)
    return summary, False


def _report_unrecorded_service() -> tuple[str, bool] | None:
    """The `report_gateway` line for a gateway service that launchd runs from the runtime root while the
    install record has no `service` section; None without one."""
    seen = gateway_participant.loaded_service()
    if seen.state in ("inconclusive", "unreadable"):
        report.info(f"Context gateway: launchd observation was inconclusive ({seen.detail}); "
                    "no unrecorded service reported")
        return None
    if seen.state != "loaded" or not seen.runs_under(locations.runtime_root()):
        return None
    summary = (f"Context gateway: a loaded service runs from the runtime without an install record entry; "
               f"register it with {report.install_command()} gateway --codex-dir <dir> --ledger-root <dir> "
               f"--state-dir <dir> --launch-agent <plist>, which restarts it")
    report.warn(summary)
    return summary, True


def migrate_record(ctx: "Context") -> None:
    """Create the install record once from an earlier installer's records (`installed_payload.migrate`)
    for every host, then the gateway's `service` section once from its installation receipt
    (`installed_payload.migrate_service`); a migration that cannot finish is reported and the command
    goes on."""
    root = locations.record_root()
    try:
        summary = installed_payload.migrate(root, locations.PLATFORMS, installer_commit=record_source(ctx)["commit"])
    except (installed_payload.RecordError, installed_payload.NotLockHolder, OSError) as exc:
        report.warn(f"Install record migration skipped: {exc}")
    else:
        if summary is not None:
            report.info(f"Install record created from earlier install records: {summary['targets']} targets "
                        f"(migrated {summary['migrated']}, unknown {summary['unknown']})")
    try:
        service = installed_payload.migrate_service(root)
    except (installed_payload.RecordError, installed_payload.NotLockHolder, OSError) as exc:
        report.warn(f"Gateway record migration skipped: {exc}")
        return
    if service is not None:
        report.info(f"Gateway record created from its installation receipt: {service['label']} "
                    f"(companion targets added {service['companions']})")


def detect_install_mode(dest: Path, copy_only: bool) -> str:
    """`symlink`, `copy`, `copy-fallback` or `missing` (`install.sh:411-426`)."""
    dest = Path(dest)
    if dest.is_symlink():
        return "symlink"
    if dest.exists():
        return "copy" if copy_only else "copy-fallback"
    return "missing"


def _remove_path(target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    else:
        shutil.rmtree(target)


def remove_installed_target(label: str, target: Path) -> bool:
    """Remove a link or directory and say `<label> removed`; False when nothing was there (`targets.sh:284-294`)."""
    target = Path(target)
    if target.is_symlink() or target.is_dir():
        _remove_path(target)
        report.ok(f"{label} removed")
        return True
    return False


def guard_gateway_removal(ctx: "Context", targets: list[Path]) -> int:
    """The gateway participant's judgment of removing `targets`, in this process: 0 to go on (the
    service was independent or is detached), else the cause's exit code; 0 with no targets
    (`targets.sh:275-282`)."""
    if not targets:
        return 0
    return gateway_participant.guard(locations.record_root(), [Path(target) for target in targets])


def cleanup_shared_if_unused(ctx: "Context", skills_root: Path) -> int:
    """0 when `_shared` was removed, 1 when kept, another code when the gateway guard refused (`targets.sh:322-338`)."""
    from installer import selection

    skills_root = Path(skills_root)
    if selection.has_managed_installs(skills_root):
        return 1
    shared = skills_root / "_shared"
    if shared.is_symlink() or shared.is_dir():
        rc = guard_gateway_removal(ctx, [shared])
        if rc != 0:
            return rc
        _remove_path(shared)
        report.ok("_shared removed")
        return 0
    return 1


def verify_after_copy(ctx: "Context", skills_dir: Path, copy_only: bool, skills: list[str]) -> bool:
    """`merge-companion/scripts/install_verifier.py --platform --state-root --target NAME SRC DEST MODE ...` (`install.sh:428-470`)."""
    skills_dir = Path(skills_dir)
    shared_src = runner.REPO_ROOT / "_shared"
    args = ["--platform", ctx.platform, "--state-root", str(locations.install_state_path(ctx.platform).parent)]
    targets: list[str] = []
    if shared_src.is_dir():
        targets.extend(["--target", "_shared", str(shared_src), str(skills_dir / "_shared"),
                        detect_install_mode(skills_dir / "_shared", copy_only)])
    for target in _targets(ctx, skills):
        dest = skills_dir / target.name
        targets.extend(["--target", target.name, str(target.path), str(dest), detect_install_mode(dest, copy_only)])
    if not targets:
        return True
    if not runner.run_tool("merge-companion/scripts/install_verifier.py", *args, *targets).ok:
        report.error("Post-install source/destination verification failed; aborting before snapshot")
        return False
    return True


def write_ownership_markers(ctx: "Context", skills_dir: Path, copy_only: bool, skills: list[str]) -> bool:
    """`installer_assets_cli.py --platform --source-repo --source-commit --target ... --addon-target ...` (`install.sh:487-536`)."""
    skills_dir = Path(skills_dir)
    info = source.resolve_source()
    targets: list[str] = []
    shared_dest = skills_dir / "_shared"
    if shared_dest.exists() or shared_dest.is_symlink():
        targets.extend(["--target", "_shared", str(shared_dest), detect_install_mode(shared_dest, copy_only)])
    for target in _targets(ctx, skills):
        dest = skills_dir / target.name
        mode = detect_install_mode(dest, copy_only)
        if target.addon_id:
            targets.extend(["--addon-target", target.name, str(dest), mode, target.addon_id])
        else:
            targets.extend(["--target", target.name, str(dest), mode])
    if not targets:
        return True
    result = runner.run_tool("_shared/installer_assets_cli.py", "--platform", ctx.platform,
                             "--source-repo", str(info.root), "--source-commit", info.head, *targets)
    if not result.ok:
        report.error("Ownership marker write failed; aborting before snapshot")
        return False
    return True


def snapshot_after_install(ctx: "Context") -> bool:
    """`merge-companion/scripts/snapshot_cli.py` then the empty pending manifest (`install.sh:364-409`)."""
    pending_root = locations.pending_root(ctx.platform)
    snapshot = pending_root / "snapshot.json"
    try:
        pending_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        report.error(f"merge-companion snapshot directory cannot be created; aborting install: {pending_root}")
        return False
    result = runner.run_tool("merge-companion/scripts/snapshot_cli.py", "--output", str(snapshot),
                             "--platform", ctx.platform, "--skills-dir", str(ctx.skills_dir))
    if not result.ok:
        report.error("merge-companion snapshot capture failed; aborting install")
        return False
    return write_empty_pending_manifest_if_missing(ctx, ctx.platform, pending_root / "manifest.json")


def write_empty_pending_manifest_if_missing(ctx: "Context", platform_dir: str, manifest: Path) -> bool:
    """`pending_manifest_writer.py <manifest> <platform>` when the manifest is missing (`install.sh:395-409`)."""
    if Path(manifest).is_file():
        return True
    return runner.run_tool("_shared/pending_manifest_writer.py", str(manifest), platform_dir).ok


def write_install_state_manifest(ctx: "Context", skills_dir: Path, copy_only: bool, skills: list[str],
                                 hook_change: "HookPathChange | None") -> bool:
    """`install_state_writer.py <platform> <root> <branch> <head> <dirty> <state> NAME SRC DEST MODE ...`
    with the `GHOST_ALICE_SOURCE_REPO_HOOK_*` environment (`install.sh:571-610`): the shared git hook path this
    install owns and the value the full uninstall restores, whenever the install knows that value (`hook_change`)."""
    skills_dir = Path(skills_dir)
    state_path = locations.install_state_path(ctx.platform)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        report.error(f"Could not create install-state directory; aborting install: {state_path.parent}")
        return False
    info = source.resolve_source()
    targets: list[str] = []
    shared_src = runner.REPO_ROOT / "_shared"
    if shared_src.exists():
        targets.extend(["_shared", str(shared_src), str(skills_dir / "_shared"),
                        detect_install_mode(skills_dir / "_shared", copy_only)])
    for target in _targets(ctx, skills):
        dest = skills_dir / target.name
        targets.extend([target.name, str(target.path), str(dest), detect_install_mode(dest, copy_only)])
    recorded = hook_change if hook_change is not None and hook_change.recorded else None
    env = {
        "GHOST_ALICE_SOURCE_REPO_HOOK_RECORDED": "1" if recorded else "0",
        "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE_PRESENT": "1" if recorded and recorded.before_present else "0",
        "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE": recorded.before if recorded else "",
        "GHOST_ALICE_SOURCE_REPO_HOOK_AFTER": recorded.after if recorded else "",
        "GHOST_ALICE_SOURCE_REPO_HOOK_APPLIED_AT": (recorded.applied_at or "") if recorded else "",
    }
    result = runner.run_tool("_shared/install_state_writer.py", ctx.platform, str(info.root), info.branch, info.head,
                             info.dirty, str(state_path), *targets, env=env)
    if not result.ok:
        report.error(f"Install-state manifest write failed; aborting install: {state_path}")
        return False
    report.info(f"Install-state manifest: {state_path}")
    return True
