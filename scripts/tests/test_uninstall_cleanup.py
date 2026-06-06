import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from glob import glob
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
UNINSTALL_CLEANUP = REPO_ROOT / "_shared" / "uninstall_cleanup.py"
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
README = REPO_ROOT / "README.md"
UNINSTALL_DOC = REPO_ROOT / "docs" / "getting-started" / "uninstall.md"
REMOVED_BASH_FLAG = "--off" + "board"
REMOVED_BASH_CONFIRM_FLAG = REMOVED_BASH_FLAG + "-confirm"
REMOVED_BASH_REPORT_FLAG = REMOVED_BASH_FLAG + "-report"
REMOVED_PS_FLAG = "-Off" + "board"
REMOVED_PS_CONFIRM_FLAG = REMOVED_PS_FLAG + "Confirm"
REMOVED_PS_REPORT_FLAG = REMOVED_PS_FLAG + "Report"
REMOVED_DOC_STEM = "off" + "boarding"


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_managed_skill(root: Path, name: str) -> Path:
    target = root / ".agents" / "skills" / name
    target.mkdir(parents=True)
    skill_file = target / "SKILL.md"
    skill_file.write_text(f"# {name}\n", encoding="utf-8")
    marker = {
        "schema_version": 1,
        "managed_by": "Ghost-ALICE",
        "platform": "codex",
        "asset_id": name,
        "source_repo": "test-repo",
        "source_commit": "abc123",
        "installed_at": "2026-05-16T00:00:00Z",
        "install_mode": "copy",
        "content_hashes": {"SKILL.md": _hash_file(skill_file)},
        "encoding_contract": {"text": "utf-8-strict"},
    }
    (target / ".ghost-alice-install.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def _write_manifest(
    root: Path,
    targets: list[dict],
    *,
    source_root: Path | None = None,
    system_env_changes: list[dict] | None = None,
) -> Path:
    manifest = root / ".ghost-alice" / "install-state" / "codex.json"
    manifest.parent.mkdir(parents=True)
    if source_root is None:
        source_root = root / "repo"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platform": "codex",
                "installed_at": "2026-05-16T00:00:00Z",
                "source_root": source_root.as_posix(),
                "source_head": "abc123",
                "targets": targets,
                "system_env_changes": system_env_changes or [],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


