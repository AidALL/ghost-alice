"""The installation source checkout.

`match_install_source` is the one judgment of whether this checkout is the
checkout the install record names as its source; `check_install_source` acts on
it before an install or a repair changes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import contextlib
from typing import TYPE_CHECKING

import install_transaction
import installed_payload
import addon_registry
import content_digest
from installer_assets import _is_windows_junction, unrecorded_copy_content
import file_lock
from installer import locations, report, runner, terminal
from installer.runner import InstallerExit

if TYPE_CHECKING:
    from installer.cli import Context

HOOKS_DIR = "hooks"
# The install-state `system_env_changes` kind of the repository's shared hook path.
HOOK_PATH_CHANGE = "source_repo_hook_path"
# The record of the hook path write for the whole installation, next to the hosts' install-state manifests.
HOOK_PATH_RECORD = "source-repo-hook-path.json"
ADOPT_HINT = ("Rerun this command from the install source, or add --adopt-source to install from this checkout "
              "and record it as the install source.")
REPAIR_HINT = ("Repair runs only from the install source; run the install from this checkout with --adopt-source to "
               "move the installation here.")
REQUEST_ENV = "GHOST_ALICE_INSTALL_REQUEST"


@dataclass(frozen=True)
class SourceInfo:
    root: Path        # `git rev-parse --show-toplevel`, else the script dir
    branch: str       # short branch name, `DETACHED` without one
    head: str         # full commit id, `unknown` without git
    dirty: str        # `dirty`, `clean` or `unknown`


@dataclass(frozen=True)
class HookPathChange:
    """The repository's shared `core.hooksPath` this install owns, as the install-state records it.

    `after` is the absolute path of the install source's hooks folder and `changed` says this command wrote it.
    `recorded` says the install knows the value from before Ghost-ALICE wrote one: `before_present` and `before`
    give it (False and "" for no value, which the full uninstall unsets). `applied_at` is when an earlier install
    wrote the value this one found in place, else None.
    """

    changed: bool
    recorded: bool
    before_present: bool = False
    before: str = ""
    after: str = ""
    applied_at: str | None = None


@dataclass(frozen=True)
class SourceMatch:
    """This checkout compared with the checkout the install record names as its source.

    `state` is `same`, `other`, `unrecorded` (no record, or a record without a `source` section) or
    `unreadable` (`detail` says why). `here` is this checkout with its links followed; `recorded` is the
    record's `source.root` as the record writes it.
    """

    state: str
    here: Path
    recorded: Path | None = None
    detail: str = ""


def _git(root: Path, *args: str) -> runner.ToolResult:
    return runner.run_command(["git", "-C", str(root), *args], capture=True)


def _git_value(root: Path, *args: str) -> str | None:
    result = _git(root, *args)
    return result.stdout.strip() if result.ok else None


def check_source_health(*, skip: bool, input_root: Path | None = None) -> bool:
    """Print the checkout's source health and return False only when the branch is behind its upstream.

    Skip → warn and pass; no git or not a worktree → warn and pass; `Source health: branch=,
    head=, upstream=`; diverged → two warnings; behind → four errors, False;
    ahead → warning; untracked → warning; tracked changes → warning.
    """
    root = input_root if input_root is not None else runner.REPO_ROOT
    if skip:
        report.warn("Skipping source health gate (--skip-source-health)")
        return True
    if shutil.which("git") is None:
        report.warn("git not found; source health is unverified (continuing)")
        return True
    if _git_value(root, "rev-parse", "--is-inside-work-tree") != "true":
        report.warn("source is not a git worktree; source health is unverified (continuing)")
        return True
    branch = _git_value(root, "symbolic-ref", "--quiet", "--short", "HEAD") or "DETACHED"
    head = _git_value(root, "rev-parse", "--short", "HEAD") or "unknown"
    upstream = _git_value(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or "none"
    tracked_dirty = untracked = False
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    for line in (status.stdout or "").splitlines():
        if not line:
            continue
        if line.startswith("?? "):
            untracked = True
        elif line.startswith("!! "):
            continue
        else:
            tracked_dirty = True
    report.info(f"Source health: branch={branch}, head={head}, upstream={upstream}")
    if upstream != "none":
        counts = _git_value(root, "rev-list", "--left-right", "--count", "HEAD...@{u}")
        if counts:
            parts = counts.split()
            ahead = int(parts[0]) if parts and parts[0].isdigit() else 0
            behind = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            if ahead > 0 and behind > 0:
                report.warn("Source branch has diverged from its upstream; continuing because local commits may be intentional.")
                report.warn("Before release, run git fetch origin, inspect git status, and reconcile this checkout history.")
            elif behind > 0:
                report.error("Source branch is behind its upstream; refusing to install from stale source.")
                report.error("Run git fetch origin, inspect git status, then fast-forward or otherwise reconcile this checkout "
                             "before installing. If the human user decided to install from this local source, rerun with --skip-source-health.")
                report.error(f"To preserve source-local changes while updating, run {report.install_command()} --update-source.")
                report.error("If an old checkout is already blocked by local changes during git pull, use the bootstrap "
                             "one-command update in docs/getting-started/troubleshooting.md.")
                return False
            elif ahead > 0:
                report.warn("Source branch is ahead of its upstream; continuing because local commits may be intentional.")
        else:
            report.warn("Source upstream comparison failed; continuing with local source health checks only.")
    if untracked:
        report.warn("Source tree has untracked files; continuing.")
    if tracked_dirty:
        report.warn("Source tree has tracked local changes; continuing. External changes to installed targets remain protected "
                    "by pending-merges backup and manifest checks.")
    return True


def _same_folder(first: Path, second: Path) -> bool:
    """Whether two paths name one folder, their links followed: the same file system entry when both exist,
    else the same resolved path."""
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve()))


def match_install_source(checkout: Path | None = None) -> SourceMatch:
    """Compare `checkout` (the checkout the installer runs from, `runner.REPO_ROOT`, when not given) with the
    install record's `source.root`, both with their links followed.

    The record is read as it will be once every unfinished publish is restored: the comparison runs before
    the lock, and a publish that stopped halfway may have written its own source already. The installer's
    check before an install or a repair and the post-merge step, which names the checkout the merge ran in,
    ask this function, so the two never disagree about which checkout is the install source.
    """
    here = Path(resolve_source().root if checkout is None else checkout).resolve()
    try:
        record = install_transaction.record_after_recovery(locations.record_root())
    except (installed_payload.RecordError, install_transaction.InstallTransactionError) as exc:
        return SourceMatch("unreadable", here, detail=str(exc))
    section = (record or {}).get("source")
    root = section.get("root") if isinstance(section, dict) else None
    if not isinstance(root, str) or not root:
        return SourceMatch("unrecorded", here)
    recorded = Path(root)
    return SourceMatch("same" if _same_folder(recorded, here) else "other", here, recorded)


def interactive() -> bool:
    """A terminal on both ends: stdin to read an answer and stdout to show the question."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _name_both(say, match: SourceMatch) -> None:
    say("This checkout is not the install source the install record names.")
    say(f"  Install source: {match.recorded}")
    say(f"  This checkout:  {match.here}")


