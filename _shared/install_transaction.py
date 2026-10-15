#!/usr/bin/env python3
"""Transactional installer filesystem operations."""

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
        wrapped = exc if isinstance(exc, InstallTransactionError) else InstallTransactionError(
            'staged replacement failed for ' + str(batch.dest) + ': ' + str(exc))
        for name in ('code', 'exit_code', 'outcome'):
            if hasattr(exc, name):
                setattr(wrapped, name, getattr(exc, name))
        wrapped.phase = getattr(exc, 'phase', batch.phase)
        _write_failure_event(Path(event_log) if event_log is not None else None,
            source=batch.source, dest=batch.dest, phase=wrapped.phase, error=str(wrapped))
        raise wrapped from exc
    finally:
        batch.cleanup()


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
