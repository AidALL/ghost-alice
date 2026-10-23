"""The install record: what the installer installed where, for every host, in one file.

`~/.ghost-alice/installed-payload.json` holds schema version 1:

- `installer`: `{commit, schema_version}` of the installer that last wrote the record.
- `source`: `{root, commit, dirty, hooks_path}` of the source checkout.
- `last_transaction`: `{id, updated_at, rollback_dir}` of the last publish.
- `targets`: one entry per install location path, used by one or more hosts:
  `name`, `kind`, `mode`, `provider`, `source`, `source_commit`, `used_by`,
  `link_target` (a link) or `files` (a copy: relative path -> `content_digest`),
  `installed_at` and `baseline` (`recorded` by a publish, `migrated` from an
  earlier installer's records, `unknown` when those records had no fingerprint).
- `service` (optional): the registered context gateway: `label`, `runtime_root`
  (the folder of the runtime `_shared` the service runs from), `program`, the three
  service files `launch_agent`, `state_path` and `config_path` with their
  fingerprints (`launch_agent_digest`, `state_digest`, `config_block_digest` of the
  provider blocks, None until a registration recorded it), `config_backup`,
  `registered_at`, `restarted_at` and `listener_ready`.
- `hooks` (optional): the hook registrations the hosts carry, written by
  `hook_launcher`: `launcher` (`path`, `python`, `digest`, `written_at` of the launcher
  file) and per host (`claude`, `codex`) `hook_file`, `destination`, `entries` (per
  hook id: `event`, `marker`, `command`, `provider`) and `registered_at`.

`update` is the one writer of record sections and `restore` puts back a copy a
publish saved; both write through a temporary file and a rename, and refuse unless
this process or the process that started it holds the install lock. `migrate`
builds the record once from the records an earlier installer wrote and
`migrate_service` the `service` section once from the gateway's installation
receipt; neither changes those records.

Dependencies: Python standard library, `content_digest`, `install_lock`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence
import uuid

import content_digest
import install_lock

SCHEMA_VERSION = 1
FILENAME = "installed-payload.json"
LOCK_FILENAME = "install.lock"
SECTIONS = ("installer", "source", "last_transaction", "targets", "service", "hooks")
KINDS = ("skill", "support", "runtime", "command", "resource")
MODES = ("link", "copy")
BASELINES = ("recorded", "migrated", "unknown")
SERVICE_KEYS = ("label", "runtime_root", "program", "launch_agent", "launch_agent_digest", "state_path",
                "state_digest", "config_path", "config_block_digest", "config_backup", "registered_at",
                "restarted_at", "listener_ready")
HOOK_HOSTS = ("claude", "codex")
HOOK_HOST_KEYS = ("hook_file", "destination", "entries", "registered_at")
HOOK_ENTRY_KEYS = ("event", "marker", "command", "provider")
HOOK_LAUNCHER_KEYS = ("path", "python", "digest", "written_at")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# The gateway's own installation receipt and binding, read once by `migrate_service`.
BINDING_FILENAME = "context-gateway-binding.json"
_RECEIPT_SCHEMA = "context-gateway-installation.v1"

# How an earlier installer named the install mode of a target.
_EARLIER_MODES = {"symlink": "link", "junction": "link", "copy": "copy", "copy-fallback": "copy"}


class RecordError(ValueError):
    """The install record, or a section given to write into it, is not schema version 1."""


class NotLockHolder(RuntimeError):
    """Neither this process nor the process that started it holds the install lock."""


def path(ghost_root: Path | str) -> Path:
    """`<ghost_root>/installed-payload.json`."""
    return Path(ghost_root) / FILENAME


def read(ghost_root: Path | str) -> dict | None:
    """The record, or None when there is none; RecordError when it is unreadable or another schema version."""
    file = path(ghost_root)
    try:
        raw = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise RecordError(f"{file} cannot be read: {exc}") from exc
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecordError(f"{file} is not JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise RecordError(f"{file} is not a JSON object")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise RecordError(f"{file} has schema_version {record.get('schema_version')!r}; this installer reads "
                          f"{SCHEMA_VERSION}")
    if not isinstance(record.get("targets", {}), dict):
        raise RecordError(f"{file} has targets that are not an object")
    return record


def _require_lock(ghost_root: Path | str) -> None:
    lock = Path(ghost_root) / LOCK_FILENAME
    try:
        held = install_lock.held_by_process_tree(lock)
    except install_lock.InstallLockError as exc:
        raise NotLockHolder(f"the install record is written only under the install lock: {exc}") from exc
    if not held:
        raise NotLockHolder(f"the install record is written only by the holder of {lock} or its child process")


def _write(file: Path, data: bytes) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(f".{file.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, file)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _encode(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _check_entry(key: str, entry: object) -> dict:
    if not isinstance(entry, Mapping):
        raise RecordError(f"target {key} is not an object")
    if entry.get("mode") not in MODES or entry.get("kind") not in KINDS or entry.get("baseline") not in BASELINES:
        raise RecordError(f"target {key} needs mode {MODES}, kind {KINDS} and baseline {BASELINES}")
    used_by = entry.get("used_by")
    if not isinstance(used_by, list) or not all(isinstance(host, str) for host in used_by):
        raise RecordError(f"target {key} needs used_by as a list of hosts")
    return dict(entry)


def _absolute(value: object) -> bool:
    return isinstance(value, str) and Path(value).is_absolute()


def _check_service(section: object) -> dict:
    if not isinstance(section, Mapping):
        raise RecordError("section service is not an object")
    missing = [key for key in SERVICE_KEYS if key not in section]
    unknown = sorted(set(section) - set(SERVICE_KEYS))
    if missing or unknown:
        raise RecordError(f"service needs exactly the keys {SERVICE_KEYS}; missing {missing}, unknown {unknown}")
    if any(not isinstance(section[key], str) or not section[key] for key in ("label", "registered_at")):
        raise RecordError("service label and registered_at must be nonempty strings")
    if any(not _absolute(section[key]) for key in ("runtime_root", "program", "launch_agent", "state_path", "config_path")):
        raise RecordError("service runtime_root, program, launch_agent, state_path and config_path must be absolute paths")
    if Path(section["runtime_root"]) not in Path(section["program"]).parents:
        raise RecordError("service program must be under runtime_root")
    if any(not isinstance(section[key], str) or not _DIGEST.fullmatch(section[key])
           for key in ("launch_agent_digest", "state_digest")):
        raise RecordError("service launch_agent_digest and state_digest must be sha256 digests")
    block = section["config_block_digest"]
    if block is not None and (not isinstance(block, str) or not _DIGEST.fullmatch(block)):
        raise RecordError("service config_block_digest must be a sha256 digest or None")
    if any(section[key] is not None and not isinstance(section[key], str) for key in ("config_backup", "restarted_at")):
        raise RecordError("service config_backup and restarted_at must be strings or None")
    if not isinstance(section["listener_ready"], bool):
        raise RecordError("service listener_ready must be a boolean")
    return dict(section)


def _check_keys(what: str, value: object, keys: tuple[str, ...]) -> dict:
    if not isinstance(value, Mapping):
        raise RecordError(f"{what} is not an object")
    missing = [key for key in keys if key not in value]
    unknown = sorted(set(value) - set(keys))
    if missing or unknown:
        raise RecordError(f"{what} needs exactly {', '.join(keys)}; missing {missing}, unknown {unknown}")
    return dict(value)


def _check_hooks(patch: object) -> dict:
    """The `hooks` patch: `launcher` and the hosts, each an object of the documented keys, or None to delete it."""
    if not isinstance(patch, Mapping):
        raise RecordError("section hooks is not an object")
    checked: dict = {}
    for key, value in patch.items():
        if key not in (*HOOK_HOSTS, "launcher"):
            raise RecordError(f"unknown hooks key: {key}")
        if value is None:
            checked[key] = None
            continue
        if key == "launcher":
            launcher = _check_keys("hooks launcher", value, HOOK_LAUNCHER_KEYS)
            if not _absolute(launcher["path"]):
                raise RecordError("hooks launcher path must be an absolute path")
            if any(not isinstance(launcher[field], str) for field in ("python", "written_at")):
                raise RecordError("hooks launcher python and written_at must be strings")
            if not isinstance(launcher["digest"], str) or not _DIGEST.fullmatch(launcher["digest"]):
                raise RecordError("hooks launcher digest must be sha256:<hex>")
            checked[key] = launcher
            continue
        host = _check_keys(f"hooks {key}", value, HOOK_HOST_KEYS)
        if not _absolute(host["hook_file"]):
            raise RecordError(f"hooks {key} hook_file must be an absolute path")
        if not isinstance(host["destination"], Mapping):
            raise RecordError(f"hooks {key} destination is not an object")
        if not isinstance(host["entries"], Mapping) or not isinstance(host["registered_at"], str):
            raise RecordError(f"hooks {key} needs entries as an object and registered_at as a string")
        entries = {}
        for hook_id, entry in host["entries"].items():
            fields = _check_keys(f"hooks {key} entry {hook_id}", entry, HOOK_ENTRY_KEYS)
            if not isinstance(hook_id, str) or any(not isinstance(fields[field], str) for field in HOOK_ENTRY_KEYS):
                raise RecordError(f"hooks {key} entry {hook_id} needs string fields")
            entries[hook_id] = fields
        checked[key] = {**host, "destination": dict(host["destination"]), "entries": entries}
    return checked


def update(ghost_root: Path | str, patches: Mapping[str, Mapping | None]) -> dict:
    """Write the given sections in one replacement of the record and return the record.

    `targets` changes only the entries it names, and an entry keeps every host its
    path already had in `used_by`; `hooks` changes only the keys it names (`launcher`
    and the hosts), a key given as None goes, and the section goes with its last key;
    `installer`, `source`, `last_transaction` and `service` replace their section, and
    `service` None deletes it. Sections that are not given stay as they are.
    """
    _require_lock(ghost_root)
    unknown = sorted(set(patches) - set(SECTIONS))
    if unknown:
        raise RecordError(f"unknown install record sections: {', '.join(unknown)}")
    record = read(ghost_root) or {"schema_version": SCHEMA_VERSION, "targets": {}}
    targets = record.setdefault("targets", {})
    for section, patch in patches.items():
        if section == "service":
            if patch is None:
                record.pop("service", None)
            else:
                record["service"] = _check_service(patch)
            continue
        if not isinstance(patch, Mapping):
            raise RecordError(f"section {section} is not an object")
        if section == "hooks":
            hooks = dict(record.get("hooks") or {})
            for key, value in _check_hooks(patch).items():
                if value is None:
                    hooks.pop(key, None)
                else:
                    hooks[key] = value
            if hooks:
                record["hooks"] = hooks
            else:
                record.pop("hooks", None)
            continue
        if section != "targets":
            record[section] = dict(patch)
            continue
        for key, entry in patch.items():
            merged = _check_entry(key, entry)
            previous = targets.get(key)
            if isinstance(previous, Mapping) and isinstance(previous.get("used_by"), list):
                merged["used_by"] = sorted(set(previous["used_by"]) | set(merged["used_by"]))
            targets[key] = merged
    _write(path(ghost_root), _encode(record))
    return record


def restore(ghost_root: Path | str, saved: Path | str | None) -> None:
    """Put back the record bytes a publish saved, or remove a record that did not exist before it (`saved` None).

    A record that already equals what it should be is left alone.
    """
    file = path(ghost_root)
    wanted = Path(saved).read_bytes() if saved is not None else None
    try:
        current = file.read_bytes()
    except FileNotFoundError:
        current = None
    if current == wanted:
        return
    _require_lock(ghost_root)
    if wanted is None:
        file.unlink()
    else:
        _write(file, wanted)


def latest_target(record: Mapping, *, kind: str, name: str) -> tuple[str, dict] | None:
    """`(install location, entry)` of the target of `kind` named `name` that the latest publish wrote; None
    without one. A location an earlier publish used stays in the record when the location moves, so the
    latest `installed_at` says which one is current."""
    matching = [(key, entry) for key, entry in (record.get("targets") or {}).items()
                if isinstance(entry, dict) and entry.get("kind") == kind and entry.get("name") == name]
    return max(matching, key=lambda item: _installed_at(item[1])) if matching else None


# One-time migration from the records an earlier installer wrote.


def _load_json(file: Path) -> object:
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _posix(value: str) -> str:
    return Path(value).as_posix()


def _sidecars(addons_dir: Path) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """What one host's addon sidecars provide: `{target path: (addon id, kind)}` and `{skill name: addon id}`."""
    by_path: dict[str, tuple[str, str]] = {}
    by_name: dict[str, str] = {}
    if not addons_dir.is_dir():
        return by_path, by_name
    for file in sorted(addons_dir.glob("*.json")):
        sidecar = _load_json(file)
        if not isinstance(sidecar, dict) or not isinstance(sidecar.get("addon_id"), str):
            continue
        for provided in sidecar.get("provided") or []:
            if not isinstance(provided, dict) or not isinstance(provided.get("kind"), str):
                continue
            if isinstance(provided.get("target"), str) and provided["target"]:
                by_path[_posix(provided["target"])] = (sidecar["addon_id"], provided["kind"])
            if provided["kind"] == "skill" and isinstance(provided.get("name"), str):
                by_name[provided["name"]] = sidecar["addon_id"]
    return by_path, by_name