def check_install_source(ctx: "Context", *, can_adopt: bool = True, match: SourceMatch | None = None) -> None:
    """Go on, or stop with exit 2, before an install or a repair changes anything.

    No record, or a record without a source, goes on (the first install), and so does the recorded checkout;
    an unreadable record skips the comparison with a warning. Another checkout stops a command that cannot
    replace the install source (`can_adopt` False: repair), which names the install that moves it. An install
    goes on from another checkout with `--adopt-source`, and without a question when the recorded checkout no
    longer exists (a fresh clone at another path). Otherwise the installer names both folders; at a terminal
    it asks whether this checkout replaces the install source (no by default), elsewhere it stops with the
    command to run. A replacement is kept in the options, so the auto mode's children carry it instead of
    judging again; the installer's source comparison result is kept for `recheck_install_source`. The command's first publish records this
    checkout as the source.
    """
    match = match_install_source() if match is None else match
    ctx.source_match = match
    if match.state == "unreadable":
        report.warn(f"Install source check skipped: {match.detail}")
        return
    if match.state != "other":
        return
    if not can_adopt:
        _name_both(report.error, match)
        report.error(REPAIR_HINT)
        raise InstallerExit(2)
    if ctx.options.adopt_source:
        report.info(f"Install source: this checkout replaces {match.recorded}")
        return
    if not match.recorded.exists():
        ctx.options.adopt_source = True
        report.info(f"Install source: {match.recorded} no longer exists; this checkout replaces it")
        return
    asking = interactive()
    _name_both(report.warn if asking else report.error, match)
    if asking:
        try:
            answer = terminal.read_answer("Replace the install source with this checkout? [y/N]: ")
        except (OSError, ValueError) as exc:
            report.error(report.message('terminal_input_unavailable', cause=exc))
            raise InstallerExit(2) from exc
        if answer is not None and answer.strip().lower() in ("y", "yes"):
            ctx.options.adopt_source = True
            report.info(f"Install source: this checkout replaces {match.recorded}")
            return
    report.error(ADOPT_HINT)
    raise InstallerExit(2)


def recheck_install_source(ctx: "Context", *, can_adopt: bool) -> None:
    """Compare again under the install lock: another installer may have changed the install source while this
    command waited for the lock. The answer the check before the lock gave goes on without a second message;
    any other answer is judged as `check_install_source` judges it."""
    match = match_install_source()
    if match == ctx.source_match:
        return
    check_install_source(ctx, can_adopt=can_adopt, match=match)


