"""Fixed core dispatch for addons that own a complete runtime bundle.

The Secretary installer owns the runtime, skills and every platform sidecar in
one transaction. Core never interprets a manifest or sidecar command as code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import addon_installer
import addon_registry

SECRETARY_ID = "ai-secretary"
PLATFORMS = frozenset({"codex", "claude"})


class ManagedAddonError(Exception):
    """An owned bundle cannot be installed or removed safely."""


def _prefix() -> Path:
    path = Path.home() / ".local/share/ai-secretary"
    return path.parent.resolve() / path.name


def _registry_dirs(platforms: Any) -> dict[str, Path]:
    if not platforms or set(platforms) - PLATFORMS:
        raise ManagedAddonError("Secretary requires codex and/or claude platform selection")
    base = (Path.home() / ".ghost-alice/addons").resolve()
    return {key: base / key for key in sorted(platforms)}


def classify_source(source: Path) -> str:
    """Reserve the known addon id for its fixed installer, not generic copying."""
    targets = addon_installer.load_addon_targets([source])
    ids = {target.addon_id for target in targets}
    if SECRETARY_ID not in ids:
        return "generic"
    if ids != {SECRETARY_ID} or len(targets) != 1 or targets[0].name != SECRETARY_ID:
        raise ManagedAddonError("Secretary's bundle source must contain only the ai-secretary addon")
    return "managed"


def _read_manifest(prefix: Path) -> dict[str, Any]:
    path = prefix / "install-manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ManagedAddonError("Secretary ownership manifest is missing or redirected; preserve the bundle")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManagedAddonError("Secretary ownership manifest is unreadable; preserve the bundle") from exc
    if not isinstance(manifest, dict) or manifest.get("prefix") != str(prefix):
        raise ManagedAddonError("Secretary ownership manifest identity does not match the fixed bundle prefix")
    return manifest


def _manifest_arguments(manifest: dict[str, Any], prefix: Path) -> list[str]:
    registries = manifest.get("platform_registries")
    if not isinstance(registries, dict) or not registries:
        raise ManagedAddonError("Existing standalone Secretary install uses its original registry; run its owned "
            "uninstaller with the original --registry-dir and --skills-dir arguments, then retry the official addon install")
    expected = _registry_dirs(registries)
    if registries != {key: str(path) for key, path in expected.items()}:
        raise ManagedAddonError("Secretary platform registry paths do not match core's ordinary registry directories")
    resources = manifest.get("resources")
    if not isinstance(resources, list) or any(not isinstance(resource, dict) for resource in resources):
        raise ManagedAddonError("Secretary ownership resources are missing or malformed")
    if any(not isinstance(resource.get("entries"), dict) for resource in resources):
        raise ManagedAddonError("Secretary ownership resource inventories are missing or malformed")
    skills = [resource.get("path") for resource in resources if isinstance(resource, dict) and resource.get("role") == "skill"]
    if not skills or any(not isinstance(path, str) or Path(path).name != SECRETARY_ID for path in skills):
        raise ManagedAddonError("Secretary skill ownership is invalid")
    allowed_skills = {Path.home() / ".agents/skills/ai-secretary",
        Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "skills/ai-secretary"}
    allowed_skills = {path.parent.resolve() / path.name for path in allowed_skills}
    if len(set(skills)) != len(skills) or any(Path(path) not in allowed_skills for path in skills):
        raise ManagedAddonError("Secretary skill ownership is outside the configured core platform paths")
    args = ["--prefix", str(prefix)]
    for path in skills:
        args += ["--skills-dir", str(Path(path).parent)]
    for key, path in expected.items():
        args += ["--platform-registry", f"{key}={path}"]
    return args


def _run_installer(script: Path, command: str, args: list[str], core_root: Path) -> dict[str, Any]:
    result = subprocess.run([sys.executable, str(script), command, *args, "--ghost-alice-root", str(core_root)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if result.returncode:
        raise ManagedAddonError(f"Secretary owned installer refused {command}: {(result.stderr or result.stdout).strip()}")
    try:
        report = json.loads(result.stdout)
    except ValueError as exc:
        raise ManagedAddonError("Secretary owned installer returned an unreadable report") from exc
    if not isinstance(report, dict):
        raise ManagedAddonError("Secretary owned installer returned an invalid report")
    return report


def install_secretary(source: Path, *, core_root: Path, platform_targets: dict[str, Path]) -> dict[str, Any]:
    """Install one bundle; optional cache paths retain the addon's pin checks."""
    # The shipped native desktop worker uses AppKit/xcrun. Reject before any
    # core or bundle install writes rather than leaving a skills-only install.
    if platform.system() != "Darwin":
        raise ManagedAddonError("The ai-secretary desktop bundle currently requires macOS")
    registries = _registry_dirs(platform_targets)
    source = source.resolve()
    if classify_source(source) != "managed":
        raise ManagedAddonError("Source is not the Secretary bundle")
    script = source / "scripts/installer.py"
    if script.is_symlink() or not script.is_file() or script.resolve().parent != source / "scripts":
        raise ManagedAddonError("Secretary's fixed scripts/installer.py entrypoint is missing or redirected")
    prefix = _prefix()
    args = ["--prefix", str(prefix), "--source", str(source)]
    skills = {key: Path(path).parent.resolve() / Path(path).name for key, path in platform_targets.items()}
    if (prefix / "install-manifest.json").exists():
        manifest = _read_manifest(prefix)
        _manifest_arguments(manifest, prefix)
        recorded_skills = {resource["path"] for resource in manifest["resources"] if resource["role"] == "skill"}
        if manifest["platform_registries"] != {key: str(path) for key, path in registries.items()} or recorded_skills != {str(path / SECRETARY_ID) for path in skills.values()}:
            raise ManagedAddonError("Changing Secretary's installed platform selection requires its original owned uninstall, then reinstall")
    for key, path in sorted(skills.items()):
        args += ["--skills-dir", str(path), "--platform-registry", f"{key}={registries[key]}"]
    for variable, flag in (("SECRETARY_OPENCLAW_SOURCE", "--openclaw-source"), ("SECRETARY_NODE_SOURCE", "--node-source")):
        value = os.environ.get(variable)
        if value:
            args += [flag, str(Path(value).expanduser())]
    return _run_installer(script, "install", args, core_root)