def _snapshot_files(file: Path) -> dict[str, str]:
    """`{posix path: sha256 hex}` from one host's merge-companion snapshot."""
    snapshot = _load_json(file)
    if not isinstance(snapshot, dict):
        return {}
    files: dict[str, str] = {}
    records = snapshot.get("file_records")
    if isinstance(records, dict):
        for key, value in records.items():
            if isinstance(key, str) and isinstance(value, dict) and isinstance(value.get("sha256"), str):
                files[_posix(key)] = value["sha256"]
    listed = snapshot.get("files")
    if isinstance(listed, dict):
        files.update({_posix(key): value for key, value in listed.items()
                      if isinstance(key, str) and isinstance(value, str)})
    return files


def _marker_files(folder: Path) -> dict[str, str] | None:
    """The fingerprints the ownership marker inside a copied folder recorded at install."""
    marker = _load_json(folder / content_digest.MARKER_FILENAME) if folder.is_dir() else None
    if not isinstance(marker, dict) or marker.get("managed_by") != "Ghost-ALICE":
        return None
    hashes = marker.get("content_hashes")
    if not isinstance(hashes, dict) or not hashes:
        return None
    return {rel: content_digest.PREFIX + value for rel, value in hashes.items()
            if isinstance(rel, str) and isinstance(value, str)}


