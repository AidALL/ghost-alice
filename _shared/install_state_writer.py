import json
import os
import sys
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from hash_utils import hash_target
import atomic_write


def as_posix(path):
    return Path(path).as_posix()


def managed_markers(name, source_path, dest_path):
    if name == "_shared":
        return ["_shared"]
    markers = []
    if (Path(source_path) / "SKILL.md").exists() or (Path(dest_path) / "SKILL.md").exists():
        markers.append("SKILL.md")
    return markers


def read_state(path):
    """Read the current inventory without adopting live files as a new baseline."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if isinstance(data, dict) and "targets" not in data and data.get("partial_failure") is True:
        # The earlier postflight writer recorded observations without an inventory.
        data = dict(data, targets=[])
    if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
        raise ValueError(f"invalid install-state targets: {path}")
    for target in data["targets"]:
        if (not isinstance(target, dict) or not isinstance(target.get("dest_path"), str)
                or not target["dest_path"] or not isinstance(target.get("target_name"), str)
                or not target["target_name"]):
            raise ValueError(f"invalid install-state target: {path}")
    return data


def record_partial_failure(state_path, platform, failures):
    """Attach failure observations without replacing installed baselines or rollback entries."""
    state = read_state(state_path)
    if state is None:
        state = {"schema_version": 1, "platform": platform, "targets": [], "system_env_changes": []}
    elif state.get("platform", platform) != platform:
        raise ValueError(f"install-state platform mismatch: {state_path}")
    state.update(status="partial_failure", partial_failure=True, failures=failures,
                 failed_at=datetime.now(timezone.utc).isoformat(), remote_freshness_state="unverified")
    atomic_write.write_json(Path(state_path), state)


def merge_system_env_changes(previous, changes):
    """Carry rollback records forward; replace only the three setting owners this writer reads."""
    def owner(change):
        if not isinstance(change, dict):
            return None
        kind = change.get("kind")
        if kind == "source_repo_hook_path" and isinstance(change.get("repo_root"), str):
            return kind, change["repo_root"]
        if kind == "codex_hooks_feature_flag" and isinstance(change.get("path"), str):
            return kind, change["path"]
        if (kind == "codex_project_trust" and isinstance(change.get("path"), str)
                and isinstance(change.get("project_path"), str)):
            return kind, change["path"], change["project_path"]
        return None

    replacements = {owner(change): change for change in changes if owner(change) is not None}
    carried = [change for change in previous if owner(change) not in replacements]
    return carried + list(replacements.values())


def remove_targets(state_path, *, destinations=(), addon_id=None):
    """Remove only confirmed destinations or the removed addon's rows from current inventory."""
    data = read_state(state_path)
    if data is None:
        return 0
    selected = {as_posix(path) for path in destinations}
    previous = data["targets"]
    data["targets"] = [target for target in previous
                       if as_posix(target["dest_path"]) not in selected
                       and not (addon_id is not None and target.get("addon_id") == addon_id)]
    removed = len(previous) - len(data["targets"])
    if removed:
        atomic_write.write_json(Path(state_path), data)
    return removed


def main(argv=None):
    platform, source_root, source_branch, source_head, source_dirty_state, state_path, *raw_targets = (sys.argv[1:] if argv is None else argv)
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

    # Keep untouched Core rows and their original baselines; this invocation replaces only its own batch.
    try:
        previous = read_state(state_path)
    except (OSError, ValueError) as exc:
        state = Path(state_path)
        backup = state.with_name(state.name + "." + uuid.uuid4().hex + ".bak")
        shutil.copy2(state, backup, follow_symlinks=False)
        print(f"Preserved unreadable install inventory at {backup}: {exc}", file=sys.stderr)
        previous = None
    if previous is not None and previous.get("platform", platform) != platform:
        raise ValueError(f"install-state platform mismatch: {state_path}")
    by_destination = {as_posix(target["dest_path"]): target for target in (previous or {}).get("targets", [])
                      if not target.get("addon_id") and target.get("owner") != "addon"}
    by_destination.update({target["dest_path"]: target for target in targets})
    targets = list(by_destination.values())

    # Rebuild addon rows from their cumulative sidecars after preserving the untouched Core rows.
    try:
        import addon_registry

        # Platform-scoped: each platform owns ~/.ghost-alice/addons/<platform>/ so the same addon installed on claude and codex keeps independent records and this aggregate never picks up the other platform's targets.
        addons_dir = Path(state_path).resolve().parent.parent / "addons" / platform
        sidecar_records, sidecar_skipped = addon_registry.scan_records(addons_dir=addons_dir)
    except Exception:
        sidecar_records, sidecar_skipped = [], []

    if sidecar_skipped:
        # Fail-closed: do NOT overwrite install-state with a success manifest that would silently hide an unreadable / tampered / future-major sidecar. Emit a registry-health diagnostic and abort so the drop is visible.
        health_path = Path(state_path).with_name(f"{platform}-registry-health.json")
        try:
            health_path.parent.mkdir(parents=True, exist_ok=True)
            health_path.write_text(
                json.dumps(
                    {
                        "platform": platform,
                        "generated_at": installed_at,
                        "skipped_sidecars": [
                            {"file": skip_name, "reason": skip_reason}
                            for skip_name, skip_reason in sidecar_skipped
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        print(
            f"install-state aborted: {len(sidecar_skipped)} addon sidecar(s) could not be read; "
            f"see {health_path}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    dest_index = {target["dest_path"]: target for target in targets}
    for sidecar_record in sidecar_records:
        attribution = {
            "addon_id": sidecar_record.get("addon_id"),
            "origin": sidecar_record.get("origin"),
            "owner": "addon",
        }
        for provided_entry in sidecar_record.get("provided", []):
            if not isinstance(provided_entry, dict):
                continue
            addon_dest = as_posix(provided_entry.get("target", ""))
            existing_target = dest_index.get(addon_dest)
            if existing_target is not None:
                existing_target.update(attribution)
                continue
            addon_target = {
                "target_name": provided_entry.get("name"),
                "source_path": as_posix(sidecar_record.get("source", "")),
                "dest_path": addon_dest,
                "install_mode": provided_entry.get("install_mode", "missing"),
                "target_tree_hash": provided_entry.get("content_hash") or "missing",
                "managed_markers": ["addon"],
                "installed_at": sidecar_record.get("installed_at", installed_at),
            }
            addon_target.update(attribution)
            targets.append(addon_target)
            dest_index[addon_dest] = addon_target

    system_env_changes = []
    # The shared git hook path this install owns, whenever the installer knows the value the full uninstall restores.
    if os.environ.get("GHOST_ALICE_SOURCE_REPO_HOOK_RECORDED") == "1":
        before_present = os.environ.get("GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE_PRESENT") == "1"
        change = {
            "kind": "source_repo_hook_path",
            "repo_root": as_posix(source_root),
            "before_present": before_present,
            "before": os.environ.get("GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE") if before_present else None,
            "after": os.environ.get("GHOST_ALICE_SOURCE_REPO_HOOK_AFTER", ""),
            "applied_at": os.environ.get("GHOST_ALICE_SOURCE_REPO_HOOK_APPLIED_AT") or installed_at,
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
        "system_env_changes": merge_system_env_changes((previous or {}).get("system_env_changes", []),
                                                       system_env_changes),
    }

    state = Path(state_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    atomic_write.write_json(state, manifest)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"install-state update failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
