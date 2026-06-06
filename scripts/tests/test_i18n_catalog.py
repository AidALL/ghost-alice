"""Tests for _shared/i18n/catalog.py.

i18n is the standard abbreviation for internationalization.
"""

import sys
import unittest
from pathlib import Path

# _shared/ lives two levels above this file:
# scripts/tests/test_i18n_catalog.py -> scripts/tests -> scripts -> repo-root -> _shared
REPO_ROOT = Path(__file__).resolve().parents[2]
_shared_path = str(REPO_ROOT / "_shared")
if _shared_path not in sys.path:
    sys.path.insert(0, _shared_path)

from i18n.catalog import lookup, validate_catalog  # noqa: E402


class TestI18nCatalogLookup(unittest.TestCase):
    def test_lookup_returns_lang_value(self):
        self.assertEqual(lookup("INSTALL_HELP_HEADER", "en"), "Show this help")

    def test_validate_catalog_passes_on_seed(self):
        # Should not raise with the seed entry present
        validate_catalog()

    def test_missing_lang_falls_back_to_en(self):
        # Any language code that is not in the entry falls back to "en"
        result = lookup("INSTALL_HELP_HEADER", "fr")
        self.assertEqual(result, "Show this help")


if __name__ == "__main__":
    unittest.main()
