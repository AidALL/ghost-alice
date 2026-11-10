"""Bind an explicit source lookup use to its native response scope.

The response model declares read purpose during its needed lookup. Core code stores
identities, never another source body. The response model's separate explicit
post-use judgment determines keep, replace or omit for the next generation;
native answers add only optional locators.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

import completion_assessment as core_api
from claude_context_adapter import (NativeContextRejected, SignedPrefixSelectionUnsupported, claude_home,
                                    require_rewriteable_prefix)
import ledger_access
from native_transcript import codex_home, codex_transcript_path, is_user_role_prompt
from source_reference import SourceReferenceError, json_digest, matches_input, reference_source, verify_source
from text_lines import committed_records


class ResponseUseRejected(ValueError):
    """The explicit use cannot be bound to one current native execution."""


def _require(condition, detail):
    if not condition:
        raise ResponseUseRejected(detail)


def _clean(value):
    result = copy.deepcopy(value)
    result.pop('internal_chat_message_metadata_passthrough', None)
    return result


@dataclass(frozen=True)
class _Native:
    records: dict
    user_role_prompts: tuple
    calls: tuple
    results: tuple
    finals: tuple
    locator: str = ''


def _native(path, platform, session):
    rows = committed_records(Path(path).read_bytes())
    _require(all(isinstance(row, dict) for row in rows.values()), 'Native JSONL objects are required')
    calls, results, user_role_prompts, finals = [], [], [], []
    if platform == 'codex':
        owners = [r.get('payload', {}).get('id') for r in rows.values() if r.get('type') == 'session_meta']
        _require(owners and all(owner == session for owner in owners), 'Native transcript owner differs')
        payloads = {n: r.get('payload') for n, r in rows.items() if r.get('type') == 'response_item'}
        _require(all(isinstance(item, dict) for item in payloads.values()), 'Native response items are required')
        records = {n: _clean(item) for n, item in payloads.items()}
        for n, item in records.items():
            _require(isinstance(item, dict), 'Native response items are required')
            typ = item.get('type')
            raw = rows[n]['payload']
            if typ == 'message' and item.get('role') == 'user':
                metadata = raw.get('internal_chat_message_metadata_passthrough')
                kinds = metadata.get('content_item_kinds') if isinstance(metadata, dict) else None
                if kinds is None or (isinstance(kinds, list) and 'user.text' in kinds):
                    user_role_prompts.append(n)
            if typ in ('custom_tool_call', 'function_call'):
                calls.append((n, 0, item.get('call_id'), item))
            if typ in ('custom_tool_call_output', 'function_call_output'):
                results.append((n, 0, item.get('call_id'), item))
            if (typ == 'message' and item.get('role') == 'assistant'
                and (item.get('phase') == 'final_answer' or
                     (item.get('phase') is None and item.get('channel') == 'final'))
                and _text_present(item.get('content'), 'output_text')):
                finals.append(n)
    else:
        import claude_context_adapter as adapter
        owned = {n: row for n, row in rows.items() if row.get('type') in ('user', 'assistant')}
        _require(all(row.get('sessionId') == session and not row.get('isSidechain')
                     for row in owned.values()), 'Native Claude transcript owner differs')
        # Owner checks and normalized records must describe the same read.
        # Native hosts append tool events while this operation is running.
        records = adapter.native_records_from_rows(rows)
        for n, item in records.items():
            row = owned[n]
            if is_user_role_prompt(row):
                user_role_prompts.append(n)
            for index, block in enumerate(item['content']):
                if block.get('type') == 'tool_use' and item['role'] == 'assistant':
                    calls.append((n, index, block.get('id'), block))
                if block.get('type') == 'tool_result' and item['role'] == 'user':
                    results.append((n, index, block.get('tool_use_id'), block))
            reason = row['message'].get('stop_reason')
            if (item['role'] == 'assistant' and reason in (None, 'end_turn')
                and not any(b.get('type') == 'tool_use' for b in item['content'])
                and _text_present(item['content'], 'text')):
                # With no explicit end_turn, only the last assistant message
                # before the next user-role prompt can establish supported closure.
                if reason == 'end_turn':
                    finals.append(n)
        # The next user-role prompt may appear after this candidate in the ordered scan.
        for n, item in records.items():
            if (item['role'] == 'assistant' and owned[n]['message'].get('stop_reason') is None
                and not any(b.get('type') == 'tool_use' for b in item['content'])
                and _text_present(item['content'], 'text')):
                later = [k for k, v in records.items() if k > n and
                         (v['role'] == 'assistant' or k in user_role_prompts)]
                if later and later[0] in user_role_prompts and n not in finals:
                    finals.append(n)
    return _Native(records, tuple(user_role_prompts), tuple(calls), tuple(results), tuple(sorted(finals)), str(Path(path).resolve()))


def _text_present(content, block_type):
    return isinstance(content, list) and any(isinstance(block, dict)
        and block.get('type') == block_type and isinstance(block.get('text'), str)
        and 'signature' not in block and block['text'].strip() for block in content)


def _path(home, platform, session):
    if platform == 'codex':
        return codex_transcript_path(str(home), session)
    import claude_context_adapter as adapter
    return adapter.native_path(home, session)


def _input_message_text(item, platform):
    if platform == 'codex':
        from context_lifecycle import _input_text
        return _input_text(item)
    return ''.join(block['text'] for block in item['content']
                   if block.get('type') == 'text' and isinstance(block.get('text'), str))


def _binding(source, event=None, *, current=False, transaction=None):
    from context_lifecycle import context_input_binding
    return context_input_binding(source, event, current=current, transaction=transaction)


def _bound_input_row(native, binding, platform):
    _require(bool(native.user_role_prompts), 'The transcript row for the bound input is unavailable')
    record = native.user_role_prompts[-1]
    item = native.records[record]
    if binding.get('context_only'):
        _require(platform == 'claude' and native.locator == binding.get('native_transcript_path')
            and record == binding.get('native_input_record') and json_digest(item) == binding.get('native_input_digest'),
            'Current SDK context must use its exact original native row and full message')
    if platform == 'claude':
        import claude_context_adapter as adapter
        _require(adapter.bound_input_message(native.records, binding) == item, 'The bound input message identity differs')
    else:
        text = _input_message_text(item, platform)
        _require(matches_input(text, binding), 'The transcript row differs from the current input that Core code recorded')
    return record, item


def _schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS core_response_context_uses (
        reference TEXT PRIMARY KEY, platform TEXT NOT NULL, session_id TEXT NOT NULL,
        payload_json TEXT NOT NULL)''')
    for operation in ('UPDATE', 'DELETE'):
        connection.execute(f'''CREATE TRIGGER IF NOT EXISTS immutable_response_use_{operation.lower()}
            BEFORE {operation} ON core_response_context_uses
            BEGIN SELECT RAISE(ABORT, 'immutable response context use'); END''')


def _source_coordinates(observation):
    _require(isinstance(observation, dict) and isinstance(observation.get('locator'), str)
        and Path(observation['locator']).is_absolute() and 'use_reference' not in observation,
        'An original source lookup observation without a use reference is required')
    return {key: observation[key] for key in ('locator', 'pointer', 'record', 'line', 'offset',
            'length', 'complete', 'next_offset') if key in observation}


def record_use(source, expected_input_event_id, observation, *, purpose, call_id=None):
    """Bind read purpose during the lookup, without judging completed use."""
    _require(isinstance(purpose, str) and purpose.strip(), 'An explicit lookup read purpose is required')
    coordinates = _source_coordinates(observation)
    core, root, owner = core_api._coordinates(source)
    platform, session = owner['platform'], owner['session_id']
    _require(platform in ('codex', 'claude'), 'The native lookup owner is unsupported')
    home = codex_home(source) if platform == 'codex' else claude_home(source)
    path = _path(home, platform, session)
    _require(path is not None, 'The owned original native transcript is unavailable')
    with core._transaction(root) as connection:
        binding = _binding(source, expected_input_event_id, current=True, transaction=connection)
        native = _native(path, platform, session)
        input_record, input_message = _bound_input_row(native, binding, platform)
        pending = [call for call in native.calls if call[0] > input_record
                   and isinstance(call[2], str) and bool(call[2])
                   and not any(result[2] == call[2] for result in native.results)]
        if call_id is not None:
            _require(isinstance(call_id, str) and bool(call_id), 'An explicit native call ID is required')
            pending = [call for call in pending if call[2] == call_id]
        _require(len(pending) == 1, 'One unique pending native lookup call is required; ambiguous calls are rejected')
        call_record, call_index, identifier, call = pending[0]
        _require(sum(c[2] == identifier for c in native.calls) == 1,
                 'The native pending call identity is replayed or ambiguous')
        payload = dict(schema_version='response-context-use.v1',
            input_binding=binding, native_input_record=input_record, native_input_digest=json_digest(input_message),
            original=reference_source(path, appendable=True), call_record=call_record,
            call_block_index=call_index, call_id=identifier, call_digest=json_digest(call),
            observation_digest=json_digest(observation), source=coordinates, purpose=purpose)
        reference = json_digest(payload)
        _schema(connection)
        connection.execute('INSERT OR IGNORE INTO core_response_context_uses VALUES (?,?,?,?)',
            (reference, platform, session, json.dumps(payload, ensure_ascii=False)))
    return dict(reference=reference, status='recorded')


def _judgment_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS core_response_use_judgments (
        reference TEXT PRIMARY KEY, platform TEXT NOT NULL, session_id TEXT NOT NULL,
        use_reference TEXT NOT NULL, payload_json TEXT NOT NULL)''')
    for operation in ('UPDATE', 'DELETE'):
        connection.execute(f'''CREATE TRIGGER IF NOT EXISTS immutable_response_use_judgment_{operation.lower()}
            BEFORE {operation} ON core_response_use_judgments
            BEGIN SELECT RAISE(ABORT, 'immutable response use judgment'); END''')
    connection.execute('''CREATE TABLE IF NOT EXISTS core_response_use_current (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, use_reference TEXT NOT NULL,
        reference TEXT NOT NULL, PRIMARY KEY(platform, session_id, use_reference))''')


def _use_payload(connection, reference, owner):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_response_context_uses'").fetchone()
    _require(exists is not None, 'The original read-use binding is unavailable')
    row = connection.execute('''SELECT payload_json FROM core_response_context_uses
        WHERE reference=? AND platform=? AND session_id=?''',
        (reference, owner['platform'], owner['session_id'])).fetchone()
    _require(row is not None, 'The original read use belongs to no matching native owner')
    payload = json.loads(row[0])
    _require(payload.get('schema_version') == 'response-context-use.v1' and json_digest(payload) == reference,
        'The immutable read-use binding is invalid')
    return payload


def record_use_judgments(source, expected_input_event_id, judgments, *, transaction=None):
    """Authenticate model post-use judgments within an existing Core operation.

    Every named read must already have one native result containing its exact
    producer object. The parent operation and these records share one transaction.
    """
    _require(isinstance(judgments, list), 'Explicit post-use judgment objects are required')
    seen = set()
    for judgment in judgments:
        _require(isinstance(judgment, dict) and set(judgment) == {
            'use_reference', 'disposition', 'summary', 'next_need'}, 'Explicit use, disposition, summary and next need are required')
        _require(judgment['disposition'] in ('keep', 'replace', 'omit') and all(
            isinstance(judgment[key], str) and judgment[key].strip() for key in ('use_reference', 'summary', 'next_need')),
            'A keep, replace or omit judgment with its conclusion and remaining need is required')
        _require(judgment['use_reference'] not in seen, 'A read use cannot receive ambiguous judgments in one operation')
        seen.add(judgment['use_reference'])
    core, root, owner = core_api._coordinates(source)
    platform, session = owner['platform'], owner['session_id']
    _require(platform in ('codex', 'claude'), 'The native responding owner is unsupported')
    home = codex_home(source) if platform == 'codex' else claude_home(source)
    path = _path(home, platform, session)
    _require(path is not None, 'The owned original native transcript is unavailable')
    references = []
    with core._transaction(root, transaction=transaction) as connection:
        binding = _binding(source, expected_input_event_id, current=True, transaction=connection)
        native = _native(path, platform, session)
        input_record, input_message = _bound_input_row(native, binding, platform)
        validated = []
        for judgment in judgments:
            payload = _use_payload(connection, judgment['use_reference'], owner)
            original_binding = _binding(source,
                payload['input_binding']['input_event_id'], transaction=connection)
            _require(payload['input_binding'] == original_binding and payload['original']['locator'] == path,
                     'The read-use input or original native owner differs')
            verify_source(payload['original'])
            _require(payload['native_input_record'] in native.user_role_prompts
                and json_digest(native.records[payload['native_input_record']]) == payload['native_input_digest'],
                'The original input that Core code bound to the read use differs')
            pair = _native_pair(native, payload, platform)
            _require(pair is not None, 'A post-use judgment requires one actual paired native result')
            value = pair[1][3]['output'] if platform == 'codex' else pair[1][3].get('content')
            _, count, occurrences = _output(value, platform, judgment['use_reference'], payload['observation_digest'], {})
            _require(count == occurrences == 1, 'A post-use judgment requires one exact read producer result')
            validated.append(dict(schema_version='response-use-judgment.v1', **judgment,
                input_binding=binding, native_input_record=input_record, native_input_digest=json_digest(input_message),
                original=reference_source(path, appendable=True), call_record=payload['call_record'],
                call_digest=payload['call_digest'], native_result_record=pair[1][0],
                native_result_block_index=pair[1][1], native_result_digest=json_digest(native.records[pair[1][0]]),
                result_block_digest=json_digest(pair[1][3]), observation_digest=payload['observation_digest']))
        _judgment_schema(connection)
        for payload in validated:
            reference = json_digest(payload)
            connection.execute('INSERT OR IGNORE INTO core_response_use_judgments VALUES (?,?,?,?,?)',
                (reference, platform, session, payload['use_reference'], json.dumps(payload, ensure_ascii=False)))
            connection.execute('''INSERT INTO core_response_use_current VALUES (?,?,?,?)
                ON CONFLICT(platform,session_id,use_reference) DO UPDATE SET reference=excluded.reference''',
                (platform, session, payload['use_reference'], reference))
            references.append(reference)
    return dict(status='recorded', references=references, judgment_count=len(references))


def _producer(value, reference, digest, replacement, *, marker='use_reference'):
    """Return transformed value, exact matches, and all reference occurrences."""
    if isinstance(value, dict):
        if value.get('type') in ('thinking', 'redacted_thinking') or 'signature' in value:
            return value, 0, 0
        if value.get(marker) == reference:
            original = {k: v for k, v in value.items() if k != marker}
            return (replacement, 1, 1) if json_digest(original) == digest else (value, 0, 1)
        changed, count, occurrences = {}, 0, 0
        for key, body in value.items():
            changed[key], found, seen = _producer(body, reference, digest, replacement, marker=marker)
            count += found
            occurrences += seen
        return (changed if count else value), count, occurrences
    if isinstance(value, list):
        changed, count, occurrences = [], 0, 0
        for body in value:
            item, found, seen = _producer(body, reference, digest, replacement, marker=marker)
            changed.append(item)
            count += found
            occurrences += seen
        return (changed if count else value), count, occurrences
    if not isinstance(value, str):
        return value, 0, 0
    decoder, parts, cursor, scan, count, occurrences = json.JSONDecoder(), [], 0, 0, 0, 0
    while scan < len(value):
        if value[scan] not in '{["':
            scan += 1
            continue
        try:
            frame, end = decoder.raw_decode(value, scan)
        except json.JSONDecodeError:
            scan += 1
            continue
        changed, found, seen = _producer(frame, reference, digest, replacement, marker=marker)
        occurrences += seen
        if found:
            parts.extend((value[cursor:scan], json.dumps(changed, ensure_ascii=False)))
            cursor, count = end, count + found
        scan = end
    if not count:
        return value, 0, occurrences
    parts.append(value[cursor:])
    return ''.join(parts), count, occurrences


def _output(value, platform, reference, digest, replacement, *, marker='use_reference'):
    if isinstance(value, str):
        return _producer(value, reference, digest, replacement, marker=marker)
    if not isinstance(value, list):
        return value, 0, 0
    block_type = 'input_text' if platform == 'codex' else 'text'
    result, count, occurrences = copy.deepcopy(value), 0, 0
    for index, block in enumerate(value):
        if (isinstance(block, dict) and block.get('type') == block_type
            and isinstance(block.get('text'), str) and 'signature' not in block):
            changed, found, seen = _producer(block['text'], reference, digest, replacement, marker=marker)
            occurrences += seen
            if found:
                result[index]['text'] = changed
                count += found
    return (result if count else value), count, occurrences


def _wire_input_index(request, platform, binding, original):
    if platform == 'codex':
        from context_lifecycle import _last_user_role_message
        item = _last_user_role_message(request['input'])
        _require(isinstance(item, dict), 'The wire message for the bound input is unavailable')
        text = _input_message_text(item, platform)
        _require(matches_input(text, binding) and json_digest(_clean(item)) == json_digest(original),
            'The wire message differs from the current input that Core code recorded')
        return max(n for n, value in enumerate(request['input']) if value is item)
    else:
        import claude_context_adapter as adapter
        item = adapter.bound_input_message(request['messages'], binding)
        _require(adapter.semantic_fingerprint(item) == adapter.semantic_fingerprint(original),
                 'The Claude wire message differs from the bound input')
        return max(n for n, value in enumerate(request['messages']) if isinstance(value, dict)
            and value.get('role') == 'user' and adapter.semantic_fingerprint(
                adapter._normalize_message('user', value.get('content'))) == adapter.semantic_fingerprint(item))


def _answer_record(native, payload):
    """Locate a supported original answer without gating the model's judgment."""
    start = payload['native_input_record']
    later = [record for record in native.user_role_prompts if record > start]
    end = later[0] if later else max(native.records, default=start) + 1
    answers = [record for record in native.finals if max(start, payload['native_result_record']) < record < end]
    return answers[-1] if answers else None


