"""Line boundaries for text and append-only JSON Lines logs.

Dependencies: Python standard library. A line ends at LF, optionally preceded
by CR, as JSON Lines (https://jsonlines.org/) and NDJSON
(https://github.com/ndjson/ndjson-spec) define the record separator. Other
characters, such as U+2028, U+2029 and U+0085, belong to the line: JSON strings
may contain them unescaped, so str.splitlines() must not split this data.

An append-only log writer ends each record with LF. Content after the last LF is
a record that is still being written; it is read after its LF arrives, as log
tailers such as Vector do.
"""
from __future__ import annotations

import json
import re

# LINE_BREAK.split(text) keeps the empty part after a final line break, so joining
# the parts with LF rebuilds the text; split_lines drops that part.
LINE_BREAK = re.compile(r'\r?\n')


def split_lines(text):
    """Lines of complete text. A final line needs no LF; a final LF adds no empty line."""
    lines = LINE_BREAK.split(text)
    if lines[-1] == '':
        lines.pop()
    return lines


def committed_length(content):
    """Length of the prefix of an append-only log that ends at its last LF."""
    return content.rfind(b'\n' if isinstance(content, bytes) else '\n') + 1


def committed_lines(content):
    """Non-blank complete lines of an append-only log, keyed by one-based line number.

    Bytes are decoded as UTF-8 after the unfinished record is removed.
    """
    complete = content[:committed_length(content)]
    if isinstance(complete, bytes):
        complete = complete.decode('utf-8')
    return {number: line for number, line in enumerate(split_lines(complete), 1) if line.strip()}


def committed_records(content):
    """JSON values of committed_lines; a complete line that is not JSON raises ValueError."""
    return {number: json.loads(line) for number, line in committed_lines(content).items()}


def committed_byte_lines(raw, start=0):
    """(start, end, line) for each complete line of raw from start; end and line include the LF."""
    while (end := raw.find(b'\n', start)) >= 0:
        yield start, end + 1, raw[start:end + 1]
        start = end + 1


def byte_lines(raw):
    """Every line of raw with its line break; a final line without LF is the last line."""
    lines = [line for _, _, line in committed_byte_lines(raw)]
    if committed_length(raw) < len(raw):
        lines.append(raw[committed_length(raw):])
    return lines


def line_at(stream, number):
    """Text of one-based line number in a binary stream, or None. A final line without LF counts.

    Binary line iteration ends lines at LF only. The stream is read through the
    selected line and only that line is decoded, so a large file stays unloaded
    and an unfinished record after it cannot fail the lookup.
    """
    if number >= 1:
        for index, line in enumerate(stream, 1):
            if index == number:
                return split_lines(line.decode('utf-8'))[0]
    return None


def stream_lines(stream):
    """(number, text) for each line of a binary stream, read and decoded one line at a time."""
    for number, line in enumerate(stream, 1):
        yield number, split_lines(line.decode('utf-8'))[0]


def line_number(raw, offset):
    """One-based number of the line that contains offset."""
    return raw.count(b'\n', 0, offset) + 1
