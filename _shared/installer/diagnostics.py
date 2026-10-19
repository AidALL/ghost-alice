"""`--status` and `--doctor`: read-only inspection of an installation.

Port of `installer_lib/uninstall.sh:142-267`. Doctor no longer resumes an
interrupted addon uninstall: it reports the leftover marker, and the next
install or uninstall command resumes the removal. Both commands report the
install record and how the installed targets differ from it; the record does
not change their result.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import content_digest
import installed_payload
from installer import hosts, locations, report, runner, source

if TYPE_CHECKING:
    from installer.cli import Context

_GREEN, _YELLOW, _RED, _RESET = "\033[0;32m", "\033[0;33m", "\033[0;31m", "\033[0m"


def _mark(symbol: str, color: str) -> str:
    return f"{color}{symbol}{_RESET}" if report.colors_enabled() else symbol


def print_target_status(label: str, install_path: Path, indent: str = "  ") -> None:
    """`●` link with its target, `■` copied, `○` not installed (`uninstall.sh:142-155`)."""
    path = Path(install_path)
    if path.is_symlink():
        try:
            target = os.readlink(path)
        except OSError:
            target = "?"
        report.emit(f"{indent}{_mark('●', _GREEN)} {label}  →  {target}")
    elif path.is_dir():
        report.emit(f"{indent}{_mark('■', _YELLOW)} {label}  (copied)")
    else:
        report.emit(f"{indent}{_mark('○', _RED)} {label}  (not installed)")


def run_install_doctor(ctx: "Context", mode: str, skills_dir: Path) -> int:
    """`install_doctor.py` with the platform's hook config, global rule and every
    core target; `--strict` in doctor mode (`uninstall.sh:157-218`)."""
    from installer import selection

    skills_dir = Path(skills_dir)
    ghost = locations.home() / ".ghost-alice"
    args = ["--platform", ctx.platform, "--repo-root", str(source.resolve_source().root),
            "--encoding-root", str(runner.REPO_ROOT), "--encoding-root", str(skills_dir),
            "--ghost-alice-root", str(ghost),
            "--install-state-manifest", str(ghost / "install-state" / f"{ctx.platform}.json"),
            "--runtime-shared", str(locations.runtime_shared_dir()), "--skills-root", str(skills_dir)]
    if mode == "doctor":
        args.append("--strict")
    if ctx.platform == "codex":
        codex_home = locations.codex_home()
        args.extend(["--hook-config", str(codex_home / "hooks.json"),
                     "--global-rule", "codex-bootstrap", str(codex_home / "AGENTS.md"),
                     hosts.CODEX_BOOTSTRAP_MARKER, hosts.CODEX_MANAGED_BLOCK_BEGIN, hosts.CODEX_MANAGED_BLOCK_END])
    elif ctx.platform == "claude":
        claude_home = locations.claude_home()
        args.extend(["--hook-config", str(claude_home / "settings.json"),
                     "--global-rule", "claude-bootstrap", str(claude_home / "CLAUDE.md"),
                     hosts.CLAUDE_BOOTSTRAP_MARKER, hosts.CLAUDE_MANAGED_BLOCK_BEGIN, hosts.CLAUDE_MANAGED_BLOCK_END])
    if (runner.REPO_ROOT / "_shared").is_dir():
        args.extend(["--target", "_shared", str(skills_dir / "_shared")])
    for skill in selection.ALL_SKILLS:
        for target in selection.expand_skill_targets(skill):
            args.extend(["--target", target.name, str(skills_dir / target.name)])
    report.emit("")
    report.info("Running installer doctor diagnostics...")
    return runner.run_tool("_shared/install_doctor.py", *args).returncode


def _names(label: str, names: list[str], limit: int = 3) -> str:
    shown = ", ".join(names[:limit])
    more = f" +{len(names) - limit} more" if len(names) > limit else ""
    return f"{label} {shown}{more}"


def report_install_record(ctx: "Context") -> None:
    """One summary line for this platform's targets in the install record, then one line per target
    that differs from it; targets without a baseline are counted, not compared. Read-only."""
    root = locations.record_root()
    record_path = installed_payload.path(root)
    try:
        record = installed_payload.read(root)
    except installed_payload.RecordError as exc:
        report.warn(f"Install record unreadable ({record_path}): {exc}")
        return
    if record is None:
        report.info(f"Install record: none yet; the next install writes {record_path}")
        return
    entries = {dest: entry for dest, entry in sorted(record.get("targets", {}).items())
               if isinstance(entry, dict) and ctx.platform in (entry.get("used_by") or [])}
    modes = [entry.get("mode") for entry in entries.values()]
    baselines = [entry.get("baseline") for entry in entries.values()]
    last = (record.get("last_transaction") or {}).get("id") or "none"
    report.info(f"Install record ({record_path}): {len(entries)} {ctx.platform} targets (link {modes.count('link')}, "
                f"copy {modes.count('copy')}; baseline recorded {baselines.count('recorded')}, migrated "
                f"{baselines.count('migrated')}, unknown {baselines.count('unknown')}); last publish {last}")
    differing = 0
    for dest, entry in entries.items():
        if entry.get("baseline") == "unknown":
            continue
        diff = content_digest.compare(entry, Path(dest))
        if diff["link_target_differs"]:
            details = "link target differs"
        else:
            details = "; ".join(_names(label, diff[label]) for label in ("missing", "changed", "extra") if diff[label])
        if details:
            differing += 1
            report.warn(f"Differs from the install record: {dest} ({details})")
    if not differing:
        report.ok("Installed targets match the install record")


def check_status(ctx: "Context") -> int:
    """Target listing, doctor in status mode, hook status (`uninstall.sh:220-246`)."""
    from installer import selection

    skills_dir = Path(ctx.skills_dir)
    report.emit(f"Install status ({skills_dir}):")
    report.emit("")
    print_target_status("_shared", skills_dir / "_shared")
    for skill in selection.ALL_SKILLS:
        targets = selection.expand_skill_targets(skill)
        if not targets:
            report.emit(f"  {_mark('○', _RED)} {skill}  (source missing)")
            continue
        if targets[0].name == skill and len(targets) == 1:
            print_target_status(skill, skills_dir / skill)
            continue
        report.emit(f"  {skill} (family)")
        for target in targets:
            print_target_status(target.name, skills_dir / target.name, "    ")
    report_install_record(ctx)
    run_install_doctor(ctx, "status", skills_dir)
    return 0 if hosts.install_hooks(ctx, "status", ctx.platform) else 1


def run_doctor(ctx: "Context") -> int:
    """Leftover `.removing` markers → warning and rc 1; then doctor strict and hook status (`uninstall.sh:248-267`, without the resume)."""
    skills_dir = Path(ctx.skills_dir)
    report.emit(f"Installer doctor ({skills_dir}):")
    report_install_record(ctx)
    rc = 0
    addons_dir = locations.home() / ".ghost-alice" / "addons" / ctx.platform
    if addons_dir.is_dir() and any(addons_dir.glob("*.json.removing")):
        report.warn("A pending addon uninstall could not complete (manual review needed); see .removing markers "
                    "under ~/.ghost-alice/addons")
        rc = 1
    doctor_rc = run_install_doctor(ctx, "doctor", skills_dir)
    if doctor_rc != 0:
        rc = doctor_rc
    if not hosts.install_hooks(ctx, "status", ctx.platform):
        rc = 1
    return rc
