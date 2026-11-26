#!/usr/bin/env python3
"""Transactional installer filesystem operations.

`publish(request, participants)` places the targets one request names and writes
their install record entries in the same step. The original each target replaces
moves into `~/.ghost-alice/rollbacks/<txid>/`, whose `journal.json` says what the
publish replaces and how far it got (`prepare`, then `done` or `restored`). The
participants are called before and after the publish and, when it fails, before
and after the restore; this module knows only their interface
(`installer.participant`). An ordinary failure attempts file, record and participant
restoration with the cause's exit code. `recover --txid` restores an uncommitted publish;
after a ledger migration reaches `committed`, it finishes service resumption without
replacing data. `unfinished` lists both kinds until recovery finishes.
"""

from __future__ import annotations

import argparse
import contextlib
from collections import Counter
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import content_digest
import install_lock
import installed_payload
import installer_assets
from installer_assets import is_link_or_reparse_point, path_entry_kind, remove_path as _remove_path, write_ownership_marker
import pending_manifest
import state_retention
from installer.participant import PublishContext

EXCLUDED_COPY_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".git",
}
EXCLUDED_COPY_FILE_NAMES = {".DS_Store"}
EXCLUDED_COPY_FILE_SUFFIXES = {".pyc"}
REQUEST_SCHEMA_VERSION = 1
JOURNAL_SCHEMA_VERSION = 1
ROLLBACKS_DIRNAME = "rollbacks"
JOURNAL_FILENAME = "journal.json"
FINISHED_PHASES = ("done", "restored")
TARGET_STATUSES = ("current", "updated", "new")
_BSD_FILE_LOCK_FLAGS = (stat.UF_IMMUTABLE | stat.SF_IMMUTABLE | stat.UF_APPEND | stat.SF_APPEND
                        | stat.UF_NOUNLINK | stat.SF_NOUNLINK)


class InstallTransactionError(RuntimeError):
    def __init__(self, message: str, *, journal_reason: str | None = None):
        super().__init__(message)
        self.journal_reason = journal_reason


def _unique_path(root: Path, prefix: str, name: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return root / f"{prefix}-{name}-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _is_excluded_copy_file(path: Path) -> bool:
    return path.name in EXCLUDED_COPY_FILE_NAMES or path.suffix in EXCLUDED_COPY_FILE_SUFFIXES


def _copy_to_stage(source: Path, stage: Path) -> None:
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(
            source,
            stage,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                *EXCLUDED_COPY_DIR_NAMES,
                *EXCLUDED_COPY_FILE_NAMES,
                *[f"*{suffix}" for suffix in EXCLUDED_COPY_FILE_SUFFIXES],
            ),
        )
        return
    if source.exists() or source.is_symlink():
        stage.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            os.symlink(os.readlink(source), stage)
        else:
            shutil.copy2(source, stage)
        return
    raise InstallTransactionError(f"source does not exist: {source}")


