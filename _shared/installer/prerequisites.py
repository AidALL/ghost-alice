"""git and node: find them or try the host package manager (port of `installer_lib/prerequisites.sh`).

A later change moves this behind a `prerequisites` command; the install still runs it first.
"""

from __future__ import annotations

import os
import shutil

from installer import report, runner

PACKAGES = {
    "git": dict(brew="git", apt="git", dnf="git", yum="git", pacman="git",
                winget="Git.Git", choco="git", scoop="git"),
    "node": dict(brew="node", apt="nodejs", dnf="nodejs", yum="nodejs", pacman="nodejs",
                 winget="OpenJS.NodeJS.LTS", choco="nodejs-lts", scoop="nodejs-lts"),
}


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    try:
        return geteuid() == 0 if geteuid is not None else False
    except OSError:
        return False


def run_as_root_or_sudo(argv: list[str]) -> int:
    """Run directly as root, through `sudo` when available, else directly (`prerequisites.sh:9-17`)."""
    if not _is_root() and command_exists("sudo"):
        argv = ["sudo", *argv]
    return runner.run_command(argv).returncode


def manual_notice(tool: str) -> None:
    """The per-tool warning block (`prerequisites.sh:19-42`)."""
    if tool == "git":
        report.warn("Git was not found and automatic setup did not complete. Install Git, then rerun the installer.")
        report.warn("  macOS:   brew install git")
        report.warn("  Ubuntu:  sudo apt install git")
        report.warn("  Fedora:  sudo dnf install git")
        report.warn("  Arch:    sudo pacman -Sy git")
        report.warn("  Windows: winget install --id Git.Git --exact")
    elif tool == "node":
        report.warn("Node.js was not found on PATH. Ghost-ALICE can continue with a configured Codex runtime, "
                    "but full capability needs Node.js installed on PATH.")
        report.warn("  Download: https://nodejs.org/en/download")
        report.warn("  macOS:    brew install node")
        report.warn("  Ubuntu:   sudo apt install nodejs")
        report.warn("  Fedora:   sudo dnf install nodejs")
        report.warn("  Arch:     sudo pacman -Sy nodejs")
        report.warn("  Windows:  winget install --id OpenJS.NodeJS.LTS --exact")
    else:
        report.warn(f"Prerequisite '{tool}' is missing and automatic setup did not complete.")


def _attempt(tool: str, label: str, run) -> bool:
    report.info(label)
    if run():
        return True
    manual_notice(tool)
    return False


def try_install_unix_prerequisite(tool: str) -> bool:
    """brew → apt-get → dnf → yum → pacman → winget → choco → scoop, first found wins (`prerequisites.sh:44-156`)."""
    packages = PACKAGES.get(tool)
    if packages is None:
        manual_notice(tool)
        return False
    if command_exists(tool):
        return True
    report.info(f"{tool} not found; trying automatic prerequisite setup.")
    if command_exists("brew"):
        return _attempt(tool, f"brew install {packages['brew']}",
                        lambda: runner.run_command(["brew", "install", packages["brew"]]).ok)
    if command_exists("apt-get"):
        return _attempt(tool, f"apt-get install -y {packages['apt']}",
                        lambda: run_as_root_or_sudo(["apt-get", "update"]) == 0
                        and run_as_root_or_sudo(["apt-get", "install", "-y", packages["apt"]]) == 0)
    if command_exists("dnf"):
        return _attempt(tool, f"dnf install -y {packages['dnf']}",
                        lambda: run_as_root_or_sudo(["dnf", "install", "-y", packages["dnf"]]) == 0)
    if command_exists("yum"):
        return _attempt(tool, f"yum install -y {packages['yum']}",
                        lambda: run_as_root_or_sudo(["yum", "install", "-y", packages["yum"]]) == 0)
    if command_exists("pacman"):
        return _attempt(tool, f"pacman -Sy --noconfirm {packages['pacman']}",
                        lambda: run_as_root_or_sudo(["pacman", "-Sy", "--noconfirm", packages["pacman"]]) == 0)
    if command_exists("winget.exe"):
        return _attempt(tool, f"winget.exe install --id {packages['winget']} --exact",
                        lambda: runner.run_command(["winget.exe", "install", "--id", packages["winget"], "--exact",
                                                    "--accept-package-agreements", "--accept-source-agreements"]).ok)
    if command_exists("choco.exe"):
        return _attempt(tool, f"choco.exe install {packages['choco']} -y",
                        lambda: runner.run_command(["choco.exe", "install", packages["choco"], "-y"]).ok)
    if command_exists("scoop"):
        return _attempt(tool, f"scoop install {packages['scoop']}",
                        lambda: runner.run_command(["scoop", "install", packages["scoop"]]).ok)
    manual_notice(tool)
    return False


def install_prerequisites() -> bool:
    """git then node; a miss is a warning and the install continues (`prerequisites.sh:158-174`)."""
    missing = False
    for tool in ("git", "node"):
        if not try_install_unix_prerequisite(tool):
            missing = True
    if missing:
        report.warn("Continuing after prerequisite guidance. A later install step may still require the missing tool.")
    return not missing
