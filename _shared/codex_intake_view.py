"""Authenticate the Codex current-authority view of pure intake receipts.

This projection concerns registered intake control envelopes only. It neither
classifies source bodies nor edits Core, native or strict originals.
"""
from __future__ import annotations

import copy
import glob
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import completion_assessment as core_api
from native_transcript import codex_transcript_path
import response_context_use as native_api
from source_reference import SourceReferenceError, reference_source, source_text, verify_source


class IntakeViewRejected(ValueError):
    """An expected shape or origin cannot authorize omission."""


def _require(condition):
    if not condition:
        raise IntakeViewRejected('Current intake view is unverified')


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _rows(reference):
    rows = {n: json.loads(line, object_pairs_hook=_unique_keys)
            for n, line in enumerate(source_text(reference).splitlines(), 1) if line.strip()}
    _require(all(isinstance(row, dict) for row in rows.values()))
    return rows


def _native(rows, session):
    owners = [row.get('payload') for row in rows.values() if row.get('type') == 'session_meta']
    _require(owners and all(isinstance(owner, dict) and owner.get('id') == session for owner in owners))
    payloads = {n: row.get('payload') for n, row in rows.items() if row.get('type') == 'response_item'}
    _require(all(isinstance(item, dict) for item in payloads.values()))
    records = {n: native_api._clean(item) for n, item in payloads.items()}
    humans = tuple(n for n, item in payloads.items() if item.get('type') == 'message'
        and item.get('role') == 'user' and _metadata(item, 'user.text') is not None)
    # Only human bindings consume this snapshot; call/result/answer bodies are
    # preserved and do not acquire a new administrative classification.
    return native_api._Native(records, humans, (), (), ())


def _metadata(item, kind):
    metadata = item.get('internal_chat_message_metadata_passthrough')
    required = {'turn_id', 'content_item_kinds'}
    if (not isinstance(metadata, dict) or not required <= set(metadata)
        or set(metadata) - required - {'create_time'}
        or metadata['content_item_kinds'] != [kind]
        or not isinstance(metadata['turn_id'], str) or not metadata['turn_id']):
        return None
    if 'create_time' in metadata:
        value = metadata['create_time']
        if not (type(value) is int or (type(value) is float and math.isfinite(value))):
            return None
    # Native time is a known representation field, never binding or order proof.
    return metadata['turn_id']


def _pure(item, root, home, session):
    if (set(item) != {'id', 'type', 'role', 'content', 'internal_chat_message_metadata_passthrough'}
        or item.get('type') != 'message' or item.get('role') != 'developer'
        or not isinstance(item.get('id'), str) or not item['id']
        or _metadata(item, 'hooks.additional_context') is None):
        return None
    content = item.get('content')
    if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
        or set(content[0]) != {'type', 'text'} or content[0]['type'] != 'input_text'
        or not isinstance(content[0]['text'], str)):
        return None
    text = content[0]['text']
    prefix, suffix = '\n[session-intent-receipt]\n', '\n[/session-intent-receipt]\n'
    if not text.startswith(prefix) or not text.endswith(suffix):
        return None
    try:
        receipt = json.loads(text[len(prefix):-len(suffix)], object_pairs_hook=_unique_keys)
    except (json.JSONDecodeError, IntakeViewRejected):
        return None
    if not isinstance(receipt, dict) or not isinstance(receipt.get('input_event_id'), str):
        return None
    expected = dict(schema_version='session-intent-observation-receipt.v1', intake_status='observed',
        ledger_root=str(root), platform='codex', session_id=session,
        state_path=str(root / 'codex' / session / 'intent-state.json'),
        events_path=str(root / 'codex' / session / 'intent-events.jsonl'),
        database_path=str(root / 'ghost-state.sqlite3'), storage_backend='sqlite',
        input_event_id=receipt['input_event_id'])
    if 'context_selection' in receipt:
        expected['context_selection'] = dict(native_provider='ghost_alice_context',
            procedure=str(Path(__file__).resolve().with_name('context_lifecycle.md')), native_dir=str(home))
    if receipt != expected or text != prefix + json.dumps(expected, ensure_ascii=True, separators=(',', ':')) + suffix:
        return None
    return receipt