def _snapshot_files_under(snapshot: dict[str, str], folder: str) -> dict[str, str] | None:
    prefix = folder.rstrip("/") + "/"
    files = {key[len(prefix):]: content_digest.PREFIX + value for key, value in snapshot.items()
             if key.startswith(prefix)}
    return files or None


def _migrated_entry(host: str, state: dict, raw: object, sidecar_paths: dict, sidecar_names: dict,
                    snapshot: dict) -> tuple[str, dict] | None:
    if not isinstance(raw, dict):
        return None
    dest, name = raw.get("dest_path"), raw.get("target_name")
    mode = _EARLIER_MODES.get(raw.get("install_mode"))
    if not isinstance(dest, str) or not dest or not isinstance(name, str) or mode is None:
        return None
    key = _posix(dest)
    addon_id = raw.get("addon_id") if raw.get("owner") == "addon" and isinstance(raw.get("addon_id"), str) else None
    if key in sidecar_paths:
        provider_id, kind = sidecar_paths[key]
        addon_id = addon_id or provider_id
    elif name in sidecar_names:
        addon_id, kind = addon_id or sidecar_names[name], "skill"
    else:
        kind = "support" if name == "_shared" else "skill"
    if kind not in KINDS:
        # A hook file sits inside an install location; it is not one.
        return None
    entry = {"name": name, "kind": kind, "mode": mode, "provider": f"addon:{addon_id}" if addon_id else "core",
             "source": str(raw.get("source_path") or ""), "source_commit": str(state.get("source_head") or "unknown"),
             "used_by": [host], "installed_at": raw.get("installed_at") or state.get("installed_at"),
             "baseline": "unknown"}
    if mode == "link":
        try:
            entry["link_target"] = content_digest.link_target(dest)
            entry["baseline"] = "migrated"
        except OSError:
            pass
        return key, entry
    files = _marker_files(Path(dest)) or _snapshot_files_under(snapshot, key)
    if files:
        entry["files"] = files
        entry["baseline"] = "migrated"
    return key, entry


