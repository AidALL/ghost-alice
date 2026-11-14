"""The installation source checkout.

`match_install_source` is the one judgment of whether this checkout is the
checkout the install record names as its source; `check_install_source` acts on
it before an install or a repair changes anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING

import install_transaction
import installed_payload
import addon_registry
import content_digest
from installer_assets import unrecorded_copy_content
import file_lock
from installer import locations, report, runner
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


def check_source_health(*, skip: bool) -> bool:
    """Print the checkout's source health and return False only when the branch is behind its upstream.

    Skip → warn and pass; no git or not a worktree → warn and pass; `Source health: branch=,
    head=, upstream=`; diverged → two warnings; behind → four errors, False;
    ahead → warning; untracked → warning; tracked changes → warning.
    """
    root = runner.REPO_ROOT
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
    here = Path(runner.REPO_ROOT if checkout is None else checkout).resolve()
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
            answer = input("Replace the install source with this checkout? [y/N]: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() in ("y", "yes"):
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
    return runner.run_tool("_shared/installer_source_update.py", "--source-dir", str(runner.REPO_ROOT)).returncode


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
               *(locations.install_state_path(platform) for platform in locations.PLATFORMS)]
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


def _recorded_hook_path(value: str) -> HookPathChange | None:
    """The hook path change the installation records as writing `value`, else None. The relative `hooks` an earlier
    installer wrote names no folder, so its record counts only for a checkout of this repository."""
    for change in _hook_path_records():
        if change.get("after") != value:
            continue
        if value == HOOKS_DIR and not (isinstance(change.get("repo_root"), str)
                                       and _same_repository(Path(change["repo_root"]), runner.REPO_ROOT)):
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
    record = {"kind": HOOK_PATH_CHANGE, "repo_root": Path(runner.REPO_ROOT).resolve().as_posix(),
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
    root = runner.REPO_ROOT
    wanted = hooks_path_value(root)
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
    hook = root / HOOKS_DIR / "post-merge"
    if hook.is_file() and not os.access(hook, os.X_OK):
        try:
            hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            pass
    return change


def configured_hooks_path() -> str | None:
    """The checkout's `core.hooksPath`, or None when it is not set."""
    return _git_value(runner.REPO_ROOT, "config", "--local", "core.hooksPath") or None


def commit_of(path: Path) -> str:
    """The commit of the repository that holds `path` (an addon source), `unknown` without one."""
    return _git_value(Path(path), "rev-parse", "HEAD") or "unknown"


