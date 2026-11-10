"""Retention of the files Ghost-ALICE writes while it runs.

`POLICIES` names every file and folder a Core installation creates under the Ghost-ALICE state root and the host
folders, and how each stays bounded. A writer calls `apply(name, path)` at its own moment for an entry that removes
something: size rotation, keep the newest N, age or line count. A log that several processes append to is written
through `append(name, path, text)`. Overwritten files and material kept for the user are named only. `apply` never
raises: a failure is one line in `state-retention-failures.jsonl` under the Ghost-ALICE state root, which the
`retention-failures` entry bounds and doctor reads. `append` records a failed write there too. `entry_for` names the
entry a created path belongs to, so a test can require every created path to have one.

Dependencies: Python 3.11+ standard library and the Core helpers `atomic_write`, `file_lock`, `install_destination`,
`text_lines` and `installed_payload` in `_shared`. When `file_lock` takes no lock (Windows, or a file system that refuses
`flock`), appends go on and the rule still applies.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Callable, Iterable, Mapping, NamedTuple

import atomic_write
import file_lock
import install_destination
from text_lines import committed_length, split_lines

MB = 1024 * 1024
DAY = 86400
METHODS = ("size-rotation", "keep-newest", "age", "line-count", "overwrite", "user-kept")
FAILURES = "state-retention-failures.jsonl"


class Policy(NamedTuple):
    """One entry of the retention table.

    `patterns` place the entry under the roots of `default_roots` (`{ghost}/io-trace.jsonl`); `*` matches within one
    folder name and `**` spans folders. `target` is the name the path given to `apply` must have. `limit` is bytes
    for a size rotation, entries for keep-newest, days for age and lines for a line count.
    """

    name: str
    method: str
    patterns: tuple[str, ...]
    target: str | None = None
    limit: int | None = None
    keep: int | None = None        # line-count: the lines left after a trim
    per_run: int | None = None     # age: the most folders one call removes
    order: str = "name"            # keep-newest: "name" (names carry the time) or "mtime"
    group: str | None = None       # keep-newest: regex whose `key` joins the entries of one run
    members: str = "*"             # keep-newest: entries counted, `{name}` is the name of the given path
    siblings: bool = False         # keep-newest: count entries beside the given path instead of inside it
    files_only: bool = False
    folders_only: bool = False
    skip_links: bool = False
    copy_truncate: bool = False    # size-rotation of a file another process holds open in append mode
    marker: str | None = None      # age: the file whose modification time dates its folder


_TABLE = (
    Policy("session-logs", "age", ("{ghost}/session-logs/*/*/**",), target="session-logs", limit=14, per_run=50,
           marker="strict-hook-output.jsonl"),
    Policy("gateway-usage", "size-rotation", ("{gateway}/usage.jsonl", "{gateway}/usage.jsonl.1"),
           target="usage.jsonl", limit=20 * MB),
    Policy("gateway-service-log", "size-rotation", ("{gateway}/service.log", "{gateway}/service.log.1"),
           target="service.log", limit=5 * MB, copy_truncate=True),
    Policy("gateway-config-backups", "keep-newest", ("{gateway}/backups/*", "{gateway}/backups/*/**"),
           target="backups", limit=5, group=r"^(?P<key>\d{8}T\d{6}\d*Z)-"),
    Policy("install-logs", "keep-newest", ("{record}/install/*.log", "{record}/install/*.events.jsonl"),
           target="install", limit=30,
           group=r"^(?P<key>\d{4}-\d{2}-\d{2}-\d{6}(?:-[0-9a-f]{32})?)\.(?:log|events\.jsonl)$",
           files_only=True),
    Policy("uninstall-reports", "keep-newest", ("{record}/uninstall-reports/*.json",), target="uninstall-reports",
           limit=30, order="mtime", members="*.json", files_only=True),
    Policy("install-record", "overwrite",
           ("{record}/installed-payload.json", "{record}/install.lock", "{record}/install.lock.guard",
            "{record}/install-state/*.json", "{record}/install-state/source-repo-hook-path.json.guard")),
    Policy("install-state-events", "size-rotation",
           ("{record}/install-state/*-events.jsonl", "{record}/install-state/*-events.jsonl.1"),
           target="*-events.jsonl", limit=5 * MB),
    Policy("retention-failures", "line-count", ("{ghost}/" + FAILURES,), target=FAILURES, limit=500, keep=500),
    Policy("io-trace", "line-count", ("{ghost}/io-trace.jsonl",), target="io-trace.jsonl", limit=10_000,
           keep=5_000),
    Policy("publish-rollbacks", "keep-newest", ("{record}/rollbacks/*", "{record}/rollbacks/*/**"),
           target="rollbacks", limit=20, folders_only=True),
    Policy("hook-settings-backups", "keep-newest", ("{claude}/settings.json.bak*", "{codex}/hooks.json.bak*"),
           target="*.json", limit=3, order="mtime", members="{name}.bak*", siblings=True, files_only=True,
           skip_links=True),
    Policy("uninstall-backup", "user-kept", ("{record}/uninstall-backup/*/support-state/**",)),
    Policy("uninstall-quarantine", "overwrite",
           ("{record}/uninstall-backup/*/*", "{record}/uninstall-backup/*/*/**")),
    Policy("runtime-config", "overwrite", ("{ghost}/config.json",)),
    Policy("hook-assets", "overwrite", ("{ghost}/hooks/*",)),
    Policy("runtime-code", "overwrite",
           ("{record}/runtime/current/_shared/**",
            "{record}/runtime/current/session-intent-analyzer/scripts/[!.]*",
            "{record}/runtime/current/session-intent-analyzer/scripts/[!.]*/**")),
    Policy("installed-skills", "overwrite",
           ("{claude_skills}/[!.]*", "{claude_skills}/[!.]*/**", "{codex_skills}/[!.]*",
            "{codex_skills}/[!.]*/**")),
    Policy("host-settings", "overwrite",
           ("{claude}/settings.json", "{claude}/settings.json.corrupt-bak", "{codex}/hooks.json",
            "{codex}/hooks.json.corrupt-bak", "{codex}/config.toml", "{codex}/config.toml.bak")),
    Policy("managed-instructions", "overwrite",
           ("{claude}/CLAUDE.md", "{claude}/CLAUDE.md.ghost-alice-proposed", "{codex}/AGENTS.md",
            "{codex}/AGENTS.md.ghost-alice-proposed", "{codex}/ghost-alice-governance.md")),
    Policy("preserved-install-records", "user-kept",
           ("{record}/install-state/*.json.*.bak", "{ghost}/addon-permissions/*.bak",
            "{claude}/CLAUDE.md.ghost-alice-proposed.*", "{codex}/AGENTS.md.ghost-alice-proposed.*")),
    Policy("pending-merges-state", "overwrite",
           ("{record}/pending-merges/*/manifest.json", "{record}/pending-merges/*/manifest.json.lock",
            "{record}/pending-merges/*/manifest.json.corrupt-bak", "{record}/pending-merges/*/snapshot.json",
            "{record}/pending-merges/*/READ-ME-FIRST.md")),
    Policy("pending-merge-copies", "user-kept",
           ("{record}/pending-merges/*/*.bak", "{record}/pending-merges/*/legacy-targets/**")),
    Policy("retired-assets", "user-kept",
           ("{record}/deprecated-skill-backups/**", "{claude}/retired-command-backups/**")),
    Policy("addon-state", "overwrite",
           ("{record}/addons/*/*.json", "{record}/addons/*/*.json.removing", "{ghost}/addon-permissions/*.json",
            "{ghost}/addon-resume.log", "{record}/resources/**", "{claude}/commands/*.md")),
    Policy("addon-source-cache", "overwrite", ("{record}/addon-source-cache/*", "{record}/addon-source-cache/*/**")),
    Policy("live-smoke", "user-kept", ("{record}/live-smoke/**",)),
    Policy("secrets", "user-kept", ("{ghost}/secrets.env",)),
    Policy("gateway-registration", "overwrite",
           ("{gateway}/state.json", "{launch_agents}/org.ghost-alice.context-gateway.plist",
            "{gateway}/installation.json", "{gateway}/installed-files.json",
            "{record}/context-gateway-binding.json")),
)
POLICIES: dict[str, Policy] = {policy.name: policy for policy in _TABLE}


def default_roots(service: Mapping[str, str] | None = None) -> dict[str, Path]:
    """The roots `POLICIES` patterns name, as the process environment places them: the Ghost-ALICE state root from
    `install_destination`, the record root, host homes and skill folders from the installer's `installer.locations`,
    and the gateway state and launch agent folders from the `state_path` and `launch_agent` of `service`, or of the
    install record's `service` section when `service` is None. An installation without a registered gateway has no
    gateway roots, and `entry_for` skips the patterns under them. An install record that cannot be read leaves out
    only the gateway roots and adds one line to the failure record."""
    from installer import locations
    import installed_payload

    roots = {
        "ghost": Path(install_destination.ghost_root()),
        "record": locations.record_root(),
        "claude": locations.claude_home(),
        "codex": locations.codex_home(),
        "claude_skills": locations.skills_dir("claude"),
        "codex_skills": locations.skills_dir("codex"),
    }
    if service is None:
        try:
            service = (installed_payload.read(roots["record"]) or {}).get("service")
        except installed_payload.RecordError as exc:
            _record_failure("install-record", installed_payload.path(roots["record"]), type(exc).__name__, str(exc))
            service = None
    if service:
        roots["gateway"] = Path(service["state_path"]).parent
        roots["launch_agents"] = Path(service["launch_agent"]).parent
    return roots


def entry_for(path: Path | str, roots: dict[str, Path], *, directory: bool = False) -> str | None:
    """The name of the first entry a pattern of which matches `path`; an empty `directory` also matches when an
    entry's pattern lies under it. None when no entry names the path. A pattern under a root `roots` lacks never
    matches."""
    parts = Path(path).parts
    for policy in POLICIES.values():
        for template in policy.patterns:
            root, _, rest = template.partition("/")
            if root.strip("{}") not in roots:
                continue
            pattern = (*roots[root.strip("{}")].parts, *rest.split("/"))
            if _matches(parts, pattern, directory):
                return policy.name
    return None


def _matches(parts: tuple[str, ...], pattern: tuple[str, ...], prefix: bool) -> bool:
    if not pattern:
        return not parts
    if not parts:
        return prefix or all(segment == "**" for segment in pattern)
    if pattern[0] == "**":
        return _matches(parts, pattern[1:], prefix) or _matches(parts[1:], pattern, prefix)
    return fnmatch.fnmatchcase(parts[0], pattern[0]) and _matches(parts[1:], pattern[1:], prefix)


def apply(name: str, path: Path | str, *, protect: Iterable[Path | str] = (),
          eligible: Callable[[Path], bool] | None = None, limit: int | None = None) -> tuple[Path, ...]:
    """Apply the entry `name` to `path` and return what changed; never raises.

    `protect` names entries never removed, or folders whose contents are never removed. `eligible` lets the
    writer exclude entries it still needs from a keep-newest count. `limit` replaces the table's limit for a caller
    that takes one explicitly.
    """
    policy = POLICIES.get(name)
    path = Path(path)
    if policy is None:
        _record_failure(name, path, "UnknownPolicy", "no retention entry has this name")
        return ()
    if policy.target is not None and not fnmatch.fnmatchcase(path.name, policy.target):
        _record_failure(name, path, "PathMismatch", f"the entry applies to {policy.target}, not {path.name}")
        return ()
    if limit is not None:
        policy = policy._replace(limit=max(0, limit))
    try:
        if policy.method == "size-rotation":
            return _size_rotation(path, policy)
        if policy.method == "line-count":
            return _line_count(path, policy)
        if policy.method == "keep-newest":
            return _keep_newest(path, policy, _protected_forms(protect), eligible)
        if policy.method == "age":
            return _age(path, policy, _protected_forms(protect))
        return ()
    except Exception as error:  # a retention failure never reaches the writer
        _record_failure(name, path, type(error).__name__, str(error))
        return ()


def append(name: str, path: Path | str, text: str, *, apply_rule: bool = True, must_write: bool = False) -> None:
    """Append `text` to the log `path` under the log's lock and, unless `apply_rule` is False, apply the entry `name`
    there too. When the lock stays held past `file_lock.LOCK_WAIT_S`, the text is appended without the lock or the rule
    and the wait is recorded under `name`. A write that fails loses the line and is recorded under `name`; only a caller
    that sets `must_write` gets the error instead, to undo the work the line describes. The entry's failures are
    recorded and never raised. A path outside the owned root always raises."""
    path = Path(path)
    install_destination.assert_mutation_path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with file_lock.exclusive(path):
                _append_once(path, text)
                if apply_rule:
                    apply(name, path)
        except file_lock.LockTimeout as timeout:
            _append_once(path, text)
            skipped = "the rule" if apply_rule else "the lock"
            _record_failure(name, path, type(timeout).__name__,
                            f"lock held past {file_lock.LOCK_WAIT_S}s; appended without {skipped}")
    except Exception as error:
        if must_write:
            raise
        _record_failure(name, path, type(error).__name__, f"{error}; the line was not written in full")


def _append_once(path: Path, text: str) -> None:
    """One append-mode write, so concurrent writers never interleave within a line; a write that the system takes only
    in part raises, as a buffered write does. The line such a write cut gets its end first, so this line never joins
    it."""
    data = text.encode("utf-8")
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        if os.fstat(descriptor).st_size:
            os.lseek(descriptor, -1, os.SEEK_END)
            if os.read(descriptor, 1) != b"\n":
                data = b"\n" + data
        written = os.write(descriptor, data)
    finally:
        os.close(descriptor)
    if written != len(data):
        raise OSError(f"appended {written} of {len(data)} bytes to {path}")


def _size_rotation(path: Path, policy: Policy) -> tuple[Path, ...]:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return ()
    if size <= policy.limit:
        return ()
    previous = path.with_name(path.name + ".1")
    install_destination.assert_mutation_path(path)
    install_destination.assert_mutation_path(previous)
    if size > 2 * policy.limit:
        # A file far past its limit keeps only its newest part as the previous file.
        atomic_write.write_bytes(previous, _tail(path, policy.limit))
    elif policy.copy_truncate:
        atomic_write.write_bytes(previous, path.read_bytes())
    else:
        os.replace(path, previous)
        return (previous,)
    if policy.copy_truncate:
        # The holder's append-mode descriptor writes from the start of the emptied file.
        os.truncate(path, 0)
    else:
        path.unlink()
    return (previous,)


def _tail(path: Path, limit: int) -> bytes:
    """The complete lines within the last `limit` bytes of `path`."""
    with path.open("rb") as stream:
        stream.seek(-(limit + 1), os.SEEK_END)
        window = stream.read(limit + 1)
    return window[window.find(b"\n") + 1:] if b"\n" in window else b""


def _line_count(path: Path, policy: Policy) -> tuple[Path, ...]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ()
    end = committed_length(raw)
    # A line a full disk cut can end inside a character, so bytes that are not UTF-8 pass through unchanged.
    lines = split_lines(raw[:end].decode("utf-8", "surrogateescape"))
    if len(lines) <= policy.limit:
        return ()
    install_destination.assert_mutation_path(path)
    # A record another writer is still appending stays after the kept lines for that writer to finish.
    path.write_bytes(("\n".join(lines[-policy.keep:]) + "\n").encode("utf-8", "surrogateescape") + raw[end:])
    return (path,)


def _keep_newest(path: Path, policy: Policy, protected: list[Path],
                 eligible: Callable[[Path], bool] | None) -> tuple[Path, ...]:
    folder = path.parent if policy.siblings else path
    members = policy.members.format(name=path.name)
    try:
        entries = sorted(folder.iterdir())
    except FileNotFoundError:
        return ()
    groups: dict[str, list[Path]] = {}
    for entry in entries:
        if not fnmatch.fnmatchcase(entry.name, members):
            continue
        link = entry.is_symlink()
        if (link and policy.skip_links) or (policy.files_only and (link or not entry.is_file())) \
                or (policy.folders_only and (link or not entry.is_dir())):
            continue
        key = entry.name
        if policy.group is not None:
            found = re.match(policy.group, entry.name)
            if found is None:
                continue  # an entry the rule does not name is left alone
            key = found.group("key")
        groups.setdefault(key, []).append(entry)
    if policy.order == "mtime":
        ordered = sorted(groups.items(), key=lambda item: (max(_mtime(entry) for entry in item[1]), item[0]))
    else:
        ordered = sorted(groups.items())
    if eligible is not None:
        ordered = [(key, group) for key, group in ordered if all(eligible(entry) for entry in group)]
    removed = []
    for _, group in ordered[:max(0, len(ordered) - policy.limit)]:
        if any(_is_protected(entry, protected) for entry in group):
            continue
        removed.extend(_remove_each(policy.name, group))
    return tuple(removed)


def _age(path: Path, policy: Policy, protected: list[Path]) -> tuple[Path, ...]:
    if not path.is_dir() or path.is_symlink():
        return ()
    install_destination.assert_mutation_path(path)
    now = time.time()
    old = []
    for host in sorted(path.iterdir()):
        if host.is_symlink() or not host.is_dir():
            continue
        for folder in sorted(host.iterdir()):
            if folder.is_symlink() or not folder.is_dir() or _is_protected(folder, protected):
                continue
            marker = folder / policy.marker if policy.marker else None
            try:
                dated = marker if marker is not None and marker.is_file() and not marker.is_symlink() else folder
                moment = dated.stat().st_mtime
            except OSError:
                continue
            if now - moment > policy.limit * DAY:
                old.append((moment, str(folder), folder))
    old.sort()
    return tuple(_remove_each(policy.name, [folder for _, _, folder in old[:policy.per_run]]))


def _remove_each(name: str, entries: Iterable[Path]) -> list[Path]:
    removed = []
    for entry in entries:
        try:
            install_destination.assert_mutation_path(entry)
            _remove(entry)
            removed.append(entry)
        except FileNotFoundError:
            continue  # another writer removed it first
        except (OSError, ValueError) as error:
            _record_failure(name, entry, type(error).__name__, str(error))
    return removed


def _remove(entry: Path) -> None:
    if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
    else:
        entry.unlink()


def _mtime(entry: Path) -> int:
    try:
        return entry.lstat().st_mtime_ns
    except OSError:
        return 0


def _protected_forms(protect: Iterable[Path | str]) -> list[Path]:
    forms = []
    for item in protect:
        item = Path(item)
        forms.append(item)
        try:
            forms.append(item.resolve())
        except OSError:
            pass
    return forms


def _is_protected(entry: Path, protected: list[Path]) -> bool:
    if not protected:
        return False
    forms = [entry]
    try:
        forms.append(entry.parent.resolve() / entry.name)
    except OSError:
        pass
    return any(form == item or item in form.parents for form in forms for item in protected)


def failure_record_path() -> Path:
    """The retention failure record under the Ghost-ALICE state root; its writer and doctor both use this path."""
    return Path(install_destination.ghost_root()) / FAILURES


def record_failure(name: str, path: Path | str, error: BaseException) -> None:
    """Record a failure outside `apply` and `append`, such as an installer staging folder left behind, under `name`."""
    _record_failure(name, Path(path), type(error).__name__, str(error))


def _record_failure(name: str, path: Path, error_type: str, detail: str) -> None:
    """One line in the retention failure record; a failure to record is dropped so the writer goes on."""
    try:
        record = failure_record_path()
        install_destination.assert_mutation_path(record)
        record.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "policy": name, "path": str(path),
               "error_type": error_type, "error": detail[:500]}
        line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            with file_lock.exclusive(record):
                _append_once(record, line)
                _line_count(record, POLICIES["retention-failures"])
        except file_lock.LockTimeout:
            _append_once(record, line)  # the record keeps its limit at the next failure
    except Exception:
        pass
