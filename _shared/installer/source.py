"""The installation source checkout.

Port of `installer_lib/source_health.sh`, `installer_lib/install.sh:538-551`
(`_install_state_resolve_source`) and `installer_lib/bootstrap.sh:5-10`.
`match_install_source` is the one judgment of whether this checkout is the
checkout the install record names as its source; `check_install_source` acts on
it before an install or a repair changes anything.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import TYPE_CHECKING

import install_transaction
import installed_payload
from installer import locations, report, runner
from installer.runner import InstallerExit

if TYPE_CHECKING:
    from installer.cli import Context

HOOKS_DIR = "hooks"
# The install-state `system_env_changes` kind of the repository's shared hook path.
HOOK_PATH_CHANGE = "source_repo_hook_path"
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


def assert_session_gate_contract() -> None:
    """`skill-catalog/session-gates.json` must exist in the checkout, else
    `Session gate contract file missing: <path>` and exit 1 (`bootstrap.sh:5-10`)."""
    path = runner.REPO_ROOT / "skill-catalog" / "session-gates.json"
    if not path.is_file():
        report.error(f"Session gate contract file missing: {path}")
        raise InstallerExit(1)


def check_source_health(*, skip: bool) -> bool:
    """Print the checkout's source health and return False only when the branch is behind its upstream.

    Same messages and decisions as `source_health.sh:5-79`: skip → warn and
    pass; no git or not a worktree → warn and pass; `Source health: branch=,
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
                             "before installing. If this local source is intentional, rerun with --skip-source-health.")
                report.error(f"To preserve source-local changes while updating, run {report.install_command()} --update-source.")
                report.error("If an old checkout is already blocked by local changes during git pull, use the bootstrap "
                             "one-command update in docs/getting-started/troubleshooting.md.")
                return False
            elif ahead > 0:
                report.warn("Source branch is ahead of its upstream; continuing because local commits may be intentional.")
        else:
            report.warn("Source upstream comparison failed; continuing with local source health checks only.")
    if untracked:
        report.warn("Source tree has untracked files; continuing because tracked files are clean.")
    if tracked_dirty:
        report.warn("Source tree has tracked local changes; continuing. Installed target user edits remain protected "
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
    judging again; the answer is kept for `recheck_install_source`. The command's first publish records this
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
    """`installer_source_update.py --source-dir <checkout>`; its exit code (`source_health.sh:81-89`)."""
    return runner.run_tool("_shared/installer_source_update.py", "--source-dir", str(runner.REPO_ROOT)).returncode


def hooks_path_value(checkout: Path) -> str:
    """The `core.hooksPath` an install from `checkout` writes: the checkout's hooks folder by absolute path, links
    followed."""
    return (Path(checkout).resolve() / HOOKS_DIR).as_posix()


def _recorded_hook_path(value: str) -> HookPathChange | None:
    """The hook path change an install-state of either host records as writing `value`, else None."""
    for platform in locations.PLATFORMS:
        try:
            state = json.loads(locations.install_state_path(platform).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        changes = state.get("system_env_changes") if isinstance(state, dict) else None
        for change in changes if isinstance(changes, list) else []:
            if isinstance(change, dict) and change.get("kind") == HOOK_PATH_CHANGE and change.get("after") == value:
                before_present = change.get("before_present") is True and isinstance(change.get("before"), str)
                return HookPathChange(changed=False, recorded=True, before_present=before_present,
                                      before=change["before"] if before_present else "", after=value,
                                      applied_at=change.get("applied_at"))
    return None


def setup_git_hooks() -> HookPathChange:
    """Write the repository's shared `core.hooksPath` as this checkout's hooks folder by absolute path, make
    `hooks/post-merge` executable, and return what the install-state records (`source_health.sh:91-116`).

    Every worktree of the repository inherits the shared value (`git config --local` writes the configuration the
    worktrees share), so a merge in any worktree runs the install source's post-merge hook and no other checkout's
    hook file; a worktree's own setting (`git config --worktree`) still comes first. The value the full uninstall
    restores is the value from before Ghost-ALICE wrote one: none for an unset value or for the relative `hooks` an
    earlier installer wrote; the recorded value when an install-state of either host names the earlier value as
    written by an install, such as a reinstall or an install source that moved; else the earlier value itself. A
    value already in place that no install-state names leaves the value to restore unknown, and the uninstall
    leaves the hook path as it is.
    """
    root = runner.REPO_ROOT
    wanted = hooks_path_value(root)
    found = _git(root, "config", "--local", "--get", "core.hooksPath")
    current = found.stdout.strip() if found.ok and found.stdout.strip() else None
    if current == wanted:
        change = _recorded_hook_path(wanted) or HookPathChange(changed=False, recorded=False, after=wanted)
    elif not _git(root, "config", "--local", "core.hooksPath", wanted).ok:
        report.warn("Failed to set Git hook path. Post-merge hook may not fire")
        return HookPathChange(changed=False, recorded=False)
    else:
        earlier = _recorded_hook_path(current) if current not in (None, HOOKS_DIR) else None
        if earlier is not None:
            change = HookPathChange(changed=True, recorded=True, before_present=earlier.before_present,
                                    before=earlier.before, after=wanted)
        else:
            kept = current not in (None, HOOKS_DIR)
            change = HookPathChange(changed=True, recorded=True, before_present=kept, before=current if kept else "",
                                    after=wanted)
        report.info(f"Git hook path → {wanted} (post-merge auto-refresh enabled)")
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
    """The checkout's root, branch, head and dirty state for the install-state manifest (`install.sh:538-551`)."""
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
