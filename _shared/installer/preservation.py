"""What the installer checks and backs up before it replaces targets."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from installer import locations, report, runner

if TYPE_CHECKING:
    from installer.cli import Context
    from installer.selection import Collected

ENCODING_GUARD = "_shared/encoding_guard.py"


def encoding_guard(ctx: "Context", collected: "Collected") -> bool:
    """`encoding_guard.py --repo-root <checkout>`, each addon source, then the
    skills dir excluding `_shared` and the install targets."""
    guard = runner.REPO_ROOT / ENCODING_GUARD
    if not guard.is_file():
        report.error(f"encoding guard not found; aborting install: {guard}")
        return False
    if not runner.run_tool(ENCODING_GUARD, "--repo-root", str(runner.REPO_ROOT)).ok:
        report.error(f"encoding guard failed; aborting install: {runner.REPO_ROOT}")
        return False
    for target in collected.addon_targets:
        if not runner.run_tool(ENCODING_GUARD, "--repo-root", str(target.path)).ok:
            report.error(f"encoding guard failed; aborting install: {target.path}")
            return False
    skills_dir = Path(ctx.skills_dir)
    if not skills_dir.is_dir():
        return True
    args = ["--repo-root", str(skills_dir)]
    if collected.support_target_total and ((skills_dir / "_shared").exists() or (skills_dir / "_shared").is_symlink()):
        args.extend(["--exclude-root", str(skills_dir / "_shared")])
    for target in collected.all_targets:
        args.extend(["--exclude-root", str(skills_dir / target.name)])
    if not runner.run_tool(ENCODING_GUARD, *args).ok:
        report.error(f"encoding guard failed; aborting install: {skills_dir}")
        return False
    return True


def is_clean_managed_target(ctx: "Context", name: str, path: Path) -> bool:
    """`installer_assets_cli.py classify-clean`: the occupied slot is a clean Ghost-ALICE managed target."""
    return runner.run_tool("_shared/installer_assets_cli.py", "classify-clean", "--asset-id", name,
                           "--path", str(path), "--repo-root", str(runner.REPO_ROOT), capture=True).ok


def preflight(ctx: "Context", collected: "Collected") -> bool:
    """No snapshot: quarantine occupied, unmanaged targets through
    `quarantine_legacy_cli.py`. With a snapshot: `diff_collector_cli.py` with
    every root this install replaces."""
    pending_root = locations.pending_root(ctx.platform)
    manifest = pending_root / "manifest.json"
    snapshot = pending_root / "snapshot.json"
    pending_root.mkdir(parents=True, exist_ok=True)
    skills_dir = Path(ctx.skills_dir)
    targets = collected.all_targets
    if not snapshot.is_file():
        legacy: list[tuple[str, Path]] = []
        shared_dest = skills_dir / "_shared"
        if collected.support_target_total and shared_dest.exists() and not shared_dest.is_symlink():
            if not is_clean_managed_target(ctx, "_shared", shared_dest):
                legacy.append(("_shared", shared_dest))
        for target in targets:
            dest = skills_dir / target.name
            if dest.exists() and not dest.is_symlink():
                if not is_clean_managed_target(ctx, target.name, dest):
                    legacy.append((target.name, dest))
        for name, path in legacy:
            result = runner.run_tool("merge-companion/scripts/quarantine_legacy_cli.py", "--target", str(path),
                                     "--target-name", name, "--pending", str(pending_root),
                                     "--manifest", str(manifest), "--platform", ctx.platform)
            if not result.ok:
                report.error(f"unowned target quarantine failed; aborting install: {name}")
                return False
        return True
    replaced: list[str] = []
    if collected.support_target_total:
        replaced.extend(["--target", "_shared"])
    for target in targets:
        replaced.extend(["--target", target.name])
    result = runner.run_tool("merge-companion/scripts/diff_collector_cli.py", "--snapshot", str(snapshot),
                             "--pending", str(pending_root), "--manifest", str(manifest),
                             "--platform", ctx.platform, "--skills-dir", str(skills_dir), *replaced)
    if not result.ok:
        report.error("merge-companion preflight change detection failed; aborting install")
        return False
    return True


def cleanup_pending(ctx: "Context") -> int:
    """`merge-companion/scripts/cleanup_false_positive_legacy.py ... --apply`."""
    pending = locations.pending_root(ctx.platform)
    return runner.run_tool("merge-companion/scripts/cleanup_false_positive_legacy.py", "--platform", ctx.platform,
                           "--pending", str(pending), "--manifest", str(pending / "manifest.json"),
                           "--repo-root", str(runner.REPO_ROOT), "--apply").returncode
