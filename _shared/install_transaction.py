#!/usr/bin/env python3
"""Transactional installer filesystem operations.

`publish --request <file>` places the targets one request names and writes their
install record entries in the same step. The original each target replaces moves
into `~/.ghost-alice/rollbacks/<txid>/`, whose `journal.json` says what the publish
replaces and how far it got (`prepare`, then `done` or `restored`). Any failure
restores the files and the record. `recover --txid` restores a publish that stopped
before its journal said `done`, and `unfinished` lists those publishes.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import content_digest
import installed_payload

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
DEFAULT_ROLLBACK_KEEP = 20
REQUEST_SCHEMA_VERSION = 1
JOURNAL_SCHEMA_VERSION = 1
ROLLBACKS_DIRNAME = "rollbacks"
JOURNAL_FILENAME = "journal.json"
FINISHED_PHASES = ("done", "restored")
TARGET_STATUSES = ("current", "updated", "new")


class InstallTransactionError(RuntimeError):
    pass


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


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()


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


def _prune_rollback_root(root: Path, keep: int = DEFAULT_ROLLBACK_KEEP) -> None:
    if keep < 0:
        keep = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    if len(entries) <= keep:
        return

    def sort_key(entry: Path) -> tuple[int, str]:
        try:
            return (entry.stat().st_mtime_ns, entry.name)
        except OSError:
            return (0, entry.name)

    entries.sort(key=sort_key)
    for entry in entries[: max(0, len(entries) - keep)]:
        try:
            _remove_path(entry)
        except OSError:
            continue


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_fingerprint(path: Path) -> str:
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
        if any(part in EXCLUDED_COPY_DIR_NAMES for part in rel_parts):
            continue
        if child.is_file() and _is_excluded_copy_file(child):
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
        event_log.parent.mkdir(parents=True, exist_ok=True)
        with event_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
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
    """Reversible filesystem participant; its caller owns service recovery."""
    def __init__(self, targets, *, rollback_root=None, rollback_keep=DEFAULT_ROLLBACK_KEEP,
                 progress_callback=None, progress_event_file=None, progress_events=None):
        self.targets = [(Path(src), Path(dst), mode) for src, dst, mode in targets]
        self.root = Path(rollback_root) if rollback_root is not None else self.targets[0][1].parent / '.ghost-alice-install-rollbacks'
        self.keep = rollback_keep
        self.callback, self.event_file, self.events = progress_callback, progress_event_file, progress_events
        if self.events is not None and len(self.events) != len(self.targets):
            raise InstallTransactionError('progress event count must match target count')
        self.prepared, self.phase = [], 'prepare'
        self.source, self.dest = self.targets[0][:2]

    @staticmethod
    def identity(path):
        try:
            info = path.lstat()
            return info.st_dev, info.st_ino, info.st_mode, _tree_fingerprint(path)
        except FileNotFoundError:
            return None

    @property
    def mappings(self):
        return tuple((item['stage'], item['dest']) for item in self.prepared)

    @property
    def destinations(self):
        return tuple(dst for _, dst, _ in self.targets)

    @property
    def recovery_reference(self):
        return str(self.root)

    def stage(self):
        for src, dst, mode in self.targets:
            self.source, self.dest = src, dst
            stage_root = dst.parent / '.ghost-alice-install-staging'
            stage = _unique_path(stage_root, 'stage', dst.name)
            item = dict(source=src, dest=dst, stage=stage, mode=mode,
                rollback=_unique_path(self.root, 'rollback', dst.name),
                before=self.identity(dst), moved=False, attempted=False, after=None)
            self.prepared.append(item)
            stage_root.mkdir(parents=True, exist_ok=True)
            try:
                if mode == 'symlink':
                    self.phase = 'stage-symlink'
                    _create_directory_link(src, stage)
                else:
                    self.phase = 'stage-copy'
                    _copy_to_stage(src, stage)
                    self.phase = 'stage-verify'
                    _verify_staged_copy(src, stage)
            finally:
                item['candidate'] = self.identity(stage)

    def publish(self):
        self.phase = 'publish'
        for item in self.prepared:
            if self.identity(item['dest']) != item['before'] or self.identity(item['stage']) != item['candidate']:
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
                current = self.identity(self.dest)
                item['moved'] = self.identity(item['rollback']) == item['before'] if item['before'] is not None else False
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
            current = self.identity(item['dest'])
            if current == item['before']:
                continue
            if current != item['after'] or (item['moved'] and self.identity(item['rollback']) != item['before']):
                raise InstallTransactionError('Concurrent target/recovery change prevents install rollback')
        for item in reversed(self.prepared):
            dst = item['dest']
            if self.identity(dst) == item['before']:
                continue
            if self.identity(dst) is not None:
                _remove_path(dst)
            if item['moved']:
                _rename_path(item['rollback'], dst)
            if self.identity(dst) != item['before']:
                raise InstallTransactionError('Restored target does not match its original')

    def finalize(self):
        # Retention cleanup is performed only after the whole service/file
        # transaction commits; never discard rollback material during recovery.
        try:
            _prune_rollback_root(self.root, self.keep)
        except OSError:
            pass

    def cleanup(self):
        for item in self.prepared:
            stage = item['stage']
            if stage.exists() or stage.is_symlink():
                try:
                    if self.identity(stage) == item.get('candidate'):
                        _remove_path(stage)
                except OSError:
                    pass

    def run_unmanaged(self):
        try:
            self.publish()
        except Exception:
            self.restore()
            raise


def _wrapped_failure(exc, batch, detail=None):
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
    wrapped.phase = getattr(exc, 'phase', batch.phase)
    return wrapped


def _replace(targets, *, rollback_root=None, event_log=None, rollback_keep=DEFAULT_ROLLBACK_KEEP,
             progress_callback=None, progress_event_file=None, progress_events=None):
    if not targets:
        return
    batch = ReplacementBatch(targets, rollback_root=rollback_root, rollback_keep=rollback_keep,
        progress_callback=progress_callback, progress_event_file=progress_event_file, progress_events=progress_events)
    try:
        batch.stage()
        from context_gateway_install import coordinate_payload_replacement
        coordinate_payload_replacement(batch)
        batch.finalize()
    except Exception as exc:
        wrapped = _wrapped_failure(exc, batch)
        _write_failure_event(Path(event_log) if event_log is not None else None,
            source=batch.source, dest=batch.dest, phase=wrapped.phase, error=str(wrapped))
        raise wrapped from exc
    finally:
        batch.cleanup()


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


def _state(path) -> dict | None:
    """What is at an install location: absent (None), a link, a folder's file digests, or a file digest."""
    path = Path(path)
    if not os.path.lexists(path):
        return None
    try:
        return {"link_target": content_digest.link_target(path)}
    except OSError:
        pass
    if path.is_dir():
        return {"files": content_digest.tree(path)}
    if path.is_file():
        return {"file": content_digest.file_digest(path)}
    return {"other": path.lstat().st_mode}


