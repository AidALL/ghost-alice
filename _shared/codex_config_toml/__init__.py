"""Codex configuration syntax: TOML 1.1 and one initial UTF-8 BOM.

Dependencies: Python 3.11+ standard library and bundled Tomli 2.4.0.
"""
from ._vendor.tomli import TOMLDecodeError, loads as _loads

__all__ = ["TOMLDecodeError", "load", "loads"]


def loads(content: str, **kwargs):
    return _loads(content.removeprefix("\ufeff"), **kwargs)


def load(handle, **kwargs):
    return loads(handle.read().decode("utf-8"), **kwargs)
