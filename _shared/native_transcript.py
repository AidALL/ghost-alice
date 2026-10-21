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

from text_lines import committed_byte_lines, committed_length, committed_lines, committed_records

# Host entrypoints whose SDK-started turns Core reads as non-human context.
SDK_ENTRYPOINTS = frozenset({'sdk-cli', 'claude-desktop'})
# Every key Claude desktop 2.1.295-2.1.299 writes on an app event delivered
# while Claude works. Its marker is the missing origin, so any other key keeps
# the row unknown instead of removing a human input's authority.
APP_EVENT_KEYS = frozenset({'type', 'commandMode', 'prompt', 'source_uuid', 'timestamp', 'delivery_id'})
# Actors a located row can resolve to; None means the markers are unknown.
RESOLVED_ACTORS = ('human', 'sdk', 'context', 'notification')


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
    for line in committed_lines(Path(path).read_bytes()).values():
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
        lines = committed_lines(Path(path_value).read_text(encoding='utf-8', errors='replace')).values()
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


def _peer_hand_back(origin):
    return (isinstance(origin, dict) and origin.get('kind') == 'peer' and origin.get('handback') is True
            and isinstance(origin.get('senderTaskId'), str) and bool(origin['senderTaskId'])
            and origin.get('from') == origin['senderTaskId'])


def is_task_notification(entry):
    message = entry.get('attachment') if entry.get('type') == 'attachment' else entry
    if not isinstance(message, dict):
        return False
    origin = message.get('origin')
    if _peer_hand_back(origin) and (
            (entry.get('type') == 'user' and entry.get('promptSource') == 'system'
             and entry.get('turnOrigin') == 'peer')
            or (entry.get('type') == 'attachment' and message.get('type') == 'queued_command'
                and message.get('commandMode') == 'prompt')):
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


def _queued_prompt(entry):
    """Attachment that delivers a prompt while Claude works; the row has no actor markers."""
    attachment = entry.get('attachment') if entry.get('type') == 'attachment' else None
    if (isinstance(attachment, dict) and attachment.get('type') == 'queued_command'
            and attachment.get('commandMode') == 'prompt' and isinstance(attachment.get('prompt'), str)
            and not entry.get('isMeta') and not entry.get('isSidechain')):
        return attachment
    return None


def is_queued_human_prompt(entry):
    """Input the user typed while Claude worked; the host stamps origin.kind=human."""
    attachment = _queued_prompt(entry)
    return (attachment is not None and not attachment.get('isMeta')
            and isinstance(attachment.get('origin'), dict) and attachment['origin'].get('kind') == 'human')


def is_prompt_row(entry):
    """Row that starts the current input's turn, for intake and completion consumers alike."""
    return is_actual_user_prompt(entry) or is_queued_human_prompt(entry)


def assistant_text_this_turn(entries):
    """Select the last Claude assistant message after the latest human input."""
    start = next((i for i in range(len(entries) - 1, -1, -1)
                  if is_prompt_row(entries[i])), -1)
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
        rows = list(committed_records(Path(path).read_bytes()).values())
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
        if is_prompt_row(owned[index]):
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
    offset = committed_length(content)
    return dict(path=str(path), device=identity.st_dev, inode=identity.st_ino, offset=offset,
                prefix_digest=hashlib.sha256(content[:offset]).hexdigest())