def resolve_source() -> SourceInfo:
    """The checkout's root, branch, head and dirty state for the install-state manifest."""
    root_path = runner.REPO_ROOT
    toplevel = _git_value(root_path, "rev-parse", "--show-toplevel")
    branch = _git_value(root_path, "symbolic-ref", "--quiet", "--short", "HEAD") or "DETACHED"
    head = _git_value(root_path, "rev-parse", "HEAD") or "unknown"
    if _git_value(root_path, "rev-parse", "--is-inside-work-tree") == "true":
        status = _git(root_path, "status", "--porcelain=v1", "--untracked-files=all")
        dirty = "dirty" if (status.stdout or "").strip() else "clean"
    else:
        dirty = "unknown"
    return SourceInfo(root=Path(toplevel) if toplevel else root_path, branch=branch, head=head, dirty=dirty)


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
    """Git's working files, the saved file list, or the declared paths of a plain source package."""
    versions = (locations.record_root() / 'versions').resolve()
    if root.is_relative_to(versions):
        folder = versions / root.relative_to(versions).parts[0]
        saved = json.loads((folder / 'version.json').read_text(encoding='utf-8'))
        prefix = root.relative_to(folder).as_posix() + '/'
        return sorted(name[len(prefix):] for name in saved['files'] if name.startswith(prefix))
    listed = _git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '.')
    if listed.ok:
        names = set(filter(None, (listed.stdout or '').split('\0')))
        for name in names:
            if (root / name).is_symlink() and (root / name).is_dir():
                raise ValueError(f'Source directory link cannot be preserved as fixed bytes: {root / name}')
        return sorted(name for name in names if (root / name).is_file())
    if addon:
        manifest = json.loads((root / 'addons-manifest.json').read_text(encoding='utf-8'))
        paths = ['addons-manifest.json', *(entry['path'] for entry in manifest['addons'])]
    else:
        from installer.selection import ALL_SKILLS
        catalog = root / 'skill-catalog/skills.json'
        roots = json.loads(catalog.read_text(encoding='utf-8')).get('install_roots', ALL_SKILLS) if catalog.is_file() else ALL_SKILLS
        paths = [*roots, '_shared', 'platforms', 'skill-catalog', 'hooks', 'scripts', '.claude/commands',
                 'install.sh', 'install.ps1', 'install.cmd', 'VERSION']
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
    for host in locations.PLATFORMS:
        for entry in addon_registry.read_all(addons_dir=locations.addons_dir(host)):
            references.append((entry.get('source'), host + ' addon'))
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
        saved = json.loads((folder / 'version.json').read_text(encoding='utf-8'))
        files = saved['files']
        if not isinstance(files, dict) or unrecorded_copy_content(folder, [*files, 'version.json']):
            return False
        return _version_hash(files) == folder.name and all(install_transaction._file_hash(folder / name) == digest
                                                          for name, digest in files.items())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def preserve_successful_source(ctx: 'Context') -> list[str]:
    """Keep the latest three distinct successful source versions; the caller completed a full Core install.

    A version contains actual working bytes and a file list for reinstalling its Core and selected addon sources.
    Reinstallation uses the saved install.sh/install.ps1 with --adopt-source. Active sources and modified archives
    stay even when that exceeds three; they are named in the returned report. No Git/index or host settings change.
    """
    root = locations.record_root() / 'versions'
    if root.is_symlink():
        raise ValueError(f'Source version root is a link: {root}')
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    record = installed_payload.read(locations.record_root()) or {}
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=root) as temporary:
        stage = Path(temporary)
        core_root = Path(runner.REPO_ROOT).resolve()
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
        # The source may have changed after placement verification. Only a source archive that still matches
        # this host's installed targets is a successful code version; unrelated earlier sources are not claimed.
        for destination, entry in record.get('targets', {}).items():
            if ctx.platform not in entry.get('used_by', []):
                continue
            original = Path(entry['source']).resolve()
            for source_root, relative in sorted(captured, key=lambda item: len(item[0].parts), reverse=True):
                if original.is_relative_to(source_root):
                    archived = stage / relative / original.relative_to(source_root)
                    if content_digest.tree(archived) != content_digest.tree(destination):
                        raise ValueError(f'Preserved source differs from installed code: {destination}')
                    break
        folder = root / _version_hash(files)
        saved = {'files': files, 'sources': sorted(sources), 'platforms': [ctx.platform]}
        if folder.is_symlink():
            raise ValueError(f'Preserved source version is a link: {folder}')
        if folder.exists():
            previous = json.loads((folder / 'version.json').read_text(encoding='utf-8'))
            if previous['files'] != files or any(install_transaction._file_hash(folder / name) != digest for name, digest in files.items()):
                raise ValueError(f'Preserved source version has changed: {folder}')
            saved['platforms'] = sorted(set(previous.get('platforms', [])) | {ctx.platform})
        else:
            # The complete file set moves into place; a failed copy never becomes a saved version.
            install_transaction._write_json(stage / 'version.json', saved)
            stage.rename(folder)
        install_transaction._write_json(folder / 'version.json', saved)
        os.utime(folder, None)
    command = ['pwsh', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(folder / 'core/install.ps1')] if os.name == 'nt' else ['bash', str(folder / 'core/install.sh')]
    command += ['--platform', ctx.platform, '--adopt-source']
    for selected in sorted(source for source in sources if source != 'core'):
        command += ['--addon-source', str(folder / selected)]
    lines = [f'Successful source version: {folder}', 'Reinstall this code: ' + (subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command))]
    try:
        active = _active_versions(root, record)
    except (OSError, ValueError, addon_registry.RegistryError, install_transaction.InstallTransactionError) as exc:
        return [*lines, f'Source versions retained because references could not be checked: {exc}']
    active.setdefault(folder, set()).add(ctx.platform + ' latest success')
    successful = sorted((path for path in root.iterdir() if path.is_dir() and not path.is_symlink()
                         and (path / 'version.json').is_file()),
                        key=lambda path: (path / 'version.json').stat().st_mtime_ns, reverse=True)
    latest = set(successful[:3])
    for obsolete in successful[3:]:
        if obsolete in active or not _unchanged_version(obsolete):
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
