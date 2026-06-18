#!/usr/bin/env python3
"""Resolve Ghost-ALICE addon skill manifests into installer targets."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import addon_registry
import hash_utils


ADDONS_MANIFEST = "addons-manifest.json"
ADDON_MANIFEST = "addon.json"
ADDON_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SEMVER_RE = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")

# Tier-2 addon hooks (plan Phase 4) may ONLY observe; events that can block or
# alter control flow / governance gates are rejected fail-closed. PostToolUse and
# SessionStart observe; PreToolUse can block a tool, Stop can block completion, and
# UserPromptSubmit can alter intent -- none are permitted for addons.
ALLOWED_ADDON_HOOK_EVENTS = frozenset({"post_tool_use", "on_session_start"})
# Reserved core hook ids an addon hook id must not collide with (the runner
# namespaces used by the core hook suite).
RESERVED_CORE_HOOK_IDS = frozenset({
    "prompt", "pending-merge-prompt", "session-intent", "web-search-first",
    "tool-checkpoint", "completion", "session-start", "io-trace",
})
_SIDECAR_KNOWN_OPTIONAL_FIELDS = frozenset({"secrets", "hooks", "migration"})


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
    min_core_version: str = "0.0.0"
    addon_root: Path | None = None
    # Addon-level extras shared by every skill target in the addon. Commands are
    # Claude-only slash-command files; resources are addon data files.
    commands: tuple[tuple[str, str], ...] = ()
    resources: tuple[tuple[str, str], ...] = ()
    # observational hooks (plan Phase 4): (hook_id, event_intent, script_abs_path).
    hooks: tuple[tuple[str, str, str], ...] = ()

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
            "min_core_version": self.min_core_version,
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


def _validate_semver(value: str, field: str, path: Path) -> str:
    if not _SEMVER_RE.fullmatch(value):
        raise AddonManifestError(f"{path}: {field} must be semver major.minor.patch: {value!r}")
    return value


def _semver_tuple(value: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.fullmatch(value)
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _read_core_version() -> str:
    """SSOT for the installed core version: the repo-root VERSION file."""
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


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
    core_version: str | None = None,
) -> list[AddonTarget]:
    core_names = set(core_skill_names)
    resolved_core_version = core_version if core_version is not None else _read_core_version()
    targets: list[AddonTarget] = []
    seen_names: dict[str, str] = {}
    seen_addon_ids: set[str] = set()

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
            raw_min_core = addon_entry.get("min_core_version")
            min_core_version = _validate_semver(
                raw_min_core if isinstance(raw_min_core, str) and raw_min_core.strip() else "0.0.0",
                "min_core_version",
                top_manifest_path,
            )
            if _semver_tuple(min_core_version) > _semver_tuple(resolved_core_version):
                raise AddonManifestError(
                    f"{top_manifest_path}: addon requires core >= {min_core_version} "
                    f"but installed core is {resolved_core_version} (fail-closed, nothing installed)"
                )
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
            if addon_id in seen_addon_ids:
                raise AddonManifestError(
                    f"{addon_manifest_path}: duplicate addon_id {addon_id!r} "
                    "(already loaded from another source); addon ids must be unique"
                )
            seen_addon_ids.add(addon_id)

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
            # depends_on_core enforcement: a declared core dependency must exist in the known core skill set,
            # else fail closed BEFORE any file operation. Enforced only when the core
            # set is provided (install-time collision detection passes --core-skill);
            # an omitted depends_on_core normalizes to [] (via _string_list) and never
            # hard-fails, per the cross-cutting contract.
            if core_names:
                for dep in depends_on_core:
                    if dep not in core_names:
                        raise AddonManifestError(
                            f"{addon_manifest_path}: depends_on_core {dep!r} is not a known "
                            "installed/core skill (a declared dependency must exist)")
            secrets = _string_list(addon_manifest.get("secrets", []), "secrets", addon_manifest_path)

            addon_commands: list[tuple[str, str]] = []
            for command in addon_manifest.get("commands", []) or []:
                if not isinstance(command, dict):
                    raise AddonManifestError(f"{addon_manifest_path}: command entries must be objects")
                command_name = _validate_id(
                    _require_string(command.get("name"), "commands[].name", addon_manifest_path),
                    "commands[].name", addon_manifest_path)
                command_src = _safe_child_path(
                    addon_path,
                    _require_string(command.get("source"), "commands[].source", addon_manifest_path),
                    "commands[].source", addon_manifest_path)
                if not command_src.is_file():
                    raise AddonManifestError(f"{addon_manifest_path}: missing command source for {command_name!r}")
                addon_commands.append((command_name, str(command_src)))

            addon_resources: list[tuple[str, str]] = []
            for resource in addon_manifest.get("resources", []) or []:
                if not isinstance(resource, dict):
                    raise AddonManifestError(f"{addon_manifest_path}: resource entries must be objects")
                resource_rel = _require_string(resource.get("path"), "resources[].path", addon_manifest_path)
                rel_parts = Path(resource_rel).parts
                if (Path(resource_rel).is_absolute() or resource_rel.startswith(("/", "\\"))
                        or ".." in rel_parts or os.path.normpath(resource_rel) in (".", "")):
                    raise AddonManifestError(
                        f"{addon_manifest_path}: resources[].path must be a safe relative path: {resource_rel!r}")
                resource_src = _safe_child_path(
                    addon_path,
                    _require_string(resource.get("source"), "resources[].source", addon_manifest_path),
                    "resources[].source", addon_manifest_path)
                if not resource_src.is_file():
                    raise AddonManifestError(f"{addon_manifest_path}: missing resource source for {resource_rel!r}")
                addon_resources.append((resource_rel, str(resource_src)))

            addon_hooks: list[tuple[str, str, str]] = []
            seen_hook_ids: set[str] = set()
            for hook in addon_manifest.get("hooks", []) or []:
                if not isinstance(hook, dict):
                    raise AddonManifestError(f"{addon_manifest_path}: hook entries must be objects")
                hook_id = _validate_id(
                    _require_string(hook.get("id"), "hooks[].id", addon_manifest_path),
                    "hooks[].id", addon_manifest_path)
                if hook_id in RESERVED_CORE_HOOK_IDS:
                    raise AddonManifestError(
                        f"{addon_manifest_path}: addon hook id {hook_id!r} collides with a reserved core hook id")
                if hook_id in seen_hook_ids:
                    raise AddonManifestError(f"{addon_manifest_path}: duplicate addon hook id {hook_id!r}")
                seen_hook_ids.add(hook_id)
                event = _require_string(hook.get("event"), "hooks[].event", addon_manifest_path)
                if event not in ALLOWED_ADDON_HOOK_EVENTS:
                    raise AddonManifestError(
                        f"{addon_manifest_path}: addon hook {hook_id!r} event {event!r} is not permitted; "
                        f"addon hooks may only observe ({', '.join(sorted(ALLOWED_ADDON_HOOK_EVENTS))}) -- "
                        "control-flow events (Stop / on_agent_stop / UserPromptSubmit / on_user_prompt / "
                        "pre_tool_use) are rejected")
                script_rel = _require_string(hook.get("script"), "hooks[].script", addon_manifest_path)
                if "/bin/sh" in script_rel or any(ch in script_rel for ch in (";", "|", "&", "`", "$", "\n")):
                    raise AddonManifestError(
                        f"{addon_manifest_path}: hook script must be a contained path, not a shell command: {script_rel!r}")
                hook_script = _safe_child_path(addon_path, script_rel, "hooks[].script", addon_manifest_path)
                if not hook_script.is_file():
                    raise AddonManifestError(f"{addon_manifest_path}: missing hook script for {hook_id!r}")
                addon_hooks.append((hook_id, event, str(hook_script)))

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
                        min_core_version=min_core_version,
                        addon_root=addon_path,
                        commands=tuple(addon_commands),
                        resources=tuple(addon_resources),
                        hooks=tuple(addon_hooks),
                    )
                )

    return targets


def build_sidecar_record(
    target: AddonTarget,
    *,
    platform: str,
    installed_at: str,
    provided: list[dict[str, Any]],
    owner: str = "addon",
) -> dict[str, Any]:
    """Build a registry sidecar record from a resolved AddonTarget identity.

    The result conforms to ``addon_registry`` required fields; ``secrets`` is
    included only when the addon declares any (its presence marks the addon
    Tier-2-ineligible until a secrets contract exists).
    """
    record: dict[str, Any] = {
        "schema_version": addon_registry.SCHEMA_VERSION,
        "addon_id": target.addon_id,
        "addon_version": target.addon_version,
        "source": str(target.addon_root if target.addon_root is not None else target.source),
        "platform": platform,
        "owner": owner,
        "origin": f"addon:{target.addon_id}",
        "depends_on_core": list(target.depends_on_core),
        "min_core_version": target.min_core_version,
        "installed_at": installed_at,
        "provided": list(provided),
    }
    if target.secrets:
        record["secrets"] = list(target.secrets)
    if target.hooks:
        # Top-level (not provided[]) so the provided[] schema + doctor hash audit
        # stay filesystem-only; uninstall reads these markers to remove the hooks.
        record["hooks"] = [
            {"hook_id": hook_id, "event": event, "marker": f"[addon:{target.addon_id}] {hook_id}"}
            for hook_id, event, _script in target.hooks
        ]
    return record


def _default_skill_provided(name: str, source: Path) -> dict[str, Any]:
    return {
        "kind": "skill",
        "name": name,
        "target": str(source),
        "ownership": "addon",
        "install_mode": "symlink",
        "content_hash": "",
        "marker": "",
        "metadata": {},
    }


def _schema_major_minor(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"^(0|[1-9][0-9]{0,3})(?:\.(0|[1-9][0-9]{0,3}))?$", value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def _merge_existing_sidecar_compat(
    addon_id: str,
    record: dict[str, Any],
    *,
    addons_dir: str | Path,
) -> dict[str, Any]:
    """Preserve forward-compatible existing sidecar fields before rewrite."""
    try:
        existing = addon_registry.read_record(addon_id, addons_dir=addons_dir)
    except addon_registry.RecordNotFound:
        return record
    except addon_registry.UnsupportedSchemaVersion as exc:
        raise AddonManifestError(
            f"existing sidecar for {addon_id!r} uses unsupported schema; refusing to overwrite"
        ) from exc
    except addon_registry.RegistryError as exc:
        raise AddonManifestError(
            f"existing sidecar for {addon_id!r} is unreadable; refusing to overwrite"
        ) from exc

    merged = dict(record)
    current_version = _schema_major_minor(addon_registry.SCHEMA_VERSION) or (addon_registry.SUPPORTED_MAJOR, 0)
    existing_version = _schema_major_minor(existing.get("schema_version"))
    if (
        existing_version
        and existing_version[0] == addon_registry.SUPPORTED_MAJOR
        and existing_version[1] > current_version[1]
    ):
        merged["schema_version"] = existing["schema_version"]

    known_fields = set(addon_registry.REQUIRED_FIELDS) | _SIDECAR_KNOWN_OPTIONAL_FIELDS
    for key, value in existing.items():
        if key not in known_fields and key not in merged:
            merged[key] = value
    return merged


def write_addon_sidecars(
    targets: Iterable[AddonTarget],
    *,
    platform: str,
    addons_dir: str | Path,
    installed_at: str,
    provided_by_addon: dict[str, list[dict[str, Any]]] | None = None,
) -> list[Path]:
    """Write one sidecar per addon, grouping all targets owned by that addon.

    ``provided_by_addon`` maps addon_id -> ``provided[]`` entries carrying real
    install metadata (dest target, install_mode, content_hash, marker). When an
    addon has no explicit entry, a minimal skill entry is derived per target.
    Returns the written sidecar paths (one per addon, in first-seen order).
    """
    provided_by_addon = provided_by_addon or {}
    target_list = list(targets)
    order: list[str] = []
    representative: dict[str, AddonTarget] = {}
    target_names: dict[str, set[str]] = {}
    for target in target_list:
        if target.addon_id not in representative:
            representative[target.addon_id] = target
            order.append(target.addon_id)
        target_names.setdefault(target.addon_id, set()).add(target.name)
    written: list[Path] = []
    for addon_id in order:
        provided = provided_by_addon.get(addon_id)
        if provided is None:
            provided = [
                _default_skill_provided(t.name, t.source)
                for t in target_list
                if t.addon_id == addon_id
            ]
        else:
            # Coverage invariant is about SKILLS: every installed skill must be
            # recorded. command/resource entries are addon-level extras and are
            # not subject to the skill-name match.
            provided_names = {
                e.get("name") for e in provided
                if isinstance(e, dict) and e.get("kind") == "skill"
            }
            if provided_names != target_names[addon_id]:
                raise ValueError(
                    f"provided_by_addon[{addon_id!r}] skill names "
                    f"{sorted(n for n in provided_names if n)} do not match installed targets "
                    f"{sorted(target_names[addon_id])}"
                )
        record = build_sidecar_record(
            representative[addon_id],
            platform=platform,
            installed_at=installed_at,
            provided=provided,
        )
        record = _merge_existing_sidecar_compat(addon_id, record, addons_dir=addons_dir)
        written.append(addon_registry.write_record(record, addons_dir=addons_dir))
    return written


def detect_collisions(
    targets: Iterable[AddonTarget],
    *,
    skills_dir: str | Path,
    addons_dir: str | Path,
    core_skill_names: Iterable[str] = (),
    claude_commands_dir: str | Path | None = None,
    resources_dir: str | Path | None = None,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    """Find install collisions against the live skills dir and extra dests.

    A collision is a target whose dest already exists on disk and is owned by
    something other than the installing addon. Each collision reports the owner
    as ``core`` / ``addon`` / ``domain`` / ``user``. A dest already owned by the
    SAME addon (reinstall/update) is not a collision.
    """
    skills_root = Path(skills_dir).resolve()
    core = set(core_skill_names)
    targets = list(targets)  # iterated twice (skill targets, then command/resource extras)

    def _norm(path_value: str | Path) -> str:
        # Normalize the LOCATION (parent dir resolved) so /tmp vs /private/tmp etc.
        # do not cause a same-addon reinstall to look like a collision.
        candidate = Path(path_value)
        try:
            return str(candidate.parent.resolve() / candidate.name)
        except OSError:
            return str(candidate)

    owned: dict[str, tuple[str | None, str, str]] = {}
    for record in addon_registry.read_all(addons_dir=addons_dir):
        record_id = record.get("addon_id")
        for entry in record.get("provided", []):
            if isinstance(entry, dict) and isinstance(entry.get("target"), str):
                owned[_norm(entry["target"])] = (
                    record_id,
                    entry.get("content_hash") or "",
                    entry.get("install_mode") or "missing",
                )
    collisions: list[dict[str, Any]] = []
    for target in targets:
        dest = skills_root / target.name
        if not os.path.lexists(dest):
            continue
        owner_id, recorded_hash, recorded_mode = owned.get(_norm(dest), (None, "", "missing"))
        if owner_id == target.addon_id:
            # Same addon already owns this dest. Allow the update ONLY if the live
            # target still matches the recorded content_hash; otherwise the user
            # drifted it (edited copy / repointed link) and overwriting would
            # destroy their change, so flag same-addon drift and abort.
            if _same_addon_target_is_clean(dest, recorded_hash, recorded_mode):
                continue
            # A skill symlink already pointing at THIS install's source is the
            # installer's own (re)target, not a user edit -- not drift. This unwedges
            # a prior failed reinstall that re-pointed the symlink before the sidecar
            # hash could be updated. A symlink pointing ELSEWHERE is still user drift.
            if dest.is_symlink() and _resolves_to(dest, target.source):
                continue
            collisions.append({
                "name": target.name, "dest": str(dest), "owner": "addon-drift",
                "owner_addon_id": owner_id,
            })
            continue
        if target.name in core:
            owner = "core"
        elif owner_id is not None:
            owner = "addon"
        elif (dest / "SKILL.md").exists():
            owner = "domain"
        else:
            owner = "user"
        collisions.append({"name": target.name, "dest": str(dest), "owner": owner, "owner_addon_id": owner_id})

    # Command/resource extras share the same ownership map (their provided[]
    # entries are in `owned`), but their dests live outside skills_dir, so they
    # must be collision-checked here at preflight too -- BEFORE hooks/skills are
    # mutated. A non-owned pre-existing extra dest that only fails at provision
    # time would orphan an already-installed addon skill + hook with no sidecar.
    commands_root = Path(claude_commands_dir) if claude_commands_dir else None
    resources_root = Path(resources_dir) if resources_dir else None
    for target in targets:
        extra_dests: list[tuple[str, Path]] = []
        if platform == "claude" and commands_root is not None:
            extra_dests += [(name, commands_root / f"{name}.md") for name, _src in target.commands]
        if resources_root is not None:
            extra_dests += [(rel, resources_root / target.addon_id / rel) for rel, _src in target.resources]
        for extra_name, dest in extra_dests:
            if not os.path.lexists(dest):
                continue
            owner_id, recorded_hash, recorded_mode = owned.get(_norm(dest), (None, "", "missing"))
            if owner_id == target.addon_id and _same_addon_target_is_clean(dest, recorded_hash, recorded_mode):
                continue
            collisions.append({
                "name": extra_name, "dest": str(dest),
                "owner": "addon" if owner_id is not None else "user",
                "owner_addon_id": owner_id,
            })
    return collisions


def _same_addon_target_is_clean(dest: Path, recorded_hash: str, recorded_mode: str) -> bool:
    """True iff a same-addon dest can be safely overwritten on reinstall.

    Clean means the live target still hashes to the content_hash the sidecar
    recorded at install time. Missing recorded hash/mode, or any hashing error,
    is treated as NOT clean (fail closed -- ownership of the live bytes is
    unprovable, so we must not clobber it).
    """
    if not recorded_hash or recorded_mode in ("", "missing"):
        return False
    try:
        return hash_utils.hash_target(str(dest), recorded_mode) == recorded_hash
    except Exception:
        return False


def _resolves_to(dest: Path, source: Path) -> bool:
    """True iff ``dest`` and ``source`` resolve to the same real path."""
    try:
        return os.path.realpath(dest) == os.path.realpath(source)
    except OSError:
        return False


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
    # name|source|addon_id. The installer keeps name|source for generic target
    # iteration and uses the trailing addon_id only to attribute copy-mode
    # ownership markers for copied addon targets.
    return "\n".join(f"{target.name}|{target.source}|{target.addon_id}" for target in targets)


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


def _detect_install_mode(dest: Path) -> str:
    if dest.is_symlink():
        return "symlink"
    if dest.exists():
        return "copy"
    return "missing"


def _provided_extra(kind: str, name: str, dest: Path) -> dict[str, Any]:
    return {
        "kind": kind, "name": name, "target": str(dest), "ownership": "addon",
        "install_mode": "copy", "content_hash": hash_utils.hash_target(str(dest), "copy"),
        "marker": "", "metadata": {},
    }


def _within(base_real: Path, candidate: Path) -> bool:
    """True iff ``candidate`` stays under ``base_real`` after collapsing ``..``."""
    normalized = Path(os.path.normpath(str(base_real / candidate)))
    return normalized == base_real or base_real in normalized.parents


def _existing_extra_targets(addon_id: str, addons_dir: str | Path | None) -> dict[str, tuple[str, str]]:
    """Normalized command/resource target -> recorded content hash + install mode."""
    owned: dict[str, tuple[str, str]] = {}
    if not addons_dir:
        return owned
    try:
        record = addon_registry.read_record(addon_id, addons_dir=addons_dir)
    except (addon_registry.RecordNotFound, addon_registry.RegistryError):
        return owned
    for entry in record.get("provided", []):
        if (isinstance(entry, dict) and entry.get("kind") in ("command", "resource")
                and isinstance(entry.get("target"), str)):
            owned[os.path.normpath(entry["target"])] = (
                entry.get("content_hash") or "",
                entry.get("install_mode") or "missing",
            )
    return owned


def _safe_provision(src: str, base: Path, dest: Path, owned: dict[str, tuple[str, str]]) -> None:
    """Copy ``src`` to ``dest`` under ``base`` without following symlinks or clobbering.

    Security (addon review): the original code did a bare ``shutil.copyfile`` which (a)
    silently overwrote any pre-existing file and (b) wrote THROUGH a symlink at the
    dest leaf/parent, letting an addon escape the containment dir and overwrite
    arbitrary files. This refuses any symlink in the chain base->dest, refuses to
    clobber a path this addon does not already own, and writes atomically (temp +
    os.replace) so a write can never traverse a symlink.
    """
    # Lexical containment FIRST (do not delegate to the caller): collapse ".." and
    # require dest to stay under base, so _safe_provision is self-sufficient even if a
    # future caller forgets its own _within() check.
    base_norm = Path(os.path.normpath(str(base)))
    dest_norm = Path(os.path.normpath(str(dest)))
    if dest_norm != base_norm and base_norm not in dest_norm.parents:
        raise AddonManifestError(f"refusing to provision outside base: {dest}")
    if base.is_symlink():
        raise AddonManifestError(f"refusing to provision through a symlink at {base}")
    cur = base
    for part in dest.relative_to(base).parts:
        cur = cur / part
        if cur.is_symlink():
            raise AddonManifestError(f"refusing to provision through a symlink at {cur}")
    if os.path.lexists(dest):
        proof = owned.get(os.path.normpath(str(dest)))
        if not dest.is_file() or proof is None:
            raise AddonManifestError(f"refusing to overwrite non-addon path {dest}")
        recorded_hash, recorded_mode = proof
        if not _same_addon_target_is_clean(dest, recorded_hash, recorded_mode):
            raise AddonManifestError(f"refusing to overwrite modified addon path {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.",
        suffix=".provision.tmp",
        dir=dest.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        os.replace(tmp_path, dest)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def provision_addon_extras(
    targets: Iterable[AddonTarget],
    *,
    platform: str,
    claude_commands_dir: str | Path | None,
    resources_dir: str | Path | None,
    addons_dir: str | Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Provision addon commands (Claude-only) and resources, return provided[] entries.

    Commands are copied to the Claude commands dir only on the claude platform.
    Resources are copied under ``<resources_dir>/<addon_id>/``. Both lexical
    escapes such as ``..`` and on-disk symlinks in the destination chain are
    refused, and a pre-existing path this addon does not own is never clobbered.
    Each provisioned file is recorded as a copy-mode provided[] entry (kind
    command / resource) so the generic uninstall and doctor machinery cover it.
    Idempotent: reinstalling overwrites only the addon's own files.
    """
    extra: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for target in targets:
        if target.addon_id in seen:
            continue
        seen.add(target.addon_id)
        owned = _existing_extra_targets(target.addon_id, addons_dir)
        entries: list[dict[str, Any]] = []
        if platform == "claude" and claude_commands_dir and target.commands:
            cdir = Path(claude_commands_dir)
            cdir.mkdir(parents=True, exist_ok=True)
            for name, src in target.commands:
                dest = cdir / f"{name}.md"
                _safe_provision(src, cdir, dest, owned)
                entries.append(_provided_extra("command", name, dest))
        if resources_dir and target.resources:
            base = Path(resources_dir) / target.addon_id
            base.mkdir(parents=True, exist_ok=True)
            base_real = base.resolve()
            for rel, src in target.resources:
                if not _within(base_real, Path(rel)):
                    raise AddonManifestError(
                        f"addon {target.addon_id!r}: resource path {rel!r} escapes {base}")
                _safe_provision(src, base, base / rel, owned)
                entries.append(_provided_extra("resource", rel, base / rel))
        if entries:
            extra[target.addon_id] = entries
    return extra


