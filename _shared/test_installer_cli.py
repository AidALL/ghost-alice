"""installer.cli: options, command dispatch and the install step order (docs/reference/installer-architecture.md).

Expected texts come from install.sh:29-37, 115-201, 203-269, 448-562 and installer_lib/install.sh:880-1139.
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
from installer import cli, report  # noqa: E402
from installer.runner import InstallerExit  # noqa: E402
from installer.selection import ALL_SKILLS, Collected, Target  # noqa: E402
from installer.source import HookPathChange  # noqa: E402
from installer_testing import make_context, make_source, quiet  # noqa: E402


class CliCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.source = make_source(root / "src")
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_INSTALL_REPORT_CHILD", "GHOST_ALICE_INSTALL_LOG_FILE",
                    "GHOST_ALICE_INSTALL_EVENT_FILE", "GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE"):
            os.environ.pop(key, None)
        quiet(self.home)
        self.patches = contextlib.ExitStack()
        self.calls: list[tuple[str, tuple, dict]] = []

    def tearDown(self):
        self.patches.close()
        self.env.stop()
        self.temp.cleanup()

    def stub(self, target: str, result=None, side_effect=None):
        name = target.rsplit(".", 1)[-1]

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if side_effect is not None:
                return side_effect(*args, **kwargs)
            return result

        return self.patches.enter_context(mock.patch(target, side_effect=record))

    def names(self):
        return [name for name, _, _ in self.calls]

    @staticmethod
    def captured(fn, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = fn(*args, **kwargs)
        return result, out.getvalue() + err.getvalue()

    def expect_exit(self, code, fn, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(InstallerExit) as stop:
            fn(*args)
        self.assertEqual(stop.exception.code, code)
        return out.getvalue() + err.getvalue()


class OptionTests(CliCase):
    def test_flags(self):
        # install.sh:115-201
        options = cli.parse_options(["--platform", "codex", "--skip-source-health", "--update-source", "--cleanup-pending",
                                     "--addon-source", "/a", "--addon-source", "/b", "--addon-tag", "v1", "--addon-skip",
                                     "--list-addons", "--verbose", "--visibility", "strict", "task-router", "--auto",
                                     "--status", "-a", "--bogus"])
        self.assertEqual(options.platform, "codex")
        self.assertTrue(options.platform_explicit)
        self.assertTrue(options.skip_source_health and options.update_source and options.cleanup_pending)
        self.assertEqual(options.addon_sources, ["/a", "/b"])
        self.assertEqual(options.addon_tags, ["v1"])
        self.assertTrue(options.addon_skip and options.list_addons and options.verbose)
        self.assertEqual(options.visibility, "strict")
        self.assertEqual(options.args, ["task-router", "--status", "--bogus"])
        short = cli.parse_options(["-p", "claude", "-v", "--prompt-platform", "--agent-visibility", "minimal"])
        self.assertEqual((short.platform, short.verbose, short.prompt_platform, short.visibility), ("claude", True, True, "minimal"))
        defaults = cli.parse_options([])
        self.assertEqual(defaults, cli.Options())

    def test_value_errors(self):
        for argv, message in (
            (["--platform"], "--platform requires a value (claude|codex)"),
            (["--platform", "windows"], "Unknown platform: windows (must be claude or codex)"),
            (["--addon-source"], "--addon-source requires a value"),
            (["--addon-tag"], "--addon-tag requires a value"),
            (["--visibility"], "--visibility requires a value (strict|dynamic|minimal)"),
            (["--agent-visibility"], "--agent-visibility requires a value (strict|dynamic|minimal)"),
            (["--visibility", "loud"], "Unknown agent visibility profile: loud (must be strict, dynamic, or minimal)"),
        ):
            with self.subTest(argv=argv):
                out = self.expect_exit(1, cli.parse_options, argv)
                self.assertIn(f"[ERROR] {message}", out)

    def test_isolated_destination_rejected_before_anything_else(self):
        # install.sh:29-37
        for argv in (["--destination-file", "x"], ["--destination-file=x"], ["--platform", "claude", "--isolated"]):
            with self.subTest(argv=argv):
                out = self.expect_exit(2, cli.reject_isolated_destination, argv)
                self.assertIn("[ERROR] isolated destinations require the Python hook installer; shell source integration is unsupported", out)
        cli.reject_isolated_destination(["--platform", "claude"])
        rc, out = self.captured(cli.main, ["--isolated"])
        self.assertEqual(rc, 2)
        self.assertEqual(out.count("[ERROR]"), 1)

    def test_effective_visibility_ignores_existing_config_without_a_flag(self):
        # The flag wins; without a flag the install uses "dynamic" even when config.json names another profile.
        config_path = self.home / ".ghost-alice" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('{"schema_version":"ghost-alice-config.v1","agent_visibility":{"profile":"minimal"}}',
                               encoding="utf-8")
        self.assertEqual(cli.build_context(cli.parse_options([])).visibility, "dynamic")
        self.assertEqual(cli.build_context(cli.parse_options(["--visibility", "strict"])).visibility, "strict")

    def test_official_addon_shortcuts(self):
        # install.sh:203-253
        options = cli.parse_options(["--addon", "autopilot", "task-router"])
        cli.normalize_official_addon_shortcuts(options)
        self.assertEqual(options.addon_sources, ["https://github.com/AidALL/ghost-alice-autopilot.git"])
        self.assertEqual(options.args, ["task-router"])
        with mock.patch.dict(os.environ, {"GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE": "/local/autopilot"}):
            options = cli.parse_options(["--addon", "autopilot-mode"])
            cli.normalize_official_addon_shortcuts(options)
        self.assertEqual(options.addon_sources, ["/local/autopilot"])
        kept = cli.parse_options(["--uninstall", "--addon", "autopilot"])
        cli.normalize_official_addon_shortcuts(kept)
        self.assertEqual(kept.args, ["--uninstall", "--addon", "autopilot"])
        self.assertEqual(kept.addon_sources, [])
        self.assertTrue(cli.args_request_uninstall(["x", "-u"]))
        self.assertFalse(cli.args_request_uninstall(["x"]))
        self.assertIn("[ERROR] --addon requires a value (autopilot)",
                      self.expect_exit(1, cli.normalize_official_addon_shortcuts, cli.parse_options(["--addon"])))
        self.assertIn("[ERROR] Unknown official addon: other (supported: autopilot)",
                      self.expect_exit(1, cli.normalize_official_addon_shortcuts, cli.parse_options(["--addon", "other"])))

    def test_auto_detect_matrix(self):
        # install.sh:255-269; test_installer_default_auto.py:187-201
        for argv, expected in ((["task-router"], True), ([], True), (["--status"], False), (["--help"], False),
                               (["--uninstall"], False), (["--bogus"], False), (["--list"], False), (["--sync-commands"], False)):
            with self.subTest(argv=argv):
                self.assertEqual(cli.auto_detect_requested(cli.parse_options(argv)), expected)
        for flag in (["--platform", "claude"], ["--prompt-platform"], ["--cleanup-pending"], ["--list-addons"], ["--update-source"]):
            with self.subTest(flag=flag):
                self.assertFalse(cli.auto_detect_requested(cli.parse_options([*flag, "task-router"])))

    def test_plain_full_uninstall(self):
        # install.sh:271-278
        for argv, expected in (([u], True) for u in ("--uninstall", "--remove", "-u")):
            self.assertEqual(cli.plain_full_uninstall_requested(cli.parse_options(argv)), expected)
        self.assertFalse(cli.plain_full_uninstall_requested(cli.parse_options(["--uninstall", "task-router"])))
        self.assertFalse(cli.plain_full_uninstall_requested(cli.parse_options(["--platform", "claude", "--uninstall"])))
        self.assertFalse(cli.plain_full_uninstall_requested(cli.parse_options(["--prompt-platform", "--uninstall"])))

    def test_help_text(self):
        # install.sh:508-545
        _, out = self.captured(cli.print_help)
        for line in (
            "Usage:",
            "  bash install.sh                              # Install to detected AI tools",
            "  bash install.sh --addon autopilot            # Install official autopilot addon to detected AI tools",
            "  bash install.sh --platform claude            # Install only to Claude Code",
            "  bash install.sh --platform codex             # Install to Codex",
            "Common commands:",
            "  --platform, -p PLAT    Choose claude | codex",
            "  --visibility strict|dynamic|minimal",
            "                          Set user-facing governance message visibility profile",
            "  --doctor               Run install diagnostics",
            "  --repair               Re-provision missing managed targets (never overwrites user-owned slots)",
            "Removal commands:",
            "  --uninstall, -u        Full uninstall: remove managed hooks, bootstrap, support state, and install targets",
            "Advanced/operator commands:",
            "  --update-source        Stash source checkout local changes and run git pull --ff-only",
            "  --addon-tag TAG        Checkout branch/tag for git URL addon sources",
            "  --help, -h             Show this help",
        ):
            self.assertIn(line + "\n", out)


class DispatchTests(CliCase):
    def setUp(self):
        super().setUp()
        self.stub("installer.selection.list_skills")
        self.stub("installer.diagnostics.check_status", 0)
        self.stub("installer.diagnostics.run_doctor", 3)
        self.stub("installer.lifecycle.run_repair", 0)
        self.stub("installer.lifecycle.uninstall", 0)
        self.stub("installer.hosts.sync_commands", 0)
        self.stub("installer.source.update_source", 4)
        self.stub("installer.preservation.cleanup_pending", 0)
        self.stub("installer.addons.list_addons", 0)
        self.stub("installer.addons.prepare_sources")
        self.stub("installer.auto.run_install_children", 0)
        self.stub("installer.auto.run_uninstall_children", 0)
        self.stub("installer.cli.run_install", 0)
        self.stub("installer.lock.acquire", self.home / "lock")
        self.stub("installer.lock.release")
        self.stub("installer.source.assert_session_gate_contract")
        self.stub("installer.runner.run_tool", mock.Mock(returncode=5))

    def dispatch(self, argv, present=("claude", "codex")):
        with mock.patch("installer.locations.detect_present_platforms", return_value=list(present)), \
                mock.patch("installer.locations.detect_uninstall_platforms", return_value=list(present)):
            return self.captured(cli.main, argv)

    def test_command_table(self):
        # install.sh:473-562
        for argv, expected, rc in (
            (["--platform", "claude", "--list"], "list_skills", 0),
            (["--platform", "claude", "--status"], "check_status", 0),
            (["--platform", "claude", "--doctor"], "run_doctor", 3),
            (["--platform", "claude", "--sync-commands"], "sync_commands", 0),
            (["--platform", "codex", "--check-commands"], "sync_commands", 0),
            (["--platform", "claude", "--list-addons", "--addon-source", "/a"], "list_addons", 0),
            (["--update-source"], "update_source", 4),
        ):
            with self.subTest(argv=argv):
                self.calls.clear()
                result, _ = self.dispatch(argv)
                self.assertEqual(result, rc)
                self.assertIn(expected, self.names())
                self.assertNotIn("acquire", self.names())
                if expected == "sync_commands":
                    _, args, _ = next(c for c in self.calls if c[0] == "sync_commands")
                    mode = "check" if "--check-commands" in argv else "sync"
                    self.assertEqual(args[1:], (mode, argv[1]))

    def test_locked_commands(self):
        # uninstall, repair, cleanup-pending and gateway run under the lock
        for argv, expected, command in (
            (["--platform", "claude", "--repair"], "run_repair", "repair"),
            (["--platform", "codex", "--uninstall", "task-router"], "uninstall", "uninstall"),
            (["--platform", "claude", "--cleanup-pending"], "cleanup_pending", "cleanup-pending"),
            (["--platform", "claude", "gateway", "detach", "--root", "/x"], "run_tool", "gateway"),
        ):
            with self.subTest(argv=argv):
                self.calls.clear()
                self.dispatch(argv)
                names = self.names()
                self.assertLess(names.index("acquire"), names.index(expected))
                self.assertLess(names.index(expected), names.index("release"))
                self.assertEqual(self.calls[names.index("acquire")][1][0], command)
                _, args, _ = self.calls[names.index(expected)]
                if expected == "run_tool":
                    self.assertEqual(args, ("_shared/context_gateway_install.py", "detach", "--root", "/x"))
                if expected == "uninstall":
                    self.assertEqual(args[1], ["task-router"])

    def test_install_paths(self):
        rc, _ = self.dispatch(["--platform", "claude"])
        self.assertEqual(rc, 0)
        _, args, _ = next(c for c in self.calls if c[0] == "run_install")
        self.assertEqual(args[1], [])
        self.assertEqual(args[0].platform, "claude")
        self.calls.clear()
        self.dispatch(["--platform", "codex", "task-router", "merge-companion"])
        _, args, _ = next(c for c in self.calls if c[0] == "run_install")
        self.assertEqual(args[1], ["task-router", "merge-companion"])
        self.assertEqual(args[0].skills_dir, self.home / ".agents" / "skills")

    def test_auto_and_fan_out(self):
        rc, _ = self.dispatch(["task-router", "--addon-source", "/a"])
        self.assertEqual(rc, 0)
        self.assertIn("prepare_sources", self.names())
        self.assertIn("run_install_children", self.names())
        self.assertNotIn("run_install", self.names())
        self.calls.clear()
        rc, _ = self.dispatch(["--uninstall"])
        self.assertIn("run_uninstall_children", self.names())
        self.assertNotIn("uninstall", self.names())
        rc, out = self.dispatch([], present=())
        self.assertEqual(rc, 0)
        self.assertIn("[WARN] No AI platform home directories detected. At least one of ~/.claude, ~/.codex is required.", out)
        rc, out = self.dispatch(["--uninstall"], present=())
        self.assertEqual(rc, 0)
        self.assertIn("[WARN] No install targets detected. Need a platform home or ~/.ghost-alice/install-state/<platform>.json.", out)

    def test_unknown_option_and_prompt(self):
        rc, out = self.dispatch(["--platform", "claude", "--bogus"])
        self.assertEqual(rc, 1)
        self.assertIn("[ERROR] Unknown option: --bogus", out)
        self.assertIn("See bash install.sh --help", out)
        rc, out = self.dispatch(["--platform", "claude", "--prompt-platform", "--status"])
        self.assertEqual(rc, 0)
        self.assertIn("[WARN] --prompt-platform ignored because --platform is already specified.", out)
        with mock.patch("installer.cli.sys.stdin") as stdin:
            stdin.isatty.return_value = False
            rc, out = self.dispatch(["--prompt-platform"])
        self.assertEqual(rc, 1)
        self.assertIn("[ERROR] --prompt-platform requires an interactive terminal.", out)
        rc, out = self.dispatch(["--platform", "claude", "--help"])
        self.assertEqual(rc, 0)
        self.assertIn("Usage:\n", out)


class InstallOrderTests(CliCase):
    STEPS = ("install_prerequisites", "setup_git_hooks", "acquire", "check_source_health", "collect_targets",
             "cleanup_deprecated_installed_skills", "encoding_guard", "preflight", "resume_pending_uninstalls",
             "check_addon_collisions", "sync_runtime_shared", "install_hooks", "ensure_bootstrap", "sync_commands",
             "verify_after_copy", "write_ownership_markers", "snapshot_after_install", "migrate_state_once",
             "write_sidecars", "write_install_state_manifest", "write_event", "release")

    def setUp(self):
        super().setUp()
        (self.source / "task-router").mkdir()
        (self.source / "task-router" / "SKILL.md").write_text("x", encoding="utf-8")
        self.stub("installer.prerequisites.install_prerequisites", True)
        self.stub("installer.source.setup_git_hooks", HookPathChange(False, True, "hooks"))
        self.stub("installer.lock.acquire", self.home / "lock")
        self.stub("installer.lock.release")
        self.stub("installer.source.check_source_health", True)
        self.stub("installer.selection.collect_targets", side_effect=self.collect)
        self.stub("installer.preservation.encoding_guard", True)
        self.stub("installer.preservation.preflight", True)
        self.stub("installer.selection.cleanup_deprecated_installed_skills", True)
        self.stub("installer.addons.resume_pending_uninstalls")
        self.stub("installer.selection.check_addon_collisions")
        self.stub("installer.transaction.install_shared", 0)
        self.stub("installer.transaction.link_or_copy", 0)
        self.stub("installer.transaction.copy_targets_batch", True)
        self.stub("installer.transaction.sync_runtime_shared", True)
        self.stub("installer.hosts.install_hooks", True)
        self.stub("installer.hosts.ensure_claude_bootstrap", True)
        self.stub("installer.hosts.ensure_codex_bootstrap", True)
        self.stub("installer.hosts.sync_commands", 0)
        self.stub("installer.transaction.verify_after_copy", True)
        self.stub("installer.transaction.write_ownership_markers", True)
        self.stub("installer.transaction.snapshot_after_install", True)
        self.stub("installer.addons.migrate_state_once")
        self.stub("installer.addons.write_sidecars", True)
        self.stub("installer.transaction.write_install_state_manifest", True)
        self.stub("installer.report.write_event")

    def collect(self, ctx, skills):
        targets = [Target("task-router", self.source / "task-router"), Target("alpha", self.source / "alpha")]
        return Collected(skill_targets=targets, addon_targets=[], skill_target_total=2, support_target_total=1)

    def order(self):
        names = []
        for name, _, _ in self.calls:
            if name in ("ensure_claude_bootstrap", "ensure_codex_bootstrap"):
                name = "ensure_bootstrap"
            if name in self.STEPS and (not names or names[-1] != name):
                names.append(name)
        return names

    def test_claude_link_mode_order(self):
        # S1–S19 with placement S12 as install_shared then link_or_copy per target
        ctx = make_context(self.source, self.home, "claude", args=["task-router", "alpha"])
        rc, out = self.captured(cli.run_install, ctx, ["task-router", "alpha"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.order(), list(self.STEPS))
        placement = [n for n, _, _ in self.calls if n in ("install_shared", "link_or_copy", "copy_targets_batch")]
        self.assertEqual(placement, ["install_shared", "link_or_copy", "link_or_copy"])
        names = [n for n, _, _ in self.calls]
        self.assertLess(names.index("check_source_health"), names.index("collect_targets"))
        self.assertLess(names.index("check_addon_collisions"), names.index("install_shared"))
        self.assertLess(names.index("install_hooks"), names.index("ensure_claude_bootstrap"))
        self.assertLess(names.index("sync_runtime_shared"), names.index("install_hooks"))
        self.assertIn("[OK] [5/5] Verification: install state recorded", out) if report.state().report_child else None

    def test_codex_copy_mode_uses_one_batch(self):
        ctx = make_context(self.source, self.home, "codex", args=["task-router"])
        rc, _ = self.captured(cli.run_install, ctx, ["task-router", "alpha"])
        self.assertEqual(rc, 0)
        placement = [n for n, _, _ in self.calls if n in ("install_shared", "link_or_copy", "copy_targets_batch")]
        self.assertEqual(placement, ["copy_targets_batch"])
        _, args, _ = next(c for c in self.calls if c[0] == "copy_targets_batch")
        batch = args[1]
        self.assertEqual(batch.labels, ["_shared", "task-router", "alpha"])
        self.assertEqual(batch.kinds, ["support", "skill", "skill"])
        self.assertEqual(batch.statuses, ["new", "new", "new"])
        self.assertEqual([n for n, _, _ in self.calls if n == "ensure_codex_bootstrap"], ["ensure_codex_bootstrap"])

    def test_stale_source_stops_before_selection_and_releases_the_lock(self):
        self.patches.enter_context(mock.patch("installer.source.check_source_health", return_value=False))
        ctx = make_context(self.source, self.home, "claude")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(InstallerExit) as stop:
            cli.run_install(ctx, [])
        self.assertEqual(stop.exception.code, 1)
        names = [n for n, _, _ in self.calls]
        self.assertNotIn("collect_targets", names)
        self.assertEqual(names[-1], "release")

    def test_failed_step_stops_the_install(self):
        self.patches.enter_context(mock.patch("installer.hosts.install_hooks", return_value=False))
        ctx = make_context(self.source, self.home, "claude")
        out = self.expect_exit(1, cli.run_install, ctx, [])
        self.assertIn("[ERROR] Hook install failed. Aborting skill install", out)
        names = [n for n, _, _ in self.calls]
        self.assertNotIn("ensure_claude_bootstrap", names)
        self.assertNotIn("verify_after_copy", names)
        self.assertEqual(names[-1], "release")

    def test_default_skills_are_all_skills(self):
        ctx = make_context(self.source, self.home, "claude")
        self.captured(cli.run_install, ctx, [])
        _, args, _ = next(c for c in self.calls if c[0] == "collect_targets")
        self.assertEqual(args[1], list(ALL_SKILLS))


if __name__ == "__main__":
    unittest.main()
