"""What the installer checks and backs up before it replaces targets.

Port of `installer_lib/install.sh:225-362` and `installer_lib/targets.sh:378-395`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from installer import locations, report, runner

if TYPE_CHECKING:
    from installer.cli import Context

ENCODING_GUARD = "_shared/encoding_guard.py"


def _targets(ctx: "Context", skills: list[str]):
    from installer import selection

    return selection.iter_install_targets(ctx.source_root, skills, ctx.addon_targets)


def encoding_guard(ctx: "Context", skills: list[str]) -> bool:
    """`encoding_guard.py --repo-root <source>`, each addon source, then the
    skills dir excluding `_shared` and the install targets (`install.sh:225-271`)."""
    guard = Path(ctx.source_root) / ENCODING_GUARD
    if not guard.is_file():
        report.error(f"encoding guard not found; aborting install: {guard}")
        return False
    if not runner.run_tool(ENCODING_GUARD, "--repo-root", str(ctx.source_root)).ok:
        report.error(f"encoding guard failed; aborting install: {ctx.source_root}")
        return False
    for target in ctx.addon_targets:
        if not runner.run_tool(ENCODING_GUARD, "--repo-root", str(target.path)).ok:
            report.error(f"encoding guard failed; aborting install: {target.path}")
            return False
    skills_dir = Path(ctx.skills_dir)
    if not skills_dir.is_dir():
        return True
    args = ["--repo-root", str(skills_dir)]
    if (skills_dir / "_shared").exists() or (skills_dir / "_shared").is_symlink():
        args.extend(["--exclude-root", str(skills_dir / "_shared")])
    for target in _targets(ctx, skills):
        args.extend(["--exclude-root", str(skills_dir / target.name)])
    if not runner.run_tool(ENCODING_GUARD, *args).ok:
        report.error(f"encoding guard failed; aborting install: {skills_dir}")
        return False
    return True


def is_clean_managed_target(ctx: "Context", name: str, path: Path) -> bool:
    """`installer_assets_cli.py classify-clean`: the occupied slot is a clean Ghost-ALICE managed target (`install.sh:287-296`)."""
    return runner.run_tool("_shared/installer_assets_cli.py", "classify-clean", "--asset-id", name,
                           "--path", str(path), "--repo-root", str(ctx.source_root), capture=True).ok


_is_clean_managed_target = is_clean_managed_target


def preflight(ctx: "Context", skills: list[str]) -> bool:
    """No snapshot: quarantine occupied, unmanaged targets through
    `quarantine_legacy_cli.py`. With a snapshot: `diff_collector_cli.py` with
    every root this install replaces (`install.sh:273-362`)."""
    pending_root = locations.pending_root(ctx.platform)
    manifest = pending_root / "manifest.json"
    snapshot = pending_root / "snapshot.json"
    pending_root.mkdir(parents=True, exist_ok=True)
    skills_dir = Path(ctx.skills_dir)
    targets = _targets(ctx, skills)
    if not snapshot.is_file():
        legacy: list[tuple[str, Path]] = []
        shared_dest = skills_dir / "_shared"
        if shared_dest.exists() and not shared_dest.is_symlink():
            if not _is_clean_managed_target(ctx, "_shared", shared_dest):
                legacy.append(("_shared", shared_dest))
        for target in targets:
            dest = skills_dir / target.name
            if dest.exists() and not dest.is_symlink():
                if not _is_clean_managed_target(ctx, target.name, dest):
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
    if (Path(ctx.source_root) / "_shared").is_dir():
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
    """`merge-companion/scripts/cleanup_false_positive_legacy.py ... --apply` (`targets.sh:378-395`)."""
    pending = locations.pending_root(ctx.platform)
    return runner.run_tool("merge-companion/scripts/cleanup_false_positive_legacy.py", "--platform", ctx.platform,
                           "--pending", str(pending), "--manifest", str(pending / "manifest.json"),
                           "--repo-root", str(ctx.source_root), "--apply").returncode