def _normalized_location(path: Path) -> str:
    try:
        return str(path.parent.resolve() / path.name)
    except OSError:
        return str(path)


def _target_is_missing_or_dangling(path: Path) -> bool:
    if not os.path.lexists(path):
        return True
    if path.is_symlink() and not path.exists():
        path.unlink()
        return True
    return False


def _addon_root_source_maps(addon_root: Path, addon_id: str) -> dict[str, dict[str, Path]]:
    manifest_path = addon_root / ADDON_MANIFEST
    manifest = _read_json(manifest_path)
    declared = _validate_id(
        _require_string(manifest.get("addon_id"), "addon_id", manifest_path),
        "addon_id",
        manifest_path,
    )
    if declared != addon_id:
        raise AddonManifestError(f"{manifest_path}: addon_id {declared!r} does not match sidecar {addon_id!r}")

    skills: dict[str, Path] = {}
    commands: dict[str, Path] = {}
    resources: dict[str, Path] = {}
    for skill in manifest.get("skills", []) or []:
        if not isinstance(skill, dict):
            continue
        name = _validate_id(_require_string(skill.get("name"), "skills[].name", manifest_path),
                            "skills[].name", manifest_path)
        src = _safe_child_path(
            addon_root,
            _require_string(skill.get("skill_dir"), "skills[].skill_dir", manifest_path),
            "skills[].skill_dir",
            manifest_path,
        )
        if not (src / "SKILL.md").is_file():
            raise AddonManifestError(f"{manifest_path}: missing SKILL.md for {name!r}")
        skills[name] = src
    for command in manifest.get("commands", []) or []:
        if not isinstance(command, dict):
            continue
        name = _validate_id(_require_string(command.get("name"), "commands[].name", manifest_path),
                            "commands[].name", manifest_path)
        src = _safe_child_path(
            addon_root,
            _require_string(command.get("source"), "commands[].source", manifest_path),
            "commands[].source",
            manifest_path,
        )
        if not src.is_file():
            raise AddonManifestError(f"{manifest_path}: missing command source for {name!r}")
        commands[name] = src
    for resource in manifest.get("resources", []) or []:
        if not isinstance(resource, dict):
            continue
        rel = _require_string(resource.get("path"), "resources[].path", manifest_path)
        src = _safe_child_path(
            addon_root,
            _require_string(resource.get("source"), "resources[].source", manifest_path),
            "resources[].source",
            manifest_path,
        )
        if not src.is_file():
            raise AddonManifestError(f"{manifest_path}: missing resource source for {rel!r}")
        resources[rel] = src
    return {"skill": skills, "command": commands, "resource": resources}


