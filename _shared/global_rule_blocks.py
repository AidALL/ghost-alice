#!/usr/bin/env python3
"""Managed block merge helpers for global AI rule files."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


CODEX_BOOTSTRAP_MARKER = "# Ghost-ALICE Codex Bootstrap"
CODEX_MANAGED_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->"
CODEX_MANAGED_BLOCK_END = "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"
CLAUDE_BOOTSTRAP_MARKER = "# Ghost-ALICE Claude Bootstrap"
CLAUDE_MANAGED_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: claude-bootstrap -->"
CLAUDE_MANAGED_BLOCK_END = "<!-- Ghost-ALICE managed block end: claude-bootstrap -->"


class GlobalRuleBlockError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApplyResult:
    status: str
    path: Path


@dataclass(frozen=True)
class RuleBlockSpec:
    marker: str
    begin: str
    end: str
    legacy_markers: tuple[str, ...] = ()
    legacy_blocks: tuple[tuple[str, str], ...] = ()


CODEX_SPEC = RuleBlockSpec(
    marker=CODEX_BOOTSTRAP_MARKER,
    begin=CODEX_MANAGED_BLOCK_BEGIN,
    end=CODEX_MANAGED_BLOCK_END,
    legacy_markers=("# AidALL Codex Bootstrap",),
    legacy_blocks=(
        (
            "<!-- AidALL managed block begin: codex-bootstrap -->",
            "<!-- AidALL managed block end: codex-bootstrap -->",
        ),
    ),
)

CLAUDE_SPEC = RuleBlockSpec(
    marker=CLAUDE_BOOTSTRAP_MARKER,
    begin=CLAUDE_MANAGED_BLOCK_BEGIN,
    end=CLAUDE_MANAGED_BLOCK_END,
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise GlobalRuleBlockError(f"file not found: {path}") from exc
    except UnicodeDecodeError as exc:
        raise GlobalRuleBlockError(f"file is not valid UTF-8: {path}") from exc


def _write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _known_markers(spec: RuleBlockSpec) -> tuple[str, ...]:
    return (spec.marker, *spec.legacy_markers)


def _known_blocks(spec: RuleBlockSpec) -> tuple[tuple[str, str], ...]:
    return ((spec.begin, spec.end), *spec.legacy_blocks)


def _find_managed_block(text: str, spec: RuleBlockSpec) -> tuple[int, int] | None:
    for begin_token, end_token in _known_blocks(spec):
        begin = text.find(begin_token)
        end = text.find(end_token)
        if begin != -1 and end != -1 and begin < end:
            return begin, end + len(end_token)
    return None


def _normalize_leading_marker(text: str, spec: RuleBlockSpec) -> str:
    lines = text.splitlines(keepends=True)
    if not lines:
        return text
    first_line = lines[0]
    if first_line.rstrip("\n").strip() not in _known_markers(spec):
        return text
    newline = "\n" if first_line.endswith("\n") else ""
    return spec.marker + newline + "".join(lines[1:])


def _strip_marker_line(source_text: str, spec: RuleBlockSpec) -> str:
    text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    if lines and lines[0].strip() in _known_markers(spec):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def _render_managed_block(source_text: str, spec: RuleBlockSpec) -> str:
    body = _strip_marker_line(source_text, spec)
    return f"{spec.begin}\n{body.rstrip()}\n{spec.end}"


def _render_bootstrap(source_text: str, spec: RuleBlockSpec) -> str:
    return f"{spec.marker}\n{_render_managed_block(source_text, spec)}\n"


def _merge_bootstrap_text(
    existing_text: str | None,
    source_text: str,
    spec: RuleBlockSpec,
) -> tuple[str, str]:
    rendered = _render_bootstrap(source_text, spec)
    if not existing_text:
        return "updated", rendered

    existing = existing_text.replace("\r\n", "\n").replace("\r", "\n")
    managed_block = _find_managed_block(existing, spec)
    if managed_block is not None:
        begin, end = managed_block
        replacement = _render_managed_block(source_text, spec)
        prefix = _normalize_leading_marker(existing[:begin], spec)
        merged = prefix + replacement + existing[end:]
        if not merged.endswith("\n"):
            merged += "\n"
        return "updated", merged

    if existing.startswith(_known_markers(spec)):
        return "updated", rendered

    return "proposed", rendered


def _apply_bootstrap(
    source_path: Path | str,
    dest_path: Path | str,
    spec: RuleBlockSpec,
    *,
    proposed_path: Path | str | None = None,
) -> ApplyResult:
    source = Path(source_path)
    dest = Path(dest_path)
    proposed = Path(proposed_path) if proposed_path is not None else dest.with_name(dest.name + ".ghost-alice-proposed")
    source_text = _read_text(source)

    existing_text = None
    if dest.exists():
        try:
            existing_text = dest.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            status, body = "proposed", _render_bootstrap(source_text, spec)
            _write_text(proposed, body)
            return ApplyResult(status, proposed)

    status, body = _merge_bootstrap_text(existing_text, source_text, spec)
    if status == "proposed":
        _write_text(proposed, body)
        return ApplyResult(status, proposed)

    _write_text(dest, body)
    return ApplyResult(status, dest)


def _remove_marker_line(text: str, spec: RuleBlockSpec) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() in _known_markers(spec):
        return "\n".join(lines[1:]).lstrip("\n")
    return text


def _remove_bootstrap(dest_path: Path | str, spec: RuleBlockSpec) -> ApplyResult:
    dest = Path(dest_path)
    if not dest.exists():
        return ApplyResult("unchanged", dest)

    existing = _read_text(dest).replace("\r\n", "\n").replace("\r", "\n")
    managed_block = _find_managed_block(existing, spec)
    if managed_block is not None:
        begin, end = managed_block
        remaining = _remove_marker_line(existing[:begin] + existing[end:], spec).strip()
        if not remaining:
            dest.unlink()
            return ApplyResult("removed", dest)
        _write_text(dest, remaining + "\n")
        return ApplyResult("updated", dest)

    if existing.startswith(_known_markers(spec)):
        dest.unlink()
        return ApplyResult("removed", dest)

    return ApplyResult("unchanged", dest)


def render_codex_managed_block(source_text: str) -> str:
    return _render_managed_block(source_text, CODEX_SPEC)


def render_codex_bootstrap(source_text: str) -> str:
    return _render_bootstrap(source_text, CODEX_SPEC)


def merge_codex_bootstrap_text(existing_text: str | None, source_text: str) -> tuple[str, str]:
    return _merge_bootstrap_text(existing_text, source_text, CODEX_SPEC)


def apply_codex_bootstrap(
    source_path: Path | str,
    dest_path: Path | str,
    *,
    proposed_path: Path | str | None = None,
) -> ApplyResult:
    return _apply_bootstrap(
        source_path,
        dest_path,
        CODEX_SPEC,
        proposed_path=proposed_path,
    )


def remove_codex_bootstrap(dest_path: Path | str) -> ApplyResult:
    return _remove_bootstrap(dest_path, CODEX_SPEC)


def render_claude_managed_block(source_text: str) -> str:
    return _render_managed_block(source_text, CLAUDE_SPEC)


def render_claude_bootstrap(source_text: str) -> str:
    return _render_bootstrap(source_text, CLAUDE_SPEC)


def merge_claude_bootstrap_text(existing_text: str | None, source_text: str) -> tuple[str, str]:
    return _merge_bootstrap_text(existing_text, source_text, CLAUDE_SPEC)


def apply_claude_bootstrap(
    source_path: Path | str,
    dest_path: Path | str,
    *,
    proposed_path: Path | str | None = None,
) -> ApplyResult:
    return _apply_bootstrap(
        source_path,
        dest_path,
        CLAUDE_SPEC,
        proposed_path=proposed_path,
    )


def remove_claude_bootstrap(dest_path: Path | str) -> ApplyResult:
    return _remove_bootstrap(dest_path, CLAUDE_SPEC)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge Ghost-ALICE-managed global rule file blocks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    codex = subparsers.add_parser("codex-merge")
    codex.add_argument("--source", required=True, type=Path)
    codex.add_argument("--dest", required=True, type=Path)
    codex.add_argument("--proposed", type=Path, default=None)

    codex_remove = subparsers.add_parser("codex-remove")
    codex_remove.add_argument("--dest", required=True, type=Path)

    claude = subparsers.add_parser("claude-merge")
    claude.add_argument("--source", required=True, type=Path)
    claude.add_argument("--dest", required=True, type=Path)
    claude.add_argument("--proposed", type=Path, default=None)

    claude_remove = subparsers.add_parser("claude-remove")
    claude_remove.add_argument("--dest", required=True, type=Path)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "codex-merge":
            result = apply_codex_bootstrap(
                args.source,
                args.dest,
                proposed_path=args.proposed,
            )
            print(f"{result.status}:{result.path}")
        elif args.command == "codex-remove":
            result = remove_codex_bootstrap(args.dest)
            print(f"{result.status}:{result.path}")
        elif args.command == "claude-merge":
            result = apply_claude_bootstrap(
                args.source,
                args.dest,
                proposed_path=args.proposed,
            )
            print(f"{result.status}:{result.path}")
        elif args.command == "claude-remove":
            result = remove_claude_bootstrap(args.dest)
            print(f"{result.status}:{result.path}")
        else:
            raise AssertionError(args.command)
    except GlobalRuleBlockError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
