#!/usr/bin/env python3
"""audit_wiki_integrity.py. Report-only structural integrity audit for the Wiki.

The project Wiki (`<repo>.wiki.git`) is a named design surface: architecture.md
states "Long-form design background lives in the Wiki", and the Wiki design page
names the main repository as the executable-contract SSOT. Unlike every
repo-internal doc surface, the Wiki is a separate repository not covered by the
repo test suites, so drift stays invisible until a human rereads every page.

This audit closes that gap with DETERMINISTIC, low-false-positive structural
checks only. It deliberately does NOT attempt prose, skill-name, or path-content
drift detection, which was shown to over-flag. It is report-only (no edits, no
runtime deny path); it exits non-zero when findings exist so it can run as a
local or CI audit, but it is intentionally not wired into the repo merge gate
because the Wiki lives in a separate repository.

Checks:
- broken-link : a [text](Target) link pointing at a non-existent wiki page.
- missing-ko  : an English content page with no Korean counterpart.
- missing-en  : a Korean content page with no English counterpart.

Pairing conventions handled: ``name`` <-> ``name_ko`` and ``name_en`` <-> ``name_ko``.
GitHub wiki special pages (``_Sidebar``, ``_Footer``, any ``_``-prefixed page) are
exempt from pairing but still have their links checked.

Usage:
    python3 scripts/audit_wiki_integrity.py --wiki /path/to/ghost-alice.wiki
    python3 scripts/audit_wiki_integrity.py --wiki /path/to/wiki --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# [text](target) — non-greedy text, target up to the first closing paren.
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "ftp://", "//", "/")


@dataclass(frozen=True)
class Finding:
    check: str  # "broken-link" | "missing-ko" | "missing-en"
    page: str  # wiki page slug where the problem is observed
    detail: str  # human-readable message


def link_target_to_slug(raw: str) -> str | None:
    """Resolve a markdown link target to a wiki page slug, or None if not a
    wiki-internal page link (external, image, same-page anchor, or nested path)."""
    target = raw.strip()
    if not target:
        return None
    if target.startswith("#"):
        # same-page anchor, not a page reference
        return None
    if target.startswith(_EXTERNAL_PREFIXES):
        return None
    # drop a #section anchor; validate only the page part
    page_part = target.split("#", 1)[0].strip()
    if not page_part:
        return None
    if "/" in page_part:
        # nested path (image dir, asset) — wiki pages are flat slugs
        return None
    if page_part.endswith(".md"):
        page_part = page_part[:-3]
    return page_part or None


def _is_special(slug: str) -> bool:
    return slug.startswith("_")


def check_broken_links(pages: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    known = set(pages)
    for slug in sorted(pages):
        for raw in _LINK_RE.findall(pages[slug]):
            target = link_target_to_slug(raw)
            if target is None:
                continue
            if target not in known:
                findings.append(
                    Finding("broken-link", slug, f"link to missing wiki page '{target}'")
                )
    return findings


def _ko_counterpart_candidates(slug: str) -> list[str]:
    if slug.endswith("_en"):
        return [slug[:-3] + "_ko"]
    return [slug + "_ko"]


def _en_counterpart_candidates(slug: str) -> list[str]:
    base = slug[:-3]  # strip "_ko"
    return [base, base + "_en"]


def check_pairing(pages: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    known = set(pages)
    for slug in sorted(pages):
        if _is_special(slug):
            continue
        if slug.endswith("_ko"):
            if not any(c in known for c in _en_counterpart_candidates(slug)):
                findings.append(
                    Finding("missing-en", slug, "Korean page has no English counterpart")
                )
        else:
            if not any(c in known for c in _ko_counterpart_candidates(slug)):
                findings.append(
                    Finding("missing-ko", slug, "English page has no Korean counterpart")
                )
    return findings


def audit(pages: dict[str, str]) -> list[Finding]:
    """Run all deterministic structural checks over a {slug: text} wiki map."""
    return check_broken_links(pages) + check_pairing(pages)


def load_wiki(wiki_dir: Path) -> dict[str, str]:
    """Load a wiki checkout into a {slug: text} map (slug = filename without .md)."""
    pages: dict[str, str] = {}
    for md in sorted(Path(wiki_dir).glob("*.md")):
        pages[md.stem] = md.read_text(encoding="utf-8")
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wiki", required=True, help="path to a wiki checkout (ghost-alice.wiki)")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = parser.parse_args(argv)

    wiki_dir = Path(args.wiki)
    if not wiki_dir.is_dir():
        print(f"error: wiki path is not a directory: {wiki_dir}", file=sys.stderr)
        return 2

    pages = load_wiki(wiki_dir)
    findings = audit(pages)

    if args.json:
        print(json.dumps([f.__dict__ for f in findings], ensure_ascii=False, indent=2))
    else:
        if not findings:
            print(f"wiki integrity: OK ({len(pages)} pages, no structural drift)")
        else:
            print(f"wiki integrity: {len(findings)} finding(s) across {len(pages)} pages")
            for f in findings:
                print(f"  [{f.check}] {f.page}: {f.detail}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
