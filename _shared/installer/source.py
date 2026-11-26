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
import shutil
import stat
import sys
import tempfile
import contextlib
from typing import TYPE_CHECKING

import install_transaction
import installed_payload
import addon_registry
import content_digest
from installer_assets import is_link_or_reparse_point, unrecorded_copy_content
from installer import locations, report, runner, terminal
from installer.runner import InstallerExit

if TYPE_CHECKING:
    from installer.cli import Context

RELEASE_REF = "refs/heads/main"
DEFAULT_REMOTE = "https://github.com/AidALL/ghost-alice.git"
ADOPT_HINT = ("Rerun this command from the install source, or add --adopt-source to record this checkout "
              "as the install source.")
REPAIR_HINT = ("Repair runs only from the install source; run the install from this checkout with --adopt-source to "
               "move the installation here.")
REQUEST_ENV = "GHOST_ALICE_PACKAGE_REQUEST"
# Installers that activate a package after placement send their request under this name.
EARLIER_REQUEST_ENV = "GHOST_ALICE_INSTALL_REQUEST"


@dataclass(frozen=True)
class SourceInfo:
    root: Path        # `git rev-parse --show-toplevel`, else the script dir
    branch: str       # short branch name, `DETACHED` without one
    head: str         # full commit id, `unknown` without git
    dirty: str        # `dirty`, `clean` or `unknown`


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
                             "before installing. If the human user decided to install these local files, rerun with --local-source --skip-source-health.")
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


def registration(record: dict | None) -> dict | None:
    """The recorded checkout and effective update remote, or None before an installation records a checkout."""
    origin = (record or {}).get('source', {})
    root = origin.get('root')
    if isinstance(root, str) and root:
        return {'root': root, 'remote': origin.get('remote') or DEFAULT_REMOTE}
    return None


def match_install_source(checkout: Path | None = None, *, record=...) -> SourceMatch:
    """Compare `checkout` (the checkout the installer runs from, `runner.REPO_ROOT`, when not given) with the
    install record's `source.root`, both with their links followed.

    The record is read as it will be once every unfinished publish is restored: the comparison runs before
    the lock, and a publish that stopped halfway may have written its own source already. The installer's
    check before an install or a repair and the post-merge step, which names the checkout the merge ran in,
    ask this function, so the two never disagree about which checkout is the install source.
    """
    here = Path(resolve_source().root if checkout is None else checkout).resolve()
    try:
        if record is ...:
            record = install_transaction.record_after_recovery(locations.record_root())
    except (installed_payload.RecordError, install_transaction.InstallTransactionError) as exc:
        return SourceMatch("unreadable", here, detail=str(exc))
    registered = registration(record)
    if registered is None:
        return SourceMatch("unrecorded", here)
    recorded = Path(registered['root'])
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
def acquire_release(remote: str):
    """Fetch the release branch into a temporary checkout owned by this install."""
    with tempfile.TemporaryDirectory(prefix='ghost-alice-update-') as temporary:
        root = Path(temporary)
        for args in (('init', '--quiet'), ('fetch', '--quiet', '--depth=1', '--', remote, RELEASE_REF),
                     ('checkout', '--quiet', '--detach', 'FETCH_HEAD')):
            result = _git(root, *args)
            if not result.ok:
                raise ValueError(f'Cannot acquire the Core release ({RELEASE_REF}): {result.stderr.strip() or result.stdout.strip()}')
        yield root


def release_input(checkout: Path, origin: dict) -> dict:
    """Keep the development path for ledger ownership, and identify the fetched code."""
    commit = _git_value(checkout, 'rev-parse', 'HEAD')
    if not commit:
        raise ValueError('Cannot identify the downloaded Core release commit')
    return dict(root=origin['root'], remote=origin['remote'], ref=RELEASE_REF, branch='main', commit=commit, dirty='clean')


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


def saved_package(root: Path) -> bool:
    """Whether `root` is inside the folder of the saved versions."""
    return root.resolve().is_relative_to((locations.record_root() / 'versions').resolve())


