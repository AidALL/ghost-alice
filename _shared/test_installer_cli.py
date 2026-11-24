"""installer.cli: options, command dispatch and the install step order (docs/reference/installer-architecture.md).

Expected texts come from install.sh:29-37, 115-201, 203-269, 448-562 and installer_lib/install.sh:880-1139.
"""
from __future__ import annotations

import contextlib
import dataclasses
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import addon_registry  # noqa: E402
import hash_utils  # noqa: E402
from install_transaction import cleanup_completed  # noqa: E402
from git_hook_paths import git  # noqa: E402
from installer import cli, locations, report, runner, source  # noqa: E402
from installer.runner import InstallerExit, ToolResult  # noqa: E402
from installer.selection import ALL_SKILLS, Collected, Target  # noqa: E402
from installer.transaction import report_gateway, verify_after_copy  # noqa: E402
from installer_testing import FakeRunner, hook_status, make_context, make_source, quiet, use_source  # noqa: E402


HELP = """Usage:
  bash install.sh                              # Install to detected AI tools
  bash install.sh --platform claude            # Install only to Claude Code
  bash install.sh --platform codex             # Install to Codex

Common commands:
  --platform, -p PLAT    Choose claude | codex
  --prompt-platform      Ask which AI tool to install
  --status, -s           Show install status
  --visibility strict|dynamic|minimal
                          Set user-facing governance message visibility profile
  --agent-visibility strict|dynamic|minimal
                          Alias for --visibility
  --doctor               Run install diagnostics
  update [--remote URL]  Update installed Core skills from the remote main branch
  --repair               Re-provision missing managed targets (never overwrites an occupied slot that is not a clean managed target)
  --verbose, -v          Show per-target install details

Removal commands:
  --uninstall, -u        Full uninstall: remove managed hooks, bootstrap, support state, and install targets
  --platform PLAT --uninstall SKILL_NAMES
                          Remove selected skills from one platform

Advanced commands:
  SKILL_NAMES            Install specified skills only
  --list, -l             List available skills
  --cleanup-pending      Clean false-positive pending entries
  --update-source        Stash source checkout local changes and run git pull --ff-only
  --post-merge           Run by the git post-merge hook: install when the merged checkout is the install source
  --merged-checkout PATH
                          With --post-merge: the checkout the merge ran in (default: this checkout)
  --addon-source PATH|URL
                          Add local addon repo or git URL source
  --addon-tag TAG        Checkout branch/tag for git URL addon sources
  --addon-skip           Disable addon installation
  --list-addons          List addon manifest targets
  --skip-source-health   Skip source health gate
  --local-source         Install the files in this checkout or saved package
  --adopt-source         Record this checkout as the development source
  --sync-commands        Sync workspace commands
  --check-commands       Check workspace commands only
  gateway ARGS...        Run the context gateway installer once with ARGS
  --help, -h             Show this help
"""


def command_named(name: str) -> "cli.Command":
    return next(command for command in cli.COMMANDS if name in command.names)


def selecting_argv(command: "cli.Command") -> list[str]:
    """Arguments that select `command`: a skill name for install, else its first name."""
    if command is cli.INSTALL:
        return ["task-router"]
    return [command.names[0], "detach"] if command.names[0] == "gateway" else [command.names[0]]


class CliCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.source = make_source(root / "src")
        self.enterContext(use_source(self.source))
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "GHOST_ALICE_INSTALL_LOCALE": "en"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_INSTALL_REPORT_CHILD", "GHOST_ALICE_INSTALL_LOG_FILE",
                    "GHOST_ALICE_INSTALL_EVENT_FILE"):
            os.environ.pop(key, None)
        quiet(self.home)
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.calls: list[tuple[str, tuple, dict]] = []

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

    def home_files(self) -> dict[str, bytes]:
        return {path.relative_to(self.home).as_posix(): path.read_bytes()
                for path in self.home.rglob("*") if path.is_file()}

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
    def test_public_addon_id_is_rejected_before_any_install_or_removal(self):
        (self.home / ".claude").mkdir(exist_ok=True)
        self.stub("installer.auto.run_uninstall_children", 0)
        for argv in (["--uninstall", "--addon-id", "one"],
                     ["--platform", "codex", "--local-source", "--addon-id", "one", "--list"],
                     ["--status", "--addon-id", "one"]):
            with self.subTest(argv=argv):
                code, output = self.captured(cli.main, argv)
                self.assertEqual(code, 1, output)
                self.assertIn("--addon-id", output)
        self.assertNotIn("run_uninstall_children", self.names())

    def test_internal_host_install_accepts_the_selected_addon_ids(self):
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_HOST_CHILD": "1"}):
            options = cli.parse_options(["--platform", "claude", "--addon-id", "one", "alpha"])
            self.assertEqual(cli.build_context(options).addon_ids, ["one"])

    def test_addon_skip_avoids_collisions_hooks_grants_extras_and_sidecars(self):
        checkout = Path(__file__).resolve().parents[1]
        addon = checkout / "_shared" / "tests" / "fixtures" / "rich-addon"
        settings = self.home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"permissions": {"allow": ["Skill(existing)"]},
                                        "hooks": {"PostToolUse": [{"hooks": [{"type": "command",
                                                                            "command": "existing-addon-hook"}]}]}}))
        commands = self.home / ".claude" / "commands"
        commands.mkdir()
        (commands / "richcmd.md").write_text("user command\n")
        existing = self.home / ".ghost-alice" / "addons" / "claude" / "existing.json"
        existing_skill = self.home / ".claude" / "skills" / "existing"
        existing_skill.mkdir(parents=True)
        (existing_skill / "SKILL.md").write_text("existing addon\n")
        addon_registry.write_record({
            "schema_version": "1.0", "addon_id": "existing", "addon_version": "1.0.0",
            "source": str(self.home / "existing-source"), "platform": "claude", "owner": "addon",
            "origin": "addon:existing", "depends_on_core": [], "min_core_version": "0.1.0",
            "installed_at": "2026-11-01T00:00:00Z", "provided": [{
                "kind": "skill", "name": "existing", "target": str(existing_skill), "ownership": "addon",
                "install_mode": "copy", "content_hash": hash_utils.hash_target(str(existing_skill), "copy"),
                "marker": "", "metadata": {},
            }],
        }, addons_dir=existing.parent)
        before = existing.read_bytes()
        with use_source(checkout):
            ctx = cli.build_context(cli.parse_options(["--addon-source", str(addon), "--addon-skip"]))
            self.assertEqual(cli.selection.collect_targets(ctx, ["task-router"]).addon_targets, [])
            # Collision checking must not inspect a source excluded from this request.
            cli.selection.check_addon_collisions(ctx)
            self.assertTrue(cli.hosts.install_hooks(ctx, "install", "claude"))
            self.assertTrue(cli.addons.write_sidecars(ctx, ctx.skills_dir))
        self.assertEqual(ctx.options.addon_sources, [str(addon)])
        installed_settings = json.loads(settings.read_text())
        self.assertIn("Skill(existing)", installed_settings["permissions"]["allow"])
        self.assertNotIn("Skill(richskill)", installed_settings["permissions"]["allow"])
        self.assertIn("existing-addon-hook", settings.read_text())
        self.assertNotIn("richobs", settings.read_text())
        self.assertEqual((commands / "richcmd.md").read_text(), "user command\n")
        self.assertEqual(existing.read_bytes(), before)
        self.assertEqual((existing_skill / "SKILL.md").read_text(), "existing addon\n")
        self.assertFalse((existing.parent / "rich.json").exists())
        self.assertFalse((ctx.skills_dir / "richskill").exists())
        self.assertFalse((self.home / ".ghost-alice" / "resources" / "claude" / "rich").exists())

    def test_addon_skip_filters_the_install_plan_but_keeps_the_raw_request(self):
        options = cli.parse_options(["--addon-source", "/requested/addon", "--addon-skip"])
        ctx = cli.build_context(options)
        self.assertEqual(options.addon_sources, ["/requested/addon"])
        self.assertEqual(ctx.addon_sources, [])

    def test_flags(self):
        # install.sh:115-201
        options = cli.parse_options(["--platform", "codex", "--skip-source-health", "--update-source", "--cleanup-pending",
                                     "--addon-source", "/a", "--addon-source", "/b", "--addon-tag", "v1", "--addon-skip",
                                     "--list-addons", "--verbose", "--visibility", "strict", "task-router", "--auto",
                                     "--status", "-a", "--bogus", "--adopt-source", "--merged-checkout", "/m"])
        self.assertEqual(options.platform, "codex")
        self.assertTrue(options.platform_explicit)
        self.assertTrue(options.skip_source_health and options.update_source and options.cleanup_pending)
        self.assertTrue(options.adopt_source)
        self.assertEqual(options.merged_checkout, "/m")
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
            (["--post-merge", "--merged-checkout"], "--merged-checkout requires a value"),
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


    def test_args_request_uninstall_sees_every_name_of_the_uninstall_command(self):
        self.assertTrue(cli.args_request_uninstall(["x", "-u"]))
        self.assertFalse(cli.args_request_uninstall(["x"]))

    def test_auto_detect_matrix(self):
        # Install without a platform is auto mode; every table command runs once, never per platform.
        for command in cli.COMMANDS:
            with self.subTest(command=command.usage):
                expected = command is cli.INSTALL
                self.assertEqual(cli.auto_detect_requested(cli.parse_options(selecting_argv(command))), expected)
        for argv in ([], ["task-router"], ["task-router", "merge-companion"]):
            self.assertTrue(cli.auto_detect_requested(cli.parse_options(argv)), argv)
        for argv in (["--bogus"], ["-Offboard"], ["-x", "task-router"], ["gateway", "detach", "--root", "/x"]):
            self.assertFalse(cli.auto_detect_requested(cli.parse_options(argv)), argv)
        for flag in (["--platform", "claude"], ["--prompt-platform"]):
            with self.subTest(flag=flag):
                self.assertFalse(cli.auto_detect_requested(cli.parse_options([*flag, "task-router"])))

    def test_find_command_reads_the_table(self):
        for command in cli.COMMANDS:
            for name in command.names:
                with self.subTest(name=name):
                    self.assertIs(cli.find_command(cli.parse_options([name])), command)
        self.assertIs(cli.find_command(cli.parse_options([])), cli.INSTALL)
        self.assertIs(cli.find_command(cli.parse_options(["task-router", "--bogus"])), cli.INSTALL)
        # A token that starts with "-" and names no command is never a skill name.
        for argv in (["--bogus"], ["-Offboard"], ["-x"], ["--addon", "demo-addon", "--uninstall"]):
            self.assertIsNone(cli.find_command(cli.parse_options(argv)), argv)
        # A flag command is selected wherever it appears and before an args[0] command; flags follow table order.
        self.assertIs(cli.find_command(cli.parse_options(["--status", "--list-addons"])),
                      command_named("--list-addons"))
        self.assertIs(cli.find_command(cli.parse_options(["--update-source", "--cleanup-pending"])),
                      command_named("--cleanup-pending"))
        self.assertTrue(cli.args_request_uninstall(["x", "--remove"]))

    def test_command_knowledge_has_one_owner(self):
        # Contradiction 21: dispatch, auto mode and help read the table; no second list of command names remains.
        for name in ("COMMAND_FLAGS", "COMMAND_WORDS", "UNINSTALL_FLAGS", "HELP_TEXT", "_locked"):
            self.assertFalse(hasattr(cli, name), name)
        names = {name for command in cli.COMMANDS for name in command.names}
        for reader in (cli.dispatch, cli.find_command, cli.auto_detect_requested, cli.plain_full_uninstall_requested,
                       cli.args_request_uninstall, cli.print_help, cli.run_command):
            with self.subTest(reader=reader.__name__):
                source = inspect.getsource(reader)
                self.assertEqual([name for name in names if f'"{name}"' in source or f"'{name}'" in source], [])
        self.assertEqual(cli.COMMANDS, tuple(row for row in cli.COMMAND_LINE if isinstance(row, cli.Command)))
        self.assertEqual(len({name for command in cli.COMMANDS for name in command.names}),
                         sum(len(command.names) for command in cli.COMMANDS))

    def test_table_columns(self):
        rows = {command.usage: (command.lock, command.platform, command.flag, command.checks_source, command.adopts_source)
                for command in cli.COMMANDS}
        self.assertEqual(rows, {
            "--status, -s": (None, True, None, False, False),
            "--doctor": (None, True, None, False, False),
            "update [--remote URL]": (None, False, None, False, False),
            "--repair": ("repair", True, None, True, False),
            "--uninstall, -u": ("uninstall", True, None, False, False),
            "SKILL_NAMES": ("install", True, None, True, True),
            "--list, -l": (None, False, None, False, False),
            "--cleanup-pending": ("cleanup-pending", True, "cleanup_pending", False, False),
            "--update-source": (None, False, "update_source", False, False),
            "--post-merge": (None, False, None, False, False),
            "--list-addons": (None, True, "list_addons", False, False),
            "--sync-commands": (None, True, None, False, False),
            "--check-commands": (None, True, None, False, False),
            "gateway ARGS...": ("gateway", False, None, False, False),
            "--help, -h": (None, False, None, False, False),
        })
        # The active installer runs these commands for another installer; a saved package states them.
        self.assertEqual([command.usage for command in cli.COMMANDS if command.active_runs], ["--repair"])
        self.assertEqual(cli.INSTALL.names, ())
        self.assertEqual(cli.UNINSTALL.names, ("--uninstall", "--remove", "-u"))

    def test_plain_full_uninstall(self):
        # install.sh:271-278
        for argv, expected in (([u], True) for u in ("--uninstall", "--remove", "-u")):
            self.assertEqual(cli.plain_full_uninstall_requested(cli.parse_options(argv)), expected)
        self.assertFalse(cli.plain_full_uninstall_requested(cli.parse_options(["--uninstall", "task-router"])))
        self.assertFalse(cli.plain_full_uninstall_requested(cli.parse_options(["--platform", "claude", "--uninstall"])))
        self.assertFalse(cli.plain_full_uninstall_requested(cli.parse_options(["--prompt-platform", "--uninstall"])))

    def test_help_and_hints_name_the_reported_install_command(self):
        # The usage lines name the command the shell reported, with the comment column kept.
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_COMMAND": ".\\install.cmd"}):
            _, out = self.captured(cli.print_help)
        lines = HELP.splitlines(keepends=True)
        for index in range(1, 4):
            usage, comment = lines[index].split("#", 1)
            lines[index] = f"  {usage.strip().replace('bash install.sh', '.' + chr(92) + 'install.cmd'):<45}#{comment}"
        self.assertEqual(out, "".join(lines))

    def test_help_text(self):
        # install.sh:508-545; every row comes from the table, in table order under its section.
        _, out = self.captured(cli.print_help)
        self.assertEqual(out, HELP)
        for command in cli.COMMANDS:
            with self.subTest(command=command.usage):
                section = out.index(f"{command.section}:\n")
                following = [out.index(f"{other}:\n") for other in cli.HELP_SECTIONS if out.index(f"{other}:\n") > section]
                self.assertLess(section, out.index(f"  {command.usage}"))
                self.assertLess(out.index(f"  {command.usage}"), min(following, default=len(out)))


