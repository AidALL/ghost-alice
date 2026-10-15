"""Claude native shapes and Anthropic wire contracts, using synthetic content."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import claude_context_adapter as adapter
except ModuleNotFoundError as error:
    if error.name != 'claude_context_adapter':
        raise
    adapter = None


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return 'sha256:' + hashlib.sha256(encoded).hexdigest()


def message(role, *blocks):
    return dict(type='message', role=role, content=list(blocks))


def text(value):
    return dict(type='text', text=value)


class ClaudeContextAdapterTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(adapter, 'The required pure Claude native-to-wire adapter is absent')
        self.human = message('user', text('implement now\n'))
        self.thinking = dict(type='thinking', thinking='signed reasoning', signature='synthetic-signature')
        self.call = dict(type='tool_use', id='consumed', name='Read',
                         input={'file_path': '/fixture/source'}, caller={'type': 'direct'})
        self.result = dict(type='tool_result', tool_use_id='consumed', content='consumed body', is_error=False)
        self.answer = message('assistant', text('used earlier answer'))
        # Substitution fixtures are unsigned: retaining signature bytes while
        # editing their prefix did not establish Claude's binding contract.
        self.unsigned = text('unsigned pre-call context')
        self.originals = {1: self.human, 2: message('assistant', self.unsigned, self.call),
                          3: message('user', self.result), 4: self.answer}
        self.necessary = text('necessary original source\n' * 2000)
        self.request = dict(model='fixture', thinking={'type': 'disabled'}, metadata={'user_id': 'opaque-native-owner'},
                            system=[text('required system source')], messages=[
                                dict(role='user', content=copy.deepcopy(self.human['content'])),
                                dict(role='assistant', content=copy.deepcopy(self.originals[2]['content'])),
                                dict(role='user', content=[copy.deepcopy(self.result), self.necessary]),
                                dict(role='assistant', content=copy.deepcopy(self.answer['content']))])

    def choice(self, record=3, block_index=0, kind='output'):
        item = self.originals[record]
        block = item['content'][block_index]
        value = block['content'] if kind == 'output' else block
        return dict(record=record, block_index=block_index, kind=kind,
                    summary='The selected source was consumed.', next_need='Retrieve the original when needed.',
                    item_digest=digest(item), block_digest=digest(value), source_locator='/fixture/native.jsonl',
                    replacement_text='summary; original=/fixture/native.jsonl#record=%s,content=%s' % (record, block_index))

    def binding(self, value='implement now\n'):
        return dict(input_digest='sha256:' + hashlib.sha256(value.encode()).hexdigest(), input_char_count=len(value))

    def test_human_receipt_matches_unique_whole_block_without_dropping_host_sibling(self):
        human = message('user', text('host guidance retained'), text('implement now\n'))
        self.assertEqual(adapter.human_input([human], self.binding()), human)
        with self.assertRaises(adapter.NativeContextRejected):
            adapter.human_input([message('user', text('implement now\n'), text('implement now\n'))], self.binding())

    def test_human_receipt_matches_exact_newline_joined_host_envelope(self):
        human = message('user', text('host guidance retained'), text('latest correction'))
        self.assertEqual(adapter.human_input([human], self.binding('host guidance retained\nlatest correction')), human)
        with self.assertRaises(adapter.NativeContextRejected):
            adapter.human_input([human], self.binding('host guidance retained\n\nlatest correction'))

    def server_omit(self, request=None, choices=None):
        request = copy.deepcopy(self.request if request is None else request)
        selections = []
        adapter.request_apply(request, self.originals,
            [dict(self.choice(), disposition='omit')] if choices is None else choices, _inspect=selections)
        operation = getattr(adapter, 'server_context_omit', None)
        self.assertTrue(callable(operation), 'Signed/default-thinking omission has no server-owned application path')
        return operation(request, selections)

    def test_server_omit_preserves_signed_prefix_and_requests_only_selected_results(self):
        request = copy.deepcopy(self.request)
        request['thinking'] = {'type': 'adaptive'}
        request['messages'][1]['content'].insert(0, self.thinking)
        forwarded, observed, reason = self.server_omit(request)
        self.assertEqual(forwarded['messages'], request['messages'])
        self.assertEqual(forwarded['system'], request['system'])
        self.assertEqual(forwarded['thinking'], request['thinking'])
        edit = forwarded['context_management']['edits'][-1]
        self.assertEqual(edit['type'], 'clear_tool_uses_20250919')
        self.assertEqual(edit['keep'], {'type': 'tool_uses', 'value': 0})
        self.assertFalse(edit['clear_tool_inputs'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]['provider_application'], 'requested')
        self.assertEqual(reason, 'server-context-omit-requested')

    def replacement_case(self, carrier_name='Bash'):
        request = copy.deepcopy(self.request)
        request['thinking'] = {'type': 'adaptive'}
        request['messages'][1]['content'].insert(0, self.thinking)
        choice = dict(self.choice(), disposition='replace', reference=digest('Core current declaration'))
        inspected = []
        adapter.request_apply(request, self.originals, [choice], _inspect=inspected)
        projection = dict(schema_version='context-replacement-output.v1', context_reference=choice['reference'],
            replacements=[dict(record=choice['record'], block_index=choice['block_index'],
                summary=choice['summary'], next_need=choice['next_need'],
                original=choice['source_locator'] + '#record=3,content=0')])
        request['messages'].extend([
            dict(role='assistant', content=[dict(type='tool_use', id='publication', name=carrier_name,
                input={'command': 'publish already-required current selection'})]),
            dict(role='user', content=[dict(type='tool_result', tool_use_id='publication', content=[
                text('stdout before\n' + json.dumps({'receipt': projection, 'required': 'carrier wrapper'})),
                text('required carrier sibling')])]),
            dict(role='user', content=[text('latest human instruction remains unchanged')])])
        inspected[0]['replacement_context'] = dict(projection=projection,
            carrier=dict(message_index=5, wire_block_index=0, tool_use_id='publication'))
        return request, inspected

    def test_server_replace_uses_real_native_capsule_without_rewriting_signed_history(self):
        request, choices = self.replacement_case()
        before = copy.deepcopy(request)
        forwarded, observed, reason = adapter.server_context_omit(request, choices)
        self.assertEqual(reason, 'server-context-replace-requested')
        self.assertEqual(forwarded['messages'], before['messages'])
        self.assertEqual(forwarded['system'], before['system'])
        self.assertEqual(forwarded['thinking'], before['thinking'])
        self.assertEqual(request, before)
        edit = forwarded['context_management']['edits'][-1]
        self.assertEqual(edit['exclude_tools'], ['Bash'])
        self.assertEqual(edit['keep'], {'type': 'tool_uses', 'value': 0})
        self.assertFalse(edit['clear_tool_inputs'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]['disposition'], 'replace')
        self.assertEqual(observed[0]['replacement_carrier']['tool_use_id'], 'publication')
        self.assertEqual(observed[0]['provider_application'], 'requested')
        self.assertEqual(adapter.human_input(forwarded['messages'],
            self.binding('latest human instruction remains unchanged')), message('user', text('latest human instruction remains unchanged')))

    def test_server_replace_requires_exact_unique_capsule_and_complete_context_schema(self):
        request, choices = self.replacement_case()
        variants = []
        bare = copy.deepcopy(choices); bare[0].pop('replacement_context'); variants.append((request, bare))
        boolean = copy.deepcopy(choices); boolean[0]['replacement_context'] = True; variants.append((request, boolean))
        for field, value in (('summary', 'different conclusion'), ('next_need', 'different need'),
                             ('original', '/different/original'), ('record', 99), ('block_index', 1)):
            changed = copy.deepcopy(choices)
            changed[0]['replacement_context']['projection']['replacements'][0][field] = value
            candidate = copy.deepcopy(request)
            candidate['messages'][5]['content'][0]['content'][0]['text'] = json.dumps(changed[0]['replacement_context']['projection'])
            variants.append((candidate, changed))
        for field, value in (('schema_version', 'unknown-schema'), ('context_reference', digest('foreign declaration')),
                             ('unrecognized', 'unknown field')):
            changed = copy.deepcopy(choices); changed[0]['replacement_context']['projection'][field] = value
            candidate = copy.deepcopy(request)
            candidate['messages'][5]['content'][0]['content'][0]['text'] = json.dumps(changed[0]['replacement_context']['projection'])
            variants.append((candidate, changed))
        duplicate = copy.deepcopy(request)
        duplicate['messages'][5]['content'][0]['content'].append(text(json.dumps(choices[0]['replacement_context']['projection'])))
        variants.append((duplicate, choices))
        for index, (candidate, selected) in enumerate(variants):
            with self.subTest(variant=index):
                forwarded, observed, _ = adapter.server_context_omit(candidate, selected)
                self.assertEqual(forwarded, candidate)
                self.assertEqual(observed, [])

    def test_server_replace_protects_carrier_name_and_unselected_same_name_sources(self):
        request, choices = self.replacement_case(carrier_name='Read')
        forwarded, observed, reason = adapter.server_context_omit(request, choices)
        self.assertEqual(forwarded, request)
        self.assertEqual(observed, [])
        self.assertEqual(reason, 'server-context-name-scope-unrepresentable')
        request, choices = self.replacement_case()
        request['messages'].extend([
            dict(role='assistant', content=[dict(type='tool_use', id='needed', name='Read', input={})]),
            dict(role='user', content=[dict(type='tool_result', tool_use_id='needed', content='unselected required source')])])
        forwarded, observed, reason = adapter.server_context_omit(request, choices)
        self.assertEqual(forwarded, request)
        self.assertEqual(observed, [])
        self.assertEqual(reason, 'server-context-name-scope-unrepresentable')

    def test_server_replace_requires_a_later_unique_unsigned_carrier_pair(self):
        request, choices = self.replacement_case()
        variants = []
        missing = copy.deepcopy(request); missing['messages'][4]['content'] = [text('carrier call absent')]; variants.append(missing)
        duplicate = copy.deepcopy(request); duplicate['messages'][4]['content'] *= 2; variants.append(duplicate)
        signed = copy.deepcopy(request); signed['messages'][5]['content'][0]['signature'] = 'opaque'; variants.append(signed)
        mismatched = copy.deepcopy(request); mismatched['messages'][5]['content'][0]['tool_use_id'] = 'different'; variants.append(mismatched)
        reordered = copy.deepcopy(request)
        reordered['messages'][4], reordered['messages'][5] = reordered['messages'][5], reordered['messages'][4]
        variants.append(reordered)
        earlier = copy.deepcopy(request)
        earlier['messages'] = [*earlier['messages'][4:6], *earlier['messages'][:4], earlier['messages'][6]]
        early_choices = copy.deepcopy(choices)
        early_choices[0]['message_index'] = 4
        early_choices[0]['replacement_context']['carrier']['message_index'] = 1
        for index, candidate in enumerate(variants):
            with self.subTest(variant=index):
                forwarded, observed, _ = adapter.server_context_omit(candidate, choices)
                self.assertEqual(forwarded, candidate)
                self.assertEqual(observed, [])
        forwarded, observed, _ = adapter.server_context_omit(earlier, early_choices)
        self.assertEqual(forwarded, earlier)
        self.assertEqual(observed, [])

    def test_server_replace_carrier_cannot_be_cleared_and_independent_omit_continues(self):
        request, choices = self.replacement_case()
        choices.append(dict(kind='output', disposition='omit', message_index=5, wire_block_index=0))
        forwarded, observed, reason = adapter.server_context_omit(request, choices)
        self.assertEqual(reason, 'server-context-replace-requested')
        self.assertEqual(forwarded['context_management']['edits'][-1]['exclude_tools'], ['Bash'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]['tool_use_id'], 'consumed')
        request, choices = self.replacement_case()
        choices[0].pop('replacement_context')
        request['messages'].extend([
            dict(role='assistant', content=[dict(type='tool_use', id='independent', name='Search', input={})]),
            dict(role='user', content=[dict(type='tool_result', tool_use_id='independent', content='used independent source')])])
        choices.append(dict(kind='output', disposition='omit', message_index=8, wire_block_index=0))
        forwarded, observed, reason = adapter.server_context_omit(request, choices)
        self.assertEqual(reason, 'server-context-omit-requested')
        self.assertEqual(forwarded['context_management']['edits'][-1]['exclude_tools'], ['Bash', 'Read'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]['tool_use_id'], 'independent')

    def test_server_omit_matches_native_direct_caller_omitted_by_host_replay(self):
        request = copy.deepcopy(self.request)
        request['thinking'] = {'type': 'adaptive'}
        request['messages'][1]['content'][-1].pop('caller')
        forwarded, observed, reason = self.server_omit(request)
        self.assertEqual(reason, 'server-context-omit-requested')
        self.assertEqual(len(observed), 1)
        self.assertEqual(forwarded['messages'], request['messages'])
        self.assertEqual(self.call['caller'], {'type': 'direct'})

    def input_omit_choice(self):
        return dict(self.choice(), disposition='omit', clear_tool_inputs=True,
                    call_record=2, call_block_index=1, call_digest=digest(self.call))

    def test_server_omit_inputs_requires_explicit_authenticated_combined_scope(self):
        request = dict(self.request, thinking={'type': 'adaptive'})
        forwarded, observed, reason = self.server_omit(request, [self.input_omit_choice()])
        self.assertEqual(reason, 'server-context-omit-requested')
        self.assertTrue(forwarded['context_management']['edits'][-1]['clear_tool_inputs'])
        self.assertEqual(forwarded['messages'], request['messages'])
        self.assertTrue(observed[0]['clear_tool_inputs'])

    def test_input_omit_stale_call_binding_and_client_only_path_preserve_inputs(self):
        choice = self.input_omit_choice()
        forwarded, observed, _ = self.server_omit(choices=[dict(choice, call_digest=digest({}))])
        self.assertEqual(forwarded, self.request)
        self.assertEqual(observed, [])
        with self.assertRaises(adapter.NativeContextRejected):
            self.apply(choices=[choice])

    def test_server_omit_mixed_eligible_input_scopes_are_not_broadened(self):
        request = copy.deepcopy(self.request)
        request['messages'].extend([
            dict(role='assistant', content=[dict(type='tool_use', id='other', name='Other', input={})]),
            dict(role='user', content=[dict(type='tool_result', tool_use_id='other', content='other')])])
        inspected = []
        adapter.request_apply(request, self.originals, [self.input_omit_choice()], _inspect=inspected)
        inspected.append(dict(kind='output', disposition='omit', message_index=5, wire_block_index=0))
        forwarded, observed, reason = adapter.server_context_omit(request, inspected)
        self.assertEqual(forwarded, request)
        self.assertEqual(observed, [])
        self.assertEqual(reason, 'server-context-input-scope-unrepresentable')

    def test_pair_matching_protects_other_caller_semantics_and_arguments(self):
        for caller in ({'type': 'code_execution_20260120', 'tool_id': 'execution'},
                       {'type': 'direct', 'unknown': True}):
            request = copy.deepcopy(self.request)
            request['messages'][1]['content'][-1]['caller'] = caller
            with self.subTest(caller=caller):
                forwarded, observed, _ = self.server_omit(request)
                self.assertEqual(forwarded, request)
                self.assertEqual(observed, [])
        request = copy.deepcopy(self.request)
        request['messages'][1]['content'][-1].pop('caller')
        request['messages'][1]['content'][-1]['input']['file_path'] = '/different'
        forwarded, observed, _ = self.server_omit(request)
        self.assertEqual(forwarded, request)
        self.assertEqual(observed, [])

    def test_server_omit_protects_unselected_other_tool_name(self):
        request = copy.deepcopy(self.request)
        request['messages'].extend([
            dict(role='assistant', content=[dict(type='tool_use', id='needed', name='Bash', input={})]),
            dict(role='user', content=[dict(type='tool_result', tool_use_id='needed', content='needed output')])])
        forwarded, observed, _ = self.server_omit(request)
        self.assertEqual(forwarded['context_management']['edits'][-1]['exclude_tools'], ['Bash'])
        self.assertEqual(forwarded['messages'], request['messages'])
        self.assertEqual(len(observed), 1)

    def test_server_omit_preserves_same_name_necessary_result(self):
        request = copy.deepcopy(self.request)
        request['messages'].extend([
            dict(role='assistant', content=[dict(type='tool_use', id='needed', name='Read', input={})]),
            dict(role='user', content=[dict(type='tool_result', tool_use_id='needed', content='needed output')])])
        forwarded, observed, reason = self.server_omit(request)
        self.assertEqual(forwarded, request)
        self.assertEqual(observed, [])
        self.assertEqual(reason, 'server-context-name-scope-unrepresentable')

    def test_server_omit_preserves_unknown_or_existing_tool_clear_policy(self):
        for policy in ({'unknown': True}, {'edits': [{'type': 'clear_tool_uses_20250919'}]},
                       {'edits': [{'type': 'compact_20260112'}]}):
            request = dict(self.request, context_management=policy)
            with self.subTest(policy=policy):
                forwarded, observed, _ = self.server_omit(request)
                self.assertEqual(forwarded, request)
                self.assertEqual(observed, [])

    def test_server_omit_retains_host_thinking_policy_without_changing_it(self):
        policy = {'edits': [{'type': 'clear_thinking_20251015', 'keep': 'all'}]}
        request = dict(self.request, context_management=policy)
        forwarded, _, _ = self.server_omit(request)
        self.assertEqual(forwarded['context_management']['edits'][0], policy['edits'][0])

    def test_server_omit_rejects_compaction_and_prior_text_selections(self):
        compact = dict(self.request)
        compact['messages'] = [dict(role='assistant', content=[dict(type='compaction', content='summary', signature='opaque')])] + compact['messages']
        for request, choices in [(compact, None), (self.request, [dict(self.choice(4, 0, 'assistant'), disposition='omit')])]:
            with self.subTest(request=request):
                forwarded, observed, _ = self.server_omit(request, choices)
                self.assertEqual(forwarded, request)
                self.assertEqual(observed, [])

    def apply(self, choices=None, request=None, originals=None):
        return adapter.request_apply(request or self.request, originals or self.originals,
                                     [self.choice()] if choices is None else choices)

    def test_native_records_normalize_observed_envelopes_without_metadata(self):
        # Shapes inspected at records 5/25/26/29/35 of the existing native log:
        # string user content, signed thinking, tool_use caller, tool_result string, text.
        rows = [dict(type='file-history-snapshot', snapshot={}),
                dict(type='user', sessionId='exact-session', message=dict(role='user', content='implement now\n')),
                dict(type='assistant', message=dict(role='assistant', content=[self.thinking],
                                                   model='native-model', usage={'input_tokens': 123})),
                dict(type='assistant', message=dict(role='assistant', content=[self.call])),
                dict(type='user', message=dict(role='user', content=[self.result])),
                dict(type='assistant', message=dict(role='assistant', content=self.answer['content']))]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'exact-session.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            records = adapter.native_records(path)
        self.assertEqual(set(records), {2, 3, 4, 5, 6})
        self.assertEqual(records[2], self.human)
        self.assertEqual(records[3], message('assistant', self.thinking))
        self.assertEqual(records[4], message('assistant', self.call))
        self.assertEqual(records[5], message('user', self.result))
        self.assertEqual(records[6], self.answer)
        self.assertNotIn('model', records[3])
        self.assertNotIn('usage', records[3])

    def test_omit_unsigned_tool_result_preserves_envelope_and_necessary_siblings(self):
        choice = dict(self.choice(), disposition='omit', replacement_text='')
        forwarded, observations = self.apply([choice])
        self.assertEqual(forwarded['messages'][2]['content'], [dict(self.result, content=''), self.necessary])
        self.assertEqual(forwarded['messages'][:2], self.request['messages'][:2])
        self.assertEqual(observations[0]['disposition'], 'omit')
        self.assertNotIn('original=/fixture', json.dumps(forwarded))

    def test_omit_two_prior_blocks_groups_removal_and_preserves_unknown_sibling(self):
        original = message('assistant', text('used first'), text('used second'),
                           dict(type='future-block', opaque='retain'), text('necessary'))
        self.originals[4] = original
        self.request['messages'][3]['content'] = copy.deepcopy(original['content'])
        self.request['messages'].append(dict(role='user', content='latest human'))
        choices = [dict(self.choice(4, index, 'assistant'), disposition='omit', replacement_text='')
                   for index in (0, 1)]
        forwarded, observations = self.apply(choices)
        self.assertEqual(forwarded['messages'][3]['content'], original['content'][2:])
        self.assertEqual(forwarded['messages'][-1], self.request['messages'][-1])
        self.assertEqual(len(observations), 2)

    def test_omit_single_prior_text_removes_message_but_preserves_latest_human_and_unknown_metadata(self):
        self.request['messages'].append(dict(role='user', content='latest human'))
        choice = dict(self.choice(4, 0, 'assistant'), disposition='omit', replacement_text='')
        forwarded, observations = self.apply([choice])
        self.assertEqual(forwarded['messages'], self.request['messages'][:3] + self.request['messages'][4:])
        self.assertEqual(len(observations), 1)
        self.request['messages'][3]['opaque_metadata'] = {'retain': True}
        forwarded, observations = self.apply([choice])
        self.assertEqual(forwarded, self.request)
        self.assertEqual(observations, [])
        current = dict(self.choice(1, 0, 'message'), disposition='omit', replacement_text='')
        request = copy.deepcopy(self.request)
        request['messages'] = [request['messages'][0]]
        forwarded, observations = self.apply([current], request=request)
        self.assertEqual(forwarded, request)
        self.assertEqual(observations, [])

    def test_omit_active_missing_and_signed_thinking_prefixes_are_rejected(self):
        choice = dict(self.choice(), disposition='omit', replacement_text='')
        for thinking in (None, {'type': 'enabled'}, {'type': 'adaptive'}, {'type': 'disabled'}):
            request = copy.deepcopy(self.request)
            if thinking is None:
                request.pop('thinking')
            else:
                request['thinking'] = thinking
            if thinking == {'type': 'disabled'}:
                request['messages'][1]['content'].insert(0, self.thinking)
            original = copy.deepcopy(request)
            with self.assertRaises(adapter.SignedPrefixSelectionUnsupported):
                self.apply([choice], request=request)
            self.assertEqual(request, original)

    def test_native_path_requires_unique_exact_session(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            first = home / 'projects' / 'one' / 'exact-session.jsonl'
            first.parent.mkdir(parents=True)
            first.write_text('{}\n')
            (first.parent / 'prefix-exact-session.jsonl').write_text('{}\n')
            self.assertEqual(adapter.native_path(home, 'exact-session'), str(first.resolve()))
            self.assertIsNone(adapter.native_path(home, 'missing-session'))
            second = home / 'projects' / 'two' / first.name
            second.parent.mkdir(parents=True)
            second.write_text('{}\n')
            self.assertIsNone(adapter.native_path(home, 'exact-session'))
            with self.assertRaises(adapter.NativeContextRejected):
                adapter.native_path(home, '../exact-session')

    def test_human_input_uses_latest_real_message_and_canonical_identity(self):
        items = dict(self.originals, **{})
        items[5] = message('user', text('<task-notification>finished</task-notification>'))
        items[6] = message('user', dict(type='tool_result', tool_use_id='other', content='tool output'))
        self.assertEqual(adapter.human_input(items, self.binding()), self.human)
        self.assertEqual(adapter.human_input(list(items.values()), self.binding()), self.human)

    def test_human_input_does_not_fall_back_to_an_older_matching_human(self):
        items = dict(self.originals)
        items[7] = message('user', text('new human request'))
        with self.assertRaises(adapter.NativeContextRejected):
            adapter.human_input(items, self.binding())

    def test_human_input_preserves_unicode_and_whitespace(self):
        value = '한글 α\n '
        item = message('user', text(value))
        self.assertEqual(adapter.human_input([item], self.binding(value)), item)
        wrong = self.binding(value)
        wrong['input_char_count'] += 1
        with self.assertRaises(adapter.NativeContextRejected):
            adapter.human_input([item], wrong)

    def test_selected_value_supports_native_text_and_result_content(self):
        self.assertEqual(adapter.selected_value(self.answer, self.choice(4, kind='assistant')), self.answer['content'][0])
        self.assertEqual(adapter.selected_value(self.human, self.choice(1, kind='message')), self.human['content'][0])
        self.assertEqual(adapter.selected_value(self.originals[3], self.choice()), 'consumed body')

    def test_unknown_exchange_and_signed_reasoning_are_explicitly_rejected(self):
        for kind in ('exchange', 'unknown'):
            with self.subTest(kind=kind), self.assertRaises(adapter.NativeContextRejected):
                adapter.selected_value(self.originals[3], dict(kind=kind, block_index=0))
        with self.assertRaises(adapter.NativeContextRejected):
            adapter.selected_value(message('assistant', self.thinking), dict(kind='assistant', block_index=0))
        with self.assertRaises(adapter.NativeContextRejected):
            adapter.selected_value(self.human, dict(kind='assistant', block_index=0))

    def test_merged_result_replacement_preserves_id_error_and_necessary_siblings(self):
        before_request, before_originals = copy.deepcopy(self.request), copy.deepcopy(self.originals)
        choice = self.choice()
        updated, observations = self.apply([choice])
        expected = dict(self.result, content=choice['replacement_text'])
        self.assertEqual(updated['messages'][2]['content'], [expected, self.necessary])
        self.assertEqual(updated['messages'][1], self.request['messages'][1])
        self.assertEqual(updated['system'], self.request['system'])
        self.assertEqual(self.request, before_request)
        self.assertEqual(self.originals, before_originals)
        self.assertIsNot(updated, self.request)
        self.assertEqual(len(observations), 1)
        observed = observations[0]
        self.assertEqual((observed['record'], observed['block_index'], observed['kind']), (3, 0, 'output'))
        self.assertEqual(observed['source_locator'], choice['source_locator'])
        self.assertEqual(observed['original_block_digest'], choice['block_digest'])
        self.assertEqual(observed['forwarded_block_digest'], digest(choice['replacement_text']))
        self.assertNotEqual(observed['original_semantic_block_digest'], observed['forwarded_semantic_block_digest'])

    def test_unsigned_merged_assistant_text_replacement_preserves_unknown_sibling(self):
        unknown = dict(type='future-block', opaque={'signature': 'untouched'})
        self.request['messages'][3]['content'] = [copy.deepcopy(self.unsigned), self.necessary,
                                                  copy.deepcopy(self.answer['content'][0]), unknown]
        updated, observations = self.apply([self.choice(4, kind='assistant')])
        expected = copy.deepcopy(self.request)
        expected['messages'][3]['content'][2]['text'] = self.choice(4, kind='assistant')['replacement_text']
        self.assertEqual(updated, expected)
        self.assertEqual(len(observations), 1)

    def test_message_replacement_preserves_user_role(self):
        choice = self.choice(1, kind='message')
        updated, observations = self.apply([choice])
        self.assertEqual(updated['messages'][0], dict(role='user', content=[text(choice['replacement_text'])]))
        self.assertEqual(len(observations), 1)

    def test_unique_match_is_scoped_to_native_role(self):
        self.request['messages'][2]['content'].append(copy.deepcopy(self.answer['content'][0]))
        updated, observations = self.apply([self.choice(4, kind='assistant')])
        self.assertEqual(updated['messages'][2], self.request['messages'][2])
        self.assertEqual(len(observations), 1)

    def test_cache_control_is_ignored_for_matching_and_preserved_on_replacement(self):
        self.request['messages'][2]['content'][0]['cache_control'] = {'type': 'ephemeral', 'ttl': '1h'}
        self.request['messages'][1]['content'][1]['cache_control'] = {'type': 'ephemeral'}
        updated, observations = self.apply()
        self.assertEqual(updated['messages'][2]['content'][0]['cache_control'], {'type': 'ephemeral', 'ttl': '1h'})
        self.assertEqual(updated['messages'][1], self.request['messages'][1])
        self.assertEqual(len(observations), 1)

    def test_tool_result_block_list_is_replaced_as_declared_content(self):
        blocks = [text('consumed text'), dict(type='image', source={'type': 'base64', 'media_type': 'image/png', 'data': 'fixture'})]
        self.originals[3]['content'][0]['content'] = blocks
        self.request['messages'][2]['content'][0]['content'] = copy.deepcopy(blocks)
        self.request['messages'][2]['content'][0]['is_error'] = self.originals[3]['content'][0]['is_error'] = True
        choice = self.choice()
        updated, observations = self.apply([choice])
        self.assertEqual(updated['messages'][2]['content'][0]['content'], choice['replacement_text'])
        self.assertIs(updated['messages'][2]['content'][0]['is_error'], True)
        self.assertEqual(len(observations), 1)

    def test_changed_original_item_preserves_request_without_observations(self):
        choice = self.choice()
        self.originals[3]['content'][0]['content'] = 'changed original'
        updated, observations = self.apply([choice])
        self.assertEqual(updated, self.request)
        self.assertEqual(observations, [])

    def test_changed_original_value_digest_preserves_request_without_observations(self):
        choice = self.choice()
        choice['block_digest'] = digest('other content')
        updated, observations = self.apply([choice])
        self.assertEqual(updated, self.request)
        self.assertEqual(observations, [])

    def test_ambiguous_text_match_preserves_request_without_observations(self):
        self.request['messages'][3]['content'].append(copy.deepcopy(self.answer['content'][0]))
        updated, observations = self.apply([self.choice(4, kind='assistant')])
        self.assertEqual(updated, self.request)
        self.assertEqual(observations, [])

    def test_duplicate_paired_tool_ids_preserve_request_without_observations(self):
        for side in ('native-call', 'native-result', 'wire-call', 'wire-result'):
            with self.subTest(side=side):
                originals, request = copy.deepcopy(self.originals), copy.deepcopy(self.request)
                if side == 'native-call':
                    originals[2]['content'].append(copy.deepcopy(self.call))
                elif side == 'native-result':
                    originals[5] = message('user', copy.deepcopy(self.result))
                elif side == 'wire-call':
                    request['messages'][1]['content'].append(copy.deepcopy(self.call))
                else:
                    request['messages'][2]['content'].append(copy.deepcopy(self.result))
                updated, observations = self.apply(request=request, originals=originals)
                self.assertEqual(updated, request)
                self.assertEqual(observations, [])

    def test_missing_or_reordered_tool_call_preserves_request(self):
        for mode in ('missing', 'reordered'):
            with self.subTest(mode=mode):
                request = copy.deepcopy(self.request)
                if mode == 'missing':
                    del request['messages'][1]
                else:
                    request['messages'][1], request['messages'][2] = request['messages'][2], request['messages'][1]
                updated, observations = self.apply(request=request)
                self.assertEqual(updated, request)
                self.assertEqual(observations, [])

    def test_semantic_tool_input_cache_control_is_not_transport_metadata(self):
        self.originals[2]['content'][1]['input']['cache_control'] = 'semantic argument'
        self.request['messages'][1]['content'][1]['input']['cache_control'] = 'different argument'
        updated, observations = self.apply()
        self.assertEqual(updated, self.request)
        self.assertEqual(observations, [])

    def test_two_original_text_records_can_map_to_one_merged_wire_message(self):
        self.originals[5] = message('assistant', text('second consumed answer'))
        self.request['messages'][3]['content'].append(copy.deepcopy(self.originals[5]['content'][0]))
        choices = [self.choice(4, kind='assistant'), self.choice(5, kind='assistant')]
        updated, observations = self.apply(choices)
        self.assertEqual([block['text'] for block in updated['messages'][3]['content']],
                         [choice['replacement_text'] for choice in choices])
        self.assertEqual(len(observations), 2)

    def test_duplicate_selections_cannot_overwrite_the_same_wire_block(self):
        updated, observations = self.apply([self.choice(), self.choice()])
        self.assertEqual(updated, self.request)
        self.assertEqual(observations, [])

    def test_no_selections_return_an_independent_copy(self):
        updated, observations = self.apply([])
        self.assertEqual(updated, self.request)
        self.assertIsNot(updated['messages'][0], self.request['messages'][0])
        self.assertEqual(observations, [])

    def test_unknown_request_selection_is_rejected_before_changes(self):
        choices = [self.choice(), dict(self.choice(), kind='unknown')]
        before = copy.deepcopy(self.request)
        with self.assertRaises(adapter.NativeContextRejected):
            self.apply(choices)
        self.assertEqual(self.request, before)

    def test_structured_unknown_selection_kind_uses_declared_rejection(self):
        with self.assertRaises(adapter.NativeContextRejected):
            self.apply([dict(self.choice(), kind={'unsupported': 'value'})])

    def test_structured_unknown_wire_type_remains_intact(self):
        unknown = dict(type={'future': 'structured-tag'}, opaque='retained')
        self.request['messages'][2]['content'].append(unknown)
        updated, observations = self.apply()
        self.assertEqual(updated['messages'][2]['content'][-1], unknown)
        self.assertEqual(len(observations), 1)

    def assert_prefix_selection_rejected(self, request, choices=None, originals=None):
        originals = self.originals if originals is None else originals
        before_request, before_originals = copy.deepcopy(request), copy.deepcopy(originals)
        with self.assertRaisesRegex(adapter.NativeContextRejected, 'signed-prefix-selection-unsupported'):
            self.apply(choices=choices, request=request, originals=originals)
        self.assertEqual(request, before_request)
        self.assertEqual(originals, before_originals)

    def test_same_tool_needed_results_around_selected_middle_before_later_thinking_are_unsupported(self):
        originals = copy.deepcopy(self.originals)
        request = copy.deepcopy(self.request)
        first_call, last_call = [dict(self.call, id=identifier) for identifier in ('needed-A', 'needed-C')]
        first_result, last_result = [dict(self.result, tool_use_id=identifier, content='needed ' + identifier)
                                     for identifier in ('needed-A', 'needed-C')]
        originals[2]['content'] = [first_call, copy.deepcopy(self.call), last_call]
        originals[3]['content'] = [first_result, copy.deepcopy(self.result), last_result]
        originals[5] = message('assistant', copy.deepcopy(self.thinking))
        request['messages'][1]['content'] = copy.deepcopy(originals[2]['content'])
        request['messages'][2]['content'] = copy.deepcopy(originals[3]['content'])
        request['messages'].append(dict(role='assistant', content=[copy.deepcopy(self.thinking)]))
        original = originals[3]
        choice = dict(self.choice(), block_index=1, item_digest=digest(original),
                      block_digest=digest(original['content'][1]['content']))
        self.assert_prefix_selection_rejected(request, [choice], originals)

    def test_prior_unsigned_text_before_later_thinking_is_unsupported(self):
        request = copy.deepcopy(self.request)
        request['messages'].append(dict(role='assistant', content=[copy.deepcopy(self.thinking)]))
        self.assert_prefix_selection_rejected(request, [self.choice(1, kind='message')])

    def test_thinking_capable_initial_request_is_unsupported_without_existing_signed_blocks(self):
        for kind in ('enabled', 'adaptive', 'between_tools'):
            with self.subTest(kind=kind):
                request = dict(copy.deepcopy(self.request), thinking={'type': kind})
                self.assert_prefix_selection_rejected(request)

    def test_missing_or_unknown_thinking_on_first_request_does_not_imply_disabled(self):
        for configuration in ('absent', None, {}, {'type': 'future'}, {'type': {'unknown': True}}):
            with self.subTest(configuration=configuration):
                request = copy.deepcopy(self.request)
                if configuration == 'absent':
                    del request['thinking']
                else:
                    request['thinking'] = configuration
                self.assert_prefix_selection_rejected(request)

    def test_thinking_redacted_compaction_and_unknown_top_level_signed_blocks_are_unsupported(self):
        blocks = [self.thinking, dict(type='redacted_thinking', data='opaque'),
                  dict(type='compaction', content='signed summary'),
                  dict(type='future-block', signature='opaque unknown binding')]
        for block in blocks:
            with self.subTest(block=block['type']):
                request = copy.deepcopy(self.request)
                request['messages'].append(dict(role='assistant', content=[copy.deepcopy(block)]))
                self.assert_prefix_selection_rejected(request)

    def test_signed_system_block_is_unsupported_but_tool_input_signature_is_semantic_data(self):
        request = copy.deepcopy(self.request)
        request['system'].append(dict(type='future-block', signature='opaque'))
        self.assert_prefix_selection_rejected(request)
        self.call['input']['signature'] = 'ordinary tool argument'
        self.request['messages'][1]['content'][1]['input']['signature'] = 'ordinary tool argument'
        updated, observations = self.apply()
        self.assertNotEqual(updated, self.request)
        self.assertEqual(len(observations), 1)

    def test_signed_prefix_without_matching_changes_retains_existing_no_change_behavior(self):
        request = copy.deepcopy(self.request)
        request['messages'].append(dict(role='assistant', content=[copy.deepcopy(self.thinking)]))
        for choices in ([], [dict(self.choice(), block_digest=digest('stale'))]):
            with self.subTest(choices=choices):
                updated, observations = self.apply(choices=choices, request=request)
                self.assertEqual(updated, request)
                self.assertEqual(observations, [])
        del request['messages'][1]
        updated, observations = self.apply(request=request)
        self.assertEqual(updated, request)
        self.assertEqual(observations, [])


class SignedCompactionControllerTests(unittest.TestCase):
    def setUp(self):
        self.request = dict(model='claude-opus-5-5', max_tokens=8192,
            thinking={'type': 'adaptive'}, output_config={'effort': 'xhigh'},
            system=[text('Preserve the objective and restrictions')], tools=[], messages=[
                dict(role='user', content='earlier work'),
                dict(role='assistant', content='earlier conclusion'),
                dict(role='user', content='latest correction; preserve exactly'),
                dict(role='assistant', content=[dict(type='thinking', thinking='kept', signature='kept-signature')])])
        self.betas = ('compact-2026-09-04',)
        self.response = dict(id='msg-compaction', model=self.request['model'], role='assistant',
            stop_reason='compaction', content=[dict(type='compaction', content='accepted summary', signature='opaque')],
            usage=dict(input_tokens=0, output_tokens=0,
                       iterations=[dict(type='compaction', input_tokens=100, output_tokens=20)]))

    def prepare(self, request=None, cut=2, betas=None):
        operation = getattr(adapter, 'prepare_signed_compaction', None)
        self.assertTrue(callable(operation), 'The signed API compaction controller is not implemented')
        return operation(self.request if request is None else request, cut,
                         native_betas=self.betas if betas is None else betas)

    def test_supported_controller_keeps_latest_tail_thinking_settings_and_originals(self):
        original = copy.deepcopy(self.request)
        plan = self.prepare()
        self.assertEqual(plan.request['messages'], self.request['messages'][:2])
        self.assertEqual(plan.request['compaction'], {'type': 'summarize'})
        current = copy.deepcopy(self.request)
        current['messages'].append(dict(role='user', content='newest instruction'))
        forwarded = plan.adopt(self.response, current, native_betas=self.betas, summary_accepted=True)
        self.assertEqual(forwarded['messages'][0]['content'], self.response['content'])
        self.assertEqual(forwarded['messages'][1:], current['messages'][2:])
        for key in ('model', 'thinking', 'output_config', 'system', 'tools', 'max_tokens'):
            self.assertEqual(forwarded[key], current[key])
        self.assertEqual(self.request, original)

    def test_controller_requires_native_beta_and_semantic_summary_acceptance(self):
        with self.assertRaises(adapter.NativeContextRejected):
            self.prepare(betas=())
        plan = self.prepare()
        with self.assertRaises(adapter.NativeContextRejected):
            plan.adopt(self.response, self.request, native_betas=self.betas, summary_accepted=False)

    def test_controller_rejects_pair_split_latest_input_and_required_system_summary(self):
        for cut in (0, 3, 4, True):
            with self.subTest(cut=cut), self.assertRaises(adapter.NativeContextRejected):
                self.prepare(cut=cut)
        request = copy.deepcopy(self.request)
        request['messages'][1] = dict(role='assistant', content=[dict(type='tool_use', id='open', name='Read', input={})])
        request['messages'][2] = dict(role='user', content=[dict(type='tool_result', tool_use_id='open', content='result')])
        with self.assertRaises(adapter.NativeContextRejected):
            self.prepare(request)
        request = copy.deepcopy(self.request)
        request['messages'][0]['role'] = 'system'
        with self.assertRaises(adapter.NativeContextRejected):
            self.prepare(request)

    def test_controller_rejects_changed_prefix_settings_failed_or_foreign_response(self):
        plan = self.prepare()
        for key, value in [('system', []), ('model', 'different'), ('thinking', {'type': 'disabled'}),
                           ('messages', [dict(role='user', content='changed')] + self.request['messages'][1:])]:
            current = copy.deepcopy(self.request)
            current[key] = value
            with self.subTest(key=key), self.assertRaises(adapter.NativeContextRejected):
                plan.adopt(self.response, current, native_betas=self.betas, summary_accepted=True)
        for key, value in [('model', 'different'), ('stop_reason', 'max_tokens'), ('content', [])]:
            response = dict(self.response, **{key: value})
            with self.subTest(response_key=key), self.assertRaises(adapter.NativeContextRejected):
                plan.adopt(response, self.request, native_betas=self.betas, summary_accepted=True)

    def test_controller_rejects_nonstring_result_id_inside_a_closed_cut(self):
        request = copy.deepcopy(self.request)
        request['messages'] = [self.request['messages'][0],
            dict(role='assistant', content=[dict(type='tool_use', id='read', name='Read', input={})]),
            dict(role='user', content=[dict(type='tool_result', tool_use_id=[], content='result')]),
            self.request['messages'][1], *self.request['messages'][2:]]
        with self.assertRaises(adapter.NativeContextRejected):
            self.prepare(request, cut=4)

    def test_controller_rejects_malformed_shapes_and_incompatible_threshold_before_sending(self):
        for key, value in [('tool_choice', None), ('context_management', None),
                           ('context_management', {'edits': [None]}),
                           ('context_management', {'edits': [{'type': 'compact_20260112'}]}),
                           ('messages', [{'role': 'user'}] + self.request['messages'][1:])]:
            request = copy.deepcopy(self.request)
            request[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(adapter.NativeContextRejected):
                self.prepare(request)
        plan = self.prepare()
        current = dict(self.request, context_management=None)
        with self.assertRaises(adapter.NativeContextRejected):
            plan.adopt(self.response, current, native_betas=self.betas, summary_accepted=True)


if __name__ == '__main__':
    unittest.main()