def uninstall_secretary(record: dict[str, Any], *, addons_dir: Path, confirm: bool) -> dict[str, Any]:
    """Delegate complete bundle removal before generic core target deletion."""
    try:
        prefix = _prefix()
        installation = record.get("installation")
        if not isinstance(installation, dict) or installation.get("installer") != SECRETARY_ID or installation.get("prefix") != str(prefix):
            raise ManagedAddonError("Secretary sidecar does not identify the fixed owned bundle")
        manifest = _read_manifest(prefix)
        args = _manifest_arguments(manifest, prefix)
        registries = manifest["platform_registries"]
        if installation.get("platform_registries") != registries or str(addons_dir) not in registries.values():
            raise ManagedAddonError("Secretary sidecar and owned platform registries disagree")
        selected_records = []
        for key, directory in sorted(registries.items()):
            selected = addon_registry.read_record(SECRETARY_ID, addons_dir=directory)
            owner = selected.get("installation")
            if not isinstance(owner, dict) or owner.get("installer") != SECRETARY_ID or owner.get("prefix") != str(prefix) or owner.get("platform_registries") != registries or selected.get("platform") != key:
                raise ManagedAddonError("Secretary's selected platform sidecar has inconsistent ownership")
            selected_records.append({"platform": key, "addons_dir": directory, "provided": selected["provided"]})
        script = prefix / "managed/app/scripts/installer.py"
        for path in [prefix, script.parent.parent.parent, script.parent.parent, script.parent, script]:
            if path.is_symlink():
                raise ManagedAddonError("Secretary owned uninstaller path is redirected")
        runtime = next((resource for resource in manifest["resources"] if resource.get("role") == "runtime" and resource.get("path") == str(prefix / "managed")), None)
        entry = runtime.get("entries", {}).get("app/scripts/installer.py") if runtime else None
        if not isinstance(entry, dict) or entry.get("kind") != "file" or not script.is_file() or hashlib.sha256(script.read_bytes()).hexdigest() != entry.get("sha256"):
            raise ManagedAddonError("Secretary owned uninstaller was modified; preserve the bundle")
        if not confirm:
            return {"addon_id": SECRETARY_ID, "status": "dry-run", "items": [{"name": SECRETARY_ID, "action": "would-remove", "scope": "complete shared bundle"}]}
        report = _run_installer(script, "uninstall", args, Path(__file__).resolve().parents[1])
        if report.get("status") != "uninstalled":
            return {"addon_id": SECRETARY_ID, "status": "partial", "items": [], "reason": "Owned uninstaller preserved changed resources", "owned_report": report}
        return {"addon_id": SECRETARY_ID, "status": "removed", "items": [], "owned_report": report,
            "removed_platform_records": selected_records}
    except (ManagedAddonError, addon_registry.RegistryError, OSError, KeyError, TypeError) as exc:
        return {"addon_id": SECRETARY_ID, "status": "partial", "items": [], "reason": str(exc)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["classify", "install"])
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--core-root", type=Path)
    parser.add_argument("--platform-target", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == "classify":
            print(classify_source(args.source))
        else:
            targets = {}
            for item in args.platform_target:
                key, path = item.split("=", 1)
                if key in targets:
                    raise ManagedAddonError("Duplicate platform selection")
                targets[key] = Path(path)
            if args.core_root is None:
                raise ManagedAddonError("Core root is required")
            print(json.dumps(install_secretary(args.source, core_root=args.core_root, platform_targets=targets)))
    except (ManagedAddonError, addon_installer.AddonManifestError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
