"""Report-only Wiki integrity checks.

Validate internal page links, page-name uniqueness, the language switch line,
its pairing, folder placement and sidebar language. External links, images,
same-page anchors and special pages are excluded from page-target checks.
Main-repository contracts remain normative.

Run: python3 -m pytest scripts/tests/test_audit_wiki_integrity.py -q
"""

from __future__ import annotations

import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_wiki_integrity as awi  # noqa: E402


def _checks(findings) -> set:
    return {f.check for f in findings}


def _pages_with(findings, check) -> set:
    return {f.page for f in findings if f.check == check}


# A minimal but structurally-valid wiki: English pages at the root, Korean pages
# under ko/, switch lines paired, sidebars in their folder's language.
CLEAN = {
    "Home": "언어: 🇺🇸 English | [🇰🇷 한국어](홈)\n\nRead [Design Principles](Design-Principles).",
    "ko/홈": "Language: [🇺🇸 English](Home) | 🇰🇷 한국어\n\n[설계 원칙](설계-원칙)을 읽어 주세요.",
    "Design-Principles": "언어: 🇺🇸 English | [🇰🇷 한국어](설계-원칙)\n\nBody.\n\n[← Home](Home)",
    "ko/설계-원칙": "Language: [🇺🇸 English](Design-Principles) | 🇰🇷 한국어\n\n본문.\n\n[← 홈](홈)",
    "_Sidebar": "[Home](Home)\n\n1. [Design Principles](Design-Principles)",
    "ko/_Sidebar": "[홈](홈)\n\n1. [설계 원칙](설계-원칙)",
}


class TestCleanWiki(unittest.TestCase):
    def test_clean_wiki_has_no_findings(self):
        self.assertEqual(awi.audit(CLEAN), [])


class TestBrokenInternalLinks(unittest.TestCase):
    def test_link_to_missing_page_is_flagged(self):
        pages = dict(CLEAN)
        pages["Home"] = CLEAN["Home"] + " See [Gone](this-page-does-not-exist)."
        findings = awi.audit(pages)
        self.assertIn("broken-link", _checks(findings))
        self.assertIn("Home", _pages_with(findings, "broken-link"))

    def test_link_with_md_suffix_resolves(self):
        # [x](Home.md) must resolve to the Home page, not be flagged.
        pages = dict(CLEAN)
        pages["Design-Principles"] = CLEAN["Design-Principles"] + " [Home](Home.md)"
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_link_with_anchor_validates_page_part_only(self):
        pages = dict(CLEAN)
        pages["Home"] = CLEAN["Home"] + " Jump [here](Design-Principles#overview)."
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_link_to_missing_page_with_anchor_is_flagged(self):
        pages = dict(CLEAN)
        pages["Home"] = CLEAN["Home"] + " Jump [here](nope#overview)."
        self.assertIn("broken-link", _checks(awi.audit(pages)))

    def test_link_to_page_in_another_folder_resolves(self):
        # GitHub serves wiki pages by name, so ko/ pages are linked by name.
        pages = dict(CLEAN)
        pages["Home"] = CLEAN["Home"] + " [설계 원칙](설계-원칙)"
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_percent_encoded_korean_link_resolves(self):
        pages = dict(CLEAN)
        pages["Home"] = CLEAN["Home"] + " [홈](%ED%99%88)"
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_sidebar_broken_link_is_caught(self):
        pages = dict(CLEAN)
        pages["_Sidebar"] = "- [Dead](dead-page)"
        self.assertIn("_Sidebar", _pages_with(awi.audit(pages), "broken-link"))


class TestNonOverblock(unittest.TestCase):
    def test_external_links_not_flagged(self):
        pages = dict(CLEAN)
        pages["Home"] = CLEAN["Home"] + (
            " See [site](https://example.com) and [http](http://x.y) "
            "and [mail](mailto:a@b.c) and [abs](/wiki/x)."
        )
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_same_page_anchor_not_flagged(self):
        pages = dict(CLEAN)
        pages["Home"] = CLEAN["Home"] + " Back to [top](#overview)."
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))

    def test_image_or_nested_path_links_not_treated_as_wiki_pages(self):
        pages = dict(CLEAN)
        pages["Home"] = CLEAN["Home"] + " ![logo](images/icon.png) and [asset](sub/dir/file.md)."
        self.assertNotIn("broken-link", _checks(awi.audit(pages)))