def _merge(existing: dict, new: dict) -> dict:
    """One entry for a path two hosts use: the entry with a baseline wins, and both hosts are kept."""
    keep = new if existing["baseline"] == "unknown" and new["baseline"] != "unknown" else existing
    merged = dict(keep)
    merged["used_by"] = sorted(set(existing["used_by"]) | set(new["used_by"]))
    return merged


def _installed_at(state: dict) -> datetime:
    """When an install-state or a record entry was written: a time without an offset counts as UTC,
    and a value that is not a time counts as the earliest."""
    try:
        moment = datetime.fromisoformat(str(state.get("installed_at")))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _source(states: list[dict]) -> dict | None:
    """The source section the latest earlier install-state names."""
    if not states:
        return None
    latest = max(states, key=_installed_at)
    hooks_path = next((change.get("after") for change in latest.get("system_env_changes") or []
                       if isinstance(change, dict) and change.get("kind") == "source_repo_hook_path"), None)
    return {"root": latest.get("source_root"), "commit": latest.get("source_head"),
            "dirty": latest.get("source_dirty_state"), "hooks_path": hooks_path}


def migrate(ghost_root: Path | str, hosts: Sequence[str], *, installer_commit: str) -> dict | None:
    """Create the record from an earlier installer's records; None when there is a record or nothing to migrate.

    Every host's `install-state/<host>.json` target becomes an entry: a link keeps the
    path it points to now; a copy takes its file fingerprints from its ownership marker,
    else from the host's merge-companion snapshot, else has none (`baseline` unknown).
    Current file contents are never adopted as a baseline. A sidecar that provides the
    target makes its provider `addon:<id>`. Returns `{targets, migrated, unknown}`.
    """
    root = Path(ghost_root)
    if path(root).exists():
        return None
    state_files = [(host, root / "install-state" / f"{host}.json") for host in hosts]
    state_files = [(host, file) for host, file in state_files if file.is_file()]
    if not state_files:
        return None
    entries: dict[str, dict] = {}
    states: list[dict] = []
    for host, file in state_files:
        state = _load_json(file)
        if not isinstance(state, dict) or not isinstance(state.get("targets"), list):
            continue
        states.append(state)
        sidecar_paths, sidecar_names = _sidecars(root / "addons" / host)
        snapshot = _snapshot_files(root / "pending-merges" / host / "snapshot.json")
        for raw in state["targets"]:
            migrated = _migrated_entry(host, state, raw, sidecar_paths, sidecar_names, snapshot)
            if migrated is None:
                continue
            key, entry = migrated
            entries[key] = _merge(entries[key], entry) if key in entries else entry
    patches: dict[str, Mapping] = {"targets": entries,
                                   "installer": {"commit": installer_commit, "schema_version": SCHEMA_VERSION}}
    source = _source(states)
    if source is not None:
        patches["source"] = source
    update(root, patches)
    baselines = [entry["baseline"] for entry in entries.values()]
    return {"targets": len(entries), "migrated": baselines.count("migrated"), "unknown": baselines.count("unknown")}


