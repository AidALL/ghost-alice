import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "fetch_design_catalog.py"
SPEC = importlib.util.spec_from_file_location("fetch_design_catalog", SCRIPT)
assert SPEC and SPEC.loader
fetch_design_catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_design_catalog)


class FetchDesignCatalogTests(unittest.TestCase):
    def test_sync_catalog_adds_contents_and_keeps_dry_run_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            templates = root / "templates"
            catalog = root / "catalog"
            templates.mkdir()
            catalog.mkdir()
            body = [
                "# Design System Inspired By Example",
                "",
                "Intro.",
                "",
            ]
            for index in range(1, 6):
                body.extend([f"## {index}. Section {index}", "", "Body.", ""])
            (templates / "example.md").write_text("\n".join(body), encoding="utf-8")

            diff = fetch_design_catalog.sync_catalog(templates, catalog, dry_run=False)
            written = (catalog / "example.md").read_text(encoding="utf-8")

            self.assertEqual(diff["add"], ["example.md"])
            self.assertIn("## Contents", written)
            self.assertIn("- [1. Section 1](#1-section-1)", written)
            self.assertIn("- [5. Section 5](#5-section-5)", written)

            dry_run_diff = fetch_design_catalog.sync_catalog(templates, catalog, dry_run=True)
            self.assertEqual(dry_run_diff["update"], [])
            self.assertEqual(dry_run_diff["unchanged"], ["example.md"])

    def test_sync_catalog_leaves_short_simple_catalog_without_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            templates = root / "templates"
            catalog = root / "catalog"
            templates.mkdir()
            catalog.mkdir()
            (templates / "simple.md").write_text(
                "# Simple\n\n## One\n\nBody.\n\n## Two\n\nBody.\n",
                encoding="utf-8",
            )

            fetch_design_catalog.sync_catalog(templates, catalog, dry_run=False)
            written = (catalog / "simple.md").read_text(encoding="utf-8")

            self.assertNotIn("## Contents", written)


if __name__ == "__main__":
    unittest.main()