def native_input_actor(entry):
    """Require positive host origin; message wording supplies no actor evidence.

    An SDK-started turn or a queued app event is non-human context: 'sdk' for a
    user row that can serve as a content original, 'context' for an app event.
    """
    if entry.get('isSidechain'):
        return None
    if is_task_notification(entry):
        return 'notification'
    if entry.get('isMeta'):
        return None
    if is_queued_human_prompt(entry):
        return 'human'
    origin = entry.get('origin')
    if (is_actual_user_prompt(entry) and isinstance(origin, dict) and origin.get('kind') == 'human'
            and entry.get('promptSource') != 'system' and entry.get('turnOrigin') in (None, 'human')):
        return 'human'
    if (is_actual_user_prompt(entry) and origin is None
        and entry.get('entrypoint') in SDK_ENTRYPOINTS and entry.get('userType') == 'external'
        and entry.get('promptSource') == 'sdk' and entry.get('turnOrigin') == 'sdk'
        and (entry.get('isMeta') is None or entry.get('isMeta') is False)
        and (entry.get('isSidechain') is None or entry.get('isSidechain') is False)):
        content = entry['message'].get('content')
        if isinstance(content, str) or (isinstance(content, list) and content and all(
            isinstance(block, dict) and block.get('type') == 'text'
            and isinstance(block.get('text'), str) and not set(block) - {'type', 'text', 'cache_control'}
            for block in content)):
            return 'sdk'
    attachment = _queued_prompt(entry)
    if (attachment is not None and set(attachment) <= APP_EVENT_KEYS
            and entry.get('entrypoint') in SDK_ENTRYPOINTS and entry.get('userType') == 'external'):
        return 'context'
    return None


def input_shape(entry):
    """Host markers of an input row for failure records; the message text never enters it."""
    def marker(value):
        if value is None or isinstance(value, bool):
            return value
        return value[:64] if isinstance(value, str) else type(value).__name__
    if not isinstance(entry, dict):
        return json.dumps(dict(type='invalid'))
    attachment = entry.get('attachment') if entry.get('type') == 'attachment' else None
    origin = (attachment if isinstance(attachment, dict) else entry).get('origin')
    shape = dict(type=marker(entry.get('type')), entrypoint=marker(entry.get('entrypoint')),
                 userType=marker(entry.get('userType')), promptSource=marker(entry.get('promptSource')),
                 turnOrigin=marker(entry.get('turnOrigin')), isMeta=bool(entry.get('isMeta')),
                 origin=marker(origin.get('kind')) if isinstance(origin, dict) else marker(origin))
    if isinstance(attachment, dict):
        shape.update(attachment=marker(attachment.get('type')), commandMode=marker(attachment.get('commandMode')),
                     keys=sorted(str(key)[:64] for key in attachment))
    return json.dumps(shape, sort_keys=True, separators=(',', ':'))


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


def _row_content(entry):
    message = entry.get('message')
    return message.get('content') if isinstance(message, dict) else _input_text(entry)


def _candidate_texts(content):
    """Every text form matches_input_text can bind, so an index lookup misses no match."""
    if isinstance(content, str):
        return [content]
    if (not isinstance(content, list) or any(not isinstance(block, dict) or not isinstance(block.get('type'), str)
        or (block['type'] == 'text' and not isinstance(block.get('text'), str)) for block in content)):
        return []
    texts = [block['text'] for block in content if block['type'] == 'text']
    return [''.join(texts), '\n'.join(texts), *texts] if texts else []


class _TranscriptSnapshot:
    """One read of a transcript: identity, bytes, prefix digests and a text-digest index."""

    def __init__(self, identity, raw):
        self.identity, self.raw = identity, raw
        self._hashers = {0: hashlib.sha256()}
        self._index, self._indexed_from = None, None

    @classmethod
    def read(cls, path):
        try:
            with Path(path).open('rb') as stream:
                identity = os.fstat(stream.fileno())
                return cls((identity.st_dev, identity.st_ino), stream.read())
        except (OSError, TypeError, ValueError):
            return None

    def prefix_digest(self, offset):
        """Digest of raw[:offset]; queue boundaries grow, so each call hashes only new bytes."""
        if type(offset) is not int or not 0 <= offset <= len(self.raw):
            return None
        base = max(point for point in self._hashers if point <= offset)
        hasher = self._hashers[base].copy()
        hasher.update(self.raw[base:offset])
        self._hashers[offset] = hasher.copy()
        return hasher.hexdigest()

    def rows_with(self, char_count, digest, start):
        """(start, end) of committed user or attachment rows at or after start that carry the text."""
        if self._index is None or start < self._indexed_from:
            self._index, self._indexed_from = {}, start
            for row_start, end, line in committed_byte_lines(self.raw, start):
                try:
                    entry = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(entry, dict) or entry.get('type') not in ('user', 'attachment'):
                    continue
                for text in set(_candidate_texts(_row_content(entry))):
                    key = (len(text), 'sha256:' + hashlib.sha256(text.encode()).hexdigest())
                    self._index.setdefault(key, []).append((row_start, end))
        return [row for row in self._index.get((char_count, digest), []) if row[0] >= start]


