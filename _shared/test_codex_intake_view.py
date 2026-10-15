"""Current-authority projection through real Core, producer and native sources."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import context_lifecycle as lifecycle
import session_intent_analyzer_hook as producer
import strict_session_log
import codex_intake_view as view
from test_response_route import ledger


class CodexIntakeViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root, self.home, self.strict_home = [base / name for name in ('ledger', 'codex', 'strict')]
        self.log = self.home / 'sessions' / 'rollout-current.jsonl'
        self.log.parent.mkdir(parents=True)
        self.rows = [dict(type='session_meta', payload=dict(id='current', model_provider='ghost_alice_context'))]
        self.events, self.receipts, self.humans = [], [], []
        self.source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(self.root), GHOST_ALICE_PLATFORM='codex',
            GHOST_ALICE_SESSION_ID='current', CODEX_THREAD_ID='current', GHOST_ALICE_CODEX_DIR=str(self.home))
        for n, text in enumerate(('first decision', 'repeat decision', 'repeat decision'), 1):
            human = dict(id=f'msg_h{n}', type='message', role='user',
                content=[dict(type='input_text', text=text)])
            self.humans.append(human)
            self.add_native(human, f't{n}', ['user.text'])
            self.write_native()
            observation = ledger.build_input_observation(platform='codex', session_id='current', raw_user_input=text)
            paths = ledger.record_turn(root=self.root, platform='codex', session_id='current',
                raw_user_input=text, source='hook', observation=observation)
            self.events.append(observation['input_event_id'])
            with patch.dict(os.environ, {'GHOST_ALICE_CODEX_DIR': str(self.home)}):
                body = producer.observation_receipt(paths, observation)
            stdout = producer.render_payload('json', body, self.root, routine=True) + '\n'
            self.strict_log = strict_session_log.append_event(self.strict_home, 'codex', 'current',
                dict(hook_id='session-intent', event='UserPromptSubmit', exit_code=0, stdout=stdout))
            receipt = dict(id=f'msg_admin_r{n}', type='message', role='developer',
                content=[dict(type='input_text', text=body)])
            self.receipts.append(receipt)
            self.add_native(receipt, f't{n}', ['hooks.additional_context'])
            if n == 2:
                # Raw copies do not inherit the registered developer item's origin.
                # Their independent turn keeps this positive fixture unambiguous;
                # a separate regression retains the original same-turn layout.
                for role in ('user', 'system', 'assistant'):
                    self.add_native(dict(id='copy_' + role, type='message', role=role,
                        content=[dict(type='input_text', text=body)]), 'copy_turn', ['synthetic.copy'])
        for item in (
            dict(id='active', type='message', role='developer', content=[dict(type='input_text', text='Follow active rules.')]),
            dict(id='correction_stop', type='message', role='developer', content=[dict(type='input_text', text='Correction: preserve this stop instruction.')]),
            dict(id='opaque', type='reasoning', encrypted_content='opaque signed bytes', summary=[]),
            dict(id='call', type='custom_tool_call', call_id='pair', name='exec', input='read original'),
            dict(id='result', type='custom_tool_call_output', call_id='pair', output='original result')):
            self.add_native(item, 't3', ['protected'])
        self.write_native()
        self.strict = self.strict_log.parents[2]
        self.request = dict(model='test', prompt_cache_key='current', input=[
            lifecycle._item(row['payload']) for row in self.rows if row['type'] == 'response_item'])

    def add_native(self, item, turn, kinds):
        payload = copy.deepcopy(item)
        payload['internal_chat_message_metadata_passthrough'] = dict(turn_id=turn, content_item_kinds=kinds)
        self.rows.append(dict(type='response_item', payload=payload))

    def write_native(self):
        self.log.write_text(''.join(json.dumps(row) + '\n' for row in self.rows))

    def select(self, request=None, strict=True):
        return lifecycle.select_request(request if request is not None else self.request, self.root, self.home,
            strict_log_root=self.strict if strict else None)

    def native_item(self, identifier):
        return next(row['payload'] for row in self.rows if row['type'] == 'response_item'
                    and row['payload']['id'] == identifier)

    def update_native(self):
        self.write_native()
        self.request['input'] = [lifecycle._item(row['payload']) for row in self.rows if row['type'] == 'response_item']

    def change_receipt(self, number, change, register=True):
        item = self.native_item(f'msg_admin_r{number}')
        change(item)
        self.update_native()
        if register:
            strict_session_log.append_event(self.strict_home, 'codex', 'current', dict(hook_id='session-intent',
                event='UserPromptSubmit', exit_code=0,
                stdout=producer.render_payload('json', item['content'][0]['text'], self.root, routine=True) + '\n'))

    def rewrite_receipt_json(self, item, change):
        receipt = json.loads(item['content'][0]['text'].split('\n')[2])
        change(receipt)
        item['content'][0]['text'] = '\n[session-intent-receipt]\n' + json.dumps(receipt, ensure_ascii=True,
            separators=(',', ':')) + '\n[/session-intent-receipt]\n'

    def assert_preserved(self, result=None):
        result = result if result is not None else self.select()
        self.assertEqual(result.request, self.request)
        self.assertEqual(result.state, 'preserved')
        self.assertEqual(result.observations, ())

    def assert_item_preserved(self, identifier):
        original = copy.deepcopy(self.request)
        result = self.select()
        before = next(item for item in original['input'] if item['id'] == identifier)
        self.assertIn(before, result.request['input'])
        removed = {item['id'] for item in original['input']} - {item['id'] for item in result.request['input']}
        self.assertTrue(removed.issubset({'msg_admin_r1', 'msg_admin_r2'}))
        for item in original['input']:
            if item['id'] not in removed:
                self.assertIn(item, result.request['input'])
        self.assertEqual(self.request, original)

    def test_real_current_authority_removes_only_superseded_pure_items(self):
        original = copy.deepcopy(self.request)
        sources = {p: p.read_bytes() for p in (self.log, self.strict_log, self.root / 'ghost-state.sqlite3')}
        self.assertEqual(len(set(self.events)), 3)
        self.assertEqual(self.humans[1]['content'], self.humans[2]['content'])
        result = self.select()
        expected = dict(original, input=[item for item in original['input'] if item['id'] not in {'msg_admin_r1', 'msg_admin_r2'}])
        self.assertEqual(result.request, expected)
        self.assertEqual(result.state, 'selected')
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(self.request, original)
        self.assertEqual({p: p.read_bytes() for p in sources}, sources)

    def test_missing_explicit_strict_root_disables_only_this_view(self):
        with patch.dict(os.environ, {'HOME': str(self.strict_home), 'GHOST_ALICE_STRICT_LOG_ROOT': str(self.strict)}):
            self.assert_preserved(self.select(strict=False))

    def test_invalid_explicit_strict_root_preserves(self):
        for root in ('relative', '', 3):
            with self.subTest(root=root):
                self.assert_preserved(view.select_intake_view(self.request, self.root, self.home, root))

    def test_previous_response_key_even_null_or_empty_preserves(self):
        for value in (None, '', 'previous'):
            with self.subTest(value=value):
                self.request['previous_response_id'] = value
                self.assert_preserved()

    def test_conversation_key_even_unknown_or_null_preserves(self):
        for value in ('conversation_fixture', {'id': 'conversation_fixture'}, None, {}, ['unknown']):
            with self.subTest(value=value):
                self.request['conversation'] = value
                self.assert_preserved()

    def test_unknown_secondary_native_owner_shape_preserves(self):
        original = copy.deepcopy(self.rows)
        for payload in ([], None, 'unknown', 3, {}):
            with self.subTest(payload=payload):
                self.rows = copy.deepcopy(original) + [dict(type='session_meta', payload=payload)]
                self.update_native()
                self.assert_preserved()

    def test_unknown_primary_native_owner_shape_preserves(self):
        original = copy.deepcopy(self.rows)
        for payload in ([], None, 'unknown', 3, {}):
            with self.subTest(payload=payload):
                self.rows = copy.deepcopy(original)
                self.rows[0]['payload'] = payload
                self.update_native()
                self.assert_preserved()

    def test_mixed_user_text_same_turn_holds_only_dependent_historical_receipt(self):
        self.add_native(dict(self.humans[0], id='mixed_historical_actor'), 't1', ['user.text', 'unknown'])
        self.rows.insert(2, self.rows.pop())
        self.update_native()
        original = copy.deepcopy(self.request)
        sources = {p: p.read_bytes() for p in (self.log, self.strict_log, self.root / 'ghost-state.sqlite3')}
        result = self.select()
        expected = dict(original, input=[item for item in original['input'] if item['id'] != 'msg_admin_r2'])
        self.assertEqual(result.request, expected)
        self.assertEqual(result.state, 'selected')
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0]['historical_input_event_id'], self.events[1])
        self.assertEqual(self.request, original)
        self.assertEqual({p: p.read_bytes() for p in sources}, sources)

    def assert_omits_only(self, omitted_id, historical_event):
        original = copy.deepcopy(self.request)
        sources = {p: p.read_bytes() for p in (self.log, self.strict_log, self.root / 'ghost-state.sqlite3')}
        result = self.select()
        self.assertEqual(result.request, dict(original, input=[item for item in original['input'] if item['id'] != omitted_id]))
        self.assertEqual(result.state, 'selected')
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0]['historical_input_event_id'], historical_event)
        self.assertEqual(self.request, original)
        self.assertEqual({p: p.read_bytes() for p in sources}, sources)

    def assert_omits_old_receipts(self):
        original = copy.deepcopy(self.request)
        sources = {p: p.read_bytes() for p in (self.log, self.strict_log, self.root / 'ghost-state.sqlite3')}
        result = self.select()
        expected = dict(original, input=[item for item in original['input'] if item['id'] not in {'msg_admin_r1', 'msg_admin_r2'}])
        self.assertEqual(result.request, expected)
        self.assertEqual(result.state, 'selected')
        self.assertEqual({observation['historical_input_event_id'] for observation in result.observations}, set(self.events[:2]))
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(self.request, original)
        self.assertEqual({p: p.read_bytes() for p in sources}, sources)

    def add_observed_native_times(self):
        # Values copied from retained metadata-only preflight, never live sources.
        times = dict(msg_h1=1791922339.458377, msg_admin_r1=1791922339.464437,
            msg_h2=1791922526.837737, msg_admin_r2=1791922526.839434,
            msg_h3=1791922765.114127, msg_admin_r3=1791922765.11579)
        for identifier, value in times.items():
            self.native_item(identifier)['internal_chat_message_metadata_passthrough']['create_time'] = value

    def test_observed_native_time_shape_associates_distinct_plaintext_first_turn(self):
        self.add_observed_native_times()
        instruction = dict(id='instruction_environment', type='message', role='user',
            content=[dict(type='input_text', text='Project instructions and environment context.')])
        self.add_native(instruction, 't1', ['agents_md.instructions', 'environments.environment_context'])
        self.rows[-1]['payload']['internal_chat_message_metadata_passthrough']['create_time'] = 1791922339.122851
        self.rows.insert(1, self.rows.pop())
        self.update_native()
        self.assert_omits_old_receipts()

    def add_association_candidate(self, turn, kinds, text, variant='plain'):
        self.add_native(dict(id='association_' + turn, type='message', role='user',
            content=[dict(type='input_text', text=text)]), turn, kinds)
        raw = self.rows[-1]['payload']
        if variant == 'missing_kind':
            raw['internal_chat_message_metadata_passthrough'].pop('content_item_kinds')
        elif variant == 'opaque':
            raw['content'].append(dict(type='input_image', image_url='opaque original'))
        elif variant == 'unknown_field':
            raw['opaque_original'] = 'unknown original'
        elif variant == 'unknown_block_field':
            raw['content'][0]['opaque_original'] = 'unknown original'
        elif variant == 'malformed':
            raw['content'][0]['text'] = None
        elif variant == 'duplicate_id':
            raw['id'] = 'msg_h' + turn[-1]
        elif variant == 'surrogate':
            raw['content'][0]['text'] = '\ud800'
        position = next(n for n, row in enumerate(self.rows) if row.get('payload', {}).get('id') == 'msg_h' + turn[-1])
        self.rows.insert(position, self.rows.pop())

    def test_current_and_historical_distinct_plaintext_candidates_remain_exact(self):
        self.add_observed_native_times()
        for turn in ('t1', 't2', 't3'):
            self.add_association_candidate(turn, ['agents_md.instructions', 'environments.environment_context'],
                ('Retained instruction and environment context. ' * 50)[:2021])
        self.update_native()
        self.assert_omits_old_receipts()

    def test_same_count_different_digest_candidate_is_not_this_input_event(self):
        for turn in ('t2', 't3'):
            self.add_association_candidate(turn, ['unknown'], 'other decision!')
        self.update_native()
        self.assertEqual(len('other decision!'), len(self.humans[2]['content'][0]['text']))
        self.assert_omits_old_receipts()

    def test_matching_unknown_mixed_untyped_current_and_historical_twins_hold(self):
        original_rows = copy.deepcopy(self.rows)
        for turn, text in (('t1', 'first decision'), ('t3', 'repeat decision')):
            for kinds, variant in ((['unknown'], 'plain'), (['user.text', 'unknown'], 'plain'), (None, 'missing_kind')):
                with self.subTest(turn=turn, kinds=kinds, variant=variant):
                    self.rows = copy.deepcopy(original_rows)
                    self.add_association_candidate(turn, kinds, text, variant)
                    self.update_native()
                    if turn == 't1':
                        self.assert_omits_only('msg_admin_r2', self.events[1])
                    else:
                        original = copy.deepcopy(self.request)
                        sources = {p: p.read_bytes() for p in (self.log, self.strict_log, self.root / 'ghost-state.sqlite3')}
                        self.assert_preserved()
                        self.assertEqual(self.request, original)
                        self.assertEqual({p: p.read_bytes() for p in sources}, sources)

    def test_indeterminate_current_and_historical_candidates_hold_even_with_different_text(self):
        original_rows = copy.deepcopy(self.rows)
        for turn in ('t1', 't3'):
            for variant in ('opaque', 'unknown_field', 'unknown_block_field', 'malformed', 'duplicate_id', 'surrogate'):
                with self.subTest(turn=turn, variant=variant):
                    self.rows = copy.deepcopy(original_rows)
                    self.add_association_candidate(turn, ['unknown'], 'Different ordinary part.', variant)
                    self.update_native()
                    if turn == 't1':
                        self.assert_omits_only('msg_admin_r2', self.events[1])
                    else:
                        original = copy.deepcopy(self.request)
                        sources = {p: p.read_bytes() for p in (self.log, self.strict_log, self.root / 'ghost-state.sqlite3')}
                        self.assert_preserved()
                        self.assertEqual(self.request, original)
                        self.assertEqual({p: p.read_bytes() for p in sources}, sources)

    def test_untyped_matching_item_cannot_replace_positive_actor_nomination(self):
        self.native_item('msg_h3')['internal_chat_message_metadata_passthrough']['content_item_kinds'] = ['unknown']
        self.add_association_candidate('t3', None, 'repeat decision', 'missing_kind')
        self.update_native()
        self.assert_preserved()

    def test_current_positive_nominee_unencodable_plaintext_is_preserved(self):
        self.native_item('msg_h3')['content'][0]['text'] = '\ud800'
        self.update_native()
        original = copy.deepcopy(self.request)
        sources = {p: p.read_bytes() for p in (self.log, self.strict_log, self.root / 'ghost-state.sqlite3')}
        self.assert_preserved()
        self.assertEqual(self.request, original)
        self.assertEqual({p: p.read_bytes() for p in sources}, sources)

    def test_distinct_plaintext_association_keeps_current_whole_wire_id_required(self):
        self.add_association_candidate('t3', ['unknown'], 'Different ordinary context.')
        self.update_native()
        self.request['input'][self.request['input'].index(self.humans[2])] = copy.deepcopy(self.humans[1])
        self.assert_preserved()

    def test_optional_finite_time_supported_for_current_historical_humans_and_receipts(self):
        original_rows = copy.deepcopy(self.rows)
        for identifier in ('msg_h1', 'msg_h2', 'msg_h3', 'msg_admin_r1', 'msg_admin_r2', 'msg_admin_r3'):
            for value in (1791922765, 1791922765.11579, 0, -1.5):
                with self.subTest(identifier=identifier, value=value):
                    self.rows = copy.deepcopy(original_rows)
                    self.native_item(identifier)['internal_chat_message_metadata_passthrough']['create_time'] = value
                    self.update_native()
                    self.assert_omits_old_receipts()

    def test_invalid_time_types_and_nonfinite_values_hold_their_dependent_view(self):
        original_rows = copy.deepcopy(self.rows)
        for identifier in ('msg_h1', 'msg_h2', 'msg_h3', 'msg_admin_r1', 'msg_admin_r2', 'msg_admin_r3'):
            for value in (None, True, False, '1791922765.11579', [], {}, float('nan'), float('inf'), float('-inf')):
                with self.subTest(identifier=identifier, value=value):
                    self.rows = copy.deepcopy(original_rows)
                    self.native_item(identifier)['internal_chat_message_metadata_passthrough']['create_time'] = value
                    self.update_native()
                    if identifier in ('msg_h1', 'msg_admin_r1'):
                        self.assert_omits_only('msg_admin_r2', self.events[1])
                    elif identifier in ('msg_h2', 'msg_admin_r2'):
                        self.assert_omits_only('msg_admin_r1', self.events[0])
                    else:
                        original = copy.deepcopy(self.request)
                        sources = {p: p.read_bytes() for p in (self.log, self.strict_log, self.root / 'ghost-state.sqlite3')}
                        self.assert_preserved()
                        self.assertEqual(self.request, original)
                        self.assertEqual({p: p.read_bytes() for p in sources}, sources)

    def test_known_time_does_not_allow_other_unknown_metadata_fields(self):
        original_rows = copy.deepcopy(self.rows)
        for identifier in ('msg_h1', 'msg_h2', 'msg_h3', 'msg_admin_r1', 'msg_admin_r2', 'msg_admin_r3'):
            with self.subTest(identifier=identifier):
                self.rows = copy.deepcopy(original_rows)
                self.native_item(identifier)['internal_chat_message_metadata_passthrough'].update(create_time=1791922765.11579, unknown=True)
                self.update_native()
                if identifier in ('msg_h1', 'msg_admin_r1'):
                    self.assert_omits_only('msg_admin_r2', self.events[1])
                elif identifier in ('msg_h2', 'msg_admin_r2'):
                    self.assert_omits_only('msg_admin_r1', self.events[0])
                else:
                    self.assert_preserved()

    def test_time_values_do_not_determine_event_or_native_order(self):
        values = dict(msg_h1=1e30, msg_h2=-20, msg_h3=0, msg_admin_r1=999999,
            msg_admin_r2=1e20, msg_admin_r3=-9e15)
        for identifier, value in values.items():
            self.native_item(identifier)['internal_chat_message_metadata_passthrough']['create_time'] = value
        self.update_native()
        self.assert_omits_old_receipts()

    def test_known_time_does_not_replace_exact_current_human_identity(self):
        self.add_observed_native_times()
        self.update_native()
        self.request['input'][self.request['input'].index(self.humans[2])] = copy.deepcopy(self.humans[1])
        self.assert_preserved()

    def test_unknown_missing_empty_mixed_same_turn_actor_holds_dependent_receipt(self):
        original_rows = copy.deepcopy(self.rows)
        for kinds in (['unknown'], [], None, 'user.text', ['synthetic.copy'], ['user.text', 'unknown'], 'missing'):
            with self.subTest(kinds=kinds):
                self.rows = copy.deepcopy(original_rows)
                self.add_native(dict(self.humans[0], id='unclassified_historical_actor'), 't1', kinds)
                if kinds == 'missing':
                    self.rows[-1]['payload']['internal_chat_message_metadata_passthrough'].pop('content_item_kinds')
                self.rows.insert(2, self.rows.pop())
                self.update_native()
                self.assert_omits_only('msg_admin_r2', self.events[1])

    def test_original_same_turn_receipt_copy_is_distinct_from_canonical_input(self):
        for role in ('user', 'system', 'assistant'):
            self.native_item('copy_' + role)['internal_chat_message_metadata_passthrough']['turn_id'] = 't2'
        self.update_native()
        self.assert_omits_old_receipts()

    def test_unknown_same_current_turn_actor_preserves_current_authority(self):
        self.add_native(dict(self.humans[2], id='unknown_current_actor'), 't3', ['unknown'])
        current = next(n for n, row in enumerate(self.rows) if row.get('payload', {}).get('id') == 'msg_h3')
        self.rows.insert(current, self.rows.pop())
        self.update_native()
        original = copy.deepcopy(self.request)
        sources = {p: p.read_bytes() for p in (self.log, self.strict_log, self.root / 'ghost-state.sqlite3')}
        self.assert_preserved()
        self.assertEqual(self.request, original)
        self.assertEqual({p: p.read_bytes() for p in sources}, sources)

    def test_missing_latest_exact_wire_receipt_preserves(self):
        self.request['input'].remove(self.receipts[2])
        self.assert_preserved()

    def test_modified_latest_whole_wire_item_preserves(self):
        latest = next(item for item in self.request['input'] if item['id'] == 'msg_admin_r3')
        latest['content'][0]['text'] += 'extra'
        self.assert_preserved()

    def test_malformed_latest_native_envelope_preserves_all(self):
        self.change_receipt(3, lambda item: self.rewrite_receipt_json(item, lambda receipt: receipt.update(extra='unknown')))
        self.assert_preserved()

    def test_missing_current_exact_wire_human_preserves(self):
        self.request['input'].remove(self.humans[2])
        self.assert_preserved()

    def test_same_text_wrong_current_human_id_preserves(self):
        self.request['input'][self.request['input'].index(self.humans[2])] = copy.deepcopy(self.humans[1])
        self.assert_preserved()

    def test_duplicate_current_wire_human_preserves(self):
        self.request['input'].insert(0, copy.deepcopy(self.humans[2]))
        self.assert_preserved()

    def test_closed_schema_and_configured_coordinates_preserve_unknown_envelopes(self):
        original = copy.deepcopy(self.rows)
        cases = dict(extra=lambda r: r.update(extra='active instruction'), schema=lambda r: r.update(schema_version='unknown'),
            transport=lambda r: r.update(schema_version='session-intent-transport-receipt.v1', intake_status='pending-native-origin'),
            owner=lambda r: r.update(session_id='foreign'), platform=lambda r: r.update(platform='claude'),
            root=lambda r: r.update(ledger_root='/foreign'), state_path=lambda r: r.update(state_path='/foreign'),
            capability=lambda r: r['context_selection'].update(procedure='/foreign'),
            capability_extra=lambda r: r['context_selection'].update(extra=True))
        for name, change in cases.items():
            with self.subTest(name=name):
                self.rows = copy.deepcopy(original)
                self.change_receipt(1, lambda item: self.rewrite_receipt_json(item, change))
                self.assert_item_preserved('msg_admin_r1')

    def test_duplicate_json_keys_are_not_canonical_pure_receipts(self):
        self.change_receipt(1, lambda item: item['content'][0].update(text=item['content'][0]['text'].replace(
            '"intake_status":"observed"', '"intake_status":"observed","intake_status":"observed"')))
        self.assert_item_preserved('msg_admin_r1')

    def test_noncanonical_json_serialization_preserves(self):
        self.change_receipt(1, lambda item: item['content'][0].update(text=item['content'][0]['text'].replace('","', '", "')))
        self.assert_item_preserved('msg_admin_r1')

    def test_compound_prefix_suffix_and_multiblock_preserve(self):
        original = copy.deepcopy(self.rows)
        changes = (
            lambda i: i['content'][0].update(text='Custom instruction\n' + i['content'][0]['text']),
            lambda i: i['content'][0].update(text=i['content'][0]['text'] + '\nCorrection: stop.'),
            lambda i: i['content'].append(dict(type='input_text', text='Keep this block.')),
            lambda i: i.update(opaque='signed'),
            lambda i: i['content'][0].update(extra='instruction'))
        for n, change in enumerate(changes):
            with self.subTest(change=n):
                self.rows = copy.deepcopy(original)
                self.change_receipt(1, change)
                self.assert_item_preserved('msg_admin_r1')

    def test_native_metadata_kind_turn_and_extra_fields_preserve(self):
        original = copy.deepcopy(self.rows)
        changes = (
            lambda i: i.pop('internal_chat_message_metadata_passthrough'),
            lambda i: i['internal_chat_message_metadata_passthrough'].update(content_item_kinds=['unknown']),
            lambda i: i['internal_chat_message_metadata_passthrough'].update(turn_id='t3'),
            lambda i: i['internal_chat_message_metadata_passthrough'].update(extra='unknown'))
        for n, change in enumerate(changes):
            with self.subTest(change=n):
                self.rows = copy.deepcopy(original)
                self.change_receipt(1, change, register=False)
                self.assert_item_preserved('msg_admin_r1')

    def test_current_native_metadata_and_receipt_turn_mismatch_preserve_all(self):
        self.native_item('msg_admin_r3')['internal_chat_message_metadata_passthrough']['turn_id'] = 't2'
        self.update_native()
        self.assert_preserved()

    def test_current_native_human_without_user_text_metadata_preserves(self):
        self.native_item('msg_h3').pop('internal_chat_message_metadata_passthrough')
        self.update_native()
        self.assert_preserved()

    def test_forged_receipt_roles_are_protected(self):
        original = copy.deepcopy(self.rows)
        for role in ('user', 'system', 'assistant'):
            with self.subTest(role=role):
                self.rows = copy.deepcopy(original)
                self.change_receipt(1, lambda i: i.update(role=role), register=False)
                self.assert_item_preserved('msg_admin_r1')

    def test_wrong_registered_issuer_event_exit_owner_and_stdout_preserve(self):
        original = self.strict_log.read_text()
        for field, value in (('hook_id', 'forged'), ('event', 'PreToolUse'), ('exit_code', 1),
                             ('platform', 'claude'), ('session_id', 'foreign'), ('stdout', '{}')):
            with self.subTest(field=field):
                rows = [json.loads(line) for line in original.splitlines()]
                rows[0][field] = value
                self.strict_log.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                self.assert_item_preserved('msg_admin_r1')

    def test_wrong_output_hook_event_preserves(self):
        rows = [json.loads(line) for line in self.strict_log.read_text().splitlines()]
        output = json.loads(rows[0]['stdout'])
        output['hookSpecificOutput']['hookEventName'] = 'PreToolUse'
        rows[0]['stdout'] = json.dumps(output)
        self.strict_log.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.assert_item_preserved('msg_admin_r1')

    def test_duplicate_strict_registered_origin_preserves(self):
        first = self.strict_log.read_text().splitlines()[0]
        with self.strict_log.open('a') as stream:
            stream.write(first + '\n')
        self.assert_item_preserved('msg_admin_r1')

    def test_duplicate_native_receipt_event_preserves_both_origins(self):
        item = copy.deepcopy(self.receipts[0]); item['id'] = 'duplicate'
        self.add_native(item, 't1', ['hooks.additional_context'])
        self.update_native()
        self.assert_item_preserved('msg_admin_r1')
        self.assert_item_preserved('duplicate')

    def test_duplicate_wire_receipt_preserves_both(self):
        self.request['input'].insert(0, copy.deepcopy(self.receipts[0]))
        result = self.select()
        self.assertEqual(sum(item == self.receipts[0] for item in result.request['input']), 2)
        self.assert_item_preserved('msg_admin_r1')

    def test_duplicate_latest_native_or_wire_origin_preserves_all(self):
        self.request['input'].append(copy.deepcopy(self.receipts[2]))
        self.assert_preserved()
        self.request['input'].pop()
        self.add_native(dict(self.receipts[2], id='duplicate_latest'), 't3', ['hooks.additional_context'])
        self.update_native()
        self.assert_preserved()

    def test_historical_canonical_binding_mismatch_preserves(self):
        self.change_receipt(1, lambda item: self.rewrite_receipt_json(item, lambda r: r.update(input_event_id=self.events[1])))
        self.assert_item_preserved('msg_admin_r1')

    def test_unknown_historical_input_event_preserves(self):
        self.change_receipt(1, lambda item: self.rewrite_receipt_json(item, lambda r: r.update(input_event_id='sha256:' + '0' * 64)))
        self.assert_item_preserved('msg_admin_r1')

    def test_historical_native_actor_ambiguity_preserves(self):
        self.add_native(dict(self.humans[0], id='ambiguous_human'), 't1', ['user.text'])
        self.rows.insert(2, self.rows.pop())
        self.update_native()
        self.assert_item_preserved('msg_admin_r1')

    def test_unverified_historical_import_origin_preserves(self):
        with ledger._transaction(self.root) as connection:
            connection.execute('INSERT INTO legacy_migrations (platform,session_id,sources_json,imported_at) VALUES (?,?,?,?)',
                ('codex', 'current', '{}', 'fixture'))
        self.assert_preserved()

    def test_modified_historical_whole_wire_item_preserves(self):
        item = next(item for item in self.request['input'] if item['id'] == 'msg_admin_r1')
        item['content'][0]['text'] += 'wire alteration'
        self.assert_item_preserved('msg_admin_r1')

    def test_native_source_append_race_rejects_projection(self):
        verify = view.verify_source
        def raced(reference):
            if reference['locator'] == str(self.log):
                with self.log.open('a') as stream:
                    stream.write('{}\n')
            return verify(reference)
        with patch.object(view, 'verify_source', side_effect=raced):
            self.assert_preserved()

    def test_duplicate_keys_in_strict_source_reject_projection(self):
        text = self.strict_log.read_text().replace('"platform": "codex"', '"platform": "codex", "platform": "codex"', 1)
        self.strict_log.write_text(text)
        self.assert_preserved()

    def test_historical_human_wrong_digest_preserves(self):
        self.native_item('msg_h1')['content'][0]['text'] = 'wrong historical human'
        self.update_native()
        self.assert_item_preserved('msg_admin_r1')

    def test_pending_canonical_origin_preserves(self):
        with ledger._transaction(self.root) as connection:
            state = ledger._stored_state(connection, 'codex', 'current')
            state['pending_native_inputs'] = [dict(platform='codex', session_id='current',
                transport_observation_id='a' * 32, input_digest=state['latest_input_digest'],
                input_char_count=state['latest_input_char_count'])]
            ledger._save_state(connection, state)
        self.assert_preserved()

    def test_foreign_native_owner_preserves(self):
        self.rows[0]['payload']['id'] = 'foreign'
        self.update_native()
        self.assert_preserved()

    def test_ambiguous_owned_native_transcript_preserves(self):
        sibling = self.log.with_name('duplicate-current.jsonl')
        sibling.write_bytes(self.log.read_bytes())
        self.assert_preserved()

    def test_source_append_race_rejects_projection(self):
        verify = view.verify_source
        def raced(reference):
            if reference['locator'] == str(self.strict_log):
                with self.strict_log.open('a') as stream:
                    stream.write('{}\n')
            return verify(reference)
        with patch.object(view, 'verify_source', side_effect=raced):
            self.assert_preserved()

    def test_missing_configured_strict_storage_is_unavailable(self):
        self.strict_log.unlink()
        result = self.select()
        self.assertEqual((result.state, result.reason), ('unavailable', 'storage-unavailable'))

    def test_unreadable_strict_record_is_unavailable(self):
        self.strict_log.write_text('invalid JSON\n')
        result = self.select()
        self.assertEqual((result.state, result.reason), ('unavailable', 'record-unreadable'))

    def test_unexpected_processing_failure_propagates(self):
        with patch.object(view, '_rows', side_effect=KeyError('processing bug')):
            with self.assertRaises(KeyError):
                self.select()

    def test_composes_with_real_manual_body_disposition(self):
        source_item = self.native_item('result')
        record = next(n for n, row in enumerate(self.rows, 1) if row.get('payload') is source_item)
        lifecycle.record_disposition(self.source, self.events[2], [dict(record=record, block_index=0,
            summary='The result established the owner.', next_need='Retrieve the original if that owner changes.')])
        result = self.select()
        ids = [item['id'] for item in result.request['input']]
        self.assertNotIn('msg_admin_r1', ids); self.assertNotIn('msg_admin_r2', ids)
        forwarded = next(item for item in result.request['input'] if item['id'] == 'result')
        self.assertNotEqual(forwarded['output'], source_item['output'])
        self.assertEqual(len(result.observations), 3)
        self.assertEqual(result.request['prompt_cache_key'], self.request['prompt_cache_key'])


if __name__ == '__main__':
    unittest.main()
