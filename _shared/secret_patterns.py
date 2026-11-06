"""Patterns of secret-like values that recorded text must not carry.

Dependencies: Python standard library. `agent-security-scan` runs without `_shared`
and keeps a copy of this list; `_shared/test_secret_patterns.py` keeps the two equal.
"""
import re

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