class Journal:
    """`rollbacks/<txid>/journal.json`: the targets one publish replaces and how far it got.

    `prepare` is written before anything changes, `done` after the files, the install
    record and the gateway coordination all finished, `restored` after a restore.
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

    def save(self) -> None:
        self.data["updated_at"] = _now()
        _write_json(self.folder / JOURNAL_FILENAME, self.data)

    def mark(self, phase: str) -> None:
        self.data["phase"] = phase
        self.save()

    @classmethod
    def begin(cls, request: dict) -> "Journal":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = rollbacks_root(request["ghost_root"]) / f"{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        folder.mkdir(parents=True)
        record = installed_payload.path(request["ghost_root"])
        copy = None
        if record.exists():
            shutil.copyfile(record, folder / installed_payload.FILENAME)
            copy = installed_payload.FILENAME
        journal = cls(folder, {
            "schema_version": JOURNAL_SCHEMA_VERSION, "txid": folder.name, "phase": "prepare",
            "host": request["host"], "created_at": _now(), "record_copy": copy,
            "targets": [{key: target[key] for key in ("name", "kind", "mode", "source", "dest")}
                        for target in request["targets"]]})
        journal.save()
        return journal

    @classmethod
    def load(cls, ghost_root, txid: str) -> "Journal":
        folder = rollbacks_root(ghost_root) / txid
        try:
            data = json.loads((folder / JOURNAL_FILENAME).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise InstallTransactionError(f"journal of publish {txid} cannot be read ({exc}); review {folder}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("targets", []), list):
            raise InstallTransactionError(f"journal of publish {txid} is not a journal; review {folder}")
        return cls(folder, data)


def _journal_phase(folder: Path):
    """The phase a publish folder's journal says; "absent" without a journal, None when it cannot be read."""
    file = folder / JOURNAL_FILENAME
    if not os.path.lexists(file):
        return "absent"
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data.get("phase") if isinstance(data, dict) else None


