"""The install record: what the installer installed where, for every host, in one file.

The record root holds one detailed record. Version 2 stores source paths and
version 3 stores host attribution when a path alone cannot identify the relationship.
Older filenames hold a notice identifying the detailed record.

- `installer`: `{commit, schema_version}` of the last writer and optional
  `active_root` selecting the package the installed management command executes.
- `source`: `{root, commit, dirty, remote, ref}` of the development input,
  with optional `ledger_source_roots` mapping installed ledger paths to their source and observed hosts.
  A path alone carries no host attribution after a change of source.
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

`update` writes record sections, `release_target` removes a confirmed host reference,
and `restore` puts back a copy a publish saved; each writes through a temporary file
and a rename, and refuses unless
this process or the process that started it holds the install lock. `migrate`
builds the record once from the records an earlier installer wrote and
`migrate_service` the `service` section once from the gateway's installation
receipt; neither changes those records.

Dependencies: Python standard library, `content_digest`, `install_lock`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext
import json
import os
from pathlib import Path, PurePath
import re
from typing import Mapping, Sequence
import uuid

import content_digest
import install_lock

SCHEMA_VERSION = 2
FILENAME = "installed-payload-v2.json"
LEGACY_FILENAME = "installed-payload.json"
ATTRIBUTED_SCHEMA_VERSION = 3
ATTRIBUTED_FILENAME = "installed-payload-v3.json"
RECORD_FILENAMES = (ATTRIBUTED_FILENAME, FILENAME, LEGACY_FILENAME)
PENDING_SOURCE_FILENAME = "pending-install-source.json"
INSTALL_RECORD_FILES = (*RECORD_FILENAMES, PENDING_SOURCE_FILENAME)
LOCK_FILENAME = "install.lock"
SECTIONS = ("installer", "source", "last_transaction", "targets", "service")
KINDS = ("skill", "support", "runtime", "command", "resource")
MODES = ("link", "copy")
BASELINES = ("recorded", "migrated", "unknown")
SERVICE_KEYS = ("label", "runtime_root", "program", "launch_agent", "launch_agent_digest", "state_path",
                "state_digest", "config_path", "config_block_digest", "config_backup", "registered_at",
                "restarted_at", "listener_ready")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# The gateway's own installation receipt and binding, read once by `migrate_service`.
BINDING_FILENAME = "context-gateway-binding.json"
_RECEIPT_SCHEMA = "context-gateway-installation.v1"

# How an earlier installer named the install mode of a target.
_EARLIER_MODES = {"symlink": "link", "junction": "link", "copy": "copy", "copy-fallback": "copy"}


class RecordError(ValueError):
    """The install record or a supplied section cannot be read by this installer."""


class NotLockHolder(RuntimeError):
    """Neither this process nor the process that started it holds the install lock."""


class InventoryError(ValueError):
    """One host's earlier inventory could not supply its full target list."""

    def __init__(self, host: str, file: Path, cause: Exception):
        self.host, self.file = host, file
        super().__init__(f"{file}: {cause}")


def path(ghost_root: Path | str) -> Path:
    """The current detailed install record under `ghost_root`."""
    attributed = Path(ghost_root) / ATTRIBUTED_FILENAME
    return attributed if os.path.lexists(attributed) else Path(ghost_root) / FILENAME


def read_path(ghost_root: Path | str) -> Path:
    """The current detail when present, otherwise the legacy record; diagnostics name this input."""
    file = path(ghost_root)
    return file if file.exists() else Path(ghost_root) / LEGACY_FILENAME


def read(ghost_root: Path | str) -> dict | None:
    """Read the detailed record under ghost_root; a missing record returns None."""
    return read_file(read_path(ghost_root))


def read_file(file: Path | None) -> dict | None:
    """Read a selected detail, including an absent pre-publication record during recovery."""
    if file is None:
        return None
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
    supported = (ATTRIBUTED_SCHEMA_VERSION,) if file.name == ATTRIBUTED_FILENAME else (1, SCHEMA_VERSION)
    if record.get("record_file") in (ATTRIBUTED_FILENAME, FILENAME):
        raise RecordError(f"{file.parent / record['record_file']} is missing; restore the detailed install record")
    if record.get("schema_version") not in supported:
        raise RecordError(f"{file} has schema_version {record.get('schema_version')!r}; this installer reads "
                          f"{supported} in this file")
    if not isinstance(record.get("targets", {}), dict):
        raise RecordError(f"{file} has targets that are not an object")
    if "source" in record:
        _check_source(record["source"])
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


