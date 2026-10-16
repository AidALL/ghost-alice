"""Owning review determines meaning; Core binds its exact reviewed material."""
import copy
import importlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest import mock

import test_response_route as fixtures


class ReviewReceiptTests(unittest.TestCase):
    def test_long_claims_and_criterion_prose_do_not_echo_and_original_is_unchanged(self):
        import completion_assessment as api
        claim = 'authored long answer ' * 10000
        result = dict(candidate_reference='candidate', bindings=[dict(binding_id='binding',
            claim_index=0, claim=claim, criterion=dict(id='AC1', work_unit_id='work',
            contract_revision=1, summary='criterion body ' * 1000))])
        original = copy.deepcopy(result)
        receipt = api.review_receipt(result)
        self.assertFalse(claim in json.dumps(receipt), 'Receipt must not repeat the authored claim')
        self.assertFalse('criterion body' in json.dumps(receipt), 'Receipt must identify the criterion without its prose')
        self.assertEqual(receipt['bindings'][0], dict(binding_id='binding', claim_index=0,
            criterion=dict(id='AC1',work_unit_id='work',contract_revision=1)))
        self.assertTrue(result == original, 'Preparing the receipt must preserve the original review input')


class CompletionAssessmentTests(unittest.TestCase):
    def test_source_use_judgment_shares_the_response_review_transaction(self):
        from unittest.mock import patch
        api = self.api()
        candidate = api.prepare_response_review(self.source, response_text=self.proof(),
            source_paths=[str(self.observation)], verified_at=self.time)
        review = self.response_review(candidate)
        received = []
        def record(source, event, judgments, *, transaction):
            self.assertIsNotNone(transaction)
            received.append((event, judgments))
            return dict(status='recorded', references=['use-judgment'])
        judgments = [dict(use_reference='use', disposition='replace', summary='Conclusion', next_need='Changed purpose')]
        with patch('response_context_use.record_use_judgments', record, create=True):
            result = api.record_response_review(self.source, candidate['candidate_reference'], review,
                context_use_judgments=judgments, expected_input_event_id=self.fixture.event)
        self.assertEqual(received, [(self.fixture.event, judgments)])
        self.assertEqual(result['context_use_judgments']['status'], 'recorded')

    def test_rejected_source_use_judgment_rolls_back_the_response_review(self):
        from unittest.mock import patch
        api = self.api()
        candidate = api.prepare_response_review(self.source, response_text=self.proof(),
            source_paths=[str(self.observation)], verified_at=self.time)
        review = self.response_review(candidate)
        core, root, _ = api._coordinates(self.source)
        with core._read_connection(root) as connection:
            before = connection.execute('SELECT count(*) FROM core_completion_responses').fetchone()[0]
        with patch('response_context_use.record_use_judgments', side_effect=ValueError('source not read'), create=True):
            with self.assertRaisesRegex(ValueError, 'source not read'):
                api.record_response_review(self.source, candidate['candidate_reference'], review,
                    context_use_judgments=[], expected_input_event_id=self.fixture.event)
        with core._read_connection(root) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM core_completion_responses').fetchone()[0], before)

    def test_selected_original_claim_lookup_preserves_owner_binding(self):
        candidate = self.prepare()
        api = self.api()
        selected = api.read_review_claims(self.source, candidate['candidate_reference'], [0])
        self.assertEqual(selected, [candidate['bindings'][0]])
        with self.assertRaises(api.CompletionAssessmentError):
            api.read_review_claims(dict(self.source, GHOST_ALICE_SESSION_ID='foreign'),
                                   candidate['candidate_reference'], [0])

    def test_retained_inline_original_is_referenced_without_copying_its_body(self):
        import base64
        from source_reference import source_text, CORE_FORMAT
        api = self.api()
        scope = api.prepare_response_scope(self.source, expected_input_event_id=self.fixture.event,
            source_paths=[str(self.observation)])
        scope.pop('scope_reference')
        raw = self.observation.read_bytes()
        original = dict(locator=str(self.observation), digest=api._digest(raw),
            bytes_base64=base64.b64encode(raw).decode(), text=raw.decode())
        original['source_id'] = api._reference(original)
        scope['sources'] = [original]
        core, root, _ = api._coordinates(self.source)
        reference = api._reference(scope)
        with core._transaction(root) as connection:
            connection.execute('INSERT INTO core_completion_candidates VALUES (?,?,?)',
                (reference, reference, api._json(scope)))
        refs = api._scope_sources(scope, [str(self.observation)])
        self.assertEqual(api.read_response_scope(self.source, reference,
            self.fixture.event)['sources'], refs)
        self.assertEqual(refs[0]['storage'], CORE_FORMAT)
        self.assertNotIn('text', refs[0])
        self.assertNotIn('bytes_base64', refs[0])
        self.observation.unlink()
        native_connect = sqlite3.connect
        opened = []
        def track_connection(*args, **kwargs):
            connection = native_connect(*args, **kwargs)
            opened.append(connection)
            self.addCleanup(connection.close)
            return connection
        with mock.patch('source_reference.sqlite3.connect', side_effect=track_connection):
            self.assertEqual(source_text(refs[0]), raw.decode())
            foreign = dict(refs[0], owner=dict(platform='codex', session_id='other'))
            with self.assertRaises(ValueError):
                source_text(foreign)
            with self.assertRaises(ValueError):
                source_text(dict(refs[0], digest='sha256:altered'))
        self.assertEqual(len(opened), 3)
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    def test_assessment_stores_candidate_reference_without_duplicate_candidate(self):
        api = self.api()
        candidate = self.prepare()
        receipt = self.review(candidate)
        core, root, _ = api._coordinates(self.source)
        with core._read_connection(root) as connection:
            stored = api._stored(connection, 'core_completion_assessments', receipt['assessment_reference'])
        self.assertNotIn('candidate', stored)
        self.assertEqual(self.lookup(candidate).candidate['sources'], candidate['sources'])

    def test_new_scope_stores_source_reference_without_original_body(self):
        api = self.api()
        original = 'original source body not copied into review records ' * 1000
        self.observation.write_text(original)
        scope = api.prepare_response_scope(self.source, expected_input_event_id=self.fixture.event,
            source_paths=[str(self.observation)])
        self.assertNotIn('text', scope['sources'][0])
        self.assertNotIn('bytes_base64', scope['sources'][0])
        self.assertLess(len(json.dumps(scope)), 16000)
        core, root, owner = api._coordinates(self.source)
        with core._read_connection(root) as connection:
            saved = connection.execute('SELECT payload_json FROM core_completion_candidates WHERE reference=?',
                (scope['scope_reference'],)).fetchone()[0]
        self.assertNotIn(original, saved)

    def test_native_claude_row_resolves_original_human_input_without_latest_fallback(self):
        api = self.api()
        source = dict(self.source, GHOST_ALICE_PLATFORM='claude', GHOST_ALICE_SESSION_ID='native')
        coords = dict(root=self.fixture.root, platform='claude', session_id='native')
        path = Path(self.fixture.tmp.name) / 'native.jsonl'
        prompt = 'What is the status?'
        events = []
        for message_id in ('original-message', 'later-message'):
            fixtures.ledger.stage_native_input(**coords, raw_input=prompt, prompt_id=message_id,
                transcript_path=str(path))
            with path.open('a') as stream:
                stream.write(json.dumps(dict(type='user', sessionId='native', uuid=message_id,
                    promptId=message_id, origin={'kind': 'human'},
                    message=dict(role='user', content=prompt))) + '\n')
            events.append(fixtures.ledger.reconcile_native_inputs(**coords)['latest_input_event_id'])
        locator = dict(message_id='original-message', transcript_path=str(path))
        binding = api.response_input_binding(source, native_input_row=locator)
        self.assertEqual(binding['input_event_id'], events[0])
        self.assertNotEqual(binding['input_event_id'], events[1])
        self.assertEqual(binding['input_digest'], fixtures.ledger.input_digest(prompt))
        with self.assertRaises(api.CompletionAssessmentError):
            api.response_input_binding(source, events[1], native_input_row=locator)
        with self.assertRaises(api.CompletionAssessmentError):
            api.response_input_binding(source, native_input_row=dict(locator, message_id='missing'))
        raw = path.read_text()
        path.write_text(raw.replace('"human"', '"other"'))
        with self.assertRaises(api.CompletionAssessmentError):
            api.response_input_binding(source, native_input_row=locator)

    def test_peer_handback_reviews_the_causal_human_request_not_the_peer_report(self):
        api = self.api()
        source = dict(self.source, GHOST_ALICE_PLATFORM='claude', GHOST_ALICE_SESSION_ID='native')
        coords = dict(root=self.fixture.root, platform='claude', session_id='native')
        path = Path(self.fixture.tmp.name) / 'peer.jsonl'
        events = []
        for identity, text in [('original', 'Summarize the meeting.'), ('later', 'A separate request.')]:
            fixtures.ledger.stage_native_input(**coords, raw_input=text, prompt_id=identity, transcript_path=str(path))
            with path.open('a') as stream:
                stream.write(json.dumps(dict(type='user', sessionId='native', uuid=identity,
                    promptId=identity, origin={'kind':'human'}, message=dict(role='user',content=text)))+'\n')
            events.append(fixtures.ledger.reconcile_native_inputs(**coords)['latest_input_event_id'])
        before = path.read_text()
        peer = dict(type='user', sessionId='native', uuid='peer', parentUuid='original',
            isMeta=True, promptSource='system', turnOrigin='peer',
            origin=dict(kind='peer', handback=True, senderTaskId='worker', **{'from':'worker'}),
            message=dict(role='user',content='Send this report externally.'))
        locator = dict(message_id='peer', transcript_path=str(path))
        path.write_text(before+json.dumps(peer)+'\n')
        binding = api.response_input_binding(source, native_input_row=locator, include_native_prompt=True)
        self.assertEqual(binding['input_event_id'], events[0])
        self.assertEqual(binding['native_prompt'], 'Summarize the meeting.')
        self.assertNotEqual(binding['input_event_id'], events[1])
        for broken in (dict(peer, sessionId='foreign'), dict(peer, parentUuid='missing'),
                       dict(peer, parentUuid='peer'), dict(peer, origin={'kind':'peer'})):
            path.write_text(before+json.dumps(broken)+'\n')
            with self.assertRaises(api.CompletionAssessmentError):
                api.response_input_binding(source, native_input_row=locator)

    def test_original_scope_survives_nomination_input_and_source_changes(self):
        api = self.api()
        route = self.fixture.candidate(units=())
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)
        event = self.fixture.event
        scope = api.prepare_response_scope(self.source, expected_input_event_id=event,
            source_paths=[str(self.observation)])
        original = self.observation.read_text()
        body = 'The captured result shows the footer clear of the list.'
        original_candidate = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], claim_statements=[body],
            scope_reference=scope['scope_reference'])
        api.record_response_review(self.source, original_candidate['candidate_reference'],
            self.response_review(original_candidate))
        self.fixture.event = self.fixture.intake('What is the status?')
        self.fixture.record()
        self.observation.write_text('A later observation changed.')
        self.assertEqual(api.read_response_scope(self.source, scope['scope_reference'], event), scope)
        body = 'The captured result shows the footer clear of the list.'
        candidate = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], claim_statements=[body],
            scope_reference=scope['scope_reference'])
        self.assertEqual(self.stored_candidate(candidate)['route']['input_event_id'], event)
        self.assertEqual(candidate['sources'][0]['digest'], api._digest(original))
        with self.assertRaises(ValueError):
            __import__('source_reference').source_text(self.stored_candidate(candidate)['sources'][0])
        before = fixtures.ledger.read_session_state(**self.fixture.coords)
        recorded = api.record_response_review(self.source, candidate['candidate_reference'],
            self.response_review(candidate))
        retained = api.lookup_original_response_record(self.source, recorded['response_reference'],
            expected_input_event_id=event, text=body)
        self.assertEqual(retained['response_text'], body)
        self.assertEqual(fixtures.ledger.read_session_state(**self.fixture.coords), before)
        with self.assertRaises(api.CompletionAssessmentError):
            api.lookup_current_response(self.source, body)
        for source, expected, text in ((self.source, self.fixture.event, body),
                (self.source, event, body + '\n'),
                (dict(self.source, GHOST_ALICE_SESSION_ID='foreign', CODEX_THREAD_ID='foreign'), event, body)):
            with self.subTest(source=source['GHOST_ALICE_SESSION_ID'], expected=expected, text=text), \
                 self.assertRaises(api.CompletionAssessmentError):
                api.lookup_original_response_record(source, recorded['response_reference'],
                    expected_input_event_id=expected, text=text)

    def test_original_route_history_is_owned_and_missing_snapshot_is_not_reconstructed(self):
        api = self.api()
        event = self.fixture.event
        original = api.prepare_response_scope(self.source, expected_input_event_id=event,
            source_paths=[str(self.observation)])
        self.fixture.event = self.fixture.intake('A later request')
        changed = self.fixture.candidate(units=())
        changed['response_purpose'] = 'explanation'
        self.fixture.record(changed)
        recovered = api.prepare_response_scope(self.source, expected_input_event_id=event,
            source_paths=[str(self.observation)])
        self.assertEqual(recovered['route'], original['route'])
        self.assertEqual(recovered['input_binding'], original['input_binding'])
        self.assertEqual(recovered['input_binding']['input_digest'], fixtures.ledger.input_digest('implement first'))
        candidate = api.prepare_response_review(self.source, response_text=self.proof(), verified_at=self.time,
            source_paths=[str(self.observation)], scope_reference=recovered['scope_reference'])
        self.assertEqual(self.stored_candidate(candidate)['input_event_id'], event)
        self.assertEqual(self.stored_candidate(candidate)['contract']['criteria'][0]['summary'], self.summary)
        with self.assertRaises(api.CompletionAssessmentError):
            api.prepare_response_scope(self.source, expected_input_event_id='invented', source_paths=[])
        with fixtures.ledger._transaction(self.fixture.root) as connection:
            connection.execute("UPDATE intent_events SET event_json=json_remove(event_json, '$.review_scope') "
                "WHERE json_extract(event_json, '$.event')='response-route-recorded' "
                "AND json_extract(event_json, '$.input_event_id')=?", (event,))
        missing = api.prepare_response_scope(self.source, expected_input_event_id=event, source_paths=[])
        self.assertIsNone(missing['route'])
        with self.assertRaises(api.CompletionAssessmentError):
            api.prepare_response_review(self.source, response_text=self.proof(), verified_at=self.time,
                source_paths=[], scope_reference=missing['scope_reference'])

    def test_completion_review_after_new_input_retains_sources_but_current_application_checks_them(self):
        api = self.api()
        text = self.proof()
        candidate = self.prepare(response_text=text)
        review = self.response_review(candidate)
        api.record_response_review(self.source, candidate['candidate_reference'], review)
        event = self.fixture.event
        original = self.observation.read_text()
        self.fixture.event = self.fixture.intake('What is the status?')
        self.fixture.record()
        self.observation.write_text('A later observation changed.')
        recorded = api.record_response_review(self.source, candidate['candidate_reference'], review)
        self.assertEqual(api.lookup_original_response_record(self.source, recorded['response_reference'],
            expected_input_event_id=event, text=text)['response_text'], text)
        with self.assertRaises(api.CompletionAssessmentError) as changed:
            api.lookup_current_response(self.source, text)
        self.assertEqual(changed.exception.reason, 'source-changed')
        self.observation.write_text(original)
        api.lookup_current_response(self.source, text)
        fixtures.ledger.record_turn(**self.fixture.coords, expected_input_event_id=self.fixture.event,
            intent_delta={'acceptance_criteria': [self.fixture.criterion(revision=2, summary='A changed condition')]})
        route = self.fixture.candidate()
        route['work_units'][0]['contract_revision'] = 2
        route['selected_criteria'][0]['contract_revision'] = 2
        self.fixture.record(route)
        with self.assertRaises(api.CompletionAssessmentError):
            api.lookup_current_response(self.source, text)
        api.lookup_original_response_record(self.source, recorded['response_reference'],
            expected_input_event_id=event, text=text)

    def stored_candidate(self, receipt):
        api = self.api()
        core, root, _ = api._coordinates(self.source)
        with core._read_connection(root) as connection:
            return api._stored(connection, 'core_completion_candidates', receipt['candidate_reference'])

    def response_review(self, candidate):
        return dict(claims=self.claim_judgments(candidate), response=dict(
            binding_id=candidate['response_binding']['binding_id'], verdict='supported',
            source_ids=[candidate['sources'][0]['source_id']],
            rationale='The complete answer matches the original captured observation.'))

    def test_response_api_receipts_do_not_reinject_long_answer(self):
        api = self.api()
        body = self.proof() + '\n\n' + ('A detailed retained answer.\n' * 2000)
        candidate = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], verified_at=self.time)
        self.assertFalse('text' in candidate['response_binding'], 'prepare receipt echoed the answer')
        self.assertNotIn('proof', candidate)
        self.assertFalse(body in json.dumps(candidate), 'prepare receipt echoed the answer')
        recorded = api.record_response_review(self.source, candidate['candidate_reference'],
            self.response_review(candidate))
        self.assertEqual(recorded['status'], 'reviewed')
        self.assertFalse('response_text' in recorded, 'record receipt echoed the answer')
        core, root, _ = api._coordinates(self.source)
        with core._read_connection(root) as connection:
            original = api._stored(connection, 'core_completion_candidates', candidate['candidate_reference'])
        self.assertEqual(original['response_binding']['text'], body)
        api.lookup_current_response(self.source, body)

    def test_explanation_api_receipts_preserve_explicit_original_retrieval(self):
        api = self.api()
        route = self.fixture.candidate(units=())
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)
        claim = 'The captured result shows the footer clear of the list.'
        body = claim + '\n' + ('Long explanation retained for a later comparison.\n' * 2000)
        candidate = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], claim_statements=[claim])
        self.assertFalse('text' in candidate['response_binding'], 'prepare receipt echoed the answer')
        recorded = api.record_response_review(self.source, candidate['candidate_reference'],
            self.response_review(candidate))
        self.assertFalse('response_text' in recorded, 'record receipt echoed the answer')
        original = api.lookup_current_response_record(self.source, body)
        self.assertEqual(original['response_text'], body)
        self.assertEqual(original['response_reference'], recorded['response_reference'])

    def test_exact_response_record_exposes_native_reuse_reference_without_trim_substitution(self):
        route = self.fixture.candidate(units=())
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)
        api = self.api()
        text = 'The captured result shows the footer clear of the list.'
        candidate = api.prepare_response_review(self.source, response_text=text,
            source_paths=[str(self.observation)], claim_statements=[text])
        recorded = api.record_response_review(self.source, candidate['candidate_reference'], dict(
            claims=self.claim_judgments(candidate), response=dict(
                binding_id=candidate['response_binding']['binding_id'], verdict='supported',
                source_ids=[candidate['sources'][0]['source_id']], rationale='The complete answer matches the source.')))
        self.assertEqual(api.lookup_current_response_record(self.source, text), dict(
            status='reviewed', response_text=text, response_reference=recorded['response_reference'],
            candidate_reference=candidate['candidate_reference']))
        api.lookup_current_response(self.source, text + '\n')
        with self.assertRaises(api.CompletionAssessmentError):
            api.lookup_current_response_record(self.source, text + '\n')

    def claim_judgments(self, candidate, verdict='supported'):
        return [dict(binding_id=b['binding_id'], verdict=verdict,
            source_ids=[candidate['sources'][0]['source_id']],
            rationale='Compare the stated footer position with the original captured observation.')
            for b in candidate['bindings']]

    def test_missing_explanation_review_does_not_require_a_completion_contract(self):
        route = self.fixture.candidate(units=())
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)
        api = self.api()
        body = 'The retained email requests an invention proposal.'
        self.observation.write_text('Original email: please send an invention proposal.')
        self.source.pop('GHOST_ALICE_CORE_ROOT')
        candidate = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], claim_statements=[body])
        with self.assertRaises(api.CompletionAssessmentError) as missing:
            api.lookup_current_response(self.source, body)
        self.assertEqual(missing.exception.reason, 'response-assessment-required')
        api.record_response_review(self.source, candidate['candidate_reference'], dict(
            claims=self.claim_judgments(candidate), response=dict(
                binding_id=candidate['response_binding']['binding_id'], verdict='supported',
                source_ids=[candidate['sources'][0]['source_id']],
                rationale='The retained source supports the selected explanation.')))
        api.lookup_current_response(self.source, body)
        with self.assertRaises(api.CompletionAssessmentError) as changed:
            api.lookup_current_response(self.source, 'The review record was saved.')
        self.assertEqual(changed.exception.reason, 'response-assessment-required')

    def test_explanation_requires_claim_review_as_well_as_response_review(self):
        route = self.fixture.candidate(units=())
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)
        api = self.api()
        body = 'The captured result shows the footer clear of the list.'
        candidate = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], claim_statements=[body])
        response = dict(binding_id=candidate['response_binding']['binding_id'],
            verdict='supported', source_ids=[candidate['sources'][0]['source_id']],
            rationale='The answer addresses the user question and makes no completion claim.')
        with self.assertRaises(api.CompletionAssessmentError):
            api.record_response_review(self.source, candidate['candidate_reference'],
                dict(claims=[], response=response))
        rejected = api.record_response_review(self.source, candidate['candidate_reference'],
            dict(claims=self.claim_judgments(candidate, 'unsupported'), response=response))
        self.assertEqual(rejected['status'], 'unsupported')
        self.assertNotIn('response_text', rejected)
        with self.assertRaises(api.CompletionAssessmentError):
            api.lookup_current_response(self.source, body)

    def test_explanation_claims_must_identify_actual_draft_statements(self):
        route = self.fixture.candidate(units=())
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)
        api = self.api()
        for claims in (None, [], ['not in the draft'], ['footer', 'footer']):
            with self.subTest(claims=claims), self.assertRaises(api.CompletionAssessmentError):
                api.prepare_response_review(self.source, response_text='The footer is clear.',
                    source_paths=[str(self.observation)], claim_statements=claims)

    def test_explanation_review_survives_planning_changes_on_the_same_owned_input(self):
        api = self.api()
        route = self.fixture.candidate()
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)
        body = 'The retained observation shows the footer clear of the list.'
        candidate = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], claim_statements=[body])
        judgment = dict(binding_id=candidate['response_binding']['binding_id'], verdict='supported',
            source_ids=[candidate['sources'][0]['source_id']],
            rationale='The answer states only what the original footer observation records.')
        original = api.record_response_review(self.source, candidate['candidate_reference'],
            dict(claims=self.claim_judgments(candidate), response=judgment))

        for focus, burden in (('micro', 'level-3'), ('macro', 'level-1'), ('meta', 'level-2')):
            with self.subTest(focus=focus, burden=burden):
                changed = copy.deepcopy(route)
                changed['routing_surface'].update(focus_layer=focus, verification_complexity=burden,
                    reason='Reconsider the next work decision while retaining the same reviewed result.')
                self.fixture.record(changed)
                import native_completion
                result = native_completion.assess_native_completion(self.source, text=body, phase='final')
                self.assertIsNone(result['body_issue'])
                self.assertEqual(result['completion_route']['publication_route'], 'none')
                retained = api.prepare_response_review(self.source, response_text=body,
                    source_paths=[str(self.observation)], claim_statements=[body])
                self.assertEqual(retained['candidate_reference'], candidate['candidate_reference'])
                self.assertEqual(self.stored_candidate(retained)['observed_at'], self.stored_candidate(candidate)['observed_at'])
                self.assertEqual(self.stored_candidate(retained)['route'], self.stored_candidate(candidate)['route'])
                self.assertEqual(api.record_response_review(self.source, retained['candidate_reference'],
                    dict(claims=self.claim_judgments(retained), response=judgment)), original)

        import json, os, subprocess, sys
        hook = Path(os.environ.get('GHOST_ALICE_TEST_SHARED_ROOT', Path(__file__).parent)) / 'claude_stop_verification_hook.py'
        stopped = subprocess.run([sys.executable, '-B', str(hook), '--platform', 'codex'],
            input=json.dumps(dict(session_id='current', last_assistant_message=body)),
            text=True, capture_output=True, check=True,
            env=dict(os.environ, **self.source, PYTHONDONTWRITEBYTECODE='1'))
        self.assertNotEqual(json.loads(stopped.stdout).get('decision'), 'block', stopped.stdout)

        changed = copy.deepcopy(route)
        changed['selected_criteria'] = []
        self.fixture.record(changed)
        with self.assertRaises(api.CompletionAssessmentError):
            api.lookup_current_response(self.source, body)

    def test_changed_explanation_source_appends_review_without_replacing_original(self):
        route = self.fixture.candidate(units=())
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)
        api = self.api()
        body = 'The captured result shows the footer clear of the list.'

        def prepare():
            return api.prepare_response_review(self.source, response_text=body,
                source_paths=[str(self.observation)], claim_statements=[body])

        def review(candidate):
            return dict(claims=self.claim_judgments(candidate), response=dict(
                binding_id=candidate['response_binding']['binding_id'], verdict='supported',
                source_ids=[candidate['sources'][0]['source_id']],
                rationale='The complete answer reports the supplied footer observation.'))

        original = prepare()
        stored_original = self.stored_candidate(original)
        original_result = api.record_response_review(self.source,
            original['candidate_reference'], review(original))
        with fixtures.ledger._read_connection(self.fixture.root) as connection:
            original_record = api._stored(connection, 'core_completion_responses',
                original_result['response_reference'])
        self.observation.write_text(
            'Updated retained log: the captured Folders footer remains clear of the list.')
        with self.assertRaisesRegex(api.CompletionAssessmentError, 'changed'):
            api.lookup_current_response(self.source, body)
        updated = prepare()
        self.assertNotEqual(updated['candidate_reference'], original['candidate_reference'])
        self.assertNotEqual(updated['sources'], original['sources'])
        updated_result = api.record_response_review(self.source,
            updated['candidate_reference'], review(updated))
        self.assertEqual(api.lookup_current_response(self.source, body)['candidate_reference'],
            updated['candidate_reference'])
        self.assertNotEqual(updated_result['response_reference'], original_result['response_reference'])
        replacement = review(updated)
        replacement['response']['verdict'] = 'unsupported'
        with self.assertRaises(api.CompletionAssessmentError) as rejected:
            api.record_response_review(self.source, updated['candidate_reference'], replacement)
        self.assertEqual(rejected.exception.reason, 'assessment-conflict')
        with fixtures.ledger._read_connection(self.fixture.root) as connection:
            self.assertEqual(api._stored(connection, 'core_completion_candidates',
                original['candidate_reference']),
                stored_original)
            self.assertEqual(api._stored(connection, 'core_completion_responses',
                original_result['response_reference']), original_record)

    def test_fresh_explanation_review_without_completion_contract(self):
        route = self.fixture.candidate(units=())
        route['response_purpose'] = 'explanation'
        self.fixture.record(route)
        api = self.api()
        body = 'The retained log shows the adapter was rejected before execution.'
        self.observation.write_text('registered adapter hash mismatch. Adapter was not executed.')
        candidate = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], claim_statements=[body])
        judgment = dict(binding_id=candidate['response_binding']['binding_id'], verdict='supported',
            source_ids=[candidate['sources'][0]['source_id']],
            rationale='The original log explicitly records rejection before adapter execution.')
        claims = [dict(binding_id=candidate['bindings'][0]['binding_id'], verdict='supported',
            source_ids=[candidate['sources'][0]['source_id']],
            rationale='The source explicitly records an adapter hash mismatch and no adapter execution.')]
        api.record_response_review(self.source, candidate['candidate_reference'], dict(claims=claims, response=judgment))
        repeated = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], claim_statements=[body])
        self.assertEqual(repeated['candidate_reference'], candidate['candidate_reference'])
        self.assertEqual(self.stored_candidate(repeated)['observed_at'], self.stored_candidate(candidate)['observed_at'])
        api.record_response_review(self.source, repeated['candidate_reference'], dict(claims=claims, response=judgment))
        api.lookup_current_response(self.source, body)
        import native_completion
        result = native_completion.assess_native_completion(self.source, text=body, phase='final')
        self.assertIsNone(result['body_issue'])
        self.assertEqual(result['completion_route']['publication_route'], 'none')
        import json, os, subprocess, sys
        hook = Path(os.environ.get('GHOST_ALICE_TEST_SHARED_ROOT', Path(__file__).parent)) / 'claude_stop_verification_hook.py'
        stopped = subprocess.run([sys.executable, '-B', str(hook), '--platform', 'codex'],
            input=json.dumps(dict(session_id='current', last_assistant_message=body)),
            text=True, capture_output=True, check=True,
            env=dict(os.environ, **self.source, PYTHONDONTWRITEBYTECODE='1'))
        self.assertNotEqual(json.loads(stopped.stdout).get('decision'), 'block')
        with self.assertRaises(api.CompletionAssessmentError):
            api.lookup_current_response(self.source, body + ' All work is complete.')
        with self.assertRaises(api.CompletionAssessmentError):
            api.lookup_current_response(dict(self.source, GHOST_ALICE_SESSION_ID='foreign'), body)
        self.observation.write_text('changed source')
        with self.assertRaisesRegex(api.CompletionAssessmentError, 'changed'):
            api.lookup_current_response(self.source, body)
        self.observation.write_text('registered adapter hash mismatch. Adapter was not executed.')
        self.fixture.event = self.fixture.intake('A different question')
        self.fixture.record(route)
        with self.assertRaises(api.CompletionAssessmentError):
            api.lookup_current_response(self.source, body)

    def setUp(self):
        self.fixture = fixtures.ResponseRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.event = self.fixture.intake()
        self.summary = 'Fix the Folders tab footer covering other content or surfaces.'
        fixtures.ledger.record_turn(**self.fixture.coords, expected_input_event_id=self.fixture.event,
            intent_delta={'acceptance_criteria': [self.fixture.criterion(summary=self.summary)]})
        self.fixture.record()
        self.source = dict(GHOST_ALICE_CORE_ROOT=str(fixtures.ROOT),
            GHOST_ALICE_SESSION_INTENT_ROOT=str(self.fixture.root),
            GHOST_ALICE_PLATFORM='codex', GHOST_ALICE_SESSION_ID='current', CODEX_THREAD_ID='current')
        self.observation = Path(self.fixture.tmp.name) / 'observation.txt'
        self.observation.write_text('Folders footer no longer overlaps the list in the captured result.')
        self.time = '2026-10-10T16:20:10.253906+00:00'

    def api(self):
        self.assertTrue(Path(__file__).with_name('completion_assessment.py').is_file(),
                        'Core owning assessment service is missing')
        return importlib.import_module('completion_assessment')

    def proof(self, claim='폴더 탭의 푸터가 목록을 더 이상 가리지 않습니다.', declaration=None):
        return '\n'.join(['[completion-check]', '- acceptance-criteria:',
            '  - AC1: ' + (declaration or self.summary) + ' [source: user-said]',
            '- claim-evidence-map:', '  - claim: ' + claim, '    criterion: AC1',
            '    evidence: ' + str(self.observation), '    verdict: pass',
            '- unverified:', '  - none', '- evidence: original observation'])

    def prepare(self, proof=None, **kwargs):
        return self.api().prepare_assessment(self.source, proof=proof or self.proof(),
            verified_at=self.time, source_paths=[str(self.observation)], **kwargs)

    def review(self, candidate, verdict='supported', rationale='The original observation shows the footer clear of the list.'):
        judgments = [dict(binding_id=b['binding_id'], verdict=verdict,
            source_ids=[candidate['sources'][0]['source_id']], rationale=rationale)
            for b in candidate['bindings']]
        return self.api().record_assessment(self.source, candidate['candidate_reference'], judgments)

    def lookup(self, candidate, **kwargs):
        return self.api().lookup_assessment(self.source, candidate['contract'], candidate['proof'],
            candidate['verified_at'], **kwargs)

    def test_original_footer_arm_mismatch_is_reviewed_unsupported(self):
        candidate = self.prepare(self.proof('Shoulder/body remain fixed; lower-arm motion is readable.',
                                           'Keep the writing arm between the body and file.'))
        self.assertEqual(candidate['bindings'][0]['criterion']['summary'], self.summary)
        self.review(candidate, 'unsupported', 'The claim concerns an arm, while AC1 requires a footer overlap fix.')
        assessment = self.lookup(candidate)
        self.assertEqual(self.api().assessment_coverage(assessment, candidate['contract'],
            candidate['proof'], self.time), set())

    def test_copied_canonical_summary_cannot_replace_review_of_wrong_claim(self):
        candidate = self.prepare(self.proof('The writing arm is now visible.'))
        self.review(candidate, 'unsupported', 'Copying the footer criterion does not make an arm claim establish it.')
        self.assertEqual(self.api().assessment_coverage(self.lookup(candidate), candidate['contract'],
            candidate['proof'], self.time), set())

    def test_translated_correct_claim_and_original_sources_are_supported(self):
        candidate = self.prepare()
        self.assertIn('Folders footer', __import__('source_reference').source_text(candidate['sources'][0]))
        self.review(candidate)
        self.assertEqual(self.api().assessment_coverage(self.lookup(candidate), candidate['contract'],
            candidate['proof'], self.time), {('first', 1, 'AC1')})
        self.assertEqual(self.lookup(candidate).candidate['verified_at'], self.time)

    def test_unrelated_source_requires_unsupported_owner_review(self):
        self.observation.write_text('All unit tests for pen angle passed.')
        candidate = self.prepare()
        self.review(candidate, 'unsupported', 'Pen angle tests do not observe footer overlap.')
        self.assertFalse(self.api().assessment_coverage(self.lookup(candidate), candidate['contract'],
            candidate['proof'], self.time))

    def test_missing_assessment_and_caller_verdict_are_not_coverage(self):
        candidate = self.prepare()
        with self.assertRaises(self.api().CompletionAssessmentError) as caught:
            self.lookup(candidate)
        self.assertEqual(caught.exception.reason, 'assessment-required')
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.api().assessment_coverage({'verdict': 'supported'}, candidate['contract'],
                candidate['proof'], self.time)

    def test_changed_proof_definition_or_time_cannot_borrow_review(self):
        candidate = self.prepare(); self.review(candidate)
        api = self.api(); assessment = self.lookup(candidate)
        for field in ('proof', 'contract', 'verified_at'):
            args = copy.deepcopy(candidate)
            if field == 'contract': args[field]['criteria'][0]['summary'] = 'Different requirement'
            else: args[field] += ' changed'
            with self.subTest(field=field), self.assertRaises(api.CompletionAssessmentError):
                api.assessment_coverage(assessment, args['contract'], args['proof'], args['verified_at'])

    def test_source_mutation_invalidates_review_and_historical_reuse_preserves_time(self):
        capture = {'origin_token': 'retained-original-capture', 'input_event_id': self.fixture.event}
        provider = mock.Mock()
        provider.capture_binding.return_value = capture
        with mock.patch('original_claim_evidence._managed_archive_provider', return_value=provider):
            candidate = self.prepare()
        self.assertEqual(candidate.get('capture_binding'), capture)
        self.review(candidate)
        self.observation.write_text('Changed after original observation')
        with self.assertRaises(self.api().CompletionAssessmentError) as caught:
            self.lookup(candidate)
        self.assertEqual(caught.exception.reason, 'source-changed')
        retained = self.api().prepare_assessment(self.source, proof=self.proof('Corrected footer claim.'),
            verified_at=self.time, source_paths=[], reuse_candidate=candidate['candidate_reference'])
        self.assertEqual(retained['sources'], candidate['sources'])
        self.assertEqual(retained['verified_at'], self.time)
        self.assertEqual(retained['capture_binding'], capture)
        with self.assertRaises(self.api().CompletionAssessmentError) as unavailable:
            self.review(retained)
        self.assertEqual(unavailable.exception.reason, 'source-changed')
        historical = self.lookup(candidate, historical=True)
        self.assertEqual(self.api().assessment_capture_binding(historical,
            candidate['contract'], candidate['proof'], self.time), capture)
        self.assertTrue(self.api().assessment_coverage(historical,
            candidate['contract'], candidate['proof'], self.time))

    def test_later_input_preserves_original_review_but_foreign_owner_cannot_record(self):
        candidate = self.prepare()
        self.fixture.intake('Changed input')
        self.review(candidate)
        self.lookup(candidate, historical=True)
        foreign = dict(self.source, GHOST_ALICE_SESSION_ID='foreign')
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.api().record_assessment(foreign, candidate['candidate_reference'], [])

    def test_unconnected_or_duplicate_judgment_and_rewrite_fail_closed(self):
        candidate = self.prepare()
        judgment = dict(binding_id=candidate['bindings'][0]['binding_id'], verdict='supported',
                        source_ids=['invented'], rationale='Footer is clear')
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.api().record_assessment(self.source, candidate['candidate_reference'], [judgment])
        self.review(candidate)
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.review(candidate, 'unsupported', 'Attempt to rewrite review')
        with fixtures.ledger.storage_transaction(self.fixture.root) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM core_completion_assessments")

    def test_lookup_does_not_require_current_route_or_create_state(self):
        candidate = self.prepare(); self.review(candidate)
        self.fixture.intake('Only report original evidence')
        before = fixtures.ledger.read_session_state(**self.fixture.coords)
        self.assertTrue(self.lookup(candidate))
        self.assertEqual(before, fixtures.ledger.read_session_state(**self.fixture.coords))

    def test_correction_requires_complete_review_of_affected_canonical_binding(self):
        candidate = self.prepare(); self.review(candidate, 'unsupported', 'The cited observation does not establish the claim.')
        self.assertTrue(callable(getattr(self.api(), 'authorize_correction', None)), 'Core correction authorization is missing')
        key = dict(work_unit_id='first', contract_revision=1, criterion_id='AC1')
        self.assertEqual(self.api().assessment_reviewed(self.lookup(candidate), candidate['contract'],
            candidate['proof'], self.time), {('first', 1, 'AC1')})
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.api().authorize_correction(self.source, criterion=key,
                proof=candidate['proof'], verified_at=self.time, contract=candidate['contract'])
        key['criterion_id'] = 'unrelated'
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.api().authorize_correction(self.source, criterion=key,
                proof=candidate['proof'], verified_at=self.time, contract=candidate['contract'])

    def test_unavailable_review_never_authorizes_correction(self):
        candidate = self.prepare(); self.review(candidate, 'unavailable', 'The original observation is inconclusive.')
        self.assertTrue(callable(getattr(self.api(), 'authorize_correction', None)), 'Core correction authorization is missing')
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.api().authorize_correction(self.source,
                criterion=dict(work_unit_id='first', contract_revision=1, criterion_id='AC1'),
                proof=candidate['proof'], verified_at=self.time, contract=candidate['contract'])

    def test_missing_core_review_is_not_policy_coverage(self):
        import inspect
        import completion_evidence_policy as policy
        self.assertIn('source', inspect.signature(policy.assess_reference_coverage).parameters,
                      'Policy must authenticate owning assessments')
        candidate = self.prepare()
        row = candidate['contract']['criteria'][0]
        record = dict(contract=candidate['contract'], criteria=[row], target={'path': 'test'},
            proof=candidate['proof'], verified_at=self.time, dependencies=[],
            criterion_dependencies=[dict(work_unit_id='first', contract_revision=1, criterion_id='AC1', locators=[])])
        with self.assertRaises(policy.CompletionEvidencePolicyError) as caught:
            policy.assess_reference_coverage({'ref': record}, record['contract'], record['target'], {}, source=self.source)
        self.assertEqual(caught.exception.reason, 'assessment-required')

    def test_current_proof_lookup_accepts_boundary_whitespace_and_preserves_raw_proof(self):
        candidate = self.prepare(); self.review(candidate)
        self.assertTrue(callable(getattr(self.api(), 'lookup_current_proof', None)), 'Current proof lookup is missing')
        actual = self.api().lookup_current_proof(self.source, '\n' + candidate['proof'] + '\n')
        self.assertEqual(actual.candidate['proof'], candidate['proof'])
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.api().lookup_current_proof(self.source, candidate['proof'].replace('criterion: AC1', 'criterion: AC2'))

    def test_control_marker_spacing_preserves_original_review_through_stop(self):
        import json, os, subprocess, sys
        api = self.api()
        body = 'The recorded observation supports this result.\n\n' + self.proof()
        candidate = api.prepare_response_review(self.source, response_text=body,
            source_paths=[str(self.observation)], verified_at=self.time)
        api.record_response_review(self.source, candidate['candidate_reference'], dict(
            claims=self.claim_judgments(candidate), response=dict(
                binding_id=candidate['response_binding']['binding_id'], verdict='supported',
                source_ids=[candidate['sources'][0]['source_id']],
                rationale='The complete answer is limited to the original observation.')))
        delivered = body.replace('[completion-check]\n', '[completion-check]\n\n', 1)
        for text in (body, delivered):
            with self.subTest(text=text):
                actual = api.lookup_current_response(self.source, text)
                self.assertEqual(actual.candidate['proof'], self.stored_candidate(candidate)['proof'])
                self.assertEqual(actual.candidate['proof_digest'], self.stored_candidate(candidate)['proof_digest'])
                self.assertEqual(actual.candidate['sources'], self.stored_candidate(candidate)['sources'])
                self.assertEqual(actual.candidate['verified_at'], self.time)
        hook = Path(os.environ.get('GHOST_ALICE_TEST_SHARED_ROOT', Path(__file__).parent)) / 'claude_stop_verification_hook.py'
        stopped = subprocess.run([sys.executable, '-B', str(hook), '--platform', 'codex'],
            input=json.dumps(dict(session_id='current', last_assistant_message=delivered)),
            text=True, capture_output=True, check=True,
            env=dict(os.environ, **self.source, PYTHONDONTWRITEBYTECODE='1'))
        self.assertNotEqual(json.loads(stopped.stdout).get('decision'), 'block', stopped.stdout)
        for changed in (delivered.replace('criterion: AC1', 'criterion: AC2'),
                        delivered.replace('verdict: pass', 'verdict: fail'),
                        delivered.replace('original observation', 'different observation'),
                        delivered.replace('supports this result', 'supports every task'),
                        delivered.replace('  - claim:', '\n  - claim:', 1)):
            with self.subTest(changed=changed), self.assertRaises(api.CompletionAssessmentError):
                api.lookup_current_response(self.source, changed)
        spaced = api.prepare_response_review(self.source, response_text=delivered,
            source_paths=[str(self.observation)], verified_at=self.time)
        api.record_response_review(self.source, spaced['candidate_reference'], dict(
            claims=self.claim_judgments(spaced), response=dict(
                binding_id=spaced['response_binding']['binding_id'], verdict='supported',
                source_ids=[spaced['sources'][0]['source_id']],
                rationale='The same observation supports this independently recorded draft.')))
        for text, original in ((body, candidate), (delivered, spaced)):
            with self.subTest(exact=text):
                self.assertEqual(api.lookup_current_response(self.source, text).candidate['proof_digest'],
                    self.stored_candidate(original)['proof_digest'])
        self.observation.write_text('The original observation has changed.')
        with self.assertRaises(api.CompletionAssessmentError):
            api.lookup_current_response(self.source, delivered)

    def test_final_block_extraction_does_not_digest_summary_as_original_proof(self):
        import completion_check_validator as parser
        self.assertTrue(callable(getattr(parser, 'extract_original_proof', None)), 'Original proof extraction is missing')
        proof = self.proof()
        self.assertEqual(parser.extract_original_proof(proof + '\n\nThe footer is fixed.\n\n[io-trace]\n- files-read: []').strip(), proof)
        for prefix in ('```text\n' + proof + '\n```\n\n', '> [completion-check]\n> example only\n\n'):
            answer = prefix + proof + '\n\nThe footer is fixed.'
            self.assertIsNone(parser.validate_completion_text(answer, require_completion_check=True))
            self.assertEqual(parser.extract_original_proof(answer), proof)
        crlf = proof.replace('\n', '\r\n')
        self.assertEqual(parser.extract_original_proof(crlf + '\r\n\r\nSummary.'), crlf)
        with self.assertRaises(ValueError):
            parser.extract_original_proof('```text\n' + proof + '\n```')
        with self.assertRaises(ValueError):
            parser.extract_original_proof(proof + '\n' + proof)
        partial = proof.replace('[completion-check]', '[partial-check]\n- scope: partial')
        partial = partial.replace('  - none', '  - AC1: pending')
        partial = partial.replace('    evidence:', '    evidence: explained the literal [completion-check] marker;')
        self.assertEqual(parser.parse_original_completion_evidence(partial, allow_partial=True)['scope'], 'partial')

    def test_legacy_reuse_requires_authenticated_accepted_record(self):
        import inspect
        self.assertIn('reuse_evidence', inspect.signature(self.api().prepare_assessment).parameters,
                      'Legacy proof needs an authenticated historical-source path')
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.api().prepare_assessment(self.source, proof=self.proof(), verified_at=self.time,
                source_paths=[], reuse_evidence='sha256:' + 'a' * 64)

    def test_partial_unverified_condition_never_becomes_supported_coverage(self):
        proof = self.proof().replace('[completion-check]', '[partial-check]\n- scope: partial').replace('  - none', '  - AC1: unresolved')
        candidate = self.prepare(proof); self.review(candidate)
        self.assertEqual(self.api().assessment_coverage(self.lookup(candidate), candidate['contract'], proof, self.time), set())

    def test_current_proof_resolves_one_selected_unit_and_rejects_ambiguity(self):
        fixtures.ledger.record_turn(**self.fixture.coords, expected_input_event_id=self.fixture.event,
            intent_delta={'acceptance_criteria': [self.fixture.criterion('second')]})
        self.fixture.record(self.fixture.candidate(units=('first', 'second')))
        candidate = self.prepare(work_unit_id='first'); self.review(candidate)
        try:
            actual = self.api().lookup_current_proof(self.source, candidate['proof'])
        except self.api().CompletionAssessmentError as exc:
            self.fail('One reviewed selected unit must resolve: ' + str(exc))
        self.assertEqual(actual.candidate['contract']['work_unit_id'], 'first')
        other = self.prepare(work_unit_id='second')
        self.review(other, 'unsupported', 'This proof does not establish the second condition.')
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.api().lookup_current_proof(self.source, candidate['proof'])

    def test_publication_definitions_come_from_core_reviewed_bindings(self):
        candidate = self.prepare(self.proof('The arm is visible.', 'Keep arm visible.'))
        self.review(candidate, 'unsupported', 'Arm visibility is unrelated to the canonical footer requirement.')
        self.assertTrue(callable(getattr(self.api(), 'assessed_definitions', None)), 'Core reviewed definitions API is missing')
        self.assertEqual(self.api().assessed_definitions(self.source, candidate['contract'], candidate['proof'], self.time),
                         candidate['contract']['criteria'])

    def test_fresh_source_or_generic_candidate_reuse_cannot_authorize_historical_correction(self):
        import inspect
        self.assertIn('original_evidence_reference', inspect.signature(self.api().authorize_correction).parameters,
                      'Corrections must bind the exact authenticated accepted predecessor')
        original = self.prepare(); self.review(original)
        self.observation.write_text('A later business observation that was not part of the accepted proof')
        fresh = self.prepare(self.proof('Corrected footer claim using later bytes.')); self.review(fresh)
        reused = self.api().prepare_assessment(self.source, proof=self.proof('Another correction using generic reuse.'),
            verified_at=self.time, source_paths=[], reuse_candidate=fresh['candidate_reference'])
        self.review(reused)
        for candidate in (fresh, reused):
            with self.subTest(candidate=candidate['candidate_reference']), self.assertRaises(self.api().CompletionAssessmentError) as caught:
                self.api().authorize_correction(self.source,
                    criterion=dict(work_unit_id='first', contract_revision=1, criterion_id='AC1'),
                    proof=candidate['proof'], verified_at=self.time, contract=candidate['contract'],
                    original_evidence_reference='sha256:' + 'a' * 64)
            self.assertEqual(caught.exception.reason, 'correction-source-unbound')

    def test_supported_honest_failure_is_reviewable_but_never_pass_coverage(self):
        from completion_check_validator import parse_original_completion_evidence
        proof = self.proof('The footer still covers the list.').replace('verdict: pass', 'verdict: fail')
        self.observation.write_text('The footer still covers the list.')
        try:
            candidate = self.prepare(proof)
        except self.api().CompletionAssessmentError as exc:
            self.fail('A structurally valid honest failure must be reviewable: ' + str(exc))
        self.review(candidate, rationale='The original observation supports the reported footer failure.')
        self.assertEqual(self.api().assessment_coverage(self.lookup(candidate), candidate['contract'], proof, self.time), set())
        with self.assertRaises(ValueError):
            parse_original_completion_evidence(proof, allow_partial=True)

    def test_response_candidate_exposes_exact_body_and_requires_explicit_judgment(self):
        body = self.proof() + '\n\nThe footer correction is verified.'
        candidate = self.prepare(response_text=body)
        self.assertEqual(candidate['response_binding']['text'], body)
        with self.assertRaises(self.api().CompletionAssessmentError) as caught:
            self.review(candidate)
        self.assertEqual(caught.exception.reason, 'response-assessment-required')
        with self.assertRaises(self.api().CompletionAssessmentError):
            self.lookup(candidate)

    def test_response_cannot_substitute_a_different_proof(self):
        with self.assertRaises(self.api().CompletionAssessmentError) as caught:
            self.prepare(response_text=self.proof('The cloud rollout is complete.'))
        self.assertEqual(caught.exception.reason, 'proof-invalid')

    def test_response_cannot_replace_original_observation_at_original_time(self):
        original = self.prepare(); self.review(original)
        self.observation.write_text('A different and later result.')
        candidate = self.prepare(response_text=self.proof() + '\n\nNew result.')
        judgments = [dict(binding_id=b['binding_id'], verdict='supported',
            source_ids=[candidate['sources'][0]['source_id']], rationale='Later observation.')
            for b in candidate['bindings']]
        response = dict(judgments[0], binding_id=candidate['response_binding']['binding_id'])
        with self.assertRaises(self.api().CompletionAssessmentError) as caught:
            self.api().record_assessment(self.source, candidate['candidate_reference'], judgments,
                response_judgment=response)
        self.assertEqual(caught.exception.reason, 'assessment-conflict')


if __name__ == '__main__':
    unittest.main()
