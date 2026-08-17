import os
import runpy
import subprocess
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_DOC = REPO_ROOT / "docs" / "policies" / "installer-platform-compatibility-matrix.md"
RUNNER = REPO_ROOT / "scripts" / "run_installer_compat_tests.py"
SKILL_VALIDATION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skill-validation.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SKILL_GATE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skill-gate-contract.yml"


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
        self.assertIn(".\\install.cmd --platform codex", matrix)

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
            "shared-all",
            "scripts-all",
            "skill-gate-contract",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, output)

    def test_runner_does_not_hardcode_future_python_allowlist(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8")

        self.assertIn("sys.version_info >= (3, 11)", runner)
        self.assertNotIn("3.13", runner)
        self.assertNotIn("3.14", runner)
        self.assertNotIn("python_versions = [", runner)

    def test_full_discovery_runners_report_method_progress(self) -> None:
        namespace = runpy.run_path(str(RUNNER))
        groups = {group.name: group for group in namespace["TEST_GROUPS"]}

        for name in ("shared-all", "scripts-all"):
            with self.subTest(name=name):
                self.assertIn("-v", groups[name].command)

    def test_runner_replaces_hostile_temp_env_only_for_its_child_process(self) -> None:
        namespace = runpy.run_path(str(RUNNER))
        group = namespace["TestGroup"](
            "temp-probe",
            "capture child runtime",
            (sys.executable, "-c", "pass"),
        )
        calls = []

        def run_process(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        hostile = r"C:\Users\Public\Documents\ESTsoft\CreatorTemp"
        with mock.patch.dict(
            os.environ,
            {
                "TEMP": hostile,
                "TMP": hostile,
                "TMPDIR": hostile,
                "PYTHONPYCACHEPREFIX": hostile,
            },
            clear=False,
        ), mock.patch.object(namespace["subprocess"], "run", run_process):
            parent_before = {
                key: os.environ[key]
                for key in ("TEMP", "TMP", "TMPDIR", "PYTHONPYCACHEPREFIX")
            }
            result = namespace["run"]([group])
            self.assertEqual(
                {
                    key: os.environ[key]
                    for key in ("TEMP", "TMP", "TMPDIR", "PYTHONPYCACHEPREFIX")
                },
                parent_before,
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 1)
        _, kwargs = calls[0]
        self.assertEqual(Path(kwargs["cwd"]).resolve(), REPO_ROOT.resolve())
        self.assertIn("env", kwargs)
        run_root = Path(kwargs["env"]["TMPDIR"]).parent
        self.assertTrue(run_root.is_relative_to(REPO_ROOT / ".tmp"))
        self.assertEqual(kwargs["env"]["TEMP"], kwargs["env"]["TMPDIR"])
        self.assertEqual(kwargs["env"]["TMP"], kwargs["env"]["TMPDIR"])
        self.assertEqual(
            Path(kwargs["env"]["PYTHONPYCACHEPREFIX"]).parent,
            run_root,
        )
        self.assertFalse(run_root.exists())

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

    def test_ci_unittest_batches_use_repo_local_runtime_runner(self) -> None:
        ci = CI_WORKFLOW.read_text(encoding="utf-8")
        skill_gate = SKILL_GATE_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "python3 scripts/run_installer_compat_tests.py --group shared-all",
            ci,
        )
        self.assertIn(
            "python3 scripts/run_installer_compat_tests.py --group scripts-all",
            ci,
        )
        self.assertIn(
            "python scripts/run_installer_compat_tests.py --group skill-gate-contract",
            skill_gate,
        )
        self.assertNotIn("-m unittest discover", ci)
        self.assertNotIn("-m unittest", skill_gate)
        skill_validation = SKILL_VALIDATION_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(".tmp/skill-validation-result.json", skill_validation)
        self.assertNotIn("/tmp/result.json", skill_validation)


if __name__ == "__main__":
    unittest.main()
