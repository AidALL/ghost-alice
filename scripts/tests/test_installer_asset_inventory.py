from __future__ import annotations

import json
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER_CLI = REPO_ROOT / "_shared" / "installer_assets_cli.py"
sys.path.insert(0, str(REPO_ROOT / "_shared"))

from installer_assets import (  # noqa: E402
    classify_global_rule_file,
    classify_skill_root,
    inventory_skill_roots,
    write_ownership_marker,
)


def _make_directory_link_or_skip(testcase: unittest.TestCase, source: Path, link: Path) -> str:
    try:
        link.symlink_to(source, target_is_directory=True)
        return "symlink"
    except OSError as exc:
        if sys.platform != "win32":
            testcase.skipTest(f"directory symlink unavailable: {exc}")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            testcase.skipTest(f"directory link unavailable: {result.stderr or result.stdout}")
        return "junction"


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

    def test_markerless_custom_skill_is_unclaimed(self) -> None:
        custom = self._skill(self.skills, "my-local-skill")

        result = classify_skill_root(custom)

        self.assertEqual(result.asset_id, "my-local-skill")
        self.assertEqual(result.ownership, "unclaimed")
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

    def test_marker_hash_mismatch_is_managed_with_external_change(self) -> None:
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

        self.assertEqual(result.ownership, "externally-modified-managed")
        self.assertEqual(result.reason, "content-hash-mismatch")

    def test_malformed_marker_baseline_never_certifies_clean_ownership(self) -> None:
        managed = self._skill(self.skills, "task-router", "user bytes\n")
        marker = write_ownership_marker(managed, platform="codex", asset_id="task-router",
            source_repo=str(self.repo), source_commit="abc123", install_mode="copy")
        original = json.loads(marker.read_text(encoding="utf-8"))
        for baseline in (None, [], "invalid", {"SKILL.md": None}, {"SKILL.md": "not-a-digest"}):
            with self.subTest(baseline=baseline):
                marker.write_text(json.dumps({**original, "content_hashes": baseline}), encoding="utf-8")
                result = classify_skill_root(managed, expected_asset_id="task-router")
                self.assertEqual(result.ownership, "ownership-conflict")
        original.pop("content_hashes")
        marker.write_text(json.dumps(original), encoding="utf-8")
        self.assertEqual(classify_skill_root(managed, expected_asset_id="task-router").ownership,
                         "ownership-conflict")
        self.assertEqual((managed / "SKILL.md").read_text(encoding="utf-8"), "user bytes\n")

    def test_symlink_to_repo_is_managed_and_broken_symlink_is_conflict(self) -> None:
        source = self._skill(self.repo, "task-router")
        linked = self.skills / "task-router"
        link_kind = _make_directory_link_or_skip(self, source, linked)

        result = classify_skill_root(
            linked,
            expected_asset_id="task-router",
            repo_root=self.repo,
        )

        self.assertEqual(result.ownership, "ghost-alice-managed")
        self.assertEqual(result.reason, f"{link_kind}-to-repo")

        if link_kind != "symlink":
            return
        broken = self.skills / "broken-skill"
        broken.symlink_to(self.repo / "missing", target_is_directory=True)

        broken_result = classify_skill_root(
            broken,
            expected_asset_id="broken-skill",
            repo_root=self.repo,
        )

        self.assertEqual(broken_result.ownership, "ownership-conflict")
        self.assertEqual(broken_result.reason, "broken-symlink")

    def test_markerless_global_rule_file_is_unmanaged(self) -> None:
        agents = self.root / ".codex" / "AGENTS.md"
        agents.parent.mkdir()
        agents.write_text("# my local rules\n", encoding="utf-8")

        result = classify_global_rule_file(
            agents,
            full_file_marker="# Ghost-ALICE Codex Bootstrap",
        )

        self.assertEqual(result.asset_id, "AGENTS.md")
        self.assertEqual(result.kind, "global-rule")
        self.assertEqual(result.ownership, "unclaimed")
        self.assertEqual(result.reason, "markerless-existing-rule-file")

    def test_inventory_includes_expected_absent_and_non_ascii_paths(self) -> None:
        self._skill(self.skills, "my-skill")

        results = inventory_skill_roots(
            self.skills,
            expected_asset_ids=["task-router"],
            repo_root=self.repo,
        )

        by_asset = {item.asset_id: item for item in results}
        self.assertEqual(by_asset["my-skill"].ownership, "unclaimed")
        self.assertEqual(by_asset["task-router"].ownership, "absent")
        self.assertEqual(by_asset["task-router"].reason, "expected-target-absent")

    def test_retired_marker_write_cli_rejects_without_creating_files(self) -> None:
        copied = self._skill(self.skills, "task-router")
        symlink_source = self._skill(self.repo, "linked-skill")
        symlink_dest = self.skills / "linked-skill"
        _make_directory_link_or_skip(self, symlink_source, symlink_dest)

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

        self.assertNotEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse((copied / ".ghost-alice-install.json").exists())
        self.assertFalse((symlink_source / ".ghost-alice-install.json").exists())

    def test_classify_clean_cli_preserves_installer_preservation_exit_contract(self):
        import contextlib
        import io
        import os
        from unittest import mock
        import install_destination
        import uninstall_cleanup
        from installer import preservation, runner, selection, transaction
        from installer_testing import destination_fixture, installer_env, make_context, quiet

        copied = self._skill(self.skills, 'task-router')
        write_ownership_marker(copied, platform='codex', asset_id='task-router', source_repo=str(self.repo),
                               source_commit='abc', install_mode='copy')
        command = [sys.executable, str(MARKER_CLI), 'classify-clean', '--asset-id', 'task-router',
                   '--path', str(copied), '--repo-root', str(self.repo)]
        clean = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        (copied / 'SKILL.md').write_text('external edit')
        modified = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(modified.returncode, 1, modified.stderr)
        self.assertIn('externally-modified', modified.stdout)

        for operation in ('full', 'selected', 'preflight', 'cleanup-pending'):
            for material in ('private-git', 'nested-link', 'generated-cache'):
                with self.subTest(operation=operation, material=material):
                    home = self.root/operation/material
                    ghost = home/'.ghost-alice'
                    target = self._skill(home/'.agents/skills', 'task-router')
                    write_ownership_marker(target, platform='codex', asset_id='task-router',
                        source_repo=str(REPO_ROOT), source_commit='fixture', install_mode='copy')
                    outside = home/'outside-original.bin'
                    private_bytes = b'private-only-ownership-a59f\x00\xff'
                    outside.write_bytes(private_bytes)
                    relative = {'private-git': '.git/user-only.bin',
                                'nested-link': '__pycache__/personal-link',
                                'generated-cache': '__pycache__/fixture.pyc'}[material]
                    personal = target/relative
                    personal.parent.mkdir()
                    if material == 'nested-link':
                        try:
                            personal.symlink_to(outside)
                        except OSError as error:
                            self.skipTest(f'symlink capability unavailable: {error}')
                    else:
                        personal.write_bytes(private_bytes if material == 'private-git' else b'generated cache')
                    state = ghost/'install-state/codex.json'
                    state.parent.mkdir(parents=True)
                    state.write_text(json.dumps(dict(platform='codex', source_root=str(REPO_ROOT), targets=[dict(
                        target_name='task-router', source_path=str(REPO_ROOT/'task-router'),
                        dest_path=str(target), install_mode='copy')])))
                    binding = destination_fixture(home, platform_dir=str(home/'.codex'),
                        skills_dir=str(target.parent), ghost_root=str(ghost), core_source_root=str(REPO_ROOT))
                    with mock.patch.dict(os.environ, installer_env(home), clear=True), \
                            install_destination.use(binding), contextlib.redirect_stdout(io.StringIO()):
                        quiet(home)
                        ctx = make_context(home, 'codex')
                        if operation == 'full':
                            report = home/'uninstall-report.json'
                            code = uninstall_cleanup.main(['--platform', 'codex', '--apply',
                                '--install-state-manifest', str(state), '--report-path', str(report)])
                            self.assertEqual(code, 0, report.read_text())
                            result = next(item for item in json.loads(report.read_text())['items']
                                          if item.get('kind') == 'install-target')
                        elif operation == 'selected':
                            result = transaction.remove_installed_target(ctx, 'task-router', target,
                                source_path=REPO_ROOT/'task-router')
                        elif operation == 'preflight':
                            collected = selection.Collected([selection.Target('task-router', REPO_ROOT/'task-router')], [], 1, 0)
                            self.assertTrue(preservation.preflight(ctx, collected))
                        else:
                            pending = ghost/'pending-merges/codex'
                            prepared = runner.run_tool('merge-companion/scripts/quarantine_legacy_cli.py',
                                '--target', str(target), '--target-name', 'task-router', '--pending', str(pending),
                                '--manifest', str(pending/'manifest.json'), '--platform', 'codex', capture=True)
                            self.assertTrue(prepared.ok, prepared.stdout or prepared.stderr)
                            # Quarantine moves the old copy; cleanup runs after a managed replacement exists.
                            self._skill(target.parent, 'task-router')
                            write_ownership_marker(target, platform='codex', asset_id='task-router',
                                source_repo=str(REPO_ROOT), source_commit='replacement', install_mode='copy')
                            self.assertEqual(preservation.cleanup_pending(ctx), 0)
                    if operation in {'full', 'selected'}:
                        self.assertFalse(target.exists())
                        self.assertEqual(result['action'], 'removed' if material == 'generated-cache' else 'quarantined-removed')
                    recovery = list(ghost.rglob(personal.name))
                    if material == 'generated-cache':
                        self.assertEqual(recovery, [])
                    else:
                        self.assertEqual(len(recovery), 1, recovery)
                        if material == 'nested-link':
                            self.assertTrue(recovery[0].is_symlink())
                            self.assertEqual(os.readlink(recovery[0]), str(outside))
                        else:
                            self.assertEqual(recovery[0].read_bytes(), private_bytes)
                    self.assertEqual(outside.read_bytes(), private_bytes)

if __name__ == "__main__":
    unittest.main()
