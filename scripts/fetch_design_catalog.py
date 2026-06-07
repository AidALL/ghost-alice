#!/usr/bin/env python3
"""
fetch_design_catalog.py. Refresh script for design-library/catalog/.

The real DESIGN.md templates for VoltAgent/awesome-design-md are bundled in the
`templates/` directory of the `getdesign` npm package. This script downloads and
extracts that tarball, synchronizes it into `design-library/catalog/`, and
updates manifest.json plus .source-meta.json.

Usage:
    python3 scripts/fetch_design_catalog.py                   # fetch the latest version
    python3 scripts/fetch_design_catalog.py --version 0.6.0   # pin a specific version
    python3 scripts/fetch_design_catalog.py --dry-run         # show the diff without writing

Dependencies:
    Python 3.9+ standard library only: urllib, tarfile, json, shutil, hashlib.
    Requires network access to registry.npmjs.org.

Exit codes:
    0: success
    1: network or filesystem error
    2: manifest and disk mismatch detected in --check mode
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


NPM_REGISTRY = "https://registry.npmjs.org/getdesign"
PACKAGE_NAME = "getdesign"
UPSTREAM_REPO = "https://github.com/VoltAgent/awesome-design-md"

# Upstream filename -> local filename mapping.
# To avoid a CLAUDE.md auto-injection collision on case-insensitive filesystems
# (Windows and default macOS), rename claude.md to anthropic-claude.md locally.
# The "file" field in manifest.json is rewritten the same way during sync. Add
# future collisions here.
COLLISION_MAP = {
    "claude.md": "anthropic-claude.md",
}

TOC_MIN_LINES = 80
TOC_MIN_SECTION_HEADINGS = 5


def markdown_heading_rows(text: str) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    in_fence = False
    for index, line in enumerate(text.splitlines()):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = match.group(2).strip()
        if title.lower() in {"contents", "table of contents"} or title == "목차":
            continue
        rows.append((index, len(match.group(1)), title))
    return rows


def has_contents_section(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower() in {"## contents", "## table of contents"} or stripped == "## 목차":
            return True
    return False


def markdown_anchor(title: str, seen: dict[str, int]) -> str:
    text = re.sub(r"<[^>]+>", "", title).strip().lower().replace("&amp;", "and")
    chars = [ch for ch in text if ch.isalnum() or ch in {" ", "-"}]
    base = re.sub(r"\s+", "-", "".join(chars).strip())
    base = re.sub(r"-+", "-", base).strip("-")
    count = seen.get(base, 0)
    seen[base] = count + 1
    return f"{base}-{count}" if count else base


def with_contents_section(text: str) -> str:
    if has_contents_section(text):
        return text

    lines = text.splitlines()
    rows = markdown_heading_rows(text)
    section_rows = [row for row in rows if row[1] in {2, 3}]
    if len(lines) < TOC_MIN_LINES and len(section_rows) < TOC_MIN_SECTION_HEADINGS:
        return text
    if not section_rows:
        return text

    seen: dict[str, int] = {}
    toc = ["## Contents", ""]
    for _, level, title in section_rows:
        indent = "  " if level == 3 else ""
        toc.append(f"{indent}- [{title}](#{markdown_anchor(title, seen)})")
    toc.append("")

    insert_at = section_rows[0][0]
    while insert_at > 0 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    if insert_at > 0 and lines[insert_at - 1].strip() == "---":
        insert_at -= 1
        while insert_at > 0 and lines[insert_at - 1].strip() == "":
            insert_at -= 1

    rendered = "\n".join(lines[:insert_at] + toc + lines[insert_at:])
    return rendered + ("\n" if text.endswith("\n") else "")


def catalog_file_text(path: Path) -> str:
    return with_contents_section(path.read_text(encoding="utf-8"))


def local_name(upstream_name: str) -> str:
    return COLLISION_MAP.get(upstream_name, upstream_name)


def fetch_registry_metadata() -> dict:
    with urllib.request.urlopen(NPM_REGISTRY, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_version(meta: dict, requested: str | None) -> str:
    if requested:
        if requested not in meta.get("versions", {}):
            raise SystemExit(f"ERROR: version '{requested}' not found in registry")
        return requested
    latest = meta.get("dist-tags", {}).get("latest")
    if not latest:
        raise SystemExit("ERROR: latest tag is missing")
    return latest


def download_tarball(meta: dict, version: str, dest: Path) -> Path:
    version_entry = meta["versions"][version]
    tarball_url = version_entry["dist"]["tarball"]
    tarball_path = dest / f"{PACKAGE_NAME}-{version}.tgz"
    urllib.request.urlretrieve(tarball_url, tarball_path)
    return tarball_path


def extract_templates(tarball_path: Path, dest: Path) -> Path:
    with tarfile.open(tarball_path, "r:gz") as tar:
        tar.extractall(dest)
    templates_dir = dest / "package" / "templates"
    if not templates_dir.is_dir():
        raise SystemExit(f"ERROR: {templates_dir} is missing. Package structure may have changed")
    return templates_dir


def sync_catalog(templates_dir: Path, catalog_dir: Path, dry_run: bool) -> dict:
    catalog_dir.mkdir(parents=True, exist_ok=True)
    # Tuple list with upstream filenames mapped to local filenames.
    upstream_files = sorted(p.name for p in templates_dir.glob("*.md"))
    upstream_to_local = [(u, local_name(u)) for u in upstream_files]
    local_targets = sorted(l for _, l in upstream_to_local)
    existing_files = sorted(p.name for p in catalog_dir.glob("*.md"))

    to_add = sorted(set(local_targets) - set(existing_files))
    to_remove = sorted(set(existing_files) - set(local_targets))
    to_update: list[str] = []

    # Reverse lookup for upstream -> local mapping.
    local_to_upstream = {l: u for u, l in upstream_to_local}
    for local_fname in sorted(set(local_targets) & set(existing_files)):
        src = templates_dir / local_to_upstream[local_fname]
        dst = catalog_dir / local_fname
        if hash_text(catalog_file_text(src)) != hash_file(dst):
            to_update.append(local_fname)

    diff = {
        "add": to_add,
        "remove": to_remove,
        "update": to_update,
        "unchanged": sorted(
            set(local_targets) & set(existing_files) - set(to_update)
        ),
    }

    if dry_run:
        return diff

    for local_fname in to_add + to_update:
        dst = catalog_dir / local_fname
        src = templates_dir / local_to_upstream[local_fname]
        write_text_lf(dst, catalog_file_text(src))
    for local_fname in to_remove:
        (catalog_dir / local_fname).unlink()

    return diff


def sync_manifest(templates_dir: Path, library_root: Path, dry_run: bool) -> None:
    src = templates_dir / "manifest.json"
    dst = library_root / "manifest.json"
    if not src.is_file():
        raise SystemExit(f"ERROR: {src} is missing. Re-check whether the package includes manifest")
    if dry_run:
        return
    # Load upstream manifest, apply COLLISION_MAP to the file field, then store it.
    entries = json.loads(src.read_text(encoding="utf-8"))
    for entry in entries:
        orig = entry.get("file")
        if orig and orig in COLLISION_MAP:
            entry["file"] = COLLISION_MAP[orig]
    write_text_lf(dst, json.dumps(entries, ensure_ascii=False, indent=2) + "\n")


def write_source_meta(
    library_root: Path,
    version: str,
    meta: dict,
    catalog_count: int,
    dry_run: bool,
) -> None:
    version_entry = meta["versions"][version]
    source_meta = {
        "acquired_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "acquired_by": "scripts/fetch_design_catalog.py",
        "source_channel": "npm",
        "package": PACKAGE_NAME,
        "package_version": version,
        "tarball_url": version_entry["dist"]["tarball"],
        "tarball_sha": version_entry["dist"].get("shasum"),
        "upstream_repo": UPSTREAM_REPO,
        "upstream_cli_path": "cli/",
        "license": "see upstream repo LICENSE",
        "entry_count": catalog_count,
        "notes": [
            "design-md/<slug>/README.md in the awesome-design-md repository is a redirect stub to the getdesign.md landing page and does not contain the real DESIGN.md body.",
            "The real body is bundled as brand-specific .md files in the templates/ directory of the getdesign npm package.",
            "This file is generated automatically by scripts/fetch_design_catalog.py. Do not edit it manually.",
        ],
    }
    if dry_run:
        return
    write_text_lf(library_root / ".source-meta.json", json.dumps(source_meta, ensure_ascii=False, indent=2) + "\n")


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def hash_text(text: str) -> str:
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def write_text_lf(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="Package version to pin (default: latest)")
    parser.add_argument("--dry-run", action="store_true", help="Print the diff without writing")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root (default: current directory)",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    library_root = repo_root / "design-library"
    catalog_dir = library_root / "catalog"

    print(f"[1/5] Fetching registry metadata: {NPM_REGISTRY}")
    meta = fetch_registry_metadata()
    version = resolve_version(meta, args.version)
    print(f"[1/5] Version resolved: {version}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        print(f"[2/5] Downloading tarball: {PACKAGE_NAME}-{version}.tgz")
        tarball_path = download_tarball(meta, version, tmp_dir)
        print(f"[3/5] Extracting: {tmp_dir}")
        templates_dir = extract_templates(tarball_path, tmp_dir)

        print(f"[4/5] Synchronizing catalog: {catalog_dir}")
        diff = sync_catalog(templates_dir, catalog_dir, args.dry_run)
        print(f"  add: {len(diff['add'])}")
        for fname in diff["add"]:
            print(f"    + {fname}")
        print(f"  update: {len(diff['update'])}")
        for fname in diff["update"]:
            print(f"    ~ {fname}")
        print(f"  remove: {len(diff['remove'])}")
        for fname in diff["remove"]:
            print(f"    - {fname}")
        print(f"  unchanged: {len(diff['unchanged'])}")

        sync_manifest(templates_dir, library_root, args.dry_run)
        catalog_count = len([p for p in templates_dir.glob("*.md")])
        write_source_meta(library_root, version, meta, catalog_count, args.dry_run)

    if args.dry_run:
        print("[5/5] dry-run complete. No files were written.")
    else:
        print(f"[5/5] Complete. Synchronized {catalog_count} entries and updated manifest/source-meta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
