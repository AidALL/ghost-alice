import json
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

    def test_validator_accepts_floor_first_homepage_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self.make_fixture(Path(td))
            write_text(
                repo / "docs" / "index.html",
                """
                <section id="whats-inside">
                  <div class="stat-cell">
                    <div class="stat-num">1</div>
                    <div class="stat-label">Core philosophy. Quality floor first</div>
                  </div>
                  <div class="stat-cell">
                    <div class="stat-num">8</div>
                    <div class="stat-label">Operating loop steps</div>
                  </div>
                  <div class="stat-cell">
                    <div class="stat-num">4</div>
                    <div class="stat-label">Verification layers</div>
                  </div>
                  <div class="inside-card-title">Quality floor as root rule</div>
                  <div class="inside-card-desc">Agent authority is a consequence of verified state, not forward momentum.</div>
                </section>
                """,
            )

            result = self.run_validator(repo)

        self.assertEqual(result.returncode, 0, msg=result.stdout)
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

                ## Official Addons

                Official addons are maintained as separate repositories and installed from the
                Ghost-ALICE core checkout with a short alias.

                ```bash
                bash install.sh --addon autopilot
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

    def test_homepage_foregrounds_intent_analyzer_not_internal_router(self) -> None:
        text = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")

        self.assertIn(
            'content="Ghost-ALICE OS makes AI agent work more inspectable by governing intent, '
            'scope, evidence, and runtime state across agent runtimes."',
            text,
        )
        self.assertIn("session-intent-analyzer", text)
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

    def test_homepage_gate_flow_diagram_has_compact_layout_contract(self) -> None:
        text = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")

        self.assertIn('class="gate-flow-diagram"', text)
        for marker in (
            "session-intent-analyzer",
            "governance consumers",
            "boundary-contract",
            "tool-checkpoint",
            "execution surface",
            "completion-check",
            "io-trace",
        ):
            self.assertIn(marker, text)
        for css_marker in (
            ".gate-flow-grid",
            ".gate-flow-cell",
            "grid-template-columns: repeat(4, minmax(0, 1fr))",
            "padding: 12px 14px",
            "overflow-wrap: anywhere",
            "@media (max-width: 900px)",
            "@media (max-width: 560px)",
        ):
            self.assertIn(css_marker, text)

    def test_homepage_separates_gate_map_from_lifecycle_support(self) -> None:
        text = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Gate interaction map", text)
        self.assertIn('class="support-grid"', text)
        self.assertNotIn(":hover", text)
        self.assertNotIn(".support-card:hover", text)
        self.assertIn(
            "This is the inventory layer: core governance checkpoints first, then workflow "
            "and domain packs that run on top.",
            text,
        )
        self.assertNotIn('class="mechanism-grid"', text)
        self.assertNotIn('class="support-note"', text)
        self.assertNotIn("The map is the live gate path.", text)

        for marker in (
            "intent delta",
            "derived gate judgments",
            "scope reopen point",
            "Dynamic focus",
            "micro",
            "meso",
            "macro",
            "meta",
            "claim-evidence-map",
            "support handoff",
        ):
            self.assertIn(marker, text)

        support_section = text.split('class="support-section"', 1)[1].split(
            "<!-- ── Grows with your team ── -->", 1
        )[0]
        for marker in (
            "Lifecycle support",
            "io-trace audit trail",
            "skill-evolution candidates",
            "pending-merge review",
            "adversarial verification",
            "jailbreak drift check",
        ):
            self.assertIn(marker, support_section)
        for duplicated_gate_card in (
            'class="support-name">session-intent-analyzer',
            'class="support-name">governance consumers',
            'class="support-name">boundary-contract',
            'class="support-name">tool-checkpoint',
            'class="support-name">verification-before-completion',
        ):
            self.assertNotIn(duplicated_gate_card, support_section)

    def test_docs_homepage_uses_docs_root_relative_links(self) -> None:
        text = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")

        self.assertTrue((REPO_ROOT / "docs" / "imgs" / "Ghost-ALICE_logo.png").is_file())
        self.assertIn('src="imgs/Ghost-ALICE_logo.png"', text)
        for marker in (
            'href="./getting-started/installation.html"',
            'href="./getting-started/uninstall.html"',
            'href="./policies/installer-platform-compatibility-matrix.html"',
            'href="./ko/README.html"',
        ):
            self.assertIn(marker, text)
        for stale_marker in ('href="./docs/', 'href="./README_ko.md"'):
            self.assertNotIn(stale_marker, text)

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
