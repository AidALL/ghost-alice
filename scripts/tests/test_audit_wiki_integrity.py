"""TDD suite for the report-only wiki integrity audit (scripts/audit_wiki_integrity.py).

Why this exists (design+wiki review, this session): the project Wiki is a named
design surface. architecture.md line 5 ("Long-form design background lives in
the Wiki") and the Wiki design page ("normative SSOT ... remains in the main
repository") set up a two-way repo<->wiki contract. Every comparable repo-internal
doc surface has a validator (validate_entrypoints, check_skill_gate_contract,
i18n parity, toc parity, public_surfaces, platform_adapters, pending_merge_layering),
but the Wiki -- an external repo with no built-in link validation -- has zero
automated integrity protection. This matches Workstream E ("wiki and repo recovery
guide drift") of the prose-to-code separation direction.

Scope is deliberately narrow. During discovery this session, content/path/skill-name
drift heuristics were shown to over-flag (SKILL.md, settings.json, claim-evidence-map,
recovery-action all falsely flagged). So this audit checks ONLY deterministic,
zero/low-false-positive structural signals, mirroring the test_validate_toc_parity.py
philosophy:
- broken internal wiki links (a [text](Target) pointing at a non-existent wiki page)
- en<->ko page pairing (handles both name<->name_ko and name_en<->name_ko)
- it must NOT flag external links, image links, same-page anchors, or special pages

Run: python3 -m pytest scripts/tests/test_audit_wiki_integrity.py -q
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_wiki_integrity as awi  # noqa: E402


def _checks(findings) -> set:
    return {f.check for f in findings}


def _pages_with(findings, check) -> set:
    return {f.page for f in findings if f.check == check}


# A minimal but structurally-valid wiki: every content page paired, links resolve.
CLEAN = {
    "Home": "Welcome. See [Design](ghost-alice-os-design_en) and [Addons](addon-authoring).",
    "Home_ko": "환영. [설계](ghost-alice-os-design_ko) 참고.",
    "ghost-alice-os-design_en": "Language: English | [한국어](ghost-alice-os-design_ko)\nBody.",
    "ghost-alice-os-design_ko": "Language: [English](ghost-alice-os-design_en) | 한국어\n본문.",
    "addon-authoring": "See [Home](Home).",
    "addon-authoring_ko": "[Home](Home_ko) 참고.",
    "_Sidebar": "- [Home](Home)\n- [Design](ghost-alice-os-design_en)",
    "_Footer": "(c) project",
}


class TestCleanWiki(unittest.TestCase):
    def test_clean_wiki_has_no_findings(self):
        self.assertEqual(awi.audit(CLEAN), [])


class TestBrokenInternalLinks(unittest.TestCase):
    def test_link_to_missing_page_is_flagged(self):
        pages = dict(CLEAN)
        pages["Home"] = "See [Gone](this-page-does-not-exist)."
        findings = awi.audit(pages)
        self.assertIn("broken-link", _checks(findings))
        self.assertIn("Home", _pages_with(findings, "broken-link"))

    def test_link_with_md_suffix_resolves(self):
        # [x](Home.md) must resolve to the Home page, not be flagged.
        pages = dict(CLEAN)
        pages["addon-authoring"] = "See [Home](Home.md)."
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_link_with_anchor_validates_page_part_only(self):
        pages = dict(CLEAN)
        pages["addon-authoring"] = "Jump [here](ghost-alice-os-design_en#overview)."
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_link_to_missing_page_with_anchor_is_flagged(self):
        pages = dict(CLEAN)
        pages["addon-authoring"] = "Jump [here](nope#overview)."
        self.assertIn("broken-link", _checks(awi.audit(pages)))

    def test_sidebar_broken_link_is_caught(self):
        pages = dict(CLEAN)
        pages["_Sidebar"] = "- [Dead](dead-page)"
        self.assertIn("_Sidebar", _pages_with(awi.audit(pages), "broken-link"))


class TestNonOverblock(unittest.TestCase):
    def test_external_links_not_flagged(self):
        pages = dict(CLEAN)
        pages["Home"] = (
            "See [site](https://example.com) and [http](http://x.y) "
            "and [mail](mailto:a@b.c) and [abs](/wiki/x)."
        )
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_same_page_anchor_not_flagged(self):
        pages = dict(CLEAN)
        pages["Home"] = "Back to [top](#overview)."
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_image_or_nested_path_links_not_treated_as_wiki_pages(self):
        pages = dict(CLEAN)
        pages["Home"] = "![logo](images/icon.png) and [asset](sub/dir/file.md)."
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))


class TestEnKoPairing(unittest.TestCase):
    def test_en_page_without_ko_is_flagged(self):
        pages = dict(CLEAN)
        del pages["addon-authoring_ko"]
        findings = awi.audit(pages)
        self.assertIn("missing-ko", _checks(findings))
        self.assertIn("addon-authoring", _pages_with(findings, "missing-ko"))

    def test_ko_page_without_en_is_flagged(self):
        pages = dict(CLEAN)
        del pages["ghost-alice-os-design_en"]
        # fix the now-dangling link so we isolate the pairing signal
        pages["ghost-alice-os-design_ko"] = "Language: 한국어\n본문."
        pages["Home"] = "Welcome. See [Addons](addon-authoring)."
        findings = awi.audit(pages)
        self.assertIn("missing-en", _checks(findings))
        self.assertIn("ghost-alice-os-design_ko", _pages_with(findings, "missing-en"))

    def test_en_suffix_pairs_with_ko_suffix(self):
        # name_en <-> name_ko convention must NOT be flagged.
        pages = {
            "doc_en": "[ko](doc_ko)",
            "doc_ko": "[en](doc_en)",
        }
        self.assertEqual(_checks(awi.audit(pages)), set())

    def test_plain_name_pairs_with_name_ko(self):
        pages = {
            "guide": "[ko](guide_ko)",
            "guide_ko": "[en](guide)",
        }
        self.assertEqual(_checks(awi.audit(pages)), set())

    def test_special_pages_exempt_from_pairing(self):
        # _Sidebar / _Footer are GitHub wiki special pages; no _ko required.
        pages = {
            "guide": "[ko](guide_ko)",
            "guide_ko": "[en](guide)",
            "_Sidebar": "- [guide](guide)",
            "_Footer": "footer",
        }
        self.assertEqual(_checks(awi.audit(pages)), set())


class TestRealWiki(unittest.TestCase):
    """Non-overblock evidence against the real cloned wiki when present."""

    def test_real_wiki_clone_is_clean_if_available(self):
        wiki_dir = Path("/tmp/ga-wiki")
        if not wiki_dir.exists():
            self.skipTest("real wiki clone not present at /tmp/ga-wiki")
        pages = awi.load_wiki(wiki_dir)
        findings = awi.audit(pages)
        self.assertEqual(
            findings, [], msg=f"real wiki reported drift: {[(f.check, f.page) for f in findings]}"
        )


if __name__ == "__main__":
    unittest.main()
