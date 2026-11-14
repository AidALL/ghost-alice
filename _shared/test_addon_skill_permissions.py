import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import install_destination
import addon_skill_permissions as permissions
from installer_testing import installer_env


class AddonSkillPermissionTest(unittest.TestCase):
    def test_pending_before_keeps_original_scope_when_hook_candidate_differs(self):
        settings = self.prepare([], ['Skill(pilot)'])
        def fail(*args, **kwargs):
            raise OSError('before settings commit')
        with self.assertRaises(OSError):
            permissions.detach(self.record, self.settings, settings, set(), fail)
        receipt = permissions._state_path().read_bytes()
        original = self.settings.read_bytes()
        prepared = permissions.prepare_addon(self.record, self.settings, settings, set())
        rows = permissions._detach('pilot', self.settings, settings, set(), self.write,
                                   settings_after=dict(settings, hooks={}), prepared=prepared)
        self.assertEqual(rows[0]['action'], 'manual-review')
        self.assertEqual(rows[0]['settings_state'], 'preserved')
        self.assertEqual(permissions._state_path().read_bytes(), receipt)
        self.assertEqual(self.settings.read_bytes(), original)

    def test_pending_before_finishes_original_names_before_expanded_selection(self):
        self.targets.append(SimpleNamespace(addon_id='pilot', name='other'))
        settings = self.prepare([], ['Skill(pilot)', 'Skill(other)'])
        def fail(*args, **kwargs):
            raise OSError('before settings commit')
        with self.assertRaises(OSError):
            permissions.detach(self.record, self.settings, settings, set(), fail)
        self.record['provided'].append({'kind': 'skill', 'name': 'other', 'ownership': 'addon'})
        effects = []
        def write(path, updated, **kwargs):
            effects.append((list(updated['permissions']['allow']),
                            permissions._read(path)['pending_detach']['names']))
            self.write(path, updated)
        permissions.detach(self.record, self.settings, settings, set(), write)
        self.assertEqual(effects, [(['Skill(other)'], ['pilot']), ([], ['other'])])
        self.assertFalse(permissions._state_path().exists())

    def test_core_combines_hook_candidate_and_permission_effect_in_one_recoverable_write(self):
        settings = {'permissions': {'allow': ['Skill(task-router)', 'Skill(task-router)']}, 'hooks': {'Stop': [{'hooks': [{'command': 'managed'}]}]}}
        self.write(self.settings, settings)
        permissions.record_install(self.settings, [], [], ['Skill(task-router)'], core_names=['task-router'])
        candidate = dict(settings, hooks={})
        def write_then_fail(path, updated, **kwargs):
            self.write(path, updated)
            raise OSError('committed before error')
        with self.assertRaisesRegex(OSError, 'committed before error'):
            permissions.detach_core(['task-router'], self.settings, settings, write_then_fail, settings_after=candidate)
        effect = json.loads(self.settings.read_text())
        self.assertEqual(effect['hooks'], {})
        self.assertEqual(effect['permissions']['allow'], ['Skill(task-router)'])
        pending = permissions._read(self.settings)['pending_detach']
        self.assertEqual(pending['after'], permissions._settings_fingerprint(effect))
        with self.assertRaisesRegex(ValueError, 'review'):
            permissions.detach_core(['task-router'], self.settings, settings, self.write, settings_after=candidate)
        permissions.detach_core(['task-router'], self.settings, effect, self.write, settings_after=dict(effect))
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'], ['Skill(task-router)'])
        self.assertFalse(permissions._state_path().exists())

    def test_core_rejects_candidate_permission_changes_before_any_effect(self):
        settings = {'permissions': {'allow': ['Skill(task-router)']}}
        self.write(self.settings, settings)
        permissions.record_install(self.settings, [], [], ['Skill(task-router)'], core_names=['task-router'])
        receipt = permissions._state_path().read_bytes()
        original = self.settings.read_bytes()
        with self.assertRaises(ValueError):
            permissions.detach_core(['task-router'], self.settings, settings, self.write,
                                    settings_after={'permissions': {'allow': []}})
        self.assertEqual(permissions._state_path().read_bytes(), receipt)
        self.assertEqual(self.settings.read_bytes(), original)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.enterContext(mock.patch.dict('os.environ', installer_env(self.root), clear=True))
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

    def test_reinstall_preserves_independent_core_and_addon_attribution_and_existing_grants(self):
        before = ['Skill(task-router)', 'Bash(user-command)']
        after = before + ['Skill(pilot)', 'Skill(writing-plans)']
        settings = self.prepare(before, after)
        permissions.record_install(self.settings, [], before, after, core_names=['task-router', 'writing-plans'])
        permissions.record_install(self.settings, self.targets, after, after)
        permissions.record_install(self.settings, [], after, after, core_names=['task-router', 'writing-plans'])
        self.assertEqual(self.detach(settings)[0]['action'], 'removed')
        self.assertEqual(settings['permissions']['allow'], before + ['Skill(writing-plans)'])
        self.assertEqual(permissions._read(self.settings)['core'], {'task-router': False, 'writing-plans': True})
        permissions.detach_core(['task-router', 'writing-plans'], self.settings, settings, self.write)
        self.assertEqual(settings['permissions']['allow'], before)
        self.assertEqual(settings['user'], 'unchanged')
        self.assertFalse(permissions._state_path().exists())

    def test_core_grants_without_addition_evidence_stay_and_are_reported(self):
        settings = {'permissions': {'allow': ['Skill(task-router)']}}
        self.write(self.settings, settings)
        rows = permissions.detach_core(['task-router'], self.settings, settings, self.write)
        self.assertEqual(rows[0]['action'], 'manual-review')
        self.assertEqual(rows[0]['reason'], 'addition-unproven')
        self.assertEqual(settings['permissions']['allow'], ['Skill(task-router)'])

    def test_preexisting_identical_rule_and_later_duplicate_rule_preserved(self):
        before=['Skill(task-router)','Skill(pilot)']
        settings=self.prepare(before,before[:])
        row = self.detach(settings)[0]
        self.assertEqual(row['action'],'preserved-existing')
        self.assertEqual(row['reason'], 'addition-unproven')
        self.assertEqual(settings['permissions']['allow'],before)
        settings=self.prepare(['Skill(task-router)'],['Skill(task-router)','Skill(pilot)'])
        settings['permissions']['allow'].append('Skill(pilot)')
        self.detach(settings)
        self.assertEqual(settings['permissions']['allow'],['Skill(task-router)','Skill(pilot)'])

    def test_legacy_attribution_migrates_to_separate_v2_file_and_keeps_original(self):
        state = permissions._state_path()
        state.parent.mkdir(parents=True)
        legacy = state.with_name('claude.json')
        original = json.dumps({'schema_version': 1, 'settings_file': str(self.settings.resolve()),
                               'addons': {'pilot': {'pilot': True}}})
        legacy.write_text(original)
        permissions.record_install(self.settings, self.targets, ['Skill(pilot)'], ['Skill(pilot)'])
        self.assertEqual(state.name, 'claude-v2.json')
        self.assertEqual(json.loads(state.read_text())['schema_version'], 2)
        self.assertTrue(permissions._read(self.settings)['addons']['pilot']['pilot'])
        self.assertFalse(legacy.exists())
        self.assertEqual([path.read_text() for path in state.parent.glob('claude-v1-*.bak')], [original])

    def test_receipt_removal_failure_preserves_both_previous_receipts(self):
        self.prepare([], ['Skill(pilot)'])
        state = permissions._state_path()
        original = state.read_bytes()
        legacy = state.with_name('claude.json')
        legacy_bytes = original.replace(b'"schema_version": 2', b'"schema_version": 1')
        legacy.write_bytes(legacy_bytes)
        data = permissions._read(self.settings)
        data['addons'] = {}
        unlink = Path.unlink
        def fail_receipt_removal(path, *args, **kwargs):
            if path == state:
                raise PermissionError('injected receipt removal failure')
            return unlink(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', new=fail_receipt_removal):
            with self.assertRaisesRegex(PermissionError, 'receipt removal failure'):
                permissions._save(data)
        self.assertEqual(state.read_bytes(), original)
        self.assertEqual(legacy.read_bytes(), legacy_bytes)

    def test_failed_commit_and_failed_legacy_restore_keep_original_backup_and_both_errors(self):
        state = permissions._state_path()
        state.parent.mkdir(parents=True)
        legacy = state.with_name('claude.json')
        original = json.dumps({'schema_version': 1, 'settings_file': str(self.settings.resolve()),
                               'addons': {'pilot': {'pilot': True}}}).encode()
        legacy.write_bytes(original)
        replace = permissions.os.replace
        def fail_commit_and_restore(source, target):
            if Path(target) == state:
                raise PermissionError('injected receipt commit failure')
            if Path(target) == legacy:
                raise PermissionError('injected legacy restore failure')
            return replace(source, target)
        with mock.patch.object(permissions.os, 'replace', side_effect=fail_commit_and_restore):
            with self.assertRaises(ExceptionGroup) as caught:
                permissions.record_install(self.settings, [], [], ['Skill(task-router)'], core_names=['task-router'])
        self.assertEqual([str(exc) for exc in caught.exception.exceptions],
                         ['injected receipt commit failure', 'injected legacy restore failure'])
        backups = list(state.parent.glob('claude-v1-*.bak'))
        self.assertEqual([path.read_bytes() for path in backups], [original])
        self.assertIn(str(backups[0]), str(caught.exception))
        self.assertFalse(state.exists())

    def test_temporary_cleanup_failure_keeps_recovery_error_and_backup_path_primary(self):
        state = permissions._state_path()
        state.parent.mkdir(parents=True)
        legacy = state.with_name('claude.json')
        original = json.dumps({'schema_version': 1, 'settings_file': str(self.settings.resolve()),
                               'addons': {'pilot': {'pilot': True}}}).encode()
        legacy.write_bytes(original)
        replace, unlink = permissions.os.replace, Path.unlink
        candidate = None
        def fail_commit_and_restore(source, target):
            nonlocal candidate
            if Path(target) == state:
                candidate = Path(source)
                raise PermissionError('injected receipt commit failure')
            if Path(target) == legacy:
                raise PermissionError('injected legacy restore failure')
            return replace(source, target)
        def fail_temporary_cleanup(path, *args, **kwargs):
            if path == candidate:
                raise PermissionError('injected temporary cleanup failure')
            return unlink(path, *args, **kwargs)
        with mock.patch.object(permissions.os, 'replace', side_effect=fail_commit_and_restore), \
                mock.patch.object(Path, 'unlink', new=fail_temporary_cleanup):
            with self.assertRaises(ExceptionGroup) as caught:
                permissions.record_install(self.settings, [], [], ['Skill(task-router)'], core_names=['task-router'])
        self.assertEqual([str(exc) for exc in caught.exception.exceptions],
                         ['injected receipt commit failure', 'injected legacy restore failure'])
        backups = list(state.parent.glob('claude-v1-*.bak'))
        self.assertEqual([path.read_bytes() for path in backups], [original])
        self.assertIn(str(backups[0]), str(caught.exception))
        self.assertTrue(any('injected temporary cleanup failure' in note for note in caught.exception.__notes__))
        self.assertFalse(state.exists())

    def test_deleted_preexisting_rule_readded_by_installer_becomes_owned(self):
        self.prepare(['Skill(pilot)'],['Skill(pilot)'])
        settings=self.prepare([],['Skill(pilot)'])
        self.assertEqual(self.detach(settings)[0]['action'],'removed')
        self.assertEqual(settings['permissions']['allow'],[])

    def test_missing_settings_expires_owned_permission_before_removal(self):
        import addon_uninstall
        self.prepare([],['Skill(pilot)'])
        self.settings.unlink()
        addon_uninstall._skill_permission_items(self.record,platform='claude',apply_changes=True)
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

    def test_failed_attribution_save_after_detach_does_not_remove_user_duplicate_on_retry(self):
        settings=self.prepare([],['Skill(pilot)'])
        settings['permissions']['allow'].append('Skill(pilot)')
        self.write(self.settings,settings)
        save=permissions._save
        def fail_after_effect(data):
            if json.loads(self.settings.read_text())['permissions']['allow']==['Skill(pilot)']:
                raise OSError('attribution-save-failure')
            save(data)
        with mock.patch.object(permissions,'_save',side_effect=fail_after_effect):
            with self.assertRaises(OSError): self.detach(settings)
            with self.assertRaises(OSError): self.detach(json.loads(self.settings.read_text()))
        retried=json.loads(self.settings.read_text())
        self.detach(retried)
        self.assertEqual(retried['permissions']['allow'],['Skill(pilot)'])
        self.assertFalse(permissions._state_path().exists())

    def test_pre_effect_attribution_failure_never_changes_settings(self):
        for applied in (False,True):
            with self.subTest(applied=applied):
                settings=self.prepare([],['Skill(pilot)'])
                settings['permissions']['allow'].append('Skill(pilot)')
                self.write(self.settings,settings)
                original=self.settings.read_bytes()
                save=permissions._save
                def fail(data):
                    if applied: save(data)
                    raise OSError('prepare-save-failure')
                with mock.patch.object(permissions,'_save',side_effect=fail):
                    with self.assertRaises(OSError): self.detach(settings)
                self.assertEqual(self.settings.read_bytes(),original)
                self.detach(json.loads(self.settings.read_text()))
                self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'],['Skill(pilot)'])

    def test_finalization_that_applies_then_raises_does_not_reauthorize_duplicate(self):
        settings=self.prepare([],['Skill(pilot)'])
        settings['permissions']['allow'].append('Skill(pilot)')
        self.write(self.settings,settings)
        save=permissions._save
        def fail_after_finalization(data):
            save(data)
            if json.loads(self.settings.read_text())['permissions']['allow']==['Skill(pilot)']:
                raise OSError('post-finalization-failure')
        with mock.patch.object(permissions,'_save',side_effect=fail_after_finalization):
            with self.assertRaises(OSError): self.detach(settings)
        self.detach(json.loads(self.settings.read_text()))
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'],['Skill(pilot)'])

    def test_pending_detach_dry_run_and_install_preserve_recovery_evidence(self):
        for applied in (False,True):
            with self.subTest(applied=applied):
                settings=self.prepare([],['Skill(pilot)'])
                settings['permissions']['allow'].append('Skill(pilot)')
                self.write(self.settings,settings)
                def fail(path,updated,**kwargs):
                    if applied: self.write(path,updated)
                    raise OSError('settings-write-failure')
                with self.assertRaises(OSError):
                    permissions.detach(self.record,self.settings,settings,set(),fail)
                current=json.loads(self.settings.read_text())
                state=permissions._state_path().read_bytes()
                settings_bytes=self.settings.read_bytes()
                self.detach(current,dry_run=True)
                with self.assertRaisesRegex(ValueError,'pending'):
                    permissions.record_install(self.settings,self.targets,current['permissions']['allow'],current['permissions']['allow'])
                self.assertEqual(permissions._state_path().read_bytes(),state)
                self.assertEqual(self.settings.read_bytes(),settings_bytes)
                self.assertTrue(permissions._read(self.settings)['addons']['pilot']['pilot'])
                self.detach(current)
                self.assertEqual(current['permissions']['allow'], ['Skill(pilot)'])
                self.assertFalse(permissions._state_path().exists())

    def test_pending_detach_does_not_apply_unselected_or_new_core_grants(self):
        settings=self.prepare([],['Skill(pilot)'])
        def fail(*args,**kwargs): raise OSError('write-failure')
        with self.assertRaises(OSError):
            permissions.detach(self.record,self.settings,settings,set(),fail)
        other={'addon_id':'pilot','provided':[{'kind':'skill','name':'other','ownership':'addon'}]}
        state=permissions._state_path().read_bytes()
        with self.assertRaisesRegex(ValueError,'review'):
            permissions.detach(other,self.settings,settings,set(),self.write)
        with self.assertRaisesRegex(ValueError,'Core'):
            permissions.detach(self.record,self.settings,settings,{'pilot'},self.write)
        self.assertEqual(permissions._state_path().read_bytes(),state)
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'],['Skill(pilot)'])

    def test_pending_detach_preserves_unrecognized_settings_and_attribution(self):
        settings=self.prepare([],['Skill(pilot)'])
        def fail(*args,**kwargs): raise OSError('write-failure')
        with self.assertRaises(OSError):
            permissions.detach(self.record,self.settings,settings,set(),fail)
        changed=json.loads(self.settings.read_text())
        changed['permissions']['allow'].append('Skill(pilot)')
        self.write(self.settings,changed)
        state=permissions._state_path().read_bytes()
        with self.assertRaisesRegex(ValueError,'review'):
            self.detach(changed)
        self.assertEqual(json.loads(self.settings.read_text()),changed)
        self.assertEqual(permissions._state_path().read_bytes(),state)

    def test_selected_subset_keeps_other_skill_attribution(self):
        self.targets.append(SimpleNamespace(addon_id='pilot',name='other'))
        settings=self.prepare([],['Skill(pilot)','Skill(other)'])
        self.detach(settings)
        self.assertEqual(permissions._read(self.settings)['addons']['pilot'],{'other':True})
        self.assertEqual(settings['permissions']['allow'],['Skill(other)'])

    def test_resumed_removal_retires_its_authority_before_removing_another_skill(self):
        self.targets.append(SimpleNamespace(addon_id='pilot',name='other'))
        settings=self.prepare([],['Skill(pilot)','Skill(other)'])
        settings['permissions']['allow']+=['Skill(pilot)','Skill(other)']
        self.write(self.settings,settings)
        def write_then_fail(path,updated,**kwargs):
            self.write(path,updated)
            raise OSError('post-write-failure')
        with self.assertRaises(OSError):
            permissions.detach(self.record,self.settings,settings,set(),write_then_fail)
        self.record['provided'].append({'kind':'skill','name':'other','ownership':'addon'})
        with self.assertRaises(OSError):
            permissions.detach(self.record,self.settings,json.loads(self.settings.read_text()),set(),write_then_fail)
        self.detach(json.loads(self.settings.read_text()))
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'],['Skill(pilot)','Skill(other)'])

    def test_blocked_skill_does_not_reauthorize_completed_removal(self):
        self.record['provided'].append({'kind':'skill','name':'task-router','ownership':'addon'})
        self.targets.append(SimpleNamespace(addon_id='pilot',name='task-router'))
        settings=self.prepare([],['Skill(pilot)','Skill(task-router)'])
        settings['permissions']['allow'].append('Skill(pilot)')
        self.write(self.settings,settings)
        self.detach(settings)
        self.detach(json.loads(self.settings.read_text()))
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'],['Skill(task-router)','Skill(pilot)'])

    def test_codex_owned_addon_hook_trust_removed_preserving_core_and_unmanaged_tables(self):
        import install_hooks
        import tomllib
        marker='[addon:demo] continue'
        runner=install_hooks._managed_addon_runner_id('demo','continue')
        script=self.root/'skills/demo/hooks/continue.py'
        script.parent.mkdir(parents=True);script.write_text('# addon hook fixture\n')
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
        self.assertEqual(install_hooks.remove_addon_hook(marker,platform_key='codex'),1)
        self.assertEqual(tomllib.loads(config.read_text()),tomllib.loads(baseline))

    def test_codex_removed_addon_hook_takes_its_trust_table_whatever_its_hash_or_fields(self):
        # The table describes a position whose handler is gone; neither a changed hash nor a note keeps it.
        import install_hooks
        import tomllib
        marker='[addon:demo] continue'
        runner=install_hooks._managed_addon_runner_id('demo','continue')
        script=self.root/'skills/demo/hooks/continue.py'
        script.parent.mkdir(parents=True);script.write_text('# addon hook fixture\n')
        entry=install_hooks._hook_runner_command_entry(runner,install_hooks._hook_python_command(script,payload=True),marker,platform_key='codex')
        settings_file=self.settings.parent/'hooks.json';settings={'hooks':{'Stop':[entry]}}
        settings_file.write_text(json.dumps(settings))
        key=install_hooks._codex_hook_state_key(settings_file,'Stop',0,0)
        config=self.settings.parent/'config.toml'
        config.write_text(install_hooks._merge_codex_hook_trust_state('',{key:'changed-hash'})[0])
        install_hooks.remove_addon_hook(marker,platform_key='codex')
        self.assertEqual(tomllib.loads(config.read_text()).get('hooks',{}).get('state',{}),{})
        settings_file.write_text(json.dumps(settings))
        owned_hash=install_hooks._codex_command_hook_hash('Stop',settings['hooks']['Stop'][0],entry['hooks'][0])
        config.write_text(install_hooks._merge_codex_hook_trust_state('',{key:owned_hash})[0]+'user_note = "preserve"\n')
        install_hooks.remove_addon_hook(marker,platform_key='codex')
        self.assertEqual(tomllib.loads(config.read_text()).get('hooks',{}).get('state',{}),{})


if __name__ == '__main__':
    unittest.main()