def input_record(input_root: Path | None = None, *, record=...) -> dict:
    """Development origin and acquisition coordinates, separate from this process's assets."""
    request = install_request() if input_root is None else None
    if request:
        return dict(request['source'])
    root_path = Path(input_root) if input_root is not None else runner.REPO_ROOT
    if saved_package(root_path):
        try:
            # As `match_install_source` reads the record: a publish that stopped halfway may have written its own source.
            if record is ...:
                record = install_transaction.record_after_recovery(locations.record_root())
        except (installed_payload.RecordError, install_transaction.InstallTransactionError):
            record = {}
        metadata = root_path.parent / 'version.json'
        try:
            saved = json.loads(metadata.read_text())
        except FileNotFoundError:
            raise _missing_version_error(metadata) from None
        origin = dict(saved['source'])
        origin.pop('ledger_source_roots', None)
        origin.update({key: value for key, value in (record or {}).get('source', {}).items()
                       if key in {'root', 'remote'}})
        origin['ref'] = RELEASE_REF
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
    remote = _git_value(root, 'remote', 'get-url', 'origin')
    if remote and not Path(remote).is_absolute() and ':' not in remote:
        remote = str((root / remote).resolve())
    return {'root': str(root), 'commit': head, 'dirty': dirty, 'branch': branch,
            'remote': remote or DEFAULT_REMOTE, 'ref': RELEASE_REF}


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


def _version_file_hashes(root: Path, destination: Path | None = None, *, addon: bool = False) -> dict[str, str]:
    """Hash the product files and, when capturing a version, verify their staged copies."""
    files = {}
    for name in _version_source_files(root, addon=addon):
        original = root / name
        if not original.resolve().is_relative_to(root):
            raise ValueError(f'Source entry escapes its package: {original}')
        before = install_transaction._file_hash(original)
        if destination is not None:
            copy = destination / name
            if not copy.resolve().is_relative_to(destination.resolve()):
                raise ValueError(f'Source entry escapes its package: {original}')
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
                and not _missing_version_entries(folder, files))
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
    if not _version_entry_exists(metadata, directory=False):
        raise _missing_version_error(metadata)
    saved = json.loads(metadata.read_text(encoding='utf-8'))
    files = saved.get('files') if isinstance(saved, dict) else None
    if (isinstance(files, dict) and files and _version_hash(files) == folder.name
            and all(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest) for digest in files.values())):
        return saved
    raise ValueError(f'Preserved source file list is missing or has changed: {metadata}')


def _has_installer(package: Path) -> bool:
    return locations.package_script(package).is_file() and (package / '_shared/installer/cli.py').is_file()


def _registered_checkout(record: dict) -> Path | None:
    root = record.get('source', {}).get('root')
    if isinstance(root, str) and Path(root).is_absolute():
        checkout = Path(root)
        if not saved_package(checkout) and _has_installer(checkout):
            return checkout
    return None


def _replacement_hint(record: dict, package: Path | None) -> str:
    core_skills = any(entry.get('kind') == 'skill' and entry.get('provider') == 'core'
                      for entry in record.get('targets', {}).values())
    if core_skills and record.get('source', {}).get('root') and package and _has_installer(package):
        return report.message('replacement_update', command=locations.package_command(package, 'update'))
    return report.message('replacement_from_checkout')


def _missing_version_error(path: Path) -> ValueError:
    record = install_transaction.record_after_recovery(locations.record_root()) or {}
    key = 'version_metadata_missing' if path.name == 'version.json' else 'active_version_missing'
    return ValueError(report.message(key, path=path, replacement=_replacement_hint(record, _registered_checkout(record))))


def _missing_version_entries(folder: Path, files: dict[str, str]) -> list[Path]:
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
        elif relative in regular and install_transaction._file_hash(path) != regular[relative]:
            raise ValueError(f'Preserved source version has changed: {path}')
    return missing


def active_root() -> str | None:
    """The active installer that the install record names. Only when it names none, the record that recovery would
    restore: a later release can have written journals that this package cannot read."""
    root = locations.record_root()
    try:
        active = (installed_payload.read(root) or {}).get('installer', {}).get('active_root')
    except installed_payload.RecordError:
        active = None
    return active or (install_transaction.record_after_recovery(root) or {}).get('installer', {}).get('active_root')


def _active_version(root: Path) -> Path | None:
    active = active_root()
    if active:
        package = Path(active)
        if package.name == 'core' and package.parent.parent == root:
            return package.parent
    return None


def _version_root() -> Path:
    root = locations.record_root() / 'versions'
    if is_link_or_reparse_point(root):
        raise ValueError(f'Source version root is a link: {root}')
    return root.resolve()