def unfinished(ghost_root) -> list[str]:
    """Transaction ids whose journal says neither done nor restored, newest first; an unreadable journal counts."""
    root = rollbacks_root(ghost_root)
    try:
        folders = [path for path in root.iterdir() if path.is_dir() and not path.is_symlink()]
    except OSError:
        return []
    return [folder.name for folder in sorted(folders, key=lambda path: path.name, reverse=True)
            if _journal_phase(folder) not in (*FINISHED_PHASES, "absent")]


def _prune_transactions(root: Path, keep: int) -> None:
    """Keep the newest `keep` finished publish folders; an unfinished one stays until it is restored."""
    try:
        folders = sorted((path for path in root.iterdir() if path.is_dir() and not path.is_symlink()),
                         key=lambda path: path.name)
    except OSError:
        return
    finished = [folder for folder in folders if _journal_phase(folder) in (*FINISHED_PHASES, "absent")]
    for folder in finished[: max(0, len(finished) - max(0, keep))]:
        try:
            shutil.rmtree(folder)
        except OSError:
            continue


def _record_key(dest) -> str:
    return Path(dest).as_posix()


class PublishBatch(ReplacementBatch):
    """The targets of one publish request.

    Each target is staged next to its install location; publishing moves the original
    into `rollbacks/<txid>/`, renames the staged copy or link into place and writes the
    targets' install record entries with the source, installer and transaction in the
    same step. Restoring puts back the files and the record a journal saved.
    """

    def __init__(self, request: dict, journal: Journal, *, rollback_keep=DEFAULT_ROLLBACK_KEEP,
                 progress_callback=None, progress_events=None):
        self.request, self.journal = request, journal
        self.ghost_root = Path(request["ghost_root"])
        progress = request.get("progress") or {}
        super().__init__([(target["source"], target["dest"], "symlink" if target["mode"] == "link" else "copy")
                          for target in request["targets"]],
                         rollback_root=journal.folder, rollback_keep=rollback_keep,
                         progress_callback=progress_callback, progress_event_file=progress.get("event_file"),
                         progress_events=progress_events)

    def stage(self):
        super().stage()
        for item, target in zip(self.prepared, self.journal.data["targets"]):
            target.update(stage=str(item["stage"]), rollback=str(item["rollback"]), before=_state(item["dest"]),
                          candidate=_state(item["stage"]))
        self.journal.save()

    def publish(self):
        super().publish()
        self._write_record()

    def _write_record(self):
        installed_at = _now()
        entries = {}
        for target, journaled in zip(self.request["targets"], self.journal.data["targets"]):
            entry = {key: target[key] for key in ("name", "kind", "mode", "provider", "source", "source_commit")}
            entry.update(used_by=[self.request["host"]], installed_at=installed_at, baseline="recorded")
            entry.update(journaled["candidate"])
            entries[_record_key(target["dest"])] = entry
        patches = {"targets": entries, "installer": dict(self.request["installer"]),
                   "source": dict(self.request["source"]),
                   "last_transaction": {"id": self.journal.txid, "updated_at": installed_at,
                                        "rollback_dir": str(self.journal.folder)}}
        try:
            installed_payload.update(self.ghost_root, patches)
        except installed_payload.RecordError as exc:
            # A record this installer cannot read is never overwritten; the published files stay.
            print(f"[WARN] Install record left unchanged: {exc}", file=sys.stderr)

    def restore(self):
        super().restore()
        installed_payload.restore(self.ghost_root, self.journal.record_copy())
        self.journal.mark("restored")

    def finalize(self):
        self.journal.mark("done")
        _prune_transactions(rollbacks_root(self.ghost_root), self.keep)


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
        if "status" in target and target["status"] not in TARGET_STATUSES:
            fail(f"target {target['name']} status must be one of {TARGET_STATUSES}")
    progress = request.get("progress", {})
    if not isinstance(progress, dict):
        fail("progress must be an object")
    if progress.get("event_file") and any(target.get("status") is None for target in targets):
        fail("every target needs a status when progress events are written")
    return request


