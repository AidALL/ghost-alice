"""CLI for collecting installed-file changes.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from diff_collector import collect_external_changes, register_changes_in_manifest
from file_walker import copy_comparison_scope, walk_installed_copy_files


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", required=True)
    p.add_argument("--pending", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--skills-dir", required=True)
    p.add_argument("--install-record-root", help="record root for copies published after the full-install snapshot")
    p.add_argument("--target", action="append", default=[],
                   help="skill root this install replaces; checked even without its install marker")
    args = p.parse_args()
    skills_dir = Path(args.skills_dir)
    published, excluded = copy_comparison_scope(skills_dir, args.platform,
        Path(args.install_record_root) if args.install_record_root else None, args.target)
    files = walk_installed_copy_files(skills_dir, args.target, excluded_roots=excluded)
    changes = collect_external_changes(Path(args.snapshot), files, skills_dir=skills_dir, published_targets=published)
    register_changes_in_manifest(
        changes,
        pending_dir=Path(args.pending),
        manifest_path=Path(args.manifest),
        platform=args.platform,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
