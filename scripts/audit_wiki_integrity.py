#!/usr/bin/env python3
"""audit_wiki_integrity.py. Report-only structural integrity audit for the Wiki.

The Wiki is a separate checkout containing design pages and links to the
canonical repository contracts. The language policy
(docs/concepts/language-policy.md) defines its layout: English pages at the
Wiki root, Korean pages directly under ``ko/``, a sidebar per folder that lists
the pages of that folder's language, no footer, and a language switch line at
the top of every content page. This command checks page links and that layout.
It does not validate architecture prose, runtime behavior or remote
publication state. Findings produce a nonzero exit status; the audit neither
edits pages nor controls runtime permission.

Checks:
- broken-link      : a [text](Target) link pointing at a non-existent wiki page.
- duplicate-page   : two files share one page name; GitHub serves pages by name.
- switch-format    : a content page whose first line is not a language switch line.
- switch-folder    : an English page outside the root or a Korean page outside ``ko/``.
- switch-pair      : a switch link whose target does not switch back to the page.
- missing-sidebar  : English or Korean pages exist without their folder's ``_Sidebar``.
- sidebar-coverage : a content page that its language's sidebar does not list.
- sidebar-language : a sidebar link to a page in the other language.
- footer           : a ``_Footer`` page; the Wiki has no footer.

GitHub wiki special pages (any ``_``-prefixed page such as ``_Sidebar``) carry
no switch line; their links are still checked.

Usage:
    python3 scripts/audit_wiki_integrity.py --wiki /path/to/ghost-alice.wiki
    python3 scripts/audit_wiki_integrity.py --wiki /path/to/wiki --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

# [text](target) — non-greedy text, target up to the first closing paren.
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "ftp://", "//", "/")

# The switch label is written in the other language.
_EN_SWITCH_RE = re.compile(r"^언어: 🇺🇸 English \| \[🇰🇷 한국어\]\(([^)]+)\)$")
_KO_SWITCH_RE = re.compile(r"^Language: \[🇺🇸 English\]\(([^)]+)\) \| 🇰🇷 한국어$")

_KO_FOLDER = "ko"
# The folder holding each language's pages and sidebar ("" is the Wiki root).
_LANGUAGE_FOLDER = {"en": "", "ko": _KO_FOLDER}


@dataclass(frozen=True)
class Finding:
    check: str  # see the module docstring for check names
    page: str  # wiki page path (relative, without .md) where the problem is observed
    detail: str  # human-readable message


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def page_name(key: str) -> str:
    """The name GitHub serves a page under: its file name without folders."""
    return key.rsplit("/", 1)[-1]


def _folder(key: str) -> str:
    return key.rsplit("/", 1)[0] if "/" in key else ""


def _is_special(key: str) -> bool:
    return page_name(key).startswith("_")


def link_target_to_slug(raw: str) -> str | None:
    'Resolve a markdown link target to a wiki page name, or None if not a\n    wiki-internal page link (external, image, same-page anchor, or nested path).'
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
        # nested path (image dir, asset) — wiki page links use page names
        return None
    if page_part.endswith(".md"):
        page_part = page_part[:-3]
    return _nfc(unquote(page_part)) or None


def _internal_targets(text: str) -> list[str]:
    targets = []
    for raw in _LINK_RE.findall(text):
        target = link_target_to_slug(raw)
        if target is not None:
            targets.append(target)
    return targets


def parse_switch(text: str) -> tuple[str, str] | None:
    """Return (language, target page name) from the first non-empty line."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    for language, pattern in (("en", _EN_SWITCH_RE), ("ko", _KO_SWITCH_RE)):
        match = pattern.match(first)
        if match:
            return language, link_target_to_slug(match.group(1)) or ""
    return None


def _by_name(pages: dict[str, str]) -> dict[str, str]:
    return {page_name(key): key for key in sorted(pages)}


def check_duplicate_pages(pages: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    seen: dict[str, str] = {}
    for key in sorted(pages):
        if _is_special(key):
            continue  # each folder may carry its own _Sidebar
        name = page_name(key)
        if name in seen:
            findings.append(
                Finding("duplicate-page", key, f"page name '{name}' is also used by '{seen[name]}'")
            )
        else:
            seen[name] = key
    return findings


def check_broken_links(pages: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    known = set(_by_name(pages))
    for key in sorted(pages):
        for target in _internal_targets(pages[key]):
            if target not in known:
                findings.append(
                    Finding("broken-link", key, f"link to missing wiki page '{target}'")
                )
    return findings


def check_language_switches(pages: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    names = _by_name(pages)
    for key in sorted(pages):
        if _is_special(key):
            continue
        switch = parse_switch(pages[key])
        if switch is None:
            findings.append(
                Finding("switch-format", key, "first line is not a language switch line")
            )
            continue
        language, target = switch
        if _folder(key) != _LANGUAGE_FOLDER[language]:
            expected = "directly under ko/" if language == "ko" else "at the wiki root"
            findings.append(
                Finding("switch-folder", key, f"{language} page must live {expected}")
            )
        target_key = names.get(target)
        if target_key is None:
            continue  # reported as broken-link
        back = parse_switch(pages[target_key])
        if back is None or back[0] == language or back[1] != page_name(key):
            findings.append(
                Finding("switch-pair", key, f"switch target '{target}' does not switch back to this page")
            )
    return findings


def check_sidebars(pages: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    names = _by_name(pages)
    languages: dict[str, str] = {}
    for key in sorted(pages):
        switch = None if _is_special(key) else parse_switch(pages[key])
        if switch is not None:
            languages[key] = switch[0]
    for language, folder in _LANGUAGE_FOLDER.items():
        sidebar = f"{folder}/_Sidebar" if folder else "_Sidebar"
        content = [key for key, page_language in languages.items() if page_language == language]
        if sidebar not in pages:
            if content:
                findings.append(
                    Finding("missing-sidebar", sidebar, f"{language} pages have no {sidebar}.md")
                )
            continue
        listed = _internal_targets(pages[sidebar])
        for key in content:
            if page_name(key) not in listed:
                findings.append(
                    Finding("sidebar-coverage", key, f"page is not listed in {sidebar}")
                )
        for target in listed:
            target_language = languages.get(names.get(target, ""))
            if target_language is not None and target_language != language:
                findings.append(
                    Finding("sidebar-language", sidebar, f"{language} sidebar links {target_language} page '{target}'")
                )
    return findings


def check_footer(pages: dict[str, str]) -> list[Finding]:
    return [
        Finding("footer", key, "the wiki has no footer")
        for key in sorted(pages)
        if page_name(key) == "_Footer"
    ]


def audit(pages: dict[str, str]) -> list[Finding]:
    """Run all deterministic structural checks over a {page path: text} wiki map."""
    return (
        check_duplicate_pages(pages)
        + check_broken_links(pages)
        + check_language_switches(pages)
        + check_sidebars(pages)
        + check_footer(pages)
    )


def load_wiki(wiki_dir: Path) -> dict[str, str]:
    """Load a wiki checkout into {page path: text}; the path is relative to the
    wiki root, without .md, with '/' separators (for example 'ko/홈')."""
    root = Path(wiki_dir)
    pages: dict[str, str] = {}
    for md in sorted(root.rglob("*.md")):
        relative = md.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        pages[_nfc(relative.with_suffix("").as_posix())] = md.read_text(encoding="utf-8")
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
