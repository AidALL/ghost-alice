"""Read one answer after displaying its current terminal question."""

from __future__ import annotations

import os
import sys

from installer import report


def _discard_previous_input() -> None:
    if not sys.stdin.isatty():
        return
    if os.name == 'nt':
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getwch()
    else:
        import termios
        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except termios.error as exc:
            raise OSError(str(exc)) from exc


def read_answer(question: str, *, preparation_failure_text: str | None = None) -> str | None:
    try:
        _discard_previous_input()
    except (OSError, ValueError):
        if preparation_failure_text:
            report.emit(preparation_failure_text)
        raise
    report.emit(question)
    try:
        return input()
    except EOFError:
        return None