def update_source() -> int:
    """`installer_source_update.py --source-dir <checkout>`; its exit code."""
    return runner.run_tool("_shared/installer_source_update.py", "--source-dir", input_record()['root']).returncode


@contextlib.contextmanager
def acquire_update(origin: dict):
    """Fetch recorded remote/ref into a temporary input without changing the developer checkout."""
    if not origin.get('remote') or not origin.get('ref'):
        raise ValueError('Update needs its acquisition source: ghost-alice update --remote URL --ref NAME')
    with tempfile.TemporaryDirectory(prefix='ghost-alice-update-') as temporary:
        root = Path(temporary)
        for args in (('init', '--quiet'), ('fetch', '--quiet', '--depth=1', '--', origin['remote'], origin['ref']),
                     ('checkout', '--quiet', '--detach', 'FETCH_HEAD')):
            result = _git(root, *args)
            if not result.ok:
                raise ValueError(f'Update acquisition failed: {result.stderr.strip() or result.stdout.strip()}')
        yield root


def hooks_path_value(checkout: Path) -> str:
    """The `core.hooksPath` an install from `checkout` writes: the checkout's hooks folder by absolute path, links
    followed."""
    return (Path(checkout).resolve() / HOOKS_DIR).as_posix()


def _same_repository(first: Path, second: Path) -> bool:
    """Whether two checkouts belong to one repository: the same git common folder."""
    common = [_git_value(Path(path), "rev-parse", "--path-format=absolute", "--git-common-dir") for path in (first, second)]
    return all(common) and _same_folder(Path(common[0]), Path(common[1]))


def _hook_path_records() -> list[dict]:
    """The hook path changes the installation recorded: the record of the last write, then each host's
    install-state entry."""
    sources = [locations.install_state_dir() / HOOK_PATH_RECORD,
               *locations.install_state_manifests(locations.install_state_dir())]
    records: list[dict] = []
    for path in sources:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if path.name == HOOK_PATH_RECORD:
            entries = [data]
        else:
            entries = data.get("system_env_changes") if isinstance(data, dict) else None
        if isinstance(entries, list):
            records.extend(entry for entry in entries
                           if isinstance(entry, dict) and entry.get("kind") == HOOK_PATH_CHANGE)
    return records


def relative_hook_path_matches_repository(change: dict, root: Path) -> bool:
    """The legacy relative hooks path identifies an installation only within the same Git repository."""
    recorded = change.get('repo_root')
    return isinstance(recorded, str) and Path(recorded).is_absolute() and _same_repository(Path(recorded), root)


def _recorded_hook_path(value: str) -> HookPathChange | None:
    """The hook path change the installation records as writing `value`, else None. The relative `hooks` an earlier
    installer wrote names no folder, so its record counts only for a checkout of this repository."""
    for change in _hook_path_records():
        if change.get("after") != value:
            continue
        if value == HOOKS_DIR and not relative_hook_path_matches_repository(change, Path(input_record()['root'])):
            continue
        before_present = change.get("before_present") is True and isinstance(change.get("before"), str)
        return HookPathChange(changed=False, recorded=True, before_present=before_present,
                              before=change["before"] if before_present else "", after=value,
                              applied_at=change.get("applied_at"))
    return None


def _write_hook_path_record(change: HookPathChange) -> None:
    """Record the hook path this install owns in the same step as the write, so an install that stops before its
    install-state is written leaves the value to restore for the next one."""
    path = locations.install_state_dir() / HOOK_PATH_RECORD
    applied_at = change.applied_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = {"kind": HOOK_PATH_CHANGE, "repo_root": Path(input_record()['root']).resolve().as_posix(),
              "before_present": change.before_present, "before": change.before if change.before_present else None,
              "after": change.after, "applied_at": applied_at}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staged.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(staged, path)
    except OSError as exc:
        report.warn(f"Could not record the Git hook path write: {exc}")


