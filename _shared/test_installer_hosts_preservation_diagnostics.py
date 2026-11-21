"""installer.hosts, installer.preservation and installer.diagnostics.

Expected arguments and texts come from installer_lib/install.sh:5-26, 225-362, 783-847,
installer_lib/bootstrap.sh, installer_lib/targets.sh:378-395 and installer_lib/uninstall.sh:142-267.
"""
from __future__ import annotations

import contextlib
import io
import json
from dataclasses import asdict
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_digest  # noqa: E402
import install_lock  # noqa: E402
import install_transaction  # noqa: E402
import installed_payload  # noqa: E402
from installer import diagnostics, hosts, preservation, selection  # noqa: E402
from installer.runner import ToolResult  # noqa: E402
from installer.selection import ALL_SKILLS, Target  # noqa: E402
from installer_testing import FakeRunner, hook_status, make_context, make_source, quiet, use_source  # noqa: E402


class HostCase(unittest.TestCase):
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
        self.runner = FakeRunner()
        self.ghost = self.home / ".ghost-alice"

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def context(self, platform="claude", **options):
        ctx = make_context(self.home, platform, **options)
        ctx.skills_dir.mkdir(parents=True, exist_ok=True)
        return ctx

    @staticmethod
    def captured(fn, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn(*args, **kwargs)
        return result, out.getvalue()


class WriterObservationTests(unittest.TestCase):
    def test_observation_preserves_program_names_and_installer_ancestor(self):
        rows = '10 1 /Applications/Claude.app/Contents/MacOS/Claude\n20 10 /bin/zsh\n30 20 /usr/bin/python3\n'
        with mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, rows)), \
                mock.patch.object(hosts.os, 'getpid', return_value=30), \
                mock.patch.object(hosts, '_process_argv', return_value=['python3', '-c', 'pass']):
            seen = hosts.observe_writers(['claude'], platform='darwin')['claude']
        self.assertEqual(seen.state, 'running')
        self.assertEqual(seen.programs, ((10, 'Claude'),))
        self.assertEqual(seen.ancestor_pids, (10,))

    def test_posix_native_identity_and_surviving_ledger_cli_are_distinct_from_argument_mentions(self):
        processes = ('10 1 /Applications/Codex.app/Contents/MacOS/Codex\n'
                     '11 1 /usr/bin/python3\n12 1 /bin/echo\n')
        argv = ['/usr/bin/python3', '/tmp/path with spaces/session_intent_ledger.py', '--platform', 'claude']
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform), mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, processes)), \
                    mock.patch.object(hosts, '_process_argv', return_value=argv):
                seen = hosts.observe_writers(['claude', 'codex'], platform=platform)
                self.assertEqual((seen['codex'].state, seen['codex'].pids), ('running', (10,)))
                self.assertEqual((seen['claude'].state, seen['claude'].pids), ('running', (11,)))
        with mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, '12 1 /bin/echo\n')):
            self.assertEqual(hosts.observe_writers(['codex'], platform='darwin')['codex'].state, 'absent')

    def test_windows_native_executable_and_python_script_arguments(self):
        rows = [{'ProcessId': 10, 'ParentProcessId': 1, 'Name': 'Codex.exe',
                 'ExecutablePath': r'C:\Program Files\Codex\Codex.exe', 'CommandLine': 'Codex.exe', 'MainWindowHandle': 123},
                {'ProcessId': 11, 'ParentProcessId': 1, 'Name': 'python.exe', 'ExecutablePath': r'C:\Python\python.exe',
                 'CommandLine': 'python.exe "C:\\work files\\session_intent_ledger.py" --platform claude', 'MainWindowHandle': 0}]
        with mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, json.dumps(rows))):
            seen = hosts.observe_writers(['claude', 'codex'], platform='win32')
        self.assertEqual((seen['codex'].state, seen['codex'].pids), ('running', (10,)))
        self.assertEqual((seen['claude'].state, seen['claude'].pids), ('running', (11,)))
        self.assertEqual(seen['codex'].manual_pids, ())
        self.assertEqual(seen['claude'].manual_pids, (11,))
        self.assertEqual(seen['claude'].programs, ((11, 'python.exe (session_intent_ledger.py)'),))
        self.assertIn('No main window was observed', seen['claude'].detail)
        rows[0].update(MainWindowHandle=None, WindowLookupError='Access denied')
        with mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, json.dumps(rows))):
            seen = hosts.observe_writers(['codex'], platform='win32')['codex']
        self.assertEqual((seen.state, seen.manual_pids), ('running', (10,)))
        self.assertIn('Main-window lookup unavailable: Access denied', seen.detail)

    def test_node_host_launcher_and_bound_hook_runner_survive_without_native_host_parent(self):
        command = 'python3 /runtime/_shared/hook_profile_gate.py run session-intent claude eA=='
        rows = '10 1 /usr/bin/node\n11 1 /usr/bin/python3\n'
        argv = {10: ['node', '/usr/local/bin/codex'], 11: command.split()}
        with mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, rows)), \
                mock.patch.object(hosts, '_process_argv', side_effect=lambda pid, platform: argv[pid]):
            seen = hosts.observe_writers(['claude', 'codex'], platform='darwin')
        self.assertEqual(seen['codex'].pids, (10,))
        self.assertEqual(seen['claude'].pids, (11,))

    def test_unbound_writer_is_unknown_and_unrelated_python_argument_mentions_are_absent(self):
        rows = '11 1 /usr/bin/python3\n'
        cases = [(['python3', '/tmp/session_intent_ledger.py'], 'unknown'),
                 (['python3', '/runtime/response_route.py', '--platform', 'codex', '--candidate-json', '{}'], 'running'),
                 (['python3', '/runtime/context_lifecycle.py', '--platform=codex', '--selection-json', '[]'], 'running'),
                 (['python3', '/runtime/source_lookup.py', '/tmp/source', '--platform', 'codex', '--purpose', 'review'], 'running'),
                 (['python3', '/runtime/completion_assessment.py', 'record-response'], 'unknown'),
                 (['python3', '/runtime/context_lifecycle.py', '--selection-json', '[]'], 'unknown'),
                 (['python3', '/tmp/other.py', 'session_intent_ledger.py', '--platform', 'codex'], 'absent'),
                 (['python3', '/tmp/other.py', 'response_route.py', '--platform', 'codex'], 'absent'),
                 (['python3', '-W', 'session_intent_ledger.py', '/tmp/other.py'], 'absent'),
                 (['python3', '-c', 'print("session_intent_ledger.py")'], 'absent')]
        for options in (['-X', 'utf8'], ['-W', 'ignore'], ['-Xutf8'], ['-IBu'], ['--'],
                        ['-uW', 'ignore'], ['-IX', 'utf8'], ['-uWignore'], ['-IXutf8'],
                        ['--check-hash-based-pycs', 'always']):
            cases.append((['python3', *options, '/runtime/session_intent_ledger.py', '--platform', 'codex'], 'running'))
        for argv, expected in cases:
            with self.subTest(argv=argv), mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, rows)), \
                    mock.patch.object(hosts, '_process_argv', return_value=argv):
                self.assertEqual(hosts.observe_writers(['codex'], platform='darwin')['codex'].state, expected)

    def test_unbound_record_cli_is_not_attributed_to_its_native_ancestor_host(self):
        rows = ('10 1 /Applications/Codex.app/Contents/MacOS/Codex\n'
                '11 10 /bin/sh\n12 11 /usr/bin/python3\n')
        for platform, script in ((platform, script) for platform in ('darwin', 'linux')
                                for script in ('session_intent_ledger.py', 'completion_assessment.py')):
            argv = ['python3', '/runtime/' + script]
            with self.subTest(platform=platform, script=script), \
                    mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, rows)), \
                    mock.patch.object(hosts, '_process_argv', return_value=argv):
                seen = hosts.observe_writers(['claude', 'codex'], platform=platform)
                self.assertEqual((seen['codex'].state, seen['codex'].pids), ('running', (10,)))
                self.assertEqual(seen['claude'].state, 'unknown')
                self.assertEqual(hosts.observe_writers(['claude'], platform=platform)['claude'].state, 'unknown')

    def test_unavailable_process_table_or_interpreter_arguments_is_unknown(self):
        with mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, '12 1 /bin/echo\n')):
            unsupported = hosts.observe_writers(['future-host'], platform='darwin')['future-host']
        self.assertEqual(unsupported.state, 'unknown')
        self.assertIn('adapter', unsupported.detail.lower())
        for output in (ToolResult(1, '', 'denied'), ToolResult(0, 'malformed'), ToolResult(0, '')):
            with self.subTest(output=output), mock.patch.object(hosts.runner, 'run_command', return_value=output):
                seen = hosts.observe_writers(['codex'], platform='darwin')['codex']
                self.assertEqual(seen.state, 'unknown')
                self.assertTrue(seen.detail)
        with mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, '11 1 /usr/bin/python3\n')), \
                mock.patch.object(hosts, '_process_argv', side_effect=PermissionError('denied')):
            seen = hosts.observe_writers(['codex'], platform='darwin')['codex']
        self.assertEqual(seen.state, 'unknown')
        self.assertIn('11', seen.detail)
        for platform in ('darwin', 'linux'):
            for exited in (FileNotFoundError('exited'), ProcessLookupError('exited')):
                with self.subTest(platform=platform, exited=type(exited).__name__), \
                        mock.patch.object(hosts.runner, 'run_command', return_value=ToolResult(0, '11 1 /usr/bin/python3\n')), \
                        mock.patch.object(hosts, '_process_argv', side_effect=exited):
                    seen = hosts.observe_writers(['codex'], platform=platform)['codex']
                    self.assertEqual((seen.state, seen.pids), ('absent', ()))

    @unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'local POSIX process lifetime evidence')
    def test_real_ledger_cli_is_observed_until_it_exits_without_host_termination(self):
        with tempfile.TemporaryDirectory(prefix='ghost writer ') as directory:
            script = Path(directory) / 'session_intent_ledger.py'
            script.write_text('import sys\nprint("ready", flush=True)\nsys.stdin.read()\n')
            process = subprocess.Popen([sys.executable, str(script), '--platform', 'codex'],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                seen = hosts.observe_writers(['codex'])['codex']
                self.assertIn(process.pid, seen.pids)
            finally:
                process.stdin.close()
                process.wait(timeout=5)
                process.stdout.close()
            self.assertNotIn(process.pid, hosts.observe_writers(['codex'])['codex'].pids)


class WriterTerminationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'local POSIX process termination evidence')
    def test_normal_request_allows_own_ledger_cli_to_save_and_exit(self):
        with tempfile.TemporaryDirectory(prefix='ghost writer ') as directory:
            script = Path(directory) / 'session_intent_ledger.py'
            marker = Path(directory) / 'saved.txt'
            script.write_text('import signal, sys\nfrom pathlib import Path\n'
                              'def stop(signum, frame):\n'
                              '    Path(sys.argv[-1]).write_text("saved before exit")\n'
                              '    sys.exit(0)\n'
                              'signal.signal(signal.SIGTERM, stop)\n'
                              'print("ready", flush=True)\nsys.stdin.read()\n')
            process = subprocess.Popen([sys.executable, str(script), '--platform', 'codex', str(marker)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                self.assertIn(process.pid, hosts.observe_writers(['codex'])['codex'].pids)
                self.assertEqual(hosts.request_writer_termination([process.pid]), {})
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual(marker.read_text(), 'saved before exit')
                self.assertNotIn(process.pid, hosts.observe_writers(['codex'])['codex'].pids)
            finally:
                process.stdin.close()
                process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def test_posix_requests_once_per_pid_and_retains_failures_except_disappearance(self):
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform), mock.patch.object(hosts.os, 'kill',
                    side_effect=[None, ProcessLookupError('gone'), PermissionError('denied'), OSError('busy')]) as kill:
                errors = hosts.request_writer_termination([13, 11, 13, 12, 14], platform=platform)
                self.assertEqual(errors, {12: 'denied', 14: 'busy'})
                self.assertEqual(kill.call_args_list, [mock.call(pid, signal.SIGTERM) for pid in (13, 11, 12, 14)])

    def test_windows_requests_close_main_window_for_exact_pids_and_retains_failures(self):
        results = [ToolResult(0), ToolResult(1, '', 'refused'), ToolResult(1, 'command failed', ''),
                   ToolResult(1), PermissionError('denied')]
        with mock.patch.object(hosts.runner, 'run_command', side_effect=results) as run:
            errors = hosts.request_writer_termination([11, 12, 11, 13, 14, 15], platform='win32')
        self.assertEqual({pid: errors[pid] for pid in (12, 13, 15)},
                         {12: 'refused', 13: 'command failed', 15: 'denied'})
        self.assertEqual(set(errors), {12, 13, 14, 15})
        self.assertTrue(errors[14])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([command[:4] for command in commands],
                         [['powershell.exe', '-NoProfile', '-NonInteractive', '-Command']] * 5)
        self.assertEqual([int(command[-1].split('Get-Process -Id ', 1)[1].split()[0]) for command in commands],
                         [11, 12, 13, 14, 15])
        for command in commands:
            self.assertIn('.CloseMainWindow()', command[-1])
            self.assertNotIn('Stop-Process', command[-1])
            self.assertNotIn('taskkill', command[-1])

    def test_invalid_pid_or_unsupported_platform_rejects_entire_request_before_side_effects(self):
        with mock.patch.object(hosts.os, 'kill') as kill, mock.patch.object(hosts.runner, 'run_command') as run:
            for platform in ('darwin', 'linux', 'win32'):
                for invalid in (0, -1, True, '12', 1.5, None):
                    with self.subTest(platform=platform, invalid=invalid), self.assertRaises(ValueError):
                        hosts.request_writer_termination([11, invalid], platform=platform)
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                hosts.request_writer_termination([11], platform='unsupported')
            kill.assert_not_called()
            run.assert_not_called()


class HookRegistrationTests(HostCase):
    def test_downloaded_installer_confirms_the_recorded_project_or_the_original_checkout(self):
        config = self.home / '.codex/config.toml'
        config.parent.mkdir()
        project = self.home / 'codex-project'
        origin = self.home / 'original-checkout'
        config.write_text(''.join(f'[projects.{json.dumps(str(path))}]\ntrust_level = "untrusted"\n'
                                  for path in (origin, project)))
        with mock.patch.object(hosts.source, 'interactive', return_value=True), mock.patch('builtins.input', return_value='y'):
            for saved, expected in (({}, origin), ({'project_root': str(project)}, project)):
                with self.subTest(saved=saved), mock.patch('install_hooks.observed_installation', return_value=saved):
                    approved, _ = self.captured(hosts.confirm_project_trust, ['codex'], project_root=origin)
                    self.assertEqual(approved, str(expected))

    def test_trust_confirmation_targets_original_project_during_candidate_retry(self):
        from installer import source
        config = self.home / '.codex/config.toml'
        config.parent.mkdir()
        project = str(self.source.resolve())
        config.write_text(f'[projects.{json.dumps(project)}]\ntrust_level = "untrusted"\n')
        ctx = self.context('codex')
        with install_lock.command_lock(self.ghost / 'install.lock', owner='test'):
            folder, _ = source.prepare_source(ctx)
        for interactive, answer, expected in ((True, 'yes', project), (True, 'no', ''),
                                                (True, EOFError(), ''), (False, 'yes', '')):
            with self.subTest(interactive=interactive, answer=answer), use_source(folder / 'core'), \
                    mock.patch.dict(os.environ, {'GHOST_ALICE_CORE_ROOT': str(folder / 'core')}), \
                    mock.patch.object(source, 'interactive', return_value=interactive), \
                    mock.patch('builtins.input', side_effect=answer if isinstance(answer, Exception) else None,
                               return_value=answer):
                approved, output = self.captured(hosts.confirm_project_trust, ['claude', 'codex'])
                self.assertEqual(approved, expected)
                self.assertIn(project, output)
                self.assertIn('untrusted', config.read_text())
        before = config.read_bytes()
        with mock.patch.object(source, 'interactive', return_value=True), \
                mock.patch.object(hosts.terminal, '_discard_previous_input', side_effect=OSError('input failed')):
            approved, output = self.captured(hosts.confirm_project_trust, ['codex'])
        self.assertEqual(approved, '')
        self.assertEqual(config.read_bytes(), before)
        self.assertIn('input failed', output)

    def test_child_registration_states_remain_distinct_and_invalid_evidence_is_error(self):
        ctx = self.context('claude')
        for status in ('installed', 'already', 'skipped', 'error'):
            with self.subTest(status=status):
                code = 1 if status == 'error' else 0
                body = dict(returncode=code, results={'claude': asdict(hook_status(status=status))})
                with mock.patch('installer.runner.run_tool', return_value=ToolResult(code, json.dumps(body), 'child message')):
                    result, output = self.captured(hosts.install_hooks, ctx, 'install', 'claude')
                self.assertEqual(result.legacy_status, status)
                self.assertIn('child message', output)
        valid = asdict(hook_status())
        invalid = [ToolResult(0, ''), ToolResult(0, '{'),
                   ToolResult(1, json.dumps(dict(returncode=1, results={'claude': valid}))),
                   ToolResult(0, json.dumps(dict(returncode=0, results={'claude': asdict(hook_status(status='error'))}))),
                   ToolResult(0, json.dumps(dict(returncode=0, results={'codex': valid}))),
                   ToolResult(0, json.dumps(dict(returncode=0, results={'claude': dict(valid, details={'action': 'uninstall'})}))),
                   ToolResult(0, json.dumps(dict(returncode=False, results={'claude': valid})))]
        for result in invalid:
            with self.subTest(result=result), mock.patch('installer.runner.run_tool', return_value=result):
                status, _ = self.captured(hosts.install_hooks, ctx, 'install', 'claude')
                self.assertEqual(status.legacy_status, 'error')

    def test_install_hooks_arguments_per_action(self):
        # install.sh:783-847; status and doctor pass no visibility
        ctx = self.context("claude", addon_sources=["/a"])
        with self.runner.patch():
            ok, out = self.captured(hosts.install_hooks, ctx, "install", "claude")
        self.assertEqual(ok.legacy_status, 'installed')
        self.assertIn("[INFO] Agent hook install... (session-intent + tool-checkpoint + completion + "
                      "session-start + session-receipt + io-trace)", out)
        base = ["--platform", "claude",
                "--hook-shared-dir", str(self.ghost / "runtime" / "current" / "_shared"), "--skills-dir", str(ctx.skills_dir)]
        self.assertEqual(self.runner.tool_calls("install_hooks.py")[0], base + ["--core-source-root", str(self.source), "--visibility", "dynamic", "--addon-source", "/a", '--format', 'json'])
        strict = FakeRunner()
        with strict.patch():
            hosts.install_hooks(self.context("codex", visibility="strict", addon_sources=["/a"]), "install", "codex")
        self.assertEqual(strict.tool_calls("install_hooks.py")[0][8:10], ["--visibility", "strict"])
        status = FakeRunner()
        with status.patch():
            hosts.install_hooks(self.context("claude", visibility="minimal", addon_sources=["/a"]), "status", "claude")
        self.assertEqual(status.tool_calls("install_hooks.py")[0], base + ["--status", "--addon-source", "/a", '--format', 'json'])
        settings = self.home / '.claude/settings.json'
        settings.parent.mkdir(exist_ok=True)
        data = dict(custom='command-start', hooks={'UserPromptSubmit': [dict(hooks=[
            dict(type='command', command='echo [session-intent-analyzer]')])]})
        original = json.dumps(data).encode()
        data['custom'] = 'earlier-owner-effect'
        settings.write_text(json.dumps(data))
        result, _ = self.captured(hosts.install_hooks, ctx, 'uninstall', 'claude',
                                 backup_sources={settings: original})
        self.assertEqual(result.legacy_status, 'removed')
        self.assertEqual(settings.with_suffix('.json.bak').read_bytes(), original)
        self.assertEqual(json.loads(settings.read_text())['custom'], 'earlier-owner-effect')
        failing = FakeRunner()
        failing.respond_to("install_hooks.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(hosts.install_hooks, ctx, "install", "claude")
        self.assertEqual(ok.legacy_status, 'error')
        self.assertIn('[ERROR] Hook install returned an invalid result', out)

    def test_bootstrap_blocks(self):
        # bootstrap.sh:12-38, 86-112
        ctx = self.context("claude")
        with self.runner.patch():
            ok, out = self.captured(hosts.ensure_claude_bootstrap, ctx)
        self.assertTrue(ok)
        self.assertIn("[OK] Claude CLAUDE.md bootstrap block updated", out)
        self.assertTrue((self.home / ".claude").is_dir())
        rules = self.home / ".claude" / "CLAUDE.md"
        self.assertEqual(self.runner.tool_calls("global_rule_blocks.py")[0],
                         ["claude-merge", "--source", str(self.source / "platforms" / "claude" / "CLAUDE.md"),
                          "--dest", str(rules), "--proposed", f"{rules}.ghost-alice-proposed"])
        proposed = FakeRunner()
        proposed.respond_to("global_rule_blocks.py", ToolResult(0, f"proposed:{rules}.ghost-alice-proposed\n", ""))
        with proposed.patch():
            ok, out = self.captured(hosts.ensure_claude_bootstrap, ctx)
        self.assertTrue(ok)
        self.assertIn(f"[WARN] Claude CLAUDE.md has no Ghost-ALICE block; proposed file available: {rules}.ghost-alice-proposed", out)
        codex = FakeRunner()
        with codex.patch():
            ok, out = self.captured(hosts.ensure_codex_bootstrap, ctx)
        self.assertTrue(ok)
        self.assertIn("[OK] Codex AGENTS.md bootstrap block updated", out)
        agents = self.home / ".codex" / "AGENTS.md"
        self.assertEqual(codex.tool_calls("global_rule_blocks.py")[0],
                         ["codex-merge", "--source", str(self.source / "platforms" / "codex" / "AGENTS.md"),
                          "--loader", str(self.source / "platforms" / "codex" / "bootstrap.md"),
                          "--dest", str(agents), "--proposed", f"{agents}.ghost-alice-proposed"])
        failing = FakeRunner()
        failing.respond_to("global_rule_blocks.py", ToolResult(1, "", "boom\n"))
        with failing.patch():
            ok, out = self.captured(hosts.ensure_codex_bootstrap, ctx)
        self.assertFalse(ok)
        self.assertIn("[ERROR] Codex AGENTS.md block merge failed; aborting install", out)

    def test_bootstrap_removal(self):
        # bootstrap.sh:40-84, 114-152
        ctx = self.context("claude")
        self.assertTrue(hosts.remove_claude_bootstrap(ctx))
        self.assertFalse(hosts.remove_claude_bootstrap(ctx, require_change=True))
        rules = self.home / ".claude" / "CLAUDE.md"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text("# Ghost-ALICE Claude Bootstrap\n", encoding="utf-8")
        removed = FakeRunner()
        removed.respond_to("global_rule_blocks.py", ToolResult(0, f"removed:{rules}\n", ""))
        with removed.patch():
            ok, out = self.captured(hosts.remove_claude_bootstrap_if_unused, ctx, ctx.skills_dir)
        self.assertTrue(ok)
        self.assertIn("[OK] Claude CLAUDE.md bootstrap removed", out)
        self.assertEqual(removed.tool_calls("global_rule_blocks.py")[0], ["claude-remove", "--dest", str(rules)])
        updated = FakeRunner()
        updated.respond_to("global_rule_blocks.py", ToolResult(0, f"updated:{rules}\n", ""))
        with updated.patch():
            ok, out = self.captured(hosts.remove_claude_bootstrap, ctx, True)
        self.assertTrue(ok)
        self.assertIn("[OK] Claude CLAUDE.md Ghost-ALICE block removed; content outside the block preserved", out)
        unchanged = FakeRunner()
        unchanged.respond_to("global_rule_blocks.py", ToolResult(0, "unchanged\n", ""))
        with unchanged.patch():
            self.assertFalse(hosts.remove_claude_bootstrap(ctx, True))
            self.assertTrue(hosts.remove_claude_bootstrap(ctx, False))
        (ctx.skills_dir / "task-router").mkdir()
        with FakeRunner().patch():
            self.assertFalse(hosts.remove_claude_bootstrap_if_unused(ctx, ctx.skills_dir))
        codex_ctx = self.context("codex")
        self.assertFalse(hosts.remove_codex_bootstrap_if_unused(codex_ctx, codex_ctx.skills_dir))
        agents = self.home / ".codex" / "AGENTS.md"
        agents.parent.mkdir(parents=True)
        agents.write_text("x", encoding="utf-8")
        removed = FakeRunner()
        removed.respond_to("global_rule_blocks.py", ToolResult(0, f"removed:{agents}\n", ""))
        with removed.patch():
            ok, out = self.captured(hosts.remove_codex_bootstrap_if_unused, codex_ctx, codex_ctx.skills_dir)
        self.assertTrue(ok)
        self.assertIn("[OK] Codex AGENTS.md bootstrap removed", out)
        self.assertEqual(removed.tool_calls("global_rule_blocks.py")[0], ["codex-remove", "--dest", str(agents)])

    def test_sync_commands(self):
        # installer_lib/install.sh:5-26
        ctx = self.context("claude")
        with self.runner.patch():
            self.assertEqual(hosts.sync_commands(ctx, "check", "claude"), 0)
        self.assertEqual(self.runner.tool_calls("commands_sync.py")[0],
                         [str(self.source / "skill-catalog" / "skills.json"), str(self.source / ".claude" / "commands"),
                          "check", "claude"])
        (self.source / "skill-catalog" / "skills.json").unlink()
        rc, out = self.captured(hosts.sync_commands, ctx)
        self.assertEqual(rc, 1)
        self.assertIn(f"[WARN] skills.json not found: {self.source / 'skill-catalog' / 'skills.json'}", out)


class PreservationTests(HostCase):
    def test_preservation_keeps_the_selected_family_after_source_changes(self):
        ctx = self.context("codex")
        collected = selection.collect_targets(ctx, ["fam"])
        self.assertEqual([target.name for target in collected.all_targets], ["one", "two"])
        shutil.rmtree(self.source / "fam" / "one")
        (self.source / "fam" / "three").mkdir()
        (self.source / "fam" / "three" / "SKILL.md").write_text("new source member")
        pending = self.ghost / "pending-merges" / "codex"
        pending.mkdir(parents=True)
        (pending / "snapshot.json").write_text("{}")
        with self.runner.patch():
            self.assertTrue(preservation.preflight(ctx, collected))
            self.assertTrue(preservation.encoding_guard(ctx, collected))
        args = self.runner.tool_calls("diff_collector_cli.py")[0]
        self.assertEqual([args[index + 1] for index, value in enumerate(args) if value == "--target"],
                         ["_shared", "one", "two"])
        guard_args = self.runner.tool_calls("encoding_guard.py")[-1]
        self.assertEqual([guard_args[index + 1] for index, value in enumerate(guard_args) if value == "--exclude-root"],
                         [str(ctx.skills_dir / "one"), str(ctx.skills_dir / "two")])

    def test_encoding_guard_sequence(self):
        # install.sh:225-271
        ctx = self.context("claude")
        with mock.patch("installer.addons.collect_addon_targets", return_value=[Target("noop", self.home / "addon" / "noop", "noop")]):
            collected = selection.collect_targets(ctx, ["alpha", "fam"])
        (ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        with self.runner.patch():
            self.assertTrue(preservation.encoding_guard(ctx, collected))
        calls = self.runner.tool_calls("encoding_guard.py")
        self.assertEqual(calls[0], ["--repo-root", str(self.source)])
        self.assertEqual(calls[1], ["--repo-root", str(self.home / "addon" / "noop")])
        self.assertEqual(calls[2], ["--repo-root", str(ctx.skills_dir), "--exclude-root", str(ctx.skills_dir / "_shared"),
                                    "--exclude-root", str(ctx.skills_dir / "alpha"), "--exclude-root", str(ctx.skills_dir / "one"),
                                    "--exclude-root", str(ctx.skills_dir / "two"), "--exclude-root", str(ctx.skills_dir / "noop")])
        failing = FakeRunner()
        failing.respond_to("encoding_guard.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(preservation.encoding_guard, ctx, collected)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] encoding guard failed; aborting install: {self.source}", out)
        missing = self.context("codex")
        missing.skills_dir.rmdir()
        with FakeRunner().patch() as _:
            self.assertTrue(preservation.encoding_guard(missing, selection.collect_targets(missing, ["alpha"])))

    def test_preflight_without_snapshot_quarantines_unclean_targets(self):
        # install.sh:273-337
        ctx = self.context("codex")
        collected = selection.collect_targets(ctx, ["alpha", "fam"])
        (ctx.skills_dir / "_shared").mkdir()
        (ctx.skills_dir / "alpha").mkdir()
        (ctx.skills_dir / "one").symlink_to(self.source / "fam" / "one")
        self.runner.respond(lambda s, a: a[:1] == ["classify-clean"] and "alpha" in a, ToolResult(1))
        with self.runner.patch():
            self.assertTrue(preservation.preflight(ctx, collected))
        classify = self.runner.tool_calls("installer_assets_cli.py")
        self.assertEqual(classify, [["classify-clean", "--asset-id", "_shared", "--path", str(ctx.skills_dir / "_shared"),
                                     "--repo-root", str(self.source)],
                                    ["classify-clean", "--asset-id", "alpha", "--path", str(ctx.skills_dir / "alpha"),
                                     "--repo-root", str(self.source)]])
        pending = self.ghost / "pending-merges" / "codex"
        self.assertEqual(self.runner.tool_calls("quarantine_legacy_cli.py"),
                         [["--target", str(ctx.skills_dir / "alpha"), "--target-name", "alpha", "--pending", str(pending),
                           "--manifest", str(pending / "manifest.json"), "--platform", "codex"]])
        self.assertTrue(pending.is_dir())
        failing = FakeRunner()
        failing.respond_to("installer_assets_cli.py", ToolResult(1))
        failing.respond_to("quarantine_legacy_cli.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(preservation.preflight, ctx, collected)
        self.assertFalse(ok)
        self.assertIn("[ERROR] unowned target quarantine failed; aborting install: _shared", out)

    def test_preflight_with_snapshot_runs_the_diff_collector(self):
        # install.sh:339-362
        ctx = self.context("codex")
        with mock.patch("installer.addons.collect_addon_targets", return_value=[Target("noop", self.home / "addon" / "noop", "noop")]):
            collected = selection.collect_targets(ctx, ["alpha"])
        pending = self.ghost / "pending-merges" / "codex"
        pending.mkdir(parents=True)
        (pending / "snapshot.json").write_text("{}", encoding="utf-8")
        with self.runner.patch():
            self.assertTrue(preservation.preflight(ctx, collected))
        self.assertEqual(self.runner.tool_calls("diff_collector_cli.py")[0],
                         ["--snapshot", str(pending / "snapshot.json"), "--pending", str(pending),
                          "--manifest", str(pending / "manifest.json"), "--platform", "codex",
                          "--skills-dir", str(ctx.skills_dir), "--install-record-root", str(self.ghost),
                          "--target", "_shared", "--target", "alpha", "--target", "noop"])
        failing = FakeRunner()
        failing.respond_to("diff_collector_cli.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(preservation.preflight, ctx, collected)
        self.assertFalse(ok)
        self.assertIn("[ERROR] merge-companion preflight change detection failed; aborting install", out)

    def test_cleanup_pending(self):
        # targets.sh:378-395
        ctx = self.context("claude")
        self.runner.respond_to("cleanup_false_positive_legacy.py", ToolResult(3))
        with self.runner.patch():
            self.assertEqual(preservation.cleanup_pending(ctx), 3)
        pending = self.ghost / "pending-merges" / "claude"
        self.assertEqual(self.runner.tool_calls("cleanup_false_positive_legacy.py")[0],
                         ["--platform", "claude", "--pending", str(pending), "--manifest", str(pending / "manifest.json"),
                          "--repo-root", str(self.source), "--apply"])


class InspectTests(HostCase):
    def test_record_source_outcomes_keep_distinct_actions_and_paths_in_each_language(self):
        original, destination = self.home / 'A', self.home / 'B'
        keys = ('record_source_removed', 'record_source_absent', 'record_source_failed')
        for language, actions in (('en', ('was deleted', 'is already absent', 'could not be deleted')),
                                  ('ko', ('삭제했습니다', '이미 없습니다', '삭제하지 못했습니다'))):
            with mock.patch.object(diagnostics.report.state(), 'language', language):
                for key, action in zip(keys, actions):
                    with self.subTest(language=language, action=key):
                        output = diagnostics.report.message(key, current_path=original, new_path=destination)
                        self.assertIn(action, output)
                        self.assertIn(str(original), output)
                        self.assertIn(str(destination), output)

    def test_print_target_status(self):
        # uninstall.sh:142-155
        link = self.home / "linked"
        link.symlink_to(self.source / "alpha")
        copy = self.home / "copied"
        copy.mkdir()
        _, out = self.captured(diagnostics.print_target_status, "alpha", link)
        self.assertEqual(out, f"  ● alpha  →  {self.source / 'alpha'}\n")
        _, out = self.captured(diagnostics.print_target_status, "beta", copy, "    ")
        self.assertEqual(out, "    ■ beta  (copied)\n")
        _, out = self.captured(diagnostics.print_target_status, "gamma", self.home / "none")
        self.assertEqual(out, "  ○ gamma  (not installed)\n")

    def test_run_install_doctor_arguments(self):
        # uninstall.sh:157-218
        ctx = self.context("codex")
        with self.runner.patch(), mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(self.home / "other-root")}):
            self.assertEqual(diagnostics.run_install_doctor(ctx, "doctor", ctx.skills_dir), 0)
        args = self.runner.tool_calls("install_doctor.py")[0]
        self.assertEqual(args[:2], ["--platform", "codex"])
        self.assertEqual(args[args.index("--repo-root") + 1], str(self.source))
        self.assertEqual(args[args.index("--encoding-root") + 1], str(self.source))
        self.assertEqual(args[args.index("--encoding-root") + 3], str(ctx.skills_dir))
        self.assertEqual(args[args.index("--ghost-alice-root") + 1], str(self.ghost))
        self.assertEqual(args[args.index("--install-state-manifest") + 1], str(self.ghost / "install-state" / "codex.json"))
        self.assertEqual(args[args.index("--runtime-shared") + 1], str(self.ghost / "runtime" / "current" / "_shared"))
        self.assertEqual(args[args.index("--skills-root") + 1], str(ctx.skills_dir))
        self.assertIn("--strict", args)
        self.assertEqual(args[args.index("--hook-config") + 1], str(self.home / ".codex" / "hooks.json"))
        gr = args.index("--global-rule")
        self.assertEqual(args[gr + 1:gr + 6], ["codex-bootstrap", str(self.home / ".codex" / "AGENTS.md"),
                                              "# Ghost-ALICE Codex Bootstrap",
                                              "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->",
                                              "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"])
        targets = [args[i + 1] for i, t in enumerate(args) if t == "--target"]
        self.assertEqual(targets[0], "_shared")
        self.assertEqual(args[args.index("--target") + 2], str(ctx.skills_dir / "_shared"))
        self.assertEqual(targets[1:], [name for skill in ALL_SKILLS for name in
                                       ([skill] if (self.source / skill / "SKILL.md").is_file() else
                                        [p.name for p in sorted((self.source / skill).iterdir()) if (p / "SKILL.md").is_file()]
                                        if (self.source / skill).is_dir() else [])])
        status = FakeRunner()
        with status.patch():
            diagnostics.run_install_doctor(self.context("claude"), "status", self.home / ".claude" / "skills")
        args = status.tool_calls("install_doctor.py")[0]
        self.assertNotIn("--strict", args)
        self.assertEqual(args[args.index("--hook-config") + 1], str(self.home / ".claude" / "settings.json"))
        self.assertEqual(args[args.index("--global-rule") + 1:args.index("--global-rule") + 3],
                         ["claude-bootstrap", str(self.home / ".claude" / "CLAUDE.md")])

    def test_check_status_order(self):
        # uninstall.sh:220-246
        ctx = self.context("claude")
        with self.runner.patch():
            rc, out = self.captured(diagnostics.check_status, ctx)
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith(f"Install status ({ctx.skills_dir}):\n\n  ○ _shared  (not installed)\n"))
        self.assertIn("  ○ adversarial-verification  (source missing)\n", out)
        self.assertIn("[INFO] Running installer doctor diagnostics...", out)
        self.assertEqual([c[0].rsplit("/", 1)[-1] for c in self.runner.calls if c[0] != "command"],
                         ["install_doctor.py", "install_hooks.py"])
        self.assertIn("--status", self.runner.tool_calls("install_hooks.py")[0])

    def test_run_doctor_reports_leftover_markers_and_never_resumes(self):
        # uninstall.sh:248-267 without the resume
        ctx = self.context("claude")
        with self.runner.patch():
            rc, out = self.captured(diagnostics.run_doctor, ctx)
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith(f"Installer doctor ({ctx.skills_dir}):\n"))
        self.assertEqual([c[0].rsplit("/", 1)[-1] for c in self.runner.calls if c[0] != "command"],
                         ["install_doctor.py", "install_hooks.py"])
        self.assertIn("--strict", self.runner.tool_calls("install_doctor.py")[0])
        addons_dir = self.ghost / "addons" / "claude"
        addons_dir.mkdir(parents=True)
        (addons_dir / "noop.json.removing").write_text("{}", encoding="utf-8")
        again = FakeRunner()
        with again.patch():
            rc, out = self.captured(diagnostics.run_doctor, ctx)
        self.assertEqual(rc, 1)
        self.assertIn("[WARN] A pending addon uninstall could not complete (the human user must review the kept items); see .removing markers "
                      "under ~/.ghost-alice/addons", out)
        self.assertNotIn("--resume-pending", [a for _, args, _ in again.calls for a in args])
        # uninstall.sh:264-265: the doctor's own code propagates; a hook status failure is 1
        failing = FakeRunner()
        failing.respond_to("install_doctor.py", ToolResult(2))
        with failing.patch():
            rc, _ = self.captured(diagnostics.run_doctor, self.context("codex"))
        self.assertEqual(rc, 2)
        hooks_failing = FakeRunner()
        hooks_failing.respond_to("install_hooks.py", ToolResult(2))
        with hooks_failing.patch():
            rc, _ = self.captured(diagnostics.run_doctor, self.context("codex"))
        self.assertEqual(rc, 1)


class InstallRecordReportTests(HostCase):
    """`--status` and `--doctor` read the install record and report it; their result stays what the checks decide."""

    def write_record(self, targets, **sections):
        lock = self.ghost / "install.lock"
        generation = install_lock.acquire_lock(lock, owner="test")
        try:
            installed_payload.update(self.ghost, {"targets": targets, **sections})
        finally:
            install_lock.release_lock(lock, generation)

    def write_cleanup_journal(self, original, destination, phase='done'):
        lock = self.ghost / 'install.lock'
        generation = install_lock.acquire_lock(lock, owner='test')
        try:
            journal = install_transaction.Journal.begin(dict(ghost_root=str(self.ghost), host='installer'), [])
            journal.data['source_cleanup'] = dict(path=str(original), destination=str(destination),
                                                  baseline=content_digest.state(original, include_generated=True))
            journal.mark(phase)
            return journal
        finally:
            install_lock.release_lock(lock, generation)

    @staticmethod
    def entry(mode, used_by, **fields):
        entry = {"name": "x", "kind": "skill", "mode": mode, "provider": "core", "source": "/src/x",
                 "source_commit": "abc", "used_by": used_by, "installed_at": "2026-10-20T00:00:00+00:00",
                 "baseline": "recorded"}
        entry.update(fields)
        return entry

    def run_both(self, ctx):
        for run in (diagnostics.check_status, diagnostics.run_doctor):
            runner = FakeRunner()
            with runner.patch():
                rc, out = self.captured(run, ctx)
            yield run.__name__, rc, out

    def test_no_install_record_yet(self):
        record = installed_payload.path(self.ghost)
        for name, rc, out in self.run_both(self.context("claude")):
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn(f"[INFO] Install record: none yet; the next install writes {record}", out)

    def test_doctor_does_not_describe_retired_hook_binding_as_current_location(self):
        from installer import transaction
        root = self.home / 'retired-A'
        observed = transaction.LedgerLocations({root: dict(state='retired', hosts={'claude': {}}, source_root=None)})
        with mock.patch.object(diagnostics.record_move, 'read_locations', return_value=observed):
            runner = FakeRunner()
            with runner.patch():
                rc, output = self.captured(diagnostics.run_doctor, self.context('claude'))
        self.assertEqual(rc, 0)
        self.assertNotIn(str(root), output)

    def test_unconfirmed_hook_location_is_not_presented_as_current_or_retired(self):
        from installer import lifecycle, report, transaction
        root = self.home / 'observed-hook-records'
        observed = transaction.LedgerLocations({root: dict(state='unconfirmed', hosts={'claude': {}},
                                                          source_root=self.source)})
        for language, expected, incorrect in (
                ('en', 'could not confirm whether it is current or retired', 'The current work record location'),
                ('ko', '현재 사용 중인지 사용이 종료됐는지 확인하지 못했습니다', '현재 작업 기록 위치는')):
            for command in (diagnostics.run_doctor, lifecycle.run_full_uninstall):
                with self.subTest(language=language, command=command.__name__), \
                        mock.patch.object(report.state(), 'language', language), \
                        mock.patch.object(diagnostics.record_move, 'read_locations', return_value=observed), \
                        FakeRunner().patch():
                    _, output = self.captured(command, self.context('claude'))
                    self.assertIn(str(root), output)
                    self.assertIn(expected, output)
                    self.assertNotIn(incorrect, output)

    def test_bad_payload_does_not_invalidate_configured_or_retired_location(self):
        import runtime_config
        from installer import lifecycle, report
        active, retired = self.home.resolve() / 'configured-records', self.home.resolve() / 'retired-records'
        runtime_config.save_config({'install_locations': {
            'claude': {'ledger_root': str(active)}, 'codex': {'ledger_root': str(retired)}},
            'retired_ledger_roots': [str(retired)]})
        record = installed_payload.path(self.ghost)
        record.write_text('not JSON', encoding='utf-8')
        for command in (diagnostics.run_doctor, lifecycle.run_full_uninstall):
            for language in ('en', 'ko'):
                with self.subTest(command=command.__name__, language=language), \
                        mock.patch.object(report.state(), 'language', language), FakeRunner().patch():
                    _, output = self.captured(command, self.context('claude'))
                    self.assertIn(str(record), output)
                    self.assertIn(report.message('move_relation_unknown', current_path=active), output)
                    self.assertNotIn(str(retired), output)
                    self.assertNotIn(report.message('move_location_unconfirmed', current_path=active), output)

    def test_completed_record_cleanup_reports_remaining_source_without_changes(self):
        ctx = self.context('claude')
        original, destination = self.home / 'A', self.home / 'B'
        original.mkdir()
        destination.mkdir()
        (original / 'keep.txt').write_text('original source')
        (destination / 'new-input.txt').write_text('new B input')
        journal = self.write_cleanup_journal(original, destination)
        (original / 'keep.txt').write_text('source changed after the cleanup baseline')
        removed, restored = self.home / 'already-removed', self.home / 'restored-source'
        removed.mkdir()
        restored.mkdir()
        self.write_cleanup_journal(removed, destination)
        self.write_cleanup_journal(restored, destination, 'restored')
        removed.rmdir()
        before = content_digest.state(self.home, include_generated=True)
        for language, pending, retry, preserve in (
                ('en', 'Old record folder deletion is pending', 'it can fail again', 'resolve its reported cause'),
                ('ko', '옛 기록 폴더 삭제가 대기 중입니다', '다시 실패할 수 있습니다', '원인을 확인하고 해결')):
            with mock.patch.object(diagnostics.report.state(), 'language', language):
                for name, rc, output in self.run_both(ctx):
                    with self.subTest(language=language, command=name):
                        self.assertIn(pending, output)
                        self.assertIn(str(original), output)
                        self.assertIn(str(destination), output)
                        self.assertIn(str(journal.folder / install_transaction.JOURNAL_FILENAME), output)
                        self.assertIn(retry, output)
                        self.assertIn(preserve, output)
                        self.assertNotIn(str(removed), output)
                        self.assertNotIn(str(restored), output)
                        self.assertEqual(rc, 1 if name == 'run_doctor' else 0)
                        self.assertEqual(content_digest.state(self.home, include_generated=True), before)

    def test_cleanup_journal_inspection_rejects_links_and_malformed_records(self):
        ctx = self.context('claude')
        original, destination = self.home / 'A', self.home / 'B'
        original.mkdir()
        destination.mkdir()
        (original / 'keep.txt').write_text('source to preserve')
        journal = self.write_cleanup_journal(original, destination)
        journal_path = journal.folder / install_transaction.JOURNAL_FILENAME
        original_journal = journal_path.read_bytes()
        for case in ('root-link', 'entry-link', 'journal-link', 'cleanup-shape', 'cleanup-path',
                     'unsupported-schema', 'wrong-txid', 'target-element', 'record-copy'):
            target = {'root-link': install_transaction.rollbacks_root(self.ghost),
                      'entry-link': journal.folder, 'journal-link': journal_path}.get(case)
            outside = self.home / ('outside-' + case)
            if target is not None:
                target.rename(outside)
                target.symlink_to(outside, target_is_directory=outside.is_dir())
            else:
                data = json.loads(original_journal)
                if case == 'cleanup-shape':
                    data['source_cleanup'] = []
                elif case == 'cleanup-path':
                    data['source_cleanup']['path'] = 'relative-records'
                elif case == 'unsupported-schema':
                    data['schema_version'] = 99
                elif case == 'wrong-txid':
                    data['txid'] = 'another-publish'
                elif case == 'target-element':
                    data['targets'] = ['not-a-target']
                else:
                    data['record_files'] = ['../installed-payload.json']
                journal_path.write_text(json.dumps(data))
            try:
                before = content_digest.state(self.home, include_generated=True)
                for name, rc, output in self.run_both(ctx):
                    with self.subTest(case=case, command=name):
                        self.assertIn('Could not inspect record cleanup journal', output)
                        self.assertIn(str(target or journal_path), output)
                        self.assertNotIn('Old record folder deletion is pending', output)
                        if case == 'unsupported-schema':
                            self.assertIn('unsupported schema version 99', output)
                            self.assertNotIn('cannot be read', output)
                        self.assertEqual(rc, 1 if name == 'run_doctor' else 0)
                        self.assertEqual(content_digest.state(self.home, include_generated=True), before)
            finally:
                if target is not None:
                    target.unlink()
                    outside.rename(target)
                else:
                    journal_path.write_bytes(original_journal)

    def test_doctor_reports_known_location_despite_other_host_error_without_changing_its_result(self):
        import runtime_config
        records = self.home / 'work-records'
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(records)}}})
        before = runtime_config.config_path().read_bytes()
        hooks = self.home / '.codex/hooks.json'
        hooks.parent.mkdir(exist_ok=True)
        hooks.write_text('{"hooks": null}')
        with FakeRunner().patch(), mock.patch.object(diagnostics, 'run_install_doctor', return_value=7):
            code, output = self.captured(diagnostics.run_doctor, self.context('claude'))
        self.assertEqual(code, 7)
        self.assertIn(str(records), output)
        self.assertIn('codex', output)
        self.assertNotIn('did not move', output)
        self.assertEqual(runtime_config.config_path().read_bytes(), before)
        self.assertFalse(records.exists())

    def test_the_summary_and_every_difference_of_this_hosts_targets(self):
        ctx = self.context("claude")
        linked = ctx.skills_dir / "alpha"
        linked.symlink_to(self.source / "alpha")
        moved = ctx.skills_dir / "one"
        moved.symlink_to(self.home / "elsewhere")
        runtime = self.ghost / "runtime" / "current" / "_shared"
        runtime.mkdir(parents=True)
        (runtime / "a.py").write_text("a", encoding="utf-8")
        (runtime / "b.py").write_text("b", encoding="utf-8")
        files = content_digest.tree(runtime)
        (runtime / "a.py").write_text("edited", encoding="utf-8")
        (runtime / "b.py").unlink()
        (runtime / "c.py").write_text("new", encoding="utf-8")
        codex_copy = self.home / ".agents" / "skills" / "alpha"
        self.write_record({
            str(linked): self.entry("link", ["claude"], link_target=str(self.source / "alpha")),
            str(moved): self.entry("link", ["claude"], link_target=str(self.source / "fam" / "one")),
            str(runtime): self.entry("copy", ["claude", "codex"], kind="runtime", files=files),
            str(ctx.skills_dir / "two"): self.entry("copy", ["claude"], baseline="unknown"),
            str(codex_copy): self.entry("copy", ["codex"], files={"SKILL.md": "sha256:" + "0" * 64}),
        }, last_transaction={"id": "20261020T000000Z-1-aaaaaaaa", "updated_at": "2026-10-20T00:00:00+00:00",
                             "rollback_dir": str(self.ghost / "rollbacks" / "20261020T000000Z-1-aaaaaaaa")})
        record = installed_payload.path(self.ghost)
        for name, rc, out in self.run_both(ctx):
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn(f"[INFO] Install record ({record}): 4 claude targets (link 2, copy 2; baseline recorded 3, "
                              "migrated 0, unknown 1); last publish 20261020T000000Z-1-aaaaaaaa", out)
                self.assertIn(f"[WARN] Differs from the install record: {moved} (link target differs)", out)
                self.assertIn(f"[WARN] Differs from the install record: {runtime} (missing b.py; changed a.py; "
                              "extra c.py)", out)
                self.assertNotIn(f"Differs from the install record: {linked}", out)
                self.assertNotIn(str(codex_copy), out)
                self.assertNotIn("match the install record", out)

    def test_matching_targets_are_reported_as_matching(self):
        ctx = self.context("claude")
        linked = ctx.skills_dir / "alpha"
        linked.symlink_to(self.source / "alpha")
        self.write_record({str(linked): self.entry("link", ["claude"], link_target=str(self.source / "alpha"))})
        for name, rc, out in self.run_both(ctx):
            with self.subTest(command=name):
                self.assertIn("[INFO] Install record (", out)
                self.assertIn("last publish none", out)
                self.assertIn("[OK] Installed targets match the install record", out)

    @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "needs a file this user cannot read")
    def test_a_target_that_cannot_be_read_is_reported_and_the_command_goes_on(self):
        ctx = self.context("claude")
        runtime = self.ghost / "runtime" / "current" / "_shared"
        runtime.mkdir(parents=True)
        secret = runtime / "a.py"
        secret.write_text("a", encoding="utf-8")
        self.write_record({str(runtime): self.entry("copy", ["claude"], kind="runtime",
                                                    files=content_digest.tree(runtime))})
        secret.chmod(0)
        try:
            results = list(self.run_both(ctx))
        finally:
            secret.chmod(0o644)
        for name, rc, out in results:
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn(f"[WARN] Install record comparison failed for {runtime}: ", out)

    def test_targets_without_a_baseline_are_counted_and_never_reported_as_matching(self):
        ctx = self.context("claude")
        linked = ctx.skills_dir / "alpha"
        linked.symlink_to(self.source / "alpha")
        self.write_record({
            str(linked): self.entry("link", ["claude"], link_target=str(self.source / "alpha")),
            str(ctx.skills_dir / "two"): self.entry("copy", ["claude"], baseline="unknown"),
            str(ctx.skills_dir / "one"): self.entry("copy", ["claude"], baseline="unknown"),
        })
        for name, rc, out in self.run_both(ctx):
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn("[INFO] Not compared, no baseline in the install record: 2 targets", out)
                self.assertIn("[OK] Compared targets match the install record: 1 target", out)
                self.assertNotIn("Installed targets match the install record", out)
        self.write_record({str(ctx.skills_dir / name): self.entry("copy", ["claude"], baseline="unknown")
                           for name in ("one", "two")} | {
            str(linked): self.entry("link", ["claude"], baseline="unknown")})
        for name, rc, out in self.run_both(ctx):
            with self.subTest(command=name, compared="none"):
                self.assertIn("[INFO] Not compared, no baseline in the install record: 3 targets", out)
                self.assertNotIn("match the install record", out)

    def test_an_unreadable_install_record_is_reported_and_changes_no_result(self):
        record = self.ghost / "installed-payload.json"
        self.ghost.mkdir(parents=True)
        record.write_text("{broken", encoding="utf-8")
        for name, rc, out in self.run_both(self.context("claude")):
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn(f"[WARN] Install record unreadable ({record}): ", out)


if __name__ == "__main__":
    unittest.main()
