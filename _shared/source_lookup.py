"""Read selected original-source passages without creating another source copy.

This reader locates information; Core authenticates evidence separately.
Selection follows the caller's source coordinates, without an output budget.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from text_lines import line_at, split_lines


def _pointer(value, pointer):
    if pointer and not pointer.startswith('/'):
        raise ValueError('A JSON pointer starts with /')
    for part in pointer.split('/')[1:] if pointer else ():
        key = part.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _line(path, number):
    line = line_at(path.read_bytes(), number)
    if line is None:
        raise ValueError('Requested line is outside the source')
    return line


def lookup(path, *, pointer=None, record=None, line=None, query=None, offset=None, length=None, full=False):
    path = Path(path).expanduser().resolve(strict=True)
    if (length is not None and length < 1) or (offset is not None and offset < 0):
        raise ValueError('Length must be positive and offset nonnegative')
    if sum(x is not None for x in (line, query, pointer)) + bool(full) > 1:
        raise ValueError('Select a line, literal search, or JSON pointer')
    if record is not None and (record < 1 or pointer is None):
        raise ValueError('A JSONL record is a positive line number with a JSON pointer')
    stat = path.stat()
    result = dict(locator=str(path), bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
    if full:
        if offset is not None or length is not None or record is not None:
            raise ValueError('A full read does not use field or range coordinates')
        result.update(text=path.read_text(encoding='utf-8'), complete=True, full=True)
    elif line is not None:
        if line < 1:
            raise ValueError('Line numbers start at one')
        value = _line(path, line)
        start = offset or 0
        end = len(value) if length is None else start+length
        result.update(line=line, offset=start, text=value[start:end],
                      complete=end >= len(value))
        if not result['complete']:
            result['next_offset'] = end
    elif query is not None:
        if not query:
            raise ValueError('Literal search must not be empty')
        matches = []
        for i, text in enumerate(split_lines(path.read_text(encoding='utf-8')), 1):
            pos = text.casefold().find(query.casefold())
            if pos < 0:
                continue
            matches.append(dict(line=i, offset=pos))
        result.update(matches=matches, total_matches=len(matches),
                      selection='Matching source coordinates; select the needed line or JSON field')
    elif pointer is not None:
        if record is None:
            with path.open(encoding='utf-8') as stream:
                value = json.load(stream)
        else:
            value = json.loads(_line(path, record))
        value = _pointer(value, pointer)
        result.update(pointer=pointer, record=record)
        if isinstance(value, str) and (offset is not None or length is not None):
            start = offset or 0
            end = len(value) if length is None else start+length
            result.update(text=value[start:end], offset=start,
                          complete=end >= len(value))
            if not result['complete']:
                result['next_offset'] = end
        else:
            result.update(value=value, complete=True)
    else:
        result['selection'] = 'Use --find, --line, or --pointer; --record selects a JSONL record'
    after = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Source changed during this lookup; select the current source again')
    return result


def _record_use(*args, **kwargs):
    from response_context_use import record_use
    return record_use(*args, **kwargs)


def lookup_with_use(path, *, source, expected_input_event_id, purpose, call_id=None, **selection):
    """Bind retrieval purpose without predicting the source's future lifetime."""
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError('An explicit retrieval purpose is required')
    result = lookup(path, **selection)
    receipt = _record_use(source, expected_input_event_id, result,
                          purpose=purpose, call_id=call_id)
    if receipt.get('status') != 'recorded' or not receipt.get('reference'):
        raise ValueError('The declared source use was not bound to the current native call')
    return dict(result, use_reference=receipt['reference'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--find', dest='query')
    group.add_argument('--line', type=int)
    group.add_argument('--pointer')
    group.add_argument('--full', action='store_true', help='Return the complete explicitly requested text source')
    parser.add_argument('--record', type=int)
    parser.add_argument('--offset', type=int)
    parser.add_argument('--length', type=int, help='Optional length of the requested string or line selection')
    parser.add_argument('--purpose')
    parser.add_argument('--root')
    parser.add_argument('--platform', choices=('codex', 'claude'))
    parser.add_argument('--session-id')
    parser.add_argument('--native-dir')
    parser.add_argument('--expected-input-event-id')
    parser.add_argument('--call-id')
    args = vars(parser.parse_args())
    try:
        control = {key: args.pop(key) for key in ('purpose', 'root',
            'platform', 'session_id', 'native_dir', 'expected_input_event_id', 'call_id')}
        if any(value is not None for value in control.values()):
            if any(not control[key] for key in ('purpose', 'root',
                                               'platform', 'session_id', 'native_dir')):
                raise ValueError('A declared use requires its exact runtime root, platform, session and native directory')
            source = dict(os.environ, GHOST_ALICE_SESSION_INTENT_ROOT=control['root'],
                GHOST_ALICE_PLATFORM=control['platform'], GHOST_ALICE_SESSION_ID=control['session_id'])
            source['GHOST_ALICE_CODEX_DIR' if control['platform'] == 'codex'
                   else 'GHOST_ALICE_CLAUDE_DIR'] = control['native_dir']
            result = lookup_with_use(**args, source=source,
                expected_input_event_id=control['expected_input_event_id'],
                purpose=control['purpose'], call_id=control['call_id'])
        else:
            result = lookup(**args)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(json.dumps(dict(status='unavailable', reason=str(exc)), ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
