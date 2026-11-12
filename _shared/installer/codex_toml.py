"""Codex config syntax: TOML 1.1 with an optional initial UTF-8 BOM.

All hook installer reads and edit validation use the bundled upstream parser so
the supported Python 3.11+ runtime does not limit Codex's accepted TOML version.
"""
from installer._vendor.tomli import TOMLDecodeError, loads as _loads


def loads(content: str, **kwargs):
    return _loads(content.removeprefix("\ufeff"), **kwargs)


def load(handle, **kwargs):
    return loads(handle.read().decode("utf-8"), **kwargs)