def _registered(rows, text, session):
    matches = 0
    for row in rows.values():
        if (row.get('schema_version') != 'strict-hook-output.v1' or row.get('platform') != 'codex'
            or row.get('session_id') != session or row.get('hook_id') != 'session-intent'
            or row.get('event') != 'UserPromptSubmit' or type(row.get('exit_code')) is not int
            or row['exit_code'] != 0 or not isinstance(row.get('stdout'), str)):
            continue
        try:
            output = json.loads(row['stdout'], object_pairs_hook=_unique_keys)
        except (json.JSONDecodeError, IntakeViewRejected):
            continue
        hook = output.get('hookSpecificOutput') if isinstance(output, dict) else None
        if (isinstance(hook, dict) and hook.get('hookEventName') == 'UserPromptSubmit'
            and hook.get('additionalContext') == text):
            matches += 1
    return matches == 1


def _wire_match(request, original):
    matches = [n for n, item in enumerate(request['input']) if isinstance(item, dict)
               and native_api._clean(item) == original]
    return matches[0] if len(matches) == 1 else None


def _matches_input_event(native, record, binding):
    """Compare established plaintext identity only; never choose body use."""
    item = native.records[record]
    _require(set(item) == {'id', 'type', 'role', 'content'}
        and item.get('type') == 'message' and item.get('role') == 'user'
        and isinstance(item.get('id'), str) and bool(item['id'])
        and sum(value.get('id') == item['id'] for value in native.records.values()) == 1)
    content = item['content']
    _require(isinstance(content, list) and bool(content) and all(isinstance(block, dict)
        and set(block) == {'type', 'text'} and block['type'] == 'input_text'
        and isinstance(block['text'], str) for block in content))
    text = native_api._human_text(item, 'codex')
    try:
        encoded = text.encode('utf-8')
    except UnicodeEncodeError as error:
        raise IntakeViewRejected('Original plaintext identity is unestablished') from error
    return (len(text) == binding['input_char_count']
            and 'sha256:' + hashlib.sha256(encoded).hexdigest() == binding['input_digest'])


def _human(rows, native, turn, binding):
    # Every same-turn user is examined. Only established different plaintext
    # rules an item out of this event mapping; its body remains untouched.
    matches = [n for n, item in native.records.items()
               if item.get('type') == 'message' and item.get('role') == 'user'
               and isinstance(metadata := rows[n]['payload'].get('internal_chat_message_metadata_passthrough'), dict)
               and metadata.get('turn_id') == turn and _matches_input_event(native, n, binding)]
    _require(len(matches) == 1)
    record = matches[0]
    _require(_metadata(rows[record]['payload'], 'user.text') == turn)
    # Reuse the real native binding check, with this explicitly identified turn.
    scope = native_api._Native(native.records, (record,), native.calls, native.results, native.finals)
    bound_record, item = native_api._bound_human(scope, binding, 'codex')
    _require(isinstance(item.get('id'), str) and bool(item['id'])
             and sum(i.get('id') == item['id'] for i in native.records.values()) == 1)
    return bound_record, item