def publish(request: dict, *, rollback_keep: int = DEFAULT_ROLLBACK_KEEP) -> str:
    """Publish the request's targets and their install record entries; return the transaction id.

    A failure anywhere restores every file and the record, says `restored` in the
    journal and raises InstallTransactionError with the cause's exit code. A restore
    that fails leaves the journal unfinished and names its folder.
    """
    request = _validated_request(request)
    progress = request.get("progress") or {}
    statuses = [target.get("status") for target in request["targets"]]
    callback = None
    if progress.get("label"):
        counted = statuses if all(statuses) else None
        _write_progress(progress["label"], 0, len(statuses), counted)

        def callback(done: int, total: int) -> None:
            _write_progress(progress["label"], done, total, counted)

    events = None
    if progress.get("event_file"):
        events = [{"platform": request["host"], "target_id": target["name"], "target_kind": target["kind"],
                   "status": target["status"]} for target in request["targets"]]
    journal = Journal.begin(request)
    batch = PublishBatch(request, journal, rollback_keep=rollback_keep, progress_callback=callback,
                         progress_events=events)
    try:
        batch.stage()
        from context_gateway_install import coordinate_payload_replacement
        coordinate_payload_replacement(batch)
        batch.finalize()
    except Exception as exc:
        wrapped = _wrapped_failure(exc, batch)
        try:
            batch.restore()
        except Exception as recovery:
            wrapped = _wrapped_failure(exc, batch, f"{wrapped}; restore failed ({recovery}); review {journal.folder}")
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
    current = _state(dest)
    if kept is not None and os.path.lexists(kept):
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


def recover(ghost_root, txid: str) -> list[str]:
    """Restore the publish `txid` from its journal unless the journal says done or restored.

    Every target is checked before any changes: the install location must still hold
    what the publish left there (or its original), and a moved original must still be
    kept. Then the originals return, the install record returns to its saved copy and
    the journal says `restored`. Returns one line per restored target.
    """
    journal = Journal.load(ghost_root, txid)
    if journal.phase in FINISHED_PHASES:
        return []
    actions = [action for action in (_recovery_action(journal.folder, target)
                                     for target in reversed(journal.data.get("targets", []))) if action]
    lines = []
    for action, dest, kept in actions:
        if action in ("replace", "remove"):
            _remove_path(dest)
        if action == "remove":
            lines.append(f"Removed {dest}; it did not exist before publish {txid}")
            continue
        _rename_path(kept, dest)
        lines.append(f"Restored {dest} from {kept}")
    installed_payload.restore(ghost_root, journal.record_copy())
    journal.mark("restored")
    for target in journal.data.get("targets", []):
        stage = target.get("stage")
        if stage and os.path.lexists(stage) and _state(stage) == target.get("candidate"):
            _remove_path(Path(stage))
    return lines