def _rename_path(source: Path, dest: Path) -> None:
    attempts = 5 if os.name == "nt" else 1
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            source.rename(dest)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _create_directory_link(source: Path, link: Path) -> str:
    try:
        os.symlink(source, link, target_is_directory=True)
        return "symlink"
    except OSError as exc:
        if os.name != "nt" or not source.is_dir() or getattr(exc, "winerror", None) != 1314:
            raise
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise exc
        return "junction"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_fingerprint(path: Path, *, include_generated: bool = False) -> str:
    if path.is_symlink():
        return hashlib.sha256(f"link\0{os.readlink(path)}".encode("utf-8")).hexdigest()
    if not path.exists():
        return "missing"
    if path.is_file():
        return hashlib.sha256(f"file\0{_file_hash(path)}".encode("utf-8")).hexdigest()
    if not path.is_dir():
        return hashlib.sha256(f"special\0{path.stat().st_mode}".encode("utf-8")).hexdigest()

    digest = hashlib.sha256()
    for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        rel_parts = child.relative_to(path).parts
        if not include_generated and any(part in EXCLUDED_COPY_DIR_NAMES for part in rel_parts):
            continue
        if not include_generated and child.is_file() and _is_excluded_copy_file(child):
            continue
        rel = child.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        if child.is_symlink():
            digest.update(b"link\0")
            digest.update(os.readlink(child).encode("utf-8"))
        elif child.is_file():
            digest.update(b"file\0")
            digest.update(_file_hash(child).encode("utf-8"))
        elif child.is_dir():
            digest.update(b"dir\0")
        else:
            digest.update(b"special\0")
            digest.update(str(child.stat().st_mode).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _verify_staged_copy(source: Path, stage: Path) -> None:
    source_fingerprint = _tree_fingerprint(source)
    stage_fingerprint = _tree_fingerprint(stage)
    if source_fingerprint != stage_fingerprint:
        raise InstallTransactionError(
            f"staged copy verification failed for {stage}: "
            f"source fingerprint {source_fingerprint} != stage fingerprint {stage_fingerprint}"
        )


def _write_failure_event(
    event_log: Path | None,
    *,
    source: Path,
    dest: Path,
    phase: str,
    error: str,
) -> None:
    if event_log is None:
        return

    event = {
        "schema_version": 1,
        "event": "copy_replace_failure",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "phase": phase,
        "source_path": source.as_posix(),
        "dest_path": dest.as_posix(),
        "error": error,
    }
    try:
        state_retention.append("install-state-events", event_log,
                               json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except ValueError:
        return


def _write_target_progress_event(
    event_file: Path | None,
    *,
    platform: str,
    target_id: str,
    target_kind: str,
    status: str,
) -> None:
    if event_file is None:
        return

    event = {
        "type": "target-result",
        "platform": platform,
        "target_id": target_id,
        "target_kind": target_kind,
        "status": status,
    }
    try:
        event_file.parent.mkdir(parents=True, exist_ok=True)
        with event_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return


class ReplacementBatch:
    """Reversible filesystem participant; its caller owns service recovery and names the rollback folder the
    replaced originals move into."""
    def __init__(self, targets, *, rollback_root,
                 progress_callback=None, progress_event_file=None, progress_events=None):
        self.targets = [(Path(src), Path(dst), mode) for src, dst, mode in targets]
        self.root = Path(rollback_root)
        self.callback, self.event_file, self.events = progress_callback, progress_event_file, progress_events
        if self.events is not None and len(self.events) != len(self.targets):
            raise InstallTransactionError('progress event count must match target count')
        self.prepared, self.phase = [], 'prepare'
        self.source, self.dest = self.targets[0][:2] if self.targets else (None, None)

    @staticmethod
    def identity(path, *, mode='copy'):
        try:
            info = path.lstat()
            return info.st_dev, info.st_ino, info.st_mode, _tree_fingerprint(path, include_generated=mode == 'data')
        except FileNotFoundError:
            return None

    def stage(self):
        for src, dst, mode in self.targets:
            self.source, self.dest = src, dst
            stage_root = dst.parent / '.ghost-alice-install-staging'
            stage = _unique_path(stage_root, 'stage', dst.name)
            item = dict(source=src, dest=dst, stage=stage, mode=mode,
                rollback=_unique_path(self.root, 'rollback', dst.name),
                before=self.identity(dst, mode=mode), moved=False, attempted=False, after=None)
            self.prepared.append(item)
            stage_root.mkdir(parents=True, exist_ok=True)
            try:
                if mode == 'symlink':
                    self.phase = 'stage-symlink'
                    _create_directory_link(src, stage)
                elif mode == 'data':
                    shutil.copytree(src, stage, symlinks=True)
                    if self.identity(src, mode=mode)[3] != self.identity(stage, mode=mode)[3]:
                        raise InstallTransactionError('Staged data differs from its snapshot')
                else:
                    self.phase = 'stage-copy'
                    _copy_to_stage(src, stage)
                    self.phase = 'stage-verify'
                    _verify_staged_copy(src, stage)
            finally:
                item['candidate'] = self.identity(stage, mode=mode)

    def publish(self):
        self.phase = 'publish'
        for item in self.prepared:
            if (self.identity(item['dest'], mode=item['mode']) != item['before']
                    or self.identity(item['stage'], mode=item['mode']) != item['candidate']):
                raise InstallTransactionError('Concurrent target/stage change prevents install publish')
        for index, item in enumerate(self.prepared, 1):
            self.source, self.dest = item['source'], item['dest']
            self.dest.parent.mkdir(parents=True, exist_ok=True)
            item['attempted'] = True
            try:
                if item['before'] is not None:
                    item['rollback'].parent.mkdir(parents=True, exist_ok=True)
                    _rename_path(self.dest, item['rollback'])
                    item['moved'] = True
                _rename_path(item['stage'], self.dest)
            finally:
                current = self.identity(self.dest, mode=item['mode'])
                item['moved'] = self.identity(item['rollback'], mode=item['mode']) == item['before'] if item['before'] is not None else False
                # Only a moved stage or a missing partially published target
                # is our output. Never adopt a concurrent replacement.
                item['after'] = item['candidate'] if current is not None else None
            if self.events is not None:
                event = self.events[index - 1]
                _write_target_progress_event(Path(self.event_file) if self.event_file else None,
                    platform=event['platform'], target_id=event['target_id'], target_kind=event['target_kind'], status=event['status'])
            if self.callback:
                self.callback(index, len(self.prepared))

    def restore(self):
        self.phase = 'rollback'
        # Validate the complete set before deleting any published candidate.
        for item in self.prepared:
            current = self.identity(item['dest'], mode=item['mode'])
            if current == item['before']:
                continue
            if current != item['after'] or (item['moved'] and self.identity(item['rollback'], mode=item['mode']) != item['before']):
                raise InstallTransactionError('Concurrent target/recovery change prevents install rollback')
        for item in reversed(self.prepared):
            dst = item['dest']
            if self.identity(dst, mode=item['mode']) == item['before']:
                continue
            if self.identity(dst, mode=item['mode']) is not None:
                _remove_path(dst)
            if item['moved']:
                _rename_path(item['rollback'], dst)
            if self.identity(dst, mode=item['mode']) != item['before']:
                raise InstallTransactionError('Restored target does not match its original')

    def cleanup(self):
        for item in self.prepared:
            stage = item['stage']
            if stage.exists() or stage.is_symlink():
                try:
                    if self.identity(stage, mode=item['mode']) == item.get('candidate'):
                        _remove_path(stage)
                except OSError:
                    pass
        # The staging folder this batch created beside each target goes once it is empty; an entry another
        # install still stages there keeps it. Any other failure to remove it is recorded.
        for stage_root in dict.fromkeys(item['stage'].parent for item in self.prepared):
            try:
                stage_root.rmdir()
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    state_retention.record_failure("install-staging", stage_root, exc)


def _wrapped_failure(exc, batch=None, detail=None):
    """An InstallTransactionError that keeps the cause's code, exit code, outcome and phase."""
    if detail is not None:
        wrapped = InstallTransactionError(detail)
    elif isinstance(exc, InstallTransactionError):
        wrapped = exc
    else:
        wrapped = InstallTransactionError('staged replacement failed for ' + str(batch.dest) + ': ' + str(exc))
    for name in ('code', 'exit_code', 'outcome'):
        if hasattr(exc, name):
            setattr(wrapped, name, getattr(exc, name))
    wrapped.phase = getattr(exc, 'phase', batch.phase if batch is not None else 'rollback')
    return wrapped


def rollbacks_root(ghost_root) -> Path:
    """`<ghost_root>/rollbacks`: one folder per publish, named by its transaction id."""
    return Path(ghost_root) / ROLLBACKS_DIRNAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _copy_metadata_stat(source: Path, destination: Path) -> None:
    """Preserve file attributes without transferring a deletion or write lock."""
    attributes = source.stat()
    flags = getattr(attributes, 'st_flags', 0)
    if not flags & _BSD_FILE_LOCK_FLAGS and (os.name != 'nt' or attributes.st_mode & stat.S_IWRITE):
        shutil.copystat(source, destination)
        return
    mode = stat.S_IMODE(attributes.st_mode)
    destination.chmod(mode | stat.S_IWRITE if os.name == 'nt' else mode)
    os.utime(destination, ns=(attributes.st_atime_ns, attributes.st_mtime_ns))
    if flags & ~_BSD_FILE_LOCK_FLAGS:
        os.chflags(destination, flags & ~_BSD_FILE_LOCK_FLAGS)


def _unlock_old_metadata_copy(snapshot: Path) -> None:
    """An older journal may have copied its source's immutable or read-only attribute."""
    attributes = snapshot.lstat()
    if attributes.st_nlink != 1 or not stat.S_ISREG(attributes.st_mode):
        return
    flags = getattr(attributes, 'st_flags', 0)
    if flags & _BSD_FILE_LOCK_FLAGS:
        os.chflags(snapshot, flags & ~_BSD_FILE_LOCK_FLAGS)
    if os.name == 'nt' and not attributes.st_mode & stat.S_IWRITE:
        snapshot.chmod(stat.S_IMODE(attributes.st_mode) | stat.S_IWRITE)


class Journal:
    """`rollbacks/<txid>/journal.json`: the targets one publish replaces and how far it got.

    `prepare` is written before anything changes, `done` after the files, the install
    record and the gateway coordination all finished, `restored` after a restore.
    Ledger migration records `committed` before resuming writers; recovery then
    completes service resumption and never restores the committed data.
    The install record as it was before the publish is kept next to the journal.
    """

    def __init__(self, folder: Path, data: dict):
        self.folder, self.data = Path(folder), data

    @property
    def txid(self) -> str:
        return self.folder.name

    @property
    def phase(self):
        return self.data.get("phase")

    def record_copy(self) -> Path | None:
        """The saved install record, or None when there was no record before the publish."""
        name = self.data.get("record_copy")
        return self.folder / name if name else None

    def restore_record(self, ghost_root, *, metadata_first: bool = False) -> None:
        # Each saved filename and each absence belongs to the pre-publication record.
        def restore_payload():
            names = self.data.get("record_files", [self.data.get("record_copy")])
            installed_payload.restore(
                ghost_root,
                self.folder / installed_payload.FILENAME if installed_payload.FILENAME in names else None,
                attributed_saved=(self.folder / installed_payload.ATTRIBUTED_FILENAME
                                  if installed_payload.ATTRIBUTED_FILENAME in names else None),
                legacy_saved=(self.folder / installed_payload.LEGACY_FILENAME
                              if installed_payload.LEGACY_FILENAME in names else None),
            )
        failures = []
        steps = (self.restore_metadata, restore_payload) if metadata_first else (restore_payload, self.restore_metadata)
        for restore in steps:
            try:
                restore()
            except (OSError, ValueError, RuntimeError) as error:
                failures.append(str(error))
        if failures:
            raise InstallTransactionError('; '.join(failures))

    def restore_metadata(self) -> None:
        """Restore the saved regular files in their caller's dependency order."""
        failures = []
        for item in self.data.get('metadata_records', []):
            destination = Path(item['path'])
            try:
                if item.get('restore_if_absent') and os.path.lexists(destination):
                    continue
                if item['copy'] is None:
                    destination.unlink(missing_ok=True)
                    continue
                snapshot = self.folder / item['copy']
                saved = snapshot.read_bytes()
                try:
                    if stat.S_ISREG(destination.lstat().st_mode) and destination.read_bytes() == saved:
                        continue
                except (FileNotFoundError, PermissionError):
                    pass
                installed_payload._write(destination, saved)
                _copy_metadata_stat(snapshot, destination)
            except (OSError, ValueError, RuntimeError) as error:
                failures.append(f'{destination}: {error}')
        if failures:
            raise InstallTransactionError('; '.join(failures))

    def preserve_metadata(self, file: Path, *, restore_if_absent: bool = False) -> None:
        """Save an owner's metadata before it changes alongside this publication."""
        file = file.resolve()
        records = self.data.setdefault('metadata_records', [])
        name = f'metadata-{len(records)}' if file.exists() else None
        if name:
            snapshot = self.folder / name
            shutil.copyfile(file, snapshot)
            _copy_metadata_stat(file, snapshot)
        records.append(dict(path=str(file), copy=name))
        if restore_if_absent:
            records[-1]['restore_if_absent'] = True
        self.save()

    def record_staged(self, batch: ReplacementBatch) -> None:
        """Persist the exact replacement set before the batch can publish any path."""
        for item, target in zip(batch.prepared, self.data['targets']):
            target.update(stage=str(item['stage']), rollback=str(item['rollback']),
                          before=content_digest.state(item['dest'], include_generated=item['mode'] == 'data'),
                          candidate=content_digest.state(item['stage'], include_generated=item['mode'] == 'data'))
        self.save()

    def save(self) -> None:
        self.data["updated_at"] = _now()
        _write_json(self.folder / JOURNAL_FILENAME, self.data)

    def mark(self, phase: str) -> None:
        self.data["phase"] = phase
        self.save()

    def checkpoint(self, participant: str, state: dict) -> None:
        """Save the participant's recovery facts before it changes an external service."""
        self.data["participants"][participant] = state
        self.save()

    @classmethod
    def begin(cls, request: dict, targets: list[dict]) -> "Journal":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        root = rollbacks_root(request["ghost_root"])
        if is_link_or_reparse_point(root):
            raise InstallTransactionError(f"cannot create a journal under linked publish root: {root}",
                                          journal_reason='linked-publish-root')
        folder = root / f"{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        folder.mkdir(parents=True)
        record_files = []
        for name in installed_payload.RECORD_FILENAMES:
            record = Path(request["ghost_root"]) / name
            if record.exists():
                shutil.copyfile(record, folder / name)
                record_files.append(name)
        copy = record_files[0] if record_files else None
        journal = cls(folder, {
            "schema_version": JOURNAL_SCHEMA_VERSION, "txid": folder.name, "phase": "prepare",
            "host": request["host"], "created_at": _now(), "record_copy": copy,
            "record_files": record_files, "participants": {},
            "targets": [{key: target[key] for key in ("name", "kind", "mode", "source", "dest", "logical_dest") if key in target}
                        for target in targets]})
        journal.save()
        return journal

    @classmethod
    def load(cls, ghost_root, txid: str) -> "Journal":
        """Read a supported journal without following linked journal material.

        ``journal_reason`` distinguishes unsupported versions from unreadable or malformed records.
        Callers decide which phases they consume and how a recognition failure affects their work.
        Schema 1 journals that saved only ``record_copy`` remain readable.
        """
        root = rollbacks_root(ghost_root)
        folder = root / txid
        if not txid or txid in ('.', '..') or Path(txid).name != txid:
            raise InstallTransactionError(f"invalid publish transaction id {txid!r}; review {root}",
                                          journal_reason='invalid-journal')
        try:
            for path, reason in ((root, 'linked-publish-root'), (folder, 'linked-publish-entry'),
                                 (folder / JOURNAL_FILENAME, 'linked-publish-journal')):
                if is_link_or_reparse_point(path):
                    raise InstallTransactionError(f"journal of publish {txid} uses a linked path: {path}",
                                                  journal_reason=reason)
            data = json.loads((folder / JOURNAL_FILENAME).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise InstallTransactionError(f"journal of publish {txid} cannot be read ({exc}); review {folder}",
                                          journal_reason='unreadable-journal') from exc
        if not isinstance(data, dict) or type(data.get('schema_version')) is not int:
            raise InstallTransactionError(f"journal of publish {txid} has no valid schema version; review {folder}",
                                          journal_reason='invalid-journal')
        if data['schema_version'] != JOURNAL_SCHEMA_VERSION:
            raise InstallTransactionError(
                f"journal of publish {txid} has unsupported schema version {data['schema_version']}; review {folder}",
                journal_reason='unsupported-journal-schema')
        if (data.get('txid') != txid or not isinstance(data.get('targets'), list)
                or any(not isinstance(target, dict) for target in data['targets'])):
            raise InstallTransactionError(f"journal of publish {txid} has an invalid transaction id or targets; review {folder}",
                                          journal_reason='invalid-journal')

        def invalid(field: str) -> None:
            raise InstallTransactionError(f"journal of publish {txid} has invalid {field}; review {folder}",
                                          journal_reason='invalid-journal')

        def path_string(value) -> bool:
            return isinstance(value, str) and bool(value) and '\0' not in value

        if not isinstance(data.get('phase'), str):
            invalid('phase')
        if 'host' in data and not isinstance(data['host'], str):
            invalid('host')
        if 'participants' in data and not isinstance(data['participants'], dict):
            invalid('participant recovery facts')
        for target in data['targets']:
            if not path_string(target.get('dest')):
                invalid('target destination')
            for key in ('source', 'logical_dest', 'stage', 'rollback'):
                if key in target and not (key in ('stage', 'rollback') and target[key] is None):
                    if not path_string(target[key]):
                        invalid(f'target {key}')
            for key in ('kind', 'name', 'mode'):
                if key in target and not isinstance(target[key], str):
                    invalid(f'target {key}')
            # These are opaque content_digest states; their owner defines the comparison.
            for key in ('before', 'candidate'):
                if target.get(key) is not None and not isinstance(target[key], dict):
                    invalid(f'target {key} state')
        metadata = data.get('metadata_records', [])
        if not isinstance(metadata, list):
            invalid('metadata records')
        for index, item in enumerate(metadata):
            if (not isinstance(item, dict) or not path_string(item.get('path'))
                    or not Path(item['path']).is_absolute() or 'copy' not in item
                    or item['copy'] not in (None, f'metadata-{index}')):
                invalid('metadata records')
            if 'restore_if_absent' in item and not isinstance(item['restore_if_absent'], bool):
                invalid('metadata restoration condition')
            # preserve_metadata resolves the destination and names each saved copy by its index.
            if item['copy'] is not None and is_link_or_reparse_point(folder / item['copy']):
                raise InstallTransactionError(f"journal of publish {txid} has linked metadata copies; review {folder}",
                                              journal_reason='linked-publish-metadata')
        if data.get('preparation_dir') not in (None, 'input'):
            invalid('preparation directory')
        if data.get('preparation_dir') is not None and is_link_or_reparse_point(folder / data['preparation_dir']):
            raise InstallTransactionError(f"journal of publish {txid} has a linked preparation directory; review {folder}",
                                          journal_reason='linked-preparation-directory')
        copies = data.get("record_files", [data["record_copy"]] if data.get("record_copy") else [])
        if (not isinstance(copies, list) or any(name not in installed_payload.RECORD_FILENAMES for name in copies)
                or data.get("record_copy") not in (None, *copies)):
            raise InstallTransactionError(f"journal of publish {txid} has invalid record copies; review {folder}",
                                          journal_reason='invalid-journal')
        if any(is_link_or_reparse_point(folder / name) for name in copies):
            raise InstallTransactionError(f"journal of publish {txid} has linked record copies; review {folder}",
                                          journal_reason='linked-installed-baseline')
        return cls(folder, data)


@contextlib.contextmanager
def record_update(ghost_root, *, version_file: Path | None = None, cleanup_on_commit: bool = False):
    """Commit records and saved regular files; optionally retain recovery through final journal cleanup.

    Final management removal opts in because a cleanup error still needs its removed
    command. Keep only this journal's small existing copies in memory until cleanup
    returns, then discard them. Ordinary publication keeps its committed effects.
    """
    _require_lock(ghost_root)
    root = rollbacks_root(ghost_root)
    created_root = cleanup_on_commit and not os.path.lexists(root)
    journal = Journal.begin({"ghost_root": str(ghost_root), "host": "installer"}, [])
    if version_file is not None:
        journal.preserve_metadata(Path(version_file))
    copies = None
    try:
        yield journal
        if cleanup_on_commit:
            if journal.data.get('targets') or journal.data.get('participants'):
                raise InstallTransactionError('Final record cleanup only supports metadata and saved regular files')
            names = {*journal.data.get('record_files', []),
                     *(item['copy'] for item in journal.data.get('metadata_records', []) if item['copy'])}
            copies = {name: ((journal.folder / name).read_bytes(), (journal.folder / name).stat())
                      for name in names}
        journal.mark("done")
        if cleanup_on_commit:
            retained = cleanup_completed(ghost_root, txids=(journal.txid,))
            if retained:
                raise InstallTransactionError('Final record cleanup pending: ' + '; '.join(retained))
            if created_root and root.is_dir() and not any(root.iterdir()):
                root.rmdir()
    except (Exception, KeyboardInterrupt) as exc:
        # After the durable commit a later removal only has journal cleanup to finish.
        if isinstance(exc, KeyboardInterrupt) and (not cleanup_on_commit or _journal_phase(journal.folder) != "prepare"):
            raise
        prepare_error = None
        try:
            if copies is not None:
                # cleanup_completed may have deleted any prefix of the saved copies,
                # including every copy before a final rmdir failure. Reconstitute the
                # same journal before restoring; never create another recovery owner.
                journal.folder.mkdir(parents=True, exist_ok=True)
                for name, (data, metadata) in copies.items():
                    saved = journal.folder / name
                    if not saved.exists():
                        installed_payload._write(saved, data)
                        saved.chmod(stat.S_IMODE(metadata.st_mode))
                        os.utime(saved, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
                try:
                    journal.mark('prepare')
                except Exception as error:
                    # A journal write failure must not prevent restoring readable
                    # saved commands and records to independently writable paths.
                    prepare_error = error
            # The removed management entry must be restored even if the install
            # record cannot yet be written. Its next invocation reads this pending
            # journal's active binding before the lock retries record recovery.
            journal.restore_record(ghost_root, metadata_first=cleanup_on_commit)
        except Exception as recovery:
            phase_detail = f'; recovery prepare mark failed ({prepare_error})' if prepare_error else ''
            raise InstallTransactionError(
                f"install record update failed ({exc}); restore failed ({recovery}){phase_detail}; review {journal.folder}"
            ) from recovery
        exc.add_note('saved files and records were restored')
        if prepare_error is not None:
            exc.add_note(f'Recovery prepare mark failed ({prepare_error}); review {journal.folder}')
        try:
            journal.mark('restored')
        except Exception as mark_error:
            exc.add_note(f'Could not write the restored phase ({mark_error}); review {journal.folder}')
        raise


_ABSENT_JOURNAL = object()


def _journal_phase(folder: Path):
    """Read the stored phase; distinguish a missing file from every value the file can contain."""
    file = folder / JOURNAL_FILENAME
    try:
        if is_link_or_reparse_point(file):
            return None
        data = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _ABSENT_JOURNAL
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data.get("phase") if isinstance(data, dict) else None


def is_journal_candidate(path: Path) -> bool:
    """Recognize entries the journal reader must inspect, including broken linked entries."""
    mode = path.lstat().st_mode
    return stat.S_ISLNK(mode) or stat.S_ISDIR(mode)


def unfinished(ghost_root) -> list[str]:
    """Unfinished transaction ids, newest first; unreadable journals count and traversal failures raise.

    An absent root or vanished entry has no recovery work. Linked entries are not recovery targets;
    linked journal files remain unfinished so the journal reader can refuse them without following them.
    """
    root = rollbacks_root(ghost_root)
    try:
        if is_link_or_reparse_point(root):
            raise InstallTransactionError(f"cannot inspect linked publish root: {root}",
                                          journal_reason='linked-publish-root')
        entries = list(root.iterdir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise InstallTransactionError(f"cannot inspect publish journals under {root}: {exc}") from exc
    folders = []
    for path in entries:
        try:
            kind = path_entry_kind(path)
        except OSError as exc:
            raise InstallTransactionError(f"cannot inspect publish entry {path}: {exc}") from exc
        if kind == "directory":
            folders.append(path)
    return [folder.name for folder in sorted(folders, key=lambda path: path.name, reverse=True)
            if _journal_phase(folder) not in (*FINISHED_PHASES, _ABSENT_JOURNAL)]


def record_path_after_recovery(ghost_root) -> Path | None:
    """Select the oldest rollback-capable publish's saved detail, else the current record's path.

    Committed ledger migrations resume services without restoring the old record.
    """
    pending = unfinished(ghost_root)
    for txid in reversed(pending):
        journal = Journal.load(ghost_root, txid)
        if journal.phase != 'committed':
            copy = journal.record_copy()
            return installed_payload.read_path(copy.parent) if copy is not None else None
    return installed_payload.read_path(ghost_root)


def record_after_recovery(ghost_root) -> dict | None:
    return installed_payload.read_file(record_path_after_recovery(ghost_root))


def _record_value(path, mode: str) -> dict:
    """What the install record keeps for a published target: the link's target, or the copy's file digests."""
    if mode == "link":
        return {"link_target": content_digest.link_target(path)}
    return {"files": content_digest.tree(path)}


def _links_to_its_source(target: dict) -> bool:
    """A link the installer found already in place (status `current`) that still points at its source."""
    if target["mode"] != "link" or target.get("status") != "current":
        return False
    try:
        return content_digest.link_target(target["dest"]) == target["source"]
    except OSError:
        return False


class PublishBatch(ReplacementBatch):
    """The targets of one publish request.

    Each replaced target is staged next to its install location; publishing moves the
    original into `rollbacks/<txid>/`, renames the staged copy or link into place and
    writes the install record entries of every target, the current links included, with
    the source, installer and transaction in the same step. Restoring puts back the files
    and the record a journal saved.
    """

    def __init__(self, request: dict, replaced: list[dict], current: list[dict], journal: Journal, *,
                 progress_callback=None, progress_events=None):
        self.request, self.journal = request, journal
        self.asset_root = request.get('asset_root') or request['source'].get('root')
        self.replaced, self.current = replaced, current
        self.ghost_root = Path(request["ghost_root"])
        progress = request.get("progress") or {}
        super().__init__([(target["source"], target["dest"], "symlink" if target["mode"] == "link" else "copy")
                          for target in replaced],
                         rollback_root=journal.folder,
                         progress_callback=progress_callback, progress_event_file=progress.get("event_file"),
                         progress_events=progress_events)

    def stage(self):
        super().stage()
        for item, target, selected in zip(self.prepared, self.journal.data["targets"], self.replaced):
            if 'command_text' in selected:
                item['stage'].write_text(selected['command_text'], encoding='utf-8', newline='\n')
                item['candidate'] = self.identity(item['stage'])
            if (selected["mode"] == "copy" and selected["kind"] in {"skill", "support"}
                    and item["stage"].is_dir() and not item["stage"].is_symlink()):
                addon_id = selected["provider"].removeprefix("addon:") if selected["provider"].startswith("addon:") else None
                write_ownership_marker(
                    item["stage"], platform=self.request["host"], asset_id=selected["name"],
                    source_repo=self.asset_root, source_commit=selected["source_commit"],
                    install_mode="copy", owner="addon" if addon_id else "ghost-alice", addon_id=addon_id,
                    provided_kind=selected["kind"],
                )
                item["candidate"] = self.identity(item["stage"])
            item["record"] = _record_value(item["stage"], target["mode"])
        self.journal.record_staged(self)

    def publish(self):
        super().publish()
        self._write_record()

    def _write_record(self):
        installed_at = _now()
        entries = {}
        recorded = [(target, item["record"]) for target, item in zip(self.replaced, self.prepared)]
        recorded += [(target, _record_value(target["dest"], "link")) for target in self.current]
        for target, value in recorded:
            entry = {key: target[key] for key in ("name", "kind", "mode", "provider", "source", "source_commit")}
            entry.update(used_by=[self.request["host"]], installed_at=installed_at, baseline="recorded")
            entry.update(value)
            entries[installed_payload.target_key(target["dest"])] = entry
        patches = {"targets": entries, "installer": dict(self.request["installer"]),
                   "source": dict(self.request["source"]),
                   "last_transaction": {"id": self.journal.txid, "updated_at": installed_at,
                                        "rollback_dir": str(self.journal.folder)}}
        installed_payload.update(self.ghost_root, patches, journal=self.journal)
        if self.request.get('install_state'):
            import install_state_writer
            install_state_writer.rebase_source(self.request['install_state'], self.asset_root,
                                              self.request['targets'], self.request['source'])

    def restore(self):
        super().restore()
        self.journal.restore_record(self.ghost_root)

    def finalize(self):
        self.journal.mark("done")


def _validated_request(request) -> dict:
    def fail(reason):
        raise InstallTransactionError(f"invalid publish request: {reason}")

    if not isinstance(request, dict) or request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        fail(f"schema_version must be {REQUEST_SCHEMA_VERSION}")
    for key in ("host", "ghost_root"):
        if not isinstance(request.get(key), str) or not request[key]:
            fail(f"{key} must be a nonempty string")
    for key in ("installer", "source"):
        if not isinstance(request.get(key), dict):
            fail(f"{key} must be an object")
    targets = request.get("targets")
    if not isinstance(targets, list) or not targets:
        fail("targets must be a nonempty list")
    for target in targets:
        if not isinstance(target, dict):
            fail("every target must be an object")
        for key in ("name", "provider", "source", "source_commit", "dest"):
            if not isinstance(target.get(key), str) or not target[key]:
                fail(f"target {key} must be a nonempty string")
        if target.get("kind") not in installed_payload.KINDS or target.get("mode") not in installed_payload.MODES:
            fail(f"target {target['name']} needs kind {installed_payload.KINDS} and mode {installed_payload.MODES}")
        if 'command_text' in target and (target['kind'] != 'command' or target['mode'] != 'copy'
                                         or not isinstance(target['command_text'], str)):
            fail('command_text requires a copied command target and rendered text')
        if "status" in target and target["status"] not in TARGET_STATUSES:
            fail(f"target {target['name']} status must be one of {TARGET_STATUSES}")
    progress = request.get("progress", {})
    if not isinstance(progress, dict):
        fail("progress must be an object")
    if progress.get("event_file") and any(target.get("status") is None for target in targets):
        fail("every target needs a status when progress events are written")
    return request


def _require_lock(ghost_root) -> None:
    lock = Path(ghost_root) / installed_payload.LOCK_FILENAME
    try:
        held = install_lock.held_by_process_tree(lock)
    except install_lock.InstallLockError as exc:
        raise InstallTransactionError(f"publish runs only under the install lock: {exc}") from exc
    if not held:
        raise InstallTransactionError(f"publish runs only under the install lock of this process or its parent: {lock}")


def _cleanup_references(ghost_root: Path, *, record: dict | None = None) -> list[tuple[Path, str]]:
    references = []
    record = (installed_payload.read(ghost_root) or {}) if record is None else record
    active = record.get('installer', {}).get('active_root')
    if active:
        references.append((Path(active), 'active-installer-reference'))
    for dest, entry in record.get("targets", {}).items():
        if not isinstance(entry, dict) or not entry.get("used_by"):
            continue
        reason = "current-target-reference:" + ",".join(sorted(entry["used_by"]))
        for value in (dest, entry.get("source"), entry.get("link_target")):
            if isinstance(value, str) and value:
                path = Path(value)
                references.append((path if path.is_absolute() else Path(dest).parent / path,
                                   reason))
        path = Path(dest)
        if path_entry_kind(path) in ('symlink', 'junction'):
            references.append((path.resolve(), reason))
    service = record.get("service") or {}
    if not isinstance(service, dict):
        raise ValueError("current service record is not an object")
    for key in ("runtime_root", "program", "launch_agent", "state_path", "config_path", "config_backup"):
        value = service.get(key)
        if isinstance(value, str) and value:
            references.append((Path(value), f"current-service-reference:{key}"))
    pending = ghost_root / "pending-merges"
    if pending.exists():
        for host in pending.iterdir():
            manifest = host / "manifest.json"
            if not manifest.exists():
                continue
            try:
                data = pending_manifest.read_json(manifest)
                if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
                    raise ValueError("manifest needs entries")
                for entry in data["entries"]:
                    if not isinstance(entry, dict):
                        raise ValueError("manifest entry is not an object")
                    if pending_manifest.is_pending(entry):
                        for key in ("source_path", "backup_path"):
                            value = entry.get(key)
                            if isinstance(value, str) and value:
                                path = Path(value)
                                references.append((path if path.is_absolute() else manifest.parent / path,
                                                   f"pending-merge-reference:{manifest}"))
            except (OSError, ValueError) as exc:
                raise InstallTransactionError(f"completed publish cleanup cannot read pending manifest {manifest}: {exc}") from exc
    return references


def _reference_reason(path: Path, references: list[tuple[Path, str]], *, ghost_root: Path, platform: str) -> str | None:
    """Keep recorded consumers and ask the live-reference owner about every host's inventory and addons."""
    original = installer_assets.reference_path(path.absolute())
    for reference, reason in references:
        # Do not resolve the original: deleting a recorded old link does not delete its source.
        for spelling in (reference.absolute(), reference.resolve()):
            candidate = installer_assets.reference_path(spelling)
            if candidate == original or original in candidate.parents or candidate in original.parents:
                return reason
    from uninstall_cleanup import platforms_referencing_target
    hosts = platforms_referencing_target(path, current_manifest=ghost_root/'install-state'/f'{platform}.json',
                                         current_platform=platform, include_current=True, source_tree=True)
    return 'live-host-source-reference:' + ','.join(hosts) if hosts else None


def recorded_copy_cleanup_reason(path: Path, entry: dict, *, allow_missing: bool = False) -> str | None:
    differences = content_digest.compare(entry, path)
    if allow_missing and path.is_dir() and "." not in entry["files"]:
        # A removed file may be absent, but a directory or link at its path is a replacement.
        differences["missing"] = [name for name in differences["missing"] if os.path.lexists(path / name)]
    if any(differences.values()):
        return "installed-content-changed"
    return installer_assets.unrecorded_copy_content(path, entry["files"])


def _original_cleanup_reason(original: Path, target: dict, record: dict) -> str | None:
    if path_entry_kind(original) == 'other-reparse-point':
        return 'unsupported-reparse-point'
    if target.get('kind') in {'data', 'configuration'} and target.get('before') is not None:
        return (None if content_digest.state(original, include_generated=target.get('mode') == 'data') == target['before']
                else 'migration-original-changed')
    targets = record.get("targets", {})
    entry = targets.get(installed_payload.target_key(target["dest"], targets)) if target.get("dest") else None
    if isinstance(entry, dict) and entry.get("baseline") in ("recorded", "migrated"):
        if entry.get("mode") == "link" and isinstance(entry.get("link_target"), str):
            return "installed-link-changed" if content_digest.compare(entry, original)["link_target_differs"] else None
        if entry.get("mode") == "copy" and isinstance(entry.get("files"), dict):
            return recorded_copy_cleanup_reason(original, entry)
    if target.get("kind") in ("skill", "support") and not original.is_symlink():
        classified = installer_assets.classify_skill_root(original, expected_asset_id=target.get("name"), whole_copy=True)
        if classified.ownership == installer_assets.OWNERSHIP_GHOST_ALICE_MANAGED:
            return None
        return classified.reason
    return "no-installed-baseline"


def cleanup_completed(ghost_root: Path | str, *,
                      record_source_cleanup: Callable[[Path, dict], object] | None = None,
                      txids: Sequence[str] | None = None) -> tuple[str, ...]:
    """Discard redundant managed publish originals under the install lock after installation or target removal.

    Return retained paths with their reasons. An unreadable dependency or failed deletion raises outside
    publish's restore boundary; cleanup never reverts the caller's file changes. Successful source versions have
    their own owner and retention policy. The caller supplies the policy for a completed record move.
    This function creates no backups. A failed final directory deletion restores the completed journal identity.
    A caller can limit cleanup to its completed transaction ids without selecting unrelated source journals.
    """
    ghost_root = Path(ghost_root)
    _require_lock(ghost_root)
    root = rollbacks_root(ghost_root)
    retained = []
    try:
        if is_link_or_reparse_point(root):
            return (f"{root}: linked-publish-root",)
        if not root.exists():
            return ()
        active = unfinished(ghost_root)
        if active:
            return tuple(f"{root / txid}: unfinished-publish" for txid in active)
        references = _cleanup_references(ghost_root)
        for folder in sorted(root.iterdir()):
            if txids is not None and folder.name not in txids:
                continue
            try:
                candidate = is_journal_candidate(folder)
            except FileNotFoundError:
                continue
            if not candidate:
                retained.append(f"{folder}: unrecognized-publish-entry")
                continue
            try:
                journal = Journal.load(ghost_root, folder.name)
            except InstallTransactionError as exc:
                retained.append(f"{folder}: {exc.journal_reason}: {exc}")
                continue
            if journal.phase not in FINISHED_PHASES:
                retained.append(f"{folder}: no-finished-journal")
                continue
            removal = journal.data.get('source_cleanup')
            if removal is not None and journal.phase == 'done':
                if record_source_cleanup is None:
                    raise InstallTransactionError(f'{folder}: record source cleanup policy is required')
                record_source_cleanup(ghost_root, removal)
            record_files = journal.data.get("record_files", [journal.data["record_copy"]]
                                            if journal.data.get("record_copy") else [])
            try:
                before = installed_payload.read(folder) if journal.record_copy() is not None else None
            except installed_payload.RecordError as exc:
                retained.append(f"{folder}: unavailable-installed-baseline: {exc}")
                continue
            unrecognized_original = False
            for target in journal.data.get("targets", []):
                value = target.get("rollback")
                if not isinstance(value, str):
                    continue
                original = Path(value)
                if original.parent != folder or not original.name.startswith("rollback-"):
                    retained.append(f"{folder}: unrecognized-original-path: {original}")
                    unrecognized_original = True
                    continue
                if not os.path.lexists(original):
                    continue
                reason = (_reference_reason(original, references, ghost_root=ghost_root, platform=journal.data.get('host', ''))
                          or _original_cleanup_reason(original, target, before or {}))
                if reason:
                    retained.append(f"{original}: {reason}")
                else:
                    try:
                        _remove_path(original)
                    except OSError as exc:
                        raise InstallTransactionError(f"cannot remove saved original {original}: {exc}") from exc
            if unrecognized_original:
                continue
            preparation = journal.data.get('preparation_dir')
            if preparation is not None:
                prepared = folder / preparation
                if os.path.lexists(prepared):
                    reason = _reference_reason(prepared, references, ghost_root=ghost_root,
                                               platform=journal.data.get('host', ''))
                    if reason or is_link_or_reparse_point(prepared):
                        retained.append(f'{prepared}: {reason or "linked-preparation-directory"}')
                        continue
                    _remove_path(prepared)
            metadata = {JOURNAL_FILENAME}
            metadata.update(record_files)
            saved_metadata = {item['copy'] for item in journal.data.get('metadata_records', []) if item['copy']}
            metadata.update(saved_metadata)
            remaining = [path for path in folder.iterdir() if path.name not in metadata]
            if remaining:
                known = {target.get("rollback") for target in journal.data.get("targets", [])}
                retained.extend(f"{path}: unrecognized-publish-content" for path in remaining if str(path) not in known)
                continue
            reason = _reference_reason(folder, references, ghost_root=ghost_root, platform=journal.data.get('host', ''))
            if reason:
                retained.append(f"{folder}: {reason}")
                continue
            if any(is_link_or_reparse_point(path) or not path.is_file() for path in folder.iterdir()):
                retained.append(f"{folder}: unrecognized-publish-metadata")
                continue
            # Remove the legacy pointer before its detailed record, and the journal last.
            # Every interrupted prefix still has a readable record (or none) and an operation identity.
            for path in sorted(folder.iterdir(), key=lambda path: (
                    path.name == JOURNAL_FILENAME, path.name != installed_payload.LEGACY_FILENAME, path.name)):
                if path.name in saved_metadata:
                    _unlock_old_metadata_copy(path)
                path.unlink()
            try:
                folder.rmdir()
            except FileNotFoundError:
                pass
            except OSError as error:
                try:
                    journal.save()
                except OSError as save_error:
                    raise InstallTransactionError(
                        f'{folder}: directory deletion failed ({error}); completed journal could not be retained ({save_error})'
                    ) from save_error
                raise
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        if isinstance(exc, InstallTransactionError):
            raise
        raise InstallTransactionError(f"completed publish cleanup failed under {root}: {exc}; cleanup did not revert earlier file changes") from exc
    return tuple(retained)


def publish(request: dict, participants: Sequence = ()) -> str:
    """Publish the request's targets and their install record entries; return the transaction id.

    When the request replaces targets, every participant's `before_publish` runs after
    the staging and before any install location changes, and `after_publish` once the
    files and the record are published. On failure, `before_restore` runs first;
    the batch restores files and the record, then `after_restore` runs. Only a
    complete restore writes `restored` to the journal; an unfinished participant
    leaves recovery pending even when the files and record are already back.
    InstallTransactionError retains the original cause's exit code unless the
    failed recovery supplies its own code. A recovery failure also names the
    folder for review.
    """
    request = _validated_request(request)
    _require_lock(request["ghost_root"])
    current = [target for target in request["targets"] if _links_to_its_source(target)]
    replaced = [target for target in request["targets"] if not _links_to_its_source(target)]
    try:
        journal = Journal.begin(request, replaced)
        if request.get('install_state'):
            journal.preserve_metadata(Path(request['install_state']))
    except OSError as exc:
        raise InstallTransactionError(f"publish journal cannot be written under "
                                      f"{rollbacks_root(request['ghost_root'])}: {exc}") from exc
    context = PublishContext(ghost_root=Path(request["ghost_root"]), host=request["host"], txid=journal.txid,
                             destinations=tuple(Path(target["dest"]) for target in replaced),
                             checkpoint=journal.checkpoint)
    progress = request.get("progress") or {}
    statuses = [target.get("status") for target in replaced]
    callback = None
    if progress.get("label"):
        _write_progress(progress["label"], 0, statuses)

        def callback(done: int, total: int) -> None:
            _write_progress(progress["label"], done, statuses)

    events = None
    if progress.get("event_file"):
        events = [{"platform": request["host"], "target_id": target["name"], "target_kind": target["kind"],
                   "status": target["status"]} for target in replaced]
    batch = PublishBatch(request, replaced, current, journal,
                         progress_callback=callback, progress_events=events)
    # Only current links change no file, so the participants have nothing to do.
    attending = list(participants) if replaced else []
    try:
        batch.stage()
        for participant in attending:
            participant.before_publish(context)
        batch.publish()
        for participant in attending:
            participant.after_publish(context)
        batch.finalize()
    except Exception as exc:
        wrapped = _wrapped_failure(exc, batch)
        try:
            for participant in attending:
                participant.before_restore(context)
            batch.restore()
            for participant in attending:
                participant.after_restore(context)
            journal.mark("restored")
        except Exception as recovery:
            cause = recovery if hasattr(recovery, "exit_code") else exc
            wrapped = _wrapped_failure(cause, batch, f"{wrapped}; restore failed ({recovery}); review {journal.folder}")
        event_log = request.get("event_log")
        _write_failure_event(Path(event_log) if event_log else None, source=batch.source, dest=batch.dest,
                             phase=wrapped.phase, error=str(wrapped))
        raise wrapped from exc
    finally:
        batch.cleanup()
        if progress.get("label"):
            sys.stdout.write("\n")
            sys.stdout.flush()
    return journal.txid


def _recovery_action(folder: Path, target: dict):
    """What restores one journaled target: None when it never changed, else (action, dest, kept original)."""
    if "candidate" not in target:
        # The publish never finished staging, so nothing was moved.
        return None
    dest = Path(target["dest"])
    kept = Path(target["rollback"]) if target.get("rollback") else None
    include_generated = target.get('mode') == 'data'
    current = content_digest.state(dest, include_generated=include_generated)
    if kept is not None and os.path.lexists(kept):
        if content_digest.state(kept, include_generated=include_generated) != target.get("before"):
            raise InstallTransactionError(f"{kept} no longer matches its original; review {folder}")
        if current is None:
            return "put-back", dest, kept
        if current == target["candidate"]:
            return "replace", dest, kept
    elif target.get("before") is None:
        if current is None:
            return None
        if current == target["candidate"]:
            return "remove", dest, None
    elif current == target["before"]:
        return None
    raise InstallTransactionError(f"{dest} is neither its original nor what the publish left there; "
                                  f"review {folder}")


def recover(ghost_root, txid: str, *,
            participant_factory: Callable[[PublishContext, dict | None, dict | None], Sequence] | None = None) -> list[str]:
    """Restore an uncommitted publish, or finish service resumption after data commit.

    Every target is checked before any changes: the install location must still hold
    what the publish left there (or its original), and a moved original must still be
    kept with its recorded before state. The installer supplies a participant factory
    when service recovery facts need interpretation; without it only proven file-only
    recovery is allowed. Reconstructed participants confirm service
    absence before the originals and record return, then restore prior service state.
    Only a complete restoration marks `restored`. A committed migration checks its
    published configuration and resumes services before marking `done`; it never
    restores data or the prior record. Returns the actions completed.
    """
    journal = Journal.load(ghost_root, txid)
    if journal.phase in FINISHED_PHASES:
        return []
    try:
        for target in journal.data.get('targets', []):
            if ('logical_dest' in target and Path(target['logical_dest']).resolve() != Path(target['dest'])):
                raise InstallTransactionError(f"Configuration link changed: {target['logical_dest']}; review {journal.folder}")
        context = PublishContext(ghost_root=Path(ghost_root), host=journal.data.get("host", ""), txid=txid,
                                 destinations=tuple(Path(target["dest"]) for target in journal.data.get("targets", []))
                                 + tuple(Path(target['logical_dest']) for target in journal.data.get('targets', []) if 'logical_dest' in target)
                                 + tuple(Path(item['path']) for item in journal.data.get('metadata_records', [])))
        copy = journal.record_copy()
        states = journal.data.get("participants")
        if journal.phase == 'committed':
            # A resumed writer may have changed ledger data. Only immutable bindings
            # and service ownership constrain resumption; committed data never rolls back.
            for target in journal.data.get('targets', []):
                path = Path(target['dest'])
                if target.get('kind') == 'data':
                    if not path.is_dir() or path.is_symlink():
                        raise InstallTransactionError(f'Committed ledger is unavailable: {path}')
                elif content_digest.state(path) != target.get('candidate'):
                    raise InstallTransactionError(f'Committed configuration changed: {path}')
            if states and participant_factory is None:
                raise InstallTransactionError('Participant recovery requires the installer owner; run the installer again')
            attending = participant_factory(context, states, installed_payload.read(ghost_root)) if participant_factory else ()
            for participant in attending:
                participant.after_publish(context)
            journal.mark('done')
            return [f'Completed service resumption for committed publish {txid}']
        actions = [action for action in (_recovery_action(journal.folder, target)
                                         for target in reversed(journal.data.get("targets", []))) if action]
        # An empty checkpoint set establishes that no participant reached its effects;
        # an unreadable original record can still return byte-for-byte in this case.
        original_record = installed_payload.read(copy.parent) if copy is not None and states != {} else None
        if participant_factory is None:
            if states or (states is None and (original_record or {}).get("service") is not None):
                raise InstallTransactionError("Participant recovery requires the installer owner; run the installer again")
            attending = ()
        else:
            attending = participant_factory(context, states, original_record)
        for participant in attending:
            participant.before_restore(context)
        lines = []
        for action, dest, kept in actions:
            if action in ("replace", "remove"):
                _remove_path(dest)
            if action == "remove":
                lines.append(f"Removed {dest}; it did not exist before publish {txid}")
                continue
            _rename_path(kept, dest)
            lines.append(f"Restored {dest} from {kept}")
        journal.restore_record(ghost_root)
        for participant in attending:
            participant.after_restore(context)
        journal.mark("restored")
    except Exception as exc:
        raise _wrapped_failure(exc, detail=f"publish recovery failed ({exc}); review {journal.folder}") from exc
    try:
        for target in journal.data.get("targets", []):
            stage = target.get("stage")
            if (stage and os.path.lexists(stage)
                    and content_digest.state(stage, include_generated=target.get('mode') == 'data') == target.get("candidate")):
                _remove_path(Path(stage))
    except Exception as exc:
        raise _wrapped_failure(exc, detail=(
            "The installer restored the files, install record and required service state for this publish, "
            f"but could not inspect or remove staged files ({exc}); review {journal.folder}")) from exc
    return lines


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ghost-ALICE installer transaction helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    recover_parser = subparsers.add_parser("recover", help="Restore an unfinished publish from its journal.")
    recover_parser.add_argument("--txid", required=True)
    recover_parser.add_argument("--ghost-root", type=Path, default=None)

    return parser.parse_args(argv)


def _write_progress(label: str, done: int, statuses: Sequence[str]) -> None:
    counts = Counter(statuses[:done])
    sys.stdout.write(
        f"\r{label} "
        f"[{counts['current']}] [Current], "
        f"[{counts['updated']}] [updated], "
        f"[{counts['new']}] [newly added]"
    )
    sys.stdout.flush()


def failure_message(exc: InstallTransactionError) -> str:
    """The recovery diagnostic shared by the low CLI and the installer's lock owner."""
    if hasattr(exc, "code"):
        return json.dumps(dict(status=exc.outcome, error_code=exc.code, phase=exc.phase, detail=str(exc)))
    return str(exc)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "recover":
            for line in recover(args.ghost_root or Path.home() / ".ghost-alice", args.txid):
                print(line)
        else:
            raise AssertionError(args.command)
    except InstallTransactionError as exc:
        print(failure_message(exc), file=sys.stderr)
        return getattr(exc, 'exit_code', 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