class TranscriptCache:
    """Each transcript is read once per caller operation, such as one reconcile.

    Rows the host appends after that read wait for the caller's next operation.
    """

    def __init__(self):
        self._snapshots = {}

    def snapshot(self, path):
        if path not in self._snapshots:
            self._snapshots[path] = _TranscriptSnapshot.read(path)
        return self._snapshots[path]


def locate_staged_input(observation, session_id, cursors, *, cache=None):
    """Find the one native row of a staged input, or name why no row is usable.

    status is located, row-not-found, row-ambiguous or transcript-unavailable.
    A digest correlates content within an authenticated append window; it does
    not classify content. Exact native origin supplies the actor independently.
    """
    unavailable = dict(status='transcript-unavailable')
    boundary = observation.get('transcript_boundary')
    if not isinstance(boundary, dict) or not isinstance(boundary.get('path'), str):
        return unavailable
    snapshot = (cache if cache is not None else TranscriptCache()).snapshot(boundary['path'])
    if snapshot is None:
        return unavailable
    try:
        if boundary['inode'] is not None and snapshot.identity != (boundary['device'], boundary['inode']):
            return unavailable
        if snapshot.prefix_digest(boundary['offset']) != boundary['prefix_digest']:
            return unavailable
        cursor_key = hashlib.sha256(json.dumps([boundary['path'], *snapshot.identity]).encode()).hexdigest()
        start = max(boundary['offset'], cursors.get(cursor_key, 0))
    except (KeyError, TypeError, ValueError):
        return unavailable
    if start > len(snapshot.raw):
        return unavailable
    matches = []
    for row_start, end in snapshot.rows_with(observation.get('input_char_count'), observation.get('input_digest'), start):
        entry = json.loads(snapshot.raw[row_start:end])
        if (entry.get('sessionId') != session_id
                or (entry.get('isMeta') and not is_task_notification(entry)) or entry.get('isSidechain')):
            continue
        # promptId belongs to a native prompt grain and may repeat. It is only
        # an additional correlation constraint, never observation identity.
        native_id = entry.get('promptId')
        if native_id is not None and native_id != observation.get('native_prompt_id'):
            continue
        if matches_input_text(_row_content(entry), observation):
            matches.append((row_start, end, entry))
    if len(matches) != 1:
        return dict(status='row-ambiguous' if matches else 'row-not-found', cursor_key=cursor_key)
    row_start, end, entry = matches[0]
    match = dict(status='located', actor=native_input_actor(entry), cursor_key=cursor_key,
                 start_offset=row_start, end_offset=end)
    if match['actor'] is None:
        match['shape'] = input_shape(entry)
    if match['actor'] == 'sdk':
        source = dict(storage='source-reference.v1', locator=boundary['path'],
            digest='sha256:' + snapshot.prefix_digest(end), byte_length=end, appendable=True)
        match.update(native_source=dict(source, source_id='sha256:' + hashlib.sha256(json.dumps(
            source, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()),
            native_file_identity=dict(device=snapshot.identity[0], inode=snapshot.identity[1]))
    return match


def resolve_staged_input(observation, session_id, cursors, *, allow_unknown=False, cache=None):
    """Return one native actor/row locator, or leave the transport unresolved."""
    located = locate_staged_input(observation, session_id, cursors, cache=cache)
    if located['status'] != 'located' or (located['actor'] is None and allow_unknown is not True):
        return None
    return {key: value for key, value in located.items() if key not in ('status', 'shape')}
