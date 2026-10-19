"""installer.selection: skill targets, families, validation, deprecated cleanup, collisions, --list.

Expected texts come from installer_lib/targets.sh, installer_lib/install.sh:728-764, 849-878
and installer_lib/uninstall.sh:40-48, 81-108.
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
from installer import cli  # noqa: E402
from installer import report  # noqa: E402
from installer import runner  # noqa: E402
from installer import selection  # noqa: E402
from installer.selection import Target  # noqa: E402
from installer_testing import use_source  # noqa: E402


def make_source(root: Path) -> Path:
    (root / "_shared").mkdir(parents=True)
    (root / "_shared" / "marker.py").write_text("", encoding="utf-8")
    (root / "alpha").mkdir()
    (root / "alpha" / "SKILL.md").write_text('---\nname: alpha\ndescription: "Alpha does one thing."\n---\n', encoding="utf-8")
    for sub in ("one", "two"):
        (root / "fam" / sub).mkdir(parents=True)
        (root / "fam" / sub / "SKILL.md").write_text(f"---\nname: {sub}\ndescription: {sub} skill\n---\n", encoding="utf-8")
    (root / "fam" / "notes").mkdir()
    (root / "skill-catalog").mkdir()
    (root / "skill-catalog" / "session-gates.json").write_text("{}", encoding="utf-8")
    return root


class SelectionCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = make_source(self.root / "src")
        self.enterContext(use_source(self.source))
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_GHOST_ROOT"):
            os.environ.pop(key, None)
        report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=self.home)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def context(self, platform: str = "claude", **options) -> cli.Context:
        opts = cli.Options(platform=platform, **options)
        skills_dir = self.home / ".claude" / "skills" if platform == "claude" else self.home / ".agents" / "skills"
        return cli.Context(options=opts, platform=platform, skills_dir=skills_dir,
                           addon_sources=list(opts.addon_sources))


class TargetTests(SelectionCase):
    def test_skill_lists(self):
        # install.sh:76-92
        self.assertEqual(selection.ALL_SKILLS, ("adversarial-verification", "boundary-contract", "coding-convention",
                                                "skill-evolution", "agent-security-scan", "merge-companion",
                                                "necessity-gate", "session-intent-analyzer", "compact-handoff",
                                                "task-router"))
        self.assertEqual(selection.DEPRECATED_INSTALLED_SKILLS, ("harness-security-scan", "session-intent-guard"))

    def test_expand_single_family_and_missing(self):
        # targets.sh:5-26
        self.assertEqual(selection.expand_skill_targets("alpha"), [Target("alpha", self.source / "alpha")])
        self.assertEqual(selection.expand_skill_targets("fam"),
                         [Target("one", self.source / "fam" / "one"), Target("two", self.source / "fam" / "two")])
        self.assertEqual(selection.expand_skill_targets("missing"), [])

    def test_iter_install_targets_keeps_order_and_appends_addons(self):
        # targets.sh:230-246
        addon = Target("noop", self.root / "addon" / "noop", addon_id="noop")
        targets = selection.iter_install_targets(["fam", "alpha"], [addon])
        self.assertEqual([t.name for t in targets], ["one", "two", "alpha", "noop"])
        self.assertEqual(targets[-1].addon_id, "noop")

    def test_validate_requested_skills(self):
        # targets.sh:263-273
        selection.validate_requested_skills(["alpha", "fam"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit) as stop:
            selection.validate_requested_skills(["alpha", "nope"])
        self.assertEqual(stop.exception.code, 1)
        self.assertIn("[ERROR] Skill not found: nope", out.getvalue())
        self.assertIn("[ERROR] Run bash install.sh --list to see available skills.", out.getvalue())
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_COMMAND": ".\\install.cmd"}), \
                contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit):
            selection.validate_requested_skills(["nope"])
        self.assertIn("[ERROR] Run .\\install.cmd --list to see available skills.", out.getvalue())

    def test_has_managed_installs(self):
        # targets.sh:296-320: core names are checked under the skills root even when the source lacks them
        skills_root = self.home / ".claude" / "skills"
        skills_root.mkdir(parents=True)
        self.assertFalse(selection.has_managed_installs(skills_root))
        (skills_root / "task-router").mkdir()
        self.assertTrue(selection.has_managed_installs(skills_root))
        (skills_root / "task-router").rmdir()
        (self.source / "coding-convention" / "writing-plans").mkdir(parents=True)
        (self.source / "coding-convention" / "writing-plans" / "SKILL.md").write_text("x", encoding="utf-8")
        (skills_root / "writing-plans").symlink_to(self.source / "coding-convention" / "writing-plans")
        self.assertTrue(selection.has_managed_installs(skills_root))
        self.assertFalse(selection.has_managed_installs(self.home / "nowhere"))


class CleanupTests(SelectionCase):
    def test_deprecated_skills_move_to_backups_then_retired_assets_runs(self):
        # targets.sh:340-376
        ctx = self.context("claude")
        skills_root = ctx.skills_dir
        (skills_root / "harness-security-scan").mkdir(parents=True)
        (skills_root / "harness-security-scan" / "SKILL.md").write_text("old", encoding="utf-8")
        (skills_root / "session-intent-guard").symlink_to(self.source / "alpha")
        calls = []
        with mock.patch("installer.runner.run_tool", side_effect=lambda *a, **k: (calls.append((a, k)), runner.ToolResult(0))[1]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertTrue(selection.cleanup_deprecated_installed_skills(ctx, skills_root))
        backups = sorted((self.home / ".ghost-alice" / "deprecated-skill-backups").iterdir())
        self.assertEqual([b.name.rsplit("-", 1)[0] for b in backups], ["harness-security-scan", "session-intent-guard"])
        self.assertFalse((skills_root / "harness-security-scan").exists())
        self.assertFalse((skills_root / "session-intent-guard").is_symlink())
        self.assertIn(f"[WARN] Deprecated installed skill moved out of discovery path: harness-security-scan -> {backups[0]}",
                      out.getvalue())
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args, ("_shared/retired_assets.py", "--skills-root", str(skills_root), "--source-root",
                                str(self.source), "--ghost-root", str(self.home / ".ghost-alice"), "--platform", "claude",
                                "--commands-root", str(self.home / ".claude" / "commands")))
        self.assertTrue(kwargs.get("no_bytecode"))

    def test_missing_skills_root_is_a_no_op(self):
        ctx = self.context("claude")
        with mock.patch("installer.runner.run_tool") as run_tool:
            self.assertTrue(selection.cleanup_deprecated_installed_skills(ctx, self.home / "nowhere"))
        run_tool.assert_not_called()

    def test_retirement_failure_returns_false(self):
        ctx = self.context("codex")
        ctx.skills_dir.mkdir(parents=True)
        with mock.patch("installer.runner.run_tool", return_value=runner.ToolResult(1)):
            self.assertFalse(selection.cleanup_deprecated_installed_skills(ctx, ctx.skills_dir))


class CollectTests(SelectionCase):
    def test_collect_targets_counts_core_support_and_addons(self):
        # installer_lib/install.sh:849-878
        ctx = self.context("claude")
        addon = Target("noop", self.root / "addon" / "noop", addon_id="noop")
        with mock.patch("installer.addons.collect_addon_targets", return_value=[addon]):
            collected = selection.collect_targets(ctx, ["fam", "alpha"])
        self.assertEqual([t.name for t in collected.skill_targets], ["one", "two", "alpha"])
        self.assertEqual(collected.addon_targets, [addon])
        self.assertEqual([t.name for t in collected.all_targets], ["one", "two", "alpha", "noop"])
        self.assertEqual(collected.skill_target_total, 4)
        self.assertEqual(collected.support_target_total, 1)
        self.assertEqual(collected.total_target_count, 5)
        self.assertEqual(ctx.addon_targets, [addon])

    def test_collect_targets_rejects_unknown_skills_first(self):
        ctx = self.context("claude")
        with mock.patch("installer.addons.collect_addon_targets") as collect:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(runner.InstallerExit):
                selection.collect_targets(ctx, ["nope"])
        collect.assert_not_called()

    def test_support_total_is_zero_without_shared(self):
        import shutil
        shutil.rmtree(self.source / "_shared")
        ctx = self.context("claude")
        with mock.patch("installer.addons.collect_addon_targets", return_value=[]):
            collected = selection.collect_targets(ctx, ["alpha"])
        self.assertEqual(collected.support_target_total, 0)
        self.assertEqual(collected.total_target_count, 1)


class CollisionTests(SelectionCase):
    def test_no_addon_sources_means_no_check(self):
        ctx = self.context("claude")
        with mock.patch("installer.runner.run_tool") as run_tool:
            selection.check_addon_collisions(ctx)
        run_tool.assert_not_called()

    def test_detect_collisions_arguments_and_exit_codes(self):
        # installer_lib/install.sh:728-764
        (self.source / "coding-convention" / "writing-plans").mkdir(parents=True)
        (self.source / "coding-convention" / "writing-plans" / "SKILL.md").write_text("x", encoding="utf-8")
        ctx = self.context("codex", addon_sources=["/tmp/addon-a", "/tmp/addon-b"])
        calls = []

        def fake(*args, **kwargs):
            calls.append(args)
            return runner.ToolResult(0)

        with mock.patch("installer.runner.run_tool", side_effect=fake):
            selection.check_addon_collisions(ctx)
        args = calls[0]
        self.assertEqual(args[:2], ("_shared/addon_installer.py", "detect-collisions"))
        rest = list(args[2:])
        self.assertEqual(rest[:4], ["--source", "/tmp/addon-a", "--source", "/tmp/addon-b"])
        self.assertIn("--platform", rest); self.assertEqual(rest[rest.index("--platform") + 1], "codex")
        self.assertEqual(rest[rest.index("--skills-dir") + 1], str(ctx.skills_dir))
        self.assertEqual(rest[rest.index("--addons-dir") + 1], str(self.home / ".ghost-alice" / "addons" / "codex"))
        self.assertEqual(rest[rest.index("--claude-commands-dir") + 1], str(self.home / ".claude" / "commands"))
        self.assertEqual(rest[rest.index("--resources-dir") + 1], str(self.home / ".ghost-alice" / "resources" / "codex"))
        core = [rest[i + 1] for i, token in enumerate(rest) if token == "--core-skill"]
        self.assertEqual(core, [*selection.ALL_SKILLS[:2], "coding-convention", "writing-plans", *selection.ALL_SKILLS[3:]])
        for code, message in ((2, "Addon install collision detected; aborting install"), (1, "Addon manifest error; aborting install")):
            with self.subTest(code=code):
                with mock.patch("installer.runner.run_tool", return_value=runner.ToolResult(code)):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out), self.assertRaises(runner.InstallerExit) as stop:
                        selection.check_addon_collisions(ctx)
                self.assertEqual(stop.exception.code, 1)
                self.assertIn(f"[ERROR] {message}", out.getvalue())


class ListingTests(SelectionCase):
    def test_skill_description(self):
        # uninstall.sh:40-48
        self.assertEqual(selection.skill_description(self.source / "alpha" / "SKILL.md"), "Alpha does one thing.")
        self.assertEqual(selection.skill_description(self.source / "fam" / "one" / "SKILL.md"), "one skill")
        long = self.source / "long.md"
        long.write_text("description: " + "x" * 100 + "\n", encoding="utf-8")
        self.assertEqual(selection.skill_description(long), "x" * 77 + "...")
        self.assertEqual(selection.skill_description(self.source / "missing.md"), "")
        exact = self.source / "exact.md"
        exact.write_text("description: " + "y" * 80 + "\n", encoding="utf-8")
        self.assertEqual(selection.skill_description(exact), "y" * 80)

    def test_list_skills_output(self):
        # uninstall.sh:81-108
        (self.source / "task-router").mkdir()
        (self.source / "task-router" / "SKILL.md").write_text('description: "Routes work."\n', encoding="utf-8")
        (self.source / "coding-convention" / "writing-plans").mkdir(parents=True)
        (self.source / "coding-convention" / "writing-plans" / "SKILL.md").write_text("description: Plans.\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            selection.list_skills()
        text = out.getvalue()
        self.assertTrue(text.startswith("Available skills:\n\n"))
        self.assertIn("  task-router                  Routes work.\n", text)
        self.assertIn("  coding-convention (family, 1 sub-skills)\n    └ writing-plans              Plans.\n", text)
        self.assertIn("  adversarial-verification     (source missing)\n", text)


if __name__ == "__main__":
    unittest.main()
