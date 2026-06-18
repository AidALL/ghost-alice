#!/usr/bin/env python3
"""CLI wrapper for installer asset ownership marker operations."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from installer_assets import OWNERSHIP_GHOST_ALICE_MANAGED, classify_skill_root, write_ownership_marker


MARKER_INSTALL_MODES = {"copy", "copy-fallback"}


def write_markers(args: argparse.Namespace) -> int:
    # Unify core (--target, owner=ghost-alice) and addon (--addon-target,
    # owner=addon, addon_id=<id>) targets into one ordered write list so a
    # copy-mode addon skill is attributed to its addon for classify (T2.9).
    jobs: list[tuple[str, str, str, str, str | None]] = [
        (asset_id, dest_path, install_mode, "ghost-alice", None)
        for asset_id, dest_path, install_mode in args.target
    ]
    jobs += [
        (asset_id, dest_path, install_mode, "addon", addon_id)
        for asset_id, dest_path, install_mode, addon_id in args.addon_target
    ]

    wrote = 0
    for asset_id, dest_path, install_mode, owner, addon_id in jobs:
        if install_mode not in MARKER_INSTALL_MODES:
            continue

        dest = Path(dest_path)
        if dest.is_symlink() or not dest.is_dir():
            print(
                f"cannot write ownership marker for {asset_id}: {dest} is not a regular directory",
                file=sys.stderr,
            )
            return 1

        write_ownership_marker(
            dest,
            platform=args.platform,
            asset_id=asset_id,
            source_repo=args.source_repo,
            source_commit=args.source_commit,
            install_mode=install_mode,
            owner=owner,
            addon_id=addon_id,
            provided_kind="skill",
        )
        wrote += 1

    print(f"ownership markers written: {wrote}")
    return 0


def classify_clean(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="installer_assets_cli.py classify-clean")
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    result = classify_skill_root(
        Path(args.path),
        expected_asset_id=args.asset_id,
        repo_root=Path(args.repo_root) if args.repo_root else None,
    )
    print(f"{result.asset_id}: {result.ownership} ({result.reason})")
    return 0 if result.ownership == OWNERSHIP_GHOST_ALICE_MANAGED else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--target",
        action="append",
        nargs=3,
        default=[],
        metavar=("ASSET_ID", "DEST_PATH", "INSTALL_MODE"),
    )
    parser.add_argument(
        "--addon-target",
        action="append",
        nargs=4,
        default=[],
        metavar=("ASSET_ID", "DEST_PATH", "INSTALL_MODE", "ADDON_ID"),
    )
    return parser


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "classify-clean":
        return classify_clean(sys.argv[2:])
    return write_markers(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
