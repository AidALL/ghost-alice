#!/usr/bin/env python3
"""Per-addon sidecar registry for Ghost-ALICE addon install attribution.

Plan reference: the addon "Registry Schema Requirements". This module owns the
per-addon attribution sidecars under ``~/.ghost-alice/addons/<addon_id>.json``.

Design invariants (all enforced here):

- Per-addon files only. Each addon writes ONLY its own ``<addon_id>.json``; the
  platform install-state aggregate is derived by scanning these files
  (rebuilt, never read-modify-write of a shared file -- the lost-update fix for
  separate-run/concurrent addon installs).
- addon_id is a filename, so it is charset-validated (``^[a-z][a-z0-9-]*$``) AND
  realpath-contained under the addons dir as the FIRST op of every read/write/
  delete. Charset + containment are each insufficient alone. A read also asserts
  the in-file ``addon_id`` matches the filename stem (no mis-attribution).
- Writes are atomic and durable: temp-write in the same dir + fsync(file) +
  ``os.replace`` + best-effort fsync(parent dir). A failed write never corrupts
  an existing record and leaves no temp file.
- Reads never follow symlinks and never touch non-regular files: every read
  opens with ``O_NOFOLLOW``/``O_NONBLOCK``, asserts ``S_ISREG``, and caps size
  at ``MAX_SIDECAR_BYTES`` before parsing (blocks symlink-redirect, FIFO/device
  hangs, and JSON-bomb memory amplification).
- schema_version uses strict ``major[.minor]`` semantics (no whitespace, signs,
  underscores, floats, or 3rd component). An UNSUPPORTED MAJOR fails closed on
  read and leaves the record untouched; a SUPPORTED MAJOR with a higher MINOR is
  accepted and its unknown fields are PRESERVED (never stripped). Note: a rewrite
  normalizes formatting (sorted keys, 2-space indent) -- it preserves the JSON
  *value*, not byte-for-byte layout.

Resolved P0 decisions baked in here:
- T0.3: adapter/script integrity is carried by the existing
  ``provided[].content_hash`` field; no separate adapter-hash field.
- schema_version is the string ``"1.0"`` (SUPPORTED_MAJOR = 1).

The ``addons_dir`` argument is keyword-only on every public function (it is the
security-sensitive "environment" arg; keyword-only prevents arg-order mistakes).

Pure standard library; importable without the shell installer.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

ADDON_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SCHEMA_VERSION_RE = re.compile(r"^(0|[1-9][0-9]{0,3})(?:\.(0|[1-9][0-9]{0,3}))?$")

SCHEMA_VERSION = "1.0"
SUPPORTED_MAJOR = 1
MAX_SIDECAR_BYTES = 256 * 1024

# secrets[] is optional (presence marks the addon Tier-2-ineligible); not required.
REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version",
    "addon_id",
    "addon_version",
    "source",
    "platform",
    "owner",
    "origin",
    "depends_on_core",
    "min_core_version",
    "installed_at",
    "provided",
)
_REQUIRED_STR_FIELDS: tuple[str, ...] = (
    "addon_version",
    "source",
    "platform",
    "owner",
    "origin",
    "min_core_version",
    "installed_at",
)
_REQUIRED_LIST_FIELDS: tuple[str, ...] = ("depends_on_core", "provided")
_PROVIDED_REQUIRED_STR: tuple[str, ...] = (
    "kind",
    "name",
    "target",
    "ownership",
    "install_mode",
    "content_hash",
    "marker",
)
_PROVIDED_REQUIRED_NONEMPTY_STR: tuple[str, ...] = (
    "kind",
    "name",
    "target",
    "ownership",
    "install_mode",
)
_PROVIDED_KINDS = frozenset({"skill", "command", "hook", "adapter", "resource"})
_PROVIDED_INSTALL_MODES = frozenset({"symlink", "junction", "copy", "missing"})


class RegistryError(Exception):
    """Base class for registry failures."""


class InvalidAddonId(RegistryError):
    """addon_id does not match the required charset."""


class PathContainmentError(RegistryError):
    """A resolved sidecar path escapes the addons directory."""


class SchemaValidationError(RegistryError):
    """A record is missing/mistyped fields or has an unparseable version."""


class UnsupportedSchemaVersion(RegistryError):
    """A record declares a schema major version this core cannot handle."""


class RecordNotFound(RegistryError):
    """No sidecar exists for the requested addon."""


def default_addons_dir(home: str | os.PathLike[str] | None = None) -> Path:
    base = Path(home) if home is not None else Path.home()
    return base / ".ghost-alice" / "addons"


def validate_addon_id(addon_id: Any) -> str:
    if not isinstance(addon_id, str) or not ADDON_ID_RE.fullmatch(addon_id):
        raise InvalidAddonId(f"addon_id must match {ADDON_ID_RE.pattern}: {addon_id!r}")
    return addon_id


def sidecar_path(addon_id: str, *, addons_dir: str | os.PathLike[str]) -> Path:
    """Resolve the sidecar path, validating the id and enforcing containment.

    addon_id is validated FIRST (the charset forbids ``/`` and ``.``), then the
    resolved path is checked to stay inside the resolved addons dir (defense in
    depth against any future charset relaxation).
    """
    validate_addon_id(addon_id)
    base = Path(addons_dir)
    resolved_base = base.resolve()
    candidate = (resolved_base / f"{addon_id}.json").resolve()
    try:
        candidate.relative_to(resolved_base)
    except ValueError as exc:
        raise PathContainmentError(
            f"sidecar for {addon_id!r} escapes {resolved_base}"
        ) from exc
    return base / f"{addon_id}.json"


def _parse_schema_version(value: Any) -> tuple[int, int]:
    if not isinstance(value, str):
        raise SchemaValidationError(f"schema_version must be a string: {value!r}")
    match = _SCHEMA_VERSION_RE.fullmatch(value)
    if not match:
        raise SchemaValidationError(f"schema_version is not strict major[.minor]: {value!r}")
    return int(match.group(1)), int(match.group(2) or 0)


def _validate_record(record: Any) -> str:
    """Validate a record for WRITING. Returns the addon_id."""
    if not isinstance(record, dict):
        raise SchemaValidationError("record must be a JSON object")
    addon_id = validate_addon_id(record.get("addon_id"))
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    if missing:
        raise SchemaValidationError(f"record for {addon_id!r} missing fields: {missing}")
    major, _minor = _parse_schema_version(record.get("schema_version"))
    # We only ever WRITE the major version we understand. A future-major record
    # handed to us to write is a bug; reject it fail-closed.
    if major != SUPPORTED_MAJOR:
        raise UnsupportedSchemaVersion(
            f"cannot write schema major {major} (supported {SUPPORTED_MAJOR})"
        )
    for field in _REQUIRED_STR_FIELDS:
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SchemaValidationError(f"{addon_id!r}: {field} must be a non-empty string")
    for field in _REQUIRED_LIST_FIELDS:
        if not isinstance(record.get(field), list):
            raise SchemaValidationError(f"{addon_id!r}: {field} must be a list")
    for index, entry in enumerate(record["provided"]):
        if not isinstance(entry, dict):
            raise SchemaValidationError(f"{addon_id!r}: provided[{index}] must be an object")
        for key in _PROVIDED_REQUIRED_STR:
            if key not in entry:
                raise SchemaValidationError(
                    f"{addon_id!r}: provided[{index}].{key} is required"
                )
            value = entry[key]
            if not isinstance(value, str):
                raise SchemaValidationError(
                    f"{addon_id!r}: provided[{index}].{key} must be a string"
                )
            if key in _PROVIDED_REQUIRED_NONEMPTY_STR and not value.strip():
                raise SchemaValidationError(
                    f"{addon_id!r}: provided[{index}].{key} must be a non-empty string"
                )
        if entry["kind"] not in _PROVIDED_KINDS:
            raise SchemaValidationError(
                f"{addon_id!r}: provided[{index}].kind must be one of {sorted(_PROVIDED_KINDS)}"
            )
        if entry["install_mode"] not in _PROVIDED_INSTALL_MODES:
            raise SchemaValidationError(
                f"{addon_id!r}: provided[{index}].install_mode must be one of {sorted(_PROVIDED_INSTALL_MODES)}"
            )
        if "metadata" not in entry:
            raise SchemaValidationError(f"{addon_id!r}: provided[{index}].metadata is required")
        if not isinstance(entry["metadata"], dict):
            raise SchemaValidationError(f"{addon_id!r}: provided[{index}].metadata must be an object")
    if "secrets" in record and not isinstance(record["secrets"], list):
        raise SchemaValidationError(f"{addon_id!r}: secrets must be a list when present")
    return addon_id


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.stem}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    # Best-effort durability of the rename itself.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def write_record(record: dict[str, Any], *, addons_dir: str | os.PathLike[str]) -> Path:
    """Validate and atomically persist one addon's sidecar.

    Validation (id charset, required fields, field types, supported major)
    happens BEFORE any filesystem operation, so a rejected record never creates
    a file and never corrupts an existing one.
    """
    addon_id = _validate_record(record)
    path = sidecar_path(addon_id, addons_dir=addons_dir)
    serialized = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(serialized.encode("utf-8")) > MAX_SIDECAR_BYTES:
        raise SchemaValidationError(
            f"{addon_id!r}: serialized record exceeds {MAX_SIDECAR_BYTES} bytes"
        )
    _atomic_write_json(path, record)
    return path


def _read_json_object(path: Path, *, what: str) -> dict[str, Any]:
    """Read a JSON object without following symlinks or hanging on FIFOs."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise RecordNotFound(f"{what}: not found") from exc
    except OSError as exc:  # ELOOP for a symlink, ENXIO, etc.
        raise SchemaValidationError(f"{what}: cannot open ({exc})") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SchemaValidationError(f"{what}: not a regular file")
        if st.st_size > MAX_SIDECAR_BYTES:
            raise SchemaValidationError(f"{what}: exceeds {MAX_SIDECAR_BYTES} bytes")
        handle = os.fdopen(fd, "rb")  # consumes fd; the with-block below owns closing it
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    with handle:
        raw = handle.read(MAX_SIDECAR_BYTES + 1)
    if len(raw) > MAX_SIDECAR_BYTES:
        raise SchemaValidationError(f"{what}: exceeds {MAX_SIDECAR_BYTES} bytes")
    try:
        record = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SchemaValidationError(f"{what}: not valid JSON ({exc})") from exc
    if not isinstance(record, dict):
        raise SchemaValidationError(f"{what}: must be a JSON object")
    return record


