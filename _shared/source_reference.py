"""Authenticate original files without retaining additional copies of their bodies."""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

FORMAT = 'source-reference.v1'
CORE_FORMAT = 'core-source-reference.v1'


class SourceReferenceError(ValueError):
    """The supplied original reference or bytes do not match."""


def _identity(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _scan(path, length=None):
    digest, count = hashlib.sha256(), 0
    with Path(path).open('rb') as stream:
        while length is None or count < length:
            block = stream.read(65536 if length is None else min(65536, length-count))
            if not block:
                break
            digest.update(block)
            count += len(block)
        more = bool(stream.read(1)) if length is not None else False
    return 'sha256:' + digest.hexdigest(), count, more


def reference_source(path, *, appendable=False):
    if not isinstance(path, str) or not Path(path).is_absolute() or type(appendable) is not bool:
        raise SourceReferenceError('An explicit absolute original source is required')
    digest, length, _ = _scan(path)
    value = dict(storage=FORMAT, locator=path, digest=digest,
                 byte_length=length, appendable=appendable)
    return dict(value, source_id=_identity(value))


def _validate(reference):
    fields = ('storage', 'locator', 'digest', 'byte_length', 'appendable')
    if (any(k not in reference for k in fields) or reference['storage'] != FORMAT
            or not isinstance(reference['locator'], str) or not Path(reference['locator']).is_absolute()
            or type(reference['byte_length']) is not int or reference['byte_length'] < 0
            or type(reference['appendable']) is not bool
            or reference.get('source_id') != _identity({k: reference[k] for k in fields})):
        raise SourceReferenceError('Invalid original source reference')


def core_source_reference(source, root, owner, candidate_reference):
    if 'text' not in source and 'bytes_base64' not in source:
        return source
    return dict(storage=CORE_FORMAT, intent_root=str(root), owner=owner,
        candidate_reference=candidate_reference,
        **{k: source[k] for k in ('source_id', 'locator', 'digest')})


def _core_source(reference):
    root = Path(reference['intent_root'])
    if not root.is_absolute():
        raise SourceReferenceError('Original Core source needs its absolute owning root')
    try:
        with closing(sqlite3.connect((root / 'ghost-state.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
            row = connection.execute('SELECT payload_json FROM core_completion_candidates WHERE reference=?',
                                     (reference['candidate_reference'],)).fetchone()
    except sqlite3.Error as exc:
        raise OSError('Original Core source record is unavailable') from exc
    if row is None:
        raise SourceReferenceError('Original Core source record is unavailable')
    record = json.loads(row[0])
    owner = record.get('owner') or record.get('contract', {}).get('owner') or record.get('input_binding', {})
    if (_identity(record) != reference['candidate_reference'] or record.get('intent_root') != str(root)
            or any(owner.get(k) != reference['owner'].get(k) for k in ('platform', 'session_id'))):
        raise SourceReferenceError('Original Core source record is unbound')
    matches = [s for s in record['sources'] if all(s.get(k) == reference[k]
               for k in ('source_id', 'locator', 'digest'))]
    if len(matches) != 1 or ('text' not in matches[0] and 'bytes_base64' not in matches[0]):
        raise SourceReferenceError('Original Core source must identify one retained body')
    return matches[0]


def verify_source(reference):
    if reference.get('storage') == CORE_FORMAT:
        source_text(_core_source(reference))
        return
    _validate(reference)
    digest, length, more = _scan(reference['locator'], reference['byte_length'])
    if (digest != reference['digest'] or length != reference['byte_length']
            or (more and not reference['appendable'])):
        raise SourceReferenceError('Original source bytes changed')


def source_text(reference):
    if reference.get('storage') == CORE_FORMAT:
        return source_text(_core_source(reference))
    if reference.get('storage') != FORMAT:
        # Authenticated existing records can contain their original inline proof.
        if 'bytes_base64' in reference:
            raw = base64.b64decode(reference['bytes_base64'], validate=True)
            if 'sha256:' + hashlib.sha256(raw).hexdigest() != reference['digest']:
                raise SourceReferenceError('Original source bytes changed')
            return raw.decode('utf-8', errors='replace')
        return reference['text']
    _validate(reference)
    with Path(reference['locator']).open('rb') as stream:
        raw = stream.read(reference['byte_length'])
        more = bool(stream.read(1))
    if (len(raw) != reference['byte_length'] or (more and not reference['appendable'])
            or 'sha256:' + hashlib.sha256(raw).hexdigest() != reference['digest']):
        raise SourceReferenceError('Original source bytes changed')
    return raw.decode('utf-8', errors='replace')
