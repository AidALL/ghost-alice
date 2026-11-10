"""Unfenced ATX heading facts for repository document checks."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass


_FENCE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>[^\r\n]*)$")
_ATX = re.compile(r"^ {0,3}(?P<marks>#{1,6})(?:[ \t]+(?P<title>.*)|[ \t]*)$")


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    start: int
    end: int


def iter_unfenced_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield character offsets and original lines outside backtick/tilde fences.

    Fence delimiters use up to three leading spaces. A closing delimiter must
    use the opening character, at least its length, and no information string.
    """
    fence: tuple[str, int] | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE.fullmatch(line.rstrip("\r\n"))
        if fence is not None:
            if (match and match["marker"][0] == fence[0]
                    and len(match["marker"]) >= fence[1] and not match["info"].strip()):
                fence = None
        elif match and (match["marker"][0] != "`" or "`" not in match["info"]):
            fence = match["marker"][0], len(match["marker"])
        else:
            yield offset, line
        offset += len(line)


def atx_heading(offset: int, line: str) -> Heading | None:
    """Read an ATX heading from one unfenced source line."""
    match = _ATX.fullmatch(line.rstrip("\r\n"))
    if match is None:
        return None
    title = re.sub(r"[ \t]+#+[ \t]*$", "", match["title"] or "").strip()
    return Heading(len(match["marks"]), title, offset, offset + len(line))


def atx_headings(text: str) -> Iterator[Heading]:
    """Yield real ATX headings with source spans; leave section bodies intact."""
    for offset, line in iter_unfenced_lines(text):
        if heading := atx_heading(offset, line):
            yield heading
