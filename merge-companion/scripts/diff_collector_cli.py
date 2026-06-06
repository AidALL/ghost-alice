"""Diff detection CLI wrapper. Called by install.sh.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from diff_collector import collect_user_changes, register_changes_in_manifest
from file_walker import walk_user_files


def _walk_user_files(skills_dir: Path) -> list[Path]:
    return walk_user_files(skills_dir)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", required=True)
    p.add_argument("--pending", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--skills-dir", required=True)
    args = p.parse_args()
    skills_dir = Path(args.skills_dir)
    files = _walk_user_files(skills_dir)
    changes = collect_user_changes(Path(args.snapshot), files, skills_dir=skills_dir)
    register_changes_in_manifest(
        changes,
        pending_dir=Path(args.pending),
        manifest_path=Path(args.manifest),
        platform=args.platform,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
