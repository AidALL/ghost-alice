#!/usr/bin/env python3
"""Per-addon uninstall for Ghost-ALICE.

Removes ONLY the targets recorded in one addon's sidecar, and only when:
- the target path is CONTAINED under an allowed managed root (e.g. the skills
  dir) -- a tampered sidecar cannot point ``target`` at /etc or ~/.ssh; and
- the live target still matches the ``content_hash`` the sidecar recorded at
  install time. That recorded hash is the ownership proof: a target that still
  matches is safe to remove; a target that has drifted (re-pointed symlink,
  edited copy) or has no recorded hash is PRESERVED as manual-review. Never
  clobber a user-modified asset; fail closed when ownership is unprovable.

Safety properties:
- Containment FIRST: ``target``'s location (its parent dir, resolved) must be
  inside an allowed root; otherwise the entry is ``refused`` and nothing is
  touched. install_mode ``missing`` means the asset was never installed -> no-op.
- Symlink-safe: a symlink target is removed by unlinking the LINK, never the
  resolved target; a copy target is removed as a directory tree.
- Two-phase + resumable: a ``<addon_id>.json.removing`` intent marker
  is written before any removal and cleared only after the sidecar is deleted.
  ``resume_pending`` finds leftover markers and finishes the removal idempotently.
- Sidecar deleted LAST: only after every target is removed or safely
  skipped; if anything is blocked (manual-review/refused), the sidecar and marker
  stay so a retry can resume and the state stays auditable. Managed addon hooks
  are still removed on partial uninstall because they are executable code and
  have exact marker ownership independent of file-target drift.

Pure standard library; reuses addon_registry (read/remove/sidecar_path) and
hash_utils (the same content-hash function the install path records with).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import addon_registry
import hash_utils

_REMOVING_SUFFIX = ".json.removing"
_BLOCKED_ACTIONS = frozenset({"manual-review", "refused"})


def _removing_marker_path(addon_id: str, *, addons_dir: str | os.PathLike[str]) -> Path:
    # sidecar_path validates the addon_id charset and enforces containment.
    sidecar = addon_registry.sidecar_path(addon_id, addons_dir=addons_dir)
    return sidecar.with_name(sidecar.name + ".removing")


def _write_marker(marker: Path, addon_id: str, provided: list[dict[str, Any]]) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "addon_id": addon_id,
        "stage": "removing",
        "targets": [entry.get("name") for entry in provided],
    }
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _within_allowed(dest: str, allowed_roots: list[Path]) -> bool:
    """True if dest's LOCATION (parent dir, resolved) is inside an allowed root.

    The parent is resolved (not the leaf), so a managed symlink under the skills
    dir is allowed while its link destination is irrelevant; a dest like
    ``~/.claude/skills/../../.ssh/key`` resolves its parent to ~/.ssh and is
    rejected. An empty allow-list rejects everything (fail closed).
    """
    if not allowed_roots:
        return False
    name = Path(dest).name
    if not name or name in (".", ".."):
        return False
    try:
        parent = Path(dest).parent.resolve()
    except OSError:
        return False
    for root in allowed_roots:
        try:
            root_real = Path(root).resolve()
        except OSError:
            continue
        if parent == root_real or root_real in parent.parents:
            return True
    return False


def _symlink_safe_remove(path: Path) -> None:
    if path.is_symlink():
        path.unlink()  # remove the link, NEVER the resolved target
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _process_target(entry: dict[str, Any], *, allowed_roots: list[Path], confirm: bool) -> dict[str, Any]:
    name = entry.get("name")
    dest = entry.get("target")
    mode = entry.get("install_mode") or "missing"
    recorded = entry.get("content_hash")
    item: dict[str, Any] = {"name": name, "target": dest, "install_mode": mode}
    if mode == "missing":
        item["action"] = "missing"  # never installed -> nothing to uninstall
        return item
    if not dest or not os.path.lexists(dest):  # lexists: do not follow the symlink
        item["action"] = "missing"
        return item
    if not _within_allowed(dest, allowed_roots):
        item["action"] = "refused"
        item["reason"] = "target escapes allowed roots"
        return item
    if not recorded:
        item["action"] = "manual-review"
        item["reason"] = "no content_hash recorded (ownership unprovable)"
        return item
    current = hash_utils.hash_target(dest, mode)
    if current != recorded:
        item["action"] = "manual-review"
        item["reason"] = "modified-since-install (preserved)"
        return item
    if confirm:
        _symlink_safe_remove(Path(dest))
        item["action"] = "removed"
    else:
        item["action"] = "would-remove"
    return item


def _prune_install_state(addon_id: str, *, addons_dir: str | os.PathLike[str], platform: str) -> None:
    """Drop the removed addon's targets from the platform install-state manifest so
    --status/--doctor do not report the now-absent paths as errors."""
    # addons_dir is ~/.ghost-alice/addons/<platform>, so climb TWO levels to
    # ~/.ghost-alice before descending into install-state.
    state_path = Path(addons_dir).parent.parent / "install-state" / f"{platform}.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    targets = data.get("targets")
    if not isinstance(targets, list):
        return
    kept = [t for t in targets if not (isinstance(t, dict) and t.get("addon_id") == addon_id)]
    if len(kept) == len(targets):
        return
    data["targets"] = kept
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, state_path)


def _addon_hook_items(record: dict[str, Any], *, platform: str, confirm: bool) -> list[dict[str, Any]]:
    """Remove (or, in dry-run, preview) the addon's observational hooks by exact marker.

    Hook markers are recorded top-level in the sidecar (kind not in provided[]); each
    is stripped from the platform hook config via install_hooks.remove_addon_hook
    (plan Phase 4 / addon-hook-uninstall-cleanup). Best-effort: a hook already gone
    is reported "missing", never blocks the uninstall.
    """
    specs = record.get("hooks")
    if not isinstance(specs, list) or not specs:
        return []
    addon_id = record.get("addon_id")
    if not isinstance(addon_id, str) or not addon_registry.ADDON_ID_RE.fullmatch(addon_id):
        return []
    out: list[dict[str, Any]] = []
    remover = None
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        hook_id = spec.get("hook_id")
        # Re-derive the marker from the VALIDATED record addon_id + a charset-checked
        # hook_id; never trust spec["marker"] (a tampered sidecar could forge a core
        # marker). A bad hook_id is skipped -> it can never address a non-addon hook.
        if not isinstance(hook_id, str) or not addon_registry.ADDON_ID_RE.fullmatch(hook_id):
            continue
        marker_str = f"[addon:{addon_id}] {hook_id}"
        item: dict[str, Any] = {"kind": "hook", "name": hook_id, "marker": marker_str}
        if not confirm:
            item["action"] = "would-remove"
            out.append(item)
            continue
        if remover is None:
            import install_hooks
            remover = install_hooks.remove_addon_hook
        removed = remover(marker_str, platform_key=platform, dry_run=False)
        item["action"] = "removed" if removed else "missing"
        out.append(item)
    return out


def _adapter_hook_items(provided: list[dict[str, Any]], *, platform: str, confirm: bool) -> list[dict[str, Any]]:
    """Remove core-owned privileged adapter hooks recorded in provided[]."""
    out: list[dict[str, Any]] = []
    remover = None
    for entry in provided:
        if entry.get("kind") != "adapter":
            continue
        adapter_id = entry.get("name")
        metadata = entry.get("metadata")
        hook_id = metadata.get("hook_id") if isinstance(metadata, dict) else None
        if not isinstance(adapter_id, str) or not addon_registry.ADDON_ID_RE.fullmatch(adapter_id):
            continue
        if not isinstance(hook_id, str) or not addon_registry.ADDON_ID_RE.fullmatch(hook_id):
            continue
        marker_str = f"[adapter:{adapter_id}] {hook_id}"
        item: dict[str, Any] = {"kind": "adapter", "name": adapter_id, "marker": marker_str}
        if not confirm:
            item["action"] = "would-remove"
            out.append(item)
            continue
        if remover is None:
            import install_hooks
            remover = install_hooks.remove_adapter_hook
        removed = remover(marker_str, platform_key=platform, dry_run=False)
        item["action"] = "removed" if removed else "missing"
        out.append(item)
    return out


def _managed_executable_items(
    record: dict[str, Any],
    provided: list[dict[str, Any]],
    *,
    platform: str,
    confirm: bool,
) -> list[dict[str, Any]]:
    return (
        _addon_hook_items(record, platform=platform, confirm=confirm)
        + _adapter_hook_items(provided, platform=platform, confirm=confirm)
    )


def uninstall_addon(
    addon_id: str,
    *,
    addons_dir: str | os.PathLike[str],
    allowed_roots: list[Path],
    platform: str = "claude",  # reserved for future per-platform scoping; sidecars record absolute targets
    confirm: bool = True,
) -> dict[str, Any]:
    """Uninstall one addon. Returns a report dict with status + per-target items.

    status: ``removed`` (all targets removed, sidecar deleted), ``partial`` (some
    target preserved as manual-review/refused; sidecar + marker kept for retry,
    but managed executable hooks removed), ``dry-run`` (confirm=False),
    ``resumed-noop`` (a leftover marker but the sidecar was already gone --
    nothing to remove).
    """
    addons_dir = Path(addons_dir)
    marker = _removing_marker_path(addon_id, addons_dir=addons_dir)
    try:
        record = addon_registry.read_record(addon_id, addons_dir=addons_dir)
    except addon_registry.RecordNotFound as not_found:
        try:
            marker.unlink()
        except FileNotFoundError:
            raise not_found from None  # no sidecar and no marker -> genuinely not found
        return {"addon_id": addon_id, "status": "resumed-noop", "items": []}
    except addon_registry.RegistryError as exc:
        # Unreadable/corrupt/future-major sidecar (e.g. unsupported schema, identity
        # mismatch). Never crash the uninstall or the resume loop, and never delete
        # an asset we cannot attribute: preserve the sidecar + marker untouched and
        # surface a non-removed status so automation/doctor flag it for manual review.
        return {"addon_id": addon_id, "status": "error",
                "reason": f"sidecar unreadable: {exc}", "items": []}

    provided = [entry for entry in record.get("provided", []) if isinstance(entry, dict)]
    if confirm:
        _write_marker(marker, addon_id, provided)  # intent marker BEFORE any removal

    # Adapter entries are intentionally excluded from the hash-gated file
    # removal below. A privileged adapter's script lives inside its owning skill
    # directory, so the skill target is the file deletion unit and the adapter
    # entry is an install-time integrity/ownership snapshot (T0.3), not a second
    # uninstall-time delete gate. If the skill target is fully removable, its
    # own hash gate covers the adapter script along with the rest of the skill
    # tree. If the skill target is blocked for manual review, the script stays
    # with it. In both cases only the adapter HOOK is removed here, by exact
    # marker, via _adapter_hook_items.
    file_targets = [entry for entry in provided if entry.get("kind") != "adapter"]
    items = [_process_target(entry, allowed_roots=allowed_roots, confirm=confirm) for entry in file_targets]
    blocked = [item for item in items if item["action"] in _BLOCKED_ACTIONS]

    if not confirm:
        report = {"addon_id": addon_id, "status": "dry-run",
                  "items": items + _managed_executable_items(record, provided, platform=platform, confirm=False)}
        report["has_pending_marker"] = marker.exists()
        return report
    if blocked:
        # Keep sidecar + .removing marker for retry/manual review, but disable
        # the addon's managed hook commands. Hook ownership is proven by exact
        # marker + hook-runner signature, so removing them does not risk user data.
        hook_items = _managed_executable_items(record, provided, platform=platform, confirm=True)
        return {"addon_id": addon_id, "status": "partial", "items": items + hook_items}

    # Fully removable: strip the addon's observational hooks from the platform
    # config (plan Phase 4) before deleting the sidecar that records their markers.
    hook_items = _managed_executable_items(record, provided, platform=platform, confirm=True)
    addon_registry.remove_record(addon_id, addons_dir=addons_dir)  # sidecar deleted LAST
    _prune_install_state(addon_id, addons_dir=addons_dir, platform=platform)
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    return {"addon_id": addon_id, "status": "removed", "items": items + hook_items}


def resume_pending(
    *,
    addons_dir: str | os.PathLike[str],
    allowed_roots: list[Path],
    platform: str = "claude",
    confirm: bool = True,
) -> list[dict[str, Any]]:
    """Finish any addon uninstall interrupted mid-flight, idempotently."""
    base = Path(addons_dir)
    if not base.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for marker in sorted(base.glob("*" + _REMOVING_SUFFIX)):
        stem = marker.name[: -len(_REMOVING_SUFFIX)]
        if not addon_registry.ADDON_ID_RE.fullmatch(stem):
            continue
        try:
            results.append(
                uninstall_addon(
                    stem, addons_dir=base, allowed_roots=allowed_roots,
                    platform=platform, confirm=confirm,
                )
            )
        except addon_registry.RecordNotFound:
            # Sidecar and marker both vanished concurrently; nothing to do for this id.
            continue
    return results


def core_skill_dependents(core_skill: str, *, addons_dir: str | os.PathLike[str]) -> list[str]:
    """addon_ids of installed addons that declare depends_on_core on ``core_skill``.

    Used to block uninstall of a core skill that an installed addon still needs.
    """
    dependents: list[str] = []
    for record in addon_registry.read_all(addons_dir=addons_dir):
        deps = record.get("depends_on_core")
        if isinstance(deps, list) and core_skill in deps:
            addon_id = record.get("addon_id")
            if isinstance(addon_id, str):
                dependents.append(addon_id)
    return sorted(dependents)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-addon uninstall / resume for Ghost-ALICE addons")
    parser.add_argument("--addons-dir", required=True)
    parser.add_argument("--platform", choices=["claude", "codex"], default="claude")
    parser.add_argument("--skills-dir", action="append", default=[],
                        help="An allowed managed root for target containment (repeatable)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="confirm removals (the default unless --dry-run)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--addon-id")
    group.add_argument("--resume-pending", action="store_true")
    group.add_argument("--dependents", metavar="CORE_SKILL",
                      help="print addon_ids depending on CORE_SKILL; exit 2 if any")
    args = parser.parse_args(argv)

    if args.dependents:
        # Fail closed: an unreadable sidecar could itself declare depends_on_core
        # on this skill, so it must BLOCK the core uninstall instead of being
        # silently treated as absence.
        records, skipped = addon_registry.scan_records(addons_dir=args.addons_dir)
        deps = sorted({
            record["addon_id"]
            for record in records
            if isinstance(record.get("depends_on_core"), list)
            and args.dependents in record["depends_on_core"]
            and isinstance(record.get("addon_id"), str)
        })
        print(json.dumps(
            {"core_skill": args.dependents, "dependents": deps,
             "skipped": [name for name, _reason in skipped]},
            ensure_ascii=False))
        return 2 if (deps or skipped) else 0

    allowed_roots = [Path(root) for root in args.skills_dir]
    confirm = not args.dry_run
    if args.resume_pending:
        results = resume_pending(addons_dir=args.addons_dir, allowed_roots=allowed_roots,
                                 platform=args.platform, confirm=confirm)
        print(json.dumps({"resumed": results}, ensure_ascii=False, indent=2))
        return 0
    try:
        result = uninstall_addon(args.addon_id, addons_dir=args.addons_dir,
                                 allowed_roots=allowed_roots, platform=args.platform, confirm=confirm)
    except addon_registry.RecordNotFound:
        print(json.dumps({"addon_id": args.addon_id, "status": "not-found", "items": []}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"removed", "dry-run", "resumed-noop"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