def setup_git_hooks() -> HookPathChange:
    """Write the repository's shared `core.hooksPath` as this checkout's hooks folder by absolute path, make
    `hooks/post-merge` executable, and return what the install-state records.

    Every worktree of the repository inherits the shared value (`git config --local` writes the configuration the
    worktrees share), so a merge in any worktree runs the install source's post-merge hook and no other checkout's
    hook file; a worktree's own setting (`git config --worktree`) still comes first. The value the full uninstall
    restores is the value from before Ghost-ALICE wrote one: the recorded value when the installation records the
    earlier value as written by an install (a reinstall, an install that stopped before its install-state, an
    install source that moved, or the relative `hooks` an earlier installer wrote over another value in this
    repository); else none for an unset value or the relative `hooks`; else the earlier value itself. A value
    already in place that no record names leaves the value to restore unknown, and the uninstall leaves the hook
    path as it is. A known value is recorded in the same step as the write (`HOOK_PATH_RECORD`). The sidecar's
    persistent guard serializes the shared Git value and its first-before record across source adoptions. This
    guard ends before the caller takes the installation lock; prerequisite preparation keeps its own lifetime.
    """
    root = Path(input_record()['root'])
    if not root.is_dir() and root != runner.REPO_ROOT:
        return HookPathChange(changed=False, recorded=False)
    wanted = hooks_path_value(runner.REPO_ROOT)
    record = locations.install_state_dir() / HOOK_PATH_RECORD
    try:
        record.parent.mkdir(parents=True, exist_ok=True)
        with file_lock.exclusive(record.with_name(record.name + ".guard"), must_lock=True):
            found = _git(root, "config", "--local", "--get", "core.hooksPath")
            current = found.stdout.strip() if found.ok and found.stdout.strip() else None
            if current == wanted:
                change = _recorded_hook_path(wanted) or HookPathChange(changed=False, recorded=False, after=wanted)
            elif not _git(root, "config", "--local", "core.hooksPath", wanted).ok:
                report.warn("Failed to set Git hook path. Post-merge hook may not fire")
                return HookPathChange(changed=False, recorded=False)
            else:
                earlier = _recorded_hook_path(current) if current is not None else None
                if earlier is not None:
                    change = HookPathChange(changed=True, recorded=True, before_present=earlier.before_present,
                                            before=earlier.before, after=wanted)
                else:
                    kept = current not in (None, HOOKS_DIR)
                    change = HookPathChange(changed=True, recorded=True, before_present=kept,
                                            before=current if kept else "", after=wanted)
                report.info(f"Git hook path → {wanted} (post-merge auto-refresh enabled)")
            if change.recorded:
                _write_hook_path_record(change)
    except (OSError, file_lock.LockTimeout) as exc:
        report.warn(f"Could not serialize the Git hook path write: {exc}")
        return HookPathChange(changed=False, recorded=False)
    hook = runner.REPO_ROOT / HOOKS_DIR / "post-merge"
    if hook.is_file() and not os.access(hook, os.X_OK):
        try:
            hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            pass
    return change


def configured_hooks_path() -> str | None:
    """The checkout's `core.hooksPath`, or None when it is not set."""
    return _git_value(Path(input_record()['root']), "config", "--local", "core.hooksPath") or None


def commit_of(path: Path) -> str:
    """The commit of the repository that holds `path` (an addon source), `unknown` without one."""
    request = install_request()
    if request:
        for root, commit in request.get('addon_commits', {}).items():
            if Path(path).resolve().is_relative_to(root):
                return commit
    return _git_value(Path(path), "rev-parse", "HEAD") or "unknown"


def install_request() -> dict | None:
    """The parent's input provenance, bound to the package this process executes."""
    raw = os.environ.get(REQUEST_ENV)
    if not raw:
        return None
    request = json.loads(raw)
    if (not isinstance(request, dict) or request.get('package_root') != str(runner.REPO_ROOT.resolve())
            or not isinstance(request.get('source'), dict)):
        raise ValueError('Installer request does not identify this package and its input source')
    return request


def input_record(input_root: Path | None = None) -> dict:
    """Development origin and acquisition coordinates, separate from this process's assets."""
    request = install_request() if input_root is None else None
    if request:
        return {key: value for key, value in request['source'].items() if key != 'ledger_source_roots'}
    root_path = Path(input_root) if input_root is not None else runner.REPO_ROOT
    if root_path.resolve().is_relative_to((locations.record_root() / 'versions').resolve()):
        record = installed_payload.read(locations.record_root()) or {}
        saved = json.loads((root_path.parent / 'version.json').read_text())
        origin = dict(saved['source'])
        origin.pop('ledger_source_roots', None)
        origin.update({key: value for key, value in record.get('source', {}).items()
                       if key in {'root', 'remote', 'ref', 'branch', 'hooks_path'}})
        return origin
    toplevel = _git_value(root_path, "rev-parse", "--show-toplevel")
    branch = _git_value(root_path, "symbolic-ref", "--quiet", "--short", "HEAD") or "DETACHED"
    head = _git_value(root_path, "rev-parse", "HEAD") or "unknown"
    if _git_value(root_path, "rev-parse", "--is-inside-work-tree") == "true":
        status = _git(root_path, "status", "--porcelain=v1", "--untracked-files=all")
        dirty = "dirty" if (status.stdout or "").strip() else "clean"
    else:
        dirty = "unknown"
    root = Path(toplevel) if toplevel else root_path
    remote_name = _git_value(root, 'config', f'branch.{branch}.remote')
    remote = _git_value(root, 'remote', 'get-url', remote_name or 'origin')
    if remote and not Path(remote).is_absolute() and ':' not in remote:
        remote = str((root / remote).resolve())
    ref = _git_value(root, 'config', f'branch.{branch}.merge')
    return {'root': str(root), 'commit': head, 'dirty': dirty, 'branch': branch,
            'hooks_path': _git_value(root, 'config', '--local', 'core.hooksPath'), 'remote': remote,
            'ref': ref or (branch if branch != 'DETACHED' else None)}


