#!/usr/bin/env python3
"""Preflight encoding and structure guard for semantic installer assets."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tomllib
from pathlib import Path
from typing import Iterable, Sequence


UTF8_BOM = b"\xef\xbb\xbf"

SEMANTIC_SUFFIXES = {
    ".bat",
    ".cmd",
    ".json",
    ".js",
    ".mjs",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".yaml",
    ".yml",
}

SEMANTIC_NAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "SKILL.md",
    "hooks.json",
    "config.toml",
}

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".worktrees",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "worktrees",
}


@dataclasses.dataclass(frozen=True)
class EncodingIssue:
    path: Path
    code: str
    message: str
    byte_offset: int | None = None
    line: int | None = None
    column: int | None = None

    def format(self, *, root: Path | None = None) -> str:
        display_path = self.path
        if root is not None:
            try:
                display_path = self.path.relative_to(root)
            except ValueError:
                display_path = self.path

        location = ""
        if self.byte_offset is not None:
            location = f" byte={self.byte_offset}"
        elif self.line is not None:
            location = f" line={self.line}"
            if self.column is not None:
                location += f" column={self.column}"
        return f"{display_path}: {self.code}{location}: {self.message}"


def is_semantic_path(path: Path) -> bool:
    return path.name in SEMANTIC_NAMES or path.suffix.lower() in SEMANTIC_SUFFIXES


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def iter_semantic_paths(root: Path, *, exclude_roots: Sequence[Path | str] = ()) -> Iterable[Path]:
    root = root.resolve()
    resolved_excludes = [Path(exclude).resolve() for exclude in exclude_roots]
    for path in root.rglob("*"):
        resolved_path = path.resolve()
        if any(_is_relative_to(resolved_path, excluded) for excluded in resolved_excludes):
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if not path.is_file():
            continue
        if is_semantic_path(path):
            yield path


def _decode_utf8(path: Path, raw: bytes) -> tuple[str | None, EncodingIssue | None]:
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError as exc:
        return None, EncodingIssue(
            path=path,
            code="invalid-utf8",
            message=(
                "expected UTF-8 or UTF-8 with BOM. Re-save this semantic asset as UTF-8 "
                "before installing."
            ),
            byte_offset=exc.start,
        )


def _frontmatter_issue(path: Path, text: str) -> EncodingIssue | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return EncodingIssue(
            path=path,
            code="missing-frontmatter",
            message="SKILL.md must start with YAML frontmatter delimited by ---.",
            line=1,
        )

    close_idx = None
    for idx, line in enumerate(lines[1:], start=2):
        if line.strip() == "---":
            close_idx = idx
            break
    if close_idx is None:
        return EncodingIssue(
            path=path,
            code="invalid-frontmatter",
            message="SKILL.md frontmatter must close with a second --- delimiter.",
            line=1,
        )

    frontmatter = lines[1 : close_idx - 1]
    keys = {line.split(":", 1)[0].strip() for line in frontmatter if ":" in line}
    missing = sorted({"name", "description"} - keys)
    if missing:
        return EncodingIssue(
            path=path,
            code="invalid-frontmatter",
            message=f"SKILL.md frontmatter is missing required key(s): {', '.join(missing)}.",
            line=1,
        )
    return None


def _json_issue(path: Path, text: str) -> EncodingIssue | None:
    try:
        json.loads(text)
        return None
    except json.JSONDecodeError as exc:
        return EncodingIssue(
            path=path,
            code="invalid-json",
            message=f"JSON parse failed: {exc.msg}.",
            line=exc.lineno,
            column=exc.colno,
        )


def _toml_issue(path: Path, text: str) -> EncodingIssue | None:
    try:
        tomllib.loads(text)
        return None
    except tomllib.TOMLDecodeError as exc:
        return EncodingIssue(
            path=path,
            code="invalid-toml",
            message=f"TOML parse failed: {exc}.",
        )


def validate_path(path: Path) -> list[EncodingIssue]:
    path = Path(path)
    issues: list[EncodingIssue] = []
    if not path.exists():
        return [
            EncodingIssue(
                path=path,
                code="missing-file",
                message="semantic asset path does not exist.",
            )
        ]
    if not path.is_file():
        return []
    if not is_semantic_path(path):
        return []

    raw = path.read_bytes()
    if path.suffix.lower() == ".ps1" and not raw.startswith(UTF8_BOM):
        issues.append(
            EncodingIssue(
                path=path,
                code="missing-utf8-bom",
                message="PowerShell scripts must keep a UTF-8 BOM for Windows PowerShell 5.1.",
                byte_offset=0,
            )
        )

    text, decode_issue = _decode_utf8(path, raw)
    if decode_issue is not None:
        issues.append(decode_issue)
        return issues
    assert text is not None

    if path.name == "SKILL.md":
        issue = _frontmatter_issue(path, text)
        if issue is not None:
            issues.append(issue)

    suffix = path.suffix.lower()
    if suffix == ".json":
        issue = _json_issue(path, text)
        if issue is not None:
            issues.append(issue)
    elif suffix == ".toml":
        issue = _toml_issue(path, text)
        if issue is not None:
            issues.append(issue)

    return issues


def validate_paths(paths: Sequence[Path | str]) -> list[EncodingIssue]:
    issues: list[EncodingIssue] = []
    for path in paths:
        issues.extend(validate_path(Path(path)))
    return issues


def validate_repo(root: Path | str, *, exclude_roots: Sequence[Path | str] = ()) -> list[EncodingIssue]:
    root_path = Path(root)
    return validate_paths(list(iter_semantic_paths(root_path, exclude_roots=exclude_roots)))


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate installer semantic asset encodings.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo-root", type=Path, help="Repository root to scan recursively.")
    group.add_argument("--paths", nargs="+", type=Path, help="Specific files to validate.")
    parser.add_argument("--exclude-root", action="append", type=Path, default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    root = args.repo_root.resolve() if args.repo_root else None
    issues = validate_repo(root, exclude_roots=args.exclude_root) if root is not None else validate_paths(args.paths)
    if not issues:
        return 0

    for issue in issues:
        print(issue.format(root=root), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
