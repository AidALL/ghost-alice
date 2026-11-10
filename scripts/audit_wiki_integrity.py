#!/usr/bin/env python3
"""audit_wiki_integrity.py. Report-only integrity audit for the Wiki.

The Wiki is a separate checkout containing design pages and links to the
canonical repository contracts. The language policy
(docs/concepts/language-policy.md) defines its layout: English pages at the
Wiki root, Korean pages directly under ``ko/``, a sidebar per folder that lists
the pages of that folder's language, no footer, and a language switch line at
the top of every content page. This command checks page links and that layout,
and checks every page against the public-text rules in
scripts/public_text_rules.py. Beyond these fixed patterns, it does not judge
what the prose says, and it does not check runtime behavior or remote
publication state. Findings produce a nonzero exit status; the audit neither
edits pages nor controls runtime permission.

Checks:
- broken-link      : a [text](Target) link pointing at a non-existent wiki page.
- duplicate-page   : two files share one page name; GitHub serves pages by name.
- page-folder      : a content page neither at the Wiki root nor directly under ``ko/``.
- switch-format    : a content page whose first line is not a language switch line.
- switch-pair      : a switch line whose own-language link does not name the page,
                     or whose other-language link does not name a page in the other
                     language's folder that carries the same switch line. A link to a
                     page that does not exist is reported as broken-link instead.
- missing-sidebar  : English or Korean pages exist without their folder's ``_Sidebar``.
- sidebar-coverage : a content page that its language's sidebar does not list.
- sidebar-language : a sidebar link to a page in the other language.
- footer           : a ``_Footer`` page; the Wiki has no footer.
- opaque-term      : a line with an opaque term or a bare layer token.
- removed-command  : a line with a removed or unimplemented installer command
                     or a stale installer claim.
- powershell-command : a line with a powershell code fence, an install.ps1 call
                     or a PowerShell-style installer flag, or a line that
                     presents Git Bash as the Windows default.

A page's language is its folder's: English at the Wiki root, Korean directly
under ``ko/``. GitHub wiki special pages (any ``_``-prefixed page such as
``_Sidebar``) carry no switch line; their links are still checked.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import public_text_rules  # noqa: E402

# [text](target) — non-greedy text, target up to the first closing paren.
_LINK_RE = re.compile(r"\[[^\[\]]*\]\(([^)]+)\)")

_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "ftp://", "//", "/")

# Both editions carry the same line: the English page link, then the Korean one.
_SWITCH_RE = re.compile(r"^\[🇺🇸 English\]\(([^)]+)\) \| \[🇰🇷 한국어\]\(([^)]+)\)$")

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
    for match in _LINK_RE.finditer(text):
        prefix = text[:match.start()]
        if prefix.endswith('!'):
            before_marker = prefix[:-1]
            if (len(before_marker) - len(before_marker.rstrip('\\'))) % 2 == 0:
                continue
        elif (len(prefix) - len(prefix.rstrip('\\'))) % 2:
            continue
        target = link_target_to_slug(match[1])
        if target is not None:
            targets.append(target)
    return targets


def page_language(key: str) -> str | None:
    """The language whose folder directly holds the page, or None."""
    folder = _folder(key)
    for language, language_folder in _LANGUAGE_FOLDER.items():
        if folder == language_folder:
            return language
    return None


def parse_switch(text: str) -> dict[str, str] | None:
    """Return {language: target page name} from the first non-empty line, or None
    when that line is not a switch line; a link that names no wiki page maps to ""."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    match = _SWITCH_RE.match(first)
    if not match:
        return None
    return {
        language: link_target_to_slug(target) or ""
        for language, target in zip(("en", "ko"), match.groups())
    }


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
        language = page_language(key)
        if language is None:
            findings.append(
                Finding("page-folder", key, "page must live at the wiki root or directly under ko/")
            )
        switch = parse_switch(pages[key])
        if switch is None:
            findings.append(
                Finding("switch-format", key, "first line is not a language switch line")
            )
            continue
        if language is None:
            continue
        pair_language = "ko" if language == "en" else "en"
        pair_key = names.get(switch[pair_language])
        if pair_key is None and switch[pair_language] and switch[language] == page_name(key):
            continue  # a missing pair page is reported as broken-link
        if (
            switch[language] != page_name(key)
            or pair_key is None
            or page_language(pair_key) != pair_language
            or parse_switch(pages[pair_key]) != switch
        ):
            findings.append(
                Finding("switch-pair", key, f"switch line does not pair this page with a {pair_language} page carrying the same line")
            )
    return findings


def check_sidebars(pages: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    names = _by_name(pages)
    languages: dict[str, str] = {}
    for key in sorted(pages):
        language = None if _is_special(key) else page_language(key)
        if language is not None:
            languages[key] = language
    for language, folder in _LANGUAGE_FOLDER.items():
        sidebar = f"{folder}/_Sidebar" if folder else "_Sidebar"
        content = [key for key, key_language in languages.items() if key_language == language]
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


def check_public_text(pages: dict[str, str]) -> list[Finding]:
    """Apply the rules in public_text_rules.py to every page, special pages included."""
    findings: list[Finding] = []
    for key in sorted(pages):
        text = pages[key]
        for lineno, message in public_text_rules.term_findings(text):
            findings.append(Finding("opaque-term", key, f"line {lineno}: {message}"))
        for check, patterns in (
            ("removed-command", public_text_rules.REMOVED_COMMAND_PATTERNS),
            ("powershell-command", public_text_rules.POWERSHELL_COMMAND_PATTERNS),
        ):
            for lineno, label, line in public_text_rules.pattern_findings(text, patterns):
                findings.append(Finding(check, key, f"line {lineno}: {label}: {line}"))
    return findings


def audit(pages: dict[str, str]) -> list[Finding]:
    """Run all deterministic checks over a {page path: text} wiki map."""
    return (
        check_duplicate_pages(pages)
        + check_broken_links(pages)
        + check_language_switches(pages)
        + check_sidebars(pages)
        + check_footer(pages)
        + check_public_text(pages)
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
            print(f"wiki integrity: OK ({len(pages)} pages, no findings)")
        else:
            print(f"wiki integrity: {len(findings)} finding(s) across {len(pages)} pages")
            for f in findings:
                print(f"  [{f.check}] {f.page}: {f.detail}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
