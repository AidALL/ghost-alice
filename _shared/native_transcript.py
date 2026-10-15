"""Native transcript boundaries shared by intake and completion consumers.

Dependencies: Python standard library. Native metadata, never message wording,
identifies the actor. Unresolved transport never becomes user authority.
"""
from __future__ import annotations

import hashlib
import glob
import json
import os
from pathlib import Path


def codex_transcript_path(platform_dir, session_id):
    """Find one host-owned transcript by native session identity, without copying it."""
    if not isinstance(platform_dir, str) or not Path(platform_dir).is_absolute():
        return None
    matches = []
    for area in ('sessions', 'archived_sessions'):
        matches.extend((Path(platform_dir) / area).rglob('*-' + glob.escape(session_id) + '.jsonl'))
    if len(matches) != 1:
        return None
    path = matches[0]
    try:
        with path.open() as stream:
            header = json.loads(stream.readline())
    except (OSError, ValueError):
        return None
    if header.get('type') != 'session_meta' or header.get('payload', {}).get('id') != session_id:
        return None
    return str(path.resolve())


def codex_transcript_contains_response(path, turn_id, text):
    """A session filename alone does not establish coverage of the current turn."""
    current_turn = None
    with Path(path).open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            payload = row.get('payload', {})
            if row.get('type') == 'turn_context':
                current_turn = payload.get('turn_id')
            if (current_turn == turn_id and row.get('type') == 'response_item'
                    and payload.get('type') == 'message' and payload.get('role') == 'assistant'):
                body = '\n'.join(p.get('text', '') for p in payload.get('content', [])
                    if isinstance(p, dict) and p.get('type') == 'output_text')
                if body == text:
                    return True
    return False


def read_entries(path_value, session=None):
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        return []
    try:
        lines = Path(path_value).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and (session is None or row.get('sessionId') == session):
            entries.append(row)
    return entries


def is_task_notification(entry):
    message = entry.get('attachment') if entry.get('type') == 'attachment' else entry
    if not isinstance(message, dict):
        return False
    origin = message.get('origin')
    if (entry.get('type') == 'user' and entry.get('promptSource') == 'system'
            and entry.get('turnOrigin') == 'peer' and isinstance(origin, dict)
            and origin.get('kind') == 'peer' and origin.get('handback') is True
            and isinstance(origin.get('senderTaskId'), str) and origin['senderTaskId']
            and origin.get('from') == origin['senderTaskId']):
        return True
    native_origin = (isinstance(origin, dict) and origin.get('kind') == 'task-notification'
                     and origin.get('producer') == 'session-task')
    return native_origin and (
        (entry.get('type') == 'user' and entry.get('promptSource') == 'system'
         and entry.get('turnOrigin') == 'task_notification')
        or (entry.get('type') == 'attachment' and message.get('type') == 'queued_command'
            and message.get('commandMode') == 'task-notification'))


def is_actual_user_prompt(entry):
    if entry.get('type') != 'user' or entry.get('isMeta') or is_task_notification(entry):
        return False
    message = entry.get('message')
    if not isinstance(message, dict) or message.get('role') != 'user':
        return False
    content = message.get('content')
    items = content if isinstance(content, list) else [content]
    return bool(content) and not any(isinstance(item, dict) and item.get('type') == 'tool_result'
                                     for item in items)


def assistant_text_this_turn(entries):
    """Select the last Claude assistant message after the latest human input."""
    start = next((i for i in range(len(entries) - 1, -1, -1)
                  if is_actual_user_prompt(entries[i])), -1)
    for entry in reversed(entries[start + 1:]):
        message = entry.get('message')
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            continue
        content = message.get('content')
        items = content if isinstance(content, list) else [content]
        return '\n'.join(item if isinstance(item, str) else item['text']
            for item in items if isinstance(item, str) or (isinstance(item, dict)
                and item.get('type') == 'text' and isinstance(item.get('text'), str)))
    return ''


