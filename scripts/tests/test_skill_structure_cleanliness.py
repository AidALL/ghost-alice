import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOTS = (
    "adversarial-verification",
    "agent-security-scan",
    "boundary-contract",
    "coding-convention",
    "compact-handoff",
    "jailbreak-detector",
    "merge-companion",
    "necessity-gate",
    "session-intent-analyzer",
    "skill-evolution",
    "task-router",
)


class SkillStructureCleanlinessTest(unittest.TestCase):
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
