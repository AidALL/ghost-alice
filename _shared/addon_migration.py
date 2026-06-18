#!/usr/bin/env python3
"""Best-effort migration of a legacy install-state into per-addon sidecars.

Plan tasks T1.6/T1.7/T1.8. The legacy platform install-state lists targets with
no addon identity, so this migration is deliberately conservative:

- T1.6 audit: read the existing install-state read-only.
- T1.7 seed: write a sidecar ONLY when the install-state target already carries
  unambiguous addon identity (``addon_id``) and no sidecar exists yet. We never
  fabricate ownership for a target that lacks identity.
- T1.8 report: every non-core target we cannot attribute is written to
  ``~/.ghost-alice/addons/_migration-report.json`` as a reconciliation candidate
  (the user should re-install that addon via ``--addon-source`` to get a real
  sidecar) -- it is not silently dropped and not mis-attributed.

Pure standard library.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import addon_registry

MIGRATION_REPORT = "_migration-report.json"


def _load_state(install_state_path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(install_state_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _seed_record(addon_id: str, targets: list[dict[str, Any]], *, platform: str, installed_at: str) -> dict[str, Any]:
    """Build ONE sidecar for an addon, with every one of its targets in provided[]."""
    representative = targets[0]
    return {
        "schema_version": addon_registry.SCHEMA_VERSION,
        "addon_id": addon_id,
        "addon_version": representative.get("addon_version") or "0.0.0",
        "source": representative.get("source_path") or f"addon:{addon_id}",
        "platform": platform,
        "owner": "addon",
        "origin": representative.get("origin") or f"addon:{addon_id}",
        "depends_on_core": [],
        "min_core_version": "0.0.0",
        "installed_at": representative.get("installed_at") or installed_at,
        "provided": [
            {
                "kind": "skill",
                "name": target.get("target_name"),
                "target": target.get("dest_path") or f"addon:{addon_id}",
                "ownership": "addon",
                "install_mode": target.get("install_mode") or "missing",
                "content_hash": target.get("target_tree_hash") or "missing",
                "marker": "",
                "metadata": {"migrated": True},
            }
            for target in targets
        ],
        "migration": {"from": "platform-install-state", "mode": "best-effort"},
    }


def audit(
    *,
    install_state_path: str | Path,
    addons_dir: str | Path,
    core_skill_names: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify legacy targets into (seedable-with-identity, unresolved)."""
    core = set(core_skill_names)
    state = _load_state(install_state_path)
    targets = state.get("targets") if isinstance(state.get("targets"), list) else []
    existing_ids = set(addon_registry.iter_addon_ids_on_disk(addons_dir=addons_dir))
    seedable: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        name = target.get("target_name")
        if name in core or name == "_shared":
            continue
        addon_id = target.get("addon_id")
        if isinstance(addon_id, str) and addon_registry.ADDON_ID_RE.fullmatch(addon_id):
            if addon_id in existing_ids:
                continue  # already has a sidecar; leave it authoritative
            seedable.append(target)
        else:
            unresolved.append(
                {
                    "target_name": name,
                    "dest_path": target.get("dest_path"),
                    "reason": "no addon identity in legacy install-state",
                }
            )
    return seedable, unresolved


def run_migration(
    *,
    platform: str,
    install_state_path: str | Path,
    addons_dir: str | Path,
    core_skill_names: Iterable[str],
    installed_at: str,
) -> dict[str, Any]:
    seedable, unresolved = audit(
        install_state_path=install_state_path,
        addons_dir=addons_dir,
        core_skill_names=core_skill_names,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for target in seedable:
        addon_id = target["addon_id"]
        if addon_id not in grouped:
            grouped[addon_id] = []
            order.append(addon_id)
        grouped[addon_id].append(target)
    seeded_ids: list[str] = []
    for addon_id in order:
        record = _seed_record(addon_id, grouped[addon_id], platform=platform, installed_at=installed_at)
        try:
            addon_registry.write_record(record, addons_dir=addons_dir)
            seeded_ids.append(addon_id)
        except addon_registry.RegistryError:
            for target in grouped[addon_id]:
                unresolved.append(
                    {
                        "target_name": target.get("target_name"),
                        "dest_path": target.get("dest_path"),
                        "reason": "seed record failed validation",
                    }
                )
    report_path = Path(addons_dir) / MIGRATION_REPORT
    if unresolved:
        Path(addons_dir).mkdir(parents=True, exist_ok=True)
        report = {
            "platform": platform,
            "generated_at": installed_at,
            "from": "platform-install-state",
            "mode": "best-effort",
            "unresolved_targets": unresolved,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return {
        "seeded": seeded_ids,
        "unresolved": unresolved,
        "report": str(report_path) if unresolved else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy install-state into addon sidecars")
    parser.add_argument("--platform", choices=["claude", "codex"], default="claude")
    parser.add_argument("--install-state", required=True)
    parser.add_argument("--addons-dir", required=True)
    parser.add_argument("--core-skill", action="append", default=[])
    parser.add_argument("--installed-at", default="unknown")
    args = parser.parse_args(argv)
    result = run_migration(
        platform=args.platform,
        install_state_path=args.install_state,
        addons_dir=args.addons_dir,
        core_skill_names=args.core_skill,
        installed_at=args.installed_at,
    )
    if result["seeded"]:
        print(f"migrated {len(result['seeded'])} addon record(s): {', '.join(result['seeded'])}", file=sys.stderr)
    if result["unresolved"]:
        print(f"{len(result['unresolved'])} unresolved target(s) -> {result['report']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