class TestDuplicatePages(unittest.TestCase):
    def test_same_page_name_in_two_folders_is_flagged(self):
        pages = dict(CLEAN)
        pages["ko/Home"] = CLEAN["Home"]
        self.assertIn("duplicate-page", _checks(awi.audit(pages)))


class TestLanguageSwitch(unittest.TestCase):
    def test_missing_switch_line_is_flagged(self):
        pages = dict(CLEAN)
        pages["Design-Principles"] = "Body without a switch."
        self.assertIn("Design-Principles", _pages_with(awi.audit(pages), "switch-format"))

    def test_unflagged_switch_line_is_flagged(self):
        pages = dict(CLEAN)
        pages["Design-Principles"] = "Language: English | [한국어](설계-원칙)\n\nBody."
        self.assertIn("Design-Principles", _pages_with(awi.audit(pages), "switch-format"))

    def test_label_in_the_page_language_is_flagged(self):
        # An English page labels its switch in Korean, not "Language:".
        pages = dict(CLEAN)
        pages["Design-Principles"] = "Language: 🇺🇸 English | [🇰🇷 한국어](설계-원칙)\n\nBody."
        self.assertIn("Design-Principles", _pages_with(awi.audit(pages), "switch-format"))

    def test_korean_page_outside_ko_folder_is_flagged(self):
        pages = dict(CLEAN)
        pages["설계-원칙"] = pages.pop("ko/설계-원칙")
        self.assertIn("설계-원칙", _pages_with(awi.audit(pages), "switch-folder"))

    def test_english_page_inside_ko_folder_is_flagged(self):
        pages = dict(CLEAN)
        pages["ko/Design-Principles"] = pages.pop("Design-Principles")
        self.assertIn("ko/Design-Principles", _pages_with(awi.audit(pages), "switch-folder"))

    def test_switch_that_does_not_come_back_is_flagged(self):
        pages = dict(CLEAN)
        pages["Architecture"] = "언어: 🇺🇸 English | [🇰🇷 한국어](설계-원칙)\n\nBody."
        self.assertIn("Architecture", _pages_with(awi.audit(pages), "switch-pair"))

    def test_switch_to_a_same_language_page_is_flagged(self):
        pages = dict(CLEAN)
        pages["Design-Principles"] = "언어: 🇺🇸 English | [🇰🇷 한국어](Home)\n\nBody."
        self.assertIn("Design-Principles", _pages_with(awi.audit(pages), "switch-pair"))

    def test_special_pages_carry_no_switch_line(self):
        pages = dict(CLEAN)
        pages["_Footer"] = "footer"
        self.assertEqual(awi.audit(pages), [])


class TestSidebarLanguage(unittest.TestCase):
    def test_korean_sidebar_linking_an_english_page_is_flagged(self):
        pages = dict(CLEAN)
        pages["ko/_Sidebar"] = CLEAN["ko/_Sidebar"] + "\n2. [Design Principles](Design-Principles)"
        self.assertIn("ko/_Sidebar", _pages_with(awi.audit(pages), "sidebar-language"))

    def test_english_sidebar_linking_a_korean_page_is_flagged(self):
        pages = dict(CLEAN)
        pages["_Sidebar"] = CLEAN["_Sidebar"] + " · [설계 원칙](설계-원칙)"
        self.assertIn("_Sidebar", _pages_with(awi.audit(pages), "sidebar-language"))


class TestLoadWiki(unittest.TestCase):
    def test_loads_folders_skips_dot_directories_and_normalizes_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ko").mkdir()
            (root / ".git").mkdir()
            (root / "Home.md").write_text(CLEAN["Home"], encoding="utf-8")
            decomposed = unicodedata.normalize("NFD", "홈")
            (root / "ko" / f"{decomposed}.md").write_text(CLEAN["ko/홈"], encoding="utf-8")
            (root / ".git" / "ignored.md").write_text("x", encoding="utf-8")
            pages = awi.load_wiki(root)
        self.assertEqual(set(pages), {"Home", "ko/홈"})


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
