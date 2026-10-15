"""Explicit lookup-use scopes, native closure, and exact producer substitution."""
import copy
from contextlib import closing
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_response_route as route_fixture
import source_lookup

try:
    use = importlib.import_module('response_context_use')
except ModuleNotFoundError as error:
    if error.name != 'response_context_use':
        raise
    use = None


def _measure_projection(case, result, capsule, observation, platform, message_index):
    """Measure only representation bytes using the same fixture and serializer."""
    audit = result.observations[0]
    before_capsule = dict(use_reference=capsule['use_reference'], disposition='replace', purpose=capsule['purpose'],
        summary=capsule['summary'], source=audit['source'], next_need=capsule['next_need'],
        judgment_reference=audit['judgment_reference'], original=capsule['original'],
        original_pointer=capsule['original_pointer'], original_item_digest=audit['original_item_digest'],
        observation_digest=use.fingerprint(observation))
    if audit['native_answer'] is not None:
        before_capsule['native_answer'] = audit['native_answer']
    before_text = json.dumps(before_capsule, ensure_ascii=False)
    before_request = copy.deepcopy(result.request)
    if platform == 'codex':
        after_text = result.request['input'][message_index]['output'][0]['text']
        before_request['input'][message_index]['output'][0]['text'] = before_text
    else:
        after_text = result.request['messages'][message_index]['content'][0]['content'][0]['text']
        before_request['messages'][message_index]['content'][0]['content'][0]['text'] = before_text
    serialize = lambda request: json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    measurement = dict(case=case, unit='utf8_bytes',
        capsule_serializer='json.dumps(ensure_ascii=False), default separators',
        request_serializer='json.dumps(ensure_ascii=False,sort_keys=True,separators=(comma,colon))',
        before_capsule_bytes=len(before_text.encode()), after_capsule_bytes=len(after_text.encode()),
        before_projected_request_bytes=len(serialize(before_request)),
        after_projected_request_bytes=len(serialize(result.request)),
        before_fields=list(before_capsule), after_fields=list(capsule))
    print(json.dumps(dict(representation_measurement=measurement), ensure_ascii=False))
    return measurement