def source_paths(relations: dict) -> dict:
    """The path-only relationships readable by a v2 install-record reader."""
    return {path: fact['source_root'] if isinstance(fact, dict) else fact for path, fact in relations.items()}


def pending_source(root: Path) -> dict | None:
    """The source facts that an earlier installer's interrupted activation left in its own file."""
    file = root / PENDING_SOURCE_FILENAME
    if file.is_symlink():
        raise RecordError(f'Pending installation source is a symbolic link: {file}')
    try:
        pending = json.loads(file.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RecordError(f'Cannot read pending installation source {file}: {exc}') from exc
    if (not isinstance(pending, dict) or not _absolute(pending.get('active_root'))
            or not isinstance(pending.get('source'), dict) or not _absolute(pending['source'].get('root'))
            or not isinstance(pending['source'].get('ledger_source_roots'), dict)):
        raise RecordError(f'Invalid pending installation source: {file}')
    _check_source(pending['source'])
    return pending


def matches_pending(record: dict, pending: dict) -> bool:
    """Whether the record still holds the activation that left `pending`."""
    source = record.get('source', {})
    saved = pending['source']
    return (record.get('installer', {}).get('active_root') == pending['active_root']
            and source.get('root') == saved['root']
            and source.get('ledger_source_roots', {}) == source_paths(saved['ledger_source_roots']))


def _save(ghost_root: Path | str, record: dict, *, journal=None) -> None:
    root = Path(ghost_root)
    relations = record.get("source", {}).get("ledger_source_roots", {})
    attributed = any(isinstance(value, dict) for value in relations.values())
    version = ATTRIBUTED_SCHEMA_VERSION if attributed else SCHEMA_VERSION
    name = ATTRIBUTED_FILENAME if attributed else FILENAME
    record["schema_version"] = version
    notice = _encode({"schema_version": version, "record_file": name})
    guards = [root / LEGACY_FILENAME]
    if attributed:
        guards.append(root / FILENAME)
    guards = [file for file in guards if not file.exists() or file.read_bytes() != notice]
    obsolete = root / ATTRIBUTED_FILENAME
    demoting = not attributed and obsolete.exists()
    operation = nullcontext()
    if (guards or demoting) and journal is None:
        from install_transaction import record_update
        operation = record_update(root)
    with operation:
        _write(root / name, _encode(record))
        for file in guards:
            _write(file, notice)
        if demoting:
            obsolete.unlink()


def target_hosts(key: str, entry: Mapping) -> list[str]:
    used_by = entry.get("used_by")
    if not isinstance(used_by, list) or not all(isinstance(host, str) for host in used_by):
        raise RecordError(f"target {key} needs used_by as a list of hosts")
    return used_by


def _check_entry(key: str, entry: object) -> dict:
    if not isinstance(entry, Mapping):
        raise RecordError(f"target {key} is not an object")
    if entry.get("mode") not in MODES or entry.get("kind") not in KINDS or entry.get("baseline") not in BASELINES:
        raise RecordError(f"target {key} needs mode {MODES}, kind {KINDS} and baseline {BASELINES}")
    target_hosts(key, entry)
    return dict(entry)


def _absolute(value: object) -> bool:
    return isinstance(value, str) and Path(value).is_absolute()


def _check_source(section: object) -> dict:
    if not isinstance(section, Mapping):
        raise RecordError("section source is not an object")
    if "ledger_source_roots" in section:
        roots = section["ledger_source_roots"]
        if not isinstance(roots, dict):
            raise RecordError("source ledger_source_roots must be an object")
        for root, relation in roots.items():
            source = relation
            if isinstance(relation, dict):
                hosts = relation.get('hosts')
                if (set(relation) != {'source_root', 'hosts'} or not isinstance(hosts, list)
                        or any(not isinstance(host, str) or not host for host in hosts)):
                    raise RecordError("source ledger_source_roots relationship needs source_root and a list of hosts")
                source = relation['source_root']
            if any(not _absolute(value) or os.path.normpath(value) != value for value in (root, source)):
                raise RecordError("source ledger_source_roots must name normalized absolute ledger and source paths")
    return dict(section)


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


def update(ghost_root: Path | str, patches: Mapping[str, Mapping | None], *, journal=None) -> dict:
    """Write the given sections in one replacement of the record and return the record.

    `targets` changes only the entries it names, and an entry keeps every host its
    path already had in `used_by`; `installer` and `source` change only named fields.
    `last_transaction` and `service` replace their section; `service` None deletes it.
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
        if section != "targets":
            if section == "source":
                patch = _check_source(patch)
            if section in ("installer", "source"):
                record.setdefault(section, {}).update(patch)
            else:
                record[section] = dict(patch)
            continue
        for key, entry in patch.items():
            if isinstance(key, str) and key not in targets:
                alternate = target_key(key, targets)
                if alternate in targets:
                    key = alternate
            merged = _check_entry(key, entry)
            previous = targets.get(key)
            if isinstance(previous, Mapping) and isinstance(previous.get("used_by"), list):
                merged["used_by"] = sorted(set(previous["used_by"]) | set(merged["used_by"]))
            targets[key] = merged
    _save(ghost_root, record, journal=journal)
    return record


def release_target(ghost_root: Path | str, *, destination: Path | str,
                   provider: str, platform: str) -> dict | None:
    """Release one confirmed host reference at an exact destination and provider.

    The caller confirms removal or shared-reference release before calling. Other
    hosts keep the target entry; the last host removes it. Missing references are
    unchanged, and this operation never changes service or recovery records.
    """
    _require_lock(ghost_root)
    if not _absolute(str(destination)) or not isinstance(provider, str) or not provider or not isinstance(platform, str) or not platform:
        raise RecordError("release_target needs an absolute destination, provider and platform")
    record = read(ghost_root)
    targets = (record or {}).get("targets", {})
    key = target_key(destination, targets)
    if key not in targets:
        return record
    entry = _check_entry(key, targets[key])
    if not isinstance(entry.get("provider"), str) or not entry["provider"]:
        raise RecordError(f"target {key} needs a provider")
    if entry["provider"] != provider or platform not in entry["used_by"]:
        return record
    remaining = [host for host in entry["used_by"] if host != platform]
    if remaining:
        entry["used_by"] = remaining
        targets[key] = entry
    else:
        del targets[key]
    _save(ghost_root, record)
    return record


def restore(ghost_root: Path | str, saved: Path | str | None, *,
            legacy_saved: Path | str | None = None, attributed_saved: Path | str | None = None) -> None:
    """Put back the record bytes a publish saved, or remove a record that did not exist before it (`saved` None).

    A record that already equals what it should be is left alone.
    """
    failures = []
    for name, original in ((FILENAME, saved), (LEGACY_FILENAME, legacy_saved), (ATTRIBUTED_FILENAME, attributed_saved)):
        file = Path(ghost_root) / name
        try:
            wanted = Path(original).read_bytes() if original is not None else None
            try:
                current = file.read_bytes()
            except FileNotFoundError:
                current = None
            if current == wanted:
                continue
            _require_lock(ghost_root)
            if wanted is None:
                file.unlink()
            else:
                _write(file, wanted)
        except OSError as exc:
            failures.append(f"{file}: {exc}")
    if failures:
        raise RecordError("; ".join(failures))


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


def target_key(destination: PurePath | str, targets: Mapping | None = None) -> str:
    """Use the published POSIX spelling, retaining an existing native Windows key when reading old records."""
    path = destination if isinstance(destination, PurePath) else Path(destination)
    key = path.as_posix()
    if targets is not None and key not in targets and str(path) in targets:
        return str(path)
    return key


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
                by_path[target_key(provided["target"])] = (sidecar["addon_id"], provided["kind"])
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
                files[Path(key).as_posix()] = value["sha256"]
    listed = snapshot.get("files")
    if isinstance(listed, dict):
        files.update({Path(key).as_posix(): value for key, value in listed.items()
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
    dest = inventory_destination(dest)
    key = target_key(dest)
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


def _source(root: Path, states: list[dict]) -> dict | None:
    """The source section the latest earlier install-state names. None when it names a saved package under `root`,
    which is no development source."""
    if not states:
        return None
    latest = max(states, key=_installed_at)
    origin = latest.get("source_root")
    if _absolute(origin) and Path(origin).resolve().is_relative_to((root / "versions").resolve()):
        return None
    return {"root": origin, "commit": latest.get("source_head"), "dirty": latest.get("source_dirty_state")}


def awaits_migration(record: Mapping | None) -> bool:
    """Whether no installer has written `record` yet: there is none, it has an earlier form, or only a service
    registration wrote it. Such a record has consumed no host inventory."""
    return (record is None or record['schema_version'] not in (SCHEMA_VERSION, ATTRIBUTED_SCHEMA_VERSION)
            or 'installer' not in record)


def inventory_destination(value: Path | str) -> Path:
    """Expand a stored home path. For a parent traversal, follow directory links but keep the destination slot."""
    path = Path(value).expanduser()
    if '..' in path.parts:
        return path.parent.resolve() / path.name if path.name != '..' else path.resolve()
    return path


def read_inventory(path: Path | str, *, platform: str | None = None) -> dict | None:
    """Read the current inventory without adopting live files as a new baseline."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if os.path.lexists(path):
            raise
        return None
    except ValueError as exc:
        raise ValueError(f"invalid install-state JSON: {path}: {exc}") from exc
    if isinstance(data, dict) and "targets" not in data and data.get("partial_failure") is True:
        # The earlier postflight writer recorded observations without an inventory.
        data = dict(data, targets=[])
    if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
        raise ValueError(f"invalid install-state targets: {path}")
    if platform is not None and data.get("platform", platform) != platform:
        raise ValueError(f"install-state platform mismatch: {path}")
    for target in data["targets"]:
        if (not isinstance(target, dict) or not isinstance(target.get("dest_path"), str)
                or not target["dest_path"] or not isinstance(target.get("target_name"), str)
                or not target["target_name"]):
            raise ValueError(f"invalid install-state target: {path}")
    return data


def earlier_inventories(root: Path, hosts: Sequence[str]) -> tuple[dict[str, dict], list[InventoryError]]:
    """Read each complete host inventory and identify every file that could not be read."""
    inventories, errors = {}, []
    for host in hosts:
        file = root / 'install-state' / f'{host}.json'
        try:
            state = read_inventory(file)
        except (OSError, ValueError) as exc:
            errors.append(InventoryError(host, file, exc))
            continue
        if state is not None:
            if state.get('platform', host) != host:
                raise InventoryError(host, file, ValueError('install-state platform mismatch'))
            inventories[host] = state
    return inventories, errors


def migrate(ghost_root: Path | str, hosts: Sequence[str], *, installer_commit: str,
            runtime_targets: dict[str, list[dict]] | None = None, inventories: dict[str, dict] | None = None) -> dict | None:
    """Carry earlier inventories and v1 records into the record; None for a record that an installer wrote
    (`awaits_migration`) or no readable earlier record.

    Every host's `install-state/<host>.json` target becomes an entry: a link keeps the
    path it points to now; a copy takes its file fingerprints from its ownership marker,
    else from the host's merge-companion snapshot, else has none (`baseline` unknown).
    Current file contents are never adopted as a baseline. A sidecar that provides the
    target makes its provider `addon:<id>`. Returns `{targets, migrated, unknown}`.
    """
    root = Path(ghost_root)
    existing = read(root)
    if not awaits_migration(existing):
        return None
    if inventories is None:
        inventories, errors = earlier_inventories(root, hosts)
        if not inventories and not errors:
            return None
    elif not inventories:
        return None
    entries: dict[str, dict] = dict((existing or {}).get('targets', {}))
    states: list[dict] = []
    for host, state in inventories.items():
        # A failed installation without targets identifies nothing that is installed.
        if state.get("partial_failure") is True and not state["targets"]:
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
        for raw in (runtime_targets or {}).get(host, []):
            if not os.path.lexists(raw['dest_path']):
                continue
            original = raw.get('source_path') or str(Path(state.get('source_root') or '') / raw['target_name'])
            key, entry = _migrated_entry(host, state, dict(raw, source_path=original), {}, {}, snapshot)
            entry.update(kind='runtime', provider='core')
            entries[key] = _merge(entries[key], entry) if key in entries else entry
    if not states and not entries:
        # An empty record would stop the migration of an inventory that becomes readable later.
        return None
    patches: dict[str, Mapping] = {"targets": entries,
                                   "installer": {"commit": installer_commit, "schema_version": SCHEMA_VERSION}}
    source = _source(root, states)
    if source is not None and not (existing or {}).get('source'):
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
    runtime_entry = targets.get(target_key(runtime_shared))
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
        key = target_key(file)
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