def resolve_source() -> SourceInfo:
    """The input's provenance for install-state and source matching, never an asset lookup."""
    record = input_record()
    return SourceInfo(Path(record['root']), record.get('branch', 'DETACHED'), record['commit'], record['dirty'])


def _version_hash(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode('utf-8')).hexdigest()


def _package_files(root: Path, paths) -> list[str]:
    """Regular source files under the package's declared roots, without generated caches."""
    names = set()
    for relative in paths:
        selected = root / relative
        if not selected.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Source entry escapes its package: {selected}')
        if selected.is_file():
            names.add(selected.relative_to(root).as_posix())
        elif selected.is_dir():
            if selected.is_symlink():
                raise ValueError(f'Source directory link cannot be preserved as fixed bytes: {selected}')
            for current, dirs, files in os.walk(selected):
                dirs[:] = [name for name in dirs if name not in install_transaction.EXCLUDED_COPY_DIR_NAMES]
                for name in dirs:
                    if (Path(current) / name).is_symlink():
                        raise ValueError(f'Source directory link cannot be preserved as fixed bytes: {Path(current) / name}')
                for name in files:
                    path = Path(current) / name
                    if not install_transaction._is_excluded_copy_file(path):
                        names.add(path.relative_to(root).as_posix())
    return sorted(names)


def _version_source_files(root: Path, *, addon: bool = False) -> list[str]:
    """The saved file list, or declared product paths, independent of Git availability."""
    versions = (locations.record_root() / 'versions').resolve()
    if root.is_relative_to(versions):
        folder = versions / root.relative_to(versions).parts[0]
        saved = json.loads((folder / 'version.json').read_text(encoding='utf-8'))
        prefix = root.relative_to(folder).as_posix() + '/'
        return sorted(name[len(prefix):] for name in saved['files'] if name.startswith(prefix))
    if addon:
        manifest = json.loads((root / 'addons-manifest.json').read_text(encoding='utf-8'))
        paths = ['addons-manifest.json', *(entry['path'] for entry in manifest['addons'])]
    else:
        from installer.selection import ALL_SKILLS
        catalog = root / 'skill-catalog/skills.json'
        roots = json.loads(catalog.read_text(encoding='utf-8')).get('install_roots', ALL_SKILLS) if catalog.is_file() else ALL_SKILLS
        paths = [*roots, '_shared', 'platforms', 'skill-catalog', 'hooks', 'scripts', '.claude/commands',
                 'install.sh', 'install.ps1', 'install.cmd', 'VERSION',
                 *[f'docs/{language}policies/{name}.md' for language in ('', 'ko/')
                   for name in ('evaluator-artifact-contract', 'live-smoke-regression')]]
    return _package_files(root, paths)


def _copy_version_source(root: Path, destination: Path, *, addon: bool = False) -> dict[str, str]:
    files = {}
    for name in _version_source_files(root, addon=addon):
        original, copy = root / name, destination / name
        if not original.resolve().is_relative_to(root) or not copy.resolve().is_relative_to(destination.resolve()):
            raise ValueError(f'Source entry escapes its package: {original}')
        before = install_transaction._file_hash(original)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, copy)
        if before != install_transaction._file_hash(copy) or before != install_transaction._file_hash(original):
            raise ValueError(f'Source changed while preserving it: {original}')
        files[name] = before
    if not files:
        raise ValueError(f'No source files found for {root}')
    return files


def _active_versions(root: Path, record: dict) -> dict[Path, set[str]]:
    """Versions consumed by current targets, service, pending merges, install source or addon registries."""
    references = install_transaction._cleanup_references(locations.record_root(), record=record)
    references.append((record.get('source', {}).get('root'), 'install source'))
    origin = record.get('source', {}).get('root')
    if origin and Path(origin).is_dir():
        hooks = _git_value(Path(origin), 'config', '--local', 'core.hooksPath')
        if hooks:
            references.append((Path(hooks) if Path(hooks).is_absolute() else Path(origin) / hooks, 'developer post-merge hook'))
    addon_root = locations.record_root() / 'addons'
    for directory in sorted(addon_root.iterdir()) if addon_root.is_dir() else ():
        if directory.is_dir():
            for entry in addon_registry.read_all(addons_dir=directory):
                references.append((entry.get('source'), directory.name + ' addon'))
    active = {}
    for value, host in references:
        if value:
            path = Path(value).resolve()
            if path.is_relative_to(root) and path != root:
                folder = root / path.relative_to(root).parts[0]
                if folder.is_dir() and not folder.is_symlink():
                    active.setdefault(folder, set()).add(host)
    return active