def staged_copy_replace(source, dest, *, rollback_root=None, event_log=None, rollback_keep=DEFAULT_ROLLBACK_KEEP):
    _replace([(source, dest, 'copy')], rollback_root=rollback_root, event_log=event_log, rollback_keep=rollback_keep)


def staged_symlink_replace(source, dest, *, rollback_root=None, event_log=None, rollback_keep=DEFAULT_ROLLBACK_KEEP):
    _replace([(source, dest, 'symlink')], rollback_root=rollback_root, event_log=event_log, rollback_keep=rollback_keep)


def staged_copy_replace_many(targets, *, rollback_root=None, event_log=None, rollback_keep=DEFAULT_ROLLBACK_KEEP,
        progress_callback=None, progress_event_file=None, progress_events=None):
    _replace([(src, dst, 'copy') for src, dst in targets], rollback_root=rollback_root, event_log=event_log,
        rollback_keep=rollback_keep, progress_callback=progress_callback,
        progress_event_file=progress_event_file, progress_events=progress_events)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ghost-ALICE installer transaction helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    copy_replace = subparsers.add_parser("copy-replace")
    copy_replace.add_argument("--source", required=True, type=Path)
    copy_replace.add_argument("--dest", required=True, type=Path)
    copy_replace.add_argument("--rollback-root", type=Path, default=None)
    copy_replace.add_argument("--event-log", type=Path, default=None)
    copy_replace.add_argument("--rollback-keep", type=int, default=DEFAULT_ROLLBACK_KEEP)

    symlink_replace = subparsers.add_parser("symlink-replace")
    symlink_replace.add_argument("--source", required=True, type=Path)
    symlink_replace.add_argument("--dest", required=True, type=Path)
    symlink_replace.add_argument("--rollback-root", type=Path, default=None)
    symlink_replace.add_argument("--event-log", type=Path, default=None)
    symlink_replace.add_argument("--rollback-keep", type=int, default=DEFAULT_ROLLBACK_KEEP)

    copy_replace_many = subparsers.add_parser("copy-replace-many")
    copy_replace_many.add_argument("--target", action="append", nargs=2, required=True, type=Path, metavar=("SOURCE", "DEST"))
    copy_replace_many.add_argument("--rollback-root", type=Path, default=None)
    copy_replace_many.add_argument("--event-log", type=Path, default=None)
    copy_replace_many.add_argument("--rollback-keep", type=int, default=DEFAULT_ROLLBACK_KEEP)
    copy_replace_many.add_argument("--progress-label", default=None)
    copy_replace_many.add_argument(
        "--progress-status",
        action="append",
        choices=("current", "updated", "new"),
        default=[],
        help="Per-target status for category-aware progress rendering.",
    )
    copy_replace_many.add_argument("--progress-event-file", type=Path, default=None)
    copy_replace_many.add_argument("--progress-platform", default=None)
    copy_replace_many.add_argument("--progress-target-id", action="append", default=[])
    copy_replace_many.add_argument("--progress-target-kind", action="append", choices=("skill", "support"), default=[])
    copy_replace_many.add_argument(
        "--progress-target-status",
        action="append",
        choices=("current", "updated", "new"),
        default=[],
    )

    publish_parser = subparsers.add_parser("publish", help="Publish the targets of a request file with their record.")
    publish_parser.add_argument("--request", required=True, type=Path)
    publish_parser.add_argument("--rollback-keep", type=int, default=DEFAULT_ROLLBACK_KEEP)

    recover_parser = subparsers.add_parser("recover", help="Restore an unfinished publish from its journal.")
    recover_parser.add_argument("--txid", required=True)
    recover_parser.add_argument("--ghost-root", type=Path, default=None)

    return parser.parse_args(argv)


