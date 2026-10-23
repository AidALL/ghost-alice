"""Receipt-bound context selection contracts. Python standard library."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import unittest
import types
import io
import os
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_response_route import ResponseRouteTests
import context_lifecycle as lifecycle


class ContextLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.event = f.setup_owned()
        f.record()
        self.home = Path(f.tmp.name) / 'codex'
        self.log = self.home / 'sessions' / 'rollout-current.jsonl'
        self.log.parent.mkdir(parents=True)
        self.user = dict(type='message', role='user', content=[dict(type='input_text', text='implement first')])
        self.call = dict(type='custom_tool_call', call_id='consumed', name='exec', input='read original')
        self.output = dict(type='custom_tool_call_output', call_id='consumed', output=[
            dict(type='input_text', text='consumed navigation body ' * 5000),
            dict(type='input_text', text='required long source ' * 10000)])
        self.rows = [dict(type='session_meta', payload={'id': 'current'}),
                     dict(type='response_item', payload=self.user),
                     dict(type='response_item', payload=self.call),
                     dict(type='response_item', payload=self.output)]
        self.write_rows()
        self.source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(f.root), GHOST_ALICE_PLATFORM='codex',
                           GHOST_ALICE_SESSION_ID='current', CODEX_THREAD_ID='current',
                           GHOST_ALICE_CODEX_DIR=str(self.home))
        self.selection = dict(record=4, block_index=0, summary='Navigation used; source owner established.',
                              next_need='Retrieve the original only when source selection changes.')
        self.request = dict(prompt_cache_key='current', input=[self.user, self.call, self.output], model='test')

    def write_rows(self):
        self.log.write_text(''.join(json.dumps(row) + '\n' for row in self.rows))

    def record(self, selected=None, source=None, event=None):
        return lifecycle.record_disposition(source or self.source, event or self.fixture.event,
                                             [self.selection] if selected is None else selected)

    def select(self, request=None):
        result = lifecycle.select_request(request or self.request, self.fixture.root, self.home)
        return result.request, list(result.observations)

    def test_storage_failure_is_unavailable_instead_of_original_success(self):
        self.record()
        with patch.object(lifecycle, '_native', side_effect=OSError('fixture read failure')):
            result = lifecycle.select_request(self.request, self.fixture.root, self.home)
        self.assertEqual(result.state, 'unavailable')
        self.assertEqual(result.reason, 'storage-unavailable')
        self.assertEqual(result.observations, ())

    def test_programming_failure_cannot_be_reported_as_preserved_input(self):
        self.record()
        with patch.object(lifecycle, '_native', side_effect=KeyError('fixture bug')):
            with self.assertRaises(KeyError):
                lifecycle.select_request(self.request, self.fixture.root, self.home)

    def test_processing_value_error_is_not_a_source_rejection(self):
        self.record()
        with patch.object(lifecycle, '_native', side_effect=ValueError('fixture processing bug')):
            with self.assertRaises(ValueError):
                lifecycle.select_request(self.request, self.fixture.root, self.home)

    def test_database_failure_is_unavailable(self):
        with patch.object(lifecycle.core_api, '_coordinates', side_effect=sqlite3.OperationalError('fixture database failure')):
            result = lifecycle.select_request(self.request, self.fixture.root, self.home)
        self.assertEqual(result.state, 'unavailable')
        self.assertEqual(result.reason, 'storage-unavailable')

    def test_no_selection_has_an_explicit_preservation_reason(self):
        result = lifecycle.select_request(self.request, self.fixture.root, self.home)
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.reason, 'selection-absent')
        self.assertIs(result.request, self.request)

    def test_declared_use_reaches_gateway_without_a_manual_publication(self):
        changed = dict(self.request, input=[self.user])
        selection = lifecycle.RequestSelection(changed, 'selected', 'response-use', ({'reference':'use'},))
        adapter = types.SimpleNamespace(select_uses=lambda *a, **k: selection)
        with patch.dict(sys.modules, {'response_context_use':adapter}):
            result = lifecycle.select_request(self.request, self.fixture.root, self.home)
        self.assertEqual(result.request, changed)
        self.assertEqual(result.observations, selection.observations)
        self.assertEqual(result.state, 'selected')

    def test_use_storage_failure_stops_an_otherwise_valid_manual_selection(self):
        self.record()
        adapter = types.SimpleNamespace(select_uses=lambda request, *a, **k:
            lifecycle.RequestSelection(request, 'unavailable', 'use-storage-unavailable'))
        with patch.dict(sys.modules, {'response_context_use':adapter}):
            result = lifecycle.select_request(self.request, self.fixture.root, self.home)
        self.assertEqual(result.state, 'unavailable')
        self.assertEqual(result.observations, ())

    def test_claude_uses_the_same_response_lifecycle_with_explicit_native_session(self):
        received = []
        request = {'messages':[{'role':'user','content':'new input'}]}
        def selected(value, root, home, **kwargs):
            received.append(kwargs)
            return lifecycle.RequestSelection(value, 'preserved', 'active-use')
        with patch.dict(sys.modules, {'response_context_use':types.SimpleNamespace(select_uses=selected)}):
            result = lifecycle.select_request(request, self.fixture.root, self.home,
                platform='claude', session_id='native-claude')
        self.assertEqual(result.request, request)
        self.assertEqual(received, [{'platform':'claude','session_id':'native-claude'}])

    def test_native_cli_publishes_a_changed_selection_under_the_actual_owner(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'CODEX_THREAD_ID':'current'}), redirect_stdout(output):
            lifecycle.main(['--root',str(self.fixture.root),'--native-dir',str(self.home),
                '--session-id','current','--expected-input-event-id',self.fixture.event,
                '--use-judgments-json','[]'])
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt['status'], 'recorded')
        self.assertEqual(receipt['judgment_count'], 0)

    def test_current_receipt_replaces_only_consumed_block_and_keeps_original(self):
        original = self.log.read_bytes()
        receipt = self.record()
        self.assertEqual(set(receipt), {'status', 'reference', 'selected_count'})
        self.assertNotIn('consumed navigation body', json.dumps(receipt))
        for _ in range(2):
            forwarded, observations = self.select()
            self.assertEqual(len(observations), 1)
            self.assertNotIn('consumed navigation body', forwarded['input'][2]['output'][0]['text'])
            self.assertEqual(forwarded['input'][2]['output'][1], self.output['output'][1])
            self.assertEqual(forwarded['input'][:2], self.request['input'][:2])
        self.assertEqual(self.log.read_bytes(), original)

    def test_omit_output_preserves_pair_siblings_and_internal_judgment(self):
        before = self.log.read_bytes()
        self.record([dict(self.selection, disposition='omit')])
        result = lifecycle.select_request(self.request, self.fixture.root, self.home)
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.request['input'][:2], self.request['input'][:2])
        self.assertEqual(result.request['input'][2], dict(self.output, output=self.output['output'][1:]))
        self.assertNotIn(self.selection['summary'], json.dumps(result.request))
        self.assertEqual(result.observations[0]['disposition'], 'omit')
        self.assertIn('#record=4', result.observations[0]['original'])
        self.assertEqual(self.log.read_bytes(), before)
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            rows = connection.execute('SELECT payload_json FROM core_context_dispositions').fetchall()
        self.assertIn(self.selection['summary'], json.dumps(rows))

    def test_unfinished_last_record_is_not_yet_in_the_native_log(self):
        """Codex may still be appending a record when its request arrives."""
        record = json.dumps(dict(type='event_msg', payload=dict(type='token_count', note='가')),
                            ensure_ascii=False).encode() + b'\n'
        cut = record.index('가'.encode()) + 1
        with self.log.open('ab') as stream:
            stream.write(record[:cut])
        self.record([dict(self.selection, disposition='omit')])
        omitted = dict(self.output, output=self.output['output'][1:])
        result = lifecycle.select_request(self.request, self.fixture.root, self.home)
        self.assertEqual((result.state, result.request['input'][2]), ('selected', omitted))
        with self.log.open('ab') as stream:
            stream.write(record[cut:] + record[:cut])
        result = lifecycle.select_request(self.request, self.fixture.root, self.home)
        self.assertEqual((result.state, result.request['input'][2]), ('selected', omitted))

    def test_omit_scalar_output_and_exact_exchange_have_no_capsule(self):
        self.output['output'] = 'scalar obsolete original'
        self.write_rows()
        self.record([dict(self.selection, disposition='omit')])
        forwarded, observations = self.select()
        self.assertEqual(forwarded['input'][2], dict(self.output, output=''))
        self.record([dict(self.selection, kind='exchange', disposition='omit')])
        forwarded, observations = self.select()
        self.assertEqual(forwarded['input'], [self.user])
        self.assertEqual(observations[0]['kind'], 'exchange')
        self.assertEqual(observations[0]['disposition'], 'omit')
        for request in (dict(self.request, input=[self.user, self.output]),
                        dict(self.request, input=[self.user, self.call, self.call, self.output])):
            result = lifecycle.select_request(request, self.fixture.root, self.home)
            self.assertEqual(result.request, request)
            self.assertEqual(result.observations, ())

    def test_omit_prior_blocks_is_grouped_and_preserves_unknown_siblings(self):
        prior = dict(type='message', role='assistant', content=[
            dict(type='output_text', text='used first'), dict(type='output_text', text='used second'),
            dict(type='future-block', signed_opaque='retain unchanged'),
            dict(type='output_text', text='required sibling')])
        self.rows.insert(1, dict(type='response_item', payload=prior))
        self.write_rows()
        self.request['input'].insert(0, prior)
        self.record([dict(self.selection, record=2, block_index=index, kind='assistant', disposition='omit')
                     for index in (0, 1)])
        forwarded, observations = self.select()
        self.assertEqual(forwarded['input'][0], dict(prior, content=prior['content'][2:]))
        self.assertEqual(forwarded['input'][1:], self.request['input'][1:])
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(o['forwarded_item_digest'] == lifecycle.fingerprint(forwarded['input'][0])
                            for o in observations))

    def test_omit_prior_whole_text_keep_reopens_and_next_humans_remain_intact(self):
        prior = dict(type='message', role='user', content=[dict(type='input_text', text='old used body')])
        self.rows.insert(1, dict(type='response_item', payload=prior)); self.write_rows()
        self.request['input'].insert(0, prior)
        choice = dict(self.selection, record=2, kind='message', disposition='omit')
        self.record([choice])
        self.assertEqual(self.select()[0]['input'], self.request['input'][1:])
        for text in ('next human', 'third human'):
            event = self.fixture.intake(text)
            human = dict(self.user, content=[dict(type='input_text', text=text)])
            self.rows.append(dict(type='response_item', payload=human)); self.write_rows()
            self.request['input'].append(human)
            forwarded, _ = self.select()
            self.assertEqual(forwarded['input'][-1], human)
            self.assertNotIn(prior, forwarded['input'])
        self.record([dict(choice, disposition='keep')], event=event)
        self.assertEqual(self.select()[0], self.request)

    def test_omit_unknown_metadata_signed_text_and_current_human_are_protected(self):
        prior = dict(type='message', role='assistant', opaque_metadata={'retain': True},
                     content=[dict(type='output_text', text='opaque metadata body')])
        self.rows.insert(1, dict(type='response_item', payload=prior)); self.write_rows()
        self.request['input'].insert(0, prior)
        self.record([dict(self.selection, record=2, kind='assistant', disposition='omit')])
        result = lifecycle.select_request(self.request, self.fixture.root, self.home)
        self.assertEqual(result.request, self.request)
        self.assertEqual(result.observations, ())
        with self.assertRaises(ValueError):
            self.record([dict(self.selection, record=3, kind='message', disposition='omit')])
        prior['content'][0]['signature'] = 'opaque signed'; self.write_rows()
        with self.assertRaises(ValueError):
            self.record([dict(self.selection, record=2, kind='assistant', disposition='omit')])

    def test_stateful_codex_manual_and_composed_selection_preserve_unknown_values(self):
        self.record()
        for key in ('previous_response_id', 'conversation'):
            for value in (None, 'remote-owned-history', {}, False):
                with self.subTest(key=key, value=value):
                    request = dict(self.request, **{key: value})
                    for selector in (lifecycle._select_manual_request, lifecycle.select_request):
                        result = selector(request, self.fixture.root, self.home)
                        self.assertEqual(result.request, request)
                        self.assertEqual(result.state, 'preserved')
                        self.assertEqual(result.reason, 'stateful-request-selection-unsupported')
                        self.assertEqual(result.observations, ())

    def test_omit_changed_wire_call_preserves_exact_original_execution_scope(self):
        self.record([dict(self.selection, disposition='omit')])
        for call in (dict(self.call, input='different executed arguments'),
                     dict(self.call, opaque_metadata='unknown changed envelope')):
            request = dict(self.request, input=[self.user, call, self.output])
            result = lifecycle.select_request(request, self.fixture.root, self.home)
            self.assertEqual(result.state, 'preserved')
            self.assertEqual(result.observations, ())
            self.assertEqual(result.request, request)

    def record_context_kind(self, selected):
        try:
            return self.record(selected)
        except ValueError as error:
            self.fail('Explicit consumed context selection is unavailable: ' + str(error))

    def test_prior_user_and_developer_text_follow_purpose_without_changing_role(self):
        history = [dict(type='message', role=role, content=[dict(type='input_text', text=text)])
                   for role, text in [('user', 'superseded source body ' * 3000),
                                      ('developer', 'procedure body already applied ' * 3000)]]
        self.rows[1:1] = [dict(type='response_item', payload=item) for item in history]
        self.write_rows()
        selected = [dict(kind='message', record=i + 2, block_index=0,
                         summary='The needed current constraint is retained.',
                         next_need='Read the original when its detailed procedure is needed.')
                    for i in range(2)]
        self.record_context_kind(selected)
        forwarded, changes = self.select(dict(self.request, input=history + self.request['input']))
        self.assertEqual(len(changes), 2)
        self.assertEqual([i['role'] for i in forwarded['input'][:2]], ['user', 'developer'])
        self.assertNotIn('superseded source body', json.dumps(forwarded))
        self.assertNotIn('procedure body already applied', json.dumps(forwarded))
        self.assertEqual(forwarded['input'][2:], self.request['input'])

    def test_current_user_input_cannot_be_replaced_by_a_prior_use_judgment(self):
        with self.assertRaisesRegex(ValueError, 'current|before'):
            self.record([dict(kind='message', record=2, block_index=0,
                              summary='Current request', next_need='None')])

    def test_semantic_publication_resolves_the_original_in_the_same_operation(self):
        choice = {k: self.selection[k] for k in ('summary', 'next_need')}
        choice.update(kind='output', locate_text='consumed navigation body')
        receipt = self.record([choice])
        self.assertEqual(receipt['selected_count'], 1)
        forwarded, changes = self.select()
        self.assertEqual(len(changes), 1)
        self.assertNotIn('consumed navigation body', json.dumps(forwarded))
        self.assertIn('required long source', json.dumps(forwarded))

    def test_existing_material_uses_the_same_explicit_keep_or_replace_judgment(self):
        choice = dict(kind='output', locate_text='consumed navigation body', disposition='replace',
            summary='Navigation has served its purpose; source status is unresolved.',
            next_need='Retrieve when the source decision changes')
        self.record([choice])
        self.assertNotIn('consumed navigation body', json.dumps(self.select()[0]))
        self.record([dict(choice, disposition='keep')])
        self.assertEqual(self.select()[0], self.request)

    def test_unknown_disposition_is_rejected_as_a_contract_error(self):
        with self.assertRaisesRegex(ValueError, 'judgment|disposition'):
            self.record([dict(self.selection, disposition='assumed-finished')])

    def test_ambiguous_original_selector_is_rejected_without_guessing(self):
        choice = dict(kind='output', locate_text='source', summary='Used source', next_need='Retrieve as needed')
        self.output['output'][0]['text'] += ' source'
        self.write_rows()
        with self.assertRaisesRegex(ValueError, 'ambiguous|unique'):
            self.record([choice])

    def test_work_selection_and_context_use_commit_together(self):
        f = self.fixture
        choice = dict(kind='output', locate_text='consumed navigation body',
                      summary='Source selection established.', next_need='Reopen when source selection changes.')
        result = f.api.record_response_route(**f.coords, expected_input_event_id=f.event,
            candidate=f.candidate(), boundary_ref='test-boundary',
            context_selection=[choice], context_source=self.source)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['context_disposition']['selected_count'], 1)
        self.assertEqual(len(self.select()[1]), 1)

    def test_post_use_judgment_is_committed_with_an_existing_work_action(self):
        f = self.fixture
        judgments = [dict(use_reference='use', disposition='replace', summary='Needed conclusion', next_need='Re-read if changed')]
        received = []
        def record(source, event, values, *, transaction):
            self.assertIsNotNone(transaction)
            received.append((event, values))
            transaction.execute('CREATE TABLE integrated_use_marker(value TEXT)')
            transaction.execute("INSERT INTO integrated_use_marker VALUES ('bound')")
            return dict(status='recorded', references=['judgment'])
        with patch('response_context_use.record_use_judgments', record, create=True):
            result = f.api.record_response_route(**f.coords, expected_input_event_id=f.event,
                candidate=f.candidate(), boundary_ref='test-boundary',
                context_use_judgments=judgments, context_source=self.source)
        self.assertEqual(received, [(f.event, judgments)])
        self.assertEqual(result['context_use_judgments']['status'], 'recorded')

    def test_rejected_post_use_judgment_rolls_back_the_work_action(self):
        f = self.fixture
        before = f.api.read_response_route(**f.coords)
        with patch('response_context_use.record_use_judgments', side_effect=ValueError('unobserved source'), create=True):
            with self.assertRaisesRegex(ValueError, 'unobserved'):
                f.api.record_response_route(**f.coords, expected_input_event_id=f.event,
                    candidate=f.candidate(), boundary_ref='new-boundary',
                    context_use_judgments=[], context_source=self.source)
        self.assertEqual(f.api.read_response_route(**f.coords), before)

    def test_invalid_context_use_rolls_back_work_selection(self):
        f = self.fixture
        before = f.api.ledger.read_session_state(**f.coords)
        candidate = f.candidate(); candidate['routing_surface']['reason'] = 'changed selection'
        invalid = dict(kind='output', locate_text='consumed navigation body', summary='', next_need='Retain locator')
        with self.assertRaises(ValueError):
            f.api.record_response_route(**f.coords, expected_input_event_id=f.event,
                candidate=candidate, boundary_ref='test-boundary',
                context_selection=[invalid], context_source=self.source)
        self.assertEqual(f.api.ledger.read_session_state(**f.coords), before)
        self.assertEqual(self.select()[1], [])

    def test_consumed_exchange_excludes_executed_arguments_and_all_results(self):
        self.call['input'] = 'completed write with original answer ' * 4000
        self.write_rows()
        self.record_context_kind([dict(self.selection, kind='exchange')])
        before = self.log.read_bytes()
        forwarded, changes = self.select()
        serialized = json.dumps(forwarded)
        self.assertNotIn('completed write with original answer', serialized)
        self.assertNotIn('consumed navigation body', serialized)
        self.assertNotIn('required long source', serialized)
        self.assertEqual(forwarded['input'][0], self.user)
        self.assertIn('Navigation used; source owner established.', serialized)
        self.assertEqual(len(changes), 1)
        self.assertEqual(self.log.read_bytes(), before)

    def test_prior_answer_can_be_selected_but_current_answer_and_user_cannot(self):
        prior = dict(type='message', role='assistant', content=[dict(type='output_text', text='already delivered detail ' * 4000)])
        self.rows.insert(1, dict(type='response_item', payload=prior))
        self.rows.append(dict(type='response_item', payload=dict(prior, content=[dict(type='output_text', text='current answer')])))
        self.write_rows()
        choice = dict(record=2, block_index=0, kind='assistant', summary='Prior answer consumed.', next_need='Explicit retrieval when wording is needed.')
        self.record_context_kind([choice])
        request = dict(self.request, input=[prior, *self.request['input']])
        forwarded, changes = self.select(request)
        self.assertNotIn('already delivered detail', json.dumps(forwarded))
        self.assertEqual(forwarded['input'][1:], request['input'][1:])
        for record in (3,6):
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.record([dict(choice, record=record)])

    def test_consumed_image_exchange_is_explicit_and_unselected_image_is_kept(self):
        image = dict(type='input_image', image_url='data:image/png;base64,TEST')
        self.output['output'].append(image)
        self.write_rows()
        self.record_context_kind([dict(self.selection, kind='exchange')])
        forwarded, changes = self.select()
        self.assertNotIn('data:image/png', json.dumps(forwarded))
        self.assertEqual(len(changes), 1)
        self.record([self.selection])
        self.assertEqual(self.select()[0]['input'][2]['output'][-1], image)

    def test_partial_or_changed_exchange_is_preserved_without_orphaning(self):
        self.record_context_kind([dict(self.selection, kind='exchange')])
        for items in ([self.user, self.output], [self.user,self.call],
                      [self.user,dict(self.call,input='changed'),self.output]):
            request = dict(self.request,input=items)
            self.assertEqual(self.select(request),(request,[]))

    def test_refresh_replaces_current_selection_and_empty_clears_it(self):
        self.record()
        self.record([])
        forwarded, changes = self.select()
        self.assertEqual(forwarded, self.request)
        self.assertEqual(changes, [])

    def test_new_input_carries_consumed_state_and_preserves_explicit_original_relookup(self):
        self.record()
        self.fixture.intake('read the original again')
        new_user = dict(self.user, content=[dict(type='input_text', text='read the original again')])
        self.rows.append(dict(type='response_item', payload=new_user))
        self.write_rows()
        request = dict(self.request, input=[new_user,self.call,self.output,
            dict(type='function_call', call_id='relookup', name='read', arguments='{}'),
            dict(type='function_call_output', call_id='relookup', output='required original wording ' * 10000)])
        actual, changes = self.select(request)
        self.assertEqual(len(changes), 1)
        self.assertEqual(actual['input'][-1], request['input'][-1])
        self.assertEqual(actual['input'][0], new_user)
        self.assertNotIn('consumed navigation body', actual['input'][2]['output'][0]['text'])
        with self.assertRaises(ValueError):
            self.record()

    def test_missing_or_foreign_native_owner_cannot_publish(self):
        for owner in ('other', None):
            source = dict(self.source)
            if owner is None: source.pop('CODEX_THREAD_ID')
            else: source['CODEX_THREAD_ID'] = owner
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                self.record(source=source)

    def test_changed_original_is_preserved_and_append_is_allowed(self):
        self.record()
        with self.log.open('a') as stream:
            stream.write(json.dumps(dict(type='event_msg', payload={'type': 'token_count'})) + '\n')
        self.assertEqual(len(self.select()[1]), 1)
        self.rows[3]['payload']['output'][0]['text'] = 'changed original'
        self.write_rows()
        self.assertEqual(self.select(), (self.request, []))

    def test_unclassified_requests_and_unpaired_outputs_are_preserved(self):
        self.record()
        for changed in (dict(self.request, prompt_cache_key='other'),
                        dict(self.request, input=[self.user, self.output]),
                        dict(self.request, input=[dict(self.user, content='unknown format'), self.call, self.output])):
            with self.subTest(request=changed):
                self.assertEqual(self.select(changed), (changed, []))

    def test_unknown_blocks_and_ambiguous_native_inputs_cannot_be_selected(self):
        with self.assertRaises(ValueError):
            self.record([dict(self.selection, block_index=9)])
        self.rows.append(dict(type='response_item', payload=dict(self.user, content=[dict(type='input_text', text='different input')])))
        self.write_rows()
        with self.assertRaises(ValueError):
            self.record()

    def test_function_output_contract_and_no_original_bodies_in_core(self):
        self.call = dict(type='function_call', call_id='consumed', name='read', arguments='{}')
        self.output = dict(type='function_call_output', call_id='consumed', output='consumed body ' * 1000)
        self.rows[2]['payload'] = self.call
        self.rows[3]['payload'] = self.output
        self.request['input'] = [self.user, self.call, self.output]
        self.write_rows()
        self.record()
        forwarded, changes = self.select()
        self.assertEqual(len(changes), 1)
        self.assertIsInstance(forwarded['input'][2]['output'], str)
        self.assertNotIn('consumed body', forwarded['input'][2]['output'])
        import sqlite3
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            payload = connection.execute('SELECT payload_json FROM core_context_dispositions').fetchone()[0]
        self.assertNotIn('consumed body', payload)
        self.assertNotIn('required long source', payload)

    def test_locates_selected_literal_without_echoing_output(self):
        coordinates = lifecycle.locate_outputs(self.source, self.fixture.event, 'required long source')
        self.assertEqual([(c['record'], c['block_index']) for c in coordinates], [(4,1)])
        self.assertNotIn('required long source', json.dumps(coordinates))

    def test_source_backed_explanation_requires_no_artificial_execution_unit(self):
        candidate = self.fixture.candidate()
        candidate.update(work_units=[], selected_work_unit_ids=[], selected_criteria=[])
        self.fixture.record(candidate)
        self.record()
        self.assertEqual(len(self.select()[1]), 1)

    def test_consumption_requires_native_input_but_no_work_route(self):
        import test_response_route as fixtures
        fresh = ResponseRouteTests()
        fresh.setUp()
        self.addCleanup(fresh.doCleanups)
        fresh.event = fresh.intake()
        self.fixture = fresh
        self.source['GHOST_ALICE_SESSION_INTENT_ROOT'] = str(fresh.root)
        before = fixtures.ledger.read_session_state(**fresh.coords)
        self.assertIsNone(fresh.api.read_response_route(**fresh.coords))
        self.record()
        self.assertEqual(len(self.select()[1]), 1)
        after = fixtures.ledger.read_session_state(**fresh.coords)
        self.assertEqual(before, after)
        self.assertIsNone(fresh.api.read_response_route(**fresh.coords))

    def test_work_selection_cannot_reintroduce_consumed_body(self):
        self.record()
        self.fixture.record(self.fixture.candidate(units=('second',)))
        actual, observations = self.select()
        self.assertEqual(len(observations), 1)
        self.assertNotIn('consumed navigation body', actual['input'][2]['output'][0]['text'])
        self.assertEqual(actual['input'][2]['output'][1], self.output['output'][1])


class ClaudeContextLifecycleTests(unittest.TestCase):
    """Existing material uses native coordinates without pre-binding every read."""
    def setUp(self):
        self.fixture = ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.coords['platform'] = 'claude'
        self.event = self.fixture.intake('continue actual work')
        self.home = Path(self.fixture.tmp.name) / 'claude'
        self.log = self.home / 'projects' / 'native-project' / 'current.jsonl'
        self.log.parent.mkdir(parents=True)
        self.source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root),
            GHOST_ALICE_PLATFORM='claude', GHOST_ALICE_SESSION_ID='current',
            GHOST_ALICE_CLAUDE_DIR=str(self.home))
        self.thinking = dict(type='thinking', thinking='signed native reasoning', signature='fixture-signature')
        self.unsigned = dict(type='text', text='unsigned prior context')
        self.unknown = dict(type='future-block', opaque={'original': 'unchanged'})
        self.call = dict(type='tool_use', id='read-existing', name='Read', input={'file_path':'/fixture/original'})
        self.output = dict(type='tool_result', tool_use_id='read-existing',
            content='consumed existing navigation ' * 500, is_error=False)
        self.necessary = dict(type='text', text='necessary long sibling ' * 1000)
        self.rows = [self.row('user', 'previous original wording ' * 300),
            # Positive substitution fixtures explicitly have no signed prefix.
            self.row('assistant', [self.unsigned]),
            self.row('assistant', [dict(type='text', text='already delivered answer ' * 300), self.unknown, self.call]),
            self.row('user', [self.output, self.necessary]),
            self.row('user', 'continue actual work', promptSource='sdk', turnOrigin='sdk'),
            self.row('assistant', [dict(type='text', text='current unfinished answer')]),
            self.row('user', '<task-notification>peer result</task-notification>',
                promptSource='system', turnOrigin='task_notification',
                origin={'kind':'task-notification','producer':'session-task'})]
        self.write_rows()
        self.choice = dict(record=4, block_index=0, kind='output', disposition='replace',
            summary='Navigation served the present source decision; uncertainty remains.',
            next_need='Retrieve original wording when the source decision changes.')

    def row(self, role, content, **extra):
        return dict(type=role, sessionId='current', isSidechain=False,
            message=dict(role=role, content=copy.deepcopy(content)), **extra)

    def write_rows(self):
        self.log.write_text(''.join(json.dumps(row) + '\n' for row in self.rows))

    def request(self):
        return dict(model='fixture', thinking={'type': 'disabled'}, system=[dict(type='text', text='necessary system source')],
            messages=[copy.deepcopy(row['message']) for row in self.rows])

    def record(self, choices=None, source=None, event=None):
        return lifecycle.record_disposition(self.source if source is None else source,
            self.event if event is None else event, [self.choice] if choices is None else choices)

    def select(self, request=None, session='current'):
        return lifecycle.select_request(self.request() if request is None else request,
            self.fixture.root, self.home, platform='claude', session_id=session)

    def stage_next_input(self, text, *, origin='human'):
        import test_response_route as fixture_module
        core = fixture_module.ledger
        core.stage_native_input(root=self.fixture.root, platform='claude', session_id='current',
            raw_input=text, prompt_id='next-native-input', transcript_path=str(self.log))
        self.rows.append(self.row('user', text, promptId='next-native-input',
            origin={'kind': origin}, promptSource='sdk', turnOrigin='human' if origin == 'human' else 'sdk'))
        with self.log.open('a') as stream:
            stream.write(json.dumps(self.rows[-1]) + '\n')
        return core

    def append_publication_call(self, name='Bash', identifier='publish-context'):
        self.rows.append(self.row('assistant', [dict(type='tool_use', id=identifier, name=name,
            input={'command': 'already-required context lifecycle publication'}, caller={'type': 'direct'})]))
        self.write_rows()

    def complete_publication(self, receipt):
        self.rows.append(self.row('user', [dict(type='tool_result', tool_use_id='publish-context', content=[
            dict(type='text', text='stdout before\n' + json.dumps({'native_result': receipt})),
            dict(type='text', text='necessary publication sibling')])]))
        self.write_rows()

    def project(self):
        return lifecycle.record_disposition(self.source, self.event, [self.choice], project_replacements=True)

    def server_select(self, request=None):
        request = self.request() if request is None else request
        request = copy.deepcopy(request); request.pop('thinking', None)
        return lifecycle.select_request(request, self.fixture.root, self.home, platform='claude',
            session_id='current', native_betas=('context-management-2025-06-27',))

    def test_native_replacement_publication_reaches_signed_request_before_source_clearing(self):
        self.rows[1]['message']['content'].insert(0, self.thinking)
        self.append_publication_call()
        receipt = self.project()
        self.assertEqual(set(receipt), {'status', 'reference', 'selected_count', 'replacement_context'})
        projection = receipt['replacement_context']
        self.assertEqual(projection, dict(schema_version='context-replacement-output.v1',
            context_reference=receipt['reference'], replacements=[dict(record=4, block_index=0,
                summary=self.choice['summary'], next_need=self.choice['next_need'],
                original=str(self.log.resolve()) + '#record=4,content=0')]))
        self.assertNotIn(self.output['content'], json.dumps(receipt))
        before_result = self.server_select()
        self.assertEqual(before_result.state, 'preserved')
        self.assertNotIn('context_management', before_result.request)
        self.complete_publication(receipt)
        native_before = self.log.read_bytes()
        request = self.request(); request.pop('thinking')
        request['messages'][7]['content'][0].pop('caller')
        result = self.server_select(request)
        self.assertEqual((result.state, result.reason), ('selected', 'server-context-replace-requested'))
        self.assertEqual(result.request['messages'], request['messages'])
        self.assertEqual(result.request['system'], request['system'])
        self.assertEqual(result.request['context_management']['edits'][-1]['exclude_tools'], ['Bash'])
        self.assertFalse(result.request['context_management']['edits'][-1]['clear_tool_inputs'])
        self.assertEqual(result.observations[0]['replacement_carrier']['tool_use_id'], 'publish-context')
        self.assertEqual(result.observations[0]['disposition'], 'replace')
        self.assertEqual(result.observations[0]['provider_application'], 'requested')
        self.assertEqual(self.log.read_bytes(), native_before)
        self.assertEqual(json.loads(self.log.read_text().splitlines()[3])['message']['content'][0], self.output)
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            payload = json.loads(connection.execute('SELECT payload_json FROM core_context_dispositions WHERE reference=?',
                (receipt['reference'],)).fetchone()[0])
        self.assertEqual(payload['schema_version'], 'context-disposition.v5')
        self.assertEqual(payload['replacement_carrier']['call_record'], 8)
        self.assertEqual(payload['replacement_carrier']['call_digest'], lifecycle.fingerprint(self.rows[7]['message']['content'][0]))

    def test_native_replacement_default_return_and_legacy_signed_behavior_are_unchanged(self):
        receipt = self.record()
        self.assertEqual(set(receipt), {'status', 'reference', 'selected_count'})
        result = self.server_select()
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.observations, ())
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            payload = json.loads(connection.execute('SELECT payload_json FROM core_context_dispositions WHERE reference=?',
                (receipt['reference'],)).fetchone()[0])
        self.assertEqual(payload['schema_version'], 'context-disposition.v3')

    def test_native_replacement_requires_actual_unique_pending_carrier_and_whole_replace(self):
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            self.project()
        self.append_publication_call()
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            lifecycle.record_disposition(self.source, self.event, [dict(self.choice, disposition='omit')], project_replacements=True)
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            lifecycle.record_disposition(self.source, self.event, [dict(self.choice, kind='message', record=1)], project_replacements=True)
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            lifecycle.record_disposition(self.source, self.event, [self.choice], project_replacements='yes')
        self.append_publication_call(identifier='ambiguous-context')
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            self.project()
        self.rows.pop(); self.write_rows()
        self.assertIn('replacement_context', self.project())

    def test_native_replacement_exact_completed_carrier_and_wire_are_required(self):
        self.append_publication_call(); receipt = self.project(); self.complete_publication(receipt)
        request = self.request()
        variants = []
        missing = copy.deepcopy(request); missing['messages'][8]['content'][0]['content'] = 'capsule not present'; variants.append(missing)
        changed = copy.deepcopy(request)
        projected = copy.deepcopy(receipt); projected['replacement_context']['replacements'][0]['summary'] = 'changed conclusion'
        changed['messages'][8]['content'][0]['content'] = json.dumps(projected); variants.append(changed)
        duplicate = copy.deepcopy(request)
        duplicate['messages'][8]['content'][0]['content'].append(dict(type='text', text=json.dumps(receipt)))
        variants.append(duplicate)
        changed_call = copy.deepcopy(request); changed_call['messages'][7]['content'][0]['input']['command'] = 'other publication'; variants.append(changed_call)
        duplicate_call = copy.deepcopy(request); duplicate_call['messages'][7]['content'].append(copy.deepcopy(duplicate_call['messages'][7]['content'][0])); variants.append(duplicate_call)
        for index, candidate in enumerate(variants):
            with self.subTest(variant=index):
                result = self.server_select(candidate)
                self.assertEqual(result.state, 'preserved')
                self.assertNotIn('context_management', result.request)
                self.assertEqual(result.observations, ())
        self.assertEqual(self.server_select().state, 'selected')
        self.rows[7]['message']['content'][0]['input']['command'] = 'changed native original'; self.write_rows()
        self.assertEqual(self.server_select().state, 'preserved')

    def test_native_replacement_preserves_carrier_name_collision_and_new_current_human(self):
        self.append_publication_call(name='Read'); receipt = self.project(); self.complete_publication(receipt)
        collision = self.server_select()
        self.assertEqual(collision.state, 'selected')
        self.assertEqual(collision.reason, 'server-context-replace-requested')
        self.assertEqual(collision.request['context_management']['edits'][-1]['keep'],{'type':'tool_uses','value':1})
        self.assertEqual(collision.observations[0]['replacement_carrier']['tool_use_id'],'publish-context')
        # A new valid publication uses its actual distinct native tool name.
        self.rows.pop(); self.rows.pop(); self.write_rows()
        self.append_publication_call(); receipt = self.project(); self.complete_publication(receipt)
        core = self.stage_next_input('Newest human requires the retained uncertainty')
        before = self.log.read_bytes(); request = self.request(); request.pop('thinking')
        result = lifecycle.prepare_native_request(request, self.fixture.root, self.home,
            platform='claude', session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.reason, 'server-context-replace-requested')
        self.assertEqual(result.request['messages'], request['messages'])
        self.assertEqual(self.log.read_bytes(), before)
        self.assertFalse(core.read_session_state(**self.fixture.coords)['pending_native_inputs'])

    def test_native_replacement_never_uses_records_outside_its_declared_original_prefix(self):
        import claude_context_adapter as adapter
        self.append_publication_call(); receipt = self.project(); self.complete_publication(receipt)
        self.rows.extend([self.row('assistant', [dict(type='tool_use', id='later-source', name='Other', input={})]),
            self.row('user', [dict(type='tool_result', tool_use_id='later-source', content='later required original')])])
        self.write_rows()
        native = adapter.native_records(str(self.log.resolve()))
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            payload = json.loads(connection.execute('SELECT payload_json FROM core_context_dispositions WHERE reference=?',
                (receipt['reference'],)).fetchone()[0])
            payload['selected'].append(dict(record=11, block_index=0, kind='output', disposition='omit',
                summary='Unsupported claim that later source was used', next_need='Retrieve if needed',
                item_digest=lifecycle.fingerprint(native[11]), block_digest=lifecycle.fingerprint('later required original')))
            forged_reference = lifecycle.fingerprint(payload)
            connection.execute('INSERT INTO core_context_dispositions VALUES (?,?)', (forged_reference, json.dumps(payload)))
            connection.execute('UPDATE core_context_current SET reference=?', (forged_reference,)); connection.commit()
        result = self.server_select()
        self.assertEqual(result.state, 'preserved')
        self.assertNotIn('context_management', result.request)
        self.assertEqual(result.observations, ())

    def test_native_replacement_unclassified_or_malformed_original_descriptor_is_preserved(self):
        self.append_publication_call(); receipt = self.project(); self.complete_publication(receipt)
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            original = json.loads(connection.execute('SELECT payload_json FROM core_context_dispositions WHERE reference=?',
                (receipt['reference'],)).fetchone()[0])
            for descriptor in ({}, {'locator': str(self.log.resolve())},
                {'storage': 'unknown-inline', 'locator': str(self.log.resolve()), 'text': self.log.read_text()}):
                with self.subTest(descriptor=list(descriptor)):
                    payload = copy.deepcopy(original); payload['original'] = descriptor
                    reference = lifecycle.fingerprint(payload)
                    connection.execute('INSERT INTO core_context_dispositions VALUES (?,?)', (reference, json.dumps(payload)))
                    connection.execute('UPDATE core_context_current SET reference=?', (reference,)); connection.commit()
                    result = self.server_select()
                    self.assertEqual(result.state, 'preserved')
                    self.assertNotIn('context_management', result.request)
                    self.assertEqual(result.observations, ())

    def test_native_replacement_does_not_clear_a_whole_result_with_a_kept_producer(self):
        import response_context_use as use
        import source_lookup
        source_file = Path(self.fixture.tmp.name) / 'required-original.json'
        source_file.write_text(json.dumps({'detail': 'Exact original wording remains needed'}))
        # Keep this causal test independent of the same-name scope limitation.
        self.rows[2]['message']['content'][-1]['name'] = 'OtherOldSource'
        observation = source_lookup.lookup(source_file, pointer='/detail')
        self.rows.append(self.row('assistant', [dict(type='tool_use', id='leased-read', name='Read', input={})]))
        self.write_rows()
        lease = use.record_use(self.source, self.event, observation, purpose='Compare exact original wording')
        self.rows.append(self.row('user', [dict(type='tool_result', tool_use_id='leased-read', content=[
            dict(type='text', text=json.dumps(dict(observation, use_reference=lease['reference']))),
            dict(type='text', text='necessary whole-result sibling')])]))
        self.write_rows()
        use.record_use_judgments(self.source, self.event, [dict(use_reference=lease['reference'], disposition='keep',
            summary='Exact original wording is still needed', next_need='Compare the original wording')])
        self.choice = dict(self.choice, record=9)
        self.append_publication_call(); receipt = self.project(); self.complete_publication(receipt)
        result = self.server_select()
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.reason, 'context-disposition-conflict')
        self.assertNotIn('context_management', result.request)
        self.assertEqual(result.observations, ())
        self.assertIn(observation['value'], json.dumps(result.request))
        self.assertIn('necessary whole-result sibling', json.dumps(result.request))
        use.record_use_judgments(self.source, self.event, [dict(use_reference=lease['reference'], disposition='omit',
            summary='Only the explicitly published whole-result conclusion is now needed', next_need='Retrieve original on changed comparison')])
        changed_need = self.server_select()
        self.assertEqual((changed_need.state, changed_need.reason), ('selected', 'server-context-replace-requested'))
        self.assertEqual(changed_need.request['context_management']['edits'][-1]['exclude_tools'], ['Bash', 'OtherOldSource'])

    def test_native_replacement_public_cli_returns_capsule_in_existing_publication(self):
        self.append_publication_call()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            lifecycle.main(['--root', str(self.fixture.root), '--platform', 'claude', '--session-id', 'current',
                '--native-dir', str(self.home), '--expected-input-event-id', self.event,
                '--selection-json', json.dumps([self.choice]), '--project-replacements'])
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt['replacement_context']['replacements'][0]['summary'], self.choice['summary'])
        self.complete_publication(receipt)
        self.assertEqual(self.server_select().state, 'selected')

    def test_first_generation_reconciles_committed_input_before_any_tool_call(self):
        self.record([dict(self.choice, disposition='omit')])
        core = self.stage_next_input('Latest request needs only its saved link')
        before = core.read_session_state(**self.fixture.coords)
        self.assertTrue(before['pending_native_inputs'])
        native_before = self.log.read_bytes()
        request = dict(self.request(), thinking={'type': 'adaptive'})
        result = lifecycle.prepare_native_request(request, self.fixture.root, self.home,
            platform='claude', session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual((result.state, result.reason), ('selected', 'server-context-omit-requested'))
        self.assertEqual(result.request['messages'], request['messages'])
        self.assertEqual(result.request['messages'][-1]['content'], self.rows[-1]['message']['content'])
        self.assertEqual(self.log.read_bytes(), native_before)
        after = core.read_session_state(**self.fixture.coords)
        self.assertFalse(after['pending_native_inputs'])
        self.assertNotEqual(after['latest_input_event_id'], before['latest_input_event_id'])
        count = len(core.read_session_events(**self.fixture.coords))
        lifecycle.prepare_native_request(request, self.fixture.root, self.home,
            platform='claude', session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual(len(core.read_session_events(**self.fixture.coords)), count)

    def test_generation_never_promotes_unknown_origin_to_apply_an_old_selection(self):
        self.record([dict(self.choice, disposition='omit')])
        core = self.stage_next_input('Unknown source remains pending', origin='unclassified')
        before = core.read_session_state(**self.fixture.coords)
        request = dict(self.request(), thinking={'type': 'adaptive'})
        result = lifecycle.prepare_native_request(request, self.fixture.root, self.home,
            platform='claude', session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.request, request)
        after = core.read_session_state(**self.fixture.coords)
        self.assertEqual(after['latest_input_event_id'], before['latest_input_event_id'])
        self.assertEqual(after['pending_native_inputs'], before['pending_native_inputs'])

    def test_new_native_input_does_not_authorize_an_older_wire_request(self):
        self.record([dict(self.choice, disposition='omit')])
        old_request = dict(self.request(), thinking={'type': 'adaptive'})
        self.stage_next_input('A new user correction changes the current instruction')
        result = lifecycle.prepare_native_request(old_request, self.fixture.root, self.home,
            platform='claude', session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.request, old_request)

    def test_generation_cannot_authenticate_a_replaced_native_source(self):
        self.record([dict(self.choice, disposition='omit')])
        core = self.stage_next_input('Only the committed original may establish current input')
        before = core.read_session_state(**self.fixture.coords)
        replacement = self.log.with_suffix('.replacement')
        replacement.write_bytes(self.log.read_bytes())
        replacement.replace(self.log)
        request = dict(self.request(), thinking={'type': 'adaptive'})
        result = lifecycle.prepare_native_request(request, self.fixture.root, self.home,
            platform='claude', session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.request, request)
        self.assertEqual(core.read_session_state(**self.fixture.coords)['latest_input_event_id'],
                         before['latest_input_event_id'])

    def test_generation_intake_storage_failure_does_not_forward(self):
        request = dict(self.request(), thinking={'type': 'adaptive'})
        with patch.object(lifecycle.core_api, '_coordinates', side_effect=sqlite3.OperationalError('unavailable')):
            result = lifecycle.prepare_native_request(request, self.fixture.root, self.home,
                platform='claude', session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual((result.state, result.reason), ('unavailable', 'intake-storage-unavailable'))
        self.assertEqual(result.request, request)

    def test_current_desktop_human_block_preserves_host_guidance_in_entire_wire_identity(self):
        self.rows[4]['message']['content'] = [dict(type='text', text='host guidance retained'),
                                            dict(type='text', text='continue actual work')]
        self.write_rows()
        self.record([dict(self.choice, disposition='omit')])
        result = self.select()
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.request['messages'][4], self.request()['messages'][4])

    def test_native_beta_allows_authenticated_server_omit_with_signed_default_thinking(self):
        import inspect
        self.assertIn('native_betas', inspect.signature(lifecycle.select_request).parameters,
                      'The production selector cannot use a host-advertised server-edit capability')
        self.rows[1]['message']['content'] = [copy.deepcopy(self.thinking)]
        self.write_rows()
        self.record([dict(self.choice, disposition='omit')])
        request = self.request()
        del request['thinking']
        before = self.log.read_bytes()
        result = lifecycle.select_request(request, self.fixture.root, self.home, platform='claude',
            session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.reason, 'server-context-omit-requested')
        self.assertEqual(result.request['messages'], request['messages'])
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(self.log.read_bytes(), before)

    def test_host_owned_signed_compaction_request_never_gets_tool_clear_policy(self):
        self.record([dict(self.choice, disposition='omit')])
        request = self.request()
        request['thinking'] = {'type': 'adaptive'}
        request['compaction'] = {'type': 'summarize'}
        result = lifecycle.select_request(request, self.fixture.root, self.home, platform='claude',
            session_id='current', native_betas=('context-management-2025-06-27', 'compact-2026-09-04'))
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.reason, 'signed-compaction-request-owned-by-host')
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())

    def test_core_authenticates_explicit_input_scope_and_server_applies_only_that_scope(self):
        self.record([dict(self.choice, disposition='omit', clear_tool_inputs=True)])
        request = dict(self.request(), thinking={'type': 'adaptive'})
        result = lifecycle.select_request(request, self.fixture.root, self.home, platform='claude',
            session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'selected')
        self.assertTrue(result.request['context_management']['edits'][-1]['clear_tool_inputs'])
        choice = result.observations[0]
        self.assertEqual(choice['call_record'], 3)
        self.assertEqual(choice['call_digest'], lifecycle.fingerprint(self.call))
        self.assertEqual(result.request['messages'], request['messages'])
        missing = self.select(request)
        self.assertEqual(missing.state, 'preserved')
        unsigned = dict(request, thinking={'type': 'disabled'})
        missing_unsigned = self.select(unsigned)
        self.assertEqual(missing_unsigned.reason, 'tool-input-selection-requires-server')
        supported_unsigned = lifecycle.select_request(unsigned, self.fixture.root, self.home,
            platform='claude', session_id='current', native_betas=('context-management-2025-06-27',))
        self.assertEqual(supported_unsigned.state, 'selected')
        self.assertTrue(supported_unsigned.request['context_management']['edits'][-1]['clear_tool_inputs'])
        self.assertEqual(supported_unsigned.request['messages'], unsigned['messages'])

    def test_input_scope_cannot_expand_non_omit_or_untyped_judgment(self):
        for flag, disposition in [(True, 'replace'), ('yes', 'omit'), (True, 'keep')]:
            with self.subTest(flag=flag, disposition=disposition), self.assertRaises(lifecycle.ContextSelectionRejected):
                self.record([dict(self.choice, disposition=disposition, clear_tool_inputs=flag)])

    def test_unsigned_input_scope_requires_exact_native_beta_tokens(self):
        self.record([dict(self.choice, disposition='omit', clear_tool_inputs=True)])
        for betas in (None, 'not-a-beta-context-management-2025-06-27-suffix',
                      {'context-management-2025-06-27': True}, (), ['not-a-beta']):
            with self.subTest(betas=betas):
                result = lifecycle.select_request(self.request(), self.fixture.root, self.home,
                    platform='claude', session_id='current', native_betas=betas)
                self.assertEqual(result.state, 'preserved')
                self.assertEqual(result.reason, 'tool-input-selection-requires-server')

    def test_omit_unsigned_existing_output_and_prior_text_preserve_protocol(self):
        self.record([dict(self.choice, disposition='omit'),
                     dict(self.choice, record=3, block_index=0, kind='assistant', disposition='omit')])
        before = self.log.read_bytes()
        result = self.select()
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.request['messages'][3]['content'], [dict(self.output, content=''), self.necessary])
        self.assertEqual(result.request['messages'][2]['content'], [self.unknown, self.call])
        self.assertEqual(result.request['messages'][4:], self.request()['messages'][4:])
        self.assertTrue(all(o['disposition'] == 'omit' for o in result.observations))
        self.assertNotIn(self.choice['summary'], json.dumps(result.request))
        self.assertEqual(self.log.read_bytes(), before)
        for thinking in (None, {'type':'enabled'}):
            request = self.request()
            if thinking is None:
                request.pop('thinking')
            else:
                request['thinking'] = thinking
            result = self.select(request)
            self.assertEqual(result.request, request)
            self.assertEqual(result.reason, 'signed-prefix-selection-unsupported')
            self.assertEqual(result.observations, ())

    def test_existing_output_coordinates_replace_only_selected_native_block(self):
        before = self.log.read_bytes()
        receipt = self.record()
        request = self.request()
        result = self.select(request)
        self.assertEqual((receipt['selected_count'], result.state), (1, 'selected'))
        self.assertEqual(result.request['messages'][:3], request['messages'][:3])
        self.assertEqual(result.request['messages'][4:], request['messages'][4:])
        self.assertEqual(result.request['messages'][3]['content'][1], self.necessary)
        replacement = result.request['messages'][3]['content'][0]
        self.assertEqual({k:v for k,v in replacement.items() if k != 'content'},
                         {k:v for k,v in self.output.items() if k != 'content'})
        payload = json.loads(replacement['content'])
        self.assertEqual(payload['original'], str(self.log.resolve()) + '#record=4,content=0')
        self.assertEqual(payload['disposition_input_event_id'], self.event)
        self.assertEqual(result.observations[0]['current_input_event_id'], self.event)
        self.assertEqual((result.observations[0]['record'], result.observations[0]['block_index']), (4,0))
        self.assertEqual(self.log.read_bytes(), before)
        self.assertIsNone(self.fixture.api.read_response_route(**self.fixture.coords))
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            stored = connection.execute('SELECT payload_json FROM core_context_dispositions').fetchone()[0]
        self.assertNotIn('consumed existing navigation', stored)
        self.assertNotIn('necessary long sibling', stored)

    def test_line_separator_inside_a_native_record_does_not_split_it(self):
        """Claude Code writes U+2028 unescaped inside JSON strings; only a newline ends a record."""
        self.rows[0]['message']['content'] += '\u2028after the separator'
        self.log.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in self.rows), encoding='utf-8')
        self.assertIn('\u2028', self.log.read_text(encoding='utf-8'))
        receipt = self.record()
        result = self.select()
        self.assertEqual((receipt['selected_count'], result.state), (1, 'selected'))

    def test_unfinished_last_record_is_not_yet_in_the_native_log(self):
        """Claude Code may still be appending a record when its request arrives."""
        record = json.dumps(self.row('assistant', [dict(type='text', text='가')]), ensure_ascii=False).encode()
        with self.log.open('ab') as stream:
            stream.write(record[:record.index('가'.encode()) + 1])
        receipt = self.record()
        result = self.select()
        self.assertEqual((receipt['selected_count'], result.state), (1, 'selected'))

    def test_unique_literal_keep_replace_and_empty_refresh_need_no_lookup_receipt(self):
        choice = {k:v for k,v in self.choice.items() if k not in ('record','block_index')}
        choice['locate_text'] = 'consumed existing navigation'
        found = lifecycle.locate_outputs(self.source, self.event, choice['locate_text'])
        self.assertEqual([(v['record'],v['block_index']) for v in found], [(4,0)])
        self.assertNotIn(choice['locate_text'], json.dumps(found))
        self.record([dict(choice, disposition='keep')])
        self.assertEqual(self.select().request, self.request())
        self.record([choice])
        self.assertEqual(self.select().state, 'selected')
        self.record([])
        self.assertEqual(self.select().request, self.request())

    def test_unsigned_prior_user_and_assistant_text_preserve_roles_and_siblings(self):
        choices = [dict(self.choice, kind=kind, record=record, block_index=0)
            for kind,record in [('message',1),('assistant',3)]]
        self.record(choices)
        request = self.request()
        result = self.select(request)
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(result.request['messages'][0]['role'], 'user')
        self.assertIsInstance(result.request['messages'][0]['content'], str)
        self.assertEqual(result.request['messages'][2]['role'], 'assistant')
        self.assertEqual(result.request['messages'][1], request['messages'][1])
        self.assertEqual(result.request['messages'][2]['content'][1:], request['messages'][2]['content'][1:])
        self.assertEqual(result.request['messages'][3:], request['messages'][3:])

    def test_merged_wire_messages_match_unique_blocks_with_cache_metadata(self):
        self.record([self.choice, dict(self.choice, kind='assistant', record=3)])
        request = self.request()
        request['messages'][1:3] = [dict(role='assistant', content=[
            copy.deepcopy(self.unsigned), *copy.deepcopy(self.rows[2]['message']['content'])])]
        request['messages'][1]['content'][-1]['cache_control'] = {'type':'ephemeral'}
        request['messages'][2]['content'][0]['cache_control'] = {'type':'ephemeral'}
        result = self.select(request)
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(result.request['messages'][1]['content'][0], self.unsigned)
        self.assertEqual(result.request['messages'][1]['content'][2:], request['messages'][1]['content'][2:])
        self.assertEqual(result.request['messages'][2]['content'][0]['cache_control'], {'type':'ephemeral'})
        self.assertEqual(result.request['messages'][2]['content'][1], self.necessary)

    def test_disposition_carries_forward_under_next_actual_human_and_exact_binding(self):
        receipt = self.record()
        next_event = self.fixture.intake('continue source analysis')
        self.rows.append(self.row('user', 'continue source analysis', promptSource='sdk', turnOrigin='sdk'))
        self.write_rows()
        result = self.select()
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.observations[0]['reference'], receipt['reference'])
        self.assertEqual(result.observations[0]['current_input_event_id'], next_event)
        self.assertEqual(result.observations[0]['disposition_input_event_id'], self.event)
        self.assertEqual(result.request['messages'][-1], self.request()['messages'][-1])
        with self.assertRaises(ValueError):
            self.record()

    def test_changed_current_human_and_missing_explicit_session_preserve_request(self):
        self.record()
        request = self.request()
        request['messages'][4]['content'] += '\nchanged input'
        for candidate, session in [(request,'current'), (self.request(),None), (self.request(),'foreign')]:
            with self.subTest(session=session):
                result = self.select(candidate, session=session)
                self.assertEqual(result.request, candidate)
                self.assertEqual(result.observations, ())

    def test_latest_native_human_does_not_fall_back_to_an_old_matching_input(self):
        self.record()
        self.rows.append(self.row('user', 'another actual human', promptSource='sdk', turnOrigin='sdk'))
        self.write_rows()
        self.assertEqual(self.select().state, 'preserved')
        with self.assertRaises(ValueError):
            self.record()

    def test_current_human_cannot_be_substituted_when_prior_equal_text_is_absent(self):
        self.rows[0]['message']['content'] = 'continue actual work'
        self.write_rows()
        self.record([dict(self.choice, kind='message', record=1)])
        request = self.request()
        del request['messages'][0]
        result = self.select(request)
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())

    def test_missing_ambiguous_or_changed_wire_tool_pair_is_preserved(self):
        self.record()
        candidates = []
        missing = self.request(); del missing['messages'][2]['content'][2]; candidates.append(missing)
        duplicate = self.request(); duplicate['messages'][3]['content'].append(copy.deepcopy(self.output)); candidates.append(duplicate)
        changed = self.request(); changed['messages'][2]['content'][2]['input']['file_path'] = '/fixture/other'; candidates.append(changed)
        for candidate in candidates:
            with self.subTest(candidate=candidates.index(candidate)):
                result = self.select(candidate)
                self.assertEqual(result.request, candidate)
                self.assertEqual(result.observations, ())

    def test_current_turn_call_cannot_move_before_its_human_while_result_stays_after(self):
        call = dict(type='tool_use', id='current-turn-read', name='Read', input={'file_path':'/fixture/current'})
        output = dict(type='tool_result', tool_use_id=call['id'], content='consumed current result')
        self.rows.extend([self.row('assistant', [call]), self.row('user', [output])])
        self.write_rows()
        self.record([dict(self.choice, record=9)])
        self.assertEqual(self.select().state, 'selected')
        request = self.request()
        request['messages'].insert(4, request['messages'].pop(7))
        result = self.select(request)
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())

    def test_native_owner_mismatch_sidechain_or_duplicate_exact_path_cannot_publish(self):
        for extra in (dict(sessionId='foreign'), dict(isSidechain=True)):
            self.rows[4].update(extra); self.write_rows()
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.record()
            self.rows[4] = self.row('user', 'continue actual work', promptSource='sdk', turnOrigin='sdk')
        self.write_rows()
        duplicate = self.home / 'projects' / 'other' / 'current.jsonl'
        duplicate.parent.mkdir(); duplicate.write_bytes(self.log.read_bytes())
        with self.assertRaises(ValueError):
            self.record()

    def test_ambiguous_original_literal_rejected_and_native_pair_must_be_unique(self):
        duplicate = dict(self.output, tool_use_id='other-call')
        self.rows[3]['message']['content'].append(duplicate); self.write_rows()
        literal = dict(kind='output', locate_text='consumed existing navigation',
            summary=self.choice['summary'], next_need=self.choice['next_need'])
        with self.assertRaisesRegex(ValueError, 'ambiguous|unique'):
            self.record([literal])
        self.rows[3]['message']['content'][-1]['tool_use_id'] = self.call['id']; self.write_rows()
        with self.assertRaisesRegex(ValueError, 'pair|one|unique'):
            self.record()

    def test_current_signed_unknown_and_exchange_choices_are_rejected(self):
        self.rows[1]['message']['content'] = [copy.deepcopy(self.thinking)]
        self.write_rows()
        for kind,record,index in [('message',5,0),('assistant',6,0),('assistant',2,0),
                                  ('assistant',3,1),('exchange',4,0),('future-kind',4,0)]:
            with self.subTest(kind=kind, record=record), self.assertRaises(ValueError):
                self.record([dict(self.choice, kind=kind, record=record, block_index=index)])
        self.assertEqual(self.select().request, self.request())

    def test_original_append_allowed_and_original_changed_content_preserved(self):
        self.record()
        with self.log.open('a') as stream:
            stream.write(json.dumps({'type':'file-history-snapshot','snapshot':{}}) + '\n')
        self.assertEqual(self.select().state, 'selected')
        self.rows[3]['message']['content'][0]['content'] = 'changed original'
        self.write_rows()
        request = self.request()
        result = self.select(request)
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())

    def ledger_rows(self):
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            return connection.execute('SELECT * FROM core_context_dispositions').fetchall(), \
                connection.execute('SELECT * FROM core_context_current').fetchall()

    def test_prior_text_before_later_thinking_preserves_whole_request_native_and_ledger(self):
        self.rows.insert(4, self.row('assistant', [copy.deepcopy(self.thinking)]))
        self.write_rows()
        self.record([dict(self.choice, kind='message', record=1)])
        request = self.request()
        before_native, before_ledger = self.log.read_bytes(), self.ledger_rows()
        serialized = json.dumps(request, ensure_ascii=False)
        result = self.select(request)
        self.assertEqual((result.state, result.reason), ('preserved', 'signed-prefix-selection-unsupported'))
        self.assertEqual(result.observations, ())
        self.assertEqual(result.request, request)
        self.assertEqual(json.dumps(result.request, ensure_ascii=False), serialized)
        self.assertEqual(self.log.read_bytes(), before_native)
        self.assertEqual(self.ledger_rows(), before_ledger)

    def test_thinking_enabled_initial_request_preserves_manual_output_selection(self):
        self.record()
        for kind in ('enabled', 'adaptive', 'between_tools'):
            with self.subTest(kind=kind):
                request = dict(self.request(), thinking={'type': kind})
                result = self.select(request)
                self.assertEqual((result.state, result.reason), ('preserved', 'signed-prefix-selection-unsupported'))
                self.assertEqual(result.request, request)
                self.assertEqual(result.observations, ())

    def test_missing_thinking_on_initial_request_preserves_manual_output_selection(self):
        self.record()
        request = self.request()
        del request['thinking']
        result = self.select(request)
        self.assertEqual((result.state, result.reason), ('preserved', 'signed-prefix-selection-unsupported'))
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())

    def test_signed_prefix_with_keep_empty_or_missing_selection_retains_plain_preserved_reason(self):
        self.rows.insert(4, self.row('assistant', [copy.deepcopy(self.thinking)]))
        self.write_rows()
        self.assertEqual(self.select().reason, 'selection-absent')
        for choices in ([], [dict(self.choice, disposition='keep')]):
            with self.subTest(choices=choices):
                self.record(choices)
                result = self.select()
                self.assertEqual((result.state, result.reason), ('preserved', 'selection-not-in-request'))
                self.assertEqual(result.request, self.request())
                self.assertEqual(result.observations, ())


if __name__ == '__main__':
    unittest.main()
