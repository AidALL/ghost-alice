"""The fixed Git hooks folder, the shared `core.hooksPath` of the registered repositories and the values to restore.

`git-hooks` in the record root holds the post-merge script and `repositories.json`. `register` writes both and names
the folder in the repository's shared `core.hooksPath`. Each row of `repositories.json` names one repository and the
value of its `core.hooksPath` from before this installation. `rows` reads these rows for the registration and the
removal.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

import atomic_write
from installer import locations, report, runner, source

ROW_KIND = "source_repo_hook_path"
# An installation without the hooks folder keeps its rows in this record and in the host inventories beside it.
EARLIER_RECORD = "source-repo-hook-path.json"
# The value that such an installation can have written as a path of its own checkout.
EARLIER_RELATIVE_VALUE = "hooks"


def files() -> tuple[Path, Path]:
    """The script and the row file in the hooks folder."""
    folder = locations.git_hooks_dir()
    return folder / "post-merge", folder / "repositories.json"


def record_file() -> Path:
    """The file that `rows` reads first."""
    record = files()[1]
    return record if os.path.lexists(record) else locations.install_state_dir() / EARLIER_RECORD


def written_here(value: str) -> bool:
    """Whether `value` is an absolute path inside the record root. Only this installation writes such a value."""
    return os.path.isabs(value) and Path(os.path.realpath(value)).is_relative_to(
        os.path.realpath(locations.record_root()))


def previous_value(row: dict) -> str | None:
    """The value from before this installation, or None when there was none. A path of this installation counts as
    none."""
    before = row.get("before")
    return before if before is not None and not written_here(before) else None


def _complete(row: object) -> bool:
    """Whether `row` names the repository, the value that an installation wrote and the value to restore, or that
    there was none."""
    return (isinstance(row, dict) and row.get("kind") == ROW_KIND
            and all(isinstance(row.get(name), str) and row[name] for name in ("repo_root", "after"))
            and ("common_dir" not in row or isinstance(row["common_dir"], str) and os.path.isabs(row["common_dir"]))
            and type(row.get("before_present")) is bool
            and isinstance(row.get("before"), str if row["before_present"] else type(None)))


def _read_record(path: Path) -> list[dict]:
    """The rows of the row file, or the row of the earlier record."""
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(report.message("git_hooks_record_unreadable", path=path, cause=error)) from error
    found = [found] if path.name == EARLIER_RECORD else found
    if not isinstance(found, list) or not all(map(_complete, found)):
        raise ValueError(report.message("git_hooks_record_unreadable", path=path))
    return found


def _inventory_rows(path: Path) -> list[dict]:
    """The hook path rows of one host inventory. Other owners report an unreadable inventory."""
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    changes = inventory.get("system_env_changes") if isinstance(inventory, dict) else None
    return [row for row in changes if _complete(row)] if isinstance(changes, list) else []


def rows() -> list[dict]:
    """The rows of `repositories.json`. Without this file, the rows that the installation keeps in `install-state`."""
    record = record_file()
    found = _read_record(record) if os.path.lexists(record) else []
    if record == files()[1]:
        return found
    for path in locations.install_state_manifests(locations.install_state_dir()):
        found.extend(_inventory_rows(path))
    first: dict[tuple[str, str], dict] = {}
    for row in found:
        first.setdefault((row["repo_root"], row["after"]), row)
    return list(first.values())


def _common_dir(root: Path) -> Path | None:
    """The Git folder that all worktrees of the repository at `root` share, or None without a repository or Git."""
    found = source._git(root, "rev-parse", "--git-common-dir")
    if found.ok:
        return (root / found.stdout.removesuffix("\n")).resolve()
    if found.returncode == 127 or not os.path.lexists(root / ".git"):
        return None
    raise ValueError(report.message("git_hooks_repository_unreadable", root=root, cause=found.stderr.strip()))


def _same_repository(row: dict, common: Path) -> bool:
    """Whether `row` names the repository whose Git folder is `common`. Git follows a Git folder that moved."""
    place, options = (common, ("--git-dir", row["common_dir"])) if "common_dir" in row else (Path(row["repo_root"]), ())
    found = source._git(place, *options, "rev-parse", "--git-common-dir")
    return found.ok and source._same_folder(place / found.stdout.removesuffix("\n"), common)


def _git_config(config: Path, *args: str) -> runner.ToolResult:
    return runner.run_command(["git", "config", "--file", str(config), *args], capture=True)


def _failure(config: Path, cause: object) -> str:
    """The message for a Git command that could not read or write `config`."""
    lock = config.with_name("config.lock")
    if os.path.lexists(lock):
        return "\n".join((report.message("git_hooks_failed", config=config),
                          report.message("git_hooks_locked", lock=lock, cause=cause)))
    return report.message("git_hooks_failed", config=config, cause=cause)


def _current_value(config: Path) -> str | None:
    found = _git_config(config, "--get", "core.hooksPath")
    if found.ok:
        return found.stdout.removesuffix("\n")
    if found.returncode == 1:
        return None
    raise ValueError(_failure(config, found.stderr.strip()))


def _value_to_restore(current: str | None, fixed: str, common: Path, known: list[dict]) -> str | None:
    """The value of `core.hooksPath` from before this installation, or None when there was none.

    A row that names `current` as a value of this installation holds the value from before it. The fixed value and
    the relative value name no repository, so only a row of the same repository counts for them. A value of this
    installation without such a row takes the value of the repository's first row. A repository without a row can have
    moved: the rows of repositories that no longer exist count for it when they agree on the value. Any other value is
    the value to restore.
    """
    if current is None:
        return None
    own = [row for row in known if _same_repository(row, common)]
    matching = [row for row in (own if current in (fixed, EARLIER_RELATIVE_VALUE) else [*own, *known])
                if row["after"] == current]
    if matching:
        return previous_value(matching[0])
    if current != EARLIER_RELATIVE_VALUE and not written_here(current):
        return current
    if own:
        return previous_value(own[0])
    moved = {previous_value(row) for row in known
             if row["after"] == current and "common_dir" in row and not os.path.lexists(row["common_dir"])}
    return moved.pop() if len(moved) == 1 else None


def _saved(path: Path) -> tuple[bytes, int] | None:
    """The bytes and the mode of a regular file, or None without a file."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"Not a regular file: {path}")
    return path.read_bytes(), stat.S_IMODE(info.st_mode)


