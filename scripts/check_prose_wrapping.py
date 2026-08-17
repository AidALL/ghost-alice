#!/usr/bin/env python3
# Canonical source: Ghost-ALICE core scripts/check_prose_wrapping.py.
# Addon releases vendor this file byte-for-byte so their local gate remains runnable.
"""Reject Markdown prose paragraphs split across physical source lines."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

MARKDOWN_SUFFIXES = {".md", ".markdown"}
RULE = "prose-paragraph-single-source-line"
MESSAGE = "prose paragraph spans multiple physical source lines"
BLOCKQUOTE_RE = re.compile(r"^ {0,3}>[ \t]?")
FENCE_RE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
ATX_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
SETEXT_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
THEMATIC_RE = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
LIST_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-+*]|\d{1,9}[.)])(?:(?P<space>[ \t]+)(?P<text>.*))?$")
LINK_RE = re.compile(r"^ {0,3}\[[^\]]+\]:[ \t]*(?:\S.*)?$")
PROTOCOL_RE = re.compile(r"^\[[A-Za-z][A-Za-z0-9_-]*\][ \t]*$")
DIRECTIVE_RE = re.compile(r"^(?::::+|!{3,}|\?{3})(?:[ \t]+|$)")
TABLE_DELIMITER_RE = re.compile(r"^\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$")
RAW_HTML_RE = re.compile(r"^<(script|pre|style|textarea)(?:[ \t]|>|$)", re.I)
BLOCK_HTML_RE = re.compile(r"^</?(?P<tag>[A-Za-z][A-Za-z0-9-]*)(?:[ \t]|/?>|$)")
STANDALONE_HTML_RE = re.compile(r"^</?[A-Za-z][A-Za-z0-9-]*(?:[ \t]+[^<>]*)?/?>[ \t]*$")
HTML_BLOCK_TAGS = {
    "address", "article", "aside", "base", "basefont", "blockquote", "body", "caption", "center", "col", "colgroup", "dd", "details", "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "frame", "frameset", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hr", "html", "iframe", "legend", "li", "link", "main", "menu", "menuitem", "nav", "noframes", "ol", "optgroup", "option", "p", "param", "search", "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead", "title", "tr", "track", "ul",
}
class ScanError(RuntimeError):
    """Raised when the release Markdown surface cannot be scanned."""
@dataclass(frozen=True)
class Violation:
    path: str
    start_line: int
    end_line: int
    rule: str = RULE
    message: str = MESSAGE
def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="surrogateescape")
def release_markdown_paths(repo_root: Path) -> list[tuple[str, Path]]:
    """Return tracked and unignored untracked Markdown in stable path order."""
    result = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=repo_root, capture_output=True, check=False)
    if result.returncode:
        raise ScanError(_decode(result.stderr).strip() or "git ls-files failed")
    root = repo_root.resolve()
    selected: dict[str, Path] = {}
    for raw_path in result.stdout.split(b"\0"):
        relative = _decode(raw_path).replace("\\", "/") if raw_path else ""
        candidate = repo_root / Path(relative)
        if Path(relative).suffix.lower() not in MARKDOWN_SUFFIXES:
            continue
        try:
            candidate.resolve().relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            selected[relative] = candidate
    return [(relative, selected[relative]) for relative in sorted(selected)]
def _strip_blockquote(line: str) -> tuple[int, str]:
    depth, body = 0, line
    while match := BLOCKQUOTE_RE.match(body):
        depth += 1
        body = body[match.end() :]
    return depth, body
def _indent(text: str) -> int:
    width = 0
    for character in text:
        if character == " ":
            width += 1
        elif character == "\t":
            width += 4 - width % 4
        else:
            break
    return width
def _drop_indent(text: str, target: int) -> str:
    width = index = 0
    while index < len(text) and width < target and text[index] in " \t":
        width += 1 if text[index] == " " else 4 - width % 4
        index += 1
    return text[index:]
def _has_pipe(text: str) -> bool:
    escaped = False
    for character in text:
        if character == "\\":
            escaped = not escaped
        else:
            if character == "|" and not escaped:
                return True
            escaped = False
    return False
def _structural_lines(lines: list[str]) -> set[int]:
    normalized = [_strip_blockquote(line) for line in lines]
    structural: set[int] = set()
    for index in range(1, len(lines)):
        depth, body = normalized[index]
        previous_depth, previous = normalized[index - 1]
        if depth == previous_depth and previous.strip() and SETEXT_RE.fullmatch(body):
            structural.update((index - 1, index))
        if depth != previous_depth or not TABLE_DELIMITER_RE.fullmatch(body.lstrip(" \t")) or not _has_pipe(previous.lstrip(" \t")):
            continue
        structural.update((index - 1, index))
        cursor = index + 1
        while cursor < len(lines):
            row_depth, row = normalized[cursor]
            if row_depth != depth or not row.strip() or not _has_pipe(row.lstrip(" \t")):
                break
            structural.add(cursor)
            cursor += 1
    return structural
def _frontmatter_end(lines: list[str]) -> int | None:
    if not lines or re.fullmatch(r"---[ \t]*", lines[0]) is None:
        return None
    for index, line in enumerate(lines[1:], start=1):
        if re.fullmatch(r"(?:---|\.\.\.)[ \t]*", line):
            return index
    return None
def _hard_break(text: str) -> bool:
    stripped = text.rstrip()
    return len(text) - len(text.rstrip(" ")) >= 2 or (len(stripped) - len(stripped.rstrip("\\"))) % 2 == 1 or stripped.lower().endswith(("<br>", "<br/>", "<br />"))
def _standalone_code_span(text: str) -> bool:
    text = text.strip()
    opening = len(text) - len(text.lstrip("`"))
    cursor = opening
    while opening and cursor < len(text):
        if text[cursor] != "`":
            cursor += 1
            continue
        end = cursor
        while end < len(text) and text[end] == "`":
            end += 1
        if end - cursor == opening:
            return cursor > opening and end == len(text)
        cursor = end
    return False
def _fence_open(text: str) -> tuple[str, int] | None:
    match = FENCE_RE.match(text)
    if match is None or match.group("marker")[0] == "`" and "`" in match.group("info"):
        return None
    return match.group("marker")[0], len(match.group("marker"))
def _fence_close(text: str, fence: tuple[str, int]) -> bool:
    match = FENCE_RE.match(text)
    return bool(match and match.group("marker")[0] == fence[0] and len(match.group("marker")) >= fence[1] and not match.group("info").strip())
def _html_block_start(text: str) -> tuple[str, str | None] | None:
    stripped, lowered = text.lstrip(), text.lstrip().lower()
    for prefix, marker in (("<!--", "-->"), ("<![cdata[", "]]>") , ("<?", "?>")):
        if lowered.startswith(prefix):
            return ("line", None) if marker in lowered else ("marker", marker)
    if re.match(r"^<![A-Z]", stripped):
        return ("line", None) if ">" in stripped else ("marker", ">")
    if match := RAW_HTML_RE.match(stripped):
        marker = f"</{match.group(1).lower()}>"
        return ("line", None) if marker in lowered else ("marker", marker)
    match = BLOCK_HTML_RE.match(stripped)
    return ("blank", None) if match and (match.group("tag").lower() in HTML_BLOCK_TAGS or STANDALONE_HTML_RE.fullmatch(stripped)) else None
def prose_wrapping_violations(relative_path: str, path: Path) -> list[Violation]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as error:
        raise ScanError(f"{relative_path}: {error}") from error
    structural, frontmatter_end = _structural_lines(lines), _frontmatter_end(lines)
    violations: list[Violation] = []
    segment: list[int] = []
    context: tuple[object, ...] | None = None
    fence: tuple[str, int] | None = None
    html: tuple[str, str | None] | None = None
    math_fence = link_continuation = False
    active_list: tuple[int, int, int] | None = None
    list_serial = 0

    def flush() -> None:
        nonlocal segment, context
        if len(segment) > 1:
            violations.append(Violation(relative_path, segment[0], segment[-1]))
        segment, context = [], None

    def prose(line_number: int, line_context: tuple[object, ...], text: str) -> None:
        nonlocal context
        if context != line_context:
            flush()
            context = line_context
        segment.append(line_number)
        if _hard_break(text):
            flush()

    for index, raw_line in enumerate(lines):
        line_number = index + 1
        if frontmatter_end is not None and index <= frontmatter_end:
            flush()
            continue
        quote_depth, body = _strip_blockquote(raw_line)
        if not body.strip():
            flush()
            link_continuation = False
            if html and html[0] == "blank":
                html = None
            continue
        leading = _indent(body)
        lazy_list = bool(active_list and quote_depth == 0 and context == ("list", active_list[0], active_list[1]))
        if active_list and not lazy_list and (quote_depth != active_list[1] or leading < active_list[2]):
            active_list = None
        prefix = active_list[2] if active_list else 0
        syntax = _drop_indent(body, prefix) if active_list else body
        stripped = syntax.strip()
        if html:
            flush()
            if html[0] == "marker" and html[1] in stripped.lower():
                html = None
            continue
        if fence:
            flush()
            if _fence_close(syntax, fence):
                fence = None
            continue
        if opened := _fence_open(syntax):
            flush()
            fence = opened
            continue
        if stripped == "$$":
            flush()
            math_fence = not math_fence
            continue
        if math_fence or index in structural:
            flush()
            continue
        if started := _html_block_start(syntax):
            flush()
            html = None if started[0] == "line" else started
            continue
        if link_continuation and leading > 0:
            flush()
            continue
        link_continuation = False
        if LINK_RE.match(syntax):
            flush()
            link_continuation = True
            continue
        if ATX_RE.match(syntax) or THEMATIC_RE.fullmatch(syntax) or PROTOCOL_RE.fullmatch(stripped) or DIRECTIVE_RE.match(stripped) or _standalone_code_span(stripped):
            flush()
            continue
        if list_match := LIST_RE.match(syntax):
            list_serial += 1
            marker_indent = prefix + _indent(list_match.group("indent"))
            content_indent = marker_indent + len(list_match.group("marker")) + _indent(list_match.group("space") or "")
            active_list = (list_serial, quote_depth, content_indent)
            flush()
            content = list_match.group("text") or ""
            if content.strip():
                prose(line_number, ("list", list_serial, quote_depth), content)
            continue
        if active_list and leading >= active_list[2] + 4 or not active_list and leading >= 4:
            flush()
            continue
        if quote_depth == 0 and context and context[0] == "quote":
            line_context = context
        elif active_list:
            line_context = ("list", active_list[0], active_list[1])
        else:
            line_context = ("quote", quote_depth) if quote_depth else ("paragraph",)
        prose(line_number, line_context, body)
    flush()
    return violations
def scan_repository(repo_root: Path) -> tuple[list[tuple[str, Path]], list[Violation]]:
    paths = release_markdown_paths(repo_root)
    return paths, [violation for relative, path in paths for violation in prose_wrapping_violations(relative, path)]
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1], help="Git worktree to scan")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable result")
    return parser.parse_args(argv)
def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = args.repo_root.resolve()
    try:
        paths, violations = scan_repository(root)
    except ScanError as error:
        if args.json:
            print(json.dumps({"repo_root": str(root), "error": str(error)}, ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    payload = {"repo_root": str(root), "checked_file_count": len(paths), "violation_count": len(violations), "violations": [asdict(item) for item in violations]}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in violations:
            print(f"{item.path}:{item.start_line}-{item.end_line}: {item.message} [{item.rule}]")
        print(f"Found {len(violations)} prose paragraph(s) split across source lines." if violations else f"Checked {len(paths)} Markdown file(s); no width-only prose wrapping found.")
    return 1 if violations else 0
if __name__ == "__main__":
    raise SystemExit(main())