class ResponseContextUseTests(unittest.TestCase):
    def test_claude_owner_and_messages_use_one_snapshot_during_native_append(self):
        import claude_context_adapter as adapter
        path = Path(self.fixture.tmp.name) / 'claude-live.jsonl'
        first = dict(type='user', sessionId='current', origin={'kind': 'human'},
                     message=dict(role='user', content='current request'))
        later = dict(type='assistant', sessionId='other-owner',
                     message=dict(role='assistant', content=[dict(type='text', text='appended later')]))
        path.write_text(json.dumps(first) + '\n')
        normalize = adapter._normalize_message
        appended = False
        def append_while_normalizing(role, content):
            nonlocal appended
            if not appended:
                with path.open('a') as stream:
                    stream.write(json.dumps(later) + '\n')
                appended = True
            return normalize(role, content)
        with patch.object(adapter, '_normalize_message', side_effect=append_while_normalizing):
            native = use._native(str(path), 'claude', 'current')
        self.assertEqual(list(native.records), [1])
        self.assertEqual(native.humans, (1,))
        with self.assertRaises(use.ResponseUseRejected):
            use._native(str(path), 'claude', 'current')

    def setUp(self):
        self.fixture = route_fixture.ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.event = self.fixture.intake()
        self.home = Path(self.fixture.tmp.name) / 'codex'
        self.log = self.home / 'sessions' / 'rollout-current.jsonl'
        self.log.parent.mkdir(parents=True)
        self.source_file = Path(self.fixture.tmp.name) / 'PLAN.json'
        self.source_file.write_text(json.dumps({'needed': 'source body needed for this response',
                                                'unleased': 'required independent source'}))
        self.observation = source_lookup.lookup(self.source_file, pointer='/needed')
        self.user = self.human('implement first')
        self.call = dict(type='custom_tool_call', call_id='lookup', name='exec', input='read selected original')
        self.rows = [dict(type='session_meta', payload={'id': 'current'}),
                     dict(type='response_item', payload=self.user),
                     dict(type='response_item', payload=self.call)]
        self.source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root),
                           GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='current',
                           CODEX_THREAD_ID='current', GHOST_ALICE_CODEX_DIR=str(self.home))
        self.write_rows()

    def human(self, text):
        return dict(type='message', role='user', content=[dict(type='input_text', text=text)])

    def write_rows(self):
        self.log.write_text(''.join(json.dumps(row) + '\n' for row in self.rows))

    def append(self, item):
        self.rows.append(dict(type='response_item', payload=item))
        with self.log.open('a') as stream:
            stream.write(json.dumps(self.rows[-1]) + '\n')

    def record(self, observation=None, **kwargs):
        self.assertIsNotNone(use, 'Required same-lookup record_use API is absent')
        return use.record_use(self.source, kwargs.pop('event', self.event),
            self.observation if observation is None else observation,
            purpose=kwargs.pop('purpose', 'Compare the selected original for this answer'),
            **kwargs)

    def output(self, receipt, payload=None):
        producer = dict(self.observation, use_reference=receipt['reference'])
        value = producer if payload is None else payload(producer)
        output = dict(type='custom_tool_call_output', call_id='lookup',
                      output=[dict(type='input_text', text=json.dumps(value))])
        self.append(output)
        return output

    def judge(self, receipt, *, disposition='replace', summary='The original supports this comparison; remaining uncertainty needs follow-up',
              next_need='Retrieve the original when its wording is needed', event=None, transaction=None):
        self.assertTrue(callable(getattr(use, 'record_use_judgments', None)), 'Required explicit post-use judgment API is absent')
        judgments = [dict(use_reference=reference['reference'], disposition=disposition,
                          summary=summary, next_need=next_need)
                     for reference in (receipt if isinstance(receipt, list) else [receipt])]
        return use.record_use_judgments(self.source, self.event if event is None else event,
                                         judgments, transaction=transaction)

    def final(self, **values):
        item = dict(type='message', role='assistant', phase='final_answer',
                    content=[dict(type='output_text', text='The selected original supports this conclusion.')])
        item.update(values)
        self.append(item)
        return item

    def next_human(self, text='next decision'):
        self.fixture.intake(text)
        item = self.human(text)
        self.append(item)
        return item

    def request(self):
        return dict(prompt_cache_key='current', input=[copy.deepcopy(r['payload']) for r in self.rows
                    if r['type'] == 'response_item'], model='fixture')

    def select(self, request=None):
        self.assertIsNotNone(use, 'Required native response-bound use selector is absent')
        return use.select_uses(self.request() if request is None else request, self.fixture.root, self.home)

    def test_explicit_replace_changes_same_response_and_multiple_next_inputs(self):
        receipt = self.record()
        original_source = self.source_file.read_bytes()
        original_output = self.output(receipt)
        active = self.select()
        self.assertEqual(active.state, 'preserved')
        self.assertEqual(active.request, self.request())
        self.judge(receipt)
        current = self.select()
        self.assertEqual(current.state, 'selected')
        self.assertNotIn(self.observation['value'], json.dumps(current.request))
        self.assertIn('remaining uncertainty needs follow-up', json.dumps(current.request))
        self.assertIn('wording is needed', json.dumps(current.request))
        self.assertEqual(current.request['input'][:2], self.request()['input'][:2])
        answer = self.final()
        self.assertEqual(self.select().state, 'selected')
        self.next_human()
        for next_input in (None, 'another actual human'):
            if next_input:
                self.next_human(next_input)
            before = self.log.read_bytes()
            result = self.select()
            self.assertEqual(result.state, 'selected')
            self.assertNotIn(self.observation['value'], json.dumps(result.request))
            self.assertEqual(result.request['input'][1], self.call)
            self.assertEqual(result.request['input'][3], answer)
            self.assertIn('/needed', json.dumps(result.observations))
            self.assertIn('PLAN.json', json.dumps(result.observations))
            self.assertEqual(self.log.read_bytes(), before)
            observation = result.observations[0]
            self.assertEqual(observation['original_item_digest'], use.fingerprint(original_output))
            self.assertEqual(observation['forwarded_item_digest'], use.fingerprint(result.request['input'][2]))
        self.assertEqual(self.source_file.read_bytes(), original_source)
        self.assertEqual(source_lookup.lookup(self.source_file, pointer='/needed')['value'], self.observation['value'])

    def test_same_transaction_can_resolve_current_event_without_cached_receipt(self):
        receipt = self.record(event=None)
        self.output(receipt); self.judge(receipt); self.final(); self.next_human()
        self.assertEqual(self.select().state, 'selected')

    def test_omit_nested_producer_retains_stdout_json_sdk_and_result_envelopes(self):
        receipt = self.record()
        self.output(receipt, lambda producer: dict(content=[dict(type='text', text=json.dumps(producer))],
                                                  necessary='required independent wrapper'))
        self.rows[-1]['payload']['output'][0]['text'] = 'stdout before\n' + self.rows[-1]['payload']['output'][0]['text'] + '\nstdout after'
        self.write_rows()
        original = self.log.read_bytes()
        self.judge(receipt, disposition='omit')
        result = self.select()
        self.assertEqual(result.state, 'selected')
        expected = dict(content=[dict(type='text', text='{}')], necessary='required independent wrapper')
        self.assertEqual(result.request['input'][2]['output'][0]['text'],
                         'stdout before\n' + json.dumps(expected) + '\nstdout after')
        self.assertEqual(result.request['input'][:2], self.request()['input'][:2])
        self.assertEqual(result.observations[0]['disposition'], 'omit')
        self.assertIn('PLAN.json', json.dumps(result.observations))
        for forbidden in (receipt['reference'], self.observation['value'], 'remaining uncertainty needs follow-up'):
            self.assertNotIn(forbidden, json.dumps(result.request))
        self.assertEqual(self.log.read_bytes(), original)

    def test_omit_independent_sources_then_keep_reopens_only_its_original_scope(self):
        first = self.record()
        other = source_lookup.lookup(self.source_file, pointer='/unleased')
        second = self.record(other, purpose='Use independent source')
        original = self.output(first, lambda producer: [producer, dict(other, use_reference=second['reference']),
                                                       {'necessary': 'sibling'}])
        self.judge([first, second], disposition='omit')
        for text in (None, 'second human', 'third human'):
            if text is not None:
                self.next_human(text)
            result = self.select()
            self.assertEqual(json.loads(result.request['input'][2]['output'][0]['text']),
                             [{}, {}, {'necessary': 'sibling'}])
            self.assertEqual(len(result.observations), 2)
            if text is not None:
                self.assertEqual(result.request['input'][-1], self.request()['input'][-1])
        event = route_fixture.ledger.read_session_state(**self.fixture.coords)['latest_input_event_id']
        self.judge(first, disposition='keep', event=event)
        result = self.select()
        self.assertEqual(len(result.observations), 1)
        forwarded = json.loads(result.request['input'][2]['output'][0]['text'])
        self.assertEqual(forwarded, [dict(self.observation, use_reference=first['reference']), {}, {'necessary': 'sibling'}])
        self.assertEqual(self.rows[3]['payload'], original)
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            judgments = connection.execute('SELECT payload_json FROM core_response_use_judgments').fetchall()
        self.assertEqual(len(judgments), 3)

    def test_omit_changed_or_ambiguous_producer_is_preserved_and_relookup_is_independent(self):
        receipt = self.record(); self.output(receipt); self.judge(receipt, disposition='omit')
        original = self.request()
        for producer in (dict(self.observation, value='changed body', use_reference=receipt['reference']),
                         [dict(self.observation, use_reference=receipt['reference'])] * 2):
            request = copy.deepcopy(original)
            request['input'][2]['output'][0]['text'] = json.dumps(producer)
            result = self.select(request)
            self.assertEqual(result.request, request)
            self.assertEqual(result.observations, ())
        self.append(dict(type='custom_tool_call', call_id='relookup', name='exec', input='retrieve original again'))
        self.append(dict(type='custom_tool_call_output', call_id='relookup', output=json.dumps(self.observation)))
        result = self.select()
        self.assertEqual(result.request['input'][-2:], self.request()['input'][-2:])
        self.assertEqual(result.observations[0]['disposition'], 'omit')

    def test_stateful_codex_direct_postuse_selector_preserves_complete_request(self):
        receipt = self.record(); self.output(receipt); self.judge(receipt)
        for key in ('previous_response_id', 'conversation'):
            for value in (None, 'history', {}, False):
                request = dict(self.request(), **{key: value})
                result = self.select(request)
                self.assertEqual(result.request, request)
                self.assertEqual((result.state, result.reason), ('preserved', 'stateful-request-selection-unsupported'))
                self.assertEqual(result.observations, ())

    def test_cross_scope_keep_protects_only_overlap_while_independent_sibling_omits(self):
        import context_lifecycle as lifecycle
        for keeping_scope in ('manual', 'producer'):
            for destructive in ('replace', 'omit'):
                with self.subTest(keeping_scope=keeping_scope, destructive=destructive):
                    self.setUp()
                    receipt = self.record(); self.output(receipt)
                    self.rows[-1]['payload']['output'].append(dict(type='input_text', text='obsolete independent sibling'))
                    self.write_rows()
                    self.judge(receipt, disposition='keep' if keeping_scope == 'producer' else destructive)
                    choices = [dict(record=4, block_index=0, summary='Use judgment', next_need='Retrieve when needed',
                                    disposition='keep' if keeping_scope == 'manual' else destructive),
                               dict(record=4, block_index=1, summary='Sibling used', next_need='Changed sibling need', disposition='omit')]
                    lifecycle.record_disposition(self.source, self.event, choices)
                    result = lifecycle.select_request(self.request(), self.fixture.root, self.home)
                    self.assertEqual(result.state, 'selected')
                    self.assertEqual(result.reason, 'selected-with-preserved-context-conflict')
                    self.assertEqual(result.request['input'][2]['output'], self.request()['input'][2]['output'][:1])
                    self.assertEqual(len(result.observations), 1)
                    self.assertEqual(result.observations[0]['disposition'], 'omit')
                    only_conflict = [choices[0]]
                    lifecycle.record_disposition(self.source, self.event, only_conflict)
                    result = lifecycle.select_request(self.request(), self.fixture.root, self.home)
                    self.assertEqual(result.request, self.request())
                    self.assertEqual(result.observations, ())
                    self.assertEqual(result.reason, 'context-disposition-conflict')

    def test_kept_producer_in_nonzero_block_protects_the_whole_exchange(self):
        import context_lifecycle as lifecycle
        receipt = self.record(); output = self.output(receipt)
        output['output'].insert(0,dict(type='input_text',text='Independent prior output block'))
        self.write_rows(); self.judge(receipt,disposition='keep')
        lifecycle.record_disposition(self.source,self.event,[dict(record=4,block_index=0,kind='exchange',
            disposition='omit',summary='The exchange was consumed.',next_need='Retrieve its original if needed.')])
        request = self.request()
        result = lifecycle.select_request(request,self.fixture.root,self.home)
        self.assertEqual(result.request,request)
        self.assertEqual(result.observations,())
        self.assertEqual(result.reason,'context-disposition-conflict')

    def test_body_and_credentials_are_not_copied_into_lease_sqlite(self):
        observation = dict(self.observation, value='private source body auth-token-fixture')
        receipt = self.record(observation)
        self.assertEqual(set(receipt), {'reference', 'status'})
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            payloads = connection.execute('SELECT payload_json FROM core_response_context_uses').fetchall()
        self.assertNotIn(observation['value'], json.dumps(payloads))
        self.assertIn(use.fingerprint(observation), json.dumps(payloads))
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE core_response_context_uses SET payload_json='{}'")

    def test_read_binding_does_not_predict_future_scope_or_remaining_need(self):
        self.record()
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            payload = json.loads(connection.execute('SELECT payload_json FROM core_response_context_uses').fetchone()[0])
        self.assertNotIn('scope', payload)
        self.assertNotIn('next_need', payload)
        self.assertNotIn('summary', payload)
        self.assertNotIn('disposition', payload)

    def test_nested_json_sdk_wrappers_and_stdout_keep_unleased_siblings(self):
        for wrapping in ('object', 'nested-string', 'stdout-frame'):
            with self.subTest(wrapping=wrapping):
                # Each fixture has one uniquely bound result, independent of string framing.
                self.setUp()
                receipt = self.record()
                sibling = dict(source_lookup.lookup(self.source_file, pointer='/unleased'))
                def payload(producer):
                    value = dict(content=[dict(type='text', text=json.dumps(producer))], sibling=sibling)
                    return value if wrapping == 'object' else json.dumps(value)
                output = self.output(receipt, payload)
                if wrapping == 'stdout-frame':
                    output['output'][0]['text'] = 'stdout before\n' + output['output'][0]['text'] + '\nstdout after'
                    self.write_rows()
                self.judge(receipt); self.final(); self.next_human()
                result = self.select()
                self.assertEqual(result.state, 'selected')
                text = result.request['input'][2]['output'][0]['text']
                self.assertNotIn(self.observation['value'], text)
                self.assertIn(sibling['value'], text)
                if wrapping == 'stdout-frame':
                    self.assertTrue(text.startswith('stdout before\n'))
                    self.assertTrue(text.endswith('\nstdout after'))

    def test_unleased_output_blocks_calls_messages_and_signed_reasoning_stay_intact(self):
        receipt = self.record()
        output = self.output(receipt)
        output['output'].append(dict(type='input_text', text='unleased required complete source'))
        output['output'].append(dict(type='thinking', text='signed original', signature='fixture signature'))
        self.write_rows()
        signed = dict(type='reasoning', encrypted_content='signed opaque original')
        self.append(signed)
        self.judge(receipt); self.final(); self.next_human()
        result = self.select()
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.request['input'][2]['output'][1:], output['output'][1:])
        self.assertEqual(result.request['input'][3], signed)

    def test_multiple_independent_leases_in_one_call_replace_each_named_object(self):
        first = self.record()
        second_observation = source_lookup.lookup(self.source_file, pointer='/unleased')
        second = self.record(second_observation, purpose='Compare the other selected original')
        self.output(first, lambda producer: [producer, dict(second_observation, use_reference=second['reference']),
                                            {'plain': 'retain independent sibling'}])
        self.judge([first, second]); self.final(); self.next_human()
        result = self.select()
        self.assertEqual(result.state, 'selected')
        self.assertEqual(len(result.observations), 2)
        self.assertNotEqual(first['reference'], second['reference'])
        self.assertTrue(all(o['forwarded_item_digest'] == use.fingerprint(result.request['input'][2])
                            for o in result.observations))
        self.assertNotIn(self.observation['value'], json.dumps(result.request))
        self.assertNotIn(second_observation['value'], json.dumps(result.request))
        self.assertIn('retain independent sibling', json.dumps(result.request))

    def test_commentary_or_new_actual_human_without_judgment_never_infers_consumption(self):
        receipt = self.record(); self.output(receipt)
        self.final(phase='commentary')
        self.next_human()
        self.assertEqual(self.select().state, 'preserved')

    def test_legacy_final_channel_is_only_an_optional_answer_locator(self):
        receipt = self.record(); self.output(receipt)
        self.judge(receipt)
        final = self.final(channel='final'); final.pop('phase'); self.write_rows()
        self.assertEqual(self.select().state, 'selected')

    def test_empty_read_purpose_and_invalid_postuse_judgments_are_rejected(self):
        with self.assertRaises(ValueError):
            self.record(purpose='')
        receipt = self.record(); self.output(receipt)
        for key, value in (('summary', ''), ('next_need', ''), ('disposition', 'guess')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.judge(receipt, **{key: value})

    def test_stale_event_and_foreign_native_owner_cannot_record(self):
        self.fixture.intake('new actual input')
        with self.assertRaises(ValueError):
            self.record()
        self.source['CODEX_THREAD_ID'] = 'other'
        with self.assertRaises(ValueError):
            self.record(event=None)

    def test_missing_ambiguous_or_already_returned_calls_cannot_bind(self):
        second_call = dict(self.call, call_id='other')
        self.append(second_call)
        with self.assertRaisesRegex(ValueError, 'ambiguous|unique|pending'):
            self.record()
        receipt = self.record(call_id='lookup')
        self.assertEqual(receipt['status'], 'recorded')
        self.output(receipt)
        with self.assertRaises(ValueError):
            self.record(call_id='lookup')
        with self.assertRaises(ValueError):
            self.record(call_id='absent')

    def test_original_call_replay_and_changed_prefix_are_preserved(self):
        receipt = self.record(); self.output(receipt); self.judge(receipt); self.final(); self.next_human()
        request = self.request()
        request['input'].insert(2, copy.deepcopy(self.call))
        self.assertEqual(self.select(request).state, 'preserved')
        self.call['input'] = 'different native execution'
        self.write_rows()
        self.assertEqual(self.select().state, 'preserved')

    def test_stale_request_current_human_and_replayed_result_are_preserved(self):
        receipt = self.record(); output = self.output(receipt); self.judge(receipt); self.final()
        stale = self.request(); self.next_human()
        self.assertEqual(self.select(stale).state, 'preserved')
        request = self.request(); request['input'].insert(3, copy.deepcopy(output))
        self.assertEqual(self.select(request).state, 'preserved')

    def test_ambiguous_producer_or_wrong_fingerprint_cannot_replace_body(self):
        receipt = self.record()
        output = self.output(receipt, lambda producer: [producer, copy.deepcopy(producer)])
        with self.assertRaises(ValueError):
            self.judge(receipt)
        self.final(); self.next_human()
        self.assertEqual(self.select().state, 'preserved')
        output['output'][0]['text'] = json.dumps(dict(self.observation, value='changed selected body',
                                                     use_reference=receipt['reference']))
        self.write_rows()
        with self.assertRaises(ValueError):
            self.judge(receipt, event=self.fixture.intake())
        self.assertEqual(self.select().state, 'preserved')

    def test_unknown_signed_result_blocks_cannot_be_changed(self):
        receipt = self.record(); output = self.output(receipt)
        output['output'][0]['type'] = 'thinking'
        output['output'][0]['signature'] = 'native signed block'
        self.write_rows()
        with self.assertRaises(ValueError):
            self.judge(receipt)
        self.final(); self.next_human()
        self.assertEqual(self.select().state, 'preserved')

    def test_new_relookup_body_is_independent_of_prior_use_judgment(self):
        receipt = self.record(); self.output(receipt); self.judge(receipt); self.final(); self.next_human('read the source again')
        relookup = dict(type='function_call', call_id='relookup', name='read', arguments='{}')
        current_body = dict(type='function_call_output', call_id='relookup', output=json.dumps(self.observation))
        self.append(relookup); self.append(current_body)
        result = self.select()
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.request['input'][-2:], [relookup, current_body])

    def test_unavailable_storage_is_distinct_and_unexpected_bugs_propagate(self):
        self.assertIsNotNone(use)
        receipt = self.record(); self.output(receipt); self.judge(receipt); self.final(); self.next_human()
        with patch.object(use, '_native', side_effect=OSError('read unavailable')):
            self.assertEqual(self.select().state, 'unavailable')
        with patch.object(use, '_native', side_effect=KeyError('unexpected processing defect')):
            with self.assertRaises(KeyError):
                self.select()
        with patch.object(use.core_api, '_coordinates', side_effect=sqlite3.OperationalError('database unavailable')):
            self.assertEqual(self.select().state, 'unavailable')

    def test_empty_use_storage_matches_existing_no_selection_contract(self):
        result = self.select()
        self.assertEqual((result.state, result.reason), ('preserved', 'selection-absent'))
        self.assertIs(result.request, result.request)

    def test_duplicate_reference_with_one_changed_object_is_ambiguous(self):
        receipt = self.record()
        self.output(receipt, lambda producer: [producer, dict(producer, value='different object under same reference')])
        with self.assertRaises(ValueError):
            self.judge(receipt)
        self.final(); self.next_human()
        result = self.select()
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.request, self.request())

    def test_relocated_old_result_after_current_human_is_preserved(self):
        receipt = self.record(); self.output(receipt); self.judge(receipt); self.final(); self.next_human()
        request = self.request()
        request['input'] = [request['input'][0], request['input'][3], request['input'][4],
                            request['input'][1], request['input'][2]]
        self.assertEqual(self.select(request).state, 'preserved')

    def test_function_scalar_stdout_replaces_only_json_frame(self):
        self.call = dict(type='function_call', call_id='lookup', name='shell', arguments='{}')
        self.rows[2]['payload'] = self.call; self.write_rows()
        receipt = self.record()
        text = 'command metadata\n' + json.dumps(dict(self.observation, use_reference=receipt['reference']))
        output = dict(type='function_call_output', call_id='lookup', output=text + '\nindependent stdout')
        self.append(output); self.judge(receipt); self.final(); self.next_human()
        result = self.select()
        self.assertEqual(result.state, 'selected')
        self.assertTrue(result.request['input'][2]['output'].startswith('command metadata\n'))
        self.assertTrue(result.request['input'][2]['output'].endswith('\nindependent stdout'))
        self.assertEqual(result.request['input'][1], self.call)

    def test_nonhuman_wire_context_cannot_replace_exact_current_human(self):
        receipt = self.record(); self.output(receipt); self.judge(receipt); self.final()
        context = self.human('environment context')
        context['internal_chat_message_metadata_passthrough'] = {'content_item_kinds': ['environments.environment_context']}
        self.append(context)
        self.assertEqual(self.select().state, 'preserved')

    def test_existing_sibling_disposition_and_explicit_response_use_compose(self):
        import context_lifecycle as lifecycle
        receipt = self.record(); output = self.output(receipt)
        output['output'].append(dict(type='input_text', text='separately consumed sibling source body'))
        self.write_rows()
        lifecycle.record_disposition(self.source, self.event, [dict(record=4, block_index=1,
            summary='Sibling lookup was used for its separate comparison', next_need='Reopen the sibling if needed')])
        self.judge(receipt); self.final(); self.next_human()
        result = lifecycle.select_request(self.request(), self.fixture.root, self.home)
        self.assertEqual(result.state, 'selected')
        self.assertEqual(len(result.observations), 2)
        self.assertNotIn(self.observation['value'], json.dumps(result.request))
        self.assertNotIn('separately consumed sibling source body', json.dumps(result.request))
        self.assertIn('Sibling lookup was used', json.dumps(result.request))
        self.assertEqual(result.request['input'][1], self.call)

    def test_declared_read_purpose_and_final_without_postuse_judgment_preserve_body(self):
        receipt = self.record(); self.output(receipt); self.final(); self.next_human()
        result = self.select()
        self.assertEqual(result.state, 'preserved')
        self.assertIn(self.observation['value'], json.dumps(result.request))
        self.assertEqual(result.request, self.request())

    def test_postuse_judgment_before_actual_result_is_rejected(self):
        receipt = self.record()
        with self.assertRaisesRegex(ValueError, 'result|producer|read'):
            self.judge(receipt)

    def test_latest_keep_supersedes_replace_without_rewriting_original_judgment(self):
        receipt = self.record(); self.output(receipt); self.judge(receipt)
        self.judge(receipt, disposition='keep', summary='The original remains necessary for the pending comparison',
                   next_need='Keep this body for the next decision')
        self.final(); self.next_human()
        result = self.select()
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.request, self.request())
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM core_response_use_judgments').fetchone()[0], 2)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE core_response_use_judgments SET payload_json='{}'")

    def test_explicit_summary_and_unfinished_need_survive_omitted_native_answer(self):
        receipt = self.record(); self.output(receipt)
        summary = 'The comparison is inconclusive; source quality remains unresolved'
        next_need = 'Verify the missing independent evidence before deciding'
        self.judge(receipt, summary=summary, next_need=next_need); self.final(); self.next_human()
        request = self.request(); request['input'].pop(3)
        result = self.select(request)
        self.assertEqual(result.state, 'selected')
        self.assertIn(summary, json.dumps(result.request))
        self.assertIn(next_need, json.dumps(result.request))
        self.assertNotIn('The selected original supports this conclusion.', json.dumps(result.request))

    def test_postuse_judgment_with_stale_current_input_is_rejected(self):
        receipt = self.record(); self.output(receipt); self.final(); self.next_human()
        with self.assertRaises(ValueError):
            self.judge(receipt)
        result = self.select()
        self.assertEqual(result.state, 'preserved')

    def test_judgment_and_parent_operation_share_one_transaction(self):
        receipt = self.record(); self.output(receipt)
        core, root, _ = use.core_api._coordinates(self.source)
        with self.assertRaisesRegex(RuntimeError, 'parent rollback'):
            with core._transaction(root) as connection:
                self.judge(receipt, transaction=connection)
                raise RuntimeError('parent rollback')
        self.final(); self.next_human()
        self.assertEqual(self.select().state, 'preserved')

    def test_postuse_none_event_resolves_current_canonical_in_same_transaction(self):
        receipt = self.record(); self.output(receipt)
        core, root, _ = use.core_api._coordinates(self.source)
        with core._transaction(root) as connection:
            recorded = use.record_use_judgments(self.source, None, [dict(use_reference=receipt['reference'],
                disposition='replace', summary='Read result compared; retain this answer conclusion',
                next_need='Check the unresolved independent source')], transaction=connection)
        self.assertEqual(recorded['judgment_count'], 1)
        selected = self.select()
        self.assertEqual(selected.state, 'selected')
        self.assertIn('retain this answer conclusion', json.dumps(selected.request))

    def test_same_response_keep_reopens_original_after_replace(self):
        receipt = self.record(); self.output(receipt); self.judge(receipt)
        self.assertEqual(self.select().state, 'selected')
        self.judge(receipt, disposition='keep', summary='Source wording is needed for the next answer comparison',
                   next_need='Keep the selected original body')
        retained = self.select()
        self.assertEqual(retained.state, 'preserved')
        self.assertIn(self.observation['value'], json.dumps(retained.request))
        self.assertEqual(retained.request, self.request())

    def test_current_result_cannot_be_relocated_before_its_current_human(self):
        receipt = self.record(); self.output(receipt); self.judge(receipt)
        request = self.request()
        request['input'] = [request['input'][1], request['input'][2], request['input'][0]]
        self.assertEqual(self.select(request).state, 'preserved')

    def test_prefinal_capsule_directly_locates_the_untouched_native_producer(self):
        receipt = self.record(); original_output = self.output(receipt); self.judge(receipt)
        before = self.log.read_bytes()
        result = self.select()
        self.assertEqual(result.state, 'selected')
        capsule = json.loads(result.request['input'][2]['output'][0]['text'])
        self.assertEqual(capsule['original'], str(self.log.resolve()) + '#record=4')
        self.assertEqual(capsule['original_pointer'], '/payload/output')
        self.assertNotIn('original_item_digest', capsule)
        self.assertEqual(result.observations[0]['original_item_digest'], use.fingerprint(original_output))
        self.assertNotIn('native_answer', capsule)
        original = source_lookup.lookup(self.log, record=4, pointer=capsule['original_pointer'])
        self.assertEqual(json.loads(original['value'][0]['text'])['value'], self.observation['value'])
        self.assertEqual(self.log.read_bytes(), before)

    def test_model_projection_contains_only_use_conclusion_need_and_one_retrieval_location(self):
        receipt = self.record(); original_output = self.output(receipt)
        summary = '관찰된 근거만으로는 결론이 확정되지 않았습니다.'
        next_need = '누락된 근거를 확인할 때 원문을 다시 읽습니다.'
        self.judge(receipt, summary=summary, next_need=next_need); self.final(); self.next_human()
        original_bytes = self.log.read_bytes()
        result = self.select()
        self.assertEqual(result.state, 'selected')
        capsule = json.loads(result.request['input'][2]['output'][0]['text'])
        measured = _measure_projection('codex-authenticated-replace-with-native-answer', result,
                                      capsule, self.observation, 'codex', 2)
        self.assertEqual(set(capsule), {'use_reference', 'purpose', 'summary', 'next_need', 'original', 'original_pointer'})
        self.assertEqual((capsule['summary'], capsule['next_need']), (summary, next_need))
        self.assertLess(measured['after_capsule_bytes'], measured['before_capsule_bytes'])
        self.assertLess(measured['after_projected_request_bytes'], measured['before_projected_request_bytes'])
        audit = result.observations[0]
        self.assertEqual(audit['original_item_digest'], use.fingerprint(original_output))
        self.assertEqual(audit['observation_digest'], use.fingerprint(self.observation))
        self.assertIn('judgment_reference', audit)
        self.assertIsInstance(audit['native_answer'], dict)
        self.assertEqual(audit['source']['pointer'], '/needed')
        self.assertEqual(self.log.read_bytes(), original_bytes)


