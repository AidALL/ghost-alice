#!/usr/bin/env python3
"""
validate_public_surfaces.py. Public surface and skill-catalog parity checks.

Verifies that workspace command wrappers match skill-catalog/skills.json, README
keeps its required headings and commands, and index.html keeps its repo-root links.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# These names belong to addon skills installed through --addon-source. The core repo must not check them in as .claude/commands wrappers.
ADDON_COMMAND_WRAPPERS_FORBIDDEN_IN_CORE_REPO = {
    "cardnews-automation",
    "company-profile",
    "company-profile-injector",
    "design-library-normalizer",
    "document-to-markdown",
    "edge-detector-architecture",
    "edge-detector-deployment",
    "fullstack-webapp",
    "gov-proposal-writer",
    "headless-browser-render",
    "hwpx",
    "jira-issue-writer",
    "logical-writing",
    "ml-model-builder",
    "robot-control-system",
}


class Finding:
    __slots__ = ("severity", "area", "check", "message")

    def __init__(self, severity: str, area: str, check: str, message: str):
        self.severity = severity
        self.area = area
        self.check = check
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "area": self.area,
            "check": self.check,
            "message": self.message,
        }


def load_catalog(repo: Path, findings: list[Finding]) -> list[dict[str, Any]]:
    path = repo / "skill-catalog" / "skills.json"
    if not path.is_file():
        findings.append(
            Finding("ERROR", "catalog", "exist", "skill-catalog/skills.json is missing.")
        )
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        findings.append(
            Finding("ERROR", "catalog", "parse", f"skill-catalog/skills.json parse failed: {exc}")
        )
        return []
    skills = data.get("skills", [])
    if not isinstance(skills, list):
        findings.append(
            Finding("ERROR", "catalog", "shape", "skill-catalog/skills.json field 'skills' is not a list.")
        )
        return []
    return [skill for skill in skills if isinstance(skill, dict)]


def split_skill_sets(skills: list[dict[str, Any]]) -> tuple[list[str], list[str], dict[str, str]]:
    names: list[str] = []
    paths: dict[str, str] = {}
    top_level: list[str] = []
    coding: list[str] = []
    for skill in skills:
        name = str(skill.get("name", "")).strip()
        path = str(skill.get("path", "")).strip()
        if not name or not path:
            continue
        names.append(name)
        paths[name] = path
        if skill.get("family") == "coding-convention":
            coding.append(name)
        else:
            top_level.append(name)
    return sorted(top_level), sorted(coding), paths


def check_readme(repo: Path, findings: list[Finding]) -> None:
    path = repo / "README.md"
    if not path.is_file():
        findings.append(Finding("ERROR", "README.md", "exist", "README.md is missing."))
        return
    text = path.read_text(encoding="utf-8")
    has_validation_command = (
        "python scripts/validate_public_surfaces.py" in text
        or "python3 scripts/validate_public_surfaces.py" in text
    )
    compact_entrypoint = (
        "## Quick Start" in text
        and "## Addons" in text
        and "## Documentation Map" in text
        and "bash install.sh --addon-source" in text
        and has_validation_command
    )

    if not compact_entrypoint:
        findings.append(
            Finding(
                "ERROR",
                "README.md",
                "entrypoint-markers",
                "README.md is missing a required entrypoint marker: ## Quick Start, ## Addons, ## Documentation Map, "
                "bash install.sh --addon-source, or python scripts/validate_public_surfaces.py.",
            )
        )


def check_repo_index(repo: Path, findings: list[Finding]) -> None:
    path = repo / "index.html"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if 'id="routes"' not in text or "hero-logo-lockup" not in text:
        return

    required_repo_root_markers = [
        'src="imgs/Ghost-ALICE_logo.png"',
        'href="./docs/getting-started/installation.html"',
        'href="./docs/getting-started/uninstall.html"',
        'href="./docs/policies/installer-platform-compatibility-matrix.html"',
        'href="./index_ko.html"',
    ]
    missing_repo_root_markers = [
        marker for marker in required_repo_root_markers if marker not in text
    ]
    if missing_repo_root_markers:
        findings.append(
            Finding(
                "ERROR",
                "index.html",
                "repo-root-links",
                "index.html is missing repo-root-relative homepage links: "
                + ", ".join(missing_repo_root_markers),
            )
        )

    forbidden_repo_root_markers = [
        'href="./getting-started/',
        'href="./policies/',
        'href="./ko/README.md"',
    ]
    stale_repo_root_markers = [
        marker for marker in forbidden_repo_root_markers if marker in text
    ]
    if stale_repo_root_markers:
        findings.append(
            Finding(
                "ERROR",
                "index.html",
                "repo-root-links",
                "index.html contains docs-root-only links that break from repo root: "
                + ", ".join(stale_repo_root_markers),
            )
        )

    repo_logo = repo / "imgs" / "Ghost-ALICE_logo.png"
    if not repo_logo.is_file():
        findings.append(
            Finding(
                "ERROR",
                "index.html",
                "repo-root-asset",
                "imgs/Ghost-ALICE_logo.png is missing for the repo-root homepage.",
            )
        )


def expected_claude_wrapper(path: str) -> str:
    return f"@{path}\n\n$ARGUMENTS\n"


def check_command_wrappers(
    repo: Path,
    paths: dict[str, str],
    findings: list[Finding],
) -> str:
    commands_dir = repo / ".claude" / "commands"
    if not commands_dir.is_dir():
        return "skipped-not-present"

    for claude in sorted(commands_dir.glob("*.md")):
        rel = claude.relative_to(repo).as_posix()
        if claude.stem in ADDON_COMMAND_WRAPPERS_FORBIDDEN_IN_CORE_REPO:
            findings.append(
                Finding(
                    "ERROR",
                    "command-wrapper",
                    "claude-addon-residue",
                    f"addon command wrapper must not live in core: {rel}",
                )
            )
        if claude.name == "evolution.md" and (commands_dir / "skill-evolution.md").is_file():
            findings.append(
                Finding(
                    "ERROR",
                    "command-wrapper",
                    "claude-evolution-alias",
                    f"use skill-evolution.md instead of local alias: {rel}",
                )
            )

    for name, skill_path in sorted(paths.items()):
        claude = commands_dir / f"{name}.md"
        if not claude.is_file():
            findings.append(
                Finding("ERROR", "command-wrapper", "claude-exist", f".claude/commands/{name}.md is missing.")
            )
        else:
            actual = claude.read_text(encoding="utf-8")
            expected = expected_claude_wrapper(skill_path)
            if actual != expected:
                findings.append(
                    Finding(
                        "ERROR",
                        "command-wrapper",
                        "claude-target",
                        f".claude/commands/{name}.md target drift. expected @{skill_path}.",
                    )
                )
    return "checked"


def run(repo: Path) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    skills = load_catalog(repo, findings)
    top_level, coding, paths = split_skill_sets(skills)
    if skills:
        check_readme(repo, findings)
        check_repo_index(repo, findings)
        command_wrappers = check_command_wrappers(repo, paths, findings)
    else:
        command_wrappers = "skipped-no-catalog"
    checked = {
        "skill_count": len(top_level) + len(coding),
        "top_level_count": len(top_level),
        "coding_convention_count": len(coding),
        "command_wrappers": command_wrappers,
    }
    return findings, checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    findings, checked = run(repo)
    error_count = sum(1 for finding in findings if finding.severity == "ERROR")
    warning_count = sum(1 for finding in findings if finding.severity == "WARNING")

    if args.json:
        print(
            json.dumps(
                {
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "checked": checked,
                    "findings": [finding.to_dict() for finding in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Public surface parity check: repo={repo}")
        surfaces = ["README.md", "index.html"]
        if checked["command_wrappers"] == "checked":
            surfaces.append(".claude/commands")
        else:
            surfaces.append(f".claude/commands ({checked['command_wrappers']})")
        print("Checked surfaces: " + ", ".join(surfaces))
        for finding in findings:
            print(f"  [{finding.severity}] {finding.area} / {finding.check}: {finding.message}")
        print(f"Result: ERROR {error_count}, WARNING {warning_count}")

    return 2 if error_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
