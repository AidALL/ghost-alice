"""Replace one file so its readers see the old or the new contents, never a part.

`write_bytes` writes a temporary file beside the file it replaces, flushes it to disk and renames it over that file;
`write_json` writes a value with a two-space indent, unescaped text and a final newline. Both check the path with
`install_destination.assert_mutation_path` before anything is created, create the parent folder, keep the mode of the
file they replace and, when the path is a symlink, replace the file the link resolves to and leave the link in place.
The path check follows the active install destination when one is set; otherwise it reads `GHOST_ALICE_OWNED_ROOT`
from the caller's `env` when given and from the process environment otherwise. A new file keeps the owner-only mode of
its temporary file. `sync_folder=True` also flushes the folder entry after the rename, so the new name survives a
power loss; Windows cannot flush a folder.

Dependencies: Python 3.11+ standard library and `install_destination`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping

import install_destination


def link_target(path: Path | str) -> Path:
    """The file the symlink at `path` fully resolves to."""
    path = Path(path)
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise OSError(f"Cannot resolve atomic write symlink {path}: {error}") from error


def destination(path: Path | str, *, env: Mapping[str, str] | None = None) -> Path:
    """The file a write to `path` replaces, after checking it and creating the parent folder of `path`.

    An existing symlink remains in place: its fully resolved target is replaced in the target directory. A broken
    link follows direct-write behavior only when its resolved target parent already exists; otherwise it fails
    without changing the link.
    """
    path = Path(path)
    install_destination.assert_mutation_path(path, env=env)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_symlink():
        return path
    target = link_target(path)
    install_destination.assert_mutation_path(target, env=env)
    if not target.parent.is_dir():
        raise FileNotFoundError(
            f"Cannot write through broken symlink {path}: "
            f"target parent does not exist: {target.parent}"
        )
    return target


def write_bytes(path: Path | str, data: bytes, *, sync_folder: bool = False,
                env: Mapping[str, str] | None = None) -> None:
    target = destination(path, env=env)
    try:
        existing_mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        existing_mode = None

    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temp_path, existing_mode)
        os.replace(temp_path, target)
    except BaseException as primary_error:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError as close_error:
                primary_error.add_note(
                    f"Failed to close temporary file descriptor for {temp_path}: "
                    f"{close_error}"
                )
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup_error:
            primary_error.add_note(
                f"Failed to clean up temporary file {temp_path}: {cleanup_error}"
            )
        raise
    if sync_folder and os.name != "nt":
        folder = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def write_json(path: Path | str, value: Any, *, sort_keys: bool = False, sync_folder: bool = False,
               env: Mapping[str, str] | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n"
    write_bytes(path, text.encode("utf-8"), sync_folder=sync_folder, env=env)