class CandidateHandoffTests(CliCase):
    def test_shutdown_stop_logs_the_reason_without_preparing_a_candidate_or_result(self):
        from installer import hosts, source, terminal
        self.stub('installer.source.check_install_source')
        self.stub('installer.hosts.confirm_project_trust', '')
        self.stub('installer.source.check_source_health', True)
        options = cli.Options(platform='claude', platform_explicit=True, addon_skip=True, args=['alpha'])
        running = hosts.WriterObservation('running', pids=(43210, 43211),
                                          programs=((43210, 'Claude'), (43211, 'Claude helper')))
        cases = [(running, True, False, 'cancelled', None),
                 (hosts.WriterObservation('unknown', 'process lookup denied'), True, False, 'process lookup denied', None),
                 (running, False, False, 'terminal', None),
                 (running, True, True, 'cancelled', None),
                 (running, True, False, 'input preparation denied', 'preparation'),
                 (running, True, False, 'input read denied', 'read')]
        for seen, interactive, verbose, expected, input_failure in cases:
            with self.subTest(state=seen.state, interactive=interactive, verbose=verbose, input_failure=input_failure):
                report.configure(home=self.home, tty=False, verbose=verbose)
                previous_logs = set((self.home / '.ghost-alice/install').glob('*'))
                with mock.patch.dict(os.environ, {source.REQUEST_ENV: ''}), \
                        mock.patch.object(hosts, 'observe_writers', return_value={'claude': seen}) as observe, \
                        mock.patch.object(source, 'interactive', return_value=interactive), \
                        mock.patch.object(source, 'prepare_source', side_effect=AssertionError('no candidate before exit')), \
                        mock.patch.object(hosts, 'request_writer_termination', side_effect=AssertionError('no termination approved')), \
                        mock.patch.object(terminal, '_discard_previous_input',
                                          side_effect=OSError(expected) if input_failure == 'preparation' else None), \
                        mock.patch('builtins.input', return_value='n',
                                   side_effect=OSError(expected) if input_failure == 'read' else None) as read_input:
                    code, output = self.captured(cli.dispatch, options)
                self.assertEqual(code, 1, output)
                observe.assert_called_once()
                if input_failure == 'preparation':
                    read_input.assert_not_called()
                if seen.state == 'running':
                    for pid, name in seen.programs:
                        self.assertEqual(output.count(f'{name}: still running (PID: {pid})'), 1, output)
                log = report.state().log_file
                if verbose:
                    self.assertIsNone(log)
                    self.assertIn(expected, output)
                    self.assertEqual(set((self.home / '.ghost-alice/install').glob('*')), previous_logs)
                    continue
                self.assertIsNotNone(log, output)
                self.assertIn(str(log), output)
                self.assertIn(expected, log.read_text())
                if seen.state == 'running':
                    for pid, name in seen.programs:
                        self.assertEqual(log.read_text().count(f'{name}: still running (PID: {pid})'), 1)
                self.assertFalse(log.with_suffix('.events.jsonl').exists())
                self.assertFalse((self.home / '.ghost-alice/versions').exists())
                self.assertFalse((self.home / '.ghost-alice/installed-payload-v2.json').exists())

    def test_platform_requires_only_stdin_tty_and_reports_unavailable_input_as_failure(self):
        from installer import terminal
        with mock.patch.object(sys.stdin, 'isatty', return_value=True), \
                mock.patch.object(terminal, '_discard_previous_input'), \
                mock.patch('builtins.input', return_value='2'):
            selected, output = self.captured(cli.prompt_platform)
        self.assertEqual(selected, 'codex')
        self.assertIn('Enter number or name:', output)
        with mock.patch.object(sys.stdin, 'isatty', return_value=True), \
                mock.patch.object(terminal, '_discard_previous_input', side_effect=OSError('input unavailable')):
            output = self.expect_exit(1, cli.prompt_platform)
        self.assertIn('input unavailable', output)
        with mock.patch.object(sys.stdin, 'isatty', return_value=True), \
                mock.patch.object(terminal, '_discard_previous_input'), \
                mock.patch('builtins.input', side_effect=EOFError):
            self.expect_exit(1, cli.prompt_platform)

    def test_installer_ancestor_is_shown_and_blocks_shutdown_before_any_request(self):
        from installer import hosts
        seen = hosts.WriterObservation('running', pids=(11,), programs=((11, 'Claude'),), ancestor_pids=(11,))
        with mock.patch.object(hosts, 'observe_writers', return_value={'claude': seen}), \
                mock.patch.object(hosts, 'request_writer_termination', side_effect=AssertionError('ancestor cannot be stopped')), \
                mock.patch('builtins.input', side_effect=AssertionError('ancestor cannot be proposed')):
            result, output = self.captured(cli.confirm_host_shutdown, ['claude'], interactive=True)
        self.assertFalse(result)
        self.assertEqual(output.count('Claude: still running (PID: 11)'), 1, output)
        self.assertNotIn('Claude Code', output)
        self.assertIn('independent terminal', output)

    def test_windowless_program_is_left_for_manual_exit_while_supported_pid_is_requested(self):
        from installer import hosts
        current = [11, 12]
        requested = []
        displays = []
        def observe(platforms):
            return {'codex': hosts.WriterObservation('running' if current else 'absent', pids=tuple(current),
                programs=tuple((pid, 'Codex.exe' if pid == 11 else 'codex.exe') for pid in current),
                manual_pids=(12,) if 12 in current else ())}
        def answer():
            displays.append(sys.stdout.getvalue())
            if 11 in current:
                return 'y'
            current.clear()
            return ''
        def request(pids, *, macos_apps):
            requested.extend(pids)
            for pid in pids:
                current.remove(pid)
            return {}
        with mock.patch.object(hosts, 'observe_writers', side_effect=observe), \
                mock.patch.object(hosts, 'request_writer_termination', side_effect=request), \
                mock.patch('builtins.input', side_effect=answer), \
                mock.patch.object(cli.time, 'sleep'), \
                mock.patch.object(cli.time, 'monotonic', side_effect=[0, 6]):
            result, output = self.captured(cli.confirm_host_shutdown, ['codex'], interactive=True)
        self.assertTrue(result, output)
        self.assertEqual(requested, [11])
        status, question = displays[0].split('Programs for this termination request:')
        self.assertIn('Codex.exe: still running (PID: 11)', status)
        self.assertIn('codex.exe: still running (PID: 12)', status)
        self.assertEqual(question.count('Codex.exe: still running (PID: 11)'), 1, question)
        self.assertNotIn('codex.exe: still running (PID: 12)', question)
        self.assertIn('Close these programs yourself', output)

    @unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX terminal input boundary')
    def test_platform_question_discards_queued_selection(self):
        from installer_testing import installer_env, run_pty
        script = '''import select, sys
from installer import cli
print('BEFORE_PLATFORM_QUESTION', flush=True)
assert select.select([sys.stdin], [], [], 5)[0]
print('SELECTED=' + cli.prompt_platform(), flush=True)
'''
        env = installer_env(self.home)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parent)
        code, output = run_pty([sys.executable, '-c', script], env=env, cwd=self.home,
                              responses=[('BEFORE_PLATFORM_QUESTION', '1\n'), ('Enter number or name:', '2\n')],
                              timeout=10)
        self.assertEqual(code, 0, output)
        self.assertIn('SELECTED=codex', output)

    def test_enter_while_same_pid_remains_does_not_repeat_shutdown_consent(self):
        from installer import hosts
        running = {'codex': hosts.WriterObservation('running', pids=(43211,))}
        requested = []
        with mock.patch.object(hosts, 'observe_writers', return_value=running), \
                mock.patch.object(hosts, 'request_writer_termination', side_effect=lambda pids, **kwargs: requested.extend(pids) or {}), \
                mock.patch('builtins.input', side_effect=['y', '', 'n']), \
                mock.patch.object(cli.time, 'sleep'), \
                mock.patch.object(cli.time, 'monotonic', side_effect=[0, 6]):
            result, output = self.captured(cli.confirm_host_shutdown, ['codex'], interactive=True)
        self.assertFalse(result)
        self.assertEqual(requested, [43211])
        self.assertEqual(output.count('[y/N]'), 1, output)
        self.assertEqual(output.count('codex: still running (PID: 43211)'), 3, output)

    def test_only_displayed_still_present_pids_are_requested_and_new_pid_needs_consent(self):
        from installer import hosts
        current = [11, 12, 14]
        app_bundles = {14: 'com.openai.codex'}
        requested = []
        displays = []
        replies = iter(('y', 'yes', 'n'))
        def answer():
            displays.append(sys.stdout.getvalue())
            reply = next(replies)
            if reply == 'y':
                # The app moved from PID 14 to 13. PID 14 now belongs to a CLI.
                current[:] = [12, 13, 14]
                app_bundles.clear()
                app_bundles[13] = 'com.openai.codex'
            elif reply == 'yes':
                self.assertEqual(requested, [12])
            return reply
        def request(pids, *, macos_apps):
            requested.extend(pids)
            self.assertEqual(macos_apps, {13: 'com.openai.codex'} if 13 in pids else {})
            if 13 in pids:
                current.clear()
            return {}
        with mock.patch.object(hosts, 'observe_writers', side_effect=lambda platforms: {
                'codex': hosts.WriterObservation('running' if current else 'absent', pids=tuple(current),
                                                 macos_apps=app_bundles.copy())}), \
                mock.patch.object(hosts, 'request_writer_termination', side_effect=request), \
                mock.patch('builtins.input', side_effect=answer), \
                mock.patch.object(cli.time, 'sleep'), \
                mock.patch.object(cli.time, 'monotonic', side_effect=[0, 6, 7, 13]):
            result, output = self.captured(cli.confirm_host_shutdown, ['codex'], interactive=True)
        self.assertTrue(result, output)
        self.assertEqual(requested, [12, 13, 14])
        self.assertEqual(displays[0].count('codex: still running (PID: 11)'), 1, displays[0])
        self.assertEqual(displays[0].count('codex: still running (PID: 12)'), 1, displays[0])
        self.assertEqual(displays[0].count('[y/N]'), 1, displays[0])
        status, question = displays[1][len(displays[0]):].split('Programs for this termination request:')
        self.assertIn('codex: still running (PID: 12)', status)
        self.assertIn('codex: still running (PID: 13)', status)
        self.assertNotIn('codex: still running (PID: 12)', question)
        self.assertEqual(question.count('codex: still running (PID: 13)'), 1, question)
        self.assertEqual(question.count('codex: still running (PID: 14)'), 1, question)
        self.assertEqual(question.count('[y/N]'), 1, question)

    def test_shutdown_confirmation_precedes_candidate_changes_and_observes_exit(self):
        from installer import hosts, source
        absent = {'claude': hosts.WriterObservation('absent')}
        running = {'claude': hosts.WriterObservation('running', pids=(43210,))}
        self.stub('installer.source.check_install_source')
        self.stub('installer.hosts.confirm_project_trust', '')
        self.stub('installer.source.check_source_health', True)
        self.stub('installer.source.input_record', {'root': str(self.source)})
        self.stub('installer.lock.acquire')
        self.stub('installer.lock.release')
        marker = self.home / 'candidate-prepared'
        class Prepared(Exception):
            pass
        def prepare(*args, **kwargs):
            self.assertEqual(current['value'], absent)
            marker.write_text('after exit')
            raise Prepared()
        options = cli.Options(platform='claude', platform_explicit=True, addon_skip=True, args=['alpha'])
        for answer, interactive, cause in [('n', True, 'install'), ('', True, 'install'),
                                            (EOFError(), True, 'install'), ('y', False, 'install'),
                                            ('y', True, 'post-merge'), ('yes', True, 'update')]:
            with self.subTest(answer=answer, interactive=interactive, cause=cause):
                current = {'value': running}
                requested = []
                def request(pids, *, macos_apps):
                    requested.extend(pids)
                    self.assertFalse(marker.exists())
                    current['value'] = absent
                    return {}
                with mock.patch.dict(os.environ, {source.REQUEST_ENV: '', 'GHOST_ALICE_INSTALL_LOCALE': 'en'}), \
                        mock.patch.object(hosts, 'observe_writers', side_effect=lambda platforms: current['value']), \
                        mock.patch.object(hosts, 'request_writer_termination', side_effect=request), \
                        mock.patch.object(source, 'interactive', return_value=interactive), \
                        mock.patch.object(source, 'prepare_source', side_effect=prepare), \
                        mock.patch('builtins.input', side_effect=[answer]):
                    if cause == 'update':
                        with self.assertRaises(Prepared):
                            self.captured(cli.dispatch, options, execution_cause=cause)
                        self.assertEqual(marker.read_text(), 'after exit')
                        self.assertEqual(requested, [43210])
                        marker.unlink()
                    else:
                        code, output = self.captured(cli.dispatch, options, execution_cause=cause)
                        self.assertEqual(code, 1, output)
                        self.assertFalse(marker.exists())
                        self.assertEqual(requested, [])
                        self.assertEqual(output.count('claude: still running (PID: 43210)'), 1, output)

    def test_candidate_checks_selected_and_installed_hosts_before_lock_recovery_without_prompting(self):
        from installer import hosts, source
        record = {'targets': {'core': dict(kind='skill', provider='core', name='task-router', used_by=['claude'])}}
        detail = self.home / '.ghost-alice' / cli.installed_payload.LEGACY_FILENAME
        detail.parent.mkdir(exist_ok=True)
        detail.write_text(json.dumps(dict(schema_version=1, **record)))
        marker = self.home / 'recovery-started'
        def observe(platforms):
            self.assertEqual(set(platforms), {'claude', 'codex'})
            return {'claude': hosts.WriterObservation('running', pids=(43212,)),
                    'codex': hosts.WriterObservation('absent')}
        with mock.patch.object(source, 'install_request', return_value={'package_root': str(self.source)}), \
                mock.patch.object(hosts, 'observe_writers', side_effect=observe), \
                mock.patch.object(hosts, 'request_writer_termination', side_effect=AssertionError('candidate cannot ask to close apps')), \
                mock.patch.object(cli.lock, 'acquire', side_effect=lambda *args: marker.write_text('recovery')), \
                mock.patch('builtins.input', side_effect=AssertionError('candidate has no terminal')):
            code, output = self.captured(cli.dispatch, cli.Options(platform='codex', platform_explicit=True))
        self.assertEqual(code, 1, output)
        self.assertFalse(marker.exists())
        self.assertIn('43212', output)

    def test_unknown_observation_requires_a_successful_recheck_before_continuing(self):
        from installer import hosts
        with mock.patch.object(hosts, 'observe_writers', side_effect=[
                {'claude': hosts.WriterObservation('unknown', 'process lookup denied'),
                 'codex': hosts.WriterObservation('running', pids=(43214,))},
                {'claude': hosts.WriterObservation('absent'), 'codex': hosts.WriterObservation('absent')}]), \
                mock.patch.object(hosts, 'request_writer_termination', side_effect=AssertionError('no identified target')), \
                mock.patch('builtins.input', side_effect=['']):
            result, output = self.captured(cli.confirm_host_shutdown, ['claude', 'codex'], interactive=True)
        self.assertTrue(result)
        self.assertIn('process lookup denied', output)
        self.assertEqual(output.count('codex: still running (PID: 43214)'), 1, output)
        self.assertNotIn('[y/N]', output)

    def test_interrupted_other_host_publish_is_not_restored_while_that_host_runs(self):
        import install_lock
        from installer import hosts, locations, source
        (self.source / 'task-router').mkdir()
        (self.source / 'task-router/SKILL.md').write_text('fixture skill')
        for interruption in ('_write_record', 'finalize', 'corrupt-current', 'later-host'):
            with self.subTest(interruption=interruption):
                ghost = self.home / interruption / '.ghost-alice'
                target = ghost.parent / 'codex-skill'
                request = dict(schema_version=1, host='codex', ghost_root=str(ghost),
                    installer=dict(commit='test', schema_version=1),
                    source=dict(root=str(self.source), commit='test', dirty='clean', hooks_path='hooks'),
                    targets=[dict(name='task-router', kind='skill', provider='core', mode='copy',
                                  source=str(self.source / 'task-router'), dest=str(target), source_commit='test')])
                generation = install_lock.acquire_lock(ghost / 'install.lock', owner='fixture')
                try:
                    with mock.patch.object(cli.install_transaction.PublishBatch,
                                           '_write_record' if interruption == '_write_record' else 'finalize',
                                           side_effect=KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            cli.install_transaction.publish(request)
                    if interruption == 'later-host':
                        cli.install_transaction.publish(dict(request, host='claude', targets=[
                            dict(request['targets'][0], dest=str(ghost.parent / 'claude-skill'))]))
                finally:
                    install_lock.release_lock(ghost / 'install.lock', generation)
                if interruption == 'corrupt-current':
                    cli.installed_payload.path(ghost).write_text('{interrupted record')
                before = (target / 'SKILL.md').read_bytes()
                pending = cli.install_transaction.unfinished(ghost)
                running_host = 'claude' if interruption == 'later-host' else 'codex'
                def observe(platforms):
                    return {host: hosts.WriterObservation('running', pids=(43213,)) if host == running_host
                            else hosts.WriterObservation('absent') for host in platforms}
                options = cli.Options(platform='codex' if interruption == 'later-host' else 'claude', platform_explicit=True)
                with mock.patch.object(locations, 'record_root', return_value=ghost), \
                        mock.patch.object(source, 'install_request', return_value={'package_root': str(self.source),
                                          'source': request['source']}), \
                        mock.patch.object(hosts, 'observe_writers', side_effect=observe) as observer, \
                        mock.patch.object(cli.transaction, 'activate'), \
                        mock.patch.object(cli, '_dispatch', return_value=0), \
                        mock.patch.object(source, 'finalize_source', return_value=[]):
                    code, output = self.captured(cli.dispatch, options)
                    self.assertEqual(code, 1, output)
                    self.assertEqual((target / 'SKILL.md').read_bytes(), before)
                    self.assertEqual(cli.install_transaction.unfinished(ghost), pending)
                    observer.side_effect = lambda platforms: {host: hosts.WriterObservation('absent') for host in platforms}
                    code, output = self.captured(cli.dispatch, options)
                    self.assertEqual(code, 0, output)
                    self.assertFalse(target.exists(), 'Recovery may restore the interrupted publish after host exit')
                    self.assertEqual(cli.install_transaction.unfinished(ghost), [])

    def prepared_package(self) -> Path:
        """The package that the installing process prepared; the test replaces the processes it starts."""
        package = (self.home / '.ghost-alice/versions' / ('c' * 64)).resolve()
        (package / 'core').mkdir(parents=True)
        self.stub('installer.hosts.observe_writers', {'claude': cli.hosts.WriterObservation('absent')})
        self.stub('installer.source.check_install_source')
        self.stub('installer.hosts.confirm_project_trust', '')
        self.stub('installer.source.check_source_health', True)
        self.stub('installer.prerequisites.install_prerequisites', True)
        self.stub('installer.source.prepare_source', (package, {}))
        self.stub('installer.source.input_record', {'root': str(self.source), 'commit': 'installing'})
        self.stub('installer.lock.acquire')
        self.stub('installer.lock.release')
        return package

    @staticmethod
    def install_options() -> cli.Options:
        return cli.Options(platform='claude', platform_explicit=True, addon_skip=True, args=['alpha'])

    def test_missing_programs_are_installed_after_the_host_check_and_before_the_package_is_prepared(self):
        self.prepared_package()
        steps = ('input_record', 'check_source_health', 'observe_writers', 'install_prerequisites', 'acquire', 'prepare_source')
        with mock.patch.dict(os.environ, {'GHOST_ALICE_PACKAGE_REQUEST': ''}), \
                mock.patch.object(runner, 'run_command', return_value=ToolResult(6)):
            self.captured(cli.dispatch, self.install_options())
        self.assertEqual([name for name in self.names() if name in steps], list(steps))
        self.stub('installer.hosts.observe_writers', {'claude': cli.hosts.WriterObservation('running', pids=(43211,))})
        self.calls.clear()
        with mock.patch.dict(os.environ, {'GHOST_ALICE_PACKAGE_REQUEST': ''}):
            code, _ = self.captured(cli.dispatch, self.install_options())
        self.assertEqual((code, 'install_prerequisites' in self.names()), (1, False))

    def test_a_saved_package_that_lost_a_file_names_the_file_and_not_its_skill(self):
        package = (self.home / '.ghost-alice/versions' / ('d' * 64)).resolve()
        self.stub('installer.source.check_install_source')
        (package / 'core').mkdir(parents=True)
        self.stub('installer.source._version_metadata', {'files': {'core/alpha/SKILL.md': 'e' * 64}})
        before = self.home_files()
        with mock.patch.dict(os.environ, {'GHOST_ALICE_PACKAGE_REQUEST': ''}), \
                mock.patch.object(runner, 'REPO_ROOT', package / 'core'):
            code, output = self.captured(cli.main, ['--platform', 'claude', '--local-source', 'alpha'])
        self.assertEqual((code, self.home_files()), (1, before), output)
        self.assertIn(f'[ERROR] Preserved source version is incomplete: {package / "core/alpha"}', output)
        self.assertNotIn('Skill not found', output)

    def test_a_successful_candidate_hands_the_terminal_to_its_own_package_and_no_record_is_read_again(self):
        package = self.prepared_package()
        core = package / 'core'
        record, version = package.parents[1] / cli.installed_payload.FILENAME, package / 'version.json'
        record_path = cli.install_transaction.record_path_after_recovery
        for cause in ('install', 'post-merge', 'update'):
            with self.subTest(cause=cause):
                report.configure(home=self.home, tty=False)
                # Another package is the active installer before the candidate runs.
                record.write_text(json.dumps({'schema_version': 2, 'targets': {}, 'source': {'root': str(self.source)},
                                              'installer': {'active_root': str(self.home / 'earlier/core')}}))
                calls = []

                def run(argv, **kwargs):
                    calls.append(([str(value) for value in argv], kwargs))
                    # What the candidate leaves is read by its own package only.
                    for file in (record, version):
                        file.write_text('{the form that the candidate reads')
                    return ToolResult(0 if len(calls) == 1 else 9)

                def before_the_candidate(root):
                    self.assertEqual(calls, [], 'the installing process reads the record before the candidate runs')
                    return record_path(root)

                self.calls.clear()
                with mock.patch.dict(os.environ, {'GHOST_ALICE_PACKAGE_REQUEST': ''}), \
                        mock.patch.object(runner, 'run_command', side_effect=run), \
                        mock.patch.object(cli.install_transaction, 'record_path_after_recovery',
                                          side_effect=before_the_candidate):
                    code, output = self.captured(cli.dispatch, self.install_options(), execution_cause=cause)
                self.assertEqual(code, 9, output)
                self.assertNotIn('The installation did not finish', output)
                (candidate, sent), (followup, handed) = calls
                self.assertEqual(Path(candidate[1]), core / '_shared/installer/cli.py')
                self.assertEqual([followup[0], *map(Path, followup[1:])], [sys.executable, core / '_shared/installer/cli.py'])
                self.assertTrue(handed['inherit_stdin'])
                for kwargs in (sent, handed):
                    self.assertIn('GHOST_ALICE_PACKAGE_REQUEST', kwargs['env'])
                    self.assertNotIn('GHOST_ALICE_INSTALL_REQUEST', kwargs['env'])
                common = dict(package_root=str(core), source={'root': str(self.source), 'commit': 'installing'},
                              log_file=str(report.state().log_file))
                self.assertEqual(json.loads(sent['env']['GHOST_ALICE_PACKAGE_REQUEST']),
                                 dict(common, addon_commits={}, start_registration=dict(root=str(self.source),
                                      remote=source.DEFAULT_REMOTE), **({'execution_cause': cause} if cause != 'install' else {})))
                move = dict(common, stage='record-move', execution_cause=cause)
                self.assertEqual(json.loads(handed['env']['GHOST_ALICE_PACKAGE_REQUEST']), move)
                with mock.patch.dict(os.environ, handed['env']), mock.patch.object(runner, 'REPO_ROOT', core):
                    self.assertEqual(source.install_request(), move)

    def test_an_unfinished_installation_names_the_command_that_continues_it_and_the_active_installer(self):
        package = self.prepared_package()
        core = package / 'core'
        earlier = (self.home / 'earlier/core').resolve()
        record = package.parents[1] / cli.installed_payload.FILENAME
        command = locations.management_command()
        again = {'en': 'run the same command again', 'ko': '같은 명령을 다시 실행'}
        for language in ('en', 'ko'):
            for cause in ('install', 'update', 'post-merge'):
                for active in (core, earlier, None):
                    with self.subTest(language=language, cause=cause, active=active), \
                            mock.patch.dict(os.environ, {'GHOST_ALICE_PACKAGE_REQUEST': '',
                                                         'GHOST_ALICE_INSTALL_LOCALE': language}):
                        report.configure(home=self.home, tty=False)
                        record.unlink(missing_ok=True)
                        started = []

                        def run(argv, **kwargs):
                            started.append(argv)
                            if active is not None:
                                record.write_text(json.dumps({'schema_version': 2, 'targets': {},
                                                              'installer': {'active_root': str(active)}}))
                            return ToolResult(6)

                        with mock.patch.object(runner, 'run_command', side_effect=run):
                            code, output = self.captured(cli.dispatch, self.install_options(), execution_cause=cause)
                        self.assertEqual((code, len(started)), (6, 1), output)
                        self.assertEqual(again[language] in output, cause != 'post-merge', output)
                        self.assertEqual(command in output, cause == 'post-merge', output)
                        # The plain command of another active package installs that package again.
                        self.assertEqual(command + ' update' in output, cause == 'post-merge' and active != core, output)
                        self.assertNotIn('--local-source', output)
                        for path in (core, earlier):
                            self.assertEqual(str(path) in output, path == active, output)
                        self.assertEqual(bool(re.search('[\uac00-\ud7a3]', output)), language == 'ko', output)


class CandidateInstallTests(CliCase):
    """The candidate process under the install lock: its package becomes the active installer before a host child
    starts, and its success is recorded after the last one."""

    def setUp(self):
        super().setUp()
        self.core = (self.home / '.ghost-alice/versions' / ('c' * 64) / 'core').resolve()
        self.core.mkdir(parents=True)
        self.patches.enter_context(mock.patch('installer.runner.REPO_ROOT', self.core))
        self.stub('installer.hosts.observe_writers', {'claude': cli.hosts.WriterObservation('absent')})
        self.stub('installer.lock.acquire', 'handle')
        self.stub('installer.lock.release')
        self.stub('installer.source.match_install_source', source.SourceMatch('same', self.source, self.source))
        self.stub('installer.prerequisites.install_prerequisites', True)
        self.stub('installer.transaction.activate')
        self.stub('installer.cli._dispatch', 0)
        self.stub('installer.source.finalize_source', ['the version is complete'])

    STEPS = ('install_prerequisites', 'acquire', 'match_install_source', 'activate', '_dispatch', 'finalize_source', 'release')

    def candidate(self, *arguments, **fields):
        self.calls.clear()
        request = dict(package_root=str(self.core), source={'root': str(self.source), 'commit': 'c0ffee'},
                       addon_commits={}, **fields)
        with mock.patch.dict(os.environ, {'GHOST_ALICE_PACKAGE_REQUEST': json.dumps(request)}):
            code, output = self.captured(cli.main, ['--platform', 'claude', *arguments, 'task-router'])
        return code, output, [name for name in self.names() if name in self.STEPS]

    def test_the_package_is_activated_before_the_host_children_and_completed_after_them(self):
        code, output, steps = self.candidate()
        self.assertEqual((code, steps), (0, list(self.STEPS[1:])), output)
        arguments = {name: (args, kwargs) for name, args, kwargs in self.calls}
        self.assertEqual(arguments['acquire'], (('install', 'claude'), {}))
        ((context, choices), _), ((options,), _) = arguments['activate'], arguments['_dispatch']
        self.assertEqual((context.platform, options.args, choices), ('claude', ['task-router'], {'claude': ['task-router']}))
        self.assertEqual(arguments['finalize_source'], ((context, self.core.parent, ['claude']), {}))
        self.assertEqual(arguments['release'], (('handle',), {}))
        self.assertIn('the version is complete', output)

    def test_the_recorded_source_is_compared_under_the_lock_before_activation(self):
        others = {'recorded': self.source, 'gone': self.home / 'no-such-folder'}
        for state, arguments, recorded, code, adopts in (
                ('same', [], 'recorded', 0, False), ('other', [], 'recorded', 2, False),
                ('other', ['--adopt-source'], 'recorded', 0, True), ('other', [], 'gone', 0, True),
                # The installation record that activation creates can name another source.
                ('unrecorded', [], 'recorded', 0, True), ('unreadable', [], 'recorded', 0, False)):
            with self.subTest(state=state, arguments=arguments, recorded=recorded):
                self.stub('installer.source.match_install_source',
                          source.SourceMatch(state, self.home, others[recorded], detail='cause'))
                result, output, steps = self.candidate(*arguments)
                following = ['activate', '_dispatch', 'finalize_source'] if code == 0 else []
                self.assertEqual((result, steps), (code, ['acquire', 'match_install_source', *following, 'release']), output)
                if following:
                    ((context, _), _), ((options,), _) = (next(call[1:] for call in self.calls if call[0] == name)
                                                          for name in ('activate', '_dispatch'))
                    self.assertEqual((context.options.adopt_source, options.adopt_source), (adopts, adopts))
                else:
                    self.assertIn(str(self.source), output)

    def test_a_merged_source_is_compared_under_the_lock_before_activation(self):
        for state, following in (('same', ['activate', '_dispatch', 'finalize_source']),
                                 ('other', []), ('unrecorded', [])):
            with self.subTest(state=state):
                self.stub('installer.source.match_install_source', source.SourceMatch(state, self.source, self.source))
                code, output, steps = self.candidate(execution_cause='post-merge')
                self.assertEqual(steps, ['acquire', 'match_install_source', *following, 'release'], output)
                self.assertEqual(code, 0 if following else 1, output)

    def test_record_that_became_unreadable_under_the_lock_names_its_read_error(self):
        record = self.home / '.ghost-alice' / cli.installed_payload.FILENAME
        self.stub('installer.lock.acquire', side_effect=lambda *args: record.write_text('{broken'))
        code, output, steps = self.candidate(start_registration=None)
        self.assertEqual(code, 1, output)
        self.assertIn(f'{record} is not JSON', output)
        self.assertNotIn('activate', steps)

    def test_merge_keeps_a_remote_registered_while_upgrading_a_record_without_one(self):
        record = self.home / '.ghost-alice' / cli.installed_payload.FILENAME
        old_source = {'root': str(self.source)}
        record.write_text(json.dumps(dict(schema_version=2, targets={}, source=old_source)))
        original_registration = source.registration({'source': old_source})
        new_source = dict(old_source, remote=source.DEFAULT_REMOTE)
        self.stub('installer.lock.acquire', side_effect=lambda *args: record.write_text(
            json.dumps(dict(schema_version=2, targets={}, source=new_source))))
        request = dict(package_root=str(self.core), source=dict(old_source, remote='/local/checkout-origin'),
                       execution_cause='post-merge', start_registration=original_registration)
        with mock.patch.dict(os.environ, {source.REQUEST_ENV: json.dumps(request)}):
            code, output = self.captured(cli.main, ['--platform', 'claude'])
        self.assertEqual(code, 1, output)
        self.assertIn('registered checkout or remote changed', output)
        self.assertNotIn('activate', self.names())

    def test_an_activation_that_fails_starts_no_host_child_and_releases_the_lock(self):
        def stop(context, choices):
            raise ValueError('the Git setting cannot be written')

        self.stub('installer.transaction.activate', side_effect=stop)
        code, output, steps = self.candidate()
        self.assertEqual((code, steps), (1, ['acquire', 'match_install_source', 'activate', 'release']), output)
        self.assertIn('[ERROR] the Git setting cannot be written', output)

    def test_an_unreadable_addon_registration_prevents_activation(self):
        directory = cli.locations.addons_dir('claude')
        directory.mkdir(parents=True)
        sidecar = directory / 'one.json'
        sidecar.write_text('truncated', encoding='utf-8')
        code, output, steps = self.candidate(held_addons=True)
        self.assertEqual(code, 1, output)
        self.assertIn(str(sidecar), output)
        self.assertEqual(steps, ['acquire', 'match_install_source', 'release'])

    def test_a_host_child_that_fails_leaves_the_version_unfinished(self):
        self.stub('installer.cli._dispatch', 4)
        code, output, steps = self.candidate()
        self.assertEqual((code, steps), (4, ['acquire', 'match_install_source', 'activate', '_dispatch', 'release']), output)

    def test_a_host_child_neither_activates_nor_locks_for_the_whole_install(self):
        with mock.patch.dict(os.environ, {'GHOST_ALICE_INSTALL_HOST_CHILD': '1'}):
            code, output, steps = self.candidate()
        self.assertEqual((code, steps), (0, ['_dispatch']), output)


class EarlierRequestTests(CliCase):
    """A process that an earlier release starts with its own request changes nothing and names this package's install
    file, whatever the request holds and whatever the arguments are."""

    ARGUMENTS = ([], ['--platform', 'claude'], ['--status'], ['--platform', 'claude', '--status'], ['--uninstall'],
                 ['update'], ['--management-entry'], ['--management-entry', 'update'], ['--post-merge'],
                 ['--install-acquired-release', '/folder', 'remote'],
                 ['--install-acquired-update', '/folder', 'remote', 'main', 'update'],
                 ['--isolated'], ['--platform'], ['--bogus'])

    def setUp(self):
        super().setUp()
        package = str(self.source.resolve())
        install = {'package_root': package, 'source': {'root': package}, 'addon_commits': {}}
        self.requests = {'install': json.dumps(install),
                         'record move': json.dumps(dict(install, stage='record-move', execution_cause='install')),
                         'invalid JSON': '{not JSON'}
        self.command = cli.locations.package_command(self.source)
        ghost = self.home / '.ghost-alice'
        ghost.mkdir(exist_ok=True)
        (ghost / cli.installed_payload.FILENAME).write_text('{not JSON either')
        for target in ('installer.cli.install_release', 'installer.cli.dispatch', 'installer.runner.run_command',
                       'installer.runner.popen', 'installer.lock.acquire', 'installer.source.acquire_release',
                       'installer.report.log_init', 'installed_payload.read_file',
                       'install_transaction.record_path_after_recovery'):
            self.patches.enter_context(mock.patch(target, side_effect=AssertionError(f'{target} has no part in a refusal')))

    def refused(self, request, argv, language='en'):
        with mock.patch.dict(os.environ, {'GHOST_ALICE_INSTALL_REQUEST': request, 'GHOST_ALICE_INSTALL_LOCALE': language}):
            code, output = self.captured(cli.main, argv)
        self.assertEqual(code, 1, output)
        self.assertTrue(output.startswith('[ERROR] '), output)
        self.assertIn(self.command, output)
        return output

    def test_every_command_stops_before_any_read_or_change(self):
        before = self.home_files()
        for name, request in self.requests.items():
            for argv in self.ARGUMENTS:
                with self.subTest(request=name, argv=argv):
                    self.refused(request, argv)
                    self.assertEqual(self.home_files(), before)

    def test_the_refusal_follows_the_display_language(self):
        english, korean = (self.refused(self.requests['install'], [], language) for language in ('en', 'ko'))
        self.assertNotRegex(english, '[\uac00-\ud7a3]')
        self.assertRegex(korean, '[\uac00-\ud7a3]')

    def test_an_empty_earlier_request_is_no_request(self):
        with mock.patch.dict(os.environ, {'GHOST_ALICE_INSTALL_REQUEST': ''}), \
                mock.patch('installer.cli.install_release', return_value=23) as install:
            code, output = self.captured(cli.main, ['--platform', 'claude'])
        self.assertEqual(code, 23, output)
        install.assert_called_once_with(runner.REPO_ROOT, ['--platform', 'claude'])


REMOTE = 'https://example.invalid/product.git'


class ReleaseCase(CliCase):
    """Commands that install the remote main branch, with the Git settings of the fixture only."""

    def setUp(self):
        super().setUp()
        os.environ.update(GIT_CONFIG_GLOBAL=str(self.home / '.gitconfig'), GIT_CONFIG_NOSYSTEM='1')
        self.ghost = self.home / '.ghost-alice'
        self.record = self.ghost / cli.installed_payload.FILENAME

    def repository(self, root: Path, *, remote: str | None = None) -> str:
        """Commit every file under `root` in its own repository; the commit."""
        settings = ('-c', 'user.name=t', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false',
                    '-c', 'core.hooksPath=/dev/null', '-c', 'core.autocrlf=false', '-c', 'init.defaultBranch=main')
        if not (root / '.git').exists():
            git('-C', str(root), *settings, 'init', '-q', check=True)
            git('-C', str(root), 'config', 'maintenance.auto', 'false', check=True)
        if remote is not None:
            git('-C', str(root), 'remote', 'add', 'origin', remote, check=True)
        git('-C', str(root), *settings, 'add', '-A', check=True)
        git('-C', str(root), *settings, 'commit', '-q', '--allow-empty', '-m', 'fixture', check=True)
        return git('-C', str(root), 'rev-parse', 'HEAD', check=True).stdout.strip()

    def write_record(self, *, active: Path | None = None, skills: dict[str, list[str]] | None = None, **fields) -> None:
        """The install record: its source fields, the active installer and the Core skills of each host."""
        targets = {str(self.home / host / name): dict(kind='skill', provider='core', name=name, used_by=[host])
                   for host, names in (skills or {}).items() for name in names}
        self.record.parent.mkdir(exist_ok=True)
        self.record.write_text(json.dumps(dict(schema_version=2, targets=targets, source=fields,
                                               installer={'active_root': str(active)} if active else {})))

    def saved_package(self, *, status: str, selections: dict[str, list[str]], root: Path, remote: str) -> Path:
        """A preserved package of the fixture source, with the facts its version file holds."""
        (self.source / 'VERSION').write_text('saved\n')
        with use_source(self.source):
            folder, _ = source.prepare_source(make_context(self.home), selections=selections, provenance=dict(
                root=str(root), remote=remote, ref='refs/heads/main', branch='main', commit='c0ffee', dirty='clean'))
        version = folder / 'version.json'
        version.write_text(json.dumps(dict(json.loads(version.read_text()), status=status)))
        return folder / 'core'


class InstallReleaseTests(ReleaseCase):
    """The running installer downloads the release, starts the downloaded installer and returns its exit code."""

    def setUp(self):
        super().setUp()
        self.release = (self.home.parent / 'downloaded').resolve()
        self.steps: list = []
        self.started = False
        self.stub('installer.prerequisites.require_git_for_release',
                  side_effect=lambda: self.steps.append('Git is available'))

        @contextlib.contextmanager
        def acquire_release(remote):
            self.steps.append(('download', remote))
            yield self.release
            self.steps.append('discard the download')

        self.patches.enter_context(mock.patch('installer.source.acquire_release', acquire_release))
        run_command, read_file = runner.run_command, cli.installed_payload.read_file

        def run(argv, **kwargs):
            if argv[0] != sys.executable:
                return run_command(argv, **kwargs)
            self.started = True
            self.steps.append(([str(value) for value in argv], kwargs))
            # The release owns the record from here on and may keep it in a form that only it reads.
            self.ghost.mkdir(exist_ok=True)
            self.record.write_text('{the form that the release reads')
            return ToolResult(self.child_code)

        def read_before_the_release(file):
            self.assertFalse(self.started, 'the running installer reads no record once the release has started')
            return read_file(file)

        self.patches.enter_context(mock.patch('installer.runner.run_command', side_effect=run))
        self.patches.enter_context(mock.patch('installed_payload.read_file', side_effect=read_before_the_release))

    def run_installer(self, argv, *, child_code=0):
        self.steps.clear()
        self.started, self.child_code = False, child_code
        with contextlib.ExitStack() as patches:
            for target in ('installer.lock.acquire', 'installer.source.prepare_source', 'installer.hosts.observe_writers',
                           'installer.terminal.read_answer', 'installer.report.log_init'):
                patches.enter_context(mock.patch(target, side_effect=AssertionError(f'{target} belongs to the release')))
            return self.captured(cli.main, argv)

    def installed(self, argv, *, root, remote, expected=None, code=0, origin=None):
        """Run `argv` and check that the release received `expected` (the same arguments when not given) and `origin`
        (the source of `root` when not given)."""
        self.started = False
        # A command other than an update adds the registration that it read before the download.
        if origin is None:
            try:
                record = cli.install_transaction.record_after_recovery(self.ghost)
            except cli.installed_payload.RecordError:
                record = None
            origin = dict(source.input_record(root, record=record), registered=source.registration(record))
        result, output = self.run_installer(argv, child_code=code)
        self.assertEqual(result, code, output)
        self.assertEqual(self.steps, [
            'Git is available', ('download', remote),
            ([sys.executable, str(self.release / '_shared/installer/cli.py'), '--install-acquired-release', str(root), remote,
              *(argv if expected is None else expected)],
             {'inherit_stdin': True, 'env': {cli.START_ORIGIN_ENV: json.dumps(origin)}}),
            'discard the download'])

    def test_the_release_receives_the_folder_the_remote_the_arguments_and_the_terminal(self):
        arguments = ['--platform', 'claude', '--visibility', 'strict', '--addon-source', 'relative/addons', '--verbose',
                     'task-router', 'alpha']
        # An unpacked archive has no remote of its own.
        for argv in ([], arguments):
            with self.subTest(argv=argv):
                self.installed(argv, root=runner.REPO_ROOT, remote=source.DEFAULT_REMOTE)
        self.repository(self.source, remote=REMOTE)
        for code in (0, 2, 37):
            with self.subTest(code=code):
                self.installed(arguments, root=runner.REPO_ROOT, remote=REMOTE, code=code)

    def test_a_saved_package_names_its_own_folder_and_the_recorded_remote(self):
        core = self.saved_package(status='candidate', selections={'claude': ['alpha']}, root=self.source,
                                  remote='https://example.invalid/saved.git')
        with use_source(core):
            self.installed([], root=core, remote='https://example.invalid/saved.git')
            self.write_record(root=str(self.source), remote=REMOTE)
            self.installed(['--platform', 'codex'], root=core, remote=REMOTE)

    def test_a_merge_in_the_install_source_starts_the_release_for_the_merged_checkout(self):
        self.repository(self.source, remote=REMOTE)
        merged = self.source.resolve()
        with use_source(self.saved_package(status='successful', selections={}, root=merged, remote='unused')):
            for saved, expected, code in ((None, REMOTE, 0), ('https://example.invalid/recorded.git',
                                         'https://example.invalid/recorded.git', 37)):
                with self.subTest(saved=saved, code=code):
                    self.write_record(root=str(merged), **({'remote': saved} if saved else {}))
                    self.installed(['--post-merge', '--merged-checkout', str(merged)], root=merged,
                                   remote=expected,
                                   expected=['--post-merge'], code=code)

    def test_update_passes_its_own_arguments_and_the_remote_it_read(self):
        core = self.saved_package(status='successful', selections={}, root=self.source, remote='https://example.invalid/saved.git')
        for argv, recorded, remote in (
                (['update'], REMOTE, REMOTE),
                (['update'], None, source.DEFAULT_REMOTE),
                (['update', '--ref', 'main'], REMOTE, REMOTE),
                (['update', '--ref', 'refs/heads/main', '--remote', '/another/remote'], REMOTE, '/another/remote'),
                # The release owns the help, the adapter names and the install options.
                (['update', '--help'], REMOTE, REMOTE),
                (['update', '-p', 'next-adapter', '--new-adapter-option', 'value', '--verbose'], REMOTE, REMOTE)):
            with self.subTest(argv=argv, recorded=recorded), use_source(core):
                self.write_record(root=str(self.source), **({'remote': recorded} if recorded else {}))
                self.installed(argv, root=core, remote=remote, code=37,
                               origin=dict(root=str(self.source), remote=recorded or source.DEFAULT_REMOTE))

    def test_the_childs_exit_code_does_not_establish_the_age_of_its_release(self):
        code, output = self.run_installer([], child_code=37)
        self.assertEqual(code, 37, output)
        self.assertNotIn('older than this installer', output)

    def test_a_saved_package_passes_the_source_that_it_read_before_the_download(self):
        core = self.saved_package(status='successful', selections={}, root=self.source, remote='https://example.invalid/saved.git')
        acquire_release = source.acquire_release

        @contextlib.contextmanager
        def switch_during_the_download(remote):
            with acquire_release(remote) as release:
                self.write_record(root=str(self.home), remote='/another/remote')
                yield release

        with use_source(core), mock.patch('installer.source.acquire_release', switch_during_the_download):
            for argv, origin in (([], None), (['update'], dict(root=str(self.source), remote=REMOTE))):
                with self.subTest(argv=argv):
                    self.write_record(root=str(self.source), remote=REMOTE)
                    self.installed(argv, root=core, remote=REMOTE, origin=origin)

    def test_an_update_that_names_other_code_or_finds_no_recorded_source_downloads_nothing(self):
        for argv, recorded in ((['update', '--ref', 'feature'], {'root': str(self.source)}),
                               (['update', '--local-source'], {'root': str(self.source)}),
                               (['update'], {}), (['update'], None)):
            with self.subTest(argv=argv, recorded=recorded):
                self.record.unlink(missing_ok=True)
                if recorded is not None:
                    self.write_record(**recorded)
                code, output = self.run_installer(argv)
                self.assertEqual((code, self.steps), (1, []), output)
                self.assertIn('[ERROR]', output)


class ManagementEntryTests(ReleaseCase):
    """The installed command starts the active installer with its own arguments."""

    def started(self, *argv):
        """The argv of the installer that the entry starts."""
        with mock.patch('installer.runner.run_command', return_value=ToolResult(7)) as run:
            code, output = self.captured(cli.main, ['--management-entry', *argv])
        self.assertEqual(code, 7, output)
        ((command,), _), = run.call_args_list
        return command

    def test_the_install_record_names_the_active_installer_also_when_a_journal_cannot_be_read(self):
        entry = self.home / 'later/core/_shared/installer/cli.py'
        entry.parent.mkdir(parents=True)
        entry.touch()
        self.write_record(active=entry.parents[2])
        # A later release wrote the journal.
        journal = self.ghost / 'rollbacks/20261020T000000Z-1-aaaaaaaa/journal.json'
        journal.parent.mkdir(parents=True)
        journal.write_text(json.dumps({'schema_version': 2, 'phase': 'prepare'}))
        self.assertEqual(self.started('--status'), [sys.executable, str(entry), '--status'])
        # Recovery restores the record that names the active installer.
        for content in (None, '{broken', json.dumps({'schema_version': 2, 'targets': {}})):
            with self.subTest(record=content), mock.patch.object(
                    cli.install_transaction, 'record_after_recovery',
                    return_value={'installer': {'active_root': str(entry.parents[2])}}):
                self.record.unlink(missing_ok=True)
                if content is not None:
                    self.record.write_text(content)
                self.assertEqual(self.started(), [sys.executable, str(entry)])


class RepairOwnerTests(ReleaseCase):
    """The active installer runs a repair. Another installer starts it with the same arguments and returns its exit code."""

    ARGV = ['--repair', '--platform', 'claude']

    def setUp(self):
        super().setUp()
        (self.home / '.claude').mkdir()
        self.active = self.saved_package(status='successful', selections={'claude': ['alpha']}, root=self.source, remote=REMOTE)
        self.state_commands(['--repair'])
        self.write_record(active=self.active, skills={'claude': ['alpha']}, root=str(self.source), remote=REMOTE)
        # The checkout holds another release than the active installer.
        (self.source / 'VERSION').write_text('another release\n')
        self.stub('installer.lifecycle.run_repair', 5)
        self.stub('installer.lock.acquire', (self.home / 'lock', 'generation'))
        self.stub('installer.lock.release')
        self.started: list = []
        run_command = runner.run_command

        def run(argv, **kwargs):
            if argv[0] != sys.executable:
                return run_command(argv, **kwargs)
            self.started.append(([str(value) for value in argv], kwargs))
            return ToolResult(37)

        self.patches.enter_context(mock.patch('installer.runner.run_command', side_effect=run))

    def state_commands(self, commands):
        """The commands that the active package states its installer runs for another installer; none when None."""
        version = self.active.parent / 'version.json'
        saved = json.loads(version.read_text())
        saved.pop('active_runs', None)
        version.write_text(json.dumps(saved if commands is None else dict(saved, active_runs=commands)))

    def repair(self):
        self.calls.clear()
        self.started.clear()
        return self.captured(cli.main, list(self.ARGV))

    def assert_own_repair(self):
        code, output = self.repair()
        self.assertEqual((code, self.started), (5, []), output)
        self.assertEqual(self.names(), ['acquire', 'run_repair', 'release'])

    def test_another_installer_starts_the_active_installer_and_returns_its_exit_code(self):
        entry = [sys.executable, str(self.active / '_shared/installer/cli.py')]
        (self.home / '.codex').mkdir()
        addon = self.home / 'addon'
        addon.mkdir()
        options = ['--skip-source-health', '--visibility', 'strict', '--verbose', '--addon-source', str(addon), '--addon-tag', 'v1']
        # Like the ghost-alice command, the handover starts the active installer outside any install request.
        started = {'inherit_stdin': True, 'env': {source.REQUEST_ENV: '', 'GHOST_ALICE_INSTALL_HOST_CHILD': '0'}}
        # Claude is the host of a repair that names none; the options go on unchanged.
        for argv, passed in ((['--repair'], ['--platform', 'claude', '--repair']),
                             (['--repair', '--platform', 'codex', *options], ['--platform', 'codex', *options, '--repair']),
                             (['--repair', '--addon-skip'], ['--platform', 'claude', '--addon-skip', '--repair'])):
            with self.subTest(argv=argv), mock.patch.object(self, 'ARGV', argv):
                code, output = self.repair()
                self.assertEqual(code, 37, output)
                self.assertEqual(self.started, [([*entry, *passed], started)])
                self.assertEqual(self.names(), [])
        with self.subTest('a saved package that is not active'):
            (self.source / 'VERSION').write_text('an earlier saved release\n')
            with use_source(self.source):
                folder, _ = source.prepare_source(make_context(self.home), selections={}, provenance=dict(
                    root=str(self.source), remote=REMOTE, ref='refs/heads/main', branch='main', commit='c0ffee', dirty='clean'))
            with use_source(folder / 'core'):
                code, output = self.repair()
            self.assertEqual((code, self.started), (37, [([*entry, '--platform', 'claude', '--repair'], started)]), output)

    def test_a_release_that_states_no_repair_for_another_installer_gets_none(self):
        # An earlier release restores every skill of its package; this installer keeps its own repair and its stop.
        for commands in (None, [], ['--status']):
            with self.subTest(commands=commands):
                self.state_commands(commands)
                self.assert_own_repair()

    def test_the_active_installer_names_the_source_that_recovery_restores(self):
        # A publish that stopped halfway wrote its own source into the record; the source check reads the restored one.
        self.write_record(active=self.active, skills={'claude': ['alpha']}, root=str(self.home / 'halfway'), remote='/halfway')
        restored = {'source': {'root': str(self.source), 'remote': REMOTE}}
        with mock.patch.object(cli.install_transaction, 'record_after_recovery', return_value=restored):
            origin = source.input_record(self.active)
        self.assertEqual((origin['root'], origin['remote']), (str(self.source), REMOTE))
        # A later release wrote a journal that this package cannot read: the package names the source that it saved.
        unreadable = cli.install_transaction.InstallTransactionError('journal of a later release')
        with mock.patch.object(cli.install_transaction, 'record_after_recovery', side_effect=unreadable):
            self.assertEqual(source.input_record(self.active)['root'], str(self.source))

    def test_the_active_installer_repairs_by_itself(self):
        with use_source(self.active):
            self.assert_own_repair()

    def test_an_installer_repairs_by_itself_while_the_active_package_cannot_run(self):
        with self.subTest('a file of the active package is missing'):
            (self.active / 'VERSION').unlink()
            self.assert_own_repair()
        with self.subTest('no active package'):
            shutil.rmtree(self.active)
            self.assert_own_repair()
        with self.subTest('an installation of an earlier installer names no active package'):
            self.write_record(skills={'claude': ['alpha']}, root=str(self.source), remote=REMOTE)
            self.assert_own_repair()

    def test_a_changed_active_package_and_another_checkout_stop_before_anything_starts(self):
        with self.subTest('another checkout'):
            other = self.home / 'another-checkout'
            other.mkdir()
            self.write_record(active=self.active, skills={'claude': ['alpha']}, root=str(other), remote=REMOTE)
            code, output = self.repair()
            self.assertEqual((code, self.started, self.names()), (2, [], []), output)
        with self.subTest('a changed file of the active package'):
            self.write_record(active=self.active, skills={'claude': ['alpha']}, root=str(self.source), remote=REMOTE)
            (self.active / 'VERSION').write_text('changed\n')
            code, output = self.repair()
            self.assertEqual((code, self.started, self.names()), (1, [], []), output)
            self.assertIn(str(self.active / 'VERSION'), output)


class AcquiredReleaseTests(ReleaseCase):
    """The downloaded installer installs its own files for the folder and the remote that its caller names."""

    ENTRY = '--install-acquired-release'

    def setUp(self):
        super().setUp()
        self.release = make_source(self.home.parent / 'release')
        self.patches.enter_context(mock.patch('installer.runner.REPO_ROOT', self.release))
        for host in ('.claude', '.codex'):
            (self.home / host).mkdir()
        self.stub('installer.hosts.observe_writers',
                  side_effect=lambda platforms: {host: cli.hosts.WriterObservation('absent') for host in platforms})
        self.stub('installer.hosts.confirm_project_trust', '')
        for target in ('installer.source.acquire_release', 'installer.prerequisites.require_git_for_release'):
            self.patches.enter_context(mock.patch(target, side_effect=AssertionError(f'{target} belongs to the caller')))
        run_command = runner.run_command
        self.children: list[tuple[list[str], dict]] = []

        def run(argv, **kwargs):
            if argv[0] != sys.executable:
                return run_command(argv, **kwargs)
            self.children.append(([str(value) for value in argv], kwargs))
            return ToolResult(0)

        self.patches.enter_context(mock.patch('installer.runner.run_command', side_effect=run))

    def publish(self, text: str) -> str:
        """Give the release its own files and commit."""
        (self.release / 'VERSION').write_text(text + '\n')
        return self.repository(self.release)

    def run_entry(self, *argv, start=True):
        """Start an entry. Like the calling installer, pass an installation the install source read before the download."""
        self.children.clear()
        argv = [str(value) for value in argv]
        environment = {}
        if (start and argv[:1] == [self.ENTRY] and argv[3:4] != ['update'] and cli.START_ORIGIN_ENV not in os.environ
                and len(argv) > 1 and Path(argv[1]).is_absolute() and Path(argv[1]).is_dir()):
            environment[cli.START_ORIGIN_ENV] = json.dumps(dict(source.input_record(Path(argv[1])),
                                                                registered=source.registration(cli.install_transaction.record_after_recovery(self.ghost))))
        with mock.patch.dict(os.environ, environment):
            return self.captured(cli.main, argv)

    def installed(self, *argv, root: Path, version: str, commit: str, cause: str, remote: str = REMOTE) -> dict:
        """Run the entry and check the package that it prepared and the two processes that it started; the
        candidate's request."""
        code, output = self.run_entry(*argv)
        self.assertEqual(code, 0, output)
        (candidate, sent), (followup, handed) = self.children
        request, move = (json.loads(kwargs['env']['GHOST_ALICE_PACKAGE_REQUEST']) for kwargs in (sent, handed))
        core = Path(request['package_root'])
        self.assertEqual(core.parent.parent, (self.ghost / 'versions').resolve())
        self.assertEqual(json.loads((core.parent / 'version.json').read_text())['active_runs'], ['--repair'])
        self.assertEqual((core / 'VERSION').read_text(), version + '\n')
        for process in (candidate, followup):
            self.assertEqual(Path(process[1]), core / '_shared/installer/cli.py')
        expected = dict(root=str(root), remote=remote, ref='refs/heads/main', branch='main', commit=commit, dirty='clean')
        for fields in (request['source'], move['source']):
            self.assertEqual({key: fields.get(key) for key in expected}, expected)
        self.assertEqual(move['package_root'], str(core))
        self.assertEqual((move['stage'], move['execution_cause']), ('record-move', cause))
        self.assertEqual(request.get('execution_cause'), cause if cause != 'install' else None)
        self.assertTrue(handed['inherit_stdin'])
        return request

    def test_a_checkout_and_an_unpacked_archive_stay_the_install_source(self):
        checkout = make_source(self.home.parent / 'checkout').resolve()
        (checkout / 'VERSION').write_text('checkout\n')
        self.repository(checkout, remote='https://example.invalid/checkout.git')
        for root in (checkout, self.source.resolve()):
            with self.subTest(root=root.name):
                commit = self.publish('for ' + root.name)
                request = self.installed(self.ENTRY, root, REMOTE, '--platform', 'claude', 'alpha', root=root,
                                         version='for ' + root.name, commit=commit, cause='install')
                self.assertNotIn('selections', request)
                self.assertEqual(self.children[0][0][2:], ['--platform', 'claude', 'alpha'])
                saved = json.loads((Path(request['package_root']).parent / 'version.json').read_text())
                self.assertEqual((saved['status'], saved['selections']), ('candidate', {'claude': ['alpha']}))

    def test_a_saved_package_that_never_finished_gives_its_own_selection_and_source(self):
        core = self.saved_package(status='candidate', selections={'claude': ['alpha']}, root=self.source,
                                  remote='https://example.invalid/saved.git')
        commit = self.publish('release')
        request = self.installed(self.ENTRY, core, REMOTE, root=self.source, version='release', commit=commit, cause='install')
        self.assertEqual(request['selections'], {'claude': ['alpha']})
        self.assertEqual(self.children[0][0][2:], [])

    def test_a_release_that_a_saved_package_downloads_gives_the_commands_addon_source_to_every_host(self):
        core = self.saved_package(status='successful', selections={'claude': ['alpha']}, root=self.source,
                                  remote='https://example.invalid/saved.git')
        installed = {'claude': ['alpha'], 'codex': ['alpha']}
        self.write_record(active=core, skills=installed, root=str(self.source), remote='https://example.invalid/recorded.git')
        addon = self.home.parent / 'addon'
        (addon / 'demo/skill').mkdir(parents=True)
        (addon / 'addons-manifest.json').write_text(json.dumps({'manifest_version': 1, 'addons': [{'id': 'demo', 'path': 'demo'}]}))
        (addon / 'demo/addon.json').write_text('{}')
        (addon / 'demo/skill/SKILL.md').write_text('addon bytes')
        commit = self.publish('with an addon')
        request = self.installed(self.ENTRY, core, REMOTE, '--addon-source', str(addon), root=self.source,
                                 version='with an addon', commit=commit, cause='install')
        # The record names the hosts. Only a package that installs itself again asks each host's addon registry.
        self.assertEqual(request['selections'], installed)
        self.assertNotIn('held_addons', request)
        (saved,) = request['addon_commits']
        self.assertEqual(self.children[0][0][2:], ['--addon-source', saved])

    def test_a_source_that_another_command_recorded_during_the_download_stops_the_installation(self):
        core = self.saved_package(status='successful', selections={'claude': ['alpha']}, root=self.source,
                                  remote='https://example.invalid/saved.git')
        self.publish('release')
        self.write_record(active=core, skills={'claude': ['alpha']}, root=str(self.home), remote=REMOTE)
        before = self.home_files()
        # A saved package can name no remote; the release installs from the one that it was downloaded from.
        start = json.dumps(dict(root=str(self.source), registered=None))
        with mock.patch.dict(os.environ, {cli.START_ORIGIN_ENV: start}):
            for argv in ([], ['--platform', 'claude', 'alpha']):
                with self.subTest(argv=argv):
                    code, output = self.run_entry(self.ENTRY, core, REMOTE, *argv)
                    self.assertEqual((code, self.children), (1, []), output)
                    self.assertIn('registered checkout or remote changed', output)
                    self.assertNotIn('--remote', output)
                    self.assertEqual(self.home_files(), before)

    def test_local_install_keeps_a_registration_created_after_its_source_check(self):
        self.publish('local release')
        code, output = self.run_entry('--platform', 'claude', '--adopt-source', '--local-source', 'alpha')
        self.assertEqual(code, 0, output)
        (candidate, sent), _ = self.children
        self.write_record(root=str(self.home), remote=REMOTE)
        before = self.record.read_bytes()
        with mock.patch.dict(os.environ, sent['env']), use_source(Path(candidate[1]).parents[2]), \
                mock.patch.object(cli.transaction, 'activate') as activate, \
                mock.patch.object(cli, '_dispatch', return_value=0), mock.patch.object(source, 'finalize_source', return_value=[]):
            code, output = self.captured(cli.main, candidate[2:])
        self.assertEqual(code, 1, output)
        self.assertIn('registered checkout or remote changed', output)
        self.assertNotIn('--remote', output)
        self.assertEqual(self.record.read_bytes(), before)
        activate.assert_not_called()

    def test_update_and_saved_reinstall_keep_a_concurrent_removal(self):
        self.publish('release')
        package = self.saved_package(status='successful', selections={'claude': ['alpha', 'fam']},
                                     root=self.source, remote=REMOTE)
        installed = {'claude': ['alpha', 'fam'], 'codex': ['alpha']}
        for argv in ([], ['update'], ['update', '--platform', 'claude']):
            with self.subTest(argv=argv):
                self.write_record(active=package, skills=installed, root=str(self.source), remote=REMOTE)
                code, output = self.run_entry(self.ENTRY, package, REMOTE, *argv)
                self.assertEqual(code, 0, output)
                (candidate, sent), _ = self.children
                self.write_record(active=package, skills=dict(installed, claude=['alpha']), root=str(self.source), remote=REMOTE)
                before = self.record.read_bytes()
                with mock.patch.dict(os.environ, sent['env']), use_source(Path(candidate[1]).parents[2]), \
                        mock.patch.object(cli.transaction, 'activate') as activate, \
                        mock.patch.object(cli, '_dispatch', return_value=0), mock.patch.object(source, 'finalize_source', return_value=[]):
                    code, output = self.captured(cli.main, candidate[2:])
                self.assertEqual(code, 1, output)
                self.assertIn('Core selection changed', output)
                self.assertEqual(self.record.read_bytes(), before)
                activate.assert_not_called()

    def test_an_installation_without_the_source_that_the_calling_installer_read_is_refused(self):
        self.publish('release')
        self.write_record(skills={'claude': ['alpha']}, root=str(self.source), remote=REMOTE)
        before = self.home_files()
        for argv in ([], ['--platform', 'claude', 'alpha'], ['--post-merge']):
            with self.subTest(argv=argv):
                code, output = self.run_entry(self.ENTRY, self.source, REMOTE, *argv, start=False)
                self.assertEqual((code, self.children), (1, []), output)
                self.assertIn('[ERROR]', output)
                self.assertEqual(self.home_files(), before)

    def test_a_request_that_did_not_finish_never_narrows_the_installed_selection(self):
        installed = {'claude': ['alpha', 'two'], 'codex': ['one']}
        self.patches.enter_context(mock.patch('installer.selection.ALL_SKILLS', ('alpha', 'fam')))
        for text, names, active, expected in (
                # The family and its skill name one target.
                ('one host', ['fam', 'one'], True, {'claude': ['alpha', 'one', 'two'], 'codex': ['one']}),
                ('a skill that the release does not hold', ['retired'], True, None),
                # An earlier release prepared this package and its request was refused.
                ('refused', ['fam'], False, installed)):
            with self.subTest(text):
                core = self.saved_package(status='candidate', selections={'claude': names}, root=self.source,
                                          remote='https://example.invalid/saved.git')
                self.write_record(active=core if active else self.home / 'earlier/core', skills=installed,
                                  root=str(self.source), remote='https://example.invalid/recorded.git')
                commit = self.publish(text)
                if expected is None:
                    code, output = self.run_entry(self.ENTRY, core, REMOTE)
                    self.assertEqual((code, self.children), (1, []), output)
                    self.assertIn('Skill not found: retired', output)
                else:
                    request = self.installed(self.ENTRY, core, REMOTE, root=self.source, version=text, commit=commit,
                                             cause='install')
                    self.assertEqual(request['selections'], expected)
                shutil.rmtree(core.parent)

    def test_a_saved_package_that_finished_gives_the_installed_selection_and_the_platform_filters_it(self):
        core = self.saved_package(status='successful', selections={'claude': ['fam']}, root=self.source,
                                  remote='https://example.invalid/saved.git')
        installed = {'claude': ['alpha'], 'codex': ['alpha', 'fam']}
        self.write_record(active=core, skills=installed, root=str(self.source), remote='https://example.invalid/recorded.git')
        for argv, expected in (([], installed), (['--platform', 'codex'], {'codex': ['alpha', 'fam']})):
            with self.subTest(argv=argv):
                version = ' '.join(['release', *argv])
                commit = self.publish(version)
                request = self.installed(self.ENTRY, core, REMOTE, *argv, root=self.source, version=version, commit=commit,
                                         cause='install')
                self.assertEqual(request['selections'], expected)
                # The installer that holds the install lock compares the registration of the command's start.
                self.assertEqual(request['start_registration'],
                                 dict(root=str(self.source), remote='https://example.invalid/recorded.git'))
        # Named skills replace the recorded selection.
        commit = self.publish('named skills')
        request = self.installed(self.ENTRY, core, REMOTE, '--platform', 'claude', 'fam', root=self.source,
                                 version='named skills', commit=commit, cause='install')
        self.assertNotIn('selections', request)
        self.assertEqual(self.children[0][0][2:], ['--platform', 'claude', 'fam'])

    def test_a_named_host_without_an_installed_selection_receives_every_skill(self):
        core = self.saved_package(status='successful', selections={'claude': ['fam']}, root=self.source,
                                  remote='https://example.invalid/saved.git')
        self.write_record(active=core, skills={'claude': ['alpha']}, root=str(self.source),
                          remote='https://example.invalid/recorded.git')
        commit = self.publish('another host')
        request = self.installed(self.ENTRY, core, REMOTE, '--platform', 'codex', root=self.source, version='another host',
                                 commit=commit, cause='install')
        self.assertNotIn('selections', request)
        self.assertEqual(self.children[0][0][2:], ['--platform', 'codex'])
        saved = json.loads((Path(request['package_root']).parent / 'version.json').read_text())
        self.assertEqual(saved['selections'], {'codex': list(ALL_SKILLS)})

    def test_a_merge_installs_for_every_detected_host_and_names_its_cause(self):
        commit = self.publish('release')
        self.repository(self.source, remote='https://example.invalid/checkout.git')
        merged = self.source.resolve()
        self.write_record(skills={'claude': ['alpha']}, root=str(merged), remote='https://example.invalid/recorded.git')
        with mock.patch('installer.source.interactive', side_effect=AssertionError('a merge asks nothing')):
            request = self.installed(self.ENTRY, merged, REMOTE, '--post-merge', root=merged, version='release',
                                     commit=commit, cause='post-merge')
        self.assertNotIn('selections', request)
        self.assertEqual(self.children[0][0][2:], [])
        saved = json.loads((Path(request['package_root']).parent / 'version.json').read_text())
        self.assertEqual(saved['selections'], {host: list(ALL_SKILLS) for host in ('claude', 'codex')})

    def test_update_keeps_the_recorded_source_takes_the_remote_and_installs_the_installed_selection(self):
        core = self.saved_package(status='successful', selections={'claude': ['fam']}, root=self.source,
                                  remote='https://example.invalid/saved.git')
        installed = {'claude': ['alpha'], 'codex': ['fam']}
        release_input = source.release_input
        for argv, expected in ((['update'], installed), (['update', '--platform', 'claude'], {'claude': ['alpha']}),
                               (['update', '--remote', REMOTE, '--ref', 'main', '--skip-source-health'], installed)):
            with self.subTest(argv=argv):
                recorded = 'https://example.invalid/recorded.git' if '--remote' in argv else REMOTE
                self.write_record(active=core, skills=installed, root=str(self.source), remote=recorded)
                version = ' '.join(argv)
                commit = self.publish(version)

                def switch_after_the_comparison(checkout, origin):
                    self.write_record(active=core, skills=installed, root=str(self.release), remote='/another/remote')
                    return release_input(checkout, origin)

                # The candidate compares the origin of the request under the lock.
                with mock.patch('installer.source.release_input', side_effect=switch_after_the_comparison):
                    request = self.installed(self.ENTRY, core, REMOTE, *argv, root=self.source, version=version, commit=commit,
                                             cause='update')
                self.assertEqual(request['selections'], expected)
                self.assertEqual(request['start_registration'], dict(root=str(self.source), remote=recorded))
        self.write_record(active=core, skills=installed, root=str(self.source), remote=REMOTE)
        for start in (dict(root='/another/folder', remote=REMOTE), dict(root=str(self.source), remote='/another/remote')):
            with self.subTest(start=start), mock.patch.dict(os.environ, {cli.START_ORIGIN_ENV: json.dumps(start)}):
                code, output = self.run_entry(self.ENTRY, core, REMOTE, 'update', '--remote', REMOTE)
                self.assertEqual((code, self.children), (1, []), output)
        # An earlier caller can supply only the download remote. Detect a change when it used no override.
        self.write_record(active=core, skills=installed, root=str(self.source), remote='/changed')
        code, output = self.run_entry(self.ENTRY, core, REMOTE, 'update')
        self.assertEqual((code, self.children), (1, []), output)
        self.assertIn(report.message('update_origin_changed'), output)
        self.write_record(active=core, root=str(self.source), remote=REMOTE)
        code, output = self.run_entry(self.ENTRY, core, REMOTE, 'update')
        self.assertEqual((code, self.children), (1, []), output)

    def test_a_skill_that_the_release_does_not_hold_stops_before_the_package_is_saved(self):
        self.publish('release')
        core = self.saved_package(status='successful', selections={'claude': ['fam']}, root=self.source,
                                  remote='https://example.invalid/saved.git')
        self.write_record(active=core, skills={'claude': ['alpha', 'no-longer-here']}, root=str(self.source), remote=REMOTE)
        before = self.home_files()
        for entry, argv, name in (
                (self.ENTRY, ['--platform', 'claude', 'alpha', 'no-such-skill'], 'no-such-skill'),
                (self.ENTRY, ['no-such-skill'], 'no-such-skill'),
                (self.ENTRY, ['update'], 'no-longer-here'), (self.ENTRY, [], 'no-longer-here'),
                ('--install-acquired-update', ['update'], 'no-longer-here')):
            with self.subTest(entry=entry, argv=argv):
                root = core if not argv else self.source
                coordinates = [root, REMOTE, *(['main'] if entry != self.ENTRY else [])]
                code, output = self.run_entry(entry, *coordinates, *argv)
                self.assertEqual((code, self.children), (1, []), output)
                self.assertIn(f'Skill not found: {name}', output)
                self.assertEqual(self.home_files(), before)

    def test_the_help_of_update_comes_from_the_release_and_installs_nothing(self):
        self.publish('release')
        self.write_record(skills={'claude': ['alpha']}, root=str(self.source), remote=REMOTE)
        before = self.home_files()
        for entry in ([self.ENTRY, self.source, REMOTE], ['--install-acquired-update', self.source, REMOTE, 'main']):
            with self.subTest(entry=entry[0]):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stop:
                    cli.main([*map(str, entry), 'update', '--help'])
                self.assertEqual((stop.exception.code, self.children), (0, []))
                self.assertIn('usage: ghost-alice update', output.getvalue())
                self.assertEqual(self.home_files(), before)

    def test_everything_else_is_refused_before_any_change(self):
        commit = self.publish('release')
        self.write_record(skills={'claude': ['alpha']}, root=str(self.source), remote=REMOTE, commit=commit)
        root, request = str(self.source), {'package_root': str(runner.REPO_ROOT.resolve()), 'source': {'root': str(self.source)}}
        before = self.home_files()
        for arguments in ([], [root], [root, ''], ['', REMOTE], ['relative/folder', REMOTE],
                          [str(self.home / 'no-such-folder'), REMOTE],
                          *([root, REMOTE, *command] for command in (
                              ['--local-source'], ['update', '--local-source'], ['--platform', 'claude', '--local-source'],
                              ['--status'], ['--uninstall'], ['--platform', 'claude', '--uninstall', 'alpha'], ['--repair'],
                              ['--doctor'], ['--list'], ['--update-source'], ['--cleanup-pending'], ['gateway', 'detach'],
                              ['--help'], ['--bogus']))):
            with self.subTest(arguments=['ROOT' if argument == root else argument for argument in arguments]):
                code, output = self.run_entry(self.ENTRY, *arguments)
                self.assertEqual((code, self.children), (1, []), output)
                self.assertIn('[ERROR]', output)
                self.assertNotIn(f'Unknown option: {self.ENTRY}', output)
                self.assertEqual(self.home_files(), before)
        for command in ([], ['update'], ['--post-merge']):
            with self.subTest(request=command), \
                    mock.patch.dict(os.environ, {'GHOST_ALICE_PACKAGE_REQUEST': json.dumps(request)}):
                code, output = self.run_entry(self.ENTRY, root, REMOTE, *command)
                self.assertEqual((code, self.children), (1, []), output)
                self.assertNotIn(f'Unknown option: {self.ENTRY}', output)
                self.assertEqual(self.home_files(), before)

    def test_the_update_entry_of_earlier_releases_compares_its_coordinates_with_the_record(self):
        entry = '--install-acquired-update'
        installed = {'claude': ['alpha'], 'codex': ['fam']}
        recorded = dict(root=str(self.source), remote=REMOTE)
        for ref, argv, remote, expected in (
                ('main', ['update'], REMOTE, installed), ('refs/heads/main', ['update'], REMOTE, installed),
                ('main', ['update', '--platform', 'claude', '--ref', 'main'], REMOTE, {'claude': ['alpha']}),
                ('main', ['update', '--remote', '/another/remote'], '/another/remote', installed)):
            with self.subTest(ref=ref, argv=argv):
                self.write_record(skills=installed, **recorded)
                version = ' '.join([ref, *argv])
                commit = self.publish(version)
                request = self.installed(entry, self.source, remote, ref, *argv, root=self.source, version=version,
                                         commit=commit, cause='update', remote=remote)
                self.assertEqual(request['selections'], expected)
                self.assertEqual(request['start_registration'], recorded)
        before = self.home_files()
        request = {'package_root': str(runner.REPO_ROOT.resolve()), 'source': recorded}
        for name, coordinates, argv, environment in (
                ('another folder', [str(self.release), REMOTE, 'main'], ['update'], {}),
                ('another remote', [str(self.source), '/another/remote', 'main'], ['update'], {}),
                ('another branch', [str(self.source), REMOTE, 'refs/heads/feature'], ['update'], {}),
                ('a remote of the arguments', [str(self.source), REMOTE, 'main'], ['update', '--remote', '/another/remote'], {}),
                ('a branch of the arguments', [str(self.source), REMOTE, 'main'], ['update', '--ref', 'feature'], {}),
                ('local files', [str(self.source), REMOTE, 'main'], ['update', '--local-source'], {}),
                ('an install', [str(self.source), REMOTE, 'main'], ['--platform', 'claude'], {}),
                ('a status', [str(self.source), REMOTE, 'main'], ['--status'], {}),
                ('a request', [str(self.source), REMOTE, 'main'], ['update'],
                 {'GHOST_ALICE_PACKAGE_REQUEST': json.dumps(request)})):
            with self.subTest(refused=name), mock.patch.dict(os.environ, environment):
                code, output = self.run_entry(entry, *coordinates, *argv)
                self.assertEqual((code, self.children), (1, []), output)
                self.assertIn('[ERROR]', output)
                self.assertEqual(self.home_files(), before)


class DispatchCase(CliCase):
    """Command routing within a prepared installer; package execution is covered by shell installation tests."""

    def setUp(self):
        super().setUp()
        self.stub("installer.selection.list_skills", 0)
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
        self.stub("installer.lock.acquire", (self.home / "lock", "generation"))
        self.stub("installer.lock.release")
        self.stub("installer.runner.run_tool", mock.Mock(returncode=5))
        self.release = self.home.parent / 'downloaded-main'
        self.release.mkdir()
        self.patches.enter_context(mock.patch('installer.source.acquire_release',
                                              side_effect=lambda remote: contextlib.nullcontext(self.release)))
        self.patches.enter_context(mock.patch('installer.source.release_input',
            side_effect=lambda checkout, origin: dict(origin, branch='main', commit='fixture-release')))
        run_command = runner.run_command
        self.release_processes: list[list[str]] = []

        def run_release_in_this_process(argv, **kwargs):
            if list(argv[2:3]) != ['--install-acquired-release']:
                return run_command(argv, **kwargs)
            self.release_processes.append([str(value) for value in argv])
            with mock.patch.dict(os.environ, kwargs.get('env') or {}):
                return ToolResult(cli.main(argv[2:]))

        self.patches.enter_context(mock.patch('installer.runner.run_command', side_effect=run_release_in_this_process))
        self.dispatched_inputs = []
        def dispatch_without_candidate_process(options, **kwargs):
            self.dispatched_inputs.append(kwargs)
            return cli._dispatch(options)
        self.patches.enter_context(mock.patch("installer.cli.dispatch", side_effect=dispatch_without_candidate_process))

    def dispatch(self, argv, present=("claude", "codex")):
        with mock.patch("installer.locations.detect_present_platforms", return_value=list(present)), \
                mock.patch("installer.locations.detect_uninstall_platforms", return_value=list(present)):
            return self.captured(cli.main, argv)


class DispatchTests(DispatchCase):
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

    def test_gateway_without_platform_runs_the_passthrough_once(self):
        # A state-changing gateway operation must not fan out to every detected platform.
        rc, _ = self.dispatch(["gateway", "detach", "--root", "/x"])
        self.assertEqual(rc, 5)
        self.assertEqual(self.names().count("run_tool"), 1)
        self.assertNotIn("run_install_children", self.names())
        _, args, _ = next(c for c in self.calls if c[0] == "run_tool")
        self.assertEqual(args, ("_shared/context_gateway_install.py", "detach", "--root", "/x"))

    def test_install_paths(self):
        rc, _ = self.dispatch(["--platform", "claude"])
        self.assertEqual(rc, 0)
        self.assertEqual([n for n in self.names() if n in ("acquire", "run_install", "release")],
                         ["acquire", "run_install", "release"])
        _, args, _ = next(c for c in self.calls if c[0] == "run_install")
        self.assertEqual(args[0].options.args, [])
        self.assertEqual(args[0].platform, "claude")
        self.calls.clear()
        self.dispatch(["--platform", "codex", "task-router", "merge-companion"])
        _, args, _ = next(c for c in self.calls if c[0] == "run_install")
        self.assertEqual(args[0].options.args, ["task-router", "merge-companion"])
        self.assertEqual(args[0].skills_dir, self.home / ".agents" / "skills")

    def test_every_table_command_is_reachable_from_dispatch(self):
        # Without a platform, install fans out (auto mode) and a lone uninstall fans out (full cleanup);
        # every other selection runs its table command once.
        fan_out = {cli.INSTALL: "run_install_children", cli.UNINSTALL: "run_uninstall_children"}
        for command in cli.COMMANDS:
            for argv in (["--platform", "codex", *selecting_argv(command)], selecting_argv(command)):
                with self.subTest(argv=argv), mock.patch("installer.cli.run_command", return_value=0) as run:
                    self.calls.clear()
                    rc, _ = self.dispatch(argv)
                    self.assertEqual(rc, 0)
                    if command in fan_out and "--platform" not in argv:
                        run.assert_not_called()
                        self.assertIn(fan_out[command], self.names())
                        continue
                    run.assert_called_once()
                    self.assertIs(run.call_args.args[0], command)

    def test_run_command_holds_the_table_lock_around_the_handler(self):
        ctx = make_context(self.home, "codex")
        for command in cli.COMMANDS:
            with self.subTest(command=command.usage):
                self.calls.clear()
                handler = mock.Mock(side_effect=lambda c: self.calls.append(("handler", (c,), {})) or 7)
                self.assertEqual(cli.run_command(dataclasses.replace(command, run=handler), ctx), 7)
                self.assertEqual(self.names(), ["acquire", "handler", "release"] if command.lock else ["handler"])
                if command.lock:
                    self.assertEqual(self.calls[self.names().index("acquire")][1], (command.lock, "codex"))
                    self.assertEqual(self.calls[self.names().index("release")][1], ((self.home / "lock", "generation"),))

    def test_only_platform_commands_prompt_and_prepare_addon_sources(self):
        with mock.patch("installer.cli.sys.stdin") as stdin:
            stdin.isatty.return_value = False
            for command in cli.COMMANDS:
                if command is cli.INSTALL:
                    continue
                argv = ["--prompt-platform", "--addon-source", "/a", *selecting_argv(command)]
                with self.subTest(argv=argv):
                    self.calls.clear()
                    rc, out = self.dispatch(argv)
                    prompted = "[ERROR] --prompt-platform requires an interactive terminal." in out
                    self.assertEqual(prompted, command.platform, out)
                    if not command.platform:
                        self.assertNotIn("prepare_sources", self.names())
        self.calls.clear()
        self.dispatch(["--platform", "claude", "--addon-source", "/a", "--status"])
        self.assertLess(self.names().index("prepare_sources"), self.names().index("check_status"))

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
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_COMMAND": ".\\install.cmd"}):
            _, out = self.dispatch(["--bogus"])
        self.assertIn("See .\\install.cmd --help", out)
        # An unknown "-" token (a PowerShell-style name install.ps1 passes on) is an unknown option, before any prompt.
        for argv in (["-Offboard"], ["--prompt-platform", "-Repair"], ["--prompt-platform", "--bogus"]):
            with self.subTest(argv=argv):
                self.calls.clear()
                rc, out = self.dispatch(argv)
                self.assertEqual(rc, 1)
                self.assertIn(f"[ERROR] Unknown option: {argv[-1]}", out)
                self.assertNotIn("interactive terminal", out)
                self.assertNotIn("run_install_children", self.names())
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


class SourceCheckTests(DispatchCase):
    """Install (auto mode and one platform) and repair compare the install record's `source.root` with the
    checkout the installer runs from before anything changes: before addon sources are prepared, before the
    prerequisites, before the lock (which restores unfinished publishes)."""

    def setUp(self):
        super().setUp()
        self.other = self.home.parent / "other-checkout"
        self.other.mkdir()
        record = self.home / ".ghost-alice" / "installed-payload.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        self.record = record

    def record_source(self, root: Path) -> None:
        self.record.write_text(json.dumps({"schema_version": 1, "targets": {}, "source": {
            "root": str(root), "commit": "c0ffee", "dirty": "clean", "hooks_path": "hooks"}}), encoding="utf-8")

    CHANGES = ("prepare_sources", "acquire", "run_install", "run_repair", "run_install_children")

    def test_another_checkout_stops_install_and_repair_before_anything_changes(self):
        self.record_source(self.other)
        for argv in (["--platform", "codex"], ["--platform", "claude", "task-router"], [],
                     ["--addon-source", "/a"], ["--platform", "codex", "--addon-source", "/a"],
                     ["--platform", "claude", "--repair"]):
            with self.subTest(argv=argv):
                self.calls.clear()
                rc, out = self.dispatch(argv)
                self.assertEqual(rc, 2, out)
                self.assertIn("[ERROR] This checkout is not the install source the install record names.", out)
                self.assertEqual([name for name in self.names() if name in self.CHANGES], [])

    def test_the_check_comes_first_and_the_auto_mode_parent_checks_once(self):
        # Before any change the command compares once; a locked command compares again once it holds the lock.
        self.record_source(self.source)
        for name in ("check_install_source", "recheck_install_source"):
            self.patches.enter_context(mock.patch(f"installer.source.{name}", create=True,
                                                  side_effect=lambda ctx, _name=name, **kwargs:
                                                  self.calls.append((_name, (), kwargs))))
        for argv, steps in ((["--platform", "codex", "--addon-source", "/a"],
                             ["check_install_source", "prepare_sources", "acquire", "recheck_install_source",
                              "run_install", "release"]),
                            (["--platform", "claude"],
                             ["check_install_source", "acquire", "recheck_install_source", "run_install", "release"]),
                            (["--platform", "claude", "--repair"],
                             ["check_install_source", "acquire", "recheck_install_source", "run_repair", "release"]),
                            (["--addon-source", "/a"], ["check_install_source", "prepare_sources", "run_install_children"]),
                            ([], ["check_install_source", "run_install_children"])):
            with self.subTest(argv=argv):
                self.calls.clear()
                rc, _ = self.dispatch(argv)
                self.assertEqual(rc, 0)
                self.assertEqual([name for name in self.names() if name in steps], steps)
                adopts = [kwargs["can_adopt"] for name, _, kwargs in self.calls if name.endswith("check_install_source")]
                self.assertEqual(set(adopts), {"--repair" not in argv})

    def test_the_same_checkout_and_adopt_source_go_on(self):
        self.record_source(self.source)
        rc, out = self.dispatch(["--platform", "codex"])
        self.assertEqual(rc, 0)
        self.assertIn("run_install", self.names())
        self.assertNotIn("install source", out.lower())
        self.record_source(self.other)
        self.calls.clear()
        rc, out = self.dispatch(["--platform", "codex", "--adopt-source"])
        self.assertEqual(rc, 0)
        self.assertIn("run_install", self.names())
        self.assertIn(f"[INFO] Install source: this checkout replaces {self.other}", out)

    def test_a_source_that_changed_while_waiting_for_the_lock_stops_the_command(self):
        # Another installer adopted its own checkout between this command's check and its lock: the command compares
        # again under the lock and publishes nothing from a checkout that is no longer the install source.
        self.record_source(self.source)

        def adopted_elsewhere(command, platform):
            self.calls.append(("acquire", (command, platform), {}))
            self.record_source(self.other)
            return self.home / "lock", "generation"

        self.patches.enter_context(mock.patch("installer.lock.acquire", side_effect=adopted_elsewhere))
        for argv, handler in ((["--platform", "codex"], "run_install"), (["--platform", "claude", "--repair"], "run_repair")):
            with self.subTest(argv=argv):
                self.record_source(self.source)
                self.calls.clear()
                rc, out = self.dispatch(argv)
                self.assertEqual(rc, 2, out)
                self.assertIn(f"[ERROR]   Install source: {self.other}\n", out)
                self.assertNotIn(handler, self.names())
                self.assertEqual(self.names()[-1], "release")
        # An unchanged source under the lock goes on without a second message.
        self.patches.enter_context(mock.patch("installer.lock.acquire", return_value=(self.home / "lock", "generation")))
        self.record_source(self.source)
        self.calls.clear()
        rc, out = self.dispatch(["--platform", "codex"])
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("run_install", self.names())

    def test_repair_never_replaces_the_install_source(self):
        # A repair re-provisions missing targets only: from another checkout it would publish nothing, or mix two
        # checkouts. Moving the installation is the install's job, with --adopt-source or after the old one is gone.
        hint = ("[ERROR] Repair runs only from the install source; run the install from this checkout with "
                "--adopt-source to move the installation here.")
        for recorded, extra in ((self.other, ["--adopt-source"]), (self.other, []),
                                (self.home.parent / "removed-clone", [])):
            with self.subTest(recorded=recorded.name, extra=extra):
                self.record_source(recorded)
                self.calls.clear()
                rc, out = self.dispatch(["--platform", "claude", "--repair", *extra])
                self.assertEqual(rc, 2, out)
                self.assertIn(hint, out)
                self.assertNotIn("this checkout replaces", out)
                self.assertEqual([name for name in self.names() if name in self.CHANGES], [])

    def test_commands_that_install_nothing_are_not_checked(self):
        self.record_source(self.other)
        for argv, handler in ((["--platform", "claude", "--status"], "check_status"),
                              (["--platform", "claude", "--doctor"], "run_doctor"),
                              (["--platform", "codex", "--uninstall", "task-router"], "uninstall"),
                              (["--update-source"], "update_source"),
                              (["--platform", "claude", "--cleanup-pending"], "cleanup_pending"),
                              (["gateway", "detach"], "run_tool"),
                              (["--list"], "list_skills")):
            with self.subTest(argv=argv):
                self.calls.clear()
                _, out = self.dispatch(argv)
                self.assertIn(handler, self.names())
                self.assertNotIn("install source", out.lower())


class PostMergeTests(DispatchCase):
    """`--post-merge --merged-checkout PATH`, which the install source's git post-merge hook runs after a merge in
    any worktree: when the merged checkout is the checkout the install record names as the install source, the
    install goes on in auto mode, for every detected host; for any other checkout one line says so and nothing is
    installed. The install source check's own function judges, and the install lock (taken by the install, not by
    this step) judges whether another install runs."""

    def setUp(self):
        super().setUp()
        self.record = self.home / ".ghost-alice" / "installed-payload.json"
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.other = self.home.parent / "other-checkout"
        self.other.mkdir()

    def record_source(self, root: Path) -> None:
        self.record.write_text(json.dumps({"schema_version": 1, "targets": {}, "source": {
            "root": str(root), "commit": "c0ffee", "dirty": "clean", "hooks_path": "hooks"}}), encoding="utf-8")

    INSTALLS = ("prepare_sources", "acquire", "run_install", "run_repair", "run_install_children")

    def test_the_install_source_goes_on_with_the_auto_mode_install(self):
        self.record_source(self.source)
        rc, out = self.dispatch(["--post-merge"])
        self.assertEqual(rc, 0, out)
        self.assertIn("run_install_children", self.names())
        _, args, _ = next(call for call in self.calls if call[0] == "run_install_children")
        self.assertFalse(args[0].options.platform_explicit)
        self.assertEqual(args[1], ["claude", "codex"])
        self.assertEqual([(Path(entry[1]), entry[3], entry[5:]) for entry in self.release_processes],
                         [(self.release / '_shared/installer/cli.py', str(self.source.resolve()), ['--post-merge'])])
        self.assertEqual(self.dispatched_inputs[-1]['provenance']['root'], str(self.source.resolve()))
        # The step itself takes no lock: each platform's install takes it, and refuses while another install holds it.
        self.assertNotIn("acquire", self.names())

    def test_another_checkout_reports_one_line_and_installs_nothing(self):
        # A recorded source that no longer exists is replaced only by a direct install run, never by the post-merge hook after a merge.
        for recorded in (self.other, self.home.parent / "removed-clone"):
            with self.subTest(recorded=recorded.name):
                self.calls.clear()
                self.record_source(recorded)
                rc, out = self.dispatch(["--post-merge"])
                self.assertEqual(rc, 0)
                self.assertEqual(out, f"[INFO] Post-merge: the merged checkout {self.source.resolve()} is not the "
                                      f"install source ({recorded}); nothing was installed.\n")
                self.assertEqual([name for name in self.names() if name in self.INSTALLS], [])

    def test_the_merged_checkout_is_judged_not_the_checkout_the_installer_runs_from(self):
        # The hook file and the installer belong to the install source; the merge ran in another worktree.
        self.record_source(self.source)
        rc, out = self.dispatch(["--post-merge", "--merged-checkout", str(self.other)])
        self.assertEqual(rc, 0)
        self.assertEqual(out, f"[INFO] Post-merge: the merged checkout {self.other.resolve()} is not the install "
                              f"source ({self.source}); nothing was installed.\n")
        self.assertEqual([name for name in self.names() if name in self.INSTALLS], [])
        # A merge in the install source itself goes on with the auto-mode install.
        rc, out = self.dispatch(["--post-merge", "--merged-checkout", str(self.source)])
        self.assertEqual(rc, 0, out)
        self.assertIn("run_install_children", self.names())

    def test_without_a_readable_record_nothing_is_installed(self):
        rc, out = self.dispatch(["--post-merge"])
        self.assertEqual((rc, out), (0, "[INFO] Post-merge: no install record names an install source; "
                                        "nothing was installed.\n"))
        self.record.write_text("{broken", encoding="utf-8")
        rc, out = self.dispatch(["--post-merge"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith(f"[WARN] Post-merge: the install record cannot be read ({self.record} is not JSON"))
        self.assertTrue(out.endswith("); nothing was installed.\n"))
        self.assertEqual([name for name in self.names() if name in self.INSTALLS], [])

    def test_management_entry_identifies_the_missing_active_file_and_repair_source(self):
        active = self.home / "incomplete package"
        with mock.patch.object(source, "active_root", return_value=str(active)):
            code, output = self.dispatch(["--management-entry", "--doctor"])
        self.assertEqual(code, 1)
        self.assertIn(str(active / "_shared/installer/cli.py"), output)
        self.assertIn("--repair from a Ghost-ALICE checkout", output)

    def test_a_merge_without_an_active_installer_installs_nothing(self):
        # Git can run the hook with another HOME, which holds no installation.
        rc, out = self.dispatch(["--management-entry", "--post-merge", "--merged-checkout", str(self.source)])
        self.assertEqual((rc, out), (0, "[INFO] Post-merge: no install record names an install source; "
                                        "nothing was installed.\n"))
        rc, out = self.dispatch(["--management-entry", "--doctor"])
        self.assertEqual((rc, out), (1, "[ERROR] No installer is active. Run the installation command again.\n"))

    def test_the_install_source_check_function_judges(self):
        # The record names another folder, but the one judgment says this is the install source: the step and the
        # install's own check both follow it, so the hook keeps no copy of the comparison.
        from installer.source import SourceMatch
        self.record_source(self.other)
        same = SourceMatch("same", self.source.resolve(), self.source)
        with mock.patch("installer.source.match_install_source", return_value=same) as judged:
            rc, _ = self.dispatch(["--post-merge"])
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(judged.call_count, 2)
        self.assertIn("run_install_children", self.names())

    def test_a_refused_install_keeps_its_exit_code_for_the_hook(self):
        self.record_source(self.source)
        refused = self.patches.enter_context(mock.patch("installer.auto.run_install_children", return_value=1))
        rc, _ = self.dispatch(["--post-merge"])
        refused.assert_called_once()
        self.assertEqual(rc, 1)


class UnfinishedPublishTests(CliCase):
    def test_a_publish_that_cannot_be_restored_stops_every_locked_command_with_exit_1(self):
        folder = self.home / ".ghost-alice" / "rollbacks" / "20261020T000000Z-1-aaaaaaaa"
        folder.mkdir(parents=True)
        (folder / "journal.json").write_text("{broken", encoding="utf-8")
        for target in ("installer.cli.run_install", "installer.lifecycle.uninstall", "installer.lifecycle.run_repair",
                       "installer.preservation.cleanup_pending"):
            self.stub(target, 0)
        runner = FakeRunner()
        locked = [command for command in cli.COMMANDS if command.lock]
        self.assertEqual(sorted(command.lock for command in locked),
                         ["cleanup-pending", "gateway", "install", "repair", "uninstall"])
        for command in locked:
            with self.subTest(lock=command.lock):
                self.calls.clear()
                ctx = make_context(self.home, "claude", args=selecting_argv(command))
                with runner.patch():
                    out = self.expect_exit(1, cli.run_command, command, ctx)
                self.assertIn(f"review {folder}", out)
                self.assertEqual(self.calls, [])
                self.assertEqual(runner.tool_calls("context_gateway_install.py"), [])
                self.assertFalse((self.home / ".ghost-alice" / "install.lock").exists())


class InstallOrderTests(CliCase):
    STEPS = ("acquire", "check_source_health",
             "collect_targets",
             "cleanup_deprecated_installed_skills", "encoding_guard", "preflight", "resume_pending_uninstalls",
             "check_addon_collisions", "sync_runtime_shared", "report_gateway", "install_hooks", "ensure_bootstrap",
             "sync_commands",
             "verify_after_copy", "snapshot_after_install", "migrate_state_once",
             "write_sidecars", "write_install_state_manifest", "write_event", "release")

    def setUp(self):
        super().setUp()
        (self.source / "task-router").mkdir()
        (self.source / "task-router" / "SKILL.md").write_text("x", encoding="utf-8")
        self.stub("installer.lock.acquire", (self.home / "lock", "generation"))
        self.stub("installer.lock.release")
        self.stub("installer.transaction.migrate_record")
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
        self.stub("installer.transaction.record_current_links")
        self.stub("installer.transaction.sync_runtime_shared", True)
        self.stub("installer.transaction.report_gateway")
        self.stub("installer.hosts.install_hooks", side_effect=lambda ctx, action, platform: hook_status(platform, action))
        self.stub("installer.hosts.ensure_claude_bootstrap", True)
        self.stub("installer.hosts.ensure_codex_bootstrap", True)
        self.stub("installer.hosts.sync_commands", 0)
        self.stub("installer.transaction.verify_after_copy", True)
        self.stub("installer.transaction.snapshot_after_install", True)
        self.stub("installer.addons.migrate_state_once")
        self.stub("installer.addons.write_sidecars", True)
        self.stub("installer.transaction.write_install_state_manifest", True)
        self.stub("install_transaction.cleanup_completed", ())
        self.stub("installer.report.write_event", side_effect=report.write_event)

    def collect(self, ctx, skills):
        targets = [Target("task-router", self.source / "task-router"), Target("alpha", self.source / "alpha")]
        self.collected = Collected(skill_targets=targets, addon_targets=[], skill_target_total=2, support_target_total=1)
        return self.collected

    def test_install_finishes_the_previous_moves_record_source_cleanup(self):
        import content_digest
        import install_lock
        import install_transaction
        import runtime_config

        ghost = (self.home / '.ghost-alice').resolve()
        old, current = (self.home / 'old-records').resolve(), ghost / 'session-intent'
        old.mkdir()
        current.mkdir(parents=True)
        (old / 'original.txt').write_text('verified history')
        (current / 'ghost-state.sqlite3').write_bytes(b'current records')
        config = runtime_config.config_path()
        config.write_text(json.dumps({'retired_ledger_roots': [str(old)]}))
        with install_lock.command_lock(ghost / 'install.lock', owner='installer:cleanup-fixture'):
            journal = install_transaction.Journal.begin(dict(ghost_root=str(ghost), host='claude'), [])
            journal.data['source_cleanup'] = dict(path=str(old), destination=str(current),
                                                 baseline=content_digest.state(old, include_generated=True))
            journal.mark('done')
            with mock.patch.object(install_transaction, 'cleanup_completed', cleanup_completed):
                rc, output = self.captured(cli.run_install, make_context(self.home, 'claude', args=['task-router']))
        self.assertEqual(rc, 0, output)
        self.assertFalse(old.exists())
        self.assertFalse(journal.folder.exists())
        self.assertEqual((current / 'ghost-state.sqlite3').read_bytes(), b'current records')

    def test_install_reports_the_cause_and_backup_for_an_unreadable_inventory(self):
        state = self.home / '.ghost-alice' / 'install-state' / 'claude.json'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text('{"targets":[{}]}')
        rc, output = self.captured(cli.run_install, make_context(self.home, "claude", args=["alpha"]))
        self.assertEqual(rc, 0, output)
        backup, = state.parent.glob('claude.json.*.bak')
        self.assertEqual(backup.read_text(), '{"targets":[{}]}')
        self.assertIn(str(backup), output)
        self.assertIn('invalid install-state target', output)

    def test_real_postflight_source_loss_stops_the_cli_before_snapshot(self):
        checkout = Path(__file__).resolve().parents[1]
        verifier = self.source / "merge-companion" / "scripts"
        verifier.mkdir(parents=True)
        for name in ("install_verifier.py", "file_walker.py"):
            shutil.copy2(checkout / "merge-companion" / "scripts" / name, verifier / name)
        ctx = make_context(self.home, "claude", args=["alpha"])
        ctx.skills_dir.mkdir(parents=True)
        for name in ("_shared", "task-router"):
            (ctx.skills_dir / name).symlink_to(self.source / name)
        shutil.copytree(self.source / "alpha", ctx.skills_dir / "alpha")

        def source_disappears(*args):
            shutil.rmtree(self.source / "alpha")
            return 0

        self.stub("installer.hosts.sync_commands", side_effect=source_disappears)
        self.stub("installer.transaction.verify_after_copy", side_effect=verify_after_copy)
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(checkout / "_shared")}):
            self.expect_exit(1, cli.run_command, cli.INSTALL, ctx)
        self.assertNotIn("snapshot_after_install", self.names())
        self.assertNotIn("write_install_state_manifest", self.names())
        state = json.loads((self.home / ".ghost-alice" / "install-state" / "claude.json").read_text())
        self.assertEqual([(row["target_name"], row["reason"]) for row in state["failures"]],
                         [("alpha", "missing-source")])

    def order(self):
        names = []
        for name, _, _ in self.calls:
            if name in ("ensure_claude_bootstrap", "ensure_codex_bootstrap"):
                name = "ensure_bootstrap"
            if name in self.STEPS and (not names or names[-1] != name):
                names.append(name)
        return names

    def test_claude_core_copy_finishes_before_hooks_and_verification(self):
        ctx = make_context(self.home, "claude", args=["task-router", "alpha"])
        rc, out = self.captured(cli.run_command, cli.INSTALL, ctx)
        self.assertEqual(rc, 0)
        self.assertEqual(self.order(), list(self.STEPS))
        placement = [n for n, _, _ in self.calls if n in ("install_shared", "link_or_copy", "copy_targets_batch")]
        self.assertEqual(placement, ["copy_targets_batch"])
        names = [n for n, _, _ in self.calls]
        self.assertLess(names.index("check_source_health"), names.index("collect_targets"))
        self.assertLess(names.index("check_addon_collisions"), names.index("copy_targets_batch"))
        self.assertLess(names.index("install_hooks"), names.index("ensure_claude_bootstrap"))
        self.assertLess(names.index("sync_runtime_shared"), names.index("install_hooks"))
        # The activation migrates the record before it starts this host's installation.
        self.assertNotIn("migrate_record", names)
        for name, args, _ in self.calls:
            if name in ("encoding_guard", "preflight"):
                self.assertIs(args[1], self.collected)
            if name in ("verify_after_copy", "write_install_state_manifest"):
                self.assertIs(args[3], self.collected)
        self.assertIn("[OK] [5/5] Verification: install state recorded", out) if report.state().report_child else None

    def test_the_gateway_line_reaches_the_terminal_in_every_output_mode(self):
        runtime = str(self.home / "gateway-output-fixture" / "runtime")
        note = f"Context gateway: registered, runtime {runtime}, service not running"
        self.patches.enter_context(mock.patch("installer.transaction.installed_payload.read",
                                              return_value={"service": {"runtime_root": runtime}}))
        self.patches.enter_context(mock.patch("installer.gateway_participant.loaded_service", return_value=mock.Mock(state='absent')))
        self.patches.enter_context(mock.patch("installer.transaction.report_gateway", side_effect=report_gateway))
        # Compact mode: steps go to the log, the report tail carries the note under Attention.
        report.configure(verbose=False, report_child=False, tty=False, progress_env="off", home=self.home)
        ctx = make_context(self.home, "claude", args=["task-router"])
        rc, out = self.captured(cli.run_command, cli.INSTALL, ctx)
        self.assertEqual(rc, 0)
        self.assertIn(f"Attention\n  - visibility can be changed later in Claude Code with /visibility between: dynamic | minimal | strict\n"
                      f"  - {note}\n", out)
        # Verbose mode: the line prints where it happens and the tail is not printed.
        quiet(self.home, verbose=True)
        ctx = make_context(self.home, "claude", args=["task-router"])
        rc, out = self.captured(cli.run_command, cli.INSTALL, ctx)
        self.assertEqual(rc, 0)
        self.assertIn(note, out)
        self.assertNotIn("Attention", out)
        # A child of the auto mode writes the note as an event for the parent's tail.
        event_file = self.home / "run.events.jsonl"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_EVENT_FILE": str(event_file)}):
            quiet(self.home, report_child=True)
        ctx = make_context(self.home, "codex", args=["task-router"])
        rc, _ = self.captured(cli.run_command, cli.INSTALL, ctx)
        self.assertEqual(rc, 0)
        events = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]
        self.assertIn({"type": "gateway-result", "platform": "codex", "summary": note, "running": False}, events)
        # Without a gateway nothing is written or printed.
        self.patches.enter_context(mock.patch("installer.transaction.report_gateway", return_value=None))
        event_file.write_text("", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_EVENT_FILE": str(event_file)}):
            quiet(self.home, report_child=True)
        self.captured(cli.run_command, cli.INSTALL, make_context(self.home, "codex", args=["task-router"]))
        self.assertNotIn("gateway-result", event_file.read_text(encoding="utf-8"))

    def test_codex_copy_mode_uses_one_batch(self):
        ctx = make_context(self.home, "codex", args=["task-router", "alpha"])
        rc, _ = self.captured(cli.run_command, cli.INSTALL, ctx)
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
        ctx = make_context(self.home, "claude")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(InstallerExit) as stop:
            cli.run_command(cli.INSTALL, ctx)
        self.assertEqual(stop.exception.code, 1)
        names = [n for n, _, _ in self.calls]
        self.assertNotIn("collect_targets", names)
        self.assertEqual(names[-1], "release")

    def test_the_earlier_records_are_still_written_after_placement(self):
        # The install record and copy markers are written at publish; the three earlier records keep their own steps until their
        # readers move to the install record.
        ctx = make_context(self.home, "codex", args=["task-router"])
        rc, _ = self.captured(cli.run_command, cli.INSTALL, ctx)
        self.assertEqual(rc, 0)
        names = [n for n, _, _ in self.calls]
        for step in ("snapshot_after_install", "write_sidecars",
                     "write_install_state_manifest"):
            self.assertIn(step, names)
            self.assertLess(names.index("copy_targets_batch"), names.index(step))

    def test_every_required_step_failure_stops_the_install_and_releases_the_lock(self):
        # Every step the bash installer aborted on (installer_lib/install.sh:880-1139) still aborts the install:
        # the failing step itself ran, the step after it never runs, and the lock is
        # released last. A hook failure reports its result without claiming later verification ran.
        # Placement failures carry their own codes: install_shared propagates the transaction's
        # code, a failed link or copy exits 1.
        cases = (
            ("claude", "installer.selection.cleanup_deprecated_installed_skills", False, 1, None, "encoding_guard"),
            ("claude", "installer.preservation.encoding_guard", False, 1, "[ERROR] encoding guard failed", "preflight"),
            ("claude", "installer.preservation.preflight", False, 1, None, "resume_pending_uninstalls"),
            ("claude", "installer.transaction.copy_targets_batch", False, 1, None, "sync_runtime_shared"),
            ("codex", "installer.transaction.copy_targets_batch", False, 1, None, "sync_runtime_shared"),
            ("claude", "installer.transaction.sync_runtime_shared", False, 1,
             "[ERROR] Runtime shared core sync failed. Aborting hook install", "report_gateway"),
            ("claude", "installer.hosts.install_hooks", hook_status(status='error'), 1, "[ERROR] Hook install failed. Aborting skill install",
             "ensure_claude_bootstrap"),
            ("claude", "installer.hosts.ensure_claude_bootstrap", False, 1, None, "sync_commands"),
            ("codex", "installer.hosts.ensure_codex_bootstrap", False, 1, None, "sync_commands"),
            ("claude", "installer.transaction.verify_after_copy", False, 1, None, "snapshot_after_install"),
            ("claude", "installer.transaction.snapshot_after_install", False, 1, None, "migrate_state_once"),
            ("claude", "installer.addons.write_sidecars", False, 1, None, "write_install_state_manifest"),
            ("claude", "installer.transaction.write_install_state_manifest", False, 1, None, "write_event"),
        )
        for platform, target, failure, code, message, never_called in cases:
            with self.subTest(platform=platform, step=target.rsplit(".", 1)[-1]):
                self.calls.clear()
                event_file = self.home / "failed-install.events.jsonl"
                event_file.unlink(missing_ok=True)
                report.configure(verbose=False, report_child=True, tty=False, progress_env="off",
                                 home=self.home, event_file=event_file)
                with mock.patch(target, return_value=failure) as failing:
                    ctx = make_context(self.home, platform, args=["task-router", "alpha"])
                    out = self.expect_exit(code, cli.run_command, cli.INSTALL, ctx)
                failing.assert_called()
                if message:
                    self.assertIn(message, out)
                names = [n for n, _, _ in self.calls]
                self.assertNotIn(never_called, names)
                events = [json.loads(line) for line in event_file.read_text().splitlines()] if event_file.exists() else []
                events = [event for event in events if event.get("type") == "platform-result"]
                if target == "installer.hosts.install_hooks":
                    self.assertNotIn("verify_after_copy", names)
                    self.assertEqual([(event["hooks"], event["verification"]) for event in events],
                                     [("error", "not-run")])
                else:
                    self.assertEqual(events, [])
                self.assertEqual(names[-1], "release")

    def test_update_hint_names_the_fixed_command_on_each_host(self):
        quiet(self.home, verbose=True)
        for platform in ('claude', 'codex'):
            with self.subTest(platform=platform):
                ctx = make_context(self.home, platform, args=["task-router"])
                rc, out = self.captured(cli.run_command, cli.INSTALL, ctx)
                self.assertEqual(rc, 0)
                self.assertIn(f"To update the installed Core selection: {locations.management_command('update')}", out)

    def test_default_skills_are_all_skills(self):
        ctx = make_context(self.home, "claude")
        self.captured(cli.run_command, cli.INSTALL, ctx)
        _, args, _ = next(c for c in self.calls if c[0] == "collect_targets")
        self.assertEqual(args[1], list(ALL_SKILLS))

    def test_completed_publish_cleanup_failure_prevents_version_and_success_reporting(self):
        for requested in ([], ['task-router']):
            with self.subTest(requested=requested):
                self.calls.clear()
                with mock.patch('install_transaction.cleanup_completed', side_effect=cli.install_transaction.InstallTransactionError('old copy is locked')):
                    output = self.expect_exit(1, cli.run_command, cli.INSTALL, make_context(self.home, 'codex', args=requested))
                self.assertIn('Code placement finished, but completed-publish cleanup failed', output)
                self.assertNotIn('write_event', self.names())


if __name__ == "__main__":
    unittest.main()
