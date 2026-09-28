#!/usr/bin/env python3
"""Read-only exact file-state capture and comparison.

Dependencies: Python 3.11+ standard library. Pass the token unchanged between
tools; it is a precision-preserving transport, not authorization or
a signed security attestation. Checks are observations, not atomic write locks.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

PREFIX = 'file-guard-v1:'
FIELDS = {'path', 'device', 'inode', 'size', 'mode', 'mtime_ns', 'sha256'}
INTEGER_FIELDS = FIELDS - {'path', 'sha256'}


class GuardError(ValueError):
    """Invalid evidence, unsupported path or observed file drift."""


def _path(value: str) -> Path:
    if not isinstance(value, str) or not value or '\x00' in value:
        raise GuardError('invalid file path')
    if '..' in Path(value).parts:
        raise GuardError('parent traversal is not supported; use the actual absolute file path')
    path = Path(os.path.abspath(value))
    # Do not resolve away a symlink before checking its actual path components.
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise GuardError(f'symlink path is not supported: {part}')
    return path


def _stable(st: os.stat_result) -> tuple[int, ...]:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _record(value: str) -> dict[str, str]:
    path = _path(value)
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode):
        raise GuardError(f'not a regular file: {path}')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or _stable(before) != _stable(initial):
            raise GuardError(f'file changed during capture: {path}')
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        named = _path(str(path)).lstat()
        if _stable(before) != _stable(after) or _stable(after) != _stable(named):
            raise GuardError(f'file changed during capture: {path}')
    return {
        'path': str(path), 'device': str(after.st_dev), 'inode': str(after.st_ino),
        'size': str(after.st_size), 'mode': str(after.st_mode),
        'mtime_ns': str(after.st_mtime_ns), 'sha256': digest.hexdigest(),
    }


def capture(paths: list[str]) -> str:
    if not paths:
        raise GuardError('invalid empty file set')
    records = [_record(path) for path in paths]
    if len({row['path'] for row in records}) != len(records):
        raise GuardError('invalid duplicate file path')
    payload = json.dumps({'schema': 'file-guard.v1', 'files': records}, sort_keys=True, separators=(',', ':')).encode()
    return PREFIX + base64.urlsafe_b64encode(payload).decode('ascii')


def _decode(token: str) -> list[dict[str, str]]:
    try:
        if not isinstance(token, str) or not token.startswith(PREFIX):
            raise ValueError('prefix')
        data = json.loads(base64.b64decode(token[len(PREFIX):], altchars=b'-_', validate=True))
        if not isinstance(data, dict) or set(data) != {'schema', 'files'} or data['schema'] != 'file-guard.v1':
            raise ValueError('schema')
        rows = data['files']
        if not isinstance(rows, list) or not rows:
            raise ValueError('file set')
        names = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != FIELDS or not all(isinstance(v, str) for v in row.values()):
                raise ValueError('fields must be exact strings')
            if not row['path'] or '\x00' in row['path'] or not Path(row['path']).is_absolute() or os.path.abspath(row['path']) != row['path'] or row['path'] in names:
                raise ValueError('path')
            names.add(row['path'])
            for key in INTEGER_FIELDS:
                if not re.fullmatch(r'-?(?:0|[1-9][0-9]*)' if key == 'mtime_ns' else r'(?:0|[1-9][0-9]*)', row[key]):
                    raise ValueError('integer encoding')
            if not re.fullmatch(r'[0-9a-f]{64}', row['sha256']):
                raise ValueError('digest')
        return rows
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error) as exc:
        raise GuardError(f'invalid file guard token: {exc}') from exc


def check(token: str) -> list[str]:
    records = _decode(token)
    for expected in records:
        observed = _record(expected['path'])
        differences = sorted(key for key in FIELDS if observed[key] != expected[key])
        if differences:
            raise GuardError(f'file drift: {expected["path"]}: {", ".join(differences)}')
    return [row['path'] for row in records]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    capture_args = sub.add_parser('capture', help='Capture exact regular-file state without writing files.')
    capture_args.add_argument('--path', action='append', required=True)
    check_args = sub.add_parser('check', help='Compare current files with an unchanged original token.')
    check_args.add_argument('--token', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'capture':
            token = capture(args.path)
            print(json.dumps({'token': token, 'paths': [row['path'] for row in _decode(token)]}))
        else:
            print(json.dumps({'unchanged': True, 'paths': check(args.token)}))
    except (GuardError, OSError) as exc:
        print(f'file-guard: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
