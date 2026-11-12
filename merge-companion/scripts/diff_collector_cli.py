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
from file_walker import walk_installed_copy_files
import installed_payload
from hook_definitions import SUPPORTED_PLATFORMS


def _walk_installed_copy_files(skills_dir: Path, targets: list[str] | tuple[str, ...] = ()) -> list[Path]:
    return walk_installed_copy_files(skills_dir, targets)


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
    files = _walk_installed_copy_files(skills_dir, args.target)
    published = {}
    if args.install_record_root:
        try:
            record = installed_payload.read(Path(args.install_record_root)) or {}
            published = {path: entry for path, entry in record.get("targets", {}).items()
                         if isinstance(entry, dict) and isinstance(entry.get("used_by"), list)
                         and all(isinstance(host, str) and host in SUPPORTED_PLATFORMS for host in entry["used_by"])
                         and args.platform in entry["used_by"]
                         and (not args.target or entry.get("name") in args.target)}
        except installed_payload.RecordError as exc:
            print(f"[WARN] Cannot read published baseline; preserving snapshot comparison: {exc}")
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
