"""SDK context intake never supplies human execution or completion authority."""
import copy
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_response_route as fixtures
import context_lifecycle as lifecycle
import completion_assessment as completion
import response_context_use as uses
import source_lookup

core = fixtures.ledger


class ClaudeContextIntakeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.coords['platform'] = 'claude'
        self.coords = self.fixture.coords
        self.home = Path(self.fixture.tmp.name) / 'claude'
        self.log = self.home / 'projects' / 'native-project' / 'current.jsonl'
        self.log.parent.mkdir(parents=True)
        self.source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root),
            GHOST_ALICE_PLATFORM='claude', GHOST_ALICE_SESSION_ID='current',
            GHOST_ALICE_CLAUDE_DIR=str(self.home))
        self.rows = []
        self.append(self.row('user', 'Earlier context remains retrievable.'))
        self.append(self.row('assistant', [dict(type='thinking', thinking='signed native reasoning', signature='fixture-signature'),
            dict(type='tool_use', id='read-existing', name='Read', input={'file_path':'/fixture/source'})]))
        self.append(self.row('user', [dict(type='tool_result', tool_use_id='read-existing',
            content='consumed navigation ' * 500), dict(type='text', text='Necessary sibling remains.')]))
        self.choice = dict(record=3, block_index=0, kind='output', disposition='replace',
            summary='Navigation served the source decision; uncertainty remains.',
            next_need='Retrieve the original if the source decision changes.')

    def row(self, role, content, **metadata):
        return dict(dict(type=role, sessionId='current', isSidechain=False,
            uuid=f'row-{len(self.rows)+1}', message=dict(role=role, content=copy.deepcopy(content))), **metadata)

    def append(self, row):
        self.rows.append(row)
        with self.log.open('a') as stream:
            stream.write(json.dumps(row) + '\n')

    def request(self):
        return dict(model='fixture', messages=[copy.deepcopy(row['message']) for row in self.rows])

    def stage(self, text='Continue the source comparison.', *, actor='sdk', metadata=None):
        prompt = f'prompt-{len(self.rows)+1}'
        core.stage_native_input(**self.coords, raw_input=text, prompt_id=prompt, transcript_path=str(self.log))
        values = dict(entrypoint='sdk-cli', userType='external', promptSource='sdk', turnOrigin='sdk')
        if actor == 'human':
            values.update(origin={'kind':'human'}, turnOrigin='human')
        values.update(metadata or {})
        self.append(self.row('user', text, promptId=prompt, **values))
        return core.read_session_state(**self.coords)['pending_native_inputs'][-1]

    def reconcile(self):
        lifecycle.prepare_native_request(self.request(), self.fixture.root, self.home,
            platform='claude', session_id='current')
        return core.read_session_state(**self.coords)

    def binding(self, expected=None, *, current=True):
        return lifecycle.context_input_binding(self.source, expected, current=current)

    def publish_call(self):
        self.append(self.row('assistant', [dict(type='tool_use', id='publish-context', name='Bash',
            input={'command':'already-required context publication'}, caller={'type':'direct'})]))

    def complete_publication(self, receipt):
        self.append(self.row('user', [dict(type='tool_result', tool_use_id='publish-context',
            content=[dict(type='text', text=json.dumps(receipt))]), dict(type='text', text='Necessary carrier sibling.')]))

    def select(self, request=None):
        return lifecycle.select_request(request or self.request(), self.fixture.root, self.home,
            platform='claude', session_id='current', native_betas=('context-management-2025-06-27',))

    def test_sdk_reconciles_without_any_human_event_or_canonical_promotion(self):
        observed = self.stage()
        state = self.reconcile()
        self.assertFalse(state['pending_native_inputs'])
        self.assertTrue(state['current_context_input_event_id'])
        self.assertEqual((state['latest_input_event_id'], state['canonical_input_event_id']), ('',''))
        binding = self.binding()
        self.assertEqual(binding['input_event_id'], state['current_context_input_event_id'])
        self.assertTrue(binding['context_only'])
        self.assertEqual(binding['input_digest'], observed['input_digest'])
        events = core.read_session_events(**self.coords)
        self.assertFalse(any(event['event'] == 'user-input-observed' for event in events))
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as tx:
            self.assertIsNone(tx.execute('SELECT 1 FROM canonical_intakes').fetchone())
            record = json.loads(tx.execute('SELECT payload_json FROM core_context_intakes').fetchone()[0])
            self.assertEqual(record['observation']['observed_at'], observed['observed_at'])
            self.assertNotIn('Continue the source comparison.', json.dumps(record))
            with self.assertRaises(sqlite3.IntegrityError):
                tx.execute('UPDATE core_context_intakes SET payload_json=?', ('{}',))

    def test_sdk_publication_cli_and_signed_selection_reach_the_real_content_path(self):
        self.stage(); self.reconcile(); self.publish_call()
        stdout = io.StringIO()
        with patch.dict('os.environ', self.source), redirect_stdout(stdout):
            lifecycle.main(['--root',str(self.fixture.root),'--native-dir',str(self.home),
                '--platform','claude','--session-id','current','--selection-json',json.dumps([self.choice]),
                '--project-replacements'])
        receipt = json.loads(stdout.getvalue())
        self.assertIn('replacement_context', receipt)
        self.assertEqual(self.select().state, 'preserved')
        self.complete_publication(receipt)
        before = self.log.read_bytes(); request = self.request()
        result = self.select(request)
        self.assertEqual((result.state, result.reason), ('selected','server-context-replace-requested'))
        self.assertEqual(result.request['messages'], request['messages'])
        self.assertEqual(result.observations[0]['replacement_carrier']['tool_use_id'], 'publish-context')
        self.assertEqual(self.log.read_bytes(), before)
        changed = copy.deepcopy(request); changed['messages'][3]['content'] += ' changed native sibling'
        self.assertEqual(self.select(changed).state, 'preserved')

    def test_historical_sdk_binding_and_retained_selection_survive_new_sdk_input(self):
        self.stage(); self.reconcile(); first = self.binding(); self.publish_call()
        receipt = lifecycle.record_disposition(self.source, first['input_event_id'], [self.choice], project_replacements=True)
        self.complete_publication(receipt)
        self.stage('Next content decision.'); self.reconcile()
        self.assertNotEqual(self.binding()['input_event_id'], first['input_event_id'])
        self.assertEqual(self.binding(first['input_event_id'], current=False), first)
        self.assertEqual(self.select().state, 'selected')
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            lifecycle.record_disposition(self.source, first['input_event_id'], [self.choice])

    def setup_human_authority(self):
        self.stage('Human authorized implementation.', actor='human'); state = self.reconcile()
        event = state['latest_input_event_id']
        core.record_turn(**self.coords, expected_input_event_id=event, intent_delta={'acceptance_criteria':[
            self.fixture.criterion('first'),dict(id='ownerless', summary='Original proof', source='user-said', admitted=True)]})
        candidate = self.fixture.candidate(); candidate['work_units'][0]['owner']['platform'] = 'claude'
        core.record_response_route(**self.coords, expected_input_event_id=event, candidate=candidate, boundary_ref='boundary-1')
        return event, candidate, core.read_session_state(**self.coords)

    def test_current_sdk_closes_old_human_semantic_ownerless_route_and_completion_gates(self):
        human, candidate, before = self.setup_human_authority()
        human_events = [e for e in core.read_session_events(**self.coords) if e['event']=='user-input-observed']
        self.stage(); state = self.reconcile(); sdk = self.binding()['input_event_id']
        self.assertEqual(state['latest_input_event_id'], human)
        self.assertEqual(state['canonical_input_event_id'], human)
        self.assertEqual(state['acceptance_criteria'], before['acceptance_criteria'])
        self.assertEqual(state['response_route']['status'], 'stale')
        self.assertEqual(core.read_response_route(**self.coords)['status'], 'stale')
        self.assertEqual(self.fixture.api.project_stored_response_route(state)['status'], 'stale')
        self.assertEqual([e for e in core.read_session_events(**self.coords) if e['event']=='user-input-observed'], human_events)
        with self.assertRaises(completion.CompletionAssessmentError):
            completion.response_input_binding(self.source, sdk)
        for identity in (human,sdk):
            with self.subTest(identity=identity):
                with self.assertRaises(ValueError):
                    core.record_turn(**self.coords, expected_input_event_id=identity, intent_delta={'current_goal':'Forbidden SDK authority'})
                with self.assertRaises(ValueError):
                    core.mark_acceptance_criterion_met(**self.coords, criterion_id='ownerless', expected_criterion=state['acceptance_criteria'][1],
                        expected_input_event_id=identity, completion_check_digest='a'*64)
                with self.assertRaises(ValueError):
                    core.record_response_route(**self.coords, expected_input_event_id=identity, candidate=candidate, boundary_ref='boundary-1')
        with core._read_connection(self.fixture.root) as tx:
            with self.assertRaises(completion.CompletionAssessmentError):
                completion._current_contract(core, self.fixture.root, {'platform':'claude','session_id':'current'}, tx)
        self.assertEqual(core.read_session_state(**self.coords), state)

    def test_generic_raw_input_cannot_clear_sdk_marker_but_positive_native_human_can(self):
        self.stage(); state = self.reconcile(); first = self.binding()
        for source in ('agent','hook'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                core.record_turn(**self.coords, raw_user_input='Caller invented human authority.', source=source)
        self.assertEqual(core.read_session_state(**self.coords), state)
        self.stage('Genuine human transition.', actor='human'); later = self.reconcile()
        self.assertFalse(later['current_context_input_event_id'])
        self.assertTrue(later['latest_input_event_id'])
        self.assertNotIn('context_only', self.binding())
        core.record_turn(**self.coords, expected_input_event_id=later['latest_input_event_id'], intent_delta={'current_goal':'Human authority restored'})
        self.assertEqual(self.binding(first['input_event_id'], current=False), first)

    def test_unknown_conflicting_meta_sidechain_tool_result_and_notification_shapes_stay_guarded(self):
        variants = [dict(entrypoint='unknown'),dict(userType='internal'),dict(promptSource='system'),
            dict(turnOrigin='human'),dict(origin={'kind':'human'}),dict(origin={'kind':'unknown'}),
            dict(isMeta=True),dict(isSidechain=True)]
        for index, metadata in enumerate(variants):
            with self.subTest(metadata=metadata):
                self.fixture.root = Path(self.fixture.tmp.name) / f'guard-{index}'
                self.coords['root'] = self.fixture.root
                self.stage(metadata=metadata)
                self.assertTrue(self.reconcile()['pending_native_inputs'])
                with self.assertRaises(lifecycle.ContextSelectionRejected): self.binding()
        self.stage(); self.rows[-1]['message']['content'] = [dict(type='tool_result',tool_use_id='tool',content='Continue the source comparison.')]
        self.log.write_text(''.join(json.dumps(row)+'\n' for row in self.rows))
        self.assertTrue(self.reconcile()['pending_native_inputs'])

    def test_notification_does_not_clear_current_sdk_or_create_human_authority(self):
        self.stage(); self.reconcile(); first = self.binding()
        self.stage('<task-notification>peer result</task-notification>', metadata=dict(promptSource='system',turnOrigin='task_notification',
            origin={'kind':'task-notification','producer':'session-task'}))
        state = self.reconcile()
        self.assertFalse(state['pending_native_inputs'])
        self.assertEqual(self.binding(), first)
        with self.assertRaises(ValueError): core.record_turn(**self.coords, raw_user_input='Not a human transition', source='hook')

    def test_ambiguous_mismatched_or_changed_append_source_cannot_bind_context(self):
        self.stage(); self.append(copy.deepcopy(self.rows[-1]))
        self.assertTrue(self.reconcile()['pending_native_inputs'])
        with self.assertRaises(lifecycle.ContextSelectionRejected): self.binding()
        self.rows.pop(); self.rows[-1]['message']['content'] = 'Digest mismatch.'
        self.log.write_text(''.join(json.dumps(row)+'\n' for row in self.rows))
        self.assertTrue(self.reconcile()['pending_native_inputs'])
        self.rows[-1]['message']['content'] = 'Continue the source comparison.'
        self.rows[0]['message']['content'] += ' changed committed prefix'
        self.log.write_text(''.join(json.dumps(row)+'\n' for row in self.rows))
        self.assertTrue(self.reconcile()['pending_native_inputs'])

    def test_sdk_frozen_row_survives_later_repeat_but_changed_prefix_or_inode_is_rejected(self):
        self.stage(); self.reconcile(); first = self.binding()
        self.append(self.row('user', 'Continue the source comparison.', entrypoint='sdk-cli',userType='external',promptSource='sdk',turnOrigin='sdk'))
        self.assertEqual(self.binding(first['input_event_id'], current=False), first)
        original = self.log.read_bytes()
        changed = original.replace(b'Earlier context',b'Altered context',1); self.log.write_bytes(changed)
        with self.assertRaises(lifecycle.ContextSelectionRejected): self.binding(first['input_event_id'], current=False)
        self.log.write_bytes(original)
        replacement = self.log.with_suffix('.replacement'); replacement.write_bytes(original); replacement.replace(self.log)
        with self.assertRaises(lifecycle.ContextSelectionRejected): self.binding(first['input_event_id'], current=False)

    def test_unstaged_repeated_native_row_cannot_substitute_the_frozen_sdk_input(self):
        self.stage(); self.reconcile()
        self.append(self.row('user','Continue the source comparison.',entrypoint='sdk-cli',userType='external',promptSource='sdk',turnOrigin='sdk'))
        self.publish_call()
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            lifecycle.record_disposition(self.source,None,[self.choice],project_replacements=True)

    def test_copied_native_transcript_cannot_substitute_the_sdk_original_owner(self):
        self.stage(); self.reconcile(); self.publish_call()
        other = Path(self.fixture.tmp.name) / 'other-claude'
        copied = other / 'projects' / 'native-project' / 'current.jsonl'; copied.parent.mkdir(parents=True)
        copied.write_bytes(self.log.read_bytes())
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            lifecycle.record_disposition(dict(self.source,GHOST_ALICE_CLAUDE_DIR=str(other)),None,[self.choice],project_replacements=True)

    def test_pending_unknown_blocks_existing_context_binding_and_public_selectors_remain_pure(self):
        self.stage(); self.reconcile(); first = self.binding()
        self.publish_call(); receipt = lifecycle.record_disposition(self.source, None, [self.choice], project_replacements=True)
        self.complete_publication(receipt)
        self.stage('Unknown input.', metadata={'entrypoint':'unknown'})
        before = core.read_session_state(**self.coords)
        self.assertEqual(self.select().state, 'preserved')
        with self.assertRaises(lifecycle.ContextSelectionRejected): self.binding(first['input_event_id'], current=True)
        self.assertEqual(core.read_session_state(**self.coords), before)

    def test_copied_sdk_state_without_native_context_records_has_no_binding(self):
        self.stage(); state = self.reconcile(); first = self.binding()
        copied = Path(self.fixture.tmp.name) / 'copied'
        paths = core.session_paths(copied,'claude','current'); paths['state'].parent.mkdir(parents=True)
        paths['state'].write_text(json.dumps(state))
        paths['events'].write_text(''.join(json.dumps(event)+'\n' for event in core.read_session_events(**self.coords)))
        core.migrate_session(root=copied,platform='claude',session_id='current')
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            lifecycle.context_input_binding(dict(self.source,GHOST_ALICE_SESSION_INTENT_ROOT=str(copied)),first['input_event_id'])

    def test_sdk_source_use_and_historical_judgment_share_context_only_binding(self):
        self.stage(); self.reconcile(); first = self.binding()
        source_file = Path(self.fixture.tmp.name) / 'source.json'; source_file.write_text(json.dumps({'needed':'Original source body.'}))
        observation = source_lookup.lookup(source_file,pointer='/needed')
        self.append(self.row('assistant',[dict(type='tool_use',id='lookup',name='Read',input={'file_path':str(source_file)})]))
        receipt = uses.record_use(self.source,first['input_event_id'],observation,purpose='Compare the original source.')
        self.append(self.row('user',[dict(type='tool_result',tool_use_id='lookup',content=json.dumps(dict(observation,use_reference=receipt['reference'])))]))
        self.append(self.row('assistant',[dict(type='text',text='The original supports the comparison.')]))
        self.stage('Next source decision.'); self.reconcile()
        judgment = uses.record_use_judgments(self.source,None,[dict(use_reference=receipt['reference'],disposition='omit',
            summary='Source comparison is complete.',next_need='Retrieve if the comparison changes.')])
        self.assertEqual(judgment['judgment_count'],1)
        inspected = []
        result = uses.select_uses(self.request(),self.fixture.root,self.home,platform='claude',session_id='current',_inspect=inspected)
        self.assertEqual(result.reason,'selection-inspected')
        self.assertEqual(inspected[0]['use_reference'],receipt['reference'])
        self.assertEqual(inspected[0]['disposition'],'omit')
        with closing(sqlite3.connect(self.fixture.root / 'ghost-state.sqlite3')) as tx:
            original = json.loads(tx.execute('SELECT payload_json FROM core_response_context_uses').fetchone()[0])
            self.assertEqual(original['input_binding'], first)


if __name__ == '__main__':
    unittest.main()
