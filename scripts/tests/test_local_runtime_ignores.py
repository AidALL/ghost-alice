import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class LocalRuntimeIgnoreTests(unittest.TestCase):
    def test_autopilot_runtime_state_is_ignored_by_git(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is required for ignore behavior verification")

        result = subprocess.run(
            ["git", "check-ignore", "-q", ".autopilot/approved-run.json"],
            cwd=ROOT,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            ".autopilot runtime state should not appear as repo-local work",
        )


if __name__ == "__main__":
    unittest.main()
