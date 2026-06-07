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


def staged_copy_replace(
    source: Path | str,
    dest: Path | str,
    *,
    rollback_root: Path | str | None = None,
    event_log: Path | str | None = None,
    rollback_keep: int = DEFAULT_ROLLBACK_KEEP,
) -> None:
    """Copy source to a complete stage, then swap it into dest.

    Existing dest is untouched until source has been fully copied to stage.
    If the install move fails after dest was moved aside, this best-effort
    restores the previous dest before raising.
    """

    src = Path(source)
    dst = Path(dest)
    root = Path(rollback_root) if rollback_root is not None else dst.parent / ".ghost-alice-install-rollbacks"
    event_log_path = Path(event_log) if event_log is not None else None
    stage_root = dst.parent / ".ghost-alice-install-staging"
    stage = _unique_path(stage_root, "stage", dst.name)
    rollback = _unique_path(root, "rollback", dst.name)
    phase = "prepare"

    try:
        stage_root.mkdir(parents=True, exist_ok=True)
        root.mkdir(parents=True, exist_ok=True)
        phase = "stage-copy"
        _copy_to_stage(src, stage)
        phase = "stage-verify"
        _verify_staged_copy(src, stage)

        moved_existing = False
        try:
            phase = "publish"
            if dst.exists() or dst.is_symlink():
                rollback.parent.mkdir(parents=True, exist_ok=True)
                _rename_path(dst, rollback)
                moved_existing = True
            _rename_path(stage, dst)
            _prune_rollback_root(root, rollback_keep)
        except OSError as exc:
            if moved_existing and not (dst.exists() or dst.is_symlink()) and (rollback.exists() or rollback.is_symlink()):
                try:
                    _rename_path(rollback, dst)
                except OSError as rollback_exc:
                    phase = "rollback"
                    raise InstallTransactionError(
                        f"install move failed and rollback restore failed for {dst}"
                    ) from rollback_exc
            raise InstallTransactionError(f"install move failed for {dst}") from exc
    except InstallTransactionError as exc:
        _write_failure_event(event_log_path, source=src, dest=dst, phase=phase, error=str(exc))
        raise
    except OSError as exc:
        wrapped = InstallTransactionError(f"staged copy replace failed for {dst}")
        _write_failure_event(event_log_path, source=src, dest=dst, phase=phase, error=str(wrapped))
        raise wrapped from exc
    finally:
        if stage.exists() or stage.is_symlink():
            try:
                _remove_path(stage)
            except OSError:
                pass


def staged_symlink_replace(
    source: Path | str,
    dest: Path | str,
    *,
    rollback_root: Path | str | None = None,
    event_log: Path | str | None = None,
    rollback_keep: int = DEFAULT_ROLLBACK_KEEP,
) -> None:
    """Create a symlink dest -> source atomically.

    The existing dest is renamed aside into the rollback root before the staged
    symlink is moved into place, and restored if the publish fails. The previous
    dest is never removed without a rollback copy.
    """
    src = Path(source)
    dst = Path(dest)
    root = Path(rollback_root) if rollback_root is not None else dst.parent / ".ghost-alice-install-rollbacks"
    event_log_path = Path(event_log) if event_log is not None else None
    stage_root = dst.parent / ".ghost-alice-install-staging"
    stage = _unique_path(stage_root, "stage", dst.name)
    rollback = _unique_path(root, "rollback", dst.name)
    phase = "prepare"

    try:
        stage_root.mkdir(parents=True, exist_ok=True)
        root.mkdir(parents=True, exist_ok=True)
        phase = "stage-symlink"
        _create_directory_link(src, stage)

        moved_existing = False
        try:
            phase = "publish"
            if dst.exists() or dst.is_symlink():
                rollback.parent.mkdir(parents=True, exist_ok=True)
                _rename_path(dst, rollback)
                moved_existing = True
            _rename_path(stage, dst)
            _prune_rollback_root(root, rollback_keep)
        except OSError as exc:
            if moved_existing and not (dst.exists() or dst.is_symlink()) and (rollback.exists() or rollback.is_symlink()):
                try:
                    _rename_path(rollback, dst)
                except OSError as rollback_exc:
                    phase = "rollback"
                    raise InstallTransactionError(
                        f"symlink install failed and rollback restore failed for {dst}"
                    ) from rollback_exc
            raise InstallTransactionError(f"symlink install move failed for {dst}") from exc
    except InstallTransactionError as exc:
        _write_failure_event(event_log_path, source=src, dest=dst, phase=phase, error=str(exc))
        raise
    except OSError as exc:
        wrapped = InstallTransactionError(f"staged symlink replace failed for {dst}")
        _write_failure_event(event_log_path, source=src, dest=dst, phase=phase, error=str(wrapped))
        raise wrapped from exc
    finally:
        if stage.is_symlink() or stage.exists():
            try:
                _remove_path(stage)
            except OSError:
                pass