def stop_response_text(native_input):
    """Select one native Stop body for both Core and its completion consumer.

    Inline event text outranks a transcript that can lag behind that event.
    Transcript fallback retains native session and turn boundaries.
    """
    data = native_input if isinstance(native_input, dict) else {}
    for key in ('last_assistant_message', 'lastAssistantMessage', 'prompt_response', 'promptResponse'):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    entries = read_entries(data.get('transcript_path') or data.get('transcriptPath'))
    session = data.get('session_id') or data.get('sessionId')
    if not isinstance(session, str) or not session:
        return ''
    owners = [row.get('payload', {}).get('id') for row in entries
              if row.get('type') == 'session_meta' and isinstance(row.get('payload'), dict)]
    if owners:
        turn = data.get('turn_id')
        contexts = [i for i, row in enumerate(entries) if row.get('type') == 'turn_context'
                    and isinstance(row.get('payload'), dict)]
        if (any(owner != session for owner in owners) or not turn or not contexts
                or entries[contexts[-1]]['payload'].get('turn_id') != turn):
            return ''
        for row in reversed(entries[contexts[-1] + 1:]):
            body = row.get('payload', {})
            if row.get('type') != 'response_item' or not isinstance(body, dict):
                continue
            if body.get('type') == 'message' and body.get('role') == 'user':
                return ''
            if body.get('type') != 'message' or body.get('role') != 'assistant':
                continue
            metadata = body.get('internal_chat_message_metadata_passthrough', {})
            if metadata.get('turn_id') != turn:
                return ''
            return '\n'.join(part['text'] for part in body.get('content', [])
                if isinstance(part, dict) and part.get('type') == 'output_text'
                and isinstance(part.get('text'), str))
        return ''
    return assistant_text_this_turn([row for row in entries
        if row.get('sessionId') == session and not row.get('isSidechain')])


def completion_scope(native_input):
    """Preserve the Stop phase even when the host supplies its body in a transcript."""
    observed = native_input if isinstance(native_input, dict) else {}
    text = stop_response_text(observed)
    return dict(text=text, phase='final' if text or observed.get('hook_event_name') == 'Stop'
                else 'before_tool', native_input=observed)


def native_tool_activity(platform, session_id, native_input, *, input_observation=None):
    """Observe calls after the current human input; unavailable evidence is not absence.

    This establishes native activity only. It neither judges the response's
    meaning nor substitutes for the current Core route or authenticated review.
    """
    def result(status, reason):
        return dict(status=status, reason=reason)

    data = native_input if isinstance(native_input, dict) else {}
    if (platform not in {'claude', 'codex'} or not isinstance(session_id, str) or not session_id
            or (data.get('session_id') or data.get('sessionId')) != session_id):
        return result('unavailable', 'native-owner-mismatch')
    path = data.get('transcript_path') or data.get('transcriptPath')
    if not isinstance(path, str) or not Path(path).is_absolute():
        return result('unavailable', 'native-transcript-path-missing')
    try:
        rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    except (OSError, ValueError, UnicodeError):
        return result('unavailable', 'native-transcript-unreadable')
    if not rows or any(not isinstance(row, dict) for row in rows):
        return result('unavailable', 'native-transcript-invalid')
    if platform == 'codex':
        owners = [row.get('payload', {}).get('id') for row in rows
                  if row.get('type') == 'session_meta' and isinstance(row.get('payload'), dict)]
        if not owners or any(owner != session_id for owner in owners):
            return result('unavailable', 'native-transcript-owner-mismatch')
        turn_id = data.get('turn_id')
        contexts = [row['payload'].get('turn_id') for row in rows
                    if row.get('type') == 'turn_context' and isinstance(row.get('payload'), dict)]
        if not isinstance(turn_id, str) or not turn_id or not contexts or contexts[-1] != turn_id:
            return result('unavailable', 'native-turn-unavailable')
        # A host turn can contain several human inputs. Native actor metadata
        # locates the latest human message; the ledger observation binds its
        # content to this input independently of tool or hook message wording.
        human = None
        for index, row in enumerate(rows):
            payload = row.get('payload')
            if (row.get('type') != 'response_item' or not isinstance(payload, dict)
                    or payload.get('type') != 'message' or payload.get('role') != 'user'):
                continue
            metadata = payload.get('internal_chat_message_metadata_passthrough')
            if (isinstance(metadata, dict) and metadata.get('turn_id') == turn_id
                    and isinstance(metadata.get('content_item_kinds'), list)
                    and 'user.text' in metadata['content_item_kinds']):
                human = (index, payload)
        if human is None:
            return result('unavailable', 'native-user-boundary-unavailable')
        content = human[1].get('content')
        texts = [part.get('text') for part in content
                 if isinstance(part, dict) and part.get('type') == 'input_text'] if isinstance(content, list) else []
        if not texts or any(not isinstance(text, str) for text in texts):
            return result('unavailable', 'native-user-text-unavailable')
        text = ''.join(texts)
        if (not isinstance(input_observation, dict)
                or input_observation.get('input_char_count') != len(text)
                or input_observation.get('input_digest') != 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()):
            return result('unavailable', 'native-input-observation-mismatch')
        active_turn = turn_id
        for row in rows[human[0] + 1:]:
            payload = row.get('payload')
            if not isinstance(payload, dict):
                continue
            if row.get('type') == 'turn_context':
                active_turn = payload.get('turn_id')
            if (row.get('type') != 'response_item'
                    or payload.get('type') not in {'custom_tool_call', 'function_call'}):
                continue
            metadata = payload.get('internal_chat_message_metadata_passthrough')
            event_turn = metadata.get('turn_id') if isinstance(metadata, dict) else None
            if event_turn == turn_id:
                return result('observed', 'current-input-tool-call')
            if active_turn == turn_id and event_turn is None:
                return result('unavailable', 'native-tool-turn-unavailable')
        return result('absent', 'current-input-has-no-tool-call')
    owned = [row for row in rows if row.get('sessionId') == session_id and not row.get('isSidechain')]
    for index in range(len(owned) - 1, -1, -1):
        if is_actual_user_prompt(owned[index]):
            for row in owned[index + 1:]:
                message = row.get('message')
                if (row.get('type') != 'assistant' or not isinstance(message, dict)
                        or message.get('role') != 'assistant'):
                    continue
                content = message.get('content')
                if isinstance(content, list) and any(isinstance(item, dict)
                        and item.get('type') == 'tool_use' for item in content):
                    return result('observed', 'current-turn-tool-call')
            return result('absent', 'current-turn-has-no-tool-call')
    return result('unavailable', 'native-user-boundary-unavailable')


