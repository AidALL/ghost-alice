"""Legacy no-baseline target quarantine CLI wrapper.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from diff_collector import quarantine_legacy_target


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True)
    p.add_argument("--target-name", required=True)
    p.add_argument("--pending", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--platform", required=True)
    args = p.parse_args()
    quarantine_legacy_target(
        target_path=Path(args.target),
        target_name=args.target_name,
        pending_dir=Path(args.pending),
        manifest_path=Path(args.manifest),
        platform=args.platform,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
