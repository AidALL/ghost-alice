#!/usr/bin/env python3
"""
build_catalog.py. Ghost-ALICE OS skill catalog builder.

Reads SKILL.md frontmatter from each skill directory and generates
skill-catalog/skills.json. Frontmatter is the SSOT. This script only builds the
derived artifact.

Usage:
    python3 scripts/build_catalog.py              # run from repo root
    python3 scripts/build_catalog.py --check      # check stale status without writing
    python3 scripts/build_catalog.py --output X   # set output file

Dependencies:
    Python 3.11+ standard library only (no PyYAML; includes a simple parser)

Exit codes:
    0: success
    1: I/O or parse error
    2: stale artifact detected in --check mode
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Skill family directories. Scan only inside this list.
FAMILY_DIRS = {
    "coding-convention": "coding-convention",
}
# System skills at the repo root. These form pre-work Ghost-ALICE gates such as routing and contracts.
SYSTEM_FAMILY = "system"
SYSTEM_SKILLS = {"boundary-contract", "session-intent-analyzer", "task-router"}

# Domain skills at the repo root: directories with SKILL.md outside FAMILY_DIRS and SYSTEM_SKILLS.
DOMAIN_FAMILY = "domain"

# Meta entrypoint skills that dispatch to other skills.
META_SKILLS = {"using-coding-convention"}

# governance_class is a governance involvement axis independent of family.
CORE_GATE_SKILLS = {"task-router"}
INTENT_STATE_PRODUCER_SKILLS = {"session-intent-analyzer"}
BOUNDARY_GATE_SKILLS = {"boundary-contract"}
GOVERNANCE_SUBSKILLS = {
    "agent-security-scan",
    "compact-handoff",
    "jailbreak-detector",
    "merge-companion",
    "necessity-gate",
    "skill-evolution",
    "using-coding-convention",
    "writing-skills",
}
VERIFICATION_SUBSKILLS = {
    "adversarial-verification",
    "receiving-code-review",
    "requesting-code-review",
    "systematic-debugging",
    "test-driven-development",
}
COMPLETION_GATE_SKILLS = {
    "finishing-a-development-branch",
    "verification-before-completion",
}
DOMAIN_ORCHESTRATION_SKILLS: set[str] = set()

GOVERNANCE_CLASSES = {
    "boundary-gate",
    "coding-process",
    "completion-gate",
    "core-gate",
    "domain-orchestration",
    "governance-subskill",
    "intent-state-producer",
    "non-governance-domain",
    "verification-subskill",
}

# calls field type enum.
VALID_CALL_TYPES = {"hard", "soft", "union", "meta", "external-hard", "external-union"}
EXTERNAL_TYPES = {"external-hard", "external-union"}
UNION_TYPES = {"union", "external-union"}

CATALOG_VERSION = "1.3.0"


def parse_frontmatter(text: str) -> dict[str, Any]:
    """
    Parse frontmatter from the SKILL.md body.
    Uses a simple parser to avoid a PyYAML dependency.
    Supports scalars (name: value), quoted scalars (name: "value"), and lists (- item).
    """
    if not text.startswith("---"):
        raise ValueError("missing frontmatter start delimiter (---)")

    lines = text.split("\n")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("missing frontmatter end delimiter (---)")

    fm: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for raw in lines[1:end_idx]:
        if not raw.strip():
            continue
        # List item.
        if raw.lstrip().startswith("- "):
            if current_list is None:
                raise ValueError(f"list item appeared without list context: {raw}")
            item = raw.lstrip()[2:].strip()
            current_list.append(_strip_quotes(item))
            continue
        # key: value
        m = re.match(r"^([A-Za-z][\w-]*)\s*:\s*(.*)$", raw)
        if not m:
            # Continuation of a multiline value, such as an unquoted wrapped description.
            if current_key and isinstance(fm.get(current_key), str):
                fm[current_key] += " " + raw.strip()
                continue
            raise ValueError(f"frontmatter parse failed: {raw}")
        key, value = m.group(1), m.group(2).strip()
        current_key = key
        current_list = None
        if value == "":
            # A list may start on the next line.
            current_list = []
            fm[key] = current_list
        else:
            fm[key] = _strip_quotes(value)
    return fm


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def discover_skills(repo_root: Path) -> list[Path]:
    """Find SKILL.md paths. Scan known families and domain roots only."""
    found: list[Path] = []
    # coding-convention family.
    for family_dir in FAMILY_DIRS.values():
        family_path = repo_root / family_dir
        if not family_path.is_dir():
            continue
        for skill_dir in sorted(family_path.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                found.append(skill_md)
    # Domain skills at the repo root.
    excluded_roots = set(FAMILY_DIRS.values()) | {
        ".github", "skill-catalog", "scripts", "official-docs", "_shared",
        "company-info-files", "platforms", "psyco-neu-main", "psyco-neu-vision-main",
    }
    for entry in sorted(repo_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in excluded_roots:
            continue
        skill_md = entry / "SKILL.md"
        if skill_md.is_file():
            found.append(skill_md)
    return found


def discover_install_roots(repo_root: Path) -> list[str]:
    """Compute top-level skill roots that should be present in installer ALL_SKILLS."""
    roots: set[str] = set()
    for family_dir in FAMILY_DIRS.values():
        if (repo_root / family_dir).is_dir():
            roots.add(family_dir)

    excluded_roots = set(FAMILY_DIRS.values()) | {
        ".github", "skill-catalog", "scripts", "official-docs", "_shared",
        "company-info-files", "platforms", "psyco-neu-main", "psyco-neu-vision-main",
    }
    for entry in sorted(repo_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in excluded_roots:
            continue
        if (entry / "SKILL.md").is_file():
            roots.add(entry.name)
    return sorted(roots)


def infer_governance_class(name: str, family: str) -> str:
    """Infer the governance involvement taxonomy independent of family."""
    if name in CORE_GATE_SKILLS:
        return "core-gate"
    if name in INTENT_STATE_PRODUCER_SKILLS:
        return "intent-state-producer"
    if name in BOUNDARY_GATE_SKILLS:
        return "boundary-gate"
    if name in GOVERNANCE_SUBSKILLS:
        return "governance-subskill"
    if name in VERIFICATION_SUBSKILLS:
        return "verification-subskill"
    if name in COMPLETION_GATE_SKILLS:
        return "completion-gate"
    if name in DOMAIN_ORCHESTRATION_SKILLS:
        return "domain-orchestration"
    if family == "coding-convention":
        return "coding-process"
    return "non-governance-domain"


def build_skill_entry(skill_md: Path, repo_root: Path) -> dict[str, Any]:
    text = skill_md.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    name = fm.get("name", "")
    if not name:
        raise ValueError(f"{skill_md}: missing name field")

    rel_path = skill_md.relative_to(repo_root).as_posix()
    parent_dir = skill_md.parent.relative_to(repo_root).parts
    if name in SYSTEM_SKILLS:
        family = SYSTEM_FAMILY
    else:
        family = FAMILY_DIRS.get(parent_dir[0], DOMAIN_FAMILY) if parent_dir else DOMAIN_FAMILY
    governance_class = infer_governance_class(name, family)
    if governance_class not in GOVERNANCE_CLASSES:
        raise ValueError(f"{skill_md}: unknown governance_class '{governance_class}'")

    entry: dict[str, Any] = {
        "name": name,
        "path": rel_path,
        "description": fm.get("description", ""),
        "family": family,
        "governance_class": governance_class,
    }
    if fm.get("compatibility"):
        compat = fm["compatibility"]
        if isinstance(compat, list):
            entry["compatibility"] = compat
        elif isinstance(compat, str):
            entry["compatibility"] = [compat]
    if fm.get("allowed-tools"):
        entry["allowed_tools"] = fm["allowed-tools"]
    if name in META_SKILLS:
        entry["meta"] = True
    if fm.get("calls"):
        calls_raw = fm["calls"]
        if isinstance(calls_raw, str):
            calls_raw = [calls_raw]
        relations = []
        for call_str in calls_raw:
            try:
                relations.append(parse_call_string(call_str))
            except ValueError as e:
                raise ValueError(f"{skill_md}: calls item '{call_str}'. {e}")
        if relations:
            entry["calls_relation"] = relations
    return entry


def parse_call_string(s: str) -> dict[str, Any]:
    """
    Parse a single string from the calls field.
    Format: "<type>:<target>" or "<type>:<target>:<union_group>"
    type ∈ {hard, soft, union, meta, external-hard, external-union}
    union types require union_group.
    external types convert target to external_target.
    """
    parts = s.split(":", 2)
    if len(parts) < 2:
        raise ValueError("format error: '<type>:<target>[:<group>]' required")
    call_type = parts[0]
    target = parts[1]
    union_group = parts[2] if len(parts) == 3 else None

    if call_type not in VALID_CALL_TYPES:
        raise ValueError(f"unknown type '{call_type}'. allowed: {sorted(VALID_CALL_TYPES)}")

    if call_type in UNION_TYPES and not union_group:
        raise ValueError(f"type '{call_type}' requires union_group")
    if call_type not in UNION_TYPES and union_group:
        raise ValueError(f"type '{call_type}' cannot use union_group")

    relation: dict[str, Any] = {"type": call_type}
    if call_type in EXTERNAL_TYPES:
        relation["external_target"] = target
    else:
        relation["target"] = target
    if union_group:
        relation["union_group"] = union_group
    return relation


def build_catalog(repo_root: Path) -> dict[str, Any]:
    skills = []
    for skill_md in discover_skills(repo_root):
        skills.append(build_skill_entry(skill_md, repo_root))
    skills.sort(key=lambda s: s["name"])
    return {
        "version": CATALOG_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_root": ".",
        "install_roots": discover_install_roots(repo_root),
        "skills": skills,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Only compare the build result with the existing file")
    parser.add_argument("--output", default="skill-catalog/skills.json", help="Output path")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    output_path = repo_root / args.output

    try:
        catalog = build_catalog(repo_root)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    rendered = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        if not output_path.is_file():
            print(f"ERROR: {output_path} is missing. Build required.", file=sys.stderr)
            return 2
        existing = output_path.read_text(encoding="utf-8")
        # generated_at changes on every run, so exclude it from the comparison.
        existing_norm = re.sub(r'"generated_at":\s*"[^"]+"', '"generated_at": "*"', existing)
        new_norm = re.sub(r'"generated_at":\s*"[^"]+"', '"generated_at": "*"', rendered)
        if existing_norm != new_norm:
            print(
                f"ERROR: {output_path} is stale. Rerun `python3 scripts/build_catalog.py` and commit the result.",
                file=sys.stderr,
            )
            return 2
        print(f"OK: {output_path} is up to date ({len(catalog['skills'])} skills)")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"OK: generated {output_path} ({len(catalog['skills'])} skills)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