def _repair_skill_target(src: Path, dest: Path, install_mode: str) -> str:
    if not _target_is_missing_or_dangling(dest):
        return "occupied"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if install_mode in {"symlink", "junction"}:
        try:
            dest.symlink_to(src, target_is_directory=True)
            return "symlink"
        except OSError:
            pass
    shutil.copytree(src, dest)
    return "copy"


def repair_missing_addon_targets(
    *,
    platform: str,
    addons_dir: str | Path,
    skills_dir: str | Path,
    claude_commands_dir: str | Path | None,
    resources_dir: str | Path | None,
) -> dict[str, Any]:
    records, skipped = addon_registry.scan_records(addons_dir=addons_dir)
    report: dict[str, Any] = {
        "repaired": [],
        "kept": [],
        "errors": [{"sidecar": name, "reason": reason} for name, reason in skipped],
    }
    for record in records:
        addon_id = str(record.get("addon_id"))
        addon_root = Path(str(record.get("source", "")))
        try:
            source_maps = _addon_root_source_maps(addon_root, addon_id)
        except (AddonManifestError, OSError) as exc:
            report["errors"].append({"addon_id": addon_id, "reason": str(exc)})
            continue

        changed = False
        for entry in record.get("provided", []):
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind")
            name = entry.get("name")
            target = entry.get("target")
            mode = entry.get("install_mode") or "missing"
            if not isinstance(kind, str) or not isinstance(name, str) or not isinstance(target, str):
                continue

            dest = Path(target)
            try:
                if kind == "skill":
                    expected = Path(skills_dir) / name
                    src = source_maps["skill"].get(name)
                    if src is None or _normalized_location(dest) != _normalized_location(expected):
                        report["errors"].append({"addon_id": addon_id, "target": target, "reason": "skill-target-mismatch"})
                        continue
                    actual_mode = _repair_skill_target(src, expected, mode)
                elif kind == "command":
                    if platform != "claude" or claude_commands_dir is None:
                        continue
                    expected = Path(claude_commands_dir) / f"{name}.md"
                    src = source_maps["command"].get(name)
                    if src is None or _normalized_location(dest) != _normalized_location(expected):
                        report["errors"].append({"addon_id": addon_id, "target": target, "reason": "command-target-mismatch"})
                        continue
                    if not _target_is_missing_or_dangling(expected):
                        actual_mode = "occupied"
                    else:
                        _safe_provision(str(src), Path(claude_commands_dir), expected, {})
                        actual_mode = "copy"
                elif kind == "resource":
                    if resources_dir is None:
                        continue
                    base = Path(resources_dir) / addon_id
                    expected = base / name
                    src = source_maps["resource"].get(name)
                    if src is None or not _within(base.resolve(), Path(name)) or _normalized_location(dest) != _normalized_location(expected):
                        report["errors"].append({"addon_id": addon_id, "target": target, "reason": "resource-target-mismatch"})
                        continue
                    if not _target_is_missing_or_dangling(expected):
                        actual_mode = "occupied"
                    else:
                        base.mkdir(parents=True, exist_ok=True)
                        _safe_provision(str(src), base, expected, {})
                        actual_mode = "copy"
                else:
                    continue
            except (AddonManifestError, OSError) as exc:
                report["errors"].append({"addon_id": addon_id, "target": target, "reason": str(exc)})
                continue

            if actual_mode == "occupied":
                report["kept"].append({"addon_id": addon_id, "kind": kind, "name": name, "reason": "occupied"})
                continue
            entry["install_mode"] = actual_mode
            entry["content_hash"] = hash_utils.hash_target(str(dest), actual_mode)
            changed = True
            report["repaired"].append({"addon_id": addon_id, "kind": kind, "name": name})

        if changed:
            addon_registry.write_record(record, addons_dir=addons_dir)
    return report


