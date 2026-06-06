import subprocess
import shutil
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_DOC = REPO_ROOT / "docs" / "policies" / "installer-platform-compatibility-matrix.md"
RUNNER = REPO_ROOT / "scripts" / "run_installer_compat_tests.py"
SKILL_VALIDATION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skill-validation.yml"


class InstallerCompatMatrixTest(unittest.TestCase):
    def test_matrix_document_covers_required_shell_and_python_contracts(self) -> None:
        matrix = MATRIX_DOC.read_text(encoding="utf-8")

        for label in (
            "macOS bash 3.2",
            "modern bash",
            "zsh invocation",
            "Linux bash",
            "WSL",
            "Git Bash",
            "Windows PowerShell 5.1",
            "PowerShell 7",
            "CMD wrapper",
        ):
            with self.subTest(label=label):
                self.assertIn(label, matrix)

        self.assertIn("Python 3.11+", matrix)
        self.assertIn("no upper bound", matrix)
        self.assertIn("non-ASCII HOME", matrix)
        self.assertIn("PSScriptAnalyzer optional", matrix)
        self.assertIn("Windows native Codex hook smoke", matrix)
        self.assertIn("install.ps1 --platform codex", matrix)

    def test_matrix_document_covers_ghost_alice_fresh_clone_policy(self) -> None:
        matrix = MATRIX_DOC.read_text(encoding="utf-8")

        self.assertIn("Ghost-ALICE fresh clone install policy", matrix)
        self.assertIn("fresh `AidALL/ghost-alice` clone plus install", matrix)
        self.assertIn("does not rewrite existing remotes", matrix)
        self.assertIn("rename local checkout directories", matrix)
        self.assertIn("expose repository migration flags", matrix)
        self.assertIn("managed stale checkout path allow rules", matrix)

    def test_runner_lists_full_installer_compatibility_suite(self) -> None:
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--list"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        output = result.stdout
        for expected in (
            "merge-companion-v2",
            "installer-runtime-detection",
            "installer-encoding",
            "installer-powershell-static",
            "installer-cmd-wrapper",
            "installer-status-contract",
            "installer-transaction",
            "shared-install-hooks",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, output)

    def test_runner_does_not_hardcode_future_python_allowlist(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8")

        self.assertIn("sys.version_info >= (3, 11)", runner)
        self.assertNotIn("3.13", runner)
        self.assertNotIn("3.14", runner)
        self.assertNotIn("python_versions = [", runner)

    def test_zsh_direct_invocation_reexecs_under_bash(self) -> None:
        zsh = shutil.which("zsh")
        if not zsh:
            self.skipTest("zsh executable is required for direct invocation compatibility test")

        result = subprocess.run(
            [zsh, str(REPO_ROOT / "install.sh"), "--list"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertNotIn("BASH_SOURCE", result.stderr + result.stdout)
        self.assertIn("Available skills:", result.stdout)

    def test_install_sh_auto_detect_guards_empty_source_health_args_for_bash32(self) -> None:
        install_sh = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        auto_calls = [
            line
            for line in install_sh.splitlines()
            if 'bash "${BASH_SOURCE[0]}" --platform "$plat"' in line
            and "source_health_args" in line
        ]
        uninstall_calls = [
            line
            for line in install_sh.splitlines()
            if 'bash "${BASH_SOURCE[0]}" --platform "$plat"' in line
            and "--uninstall" in line
        ]

        self.assertEqual(auto_calls, [
            '      if bash "${BASH_SOURCE[0]}" --platform "$plat" "${source_health_args[@]+"${source_health_args[@]}"}" "${agent_visibility_args[@]+"${agent_visibility_args[@]}"}" "${verbose_args[@]+"${verbose_args[@]}"}" "${addon_args[@]+"${addon_args[@]}"}" "${ARGS[@]+"${ARGS[@]}"}"; then',
            '      bash "${BASH_SOURCE[0]}" --platform "$plat" "${source_health_args[@]+"${source_health_args[@]}"}" "${agent_visibility_args[@]+"${agent_visibility_args[@]}"}" "${verbose_args[@]+"${verbose_args[@]}"}" "${addon_args[@]+"${addon_args[@]}"}" "${ARGS[@]+"${ARGS[@]}"}" >>"$INSTALL_REPORT_LOG_FILE" 2>&1 &',
        ])
        self.assertEqual(uninstall_calls, [
            '    if ! bash "${BASH_SOURCE[0]}" --platform "$plat" --uninstall; then'
        ])

    def test_skill_validation_workflow_runs_installer_compatibility_suite(self) -> None:
        workflow = SKILL_VALIDATION_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("installer compatibility matrix", workflow)
        self.assertIn("python3 scripts/run_installer_compat_tests.py", workflow)


if __name__ == "__main__":
    unittest.main()