class UninstallCleanupTest(unittest.TestCase):
    def run_cleanup(
        self,
        root: Path,
        manifest: Path,
        report: Path,
        *,
        confirm: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(root)
        args = [
            sys.executable,
            str(UNINSTALL_CLEANUP),
            "--platform",
            "codex",
            "--install-state-manifest",
            str(manifest),
            "--report-path",
            str(report),
        ]
        if confirm:
            args.append("--confirm")
        return subprocess.run(
            args,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_dry_run_reports_managed_targets_without_deleting_user_owned_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            user_owned = root / ".agents" / "skills" / "hwpx"
            user_owned.mkdir(parents=True)
            (user_owned / "SKILL.md").write_text("# user owned\n", encoding="utf-8")
            manifest = _write_manifest(
                root,
                [
                    {"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"},
                    {"target_name": "hwpx", "dest_path": user_owned.as_posix(), "install_mode": "copy"},
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(managed.exists())
            self.assertTrue(user_owned.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "dry-run")
            actions = {item["target_name"]: item["action"] for item in data["items"] if "target_name" in item}
            self.assertEqual(actions["task-router"], "would-remove")
            self.assertEqual(actions["hwpx"], "manual-review")

    def test_confirm_removes_only_ghost_alice_managed_targets_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            user_owned = root / ".agents" / "skills" / "hwpx"
            user_owned.mkdir(parents=True)
            (user_owned / "SKILL.md").write_text("# user owned\n", encoding="utf-8")
            manifest = _write_manifest(
                root,
                [
                    {"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"},
                    {"target_name": "hwpx", "dest_path": user_owned.as_posix(), "install_mode": "copy"},
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(managed.exists())
            self.assertTrue(user_owned.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "confirm")
            install_actions = {
                item["target_name"]: item["action"]
                for item in data["items"]
                if item.get("kind") == "install-target"
            }
            self.assertEqual(install_actions["task-router"], "removed")
            self.assertEqual(install_actions["hwpx"], "manual-review")

    def test_confirm_unlinks_manifest_symlink_without_removing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "repo" / "_shared"
            source.mkdir(parents=True)
            (source / "helper.py").write_text("# helper\n", encoding="utf-8")
            dest = root / ".agents" / "skills" / "_shared"
            dest.parent.mkdir(parents=True)
            dest.symlink_to(source, target_is_directory=True)
            manifest = _write_manifest(
                root,
                [{"target_name": "_shared", "dest_path": dest.as_posix(), "install_mode": "symlink"}],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(dest.exists() or dest.is_symlink())
            self.assertTrue(source.exists())

    def test_confirm_refuses_symlink_inside_platform_root_when_target_is_outside_source_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            source = root / "personal-source"
            source.mkdir()
            (source / "SKILL.md").write_text("# personal\n", encoding="utf-8")
            dest = root / ".agents" / "skills" / "personal-link"
            dest.parent.mkdir(parents=True)
            dest.symlink_to(source, target_is_directory=True)
            manifest = _write_manifest(
                root,
                [
                    {
                        "target_name": "personal-link",
                        "source_path": (repo / "personal-link").as_posix(),
                        "dest_path": dest.as_posix(),
                        "install_mode": "symlink",
                    }
                ],
                source_root=repo,
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(dest.is_symlink())
            self.assertTrue(source.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            item = next(item for item in data["items"] if item.get("target_name") == "personal-link")
            self.assertEqual(item["action"], "manual-review")
            self.assertEqual(item["ownership"], "user-owned")
            self.assertEqual(item["reason"], "symlink-outside-repo")

    def test_confirm_refuses_manifest_targets_outside_platform_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "repo" / "_shared"
            source.mkdir(parents=True)
            outside = root / "outside-link"
            outside.symlink_to(source, target_is_directory=True)
            manifest = _write_manifest(
                root,
                [{"target_name": "_shared", "dest_path": outside.as_posix(), "install_mode": "symlink"}],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(outside.is_symlink())
            data = json.loads(report.read_text(encoding="utf-8"))
            item = next(item for item in data["items"] if item.get("target_name") == "_shared")
            self.assertEqual(item["action"], "manual-review")
            self.assertEqual(item["reason"], "outside-allowed-roots")

    def test_confirm_checks_report_path_before_removing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            report = root / "report-as-directory"
            report.mkdir()

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(managed.exists())

    def test_confirm_removes_trace_backed_support_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            pending = root / ".ghost-alice" / "pending-merges" / "codex"
            pending.mkdir(parents=True)
            (pending / "snapshot.json").write_text('{"version": 2, "files": {}}\n', encoding="utf-8")
            (pending / "manifest.json").write_text('{"entries": []}\n', encoding="utf-8")
            events = root / ".ghost-alice" / "install-state" / "codex-events.jsonl"
            events.write_text('{"event":"copy_replace_failure"}\n', encoding="utf-8")
            feature_change = root / ".ghost-alice" / "install-state" / "codex-hook-feature-change.json"
            feature_change.write_text('{"kind":"codex_hooks_feature_flag"}\n', encoding="utf-8")
            hooks_dir = root / ".ghost-alice" / "hooks"
            hooks_dir.mkdir(parents=True)
            (hooks_dir / "ghost-alice-hook.mjs").write_text("// managed dispatcher\n", encoding="utf-8")
            rollback_dir = root / ".ghost-alice" / "install-rollbacks"
            rollback_dir.mkdir(parents=True)
            (rollback_dir / "rollback-task-router").write_text("previous copy\n", encoding="utf-8")
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(managed.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(events.exists())
            self.assertFalse(feature_change.exists())
            self.assertFalse(pending.exists())
            self.assertFalse(hooks_dir.exists())
            self.assertFalse(rollback_dir.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            support = {item["target_name"]: item for item in data["items"] if item.get("kind") == "support-artifact"}
            self.assertEqual(support["install-state-manifest"]["action"], "removed")
            self.assertEqual(support["install-state-events"]["action"], "removed")
            self.assertEqual(support["codex-hook-feature-change"]["action"], "removed")
            self.assertEqual(support["pending-merges"]["action"], "removed")
            self.assertEqual(support["hook-dispatcher-assets"]["action"], "removed")
            self.assertEqual(support["install-rollbacks"]["action"], "removed")

    def test_confirm_reports_hook_uninstall_before_target_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            data = json.loads(report.read_text(encoding="utf-8"))
            kinds = [item["kind"] for item in data["items"]]
            self.assertLess(kinds.index("hook-config"), kinds.index("install-target"))

    def test_confirm_restores_source_repo_hook_path_when_manifest_records_it(self) -> None:
        git = shutil.which("git")
        if not git:
            self.skipTest("git executable is required for source repo hook rollback test")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run([git, "init"], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run([git, "config", "--local", "core.hooksPath", "hooks"], cwd=repo, check=True)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                source_root=repo,
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            current = subprocess.run(
                [git, "config", "--local", "--get", "core.hooksPath"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(current.returncode, 0)
            data = json.loads(report.read_text(encoding="utf-8"))
            source_hook = next(item for item in data["items"] if item.get("kind") == "source-repo-hook-config")
            self.assertEqual(source_hook["action"], "removed-source-repo-hook-path")

    def test_confirm_restores_previous_source_repo_hook_path_from_change_record(self) -> None:
        git = shutil.which("git")
        if not git:
            self.skipTest("git executable is required for source repo hook rollback test")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run([git, "init"], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run([git, "config", "--local", "core.hooksPath", "hooks"], cwd=repo, check=True)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                source_root=repo,
                system_env_changes=[
                    {
                        "kind": "source_repo_hook_path",
                        "repo_root": repo.as_posix(),
                        "before_present": True,
                        "before": "custom-hooks",
                        "after": "hooks",
                        "applied_at": "2026-05-16T00:00:00Z",
                    }
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            current = subprocess.run(
                [git, "config", "--local", "--get", "core.hooksPath"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(current.returncode, 0)
            self.assertEqual(current.stdout.strip(), "custom-hooks")
            data = json.loads(report.read_text(encoding="utf-8"))
            source_hook = next(item for item in data["items"] if item.get("kind") == "source-repo-hook-config")
            self.assertEqual(source_hook["action"], "restored-source-repo-hook-path")
            self.assertEqual(source_hook["restored_to"], "custom-hooks")

    def test_confirm_restores_codex_hooks_feature_flag_from_change_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_dir = root / ".codex"
            codex_dir.mkdir()
            config_toml = codex_dir / "config.toml"
            config_toml.write_text("[features]\nhooks = true\nmulti_agent = true\n", encoding="utf-8")
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                system_env_changes=[
                    {
                        "kind": "codex_hooks_feature_flag",
                        "path": config_toml.as_posix(),
                        "before_state": "false",
                        "after_state": "true",
                        "applied_at": "2026-06-04T00:00:00Z",
                    }
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            content = config_toml.read_text(encoding="utf-8")
            self.assertIn("hooks = false", content)
            self.assertIn("multi_agent = true", content)
            data = json.loads(report.read_text(encoding="utf-8"))
            feature = next(item for item in data["items"] if item.get("kind") == "codex-hook-feature-config")
            self.assertEqual(feature["action"], "restored-codex-hooks-feature-flag")
            self.assertEqual(feature["restored_to"], "false")

    def test_codex_hooks_feature_flag_rollback_preserves_user_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_dir = root / ".codex"
            codex_dir.mkdir()
            config_toml = codex_dir / "config.toml"
            config_toml.write_text("[features]\nhooks = true\n", encoding="utf-8")
            (codex_dir / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [
                                {
                                    "matcher": "",
                                    "hooks": [{"type": "command", "command": "echo user-hook"}],
                                }
                            ]
                        }
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
                system_env_changes=[
                    {
                        "kind": "codex_hooks_feature_flag",
                        "path": config_toml.as_posix(),
                        "before_state": "false",
                        "after_state": "true",
                    }
                ],
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("hooks = true", config_toml.read_text(encoding="utf-8"))
            data = json.loads(report.read_text(encoding="utf-8"))
            feature = next(item for item in data["items"] if item.get("kind") == "codex-hook-feature-config")
            self.assertEqual(feature["action"], "manual-review")
            self.assertEqual(feature["reason"], "codex-hooks-feature-required-by-non-ghost-hooks")

    def test_confirm_defers_target_still_referenced_by_another_platform_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            other_manifest = root / ".ghost-alice" / "install-state" / "claude.json"
            other_manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "platform": "claude",
                        "source_root": (root / "repo").as_posix(),
                        "targets": [
                            {"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}
                        ],
                        "system_env_changes": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            report = root / "uninstall-report.json"

            result = self.run_cleanup(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(managed.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            item = next(item for item in data["items"] if item.get("target_name") == "task-router")
            self.assertEqual(item["action"], "manual-review")
            self.assertEqual(item["reason"], "shared-with-other-platform")
            self.assertEqual(item["shared_platforms"], ["claude"])

    def test_public_uninstall_surface_has_no_removed_entrypoints(self) -> None:
        sh = installer_bash_source()
        ps1 = installer_ps1_source()
        readme = README.read_text(encoding="utf-8")
        legacy_doc = REPO_ROOT / "docs" / f"{REMOVED_DOC_STEM}.md"

        for body in (sh, ps1, readme):
            with self.subTest(surface=body[:20]):
                self.assertNotIn(REMOVED_BASH_FLAG, body)
                self.assertNotIn(REMOVED_BASH_CONFIRM_FLAG, body)
                self.assertNotIn(REMOVED_BASH_REPORT_FLAG, body)
                self.assertNotIn(REMOVED_PS_FLAG, body)
                self.assertNotIn(REMOVED_PS_CONFIRM_FLAG, body)
                self.assertNotIn(REMOVED_PS_REPORT_FLAG, body)
                self.assertNotIn(REMOVED_DOC_STEM, body.lower())
                self.assertNotIn("Advanced uninstall preview", body)

        self.assertIn("Full uninstall", sh)
        self.assertIn("_shared/uninstall_cleanup.py", sh)
        self.assertIn("Full uninstall", ps1)
        self.assertIn("_shared/uninstall_cleanup.py", ps1)
        self.assertIn("bash install.sh --uninstall", readme)
        self.assertIn(".\\install.ps1 -Uninstall", readme)
        self.assertFalse(legacy_doc.exists())
        self.assertTrue(UNINSTALL_DOC.exists())

    def test_bash_removed_preview_flags_are_rejected_as_unknown_options(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash executable is required for install.sh argument test")

        cases = (
            [REMOVED_BASH_FLAG],
            [REMOVED_BASH_CONFIRM_FLAG],
            [REMOVED_BASH_REPORT_FLAG, "report.json"],
            ["--confirm"],
        )
        for args in cases:
            with self.subTest(args=args):
                result = subprocess.run(
                    [bash, str(INSTALL_SH), *args],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unknown option", result.stdout + result.stderr)

    def test_powershell_removed_preview_flags_are_rejected_as_unknown_arguments(self) -> None:
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            self.skipTest("PowerShell executable is required for install.ps1 argument test")

        cases = (
            [REMOVED_PS_FLAG],
            [REMOVED_PS_CONFIRM_FLAG],
            [REMOVED_PS_REPORT_FLAG, "report.json"],
        )
        for args in cases:
            with self.subTest(args=args), tempfile.TemporaryDirectory() as temp_dir:
                env = os.environ.copy()
                env["HOME"] = temp_dir
                env["USERPROFILE"] = temp_dir
                env["GHOST_ALICE_LANG"] = "en"

                result = subprocess.run(
                    [pwsh, "-NoLogo", "-NoProfile", "-File", str(INSTALL_PS1), *args],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unknown argument", result.stdout + result.stderr)

    def test_bash_uninstall_without_skill_args_runs_full_confirm_cleanup(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash executable is required for install.sh uninstall test")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".codex").mkdir()
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            env = os.environ.copy()
            env["HOME"] = str(root)
            env["GHOST_ALICE_LANG"] = "en"

            result = subprocess.run(
                [bash, str(INSTALL_SH), "--platform", "codex", "--uninstall"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(managed.exists())
            self.assertFalse(manifest.exists())
            reports = glob(str(root / ".ghost-alice" / "uninstall-reports" / "codex-confirm-*.json"))
            self.assertEqual(len(reports), 1)
            data = json.loads(Path(reports[0]).read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "confirm")

    def test_bash_plain_uninstall_detects_install_state_manifest_platforms(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash executable is required for install.sh uninstall test")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = _write_managed_skill(root, "task-router")
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": managed.as_posix(), "install_mode": "copy"}],
            )
            env = os.environ.copy()
            env["HOME"] = str(root)
            env["GHOST_ALICE_LANG"] = "en"

            result = subprocess.run(
                [bash, str(INSTALL_SH), "--uninstall"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(managed.exists())
            self.assertFalse(manifest.exists())


class UninstallUserModifiedQuarantineTest(unittest.TestCase):
    def _run(self, root, manifest, report, *, confirm=False, purge_modified=False):
        env = os.environ.copy()
        env["HOME"] = str(root)
        args = [
            sys.executable,
            str(UNINSTALL_CLEANUP),
            "--platform",
            "codex",
            "--install-state-manifest",
            str(manifest),
            "--report-path",
            str(report),
        ]
        if confirm:
            args.append("--confirm")
        if purge_modified:
            args.append("--purge-modified")
        return subprocess.run(
            args,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _user_modified_skill(self, root: Path, name: str = "task-router") -> Path:
        target = _write_managed_skill(root, name)
        # Mutate content so the recorded hash no longer matches -> USER_MODIFIED_MANAGED.
        (target / "SKILL.md").write_text("# task-router\nUSER EDIT KEEP ME\n", encoding="utf-8")
        return target

    def _actions(self, report: Path) -> dict:
        data = json.loads(report.read_text(encoding="utf-8"))
        return {i["target_name"]: i["action"] for i in data["items"] if "target_name" in i}

    def test_confirm_quarantines_user_modified_before_remove(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = self._user_modified_skill(root)
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": target.as_posix(), "install_mode": "copy"}],
            )
            report = root / "report.json"

            result = self._run(root, manifest, report, confirm=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            backup = root / ".ghost-alice" / "uninstall-backup" / "codex" / "task-router" / "SKILL.md"
            self.assertTrue(backup.exists(), "user-modified skill must be backed up before removal")
            self.assertIn("USER EDIT KEEP ME", backup.read_text(encoding="utf-8"))
            self.assertFalse(target.exists(), "footprint removed after backup")
            self.assertEqual(self._actions(report)["task-router"], "quarantined-removed")

    def test_dry_run_user_modified_would_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = self._user_modified_skill(root)
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": target.as_posix(), "install_mode": "copy"}],
            )
            report = root / "report.json"

            result = self._run(root, manifest, report)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(target.exists())
            self.assertEqual(self._actions(report)["task-router"], "would-quarantine-remove")

    def test_purge_modified_removes_user_modified_without_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = self._user_modified_skill(root)
            manifest = _write_manifest(
                root,
                [{"target_name": "task-router", "dest_path": target.as_posix(), "install_mode": "copy"}],
            )
            report = root / "report.json"

            result = self._run(root, manifest, report, confirm=True, purge_modified=True)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse(target.exists())
            backup = root / ".ghost-alice" / "uninstall-backup" / "codex" / "task-router"
            self.assertFalse(backup.exists(), "--purge-modified must not leave a backup")
            self.assertEqual(self._actions(report)["task-router"], "removed")


if __name__ == "__main__":
    unittest.main()
