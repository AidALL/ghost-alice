#!/usr/bin/env python3
"""
validate_public_surfaces.py. Public surface and skill-catalog parity checks.

Verifies that README, docs/index.html, and workspace command wrappers expose the
same skill count, list, and target references as skill-catalog/skills.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PUBLIC_SKILL_NAME_ALIASES = {}


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


def split_skill_sets(skills: list[dict[str, Any]]) -> tuple[list[str], list[str], dict[str, str], dict[str, str]]:
    names: list[str] = []
    paths: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    top_level: list[str] = []
    coding: list[str] = []
    for skill in skills:
        name = str(skill.get("name", "")).strip()
        path = str(skill.get("path", "")).strip()
        if not name or not path:
            continue
        names.append(name)
        paths[name] = path
        descriptions[name] = str(skill.get("description") or name)
        if skill.get("family") == "coding-convention":
            coding.append(name)
        else:
            top_level.append(name)
    return sorted(top_level), sorted(coding), paths, descriptions


def parse_name_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def describe_set_delta(expected: list[str], actual: list[str]) -> str:
    expected_set = set(expected)
    actual_set = set(actual)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    parts: list[str] = []
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if extra:
        parts.append("extra: " + ", ".join(extra))
    return "; ".join(parts) if parts else "order/count drift"


def public_skill_name(name: str) -> str:
    return PUBLIC_SKILL_NAME_ALIASES.get(name, name)


def check_readme(repo: Path, top_level: list[str], coding: list[str], findings: list[Finding]) -> None:
    path = repo / "README.md"
    if not path.is_file():
        findings.append(Finding("ERROR", "README.md", "exist", "README.md is missing."))
        return
    text = path.read_text(encoding="utf-8")
    total = len(top_level) + len(coding)

    summary = re.search(
        r"top-level\s+(\d+)\s+skills?\s+and\s+(\d+)\s+coding-convention\s+sub-skills?,\s+total\s+(\d+)",
        text,
        re.IGNORECASE,
    )
    if not summary:
        findings.append(
            Finding(
                "ERROR",
                "README.md",
                "skill-count-summary",
                "README.md is missing the skill-catalog-based top-level/coding-convention/total count summary.",
            )
        )
    else:
        actual_counts = tuple(int(part) for part in summary.groups())
        expected_counts = (len(top_level), len(coding), total)
        if actual_counts != expected_counts:
            findings.append(
                Finding(
                    "ERROR",
                    "README.md",
                    "skill-count-summary",
                    f"README.md count drift. expected top-level/coding/total={expected_counts}, actual={actual_counts}.",
                )
            )

    top_match = re.search(
        r"-\s+top-level\s+skills?\s+(\d+)\s+\(([^)]*)\)",
        text,
        re.IGNORECASE,
    )
    if not top_match:
        findings.append(
            Finding("ERROR", "README.md", "top-level-list", "README.md top-level skill list is missing.")
        )
    else:
        actual_count = int(top_match.group(1))
        actual_names = parse_name_list(top_match.group(2))
        expected_public_top_level = sorted(public_skill_name(name) for name in top_level)
        if actual_count != len(top_level) or set(actual_names) != set(expected_public_top_level):
            delta = describe_set_delta(expected_public_top_level, actual_names)
            findings.append(
                Finding(
                    "ERROR",
                    "README.md",
                    "top-level-list",
                    f"README.md top-level list mismatch. expected {len(top_level)} names; {delta}.",
                )
            )

    coding_match = re.search(
        r"-\s+coding-convention\s+family\s+(\d+)\s+sub-skills?",
        text,
        re.IGNORECASE,
    )
    if not coding_match:
        findings.append(
            Finding("ERROR", "README.md", "coding-count", "README.md coding-convention count is missing.")
        )
    elif int(coding_match.group(1)) != len(coding):
        findings.append(
            Finding(
                "ERROR",
                "README.md",
                "coding-count",
                f"README.md coding-convention count drift. expected {len(coding)}, actual {coding_match.group(1)}.",
            )
        )

    if (
        "python scripts/validate_public_surfaces.py" not in text
        and "python3 scripts/validate_public_surfaces.py" not in text
    ):
        findings.append(
            Finding(
                "ERROR",
                "README.md",
                "validation-command",
                "README.md installer development/release validation commands do not include python scripts/validate_public_surfaces.py.",
            )
        )


def extract_stat_number(html: str, label_pattern: str) -> int | None:
    pattern = re.compile(
        r'<div\s+class="stat-cell">\s*'
        r'<div\s+class="stat-num">\s*(\d+)\s*</div>\s*'
        r'<div\s+class="stat-label">\s*'
        + label_pattern,
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return None
    return int(match.group(1))


def check_docs_index(repo: Path, top_level: list[str], coding: list[str], findings: list[Finding]) -> None:
    path = repo / "docs" / "index.html"
    if not path.is_file():
        findings.append(Finding("ERROR", "docs/index.html", "exist", "docs/index.html is missing."))
        return
    text = path.read_text(encoding="utf-8")
    expected_total = len(top_level) + len(coding)
    legacy_stats = {
        "Skills across": (extract_stat_number(text, r"Skills\s+across"), expected_total),
        "Coding-convention sub-skills": (
            extract_stat_number(text, r"Coding-convention\s+sub-skills"),
            len(coding),
        ),
    }
    has_legacy_skill_stats = all(actual is not None for actual, _ in legacy_stats.values())
    if has_legacy_skill_stats:
        for label, (actual, expected) in legacy_stats.items():
            if actual != expected:
                findings.append(
                    Finding(
                        "ERROR",
                        "docs/index.html",
                        "skill-stat",
                        f"docs/index.html '{label}' stat drift. expected {expected}, actual {actual}.",
                    )
                )
        return

    required_floor_markers = [
        "Core philosophy. Quality floor first",
        "Operating loop steps",
        "Verification layers",
        "Quality floor as root rule",
        "verified state, not forward momentum",
    ]
    missing_markers = [marker for marker in required_floor_markers if marker not in text]
    if missing_markers:
        findings.append(
            Finding(
                "ERROR",
                "docs/index.html",
                "floor-first-contract",
                "docs/index.html is missing floor-first homepage contract markers: "
                + ", ".join(missing_markers),
            )
        )


def expected_claude_wrapper(path: str) -> str:
    return f"@{path}\n\n$ARGUMENTS\n"


def check_command_wrappers(
    repo: Path,
    paths: dict[str, str],
    descriptions: dict[str, str],
    findings: list[Finding],
) -> str:
    commands_dir = repo / ".claude" / "commands"
    if not commands_dir.is_dir():
        return "skipped-not-present"

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


def check_wiki_mirrors(repo: Path, total: int, findings: list[Finding]) -> list[str]:
    checked: list[str] = []
    for rel in ("wiki", "docs/wiki", ".wiki", "github-wiki"):
        root = repo / rel
        if not root.is_dir():
            continue
        checked.append(rel)
        for path in root.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            if "skill-catalog/skills.json" in text and str(total) not in text:
                findings.append(
                    Finding(
                        "WARNING",
                        "wiki-mirror",
                        "skill-count",
                        f"{path.relative_to(repo).as_posix()} mentions skill-catalog, but lacks a current total {total} signal.",
                    )
                )
    return checked


def run(repo: Path) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    skills = load_catalog(repo, findings)
    top_level, coding, paths, descriptions = split_skill_sets(skills)
    if skills:
        check_readme(repo, top_level, coding, findings)
        check_docs_index(repo, top_level, coding, findings)
        command_wrappers = check_command_wrappers(repo, paths, descriptions, findings)
    else:
        command_wrappers = "skipped-no-catalog"
    wiki_mirrors = check_wiki_mirrors(repo, len(top_level) + len(coding), findings)
    checked = {
        "skill_count": len(top_level) + len(coding),
        "top_level_count": len(top_level),
        "coding_convention_count": len(coding),
        "command_wrappers": command_wrappers,
        "wiki_mirrors": wiki_mirrors,
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
        surfaces = ["README.md", "docs/index.html"]
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
