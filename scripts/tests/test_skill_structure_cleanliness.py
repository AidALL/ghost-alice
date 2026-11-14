import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import validate_skills


SKILL_ROOTS = (
    "adversarial-verification",
    "agent-security-scan",
    "boundary-contract",
    "coding-convention",
    "compact-handoff",
    "merge-companion",
    "necessity-gate",
    "session-intent-analyzer",
    "skill-evolution",
    "task-router",
)


class SkillStructureCleanlinessTest(unittest.TestCase):
    def tomli_fixture(self, root):
        source = ROOT / '_shared/codex_config_toml'
        shutil.copytree(source, root / '_shared/codex_config_toml', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        skill = root / 'agent-security-scan'
        shutil.copytree(source, skill / 'scripts/codex_config_toml', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(ROOT / 'agent-security-scan/SKILL.md', skill / 'SKILL.md')
        shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', root / 'THIRD_PARTY_NOTICES.md')
        return skill

    def test_only_the_exact_unmodified_tomli_vendor_uses_upstream_dependency_docs(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill = self.tomli_fixture(Path(temporary))
            outside = skill / 'scripts/other/_vendor/tomli/undocumented.py'
            outside.parent.mkdir(parents=True)
            outside.write_text('print("project script")\n')
            first_party = skill / 'scripts/codex_config_toml/project.py'
            first_party.write_text('print("project script")\n')
            issues = []
            validate_skills.validate_phase4_references(skill / 'SKILL.md', issues)
            warnings = [issue.message for issue in issues if issue.rule == '4-3']
            self.assertEqual(len(warnings), 2, warnings)
            self.assertTrue(any('other/_vendor/tomli/undocumented.py' in message for message in warnings))
            self.assertTrue(any('codex_config_toml/project.py' in message for message in warnings))

    def test_missing_license_or_modified_vendor_fails_the_integrity_check(self):
        for change in ('license', 'parser', 'notice', 'provenance'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                skill = self.tomli_fixture(root)
                package = skill / 'scripts/codex_config_toml'
                if change == 'license':
                    (package / '_vendor/tomli/LICENSE').unlink()
                elif change == 'parser':
                    with (package / '_vendor/tomli/_parser.py').open('a') as handle:
                        handle.write('\n# modified vendor\n')
                elif change == 'notice':
                    (root / 'THIRD_PARTY_NOTICES.md').write_text('Missing bundled-code notice.\n')
                else:
                    (package / 'README.md').unlink()
                issues = []
                validate_skills.validate_phase4_references(skill / 'SKILL.md', issues)
                self.assertTrue(any(issue.rule == 'vendor-integrity' and issue.severity == 'ERROR' for issue in issues),
                                [issue.to_dict() for issue in issues])

    def test_validate_skills_has_no_structure_warnings(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_skills.py", "--json"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        payload = json.loads(result.stdout)
        issues = payload.get("issues", [])
        self.assertEqual(payload.get("error_count"), 0, msg=json.dumps(issues, ensure_ascii=False, indent=2))
        self.assertEqual(payload.get("warning_count"), 0, msg=json.dumps(issues, ensure_ascii=False, indent=2))

    def test_no_marker_or_option_lowers_an_error_finding(self) -> None:
        # An empty root has ERROR findings.
        with tempfile.TemporaryDirectory() as empty_root:
            marked = subprocess.run(
                [sys.executable, "scripts/validate_skills.py", "--repo-root", empty_root, "--json"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(marked.returncode, 2, msg=marked.stderr + marked.stdout)
        self.assertGreater(json.loads(marked.stdout)["error_count"], 0)
        rejected = subprocess.run(
            [sys.executable, "scripts/validate_skills.py", "--escape-hatch"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("unrecognized arguments: --escape-hatch", rejected.stderr)

    def test_skill_bodies_do_not_keep_non_operational_history_or_personal_traces(self) -> None:
        forbidden = {
            "## A Real Example from a Session": "session-specific example belongs in references or tests, not SKILL.md",
            "## Real-World Impact": "impact marketing is not operational guidance",
            "## Real Impact": "session statistics are not reusable guidance",
            "Based on a debugging session": "dated session notes are not reusable guidance",
            "Debugging session statistics": "numeric impact claims do not belong in core SKILL.md",
            "/Users/jesse": "personal absolute paths do not belong in examples",
            "Jesse's": "personal naming does not belong in reusable skill guidance",
            "Strange things are afoot": "jokes are not operational review guidance",
            "persist the durable lesson to memory": "report-only skill guidance must not imply automatic memory promotion",
        }

        skill_files: list[Path] = []
        for root in SKILL_ROOTS:
            skill_files.extend((ROOT / root).rglob("SKILL.md"))

        violations: list[str] = []
        for skill_file in sorted(skill_files):
            text = skill_file.read_text(encoding="utf-8")
            for needle, reason in forbidden.items():
                if needle in text:
                    violations.append(f"{skill_file.relative_to(ROOT)}: {needle!r} - {reason}")

        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
