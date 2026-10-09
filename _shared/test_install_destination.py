"""Explicit locations are transport, never hook admission or session authority."""
import base64
import inspect
import json
import os
import shlex
from pathlib import Path
from unittest.mock import patch
import install_hooks
import hook_profile_gate
import pytest


def test_explicit_ledger_is_independent_of_source_and_runtime():
    root = Path('/owned/authoritative-ledger')
    with patch.dict(os.environ, {'GHOST_ALICE_SESSION_INTENT_ROOT':str(root)}):
        assert install_hooks._session_intent_root() == root


def test_installer_exposes_one_destination_across_actions():
    for function in (install_hooks.install_hook, install_hooks.uninstall_hook, install_hooks.check_status_detail, install_hooks.check_status):
        assert 'destination' in inspect.signature(function).parameters


def test_recovery_trace_is_optional():
    from response_delivery import recovery_delivery_guidance
    for route in ('preserve_answer_and_patch_metadata','assembled_complete_payload','corrected_complete_answer'):
        assert 'optional' in recovery_delivery_guidance(route)


def test_bound_runner_transports_locations_without_adapter_args():
    assert 'binding' in inspect.signature(hook_profile_gate.run).parameters


def destination_fixture(root):
    return dict(mode='isolated',owned_root=str(root),platform_dir=str(root/'platform'),
        skills_dir=str(root/'skills'),ghost_root=str(root/'ghost'),ledger_root=str(root/'ledger'),
        project_root=str(root/'project'),core_source_root=str(Path(install_hooks.__file__).resolve().parents[1]))


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


@pytest.mark.parametrize('action',(install_hooks.install_hook,install_hooks.check_status_detail,install_hooks.check_status))
def test_none_destination_preserves_default_dispatch(action):
    class DefaultSelected(Exception):pass
    with patch.object(install_hooks,'_default_destination',side_effect=DefaultSelected) as default:
        with pytest.raises(DefaultSelected):action('codex',destination=None)
        default.assert_called_once_with('codex')


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


@pytest.mark.parametrize('value',('', 'owned-destination.json',None))
def test_powershell_destination_option_rejects_before_effects_when_available(tmp_path,value):
    import shutil,subprocess
    powershell=shutil.which('pwsh') or shutil.which('powershell')
    if not powershell:pytest.skip('PowerShell runtime unavailable; source-control-flow verification only')
    source=(Path(install_hooks.__file__).resolve().parents[1]/'install.ps1').read_text(encoding='utf-8-sig')
    barrier='# Best-effort UTF-8 console output'
    assert source.count(barrier)==1
    # Execute the actual public parameter binding and rejection in a copied
    # entrypoint. The sentinel blocks every normal effect if rejection regresses.
    guarded=tmp_path/'guarded-install.ps1'
    guarded.write_text(source.replace(barrier,"throw 'normal-effect-sentinel'\n"+barrier,1),encoding='utf-8-sig')
    options='@{}' if value is None else "@{DestinationFile='"+value.replace("'","''")+"'}"
    quoted=str(guarded).replace("'","''")
    command="$fixtureOptions="+options+"; try { & '"+quoted+"' @fixtureOptions; exit 97 } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 71 }"
    result=subprocess.run([powershell,'-NoLogo','-NoProfile','-NonInteractive','-Command',command],
        cwd=tmp_path,text=True,capture_output=True)
    assert result.returncode==71,result.stdout+result.stderr
    expected='normal-effect-sentinel' if value is None else 'PowerShell source integration is unsupported'
    assert expected in result.stderr,result.stdout+result.stderr
    assert list(tmp_path.iterdir())==[guarded]


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
        ('UserPromptSubmit','[hook-runner:prompt]',{}),
        ('PreToolUse','[hook-runner:tool-checkpoint]',{}),
        ('PostToolUse','[hook-runner:io-trace]',{'tool_name':'Bash','tool_input':{'command':'fixture .env'}}),
        ('Stop','[hook-runner:completion]',{'last_assistant_message':'A routine source-fixture explanation.'})):
        command=next(h['command'] for row in hooks[event] for h in row['hooks'] if marker in h['command'])
        run(['/bin/sh','-c',command],json.dumps(dict(hook_event_name=event,session_id='location-source-fixture',cwd=str(root/'project'),**payload)))
    assert (root/'ledger/ghost-state.sqlite3').is_file()
    assert (root/'ghost/io-trace.jsonl').is_file()
    assert (root/'ghost/governance-events.jsonl').is_file()
    assert (root/'ghost/hooks/tool-checkpoint-surface-state.json').is_file()
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
    assert not install_hooks._hook_runner_id_match('python3 /tmp/not_hook_profile_gate.py run adapter-autopilot-mode-continue "eA=="','adapter-autopilot-mode-continue')


def test_normal_upgrade_retains_ledger_after_source_relocation(tmp_path):
    import dataclasses
    import runtime_config
    import install_destination
    root=tmp_path/'owned';root.mkdir()
    value=destination_fixture(root); value['mode']='normal'
    with install_destination.use(value):
        runtime_config.save_config({'install_locations':{'codex':{'ledger_root':str(root/'authority')}}})
        os.environ.pop('GHOST_ALICE_SESSION_INTENT_ROOT',None)
        resolved=install_hooks._default_destination('codex')
        assert resolved.ledger_root==str(root/'authority')


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
