import json
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "validate_public_surfaces.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skill-validation.yml"
RUNNER = REPO_ROOT / "scripts" / "run_installer_compat_tests.py"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


class PublicSurfaceParityValidatorTest(unittest.TestCase):
    def run_validator(self, repo: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(repo), "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def load_json_output(self, result: subprocess.CompletedProcess[str]) -> dict:
        self.assertTrue(
            result.stdout.strip().startswith("{"),
            msg="validator must emit JSON in --json mode; stderr was:\n" + result.stderr,
        )
        return json.loads(result.stdout)

    def make_fixture(self, tmp: Path) -> Path:
        skills = [
            {"name": "alpha", "path": "alpha/SKILL.md", "family": "domain"},
            {"name": "beta", "path": "beta/SKILL.md", "family": "system"},
            {
                "name": "using-coding-convention",
                "path": "coding-convention/using-coding-convention/SKILL.md",
                "family": "coding-convention",
            },
        ]
        write_text(
            tmp / "skill-catalog" / "skills.json",
            json.dumps({"skills": skills}, ensure_ascii=False, indent=2),
        )
        write_text(
            tmp / "README.md",
            """
            The current `skill-catalog/skills.json` contains top-level 2 skills and 1 coding-convention sub-skill, total 3.

            Installer development and release validation:

            ```bash
            python scripts/validate_public_surfaces.py
            ```

            - top-level skills 2 (alpha, beta)
            - coding-convention family 1 sub-skill (using-coding-convention)
            """,
        )
        write_text(
            tmp / "docs" / "index.html",
            """
            <section id="whats-inside">
              <div class="stat-cell">
                <div class="stat-num">3</div>
                <div class="stat-label">Skills across 6 domains</div>
              </div>
              <div class="stat-cell">
                <div class="stat-num">1</div>
                <div class="stat-label">Coding-convention sub-skills</div>
              </div>
            </section>
            """,
        )
        for skill in skills:
            name = skill["name"]
            path = skill["path"]
            write_text(tmp / ".claude" / "commands" / f"{name}.md", f"@{path}\n\n$ARGUMENTS\n")
        return tmp

    def test_public_surface_validator_passes_current_repo(self) -> None:
        result = self.run_validator(REPO_ROOT)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = self.load_json_output(result)
        self.assertEqual(data["error_count"], 0, msg=result.stdout)

    def test_validator_accepts_english_readme_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self.make_fixture(Path(td))
            write_text(
                repo / "README.md",
                """
                The current `skill-catalog/skills.json` snapshot contains top-level 2 skills and 1 coding-convention sub-skills, total 3.

                Installer development and release validation:

                ```bash
                python3 scripts/validate_public_surfaces.py
                ```

                - top-level skills 2 (alpha, beta)
                - coding-convention family 1 sub-skills (using-coding-convention)
                """,
            )

            result = self.run_validator(repo)

        self.assertEqual(result.returncode, 0, msg=result.stdout)
        data = self.load_json_output(result)
        self.assertEqual(data["error_count"], 0, msg=result.stdout)

    def test_validator_accepts_compact_readme_entrypoint_without_live_skill_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self.make_fixture(Path(td))
            write_text(
                repo / "README.md",
                """
                # Ghost-ALICE OS

                Ghost-ALICE OS is an agent governance layer for AI work.

                ## Quick Start

                ```bash
                bash install.sh
                ```

                ## Addons

                An addon is installed from its own repository or a local path.

                ```bash
                bash install.sh --addon-source <repository-or-path>
                ```

                ## Documentation Map

                - [Skill catalog guide](./docs/reference/skills.md)

                ## Contributing And Validation

                ```bash
                python3 scripts/validate_public_surfaces.py
                ```
                """,
            )

            result = self.run_validator(repo)

        self.assertEqual(result.returncode, 0, msg=result.stdout)
        data = self.load_json_output(result)
        self.assertEqual(data["error_count"], 0, msg=result.stdout)

    def test_homepage_has_public_metadata_without_internal_badges(self) -> None:
        text = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")

        self.assertIn(
            'content="Ghost-ALICE OS makes AI agent work more inspectable by governing intent, '
            'scope, evidence, and runtime state across agent runtimes."',
            text,
        )
        self.assertNotIn("session-intent-" + "guard", text)
        for forbidden in (
            'class="gate-key">task-router',
            'class="mechanism-name">task-router',
            'class="skill-chip">task-router',
        ):
            self.assertNotIn(forbidden, text)

    def test_public_docs_do_not_render_legacy_intent_guard_label(self) -> None:
        public_paths = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "index.html",
            REPO_ROOT / "docs" / "reference" / "repository-structure.md",
            REPO_ROOT / "docs" / "ko" / "README.md",
        ]
        legacy_name = "session-intent-" + "guard"
        visible_legacy_patterns = (
            f"[{legacy_name}",
            f">{legacy_name}",
            f"{legacy_name}      ",
        )

        offenders = []
        for path in public_paths:
            text = path.read_text(encoding="utf-8")
            for pattern in visible_legacy_patterns:
                if pattern in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {pattern}")

        self.assertEqual(offenders, [])

    def test_public_docs_do_not_render_stale_design_data_residue(self) -> None:
        public_paths = [
            REPO_ROOT / "docs" / "index.html",
            REPO_ROOT / "docs" / "reference" / "repository-structure.md",
            REPO_ROOT / "docs" / "ko" / "reference" / "repository-structure.md",
        ]
        stale_patterns = ("Design Data", "design data")

        offenders = []
        for path in public_paths:
            text = path.read_text(encoding="utf-8")
            for pattern in stale_patterns:
                if pattern in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {pattern}")

        self.assertEqual(offenders, [])

    def assert_accessible_homepage(self, text: str) -> None:
        # Actor, judgment and return-path meaning requires source-backed review;
        # these checks cover addressable navigation and accessible references.
        self.assertIn('class="skip-link"', text)
        ids = re.findall(r'\bid="([^"]+)"', text)
        self.assertEqual(len(ids), len(set(ids)), "HTML IDs must be unique")
        labels = re.findall(r'aria-labelledby="([^"]+)"', text)
        self.assertTrue(labels, "The workflow figure needs an accessible name")
        for label in labels:
            self.assertTrue(set(label.split()) <= set(ids), label)
        self.assertIn('href="#main"', text)
        self.assertIn("main", ids)

    def test_homepage_has_accessible_navigation_and_figure_references(self) -> None:
        self.assert_accessible_homepage((REPO_ROOT / "index.html").read_text(encoding="utf-8"))

    def test_homepage_accessibility_rejects_dangling_or_duplicate_references(self) -> None:
        text = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
        candidates = (
            text.replace('href="#main"', 'href="#missing"'),
            text.replace('id="main"', 'id="missing"'),
            text.replace('id="flow-heading"', 'id="unrelated-description"'),
            text + '<div id="main"></div>',
        )
        for number, candidate in enumerate(candidates):
            with self.subTest(case=number), self.assertRaises(AssertionError):
                self.assert_accessible_homepage(candidate)

    def test_homepage_languages_share_navigation_and_accessible_structure(self) -> None:
        english = (REPO_ROOT / "index.html").read_text()
        korean = (REPO_ROOT / "index_ko.html").read_text()
        self.assert_accessible_homepage(english)
        self.assert_accessible_homepage(korean)
        self.assertIn('<html lang="ko">', korean)
        self.assertIn('href="./index.html"', korean)
        self.assertIn('href="./index_ko.html"', english)
        self.assertEqual(re.findall(r'\bid="([^"]+)"', english), re.findall(r'\bid="([^"]+)"', korean))
        self.assertEqual((REPO_ROOT / "docs/index_ko.html").read_bytes(), (REPO_ROOT / "index_ko.html").read_bytes())
        self.assertIn('docs/index_ko.html', (REPO_ROOT / "_config.yml").read_text())

    def test_docs_homepage_matches_root_pages_entry(self) -> None:
        self.assertEqual((REPO_ROOT / "docs" / "index.html").read_bytes(),
                         (REPO_ROOT / "index.html").read_bytes())

    def test_validator_detects_homepage_copy_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self.make_fixture(Path(td))
            text = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
            write_text(repo / "index.html", text)
            write_text(repo / "docs" / "index.html", text + "<!-- drift -->")
            result = self.run_validator(repo)
            self.assertEqual(result.returncode, 2)
            self.assertIn("homepage-copy-parity", result.stdout)

    def test_validator_rejects_broken_navigation_in_identical_homepage_copies(self) -> None:
        text = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
        normalizer = re.search(r"<script>.*?</script>", text, re.DOTALL).group(0)
        candidates = {
            "missing": text.replace(normalizer, ""),
            "wrong_base": text.replace("new URL('../', location.href)", "new URL('./', location.href)"),
            "wrong_scope": text.replace(r"/\/docs\/(?:index(?:_ko)?\.html)?$/", r"/./"),
            "comment_only": text.replace(normalizer, "<!--" + normalizer + "-->"),
        }
        for case, candidate in candidates.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                repo = self.make_fixture(Path(td))
                write_text(repo / "index.html", candidate)
                write_text(repo / "docs" / "index.html", candidate)
                (repo / "imgs").mkdir()
                (repo / "imgs" / "Ghost-ALICE_logo.png").touch()
                result = self.run_validator(repo)
                self.assertEqual(result.returncode, 2, msg=result.stdout)
                data = self.load_json_output(result)
                self.assertTrue(any(f["check"] == "homepage-base-resolution"
                                    for f in data["findings"]), msg=result.stdout)
                self.assertFalse(any(f["check"] == "homepage-copy-parity"
                                     for f in data["findings"]), msg=result.stdout)

    def test_validator_detects_repo_root_homepage_link_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self.make_fixture(Path(td))
            write_text(
                repo / "index.html",
                """
                <div class="hero-logo-lockup">
                  <img src="imgs/Ghost-ALICE_logo.png" alt="Ghost-ALICE OS">
                </div>
                <section id="routes">
                  <h2>Where to go next</h2>
                  <a href="./getting-started/installation.md">Install</a>
                  <a href="./getting-started/uninstall.md">Uninstall</a>
                  <a href="./policies/installer-platform-compatibility-matrix.md">Matrix</a>
                  <a href="./ko/README.md">Korean docs</a>
                </section>
                """,
            )

            result = self.run_validator(repo)

        self.assertEqual(result.returncode, 2, msg=result.stdout)
        data = self.load_json_output(result)
        messages = "\n".join(finding["message"] for finding in data["findings"])
        self.assertIn("index.html", messages)
        self.assertIn("repo-root-relative", messages)

    def test_validator_detects_readme_top_level_list_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self.make_fixture(Path(td))
            readme = repo / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8").replace("(alpha, beta)", "(alpha)"),
                encoding="utf-8",
            )

            result = self.run_validator(repo)

        self.assertEqual(result.returncode, 2, msg=result.stdout)
        data = self.load_json_output(result)
        messages = "\n".join(finding["message"] for finding in data["findings"])
        self.assertIn("README.md", messages)
        self.assertIn("top-level", messages)
        self.assertIn("beta", messages)

    def test_validator_detects_claude_wrapper_target_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self.make_fixture(Path(td))
            (repo / ".claude" / "commands" / "alpha.md").write_text(
                "@beta/SKILL.md\n\n$ARGUMENTS\n",
                encoding="utf-8",
            )

            result = self.run_validator(repo)

        self.assertEqual(result.returncode, 2, msg=result.stdout)
        data = self.load_json_output(result)
        messages = "\n".join(finding["message"] for finding in data["findings"])
        self.assertIn(".claude/commands/alpha.md", messages)
        self.assertIn("alpha/SKILL.md", messages)

    def test_validator_rejects_addon_command_wrapper_checked_into_core(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self.make_fixture(Path(td))
            write_text(
                repo / ".claude" / "commands" / "hwpx.md",
                "@hwpx/SKILL.md\n\n$ARGUMENTS\n",
            )

            result = self.run_validator(repo)

        self.assertEqual(result.returncode, 2, msg=result.stdout)
        data = self.load_json_output(result)
        messages = "\n".join(finding["message"] for finding in data["findings"])
        self.assertIn("addon command wrapper must not live in core", messages)
        self.assertIn(".claude/commands/hwpx.md", messages)

    def test_validator_rejects_evolution_alias_when_skill_evolution_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self.make_fixture(Path(td))
            skills_path = repo / "skill-catalog" / "skills.json"
            data = json.loads(skills_path.read_text(encoding="utf-8"))
            data["skills"].append(
                {
                    "name": "skill-evolution",
                    "path": "skill-evolution/SKILL.md",
                    "family": "governance",
                }
            )
            skills_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            readme = repo / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8")
                .replace("top-level 2 skills", "top-level 3 skills")
                .replace("- top-level skills 2", "- top-level skills 3")
                .replace("total 3", "total 4")
                .replace("(alpha, beta)", "(alpha, beta, skill-evolution)"),
                encoding="utf-8",
            )
            docs_index = repo / "docs" / "index.html"
            docs_index.write_text(
                docs_index.read_text(encoding="utf-8").replace('<div class="stat-num">3</div>', '<div class="stat-num">4</div>'),
                encoding="utf-8",
            )
            write_text(
                repo / ".claude" / "commands" / "skill-evolution.md",
                "@skill-evolution/SKILL.md\n\n$ARGUMENTS\n",
            )
            write_text(
                repo / ".claude" / "commands" / "evolution.md",
                "@skill-evolution/SKILL.md\n\n$ARGUMENTS\n",
            )

            result = self.run_validator(repo)

        self.assertEqual(result.returncode, 2, msg=result.stdout)
        data = self.load_json_output(result)
        messages = "\n".join(finding["message"] for finding in data["findings"])
        self.assertIn("use skill-evolution.md instead of local alias", messages)
        self.assertIn(".claude/commands/evolution.md", messages)

    def test_skill_validation_workflow_runs_public_surface_validator(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("python3 scripts/validate_public_surfaces.py", workflow)

    def test_installer_compat_runner_includes_public_surface_contract_group(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8")

        self.assertIn('"public-surface-contract"', runner)
        self.assertIn('"scripts.tests.test_validate_public_surfaces"', runner)


if __name__ == "__main__":
    unittest.main()
