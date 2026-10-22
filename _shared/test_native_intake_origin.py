"""Claude native intake origin: queue order, waiting records and notices.

Dependencies: Python standard library. Every ledger and transcript lives in a
temporary directory; no test reads or writes an installed ledger.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'session-intent-analyzer' / 'scripts'))
import session_intent_ledger as ledger
import test_native_transcript as shapes

SESSION = shapes.SESSION


def digest(text):
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


class IntakeOriginQueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'intent'
        self.log = Path(tmp.name) / 'projects' / 'native' / (SESSION + '.jsonl')
        self.log.parent.mkdir(parents=True)
        self.log.write_text('')
        self.coords = dict(root=self.root, platform='claude', session_id=SESSION)

    def stage(self, text, row=None):
        """Stage one hook observation, then append its native row when the host wrote one."""
        prompt_id = row.get('promptId') if row else None
        ledger.stage_native_input(**self.coords, raw_input=text, prompt_id=prompt_id, transcript_path=str(self.log))
        if row is not None:
            self.append(row)
        return ledger.read_session_state(**self.coords)['pending_native_inputs'][-1]['transport_observation_id']

    def append(self, row):
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row) + '\n')

    def reconcile(self):
        """The PreToolUse or Stop wrapper's call, after the host appended the turn's input."""
        return ledger.reconcile_native_inputs(**self.coords, record_wait=True)

    def events(self, name=None):
        return [event for event in ledger.read_session_events(**self.coords) if name is None or event['event'] == name]

    def human(self, text):
        self.stage(text, shapes.typed_human(text))
        state = self.reconcile()
        self.assertEqual(state['latest_input_digest'], digest(text))
        return state

    def assert_context_refuses_writes(self, state):
        with self.assertRaisesRegex(ValueError, 'SDK context input supplies no human mutation authority'):
            ledger.record_turn(**self.coords, expected_input_event_id=state['latest_input_event_id'] or None,
                               intent_delta={'current_goal': 'No authority from a non-human turn'})

    def test_desktop_child_brief_is_sdk_context_without_human_authority(self):
        brief = 'Implement the approved design.'
        self.stage(brief, shapes.desktop_sdk(brief))
        state = self.reconcile()
        self.assertEqual(state['pending_native_inputs'], [])
        self.assertTrue(state['current_context_input_event_id'])
        self.assertEqual(state['latest_input_event_id'], '')
        self.assertEqual([event['actor'] for event in self.events('native-input-resolved')], ['sdk'])
        self.assert_context_refuses_writes(state)

    def test_typed_input_after_a_brief_starts_the_child_intent_record(self):
        brief, typed = 'Implement the approved design.', 'Try again'
        self.stage(brief, shapes.desktop_sdk(brief))
        self.stage(typed, shapes.typed_human(typed))
        state = self.reconcile()
        self.assertEqual(state['latest_input_digest'], digest(typed))
        self.assertEqual(state['current_context_input_event_id'], '')
        self.assertEqual([event['actor'] for event in self.events('native-input-resolved')], ['sdk', 'human'])
        self.assertEqual(self.events('native-input-superseded'), [])
        ledger.record_turn(**self.coords, expected_input_event_id=state['latest_input_event_id'],
                           intent_delta={'current_goal': 'Typed input authorizes the child session'})

    def test_unlocated_row_before_a_human_row_no_longer_blocks_it(self):
        missing = self.stage('Cross-session message without a matching native row.')
        typed = 'Typed after the message.'
        later = self.stage(typed, shapes.typed_human(typed))
        state = self.reconcile()
        self.assertEqual(state['pending_native_inputs'], [])
        self.assertEqual(state['intake_status'], 'observed')
        self.assertEqual(state['latest_input_digest'], digest(typed))
        superseded = self.events('native-input-superseded')
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0]['transport_observation_id'], missing)
        self.assertEqual(superseded[0]['superseded_by_transport_observation_id'], later)
        self.assertEqual((superseded[0]['actor'], superseded[0]['cause']), ('unknown', 'row-not-found'))
        self.assertNotIn('native_row', superseded[0])
        self.assertEqual(len(self.events('user-input-observed')), 1)

    def test_unknown_row_without_a_later_human_waits_and_is_recorded_once(self):
        before = self.human('Earlier typed request.')
        text = 'A turn from a host that Ghost-ALICE does not recognize.'
        blocked = self.stage(text, dict(shapes.desktop_sdk(text), entrypoint='claude-vscode'))
        for _ in range(2):
            state = self.reconcile()
        self.assertEqual(len(state['pending_native_inputs']), 1)
        self.assertEqual(state['latest_input_event_id'], before['latest_input_event_id'])
        with self.assertRaisesRegex(ValueError, 'native input origin is pending'):
            ledger.record_turn(**self.coords, expected_input_event_id=before['latest_input_event_id'],
                               intent_delta={'current_goal': 'Pending origin refuses writes'})
        waiting = self.events('native-input-waiting')
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]['transport_observation_id'], blocked)
        self.assertTrue(waiting[0]['cause'].startswith('unknown-actor:'))
        self.assertNotIn(text, json.dumps(waiting[0]))
        self.assertEqual(state['native_input_waiting']['transport_observation_id'], blocked)

    def test_a_row_text_that_cannot_be_encoded_does_not_fail_the_reconcile(self):
        missing = self.stage('Prompt whose native row the host never wrote.')
        # json.dumps escapes the unpaired surrogate; json.loads returns it as text that UTF-8 cannot encode.
        self.append(dict(shapes.typed_human('broken \ud800 row'), promptId=None))
        typed = 'Typed after the broken row.'
        self.stage(typed, shapes.typed_human(typed))
        state = self.reconcile()
        self.assertEqual(state['pending_native_inputs'], [])
        self.assertEqual(state['latest_input_digest'], digest(typed))
        superseded = self.events('native-input-superseded')
        self.assertEqual([(event['transport_observation_id'], event['cause']) for event in superseded],
                         [(missing, 'row-not-found')])

    def test_notice_check_reads_without_a_write_lock_when_nothing_is_due(self):
        self.human('Typed request.')
        blocker = sqlite3.connect(self.root / 'ghost-state.sqlite3', isolation_level=None)
        blocker.execute('BEGIN IMMEDIATE')
        try:
            started = time.monotonic()
            self.assertIsNone(ledger.claim_native_input_notice(**self.coords))
            self.assertLess(time.monotonic() - started, 2)
        finally:
            blocker.execute('ROLLBACK')
            blocker.close()

    def test_failed_reconcile_is_recorded_once_per_session_with_a_count(self):
        self.human('Typed request.')
        for error in (sqlite3.OperationalError('database is locked'),
                      UnicodeEncodeError('utf-8', 'x\ud800', 1, 2, 'surrogates not allowed')):
            ledger.record_native_intake_failure(**self.coords, error=error)
        state = ledger.read_session_state(**self.coords)
        failure = state['native_intake_failure']
        self.assertEqual((failure['count'], failure['error']), (2, 'UnicodeEncodeError'))
        self.assertLessEqual(failure['first_at'], failure['last_at'])
        self.assertTrue(failure['detail'].isascii())
        self.assertEqual([event['error'] for event in self.events('native-intake-failed')],
                         ['OperationalError', 'UnicodeEncodeError'])
        self.assertEqual(state['latest_input_digest'], digest('Typed request.'))
        listed = ledger.list_native_intake_failures(self.root, 'claude')
        self.assertEqual([(item['session_id'], item['count'], item['error']) for item in listed],
                         [(SESSION, 2, 'UnicodeEncodeError')])
        self.assertEqual(ledger.list_native_intake_failures(self.root, 'codex'), [])

    def test_repeated_failures_of_one_kind_count_without_new_events(self):
        """A cause that persists fails every PreToolUse and Stop; the count carries that, an event per repeat would not."""
        self.human('Typed request.')
        errors = [ValueError('native transcript cursors are invalid')] * 3 + [
            KeyError('pending'), KeyError('cursor'), ValueError('native transcript cursors are invalid')]
        for error in errors:
            ledger.record_native_intake_failure(**self.coords, error=error)
        state = ledger.read_session_state(**self.coords)
        failure = state['native_intake_failure']
        self.assertEqual((failure['count'], failure['error'], failure['detail']),
                         (len(errors), 'ValueError', 'native transcript cursors are invalid'))
        self.assertLessEqual(failure['first_at'], failure['last_at'])
        self.assertEqual([event['error'] for event in self.events('native-intake-failed')],
                         ['ValueError', 'KeyError', 'ValueError'])
        # A state-only save keeps the revision paired with the last event.
        self.assertEqual(state['ledger_revision'], self.events()[-1]['ledger_revision'])
        self.assertEqual([(item['count'], item['error']) for item in ledger.list_native_intake_failures(self.root)],
                         [(len(errors), 'ValueError')])

    def test_generation_boundary_reconcile_records_no_wait(self):
        self.stage('Prompt whose native row the host has not appended yet.')
        before = ledger.read_session_state(**self.coords)
        events = len(self.events())
        state = ledger.reconcile_native_inputs(**self.coords)
        self.assertEqual(state, before)
        self.assertEqual(len(self.events()), events)
        self.assertIsNone(ledger.claim_native_input_notice(**self.coords))

    def test_notice_is_claimed_once_per_session_and_cause(self):
        claim = lambda: ledger.claim_native_input_notice(**self.coords)
        self.assertIsNone(claim())
        self.stage('First message without a native row.')
        self.reconcile()
        notice = claim()
        self.assertEqual((notice['cause'], notice['pending_count']), ('row-not-found', 1))
        self.assertIsNone(claim())
        self.human('Typed input clears the wait.')
        self.assertEqual(self.reconcile()['native_input_waiting'], {})
        self.assertIsNone(claim())
        self.stage('Second message without a native row.')
        self.reconcile()
        self.assertEqual(len(self.events('native-input-waiting')), 2)
        self.assertIsNone(claim())
        text = 'Unrecognized host turn.'
        self.human('Typed input clears the second wait.')
        self.stage(text, dict(shapes.desktop_sdk(text), entrypoint='claude-vscode'))
        self.reconcile()
        self.assertTrue(claim()['cause'].startswith('unknown-actor:'))
        self.assertEqual(len(self.events('native-input-notice')), 2)

    def test_input_typed_while_claude_works_becomes_the_current_human_input(self):
        text = 'Stop and use the other file.'
        self.stage(text, shapes.queued_human(text))
        state = self.reconcile()
        self.assertEqual(state['pending_native_inputs'], [])
        self.assertEqual(state['latest_input_digest'], digest(text))
        self.assertEqual([event['actor'] for event in self.events('native-input-resolved')], ['human'])
        ledger.record_turn(**self.coords, expected_input_event_id=state['latest_input_event_id'],
                           intent_delta={'current_goal': 'Mid-turn correction'})

    def test_queued_app_event_is_context_without_wait_or_original(self):
        self.human('Typed request.')
        event_text = '<ci-monitor-event>Auto-fix enabled.</ci-monitor-event>'
        self.stage(event_text, shapes.queued_app_event(event_text))
        state = self.reconcile()
        self.assertEqual(state['pending_native_inputs'], [])
        self.assertEqual(state['native_input_waiting'], {})
        self.assertTrue(state['current_context_input_event_id'])
        self.assertEqual(self.events('native-input-resolved')[-1]['actor'], 'context')
        self.assertEqual(self.events('native-input-waiting'), [])
        with closing(sqlite3.connect(self.root / 'ghost-state.sqlite3')) as connection:
            self.assertIsNone(connection.execute('SELECT 1 FROM core_context_intakes').fetchone())
        self.assert_context_refuses_writes(state)
        self.assertIsNone(ledger.claim_native_input_notice(**self.coords))

    def test_queued_peer_hand_back_keeps_the_current_human_input(self):
        before = self.human('Typed request.')
        text = '<agent-message from="worker">Subagent hand-back.</agent-message>'
        self.stage(text, shapes.queued_peer(text))
        state = self.reconcile()
        self.assertEqual(state['pending_native_inputs'], [])
        self.assertEqual(state['latest_input_event_id'], before['latest_input_event_id'])
        self.assertEqual(state['current_context_input_event_id'], '')
        self.assertEqual(self.events('native-input-resolved')[-1]['actor'], 'notification')

    def test_task_notification_keeps_the_current_human_input(self):
        before = self.human('Typed request.')
        text = '<task-notification>done</task-notification>'
        origin = dict(kind='task-notification', producer='session-task')
        self.stage(text, dict(shapes.typed_human(text), promptSource='system', turnOrigin='task_notification',
                              origin=origin, promptId=None))
        state = self.reconcile()
        self.assertEqual(state['pending_native_inputs'], [])
        self.assertEqual(state['latest_input_event_id'], before['latest_input_event_id'])

    def test_first_reconcile_clears_a_queue_staged_before_the_fix(self):
        note = '<system-reminder>Linked sessions now.</system-reminder>'
        self.stage(note, shapes.desktop_sdk(note))
        self.stage('<cross-session-message>Child finished.</cross-session-message>',
                   dict(shapes.typed_human('Another Claude session sent a message: <cross-session-message>'
                                           'Child finished.</cross-session-message>'), isMeta=True, promptId=None))
        event_text = '<ci-monitor-event>Auto-fix enabled.</ci-monitor-event>'
        self.stage(event_text, shapes.queued_app_event(event_text))
        correction = 'Mid-turn correction.'
        self.stage(correction, shapes.queued_human(correction))
        typed = 'Latest typed request.'
        self.stage(typed, dict(shapes.typed_human(typed), promptId='latest'))
        state = self.reconcile()
        self.assertEqual(state['pending_native_inputs'], [])
        self.assertEqual(state['latest_input_digest'], digest(typed))
        self.assertEqual(state['current_context_input_event_id'], '')
        self.assertEqual([event['actor'] for event in self.events('native-input-resolved')],
                         ['sdk', 'context', 'human', 'human'])
        self.assertEqual([event['cause'] for event in self.events('native-input-superseded')], ['row-not-found'])

    def test_reconcile_reads_each_transcript_once(self):
        for index in range(30):
            text = f'Typed request {index}.'
            self.stage(text, dict(shapes.typed_human(text), promptId=f'prompt-{index}'))
        opened = []
        original = Path.open
        def counting_open(path, *args, **kwargs):
            if Path(path) == self.log:
                opened.append(args[0] if args else kwargs.get('mode', 'r'))
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'open', counting_open):
            state = self.reconcile()
        self.assertEqual(state['pending_native_inputs'], [])
        self.assertEqual(len(self.events('user-input-observed')), 30)
        self.assertEqual(len(opened), 1)


if __name__ == '__main__':
    unittest.main()
