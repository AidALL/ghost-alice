"""Authenticate model-selected context dispositions without retaining source bodies.

The responding model decides use. Core binds that decision to native input and
original records; request selection preserves anything outside that binding.
Dependencies: Python 3.11+ standard library and the installed Core ledger.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import completion_assessment as core_api
from claude_context_adapter import NativeContextRejected, SignedPrefixSelectionUnsupported, ToolInputSelectionUnsupported
from native_transcript import codex_transcript_path
from source_reference import FORMAT as FILE_SOURCE_FORMAT, SourceReferenceError, reference_source, source_text, verify_source
from text_lines import committed_byte_lines, committed_records, line_number


class ContextSelectionRejected(ValueError):
    """The native input or source binding cannot authorize substitution."""


@dataclass(frozen=True)
class RequestSelection:
    request: object
    state: str
    reason: str
    observations: tuple = ()


def request_support_reason(request, platform='codex'):
    """A state key never establishes a supported stateless full prefix."""
    if platform == 'claude' and isinstance(request, dict) and 'compaction' in request:
        # The host owns this separate summary call. Adding context_management
        # from a retained tool-result declaration would invalidate the API call.
        return 'signed-compaction-request-owned-by-host'
    if platform == 'codex' and isinstance(request, dict) and any(
        key in request for key in ('previous_response_id', 'conversation')):
        return 'stateful-request-selection-unsupported'
    return None


def fingerprint(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _item(value):
    result = copy.deepcopy(value)
    result.pop('internal_chat_message_metadata_passthrough', None)
    return result


def _native(path):
    return {number: _item(row['payload']) for number, row in committed_records(Path(path).read_bytes()).items()
            if row.get('type') == 'response_item'}


def _input_item(items):
    return next((item for item in reversed(items) if isinstance(item, dict)
                 and item.get('type') == 'message' and item.get('role') == 'user'), None)


def _input_text(item):
    content = item.get('content') if isinstance(item, dict) else None
    if not isinstance(content, list) or not content or not all(isinstance(b, dict)
        and b.get('type') == 'input_text' and isinstance(b.get('text'), str) for b in content):
        raise ContextSelectionRejected('Native input text is unclassified')
    return ''.join(b['text'] for b in content)


def _block(item, index):
    if item.get('type') not in {'custom_tool_call_output', 'function_call_output'}:
        raise ContextSelectionRejected('Only paired native tool output can be selected')
    output = item.get('output')
    if isinstance(output, str) and index == 0:
        return output
    if isinstance(output, list) and type(index) is int and 0 <= index < len(output):
        block = output[index]
        if (isinstance(block, dict) and block.get('type') == 'input_text'
            and isinstance(block.get('text'), str) and 'signature' not in block):
            return block
    raise ContextSelectionRejected('Selected native output block is unavailable')


def _selected_value(item, choice, platform='codex'):
    if platform == 'claude':
        import claude_context_adapter as adapter
        return adapter.selected_value(item, dict(choice, kind=choice.get('kind', 'output')))
    kind, index = choice.get('kind', 'output'), choice['block_index']
    if kind == 'output':
        return _block(item, index)
    if kind in {'assistant', 'message'}:
        content = item.get('content')
        roles = {'assistant'} if kind == 'assistant' else {'user', 'assistant', 'developer', 'system'}
        if (item.get('type') == 'message' and item.get('role') in roles
            and isinstance(content, list) and type(index) is int and 0 <= index < len(content)):
            block = content[index]
            if (isinstance(block, dict) and block.get('type') in {'input_text', 'output_text'}
                and isinstance(block.get('text'), str) and 'signature' not in block):
                return block
    if kind == 'exchange' and index == 0 and item.get('type') in {'custom_tool_call_output', 'function_call_output'}:
        output = item.get('output')
        if isinstance(output, str) or (isinstance(output, list) and output and all(
            isinstance(b, dict) and 'signature' not in b and ((b.get('type') == 'input_text' and isinstance(b.get('text'), str))
            or (b.get('type') == 'input_image' and isinstance(b.get('image_url'), str))) for b in output)):
            return output
    raise ContextSelectionRejected('The explicit context kind or native text is unavailable')


def _paired_call(items, record, item):
    call_type = 'custom_tool_call' if item['type'] == 'custom_tool_call_output' else 'function_call'
    calls = [(n, i) for n, i in items.items() if n < record and i.get('type') == call_type
             and i.get('call_id') == item.get('call_id') and isinstance(i.get('call_id'), str)]
    outputs = [i for i in items.values() if i.get('type') == item['type'] and i.get('call_id') == item.get('call_id')]
    if len(calls) != 1 or len(outputs) != 1:
        raise ContextSelectionRejected('The selected output must have one native executed call and result')
    return calls[0]


def _schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS core_context_dispositions (
        reference TEXT PRIMARY KEY, payload_json TEXT NOT NULL)''')
    for operation in ('UPDATE', 'DELETE'):
        connection.execute(f'''CREATE TRIGGER IF NOT EXISTS immutable_context_{operation.lower()}
            BEFORE {operation} ON core_context_dispositions
            BEGIN SELECT RAISE(ABORT, 'immutable Core context disposition'); END''')
    connection.execute('''CREATE TABLE IF NOT EXISTS core_context_current (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, reference TEXT NOT NULL,
        PRIMARY KEY(platform, session_id))''')


def _current_input(core, root, owner, connection):
    state = core.read_session_state(root=root, **owner, transaction=connection)
    if state.get('pending_audit_event') or not core._canonical_input_bound(connection, root, state):
        raise ContextSelectionRejected('Current canonical native input is required')
    return state


def context_input_binding(source, expected_input_event_id=None, *, current=False, transaction=None):
    """Bind content operations to human intake or separated native SDK context.

    Historical SDK bindings use their immutable committed prefix and byte row.
    Current binding additionally closes every unresolved transport boundary.
    This API supplies no execution, route or completion authority.
    """
    core, root, owner = core_api._coordinates(source)
    with core._read_connection(root, transaction) as connection:
        state = core._stored_state(connection, **owner)
        if state is None or state.get('pending_audit_event'):
            raise ContextSelectionRejected('An original native content intake is required')
        if current or expected_input_event_id is None:
            if core._pending_native_inputs(state):
                raise ContextSelectionRejected('Native content intake remains pending')
            event = state.get('current_context_input_event_id') or state['latest_input_event_id']
            if expected_input_event_id is not None and expected_input_event_id != event:
                raise ContextSelectionRejected('The native content intake receipt changed')
            expected_input_event_id = event
            if not state.get('current_context_input_event_id'):
                _current_input(core, root, owner, connection)
        if not isinstance(expected_input_event_id, str) or not expected_input_event_id:
            raise ContextSelectionRejected('An exact original native content input is required')
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_context_intakes'").fetchone()
        sdk = exists and connection.execute('''SELECT 1 FROM core_context_intakes
            WHERE platform=? AND session_id=? AND input_event_id=?''',
            (owner['platform'], owner['session_id'], expected_input_event_id)).fetchone()
        if sdk or expected_input_event_id == state.get('current_context_input_event_id'):
            try:
                return core._context_input_binding(connection, root, state, expected_input_event_id)
            except (ValueError, KeyError, TypeError) as error:
                raise ContextSelectionRejected('Original SDK context intake is unverified') from error
        return core_api.response_input_binding(source, expected_input_event_id, transaction=connection)


def _locate(items, literal, kind, user_record, platform='codex'):
    coordinates = []
    for record, item in items.items():
        if kind in {'assistant', 'message'} and record >= user_record:
            continue
        output = item.get('content') if platform == 'claude' or kind in {'assistant', 'message'} else item.get('output')
        blocks = [output] if isinstance(output, str) else output if isinstance(output, list) else []
        for index in ([0] if kind == 'exchange' and blocks else range(len(blocks))):
            try:
                block = _selected_value(item, dict(kind=kind, block_index=index), platform)
            except (ContextSelectionRejected, NativeContextRejected):
                continue
            text = block if isinstance(block, str) else (block['text'] if isinstance(block, dict)
                    else '\n'.join(b.get('text', '') for b in block))
            if literal in text:
                coordinates.append(dict(record=record, block_index=index, kind=kind))
    return coordinates


def _resolve_choices(items, selected, user_record, platform='codex'):
    resolved = []
    for choice in selected:
        if not isinstance(choice, dict):
            raise ContextSelectionRejected('An explicit selection object is required')
        if 'locate_text' in choice:
            kind = choice.get('kind', 'output')
            if (set(choice) - {'kind', 'disposition'} != {'locate_text', 'summary', 'next_need'}
                or kind not in {'output', 'exchange', 'assistant', 'message'}
                or not isinstance(choice['locate_text'], str) or not choice['locate_text']):
                raise ContextSelectionRejected('A literal, use conclusion and remaining need are required')
            matches = _locate(items, choice['locate_text'], kind, user_record, platform)
            if len(matches) != 1:
                raise ContextSelectionRejected('The original selection is ambiguous or unavailable; a unique coordinate is required')
            choice = dict(matches[0], summary=choice['summary'], next_need=choice['next_need'],
                          disposition=choice.get('disposition', 'replace'))
        resolved.append(choice)
    return resolved


def locate_outputs(source, expected_input_event_id, literal, kind='output'):
    core, root, owner = core_api._coordinates(source)
    if owner['platform'] == 'claude':
        return _locate_claude(source, expected_input_event_id, literal, kind)
    if (owner['platform'] != 'codex' or source.get('CODEX_THREAD_ID') != owner['session_id']
        or not isinstance(literal, str) or not literal or kind not in {'output', 'exchange', 'assistant', 'message'}):
        raise ContextSelectionRejected('The native responding owner and explicit search literal are required')
    with core._read_connection(root) as connection:
        state = _current_input(core, root, owner, connection)
        if state['latest_input_event_id'] != expected_input_event_id:
            raise ContextSelectionRejected('The native intake receipt changed')
    home = source.get('GHOST_ALICE_CODEX_DIR') or source.get('CODEX_HOME') or str(Path.home() / '.codex')
    path = codex_transcript_path(home, owner['session_id'])
    if path is None:
        raise ContextSelectionRejected('The original native transcript is unavailable')
    coordinates, items = [], _native(path)
    user_record = max((n for n, i in items.items() if i.get('type') == 'message' and i.get('role') == 'user'), default=0)
    for match in _locate(items, literal, kind, user_record):
        item = items[match['record']]
        block = _selected_value(item, match)
        coordinate = dict(source=path, record=match['record'], block_index=match['block_index'],
            call_id=item.get('call_id'), item_digest=fingerprint(item), block_digest=fingerprint(block))
        if kind != 'output': coordinate['kind'] = kind
        coordinates.append(coordinate)
    return coordinates


def _codex_decisions(connection, reference, payload, source, path, originals):
    """Resolve current choices to their own immutable decision provenance."""
    versions = {'context-disposition.v1', 'context-disposition.v2', 'context-disposition.v4'}
    loaded = {reference: payload}

    def origin(identifier):
        if not isinstance(identifier, str) or not identifier:
            raise ContextSelectionRejected('A retained decision reference is required')
        if identifier not in loaded:
            row = connection.execute('SELECT payload_json FROM core_context_dispositions WHERE reference=?',
                                     (identifier,)).fetchone()
            if row is None:
                raise ContextSelectionRejected('The retained decision is unavailable')
            loaded[identifier] = json.loads(row[0])
        value = loaded[identifier]
        if (not isinstance(value, dict) or value.get('schema_version') not in versions
            or fingerprint(value) != identifier or not isinstance(value.get('selected'), list)
            or not isinstance(value.get('input_binding'), dict)
            or not isinstance(value['input_binding'].get('input_event_id'), str)
            or not isinstance(value.get('original'), dict) or not isinstance(value['original'].get('locator'), str)
            or value['original'].get('storage') != FILE_SOURCE_FORMAT
            or set(value['original']) != {'storage', 'locator', 'digest', 'byte_length', 'appendable', 'source_id'}
            or not isinstance(value.get('native_input_digest'), str)):
            raise ContextSelectionRejected('The retained decision is unverified')
        if value['schema_version'] == 'context-disposition.v4':
            if (set(value) != {'schema_version', 'input_binding', 'native_input_digest', 'original', 'selected', 'retained'}
                or not isinstance(value['retained'], list)):
                raise ContextSelectionRejected('The update envelope is unverified')
            for locator in value['retained']:
                if (not isinstance(locator, dict) or set(locator) != {'reference', 'selected_index'}
                    or not isinstance(locator['reference'], str) or not locator['reference']
                    or locator['reference'] == identifier or type(locator['selected_index']) is not int
                    or locator['selected_index'] < 0):
                    raise ContextSelectionRejected('A direct originating decision locator is required')
        return value

    current = origin(reference)
    locators = current.get('retained', []) + [dict(reference=reference, selected_index=index)
                                             for index in range(len(current['selected']))]
    decisions, seen = [], []
    authenticated = set()
    historical_choices = {}
    for locator in locators:
        decision_reference = locator['reference']
        decision = origin(decision_reference)
        index = locator['selected_index']
        if type(index) is not int or not 0 <= index < len(decision['selected']):
            raise ContextSelectionRejected('The retained locator must identify an originating choice')
        if decision_reference not in authenticated:
            old_binding = core_api.response_input_binding(source,
                decision['input_binding']['input_event_id'], transaction=connection)
            if decision['input_binding'] != old_binding or decision['original']['locator'] != path:
                raise ContextSelectionRejected('The retained decision belongs to a different input or original')
            # Authenticate the original prefix, rather than rebinding a choice
            # to the later input that published this update.
            rows = committed_records(source_text(decision['original']))
            if (not all(isinstance(row, dict) for row in rows.values())
                or not any(row.get('type') == 'session_meta' for row in rows.values())
                or any(not isinstance(row.get('payload'), dict)
                    or row['payload'].get('id') != source['GHOST_ALICE_SESSION_ID']
                    for row in rows.values() if row.get('type') == 'session_meta')
                or any(not isinstance(row.get('payload'), dict) for row in rows.values() if row.get('type') == 'response_item')):
                raise ContextSelectionRejected('The retained original native owner differs')
            historical = {number: _item(row['payload']) for number, row in rows.items()
                          if row.get('type') == 'response_item'}
            human = _input_item(list(historical.values()))
            text = _input_text(human)
            if (fingerprint(human) != decision['native_input_digest']
                or 'sha256:' + hashlib.sha256(text.encode()).hexdigest() != old_binding['input_digest']
                or len(text) != old_binding['input_char_count']):
                raise ContextSelectionRejected('The retained original input differs from its decision binding')
            historical_choices[decision_reference] = historical
            authenticated.add(decision_reference)
        choice = decision['selected'][index]
        if (not isinstance(choice, dict) or type(choice.get('record')) is not int
            or type(choice.get('block_index')) is not int or choice['record'] not in originals
            or choice.get('disposition', 'replace') not in {'keep', 'replace', 'omit'}
            or choice.get('kind', 'output') not in {'output', 'exchange', 'assistant', 'message'}):
            raise ContextSelectionRejected('The originating choice is unavailable')
        required = {'record', 'block_index', 'summary', 'next_need', 'item_digest', 'block_digest'}
        if choice.get('kind') == 'exchange':
            required.update(('call_record', 'call_digest'))
        if (set(choice) - {'kind', 'disposition'} != required
            or any(not isinstance(choice[key], str) or not choice[key].strip()
                   for key in ('summary', 'next_need', 'item_digest', 'block_digest'))):
            raise ContextSelectionRejected('The originating choice shape is unverified')
        item = originals[choice['record']]
        historical = historical_choices[decision_reference]
        if (choice['record'] not in historical
            or fingerprint(historical[choice['record']]) != choice['item_digest']
            or fingerprint(item) != choice['item_digest']
            or fingerprint(_selected_value(item, choice)) != choice['block_digest']):
            raise ContextSelectionRejected('The retained original choice changed')
        if choice.get('kind') == 'exchange':
            call_record, call = _paired_call(originals, choice['record'], item)
            if call_record != choice['call_record'] or fingerprint(call) != choice['call_digest']:
                raise ContextSelectionRejected('The retained native exchange changed')
        if any(other['record'] == choice['record'] and (other['block_index'] == choice['block_index']
            or other.get('kind') == 'exchange' or choice.get('kind') == 'exchange') for other in seen):
            raise ContextSelectionRejected('Retained current choices cannot overlap')
        seen.append(choice)
        decisions.append((decision_reference, decision, choice, index))
    return decisions


def _codex_update_retained(connection, source, path, originals, selections):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_context_current'").fetchone()
    row = connection.execute('''SELECT c.reference,d.payload_json FROM core_context_current c
        LEFT JOIN core_context_dispositions d ON d.reference=c.reference
        WHERE c.platform='codex' AND c.session_id=?''', (source['GHOST_ALICE_SESSION_ID'],)).fetchone() if exists else None
    if row is None:
        return []
    if row[1] is None:
        raise ContextSelectionRejected('The current originating decision is unavailable')
    prior = _codex_decisions(connection, row[0], json.loads(row[1]), source, path, originals)
    retained = []
    for reference, _payload, choice, index in prior:
        replacements = [new for new in selections if new['record'] == choice['record']
            and (new['block_index'] == choice['block_index'] or new.get('kind') == 'exchange'
                 or choice.get('kind') == 'exchange')]
        if any(choice.get('kind') == 'exchange' and new.get('kind') != 'exchange' for new in replacements):
            raise ContextSelectionRejected('An explicit exchange decision must change the retained whole-exchange scope')
        if not replacements:
            retained.append(dict(reference=reference, selected_index=index))
    return retained


def record_disposition(source, expected_input_event_id, selected, *, transaction=None, selection_mode='replace',
                       project_replacements=False):
    if type(project_replacements) is not bool:
        raise ContextSelectionRejected('Native replacement projection requires an explicit boolean')
    if not isinstance(selection_mode, str) or selection_mode not in {'replace', 'update'}:
        raise ContextSelectionRejected('Selection mode must be replace or update')
    core, root, owner = core_api._coordinates(source)
    if owner['platform'] == 'claude':
        if selection_mode != 'replace':
            raise ContextSelectionRejected('Source-local selection updates currently require Codex')
        return _record_claude(source, expected_input_event_id, selected, transaction=transaction,
                              project_replacements=project_replacements)
    if project_replacements:
        raise ContextSelectionRejected('Native replacement projection requires Claude whole-result judgments')
    if owner['platform'] != 'codex' or source.get('CODEX_THREAD_ID') != owner['session_id']:
        raise ContextSelectionRejected('The responding native Codex owner is required')
    home = source.get('GHOST_ALICE_CODEX_DIR') or source.get('CODEX_HOME') or str(Path.home() / '.codex')
    path = codex_transcript_path(home, owner['session_id'])
    if path is None or not isinstance(selected, list):
        raise ContextSelectionRejected('An owned native transcript and explicit current selection are required')
    with core._transaction(root, transaction=transaction) as connection:
        state = _current_input(core, root, owner, connection)
        if state['latest_input_event_id'] != expected_input_event_id:
            raise ContextSelectionRejected('The native intake receipt changed')
        binding = core_api.response_input_binding(source, expected_input_event_id, transaction=connection)
        items = _native(path)
        user = _input_item(list(items.values()))
        text = _input_text(user)
        if ('sha256:' + hashlib.sha256(text.encode()).hexdigest() != binding['input_digest']
            or len(text) != binding['input_char_count']):
            raise ContextSelectionRejected('Native input differs from the canonical Core receipt')
        user_record = max(n for n, i in items.items() if i == user)
        selections, seen = [], set()
        for choice in _resolve_choices(items, selected, user_record):
            if (not isinstance(choice, dict) or set(choice) - {'kind', 'disposition'} != {'record', 'block_index', 'summary', 'next_need'}
                or choice.get('kind', 'output') not in {'output', 'exchange', 'assistant', 'message'}):
                raise ContextSelectionRejected('An explicit block, use summary and next need are required')
            if choice.get('disposition', 'replace') not in {'keep', 'replace', 'omit'}:
                raise ContextSelectionRejected('The use judgment disposition must be keep, replace or omit')
            record, index = choice['record'], choice['block_index']
            if type(record) is not int or type(index) is not int or record not in items:
                raise ContextSelectionRejected('Selected native record is unavailable')
            if any(not isinstance(choice[k], str) or not choice[k].strip() for k in ('summary', 'next_need')):
                raise ContextSelectionRejected('The responding model must supply its use judgment and remaining need')
            item = items[record]
            kind = choice.get('kind', 'output')
            block = _selected_value(item, choice)
            key = (record, index)
            if key in seen or any(s['record'] == record and (kind == 'exchange' or s.get('kind') == 'exchange') for s in selections):
                raise ContextSelectionRejected('Context selections cannot overlap')
            paired = {}
            if kind in {'assistant', 'message'}:
                if record >= user_record:
                    raise ContextSelectionRejected('Only a message before the current user input can be selected')
            else:
                call_record, call = _paired_call(items, record, item)
                if kind == 'exchange':
                    paired = dict(call_record=call_record, call_digest=fingerprint(call))
            seen.add(key)
            selections.append(dict(choice, **paired, item_digest=fingerprint(item), block_digest=fingerprint(block)))
        payload = dict(schema_version='context-disposition.v2', input_binding=binding,
            native_input_digest=fingerprint(user),
            original=reference_source(path, appendable=True), selected=selections)
        if selection_mode == 'update':
            try:
                retained = _codex_update_retained(connection, source, path, items, selections)
            except SourceReferenceError as error:
                raise ContextSelectionRejected('The retained original changed') from error
            payload.update(schema_version='context-disposition.v4', retained=retained)
        reference = fingerprint(payload)
        _schema(connection)
        connection.execute('INSERT OR IGNORE INTO core_context_dispositions VALUES (?,?)',
                           (reference, json.dumps(payload, ensure_ascii=False)))
        connection.execute('''INSERT INTO core_context_current VALUES (?,?,?) ON CONFLICT(platform,session_id)
            DO UPDATE SET reference=excluded.reference''', (owner['platform'], owner['session_id'], reference))
    result = dict(status='recorded', reference=reference,
                  selected_count=len(selections) + len(payload.get('retained', [])))
    if selection_mode == 'update':
        result.update(selection_mode=selection_mode, updated_count=len(selections))
    return result


def _select_manual_request(request, root, codex_home, *, _inspect=None, _blocked=()):
    """Distinguish authenticated selection, unselected input and unavailable storage."""
    reason = request_support_reason(request)
    if reason:
        return RequestSelection(request, 'preserved', reason)
    if not isinstance(request, dict) or not isinstance(request.get('input'), list):
        return RequestSelection(request, 'preserved', 'input-unclassified')
    session = request.get('prompt_cache_key')
    if not isinstance(session, str) or not session:
        return RequestSelection(request, 'preserved', 'session-unclassified')
    source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(root), GHOST_ALICE_PLATFORM='codex',
                  GHOST_ALICE_SESSION_ID=session)
    try:
        core, root, owner = core_api._coordinates(source)
        with core._read_connection(root) as connection:
            if connection is None:
                return RequestSelection(request, 'unavailable', 'storage-unavailable')
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_context_current'").fetchone():
                return RequestSelection(request, 'preserved', 'selection-absent')
            row = connection.execute('''SELECT d.reference,d.payload_json FROM core_context_current c
                JOIN core_context_dispositions d ON d.reference=c.reference
                WHERE c.platform=? AND c.session_id=?''', ('codex', session)).fetchone()
            if row is None:
                return RequestSelection(request, 'preserved', 'selection-absent')
            state = _current_input(core, root, owner, connection)
            reference, raw = row
            payload = json.loads(raw)
            binding = core_api.response_input_binding(source, state['latest_input_event_id'], transaction=connection)
            original_binding = core_api.response_input_binding(source,
                payload['input_binding']['input_event_id'], transaction=connection)
            if (payload.get('schema_version') not in {'context-disposition.v1', 'context-disposition.v2', 'context-disposition.v4'}
                or fingerprint(payload) != reference or payload['input_binding'] != original_binding
                or payload['original']['locator'] != codex_transcript_path(str(codex_home), session)):
                return RequestSelection(request, 'preserved', 'selection-unverified')
            user = _input_item(request['input'])
            text = _input_text(user)
            if ('sha256:' + hashlib.sha256(text.encode()).hexdigest() != binding['input_digest']
                or len(text) != binding['input_char_count']):
                return RequestSelection(request, 'preserved', 'input-changed')
            verify_source(payload['original'])
            originals = _native(payload['original']['locator'])
            if fingerprint(_input_item(list(originals.values()))) != fingerprint(user):
                return RequestSelection(request, 'preserved', 'native-input-changed')
            decisions = (_codex_decisions(connection, reference, payload, source,
                          payload['original']['locator'], originals) if payload['schema_version'] == 'context-disposition.v4'
                         else [(reference, payload, choice, index) for index, choice in enumerate(payload['selected'])])
        changes = []
        human_index = max(n for n, item in enumerate(request['input']) if item is user)
        for decision_reference, decision, selected, _ in decisions:
            original = originals[selected['record']]
            kind = selected.get('kind', 'output')
            if (fingerprint(original) != selected['item_digest']
                or fingerprint(_selected_value(original, selected)) != selected['block_digest']):
                return RequestSelection(request, 'preserved', 'selected-source-changed')
            matches = [n for n, item in enumerate(request['input']) if fingerprint(item) == selected['item_digest']]
            call_index = None
            if kind not in {'assistant', 'message'}:
                call_record, call = _paired_call(originals, selected['record'], original)
                calls = [n for n, i in enumerate(request['input']) if isinstance(i, dict)
                         and i.get('type') == call['type'] and i.get('call_id') == call.get('call_id')]
                outputs = [i for i in request['input'] if isinstance(i, dict)
                           and i.get('type') == original['type'] and i.get('call_id') == original.get('call_id')]
                if len(calls) != 1 or len(outputs) != 1:
                    continue
                call_index = calls[0]
                if (len(matches) != 1 or call_index >= matches[0]
                    or fingerprint(request['input'][call_index]) != fingerprint(call)):
                    continue
                if kind == 'exchange' and (call_record != selected['call_record']
                    or fingerprint(call) != selected['call_digest']
                    or fingerprint(request['input'][call_index]) != selected['call_digest']):
                    continue
            if len(matches) == 1 and matches[0] != human_index:
                if kind in {'assistant', 'message'} and matches[0] >= human_index:
                    continue
                changes.append((matches[0], selected, call_index, decision_reference, decision))
        if _inspect is not None:
            _inspect.extend(dict(choice, message_index=index) for index, choice, _, _, _ in changes)
            return RequestSelection(request, 'preserved', 'selection-inspected')
        changes = [(index, choice, call, decision_reference, decision)
                   for index, choice, call, decision_reference, decision in changes
                   if choice.get('disposition', 'replace') != 'keep'
                   and (choice['record'], choice['block_index']) not in _blocked]
        if not changes:
            return RequestSelection(request, 'preserved', 'selection-not-in-request')
        result, observations, removed, removed_blocks = copy.deepcopy(request), [], set(), {}
        for index, choice, call_index, decision_reference, decision in changes:
            kind = choice.get('kind', 'output')
            disposition = choice.get('disposition', 'replace')
            field = 'content' if kind in {'assistant', 'message'} else 'output'
            locator = f"{decision['original']['locator']}#record={choice['record']},{field}={choice['block_index']}"
            text = json.dumps(dict(summary=choice['summary'], next_need=choice['next_need'],
                original=locator, original_block_digest=choice['block_digest'],
                disposition_input_event_id=decision['input_binding']['input_event_id']), ensure_ascii=False)
            if disposition == 'omit':
                item = result['input'][index]
                if kind == 'exchange':
                    removed.update((index, call_index))
                elif isinstance(item[field], str):
                    item[field] = ''
                else:
                    block = item[field][choice['block_index']]
                    # Unknown selected metadata cannot be discarded with its body.
                    if set(block) - {'type', 'text'}:
                        continue
                    if kind in {'assistant', 'message'} and set(item) - {'type', 'role', 'content', 'id', 'phase', 'channel'}:
                        continue
                    removed_blocks.setdefault((index, field), set()).add(choice['block_index'])
            elif kind == 'exchange':
                result['input'][index] = dict(type='message', role='assistant',
                    content=[dict(type='output_text', text=text)])
                removed.add(call_index)
            elif kind in {'assistant', 'message'}:
                result['input'][index]['content'][choice['block_index']]['text'] = text
            else:
                output = result['input'][index]['output']
                if isinstance(output, str): result['input'][index]['output'] = text
                else: output[choice['block_index']] = dict(type='input_text', text=text)
            observation = dict(reference=decision_reference, original=locator,
                disposition=disposition, message_index=index,
                current_input_event_id=binding['input_event_id'],
                disposition_input_event_id=decision['input_binding']['input_event_id'],
                original_item_digest=choice['item_digest'], original_block_digest=choice['block_digest'],
                forwarded_item_digest=fingerprint(result['input'][index]))
            if kind == 'exchange':
                observation.update(kind=kind, original_call=f"{decision['original']['locator']}#record={choice['call_record']}",
                                   removed_call_digest=choice['call_digest'])
            elif kind in {'assistant', 'message'}:
                observation['kind'] = kind
            observations.append(observation)
        for (index, field), indices in removed_blocks.items():
            item = result['input'][index]
            item[field] = [block for n, block in enumerate(item[field]) if n not in indices]
            if not item[field]:
                if field == 'content':
                    removed.add(index)
                else:
                    item[field] = ''
        for observation in observations:
            index = observation['message_index']
            observation['forwarded_item_digest'] = None if index in removed else fingerprint(result['input'][index])
        result['input'] = [item for n, item in enumerate(result['input']) if n not in removed]
        for observation in observations:
            index = observation['message_index']
            observation['message_index'] = None if index in removed else index - sum(n < index for n in removed)
        return RequestSelection(result if observations else request, 'selected' if observations else 'preserved',
                                'authenticated-selection' if observations else 'selection-protocol-unsupported', tuple(observations))
    except (OSError, sqlite3.Error):
        return RequestSelection(request, 'unavailable', 'storage-unavailable')
    except json.JSONDecodeError:
        return RequestSelection(request, 'unavailable', 'record-unreadable')
    except (ContextSelectionRejected, SourceReferenceError, core_api.CompletionAssessmentError):
        return RequestSelection(request, 'preserved', 'selection-unverified')



def _claude_originals(source, owner, binding):
    from response_context_use import _path, _native, _bound_human, ResponseUseRejected
    home = source.get('GHOST_ALICE_CLAUDE_DIR') or str(Path.home() / '.claude')
    path = _path(home, 'claude', owner['session_id'])
    if path is None:
        raise ContextSelectionRejected('The original native Claude transcript is unavailable')
    try:
        native = _native(path, 'claude', owner['session_id'])
        record, human = _bound_human(native, binding, 'claude')
    except ResponseUseRejected as error:
        raise ContextSelectionRejected(str(error)) from error
    return path, native, record, human


def _claude_input(core, root, owner, source, expected, connection):
    binding = context_input_binding(source, expected, current=True, transaction=connection)
    return binding, _claude_originals(source, owner, binding)


def _claude_pair(native, record, index):
    block = native.records[record]['content'][index]
    identifier = block.get('tool_use_id')
    calls = [call for call in native.calls if call[2] == identifier and isinstance(identifier, str)]
    results = [result for result in native.results if result[2] == identifier]
    if (len(calls) != 1 or len(results) != 1 or results[0][:2] != (record, index)
        or calls[0][:2] >= results[0][:2]):
        raise ContextSelectionRejected('The selected output must have one unique native executed pair')
    return calls[0]


def _locate_claude(source, expected, literal, kind):
    core, root, owner = core_api._coordinates(source)
    if (not isinstance(literal, str) or not literal
        or kind not in {'output', 'assistant', 'message'}):
        raise ContextSelectionRejected('An explicit supported Claude search literal is required')
    with core._read_connection(root) as connection:
        _, (path, native, user_record, _) = _claude_input(core, root, owner, source, expected, connection)
    coordinates = []
    for match in _locate(native.records, literal, kind, user_record, 'claude'):
        item = native.records[match['record']]
        block = _selected_value(item, match, 'claude')
        coordinates.append(dict(match, source=path, item_digest=fingerprint(item), block_digest=fingerprint(block)))
    return coordinates


def _claude_replacement_projection(payload, reference):
    """Return only the judged conclusion, need and native retrieval locations."""
    path = payload['original']['locator']
    return dict(schema_version='context-replacement-output.v1', context_reference=reference,
        replacements=[dict(record=choice['record'], block_index=choice['block_index'],
            summary=choice['summary'], next_need=choice['next_need'],
            original=f"{path}#record={choice['record']},content={choice['block_index']}")
            for choice in payload['selected'] if choice.get('disposition', 'replace') == 'replace'])


def _claude_pending_replacement_carrier(native, user_record, selections):
    replacements = [choice for choice in selections if choice.get('disposition', 'replace') == 'replace']
    if not replacements or any(choice.get('kind', 'output') != 'output' for choice in replacements):
        raise ContextSelectionRejected('Native projection requires an explicit whole-result replacement')
    pending = [call for call in native.calls if call[0] > user_record
        and isinstance(call[2], str) and call[2]
        and not any(result[2] == call[2] for result in native.results)]
    if (len(pending) != 1 or sum(call[2] == pending[0][2] for call in native.calls) != 1
        or any((choice['record'], choice['block_index']) >= pending[0][:2] for choice in replacements)):
        raise ContextSelectionRejected('One unique current pending native publication call after the source results is required')
    call = pending[0]
    return dict(call_record=call[0], call_block_index=call[1], call_id=call[2], call_digest=fingerprint(call[3]))


def _claude_replacement_context(request, native, payload, reference, human_record, human_index):
    """Authenticate the completed native publication and its exact wire result."""
    from response_context_use import _native_pair, _wire_pair, _output
    import claude_context_adapter as adapter
    carrier = payload.get('replacement_carrier')
    if (payload.get('schema_version') != 'context-disposition.v5'
        or not isinstance(carrier, dict)
        or set(carrier) != {'call_record', 'call_block_index', 'call_id', 'call_digest'}
        or type(carrier['call_record']) is not int or carrier['call_record'] <= payload['native_input_record']
        or type(carrier['call_block_index']) is not int or carrier['call_block_index'] < 0
        or not isinstance(carrier['call_id'], str) or not carrier['call_id']
        or not isinstance(carrier['call_digest'], str)):
        return None
    pair = _native_pair(native, carrier, 'claude')
    if pair is None:
        return None
    wire = _wire_pair(request, pair, 'claude')
    if (wire is None or wire[0] == human_index or wire[3] == human_index
        or (pair[1][0] > human_record) != (wire[0] > human_index)
        or (pair[0][0] > human_record) != (wire[3] > human_index)):
        return None
    wire_block = request['messages'][wire[0]]['content'][wire[1]]
    if adapter.semantic_fingerprint(wire_block) != adapter.semantic_fingerprint(pair[1][3]):
        return None
    projection = _claude_replacement_projection(payload, reference)
    if not projection['replacements'] or any((choice['record'], choice['block_index']) >= pair[0][:2]
        for choice in payload['selected'] if choice.get('disposition', 'replace') == 'replace'):
        return None
    digest = fingerprint({key: value for key, value in projection.items() if key != 'context_reference'})
    for output in (pair[1][3].get('content'), wire[2]):
        _, count, occurrences = _output(output, 'claude', reference, digest, {}, marker='context_reference')
        if count != 1 or occurrences != 1:
            return None
    return dict(projection=projection, carrier=dict(message_index=wire[0], wire_block_index=wire[1],
                                                   tool_use_id=carrier['call_id']))


def _record_claude(source, expected, selected, *, transaction=None, project_replacements=False):
    core, root, owner = core_api._coordinates(source)
    if not isinstance(selected, list):
        raise ContextSelectionRejected('An explicit current selection is required')
    with core._transaction(root, transaction=transaction) as connection:
        binding, (path, native, user_record, human) = _claude_input(
            core, root, owner, source, expected, connection)
        selections, seen = [], set()
        for choice in _resolve_choices(native.records, selected, user_record, 'claude'):
            if (not isinstance(choice, dict)
                or set(choice) - {'kind', 'disposition', 'clear_tool_inputs'} != {'record', 'block_index', 'summary', 'next_need'}
                or choice.get('kind', 'output') not in {'output', 'assistant', 'message'}):
                raise ContextSelectionRejected('An explicit supported Claude block and use judgment are required')
            if choice.get('disposition', 'replace') not in {'keep', 'replace', 'omit'}:
                raise ContextSelectionRejected('The use judgment disposition must be keep, replace or omit')
            if 'clear_tool_inputs' in choice and (type(choice['clear_tool_inputs']) is not bool
                or choice.get('kind', 'output') != 'output'
                or (choice['clear_tool_inputs'] and choice.get('disposition') != 'omit')):
                raise ContextSelectionRejected('Combined tool input/result scope requires an explicit output omit')
            record, index = choice['record'], choice['block_index']
            if type(record) is not int or type(index) is not int or record not in native.records:
                raise ContextSelectionRejected('Selected native record is unavailable')
            if any(not isinstance(choice[key], str) or not choice[key].strip() for key in ('summary', 'next_need')):
                raise ContextSelectionRejected('The responding model must supply its conclusion and remaining need')
            kind = choice.get('kind', 'output')
            item = native.records[record]
            block = _selected_value(item, choice, 'claude')
            if (record, index) in seen:
                raise ContextSelectionRejected('Context selections cannot overlap')
            if kind in {'message', 'assistant'} and record >= user_record:
                raise ContextSelectionRejected('Only a message before the current human input can be selected')
            call_binding = {}
            if kind == 'output':
                call = _claude_pair(native, record, index)
                if choice.get('clear_tool_inputs') is True:
                    call_binding = dict(call_record=call[0], call_block_index=call[1], call_digest=fingerprint(call[3]))
            seen.add((record, index))
            selections.append(dict(choice, item_digest=fingerprint(item), block_digest=fingerprint(block), **call_binding))
        payload = dict(schema_version='context-disposition.v5' if project_replacements else 'context-disposition.v3',
            platform='claude', input_binding=binding, native_input_record=user_record,
            native_input_digest=fingerprint(human), original=reference_source(path, appendable=True), selected=selections)
        if project_replacements:
            payload['replacement_carrier'] = _claude_pending_replacement_carrier(native, user_record, selections)
        reference = fingerprint(payload)
        _schema(connection)
        connection.execute('INSERT OR IGNORE INTO core_context_dispositions VALUES (?,?)',
                           (reference, json.dumps(payload, ensure_ascii=False)))
        connection.execute("""INSERT INTO core_context_current VALUES (?,?,?) ON CONFLICT(platform,session_id)
            DO UPDATE SET reference=excluded.reference""", (owner['platform'], owner['session_id'], reference))
    result = dict(status='recorded', reference=reference, selected_count=len(selections))
    if project_replacements:
        result['replacement_context'] = _claude_replacement_projection(payload, reference)
    return result


def _claude_wire_human(request, binding, human):
    from response_context_use import ResponseUseRejected, _wire_human
    try:
        return _wire_human(request, 'claude', binding, human)
    except ResponseUseRejected as error:
        raise ContextSelectionRejected(str(error)) from error


def _claude_selection_boundary_reason(request, native, observed, native_boundary, wire_boundary):
    """Keep selected results and calls on the authenticated/excluded input side."""
    import claude_context_adapter as adapter
    if any(value['message_index'] == wire_boundary for value in observed):
        return 'current-input-protected'
    for value in observed:
        if ((value['record'] > native_boundary) != (value['message_index'] > wire_boundary)):
            return 'native-order-unverified'
        if value['kind'] == 'output':
            call = _claude_pair(native, value['record'], value['block_index'])
            wire_calls = [target for target in adapter._blocks(request['messages'])
                if target[2] == 'assistant' and target[3].get('type') == 'tool_use'
                and target[3].get('id') == call[2]]
            if (len(wire_calls) != 1
                or (call[0] > native_boundary) != (wire_calls[0][0] > wire_boundary)):
                return 'native-order-unverified'
    return None


def _select_claude_request(request, root, native_home, session, *, _inspect=None, _blocked=(), _native_inspect=None):
    import claude_context_adapter as adapter
    if not isinstance(request, dict) or not isinstance(request.get('messages'), list):
        return RequestSelection(request, 'preserved', 'input-unclassified')
    if not isinstance(session, str) or not session:
        return RequestSelection(request, 'preserved', 'session-unclassified')
    source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(root), GHOST_ALICE_PLATFORM='claude',
                  GHOST_ALICE_SESSION_ID=session, GHOST_ALICE_CLAUDE_DIR=str(native_home))
    try:
        core, root, owner = core_api._coordinates(source)
        with core._read_connection(root) as connection:
            if connection is None:
                return RequestSelection(request, 'unavailable', 'storage-unavailable')
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_context_current'").fetchone():
                return RequestSelection(request, 'preserved', 'selection-absent')
            row = connection.execute("""SELECT d.reference,d.payload_json FROM core_context_current c
                JOIN core_context_dispositions d ON d.reference=c.reference
                WHERE c.platform=? AND c.session_id=?""", ('claude', session)).fetchone()
            if row is None:
                return RequestSelection(request, 'preserved', 'selection-absent')
            reference, raw = row
            payload = json.loads(raw)
            original = payload.get('original') if isinstance(payload, dict) else None
            if (not isinstance(original, dict) or original.get('storage') != FILE_SOURCE_FORMAT
                or set(original) != {'storage', 'locator', 'digest', 'byte_length', 'appendable', 'source_id'}
                or not isinstance(payload.get('selected'), list)):
                return RequestSelection(request, 'preserved', 'selection-unverified')
            binding, (path, native, human_record, human) = _claude_input(
                core, root, owner, source, None, connection)
            original_binding = context_input_binding(source,
                payload['input_binding']['input_event_id'], transaction=connection)
            if (payload.get('schema_version') not in {'context-disposition.v3', 'context-disposition.v5'}
                or payload.get('platform') != 'claude'
                or fingerprint(payload) != reference or payload['input_binding'] != original_binding
                or payload['original']['locator'] != path):
                return RequestSelection(request, 'preserved', 'selection-unverified')
            prefix_rows = committed_records(source_text(original))
            prefix_records = adapter.native_records_from_rows(prefix_rows)
            original_human = payload['native_input_record']
            if (original_human not in native.humans or original_human not in prefix_records
                or fingerprint(prefix_records[original_human]) != payload['native_input_digest']
                or fingerprint(native.records[original_human]) != payload['native_input_digest']):
                return RequestSelection(request, 'preserved', 'native-input-changed')
            if any(not isinstance(choice, dict) or type(choice.get('record')) is not int
                   or choice['record'] not in prefix_records
                   or fingerprint(prefix_records[choice['record']]) != choice.get('item_digest')
                   for choice in payload['selected']):
                return RequestSelection(request, 'preserved', 'selected-source-unverified')
            if payload['schema_version'] == 'context-disposition.v5':
                carrier = payload.get('replacement_carrier')
                if (set(payload) != {'schema_version', 'platform', 'input_binding', 'native_input_record',
                    'native_input_digest', 'original', 'selected', 'replacement_carrier'}
                    or not isinstance(carrier, dict)
                    or set(carrier) != {'call_record', 'call_block_index', 'call_id', 'call_digest'}
                    or type(carrier['call_record']) is not int or carrier['call_record'] not in prefix_records
                    or type(carrier['call_block_index']) is not int or carrier['call_block_index'] < 0):
                    return RequestSelection(request, 'preserved', 'selection-unverified')
                call_item = prefix_records[carrier['call_record']]
                if (call_item.get('role') != 'assistant' or carrier['call_block_index'] >= len(call_item['content'])
                    or fingerprint(call_item['content'][carrier['call_block_index']]) != carrier['call_digest']):
                    return RequestSelection(request, 'preserved', 'native-call-changed')
            human_index = _claude_wire_human(request, binding, human)
        native_replacement = _claude_replacement_context(request, native, payload, reference, human_record, human_index)
        selections = []
        for choice in payload['selected']:
            if _inspect is None and (choice.get('disposition', 'replace') == 'keep'
                or (choice['record'], choice['block_index']) in _blocked):
                continue
            kind = choice.get('kind', 'output')
            if kind == 'output':
                call = _claude_pair(native, choice['record'], choice['block_index'])
                if choice.get('clear_tool_inputs') is True and (
                    choice.get('call_record') != call[0] or choice.get('call_block_index') != call[1]
                    or choice.get('call_digest') != fingerprint(call[3])):
                    return RequestSelection(request, 'preserved', 'native-call-changed')
            elif choice['record'] >= original_human:
                return RequestSelection(request, 'preserved', 'selection-unverified')
            locator = f"{path}#record={choice['record']},content={choice['block_index']}"
            text = json.dumps(dict(summary=choice['summary'], next_need=choice['next_need'], original=locator,
                disposition_input_event_id=payload['input_binding']['input_event_id']), ensure_ascii=False)
            enriched = dict(choice, kind=kind, source_locator=path,
                            replacement_text='' if choice.get('disposition') == 'omit' else text)
            if native_replacement is not None and kind == 'output' and choice.get('disposition', 'replace') == 'replace':
                enriched['replacement_context'] = native_replacement
            selections.append(enriched)
        inspected = [] if _inspect is not None else None
        changed, observed = adapter.request_apply(request, native.records, selections, _inspect=inspected)
        if inspected is not None:
            observed = inspected
        # Semantic text matching must never substitute the current human when
        # an equal earlier text disappeared from the host's retained messages.
        boundary_reason = _claude_selection_boundary_reason(request, native, observed, human_record, human_index)
        if boundary_reason:
            return RequestSelection(request, 'preserved', boundary_reason)
        for value in observed:
            value.update(reference=reference, current_input_event_id=binding['input_event_id'],
                         disposition_input_event_id=payload['input_binding']['input_event_id'])
        if _inspect is not None:
            _inspect.extend(observed)
            if _native_inspect is not None:
                _native_inspect.append(native.records)
            return RequestSelection(request, 'preserved', 'selection-inspected')
        return RequestSelection(changed if observed else request, 'selected' if observed else 'preserved',
            'authenticated-selection' if observed else 'selection-not-in-request', tuple(observed))
    except (SignedPrefixSelectionUnsupported, ToolInputSelectionUnsupported) as error:
        return RequestSelection(request, 'preserved', error.reason)
    except (OSError, sqlite3.Error):
        return RequestSelection(request, 'unavailable', 'storage-unavailable')
    except json.JSONDecodeError:
        return RequestSelection(request, 'unavailable', 'record-unreadable')
    except (ContextSelectionRejected, NativeContextRejected,
            SourceReferenceError, core_api.CompletionAssessmentError):
        return RequestSelection(request, 'preserved', 'selection-unverified')


def _cross_scope_blocks(request, root, native_home, platform, session_id):
    """Authenticate overlapping current declarations without ordering scopes."""
    if platform not in ('codex', 'claude') or not isinstance(request, dict):
        return set(), set(), False, None
    session = request.get('prompt_cache_key') if platform == 'codex' else session_id
    if not isinstance(session, str) or not session:
        return set(), set(), False, None
    source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(root), GHOST_ALICE_PLATFORM=platform,
                  GHOST_ALICE_SESSION_ID=session)
    try:
        core, root, _ = core_api._coordinates(source)
        with core._read_connection(root) as connection:
            if connection is None:
                return set(), set(), False, None
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'core_context_current', 'core_response_use_current'} <= tables:
                return set(), set(), False, None
    except (OSError, sqlite3.Error):
        return set(), set(), False, RequestSelection(request, 'unavailable', 'storage-unavailable')
    except core_api.CompletionAssessmentError:
        return set(), set(), False, None
    from response_context_use import select_uses, _protected_coordinates
    manual, uses = [], []
    inspection = (_select_manual_request(request, root, native_home, _inspect=manual) if platform == 'codex'
                  else _select_claude_request(request, root, native_home, session_id, _inspect=manual))
    if inspection.state == 'unavailable':
        return set(), set(), False, inspection
    if not manual:
        return set(), set(), False, None
    inspection = select_uses(request, root, native_home, platform=platform, session_id=session_id, _inspect=uses)
    if inspection.state == 'unavailable':
        return set(), set(), False, inspection
    blocked_manual, blocked_uses, conflict = set(), set(), False
    protected_uses = _protected_coordinates(uses)
    for choice in manual:
        for judgment in uses:
            if (choice['record'] != judgment['record'] or (choice.get('kind', 'output') != 'exchange'
                and choice['block_index'] not in judgment['block_indices'])):
                continue
            manual_disposition = choice.get('disposition', 'replace')
            use_disposition = judgment['disposition']
            if manual_disposition == 'keep' and use_disposition != 'keep':
                blocked_uses.add(judgment['use_reference']); conflict = True
            elif manual_disposition != 'keep' and any(choice['record'] == record
                and (choice.get('kind', 'output') == 'exchange' or choice['block_index'] == index)
                for record, index in protected_uses):
                blocked_manual.add((choice['record'], choice['block_index'])); conflict = True
    if conflict and platform == 'claude':
        from claude_context_adapter import require_rewriteable_prefix
        try:
            require_rewriteable_prefix(request)
        except SignedPrefixSelectionUnsupported as error:
            return set(), set(), False, RequestSelection(request, 'preserved', error.reason)
    return blocked_manual, blocked_uses, conflict, None


def _select_claude_server_omit(request, root, native_home, session):
    """Authenticate full-result omissions without modifying signed history."""
    from claude_context_adapter import server_context_omit
    from response_context_use import select_uses, _protected_coordinates
    manual, uses, native = [], [], []
    inspected = _select_claude_request(request, root, native_home, session, _inspect=manual,_native_inspect=native)
    if inspected.state == 'unavailable' or not manual:
        return inspected
    used = select_uses(request, root, native_home, platform='claude', session_id=session, _inspect=uses)
    if used.state == 'unavailable':
        return used
    # A producer judgment is narrower than the enclosing tool result. A whole
    # result omit, or a replace with its authenticated native summary carrier,
    # still cannot clear another producer whose original or conclusion is needed.
    protected = _protected_coordinates(uses, server=True)
    conflict = any(choice['record'] == use['record'] and choice['block_index'] in use['block_indices']
                   and ((choice.get('disposition', 'replace') == 'keep' and use['disposition'] != 'keep')
                        or (use['disposition'] == 'keep' and choice.get('disposition', 'replace') != 'keep'))
                   for choice in manual for use in uses)
    choices = [choice for choice in manual if (choice['record'], choice['block_index']) not in protected]
    changed, observed, reason = server_context_omit(request, choices,originals=native[0])
    if conflict:
        reason = 'selected-with-preserved-context-conflict' if observed else 'context-disposition-conflict'
    return RequestSelection(changed, 'selected' if observed else 'preserved', reason, tuple(observed))


_CLAUDE_NATIVE_READINESS_SECONDS = 2.0
_CLAUDE_NATIVE_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class _ClaudeReadinessRequirements:
    representation: str
    current_input_required: bool
    replacement_carrier_ids: tuple = ()


def _claude_wait_eligible(request, payload, reference, native, prefix, can_rewrite, *, root, native_home,
                          session, native_boundary, wire_boundary, native_betas):
    """Return actual eligible representation and its native source dependencies."""
    import claude_context_adapter as adapter
    from response_context_use import _native_pair, _wire_pair, _output, select_uses, _protected_coordinates
    choices = []
    for choice in payload['selected']:
        if (not isinstance(choice,dict) or type(choice.get('record')) is not int
            or choice['record'] not in prefix or fingerprint(prefix[choice['record']])!=choice.get('item_digest')
            or (choice.get('kind','output')!='output' and choice['record']>=payload['native_input_record'])):
            return None
        choices.append(dict(choice,kind=choice.get('kind','output'),source_locator=payload['original']['locator'],
            replacement_text=choice.get('summary'),reference=reference))
    inspected = []
    adapter.request_apply(request,native.records,choices,_inspect=inspected)
    if _claude_selection_boundary_reason(request,native,inspected,native_boundary,wire_boundary):
        return None
    uses = []
    used = select_uses(request,root,native_home,platform='claude',session_id=session,_inspect=uses,
        _retained_boundary=(native_boundary,wire_boundary))
    if used.state == 'unavailable':
        return None
    client_protected = _protected_coordinates(uses)
    client_choices = [choice for choice in inspected if choice.get('disposition','replace')!='keep'
        and (choice['record'],choice['block_index']) not in client_protected]
    if can_rewrite and not any(choice.get('clear_tool_inputs') is True for choice in client_choices):
        return _ClaudeReadinessRequirements('client',True) if client_choices else None
    if not isinstance(native_betas,(tuple,list)) or 'context-management-2025-06-27' not in native_betas:
        return None
    protected = _protected_coordinates(uses,server=True)
    inspected = [choice for choice in inspected if (choice['record'],choice['block_index']) not in protected]
    candidate = None
    if payload['schema_version']=='context-disposition.v5':
        carrier = payload.get('replacement_carrier')
        if (isinstance(carrier,dict) and set(carrier)=={'call_record','call_block_index','call_id','call_digest'}
            and type(carrier['call_record']) is int and type(carrier['call_block_index']) is int
            and isinstance(carrier['call_id'],str) and isinstance(carrier['call_digest'],str)):
            native_calls = [call for call in native.calls if call[2]==carrier['call_id']]
            blocks = list(adapter._blocks(request['messages']))
            calls = [block for block in blocks if block[2]=='assistant' and block[3].get('type')=='tool_use'
                and block[3].get('id')==carrier['call_id']]
            results = [block for block in blocks if block[2]=='user' and block[3].get('type')=='tool_result'
                and block[3].get('tool_use_id')==carrier['call_id']]
            if (len(native_calls)==len(calls)==len(results)==1
                and native_calls[0][:2]==(carrier['call_record'],carrier['call_block_index'])
                and fingerprint(native_calls[0][3])==carrier['call_digest']
                and carrier['call_record'] in prefix
                and fingerprint(prefix[carrier['call_record']])==fingerprint(native.records[carrier['call_record']])
                and calls[0][:2]<results[0][:2]
                and adapter._call_fingerprint(calls[0][3])==adapter._call_fingerprint(native_calls[0][3])):
                projection = _claude_replacement_projection(payload,reference)
                digest = fingerprint({key:value for key,value in projection.items() if key!='context_reference'})
                _, count, occurrences = _output(results[0][3].get('content'),'claude',reference,digest,{},marker='context_reference')
                paired = _native_pair(native,carrier,'claude')
                completed = [result for result in native.results if result[2]==carrier['call_id']]
                full_result_matches = (not completed or (paired is not None and _wire_pair(request,paired,'claude') is not None
                    and adapter.semantic_fingerprint(results[0][3])==adapter.semantic_fingerprint(paired[1][3])))
                if count==occurrences==1 and full_result_matches:
                    candidate = dict(projection=projection,carrier=dict(message_index=results[0][0],
                        wire_block_index=results[0][1],tool_use_id=carrier['call_id']))
    # This dry scope calculation submits no policy. Only actual eligible
    # adapter observations determine native source dependencies; the unchanged
    # selector still requires completed native input and carrier proof.
    for choice in inspected:
        if candidate is not None and choice.get('disposition','replace')=='replace':
            choice['replacement_context'] = candidate
    _, eligible, _ = adapter.server_context_omit(request,inspected,originals=native.records,_inspect_only=True,
        _prospective_carrier=candidate['carrier']['tool_use_id'] if candidate is not None else None)
    if not eligible:
        return None
    carrier_ids = tuple(sorted({value['replacement_carrier']['tool_use_id'] for value in eligible
        if value.get('disposition') == 'replace'}))
    return _ClaudeReadinessRequirements('server',True,carrier_ids)


def _claude_native_readiness(core, root, owner, source, request, state, native_betas):
    """Nominate a needed append condition; only the existing selector admits it."""
    import native_transcript as native_api
    import claude_context_adapter as adapter
    from response_context_use import _output
    if (not isinstance(request,dict) or request_support_reason(request, 'claude')
        or not isinstance(request.get('messages'), list)):
        return None
    with core._read_connection(root) as connection:
        if connection is None or not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_context_current'").fetchone():
            return None
        row = connection.execute('''SELECT c.reference,d.payload_json FROM core_context_current c
            JOIN core_context_dispositions d ON d.reference=c.reference WHERE c.platform=? AND c.session_id=?''',
            (owner['platform'],owner['session_id'])).fetchone()
        if row is None:
            return None
        reference, raw = row
        payload = json.loads(raw)
        if (not isinstance(payload,dict) or fingerprint(payload)!=reference or payload.get('platform')!='claude'
            or payload.get('schema_version') not in {'context-disposition.v3','context-disposition.v5'}
            or not isinstance(payload.get('input_binding'),dict)
            or not isinstance(payload['input_binding'].get('input_event_id'),str)
            or type(payload.get('native_input_record')) is not int or payload['native_input_record']<1
            or not isinstance(payload.get('selected'),list) or not any(isinstance(choice,dict)
                and choice.get('disposition','replace') in ('replace','omit') for choice in payload['selected'])):
            return None
        can_rewrite = True
        try:
            adapter.require_rewriteable_prefix(request)
        except SignedPrefixSelectionUnsupported:
            can_rewrite = False
        if (not can_rewrite and (not isinstance(native_betas,(tuple,list))
            or 'context-management-2025-06-27' not in native_betas)):
            return None
        if not can_rewrite and not any(isinstance(choice,dict) and choice.get('kind','output')=='output'
            and (choice.get('disposition')=='omit' or (payload['schema_version']=='context-disposition.v5'
                and choice.get('disposition','replace')=='replace')) for choice in payload['selected']):
            return None
        original = payload.get('original')
        if (not isinstance(original,dict) or set(original)!= {'storage','locator','digest','byte_length','appendable','source_id'}
            or original.get('storage')!=FILE_SOURCE_FORMAT
            or adapter.native_path(source['GHOST_ALICE_CLAUDE_DIR'],owner['session_id'])!=original['locator']):
            return None
        verify_source(original)
        if context_input_binding(source,payload['input_binding']['input_event_id'],transaction=connection)!=payload['input_binding']:
            return None
        from response_context_use import _native, ResponseUseRejected
        try:
            native = _native(original['locator'],'claude',owner['session_id'])
        except ResponseUseRejected:
            return None
        prefix = adapter.native_records_from_rows(committed_records(source_text(original)))
        if (payload['native_input_record'] not in prefix
            or fingerprint(prefix[payload['native_input_record']])!=payload.get('native_input_digest')):
            return None
        pending = core._pending_native_inputs(state)
        if pending:
            if len(pending)!=1:
                return None
            observation = pending[0]
            # Wire content only correlates a transport that is worth waiting
            # for. It never supplies the actor or admits a content binding.
            prompts = [(index,item) for index,item in enumerate(request['messages'])
                if isinstance(item,dict) and item.get('role')=='user'
                and not (isinstance(item.get('content'),list) and any(isinstance(block,dict)
                    and block.get('type')=='tool_result' for block in item['content']))]
            if not prompts or not native_api.matches_input_text(prompts[-1][1].get('content'),observation):
                return None
            boundary = observation.get('transcript_boundary')
            if not isinstance(boundary,dict) or boundary.get('path')!=original['locator']:
                return None
            with Path(boundary['path']).open('rb') as stream:
                identity = os.fstat(stream.fileno()); content = stream.read()
            offset = boundary.get('offset')
            if (type(offset) is not int or not 0 <= offset <= len(content)
                or hashlib.sha256(content[:offset]).hexdigest()!=boundary.get('prefix_digest')
                or (boundary.get('inode') is not None and (identity.st_dev,identity.st_ino)!=
                    (boundary.get('device'),boundary['inode']))):
                return None
            cursor = hashlib.sha256(json.dumps([boundary['path'],identity.st_dev,identity.st_ino]).encode()).hexdigest()
            start = max(offset,state.get('native_input_cursors',{}).get(cursor,0))
            # This is the earliest append position, not a fabricated input
            # record. The correlated wire position only excludes ineligible
            # selections; committed provenance still owns input admission.
            native_boundary = line_number(content, start)
            requirements = _claude_wait_eligible(request,payload,reference,native,prefix,can_rewrite,root=root,
                native_home=source['GHOST_ALICE_CLAUDE_DIR'],session=owner['session_id'],
                native_boundary=native_boundary,wire_boundary=prompts[-1][0],native_betas=native_betas)
            if requirements is None or not requirements.current_input_required:
                return None
            resolved = native_api.resolve_staged_input(observation,owner['session_id'],state.get('native_input_cursors',{}),
                allow_unknown=True)
            if resolved is not None:
                return 'input-ready' if resolved['actor'] in native_api.RESOLVED_ACTORS else None
            for _, _, line in committed_byte_lines(content, start):
                entry = json.loads(line)
                if (isinstance(entry,dict) and entry.get('sessionId')==owner['session_id']
                    and entry.get('type') in ('user','attachment')
                    and entry.get('promptId') in (None,observation.get('native_prompt_id'))):
                    # An already committed contradictory, meta, sidechain,
                    # mismatched or ambiguous row is not an append condition.
                    return None
            return 'pending-input'
        binding, (_, native, human_record, human) = _claude_input(core,root,owner,source,None,connection)
        human_index = _claude_wire_human(request,binding,human)
        if context_input_binding(source,payload['input_binding']['input_event_id'],transaction=connection)!=payload['input_binding']:
            return None
        requirements = _claude_wait_eligible(request,payload,reference,native,prefix,can_rewrite,root=root,
            native_home=source['GHOST_ALICE_CLAUDE_DIR'],session=owner['session_id'],
            native_boundary=human_record,wire_boundary=human_index,native_betas=native_betas)
        if requirements is None or not requirements.replacement_carrier_ids:
            return None
        carrier = payload.get('replacement_carrier')
        if (not isinstance(carrier,dict) or set(carrier)!= {'call_record','call_block_index','call_id','call_digest'}
            or type(carrier['call_record']) is not int or carrier['call_record']<1
            or type(carrier['call_block_index']) is not int or carrier['call_block_index']<0
            or not isinstance(carrier['call_id'],str) or carrier['call_id'] not in requirements.replacement_carrier_ids
            or not isinstance(carrier['call_digest'],str)):
            return None
        calls = [call for call in native.calls if call[2]==carrier['call_id']]
        if (len(calls)!=1 or any(result[2]==carrier['call_id'] for result in native.results)
            or calls[0][:2]!=(carrier['call_record'],carrier['call_block_index'])
            or fingerprint(calls[0][3])!=carrier['call_digest'] or calls[0][0]<=payload['native_input_record']):
            return None
        blocks = list(adapter._blocks(request['messages']))
        wire_calls = [block for block in blocks if block[2]=='assistant' and block[3].get('type')=='tool_use'
            and block[3].get('id')==carrier['call_id']]
        wire_results = [block for block in blocks if block[2]=='user' and block[3].get('type')=='tool_result'
            and block[3].get('tool_use_id')==carrier['call_id']]
        if (len(wire_calls)!=1 or len(wire_results)!=1 or wire_calls[0][:2]>=wire_results[0][:2]
            or wire_calls[0][0]<=human_index or wire_results[0][0]<=human_index
            or adapter._call_fingerprint(wire_calls[0][3])!=adapter._call_fingerprint(calls[0][3])
            or 'signature' in wire_results[0][3]):
            return None
        projection = _claude_replacement_projection(payload,reference)
        if not projection['replacements']:
            return None
        digest = fingerprint({key:value for key,value in projection.items() if key!='context_reference'})
        _, count, occurrences = _output(wire_results[0][3].get('content'),'claude',reference,digest,{},marker='context_reference')
        return 'pending-carrier' if count==occurrences==1 else None


def prepare_native_request(request, root, native_home, *, platform='codex', session_id=None,
                           native_betas=()):
    """Commit already staged native origin before the host's generation boundary.

    UserPromptSubmit can precede the native transcript append. PreToolUse/Stop
    are too late for the first request, including a turn that uses no tools.
    This explicit lifecycle entrypoint uses the same positive-origin resolver;
    the request body supplies no actor authority. Public selectors stay pure.
    """
    if platform == 'claude' and isinstance(session_id, str) and session_id:
        source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(root), GHOST_ALICE_PLATFORM=platform,
                      GHOST_ALICE_SESSION_ID=session_id, GHOST_ALICE_CLAUDE_DIR=str(native_home))
        try:
            core, selected_root, owner = core_api._coordinates(source)
            state = core.reconcile_native_inputs(root=selected_root, **owner)
            deadline = time.monotonic() + _CLAUDE_NATIVE_READINESS_SECONDS
            while True:
                try:
                    condition = _claude_native_readiness(core,selected_root,owner,source,request,state,native_betas)
                except (OSError, json.JSONDecodeError, ContextSelectionRejected, NativeContextRejected,
                        SourceReferenceError, core_api.CompletionAssessmentError):
                    break
                if condition is None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if condition=='input-ready':
                    state = core.reconcile_native_inputs(root=selected_root, **owner)
                else:
                    # Poll the named source condition, not the business action;
                    # do not hold a ledger transaction while the host appends.
                    time.sleep(min(_CLAUDE_NATIVE_POLL_SECONDS,remaining))
        except (OSError, sqlite3.Error):
            return RequestSelection(request, 'unavailable', 'intake-storage-unavailable')
    options = dict(platform=platform, session_id=session_id)
    if native_betas != ():
        options['native_betas'] = native_betas
    return select_request(request, root, native_home, **options)


def select_request(request, root, native_home, *, platform='codex', session_id=None, native_betas=()):
    """Compose authenticated dispositions and declared response-bound uses."""
    from response_context_use import select_uses
    reason = request_support_reason(request, platform)
    if reason:
        return RequestSelection(request, 'preserved', reason)
    if (platform == 'claude' and isinstance(native_betas, (tuple, list))
        and 'context-management-2025-06-27' in native_betas):
        from claude_context_adapter import require_rewriteable_prefix
        try:
            require_rewriteable_prefix(request)
        except SignedPrefixSelectionUnsupported:
            return _select_claude_server_omit(request, root, native_home, session_id)
    blocked_manual, blocked_uses, conflict, failure = _cross_scope_blocks(request, root, native_home, platform, session_id)
    if failure is not None:
        return failure
    if platform == 'codex':
        manual = _select_manual_request(request, root, native_home, _blocked=blocked_manual)
    elif platform == 'claude':
        manual = _select_claude_request(request, root, native_home, session_id, _blocked=blocked_manual)
    else:
        return RequestSelection(request, 'preserved', 'platform-unclassified')
    if manual.reason == ToolInputSelectionUnsupported.reason:
        if (platform == 'claude' and isinstance(native_betas, (tuple, list))
            and 'context-management-2025-06-27' in native_betas):
            return _select_claude_server_omit(request, root, native_home, session_id)
        return manual
    if manual.state == 'unavailable' or manual.reason == SignedPrefixSelectionUnsupported.reason:
        return manual
    options = dict(platform=platform, session_id=session_id)
    if blocked_uses:
        options['_blocked'] = blocked_uses
    used = select_uses(manual.request, root, native_home, **options)
    if used.state == 'unavailable':
        return RequestSelection(request, 'unavailable', used.reason)
    if used.reason == SignedPrefixSelectionUnsupported.reason:
        return RequestSelection(request, 'preserved', used.reason)
    observations = manual.observations + used.observations
    if observations:
        return RequestSelection(used.request, 'selected',
            'selected-with-preserved-context-conflict' if conflict else 'authenticated-selection', observations)
    if conflict:
        return RequestSelection(request, 'preserved', 'context-disposition-conflict')
    return RequestSelection(request, 'preserved', used.reason if platform == 'claude' and manual.reason == 'selection-absent' else manual.reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--codex-dir', '--native-dir', dest='native_dir', required=True)
    parser.add_argument('--platform', choices=('codex','claude'), default='codex')
    parser.add_argument('--session-id')
    parser.add_argument('--expected-input-event-id')
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--selection-file', type=Path)
    operation.add_argument('--selection-json')
    operation.add_argument('--locate-text')
    operation.add_argument('--use-judgments-json')
    parser.add_argument('--selection-mode', choices=('replace', 'update'), default='replace')
    parser.add_argument('--project-replacements', action='store_true')
    parser.add_argument('--locate-kind', choices=('output', 'exchange', 'assistant', 'message'), default='output')
    args = parser.parse_args(argv)
    if args.project_replacements and (args.platform != 'claude'
        or (args.selection_file is None and args.selection_json is None)):
        raise ContextSelectionRejected('Native replacement projection requires a Claude selection publication')
    if args.selection_mode != 'replace' and args.selection_file is None and args.selection_json is None:
        raise ContextSelectionRejected('Selection update mode requires an explicit selection array')
    session = args.session_id or os.environ.get('CODEX_THREAD_ID' if args.platform == 'codex' else 'GHOST_ALICE_SESSION_ID')
    if not session:
        raise ContextSelectionRejected('The exact native session is required')
    source = dict(os.environ, GHOST_ALICE_SESSION_INTENT_ROOT=args.root, GHOST_ALICE_PLATFORM=args.platform,
                  GHOST_ALICE_SESSION_ID=session)
    source['GHOST_ALICE_CODEX_DIR' if args.platform == 'codex' else 'GHOST_ALICE_CLAUDE_DIR'] = args.native_dir
    if args.use_judgments_json is not None:
        from response_context_use import record_use_judgments
        result = record_use_judgments(source, args.expected_input_event_id, json.loads(args.use_judgments_json))
        print(json.dumps(result, ensure_ascii=False))
        return
    if not args.expected_input_event_id and args.platform == 'codex':
        raise ContextSelectionRejected('An existing-material disposition requires its exact intake event')
    result = (locate_outputs(source, args.expected_input_event_id, args.locate_text, args.locate_kind) if args.locate_text is not None
              else record_disposition(source, args.expected_input_event_id, json.loads(
                  args.selection_json if args.selection_json is not None else args.selection_file.read_text()),
                  selection_mode=args.selection_mode, project_replacements=args.project_replacements))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
