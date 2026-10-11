"""Native transcript boundaries shared by intake and completion consumers.

Dependencies: Python standard library. Native metadata, never message wording,
identifies the actor. Unresolved transport never becomes user authority.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


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


def native_tool_activity(platform, session_id, native_input):
    """Observe typed current-turn calls; unavailable evidence is not absence.

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
        active_turn = None
        for row in rows:
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
                return result('observed', 'current-turn-tool-call')
            if active_turn == turn_id and event_turn is None:
                return result('unavailable', 'native-tool-turn-unavailable')
        return result('absent', 'current-turn-has-no-tool-call')
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
    if entry.get('isMeta') or entry.get('isSidechain'):
        return None
    if is_task_notification(entry):
        return 'notification'
    origin = entry.get('origin')
    if (is_actual_user_prompt(entry) and isinstance(origin, dict) and origin.get('kind') == 'human'
            and entry.get('promptSource') != 'system' and entry.get('turnOrigin') in (None, 'human')):
        return 'human'
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


def resolve_staged_input(observation, session_id, cursors):
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
            prefix = stream.read(boundary['offset'])
            if len(prefix) != boundary['offset'] or hashlib.sha256(prefix).hexdigest() != boundary['prefix_digest']:
                return None
            cursor_key = hashlib.sha256(json.dumps([boundary['path'], identity.st_dev, identity.st_ino]).encode()).hexdigest()
            start = max(boundary['offset'], cursors.get(cursor_key, 0))
            if start > identity.st_size:
                return None
            stream.seek(start)
            content = stream.read()
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
                or entry.get('isMeta') or entry.get('isSidechain')):
            continue
        if entry.get('type') not in ('user', 'attachment'):
            continue
        # promptId belongs to a native prompt grain and may repeat. It is only
        # an additional correlation constraint, never observation identity.
        native_id = entry.get('promptId')
        if native_id is not None and native_id != observation.get('native_prompt_id'):
            continue
        text = _input_text(entry)
        if (text is None or len(text) != observation['input_char_count']
                or 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest() != observation['input_digest']):
            continue
        matches.append(dict(actor=native_input_actor(entry), cursor_key=cursor_key,
                            start_offset=row_start, end_offset=offset))
    return matches[0] if len(matches) == 1 and matches[0]['actor'] is not None else None
