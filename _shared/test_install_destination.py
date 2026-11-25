"""Explicit locations are transport, never hook admission or session authority."""
import base64
import inspect
import json
import os
import shlex
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import install_destination
import install_hooks
import hook_profile_gate
import pytest
from installer_testing import destination_fixture


def test_explicit_ledger_is_independent_of_source_and_runtime():
    root = Path('/owned/authoritative-ledger')
    with patch.dict(os.environ, {'GHOST_ALICE_SESSION_INTENT_ROOT':str(root)}):
        assert install_hooks._session_intent_root() == root


def test_installer_exposes_one_destination_across_actions():
    for function in (install_hooks.install_hook, install_hooks.uninstall_hook, install_hooks.check_status_detail, install_hooks.check_status):
        assert 'destination' in inspect.signature(function).parameters


def test_native_assessment_needs_no_display_trace_or_delivery_instruction():
    from stop_hook_testing import (
        run_reviewed_stop, WITHOUT_SELF_REPORT, assert_stop_observation,
    )
    payload = run_reviewed_stop(platform='codex', message=WITHOUT_SELF_REPORT)
    assert_stop_observation(unittest.TestCase(), payload)
    assert '[io-trace]' not in json.dumps(payload)
    assert 'delivery-route:' not in json.dumps(payload)


def test_bound_runner_transports_locations_in_the_binding():
    assert 'binding' in inspect.signature(hook_profile_gate.run).parameters


@pytest.fixture(params=[
    ('nt', r'\\server\share\ledger', True),
    ('nt', r'\\server\share\other', False),
    ('posix', '//Server/Share/Ledger', True),
    ('posix', '//Server/Share/ledger', False),
    ('posix', '//Server/Share/./Ledger', False),
])
def ledger_root_binding(tmp_path, request, monkeypatch):
    host, inner_root, matches = request.param
    value = destination_fixture(tmp_path, mode='normal', ledger_root='//Server/Share/Ledger',
                                runtime_shared_dir=str(tmp_path))
    hook = tmp_path / 'session_intent_analyzer_hook.py'
    receipt = tmp_path / 'child-root'
    hook.write_text("import sys\nfrom pathlib import Path\n"
                    "Path(__file__).with_name('child-root').write_text(sys.argv[-1])\n", encoding='utf-8')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('GHOST_ALICE_DISABLED_HOOKS', raising=False)
    # Select the binding's host without changing the host that owns the test filesystem.
    monkeypatch.setattr(install_destination, 'os', SimpleNamespace(name=host, environ=os.environ))
    return value, hook, inner_root, receipt, matches


def test_registration_checks_ledger_root_spelling(ledger_root_binding):
    value, hook, inner_root, _, matches = ledger_root_binding
    inner = shlex.join([install_hooks.HOOK_PYTHON_SENTINEL, str(hook), '--platform', 'codex',
                        '--format', 'json', '--context', 'prompt_submit', '--root', inner_root])
    with install_destination.use(value):
        command = install_hooks._hook_runner_command('session-intent', inner, install_hooks.SESSION_INTENT_MARKER,
                                                     platform_key='codex')
    assert (install_hooks._managed_session_intent_options(command) is not None) is matches


@pytest.mark.parametrize('flag', ('--root', '--session-intent-root'))
def test_bound_runner_checks_ledger_root_spelling(ledger_root_binding, flag):
    value, hook, inner_root, receipt, matches = ledger_root_binding
    inner = shlex.join([install_hooks.HOOK_PYTHON_SENTINEL, str(hook), flag, inner_root])
    binding = install_destination.InstallDestination.parse(value).binding()
    payload = base64.urlsafe_b64encode(inner.encode()).decode()
    with patch.object(hook_profile_gate, '_read_stdin', return_value='{}'):
        if matches:
            assert hook_profile_gate.run('session-intent', payload, platform='codex', binding=binding) == 0
            assert receipt.read_text() == inner_root
        else:
            with pytest.raises(hook_profile_gate.HookCommandRejected, match='inner ledger root conflicts'):
                hook_profile_gate.run('session-intent', payload, platform='codex', binding=binding)
            assert not receipt.exists()