def active_installer(command: str) -> Path | None:
    """The Core directory of the active package while another installer runs, that package is complete and its version
    file states that its installer runs `command` for another installer (`active_runs`). A package that states
    nothing or that lost files leaves the command to the installer that was started. The active installer chooses and
    copies what the command restores; the statement is a promise between releases, so the started installer passes only
    the arguments of `auto.child_command`."""
    active = _active_version(_version_root())
    if active is None or _same_folder(active / 'core', runner.REPO_ROOT) or not _version_entry_exists(active, directory=True):
        return None
    saved = _version_metadata(active)
    if command not in saved.get('active_runs', ()) or _missing_version_entries(active, saved['files']):
        return None
    return active / 'core'


def repair_active_source(ctx: 'Context') -> Path:
    """Restore the active package and return its Core directory. Older installations have only the checkout.
    The command's addon sources restore lost addon files of the package: a repair copies no addon source.
    The package is no input for an entry that it lost: the repair then names the entry and the input that restores it."""
    active = _active_version(_version_root())
    if active is None:
        return runner.REPO_ROOT
    if not _version_entry_exists(active, directory=True):
        raise _missing_version_error(active)
    saved = _version_metadata(active)
    files = saved['files']
    lost = _missing_version_entries(active, files)
    lost_addon = any(entry.parts[0] == 'addons' for entry in lost)
    addon_paths = sorted({str(active.joinpath(*Path(name).parts[:2])) for name in files if name.startswith('addons/')})
    sources = ctx.addon_sources if lost_addon and ctx.addon_sources else addon_paths
    inputs = [Path(folder).resolve() for folder in (runner.REPO_ROOT, *sources)]
    for entry in lost:
        path = active.resolve() / entry
        if any(folder == path or folder in path.parents or path in folder.parents for folder in inputs):
            if entry.parts[0] == 'addons':
                raise ValueError(report.message('active_addon_lost', package=active / 'core', file=active / entry))
            # The registered checkout holds the same Core files unless it changed after the installation.
            record = install_transaction.record_after_recovery(locations.record_root()) or {}
            checkout = _registered_checkout(record)
            restore = (report.message('restore_from_registered', repair=locations.package_command(
                checkout, '--repair', '--platform', ctx.platform))
                if checkout is not None
                else report.message('restore_from_checkout'))
            raise ValueError(report.message('active_core_lost', package=active / 'core', file=active / entry,
                                            restore=restore,
                                            replacement=_replacement_hint(record, runner.REPO_ROOT)))
    if lost:
        prepare_source(replace(ctx, addon_sources=sources), required_version=active)
    elif not _same_folder(active / 'core', runner.REPO_ROOT):
        # Older versions may leave repair to this checkout. Compare all its product bytes
        # with the verified Core file list before using the saved package for placement.
        expected = {name.removeprefix('core/'): digest for name, digest in files.items() if name.startswith('core/')}
        if _version_file_hashes(runner.REPO_ROOT.resolve()) != expected:
            raise ValueError(report.message('active_version_mismatch', package=active / 'core',
                                           update=locations.package_command(runner.REPO_ROOT, 'update')))
    # The bytes just matched this release. Its commit identifies any Core file repair restores.
    ctx.record_source = saved['source']
    return active / 'core'


def check_saved_package(folder: Path) -> None:
    """Refuse a saved package whose files are missing or changed."""
    missing = _missing_version_entries(folder, _version_metadata(folder)['files'])
    if missing:
        raise ValueError(f'Preserved source version is incomplete: {folder / missing[0]}')


def prepare_source(ctx: 'Context', *, provenance: dict | None = None,
                   selections: dict[str, list[str]] | None = None,
                   required_version: Path | None = None, active_runs: tuple[str, ...] = ()) -> tuple[Path, dict[str, str]]:
    """Capture the requested product bytes once without declaring installation success. The version file states
    `active_runs`, the commands that this package's installer runs for another installer."""
    root = _version_root()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=root) as temporary:
        stage = Path(temporary)
        core_root = runner.REPO_ROOT.resolve()
        core = _version_file_hashes(core_root, stage / 'core')
        files = {'core/' + name: digest for name, digest in core.items()}
        sources = ['core']
        captured = [(core_root, 'core')]
        for index, selected in enumerate(dict.fromkeys(ctx.addon_sources)):
            candidate = stage / f'addon-{index}'
            addon = _version_file_hashes(Path(selected).resolve(), candidate, addon=True)
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
                 'selections': selections, 'active_runs': list(active_runs),
                 'source': provenance if provenance is not None else input_record(),
                 'origins': {relative: str(original) for original, relative in captured}}
        active = required_version or _active_version(root)
        if required_version and folder != required_version:
            raise ValueError(report.message('active_version_mismatch', package=active / 'core',
                                            update=locations.package_command(runner.REPO_ROOT, 'update')))
        if _version_entry_exists(folder, directory=True):
            earlier = _version_metadata(folder)
            if earlier['files'] != files:
                raise ValueError(f'Preserved source version has changed: {folder}')
            missing = _missing_version_entries(folder, files)
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


