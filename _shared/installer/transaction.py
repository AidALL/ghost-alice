"""Place the selected targets, and write the records after placement until one install record replaces them.

Port of `installer_lib/install.sh:75-223, 411-610, 640-653` and
`installer_lib/targets.sh:248-261, 275-338`. The placement itself is
`install_transaction.py`; this module decides what to call for which target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

from installer import locations, report, runner, source

if TYPE_CHECKING:
    from installer.cli import Context
    from installer.selection import Target
    from installer.source import HookPathChange


@dataclass
class CopyBatch:
    """Targets a copy-only install replaces in one `copy-replace-many` call."""

    pairs: list[tuple[Path, Path]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)

    def add(self, src: Path, dest: Path, label: str, kind: str, status: str) -> None:
        self.pairs.append((Path(src), Path(dest)))
        self.labels.append(label)
        self.kinds.append(kind)
        self.statuses.append(status)


@dataclass
class SyncCounts:
    installed: int = 0
    updated: int = 0
    skipped: int = 0


def _targets(ctx: "Context", skills: list[str]) -> "list[Target]":
    from installer import selection

    return selection.iter_install_targets(ctx.source_root, skills, ctx.addon_targets)


def copy_target(ctx: "Context", source_path: Path, dest: Path) -> bool:
    """`install_transaction.py copy-replace --source --dest --rollback-root --event-log`;
    `Staged copy replace failed: <dest>` on failure (`install.sh:75-93`)."""
    result = runner.run_tool("_shared/install_transaction.py", "copy-replace", "--source", str(source_path),
                             "--dest", str(dest), "--rollback-root", str(locations.rollback_root()),
                             "--event-log", str(locations.events_log(ctx.platform)))
    if not result.ok:
        report.error(f"Staged copy replace failed: {dest}")
        return False
    return True


def copy_targets_batch(ctx: "Context", batch: CopyBatch, *, progress_label: str | None = None) -> bool:
    """`copy-replace-many` with the progress arguments when a label or event file is set (`install.sh:105-223`)."""
    if not batch.pairs:
        return True
    args = ["copy-replace-many", "--rollback-root", str(locations.rollback_root()),
            "--event-log", str(locations.events_log(ctx.platform))]
    if progress_label:
        args.extend(["--progress-label", progress_label])
        for status in batch.statuses:
            args.extend(["--progress-status", status])
    event_file = report.state().event_file
    if event_file is not None:
        args.extend(["--progress-event-file", str(event_file), "--progress-platform", ctx.platform])
        for label, kind, status in zip(batch.labels, batch.kinds, batch.statuses):
            args.extend(["--progress-target-id", label, "--progress-target-kind", kind, "--progress-target-status", status])
    for src, dest in batch.pairs:
        args.extend(["--target", str(src), str(dest)])
    if not runner.run_tool("_shared/install_transaction.py", *args).ok:
        report.error("Staged copy batch replace failed")
        return False
    return True


def link_or_copy(ctx: "Context", source_path: Path, dest: Path) -> int:
    """`symlink-replace`; only exit code 1 falls back to a staged copy, other
    codes are returned as they are (`install.sh:640-653`)."""
    result = runner.run_tool("_shared/install_transaction.py", "symlink-replace", "--source", str(source_path),
                             "--dest", str(dest), "--event-log", str(locations.events_log(ctx.platform)))
    if result.ok:
        return 0
    if result.returncode != 1:
        return result.returncode
    return 0 if copy_target(ctx, source_path, dest) else 1


def install_shared(ctx: "Context", skills_dir: Path, copy_only: bool) -> int:
    """`_shared` placement: copy in copy mode, skip when already linked to the
    source, else link-or-copy (`targets.sh:248-261`)."""
    shared_src = Path(ctx.source_root) / "_shared"
    shared_dest = Path(skills_dir) / "_shared"
    if not shared_src.is_dir():
        return 0
    if copy_only:
        return 0 if copy_target(ctx, shared_src, shared_dest) else 1
    if shared_dest.is_symlink() and os.readlink(shared_dest) == str(shared_src):
        report.warn("_shared. Already installed (skipped)")
        return 0
    return link_or_copy(ctx, shared_src, shared_dest)


def sync_runtime_shared(ctx: "Context") -> bool:
    """Copy `<source>/_shared` to the runtime shared dir (`install.sh:95-103`)."""
    shared_src = Path(ctx.source_root) / "_shared"
    if not shared_src.is_dir():
        return True
    if not copy_target(ctx, shared_src, locations.runtime_shared_dir()):
        return False
    report.detail_ok("runtime _shared -> copied")
    return True


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
    """`context_gateway_install.py detach --root ... --confirm`; 0 with no targets (`targets.sh:275-282`)."""
    if not targets:
        return 0
    args = ["detach"]
    for target in targets:
        args.extend(["--root", str(target)])
    args.append("--confirm")
    return runner.run_tool("_shared/context_gateway_install.py", *args).returncode


def cleanup_shared_if_unused(ctx: "Context", skills_root: Path) -> int:
    """0 when `_shared` was removed, 1 when kept, another code when the gateway guard refused (`targets.sh:322-338`)."""
    from installer import selection

    skills_root = Path(skills_root)
    if selection.has_managed_installs(ctx.source_root, skills_root):
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
    shared_src = Path(ctx.source_root) / "_shared"
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
    info = source.resolve_source(ctx.source_root)
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
    with the `GHOST_ALICE_SOURCE_REPO_HOOK_*` environment (`install.sh:571-610`)."""
    skills_dir = Path(skills_dir)
    state_path = locations.install_state_path(ctx.platform)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        report.error(f"Could not create install-state directory; aborting install: {state_path.parent}")
        return False
    info = source.resolve_source(ctx.source_root)
    targets: list[str] = []
    shared_src = Path(ctx.source_root) / "_shared"
    if shared_src.exists():
        targets.extend(["_shared", str(shared_src), str(skills_dir / "_shared"),
                        detect_install_mode(skills_dir / "_shared", copy_only)])
    for target in _targets(ctx, skills):
        dest = skills_dir / target.name
        targets.extend([target.name, str(target.path), str(dest), detect_install_mode(dest, copy_only)])
    changed = bool(hook_change and hook_change.changed)
    env = {
        "GHOST_ALICE_SOURCE_REPO_HOOK_CHANGED": "1" if changed else "0",
        "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE_PRESENT": "1" if hook_change and hook_change.before_present else "0",
        "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE": hook_change.before if hook_change else "",
        "GHOST_ALICE_SOURCE_REPO_HOOK_AFTER": hook_change.after if hook_change else source.HOOKS_DIR,
    }
    result = runner.run_tool("_shared/install_state_writer.py", ctx.platform, str(info.root), info.branch, info.head,
                             info.dirty, str(state_path), *targets, env=env)
    if not result.ok:
        report.error(f"Install-state manifest write failed; aborting install: {state_path}")
        return False
    report.info(f"Install-state manifest: {state_path}")
    return True
