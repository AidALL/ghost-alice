"""Install-time snapshot CLI wrapper. Called by install.sh.

Dependencies: Python 3.11+ standard library + local merge-companion modules.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

# CRITICAL: sys.path bootstrap so imports work when install.sh calls this from another cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from snapshot import capture_snapshot
from file_walker import walk_user_files


def _walk_user_files(skills_dir: Path) -> list[Path]:
    return walk_user_files(skills_dir)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--skills-dir", required=True)
    args = p.parse_args()
    skills_dir = Path(args.skills_dir)
    files = _walk_user_files(skills_dir)
    capture_snapshot(Path(args.output), files, args.platform, skills_dir=skills_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