def test_binding_with_an_adapter_key_is_rejected(tmp_path):
    import install_destination
    value=destination_fixture(tmp_path/'owned')
    encoded=install_destination.encode_binding(dict(schema_version='ghost-alice-hook-binding.v1',destination=value,adapter={'adapter_id':'x'}))
    with pytest.raises(ValueError):
        install_destination.decode_binding(encoded)


def _bound_hook_environment(tmp_path, platform, *, mode='normal', extra_env=None, native_cwd=None):
    import subprocess,sys
    import install_destination
    root=tmp_path/'owned';root.mkdir()
    value=destination_fixture(root);value['mode']=mode
    fields=('CLAUDE_PROJECT_DIR','GHOST_ALICE_PLATFORM')
    code=f'import json,os,sys; print(json.dumps({{"continue":True,"systemMessage":json.dumps({{"env":{{k:os.environ.get(k) for k in {fields!r}}},"input":json.load(sys.stdin)}})}}))'
    command=f'{sys.executable.replace(chr(92),"/")} -c {shlex.quote(code)}'
    wrapped=base64.urlsafe_b64encode(command.encode()).decode()
    env={key:val for key,val in os.environ.items() if not key.startswith('GHOST_ALICE_') and key!='CLAUDE_PROJECT_DIR'}
    env.update(GHOST_ALICE_AGENT_VISIBILITY='strict',**(extra_env or {}))
    binding=install_destination.encode_binding(dict(schema_version='ghost-alice-hook-binding.v1',destination=value))
    payload=dict(session_id='bound-target-fixture',hook_event_name='Stop',cwd=native_cwd)
    result=subprocess.run([sys.executable,'-B',str(Path(hook_profile_gate.__file__)),
        'run','prompt',platform,wrapped,'--binding-b64',binding],
        input=json.dumps(payload),env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads(json.loads(result.stdout)['systemMessage'])


@pytest.mark.parametrize('platform',('claude','codex'))
def test_normal_bound_runner_preserves_native_context_and_domain_project_hint(tmp_path,platform):
    native=str(tmp_path/'Windows/System32') if platform=='claude' else str(tmp_path/'native-project')
    cli_project=str(tmp_path/'claude-project')
    observed=_bound_hook_environment(tmp_path,platform,native_cwd=native,
        extra_env={'CLAUDE_PROJECT_DIR':cli_project})
    assert observed['env']['CLAUDE_PROJECT_DIR']==cli_project
    assert observed['env']['GHOST_ALICE_PLATFORM']==platform
    assert observed['input']['cwd']==native


@pytest.mark.parametrize('invalid',({},[],'',False,0))
@pytest.mark.parametrize('action',(install_hooks.install_hook,install_hooks.check_status_detail,
                                  install_hooks.check_status,install_hooks.uninstall_hook))
def test_malformed_supplied_destination_never_selects_normal_default(tmp_path,invalid,action):
    # Sentinel stops any incorrect dispatch before it can read normal
    # locations or perform effects; the public API must reject the supplied value.
    with patch.object(install_hooks,'_default_destination',side_effect=AssertionError('normal fallback selected')) as default:
        with pytest.raises(ValueError):action('codex',destination=invalid)
        default.assert_not_called()
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('representation',('dict','instance','asdict'))
def test_valid_destination_representations_do_not_select_normal_default(tmp_path,representation):
    from dataclasses import asdict
    from install_destination import InstallDestination
    value=destination_fixture(tmp_path/'owned')
    instance=InstallDestination(**value)
    supplied={'dict':value,'instance':instance,'asdict':asdict(instance)}[representation]
    with patch.object(install_hooks,'_default_destination',side_effect=AssertionError('normal fallback selected')) as default:
        install_hooks.check_status_detail('codex',destination=supplied)
        default.assert_not_called()
    assert not (tmp_path/'owned').exists()


@pytest.mark.parametrize('args',(('-DestinationFile','owned-destination.json'),('-DestinationFile',''),
    ('-DestinationFile:owned-destination.json',),('--destination-file','owned-destination.json')))
def test_powershell_destination_option_rejects_before_effects_when_available(tmp_path,args):
    # install.ps1 maps the PowerShell name to --destination-file; the cli rejects it before any effect.
    import shutil,subprocess
    pwsh=shutil.which('pwsh')
    if not pwsh:pytest.skip('PowerShell 7.6+ unavailable; the cli rejection is covered by test_installer_cli')
    script=Path(install_hooks.__file__).resolve().parents[1]/'install.ps1'
    home=tmp_path/'home';home.mkdir()
    env={**os.environ,'HOME':str(home),'USERPROFILE':str(home)}
    result=subprocess.run([pwsh,'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),*args],
        cwd=tmp_path,env=env,text=True,capture_output=True)
    assert result.returncode==2,result.stdout+result.stderr
    assert 'isolated destinations require the Python hook installer' in result.stderr,result.stdout+result.stderr
    # pwsh keeps its own data, cache and config under HOME; nothing else may appear there or in the cwd.
    from installer_testing import installer_home_effects
    assert installer_home_effects(home)==[] and list(tmp_path.iterdir())==[home]


@pytest.mark.parametrize('mode',('normal','isolated'))
def test_constructor_default_matches_dictionary_and_asdict_without_aliasing(tmp_path,mode):
    from dataclasses import asdict
    from install_destination import InstallDestination
    supplied=destination_fixture(tmp_path/'owned');supplied['mode']=mode
    original=dict(supplied)
    instance=InstallDestination(**supplied)
    expected=str(Path(supplied['ghost_root'])/'runtime/current/_shared')
    assert instance.runtime_shared_dir==expected
    assert InstallDestination.parse(instance)==InstallDestination.parse(supplied)
    assert InstallDestination.parse(asdict(instance))==instance
    assert supplied==original
    assert not (tmp_path/'owned').exists()


def test_optional_default_preserves_explicit_path_schema_and_containment_guards(tmp_path):
    from install_destination import InstallDestination
    supplied=destination_fixture(tmp_path/'owned')
    for change in ({'runtime_shared_dir':''},{'runtime_shared_dir':'relative'},
                   {'runtime_shared_dir':str(tmp_path/'outside')},{'unknown_field':'value'}):
        with pytest.raises(ValueError):InstallDestination.parse(dict(supplied,**change))
    instance=InstallDestination(**supplied,runtime_shared_dir='relative')
    with pytest.raises(ValueError,match='absolute'):InstallDestination.parse(instance)
    assert not (tmp_path/'owned').exists()


def test_isolated_validation_precedes_all_mutation(tmp_path):
    import install_destination
    import pytest
    root=tmp_path/'owned'; outside=tmp_path/'outside'
    value=destination_fixture(root);value['ghost_root']=str(outside)
    with pytest.raises(ValueError,match='escape'):
        install_hooks.install_hook('codex',destination=value)
    assert not root.exists() and not outside.exists()
    root.mkdir();(root/'ghost').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='escape'):
        install_hooks.install_hook('codex',destination=destination_fixture(root))
    assert not outside.exists()


def test_generated_chain_and_public_actions_under_os_write_deny(tmp_path):
    import subprocess
    import sys
    import shutil
    import pytest
    sandbox=shutil.which('sandbox-exec')
    if not sandbox: pytest.skip('OS write-deny runner is unavailable')
    root=tmp_path/'owned';root.mkdir();(root/'project').mkdir();(root/'tmp').mkdir()
    value=destination_fixture(root);descriptor=root/'destination.json';descriptor.write_text(json.dumps(value))
    profile='(version 1) (allow default) (deny file-write*) (allow file-write* (literal "/dev/null")) (allow file-write* (subpath '+json.dumps(str(root))+'))'
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(root/'tmp'))
    for key in ('GHOST_ALICE_HOOK_SHARED_DIR','GHOST_ALICE_RUNTIME_SHARED_DIR','GHOST_ALICE_OWNED_ROOT'):
        env.pop(key,None)
    def run(argv,input=None):
        result=subprocess.run([sandbox,'-p',profile,*argv],env=env,cwd=root/'project',input=input,text=True,capture_output=True)
        assert result.returncode == 0,result.stdout+result.stderr
        return result
    installer=Path(install_hooks.__file__).resolve()
    cli=[sys.executable,'-B',str(installer),'--platform','codex','--destination-file',str(descriptor)]
    run([*cli,'--visibility','minimal'])
    assert json.loads((root/'ghost/config.json').read_text())['agent_visibility']['profile']=='minimal'
    hooks=json.loads((root/'platform/hooks.json').read_text())['hooks']
    for event,marker,payload in (
        ('SessionStart','[hook-runner:session-start]',{}),
        ('UserPromptSubmit','[hook-runner:session-intent]',{'prompt':'Source-fixture input; no real execution admission.'}),
        ('PreToolUse','[hook-runner:tool-checkpoint]',{}),
        ('PostToolUse','[hook-runner:io-trace]',{'tool_name':'Bash','tool_input':{'command':'fixture .env'}}),
        ('Stop','[hook-runner:completion]',{'last_assistant_message':'A routine source-fixture explanation.'})):
        command=next(h['command'] for row in hooks[event] for h in row['hooks'] if marker in h['command'])
        run(['/bin/sh','-c',command],json.dumps(dict(hook_event_name=event,session_id='location-source-fixture',cwd=str(root/'project'),**payload)))
    assert (root/'ledger/ghost-state.sqlite3').is_file()
    assert (root/'ghost/io-trace.jsonl').is_file()
    assert not (root/'ghost/hooks/tool-checkpoint-surface-state.json').exists()
    config=(root/'platform/config.toml').read_text()
    assert str(root/'project') in config
    assert set(__import__('tomllib').loads(config)['projects']) == {str(root/'project')}
    run([*cli,'--status'])
    run([*cli,'--uninstall'])
    assert not any(hooks for hooks in json.loads((root/'platform/hooks.json').read_text()).get('hooks',{}).values())