# One-time migration of the registered gateway from its installation receipt.


def _receipt(binding_path: Path) -> dict:
    """The installed gateway receipt the binding names and committed; RecordError when the binding is not a
    version 1 binding, the receipt's bytes are not the ones the binding fingerprinted, or the receipt is
    unavailable or not installed."""
    binding = _load_json(binding_path)
    if (not isinstance(binding, dict) or set(binding) != {"schema_version", "installation_path", "installation_digest"}
            or binding["schema_version"] != "context-gateway-binding.v1" or not _absolute(binding["installation_path"])
            or not isinstance(binding["installation_digest"], str)):
        raise RecordError(f"{binding_path} is not a version 1 gateway binding that names the installation receipt")
    receipt_path = Path(binding["installation_path"])
    try:
        if content_digest.file_digest(receipt_path) != binding["installation_digest"]:
            raise RecordError(f"the gateway installation receipt changed after its binding: {receipt_path}")
    except OSError as exc:
        raise RecordError(f"the gateway installation receipt cannot be read: {receipt_path} ({exc})") from exc
    receipt = _load_json(receipt_path)
    if not isinstance(receipt, dict):
        raise RecordError(f"the gateway installation receipt cannot be read: {receipt_path}")
    if receipt.get("schema_version") != _RECEIPT_SCHEMA or receipt.get("status") != "installed":
        raise RecordError(f"the gateway installation receipt is not an installed {_RECEIPT_SCHEMA} receipt: {receipt_path}")
    if any(not _absolute(receipt.get(key)) for key in ("launch_agent", "state_path", "config_path", "runtime_shared_dir")):
        raise RecordError(f"the gateway installation receipt lacks absolute service file paths: {receipt_path}")
    if any(not isinstance(receipt.get(key), str) or not _DIGEST.fullmatch(receipt[key])
           for key in ("launch_agent_digest", "state_digest")):
        raise RecordError(f"the gateway installation receipt lacks the service file digests: {receipt_path}")
    if not isinstance(receipt.get("service"), str) or not receipt["service"] or not isinstance(receipt.get("observed_at"), str):
        raise RecordError(f"the gateway installation receipt lacks its service label or time: {receipt_path}")
    return receipt


