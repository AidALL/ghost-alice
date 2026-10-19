"""installer.lifecycle: uninstall (selected, addon, full) and repair flows.

Expected texts and decisions come from installer_lib/uninstall.sh:283-488 and installer_lib/install.sh:655-726.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import lifecycle  # noqa: E402
from installer.runner import InstallerExit, ToolResult  # noqa: E402
from installer_testing import FakeRunner, make_context, make_source, quiet  # noqa: E402


class LifecycleCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.source = make_source(root / "src")
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_RUNTIME_SHARED_DIR", "GHOST_ALICE_GHOST_ROOT"):
            os.environ.pop(key, None)
        quiet(self.home)
        self.ghost = self.home / ".ghost-alice"
        self.ctx = make_context(self.source, self.home, "claude")
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
        self.stub("installer.transaction.remove_installed_target", True)
        self.stub("installer.transaction.cleanup_shared_if_unused", 0)
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
        self.assertIn("[WARN] Addon kept: uninstall left items for manual review", out)
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
        self.patches.enter_context(mock.patch("installer.addons.dependents_rc", side_effect=lambda ctx, s: {"alpha": 2, "fam": 3}.get(s, 0)))
        removed_paths = []
        self.patches.enter_context(mock.patch("installer.transaction.remove_installed_target",
                                              side_effect=lambda label, path: removed_paths.append((label, Path(path))) or True))
        self.patches.enter_context(mock.patch("installer.selection.has_managed_installs", return_value=False))
        rc, out = self.captured(lifecycle.uninstall, self.ctx, ["alpha", "fam"])
        self.assertEqual(rc, 0)
        self.assertIn("[INFO] Removing selected skills...", out)
        self.assertIn("[WARN] Skipping alpha: an installed addon depends on it (use --force to override)", out)
        self.assertIn("[WARN] Dependency check failed for fam; proceeding with removal", out)
        self.assertEqual(removed_paths, [("one", self.ctx.skills_dir / "one"), ("two", self.ctx.skills_dir / "two")])
        self.assertIn("[OK] 4 item(s) removed.", out)
        self.assertEqual([c for c in self.calls if c in ("guard_gateway_removal", "cleanup_shared_if_unused",
                                                         "remove_claude_bootstrap_if_unused", "install_hooks")],
                         ["guard_gateway_removal", "cleanup_shared_if_unused", "remove_claude_bootstrap_if_unused", "install_hooks"])

    def test_force_skips_dependency_checks_and_hooks_stay_when_skills_remain(self):
        self.stub_removal_flow()
        self.patches.enter_context(mock.patch("installer.selection.has_managed_installs", return_value=True))
        self.patches.enter_context(mock.patch("installer.transaction.cleanup_shared_if_unused", return_value=1))
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
        self.assertNotIn("remove_installed_target", self.calls)
        self.patches.enter_context(mock.patch("installer.transaction.guard_gateway_removal", return_value=0))
        self.patches.enter_context(mock.patch("installer.selection.has_managed_installs", return_value=False))
        self.patches.enter_context(mock.patch("installer.hosts.install_hooks", return_value=False))
        _, out = self.captured(lambda: self.assertRaises(InstallerExit, lifecycle.uninstall, self.ctx, ["alpha"]))
        self.assertIn("[ERROR] Hook removal failed. Aborting skill removal", out)
        self.patches.enter_context(mock.patch("installer.transaction.cleanup_shared_if_unused", return_value=3))
        self.assertEqual(lifecycle.uninstall(self.ctx, ["alpha"]), 3)

    def test_unknown_skill_is_rejected(self):
        self.stub_removal_flow()
        _, out = self.captured(lambda: self.assertRaises(InstallerExit, lifecycle.uninstall, self.ctx, ["nope"]))
        self.assertIn("[ERROR] Skill not found: nope", out)

    def test_nothing_removed_message(self):
        self.stub_removal_flow()
        self.patches.enter_context(mock.patch("installer.transaction.remove_installed_target", return_value=False))
        self.patches.enter_context(mock.patch("installer.transaction.cleanup_shared_if_unused", return_value=1))
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
        self.assertIn("[ERROR] Full uninstall stopped because one or more addon targets need manual review.", out)

    def test_run_uninstall_cleanup_arguments(self):
        # uninstall.sh:440-455
        runner = FakeRunner()
        runner.respond_to("uninstall_cleanup.py", ToolResult(1))
        with runner.patch():
            self.assertEqual(lifecycle.run_uninstall_cleanup(self.ctx), 1)
        self.assertEqual(runner.tool_calls("uninstall_cleanup.py")[0],
                         ["--platform", "claude", "--install-state-manifest", str(self.ghost / "install-state" / "claude.json"),
                          "--confirm"])


class RepairTests(LifecycleCase):
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
        ctx = make_context(self.source, self.home, "codex")
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