def finalize_source(ctx: 'Context', folder: Path, platforms: list[str]) -> list[str]:
    """After the requested placements succeed, commit the success, then prune old successes."""
    root = (locations.record_root() / 'versions').resolve()
    version_file = folder / 'version.json'
    check_saved_package(folder)
    saved = _version_metadata(folder)
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
    saved.update(status='successful', platforms=sorted(set(saved.get('platforms', [])) | set(platforms)),
                 selections=selections,
                 completed_at=datetime.now(timezone.utc).isoformat())
    sections = {}
    if record.get('source', {}).get('root'):
        from installer.transaction import installed_ledger_locations
        observed = installed_ledger_locations()
        if observed.observation_errors:
            raise ValueError('; '.join(f'{path}: {cause}' for path, cause in observed.observation_errors.items()))
        sections['source'] = observed.publication_source({'root': record['source']['root']})
    with install_transaction.record_update(locations.record_root(), version_file=version_file) as journal:
        install_transaction._write_json(version_file, saved)
        installed_payload.update(locations.record_root(), sections, journal=journal)
    record = installed_payload.read(locations.record_root()) or {}
    # The line names no host: the package then reinstalls each installed host's Core selection, or the selection that
    # this version file saved while no Core skill is installed. A host here would narrow the line to that host or add
    # every skill of it.
    arguments = ['--local-source', '--adopt-source']
    for selected in sorted(source for source in saved['sources'] if source != 'core'):
        arguments += ['--addon-source', str(folder / selected)]
    lines = [f'Successful source version: {folder}',
             report.message('reinstall_command', command=locations.package_command(folder / 'core', *arguments))]
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


def drop_unfinished_choices(host: str, names: list[str] | None = None) -> None:
    """Drop the skills `names` of `host`, or the whole host, from the choices that the active package keeps for an
    installation that did not finish."""
    from installer.selection import leaf_names
    try:
        folder = _active_version(_version_root())
        saved = _version_metadata(folder) if folder else {}
    except (OSError, ValueError):
        return  # `reinstall_selection` reads no choices of such a package.
    choices = dict(saved.get('selections') or {})
    if saved.get('status') != 'candidate' or host not in choices:
        return
    chosen = leaf_names(choices.pop(host))
    left = [name for name in chosen if names is not None and name not in names]
    if left:
        choices[host] = left
    install_transaction._write_json(folder / 'version.json', dict(saved, selections=choices))


def recorded_core() -> tuple[dict, dict[str, list[str]]]:
    """Read the installation that lock recovery will restore and the Core skills it records."""
    from installer.selection import installed_core_selection
    record_path = install_transaction.record_path_after_recovery(locations.record_root())
    record = installed_payload.read_file(record_path) or {}
    return record, installed_core_selection(record, record_path)


def for_platform(choices: dict, platform: str | None) -> dict:
    if platform is None:
        return choices
    return {platform: choices[platform]} if platform in choices else {}


def reinstall_selection(package: Path | None, *, core: tuple[dict, dict] | None = None,
                        platform: str | None = None) -> dict | None:
    """Update (no package) selects installed skills. A saved package also resumes its own unfinished choices."""
    from installer.selection import leaf_names
    if package is not None and not saved_package(package):
        return None
    record, installed = core if core is not None else recorded_core()
    if package is None:
        selected = for_platform(installed, platform)
        if not selected:
            raise ValueError('Update found no installed Core skills to update')
        return selected
    saved = json.loads((package.parent / 'version.json').read_text())
    selected = dict(installed)
    active = record.get('installer', {}).get('active_root')
    if not selected or saved.get('status') == 'candidate' and active and _same_folder(Path(active), package):
        for host, names in (saved.get('selections') or {}).items():
            selected[host] = leaf_names([*selected.get(host, ()), *names])
    if not selected:
        raise ValueError('This saved package has no recorded Core selection; name the host and skills to reinstall')
    return for_platform(selected, platform) or None
