"""Read skill frontmatter and its derived catalog using the standard library."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def split_frontmatter(text: str) -> tuple[str, int]:
    """Return the raw metadata and body character offset without parsing values.

    Preserve the catalog reader's delimiter rules: an opening --- prefix and a
    closing line whose stripped value is ---. The closing newline belongs to
    the frontmatter; a closing delimiter at EOF leaves an empty body.
    """
    if not text.startswith("---"):
        raise ValueError("missing frontmatter start delimiter (---)")
    lines = text.split("\n")
    header_start = cursor = len(lines[0]) + 1
    for line in lines[1:]:
        line_end = cursor + len(line)
        if line.strip() == "---":
            return text[header_start:cursor], min(line_end + 1, len(text))
        cursor = line_end + 1
    raise ValueError("missing frontmatter end delimiter (---)")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], int]:
    """Return scalar/list metadata and the body offset; this is not full YAML."""
    header, body_start = split_frontmatter(text)
    metadata: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for raw in header.split("\n"):
        if not raw.strip():
            continue
        if raw.lstrip().startswith("- "):
            if current_list is None:
                raise ValueError(f"list item appeared without list context: {raw}")
            current_list.append(_strip_quotes(raw.lstrip()[2:].strip()))
            continue
        match = re.match(r"^([A-Za-z][\w-]*)\s*:\s*(.*)$", raw)
        if not match:
            if current_key and isinstance(metadata.get(current_key), str):
                metadata[current_key] += " " + raw.strip()
                continue
            raise ValueError(f"frontmatter parse failed: {raw}")
        key, value = match.group(1), match.group(2).strip()
        current_key = key
        current_list = None
        if value == "":
            current_list = []
            metadata[key] = current_list
        else:
            metadata[key] = _strip_quotes(value)
    return metadata, body_start


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def load_skill_catalog(path: Path) -> dict[str, Any]:
    """Read a catalog with a skills list of named objects, including an empty list.

    Keep all fields for caller projections. I/O, JSON decoding and shape errors
    propagate to callers, which own their diagnostics and required skill sets.
    """
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError("catalog must be an object")
    if "skills" not in catalog:
        raise ValueError("catalog is missing the 'skills' field")
    if not isinstance(catalog["skills"], list):
        raise ValueError("catalog field 'skills' is not a list")
    for index, skill in enumerate(catalog["skills"]):
        if not isinstance(skill, dict):
            raise ValueError(f"catalog skills[{index}] is not an object")
        if not isinstance(skill.get("name"), str) or not skill["name"].strip():
            raise ValueError(f"catalog skills[{index}].name must be a nonempty string")
    return catalog