def _check_read_compat(record: dict[str, Any]) -> None:
    """Fail closed unless the record's major equals the supported major.

    Rejecting ``major != SUPPORTED_MAJOR`` (not just ``>``) closes the
    zero/negative-major bypass. This never mutates the file.
    """
    major, _minor = _parse_schema_version(record.get("schema_version"))
    if major != SUPPORTED_MAJOR:
        raise UnsupportedSchemaVersion(
            f"record schema major {major} != supported {SUPPORTED_MAJOR}"
        )


def read_record(addon_id: str, *, addons_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Strict single read: validates version, required fields, and identity."""
    path = sidecar_path(addon_id, addons_dir=addons_dir)
    record = _read_json_object(path, what=f"sidecar {addon_id!r}")
    _check_read_compat(record)
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    if missing:
        raise SchemaValidationError(f"sidecar {addon_id!r} missing fields: {missing}")
    if record.get("addon_id") != addon_id:
        raise SchemaValidationError(
            f"sidecar {addon_id!r} declares mismatched addon_id {record.get('addon_id')!r}"
        )
    return record


def scan_records(
    *, addons_dir: str | os.PathLike[str]
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Cumulative scan returning (records, skipped).

    ``skipped`` is a list of ``(filename, reason)`` so a caller (e.g. the
    install-state rebuild) can detect that a known sidecar exists on disk but was
    dropped (tampered version, malformed JSON, non-regular file, id mismatch),
    rather than silently treating a drop as absence. Records are returned sorted
    ascending by ``addon_id`` (determinism only -- not install/dependency order).
    """
    base = Path(addons_dir)
    records: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    if not base.is_dir():
        return records, skipped
    for path in sorted(base.glob("*.json")):
        stem = path.stem
        if not ADDON_ID_RE.fullmatch(stem):
            continue  # not an addon sidecar (e.g. _migration-report.json)
        try:
            record = _read_json_object(path, what=path.name)
            _check_read_compat(record)
            missing = [field for field in REQUIRED_FIELDS if field not in record]
            if missing:
                raise SchemaValidationError(f"missing fields: {missing}")
            if record.get("addon_id") != stem:
                raise SchemaValidationError(
                    f"in-file addon_id {record.get('addon_id')!r} != filename {stem!r}"
                )
        except RegistryError as exc:
            skipped.append((path.name, str(exc)))
            continue
        records.append(record)
    return records, skipped


def read_all(*, addons_dir: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Cumulative list of valid sidecar records (skips bad/foreign files).

    Use ``scan_records`` if you need to know what was skipped, or ``read_record``
    for a strict single read.
    """
    records, _skipped = scan_records(addons_dir=addons_dir)
    return records


def read_all_ids(*, addons_dir: str | os.PathLike[str]) -> list[str]:
    """addon_ids of every VALID record (parsed, version+identity checked)."""
    return [r["addon_id"] for r in read_all(addons_dir=addons_dir)]


def iter_addon_ids_on_disk(*, addons_dir: str | os.PathLike[str]) -> list[str]:
    """addon_ids inferred from sidecar FILENAMES only (no content parsed).

    Contrast with ``read_all_ids`` (in-file ids of parseable records): this lists
    filename stems regardless of whether the content is valid, which is what a
    GC/reconciliation pass needs to find orphaned or corrupt sidecars.
    """
    base = Path(addons_dir)
    if not base.is_dir():
        return []
    return [path.stem for path in sorted(base.glob("*.json")) if ADDON_ID_RE.fullmatch(path.stem)]


def remove_record(addon_id: str, *, addons_dir: str | os.PathLike[str]) -> bool:
    """Delete a sidecar; returns True if it existed, False otherwise."""
    path = sidecar_path(addon_id, addons_dir=addons_dir)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
