#!/usr/bin/env python3
"""Resolve Ghost-ALICE addon skill manifests into installer targets."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ADDONS_MANIFEST = "addons-manifest.json"
ADDON_MANIFEST = "addon.json"
ADDON_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class AddonManifestError(Exception):
    """Raised when an addon manifest cannot be safely installed."""


@dataclass(frozen=True)
class AddonTarget:
    name: str
    source: Path
    addon_id: str
    addon_version: str
    origin: str
    platforms: tuple[str, ...]
    depends_on_core: tuple[str, ...]
    secrets: tuple[str, ...]
    tags: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": str(self.source),
            "addon_id": self.addon_id,
            "addon_version": self.addon_version,
            "origin": self.origin,
            "platforms": list(self.platforms),
            "depends_on_core": list(self.depends_on_core),
            "secrets": list(self.secrets),
            "tags": list(self.tags),
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AddonManifestError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AddonManifestError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AddonManifestError(f"manifest must be an object: {path}")
    return data


def _require_string(value: Any, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AddonManifestError(f"{path}: {field} must be a non-empty string")
    return value


def _string_list(value: Any, field: str, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AddonManifestError(f"{path}: {field} must be a string array")
    return tuple(value)


def _safe_child_path(root: Path, relative_path: str, field: str, manifest_path: Path) -> Path:
    if Path(relative_path).is_absolute():
        raise AddonManifestError(f"{manifest_path}: {field} must be relative")
    resolved = (root / relative_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AddonManifestError(f"{manifest_path}: {field} escapes addon source") from exc
    return resolved


def _validate_id(value: str, field: str, path: Path) -> str:
    if not ADDON_ID_RE.fullmatch(value):
        raise AddonManifestError(f"{path}: {field} must match {ADDON_ID_RE.pattern}")
    return value


def _iter_top_level_addons(source_root: Path) -> Iterable[tuple[dict[str, Any], Path]]:
    manifest_path = source_root / ADDONS_MANIFEST
    manifest = _read_json(manifest_path)
    if manifest.get("manifest_version") != 1:
        raise AddonManifestError(f"{manifest_path}: manifest_version must be 1")
    addons = manifest.get("addons")
    if not isinstance(addons, list):
        raise AddonManifestError(f"{manifest_path}: addons must be an array")
    for addon in addons:
        if not isinstance(addon, dict):
            raise AddonManifestError(f"{manifest_path}: addon entries must be objects")
        yield addon, manifest_path


def load_addon_targets(
    sources: Iterable[str | Path],
    *,
    core_skill_names: Iterable[str] = (),
    platform: str | None = None,
) -> list[AddonTarget]:
    core_names = set(core_skill_names)
    targets: list[AddonTarget] = []
    seen_names: dict[str, str] = {}

    for source in sources:
        source_root = Path(source).expanduser().resolve()
        for addon_entry, top_manifest_path in _iter_top_level_addons(source_root):
            addon_id = _validate_id(
                _require_string(addon_entry.get("id"), "id", top_manifest_path),
                "id",
                top_manifest_path,
            )
            addon_path = _safe_child_path(
                source_root,
                _require_string(addon_entry.get("path"), "path", top_manifest_path),
                "path",
                top_manifest_path,
            )
            tags = _string_list(addon_entry.get("tags", []), "tags", top_manifest_path)
            addon_manifest_path = addon_path / ADDON_MANIFEST
            addon_manifest = _read_json(addon_manifest_path)
            declared_id = _validate_id(
                _require_string(addon_manifest.get("addon_id"), "addon_id", addon_manifest_path),
                "addon_id",
                addon_manifest_path,
            )
            if declared_id != addon_id:
                raise AddonManifestError(
                    f"{addon_manifest_path}: addon_id {declared_id!r} does not match top-level id {addon_id!r}"
                )

            addon_version = _require_string(
                addon_manifest.get("addon_version"),
                "addon_version",
                addon_manifest_path,
            )
            platforms = _string_list(addon_manifest.get("platforms", []), "platforms", addon_manifest_path)
            if platform and platforms and platform not in platforms:
                continue
            depends_on_core = _string_list(
                addon_manifest.get("depends_on_core", []),
                "depends_on_core",
                addon_manifest_path,
            )
            secrets = _string_list(addon_manifest.get("secrets", []), "secrets", addon_manifest_path)
            skills = addon_manifest.get("skills")
            if not isinstance(skills, list) or not skills:
                raise AddonManifestError(f"{addon_manifest_path}: skills must be a non-empty array")

            for skill in skills:
                if not isinstance(skill, dict):
                    raise AddonManifestError(f"{addon_manifest_path}: skill entries must be objects")
                if skill.get("source") != "skill":
                    raise AddonManifestError(f"{addon_manifest_path}: skill.source must be 'skill'")
                skill_name = _validate_id(
                    _require_string(skill.get("name"), "skills[].name", addon_manifest_path),
                    "skills[].name",
                    addon_manifest_path,
                )
                if skill_name in core_names:
                    raise AddonManifestError(f"{addon_manifest_path}: addon skill {skill_name!r} collides with core skill")
                previous_addon = seen_names.get(skill_name)
                if previous_addon is not None:
                    raise AddonManifestError(
                        f"{addon_manifest_path}: addon skill {skill_name!r} collides with addon {previous_addon!r}"
                    )
                skill_dir = _safe_child_path(
                    addon_path,
                    _require_string(skill.get("skill_dir"), "skills[].skill_dir", addon_manifest_path),
                    "skills[].skill_dir",
                    addon_manifest_path,
                )
                if not (skill_dir / "SKILL.md").is_file():
                    raise AddonManifestError(f"{addon_manifest_path}: missing SKILL.md for {skill_name!r}")
                seen_names[skill_name] = addon_id
                targets.append(
                    AddonTarget(
                        name=skill_name,
                        source=skill_dir,
                        addon_id=addon_id,
                        addon_version=addon_version,
                        origin=f"addon:{addon_id}",
                        platforms=platforms,
                        depends_on_core=depends_on_core,
                        secrets=secrets,
                        tags=tags,
                    )
                )

    return targets


def _render_json(targets: list[AddonTarget]) -> str:
    return json.dumps(
        {
            "target_count": len(targets),
            "targets": [target.as_dict() for target in targets],
        },
        ensure_ascii=False,
        indent=2,
    )


def _render_shell(targets: list[AddonTarget]) -> str:
    return "\n".join(f"{target.name}|{target.source}" for target in targets)


def _render_text(targets: list[AddonTarget]) -> str:
    if not targets:
        return "No addon skill targets discovered."
    return "\n".join(
        f"{target.name} ({target.origin}) -> {target.source}" for target in targets
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve Ghost-ALICE addon manifests")
    parser.add_argument("--source", action="append", default=[], help="Addon source directory")
    parser.add_argument("--core-skill", action="append", default=[], help="Core skill name that addons may not shadow")
    parser.add_argument("--platform", choices=["claude", "codex"], help="Optional platform filter")
    parser.add_argument("--format", choices=["json", "shell", "text"], default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.source:
        parser.error("at least one --source is required")
    try:
        targets = load_addon_targets(
            args.source,
            core_skill_names=args.core_skill,
            platform=args.platform,
        )
    except AddonManifestError as exc:
        print(f"addon manifest error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(_render_json(targets))
    elif args.format == "shell":
        rendered = _render_shell(targets)
        if rendered:
            print(rendered)
    else:
        print(_render_text(targets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