def test_dryrun_visibility_creates_no_destination_files(tmp_path):
    import subprocess,sys
    root=tmp_path/'owned';root.mkdir()
    descriptor=root/'destination.json';descriptor.write_text(json.dumps(destination_fixture(root)))
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    env.pop('GHOST_ALICE_HOOK_SHARED_DIR',None);env.pop('GHOST_ALICE_RUNTIME_SHARED_DIR',None)
    result=subprocess.run([sys.executable,'-B',str(Path(install_hooks.__file__).resolve()),'--platform','codex',
        '--destination-file',str(descriptor),'--dry-run','--visibility','minimal'],env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert list(root.iterdir())==[descriptor]


def test_invalid_normal_source_precedes_visibility_write(tmp_path):
    import subprocess,sys
    ghost=tmp_path/'ghost'
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',GHOST_ALICE_GHOST_ROOT=str(ghost))
    result=subprocess.run([sys.executable,'-B',str(Path(install_hooks.__file__).resolve()),'--platform','codex',
        '--core-source-root',str(tmp_path/'missing-source'),'--visibility','minimal'],env=env,text=True,capture_output=True)
    assert result.returncode != 0
    assert not ghost.exists(),result.stdout+result.stderr


def test_invalid_addon_action_precedes_visibility_write(tmp_path):
    import subprocess,sys
    ghost=tmp_path/'ghost'
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',GHOST_ALICE_GHOST_ROOT=str(ghost))
    result=subprocess.run([sys.executable,'-B',str(Path(install_hooks.__file__).resolve()),
        '--list-addons','--visibility','minimal'],env=env,text=True,capture_output=True)
    assert result.returncode == 2,result.stdout+result.stderr
    assert '--list-addons requires' in result.stderr
    assert 'Traceback' not in result.stderr
    assert not ghost.exists()


def test_managed_outer_parser_does_not_accept_lookalike_runner():
    assert not install_hooks._hook_runner_id_match('python3 /tmp/not_hook_profile_gate.py run lookalike-hook "eA=="','lookalike-hook')


@pytest.mark.parametrize('installed', (False, True))
def test_default_destination_selects_source_and_retains_installed_ledger(tmp_path, installed):
    import runtime_config
    import install_destination
    root=tmp_path/'owned';root.mkdir()
    value=destination_fixture(root); value['mode']='normal'
    with install_destination.use(value):
        retained_source = str(root/'installed-package')
        runtime_config.save_config({'install_locations':{'codex':{
            'ledger_root':str(root/'authority'), 'core_source_root':retained_source}}})
        os.environ.pop('GHOST_ALICE_SESSION_INTENT_ROOT',None)
        os.environ.pop('GHOST_ALICE_CORE_ROOT',None)
        resolved=install_hooks._default_destination('codex', installed=installed)
        expected_source = retained_source if installed else str(Path(install_hooks.__file__).resolve().parents[1])
        assert resolved.core_source_root == expected_source
        assert resolved.ledger_root==str(root/'authority')


@pytest.mark.parametrize('platform', ('claude', 'codex'))
def test_ledger_retarget_prepares_existing_hooks_without_changing_other_settings(tmp_path, platform):
    from dataclasses import asdict, replace
    import install_destination
    import hook_definitions
    root = tmp_path / 'owned'
    root.mkdir()
    value = destination_fixture(root)
    value.update(platform_dir=str(root / ('.claude' if platform == 'claude' else '.codex')),
                 ledger_root=str(root / 'old ledger'))
    Path(value['platform_dir']).mkdir()
    install_hooks.install_hook(platform, destination=value)
    with install_destination.use(value):
        settings_path = install_hooks.PLATFORMS[platform]['hook_file']()
        settings = json.loads(settings_path.read_text())
        settings['user-preference'] = 'preserve'
        addon = install_hooks._hook_runner_command('addon:fixture:observe', 'printf addon-original',
            '[addon:fixture] observe', platform_key=platform)
        settings['hooks']['Stop'].append({'matcher': '', 'hooks': [{'type': 'command', 'command': addon, 'timeout': 9}]})
        settings_path.write_text(json.dumps(settings))
        original = settings_path.read_bytes()
        config_path = Path(value['platform_dir']) / 'config.toml'
        config_before = config_path.read_bytes() if config_path.exists() else None
        prepared = install_hooks.prepare_ledger_retarget(platform, destination=value, ledger_root=root / 'new ledger')
        assert settings_path.read_bytes() == original
        assert (config_path.read_bytes() if config_path.exists() else None) == config_before
        revised = json.loads(prepared[settings_path])
        assert revised['user-preference'] == 'preserve'
        for event, groups in revised['hooks'].items():
            for group in groups:
                for handler in group['hooks']:
                    parsed = hook_definitions.parse_hook_runner_command(handler['command'])
                    if parsed is None:
                        continue
                    binding = install_destination.decode_binding(parsed['binding'])['destination']
                    assert binding == asdict(replace(install_destination.InstallDestination.parse(value),
                                                    ledger_root=str(root / 'new ledger')))
                    if parsed['hook_id'] in {'session-intent', 'session-start', 'session-receipt'}:
                        assert str(root / 'old ledger') not in parsed['command']
                        assert str(root / 'new ledger') in parsed['command']
                    if '[addon:fixture]' in handler['command']:
                        assert parsed['command'] == 'printf addon-original'
                        assert handler['timeout'] == 9


def test_ledger_retarget_holds_a_custom_environment_wrapper_without_writing(tmp_path):
    import install_destination
    value = destination_fixture(tmp_path / 'owned')
    Path(value['platform_dir']).mkdir(parents=True)
    install_hooks.install_hook('claude', destination=value)
    with install_destination.use(value):
        path = install_hooks.PLATFORMS['claude']['hook_file']()
        settings = json.loads(path.read_bytes())
        handler = settings['hooks']['UserPromptSubmit'][0]['hooks'][0]
        handler['command'] = 'env CUSTOM_FLAG=1 ' + handler['command']
        path.write_text(json.dumps(settings))
        before = path.read_bytes()
        with pytest.raises(ValueError, match='customized'):
            install_hooks.prepare_ledger_retarget('claude', destination=value, ledger_root=tmp_path / 'owned/new ledger')
        assert path.read_bytes() == before


def test_normal_generated_chain_keeps_authority_across_source_and_runtime_relocation(tmp_path):
    import shutil,subprocess
    import install_destination
    root=tmp_path/'owned';root.mkdir();(root/'.codex').mkdir();(root/'project').mkdir()
    value=destination_fixture(root);value.update(mode='normal',platform_dir=str(root/'.codex'),skills_dir=str(root/'.agents/skills'))
    value['ledger_root']=str(root/'existing-authority')
    install_hooks.install_hook('codex',destination=value)
    source=root/'relocated-source';source.mkdir()
    old=Path(value['core_source_root'])
    for name in ('_shared','session-intent-analyzer','skill-catalog'):
        shutil.copytree(old/name,source/name)
    for name in ('VERSION','install.sh'):
        shutil.copyfile(old/name,source/name)
    relocated_runtime=root/'relocated-runtime/_shared'
    env=dict(os.environ,GHOST_ALICE_CORE_ROOT=str(source),GHOST_ALICE_GHOST_ROOT=value['ghost_root'],
        GHOST_ALICE_RUNTIME_SHARED_DIR=str(relocated_runtime),GHOST_ALICE_HOOK_SHARED_DIR='',
        GHOST_ALICE_SESSION_INTENT_ROOT='')
    # The public normal installer receives a relocated source/runtime while the
    # prior installation's config and exact managed commands retain authority.
    with patch.dict(os.environ,env,clear=True),patch.object(install_hooks,'_home',return_value=root),\
            patch.object(install_hooks,'_resolve_codex_dir',return_value=root/'.codex'):
        install_hooks.install_hook('codex')
    hooks=json.loads((root/'.codex/hooks.json').read_text())['hooks']
    for groups in hooks.values():
        for group in groups:
            for hook in group['hooks']:
                args=shlex.split(hook['command'],comments=True)
                binding=install_destination.decode_binding(args[args.index('--binding-b64')+1])
                assert binding['destination']['ledger_root']==value['ledger_root']
                assert binding['destination']['core_source_root']==str(source)
                assert binding['destination']['runtime_shared_dir']==str(relocated_runtime)
    command=next(h['command'] for row in hooks['UserPromptSubmit'] for h in row['hooks'] if '[hook-runner:session-intent]' in h['command'])
    result=subprocess.run(['/bin/sh','-c',command],env=env,cwd=root/'project',
        input=json.dumps(dict(hook_event_name='UserPromptSubmit',session_id='relocation-source-fixture',
            cwd=str(root/'project'),prompt='Synthetic relocation intake, no native host evidence.')),
        text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert (Path(value['ledger_root'])/'ghost-state.sqlite3').is_file()
    assert not (source/'.tmp/session-intent').exists()


def test_conflicting_isolated_runtime_override_is_rejected_before_mkdir(tmp_path):
    import pytest
    root=tmp_path/'owned'
    with patch.dict(os.environ,{'GHOST_ALICE_HOOK_SHARED_DIR':'/conflicting/runtime'}):
        with pytest.raises(ValueError,match='runtime overrides'):
            install_hooks.install_hook('codex',destination=destination_fixture(root))
    assert not root.exists()


def test_runtime_write_rechecks_symlink_target_containment(tmp_path):
    import pytest, runtime_config, install_destination
    root=tmp_path/'owned';root.mkdir();outside=tmp_path/'outside.json';outside.write_text('preserve')
    value=destination_fixture(root)
    with install_destination.use(value):
        (root/'ghost').mkdir();(root/'ghost/config.json').symlink_to(outside)
        with pytest.raises(ValueError,match='escape'):
            runtime_config.save_config({'agent_visibility':{'profile':'minimal'}})
    assert outside.read_text()=='preserve'
