"""Pure Claude native transcript and Anthropic Messages context adapter.

This module does not read credentials, choose consumed content, contact a model,
or modify originals.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

from text_lines import committed_records

platform = 'claude'


class NativeContextRejected(ValueError):
    """The supplied native message or explicit selection is unsupported."""


class SignedPrefixSelectionUnsupported(NativeContextRejected):
    """Client substitution cannot preserve Claude's signed semantic prefix."""
    reason = 'signed-prefix-selection-unsupported'


class ToolInputSelectionUnsupported(NativeContextRejected):
    """Explicit combined input/result omission requires server-owned clearing."""
    reason = 'tool-input-selection-requires-server'


def fingerprint(value):
    """Fingerprint unchanged JSON using Core's canonical serialization."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def _semantic_block(value):
    if isinstance(value, list):
        return [_semantic_block(block) for block in value]
    if not isinstance(value, dict):
        return value
    result = {key: copy.deepcopy(content) for key, content in value.items() if key != 'cache_control'}
    # cache_control inside a tool's input is a semantic argument, not metadata.
    if value.get('type') in ('message', 'tool_result') and isinstance(value.get('content'), list):
        result['content'] = _semantic_block(value['content'])
    return result


def semantic_fingerprint(block):
    return fingerprint(_semantic_block(block))


def native_path(home, session):
    """Return the unique exact-session transcript, or None when unavailable."""
    if (not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', session)
        or not isinstance(home, (str, Path)) or not str(home) or not Path(home).is_absolute()):
        raise NativeContextRejected('An absolute Claude home and exact session are required')
    projects = (Path(home) / 'projects').resolve()
    matches = []
    for candidate in projects.rglob(session + '.jsonl'):
        resolved = candidate.resolve()
        if candidate.is_file() and resolved.is_relative_to(projects):
            matches.append(str(resolved))
    return matches[0] if len(matches) == 1 else None


def _normalize_message(role, content):
    if role not in ('user', 'assistant'):
        raise NativeContextRejected('A native user or assistant role is required')
    if role == 'user' and isinstance(content, str):
        content = [dict(type='text', text=content)]
    if (not isinstance(content, list)
        or any(not isinstance(block, dict) or not isinstance(block.get('type'), str) for block in content)):
        raise NativeContextRejected('Native content blocks are unavailable')
    return dict(type='message', role=role, content=copy.deepcopy(content))


def native_records(path):
    """Normalize message envelopes while retaining their one-based JSONL lines."""
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise NativeContextRejected('An absolute native transcript path is required')
    rows = committed_records(Path(path).read_bytes())
    return native_records_from_rows(rows)


def native_records_from_rows(rows):
    """Normalize the same immutable row snapshot the caller checked for ownership."""
    if not isinstance(rows, dict) or any(type(n) is not int or n < 1 for n in rows):
        raise NativeContextRejected('One-based native row coordinates are required')
    records = {}
    for record, row in rows.items():
        if not isinstance(row, dict):
            raise NativeContextRejected('A native JSONL object is required')
        if row.get('type') not in ('user', 'assistant'):
            continue
        message = row.get('message')
        if not isinstance(message, dict) or message.get('role') != row['type']:
            raise NativeContextRejected('The native envelope and message role differ')
        records[record] = _normalize_message(message['role'], message.get('content'))
    return records


def _ordered_items(items):
    if isinstance(items, dict) and all(type(record) is int and record > 0 for record in items):
        return [item for _, item in sorted(items.items())]
    if isinstance(items, list):
        return items
    raise NativeContextRejected('Ordered native messages are required')


def human_input(items, binding):
    """Bind the latest real human message to the canonical intake text identity."""
    if (not isinstance(binding, dict) or not isinstance(binding.get('input_digest'), str)
        or type(binding.get('input_char_count')) is not int):
        raise NativeContextRejected('The canonical input digest and character count are required')
    latest = None
    for item in _ordered_items(items):
        if not isinstance(item, dict) or item.get('role') != 'user':
            continue
        normalized = _normalize_message('user', item.get('content'))
        content = normalized['content']
        if any(block.get('type') == 'tool_result' for block in content):
            continue
        texts = [block['text'] for block in content
                 if block.get('type') == 'text' and isinstance(block.get('text'), str)]
        if any(value.lstrip().startswith('<task-notification>') for value in texts):
            continue
        latest = normalized
    if latest is None:
        raise NativeContextRejected('The current native human input is unavailable')
    from native_transcript import matches_input_text
    if not matches_input_text(latest['content'], binding):
        raise NativeContextRejected('Native input differs from the canonical intake receipt')
    return latest


def _signed(block):
    return (isinstance(block, dict)
            and (block.get('type') in ('thinking', 'redacted_thinking', 'compaction') or 'signature' in block))


def require_rewriteable_prefix(request):
    """Reject client changes to an existing or future thinking-bound prefix.

    The host retains original messages, so even a first thinking-enabled request
    must keep that same prefix for the signed blocks generated from it. Thinking
    can be the model default: only explicit disabled thinking permits a rewrite.
    Examine content blocks only: tool arguments and producer JSON can contain unrelated
    fields named signature. The caller invokes this only for eligible changes.
    """
    thinking = request.get('thinking')
    if not isinstance(thinking, dict) or thinking.get('type') != 'disabled':
        raise SignedPrefixSelectionUnsupported(SignedPrefixSelectionUnsupported.reason)
    contents = [message.get('content') for message in request['messages']
                if isinstance(message, dict)]
    contents.append(request.get('system'))
    if any(isinstance(content, list) and any(_signed(block) for block in content)
           for content in contents):
        raise SignedPrefixSelectionUnsupported(SignedPrefixSelectionUnsupported.reason)


def selected_value(item, choice):
    """Return only the explicitly supported native text or tool-result value."""
    if not isinstance(choice, dict) or choice.get('kind') not in ('message', 'assistant', 'output'):
        raise NativeContextRejected('The explicit Claude context kind is unsupported')
    index = choice.get('block_index')
    if (not isinstance(item, dict) or item.get('type') != 'message'
        or item.get('role') not in ('user', 'assistant') or not isinstance(item.get('content'), list)
        or type(index) is not int or not 0 <= index < len(item['content'])):
        raise NativeContextRejected('The selected native block is unavailable')
    block = item['content'][index]
    kind = choice['kind']
    if not isinstance(block, dict) or _signed(block):
        raise NativeContextRejected('Signed or unknown native blocks cannot be selected')
    if kind in ('message', 'assistant'):
        if (kind == 'assistant' and item['role'] != 'assistant') or block.get('type') != 'text':
            raise NativeContextRejected('The selected native text role or block type is unsupported')
        if isinstance(block.get('text'), str):
            return block
    elif item['role'] == 'user' and block.get('type') == 'tool_result':
        value = block.get('content')
        if isinstance(value, str):
            return value
        if (isinstance(value, list) and all(isinstance(part, dict) and isinstance(part.get('type'), str)
                                          and not _signed(part) for part in value)):
            return value
    raise NativeContextRejected('The selected native text or tool-result content is unsupported')


def _blocks(messages):
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'):
            continue
        content = message.get('content')
        if isinstance(content, str):
            yield index, 0, message['role'], dict(type='text', text=content), True
        elif isinstance(content, list):
            for block_index, block in enumerate(content):
                if isinstance(block, dict):
                    yield index, block_index, message['role'], block, False


def _native_blocks(originals):
    for record, item in sorted(originals.items()):
        for _, block_index, role, block, _ in _blocks([item]):
            yield record, block_index, role, block


def _call_fingerprint(block):
    # Claude native responses record the direct caller; host replay omits this
    # default. Compare that exact transport difference only, without changing
    # source fingerprints, tool arguments, non-direct callers or signed history.
    if block.get('type') == 'tool_use' and block.get('caller') == {'type': 'direct'}:
        block = {key: value for key, value in block.items() if key != 'caller'}
    return semantic_fingerprint(block)


def _paired(originals, wire_blocks, record, index, target):
    identifier = target[3].get('tool_use_id')
    if not isinstance(identifier, str) or not identifier:
        return False
    native = list(_native_blocks(originals))
    native_calls = [block for block in native if block[2] == 'assistant'
                    and block[3].get('type') == 'tool_use' and block[3].get('id') == identifier]
    native_results = [block for block in native if block[2] == 'user'
                      and block[3].get('type') == 'tool_result' and block[3].get('tool_use_id') == identifier]
    wire_calls = [block for block in wire_blocks if block[2] == 'assistant'
                  and block[3].get('type') == 'tool_use' and block[3].get('id') == identifier]
    wire_results = [block for block in wire_blocks if block[2] == 'user'
                    and block[3].get('type') == 'tool_result' and block[3].get('tool_use_id') == identifier]
    return (len(native_calls) == len(native_results) == len(wire_calls) == len(wire_results) == 1
            and native_results[0][:2] == (record, index)
            and native_calls[0][:2] < native_results[0][:2]
            and wire_results[0][:2] == target[:2]
            and wire_calls[0][:2] < target[:2]
            and _call_fingerprint(native_calls[0][3]) == _call_fingerprint(wire_calls[0][3]))


def _validate_choice(choice):
    if not isinstance(choice, dict) or choice.get('kind') not in ('message', 'assistant', 'output'):
        raise NativeContextRejected('The explicit Claude context kind is unsupported')
    disposition = choice.get('disposition', 'replace')
    if disposition not in ('keep', 'replace', 'omit'):
        raise NativeContextRejected('A keep, replace or omit disposition is required')
    if 'clear_tool_inputs' in choice and (type(choice['clear_tool_inputs']) is not bool
        or choice['kind'] != 'output' or (choice['clear_tool_inputs'] and disposition != 'omit')):
        raise NativeContextRejected('Combined tool input/result scope requires an explicit output omit')
    if choice.get('clear_tool_inputs') is True and (
        type(choice.get('call_record')) is not int or choice['call_record'] < 1
        or type(choice.get('call_block_index')) is not int or choice['call_block_index'] < 0
        or not isinstance(choice.get('call_digest'), str) or not choice['call_digest']):
        raise NativeContextRejected('An authenticated original call binding is required')
    if (type(choice.get('record')) is not int or choice['record'] < 1
        or type(choice.get('block_index')) is not int or choice['block_index'] < 0
        or any(not isinstance(choice.get(key), str) or not choice[key].strip()
               for key in ('summary', 'next_need', 'item_digest', 'block_digest',
                           'source_locator'))
        or (disposition == 'replace' and (not isinstance(choice.get('replacement_text'), str)
                                         or not choice['replacement_text'].strip()))):
        raise NativeContextRejected('Explicit native coordinates, identities and replacement text are required')


def request_apply(request, originals, selections, *, _inspect=None):
    """Apply authenticated explicit choices to uniquely matched wire blocks.

    Stale source identities preserve the complete request. A missing, ambiguous,
    or unpaired wire block is left intact. Unsupported selections are rejected.
    Eligible client changes to a thinking-capable or signed prefix are rejected
    before any mutation or selection observation.
    """
    if (not isinstance(request, dict) or not isinstance(request.get('messages'), list)
        or not isinstance(originals, dict) or any(type(record) is not int or record < 1 for record in originals)
        or not isinstance(selections, list)):
        raise NativeContextRejected('Native records, explicit selections and Anthropic messages are required')
    result = copy.deepcopy(request)
    for choice in selections:
        _validate_choice(choice)
    wire_blocks = list(_blocks(request['messages']))
    changes, positions = [], set()
    latest_human = max((n for n, message in enumerate(request['messages'])
                        if isinstance(message, dict) and message.get('role') == 'user'
                        and not (isinstance(message.get('content'), list) and any(
                            isinstance(block, dict) and block.get('type') == 'tool_result'
                            for block in message['content']))
                        and not any(block.get('type') == 'text' and isinstance(block.get('text'), str)
                            and block['text'].lstrip().startswith('<task-notification>')
                            for _, _, _, block, _ in _blocks([message]))), default=-1)
    for choice in selections:
        record, index = choice['record'], choice['block_index']
        original = originals.get(record)
        if original is None or fingerprint(original) != choice['item_digest']:
            return result, []
        value = selected_value(original, choice)
        if fingerprint(value) != choice['block_digest']:
            return result, []
        block = original['content'][index]
        matches = [target for target in wire_blocks if target[2] == original['role']
                   and semantic_fingerprint(target[3]) == semantic_fingerprint(block)]
        if len(matches) != 1:
            continue
        target = matches[0]
        if choice['kind'] == 'output' and not _paired(originals, wire_blocks, record, index, target):
            continue
        if choice.get('clear_tool_inputs') is True:
            call_item = originals.get(choice['call_record'])
            call_blocks = call_item.get('content') if isinstance(call_item, dict) else None
            call_index = choice['call_block_index']
            if (not isinstance(call_blocks, list) or call_index >= len(call_blocks)
                or call_item.get('role') != 'assistant' or not isinstance(call_blocks[call_index], dict)
                or call_blocks[call_index].get('type') != 'tool_use'
                or call_blocks[call_index].get('id') != target[3].get('tool_use_id')
                or fingerprint(call_blocks[call_index]) != choice['call_digest']):
                return result, []
        if target[:2] in positions:
            return result, []
        positions.add(target[:2])
        if choice.get('disposition') == 'omit':
            if choice['kind'] != 'output':
                wire_message = request['messages'][target[0]]
                if (target[0] == latest_human or set(target[3]) - {'type', 'text', 'cache_control'}
                    or set(wire_message) - {'role', 'content', 'type'}):
                    continue
            elif isinstance(value, list) and any(part.get('type') != 'text'
                or set(part) - {'type', 'text', 'cache_control'} for part in value):
                continue
        changes.append((choice, target))
    if _inspect is not None:
        _inspect.extend(dict(choice, message_index=target[0], wire_block_index=target[1])
                        for choice, target in changes)
        return result, []
    changes = [(choice, target) for choice, target in changes if choice.get('disposition', 'replace') != 'keep']
    if any(choice.get('clear_tool_inputs') is True for choice, _ in changes):
        raise ToolInputSelectionUnsupported(ToolInputSelectionUnsupported.reason)
    if changes:
        require_rewriteable_prefix(request)
    observations, removed_blocks, removed_messages = [], {}, set()
    for choice, target in changes:
        message_index, block_index, _, before, string_content = target
        after = copy.deepcopy(before)
        field = 'content' if choice['kind'] == 'output' else 'text'
        disposition = choice.get('disposition', 'replace')
        replacement = '' if disposition == 'omit' else choice['replacement_text']
        after[field] = replacement
        if disposition == 'omit' and choice['kind'] != 'output':
            if string_content:
                removed_messages.add(message_index)
            else:
                removed_blocks.setdefault(message_index, set()).add(block_index)
        elif string_content:
            result['messages'][message_index]['content'] = replacement
        else:
            result['messages'][message_index]['content'][block_index] = after
        forwarded_value = after['content'] if choice['kind'] == 'output' else after
        observations.append(dict(record=choice['record'], block_index=choice['block_index'], kind=choice['kind'],
                                 disposition=disposition,
                                 source_locator=choice['source_locator'],
                                 original=f"{choice['source_locator']}#record={choice['record']},content={choice['block_index']}",
                                 message_index=message_index, wire_block_index=block_index,
                                 original_item_digest=choice['item_digest'], original_block_digest=choice['block_digest'],
                                 forwarded_block_digest=fingerprint(forwarded_value),
                                 original_semantic_block_digest=semantic_fingerprint(before),
                                 forwarded_semantic_block_digest=semantic_fingerprint(after)))
    for index, indices in removed_blocks.items():
        content = result['messages'][index]['content']
        result['messages'][index]['content'] = [block for n, block in enumerate(content) if n not in indices]
        if not result['messages'][index]['content']:
            removed_messages.add(index)
    for observation in observations:
        index, block_index = observation['message_index'], observation['wire_block_index']
        deleted = index in removed_messages or block_index in removed_blocks.get(index, ())
        if deleted:
            observation['forwarded_block_digest'] = None
            observation['forwarded_semantic_block_digest'] = None
        observation['forwarded_message_index'] = None if index in removed_messages else index - sum(
            n < index for n in removed_messages)
        observation['forwarded_wire_block_index'] = None if deleted else block_index - sum(
            n < block_index for n in removed_blocks.get(index, ()))
    result['messages'] = [message for n, message in enumerate(result['messages']) if n not in removed_messages]
    return result, observations


def _replacement_carrier(choice, blocks, calls, results):
    """Check Core's authenticated publication against the actual wire carrier.

    Core verifies native ownership, originals and executed publication before
    enriching the choice. This pure check neither creates that provenance nor
    rewrites the signed prefix; it confirms the needed context is in this input.
    """
    context = choice.get('replacement_context')
    if not isinstance(context, dict) or set(context) != {'projection', 'carrier'}:
        return None
    projection, carrier = context['projection'], context['carrier']
    if (not isinstance(projection, dict)
        or set(projection) != {'schema_version', 'context_reference', 'replacements'}
        or projection['schema_version'] != 'context-replacement-output.v1'
        or not isinstance(projection['context_reference'], str)
        or not re.fullmatch(r'sha256:[0-9a-f]{64}', projection['context_reference'])
        or projection['context_reference'] != choice.get('reference')
        or not isinstance(projection['replacements'], list) or not projection['replacements']
        or not isinstance(carrier, dict)
        or set(carrier) != {'message_index', 'wire_block_index', 'tool_use_id'}
        or any(type(carrier[key]) is not int or carrier[key] < 0
               for key in ('message_index', 'wire_block_index'))
        or not isinstance(carrier['tool_use_id'], str) or not carrier['tool_use_id']
        or choice.get('clear_tool_inputs', False) is not False):
        return None
    coordinates = set()
    for replacement in projection['replacements']:
        if (not isinstance(replacement, dict)
            or set(replacement) != {'record', 'block_index', 'summary', 'next_need', 'original'}
            or type(replacement['record']) is not int or replacement['record'] < 1
            or type(replacement['block_index']) is not int or replacement['block_index'] < 0
            or any(not isinstance(replacement[key], str) or not replacement[key].strip()
                   for key in ('summary', 'next_need', 'original'))):
            return None
        coordinate = (replacement['record'], replacement['block_index'])
        if coordinate in coordinates:
            return None
        coordinates.add(coordinate)
    if (type(choice.get('record')) is not int or type(choice.get('block_index')) is not int
        or not isinstance(choice.get('source_locator'), str)):
        return None
    expected = dict(record=choice['record'], block_index=choice['block_index'],
                    summary=choice.get('summary'), next_need=choice.get('next_need'),
                    original=f"{choice['source_locator']}#record={choice['record']},content={choice['block_index']}")
    if expected not in projection['replacements']:
        return None
    identifier = carrier['tool_use_id']
    position = (carrier['message_index'], carrier['wire_block_index'])
    selected_position = (choice.get('message_index'), choice.get('wire_block_index'))
    if (identifier not in calls or results.get(identifier) != position
        or any(type(index) is not int or index < 0 for index in selected_position)
        or calls[identifier][1:] <= selected_position):
        return None
    matches = [block for message_index, block_index, role, block, _ in blocks
               if (message_index, block_index) == position and role == 'user'
               and block.get('type') == 'tool_result' and block.get('tool_use_id') == identifier]
    if len(matches) != 1 or 'signature' in matches[0]:
        return None
    from response_context_use import _output
    digest = fingerprint({key: value for key, value in projection.items() if key != 'context_reference'})
    _, count, occurrences = _output(matches[0].get('content'), 'claude',
        projection['context_reference'], digest, {}, marker='context_reference')
    return copy.deepcopy(carrier) if count == occurrences == 1 else None


def _recent_pair_scope(blocks, calls, results, omitted, originals, prospective_carrier):
    """Map an authenticated selected prefix to a conservative retained suffix."""
    if set(calls) != set(results):
        return None, 0, [], 'server-context-pair-unverified'
    order = sorted(calls, key=lambda identifier: calls[identifier][1:])
    if order != sorted(results, key=results.get):
        return None, 0, [], 'server-context-order-unverified'
    native_call_blocks, native_result_blocks = {}, {}
    wire_call_blocks, wire_result_blocks = {}, {}
    for source_blocks, call_index, result_index in (
        (_native_blocks(originals),native_call_blocks,native_result_blocks),
        (blocks,wire_call_blocks,wire_result_blocks)):
        for value in source_blocks:
            role, block = value[2:4]
            if role=='assistant' and block.get('type')=='tool_use':
                identifier, index = block.get('id'), call_index
            elif role=='user' and block.get('type')=='tool_result':
                identifier, index = block.get('tool_use_id'), result_index
            else:
                continue
            if isinstance(identifier,str):
                index.setdefault(identifier,[]).append(value)
    native_calls, native_results = {}, {}
    for identifier in order:
        nc, nr = native_call_blocks.get(identifier,[]), native_result_blocks.get(identifier,[])
        wc, wr = wire_call_blocks.get(identifier,[]), wire_result_blocks.get(identifier,[])
        if len(nc)!=1 or len(wc)!=1 or len(wr)!=1 or _call_fingerprint(nc[0][3])!=_call_fingerprint(wc[0][3]):
            return None, 0, [], 'server-context-pair-unverified'
        native_calls[identifier] = nc[0][:2]
        if not nr and identifier==prospective_carrier and identifier==order[-1]:
            # Readiness already authenticates this exact Core-issued capsule.
            # Its missing native result can only contribute retained suffix
            # count in a dry inspection, never completed carrier authority.
            continue
        if (len(nr)!=1 or nc[0][:2]>=nr[0][:2]
            or semantic_fingerprint(nr[0][3])!=semantic_fingerprint(wr[0][3])):
            return None, 0, [], 'server-context-pair-unverified'
        native_results[identifier] = nr[0][:2]
    if (order != sorted(native_calls,key=native_calls.get)
        or [identifier for identifier in order if identifier in native_results] != sorted(native_results,key=native_results.get)
        or (prospective_carrier in native_calls and prospective_carrier not in native_results
            and any(position>=native_calls[prospective_carrier] for position in native_results.values()))):
        return None, 0, [], 'server-context-order-unverified'
    names = {calls[identifier][0] for identifier in omitted}
    # An older unselected result of an included name cannot be protected by
    # keeping only recent pairs. Later holes instead start the retained suffix.
    names = {name for name in names if next(identifier for identifier in order
             if calls[identifier][0]==name) in omitted}
    holes = [results[identifier] for identifier in order if calls[identifier][0] in names and identifier not in omitted]
    cutoff = min(holes) if holes else None
    eligible = {identifier for identifier in omitted if calls[identifier][0] in names
                and (cutoff is None or results[identifier]<cutoff)}
    keep = sum(position>=cutoff for position in results.values()) if cutoff is not None else 0
    included = {calls[identifier][0] for identifier in eligible}
    excluded = sorted({call[0] for call in calls.values()} - included)
    named_order = [identifier for identifier in order if calls[identifier][0] in included]
    guaranteed = set(named_order[:max(0,len(named_order)-keep)])
    prefix = set()
    for identifier in order:
        if identifier not in omitted:
            break
        prefix.add(identifier)
    # Empty exclusions make retention count unambiguous. Preserve every target
    # the named policy already guarantees under the more-retaining filtered
    # count; otherwise the global prefix would strand independent selections.
    if prefix and guaranteed <= prefix and (prefix != guaranteed or eligible != guaranteed):
        return prefix, len(order)-len(prefix), [], None
    return eligible, keep, excluded, None


def server_context_omit(request, selections, *, originals=None, _inspect_only=False, _prospective_carrier=None):
    """Request server clearing for authenticated, exactly representable results.

    An explicit replace also needs Core's exact native publication carrier;
    the replacement context must already appear in the unmodified wire input.
    Core supplies inspected whole-block choices. The provider clears by tool
    name and a recent-pair retention window, not arbitrary result ID. Every
    included result before that window must have an authenticated disposition.
    A retained suffix requires full native/wire pairs and matching chronology.
    The signed client history stays unchanged. Observations describe requested
    policy, never server adoption or guaranteed clearing of each original.
    """
    preserve = lambda reason: (request, [], reason)
    if _prospective_carrier is not None and not _inspect_only:
        return preserve('server-context-pair-unverified')
    if not isinstance(request, dict) or not isinstance(request.get('messages'), list):
        return preserve('input-unclassified')
    blocks = list(_blocks(request['messages']))
    if any(block.get('type') == 'compaction' or (str(block.get('type')).endswith('tool_result')
            and block.get('type') != 'tool_result') for _, _, _, block, _ in blocks):
        return preserve('server-context-structure-unsupported')
    policy = request.get('context_management', {})
    if (not isinstance(policy, dict) or set(policy) - {'edits'}
        or not isinstance(policy.get('edits', []), list)
        or any(not isinstance(edit, dict) or edit.get('type') != 'clear_thinking_20251015'
               for edit in policy.get('edits', []))):
        return preserve('server-context-policy-unsupported')
    calls, results = {}, {}
    for message_index, block_index, role, block, _ in blocks:
        kind = block.get('type')
        if kind == 'tool_use':
            identifier, name = block.get('id'), block.get('name')
            if (role != 'assistant' or not isinstance(identifier, str) or not identifier
                or identifier in calls or not isinstance(name, str) or not name):
                return preserve('server-context-pair-unverified')
            calls[identifier] = (name, message_index, block_index)
        elif kind == 'tool_result':
            identifier = block.get('tool_use_id')
            if role != 'user' or not isinstance(identifier, str) or not identifier or identifier in results:
                return preserve('server-context-pair-unverified')
            results[identifier] = (message_index, block_index)
        elif kind == 'server_tool_use':
            return preserve('server-context-structure-unsupported')
    if any(identifier not in calls or calls[identifier][1:] >= position
           for identifier, position in results.items()):
        return preserve('server-context-pair-unverified')
    selected, replacement_carriers = {}, {}
    for choice in selections:
        if choice.get('kind') != 'output':
            continue
        position = (choice['message_index'], choice['wire_block_index'])
        if choice.get('disposition') == 'omit':
            selected[position] = choice
        elif choice.get('disposition') == 'replace':
            carrier = _replacement_carrier(choice, blocks, calls, results)
            if carrier is not None:
                selected[position] = choice
                replacement_carriers[position] = carrier
    # The native summary is the replacement's needed input. Even another
    # current omit declaration cannot clear its carrier while it is consumed.
    protected_carriers = {carrier['tool_use_id'] for carrier in replacement_carriers.values()}
    selected = {position: choice for position, choice in selected.items()
                if not any(results.get(identifier) == position for identifier in protected_carriers)}
    omitted = {identifier for identifier, position in results.items() if position in selected}
    if not omitted:
        return preserve('server-context-selection-unrepresentable')
    protected_names = {name for identifier, (name, _, _) in calls.items() if identifier not in omitted}
    eligible = {identifier for identifier in omitted if calls[identifier][0] not in protected_names}
    keep = 0
    excluded = sorted({name for name, _, _ in calls.values()} - {calls[identifier][0] for identifier in eligible})
    if eligible != omitted and originals is not None:
        eligible, keep, excluded, reason = _recent_pair_scope(blocks,calls,results,omitted,originals,_prospective_carrier)
        if reason:
            return preserve(reason)
    if not eligible:
        return preserve('server-context-name-scope-unrepresentable')
    input_scopes = {selected[results[identifier]].get('clear_tool_inputs', False) for identifier in eligible}
    if len(input_scopes) != 1:
        return preserve('server-context-input-scope-unrepresentable')
    clear_inputs = input_scopes == {True}
    edit = dict(type='clear_tool_uses_20250919', trigger={'type': 'input_tokens', 'value': 1},
                keep={'type': 'tool_uses', 'value': keep}, clear_tool_inputs=clear_inputs, exclude_tools=excluded)
    result = copy.deepcopy(request)
    result['context_management'] = copy.deepcopy(policy)
    result['context_management'].setdefault('edits', []).append(edit)
    observations = [dict(selected[results[identifier]], tool_use_id=identifier,
                         provider_application='requested', application='server-context-management',
                         server_edit=copy.deepcopy(edit)) for identifier in results if identifier in eligible]
    for observation in observations:
        position = results[observation['tool_use_id']]
        if position in replacement_carriers:
            observation['replacement_carrier'] = replacement_carriers[position]
    reason = ('server-context-replace-requested' if any(observation.get('disposition') == 'replace'
              for observation in observations) else 'server-context-omit-requested')
    return request if _inspect_only else result, observations, reason


def _compaction_request_shape(request):
    if not isinstance(request, dict) or not isinstance(request.get('messages'), list):
        raise NativeContextRejected('A native request with ordered messages is required')
    for message in request['messages']:
        if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant', 'system'):
            raise NativeContextRejected('A classified native message role is required')
        content = message.get('content')
        if not isinstance(content, (str, list)) or (isinstance(content, list) and (
            not content or any(not isinstance(block, dict) or not isinstance(block.get('type'), str)
                or (block['type'] == 'text' and not isinstance(block.get('text'), str)) for block in content))):
            raise NativeContextRejected('Native message content is unavailable or malformed')
    policy = request.get('context_management', {})
    if (not isinstance(policy, dict) or not isinstance(policy.get('edits', []), list)
        or any(not isinstance(edit, dict) or not isinstance(edit.get('type'), str)
               for edit in policy.get('edits', []))):
        raise NativeContextRejected('The native context-management policy is malformed')
    if any(edit['type'].startswith('compact_') for edit in policy.get('edits', [])):
        raise NativeContextRejected('The host must retire threshold compaction before preparing signed compaction')
    choice = request.get('tool_choice', {})
    if not isinstance(choice, dict):
        raise NativeContextRejected('The native tool-choice shape is malformed')


class SignedCompactionPlan:
    """Host-side state for one explicitly selected API summary request.

    The host sends ``request`` with its existing native authentication and beta,
    then passes that call's response to ``adopt``. This pure controller neither
    sends a request nor authenticates a provider signature. The responding model
    compares the summary with the original objective before approving adoption.
    Desktop /compact and arbitrary signed-text omission are separate operations.
    Contract: https://platform.claude.com/docs/en/build-with-claude/compaction-on-demand
    """

    def __init__(self, original, request, cut):
        self._original = copy.deepcopy(original)
        self._original_digest = fingerprint(original)
        self.request = copy.deepcopy(request)
        self._request_digest = fingerprint(request)
        self.cut = cut
        self._cut = cut

    def adopt(self, response, current, *, native_betas, summary_accepted):
        """Swap only the unchanged summarized prefix; retain all kept turns."""
        _compaction_request_shape(current)
        if (summary_accepted is not True or not isinstance(native_betas, (list, tuple))
            or 'compact-2026-09-04' not in native_betas
            or fingerprint(self._original) != self._original_digest
            or fingerprint(self.request) != self._request_digest or self.cut != self._cut):
            raise NativeContextRejected('Unchanged compaction state, native beta and accepted summary are required')
        original = self._original
        if (not isinstance(current, dict) or not isinstance(current.get('messages'), list)
            or current['messages'][:len(original['messages'])] != original['messages']
            or any(current.get(key) != original.get(key)
                   for key in ('model', 'system', 'tools', 'thinking', 'output_config'))):
            raise NativeContextRejected('Compaction requires the original prefix, model, instructions and thinking')
        content = response.get('content') if isinstance(response, dict) else None
        if (not isinstance(response, dict) or response.get('model') != original.get('model')
            or response.get('role') != 'assistant' or response.get('stop_reason') != 'compaction'
            or not isinstance(response.get('id'), str) or not response['id']
            or not isinstance(content, list) or len(content) != 1
            or not isinstance(content[0], dict) or content[0].get('type') != 'compaction'
            or any(not isinstance(content[0].get(key), str) or not content[0][key]
                   for key in ('content', 'signature'))):
            raise NativeContextRejected('The exact successful signed response from this compaction call is required')
        forwarded = copy.deepcopy(current)
        forwarded['messages'] = [dict(role='assistant', content=copy.deepcopy(content)),
                                 *copy.deepcopy(current['messages'][self.cut:])]
        return forwarded


def prepare_signed_compaction(request, cut, *, native_betas):
    """Prepare the model-selected closed prefix without adding a beta or cap.

    The current human message and any mid-conversation system instructions stay
    outside the summarized range. Pair boundaries, unchanged kept turns and the
    same model/system/tools preserve the documented kept-thinking conditions.
    The native host must own the request/response loop; Gateway does not borrow
    credentials or invent Desktop uptake when that host capability is absent.
    """
    _compaction_request_shape(request)
    if (not isinstance(native_betas, (list, tuple)) or 'compact-2026-09-04' not in native_betas
        or not isinstance(request, dict) or not isinstance(request.get('messages'), list)
        or not isinstance(request.get('model'), str) or type(cut) is not int
        or not 0 < cut < len(request['messages']) or 'compaction' in request):
        raise NativeContextRejected('An ordinary native request, existing compaction beta and explicit cut are required')
    messages = request['messages']
    if any(not isinstance(message, dict) or message.get('role') not in ('user', 'assistant', 'system')
           for message in messages):
        raise NativeContextRejected('Ordered native message objects are required')
    latest = max((i for i, message in enumerate(messages) if message.get('role') == 'user'
                  and not any(block.get('type') == 'tool_result'
                              for _, _, _, block, _ in _blocks([message]))), default=-1)
    if (cut > latest or any(message['role'] == 'system' for message in messages[:cut])
        or messages[cut]['role'] == 'system' or messages[cut]['role'] == messages[cut-1]['role']):
        raise NativeContextRejected('Keep the latest input, live system instructions and a distinct-role tail boundary')
    calls, results = set(), set()
    for _, _, role, block, _ in _blocks(messages[:cut]):
        if block.get('type') == 'tool_use':
            identifier = block.get('id')
            if role != 'assistant' or not isinstance(identifier, str) or not identifier or identifier in calls:
                raise NativeContextRejected('A unique closed tool exchange is required')
            calls.add(identifier)
        elif block.get('type') == 'tool_result':
            identifier = block.get('tool_use_id')
            if (role != 'user' or not isinstance(identifier, str) or not identifier
                or identifier not in calls or identifier in results):
                raise NativeContextRejected('A unique ordered closed tool exchange is required')
            results.add(identifier)
        elif block.get('type') == 'server_tool_use' or str(block.get('type')).endswith('_tool_result'):
            raise NativeContextRejected('This server-tool compaction boundary is unclassified')
    if calls != results:
        raise NativeContextRejected('Compaction cannot split an open tool exchange')
    config = request.get('output_config', {})
    if (not isinstance(config, dict) or not isinstance(config.get('task_budget', {}), dict)
        or 'remaining' in config.get('task_budget', {})):
        raise NativeContextRejected('The API rejects a remaining task budget during signed compaction')
    summary = copy.deepcopy(request)
    summary['messages'] = copy.deepcopy(messages[:cut])
    summary['compaction'] = {'type': 'summarize'}
    # These generation-only controls are rejected by the summarization API.
    summary.pop('context_management', None)
    summary.pop('stop_sequences', None)
    if summary.get('tool_choice', {}).get('type') in ('any', 'tool'):
        summary.pop('tool_choice')
    if 'format' in config:
        summary['output_config'].pop('format')
    return SignedCompactionPlan(request, summary, cut)
