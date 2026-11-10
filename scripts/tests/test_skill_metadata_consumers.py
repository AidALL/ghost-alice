"""Frontmatter boundaries and catalog failures through production consumers."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_catalog import build_skill_entry  # noqa: E402
import validate_skills as skills  # noqa: E402


class FrontmatterConsumerTest(unittest.TestCase):
    def test_compatibility_length_warning_uses_scalar_or_list_logical_text(self) -> None:
        for size in (500, 501):
            text = "x" * size
            observations = []
            for compatibility in (text, [text]):
                with self.subTest(size=size, compatibility_type=type(compatibility).__name__):
                    issues = []
                    skills.validate_phase1_frontmatter(Path("alpha/SKILL.md"),
                        {"name": "alpha", "description": "Description", "compatibility": compatibility}, issues)
                    observations.append([issue.to_dict() for issue in issues])
                    self.assertEqual([issue.severity for issue in issues], ["WARNING"] if size > 500 else [])
            self.assertEqual(observations[0], observations[1])

    def test_catalog_and_body_consumers_share_the_closing_boundary(self) -> None:
        body = "\n# Alpha\n\nAn English body without placeholders.\n"
        for closing in ("---", "--- ", "  ---"):
            with self.subTest(closing=closing), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                path = repo / "alpha" / "SKILL.md"
                path.parent.mkdir()
                path.write_text(
                    "---\nname: alpha\ndescription: 메타데이터\n  TODO\n"
                    "compatibility:\n  - \"Python 3.11+\"\n" + closing + "\n" + body,
                    encoding="utf-8",
                )
                text = path.read_text(encoding="utf-8")
                entry = build_skill_entry(path, repo)
                self.assertEqual(entry["description"], "메타데이터 TODO")
                self.assertEqual(entry["compatibility"], ["Python 3.11+"])
                self.assertEqual(skills._extract_body(text), body)
                issues = []
                skills.validate_phase2_body(path, text, issues)
                skills.validate_phase5_korean_tone(path, {}, text, issues)
                self.assertEqual(issues, [])

    def test_body_boundary_does_not_require_valid_metadata_values(self) -> None:
        text = "---\n# Unsupported metadata syntax\n--- \nBody only.\n"
        self.assertEqual(skills._extract_body(text), "Body only.\n")

    def test_closing_delimiter_at_eof_leaves_an_empty_body(self) -> None:
        self.assertEqual(skills._extract_body("---\nname: alpha\n---"), "")

    def test_catalog_builder_rejects_lists_for_scalar_skill_fields(self) -> None:
        for field in ("name", "description"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                path = repo / "alpha" / "SKILL.md"
                path.parent.mkdir()
                metadata = {"name": "alpha", "description": "Alpha description"}
                metadata[field] = "\n  - alpha"
                path.write_text(
                    "---\n" + "\n".join(f"{key}: {value}" for key, value in metadata.items()) + "\n---\n# Alpha\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, rf"{field}.*string"):
                    build_skill_entry(path, repo)


class CatalogConsumerTest(unittest.TestCase):
    def run_validator(self, script: str, repo: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / script), "--repo-root", str(repo), "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertTrue(result.stdout.startswith("{"), result.stderr + result.stdout)
        return result, json.loads(result.stdout)

    def write_catalog(self, repo: Path, text: str) -> None:
        catalog_dir = repo / "skill-catalog"
        catalog_dir.mkdir(exist_ok=True)
        (catalog_dir / "skills.json").write_text(text, encoding="utf-8")
        (catalog_dir / "schema.json").write_text(
            (REPO_ROOT / "skill-catalog" / "schema.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def test_malformed_catalogs_produce_typed_findings_and_json(self) -> None:
        cases = {
            "invalid-json": ("{", "parse"),
            "non-object": ("[]", "shape"),
            "missing-skills": ("{}", "shape"),
            "non-list-skills": ('{"skills": {}}', "shape"),
            "null-row": ('{"skills": [null]}', "shape"),
            "missing-name": ('{"skills": [{}]}', "shape"),
            "non-string-name": ('{"skills": [{"name": []}]}', "shape"),
        }
        for script in ("validate_public_surfaces.py", "validate_entrypoints.py", "validate_skills.py"):
            for case, (text, kind) in cases.items():
                with self.subTest(script=script, case=case), tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    self.write_catalog(repo, text)
                    result, payload = self.run_validator(script, repo)
                    self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
                    findings = payload.get("findings", payload.get("issues", []))
                    if script == "validate_skills.py":
                        self.assertTrue(any(item["skill"] == "<catalog>" for item in findings), findings)
                    else:
                        self.assertTrue(
                            any(item["area"] == "catalog" and item["check"] == kind for item in findings),
                            findings,
                        )
                    if script == "validate_public_surfaces.py":
                        self.assertEqual(payload["checked"]["command_wrappers"], "skipped-no-catalog")

    def test_missing_catalog_remains_distinct_from_a_valid_empty_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            result, payload = self.run_validator("validate_public_surfaces.py", repo)
            self.assertEqual(result.returncode, 2)
            self.assertTrue(any(item["check"] == "exist" for item in payload["findings"]))
            self.assertEqual(payload["checked"]["command_wrappers"], "skipped-no-catalog")

            self.write_catalog(repo, '{"skills": []}')
            (repo / "README.md").write_text(
                "## Quick Start\n\n## Addons\n\nbash install.sh --addon-source example\n\n"
                "## Documentation Map\n\n## Contributing And Validation\n\n"
                "python3 scripts/validate_public_surfaces.py\n",
                encoding="utf-8",
            )
            (repo / ".claude" / "commands").mkdir(parents=True)
            result, payload = self.run_validator("validate_public_surfaces.py", repo)
            self.assertEqual(result.returncode, 0, payload)
            self.assertEqual(payload["checked"]["skill_count"], 0)
            self.assertEqual(payload["checked"]["command_wrappers"], "checked")
            _, payload = self.run_validator("validate_entrypoints.py", repo)
            catalog_findings = [item for item in payload["findings"] if item["area"] == "catalog"]
            self.assertEqual([item["check"] for item in catalog_findings], ["entry", "entry"])

    def test_catalog_rebuild_failure_preserves_accumulated_skill_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = repo / "broken" / "SKILL.md"
            path.parent.mkdir()
            path.write_text("---\nname: broken\ndescription: Missing closing delimiter\n", encoding="utf-8")
            self.write_catalog(repo, '{"skills": []}')
            result, payload = self.run_validator("validate_skills.py", repo)
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            self.assertTrue(any(item["skill"] == "broken" and item["rule"] == "parse" for item in payload["issues"]))
            self.assertTrue(any(item["skill"] == "<catalog>" and item["rule"] == "sync" for item in payload["issues"]))

    def test_invalid_fields_preserve_json_and_independent_findings(self) -> None:
        cases = {
            "relation-null": ("alpha", "Alpha description", None, "calls_relation"),
            "relation-missing-type": ("alpha", "Alpha description", [{}], "calls_relation[0].type"),
            "list-name": ("\n  - alpha", "Alpha description", [], "name"),
            "list-description": ("alpha", "\n  - description", [], "description"),
            "relation-non-object": ("alpha", "Alpha description", [None], "calls_relation[0]"),
            "relation-list-type": ("alpha", "Alpha description", [{"type": [], "target": "beta"}], "calls_relation[0].type"),
            "relation-list-target": ("alpha", "Alpha description", [{"type": "hard", "target": []}], "calls_relation[0].target"),
            "relation-list-group": ("alpha", "Alpha description", [{"type": "union", "target": "beta", "union_group": []}], "calls_relation[0].union_group"),
        }
        for case, (name, description, relations, field) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                path = repo / "alpha" / "SKILL.md"
                path.parent.mkdir()
                path.write_text(
                    f"---\nname: {name}\ndescription: {description}\n---\n# Alpha\n\nTODO\n",
                    encoding="utf-8",
                )
                self.write_catalog(repo, json.dumps({"skills": [
                    {"name": "alpha", "calls_relation": relations},
                    {"name": "beta", "calls_relation": [{"type": "hard", "target": "missing-skill"}]},
                ]}))
                result, payload = self.run_validator("validate_skills.py", repo)
                self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
                errors = [item for item in payload["issues"] if item["severity"] == "ERROR"]
                self.assertTrue(any(item["skill"] == "alpha" and field in item["message"] for item in errors), errors)
                self.assertTrue(any(item["skill"] == "alpha" and item["rule"] == "2-4" for item in errors), errors)
                self.assertTrue(any(item["skill"] == "beta" and item["rule"] == "calls.target" for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
