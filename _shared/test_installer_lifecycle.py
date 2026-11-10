"""installer.lifecycle: uninstall (selected, addon, full) and repair flows.

Expected texts and decisions come from installer_lib/uninstall.sh:283-488 and installer_lib/install.sh:655-726.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gateway_testing  # noqa: E402
from context_gateway_model import LABEL  # noqa: E402
from installer import lifecycle  # noqa: E402
from installer.runner import InstallerExit, ToolResult  # noqa: E402
from installer_testing import FakeRunner, make_context, make_source, quiet, use_source  # noqa: E402


class LifecycleCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.source = make_source(root / "src")
        self.enterContext(use_source(self.source))
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_RUNTIME_SHARED_DIR", "GHOST_ALICE_GHOST_ROOT"):
            os.environ.pop(key, None)
        quiet(self.home)
        self.ghost = self.home / ".ghost-alice"
        self.ctx = make_context(self.home, "claude")
        self.ctx.skills_dir.mkdir(parents=True)
        self.patches = contextlib.ExitStack()
        self.calls: list[str] = []

    def tearDown(self):
        self.patches.close()
        self.env.stop()
        self.temp.cleanup()

    def stub(self, target: str, result=None, side_effect=None):
        name = target.rsplit(".", 1)[-1]

        def record(*args, **kwargs):
            self.calls.append(name)
            if side_effect is not None:
                return side_effect(*args, **kwargs)
            return result

        patched = self.patches.enter_context(mock.patch(target, side_effect=record))
        return patched

    def stub_removal_flow(self):
        self.stub("installer.addons.resume_pending_uninstalls")
        self.stub("installer.addons.uninstall_addon", 0)
        self.stub("installer.addons.dependents_rc", 0)
        self.stub("installer.transaction.guard_gateway_removal", 0)
        self.stub("installer.transaction.remove_installed_target", side_effect=lambda ctx, label, path, **kwargs:
                  {"action": "removed" if kwargs.get("apply_changes", True) else "would-remove"})
        self.stub("installer.hosts.remove_claude_bootstrap_if_unused", True)
        self.stub("installer.hosts.remove_codex_bootstrap_if_unused", True)
        self.stub("installer.hosts.install_hooks", True)
        self.stub("installer.lifecycle.run_full_uninstall", 0)

    @staticmethod
    def captured(fn, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn(*args, **kwargs)
        return result, out.getvalue()


class ArgumentTests(LifecycleCase):
    def test_parse_uninstall_args(self):
        # uninstall.sh:284-301
        request = lifecycle.parse_uninstall_args(["--addon", "noop", "alpha", "--force", "--addon", "rich", "fam"])
        self.assertEqual(request, lifecycle.UninstallRequest(addon_ids=["noop", "rich"], skills=["alpha", "fam"], force=True))
        self.assertEqual(lifecycle.parse_uninstall_args([]), lifecycle.UninstallRequest())
        _, out = self.captured(lambda: self.assertRaises(InstallerExit, lifecycle.parse_uninstall_args, ["alpha", "--addon"]))
        self.assertIn("[ERROR] --addon requires an addon id", out)


class UninstallTests(LifecycleCase):
    def test_addon_only_removal(self):
        # uninstall.sh:303-358
        self.stub_removal_flow()
        addons_dir = self.ghost / "addons" / "claude"
        addons_dir.mkdir(parents=True)
        (addons_dir / "known.json.removing").write_text("{}", encoding="utf-8")
        results = {"gone": 1, "known": 1, "kept": 2, "noop": 0}
        self.patches.close()
        self.patches = contextlib.ExitStack()
        self.stub_removal_flow()
        self.patches.enter_context(mock.patch("installer.addons.uninstall_addon", side_effect=lambda ctx, aid: results[aid]))
        rc, out = self.captured(lifecycle.uninstall, self.ctx, ["--addon", "noop", "--addon", "known", "--addon", "gone", "--addon", "kept"])
        self.assertEqual(rc, 1)
        self.assertIn("[WARN] Addon gone: not installed (nothing to remove)", out)
        self.assertIn("[WARN] Addon kept: uninstall left items for the human user to review", out)
        self.assertIn("[OK] 2 addon(s) removed.", out)
        self.assertEqual(self.calls.count("resume_pending_uninstalls"), 1)
        self.assertNotIn("run_full_uninstall", self.calls)
        self.assertNotIn("install_hooks", self.calls)
        rc, out = self.captured(lifecycle.uninstall, self.ctx, ["--addon", "gone"])
        self.assertEqual(rc, 1)
        self.assertIn("[INFO] No addon targets to remove.", out)

    def test_no_arguments_runs_the_full_uninstall(self):
        self.stub_removal_flow()
        self.assertEqual(lifecycle.uninstall(self.ctx, []), 0)
        self.assertEqual(self.calls[-2:], ["resume_pending_uninstalls", "run_full_uninstall"])

    def test_bare_addon_ids_are_promoted(self):
        # uninstall.sh:66-79, 303-312
        self.stub_removal_flow()
        addons_dir = self.ghost / "addons" / "claude"
        addons_dir.mkdir(parents=True)
        (addons_dir / "noop.json").write_text("{}", encoding="utf-8")
        rc, out = self.captured(lifecycle.uninstall, self.ctx, ["noop"])
        self.assertEqual(rc, 0)
        self.assertIn("[OK] 1 addon(s) removed.", out)
        self.assertNotIn("run_full_uninstall", self.calls)

    def test_selected_skill_removal_flow(self):
        # uninstall.sh:360-438
        self.stub_removal_flow()
        self.patches.enter_context(mock.patch("installer.addons.dependents_rc", side_effect=lambda ctx, s: {"alpha": 2, "one": 3}.get(s, 0)))
        removed_paths = []
        def remove(ctx, label, path, **kwargs):
            if kwargs.get("apply_changes", True):
                removed_paths.append((label, Path(path)))
                return {"action": "removed"}
            return {"action": "would-remove"}
        self.patches.enter_context(mock.patch("installer.transaction.remove_installed_target",
                                              side_effect=remove))
        self.patches.enter_context(mock.patch("installer.selection.has_managed_installs", return_value=False))
        rc, out = self.captured(lifecycle.uninstall, self.ctx, ["alpha", "fam"])
        self.assertEqual(rc, 1)
        self.assertIn("[INFO] Removing selected skills...", out)
        self.assertIn("[WARN] Skipping alpha: an installed addon depends on it (use --force to override)", out)
        self.assertIn("[WARN] Dependency check failed for one; preserving its installed target", out)
        self.assertEqual(removed_paths, [("two", self.ctx.skills_dir / "two"), ("_shared", self.ctx.skills_dir / "_shared")])
        self.assertIn("[OK] 3 item(s) removed.", out)
        self.assertEqual([c for c in self.calls if c in ("guard_gateway_removal",
                                                         "remove_claude_bootstrap_if_unused", "install_hooks")],
                         ["guard_gateway_removal", "remove_claude_bootstrap_if_unused", "install_hooks"])

    def test_force_skips_dependency_checks_and_hooks_stay_when_skills_remain(self):
        self.stub_removal_flow()
        self.patches.enter_context(mock.patch("installer.selection.has_managed_installs", return_value=True))
        self.patches.enter_context(mock.patch("installer.hosts.remove_claude_bootstrap_if_unused", return_value=False))
        rc, out = self.captured(lifecycle.uninstall, self.ctx, ["--force", "alpha"])
        self.assertEqual(rc, 0)
        self.assertNotIn("dependents_rc", self.calls)
        self.assertNotIn("install_hooks", self.calls)
        self.assertIn("[OK] 1 item(s) removed.", out)
        self.assertIn("[INFO] Managed skills remain; keeping hooks installed.", out)

    def test_gateway_guard_and_hook_failures(self):
        self.stub_removal_flow()
        self.patches.enter_context(mock.patch("installer.transaction.guard_gateway_removal", return_value=2))
        self.assertEqual(lifecycle.uninstall(self.ctx, ["alpha"]), 2)
        self.assertNotIn("install_hooks", self.calls)
        self.patches.enter_context(mock.patch("installer.transaction.guard_gateway_removal", return_value=0))
        self.patches.enter_context(mock.patch("installer.selection.has_managed_installs", return_value=False))
        self.patches.enter_context(mock.patch("installer.hosts.install_hooks", return_value=False))
        _, out = self.captured(lambda: self.assertRaises(InstallerExit, lifecycle.uninstall, self.ctx, ["alpha"]))
        self.assertIn("[ERROR] Hook removal failed. Aborting skill removal", out)

    def test_unknown_skill_is_rejected(self):
        self.stub_removal_flow()
        _, out = self.captured(lambda: self.assertRaises(InstallerExit, lifecycle.uninstall, self.ctx, ["nope"]))
        self.assertIn("[ERROR] Skill not found: nope", out)

    def test_nothing_removed_message(self):
        self.stub_removal_flow()
        self.patches.enter_context(mock.patch("installer.transaction.remove_installed_target", return_value={"action": "missing"}))
        self.patches.enter_context(mock.patch("installer.hosts.remove_claude_bootstrap_if_unused", return_value=False))
        self.patches.enter_context(mock.patch("installer.selection.has_managed_installs", return_value=True))
        rc, out = self.captured(lifecycle.uninstall, self.ctx, ["alpha"])
        self.assertEqual(rc, 0)
        self.assertIn("[INFO] No skills to remove.", out)


class FullUninstallTests(LifecycleCase):
    def test_full_uninstall_order_and_stop(self):
        # uninstall.sh:480-488
        self.stub("installer.addons.resume_pending_uninstalls")
        self.stub("installer.addons.uninstall_all_before_full", True)
        self.stub("installer.lifecycle.run_uninstall_cleanup", 0)
        rc, out = self.captured(lifecycle.run_full_uninstall, self.ctx)
        self.assertEqual(rc, 0)
        self.assertIn("[INFO] Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets.", out)
        self.assertEqual(self.calls, ["resume_pending_uninstalls", "uninstall_all_before_full", "run_uninstall_cleanup"])
        self.patches.enter_context(mock.patch("installer.addons.uninstall_all_before_full", return_value=False))
        rc, out = self.captured(lifecycle.run_full_uninstall, self.ctx)
        self.assertEqual(rc, 1)
        self.assertIn("[ERROR] Full uninstall stopped because one or more addon targets need the human user's review.", out)

    def test_run_uninstall_cleanup_arguments(self):
        # uninstall.sh:440-455
        runner = FakeRunner()
        runner.respond_to("uninstall_cleanup.py", ToolResult(1))
        with runner.patch():
            self.assertEqual(lifecycle.run_uninstall_cleanup(self.ctx), 1)
        self.assertEqual(runner.tool_calls("uninstall_cleanup.py")[0],
                         ["--platform", "claude", "--install-state-manifest", str(self.ghost / "install-state" / "claude.json"),
                          "--apply"])


class SelectedRemovalOwnershipTests(LifecycleCase):
    def setUp(self):
        super().setUp()
        self.stub("installer.transaction.migrate_record")
        self.stub("installer.addons.resume_pending_uninstalls")
        self.stub("installer.hosts.install_hooks", True)
        self.stub("installer.hosts.remove_claude_bootstrap_if_unused", False)
        self.patches.enter_context(mock.patch("installer.selection.ALL_SKILLS", ("alpha", "fam")))
        self.dependents = self.patches.enter_context(mock.patch("installer.addons.dependents_rc", return_value=0))
        self.guard = self.patches.enter_context(mock.patch("installer.transaction.guard_gateway_removal", return_value=0))
        self.state = self.ghost / "install-state" / "claude.json"

    def installed(self, name, *, linked=False, modified=False):
        from installer_assets import write_ownership_marker
        source = self.source / name if name in {"alpha", "_shared"} else self.source / "fam" / name
        path = self.ctx.skills_dir / name
        if linked:
            path.symlink_to(source)
        else:
            shutil.copytree(source, path)
            write_ownership_marker(path, platform="claude", asset_id=name,
                source_repo=str(self.source), source_commit="fixture", install_mode="copy")
            if modified:
                (path / "SKILL.md").write_text("current user change\n", encoding="utf-8")
        return path

    def inventory(self, *names):
        self.state.parent.mkdir(parents=True, exist_ok=True)
        value = dict(schema_version=1, platform="claude", source_root=str(self.source),
            targets=[dict(target_name=name, dest_path=str(self.ctx.skills_dir/name),
                          source_path=str(self.source/name if name in {"alpha", "_shared"} else self.source/'fam'/name),
                          install_mode="copy") for name in names])
        self.state.write_text(json.dumps(value), encoding="utf-8")

    def test_personal_slots_and_private_shared_survive_even_with_force(self):
        for name in ("alpha", "_shared"):
            path = self.ctx.skills_dir/name
            path.mkdir()
            (path/'personal.txt').write_text(name, encoding="utf-8")
        rc, _ = self.captured(lifecycle.uninstall, self.ctx, ["--force", "alpha"])
        self.assertEqual(rc, 1)
        for name in ("alpha", "_shared"):
            self.assertEqual((self.ctx.skills_dir/name/'personal.txt').read_text(), name)
        self.assertFalse(any(call.args[1] for call in self.guard.call_args_list))

    def test_family_checks_exact_leaves_and_preserves_dependency_or_unknown(self):
        one, two = self.installed("one"), self.installed("two")
        self.dependents.side_effect = lambda ctx, name: {"one": 2, "two": 3}.get(name, 0)
        rc, _ = self.captured(lifecycle.uninstall, self.ctx, ["fam"])
        self.assertEqual(rc, 1)
        self.assertEqual([call.args[1] for call in self.dependents.call_args_list], ["fam", "one", "two"])
        self.assertTrue(one.exists())
        self.assertTrue(two.exists())

    def test_family_dependency_preserves_all_leaves(self):
        one, two = self.installed("one"), self.installed("two")
        self.dependents.side_effect = lambda ctx, name: 2 if name == "fam" else 0
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ["fam"])[0], 1)
        self.assertEqual([call.args[1] for call in self.dependents.call_args_list].count("fam"), 1)
        self.assertTrue(one.exists())
        self.assertTrue(two.exists())

    def test_all_actual_deletions_reach_gateway_before_any_leaf_changes(self):
        alpha, shared = self.installed("alpha", linked=True), self.installed("_shared", linked=True)
        def refuse(ctx, targets):
            self.assertTrue(alpha.is_symlink())
            self.assertTrue(shared.is_symlink())
            self.assertEqual(set(targets), {alpha, shared})
            return 3
        self.guard.side_effect = refuse
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ["alpha"])[0], 3)
        self.assertTrue(alpha.is_symlink())
        self.assertTrue(shared.is_symlink())

    def test_managed_link_and_modified_copy_remove_without_losing_earlier_backup(self):
        alpha = self.installed("alpha", linked=True)
        one = self.installed("one", modified=True)
        self.installed("two")
        backup = self.ghost/'uninstall-backup'/'claude'/'one'
        backup.mkdir(parents=True)
        (backup/'SKILL.md').write_text("earlier user change\n", encoding="utf-8")
        rc, _ = self.captured(lifecycle.uninstall, self.ctx, ["alpha", "fam"])
        self.assertEqual(rc, 0)
        self.assertFalse(alpha.is_symlink())
        self.assertFalse(one.exists())
        self.assertTrue((self.source/'alpha'/'SKILL.md').exists())
        self.assertEqual((backup/'SKILL.md').read_text(), "earlier user change\n")
        copies = [path.read_text() for path in backup.parent.glob('one*/SKILL.md')]
        self.assertIn("current user change\n", copies)

    def test_successful_selected_removal_prunes_only_removed_current_targets(self):
        alpha, one = self.installed("alpha"), self.installed("one")
        self.inventory("alpha", "one")
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ["alpha"])[0], 0)
        self.assertFalse(alpha.exists())
        self.assertTrue(one.exists())
        self.assertEqual([row['target_name'] for row in json.loads(self.state.read_text())['targets']], ['one'])

    def test_other_host_reference_preserves_target_and_current_inventory(self):
        alpha = self.installed("alpha")
        self.inventory("alpha")
        other = self.state.with_name('codex.json')
        other.write_text(json.dumps(dict(platform='codex', targets=[dict(dest_path=str(alpha))])))
        original = self.state.read_bytes()
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ["alpha"])[0], 1)
        self.assertTrue(alpha.exists())
        self.assertEqual(self.state.read_bytes(), original)

    def test_target_appearing_after_absent_preview_is_not_deleted(self):
        self.inventory("alpha")
        def appeared(ctx, targets):
            self.assertEqual(targets, [])
            self.installed("alpha")
            return 0
        self.guard.side_effect = appeared
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ["alpha"])[0], 1)
        self.assertTrue((self.ctx.skills_dir/'alpha/SKILL.md').exists())
        self.assertEqual(json.loads(self.state.read_text())['targets'][0]['target_name'], 'alpha')

    def test_shared_remains_when_a_leaf_changes_after_preview(self):
        alpha, shared = self.installed("alpha"), self.installed("_shared", linked=True)
        def changed(ctx, targets):
            (alpha/'.ghost-alice-install.json').unlink()
            return 0
        self.guard.side_effect = changed
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ["alpha"])[0], 1)
        self.assertTrue(alpha.exists())
        self.assertTrue(shared.is_symlink())

    def test_private_shared_survives_removal_of_the_last_managed_leaf(self):
        self.installed("alpha")
        shared = self.ctx.skills_dir/'_shared'
        shared.mkdir()
        (shared/'personal.txt').write_text('private support', encoding='utf-8')
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ["alpha"])[0], 1)
        self.assertEqual((shared/'personal.txt').read_text(), 'private support')
        self.assertNotIn(shared, self.guard.call_args.args[1])

    def test_invalid_copy_marker_preserves_bytes_and_inventory(self):
        alpha = self.installed("alpha")
        self.inventory("alpha")
        marker = alpha/'.ghost-alice-install.json'
        data = json.loads(marker.read_text())
        data.pop('content_hashes')
        marker.write_text(json.dumps(data))
        before = (alpha/'SKILL.md').read_bytes()
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ["alpha"])[0], 1)
        self.assertEqual((alpha/'SKILL.md').read_bytes(), before)
        self.assertEqual(len(json.loads(self.state.read_text())['targets']), 1)

    def test_force_removes_managed_family_leaves_without_dependency_query(self):
        self.installed('one')
        self.installed('two')
        self.dependents.return_value = 2
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ['--force', 'fam'])[0], 0)
        self.dependents.assert_not_called()
        self.assertFalse((self.ctx.skills_dir/'one').exists())
        self.assertFalse((self.ctx.skills_dir/'two').exists())

    def test_intentionally_absent_target_leaves_current_inventory(self):
        self.inventory('alpha')
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ['alpha'])[0], 0)
        self.assertEqual(json.loads(self.state.read_text())['targets'], [])

    def test_last_managed_leaf_removes_owned_shared_and_prunes_both(self):
        alpha = self.installed('alpha', linked=True)
        shared = self.installed('_shared', linked=True)
        self.inventory('alpha', '_shared')
        self.assertEqual(self.captured(lifecycle.uninstall, self.ctx, ['alpha'])[0], 0)
        self.assertEqual(set(self.guard.call_args.args[1]), {alpha, shared})
        self.assertFalse(alpha.is_symlink())
        self.assertFalse(shared.is_symlink())
        self.assertTrue((self.source/'_shared').is_dir())
        self.assertEqual(json.loads(self.state.read_text())['targets'], [])


class RecordedInventoryRetentionTests(LifecycleCase):
    def remove_selected(self, *, old_child_present):
        import install_hooks
        from installer_assets import write_ownership_marker
        from installer import hosts

        self.stub("installer.transaction.migrate_record")
        self.stub("installer.addons.resume_pending_uninstalls")
        self.stub("installer.addons.dependents_rc", 0)
        self.stub("installer.transaction.guard_gateway_removal", 0)
        self.patches.enter_context(mock.patch("installer.selection.ALL_SKILLS", ("alpha", "fam")))
        # Keep real configuration effects while using the fixture's selected source tree.
        self.patches.enter_context(mock.patch.object(hosts, "install_hooks",
            side_effect=lambda ctx, action, platform: install_hooks.uninstall_hook(platform) in {"removed", "not_found"}))
        checkout = Path(__file__).resolve().parents[1]
        shutil.copy2(checkout / "_shared" / "global_rule_blocks.py", self.source / "_shared" / "global_rule_blocks.py")
        targets = []
        for name, source in (("alpha", self.source / "alpha"), ("_shared", self.source / "_shared"),
                             ("one", self.source / "fam" / "one")):
            path = self.ctx.skills_dir / name
            if name != "one" or old_child_present:
                shutil.copytree(source, path)
                write_ownership_marker(path, platform="claude", asset_id=name, source_repo=str(self.source),
                                       source_commit="fixture", install_mode="copy")
            targets.append(dict(target_name=name, source_path=str(source), dest_path=str(path), install_mode="copy"))
        shutil.rmtree(self.source / "fam" / "one")
        state = self.ghost / "install-state" / "claude.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(dict(schema_version=1, platform="claude", source_root=str(self.source), targets=targets)))
        bootstrap = self.home / ".claude" / "CLAUDE.md"
        bootstrap.write_text("# Ghost-ALICE Claude Bootstrap\nmanaged rules\n", encoding="utf-8")
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
            "command": "echo [session-intent-analyzer]"}]}]}}))
        result, _ = self.captured(lifecycle.uninstall, self.ctx, ["alpha"])
        self.assertEqual(result, 0)
        self.assertFalse((self.ctx.skills_dir / "alpha").exists())
        actual = dict(old_child=(self.ctx.skills_dir / "one").exists(),
                      shared=(self.ctx.skills_dir / "_shared").exists(), bootstrap=bootstrap.exists(),
                      hooks=any(json.loads(settings.read_text()).get("hooks", {}).values()))
        self.assertEqual(actual, dict.fromkeys(actual, old_child_present))
        self.assertTrue((self.source / "alpha" / "SKILL.md").is_file())
        return json.loads(state.read_text())

    def test_source_removed_installed_child_keeps_shared_hooks_and_bootstrap(self):
        state = self.remove_selected(old_child_present=True)
        self.assertEqual({row["target_name"] for row in state["targets"]}, {"one", "_shared"})

    def test_missing_recorded_child_does_not_keep_shared_hooks_or_bootstrap(self):
        state = self.remove_selected(old_child_present=False)
        self.assertEqual({row["target_name"] for row in state["targets"]}, {"one"})


class RepairTests(LifecycleCase):
    def setUp(self):
        super().setUp()
        self.stub("installer.transaction.migrate_record")

    def test_repair_migrates_earlier_records_before_its_first_publish(self):
        # Repair publishes like install, so the first command that writes the install record migrates first.
        self.stub("installer.transaction.install_shared", 0)
        self.stub("installer.transaction.link_or_copy", 0)
        self.stub("installer.addons.repair_missing", True)
        (self.source / "task-router").mkdir()
        (self.source / "task-router" / "SKILL.md").write_text("x", encoding="utf-8")
        rc, _ = self.captured(lifecycle.run_repair, self.ctx)
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls[:3], ["migrate_record", "install_shared", "link_or_copy"])

    def test_repair_reprovisions_missing_and_keeps_occupied_targets(self):
        # install.sh:677-726
        (self.source / "task-router").mkdir()
        (self.source / "task-router" / "SKILL.md").write_text("x", encoding="utf-8")
        (self.source / "coding-convention" / "writing-plans").mkdir(parents=True)
        (self.source / "coding-convention" / "writing-plans" / "SKILL.md").write_text("x", encoding="utf-8")
        (self.ctx.skills_dir / "writing-plans").mkdir()
        (self.ctx.skills_dir / "_shared").mkdir()
        self.stub("installer.transaction.install_shared", 0)
        self.stub("installer.transaction.link_or_copy", 0)
        self.stub("installer.transaction.copy_target", True)
        self.stub("installer.addons.repair_missing", True)
        clean = {"_shared": True, "writing-plans": False}
        self.patches.enter_context(mock.patch("installer.preservation.is_clean_managed_target",
                                              side_effect=lambda ctx, name, path: clean[name]))
        rc, out = self.captured(lifecycle.run_repair, self.ctx)
        self.assertEqual(rc, 0)
        self.assertIn("[INFO] Installer repair: re-provisioning missing managed targets...", out)
        self.assertIn("[OK] repair: re-provisioned task-router", out)
        self.assertIn("[WARN] repair: writing-plans is occupied and not cleanly managed; left untouched", out)
        self.assertIn("[OK] Repair complete: 1 re-provisioned, 1 left untouched", out)
        self.assertNotIn("install_shared", self.calls)
        self.assertEqual(self.calls.count("link_or_copy"), 1)
        self.assertNotIn("copy_target", self.calls)

    def test_repair_restores_missing_shared_and_uses_copies_for_codex(self):
        ctx = make_context(self.home, "codex")
        ctx.skills_dir.mkdir(parents=True)
        (self.source / "task-router").mkdir()
        (self.source / "task-router" / "SKILL.md").write_text("x", encoding="utf-8")
        self.stub("installer.transaction.install_shared", 0)
        self.stub("installer.transaction.link_or_copy", 0)
        self.stub("installer.transaction.copy_target", True)
        self.stub("installer.addons.repair_missing", True)
        rc, out = self.captured(lifecycle.run_repair, ctx)
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls.count("install_shared"), 1)
        self.assertEqual(self.calls.count("copy_target"), 1)
        self.assertNotIn("link_or_copy", self.calls)
        self.assertIn("[OK] Repair complete: 2 re-provisioned, 0 left untouched", out)

    def test_repair_propagates_a_shared_placement_failure(self):
        # A missing `_shared` that cannot be re-provisioned is a failed repair, not a repaired target.
        self.stub("installer.transaction.install_shared", 3)
        self.stub("installer.transaction.link_or_copy", 0)
        self.stub("installer.addons.repair_missing", True)
        rc, out = self.captured(lifecycle.run_repair, self.ctx)
        self.assertEqual(rc, 3)
        self.assertEqual(self.calls.count("install_shared"), 1)
        self.assertIn("[ERROR] repair: _shared could not be re-provisioned", out)
        self.assertNotIn("Repair complete", out)
        self.assertNotIn("repair_missing", self.calls)

    def test_repair_reports_the_context_gateway_like_the_install(self):
        # The same `Context gateway:` line as the install, from the same report: here a loaded service that the
        # install record does not name, which the repair neither stops nor starts.
        reg = gateway_testing.registered_home(self, self.home, service=False)
        agent = self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": [
            sys.executable, "-B", str(reg.program), "--config", str(reg.state_path)]}))
        effects = gateway_testing.FakeEffects(self.home, running=True, agent=agent)
        self.stub("installer.transaction.install_shared", 0)
        self.stub("installer.transaction.link_or_copy", 0)
        self.stub("installer.addons.repair_missing", True)
        with mock.patch.object(sys, "platform", "darwin"), \
                mock.patch("context_gateway_install._effects", return_value=effects):
            rc, out = self.captured(lifecycle.run_repair, self.ctx)
        self.assertEqual(rc, 0)
        self.assertIn("[WARN] Context gateway: a loaded service runs from the runtime without an install record entry; "
                      "register it with bash install.sh gateway --codex-dir <dir> --ledger-root <dir> --state-dir <dir> "
                      "--launch-agent <plist>, which restarts it\n", out)
        self.assertLess(out.index("Repair complete"), out.index("Context gateway:"))
        self.assertEqual(effects.service_calls(), [])

    def test_repair_failures(self):
        (self.ctx.skills_dir / "_shared").mkdir()
        self.stub("installer.transaction.install_shared", 0)
        self.stub("installer.transaction.link_or_copy", 0)
        self.stub("installer.preservation.is_clean_managed_target", False)
        self.stub("installer.addons.repair_missing", False)
        rc, out = self.captured(lifecycle.run_repair, self.ctx)
        self.assertEqual(rc, 1)
        self.assertIn("[WARN] repair: _shared is occupied and not cleanly managed; left untouched", out)
        self.assertIn("[ERROR] repair: addon target repair failed; see addon registry sidecars", out)
        (self.source / "task-router").mkdir()
        (self.source / "task-router" / "SKILL.md").write_text("x", encoding="utf-8")
        self.patches.enter_context(mock.patch("installer.transaction.link_or_copy", return_value=4))
        rc, _ = self.captured(lifecycle.run_repair, self.ctx)
        self.assertEqual(rc, 4)


if __name__ == "__main__":
    unittest.main()
