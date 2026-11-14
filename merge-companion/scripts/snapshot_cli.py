"""Install-time snapshot CLI wrapper.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

# CRITICAL: sys.path bootstrap so imports work when install.sh calls this from another cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from snapshot import SnapshotError, capture_snapshot, prune_snapshot
from file_walker import copy_comparison_scope, walk_installed_copy_files


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--skills-dir")
    p.add_argument("--install-record-root", type=Path)
    p.add_argument("--prune", action="store_true", help="Remove confirmed destinations from the snapshot")
    p.add_argument("--prune-target", action="append", nargs=2, metavar=("KIND", "PATH"), default=[])
    p.add_argument("--provider")
    args = p.parse_args()
    if args.prune:
        if not args.prune_target:
            p.error("--prune requires at least one --prune-target KIND PATH")
        try:
            prune_snapshot(Path(args.output), args.prune_target, args.platform, provider=args.provider)
        except (SnapshotError, OSError) as exc:
            print(f"snapshot cleanup failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.prune_target or args.provider:
        p.error("--prune-target and --provider require --prune")
    if not args.skills_dir:
        p.error("capture requires --skills-dir")
    skills_dir = Path(args.skills_dir)
    _, excluded = copy_comparison_scope(skills_dir, args.platform, args.install_record_root)
    files = walk_installed_copy_files(skills_dir, excluded_roots=excluded)
    capture_snapshot(Path(args.output), files, args.platform, skills_dir=skills_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
