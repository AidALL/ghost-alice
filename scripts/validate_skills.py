#!/usr/bin/env python3
"""
validate_skills.py. Ghost-ALICE OS skill consistency validation.

Automatically validates Phase 1-5 of
official-docs/derived/skill-compliance-checklist.md and checks whether
skill-catalog/skills.json is synchronized with frontmatter.

Usage:
    python3 scripts/validate_skills.py         # full validation
    python3 scripts/validate_skills.py --json  # print JSON output for CI

Exit codes:
    0: all ERROR checks passed (WARNING is allowed)
    1: I/O error
    2: one or more ERROR findings, or an argument error reported by argparse
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Reuse build_catalog.py as a library.
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from skill_metadata import load_skill_catalog, parse_frontmatter, split_frontmatter  # noqa: E402
from build_catalog import (  # noqa: E402
    VALID_CALL_TYPES,
    build_catalog,
    discover_install_roots,
    discover_skills,
)
from check_skill_gate_contract import run_contract_checks  # noqa: E402
from markdown_structure import atx_heading, iter_unfenced_lines  # noqa: E402

NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
DESCRIPTION_HARD_LIMIT = 1024
BODY_SOFT_LIMIT = 500
INLINE_CODE_BLOCK_SOFT_LIMIT = 50  # Phase 2-5: a larger SKILL.md body code block suggests progressive disclosure drift.
REFERENCE_TOC_LIMIT = 300

CRITICAL_SECTION_PATTERN = re.compile(
    r"(?mi)^#{2,}[^\n]*("
    r"critical|gotcha|warnings?|failure\s*modes?|failure\s*patterns?|common\s*mistakes?|common\s*failures?|red\s*flags?|pitfall|caveat|troubleshoot|"
    r"prohibition|prohibited|anti-?pattern|rationaliz\w*|placeholder|iron\s*law"
    r")",
)

KOREAN_TEXT_PATTERN = re.compile(r"[\uac00-\ud7a3]")
# Placeholder detection only catches real placeholder markers.
# - a standalone token at line start or after indentation
# - TODO:/TBD:/FIXME: format with body text after the colon
# - the English "draft pending" phrase
# Avoid matching tool names such as TodoWrite or quoted TODO examples.
PLACEHOLDER_PATTERN = re.compile(
    r"(?m)^\s*(TODO|TBD|FIXME)(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])(TODO|TBD|FIXME):\s|\bdraft pending\b"
)

# English-only skills that skip the Korean tone check.
ENGLISH_ONLY_SKILLS: set[str] = set()


class Issue:
    __slots__ = ("severity", "skill", "phase", "rule", "message")

    def __init__(self, severity: str, skill: str, phase: str, rule: str, message: str):
        self.severity = severity
        self.skill = skill
        self.phase = phase
        self.rule = rule
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "skill": self.skill,
            "phase": self.phase,
            "rule": self.rule,
            "message": self.message,
        }


def validate_phase1_frontmatter(skill_md: Path, fm: dict[str, Any], issues: list[Issue]) -> None:
    skill_name = skill_md.parent.name
    name = fm.get("name", "")
    if not isinstance(name, str):
        issues.append(Issue("ERROR", skill_name, "1", "1-2", "name must be a string"))
    elif not name:
        issues.append(Issue("ERROR", skill_name, "1", "1-1", "missing name field"))
    else:
        if not NAME_PATTERN.match(name) or len(name) > 64:
            issues.append(Issue("ERROR", skill_name, "1", "1-2", f"name format violation: {name}"))
        if name != skill_md.parent.name:
            issues.append(
                Issue("ERROR", skill_name, "1", "1-3", f"name({name}) does not match directory name({skill_md.parent.name})")
            )

    desc = fm.get("description", "")
    if not isinstance(desc, str):
        issues.append(Issue("ERROR", skill_name, "1", "1-4", "description must be a string"))
    elif not desc:
        issues.append(Issue("ERROR", skill_name, "1", "1-4", "missing description field"))
    else:
        desc_len = len(desc)
        if desc_len > DESCRIPTION_HARD_LIMIT:
            issues.append(
                Issue("ERROR", skill_name, "1", "1-5", f"description length {desc_len} > 1024 hard limit")
            )

    compat = fm.get("compatibility")
    if isinstance(compat, (str, list)):
        compat_str = "\n".join(compat) if isinstance(compat, list) else compat
        if len(compat_str) > 500:
            issues.append(
                Issue("WARNING", skill_name, "1", "1-7", f"compatibility length {len(compat_str)} > 500")
            )


def _extract_body(text: str) -> str:
    """Return the SKILL.md body with frontmatter removed."""
    try:
        _, body_start = split_frontmatter(text)
    except ValueError:
        return text
    return text[body_start:]


def validate_phase2_body(skill_md: Path, text: str, issues: list[Issue]) -> None:
    skill_name = skill_md.parent.name
    body = _extract_body(text)
    body_lines = body.count("\n")
    if body_lines > BODY_SOFT_LIMIT:
        issues.append(
            Issue("WARNING", skill_name, "2", "2-1", f"body {body_lines} lines > 500 recommendation")
        )
    if PLACEHOLDER_PATTERN.search(body):
        issues.append(Issue("ERROR", skill_name, "2", "2-4", "placeholder found (TODO/TBD/FIXME/draft pending)"))
    # 2-5: Progressive Disclosure. A single SKILL.md body code block over 50 lines should move to references/.
    for match in re.finditer(r"```[^\n]*\n(.*?)```", body, re.DOTALL):
        block_lines = match.group(1).count("\n")
        if block_lines > INLINE_CODE_BLOCK_SOFT_LIMIT:
            issues.append(
                Issue(
                    "WARNING",
                    skill_name,
                    "2",
                    "2-5",
                    f"single SKILL.md code block {block_lines} lines > {INLINE_CODE_BLOCK_SOFT_LIMIT}; move it to references/",
                )
            )
            break  # Report only the first one.


def validate_phase3_structure(skill_md: Path, text: str, issues: list[Issue]) -> None:
    """
    Phase 3. Structure and pattern validation.
    3-1 Critical Rules / warnings style section exists; a keyword may appear anywhere in a `##`-or-deeper header line.
    3-4 When references/ exists, the SKILL.md body explicitly points to it.
    3-2 default tool declarations and 3-3 output format templates remain manual review items.
    """
    skill_name = skill_md.parent.name
    body = _extract_body(text)

    if not CRITICAL_SECTION_PATTERN.search(body):
        issues.append(
            Issue(
                "WARNING",
                skill_name,
                "3",
                "3-1",
                "No Critical Rules / warnings / failure modes / rationalization section appears in the body. Misuse-defense section may be missing.",
            )
        )

    refs_dir = skill_md.parent / "references"
    if refs_dir.is_dir():
        ref_files = [p for p in refs_dir.rglob("*.md") if p.is_file()]
        if ref_files:
            if "references/" not in body and "references\\" not in body:
                issues.append(
                    Issue(
                        "WARNING",
                        skill_name,
                        "3",
                        "3-4",
                        "references/ contains files, but SKILL.md has no 'references/' link. Progressive disclosure entrypoint may be missing.",
                    )
                )
            else:
                # Per-file check: if the body never mentions the filename, it may be orphaned.
                for ref_file in ref_files:
                    rel = ref_file.relative_to(refs_dir).as_posix()
                    if rel not in body and ref_file.name not in body:
                        issues.append(
                            Issue(
                                "WARNING",
                                skill_name,
                                "3",
                                "3-4",
                                f"references/{rel} is not referenced in SKILL.md body (possible orphan file)",
                            )
                        )


_ANCHOR_DROP = re.compile(r"[^\w\s-]")


def _github_anchor(heading: str) -> str:
    """Derive a GitHub-style heading anchor: lowercase, drop punctuation, spaces->hyphens."""
    text = _ANCHOR_DROP.sub("", heading.strip().lower())
    return text.replace(" ", "-")


def _parse_skill_nav(body: str) -> tuple[list[tuple[int, str]], set[str]]:
    """Single fence-aware pass over the body.

    Returns (headings, toc_anchors):
    - headings: (level, title) for ATX headings outside backtick/tilde fences.
    - toc_anchors: lowercased anchors collected from the ``## Contents`` list.
    """
    headings: list[tuple[int, str]] = []
    toc_anchors: set[str] = set()
    in_toc = False
    for offset, line in iter_unfenced_lines(body):
        if heading := atx_heading(offset, line):
            headings.append((heading.level, heading.title))
            in_toc = heading.level == 2 and heading.title.lower() == "contents"
            continue
        if in_toc:
            for anchor in re.findall(r"\]\(#([A-Za-z0-9_-]+)\)", line):
                toc_anchors.add(anchor.lower())
    return headings, toc_anchors


def validate_phase_toc_parity(skill_md: Path, text: str, issues: list[Issue]) -> None:
    """3-5: SKILL.md ``## Contents`` <-> heading parity.

    A deterministic navigation check. It runs ONLY when a ``## Contents`` section
    exists (short skills without a TOC are exempt). Then:
    - every ``##`` (h2) section heading outside fenced code must be listed in Contents;
    - every Contents anchor must resolve to a real heading (no dangling links).
    This catches the "added a section but forgot the Contents entry" and the
    "renamed a section but left the stale Contents anchor" drift. Severity is
    ERROR: TOC drift fails the build so it is caught without human review. Code-
    fence headings are not sections.
    """
    skill_name = skill_md.parent.name
    body = _extract_body(text)
    headings, toc_anchors = _parse_skill_nav(body)
    if not any(level == 2 and title.lower() == "contents" for level, title in headings):
        return  # no Contents section -> exempt
    anchored_headings: list[tuple[int, str, str]] = []
    heading_anchors: set[str] = set()
    for level, title in headings:
        base = _github_anchor(title)
        anchor, duplicate = base, 0
        while anchor in heading_anchors:
            duplicate += 1
            anchor = f"{base}-{duplicate}"
        heading_anchors.add(anchor)
        anchored_headings.append((level, title, anchor))
    for level, title, anchor in anchored_headings:
        if level != 2 or title.lower() == "contents":
            continue
        if anchor not in toc_anchors:
            issues.append(
                Issue(
                    "ERROR", skill_name, "3", "3-5",
                    f"section heading '{title}' is missing from the ## Contents list",
                )
            )
    for anchor in sorted(toc_anchors):
        if anchor not in heading_anchors:
            issues.append(
                Issue(
                    "ERROR", skill_name, "3", "3-5",
                    f"## Contents anchor '#{anchor}' has no matching heading (dangling link)",
                )
            )


def validate_phase4_references(skill_md: Path, issues: list[Issue]) -> None:
    skill_name = skill_md.parent.name
    skill_dir = skill_md.parent

    # 4-1: references/*.md files over 300 lines require a TOC.
    refs_dir = skill_dir / "references"
    if refs_dir.is_dir():
        for ref_file in refs_dir.rglob("*.md"):
            ref_text = ref_file.read_text(encoding="utf-8")
            line_count = ref_text.count("\n")
            if line_count > REFERENCE_TOC_LIMIT:
                if (
                    "## TOC" not in ref_text
                    and "## Table of Contents" not in ref_text
                    and "## Contents" not in ref_text
                ):
                    issues.append(
                        Issue(
                            "ERROR",
                            skill_name,
                            "4",
                            "4-1",
                            f"{ref_file.relative_to(skill_dir)} {line_count} lines > 300 lines, missing TOC",
                        )
                    )

            # 4-2: Absolute file links are forbidden inside references/. Flag only Markdown links `](/abs/path.ext)` that include a file extension. URL site-relative paths such as /en/docs/... are not files and are excluded.
            for m in re.finditer(r"\]\((/[^)]+)\)", ref_text):
                target = m.group(1)
                if target.startswith("//"):
                    continue
                if not re.search(r"\.(md|py|sh|json|ya?ml|txt|toml|html?|css|js|ts|tsx|jsx)(?:[#?]|$)", target):
                    continue
                issues.append(
                    Issue(
                        "WARNING",
                        skill_name,
                        "4",
                        "4-2",
                        f"{ref_file.relative_to(skill_dir)}: absolute file link '{target}'. Use a relative path",
                    )
                )
                break  # Report only the first match per file.

    # 4-3: When scripts/ exists, each executable file needs dependency documentation near the top.
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.is_dir():
        for script in scripts_dir.rglob("*.py"):
            head = script.read_text(encoding="utf-8", errors="replace")[:2000]
            # Look for dependency hints in the shebang, docstring, or comments.
            if not re.search(r"(?im)^\s*#.*depend|requires?\s*:|dependencies\b", head):
                issues.append(
                    Issue(
                        "WARNING",
                        skill_name,
                        "4",
                        "4-3",
                        f"{script.relative_to(skill_dir)}: missing dependency comment/docstring near the top",
                    )
                )


def validate_phase5_korean_tone(skill_md: Path, fm: dict[str, Any], text: str, issues: list[Issue]) -> None:
    skill_name = skill_md.parent.name
    if skill_name in ENGLISH_ONLY_SKILLS:
        return
    # Description contains trigger keywords, so validate tone only in the body.
    body = _extract_body(text)
    if KOREAN_TEXT_PATTERN.search(body):
        issues.append(Issue("WARNING", skill_name, "5", "5-2", "Korean text found in English-canonical skill body"))


def _strip_install_token(line: str) -> str | None:
    token = line.split("#", 1)[0].strip().rstrip(",")
    if not token or token == ")":
        return None
    if token.startswith(("'", '"')) and token.endswith(("'", '"')):
        token = token[1:-1]
    token = token.strip()
    if not token or any(ch.isspace() for ch in token):
        return None
    return token


def _parse_python_install_skills(path: Path) -> set[str]:
    """The `ALL_SKILLS = (` ... `)` tuple of `_shared/installer/selection.py`."""
    if not path.is_file():
        return set()
    names: set[str] = set()
    collecting = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("ALL_SKILLS = ("):
            collecting = True
            continue
        if not collecting:
            continue
        if stripped == ")":
            break
        token = _strip_install_token(stripped)
        if token:
            names.add(token)
    return names


def validate_install_list_sync(repo_root: Path, issues: list[Issue]) -> None:
    install_roots = set(discover_install_roots(repo_root))
    installers = {
        "_shared/installer/selection.py": _parse_python_install_skills(repo_root / "_shared" / "installer" / "selection.py"),
    }
    for filename, names in installers.items():
        for missing in sorted(install_roots - names):
            issues.append(
                Issue(
                    "ERROR",
                    "<installer>",
                    "A",
                    "install-list.missing",
                    f"{missing} are in catalog/install roots but missing from {filename} ALL_SKILLS",
                )
            )
        for unknown in sorted(names - install_roots):
            issues.append(
                Issue(
                    "ERROR",
                    "<installer>",
                    "A",
                    "install-list.unknown",
                    f"{unknown} are in {filename} ALL_SKILLS but missing from catalog/install roots",
                )
            )


DESCRIPTION_STOPWORDS = {
    "when", "with", "that", "this",
}


def _description_tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_-]{2,}", value)
        if token.lower() not in DESCRIPTION_STOPWORDS
    }


def validate_description_collisions(repo_root: Path, issues: list[Issue]) -> None:
    descriptions: list[tuple[str, set[str]]] = []
    for skill_md in discover_skills(repo_root):
        try:
            fm, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        description = fm.get("description", "")
        if not isinstance(description, str):
            continue  # Phase 1 owns invalid description diagnostics.
        tokens = _description_tokens(description)
        if len(tokens) >= 6:
            descriptions.append((skill_md.parent.name, tokens))

    for index, (left_name, left_tokens) in enumerate(descriptions):
        for right_name, right_tokens in descriptions[index + 1:]:
            overlap = left_tokens & right_tokens
            union = left_tokens | right_tokens
            if len(overlap) >= 6 and len(overlap) / len(union) >= 0.85:
                issues.append(
                    Issue(
                        "WARNING",
                        left_name,
                        "A",
                        "description.collision",
                        f"{left_name} and {right_name} description token overlap {len(overlap)}/{len(union)}",
                    )
                )


def validate_calls(repo_root: Path, issues: list[Issue]) -> None:
    """
    Validate calls_relation consistency from skill-catalog/skills.json.
    - target must point to another skill in the catalog.
    - union_group must contain at least two members because single-member unions are meaningless.
    - self-calls are forbidden.
    """
    catalog_path = repo_root / "skill-catalog" / "skills.json"
    if not catalog_path.is_file():
        return  # validate_catalog_sync catches this separately.
    try:
        catalog = load_skill_catalog(catalog_path)
    except (OSError, ValueError):
        return  # validate_catalog_sync owns catalog read and shape diagnostics.
    skill_names = {s["name"] for s in catalog["skills"]}

    # union_group member count: (skill_name, union_group) -> count.
    union_member_count: dict[tuple[str, str], int] = {}
    flow_edges: dict[str, set[str]] = {name: set() for name in skill_names}

    for skill in catalog["skills"]:
        name = skill["name"]
        relations = skill.get("calls_relation", [])
        if not isinstance(relations, list):
            issues.append(Issue("ERROR", name, "P12", "calls.shape", "calls_relation must be a list"))
            continue
        for index, r in enumerate(relations):
            field = f"calls_relation[{index}]"
            if not isinstance(r, dict):
                issues.append(Issue("ERROR", name, "P12", "calls.shape", f"{field} must be an object"))
                continue
            rtype = r.get("type")
            target = r.get("target")
            grp = r.get("union_group")
            valid_fields = True
            if not isinstance(rtype, str) or rtype not in VALID_CALL_TYPES:
                issues.append(
                    Issue("ERROR", name, "P12", "calls.type", f"{field}.type must be one of {', '.join(sorted(VALID_CALL_TYPES))}")
                )
                valid_fields = False
            if not isinstance(target, str) or not target:
                issues.append(
                    Issue("ERROR", name, "P12", "calls.target", f"{field}.target must be a nonempty string")
                )
                valid_fields = False
            if "union_group" in r and not isinstance(grp, str):
                issues.append(
                    Issue("ERROR", name, "P12", "calls.union", f"{field}.union_group must be a string")
                )
                valid_fields = False
            if not valid_fields:
                continue
            if target == "*" and rtype == "meta":
                pass  # meta:* means the whole catalog and is excluded from validation.
            elif target not in skill_names:
                issues.append(
                    Issue(
                        "ERROR",
                        name,
                        "P12",
                        "calls.target",
                        f"target '{target}' is not in the catalog",
                    )
                )
            elif target == name:
                issues.append(
                    Issue("ERROR", name, "P12", "calls.self", "self-call is forbidden")
                )
            elif rtype != "meta":
                flow_edges[name].add(target)
            # union_group count.
            if rtype == "union":
                if grp:
                    union_member_count[(name, grp)] = union_member_count.get((name, grp), 0) + 1

    # Detect single-member union_group values.
    for (skill_name, grp), cnt in union_member_count.items():
        if cnt < 2:
            issues.append(
                Issue(
                    "WARNING",
                    skill_name,
                    "P12",
                    "calls.union",
                    f"union_group '{grp}' has {cnt} member(s); at least 2 required",
                )
            )

    _validate_call_flow_cycles(flow_edges, issues)


def _validate_call_flow_cycles(flow_edges: dict[str, set[str]], issues: list[Issue]) -> None:
    """meta:* edges are excluded; remaining calls_relation edges are static handoff/flow."""
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()
    reported_cycles: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        visited.add(node)
        active.append(node)
        active_set.add(node)

        for target in sorted(flow_edges.get(node, set())):
            if target in active_set:
                cycle = active[active.index(target):] + [target]
                key = tuple(cycle)
                if key not in reported_cycles:
                    reported_cycles.add(key)
                    issues.append(
                        Issue(
                            "ERROR",
                            cycle[0],
                            "P12",
                            "calls.cycle",
                            "calls flow cycle detected: " + " -> ".join(cycle),
                        )
                    )
                continue
            if target not in visited:
                visit(target)

        active.pop()
        active_set.remove(node)

    for node in sorted(flow_edges):
        if node not in visited:
            visit(node)


def validate_catalog_sync(repo_root: Path, issues: list[Issue]) -> None:
    """Check whether skill-catalog/skills.json is synchronized with frontmatter."""
    catalog_path = repo_root / "skill-catalog" / "skills.json"
    if not catalog_path.is_file():
        issues.append(
            Issue("ERROR", "<catalog>", "0", "sync", "skill-catalog/skills.json is missing. Run build_catalog.py.")
        )
        return
    try:
        expected = build_catalog(repo_root)
        actual = load_skill_catalog(catalog_path)
    except (OSError, ValueError) as error:
        issues.append(Issue("ERROR", "<catalog>", "0", "sync", f"catalog comparison failed: {error}"))
        return
    # Compare while excluding generated_at.
    expected.pop("generated_at", None)
    actual.pop("generated_at", None)
    if expected != actual:
        issues.append(
            Issue(
                "ERROR",
                "<catalog>",
                "0",
                "sync",
                "skill-catalog/skills.json does not match frontmatter. Rerun build_catalog.py and commit the result.",
            )
        )


def validate_catalog_schema_contract(repo_root: Path, issues: list[Issue]) -> None:
    """Check whether skill-catalog/schema.json accepts the generated catalog top-level contract."""
    catalog_path = repo_root / "skill-catalog" / "skills.json"
    schema_path = repo_root / "skill-catalog" / "schema.json"
    if not catalog_path.is_file() or not schema_path.is_file():
        return

    try:
        catalog = load_skill_catalog(catalog_path)
    except (OSError, ValueError):
        return  # validate_catalog_sync owns catalog read and shape diagnostics.
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        issues.append(
            Issue("ERROR", "<catalog>", "0", "schema", f"catalog/schema parse failed: {e}")
        )
        return

    schema_properties = set(schema.get("properties", {}))
    extra_catalog_keys = sorted(set(catalog) - schema_properties)
    if extra_catalog_keys:
        issues.append(
            Issue(
                "ERROR",
                "<catalog>",
                "0",
                "schema.top-level",
                "catalog top-level keys missing from skill-catalog/schema.json properties: "
                + ", ".join(extra_catalog_keys),
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    issues: list[Issue] = []

    try:
        skill_files = discover_skills(repo_root)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    for skill_md in skill_files:
        try:
            text = skill_md.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(text)
        except (OSError, ValueError) as e:
            issues.append(
                Issue("ERROR", skill_md.parent.name, "0", "parse", f"parse failed: {e}")
            )
            continue
        validate_phase1_frontmatter(skill_md, fm, issues)
        validate_phase2_body(skill_md, text, issues)
        validate_phase3_structure(skill_md, text, issues)
        validate_phase_toc_parity(skill_md, text, issues)
        validate_phase4_references(skill_md, issues)
        validate_phase5_korean_tone(skill_md, fm, text, issues)

    validate_catalog_sync(repo_root, issues)
    validate_catalog_schema_contract(repo_root, issues)
    validate_install_list_sync(repo_root, issues)
    validate_description_collisions(repo_root, issues)
    validate_calls(repo_root, issues)
    for contract_issue in run_contract_checks(repo_root):
        issues.append(
            Issue(
                contract_issue.severity,
                "<session-gate-contract>",
                "G",
                "gate-contract",
                f"{contract_issue.path}: {contract_issue.message}",
            )
        )

    error_count = sum(1 for i in issues if i.severity == "ERROR")
    warning_count = sum(1 for i in issues if i.severity == "WARNING")
    skill_count = len(skill_files)

    if args.json:
        print(json.dumps({
            "skill_count": skill_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "issues": [i.to_dict() for i in issues],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"Validation target: {skill_count} skills")
        for issue in issues:
            print(f"  [{issue.severity}] {issue.skill} (Phase {issue.phase} / {issue.rule}): {issue.message}")
        print(f"Result: ERROR {error_count}, WARNING {warning_count}")

    return 2 if error_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