def _write_progress(label: str, done: int, total: int, statuses: Sequence[str] | None = None) -> None:
    if statuses:
        counts = Counter(statuses[:done])
        sys.stdout.write(
            f"\r{label} "
            f"[{counts['current']}] [Current], "
            f"[{counts['updated']}] [updated], "
            f"[{counts['new']}] [newly added]"
        )
        sys.stdout.flush()
        return

    sys.stdout.write(f"\r{label} [{done}/{total}]")
    sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "copy-replace":
            staged_copy_replace(
                args.source,
                args.dest,
                rollback_root=args.rollback_root,
                event_log=args.event_log,
                rollback_keep=args.rollback_keep,
            )
        elif args.command == "symlink-replace":
            staged_symlink_replace(
                args.source,
                args.dest,
                rollback_root=args.rollback_root,
                event_log=args.event_log,
                rollback_keep=args.rollback_keep,
            )
        elif args.command == "copy-replace-many":
            progress_callback = None
            progress_events = None
            if args.progress_label:
                progress_statuses = list(getattr(args, "progress_status", []) or [])
                if progress_statuses and len(progress_statuses) != len(args.target):
                    print(
                        "--progress-status count must match --target count",
                        file=sys.stderr,
                    )
                    return 1
                statuses = progress_statuses or None
                _write_progress(args.progress_label, 0, len(args.target), statuses)

                def progress_callback(done: int, total: int) -> None:
                    _write_progress(args.progress_label, done, total, statuses)

            progress_event_file = getattr(args, "progress_event_file", None)
            progress_platform = getattr(args, "progress_platform", None)
            progress_target_ids = list(getattr(args, "progress_target_id", []) or [])
            progress_target_kinds = list(getattr(args, "progress_target_kind", []) or [])
            progress_target_statuses = list(getattr(args, "progress_target_status", []) or [])
            if progress_event_file is not None or progress_platform or progress_target_ids or progress_target_kinds or progress_target_statuses:
                if progress_event_file is None or not progress_platform:
                    print(
                        "--progress-event-file and --progress-platform are required with progress target metadata",
                        file=sys.stderr,
                    )
                    return 1
                if not (
                    len(progress_target_ids)
                    == len(progress_target_kinds)
                    == len(progress_target_statuses)
                    == len(args.target)
                ):
                    print(
                        "--progress-target-id/kind/status counts must match --target count",
                        file=sys.stderr,
                    )
                    return 1
                progress_events = [
                    {
                        "platform": progress_platform,
                        "target_id": target_id,
                        "target_kind": target_kind,
                        "status": target_status,
                    }
                    for target_id, target_kind, target_status in zip(
                        progress_target_ids,
                        progress_target_kinds,
                        progress_target_statuses,
                    )
                ]

            staged_copy_replace_many(
                args.target,
                rollback_root=args.rollback_root,
                event_log=args.event_log,
                progress_callback=progress_callback,
                progress_event_file=progress_event_file,
                progress_events=progress_events,
                rollback_keep=args.rollback_keep,
            )
            if args.progress_label:
                sys.stdout.write("\n")
        elif args.command == "publish":
            try:
                request = json.loads(args.request.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise InstallTransactionError(f"publish request {args.request} cannot be read: {exc}") from exc
            publish(request, rollback_keep=args.rollback_keep)
        elif args.command == "recover":
            for line in recover(args.ghost_root or Path.home() / ".ghost-alice", args.txid):
                print(line)
        else:
            raise AssertionError(args.command)
    except InstallTransactionError as exc:
        if getattr(args, "progress_label", None):
            sys.stdout.write("\n")
        if hasattr(exc, 'code'):
            print(json.dumps(dict(status=exc.outcome, error_code=exc.code, phase=exc.phase, detail=str(exc))), file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return getattr(exc, 'exit_code', 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