def capture_append_boundary(path_value):
    """Capture an immutable prefix, not a timing guess about the pending row."""
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        return None
    path = Path(path_value).resolve()
    try:
        with path.open('rb') as stream:
            identity = os.fstat(stream.fileno())
            content = stream.read()
    except FileNotFoundError:
        return dict(path=str(path), device=None, inode=None, offset=0,
                    prefix_digest=hashlib.sha256(b'').hexdigest())
    except OSError:
        return None
    # An incomplete final line was not committed; inspect it after its newline arrives.
    offset = content.rfind(b'\n') + 1
    return dict(path=str(path), device=identity.st_dev, inode=identity.st_ino, offset=offset,
                prefix_digest=hashlib.sha256(content[:offset]).hexdigest())


def native_input_actor(entry):
    """Require positive host origin; message wording supplies no actor evidence."""
    if entry.get('isSidechain'):
        return None
    if is_task_notification(entry):
        return 'notification'
    if entry.get('isMeta'):
        return None
    origin = entry.get('origin')
    if (is_actual_user_prompt(entry) and isinstance(origin, dict) and origin.get('kind') == 'human'
            and entry.get('promptSource') != 'system' and entry.get('turnOrigin') in (None, 'human')):
        return 'human'
    if (is_actual_user_prompt(entry) and origin is None
        and entry.get('entrypoint') == 'sdk-cli' and entry.get('userType') == 'external'
        and entry.get('promptSource') == 'sdk' and entry.get('turnOrigin') == 'sdk'
        and (entry.get('isMeta') is None or entry.get('isMeta') is False)
        and (entry.get('isSidechain') is None or entry.get('isSidechain') is False)):
        content = entry['message'].get('content')
        if isinstance(content, str) or (isinstance(content, list) and content and all(
            isinstance(block, dict) and block.get('type') == 'text'
            and isinstance(block.get('text'), str) and not set(block) - {'type', 'text', 'cache_control'}
            for block in content)):
            return 'sdk'
    return None


def _input_text(entry):
    if entry.get('type') == 'attachment':
        attachment = entry.get('attachment')
        return attachment.get('prompt') if isinstance(attachment, dict) else None
    message = entry.get('message')
    content = message.get('content') if isinstance(message, dict) else None
    if isinstance(content, list):
        texts = [item.get('text') for item in content if isinstance(item, dict) and item.get('type') == 'text']
        return ''.join(texts) if texts and all(isinstance(text, str) for text in texts) else None
    return content if isinstance(content, str) else None


