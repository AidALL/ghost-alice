import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def as_posix(path):
    return Path(path).as_posix()


def hash_target(path, install_mode):
    target = Path(path)
    if install_mode in {"symlink", "junction"}:
        try:
            link_target = os.readlink(target)
        except OSError:
            link_target = as_posix(path)
        return hashlib.sha256(f"link:{link_target}".encode("utf-8")).hexdigest()
    if install_mode == "missing" or not target.exists():
        return "missing"
    if target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()

    digest = hashlib.sha256()
    for child in sorted(p for p in target.rglob("*") if p.is_file()):
        rel = child.relative_to(target).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def managed_markers(name, source_path, dest_path):
    if name == "_shared":
        return ["_shared"]
    markers = []
    if (Path(source_path) / "SKILL.md").exists() or (Path(dest_path) / "SKILL.md").exists():
        markers.append("SKILL.md")
    return markers


platform, source_root, source_branch, source_head, source_dirty_state, state_path, *raw_targets = sys.argv[1:]
if len(raw_targets) % 4 != 0:
    raise SystemExit("install-state target argument count is invalid")

installed_at = datetime.now(timezone.utc).isoformat()
targets = []
for offset in range(0, len(raw_targets), 4):
    name, source_path, dest_path, install_mode = raw_targets[offset : offset + 4]
    targets.append(
        {
            "target_name": name,
            "source_path": as_posix(source_path),
            "dest_path": as_posix(dest_path),
            "install_mode": install_mode,
            "target_tree_hash": hash_target(dest_path, install_mode),
            "managed_markers": managed_markers(name, source_path, dest_path),
            "installed_at": installed_at,
        }
    )

system_env_changes = []
if os.environ.get("GHOST_ALICE_SOURCE_REPO_HOOK_CHANGED") == "1":
    before_present = os.environ.get("GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE_PRESENT") == "1"
    change = {
        "kind": "source_repo_hook_path",
        "repo_root": as_posix(source_root),
        "before_present": before_present,
        "before": os.environ.get("GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE") if before_present else None,
        "after": os.environ.get("GHOST_ALICE_SOURCE_REPO_HOOK_AFTER") or "hooks",
        "applied_at": installed_at,
    }
    system_env_changes.append(change)

sidecar = Path(state_path).with_name(f"{platform}-hook-feature-change.json")
if platform == "codex" and sidecar.exists():
    try:
        change = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        change = None
    if isinstance(change, dict) and change.get("kind") == "codex_hooks_feature_flag":
        system_env_changes.append(
            {
                "kind": "codex_hooks_feature_flag",
                "path": change.get("path"),
                "before_state": change.get("before_state"),
                "after_state": change.get("after_state"),
                "applied_at": change.get("applied_at") or installed_at,
            }
        )

project_trust_sidecar = Path(state_path).with_name(f"{platform}-project-trust-change.json")
if platform == "codex" and project_trust_sidecar.exists():
    try:
        change = json.loads(project_trust_sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        change = None
    if isinstance(change, dict) and change.get("kind") == "codex_project_trust":
        system_env_changes.append(
            {
                "kind": "codex_project_trust",
                "path": change.get("path"),
                "project_path": change.get("project_path"),
                "before_state": change.get("before_state"),
                "after_state": change.get("after_state"),
                "applied_at": change.get("applied_at") or installed_at,
            }
        )

manifest = {
    "schema_version": 1,
    "platform": platform,
    "installed_at": installed_at,
    "source_root": as_posix(source_root),
    "source_branch": source_branch,
    "source_head": source_head,
    "source_dirty_state": source_dirty_state,
    "remote_freshness_state": "unverified",
    "targets": targets,
    "system_env_changes": system_env_changes,
}

state = Path(state_path)
state.parent.mkdir(parents=True, exist_ok=True)
state.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
