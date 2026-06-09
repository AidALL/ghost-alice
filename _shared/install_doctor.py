#!/usr/bin/env python3
"""Read-only installer status and doctor diagnostics."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from encoding_guard import validate_repo
from installer_assets import (
    OWNERSHIP_ABSENT,
    OWNERSHIP_GHOST_ALICE_MANAGED,
    OWNERSHIP_CONFLICT,
    OWNERSHIP_LEGACY_NO_BASELINE,
    OWNERSHIP_USER_MODIFIED_MANAGED,
    OWNERSHIP_USER_OWNED,
    AssetClassification,
    classify_global_rule_file,
    classify_skill_root,
)

from mcp_health import summarize_state


STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_RANK = {
    STATUS_OK: 0,
    STATUS_WARNING: 1,
    STATUS_ERROR: 2,
}
SUPPORTED_PLATFORMS = ("claude", "codex")


def _max_status(statuses: Sequence[str]) -> str:
    if not statuses:
        return STATUS_OK
    return max(statuses, key=lambda status: STATUS_RANK[status])


def _ownership_status(item: AssetClassification) -> str:
    if item.ownership == OWNERSHIP_GHOST_ALICE_MANAGED:
        return STATUS_OK
    if item.ownership in {
        OWNERSHIP_LEGACY_NO_BASELINE,
        OWNERSHIP_USER_MODIFIED_MANAGED,
        OWNERSHIP_USER_OWNED,
    }:
        return STATUS_WARNING
    if item.ownership in {OWNERSHIP_ABSENT, OWNERSHIP_CONFLICT}:
        return STATUS_ERROR
    return STATUS_WARNING


def _pending_manifest_status(manifest: Path) -> tuple[str, str, int]:
    if not manifest.exists():
        return STATUS_OK, "clean", 0
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return STATUS_ERROR, f"invalid-manifest ({exc.__class__.__name__})", 0

    entries = data.get("entries", [])
    if not isinstance(entries, list):
        return STATUS_ERROR, "invalid-manifest (entries-not-list)", 0
    undecided = [entry for entry in entries if isinstance(entry, dict) and not entry.get("decided", False)]
    if undecided:
        return STATUS_WARNING, f"pending ({len(undecided)} undecided)", len(undecided)
    return STATUS_OK, "clean", 0


def _pending_merge_status(ghost_alice_root: Path, platform: str) -> tuple[str, str]:
    manifest = ghost_alice_root / "pending-merges" / platform / "manifest.json"
    status, detail, _undecided_count = _pending_manifest_status(manifest)
    return status, detail


def _cross_platform_pending_merge_advisories(
    ghost_alice_root: Path,
    *,
    current_platform: str,
    current_detail: str,
) -> list[str]:
    advisories: list[str] = []
    pending_root = ghost_alice_root / "pending-merges"
    for platform in SUPPORTED_PLATFORMS:
        if platform == current_platform:
            continue
        manifest = pending_root / platform / "manifest.json"
        _status, _detail, undecided_count = _pending_manifest_status(manifest)
        if undecided_count <= 0:
            continue
        entry_word = "entry" if undecided_count == 1 else "entries"
        advisories.append(
            "merge-companion cross-platform advisory: "
            f"{platform} has {undecided_count} undecided {entry_word}; "
            f"current platform {current_platform} is {current_detail}."
        )
    return advisories


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_install_marker_time(
    items: Sequence[AssetClassification],
    *,
    platform: str | None = None,
) -> datetime | None:
    times = [
        parsed
        for item in items
        if isinstance(item.marker, dict)
        if platform is None or item.marker.get("platform") in (None, platform)
        for parsed in [_parse_timestamp(item.marker.get("installed_at"))]
        if parsed is not None
    ]
    return max(times) if times else None


def _snapshot_status(
    ghost_alice_root: Path,
    platform: str,
    *,
    latest_install_at: datetime | None = None,
) -> tuple[str, str]:
    snapshot = ghost_alice_root / "pending-merges" / platform / "snapshot.json"
    if not snapshot.exists():
        return STATUS_WARNING, "missing"

    try:
        data = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return STATUS_ERROR, f"invalid ({exc.__class__.__name__})"
    if not isinstance(data, dict):
        return STATUS_ERROR, "invalid (not-object)"
    if latest_install_at is None:
        return STATUS_OK, "present"

    captured_at = _parse_timestamp(data.get("captured_at"))
    if captured_at is None:
        return STATUS_WARNING, "stale (captured-at-missing)"
    if captured_at < latest_install_at:
        return STATUS_WARNING, "stale (captured-before-latest-install)"
    return STATUS_OK, "present"


def _encoding_roots(raw_roots: Sequence[Path] | None, repo_root: Path) -> list[Path]:
    roots = list(raw_roots) if raw_roots else [repo_root]
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        resolved = Path(root).resolve()
        key = resolved.as_posix()
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _install_state_target_names(manifest_path: Path | None) -> tuple[str | None, str | None, set[str] | None]:
    if manifest_path is None:
        return None, None, None

    manifest = manifest_path.expanduser()
    if not manifest.exists():
        return STATUS_WARNING, "missing (legacy target fallback)", None

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return STATUS_ERROR, f"invalid ({exc.__class__.__name__})", None
    if not isinstance(data, dict):
        return STATUS_ERROR, "invalid (not-object)", None

    targets = data.get("targets")
    if not isinstance(targets, list):
        return STATUS_ERROR, "invalid (targets-not-list)", None

    names: set[str] = set()
    for entry in targets:
        if not isinstance(entry, dict):
            return STATUS_ERROR, "invalid (target-not-object)", None
        target_name = entry.get("target_name")
        if not isinstance(target_name, str) or not target_name.strip():
            return STATUS_ERROR, "invalid (target-name-missing)", None
        names.add(target_name.strip())

    return STATUS_OK, f"present ({len(names)} targets)", names


def _repo_direct_skill_names(repo_root: Path) -> set[str]:
    names: set[str] = set()
    try:
        entries = list(repo_root.iterdir())
    except OSError:
        return names
    for entry in entries:
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if (entry / "SKILL.md").is_file():
            names.add(entry.name)
    return names


def _skill_layout_audit(skills_root: Path, repo_root: Path) -> list[dict[str, str]]:
    root = skills_root.expanduser()
    if not root.exists():
        return []
    repo_skill_names = _repo_direct_skill_names(repo_root)
    findings: list[dict[str, str]] = []
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return [
            {
                "status": STATUS_ERROR,
                "skill": "<skills-root>",
                "layout": "unreadable",
                "path": str(root),
                "reason": exc.__class__.__name__,
            }
        ]
    for entry in entries:
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        if entry.is_symlink():
            findings.append(
                {
                    "status": STATUS_OK,
                    "skill": entry.name,
                    "layout": "symlink",
                    "path": str(entry),
                    "reason": "repo-managed symlink style",
                }
            )
            continue
        reason = "repo skill installed as direct directory" if entry.name in repo_skill_names else "unmanaged direct directory"
        findings.append(
            {
                "status": STATUS_WARNING,
                "skill": entry.name,
                "layout": "direct-directory",
                "path": str(entry),
                "reason": reason,
            }
        )
    return findings


def _node_runtime_status(strict: bool) -> tuple[str, str]:
    """Report whether the node runtime needed by the tool-checkpoint dispatcher exists.

    The PreToolUse gate runs `node ghost-alice-hook.mjs`. The installer blocks hook
    install when node is missing, but node can be removed from PATH afterward. Since
    Claude Code treats a non-2 PreToolUse exit as non-blocking, a missing node makes
    the gate fail open silently, so doctor flags it. Under --strict it is an error.
    """
    if shutil.which("node") or shutil.which("node.exe"):
        return STATUS_OK, "ok"
    status = STATUS_ERROR if strict else STATUS_WARNING
    return status, "missing (required for tool-checkpoint hook dispatcher; gate fails open without it)"


def run(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    ghost_alice_root = args.ghost_alice_root.expanduser()
    encoding_roots = _encoding_roots(args.encoding_root, repo_root)

    statuses: list[str] = []
    print(f"doctor: platform={args.platform}")

    install_state_status, install_state_detail, install_state_targets = _install_state_target_names(
        args.install_state_manifest
    )
    if install_state_status is not None and install_state_detail is not None:
        statuses.append(install_state_status)
        print(f"install-state: {install_state_detail}")

    target_args = args.target
    if install_state_targets is not None:
        target_args = [target for target in args.target if target[0] in install_state_targets]

    ownership_results = [
        classify_skill_root(Path(dest), expected_asset_id=asset_id, repo_root=repo_root)
        for asset_id, dest in target_args
    ]
    ownership_statuses = [_ownership_status(item) for item in ownership_results]
    ownership_overall = _max_status(ownership_statuses)
    statuses.append(ownership_overall)
    print(f"ownership: {ownership_overall}")
    for item, status in zip(ownership_results, ownership_statuses, strict=True):
        print(f"  {status} {item.asset_id}: {item.ownership} {item.reason}")

    global_rule_results = [
        (
            asset_id,
            classify_global_rule_file(
                Path(rule_path),
                full_file_marker=full_file_marker,
                managed_block_begin=managed_block_begin,
                managed_block_end=managed_block_end,
            ),
        )
        for asset_id, rule_path, full_file_marker, managed_block_begin, managed_block_end in args.global_rule
    ]
    global_rule_statuses = [_ownership_status(item) for _, item in global_rule_results]
    global_rule_overall = _max_status(global_rule_statuses)
    statuses.append(global_rule_overall)
    print(f"global-rule: {global_rule_overall}")
    for (asset_id, item), status in zip(global_rule_results, global_rule_statuses, strict=True):
        print(f"  {status} {asset_id}: {item.ownership} {item.reason}")

    pending_status, pending_detail = _pending_merge_status(ghost_alice_root, args.platform)
    statuses.append(pending_status)
    print(f"pending-merge: {pending_detail}")
    cross_platform_advisories = _cross_platform_pending_merge_advisories(
        ghost_alice_root,
        current_platform=args.platform,
        current_detail=pending_detail,
    )
    if cross_platform_advisories:
        statuses.append(STATUS_WARNING)
        for advisory in cross_platform_advisories:
            print(advisory)

    snapshot_status, snapshot_detail = _snapshot_status(
        ghost_alice_root,
        args.platform,
        latest_install_at=_latest_install_marker_time(ownership_results, platform=args.platform),
    )
    statuses.append(snapshot_status)
    print(f"snapshot: {snapshot_detail}")

    for skill_root in args.skill_root:
        layout_findings = _skill_layout_audit(skill_root, repo_root)
        layout_status = _max_status([finding["status"] for finding in layout_findings])
        statuses.append(layout_status)
        print(f"skill-layout: {layout_status} {skill_root}")
        for finding in layout_findings:
            print(
                f"  {finding['status']} {finding['skill']}: {finding['layout']} {finding['reason']}"
            )

    if args.mcp_health_state is not None:
        mcp_status, mcp_detail = summarize_state(args.mcp_health_state)
        statuses.append(mcp_status)
        print(f"mcp-health: {mcp_detail}")

    encoding_issues = [
        (encoding_root, issue)
        for encoding_root in encoding_roots
        for issue in validate_repo(encoding_root)
    ]
    if encoding_issues:
        statuses.append(STATUS_ERROR)
        print("encoding: error")
        for encoding_root, issue in encoding_issues:
            print(f"  {issue.format(root=encoding_root)}")
    else:
        statuses.append(STATUS_OK)
        print("encoding: ok")

    node_status, node_detail = _node_runtime_status(args.strict)
    statuses.append(node_status)
    print(f"node-runtime: {node_detail}")

    overall = _max_status(statuses)
    print(f"overall: {overall}")
    if args.strict and overall == STATUS_ERROR:
        return 1
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run read-only Ghost-ALICE installer diagnostics.")
    parser.add_argument("--platform", required=True, choices=["claude", "codex"])
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--encoding-root", action="append", type=Path, default=None)
    parser.add_argument("--ghost-alice-root", type=Path, default=Path.home() / ".ghost-alice")
    parser.add_argument("--install-state-manifest", type=Path, default=None)
    parser.add_argument("--target", action="append", nargs=2, default=[], metavar=("ASSET_ID", "DEST_PATH"))
    parser.add_argument("--skill-root", action="append", type=Path, default=[])
    parser.add_argument("--mcp-health-state", type=Path, default=None)
    parser.add_argument(
        "--global-rule",
        action="append",
        nargs=5,
        default=[],
        metavar=("ASSET_ID", "RULE_PATH", "FULL_FILE_MARKER", "BLOCK_BEGIN", "BLOCK_END"),
    )
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return run(_parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
