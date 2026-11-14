"""Static shell-path regression and structural cost checks.

Dependencies: Python 3.11+ standard library; fixtures are never executed.
"""

import base64
import json
import math
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SCANNER = HERE / 'scan_agent_security_surface.py'
API = runpy.run_path(str(SCANNER))
CASES = json.loads((HERE / 'fixtures/hook-command-regressions.json').read_text())
RULES = CASES['expected_rules']['shell-command']


class StaticCommandRegressionTests(unittest.TestCase):
    def findings(self, command):
        return [finding.to_dict() for finding in API['scan_json_commands'](Path('fixture-settings.json'), {'command': command})]

    def assert_rules(self, command, rules=RULES, condition=None):
        findings = self.findings(command)
        self.assertEqual([item['rule'] for item in findings], rules, command)
        for item in findings:
            self.assertEqual(item['severity'], 'high - command can run without protective gate')
            self.assertEqual(item['mitigation'], 'mitigate')
            self.assertEqual(item['path'], 'fixture-settings.json')
            self.assertIn('defines', item['message'])
            if condition is not None:
                self.assertEqual(item.get('details', {}).get('execution_conditions', []), [condition] if condition else [])

    def test_literal_command_paths_and_conditions(self):
        for group in CASES['groups']:
            for case_id, command in group['commands'].items():
                with self.subTest(case=case_id):
                    self.assert_rules(command, CASES['expected_rules'][group['expected']], group.get('execution_condition'))

    def test_function_and_coproc_lifetimes(self):
        for command in (
            "function f () ( true ); sh -c ':'",
            "function f () ( sh -c ':' ); :",
            "function f () { true; }; sh -c ':'",
            "f() ( true ); sh -c ':'",
            "function f () ( true ); echo $(sh -c ':')",
            "function f () ( true ); { :; }; sh -c ':'",
            "coproc sh -c ':'",
        ):
            with self.subTest(command=command):
                self.assert_rules(command)
        for command in (
            "printf '%s' 'function f () ( sh -c id )'",
            "function f () ( printf '%s' 'sh -c id' ); :",
            "printf '%s' 'coproc NAME { sh -c id; }'",
            "coproc printf '%s' 'sh -c id'",
        ):
            self.assert_rules(command, [])

    def test_option_operands_and_shell_text_are_not_executable_data(self):
        for command in (
            "sudo -u 'sh -c id' printf safe", "env -u 'sh -c id' printf safe",
            "ionice -n 'sh -c id' printf safe", "xargs -J 'sh -c id' printf safe",
            "su -c \"echo 'sh -c id'\"", "tmux new-session \"echo 'sh -c id'\"",
            "nix-shell --run \"echo 'sh -c id'\"", "parallel echo 'sh -c id' ::: a",
            "nix-shell --arg key --run 'sh -c id'", "tmux new-session -e 'sh -c id' 'echo safe'",
            "rsync -e 'echo sh -c id' a b:c", "rsync -e 'echo x; sh -c id' a b:c",
            "rsync -e 'sh -c id' a b", "GIT_SSH_COMMAND='sh -c id' true",
            "git -c core.sshCommand='sh -c id' status",
            "git -c core.sshCommand='sh -c id' -c core.sshCommand=echo fetch",
            "git --version -c core.sshCommand='sh -c id' fetch",
            "rsync --help -e 'sh -c id' a b:c", "ssh -G host sh -c id",
            "doas -L sh -c id", "tmux --help new-session 'sh -c id'",
            "nix-shell --help --run 'sh -c id'", "su --help -c 'sh -c id'",
            "docker run --entrypoint printf alpine sh -c id",
            "docker run --name 'sh -c id' alpine printf safe",
            "docker run --help alpine sh -c id", "flock --help lock sh -c id",
            "kubectl exec --help pod -- sh -c id",
            "kubectl exec pod --container -- sh -c id",
        ):
            with self.subTest(command=command):
                self.assert_rules(command, [])
        for command in (
            'sudo -u root sh -c :', 'sudo --user root sh -c :',
            'docker run -P alpine sh -c :', 'unshare -Ur sh -c :',
            "tmux new-session -P 'sh -c :'",
        ):
            self.assert_rules(command)
        for options in ("-c core.sshCommand='sh -c id' -c color.ui=false",
                        "-c color.ui=false -c core.sshCommand='sh -c id'"):
            with self.subTest(options=options):
                self.assert_rules(f'git {options} fetch', condition=API['GIT_SSH_CONDITION'])

    def test_powershell_prefix_alias_and_operand_boundaries(self):
        encoded = base64.b64encode('Write-Output MARKER'.encode('utf-16le')).decode()
        for executable, option in (
            ('pwsh', '-encodedcommand'), ('powershell', '-ec'),
            ('/opt/microsoft/pwsh', '-co'), ('pwsh.exe', '-co'),
        ):
            self.assert_rules(f'{executable} {option} {encoded}')
        for option in ('-cwa', '-CommandWithArgs'):
            self.assert_rules(f'pwsh {option} Write-Output MARKER')
            self.assert_rules(f'powershell {option} Write-Output MARKER', [])
        self.assert_rules(r'"C:\Program Files\PowerShell\7\pwsh.exe" -co Write-Output')
        for command in (
            'pwsh -Version -co x', 'pwsh -v -ec x', 'pwsh -Help -cwa x',
            'pwsh -CommandWith x',
            'pwsh -WorkingDirectory -co script.ps1', 'pwsh -ExecutionPolicy -ec script.ps1',
            f'pwsh -ec {encoded} -Help', f'pwsh -ec {encoded} -Version',
            'pwsh ---co Write-Output',
            "printf '%s' 'pwsh -co x'", 'powershell -Version 5.1 -File x.ps1',
        ):
            with self.subTest(command=command):
                self.assert_rules(command, [])
        for prefix in ('-inp Text', '-of Text', '-config test'):
            self.assert_rules(f'pwsh {prefix} -co Write-Output')
        self.assert_rules('powershell -Version 5.1 -Command Write-Output')
        for command, condition in (
            ('pwsh /cwa Write-Output MARKER', API['CWA_CONDITION']),
            ('pwsh -WorkingDirectory -cwa -co x', ''),
            ('pwsh -cwa Write-Output -Help', API['CWA_CONDITION']),
            (f'pwsh -ec {encoded} -NoProfile', ''),
        ):
            with self.subTest(command=command):
                self.assert_rules(command, condition=condition)

    def test_rsync_remote_shell_uses_its_own_quote_rules(self):
        for inner in (r'\sh -c id', "'s''h' -c id", 'sh\t-c id'):
            # JSON/shell quoting transmits one literal -e operand to the scanner.
            import shlex
            self.assert_rules('rsync -e ' + shlex.quote(inner) + ' a b:c', [])
        self.assert_rules('rsync -e \'"sh" -c id\' a b:c')

    def test_foreign_shell_program_sources_keep_their_dialect(self):
        for executable, body in (
            ('cmd', 'rem comment; sh -c id'),
            ('pwsh', '<# comment; sh -c id #>'),
            ('pwsh', '<#\nsh -c id\n#>'),
        ):
            import shlex
            for invocation in (executable, f'{executable} -File -') if executable == 'pwsh' else (executable,):
                with self.subTest(invocation=invocation, body=body):
                    command = f'{invocation} <<\'PROGRAM\'\n{body}\nPROGRAM'
                    self.assert_rules(command, [])
                    self.assertNotIn('sh', [v.name for v in API['command_vectors'](command)])
            if executable == 'cmd':
                self.assert_rules(f'bash <<< {shlex.quote(body)}')
        for command in (
            "pwsh -Command '<# sh -c id #>'",
            "cmd /c 'rem comment; sh -c id'",
            "pwsh -File <(printf '%s' '<#\nsh -c id\n#>')",
        ):
            with self.subTest(command=command):
                self.assertNotIn('sh', [v.name for v in API['command_vectors'](command)])

    def test_git_ssh_setting_follows_env_selection(self):
        for command in (
            "GIT_SSH_COMMAND='sh -c id' git fetch",
            "GIT_SSH_COMMAND='sh -c id' env git fetch",
            "env GIT_SSH_COMMAND='sh -c id' git fetch",
            'env -S "GIT_SSH_COMMAND=\'sh -c id\' git fetch"',
            "GIT_SSH_COMMAND=printf env -i GIT_SSH_COMMAND='sh -c id' git fetch",
            "env GIT_SSH_COMMAND=printf GIT_SSH_COMMAND='sh -c id' git fetch",
            "GIT_SSH_COMMAND='sh -c id' env -u OTHER git fetch",
            "GIT_SSH_COMMAND=printf env -u GIT_SSH_COMMAND git -c core.sshCommand='sh -c id' fetch",
            "GIT_SSH_COMMAND='sh -c id' GIT_SSH_COMMAND+=' suffix' git fetch",
            "GIT_SSH_COMMAND=sh GIT_SSH_COMMAND+=' -c' GIT_SSH_COMMAND+=' id' git fetch",
            "GIT_SSH_COMMAND= GIT_SSH_COMMAND+='sh -c id' git fetch",
            "GIT_SSH_COMMAND+=' unknown' GIT_SSH_COMMAND='sh -c id' git fetch",
            "env 'GIT_SSH_COMMAND+=suffix' git -c core.sshCommand='sh -c id' fetch",
            "GIT_SSH_COMMAND='sh -c id' env 'GIT_SSH_COMMAND+=suffix' git fetch",
            "env 'OTHER+NAME=value' GIT_SSH_COMMAND='sh -c id' git fetch",
        ):
            with self.subTest(command=command):
                self.assert_rules(command, condition=API['GIT_SSH_CONDITION'])
        for command in (
            "GIT_SSH_COMMAND='sh -c id' env -u GIT_SSH_COMMAND git fetch",
            "GIT_SSH_COMMAND='sh -c id' env --unset=GIT_SSH_COMMAND git fetch",
            "GIT_SSH_COMMAND='sh -c id' env -i git fetch",
            "GIT_SSH_COMMAND='sh -c id' env --ignore-environment git fetch",
            "GIT_SSH_COMMAND='sh -c id' env - git fetch",
            "GIT_SSH_COMMAND='sh -c id' env GIT_SSH_COMMAND=printf git fetch",
            "env GIT_SSH_COMMAND='sh -c id' GIT_SSH_COMMAND=printf git fetch",
            'GIT_SSH_COMMAND=\'sh -c id\' env -S "-u GIT_SSH_COMMAND git fetch"',
            "env GIT_SSH_COMMAND='sh -c id' env -i git fetch",
            "env GIT_SSH_COMMAND= git -c core.sshCommand='sh -c id' fetch",
            "GIT_SSH_COMMAND+=' suffix' git -c core.sshCommand='sh -c id' fetch",
            "GIT_SSH_COMMAND+='sh' GIT_SSH_COMMAND+=' -c id' git -c core.sshCommand='sh -c id' fetch",
            "GIT_SSH_COMMAND='sh -c id' GIT_SSH_COMMAND+=' suffix' env -u GIT_SSH_COMMAND git fetch",
            "env 'GIT_SSH_COMMAND+=sh -c id' git fetch",
            "env GIT_SSH_COMMAND='sh -c id' git status",
        ):
            with self.subTest(command=command):
                self.assert_rules(command, [])

    def test_profile_gate_requires_an_invocation_position(self):
        for command in (
            'sh -c id # hook_profile_gate.py',
            'sh -c id # profile-gate',
            'sh -c id # ghost-alice-hook.mjs',
            "sh -c id; printf '%s' hook_profile_gate.py",
            "sh -c id; python3 other.py hook_profile_gate.py",
            "sh -c id; node other.js ghost-alice-hook.mjs",
            "sh -c id; node --title ghost-alice-hook.mjs other.js",
            "sh -c id; node --unmodeled-option ghost-alice-hook.mjs other.js",
            "sh -c id; node --check ghost-alice-hook.mjs",
            "sh -c id; python3 -c 'hook_profile_gate.py'",
        ):
            with self.subTest(command=command):
                self.assert_rules(command)
        for command in (
            'python3 /fixture/hook_profile_gate.py --profile safe -- sh -c id',
            'python3 -I -W ignore /fixture/hook_profile_gate.py -- sh -c id',
            'node /fixture/ghost-alice-hook.mjs -- sh -c id',
            'node --trace-warnings /fixture/ghost-alice-hook.mjs -- sh -c id',
            '/fixture/profile-gate -- sh -c id',
        ):
            with self.subTest(command=command):
                self.assert_rules(command + ' # [session-intent-analyzer]', [])

    def test_interpreter_startup_options_stop_at_program_arguments(self):
        cases = (
            ('python3', ['script.py', '-c', 'ordinary data'], False),
            ('python3', ['-I', '-W', 'ignore', 'script.py', '-c', 'data'], False),
            ('python3', ['-m', 'example', '-c', 'data'], False),
            ('python3', ['--', '-c', 'data'], False),
            ('python3', ['-', '-c', 'data'], False),
            ('python3', ['-W', '-c', 'script.py'], False),
            ('python3', ['-c', 'print(1)'], True),
            ('python3', ['-Ic', 'print(1)'], True),
            ('python3', ['-X', 'dev', '-c', 'print(1)'], True),
            ('node', ['script.js', '--eval', 'data'], False),
            ('node', ['--require', '-e', 'script.js'], False),
            ('node', ['--title', '-e', 'script.js'], False),
            ('node', ['--unmodeled-option', '-e', 'script.js'], False),
            ('node', ['--eval=1'], True),
            ('node', ['--trace-warnings', '-e', '1'], True),
            ('node', ['--trace-warnings', 'script.js', '-e', 'data'], False),
            ('ruby', ['script.rb', '-e', 'data'], False),
            ('ruby', ['-r', '-e', 'script.rb'], False),
            ('ruby', ['-e', 'puts 1'], True),
            ('ruby', ['-W', '-e', 'puts 1'], True),
            ('ruby', ['-W0', '-e', 'puts 1'], True),
            ('ruby', ['-W1', '-e', 'puts 1'], True),
            ('ruby', ['-W2', '-e', 'puts 1'], True),
            ('ruby', ['-W:no-experimental', '-e', 'puts 1'], True),
            ('ruby', ['-W0', 'script.rb', '-e', 'data'], False),
            ('ruby', ['-W', '0', '-e', 'data'], False),
            ('perl', ['script.pl', '-e', 'data'], False),
            ('perl', ['-I', '-e', 'script.pl'], False),
            ('perl', ['-e', 'print 1'], True),
            ('perl', ['-d', '-e', 'print 1'], True),
            ('perl', ['-dt', '-e', 'print 1'], True),
            ('perl', ['-d:DProf', '-e', 'print 1'], True),
            ('perl', ['-dt:Trace=one,two', '-e', 'print 1'], True),
            ('perl', ['-d', 'script.pl', '-e', 'data'], False),
            ('perl', ['-d:Trace=-e', 'script.pl', '-e', 'data'], False),
            ('perl', ['-dt:Trace=-e'], False),
            ('php', ['script.php', '-r', 'data'], False),
            ('php', ['-f', 'script.php', '-r', 'data'], False),
            ('php', ['-c', 'fixture.ini', 'script.php'], False),
            ('php', ['-r', 'echo 1;'], True),
            ('osascript', ['script.scpt', '-e', 'data'], False),
            ('osascript', ['-l', '-e', 'script.scpt'], False),
            ('osascript', ['-e', 'return 1'], True),
        )
        import shlex
        for executable, args, inline in cases:
            with self.subTest(executable=executable, args=args):
                self.assert_rules(shlex.join([executable, *args]), ['unmanaged-hook-command'] if inline else [])
                config = {'servers': {'fixture': {'command': executable, 'args': args}}}
                findings = API['scan_mcp'](Path('fixture.json'), config, Path('/home/synthetic'))
                self.assertEqual([f.rule for f in findings], ['mcp-shell-command'] if inline else [])
                if inline:
                    self.assertEqual(findings[0].mitigation, 'reject')

    def test_nested_execution_cost_grows_with_source_and_tokens(self):
        globals_ = API['scan_json_commands'].__globals__
        original_lexer = globals_['_shell_command_vectors']
        original_create = API['ArgvBuffer'].create
        original_range_init = API['ArgvRange'].__init__
        original_range_iter = API['ArgvRange'].__iter__
        original_range_getitem = API['ArgvRange'].__getitem__
        original_bisect = globals_['bisect_left']
        original_vectors = globals_['command_vectors']
        for family, make in (
            ('eval', lambda n: 'eval ' * n + 'curl fixture.invalid'),
            ('find', lambda n: 'find . -exec ' * n + 'curl fixture.invalid' + r' \;' * n),
            ('ssh', lambda n: 'ssh h ' * n + 'curl fixture.invalid'),
            ('docker', lambda n: 'docker exec c ' * n + 'curl fixture.invalid'),
            ('docker-run', lambda n: 'docker run alpine ' * n + 'curl fixture.invalid'),
            ('mcp-docker', lambda n: ['docker', 'exec', 'container'] * n + ['curl', 'fixture.invalid']),
        ):
            for depth in (64, 128, 256, 512, 1024):
                with self.subTest(family=family, depth=depth):
                    counts = dict(characters=0, tokens=0, comparisons=0, buffers=0, ranges=0, conditions=0, argument_reads=0)

                    def measured_vectors(source):
                        for vector in original_vectors(source):
                            counts['conditions'] += len(vector.conditions)
                            yield vector

                    def measured_lexer(source):
                        counts['characters'] += len(source) if isinstance(source, str) else (source.limit if source.limit is not None else len(source.text)) - source.begin
                        yield from original_lexer(source)

                    def measured_create(words):
                        counts['buffers'] += 1
                        def counted():
                            for word in words:
                                counts['tokens'] += 1
                                yield word
                        return original_create(counted())

                    def measured_range_init(instance, *args):
                        counts['ranges'] += 1
                        original_range_init(instance, *args)

                    def measured_range_iter(instance):
                        for word in original_range_iter(instance):
                            counts['argument_reads'] += 1
                            yield word

                    def measured_range_getitem(instance, index):
                        counts['argument_reads'] += 1
                        return original_range_getitem(instance, index)

                    def measured_bisect(sequence, value):
                        class ComparedSequence:
                            def __len__(self):
                                return len(sequence)

                            def __getitem__(self, index):
                                counts['comparisons'] += 1
                                return sequence[index]
                        return original_bisect(ComparedSequence(), value)

                    command = make(depth)
                    input_size = len(command) if isinstance(command, str) else sum(map(len, command))
                    with mock.patch.dict(globals_, _shell_command_vectors=measured_lexer, bisect_left=measured_bisect, command_vectors=measured_vectors), mock.patch.object(API['ArgvBuffer'], 'create', side_effect=measured_create), mock.patch.object(API['ArgvRange'], '__init__', measured_range_init), mock.patch.object(API['ArgvRange'], '__iter__', measured_range_iter), mock.patch.object(API['ArgvRange'], '__getitem__', measured_range_getitem):
                        if family == 'mcp-docker':
                            config = {'servers': {'nested': {'command': command[0], 'args': command[1:]}}}
                            self.assertEqual(API['scan_mcp'](Path('fixture.json'), config, Path('/home/synthetic')), [])
                        else:
                            self.assert_rules(command)
                    # All families keep one token buffer; hook text has one
                    # lexical source and native MCP argv needs none.
                    self.assertLessEqual(counts['characters'], input_size)
                    self.assertLessEqual(counts['tokens'], input_size)
                    self.assertGreater(counts['tokens'], depth)
                    self.assertEqual(counts['buffers'], 1)
                    # Each traversal edge builds at most 12 constant-size
                    # views (resolver, option boundary and child payload).
                    self.assertLessEqual(counts['ranges'], 12 * depth + 20)
                    # Binary search touches at most ceil(log2(M + 1)) entries
                    # for each of N find payloads in the shared terminator index.
                    self.assertLessEqual(counts['comparisons'], depth * math.ceil(math.log2(depth + 1)))
                    self.assertLessEqual(counts['conditions'], depth + 1)
                    if family == 'mcp-docker':
                        self.assertLessEqual(counts['argument_reads'], 32 * depth + 32)

    def test_native_shell_program_is_a_separate_source_from_literal_arguments(self):
        globals_ = API['scan_mcp'].__globals__
        original_lexer = globals_['_shell_command_vectors']
        sources = []

        def observed_lexer(source):
            sources.append(source.text if isinstance(source, API['_TextSource']) else source)
            yield from original_lexer(source)

        program = "npm exec -- some-package; printf '%s' 'npm exec data'"
        root = API['mcp_command_node']('sh', ['-c', program, 'npm exec script-argument'])
        with mock.patch.dict(globals_, _shell_command_vectors=observed_lexer):
            vectors = list(API['command_vectors'](root))
        self.assertEqual(sources, [program])
        self.assertEqual([(vector.name, list(vector.args)) for vector in vectors if vector.name == 'npm'],
                         [('npm', ['exec', '--', 'some-package'])])
        self.assertEqual(root.selected.name, 'sh')

    def test_argv_fast_path_matches_real_shell_text_boundaries(self):
        globals_ = API['scan_json_commands'].__globals__
        for command, expected in (
            ('eval ' * 8 + 'sh -c :', RULES),
            ("eval 'echo sh -c id'", []), ('''eval 'echo "$x"' ''', []),
            ('''eval "eval 'sh -c :'"''', RULES), ('eval "" sh -c :', RULES),
            ('eval --', []), ('eval -- sh -c :', RULES),
            ("eval 'function f () (true); sh -c :'", RULES),
        ):
            with self.subTest(command=command):
                optimized = self.findings(command)
                with mock.patch.dict(globals_, shell_text=lambda words: ' '.join(words)):
                    reparsed = self.findings(command)
                self.assertEqual(optimized, reparsed)
                self.assertEqual([item['rule'] for item in optimized], expected)

    def test_five_thousand_nested_launchers_keep_actual_findings(self):
        for command in (
            'eval ' * 5000 + 'sh -c :', 'ssh h ' * 5000 + 'sh -c :',
            'find . -exec ' * 5000 + 'sh -c :' + r' \;' * 5000,
            'docker exec c ' * 5000 + 'sh -c :',
            'docker run alpine ' * 5000 + 'sh -c :',
        ):
            self.assert_rules(command)

    def test_static_input_requires_program_consumer_and_last_stdin_redirect(self):
        for command in (
            "echo 'sh -c id' | cat", "cat <(echo 'sh -c id')",
            "bash script.sh <<< 'sh -c id'", "xargs bash <<< 'sh -c id'",
            "bash 3<<< 'sh -c id'",
            "echo 'sh -c id' | bash < /dev/null", "bash <<< 'sh -c id' <<< 'echo safe'",
            "printf '%q' 'sh -c id' | bash", "echo -e 'sh -c id' | bash",
            "echo 'sh -c id' > /tmp/out | bash", "echo 'sh -c id' | xargs bash",
            "docker exec --interactive=false c sh <<< 'sh -c id'",
            "docker exec -i --interactive=false c sh <<< 'sh -c id'",
            "docker exec -di c sh <<< 'sh -c id'",
            "docker exec -i --detach c sh <<< 'sh -c id'",
            "docker run -di alpine sh <<< 'sh -c id'",
            "docker compose exec -di svc sh <<< 'sh -c id'",
            "docker compose run -di svc sh <<< 'sh -c id'",
            "podman exec -di c sh <<< 'sh -c id'",
            "podman run -di alpine sh <<< 'sh -c id'",
            "nerdctl exec -di c sh -c id",
            "kubectl exec --stdin=false pod -- sh <<< 'sh -c id'",
            "kubectl exec -i --stdin=false pod -- sh <<< 'sh -c id'",
            "echo 'sh -c id' | xargs docker exec -i c sh",
            "xargs echo 'sh -c id' | bash",
            "bash <(printf '%s' 'echo '; echo 'sh -c id')",
            "bash <(echo 'sh -c id'; echo safe)",
        ):
            with self.subTest(command=command):
                self.assert_rules(command, [])
        for command in (
            "echo 'sh -c id' | env sudo bash", "printf '%s' 'sh -c id' | source /dev/stdin",
            "bash < <(printf '%s' 'sh -c id')", "echo 'sh -c id' | cat | bash",
            "docker compose exec svc sh <<< 'sh -c id'",
            "docker exec --interactive=false -i c sh <<< 'sh -c id'",
            "docker exec --interactive=false --interactive c sh <<< 'sh -c id'",
            "docker exec -di --detach=false c sh <<< 'sh -c id'",
            "kubectl exec --stdin=false -i pod -- sh <<< 'sh -c id'",
            "kubectl exec --stdin=true pod -- sh <<< 'sh -c id'",
            "echo 'sh -c id' |\n\nbash",
            "echo 'sh -c id' | docker exec -i c eval bash",
            "bash <(echo 'sh -c id' | cat)",
        ):
            with self.subTest(command=command):
                self.assert_rules(command)

    def test_representative_cli_reports_complete_observed_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            home = Path(directory) / 'home'
            home.mkdir()
            config = root / '.claude' / 'settings.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'hooks': [{'command': 'function f () (true); sh -c :'}]}))
            result = subprocess.run([sys.executable, str(SCANNER), '--root', str(root), '--home', str(home), '--json'], capture_output=True, text=True, check=True)
            report = json.loads(result.stdout)
            self.assertEqual(report['finding_count'], len(report['findings']))
            self.assertEqual([item['rule'] for item in report['findings']], RULES)
            self.assertTrue(any(item['path'] == str(config.resolve()) and item['status'] == 'observed' for item in report['coverage']))

    def test_copied_skill_reads_toml_11_without_checkout_or_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory).resolve()
            skill = sandbox / 'skill'
            shutil.copytree(HERE.parent, skill, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            root, home = sandbox / 'repo', sandbox / 'home'
            root.mkdir()
            (home / '.codex').mkdir(parents=True)
            config = home / '.codex' / 'config.toml'
            suffix = '[features]\nhooks=true\n[mcp_servers.sample]\ncommand="sh"\nargs=["-c", ":"]\n'
            prefixes = ('label="plain"\n', '\ufefflabel="bom"\n', 'metadata={\nlabel="retained",\n}\n', 'label="\\e"\n', 'label="\\x41"\n')
            env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
            for content, observed in [(prefix + suffix, True) for prefix in prefixes] + [('[broken', False), ('\ufeff\ufeff' + suffix, False)]:
                with self.subTest(content=content):
                    config.write_text(content)
                    result = subprocess.run([sys.executable, str(skill / 'scripts/scan_agent_security_surface.py'), '--root', str(root), '--home', str(home), '--json'], cwd=sandbox, env=env, text=True, capture_output=True, check=True)
                    report = json.loads(result.stdout)
                    self.assertEqual(report['finding_count'], len(report['findings']))
                    self.assertEqual([item['rule'] for item in report['findings']], ['mcp-shell-command'] if observed else [])
                    entry = next(item for item in report['coverage'] if item['path'] == str(config))
                    self.assertEqual(entry['status'], 'observed' if observed else 'unobserved')
                    if not observed:
                        self.assertEqual(entry['reason'], 'parse-error')


if __name__ == '__main__':
    unittest.main()
