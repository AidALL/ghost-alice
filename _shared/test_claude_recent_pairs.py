"""Public recent-pair retention preserves authenticated necessary suffixes."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_claude_context_intake as fixtures
import test_claude_native_readiness as readiness
import context_lifecycle as lifecycle
import claude_context_adapter as adapter
import response_context_use as uses


class ClaudeRecentPairTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ClaudeContextIntakeTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.stage(); self.f.reconcile(); self.f.publish_call()
        receipt = lifecycle.record_disposition(self.f.source,None,[self.f.choice],project_replacements=True)
        self.f.complete_publication(receipt)

    def pair(self, identifier, name='Read'):
        f = self.f
        f.append(f.row('assistant',[dict(type='tool_use',id=identifier,name=name,input={'path':identifier})]))
        f.append(f.row('user',[dict(type='tool_result',tool_use_id=identifier,content='Necessary '+identifier)]))
        return dict(f.choice,record=len(f.rows))

    def native_prefix_before_intake(self, *, leading_bash=False, selected_grep=False):
        f = fixtures.ClaudeContextIntakeTests(); f.setUp(); self.addCleanup(f.doCleanups)
        if leading_bash:
            f.rows[1:1] = [f.row('assistant',[dict(type='tool_use',id='older-bash',name='Bash',input={})],uuid='older-call'),
                f.row('user',[dict(type='tool_result',tool_use_id='older-bash',content='Necessary older Bash')],uuid='older-result')]
            f.choice = dict(f.choice,record=5)
        other = None
        if selected_grep:
            f.rows.append(f.row('assistant',[dict(type='tool_use',id='selected-grep',name='Grep',input={})]))
            f.rows.append(f.row('user',[dict(type='tool_result',tool_use_id='selected-grep',content='Consumed Grep result')]))
            other = dict(f.choice,record=len(f.rows))
        f.log.write_text(''.join(json.dumps(row)+'\n' for row in f.rows))
        f.stage(); f.reconcile(); f.publish_call()
        receipt = lifecycle.record_disposition(f.source,None,[f.choice],project_replacements=True)
        f.complete_publication(receipt); self.f = f
        return other

    def project(self, choices, *, commit=True):
        f = self.f
        f.append(f.row('assistant',[dict(type='tool_use',id='recent-publication',name='Bash',input={'command':'publish current judgments'})]))
        receipt = lifecycle.record_disposition(f.source,None,choices,project_replacements=True)
        row = f.row('user',[dict(type='tool_result',tool_use_id='recent-publication',content=json.dumps(receipt))])
        if commit:f.append(row)
        return row

    def assert_plan(self, result, request, keep, identifiers=('read-existing',), excluded=('Bash',)):
        self.assertEqual(result.state,'selected')
        self.assertEqual(result.request['messages'],request['messages'])
        edit = result.request['context_management']['edits'][-1]
        self.assertEqual(edit['keep'],{'type':'tool_uses','value':keep})
        self.assertEqual(edit['exclude_tools'],list(excluded))
        self.assertFalse(edit['clear_tool_inputs'])
        self.assertEqual([value['tool_use_id'] for value in result.observations],list(identifiers))
        self.assertTrue(all(value['provider_application']=='requested' for value in result.observations))

    def test_old_replacement_is_requested_with_two_later_needed_reads_retained(self):
        self.pair('needed-one'); self.pair('needed-two')
        f = self.f; request = f.request(); original = f.log.read_bytes()
        result = f.select(request)
        self.assert_plan(result,request,2)
        self.assertEqual(result.observations[0]['replacement_carrier']['tool_use_id'],'publish-context')
        self.assertEqual(f.log.read_bytes(),original)

    def test_suffix_count_includes_excluded_pairs_after_the_protected_boundary(self):
        self.native_prefix_before_intake(leading_bash=True)
        self.pair('needed-read'); self.pair('needed-bash','Bash')
        request = self.f.request(); result = self.f.select(request)
        self.assert_plan(result,request,2)
        # Both possible public counting orders preserve every necessary Read.
        pairs = ['older-bash','read-existing','publish-context','needed-read','needed-bash']
        for counted in (pairs,[identifier for identifier in pairs if identifier in ('read-existing','needed-read')]):
            cleared = set(counted[:-2])
            self.assertNotIn('needed-read',cleared)

    def test_two_read_and_later_bash_batch_retains_the_whole_suffix(self):
        f = self.f; pairs = [('needed-one','Read'),('needed-two','Read'),('cmp-hash','Bash')]
        f.append(f.row('assistant',[dict(type='tool_use',id=identifier,name=name,input={'path':identifier})
            for identifier,name in pairs]))
        f.append(f.row('user',[dict(type='tool_result',tool_use_id=identifier,content='Necessary '+identifier)
            for identifier,_ in pairs]))
        request = f.request(); result = f.select(request)
        self.assert_plan(result,request,4,excluded=())
        # Empty exclusions retain one unambiguous suffix, including the carrier.
        order = ['read-existing','publish-context',*[identifier for identifier,_ in pairs]]
        self.assertEqual(order[:-4],['read-existing'])

    def test_actual_six_pairs_use_global_prefix_keep_five_without_name_exclusions(self):
        self.project([self.f.choice]); self.pair('needed-one'); self.pair('needed-two'); self.pair('cmp-hash','Bash')
        f = self.f; request = f.request(); original = f.log.read_bytes(); result = f.select(request)
        self.assertEqual(result.state,'selected')
        self.assertEqual(result.request['messages'],request['messages'])
        edit = result.request['context_management']['edits'][-1]
        self.assertEqual(edit['keep'],{'type':'tool_uses','value':5})
        self.assertEqual(edit['exclude_tools'],[])
        self.assertFalse(edit['clear_tool_inputs'])
        self.assertEqual([value['tool_use_id'] for value in result.observations],['read-existing'])
        self.assertEqual(result.observations[0]['replacement_carrier']['tool_use_id'],'recent-publication')
        self.assertEqual(f.log.read_bytes(),original)

    def test_global_prefix_retains_explicitly_kept_tail_and_genuine_carrier(self):
        kept = self.pair('kept-read'); self.project([self.f.choice,dict(kept,disposition='keep')])
        request = self.f.request(); result = self.f.select(request)
        self.assertEqual(result.state,'selected')
        self.assertEqual(result.request['messages'],request['messages'])
        edit = result.request['context_management']['edits'][-1]
        self.assertEqual((edit['keep']['value'],edit['exclude_tools']),(3,[]))
        self.assertEqual([value['tool_use_id'] for value in result.observations],['read-existing'])

    def test_older_unselected_global_hole_uses_the_safe_named_fallback(self):
        self.native_prefix_before_intake(leading_bash=True); self.pair('needed-read')
        request = self.f.request(); result = self.f.select(request)
        self.assertEqual(result.state,'selected')
        self.assertEqual(result.request['messages'],request['messages'])
        edit = result.request['context_management']['edits'][-1]
        self.assertEqual((edit['keep']['value'],edit['exclude_tools']),(1,['Bash']))
        self.assertEqual([value['tool_use_id'] for value in result.observations],['read-existing'])

    def test_named_plan_preserves_guaranteed_selected_targets_beyond_the_global_hole(self):
        other = self.pair('selected-write','Write'); self.pair('needed-read')
        lifecycle.record_disposition(self.f.source,None,[dict(self.f.choice,disposition='omit'),dict(other,disposition='omit')])
        request = self.f.request(); result = self.f.select(request)
        self.assertEqual(result.state,'selected')
        self.assertEqual(result.request['messages'],request['messages'])
        edit = result.request['context_management']['edits'][-1]
        self.assertEqual((edit['keep']['value'],edit['exclude_tools']),(1,['Bash']))
        self.assertEqual([value['tool_use_id'] for value in result.observations],['read-existing','selected-write'])

    def test_mixed_input_clear_scopes_in_the_actual_global_prefix_are_preserved(self):
        other = self.native_prefix_before_intake(selected_grep=True); self.pair('needed-read')
        lifecycle.record_disposition(self.f.source,None,[dict(self.f.choice,disposition='omit',clear_tool_inputs=True),dict(other,disposition='omit')])
        result = self.f.select()
        self.assertEqual(result.state,'preserved')
        self.assertEqual(result.reason,'server-context-input-scope-unrepresentable')

    def test_interleaved_native_pairs_match_grouped_wire_batch_chronology(self):
        self.pair('needed-one'); self.pair('needed-two'); self.pair('cmp-hash','Bash')
        request = self.f.request(); suffix = request['messages'][-6:]
        calls = dict(role='assistant',content=[value for index in (0,2,4) for value in suffix[index]['content']])
        results = dict(role='user',content=[value for index in (1,3,5) for value in suffix[index]['content']])
        request['messages'] = request['messages'][:-6]+[calls,results]
        self.assert_plan(self.f.select(request),request,4,excluded=())

    def test_older_unselected_same_name_result_cannot_be_cleared_by_a_newer_choice(self):
        later = self.pair('selected-later')
        lifecycle.record_disposition(self.f.source,None,[dict(later,disposition='omit')])
        result = self.f.select()
        self.assertEqual(result.state,'preserved'); self.assertFalse(result.observations)

    def test_selected_result_after_a_protected_hole_is_not_reported_as_eligible(self):
        self.pair('necessary-hole'); later = self.pair('selected-after-hole')
        self.project([self.f.choice,dict(later,disposition='omit')])
        request = self.f.request(); result = self.f.select(request)
        self.assert_plan(result,request,4,excluded=())
        self.assertEqual(result.observations[0]['replacement_carrier']['tool_use_id'],'recent-publication')

    def test_an_explicit_older_keep_protects_the_entire_same_name_prefix(self):
        later = self.pair('selected-later')
        lifecycle.record_disposition(self.f.source,None,[dict(self.f.choice,disposition='keep'),dict(later,disposition='omit')])
        self.assertEqual(self.f.select().state,'preserved')

    def test_producer_keep_and_needed_replace_are_retained_inside_the_suffix(self):
        for disposition in ('keep','replace'):
            with self.subTest(disposition=disposition):
                case = readiness.ClaudeNativeReadinessTests(); case.setUp(); self.addCleanup(case.doCleanups)
                f = case.fixture; _, row = case.producer_fixture(disposition); f.append(row)
                request = f.request(); result = f.select(request)
                self.assert_plan(result,request,2,excluded=())

    def test_mixed_eligible_input_clear_scopes_remain_unrepresentable(self):
        other = self.pair('selected-grep','Grep'); self.pair('needed-read')
        lifecycle.record_disposition(self.f.source,None,[dict(self.f.choice,disposition='omit',clear_tool_inputs=True),dict(other,disposition='omit')])
        result = self.f.select()
        self.assertEqual(result.state,'preserved')
        self.assertEqual(result.reason,'server-context-input-scope-unrepresentable')

    def test_changed_suffix_call_or_full_result_cannot_supply_keep_chronology(self):
        self.pair('needed-read')
        for kind in ('call','result'):
            with self.subTest(kind=kind):
                request = self.f.request()
                if kind=='call':request['messages'][-2]['content'][0]['input']['path']='Changed call'
                else:request['messages'][-1]['content'][0]['content']='Changed necessary result'
                self.assertEqual(self.f.select(request).state,'preserved')

    def test_duplicate_or_incomplete_wire_pairs_cannot_supply_keep_chronology(self):
        self.pair('needed-read'); request = self.f.request()
        duplicate = copy.deepcopy(request); duplicate['messages'][-2]['content'].append(copy.deepcopy(duplicate['messages'][-2]['content'][0]))
        self.assertEqual(self.f.select(duplicate).state,'preserved')
        incomplete = copy.deepcopy(request); incomplete['messages'].pop()
        self.assertEqual(self.f.select(incomplete).state,'preserved')

    def test_crossed_native_and_wire_pair_order_is_not_a_recent_suffix(self):
        f = self.f
        f.append(f.row('assistant',[dict(type='tool_use',id=identifier,name='Read',input={}) for identifier in ('first','second')]))
        f.append(f.row('user',[dict(type='tool_result',tool_use_id=identifier,content='necessary') for identifier in ('second','first')]))
        self.assertEqual(f.select().state,'preserved')

    def test_reordered_exact_suffix_pairs_do_not_match_original_chronology(self):
        self.pair('first'); self.pair('second')
        request = self.f.request(); messages = request['messages']
        request['messages'] = messages[:-4]+messages[-2:]+messages[-4:-2]
        self.assertEqual(self.f.select(request).state,'preserved')

    def test_readiness_uses_the_same_recent_plan_for_the_exact_pending_carrier(self):
        self.pair('needed-read'); row = self.project([self.f.choice],commit=False)
        request = self.f.request(); request['messages'].append(row['message'])
        case = readiness.ClaudeNativeReadinessTests(); case.fixture = self.f
        observed, committed, errors = case.delayed_append(row); self.addCleanup(case.doCleanups)
        original = uses._native
        def reader(*args,**kwargs):
            native = original(*args,**kwargs)
            if not any(value[2]=='recent-publication' for value in native.results):observed.set()
            return native
        with patch.object(uses,'_native',side_effect=reader):result = case.prepare(request)
        self.assertTrue(committed.wait(1)); self.assertFalse(errors)
        self.assert_plan(result,request,3,excluded=())

    def test_recent_pair_policy_requires_native_chronology_in_addition_to_wire_choices(self):
        self.pair('needed-read'); f = self.f; request = f.request(); manual = []
        lifecycle._select_claude_request(request,f.fixture.root,f.home,'current',_inspect=manual)
        changed, observed, _ = adapter.server_context_omit(request,manual)
        self.assertIs(changed,request); self.assertFalse(observed)

    def test_prospective_carrier_is_exclusion_only_and_cannot_emit_a_policy(self):
        self.pair('needed-read'); row = self.project([self.f.choice],commit=False)
        f = self.f; request = f.request(); request['messages'].append(row['message']); manual = []
        lifecycle._select_claude_request(request,f.fixture.root,f.home,'current',_inspect=manual)
        receipt = json.loads(row['message']['content'][0]['content'])
        context = dict(projection=receipt['replacement_context'],carrier=dict(message_index=len(request['messages'])-1,
            wire_block_index=0,tool_use_id='recent-publication'))
        for value in manual:value['replacement_context']=context
        native = uses._native(str(f.log),'claude','current')
        changed, observed, _ = adapter.server_context_omit(request,manual,originals=native.records,
            _prospective_carrier='recent-publication')
        self.assertIs(changed,request); self.assertFalse(observed)
        changed, observed, _ = adapter.server_context_omit(request,manual,originals=native.records,
            _prospective_carrier='recent-publication',_inspect_only=True)
        self.assertIs(changed,request)
        self.assertEqual([value['tool_use_id'] for value in observed],['read-existing'])
        self.assertEqual(observed[0]['server_edit']['keep']['value'],3)
        self.assertEqual(observed[0]['server_edit']['exclude_tools'],[])


if __name__=='__main__':unittest.main()
