"""The installation source checkout.

Port of `installer_lib/source_health.sh`, `installer_lib/install.sh:538-551`
(`_install_state_resolve_source`) and `installer_lib/bootstrap.sh:5-10`.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat

from installer import report, runner
from installer.runner import InstallerExit

HOOKS_DIR = "hooks"


@dataclass(frozen=True)
class SourceInfo:
    root: Path        # `git rev-parse --show-toplevel`, else the script dir
    branch: str       # short branch name, `DETACHED` without one
    head: str         # full commit id, `unknown` without git
    dirty: str        # `dirty`, `clean` or `unknown`


@dataclass(frozen=True)
class HookPathChange:
    changed: bool
    before_present: bool
    before: str
    after: str = HOOKS_DIR


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


def update_source() -> int:
    """`installer_source_update.py --source-dir <checkout>`; its exit code (`source_health.sh:81-89`)."""
    return runner.run_tool("_shared/installer_source_update.py", "--source-dir", str(runner.REPO_ROOT)).returncode


def setup_git_hooks() -> HookPathChange:
    """Set the checkout's `core.hooksPath=hooks` when it differs, record the change
    for the install-state manifest, make `hooks/post-merge` executable (`source_health.sh:91-116`)."""
    root = runner.REPO_ROOT
    current = _git(root, "config", "--local", "core.hooksPath")
    current_path = current.stdout.strip() if current.ok and current.stdout else ""
    if current_path != HOOKS_DIR:
        if not _git(root, "config", "--local", "core.hooksPath", HOOKS_DIR).ok:
            report.warn("Failed to set Git hook path. Post-merge hook may not fire")
            return HookPathChange(changed=False, before_present=current.ok, before=current_path)
        change = HookPathChange(changed=True, before_present=current.ok, before=current_path if current.ok else "")
        report.info(f"Git hook path → {HOOKS_DIR} (post-merge auto-refresh enabled)")
    else:
        change = HookPathChange(changed=False, before_present=True, before=current_path)
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