def matches_input_text(content, binding):
    """Correlate a hook prompt with a full text or one unique complete block.

    Whole plaintext envelopes retain either native concatenation or the host's
    newline-joined prompt serialization, without whitespace normalization.
    Native actor metadata authenticates the actor separately. Desktop can add
    host guidance beside the submitted prompt; this match removes no sibling
    and never treats a substring or a textual marker as origin evidence.
    """
    if (not isinstance(binding, dict) or type(binding.get('input_char_count')) is not int
        or not isinstance(binding.get('input_digest'), str)):
        return False
    def exact(text):
        return (isinstance(text, str) and len(text) == binding['input_char_count']
                and 'sha256:' + hashlib.sha256(text.encode()).hexdigest() == binding['input_digest'])
    if isinstance(content, str):
        return exact(content)
    if (not isinstance(content, list) or any(not isinstance(block, dict)
        or not isinstance(block.get('type'), str)
        or (block['type'] == 'text' and not isinstance(block.get('text'), str)) for block in content)):
        return False
    texts = [block['text'] for block in content if block['type'] == 'text']
    if texts and (exact(''.join(texts)) or exact('\n'.join(texts))):
        return True
    return sum(block.get('type') == 'text' and not set(block) - {'type', 'text', 'cache_control'}
               and exact(block.get('text')) for block in content) == 1


def resolve_staged_input(observation, session_id, cursors, *, allow_unknown=False):
    """Return one native actor/row locator, or leave the transport unresolved.

    A digest correlates content within an authenticated append window; it does
    not classify content. Exact native origin supplies the actor independently.
    """
    boundary = observation.get('transcript_boundary')
    if not isinstance(boundary, dict):
        return None
    try:
        with Path(boundary['path']).open('rb') as stream:
            identity = os.fstat(stream.fileno())
            if boundary['inode'] is not None and (identity.st_dev, identity.st_ino) != (boundary['device'], boundary['inode']):
                return None
            raw = stream.read()
            prefix = raw[:boundary['offset']]
            if len(prefix) != boundary['offset'] or hashlib.sha256(prefix).hexdigest() != boundary['prefix_digest']:
                return None
            cursor_key = hashlib.sha256(json.dumps([boundary['path'], identity.st_dev, identity.st_ino]).encode()).hexdigest()
            start = max(boundary['offset'], cursors.get(cursor_key, 0))
            if start > len(raw):
                return None
            content = raw[start:]
    except (OSError, KeyError, TypeError, ValueError):
        return None
    matches = []
    offset = start
    for line in content.splitlines(keepends=True):
        row_start = offset
        offset += len(line)
        if not line.endswith(b'\n'):
            break
        try:
            entry = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if (not isinstance(entry, dict) or entry.get('sessionId') != session_id
                or (entry.get('isMeta') and not is_task_notification(entry)) or entry.get('isSidechain')):
            continue
        if entry.get('type') not in ('user', 'attachment'):
            continue
        # promptId belongs to a native prompt grain and may repeat. It is only
        # an additional correlation constraint, never observation identity.
        native_id = entry.get('promptId')
        if native_id is not None and native_id != observation.get('native_prompt_id'):
            continue
        content = (entry.get('message', {}).get('content') if isinstance(entry.get('message'), dict)
                   else _input_text(entry))
        if not matches_input_text(content, observation):
            continue
        match = dict(actor=native_input_actor(entry), cursor_key=cursor_key,
                     start_offset=row_start, end_offset=offset)
        if match['actor'] == 'sdk':
            source = dict(storage='source-reference.v1', locator=boundary['path'],
                digest='sha256:' + hashlib.sha256(raw[:offset]).hexdigest(), byte_length=offset, appendable=True)
            match.update(native_source=dict(source, source_id='sha256:' + hashlib.sha256(json.dumps(
                source, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()),
                native_file_identity=dict(device=identity.st_dev, inode=identity.st_ino))
        matches.append(match)
    return matches[0] if len(matches) == 1 and (matches[0]['actor'] is not None or allow_unknown is True) else None