def _unchanged_version(folder: Path) -> bool:
    """Only an intact source version without extra user files is eligible for pruning."""
    try:
        files = _version_metadata(folder)['files']
        return (not unrecorded_copy_content(folder, [*files, 'version.json'])
                and not _missing_version_entries(folder, files, check_bytes=True))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _version_entry_exists(path: Path, *, directory: bool) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if (getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError(f'Preserved source entry has changed or is a link: {path}')
    return True


def _version_metadata(folder: Path) -> dict:
    metadata = folder / 'version.json'
    if _version_entry_exists(metadata, directory=False):
        saved = json.loads(metadata.read_text(encoding='utf-8'))
        files = saved.get('files') if isinstance(saved, dict) else None
        if (isinstance(files, dict) and files and _version_hash(files) == folder.name
                and all(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest) for digest in files.values())):
            return saved
    raise ValueError(f'Preserved source file list is missing or has changed: {metadata}')


def _missing_version_entries(folder: Path, files: dict[str, str], *, check_bytes: bool) -> list[Path]:
    """Verify surviving entries before returning the topmost absent files and directories."""
    directories = set()
    regular = {}
    for name, digest in files.items():
        relative = Path(name)
        if relative.anchor or '..' in relative.parts or not relative.parts or relative.as_posix() != name:
            raise ValueError(f'Invalid preserved source entry in {folder}: {name}')
        regular[relative] = digest
        directories.update(relative.parents)
    directories.discard(Path('.'))
    absent = set()
    missing = []
    for relative in sorted(directories | regular.keys()):
        if relative.parent in absent:
            absent.add(relative)
            continue
        path = folder / relative
        if not _version_entry_exists(path, directory=relative in directories):
            absent.add(relative)
            missing.append(relative)
        elif check_bytes and relative in regular and install_transaction._file_hash(path) != regular[relative]:
            raise ValueError(f'Preserved source version has changed: {path}')
    return missing


def _active_version(root: Path) -> Path | None:
    record = installed_payload.read(locations.record_root()) or {}
    active = record.get('installer', {}).get('active_root')
    if active:
        package = Path(active)
        if package.name == 'core' and package.parent.parent == root:
            return package.parent
    return None


def _manager_missing(folder: Path) -> bool:
    """When saved files are missing, check whether the active CLI imports and its reader reads the current record."""
    if not _version_entry_exists(folder, directory=True):
        return True
    files = _version_metadata(folder)['files']
    if _missing_version_entries(folder, files, check_bytes=False):
        try:
            check_record_reader(folder / 'core', locations.record_root(), check_entry=True)
        except ValueError:
            return True
    return False


def _version_root() -> Path:
    root = locations.record_root() / 'versions'
    if root.is_symlink() or _is_windows_junction(root):
        raise ValueError(f'Source version root is a link: {root}')
    return root.resolve()


def repair_active_source(ctx: 'Context') -> None:
    """Under the repair lock, restore a recorded version before publishing any managed targets."""
    active = _active_version(_version_root())
    if active is None:
        return
    if not _version_entry_exists(active, directory=True):
        raise ValueError(report.message('active_version_missing', package=active / 'core', command=report.install_command()))
    files = _version_metadata(active)['files']
    if _missing_version_entries(active, files, check_bytes=True):
        addon_paths = sorted({str(active.joinpath(*Path(name).parts[:2])) for name in files if name.startswith('addons/')})
        inputs = replace(ctx, addon_sources=ctx.addon_sources or addon_paths)
        prepare_source(inputs, required_version=active)
    check_record_reader(active / 'core', locations.record_root(), check_entry=True)


