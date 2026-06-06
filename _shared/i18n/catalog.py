"""i18n (internationalization) message catalog loader.

Loads messages.json once at import time.  Provides lookup() and
validate_catalog() for use by installer scripts.
"""

import json
from pathlib import Path

_CATALOG_PATH = Path(__file__).with_name("messages.json")

with _CATALOG_PATH.open(encoding="utf-8") as _fh:
    _CATALOG: dict[str, dict[str, str]] = json.load(_fh)


def lookup(key: str, lang: str) -> str:
    """Return the translation for *key* in *lang*.

    Falls back to the ``"en"`` value when the requested *lang* is absent or
    empty in the entry.  Raises ``KeyError`` if *key* is not in the catalog.
    """
    entry = _CATALOG[key]  # raises KeyError if key absent
    value = entry.get(lang, "")
    if value:
        return value
    return entry["en"]


def validate_catalog() -> None:
    """Validate every entry has a non-empty ``en`` value.

    Raises ``ValueError`` if any entry is missing or has an empty ``en`` value.
    """
    for key, entry in _CATALOG.items():
        if not entry.get("en"):
            raise ValueError(f"i18n catalog missing en for key: {key}")
