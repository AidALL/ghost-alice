"""Native transcript reader: Stop answer text and host input markers. Python standard library."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_transcript
from native_transcript import assistant_text_this_turn, native_input_actor, stop_response_text


SESSION = 'reader'


def typed_human(text='Typed request.'):
    """Claude desktop 2.1.299 row for input the user typed."""
    return dict(type='user', sessionId=SESSION, entrypoint='claude-desktop', userType='external',
        promptSource='sdk', turnOrigin='human', origin=dict(kind='human'), isSidechain=False,
        promptId='typed', message=dict(role='user', content=[dict(type='text', text=text)]))


def desktop_sdk(text='Brief from the parent session.'):
    """Child-session brief or side-session note the desktop app starts as a turn."""
    return dict(type='user', sessionId=SESSION, entrypoint='claude-desktop', userType='external',
        promptSource='sdk', turnOrigin='sdk', isSidechain=False, promptId='brief',
        message=dict(role='user', content=text))


def queued(attachment):
    return dict(type='attachment', sessionId=SESSION, entrypoint='claude-desktop', userType='external',
        isSidechain=False, attachment=attachment)


def queued_human(text='Correction typed while Claude works.'):
    return queued(dict(type='queued_command', commandMode='prompt', origin=dict(kind='human'), humanTurn=True,
        prompt=text, source_uuid='source', timestamp='2026-10-21T10:07:52Z', delivery_id='delivery'))


def queued_app_event(text='<ci-monitor-event>Auto-fix enabled.</ci-monitor-event>'):
    return queued(dict(type='queued_command', commandMode='prompt', prompt=text, source_uuid='source',
        timestamp='2026-10-21T09:19:00Z', delivery_id='delivery'))


def queued_peer(text='<agent-message from="worker">Subagent hand-back.</agent-message>'):
    return queued(dict(type='queued_command', commandMode='prompt', isMeta=True, prompt=text,
        origin={'kind': 'peer', 'handback': True, 'senderTaskId': 'worker', 'from': 'worker',
                'name': 'worker', 'body': 'result'},
        reminderId='reminder', source_uuid='source', timestamp='2026-10-15T16:13:00Z', delivery_id='delivery'))


class NativeInputActorTests(unittest.TestCase):
    def test_typed_human_and_cli_sdk_rows_keep_their_actors(self):
        self.assertEqual(native_input_actor(typed_human()), 'human')
        self.assertEqual(native_input_actor(dict(desktop_sdk(), entrypoint='sdk-cli')), 'sdk')

    def test_desktop_app_turn_is_sdk_context(self):
        self.assertEqual(native_input_actor(desktop_sdk()), 'sdk')

    def test_sdk_markers_outside_the_entrypoint_allowlist_stay_unknown(self):
        for entrypoint in ('claude-vscode', 'unknown', None):
            with self.subTest(entrypoint=entrypoint):
                row = desktop_sdk()
                if entrypoint is None:
                    row.pop('entrypoint')
                else:
                    row['entrypoint'] = entrypoint
                self.assertIsNone(native_input_actor(row))

    def test_input_typed_while_claude_works_is_human(self):
        self.assertEqual(native_input_actor(queued_human()), 'human')

    def test_queued_app_event_is_non_human_context(self):
        self.assertEqual(native_input_actor(queued_app_event()), 'context')

    def test_queued_app_event_with_any_other_shape_stays_unknown(self):
        variants = {
            'human-turn key': lambda row: row['attachment'].update(humanTurn=False),
            'meta key': lambda row: row['attachment'].update(isMeta=True),
            'unknown origin': lambda row: row['attachment'].update(origin=dict(kind='unknown')),
            'reminder key': lambda row: row['attachment'].update(reminderId='reminder'),
            'other mode': lambda row: row['attachment'].update(commandMode='bash'),
            'prompt blocks': lambda row: row['attachment'].update(prompt=[dict(type='text', text='x')]),
            'cli entrypoint': lambda row: row.update(entrypoint='cli'),
            'no entrypoint': lambda row: row.pop('entrypoint'),
            'meta row': lambda row: row.update(isMeta=True),
            'sidechain row': lambda row: row.update(isSidechain=True),
        }
        for name, change in variants.items():
            with self.subTest(variant=name):
                row = queued_app_event()
                change(row)
                self.assertIsNone(native_input_actor(row))

    def test_queued_peer_hand_back_is_a_notification(self):
        self.assertEqual(native_input_actor(queued_peer()), 'notification')

    def test_queued_peer_without_the_hand_back_origin_stays_unknown(self):
        variants = {
            'no hand-back': lambda origin: origin.pop('handback'),
            'false hand-back': lambda origin: origin.update(handback=False),
            'other sender': lambda origin: origin.update({'from': 'someone-else'}),
            'empty sender': lambda origin: origin.update(senderTaskId='', **{'from': ''}),
            'cross-session shape': lambda origin: (origin.pop('handback'), origin.pop('senderTaskId'),
                                                   origin.update(fromSession='local_x', msg_id='m')),
        }
        for name, change in variants.items():
            with self.subTest(variant=name):
                row = queued_peer()
                change(row['attachment']['origin'])
                self.assertIsNone(native_input_actor(row))

    def test_task_notifications_stay_notifications(self):
        origin = dict(kind='task-notification', producer='session-task')
        user = dict(typed_human(), promptSource='system', turnOrigin='task_notification', origin=origin)
        attachment = queued(dict(type='queued_command', commandMode='task-notification', origin=origin,
                                 prompt='<task-notification>done</task-notification>'))
        self.assertEqual(native_input_actor(user), 'notification')
        self.assertEqual(native_input_actor(attachment), 'notification')

    def test_text_that_utf8_cannot_encode_never_matches_and_never_raises(self):
        text = 'Typed request.'
        binding = dict(input_char_count=len(text), input_digest='sha256:' + hashlib.sha256(text.encode()).hexdigest())
        broken = 'Typed \ud800equest.'  # Same length, so only the encoding can reject it.
        self.assertEqual(len(broken), len(text))
        self.assertFalse(native_transcript.matches_input_text(broken, binding))
        self.assertTrue(native_transcript.matches_input_text(
            [dict(type='text', text=broken), dict(type='text', text=text)], binding))

    def test_unknown_shape_key_names_markers_without_message_text(self):
        first = dict(typed_human('first secret text'), turnOrigin='scheduled', origin=None)
        second = dict(typed_human('second secret text'), turnOrigin='scheduled', origin=None)
        other = dict(first, promptSource='typed')
        self.assertEqual(native_transcript.input_shape(first), native_transcript.input_shape(second))
        self.assertNotEqual(native_transcript.input_shape(first), native_transcript.input_shape(other))
        self.assertNotIn('secret', native_transcript.input_shape(first))
        self.assertNotIn('Auto-fix', native_transcript.input_shape(dict(queued_app_event(), entrypoint='cli')))


class HumanInputBoundaryTests(unittest.TestCase):
    def rows(self):
        return [typed_human('Earlier request.'),
                dict(type='assistant', sessionId=SESSION, isSidechain=False, message=dict(role='assistant',
                     content=[dict(type='tool_use', id='call', name='Read', input={})])),
                dict(type='user', sessionId=SESSION, isSidechain=False, message=dict(role='user',
                     content=[dict(type='tool_result', tool_use_id='call', content='result')])),
                dict(type='assistant', sessionId=SESSION, isSidechain=False, message=dict(role='assistant',
                     content=[dict(type='text', text='Earlier answer.')])),
                queued_human('Correction typed while Claude works.')]

    def test_tool_activity_starts_at_input_typed_while_claude_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (SESSION + '.jsonl')
            path.write_text(''.join(json.dumps(row) + '\n' for row in self.rows()), encoding='utf-8')
            result = native_transcript.native_tool_activity('claude', SESSION,
                dict(session_id=SESSION, transcript_path=str(path)))
        self.assertEqual(result, dict(status='absent', reason='current-turn-has-no-tool-call'))

    def test_stop_text_starts_at_input_typed_while_claude_works(self):
        rows = self.rows()
        self.assertEqual(assistant_text_this_turn(rows), '')
        answer = dict(type='assistant', sessionId=SESSION, isSidechain=False, message=dict(role='assistant',
                      content=[dict(type='text', text='Answer to the correction.')]))
        self.assertEqual(assistant_text_this_turn([*rows, answer]), 'Answer to the correction.')


class StopResponseTextTests(unittest.TestCase):
    def test_transcript_fallback_keeps_a_record_with_a_line_separator(self):
        text = 'first\u2028second'
        rows = [dict(type='session_meta', payload=dict(id='current')),
                dict(type='turn_context', payload=dict(turn_id='turn')),
                dict(type='response_item', payload=dict(type='message', role='assistant',
                     internal_chat_message_metadata_passthrough=dict(turn_id='turn'),
                     content=[dict(type='output_text', text=text)]))]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rollout.jsonl'
            path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
            native_input = dict(session_id='current', turn_id='turn', transcript_path=str(path))
            self.assertEqual(stop_response_text(native_input), text)


if __name__ == '__main__':
    unittest.main()