def staged_copy_replace_many(
    targets: Sequence[tuple[Path | str, Path | str]],
    *,
    rollback_root: Path | str | None = None,
    event_log: Path | str | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    progress_event_file: Path | str | None = None,
    progress_events: Sequence[dict[str, str]] | None = None,
    rollback_keep: int = DEFAULT_ROLLBACK_KEEP,
) -> None:
    """Stage every target first, then publish them as one install batch."""

    normalized = [(Path(source), Path(dest)) for source, dest in targets]
    if not normalized:
        return
    if progress_events is not None and len(progress_events) != len(normalized):
        raise InstallTransactionError("progress event count must match target count")

    root = Path(rollback_root) if rollback_root is not None else normalized[0][1].parent / ".ghost-alice-install-rollbacks"
    event_log_path = Path(event_log) if event_log is not None else None
    progress_event_path = Path(progress_event_file) if progress_event_file is not None else None
    prepared: list[dict[str, Path | bool]] = []
    phase = "prepare"
    event_source, event_dest = normalized[0]

    def rollback_prepared() -> None:
        rollback_error: OSError | None = None
        for item in reversed(prepared):
            dst = item["dest"]
            stage = item["stage"]
            rollback = item["rollback"]
            if not isinstance(dst, Path) or not isinstance(stage, Path) or not isinstance(rollback, Path):
                continue
            try:
                if item["published_new"] and (dst.exists() or dst.is_symlink()):
                    _remove_path(dst)
                if item["moved_existing"] and (rollback.exists() or rollback.is_symlink()):
                    if dst.exists() or dst.is_symlink():
                        raise OSError(f"destination still exists during rollback: {dst}")
                    _rename_path(rollback, dst)
            except OSError as exc:
                if rollback_error is None:
                    rollback_error = exc
        if rollback_error is not None:
            raise InstallTransactionError("multi-target rollback restore failed") from rollback_error

    try:
        root.mkdir(parents=True, exist_ok=True)
        for src, dst in normalized:
            event_source, event_dest = src, dst
            phase = "prepare"
            stage_root = dst.parent / ".ghost-alice-install-staging"
            stage = _unique_path(stage_root, "stage", dst.name)
            rollback = _unique_path(root, "rollback", dst.name)
            stage_root.mkdir(parents=True, exist_ok=True)
            prepared.append(
                {
                    "source": src,
                    "dest": dst,
                    "stage": stage,
                    "rollback": rollback,
                    "moved_existing": False,
                    "published_new": False,
                }
            )
            phase = "stage-copy"
            _copy_to_stage(src, stage)
            phase = "stage-verify"
            _verify_staged_copy(src, stage)

        phase = "publish"
        published_count = 0
        total_count = len(prepared)
        for item in prepared:
            src = item["source"]
            dst = item["dest"]
            stage = item["stage"]
            rollback = item["rollback"]
            if not isinstance(src, Path) or not isinstance(dst, Path) or not isinstance(stage, Path) or not isinstance(rollback, Path):
                raise InstallTransactionError("invalid staged copy target")
            event_source, event_dest = src, dst
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() or dst.is_symlink():
                rollback.parent.mkdir(parents=True, exist_ok=True)
                _rename_path(dst, rollback)
                item["moved_existing"] = True
            _rename_path(stage, dst)
            item["published_new"] = True
            published_count += 1
            if progress_events is not None:
                progress_event = progress_events[published_count - 1]
                _write_target_progress_event(
                    progress_event_path,
                    platform=progress_event["platform"],
                    target_id=progress_event["target_id"],
                    target_kind=progress_event["target_kind"],
                    status=progress_event["status"],
                )
            if progress_callback is not None:
                progress_callback(published_count, total_count)
        _prune_rollback_root(root, rollback_keep)
    except InstallTransactionError as exc:
        if phase == "publish":
            try:
                rollback_prepared()
            except InstallTransactionError as rollback_exc:
                phase = "rollback"
                exc = InstallTransactionError(f"install move failed and rollback restore failed for {event_dest}")
                _write_failure_event(event_log_path, source=event_source, dest=event_dest, phase=phase, error=str(exc))
                raise exc from rollback_exc
        _write_failure_event(event_log_path, source=event_source, dest=event_dest, phase=phase, error=str(exc))
        raise
    except OSError as exc:
        if phase == "publish":
            try:
                rollback_prepared()
            except InstallTransactionError as rollback_exc:
                wrapped = InstallTransactionError(f"install move failed and rollback restore failed for {event_dest}")
                _write_failure_event(event_log_path, source=event_source, dest=event_dest, phase="rollback", error=str(wrapped))
                raise wrapped from rollback_exc
            wrapped = InstallTransactionError(f"install move failed for {event_dest}")
        else:
            wrapped = InstallTransactionError(f"staged copy replace failed for {event_dest}")
        _write_failure_event(event_log_path, source=event_source, dest=event_dest, phase=phase, error=str(wrapped))
        raise wrapped from exc
    finally:
        for item in prepared:
            stage = item["stage"]
            if isinstance(stage, Path) and (stage.exists() or stage.is_symlink()):
                try:
                    _remove_path(stage)
                except OSError:
                    pass


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
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