def _main_write_sidecars(argv: list[str]) -> int:
    """`write-sidecars` subcommand: write per-addon sidecars after install.

    Re-resolves addon targets from the source(s) to recover full addon identity,
    derives each skill's installed dest under --skills-dir, detects the install
    mode, computes content_hash from the dest, and writes one sidecar per addon.
    """
    parser = argparse.ArgumentParser(prog="addon_installer.py write-sidecars")
    parser.add_argument("--source", action="append", default=[], required=True)
    parser.add_argument("--platform", choices=["claude", "codex"], default="claude")
    parser.add_argument("--addons-dir", required=True)
    parser.add_argument("--skills-dir", required=True)
    parser.add_argument("--installed-at", required=True)
    parser.add_argument("--claude-commands-dir", default=None,
                        help="Claude commands dir for addon command provisioning")
    parser.add_argument("--resources-dir", default=None,
                        help="platform-scoped base for addon resource provisioning")
    args = parser.parse_args(argv)
    try:
        targets = load_addon_targets(args.source, platform=args.platform)
    except AddonManifestError as exc:
        print(f"addon manifest error: {exc}", file=sys.stderr)
        return 1
    skills_dir = Path(args.skills_dir)
    provided_by_addon: dict[str, list[dict[str, Any]]] = {}
    for target in targets:
        dest = skills_dir / target.name
        install_mode = _detect_install_mode(dest)
        provided_by_addon.setdefault(target.addon_id, []).append(
            {
                "kind": "skill",
                "name": target.name,
                "target": str(dest),
                "ownership": "addon",
                "install_mode": install_mode,
                "content_hash": hash_utils.hash_target(str(dest), install_mode),
                "marker": "",
                "metadata": {},
            }
        )
    try:
        extras = provision_addon_extras(
            targets, platform=args.platform,
            claude_commands_dir=args.claude_commands_dir, resources_dir=args.resources_dir,
            addons_dir=args.addons_dir)
    except (AddonManifestError, OSError) as exc:
        print(f"addon extras provisioning error: {exc}", file=sys.stderr)
        return 1
    for addon_id, entries in extras.items():
        provided_by_addon.setdefault(addon_id, []).extend(entries)
    try:
        write_addon_sidecars(
            targets,
            platform=args.platform,
            addons_dir=args.addons_dir,
            installed_at=args.installed_at,
            provided_by_addon=provided_by_addon,
        )
    except (AddonManifestError, addon_registry.RegistryError, ValueError) as exc:
        print(f"addon sidecar write error: {exc}", file=sys.stderr)
        return 1
    return 0