def _native_pair(native, payload, platform):
    calls = [call for call in native.calls if call[2] == payload['call_id']]
    results = [result for result in native.results if result[2] == payload['call_id']]
    if len(calls) != 1 or len(results) != 1:
        return None
    call, result = calls[0], results[0]
    if (call[:2] != (payload['call_record'], payload['call_block_index'])
        or json_digest(call[3]) != payload['call_digest'] or call[:2] >= result[:2]):
        return None
    if platform == 'codex':
        expected = 'custom_tool_call_output' if call[3]['type'] == 'custom_tool_call' else 'function_call_output'
        if result[3].get('type') != expected:
            return None
    return call, result


def _wire_pair(request, pair, platform):
    call, result = pair
    if platform == 'codex':
        calls = [(n, item) for n, item in enumerate(request['input']) if isinstance(item, dict)
                 and item.get('type') == call[3]['type'] and item.get('call_id') == call[2]]
        outputs = [(n, item) for n, item in enumerate(request['input']) if isinstance(item, dict)
                   and item.get('type') == result[3]['type'] and item.get('call_id') == call[2]]
        if (len(calls) == len(outputs) == 1 and calls[0][0] < outputs[0][0]
            and json_digest(_clean(calls[0][1])) == json_digest(call[3])
            and json_digest({k: v for k, v in _clean(outputs[0][1]).items() if k != 'output'})
                == json_digest({k: v for k, v in result[3].items() if k != 'output'})):
            return outputs[0][0], None, outputs[0][1]['output'], calls[0][0]
    else:
        import claude_context_adapter as adapter
        blocks = list(adapter._blocks(request['messages']))
        calls = [entry for entry in blocks if entry[2] == 'assistant'
                 and entry[3].get('type') == 'tool_use' and entry[3].get('id') == call[2]]
        outputs = [entry for entry in blocks if entry[2] == 'user'
                   and entry[3].get('type') == 'tool_result' and entry[3].get('tool_use_id') == call[2]]
        if (len(calls) == len(outputs) == 1 and calls[0][:2] < outputs[0][:2]
            and adapter._call_fingerprint(calls[0][3]) == adapter._call_fingerprint(call[3])
            and 'signature' not in outputs[0][3]
            and adapter.semantic_fingerprint({k: v for k, v in outputs[0][3].items() if k != 'content'})
                == adapter.semantic_fingerprint({k: v for k, v in result[3].items() if k != 'content'})):
            return outputs[0][0], outputs[0][1], outputs[0][3].get('content'), calls[0][0]
    return None