def prepare_source(ctx: 'Context', *, input_root: Path | None = None, provenance: dict | None = None,
                   selections: dict[str, list[str]] | None = None,
                   required_version: Path | None = None) -> tuple[Path, dict[str, str]]:
    """Capture the requested product bytes once without declaring installation success."""
    root = _version_root()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=root) as temporary:
        stage = Path(temporary)
        core_root = Path(input_root if input_root is not None else runner.REPO_ROOT).resolve()
        core = _copy_version_source(core_root, stage / 'core')
        files = {'core/' + name: digest for name, digest in core.items()}
        sources = ['core']
        captured = [(core_root, 'core')]
        for index, selected in enumerate(dict.fromkeys(ctx.addon_sources)):
            candidate = stage / f'addon-{index}'
            addon = _copy_version_source(Path(selected).resolve(), candidate, addon=True)
            relative = 'addons/' + _version_hash(addon)
            captured.append((Path(selected).resolve(), relative))
            if relative in sources:
                shutil.rmtree(candidate)
                continue
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            candidate.rename(destination)
            sources.append(relative)
            files.update((relative + '/' + name, digest) for name, digest in addon.items())
        folder = root / _version_hash(files)
        saved = {'files': files, 'sources': sorted(sources), 'platforms': [], 'status': 'candidate',
                 'selections': selections,
                 'source': provenance if provenance is not None else input_record(),
                 'origins': {relative: str(original) for original, relative in captured}}
        active = required_version or _active_version(root)
        if active and folder != active and (required_version or _manager_missing(active)):
            raise ValueError(report.message('active_version_mismatch', package=active / 'core', command=report.install_command()))
        if _version_entry_exists(folder, directory=True):
            if _version_metadata(folder)['files'] != files:
                raise ValueError(f'Preserved source version has changed: {folder}')
            missing = _missing_version_entries(folder, files, check_bytes=True)
            if missing and folder != active:
                raise ValueError(f'Preserved source version is incomplete: {folder}')
            if missing:
                from retired_assets import _restore_no_replace
                for relative in missing:
                    _restore_no_replace(stage / relative, folder / relative)
        else:
            # The complete file set moves into place; a failed copy never becomes a saved version.
            install_transaction._write_json(stage / 'version.json', saved)
            stage.rename(folder)
    return folder, {str(folder / relative): commit_of(original)
                    for original, relative in captured if relative != 'core'}


def finalize_source(ctx: 'Context', folder: Path, platforms: list[str], *, before_handoff: dict | None = None) -> list[str]:
    """After the requested placements succeed, commit success and active CLI, then prune old successes."""
    root = (locations.record_root() / 'versions').resolve()
    version_file = folder / 'version.json'
    saved = _version_metadata(folder)
    if _missing_version_entries(folder, saved['files'], check_bytes=True):
        raise ValueError(f'Preserved source version is incomplete: {folder}')
    from installer.selection import installed_core_selection
    record_path = installed_payload.read_path(locations.record_root())
    record = installed_payload.read_file(record_path) or {}
    selections = installed_core_selection(record, record_path)
    for destination, entry in record.get('targets', {}).items():
        if not set(platforms).intersection(entry.get('used_by', [])):
            continue
        original = Path(entry['source']).resolve()
        if original.is_relative_to(folder):
            # Commands record rendered bytes; their source identifies the bootstrap template.
            expected = entry.get('files') if entry.get('kind') == 'command' else content_digest.tree(original)
            if expected != content_digest.tree(destination):
                raise ValueError(f'Preserved source differs from installed code: {destination}')
    # The executing package declares support for the parent's record-move request.
    saved.update(status='successful', platforms=sorted(set(saved.get('platforms', [])) | set(platforms)),
                 selections=selections,
                 record_move=True,
                 completed_at=datetime.now(timezone.utc).isoformat())
    sections = {'installer': {'active_root': str(folder / 'core')}}
    if record.get('source', {}).get('root'):
        from installer.transaction import installed_ledger_locations
        observed = installed_ledger_locations(before_handoff=before_handoff)
        if observed.observation_errors:
            raise ValueError('; '.join(observed.observation_errors.values()))
        facts = observed.publication_source({'root': record['source']['root']})
        sections['source'] = facts
    defer_source = (request_writer_needs_v2() and
                    any(isinstance(value, dict) for value in sections.get('source', {}).get('ledger_source_roots', {}).values()))
    with install_transaction.record_update(locations.record_root(), version_file=version_file) as journal:
        install_transaction._write_json(version_file, saved)
        installed_payload.update(locations.record_root(), sections, journal=journal, defer_source=defer_source)
    record = installed_payload.read(locations.record_root()) or {}
    command = ['pwsh', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(folder / 'core/install.ps1')] if os.name == 'nt' else ['bash', str(folder / 'core/install.sh')]
    command += ['--platform', ctx.platform, '--adopt-source']
    for selected in sorted(source for source in saved['sources'] if source != 'core'):
        command += ['--addon-source', str(folder / selected)]
    lines = [f'Successful source version: {folder}', 'Reinstall this code: ' + (subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command))]
    from installer.transaction import cleanup_record_source
    lines.extend(install_transaction.cleanup_completed(locations.record_root(), record_source_cleanup=cleanup_record_source))
    try:
        active = _active_versions(root, record)
    except (OSError, ValueError, addon_registry.RegistryError, install_transaction.InstallTransactionError) as exc:
        return [*lines, f'Source versions retained because references could not be checked: {exc}']
    active.setdefault(folder, set()).add(ctx.platform + ' latest success')
    successful = sorted((path for path in root.iterdir() if path.is_dir() and not path.is_symlink()
                         and (path / 'version.json').is_file()
                         and json.loads((path / 'version.json').read_text()).get('status', 'successful') == 'successful'),
                        key=lambda path: (path / 'version.json').stat().st_mtime_ns, reverse=True)
    latest = set(successful[:3])
    for obsolete in successful[3:]:
        if obsolete in active or not _unchanged_version(obsolete):
            continue
        from uninstall_cleanup import platforms_referencing_target
        try:
            consumers = platforms_referencing_target(obsolete,
                current_manifest=locations.record_root() / 'install-state' / f'{ctx.platform}.json',
                current_platform=ctx.platform, include_current=True, source_tree=True)
        except (OSError, ValueError) as exc:
            lines.append(f'Source version retained because references could not be checked: {obsolete}: {exc}')
            continue
        if consumers:
            active[obsolete] = set(consumers)
            continue
        try:
            shutil.rmtree(obsolete)
        except OSError as exc:
            raise OSError(f'Source version saved at {folder}; previous version cleanup failed for {obsolete}: {exc}') from exc
    remaining = [path for path in root.iterdir() if path.is_dir() and not path.name.startswith('.')]
    if len(remaining) > 3:
        lines.extend(f'Preserved source in use or modified: {path} ({", ".join(sorted(active.get(path, {"modified or unknown"})))})'
                     for path in sorted(remaining) if path not in latest)
    return lines


