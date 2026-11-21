"""Authenticate original files without retaining additional copies of their bodies."""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

FORMAT = 'source-reference.v1'
CORE_FORMAT = 'core-source-reference.v1'


class SourceReferenceError(ValueError):
    """The supplied original reference or bytes do not match."""


def canonical_json(value):
    """Core's one JSON form of a value: sorted keys, no spaces, unescaped non-ASCII."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def text_digest(value):
    """`sha256:<hex>` of text's UTF-8 bytes, or of bytes as supplied."""
    return 'sha256:' + hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def json_digest(value):
    """`sha256:<hex>` of the canonical JSON form of a value."""
    return text_digest(canonical_json(value))


def matches_input(text, binding):
    """Whether text has the staged input's character count and UTF-8 digest.

    Staging digests the hook prompt's UTF-8 bytes, so text with an unpaired
    surrogate equals no staged input; it is a mismatch, not an error.
    """
    if (not isinstance(text, str) or not isinstance(binding, dict)
            or type(binding.get('input_char_count')) is not int
            or not isinstance(binding.get('input_digest'), str)
            or len(text) != binding['input_char_count']):
        return False
    try:
        return text_digest(text) == binding['input_digest']
    except UnicodeEncodeError:
        return False


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
    return reference_source_prefix(path, length, digest, appendable=appendable)



def reference_source_prefix(path, byte_length, digest, *, appendable=True):
    """Describe a captured prefix using the caller's length and digest, without reading its file.

    Construction binds metadata; source_bytes or verify_source authenticates
    the described bytes against the original file.
    """
    value = dict(storage=FORMAT, locator=path, digest=digest,
                 byte_length=byte_length, appendable=appendable)
    reference = dict(value, source_id=json_digest(value))
    validate_source_reference(reference)
    return reference

def validate_source_reference(reference, *, exact=False):
    """Validate file-reference metadata, optionally requiring only the stored v1 fields."""
    fields = ('storage', 'locator', 'digest', 'byte_length', 'appendable')
    if (not isinstance(reference, dict) or any(k not in reference for k in fields)
            or (exact and set(reference) != {*fields, 'source_id'}) or reference['storage'] != FORMAT
            or not isinstance(reference['locator'], str) or not Path(reference['locator']).is_absolute()
            or type(reference['byte_length']) is not int or reference['byte_length'] < 0
            or type(reference['appendable']) is not bool
            or reference.get('source_id') != json_digest({k: reference[k] for k in fields})):
        raise SourceReferenceError('Invalid original source reference')

def core_source_reference(source, root, owner, candidate_reference):
    if 'text' not in source and 'bytes_base64' not in source:
        return source
    return dict(storage=CORE_FORMAT, intent_root=str(root), owner=owner,
        candidate_reference=candidate_reference,
        **{k: source[k] for k in ('source_id', 'locator', 'digest')})


def _core_source(reference, *, storage_root=None):
    root = Path(reference['intent_root'])
    location = root if storage_root is None else Path(storage_root)
    if not root.is_absolute() or not location.is_absolute():
        raise SourceReferenceError('Original source needs its absolute owning root')
    try:
        with closing(sqlite3.connect((location / 'ghost-state.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
            row = connection.execute('SELECT payload_json FROM core_completion_candidates WHERE reference=?',
                                     (reference['candidate_reference'],)).fetchone()
    except sqlite3.Error as exc:
        raise OSError('Original source record is unavailable') from exc
    if row is None:
        raise SourceReferenceError('Original source record is unavailable')
    record = json.loads(row[0])
    owner = record.get('owner') or record.get('contract', {}).get('owner') or {}
    if (json_digest(record) != reference['candidate_reference'] or record.get('intent_root') != str(root)
            or any(owner.get(k) != reference['owner'].get(k) for k in ('platform', 'session_id'))):
        raise SourceReferenceError('Original source record is unbound')
    matches = [s for s in record['sources'] if all(s.get(k) == reference[k]
               for k in ('source_id', 'locator', 'digest'))]
    if len(matches) != 1 or ('text' not in matches[0] and 'bytes_base64' not in matches[0]):
        raise SourceReferenceError('Original source must identify one retained body')
    return matches[0]


def _file_source_path(reference, storage_root):
    """Select an installed copy only from its recorded retirement into the fixed ledger."""
    original = Path(reference['locator'])
    if storage_root is None:
        return original
    location = Path(storage_root)
    if (not location.is_absolute() or location.name != 'session-intent'
            or '..' in location.parts or '..' in original.parts):
        return original
    import runtime_config
    env = {'GHOST_ALICE_GHOST_ROOT': str(location.parent)}
    if runtime_config.config_path(env=env).resolve() != (location.parent / 'config.json').resolve():
        return original
    retired = runtime_config.load_config(env=env, strict=True).get('retired_ledger_roots', [])
    if (not isinstance(retired, list) or any(not isinstance(root, str) or not Path(root).is_absolute()
                                            or '..' in Path(root).parts for root in retired)):
        raise SourceReferenceError('Installation config has invalid retired ledger roots')
    copies = set()
    for root in map(Path, retired):
        # Preserve the path below A; only normalize a surviving ancestor alias such as /var -> /private/var.
        ancestor = root if original.is_relative_to(root) else next(
            (parent for parent in original.parents if parent.resolve() == root), None)
        if ancestor is not None:
            copies.add(location / original.relative_to(ancestor))
    if len(copies) > 1:
        raise SourceReferenceError('Original source matches more than one retired ledger root')
    return next(iter(copies), original)


def verify_source(reference, *, storage_root=None):
    if reference.get('storage') == CORE_FORMAT:
        source_text(_core_source(reference, storage_root=storage_root))
        return
    validate_source_reference(reference)
    digest, length, more = _scan(_file_source_path(reference, storage_root), reference['byte_length'])
    if (digest != reference['digest'] or length != reference['byte_length']
            or (more and not reference['appendable'])):
        raise SourceReferenceError('Original source bytes changed')

def source_bytes(reference, *, identity=None, storage_root=None):
    """Return authenticated file bytes, checking optional device/inode on the same open file."""
    validate_source_reference(reference)
    with _file_source_path(reference, storage_root).open('rb') as stream:
        if identity is not None:
            actual = os.fstat(stream.fileno())
            if (not isinstance(identity, dict) or set(identity) != {'device', 'inode'}
                    or (actual.st_dev, actual.st_ino) != (identity['device'], identity['inode'])):
                raise SourceReferenceError('Original source file identity changed')
        raw = stream.read(reference['byte_length'])
        more = bool(stream.read(1))
    if (len(raw) != reference['byte_length'] or (more and not reference['appendable'])
            or text_digest(raw) != reference['digest']):
        raise SourceReferenceError('Original source bytes changed')
    return raw



def source_text(reference, *, storage_root=None):
    """Read original bytes from an explicit physical ledger without rewriting their binding."""
    if reference.get('storage') == CORE_FORMAT:
        return source_text(_core_source(reference, storage_root=storage_root))
    if reference.get('storage') != FORMAT:
        # Authenticated existing records can contain their original inline proof.
        if 'bytes_base64' in reference:
            raw = base64.b64decode(reference['bytes_base64'], validate=True)
            if 'sha256:' + hashlib.sha256(raw).hexdigest() != reference['digest']:
                raise SourceReferenceError('Original source bytes changed')
            return raw.decode('utf-8', errors='replace')
        return reference['text']
    return source_bytes(reference, storage_root=storage_root).decode('utf-8', errors='replace')
