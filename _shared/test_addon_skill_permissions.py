import json
import subprocess
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import install_destination
import addon_skill_permissions as permissions


class AddonSkillPermissionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = self.root / 'native/settings.json'
        self.settings.parent.mkdir()
        destination = dict(mode='isolated', owned_root=str(self.root),
            platform_dir=str(self.root / 'native'), skills_dir=str(self.root / 'skills'),
            ghost_root=str(self.root / 'ghost'), ledger_root=str(self.root / 'ledger'),
            project_root=str(self.root / 'project'), core_source_root=str(Path(__file__).parents[1]),
            runtime_shared_dir=str(self.root / 'ghost/runtime/current/_shared'))
        self.context = install_destination.use(destination)
        self.context.__enter__()
        self.record = {'addon_id':'pilot','provided':[{'kind':'skill','name':'pilot','ownership':'addon'}]}
        self.targets = [SimpleNamespace(addon_id='pilot',name='pilot')]

    def tearDown(self):
        self.context.__exit__(None,None,None)
        self.temp.cleanup()

    def write(self,path,settings,**kwargs):
        path.write_text(json.dumps(settings))

    def prepare(self,before,after):
        settings={'permissions':{'allow':after},'user':'unchanged'}
        self.write(self.settings,settings)
        permissions.record_install(self.settings,self.targets,before,after)
        return settings

    def detach(self,settings,**kwargs):
        return permissions.detach(self.record,self.settings,settings,{'task-router'},self.write,**kwargs)

    def test_added_rule_removed_without_changing_core_or_user_grants(self):
        before=['Skill(task-router)','Bash(user-command)']
        settings=self.prepare(before,before+['Skill(pilot)'])
        rows=self.detach(settings)
        self.assertEqual(settings['permissions']['allow'],before)
        self.assertEqual(rows[0]['action'],'removed')
        self.assertFalse(permissions._state_path().exists())

    def test_preexisting_identical_rule_and_duplicate_user_rule_preserved(self):
        before=['Skill(task-router)','Skill(pilot)']
        settings=self.prepare(before,before[:])
        self.assertEqual(self.detach(settings)[0]['action'],'preserved-existing')
        self.assertEqual(settings['permissions']['allow'],before)
        settings=self.prepare(['Skill(task-router)'],['Skill(task-router)','Skill(pilot)'])
        settings['permissions']['allow'].append('Skill(pilot)')
        self.detach(settings)
        self.assertEqual(settings['permissions']['allow'],['Skill(task-router)','Skill(pilot)'])

    def test_owned_attribution_survives_reinstall(self):
        settings=self.prepare([],['Skill(pilot)'])
        permissions.record_install(self.settings,self.targets,['Skill(pilot)'],['Skill(pilot)'])
        self.detach(settings)
        self.assertEqual(settings['permissions']['allow'],[])

    def test_deleted_preexisting_rule_readded_by_installer_becomes_owned(self):
        self.prepare(['Skill(pilot)'],['Skill(pilot)'])
        settings=self.prepare([],['Skill(pilot)'])
        self.assertEqual(self.detach(settings)[0]['action'],'removed')
        self.assertEqual(settings['permissions']['allow'],[])

    def test_missing_settings_expires_owned_permission_before_removal(self):
        import addon_uninstall
        self.prepare([],['Skill(pilot)'])
        self.settings.unlink()
        addon_uninstall._skill_permission_items(self.record,platform='claude',confirm=True)
        self.assertFalse(permissions._state_path().exists())

    def test_dry_run_preserves_settings_and_ownership(self):
        settings=self.prepare([],['Skill(pilot)'])
        state=permissions._state_path().read_bytes()
        self.assertEqual(self.detach(settings,dry_run=True)[0]['action'],'would-remove')
        self.assertEqual(settings['permissions']['allow'],['Skill(pilot)'])
        self.assertEqual(permissions._state_path().read_bytes(),state)

    def test_unknown_legacy_rule_requires_review(self):
        settings={'permissions':{'allow':['Skill(pilot)']}}
        self.assertEqual(self.detach(settings)[0]['action'],'manual-review')
        self.assertEqual(settings['permissions']['allow'],['Skill(pilot)'])

    def test_core_permission_cannot_be_removed_by_owned_addon_state(self):
        self.record['provided'][0]['name']='task-router'
        self.targets[0].name='task-router'
        settings=self.prepare([],['Skill(task-router)'])
        self.assertEqual(self.detach(settings)[0]['action'],'refused')
        self.assertEqual(settings['permissions']['allow'],['Skill(task-router)'])

    def test_mismatched_binding_and_corrupt_registry_fail_before_write(self):
        settings=self.prepare([],['Skill(pilot)'])
        data=json.loads(permissions._state_path().read_text())
        data['settings_file']=str(self.root/'other-settings.json')
        permissions._state_path().write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.detach(settings)
        self.assertEqual(settings['permissions']['allow'],['Skill(pilot)'])

    def test_settings_write_failure_keeps_permission_and_owner(self):
        settings=self.prepare([],['Skill(pilot)'])
        state=permissions._state_path().read_bytes()
        def fail(*args,**kwargs): raise OSError('write-failure')
        with self.assertRaises(OSError):
            permissions.detach(self.record,self.settings,settings,set(),fail)
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'],['Skill(pilot)'])
        self.assertEqual(permissions._state_path().read_bytes(),state)

    def test_corrupt_ownership_cli_is_error_not_not_found_and_resume_reports_it(self):
        import addon_registry
        import hash_utils
        self.prepare([],['Skill(pilot)'])
        permissions._state_path().write_text('{broken')
        skill=self.root/'skills/pilot';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('fixture')
        addons=self.root/'ghost/addons/claude'
        record=dict(schema_version='1.0',addon_id='pilot',addon_version='1.0.0',source=str(self.root),
                    platform='claude',owner='addon',origin='addon:pilot',depends_on_core=[],
                    min_core_version='0.0.0',installed_at='fixture',provided=[dict(kind='skill',name='pilot',
                    target=str(skill),ownership='addon',install_mode='copy',content_hash=hash_utils.hash_target(str(skill),'copy'),
                    marker='',metadata={})])
        addon_registry.write_record(record,addons_dir=addons)
        source=Path(__file__).parents[1]
        wrapper="import json,sys;sys.path.insert(0,sys.argv[1]);import install_destination,addon_uninstall;d=json.loads(sys.argv[2]);\nwith install_destination.use(d): raise SystemExit(addon_uninstall.main(sys.argv[3:]))"
        binding=json.dumps(__import__('dataclasses').asdict(install_destination.current()))
        common=[sys.executable,'-c',wrapper,str(source/'_shared'),binding,'--addons-dir',str(addons),'--platform','claude','--skills-dir',str(self.root/'skills')]
        action=subprocess.run(common+['--addon-id','pilot'],capture_output=True,text=True)
        self.assertEqual(action.returncode,2,action.stderr)
        self.assertEqual(json.loads(action.stdout)['status'],'error')
        self.assertTrue(skill.exists());self.assertTrue((addons/'pilot.json').exists())
        resumed=subprocess.run(common+['--resume-pending'],capture_output=True,text=True)
        self.assertEqual(resumed.returncode,2,resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)['resumed'][0]['status'],'error')

    def test_codex_owned_adapter_trust_removed_preserving_core_and_user_tables(self):
        import install_hooks
        import tomllib
        marker='[adapter:autopilot-mode] continue'
        runner=install_hooks.addon_installer.privileged_adapter_runner_id('autopilot-mode','continue')
        script=self.root/'skills/autopilot-mode/adapters/autopilot_mode.py'
        script.parent.mkdir(parents=True);script.write_text('# adapter fixture\n')
        entry=install_hooks._hook_runner_command_entry(runner,install_hooks._hook_python_command(script,payload=True),marker,platform_key='codex')
        core=install_hooks._hook_runner_command_entry('completion','echo core','[completion-reminder] AGENTS.md',platform_key='codex')
        settings_file=self.settings.parent/'hooks.json'
        settings={'hooks':{'Stop':[core,entry]}}
        settings_file.write_text(json.dumps(settings))
        core_settings={'hooks':{'Stop':[core]}}
        baseline=install_hooks._merge_codex_hook_trust_state('[user]\nkeep = "yes"\n',install_hooks._codex_trusted_hook_state_entries(settings_file,core_settings))[0]
        command=entry['hooks'][0]['command']
        entries=install_hooks._codex_trusted_hook_state_entries(settings_file,settings,verified_addon_commands={('Stop',command)})
        config=self.settings.parent/'config.toml'
        config.write_text(install_hooks._merge_codex_hook_trust_state(baseline,entries)[0])
        self.assertEqual(install_hooks.remove_adapter_hook(marker,platform_key='codex'),1)
        self.assertEqual(tomllib.loads(config.read_text()),tomllib.loads(baseline))

    def test_codex_user_changed_trust_hash_and_extra_fields_are_preserved(self):
        import install_hooks
        import tomllib
        marker='[adapter:autopilot-mode] continue'
        runner=install_hooks.addon_installer.privileged_adapter_runner_id('autopilot-mode','continue')
        script=self.root/'skills/autopilot-mode/adapters/autopilot_mode.py'
        script.parent.mkdir(parents=True);script.write_text('# adapter fixture\n')
        entry=install_hooks._hook_runner_command_entry(runner,install_hooks._hook_python_command(script,payload=True),marker,platform_key='codex')
        settings_file=self.settings.parent/'hooks.json';settings={'hooks':{'Stop':[entry]}}
        settings_file.write_text(json.dumps(settings))
        key=install_hooks._codex_hook_state_key(settings_file,'Stop',0,0)
        config=self.settings.parent/'config.toml'
        config.write_text(install_hooks._merge_codex_hook_trust_state('',{key:'user-changed-hash'})[0])
        before=config.read_bytes();install_hooks.remove_adapter_hook(marker,platform_key='codex')
        self.assertEqual(config.read_bytes(),before)
        settings_file.write_text(json.dumps(settings))
        owned_hash=install_hooks._codex_command_hook_hash('Stop',settings['hooks']['Stop'][0],entry['hooks'][0])
        config.write_text(install_hooks._merge_codex_hook_trust_state('',{key:owned_hash})[0]+'user_note = "preserve"\n')
        install_hooks.remove_adapter_hook(marker,platform_key='codex')
        table=tomllib.loads(config.read_text())['hooks']['state'][key]
        self.assertEqual(table,{'user_note':'preserve'})


if __name__ == '__main__':
    unittest.main()