def reinstall_selection() -> dict[str, list[str]] | None:
    """A saved package uses current Core choices, or its saved choices after full removal/failed first install."""
    from installer.selection import installed_core_selection
    if not runner.REPO_ROOT.resolve().is_relative_to((locations.record_root() / 'versions').resolve()):
        return None
    saved = json.loads((runner.REPO_ROOT.parent / 'version.json').read_text())
    record_path = install_transaction.record_path_after_recovery(locations.record_root())
    current = installed_core_selection(installed_payload.read_file(record_path) or {}, record_path)
    selected = (current if saved.get('status') == 'successful' and current else saved.get('selections'))
    if not selected:
        raise ValueError('This saved package has no recorded Core selection; name the host and skills to reinstall')
    return selected


def _read_with_package(package: Path, ghost_root: Path, operation: str):
    program = 'import json,sys; sys.path.insert(0,sys.argv[1]); ' + operation
    result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(package / '_shared'), str(ghost_root)],
                            capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise ValueError(report.message('package_record_unreadable', package=package))
    return json.loads(result.stdout)


def check_record_reader(package: Path, ghost_root: Path, *, check_entry: bool = False) -> None:
    """Check that a package reads the same installation record before giving it write access."""
    expected = installed_payload.read(ghost_root)
    operation = 'import installed_payload; print(json.dumps(installed_payload.read(sys.argv[2])))'
    if check_entry:
        operation = 'from installer import cli; ' + operation
    if expected is not None and _read_with_package(package, ghost_root, operation) != expected:
        raise ValueError(report.message('package_record_unreadable', package=package))


def request_writer_needs_v2() -> bool:
    """A deployed request writer reads v2 after placement, even when another package is active."""
    request = install_request()
    return request is not None and not request.get('request_writer_reads_v3')


def check_previous_manager(source_fields: dict) -> dict:
    """Select the record the manager handling an interrupted placement must still be able to read."""
    import install_lock
    record = installed_payload.read(locations.record_root()) or {}
    active = record.get('installer', {}).get('active_root')
    recorded_source = record.get('source', {})
    relations = source_fields.get('ledger_source_roots', {})
    old_parent = request_writer_needs_v2()
    attributed = [fact for fact in relations.values() if isinstance(fact, dict)]
    if not active and not old_parent:
        return source_fields
    choices = [] if old_parent and attributed else [source_fields]
    if attributed and record.get('schema_version', 2) <= 2:
        # Only the first upgrade on the same source can temporarily expose a path-only view.
        origin = recorded_source.get('root')
        if (origin == source_fields.get('root')
                and not any(isinstance(fact, dict) for fact in recorded_source.get('ledger_source_roots', {}).values())
                and all(fact['source_root'] == origin for fact in attributed)):
            choices.append(dict(source_fields, ledger_source_roots=installed_payload.source_paths(relations)))
    with tempfile.TemporaryDirectory(prefix='ghost-alice-record-check-') as directory:
        for index, fields in enumerate(choices):
            if not active:
                return fields
            root = Path(directory) / str(index)
            with install_lock.command_lock(root / 'install.lock', owner='compatibility-check'):
                installed_payload._save(root, dict(record, source=dict(recorded_source, **fields)))
            try:
                check_record_reader(Path(active), root)
            except ValueError:
                continue
            return fields
    if active:
        # Offer the installed command only when the retained manager can still start and read today's record.
        try:
            check_record_reader(Path(active), locations.record_root(), check_entry=True)
        except ValueError as exc:
            raise ValueError(report.message('active_manager_unavailable', package=active, command=report.install_command())) from exc
    raise ValueError(report.message('previous_manager_incompatible', package=active or runner.REPO_ROOT,
                                   origin=recorded_source.get('root', active), command=locations.management_command()))
