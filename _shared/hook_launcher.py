#!/usr/bin/env python3
"""The host-facing hook command: the launcher file, the record's `hooks` section and the launch.

A host's hook settings register one short command per Ghost-ALICE hook:

    "<ghost_root>/bin/hook-launcher" <hook-id> <platform> # <marker> [hook-runner:<hook-id>]

This module owns the two things that command points at. The launcher file
`<ghost_root>/bin/hook-launcher` (POSIX sh; `hook-launcher.cmd` on Windows) finds
Python 3.11+ the way the long registration did and runs this module from the runtime
`_shared` with the install record's path. The `hooks` section of the install record holds,
per host, the destination, the hook file and one entry per hook id (event, marker, the inner
command and its provider), plus the launcher's path, Python and digest.

`main` reads that section, binds the destination into the environment and hands the runner
`hook_profile_gate.main` the arguments the long registration used to carry, so the runner's
behaviour is unchanged. A registration the record does not carry ends with
`hook registration rejected` on stderr and exit 126, with a strict log row when the host's
destination is known; a usage error exits 1. Neither exit code blocks the host.

The installer writes the section through `write_registration` under the install lock: the
lock of the installer process when that process started this one, else one this process
takes and releases. `remove_registration` drops a host's section and, after the last host,
the launcher file.

Dependencies: Python standard library, `install_destination`, `installed_payload`,
`install_lock`, `content_digest`; `hook_profile_gate` only after the environment is bound.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import dataclasses
from datetime import datetime, timezone
import os
from pathlib import Path, PureWindowsPath
import re
import shlex
import sys
from typing import Iterator, NoReturn
import uuid

import content_digest
import install_destination
import install_lock
import installed_payload

LAUNCHER_NAME = "hook-launcher"
WINDOWS_LAUNCHER_NAME = "hook-launcher.cmd"
MODULE_NAME = "hook_launcher.py"
VALID_PLATFORMS = ("claude", "codex")
HOOK_ID_RE = re.compile(r"[a-z][a-z0-9-]*")
PYTHON_VERSION_CHECK_CODE = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
NO_PYTHON_MESSAGE = "Ghost-ALICE hook requires Python 3.11+"
# Where a POSIX host looks for Python 3.11+, in order; the launcher appends the install-time interpreter.
POSIX_PYTHON_CANDIDATES = (
    '"${GHOST_ALICE_PYTHON:-}" python3 python '
    "/opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 /bin/python3 "
    "/opt/homebrew/bin/python3.[0-9]* /usr/local/bin/python3.[0-9]* "
    "/usr/bin/python3.[0-9]* /bin/python3.[0-9]*"
)
INSTALL_PYTHON_PLACEHOLDER = "__GHOST_ALICE_INSTALL_PYTHON__"
# The one-line loop an older installer embedded in every registration, with a placeholder for the quoted
# install-time interpreter. `install_hooks` still reads it to recognize those registrations.
POSIX_HOOK_PYTHON_LAUNCHER = (
    f"for py in {POSIX_PYTHON_CANDIDATES} {INSTALL_PYTHON_PLACEHOLDER}; do "
    '[ -n "$py" ] || continue; '
    'if command -v "$py" >/dev/null 2>&1 || [ -x "$py" ]; then '
    f'"$py" -c "{PYTHON_VERSION_CHECK_CODE}" >/dev/null 2>&1 && exec "$py" "$@"; '
    "fi; "
    "done; "
    f'echo "{NO_PYTHON_MESSAGE}" >&2; exit 127'
)


class RegistrationError(RuntimeError):
    """A hook registration the record does not carry, or a record that cannot be read or written."""


@dataclasses.dataclass(frozen=True)
class Registration:
    """What a short registration names: the launcher file, the hook id and the platform."""
    launcher: Path
    hook_id: str
    platform: str


def is_windows() -> bool:
    return os.name == "nt"


def launcher_path(ghost_root: Path | str, *, windows: bool | None = None) -> Path:
    """`<ghost_root>/bin/hook-launcher`, or `hook-launcher.cmd` on Windows."""
    name = WINDOWS_LAUNCHER_NAME if (is_windows() if windows is None else windows) else LAUNCHER_NAME
    return Path(ghost_root) / "bin" / name


def _quote_command_path(value: Path | str) -> str:
    return '"' + str(value).replace("\\", "/").replace('"', '\\"') + '"'


def _quote_posix(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def registration_command(launcher: Path | str, hook_id: str, platform: str, marker: str) -> str:
    """The command a host registers for `hook_id`: the launcher, the id, the platform and the ownership comment."""
    return f"{_quote_command_path(launcher)} {hook_id} {platform} # {marker} [hook-runner:{hook_id}]"


def parse_registration(command: str) -> Registration | None:
    """The launcher, hook id and platform of a short registration; None for any other command."""
    try:
        argv = shlex.split(command, comments=True)
    except ValueError:
        return None
    if len(argv) != 3 or not HOOK_ID_RE.fullmatch(argv[1]) or argv[2] not in VALID_PLATFORMS:
        return None
    launcher = Path(argv[0])
    absolute = launcher.is_absolute() or PureWindowsPath(argv[0]).is_absolute()
    if not absolute or launcher.name not in (LAUNCHER_NAME, WINDOWS_LAUNCHER_NAME):
        return None
    return Registration(launcher=launcher, hook_id=argv[1], platform=argv[2])


def launcher_text(*, install_python: str, module: Path | str, record: Path | str, windows: bool | None = None) -> str:
    """The launcher file: the Python discovery of the long registration, then this module with the record."""
    if is_windows() if windows is None else windows:
        return (f"@{_quote_command_path(install_python)} {_quote_command_path(module)} "
                f"{_quote_command_path(record)} %*\r\n")
    exec_args = f"{_quote_posix(Path(module).as_posix())} {_quote_posix(Path(record).as_posix())}"
    return (
        "#!/bin/sh\n"
        "# Ghost-ALICE hook launcher. The installer writes this file; the install record names it.\n"
        f"for py in {POSIX_PYTHON_CANDIDATES} {_quote_posix(str(install_python))}; do\n"
        '  [ -n "$py" ] || continue\n'
        '  if command -v "$py" >/dev/null 2>&1 || [ -x "$py" ]; then\n'
        f'    "$py" -c "{PYTHON_VERSION_CHECK_CODE}" >/dev/null 2>&1 && exec "$py" {exec_args} "$@"\n'
        "  fi\n"
        "done\n"
        f'echo "{NO_PYTHON_MESSAGE}" >&2\n'
        "exit 127\n"
    )


# The record's `hooks` section.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def host_section(*, hook_file: Path | str, destination, entries: dict, registered_at: str | None = None) -> dict:
    """`hooks.<platform>`: the hook file, the destination and one entry per hook id."""
    resolved = install_destination.InstallDestination.parse(destination)
    return {"hook_file": str(hook_file), "destination": dataclasses.asdict(resolved),
            "entries": {hook_id: dict(entry) for hook_id, entry in entries.items()},
            "registered_at": registered_at or _now()}


@contextmanager
def _record_lock(ghost_root: Path, owner: str) -> Iterator[None]:
    """The install lock: inherited from the installer process that started this one, else taken here."""
    lock = ghost_root / installed_payload.LOCK_FILENAME
    try:
        held = install_lock.held_by_process_tree(lock)
    except install_lock.InstallLockError as error:
        raise RegistrationError(f"the install lock cannot be read: {error}") from error
    if held:
        yield
        return
    try:
        install_lock.acquire_lock(lock, owner=owner)
    except install_lock.InstallLockError as error:
        raise RegistrationError(f"another installer holds the install lock: {error}") from error
    try:
        yield
    finally:
        install_lock.release_lock(lock)


def _read(ghost_root: Path) -> dict | None:
    try:
        return installed_payload.read(ghost_root)
    except installed_payload.RecordError as error:
        raise RegistrationError(str(error)) from error


def _update(ghost_root: Path, patch: dict) -> None:
    try:
        installed_payload.update(ghost_root, {"hooks": patch})
    except (installed_payload.RecordError, installed_payload.NotLockHolder, OSError) as error:
        raise RegistrationError(f"the install record cannot be written: {error}") from error


def _write_file(path: Path, data: bytes, *, executable: bool) -> None:
    install_destination.assert_mutation_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if executable:
            os.chmod(temporary, 0o755)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _same(current: dict, wanted: dict, *, ignore: str) -> bool:
    return ({key: value for key, value in current.items() if key != ignore}
            == {key: value for key, value in wanted.items() if key != ignore})


def _merged_entries(recorded: dict | None, entries: dict) -> dict:
    """`entries` replace the recorded entries of the same ids; an addon entry the host still carries stays."""
    kept = {hook_id: entry for hook_id, entry in (recorded or {}).items()
            if hook_id not in entries and isinstance(entry, dict) and str(entry.get("provider", "")).startswith("addon:")}
    return {**kept, **{hook_id: dict(entry) for hook_id, entry in entries.items()}}


def write_registration(ghost_root: Path | str, platform: str, *, hook_file: Path | str, destination, entries: dict,
                       runtime_shared_dir: Path | str, install_python: str, windows: bool | None = None) -> bool:
    """Write the launcher file and the record's `hooks.<platform>` and `hooks.launcher`; True when either changed.

    The launcher is rewritten only when its text differs. Core entries replace the host's recorded core entries;
    an addon entry the host still carries stays unless `entries` names it. RegistrationError when the record is
    held by another process or cannot be read or written; the host file is not this function's to touch.
    """
    root = Path(ghost_root)
    resolved = install_destination.InstallDestination.parse(destination)
    launcher = launcher_path(root, windows=windows)
    text = launcher_text(install_python=str(install_python), module=Path(runtime_shared_dir) / MODULE_NAME,
                         record=installed_payload.path(root), windows=windows).encode("utf-8")
    with _record_lock(root, f"install_hooks:{platform}:{os.getpid()}"):
        hooks = (_read(root) or {}).get("hooks") or {}
        changed = False
        if not launcher.is_file() or launcher.read_bytes() != text:
            _write_file(launcher, text, executable=not (is_windows() if windows is None else windows))
            changed = True
        wanted_launcher = {"path": str(launcher), "python": str(install_python),
                           "digest": content_digest.file_digest(launcher)}
        wanted_host = host_section(hook_file=hook_file, destination=resolved,
                                   entries=_merged_entries((hooks.get(platform) or {}).get("entries"), entries))
        patch: dict = {}
        if not _same(hooks.get("launcher") or {}, wanted_launcher, ignore="written_at"):
            patch["launcher"] = {**wanted_launcher, "written_at": _now()}
        if not _same(hooks.get(platform) or {}, wanted_host, ignore="registered_at"):
            patch[platform] = wanted_host
        if patch:
            _update(root, patch)
            changed = True
    return changed


def remove_entries(ghost_root: Path | str, platform: str, hook_ids) -> int:
    """Drop the named hook ids from `hooks.<platform>.entries`; the number dropped."""
    root = Path(ghost_root)
    with _record_lock(root, f"install_hooks:{platform}:{os.getpid()}"):
        hooks = (_read(root) or {}).get("hooks") or {}
        section = hooks.get(platform)
        if not isinstance(section, dict):
            return 0
        entries = dict(section.get("entries") or {})
        dropped = [hook_id for hook_id in hook_ids if entries.pop(hook_id, None) is not None]
        if dropped:
            _update(root, {platform: {**section, "entries": entries}})
        return len(dropped)


def remove_registration(ghost_root: Path | str, platform: str) -> dict:
    """Drop `hooks.<platform>`; after the last host also `hooks.launcher` and the launcher file.

    Returns `{"section": <dropped>, "launcher": <file removed>}`.
    """
    root = Path(ghost_root)
    with _record_lock(root, f"install_hooks:{platform}:{os.getpid()}"):
        hooks = (_read(root) or {}).get("hooks") or {}
        result = {"section": platform in hooks, "launcher": False}
        patch: dict = {platform: None} if platform in hooks else {}
        if hooks and not any(key not in (platform, "launcher") for key in hooks):
            recorded = hooks.get("launcher") or {}
            candidates = {Path(recorded["path"])} if isinstance(recorded.get("path"), str) else set()
            candidates |= {launcher_path(root, windows=False), launcher_path(root, windows=True)}
            for candidate in candidates:
                if candidate.is_file():
                    install_destination.assert_mutation_path(candidate)
                    candidate.unlink()
                    result["launcher"] = True
            if "launcher" in hooks:
                patch["launcher"] = None
        if patch:
            _update(root, patch)
    return result


def recorded_destination(ghost_root: Path | str, platform: str) -> dict | None:
    """`hooks.<platform>.destination` of the record, or None without one or when the record cannot be read."""
    try:
        record = installed_payload.read(ghost_root)
    except installed_payload.RecordError:
        return None
    section = ((record or {}).get("hooks") or {}).get(platform)
    destination = section.get("destination") if isinstance(section, dict) else None
    return dict(destination) if isinstance(destination, dict) else None


def registration_status(ghost_root: Path | str, platform: str, *, hook_file: Path | str, destination,
                        entries: dict) -> tuple[bool, bool]:
    """`(present, ok)`: whether the record has `hooks.<platform>`, and whether it and the launcher file match
    what an install with these arguments writes. Addon entries the record carries beyond `entries` are not drift."""
    try:
        record = installed_payload.read(ghost_root)
    except installed_payload.RecordError:
        return False, False
    hooks = (record or {}).get("hooks") or {}
    section = hooks.get(platform)
    if not isinstance(section, dict):
        return False, False
    resolved = install_destination.InstallDestination.parse(destination)
    recorded = section.get("entries") or {}
    ok = (section.get("hook_file") == str(hook_file)
          and section.get("destination") == dataclasses.asdict(resolved)
          and all(recorded.get(hook_id) == entry for hook_id, entry in entries.items()))
    launcher = hooks.get("launcher")
    if ok:
        path = Path(launcher["path"]) if isinstance(launcher, dict) and isinstance(launcher.get("path"), str) else None
        try:
            ok = path is not None and path.is_file() and content_digest.file_digest(path) == launcher.get("digest")
        except OSError:
            ok = False
    return True, ok


# The launch.


def read_registration(record_path: Path | str, platform: str, hook_id: str) -> tuple[dict, dict]:
    """The destination and the entry the record registers for `hook_id` on `platform`."""
    record_path = Path(record_path)
    if record_path.name != installed_payload.FILENAME:
        raise RegistrationError(f"not an install record: {record_path}")
    try:
        record = installed_payload.read(record_path.parent)
    except installed_payload.RecordError as error:
        raise RegistrationError(str(error)) from error
    if record is None:
        raise RegistrationError(f"no install record at {record_path}")
    hooks = record.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        raise RegistrationError("the install record has no hook registrations")
    section = hooks.get(platform)
    if not isinstance(section, dict):
        raise RegistrationError(f"no {platform} hook registration")
    try:
        destination = dataclasses.asdict(install_destination.InstallDestination.parse(section.get("destination")))
    except ValueError as error:
        raise RegistrationError(str(error)) from error
    entries = section.get("entries")
    entry = entries.get(hook_id) if isinstance(entries, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("command"), str) or not entry["command"].strip():
        raise RegistrationError(f"no {platform} registration for hook {hook_id}")
    return destination, dict(entry)


def _audit_rejection(record_path: Path, platform: str, hook_id: str, line: str) -> None:
    """The runner's rejection row, when the host's destination in the record is valid; otherwise nothing."""
    try:
        record = installed_payload.read(record_path.parent) if record_path.name == installed_payload.FILENAME else None
        section = ((record or {}).get("hooks") or {}).get(platform)
        destination = install_destination.InstallDestination.parse(section.get("destination") if isinstance(section, dict) else None)
    except (installed_payload.RecordError, ValueError, OSError):
        return
    try:
        os.environ.update(install_destination.binding_environment(destination, os.environ))
        import hook_profile_gate
        with install_destination.use(destination):
            hook_profile_gate._append_rejection_audit(hook_id, line, platform=platform)
    except Exception as error:  # noqa: BLE001 - the audit is best-effort, as in the runner.
        try:
            sys.stderr.write(f"hook rejection audit failed: {error}\n")
        except Exception:  # noqa: BLE001 - diagnostics never mask the rejection.
            pass


def _without_comment(args: list[str]) -> list[str]:
    """A Windows host passes the `# <marker> [hook-runner:<id>]` comment through as arguments."""
    return args[:args.index("#")] if "#" in args else args


def main(argv: list[str] | None = None) -> NoReturn:
    args = _without_comment(list(sys.argv[1:] if argv is None else argv))
    if len(args) != 3 or not HOOK_ID_RE.fullmatch(args[1]) or args[2] not in VALID_PLATFORMS:
        sys.stderr.write(f"usage: {MODULE_NAME} <record-path> <hook-id> <platform>\n")
        # A malformed registration is Ghost-ALICE's own failure; exit 2 would block the host.
        raise SystemExit(1)
    record_path, hook_id, platform = Path(args[0]), args[1], args[2]
    try:
        destination, entry = read_registration(record_path, platform, hook_id)
    except RegistrationError as error:
        line = f"hook registration rejected: {error}\n"
        sys.stderr.write(line)
        _audit_rejection(record_path, platform, hook_id, line)
        raise SystemExit(126)
    # Bind the destination before the runner's source-provider imports, as the long registration's early decode did.
    os.environ.update(install_destination.binding_environment(destination, os.environ))
    import hook_profile_gate
    payload = base64.urlsafe_b64encode(entry["command"].encode("utf-8")).decode("ascii")
    binding = install_destination.encode_binding(install_destination.InstallDestination.parse(destination).binding())
    hook_profile_gate.main(["run", hook_id, platform, payload, "--binding-b64", binding])
    raise SystemExit(0)


if __name__ == "__main__":
    main()
