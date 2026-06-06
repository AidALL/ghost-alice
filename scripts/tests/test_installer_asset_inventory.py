from __future__ import annotations

import sys
import tempfile
import subprocess
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
MARKER_CLI = REPO_ROOT / "_shared" / "installer_assets_cli.py"
sys.path.insert(0, str(REPO_ROOT / "_shared"))

from installer_assets import (  # noqa: E402
    classify_global_rule_file,
    classify_skill_root,
    inventory_skill_roots,
    write_ownership_marker,
)


class InstallerAssetInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo with space" / "source"
        self.skills = self.root / "home with space" / ".agents" / "skills"
        self.repo.mkdir(parents=True)
        self.skills.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _skill(self, parent: Path, name: str, body: str = "# skill\n") -> Path:
        path = parent / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(body, encoding="utf-8")
        return path

    def test_markerless_custom_skill_is_user_owned(self) -> None:
        custom = self._skill(self.skills, "my-local-skill")

        result = classify_skill_root(custom)

        self.assertEqual(result.asset_id, "my-local-skill")
        self.assertEqual(result.ownership, "user-owned")
        self.assertEqual(result.reason, "no-ghost-alice-marker")

    def test_expected_markerless_target_is_legacy_no_baseline(self) -> None:
        existing = self._skill(self.skills, "task-router")

        result = classify_skill_root(existing, expected_asset_id="task-router")

        self.assertEqual(result.ownership, "legacy-no-baseline")
        self.assertEqual(result.reason, "expected-ghost-alice-target-without-marker")

    def test_marker_and_hash_match_is_ghost_alice_managed(self) -> None:
        managed = self._skill(self.skills, "task-router", "# task-router\n")
        write_ownership_marker(
            managed,
            platform="codex",
            asset_id="task-router",
            source_repo=str(self.repo),
            source_commit="abc123",
            install_mode="copy",
        )

        result = classify_skill_root(managed, expected_asset_id="task-router")

        self.assertEqual(result.ownership, "ghost-alice-managed")
        self.assertEqual(result.reason, "marker-and-hash-match")
        self.assertEqual(result.marker["asset_id"], "task-router")

    def test_marker_hash_mismatch_is_user_modified_managed(self) -> None:
        managed = self._skill(self.skills, "task-router", "# original\n")
        write_ownership_marker(
            managed,
            platform="codex",
            asset_id="task-router",
            source_repo=str(self.repo),
            source_commit="abc123",
            install_mode="copy",
        )
        (managed / "SKILL.md").write_text("# user edit\n", encoding="utf-8")

        result = classify_skill_root(managed, expected_asset_id="task-router")

        self.assertEqual(result.ownership, "user-modified-managed")
        self.assertEqual(result.reason, "content-hash-mismatch")

    def test_symlink_to_repo_is_managed_and_broken_symlink_is_conflict(self) -> None:
        source = self._skill(self.repo, "task-router")
        linked = self.skills / "task-router"
        linked.symlink_to(source, target_is_directory=True)

        result = classify_skill_root(
            linked,
            expected_asset_id="task-router",
            repo_root=self.repo,
        )

        self.assertEqual(result.ownership, "ghost-alice-managed")
        self.assertEqual(result.reason, "symlink-to-repo")

        broken = self.skills / "broken-skill"
        broken.symlink_to(self.repo / "missing", target_is_directory=True)

        broken_result = classify_skill_root(
            broken,
            expected_asset_id="broken-skill",
            repo_root=self.repo,
        )

        self.assertEqual(broken_result.ownership, "ownership-conflict")
        self.assertEqual(broken_result.reason, "broken-symlink")

    def test_markerless_global_rule_file_is_user_owned(self) -> None:
        agents = self.root / ".codex" / "AGENTS.md"
        agents.parent.mkdir()
        agents.write_text("# my local rules\n", encoding="utf-8")

        result = classify_global_rule_file(
            agents,
            full_file_marker="# Ghost-ALICE Codex Bootstrap",
        )

        self.assertEqual(result.asset_id, "AGENTS.md")
        self.assertEqual(result.kind, "global-rule")
        self.assertEqual(result.ownership, "user-owned")
        self.assertEqual(result.reason, "markerless-existing-rule-file")

    def test_inventory_includes_expected_absent_and_non_ascii_paths(self) -> None:
        self._skill(self.skills, "my-skill")

        results = inventory_skill_roots(
            self.skills,
            expected_asset_ids=["task-router"],
            repo_root=self.repo,
        )

        by_asset = {item.asset_id: item for item in results}
        self.assertEqual(by_asset["my-skill"].ownership, "user-owned")
        self.assertEqual(by_asset["task-router"].ownership, "absent")
        self.assertEqual(by_asset["task-router"].reason, "expected-target-absent")

    def test_marker_cli_writes_copy_markers_and_skips_symlink_targets(self) -> None:
        copied = self._skill(self.skills, "task-router")
        symlink_source = self._skill(self.repo, "linked-skill")
        symlink_dest = self.skills / "linked-skill"
        symlink_dest.symlink_to(symlink_source, target_is_directory=True)

        result = subprocess.run(
            [
                sys.executable,
                str(MARKER_CLI),
                "--platform",
                "codex",
                "--source-repo",
                str(self.repo),
                "--source-commit",
                "abc123",
                "--target",
                "task-router",
                str(copied),
                "copy",
                "--target",
                "linked-skill",
                str(symlink_dest),
                "symlink",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertTrue((copied / ".ghost-alice-install.json").exists())
        self.assertFalse((symlink_source / ".ghost-alice-install.json").exists())

        copied_result = classify_skill_root(copied, expected_asset_id="task-router")
        self.assertEqual(copied_result.ownership, "ghost-alice-managed")

    def test_installers_call_ownership_marker_writer_before_snapshot(self) -> None:
        install_sh = installer_bash_source()
        install_ps1 = installer_ps1_source()

        sh_call = '_write_ownership_markers_after_install "$SKILLS_DIR" "$copy_only" "${skills[@]}"'
        ps1_call = "Invoke-WriteOwnershipMarker -TargetPlatform $Platform"
        sh_snapshot_call = "run_logged_if_compact _run_snapshot_after_install"
        ps1_snapshot_call = "Invoke-SnapshotAfterInstall -TargetPlatform $Platform"

        self.assertIn(sh_call, install_sh)
        self.assertIn("installer_assets_cli.py", install_sh)
        self.assertLess(
            install_sh.index(sh_call),
            install_sh.index(sh_snapshot_call),
        )

        self.assertIn(ps1_call, install_ps1)
        self.assertIn("installer_assets_cli.py", install_ps1)
        self.assertLess(
            install_ps1.index(ps1_call),
            install_ps1.index(ps1_snapshot_call),
        )


if __name__ == "__main__":
    unittest.main()