class ClaudeResponseContextUseTests(unittest.TestCase):
    """Unsigned substitutions retain SDK envelopes; signed prefixes have separate cases."""
    def setUp(self, session='current'):
        self.fixture = route_fixture.ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.coords['platform'] = 'claude'
        self.fixture.coords['session_id'] = session
        self.session = session
        self.event = self.fixture.intake('compare original')
        self.home = Path(self.fixture.tmp.name) / 'claude'
        self.log = self.home / 'projects' / 'native-project' / (session + '.jsonl')
        self.log.parent.mkdir(parents=True)
        self.source_file = Path(self.fixture.tmp.name) / 'PLAN.json'
        self.source_file.write_text(json.dumps({'needed': 'Claude selected source body', 'other': 'required sibling'}))
        self.observation = source_lookup.lookup(self.source_file, pointer='/needed')
        self.source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root), GHOST_ALICE_PLATFORM='claude',
                           GHOST_ALICE_SESSION_ID=session, GHOST_ALICE_CLAUDE_DIR=str(self.home))
        self.thinking = dict(type='thinking', thinking='signed closure reasoning', signature='signed')
        self.call = dict(type='tool_use', id='lookup', name='Bash', input={'command': 'read original'})
        self.rows = [self.row('user', 'compare original', promptSource='sdk', turnOrigin='sdk'),
                     # Keeping signature bytes while replacing their prefix
                     # contradicted prefix binding; positive fixtures are unsigned.
                     self.row('assistant', [dict(type='text', text='unsigned pre-call context')],
                              stop_reason='tool_use'),
                     self.row('assistant', [self.call], stop_reason='tool_use')]
        self.write_rows()

    def row(self, role, content, stop_reason=None, **extra):
        message = dict(role=role, content=content)
        if stop_reason:
            message['stop_reason'] = stop_reason
        return dict(type=role, sessionId=self.session, isSidechain=False, message=message, **extra)

    def write_rows(self):
        self.log.write_text(''.join(json.dumps(row) + '\n' for row in self.rows))

    def record(self):
        self.assertIsNotNone(use)
        return use.record_use(self.source, self.event, self.observation,
                              purpose='Compare the selected original for this answer')

    def close(self, receipt, stop_reason='end_turn', judgment=True, signed=False, wrapper=False):
        producer = dict(self.observation, use_reference=receipt['reference'])
        if wrapper:
            producer = dict(content=[dict(type='text', text=json.dumps(producer))], sibling='needed wrapper detail')
        self.result_block = dict(type='tool_result', tool_use_id='lookup', content=[
            dict(type='text', text='stdout\n' + json.dumps(producer)),
            dict(type='text', text='required sibling')])
        self.rows.append(self.row('user', [self.result_block]))
        self.write_rows()
        if judgment:
            self.assertTrue(callable(getattr(use, 'record_use_judgments', None)))
            use.record_use_judgments(self.source, self.event, [dict(use_reference=receipt['reference'], disposition='replace',
                summary='The selected source was compared; its uncertainty remains', next_need='Check unresolved source quality')])
        closure = self.thinking if signed else dict(type='text', text='unsigned closure context')
        self.rows.extend([self.row('assistant', [copy.deepcopy(closure)],
                                   stop_reason='end_turn'),
                          self.row('assistant', [dict(type='text', text='Explicit conclusion from this original')],
                                   stop_reason=stop_reason)])
        self.write_rows()

    def next_human(self, text='another human decision'):
        self.fixture.intake(text)
        self.rows.append(self.row('user', text, promptSource='sdk', turnOrigin='sdk'))
        self.write_rows()

    def request(self):
        return dict(messages=[dict(role=r['message']['role'], content=copy.deepcopy(r['message']['content']))
                              for r in self.rows], model='fixture', thinking={'type': 'disabled'})

    def select(self, request=None):
        return use.select_uses(self.request() if request is None else request, self.fixture.root, self.home,
                               platform='claude', session_id=self.session)

    def test_server_whole_result_omit_cannot_clear_active_kept_producer(self):
        import context_lifecycle as lifecycle
        receipt = self.record(); self.close(receipt, judgment=False, signed=True)
        use.record_use_judgments(self.source, self.event, [dict(use_reference=receipt['reference'],
            disposition='keep', summary='Original wording remains necessary', next_need='Compare original wording')])
        lifecycle.record_disposition(self.source, self.event, [dict(record=4, block_index=0, kind='output',
            disposition='omit', summary='Earlier whole result was used', next_need='Retrieve if needed')])
        self.next_human()
        request = self.request(); del request['thinking']
        result = lifecycle.select_request(request, self.fixture.root, self.home, platform='claude',
            session_id=self.session, native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.reason, 'context-disposition-conflict')
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())

    def test_direct_caller_replay_retains_kept_producer_before_server_whole_result_omit(self):
        import context_lifecycle as lifecycle
        self.call['caller'] = {'type': 'direct'}
        self.write_rows()
        receipt = self.record(); self.close(receipt, judgment=False, signed=True)
        use.record_use_judgments(self.source, self.event, [dict(use_reference=receipt['reference'],
            disposition='keep', summary='Original wording remains necessary', next_need='Compare original wording')])
        lifecycle.record_disposition(self.source, self.event, [dict(record=4, block_index=0, kind='output',
            disposition='omit', summary='Earlier whole result was used', next_need='Retrieve if needed')])
        self.next_human()
        native_before, source_before = self.log.read_bytes(), self.source_file.read_bytes()
        request = self.request(); del request['thinking']
        request['messages'][2]['content'][0].pop('caller')
        result = lifecycle.select_request(request, self.fixture.root, self.home, platform='claude',
            session_id=self.session, native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.reason, 'context-disposition-conflict')
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())
        self.assertEqual(self.log.read_bytes(), native_before)
        self.assertEqual(self.source_file.read_bytes(), source_before)

    def test_direct_caller_replay_applies_only_explicit_unsigned_source_use_disposition(self):
        self.call['caller'] = {'type': 'direct'}
        self.write_rows()
        receipt = self.record(); self.close(receipt, judgment=False, wrapper=True)
        native_before, source_before = self.log.read_bytes(), self.source_file.read_bytes()
        for disposition in ('replace', 'omit', 'keep'):
            with self.subTest(disposition=disposition):
                use.record_use_judgments(self.source, self.event, [dict(use_reference=receipt['reference'],
                    disposition=disposition, summary='Compared source; uncertainty remains', next_need='Retrieve original wording')])
                request = self.request()
                request['messages'][2]['content'][0].pop('caller')
                result = self.select(request)
                self.assertEqual(result.request['messages'][:3], request['messages'][:3])
                self.assertEqual(result.request['messages'][4:], request['messages'][4:])
                self.assertEqual(result.request['messages'][3]['content'][0]['content'][1],
                                 self.result_block['content'][1])
                if disposition == 'keep':
                    self.assertEqual(result.request, request)
                    self.assertEqual(result.observations, ())
                    continue
                self.assertEqual(result.state, 'selected')
                self.assertEqual(len(result.observations), 1)
                self.assertEqual(result.observations[0]['original_call_digest'], use.fingerprint(self.call))
                self.assertEqual(result.observations[0]['disposition'], disposition)
                text = result.request['messages'][3]['content'][0]['content'][0]['text']
                producer = json.loads(json.loads(text.removeprefix('stdout\n'))['content'][0]['text'])
                self.assertNotIn(self.observation['value'], text)
                if disposition == 'omit':
                    self.assertEqual(producer, {})
                    self.assertNotIn(receipt['reference'], text)
                else:
                    self.assertEqual(producer['summary'], 'Compared source; uncertainty remains')
                    self.assertEqual(producer['next_need'], 'Retrieve original wording')
                    self.assertEqual(producer['original'], str(self.log.resolve()) + '#record=4')
        self.assertEqual(self.log.read_bytes(), native_before)
        self.assertEqual(self.source_file.read_bytes(), source_before)
        self.assertEqual(source_lookup.lookup(self.source_file, pointer='/needed')['value'], self.observation['value'])

    def test_direct_caller_replay_does_not_relax_semantic_call_or_pair_binding(self):
        self.call['caller'] = {'type': 'direct'}
        self.call['input']['caller'] = {'type': 'direct', 'scope': 'semantic argument'}
        self.call['input']['cache_control'] = {'scope': 'semantic argument'}
        self.write_rows()
        receipt = self.record(); self.close(receipt)
        request = self.request()
        request['messages'][2]['content'][0].pop('caller')
        variants = {}
        for label, caller in (('non-direct', {'type': 'code_execution_20260120', 'tool_id': 'exec'}),
                              ('unknown-direct', {'type': 'direct', 'unknown': True})):
            candidate = copy.deepcopy(request)
            candidate['messages'][2]['content'][0]['caller'] = caller
            variants[label] = candidate
        for label, key, value in (('input-caller', 'caller', {'type': 'direct'}),
                                  ('input-cache', 'cache_control', {'scope': 'changed'}),
                                  ('input-command', 'command', 'different original')):
            candidate = copy.deepcopy(request)
            candidate['messages'][2]['content'][0]['input'][key] = value
            variants[label] = candidate
        duplicate = copy.deepcopy(request)
        duplicate['messages'][2]['content'].append(copy.deepcopy(duplicate['messages'][2]['content'][0]))
        variants['duplicate-call'] = duplicate
        reordered = copy.deepcopy(request)
        reordered['messages'][2], reordered['messages'][3] = reordered['messages'][3], reordered['messages'][2]
        variants['reordered-pair'] = reordered
        for label, candidate in variants.items():
            with self.subTest(shape=label):
                result = self.select(candidate)
                self.assertEqual(result.state, 'preserved')
                self.assertEqual(result.request, candidate)
                self.assertEqual(result.observations, ())
        # The unchanged semantic tool arguments are valid under the direct replay default.
        self.assertEqual(self.select(request).state, 'selected')

    def test_server_keep_conflict_still_allows_independent_other_tool_omission(self):
        import context_lifecycle as lifecycle
        receipt = self.record(); self.close(receipt, judgment=False, signed=True)
        use.record_use_judgments(self.source, self.event, [dict(use_reference=receipt['reference'],
            disposition='keep', summary='Original wording remains necessary', next_need='Compare original wording')])
        self.rows.extend([self.row('assistant', [dict(type='tool_use', id='other', name='Read', input={})]),
                          self.row('user', [dict(type='tool_result', tool_use_id='other', content='used independent body')])])
        self.write_rows()
        lifecycle.record_disposition(self.source, self.event, [dict(record=record, block_index=0, kind='output',
            disposition='omit', summary='Whole result was used', next_need='Retrieve if needed') for record in (4,8)])
        self.next_human()
        request = self.request(); del request['thinking']
        result = lifecycle.select_request(request, self.fixture.root, self.home, platform='claude',
            session_id=self.session, native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.reason, 'selected-with-preserved-context-conflict')
        self.assertEqual(result.request['messages'], request['messages'])
        self.assertEqual(result.request['context_management']['edits'][-1]['exclude_tools'], ['Bash'])
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0]['tool_use_id'], 'other')

    def test_server_capability_does_not_expand_partial_producer_omit_to_whole_result(self):
        import context_lifecycle as lifecycle
        receipt = self.record(); self.close(receipt, judgment=False, signed=True)
        use.record_use_judgments(self.source, self.event, [dict(use_reference=receipt['reference'],
            disposition='omit', summary='Producer is no longer needed', next_need='Retrieve if needed')])
        self.next_human()
        request = self.request(); del request['thinking']
        result = lifecycle.select_request(request, self.fixture.root, self.home, platform='claude',
            session_id=self.session, native_betas=('context-management-2025-06-27',))
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())

    def test_omit_nested_unsigned_producer_and_keep_restore_preserve_prefix_guards(self):
        receipt = self.record(); self.close(receipt, judgment=False, wrapper=True)
        original = self.log.read_bytes()
        judgment = dict(use_reference=receipt['reference'], disposition='omit', summary='No current need',
                        next_need='Retrieve on changed purpose')
        use.record_use_judgments(self.source, self.event, [judgment])
        result = self.select()
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.observations[0]['disposition'], 'omit')
        expected = dict(content=[dict(type='text', text='{}')], sibling='needed wrapper detail')
        self.assertEqual(result.request['messages'][3]['content'][0]['content'][0]['text'],
                         'stdout\n' + json.dumps(expected))
        self.assertEqual(result.request['messages'][3]['content'][0]['content'][1], self.result_block['content'][1])
        self.assertNotIn(receipt['reference'], json.dumps(result.request))
        for thinking in (None, {'type':'enabled'}, {'type':'adaptive'}, {'type':'disabled'}):
            request = self.request()
            if thinking is None:
                request.pop('thinking')
            else:
                request['thinking'] = thinking
            if thinking == {'type':'disabled'}:
                request['messages'][4]['content'] = [self.thinking]
            result = self.select(request)
            self.assertEqual(result.request, request)
            self.assertEqual(result.reason, 'signed-prefix-selection-unsupported')
            self.assertEqual(result.observations, ())
        use.record_use_judgments(self.source, self.event, [dict(judgment, disposition='keep')])
        self.assertEqual(self.select().request, self.request())
        self.assertEqual(self.log.read_bytes(), original)

    def test_explicit_replace_applies_before_or_after_optional_answer_boundaries(self):
        for reason in ('end_turn', None):
            with self.subTest(reason=reason):
                self.setUp()
                receipt = self.record(); self.close(receipt, reason)
                self.assertEqual(self.select().state, 'selected')
                for text in ('another human decision', 'second next human'):
                    self.next_human(text)
                    before = self.log.read_bytes()
                    result = self.select()
                    self.assertEqual(result.state, 'selected')
                    self.assertNotIn(self.observation['value'], json.dumps(result.request))
                    self.assertIn('required sibling', json.dumps(result.request))
                    self.assertEqual(result.request['messages'][1:3], self.request()['messages'][1:3])
                    self.assertEqual(result.request['messages'][4:6], self.request()['messages'][4:6])
                    self.assertEqual(self.log.read_bytes(), before)

    def test_explicit_replace_applies_while_answer_is_incomplete_or_tool_use_continues(self):
        receipt = self.record(); self.close(receipt, 'max_tokens'); self.next_human()
        self.assertEqual(self.select().state, 'selected')
        self.rows[-2]['message']['content'].append(dict(type='tool_use', id='next', name='Bash', input={}))
        self.rows[-2]['message'].pop('stop_reason'); self.write_rows()
        selected = self.select()
        self.assertEqual(selected.state, 'selected')
        self.assertEqual(selected.request['messages'][-2], self.request()['messages'][-2])

    def test_unsigned_cache_control_is_wire_metadata_and_other_blocks_are_unchanged(self):
        receipt = self.record(); self.close(receipt); self.next_human()
        request = self.request()
        request['messages'][2]['content'][0]['cache_control'] = {'type': 'ephemeral'}
        request['messages'][3]['content'][0]['cache_control'] = {'type': 'ephemeral'}
        result = self.select(request)
        self.assertEqual(result.state, 'selected')
        self.assertEqual(result.request['messages'][2], request['messages'][2])
        self.assertEqual(result.request['messages'][3]['content'][0]['cache_control'], {'type': 'ephemeral'})

    def test_task_notification_is_not_next_human_and_current_hash_is_not_relaxed(self):
        receipt = self.record(); self.close(receipt)
        self.rows.append(self.row('user', '<task-notification>peer result</task-notification>',
                                  promptSource='system', turnOrigin='task_notification',
                                  origin={'kind': 'task-notification', 'producer': 'session-task'}))
        self.write_rows()
        self.assertEqual(self.select().state, 'selected')
        self.next_human()
        request = self.request()
        request['messages'][-1]['content'] += '\nmerged hook context'
        self.assertEqual(self.select(request).state, 'preserved')

    def test_foreign_owner_ambiguous_wire_result_and_changed_call_are_preserved(self):
        receipt = self.record(); self.close(receipt); self.next_human()
        request = self.request(); request['messages'][3]['content'].append(copy.deepcopy(self.result_block))
        self.assertEqual(self.select(request).state, 'preserved')
        request = self.request(); request['messages'][2]['content'][0]['input']['command'] = 'other call'
        self.assertEqual(self.select(request).state, 'preserved')
        self.rows[-1]['sessionId'] = 'other'; self.write_rows()
        self.assertEqual(self.select().state, 'preserved')

    def test_no_next_tool_and_no_review_never_imply_consumption(self):
        receipt = self.record(); self.close(receipt, judgment=False); self.next_human()
        self.assertEqual(self.select().state, 'preserved')
        self.assertIn(self.observation['value'], json.dumps(self.select().request))

    def test_same_human_prefinal_replace_and_none_event_are_functional_input_changes(self):
        receipt = self.record()
        producer = dict(self.observation, use_reference=receipt['reference'])
        output = dict(type='tool_result', tool_use_id='lookup', content=[
            dict(type='text', text=json.dumps(producer)), dict(type='text', text='independent required sibling')])
        self.rows.append(self.row('user', [output])); self.write_rows()
        self.assertEqual(self.select().state, 'preserved')
        use.record_use_judgments(self.source, None, [dict(use_reference=receipt['reference'], disposition='replace',
            summary='Read source compared; uncertainty remains in the intended answer', next_need='Retain the independent required sibling')])
        selected = self.select()
        self.assertEqual(selected.state, 'selected')
        self.assertNotIn(self.observation['value'], json.dumps(selected.request))
        self.assertIn('uncertainty remains in the intended answer', json.dumps(selected.request))
        self.assertIn('independent required sibling', json.dumps(selected.request))
        self.assertEqual(selected.request['messages'][:3], self.request()['messages'][:3])

    def test_explicit_stale_event_is_rejected_even_when_none_can_resolve_current(self):
        receipt = self.record(); self.close(receipt, judgment=False); self.next_human()
        judgment = [dict(use_reference=receipt['reference'], disposition='replace', summary='Result was compared',
                         next_need='Use the conclusion for the next decision')]
        with self.assertRaises(ValueError):
            use.record_use_judgments(self.source, self.event, judgment)
        use.record_use_judgments(self.source, None, judgment)
        self.assertEqual(self.select().state, 'selected')

    def test_claude_model_projection_keeps_one_original_without_authentication_proofs(self):
        receipt = self.record()
        producer = dict(self.observation, use_reference=receipt['reference'])
        block = dict(type='tool_result', tool_use_id='lookup', content=[dict(type='text', text=json.dumps(producer)),
                     dict(type='text', text='independent sibling remains necessary')])
        self.rows.append(self.row('user', [block])); self.write_rows()
        summary = 'The source remains uncertain; the next conclusion must retain that uncertainty'
        next_need = 'Read the original when the unresolved wording is compared'
        use.record_use_judgments(self.source, None, [dict(use_reference=receipt['reference'], disposition='replace',
            summary=summary, next_need=next_need)])
        self.rows.append(self.row('assistant', [dict(type='text', text='Original native answer remains intact')],
                                  stop_reason='end_turn'))
        self.write_rows(); original_bytes = self.log.read_bytes()
        result = self.select()
        self.assertEqual(result.state, 'selected')
        capsule = json.loads(result.request['messages'][3]['content'][0]['content'][0]['text'])
        measured = _measure_projection('claude-authenticated-replace-with-native-answer', result,
                                      capsule, self.observation, 'claude', 3)
        self.assertEqual(set(capsule), {'use_reference', 'purpose', 'summary', 'next_need', 'original', 'original_pointer'})
        self.assertEqual((capsule['summary'], capsule['next_need']), (summary, next_need))
        self.assertLess(measured['after_capsule_bytes'], measured['before_capsule_bytes'])
        self.assertLess(measured['after_projected_request_bytes'], measured['before_projected_request_bytes'])
        audit = result.observations[0]
        self.assertEqual(audit['observation_digest'], use.fingerprint(self.observation))
        self.assertIsInstance(audit['native_answer'], dict)
        self.assertEqual(result.request['messages'][:3], self.request()['messages'][:3])
        self.assertEqual(result.request['messages'][3]['content'][0]['content'][1], block['content'][1])
        self.assertEqual(result.request['messages'][4], self.request()['messages'][4])
        original = source_lookup.lookup(self.log, record=4, pointer=capsule['original_pointer'])
        self.assertEqual(json.loads(original['value'][0]['text'])['value'], self.observation['value'])
        self.assertEqual(self.log.read_bytes(), original_bytes)

    def ledger_rows(self):
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as connection:
            return list(connection.iterdump())

    def assert_signed_preserved(self, request, select=None):
        before_native, before_ledger = self.log.read_bytes(), self.ledger_rows()
        before_source = self.source_file.read_bytes()
        before_request = copy.deepcopy(request)
        serialized = json.dumps(request, ensure_ascii=False)
        result = self.select(request) if select is None else select(request)
        self.assertEqual((result.state, result.reason), ('preserved', 'signed-prefix-selection-unsupported'))
        self.assertEqual(result.request, before_request)
        self.assertEqual(request, before_request)
        self.assertEqual(json.dumps(result.request, ensure_ascii=False), serialized)
        self.assertEqual(result.observations, ())
        self.assertEqual(self.log.read_bytes(), before_native)
        self.assertEqual(self.ledger_rows(), before_ledger)
        self.assertEqual(self.source_file.read_bytes(), before_source)
        return result

    def test_nested_producer_wrapper_before_later_thinking_preserves_whole_request_and_originals(self):
        receipt = self.record(); self.close(receipt, signed=True, wrapper=True); self.next_human()
        request = self.request()
        self.assert_signed_preserved(request)

    def test_thinking_capable_initial_request_preserves_post_use_selection_without_signed_history(self):
        receipt = self.record(); self.close(receipt)
        for kind in ('enabled', 'adaptive', 'between_tools'):
            with self.subTest(kind=kind):
                self.assert_signed_preserved(dict(self.request(), thinking={'type': kind}))

    def test_missing_thinking_on_initial_request_preserves_post_use_selection(self):
        receipt = self.record(); self.close(receipt)
        request = self.request()
        del request['thinking']
        self.assert_signed_preserved(request)

    def test_signed_unsupported_post_use_reason_survives_empty_or_keep_manual_disposition(self):
        import context_lifecycle as lifecycle
        receipt = self.record(); self.close(receipt, signed=True)
        select = lambda request: lifecycle.select_request(request, self.fixture.root, self.home,
            platform='claude', session_id=self.session)
        for choices in ([], [dict(kind='output', record=4, block_index=0, disposition='keep',
                                 summary='Original needed', next_need='Compare original details')]):
            with self.subTest(choices=choices):
                lifecycle.record_disposition(self.source, self.event, choices)
                self.assert_signed_preserved(self.request(), select)

    def test_signed_prefix_without_post_use_judgment_retains_existing_preserved_reason(self):
        receipt = self.record(); self.close(receipt, judgment=False, signed=True)
        request = self.request()
        result = self.select(request)
        self.assertEqual((result.state, result.reason), ('preserved', 'use-judgment-absent'))
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())

    def test_signed_prefix_with_no_eligible_wire_result_retains_existing_preserved_reason(self):
        receipt = self.record(); self.close(receipt, signed=True)
        request = self.request()
        request['messages'][3]['content'][0]['content'][0]['text'] = 'producer absent from wire'
        result = self.select(request)
        self.assertEqual((result.state, result.reason), ('preserved', 'use-active-or-unverified'))
        self.assertEqual(result.request, request)
        self.assertEqual(result.observations, ())

    def test_signed_selection_gateway_forwards_complete_raw_bytes_without_selected_observations(self):
        import gzip
        import hashlib
        import context_lifecycle as lifecycle
        from test_context_gateway import ClaudeGatewayTransportTests
        transport = ClaudeGatewayTransportTests()
        transport.setUp()
        self.addCleanup(transport.doCleanups)
        self.setUp(session=transport.session)
        receipt = self.record(); self.close(receipt, signed=True, wrapper=True)
        before_native, before_ledger = self.log.read_bytes(), self.ledger_rows()
        transport.start(selectors={'claude': lambda request, session: lifecycle.select_request(
            request, self.fixture.root, self.home, platform='claude', session_id=session)})
        body = ('  ' + json.dumps(self.request(), ensure_ascii=False, indent=2) + '\n').encode()
        for encoding, wire in ((None, body), ('gzip', gzip.compress(body))):
            with self.subTest(encoding=encoding), transport.request(body=wire, session_id=self.session,
                                                                    encoding=encoding) as reply:
                self.assertEqual(reply.read(), transport.answer)
            self.assertEqual(transport.seen[-1][1], wire)
            rows = [json.loads(line) for line in transport.log.read_text().splitlines()]
            observed = [row for row in rows if row['event'] == 'request-selection'][-1]
            self.assertEqual((observed['state'], observed['reason']),
                             ('preserved', 'signed-prefix-selection-unsupported'))
            self.assertEqual(observed['selected_blocks'], [])
            self.assertEqual(observed['original_wire_sha256'], hashlib.sha256(wire).hexdigest())
            self.assertEqual(observed['forwarded_wire_sha256'], hashlib.sha256(wire).hexdigest())
        self.assertEqual(self.log.read_bytes(), before_native)
        self.assertEqual(self.ledger_rows(), before_ledger)


if __name__ == '__main__':
    unittest.main()
