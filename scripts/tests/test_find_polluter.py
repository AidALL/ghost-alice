"""Exercise artifact detection through the real Bash helper and a disposable runner."""

from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "coding-convention/systematic-debugging/references/find-polluter.sh"
)


def run_polluter(tmp_path, candidates, pattern, expected_test="", npm_exit=0):
    project = tmp_path / "project"
    project.mkdir()
    for name in candidates:
        candidate = project / name
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("// disposable test candidate\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text(
        '#!/bin/sh\n'
        'printf "%s\\0" "$@" >> "$CALLS"\n'
        'if [ "$#" -eq 2 ] && [ "$1" = test ] && [ "$2" = "$EXPECTED_TEST" ]; then\n'
        '  printf "created by %s\\n" "$2" > "$POLLUTION_TARGET"\n'
        'fi\n'
        'exit "$NPM_EXIT"\n',
        encoding="utf-8",
    )
    npm.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    artifact = project / "artifact"
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT), "artifact", pattern],
        cwd=project,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "LC_ALL": "C",
            "EXPECTED_TEST": expected_test,
            "POLLUTION_TARGET": str(artifact),
            "CALLS": str(calls),
            "NPM_EXIT": str(npm_exit),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    arguments = calls.read_bytes().split(b"\0")[:-1] if calls.exists() else []
    return result, artifact, arguments


class FindPolluterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_no_candidates_reports_zero_without_invoking_runner(self):
        result, artifact, arguments = run_polluter(self.root, [], "src/**/*.test.ts")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Found 0 test files", result.stdout)
        self.assertFalse(artifact.exists())
        self.assertEqual(arguments, [])

    def test_single_candidate_keeps_exact_path_and_detects_artifact(self):
        for pattern in ("src/**/*.test.ts", "./src/**/*.test.ts"):
            for filename in ("one.test.ts", "space name.test.ts", "line\nbreak.test.ts"):
                with self.subTest(pattern=pattern, filename=filename), tempfile.TemporaryDirectory(dir=self.root) as directory:
                    candidate = f"src/nested/{filename}"
                    result, artifact, arguments = run_polluter(
                        Path(directory), [candidate], pattern, expected_test=f"./{candidate}", npm_exit=7
                    )

                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("Found 1 test files", result.stdout)
                    self.assertIn("FOUND POLLUTER!", result.stdout)
                    self.assertEqual(artifact.read_text(encoding="utf-8"), f"created by ./{candidate}\n")
                    self.assertEqual(arguments, [b"test", f"./{candidate}".encode()])

    def test_runner_failure_without_artifact_does_not_claim_tests_passed(self):
        result, artifact, arguments = run_polluter(
            self.root, ["src/one.test.ts"], "./src/*.test.ts", npm_exit=7
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No new target artifact observed", result.stdout)
        self.assertIn("does not establish that tests passed", result.stdout)
        self.assertFalse(artifact.exists())
        self.assertEqual(arguments, [b"test", b"./src/one.test.ts"])

    def test_candidates_run_in_order_and_stop_after_artifact(self):
        result, artifact, arguments = run_polluter(
            self.root,
            ["src/c.test.ts", "src/a.test.ts", "src/b.test.ts"],
            "src/*.test.ts",
            expected_test="./src/b.test.ts",
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Found 3 test files", result.stdout)
        self.assertEqual(artifact.read_text(encoding="utf-8"), "created by ./src/b.test.ts\n")
        self.assertEqual(arguments, [b"test", b"./src/a.test.ts", b"test", b"./src/b.test.ts"])