def select_intake_view(request, root, native_home, strict_log_root):
    from context_lifecycle import RequestSelection, _current_input, ContextSelectionRejected, fingerprint
    preserved = RequestSelection(request, 'preserved', 'intake-view-unverified')
    if strict_log_root is None:
        return RequestSelection(request, 'preserved', 'intake-view-disabled')
    if (not isinstance(strict_log_root, (str, Path)) or not Path(strict_log_root).is_absolute()
        or not isinstance(native_home, (str, Path)) or not Path(native_home).is_absolute()
        or not isinstance(request, dict) or not isinstance(request.get('input'), list)
        or 'previous_response_id' in request or 'conversation' in request):
        return preserved
    session = request.get('prompt_cache_key')
    if not isinstance(session, str) or not session:
        return preserved
    source = dict(GHOST_ALICE_SESSION_INTENT_ROOT=str(root), GHOST_ALICE_PLATFORM='codex',
                  GHOST_ALICE_SESSION_ID=session)
    try:
        core, root, owner = core_api._coordinates(source)
        home = Path(native_home).resolve()
        paths = [p for area in ('sessions', 'archived_sessions')
                 for p in (home / area).rglob('*-' + glob.escape(session) + '.jsonl')]
        if len(paths) != 1:
            return preserved
        # Validate original header types before the shared locator dereferences
        # them. I/O/JSON failures retain unavailable handling; this is a typed
        # source rejection, never a blanket processing-exception catch.
        with paths[0].open(encoding='utf-8') as stream:
            header = json.loads(stream.readline(), object_pairs_hook=_unique_keys)
        _require(isinstance(header, dict) and header.get('type') == 'session_meta'
            and isinstance(header.get('payload'), dict) and header['payload'].get('id') == session)
        path = codex_transcript_path(str(home), session)
        if path is None:
            return preserved
        native_ref = reference_source(path)
        rows = _rows(native_ref)
        native = _native(rows, session)
        _require(Path(path).is_relative_to(home))
        receipts = {n: value for n, row in rows.items() if row.get('type') == 'response_item'
                    and (value := _pure(row['payload'], root, home, session)) is not None}
        if not receipts:
            return preserved
        strict_path = Path(strict_log_root).resolve() / 'codex' / session / 'strict-hook-output.jsonl'
        strict_ref = reference_source(str(strict_path))
        strict_rows = _rows(strict_ref)
        with core._read_connection(root) as connection:
            if connection is None:
                return RequestSelection(request, 'unavailable', 'storage-unavailable')
            state = _current_input(core, root, owner, connection)
            event = state['latest_input_event_id']
            binding = core_api.response_input_binding(source, event, transaction=connection)
            # Reject unestablished original plaintext before the native binding
            # API encodes it; its unexpected processing errors still propagate.
            _require(bool(native.humans) and _matches_input_event(native, native.humans[-1], binding))
            current_record, current_human = native_api._bound_human(native, binding, 'codex')
            turn = _metadata(rows[current_record]['payload'], 'user.text')
            _require(turn is not None and _human(rows, native, turn, binding) == (current_record, current_human))
            current_index = native_api._wire_human(request, 'codex', binding, current_human)
            _require(_wire_match(request, current_human) == current_index)
            latest = [n for n, value in receipts.items() if value['input_event_id'] == event]
            _require(len(latest) == 1)
            latest_record = latest[0]
            _require(latest_record > current_record
                and _metadata(rows[latest_record]['payload'], 'hooks.additional_context') == turn
                and _registered(strict_rows, rows[latest_record]['payload']['content'][0]['text'], session))
            latest_index = _wire_match(request, native.records[latest_record])
            _require(latest_index is not None and latest_index > current_index)
            current_sequence = core._trusted_current_intake_sequence(connection, state)
            _require(current_sequence is not None)
            removed, observations = set(), []
            for record, receipt in receipts.items():
                old_event = receipt['input_event_id']
                if old_event == event or sum(v['input_event_id'] == old_event for v in receipts.values()) != 1:
                    continue
                try:
                    old_binding = core_api.response_input_binding(source, old_event, transaction=connection)
                    sequence = connection.execute('''SELECT sequence FROM intent_events WHERE platform=?
                        AND session_id=? AND json_extract(event_json, '$.event_id')=?''',
                        ('codex', session, old_event)).fetchone()[0]
                    human_record, _ = _human(rows, native,
                        _metadata(rows[record]['payload'], 'hooks.additional_context'), old_binding)
                    index = _wire_match(request, native.records[record])
                    _require(sequence < current_sequence and human_record < record < current_record
                        and index is not None and index < current_index
                        and _registered(strict_rows, rows[record]['payload']['content'][0]['text'], session))
                except (IntakeViewRejected, native_api.ResponseUseRejected, core_api.CompletionAssessmentError):
                    continue
                removed.add(index)
                observations.append(dict(kind='current-intake-view', original=f'{path}#record={record}',
                    current_input_event_id=event, historical_input_event_id=old_event,
                    removed_item_digest=fingerprint(native.records[record])))
            # Both reads must still describe the exact original bytes, including
            # no append during authentication. Core is held in a read snapshot.
            verify_source(native_ref)
            verify_source(strict_ref)
        if not removed:
            return preserved
        result = copy.deepcopy(request)
        result['input'] = [item for n, item in enumerate(result['input']) if n not in removed]
        return RequestSelection(result, 'selected', 'authenticated-intake-view', tuple(observations))
    except (OSError, sqlite3.Error):
        return RequestSelection(request, 'unavailable', 'storage-unavailable')
    except json.JSONDecodeError:
        return RequestSelection(request, 'unavailable', 'record-unreadable')
    except (IntakeViewRejected, native_api.ResponseUseRejected, ContextSelectionRejected,
            SourceReferenceError, core_api.CompletionAssessmentError):
        return preserved
