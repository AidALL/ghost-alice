"""Generation waits only for a required native original that is still appending."""
import copy
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_claude_context_intake as fixtures
import context_lifecycle as lifecycle
import response_context_use as uses
import source_lookup

core = fixtures.core


class ClaudeNativeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ClaudeContextIntakeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.stage(); self.fixture.reconcile(); self.fixture.publish_call()
        self.receipt = lifecycle.record_disposition(self.fixture.source,None,[self.fixture.choice],project_replacements=True)

    def prepare(self, request):
        f = self.fixture
        return lifecycle.prepare_native_request(request,f.fixture.root,f.home,platform='claude',session_id='current',
            native_betas=('context-management-2025-06-27',))

    def delayed_append(self, row):
        """The host commits when the first actual source read has returned absent."""
        observed, committed = threading.Event(), threading.Event()
        errors = []
        def writer():
            if not observed.wait(1):
                return
            try:
                self.fixture.append(row)
            except Exception as error:
                errors.append(error)
            finally:
                committed.set()
        thread = threading.Thread(target=writer)
        thread.start()
        self.addCleanup(thread.join,1)
        self.addCleanup(observed.set)
        return observed, committed, errors

    def publication_result(self, receipt=None):
        return self.fixture.row('user',[dict(type='tool_result',tool_use_id='publish-context',
            content=json.dumps(self.receipt if receipt is None else receipt),is_error=False)])

    def delayed_input(self):
        f = self.fixture
        core.stage_native_input(**f.coords,raw_input='A new content decision.',prompt_id='delayed-sdk',transcript_path=str(f.log))
        return f.row('user','A new content decision.',promptId='delayed-sdk',entrypoint='sdk-cli',userType='external',
            promptSource='sdk',turnOrigin='sdk')

    def test_generation_waits_for_the_exact_staged_sdk_native_append(self):
        f = self.fixture; f.append(self.publication_result())
        row = self.delayed_input(); request = f.request(); request['messages'].append(copy.deepcopy(row['message']))
        observed, committed, errors = self.delayed_append(row)
        native = core._shared_module('native_transcript'); resolve = native.resolve_staged_input
        def real_resolver(*args,**kwargs):
            result = resolve(*args,**kwargs)
            if args[0].get('native_prompt_id')=='delayed-sdk' and result is None:
                observed.set()
            return result
        with patch.object(native,'resolve_staged_input',side_effect=real_resolver):
            result = self.prepare(request)
        self.assertTrue(committed.wait(1)); self.assertFalse(errors)
        self.assertEqual((result.state,result.reason),('selected','server-context-replace-requested'))
        self.assertEqual(result.request['messages'],request['messages'])
        state = core.read_session_state(**f.coords)
        self.assertFalse(state['pending_native_inputs'])
        self.assertEqual(state['latest_input_event_id'],'')

    def test_generation_waits_for_the_named_completed_native_publication_result(self):
        f = self.fixture; row = self.publication_result(); request = f.request()
        request['messages'].append(copy.deepcopy(row['message']))
        observed, committed, errors = self.delayed_append(row)
        original = uses._native
        def actual_source_read(*args,**kwargs):
            native = original(*args,**kwargs)
            if not any(result[2]=='publish-context' for result in native.results):
                observed.set()
            return native
        with patch.object(uses,'_native',side_effect=actual_source_read):
            result = self.prepare(request)
        self.assertTrue(committed.wait(1)); self.assertFalse(errors)
        self.assertEqual((result.state,result.reason),('selected','server-context-replace-requested'))
        self.assertEqual(result.request['messages'],request['messages'])
        self.assertEqual(result.observations[0]['replacement_carrier']['tool_use_id'],'publish-context')

    def test_no_disposition_or_empty_selection_never_waits_for_pending_input(self):
        f = self.fixture; f.append(self.publication_result())
        lifecycle.record_disposition(f.source,None,[])
        self.delayed_input()
        with patch('time.sleep',side_effect=AssertionError('An empty selection has no readiness need')):
            result = self.prepare(f.request())
        self.assertEqual(result.state,'preserved')
        self.assertTrue(core.read_session_state(**f.coords)['pending_native_inputs'])

    def test_a_signed_request_without_native_server_support_does_not_wait_for_carrier(self):
        f = self.fixture; request = f.request(); request['messages'].append(self.publication_result()['message'])
        with patch('time.sleep',side_effect=AssertionError('Unsupported selection has no readiness need')):
            result = lifecycle.prepare_native_request(request,f.fixture.root,f.home,platform='claude',session_id='current')
        self.assertEqual(result.state,'preserved')

    def test_a_current_wire_input_that_differs_from_staged_transport_does_not_wait(self):
        f = self.fixture; f.append(self.publication_result()); self.delayed_input()
        request = f.request(); request['messages'].append(dict(role='user',content='Different wire input.'))
        with patch('time.sleep',side_effect=AssertionError('A different transport cannot become readiness')):
            self.assertEqual(self.prepare(request).state,'preserved')

    def test_unknown_and_contradictory_current_origin_never_waits_or_gains_authority(self):
        f = self.fixture; f.append(self.publication_result())
        row = self.delayed_input(); row['origin']={'kind':'unknown'}; f.append(row)
        before = core.read_session_state(**f.coords)
        with patch('time.sleep',side_effect=AssertionError('A committed unknown actor cannot become readiness')):
            result = self.prepare(f.request())
        self.assertEqual(result.state,'preserved')
        self.assertEqual(core.read_session_state(**f.coords),before)

    def test_missing_original_or_changed_prefix_never_waits(self):
        f = self.fixture; f.append(self.publication_result()); row = self.delayed_input()
        request = f.request(); request['messages'].append(copy.deepcopy(row['message']))
        f.log.write_bytes(f.log.read_bytes().replace(b'Earlier context',b'Altered context',1))
        with patch('time.sleep',side_effect=AssertionError('Changed originals cannot become readiness')):
            self.assertEqual(self.prepare(request).state,'preserved')
        f.log.unlink()
        with patch('time.sleep',side_effect=AssertionError('A missing original cannot become readiness')):
            self.assertEqual(self.prepare(request).state,'preserved')

    def test_public_selector_does_not_wait_or_reconcile_a_pending_input(self):
        f = self.fixture; f.append(self.publication_result()); row = self.delayed_input()
        request = f.request(); request['messages'].append(copy.deepcopy(row['message']))
        before = core.read_session_state(**f.coords)
        with (patch('time.sleep',side_effect=AssertionError('Pure selector must not wait')),
              patch.object(core,'reconcile_native_inputs',side_effect=AssertionError('Pure selector must not mutate'))):
            result = lifecycle.select_request(request,f.fixture.root,f.home,platform='claude',session_id='current',
                native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state,'preserved')
        self.assertEqual(core.read_session_state(**f.coords),before)

    def test_readiness_deadline_preserves_pending_input_and_original_request(self):
        f = self.fixture; f.append(self.publication_result()); row = self.delayed_input()
        request = f.request(); request['messages'].append(copy.deepcopy(row['message']))
        before = core.read_session_state(**f.coords)
        with patch.object(lifecycle,'_CLAUDE_NATIVE_READINESS_SECONDS',0.02,create=True):
            result = self.prepare(request)
        self.assertEqual(result.state,'preserved')
        self.assertIs(result.request,request)
        self.assertEqual(core.read_session_state(**f.coords),before)

    def test_late_native_carrier_with_changed_full_result_is_not_admitted(self):
        f = self.fixture; honest = self.publication_result(); changed = copy.deepcopy(honest)
        changed['message']['content'][0]['content'] += '\nunknown native sibling'
        request = f.request(); request['messages'].append(copy.deepcopy(honest['message']))
        observed, committed, errors = self.delayed_append(changed)
        original = uses._native
        def actual_source_read(*args,**kwargs):
            native = original(*args,**kwargs)
            if not any(result[2]=='publish-context' for result in native.results):observed.set()
            return native
        with patch.object(uses,'_native',side_effect=actual_source_read):
            result = self.prepare(request)
        self.assertTrue(committed.wait(1)); self.assertFalse(errors)
        self.assertEqual(result.state,'preserved')
        self.assertNotIn('context_management',result.request)

    def no_wait_for_ineligible_target(self, variant, *, pending_input):
        f = self.fixture
        if variant=='same-name':
            # Recent-pair keep supports a later same-name carrier. A genuinely
            # unrepresentable name has an older unselected invocation instead.
            f.append(self.publication_result())
            f.append(f.row('assistant',[dict(type='tool_use',id='later-source',name='Read',input={})]))
            f.append(f.row('user',[dict(type='tool_result',tool_use_id='later-source',content='Consumed later source')]))
            f.choice = dict(f.choice,record=len(f.rows))
            f.append(f.row('assistant',[dict(type='tool_use',id='negative-publication',name='Bash',input={})]))
            self.receipt = lifecycle.record_disposition(f.source,None,[f.choice],project_replacements=True)
            result_row = f.row('user',[dict(type='tool_result',tool_use_id='negative-publication',content=json.dumps(self.receipt))])
        else:
            result_row = self.publication_result()
        if pending_input:
            f.append(result_row)
            next_row = self.delayed_input()
            request = f.request(); request['messages'].append(copy.deepcopy(next_row['message']))
        else:
            request = f.request(); request['messages'].append(copy.deepcopy(result_row['message']))
        if variant=='changed':
            request['messages'][2]['content'][0]['content'] += ' changed selected wire source'
        elif variant=='absent':
            request['messages'][2]['content'].pop(0)
        before = core.read_session_state(**f.coords)
        with patch('time.sleep',side_effect=AssertionError('An ineligible selected wire target must not wait')):
            prepared = self.prepare(request)
        self.assertEqual(prepared.state,'preserved')
        self.assertNotIn('context_management',prepared.request)
        self.assertEqual(core.read_session_state(**f.coords),before)

    def test_changed_wire_source_does_not_wait_for_pending_carrier(self):
        self.no_wait_for_ineligible_target('changed',pending_input=False)

    def test_absent_wire_source_does_not_wait_for_pending_carrier(self):
        self.no_wait_for_ineligible_target('absent',pending_input=False)

    def test_unrepresentable_name_scope_does_not_wait_for_pending_carrier(self):
        self.no_wait_for_ineligible_target('same-name',pending_input=False)

    def test_changed_wire_source_does_not_wait_for_pending_sdk(self):
        self.no_wait_for_ineligible_target('changed',pending_input=True)

    def test_absent_wire_source_does_not_wait_for_pending_sdk(self):
        self.no_wait_for_ineligible_target('absent',pending_input=True)

    def test_unrepresentable_name_scope_does_not_wait_for_pending_sdk(self):
        self.no_wait_for_ineligible_target('same-name',pending_input=True)

    def test_foreign_native_append_does_not_wait_or_escape_the_source_guard(self):
        f = self.fixture; request = f.request(); request['messages'].append(self.publication_result()['message'])
        foreign = f.row('user','Foreign source row.'); foreign['sessionId'] = 'foreign'
        f.append(foreign)
        with patch('time.sleep',side_effect=AssertionError('A foreign native original must not wait')):
            self.assertEqual(self.prepare(request).state,'preserved')

    def producer_fixture(self, disposition, *, producer_name='Read', independent_disposition=None):
        f = self.fixture
        source = Path(f.fixture.tmp.name) / 'needed.json'; source.write_text(json.dumps({'needed':'Necessary producer original.'}))
        observation = source_lookup.lookup(source,pointer='/needed')
        f.rows[-1]['message']['content'][0] = dict(type='tool_use',id='lookup',name=producer_name,input={'file_path':str(source)})
        f.log.write_text(''.join(json.dumps(row)+'\n' for row in f.rows))
        receipt = uses.record_use(f.source,None,observation,purpose='Compare the needed producer original.')
        f.append(f.row('user',[dict(type='tool_result',tool_use_id='lookup',
            content=json.dumps(dict(observation,use_reference=receipt['reference'])))]))
        uses.record_use_judgments(f.source,None,[dict(use_reference=receipt['reference'],disposition=disposition,
            summary='The producer original or conclusion remains necessary.',next_need='Compare the original when its wording is needed.')])
        f.append(f.row('assistant',[dict(type='tool_use',id='wait-carrier',name='Bash',input={'command':'publish current use'})]))
        choice = dict(f.choice,record=6)
        independent = dict(f.choice,disposition=independent_disposition) if independent_disposition else f.choice
        self.receipt = lifecycle.record_disposition(f.source,None,[independent,choice],project_replacements=True)
        result = f.row('user',[dict(type='tool_result',tool_use_id='wait-carrier',content=json.dumps(self.receipt),is_error=False)])
        return receipt, result

    def protected_producer_does_not_wait(self, disposition, *, pending_input):
        f = self.fixture; _, result_row = self.producer_fixture(disposition,independent_disposition='keep')
        if pending_input:
            f.append(result_row); next_row = self.delayed_input()
            request = f.request(); request['messages'].append(next_row['message'])
        else:
            request = f.request(); request['messages'].append(result_row['message'])
        before = core.read_session_state(**f.coords)
        with patch('time.sleep',side_effect=AssertionError('A protected producer leaves no representable target')):
            self.assertEqual(self.prepare(request).state,'preserved')
        self.assertEqual(core.read_session_state(**f.coords),before)

    def test_kept_producer_does_not_wait_for_pending_carrier(self):
        self.protected_producer_does_not_wait('keep',pending_input=False)

    def test_needed_replacement_producer_does_not_wait_for_pending_carrier(self):
        self.protected_producer_does_not_wait('replace',pending_input=False)

    def test_kept_producer_does_not_wait_for_pending_sdk(self):
        self.protected_producer_does_not_wait('keep',pending_input=True)

    def test_needed_replacement_producer_does_not_wait_for_pending_sdk(self):
        self.protected_producer_does_not_wait('replace',pending_input=True)

    def test_omitted_producer_control_still_selects_when_carrier_is_committed(self):
        f = self.fixture; _, row = self.producer_fixture('omit'); f.append(row)
        self.assertEqual(self.prepare(f.request()).state,'selected')

    def independent_omit_has_no_protected_carrier_dependency(self, disposition):
        f = self.fixture; _, row = self.producer_fixture(disposition,producer_name='Grep',independent_disposition='omit')
        request = f.request(); request['messages'].append(row['message'])
        state, original = core.read_session_state(**f.coords), f.log.read_bytes()
        immediate = f.select(request)
        self.assertEqual([observation['tool_use_id'] for observation in immediate.observations],['read-existing'])
        with patch('time.sleep',side_effect=AssertionError('The independent omit has no protected replacement dependency')):
            prepared = self.prepare(request)
        self.assertEqual(prepared,immediate)
        self.assertEqual(core.read_session_state(**f.coords),state)
        self.assertEqual(f.log.read_bytes(),original)
        f.append(row)
        self.assertEqual([observation['tool_use_id'] for observation in f.select().observations],['read-existing'])

    def test_independent_omit_does_not_wait_for_a_kept_replacement_carrier(self):
        self.independent_omit_has_no_protected_carrier_dependency('keep')

    def test_independent_omit_does_not_wait_for_a_needed_producer_replacement_carrier(self):
        self.independent_omit_has_no_protected_carrier_dependency('replace')

    def test_independent_omit_still_waits_when_an_eligible_replacement_needs_the_carrier(self):
        f = self.fixture; _, row = self.producer_fixture('omit',producer_name='Grep',independent_disposition='omit')
        request = f.request(); request['messages'].append(row['message'])
        self.assertEqual([observation['tool_use_id'] for observation in f.select(request).observations],['read-existing'])
        observed, committed, errors = self.delayed_append(row)
        original = uses._native
        def source_read(*args,**kwargs):
            native = original(*args,**kwargs)
            if not any(result[2]=='wait-carrier' for result in native.results):observed.set()
            return native
        with patch.object(uses,'_native',side_effect=source_read):
            result = self.prepare(request)
        self.assertTrue(committed.wait(1)); self.assertFalse(errors)
        self.assertEqual(result.reason,'server-context-replace-requested')
        self.assertEqual([observation['tool_use_id'] for observation in result.observations],['read-existing','lookup'])

    def test_independent_omit_with_protected_replacement_still_needs_pending_sdk_input(self):
        f = self.fixture; _, carrier = self.producer_fixture('keep',producer_name='Grep',independent_disposition='omit'); f.append(carrier)
        row = self.delayed_input(); request = f.request(); request['messages'].append(row['message'])
        self.assertEqual(f.select(request).state,'preserved')
        observed, committed, errors = self.delayed_append(row)
        native = core._shared_module('native_transcript'); resolve = native.resolve_staged_input
        def resolver(*args,**kwargs):
            result = resolve(*args,**kwargs)
            if args[0].get('native_prompt_id')=='delayed-sdk' and result is None:observed.set()
            return result
        with patch.object(native,'resolve_staged_input',side_effect=resolver):
            result = self.prepare(request)
        self.assertTrue(committed.wait(1)); self.assertFalse(errors)
        self.assertEqual([observation['tool_use_id'] for observation in result.observations],['read-existing'])
        self.assertFalse(core.read_session_state(**f.coords)['pending_native_inputs'])

    def test_selected_result_moved_across_current_input_does_not_wait(self):
        f = self.fixture; request = f.request(); request['messages'].append(self.publication_result()['message'])
        messages = request['messages']; request['messages'] = [messages[0],messages[3],messages[1],messages[2],*messages[4:]]
        with patch('time.sleep',side_effect=AssertionError('A selected result crossed current input')):
            self.assertEqual(self.prepare(request).state,'preserved')

    def test_selected_result_moved_across_pending_sdk_does_not_wait(self):
        f = self.fixture; f.append(self.publication_result()); row = self.delayed_input()
        request = f.request(); messages = request['messages']
        request['messages'] = [messages[0],*messages[3:],row['message'],messages[1],messages[2]]
        with patch('time.sleep',side_effect=AssertionError('A selected result crossed prospective input')):
            self.assertEqual(self.prepare(request).state,'preserved')

    def test_selected_call_moved_before_its_current_input_does_not_wait(self):
        f = self.fixture; _, row = self.producer_fixture('omit'); request = f.request(); request['messages'].append(row['message'])
        messages = request['messages']; request['messages'] = [*messages[:3],messages[4],messages[3],*messages[5:]]
        with patch('time.sleep',side_effect=AssertionError('A selected call crossed current input')):
            self.assertEqual(self.prepare(request).state,'preserved')

    def test_unsigned_combined_scope_without_native_beta_does_not_wait(self):
        f = self.fixture; f.append(self.publication_result())
        lifecycle.record_disposition(f.source,None,[dict(f.choice,disposition='omit',clear_tool_inputs=True)])
        row = self.delayed_input(); request = f.request(); request['messages'].append(row['message'])
        request['thinking'] = {'type':'disabled'}
        request['messages'][1]['content'] = [block for block in request['messages'][1]['content'] if block['type']!='thinking']
        with patch('time.sleep',side_effect=AssertionError('Combined scope needs an existing native beta')):
            result = lifecycle.prepare_native_request(request,f.fixture.root,f.home,platform='claude',session_id='current')
        self.assertEqual(result.state,'preserved')

    def test_retained_producer_inspection_is_pure_while_current_sdk_is_pending(self):
        f = self.fixture; receipt, row = self.producer_fixture('replace'); f.append(row)
        next_row = self.delayed_input(); request = f.request(); request['messages'].append(next_row['message'])
        before, original = core.read_session_state(**f.coords), f.log.read_bytes()
        inspected = []
        normal = uses.select_uses(request,f.fixture.root,f.home,platform='claude',session_id='current',_inspect=[])
        self.assertEqual(normal.state,'preserved')
        with (patch.object(core,'_transaction',side_effect=AssertionError('Retained inspection must not write')),
              patch.object(uses,'_model_capsule',side_effect=AssertionError('Retained inspection must not project substitutions'))):
            result = uses.select_uses(request,f.fixture.root,f.home,platform='claude',session_id='current',
                _inspect=inspected,_retained_boundary=(len(f.rows)+1,len(request['messages'])-1))
        self.assertIs(result.request,request)
        self.assertEqual((result.state,result.reason,result.observations),('preserved','selection-inspected',()))
        self.assertEqual(inspected,[dict(use_reference=receipt['reference'],disposition='replace',record=6,block_indices=(0,))])
        self.assertEqual(core.read_session_state(**f.coords),before)
        self.assertEqual(f.log.read_bytes(),original)

    def test_retained_inspection_cannot_be_used_as_a_substitution_path(self):
        f = self.fixture; _, row = self.producer_fixture('replace'); f.append(row)
        request = f.request()
        for platform, inspection in (('claude',None),('codex',[])):
            with self.subTest(platform=platform):
                result = uses.select_uses(request,f.fixture.root,f.home,platform=platform,session_id='current',
                    _inspect=inspection,_retained_boundary=(4,3))
                self.assertIs(result.request,request)
                self.assertEqual(result.state,'preserved')
                self.assertFalse(result.observations)

    def test_retained_producer_inspection_still_rejects_changed_wire_and_originals(self):
        f = self.fixture; _, row = self.producer_fixture('keep'); f.append(row)
        next_row = self.delayed_input(); request = f.request(); request['messages'].append(next_row['message'])
        changed = copy.deepcopy(request); changed['messages'][4]['content'][0]['input']['file_path'] += '.changed'
        inspected = []
        uses.select_uses(changed,f.fixture.root,f.home,platform='claude',session_id='current',
            _inspect=inspected,_retained_boundary=(len(f.rows)+1,len(request['messages'])-1))
        self.assertFalse(inspected)
        f.log.write_bytes(f.log.read_bytes().replace(b'Necessary producer original.',b'Altered producer original.',1))
        uses.select_uses(request,f.fixture.root,f.home,platform='claude',session_id='current',
            _inspect=inspected,_retained_boundary=(len(f.rows)+1,len(request['messages'])-1))
        self.assertFalse(inspected)

    def test_equal_historical_text_cannot_nominate_a_wait_for_current_wire_input(self):
        f = self.fixture
        old_text = dict(record=1,block_index=0,kind='message',disposition='replace',
            summary='Earlier context is consumed.',next_need='Retrieve if needed.')
        self.receipt = lifecycle.record_disposition(f.source,None,[dict(f.choice,disposition='omit'),old_text])
        f.append(self.publication_result())
        text = f.rows[0]['message']['content']
        core.stage_native_input(**f.coords,raw_input=text,prompt_id='equal-sdk',transcript_path=str(f.log))
        row = f.row('user',text,promptId='equal-sdk',entrypoint='sdk-cli',userType='external',promptSource='sdk',turnOrigin='sdk')
        request = f.request(); request['messages'] = request['messages'][1:]+[row['message']]
        with patch('time.sleep',side_effect=AssertionError('The current wire input is protected')):
            self.assertEqual(self.prepare(request).state,'preserved')


if __name__=='__main__':
    unittest.main()
