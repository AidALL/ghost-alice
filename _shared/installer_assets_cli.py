#!/usr/bin/env python3
"""Read-only CLI for installer asset ownership classification."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from installer_assets import OWNERSHIP_GHOST_ALICE_MANAGED, classify_skill_root


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
        whole_copy=True,
    )
    print(f"{result.asset_id}: {result.ownership} ({result.reason})")
    return 0 if result.ownership == OWNERSHIP_GHOST_ALICE_MANAGED else 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "classify-clean":
        return classify_clean(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.error('expected classify-clean')


if __name__ == "__main__":
    sys.exit(main())