def _model_capsule(reference, purpose, summary, next_need, original, original_pointer):
    """Project needed use state and retrieval coordinates, not authentication."""
    return dict(use_reference=reference, purpose=purpose, summary=summary,
                next_need=next_need, original=original, original_pointer=original_pointer)


def _protected_coordinates(uses, *, server=False):
    """Project authenticated inspections into enclosing-result protection."""
    dispositions = ('keep', 'replace') if server else ('keep',)
    return {(use['record'], index) for use in uses if use['disposition'] in dispositions
            for index in use['block_indices']}


def select_uses(request, root, native_home, *, platform='codex', session_id=None, _inspect=None, _blocked=(),
                _retained_boundary=None):
    """Apply authenticated explicit post-use dispositions to the next generation."""
    from context_lifecycle import RequestSelection, ContextSelectionRejected, request_support_reason
    # A retained boundary supplies exclusion coordinates only. It cannot admit
    # current input or reach the mutation path; ordinary selection still needs
    # its current authenticated binding. Historical use/judgment guards remain.
    if _retained_boundary is not None and (platform != 'claude' or not isinstance(_inspect, list)
        or not isinstance(_retained_boundary, tuple) or len(_retained_boundary) != 2
        or any(type(value) is not int for value in _retained_boundary)
        or _retained_boundary[0] < 1 or _retained_boundary[1] < 0):
        return RequestSelection(request, 'preserved', 'use-binding-unverified')
    reason = request_support_reason(request, platform)
    if reason:
        return RequestSelection(request, 'preserved', reason)
    field = 'input' if platform == 'codex' else 'messages'
    if platform not in ('codex', 'claude') or not isinstance(request, dict) or not isinstance(request.get(field), list):
        return RequestSelection(request, 'preserved', 'input-unclassified')
    session = session_id if session_id is not None else request.get('prompt_cache_key')
    if not isinstance(session, str) or not session:
        return RequestSelection(request, 'preserved', 'session-unclassified')
    if platform == 'codex' and request.get('prompt_cache_key') != session:
        return RequestSelection(request, 'preserved', 'session-unclassified')
    source = ledger_access.source_environment(platform, session, root=root)
    try:
        core, root, owner = core_api._coordinates(source)
        with core._read_connection(root) as connection:
            if connection is None:
                return RequestSelection(request, 'unavailable', 'storage-unavailable')
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_response_context_uses'").fetchone():
                return RequestSelection(request, 'preserved', 'selection-absent')
            rows = connection.execute('''SELECT reference,payload_json FROM core_response_context_uses
                WHERE platform=? AND session_id=? ORDER BY rowid''', (platform, session)).fetchall()
            if not rows:
                return RequestSelection(request, 'preserved', 'selection-absent')
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'core_response_use_current', 'core_response_use_judgments'} <= tables:
                return RequestSelection(request, 'preserved', 'use-judgment-absent')
            judgment_rows = connection.execute('''SELECT c.use_reference,j.reference,j.payload_json
                FROM core_response_use_current c JOIN core_response_use_judgments j ON j.reference=c.reference
                WHERE c.platform=? AND c.session_id=? AND j.platform=c.platform AND j.session_id=c.session_id
                    AND j.use_reference=c.use_reference''', (platform, session)).fetchall()
            current_judgments = {use_reference: (reference, raw) for use_reference, reference, raw in judgment_rows}
            binding = None if _retained_boundary is not None else _binding(source, current=True, transaction=connection)
            path = _path(native_home, platform, session)
            _require(path is not None, 'The original native transcript is unavailable')
            native = _native(path, platform, session)
            if _retained_boundary is None:
                input_record, input_item = _bound_input_row(native, binding, platform)
                input_index = _wire_input_index(request, platform, binding, input_item)
            else:
                input_record, input_index = _retained_boundary
            eligible = []
            for reference, raw in rows:
                if reference not in current_judgments:
                    continue
                payload = json.loads(raw)
                if (payload.get('schema_version') != 'response-context-use.v1'
                    or json_digest(payload) != reference or payload['original']['locator'] != path):
                    continue
                original_binding = _binding(source,
                    payload['input_binding']['input_event_id'], transaction=connection)
                if payload['input_binding'] != original_binding:
                    continue
                try:
                    verify_source(payload['original'])
                except SourceReferenceError:
                    continue
                if (payload['native_input_record'] not in native.user_role_prompts
                    or json_digest(native.records[payload['native_input_record']]) != payload['native_input_digest']):
                    continue
                judgment_reference, judgment_raw = current_judgments[reference]
                judgment = json.loads(judgment_raw)
                if (json_digest(judgment) != judgment_reference
                    or judgment.get('schema_version') != 'response-use-judgment.v1'
                    or judgment.get('use_reference') != reference or judgment.get('disposition') not in ('keep', 'replace', 'omit')
                    or judgment.get('observation_digest') != payload['observation_digest']):
                    continue
                judgment_binding = _binding(source,
                    judgment['input_binding']['input_event_id'], transaction=connection)
                if judgment_binding != judgment['input_binding'] or judgment['original']['locator'] != path:
                    continue
                try:
                    verify_source(judgment['original'])
                except SourceReferenceError:
                    continue
                if (judgment['native_input_record'] not in native.user_role_prompts
                    or json_digest(native.records[judgment['native_input_record']]) != judgment['native_input_digest']):
                    continue
                pair = _native_pair(native, payload, platform)
                if pair is None:
                    continue
                if (pair[1][:2] != (judgment['native_result_record'], judgment['native_result_block_index'])
                    or json_digest(native.records[pair[1][0]]) != judgment['native_result_digest']
                    or json_digest(pair[1][3]) != judgment['result_block_digest']
                    or payload['call_digest'] != judgment['call_digest']):
                    continue
                wire = _wire_pair(request, pair, platform)
                if (wire is None or wire[0] == input_index or wire[3] == input_index
                    or (pair[1][0] > input_record) != (wire[0] > input_index)
                    or (pair[0][0] > input_record) != (wire[3] > input_index)):
                    continue
                answer_record = _answer_record(native, judgment)
                answer = None if answer_record is None else dict(locator=f'{path}#record={answer_record}',
                    item_digest=json_digest(native.records[answer_record]))
                locator = f'{path}#record={pair[1][0]}'
                pointer = '/payload/output' if platform == 'codex' else f'/message/content/{pair[1][1]}/content'
                disposition = judgment['disposition']
                capsule = (_model_capsule(reference, payload['purpose'], judgment['summary'], judgment['next_need'],
                                         locator, pointer) if disposition == 'replace' and _retained_boundary is None else {})
                native_output = pair[1][3]['output'] if platform == 'codex' else pair[1][3].get('content')
                _, native_count, native_occurrences = _output(native_output, platform, reference,
                                                              payload['observation_digest'], capsule)
                _, count, occurrences = _output(wire[2], platform, reference, payload['observation_digest'], capsule)
                if native_count == native_occurrences == count == occurrences == 1:
                    if _inspect is not None:
                        if platform == 'claude' or isinstance(native_output, str):
                            indices = (pair[1][1],)
                        else:
                            indices = tuple(index for index, block in enumerate(native_output) if _output(
                                [block], platform, reference, payload['observation_digest'], {})[1] == 1)
                        _inspect.append(dict(use_reference=reference, disposition=disposition,
                                             record=pair[1][0], block_indices=indices))
                    elif disposition != 'keep' and reference not in _blocked:
                        eligible.append((reference, payload, judgment_reference, pair, wire, capsule, answer,
                                         disposition, locator, pointer))
        if _inspect is not None:
            return RequestSelection(request, 'preserved', 'selection-inspected')
        if not eligible:
            return RequestSelection(request, 'preserved', 'use-active-or-unverified')
        if platform == 'claude':
            require_rewriteable_prefix(request)
        result, observations = copy.deepcopy(request), []
        for reference, payload, judgment_reference, pair, wire, capsule, answer, disposition, locator, pointer in eligible:
            message_index, block_index, _ = wire[:3]
            item = result[field][message_index]
            output = item['output'] if platform == 'codex' else item['content'][block_index]['content']
            changed, count, occurrences = _output(output, platform, reference, payload['observation_digest'], capsule)
            if count != 1 or occurrences != 1:
                continue
            before_digest = json_digest(item)
            if platform == 'codex':
                item['output'] = changed
            else:
                item['content'][block_index]['content'] = changed
            observations.append(dict(reference=reference,
                judgment_reference=judgment_reference,
                original=locator, original_pointer=pointer,
                source=payload['source'], native_answer=answer,
                observation_digest=payload['observation_digest'], disposition=disposition,
                current_input_event_id=binding['input_event_id'], message_index=message_index,
                original_item_digest=json_digest(native.records[pair[1][0]]),
                original_call_digest=payload['call_digest'], before_item_digest=before_digest,
                forwarded_item_digest=json_digest(item)))
        for observation in observations:
            observation['forwarded_item_digest'] = json_digest(result[field][observation['message_index']])
        return RequestSelection(result, 'selected' if observations else 'preserved',
                                ('explicit-use-omission' if all(o['disposition'] == 'omit' for o in observations)
                                 else 'explicit-use-replacement') if observations else 'use-not-in-request', tuple(observations))
    except SignedPrefixSelectionUnsupported as error:
        return RequestSelection(request, 'preserved', error.reason)
    except (OSError, sqlite3.Error, json.JSONDecodeError):
        return RequestSelection(request, 'unavailable', 'storage-unavailable')
    except (ResponseUseRejected, ContextSelectionRejected, core_api.CompletionAssessmentError,
            SourceReferenceError, NativeContextRejected):
        return RequestSelection(request, 'preserved', 'use-binding-unverified')