def _companions(receipt: dict, targets: dict, runtime_shared: Path) -> dict[str, dict]:
    """The runtime companion files the receipt fingerprinted (under the runtime root, outside `_shared`)
    that the record does not name yet, as runtime targets of the hosts the runtime `_shared` serves."""
    runtime_entry = targets.get(_posix(str(runtime_shared)))
    if not isinstance(runtime_entry, dict) or runtime_entry.get("kind") != "runtime":
        return {}
    runtime_root = runtime_shared.parent
    source_root = receipt.get("source_root") if _absolute(receipt.get("source_root")) else None
    found: dict[str, dict] = {}
    for path, value in (receipt.get("files") or {}).items():
        if not _absolute(path) or not isinstance(value, str) or not _DIGEST.fullmatch(value):
            continue
        file = Path(path)
        if runtime_root not in file.parents or file == runtime_shared or runtime_shared in file.parents:
            continue
        key = _posix(str(file))
        if key in targets:
            continue
        relative = file.relative_to(runtime_root).as_posix()
        found[key] = {"name": relative, "kind": "runtime", "mode": "copy", "provider": "core",
                      "source": str(Path(source_root) / relative) if source_root else "", "source_commit": "unknown",
                      "used_by": list(runtime_entry.get("used_by") or []), "files": {".": value},
                      "installed_at": receipt["observed_at"], "baseline": "migrated"}
    return found


def migrate_service(ghost_root: Path | str) -> dict | None:
    """Create the `service` section once from the gateway's installation receipt; None when the record already
    has one or no binding file names a receipt.

    The receipt's service label, three service files, their two digests, config backup and time become the
    section; `config_block_digest` stays None until a registration records it, `restarted_at` None. Every
    runtime companion file the receipt fingerprinted becomes a `migrated` runtime target of the hosts the
    runtime `_shared` serves. The receipt and the binding stay as they are. Returns `{label, companions}`.
    A receipt that is unavailable or not installed raises RecordError.
    """
    root = Path(ghost_root)
    binding_path = root / BINDING_FILENAME
    if not binding_path.is_file():
        return None
    record = read(root) or {}
    if isinstance(record.get("service"), dict):
        return None
    receipt = _receipt(binding_path)
    runtime_shared = Path(receipt["runtime_shared_dir"])
    section = {"label": receipt["service"], "runtime_root": str(runtime_shared.parent),
               "program": str(runtime_shared / "context_gateway.py"),
               "launch_agent": receipt["launch_agent"], "launch_agent_digest": receipt["launch_agent_digest"],
               "state_path": receipt["state_path"], "state_digest": receipt["state_digest"],
               "config_path": receipt["config_path"], "config_block_digest": None,
               "config_backup": receipt["config_backup"] if isinstance(receipt.get("config_backup"), str) else None,
               "registered_at": receipt["observed_at"], "restarted_at": None,
               "listener_ready": receipt.get("listener_ready") is True}
    companions = _companions(receipt, record.get("targets", {}), runtime_shared)
    patches: dict[str, Mapping] = {"service": section}
    if companions:
        patches["targets"] = companions
    update(root, patches)
    return {"label": section["label"], "companions": len(companions)}