def _main_detect_collisions(argv: list[str]) -> int:
    """`detect-collisions` subcommand: nonzero exit + JSON if any collision."""
    parser = argparse.ArgumentParser(prog="addon_installer.py detect-collisions")
    parser.add_argument("--source", action="append", default=[], required=True)
    parser.add_argument("--platform", choices=["claude", "codex"])
    parser.add_argument("--skills-dir", required=True)
    parser.add_argument("--addons-dir", required=True)
    parser.add_argument("--core-skill", action="append", default=[])
    parser.add_argument("--claude-commands-dir", default=None)
    parser.add_argument("--resources-dir", default=None)
    args = parser.parse_args(argv)
    try:
        targets = load_addon_targets(args.source, platform=args.platform)
    except AddonManifestError as exc:
        print(f"addon manifest error: {exc}", file=sys.stderr)
        return 1
    collisions = detect_collisions(
        targets, skills_dir=args.skills_dir, addons_dir=args.addons_dir,
        core_skill_names=args.core_skill,
        claude_commands_dir=args.claude_commands_dir,
        resources_dir=args.resources_dir, platform=args.platform,
    )
    if collisions:
        for collision in collisions:
            print(
                f"addon install collision: {collision['name']!r} already exists at "
                f"{collision['dest']} (owner={collision['owner']})",
                file=sys.stderr,
            )
        return 2
    return 0


def _main_repair_missing(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="addon_installer.py repair-missing")
    parser.add_argument("--platform", choices=["claude", "codex"], default="claude")
    parser.add_argument("--addons-dir", required=True)
    parser.add_argument("--skills-dir", required=True)
    parser.add_argument("--claude-commands-dir", default=None)
    parser.add_argument("--resources-dir", default=None)
    args = parser.parse_args(argv)
    try:
        report = repair_missing_addon_targets(
            platform=args.platform,
            addons_dir=args.addons_dir,
            skills_dir=args.skills_dir,
            claude_commands_dir=args.claude_commands_dir,
            resources_dir=args.resources_dir,
        )
    except addon_registry.RegistryError as exc:
        print(f"addon repair error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("errors") else 0


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "write-sidecars":
        return _main_write_sidecars(args_list[1:])
    if args_list and args_list[0] == "detect-collisions":
        return _main_detect_collisions(args_list[1:])
    if args_list and args_list[0] == "repair-missing":
        return _main_repair_missing(args_list[1:])
    parser = build_parser()
    args = parser.parse_args(args_list)
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