def _put_back(path: Path, saved: tuple[bytes, int] | None) -> None:
    if saved == _saved(path):
        return
    if saved is None:
        path.unlink()
        return
    atomic_write.write_bytes(path, saved[0])
    path.chmod(saved[1])


def register(root: Path) -> None:
    """Write the hooks folder and name it in the shared `core.hooksPath` of the repository at `root`.

    All worktrees of the repository share this value. A worktree's own value stays and comes first. The row with the
    value to restore is written before the Git value. A folder without a repository is skipped. When Git or the files
    cannot be written, the files and the Git value stay as they are and ValueError names the cause.
    """
    root = Path(root)
    common = _common_dir(root)
    if common is None:
        report.info(report.message("git_hooks_skipped", root=root))
        return
    config, folder = common / "config", locations.git_hooks_dir()
    hook, record = files()
    fixed = folder.resolve().as_posix()
    known = rows()
    registered = [row for row in known if "common_dir" in row and _same_repository(row, common)]
    current = _current_value(config)
    created = not os.path.lexists(folder)
    saved: dict[Path, tuple[bytes, int] | None] = {}
    try:
        if not created and not stat.S_ISDIR(folder.lstat().st_mode):
            raise OSError(f"Not a folder: {folder}")
        saved.update({path: _saved(path) for path in (hook, record)})
        script = (runner.REPO_ROOT / "hooks" / "post-merge").read_bytes()
        if saved[hook] is None or saved[hook][0] != script:
            atomic_write.write_bytes(hook, script)
        if saved[hook] is None or not saved[hook][1] & stat.S_IXUSR:
            hook.chmod(0o755)
        if registered and current == fixed:
            return
        before = _value_to_restore(current, fixed, common, known)
        row = {"kind": ROW_KIND, "repo_root": root.resolve().as_posix(), "common_dir": common.as_posix(),
               "before_present": before is not None, "before": before, "after": fixed,
               "applied_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        atomic_write.write_json(record, [row, *(other for other in known if other not in registered)])
        if current != fixed:
            written = _git_config(config, "core.hooksPath", fixed)
            if not written.ok:
                raise ValueError(_failure(config, written.stderr.strip()))
    except (OSError, ValueError) as error:
        causes = [str(error)]
        for path, content in saved.items():
            try:
                _put_back(path, content)
            except OSError as failure:
                causes.append(str(failure))
        if created:
            with contextlib.suppress(OSError):
                folder.rmdir()
        if len(causes) == 1 and isinstance(error, ValueError):
            raise
        raise ValueError(report.message("git_hooks_failed", config=config, cause="\n".join(causes))) from error
    if current != fixed:
        report.info(f"Git hook path → {fixed} (post-merge auto-refresh enabled)")
