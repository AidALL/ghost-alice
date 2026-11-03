#!/usr/bin/env python3
"""Rules for public text: opaque terms, stale installer text, PowerShell code and Git Bash default advice.

FORBIDDEN_EXACT and BARE_LAYER_RE find opaque terms and bare layer tokens;
LINE_ALLOWLIST exempts lines that carry an external model identifier.
REMOVED_COMMAND_PATTERNS finds removed or unimplemented installer commands and
stale installer claims (dry-run previews, Node.js as optional).
POWERSHELL_COMMAND_PATTERNS finds any powershell code fence, an install.ps1
call, a PowerShell-style installer flag, and advice that presents Git Bash as
the Windows default.

The repository's text tests (scripts/tests/test_terminology_clarity.py,
scripts/tests/test_public_command_surface.py) and the wiki audit
(scripts/audit_wiki_integrity.py) import this module, so each shared rule has
one definition; test_public_command_surface.py adds one stricter rule of its own
for the primary install guides.
Each function reads one text and returns its findings; callers choose the files.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import re

LINE_ALLOWLIST = [
    # External model identifiers are API literals; clarify nearby prose instead.
    re.compile(r"korean-MiniLM-L6-v2"),
]

FORBIDDEN_EXACT = [
    "SG1",
    "cNoHardcode",
    "cModelDecision",
    "cInvariant",
    "cInjectionResistant",
    "cEnforce",
    "P1",
    "P2",
    "P3",
    "P4",
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "D6",
    "D7",
    "D8",
    "D9",
    "D10",
    "D11",
    "D12",
    "EN1",
    "IN1",
    "IN2",
    "IN3",
]

BARE_LAYER_RE = re.compile(r"(?<![A-Za-z0-9_])L[1-8](?![A-Za-z0-9_])")

_EXACT_PATTERNS = [
    (term, re.compile(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])"))
    for term in FORBIDDEN_EXACT
]

REMOVED_COMMAND_PATTERNS = {
    "removed offboard flag": re.compile(r"--offboard|--offboard-confirm|-Offboard\b"),
    "unimplemented top-level addon dry-run": re.compile(
        r"install\.sh\s+(?=[^\n]*--addon-source)(?=[^\n]*--dry-run)"
    ),
    "unsupported dry-run preview claim": re.compile(r"\bdry-run previews?\b"),
    "stale Node optional prerequisite": re.compile(r"Node\.js\s+\(optional\)"),
}

POWERSHELL_COMMAND_PATTERNS = {
    "PowerShell code fence": re.compile(r"```powershell", re.IGNORECASE),
    "PowerShell installer execution": re.compile(
        r"(?m)(?:^\s*(?:\.\\)?install\.ps1(?:\s+|$)|`(?:\.\\)?install\.ps1\s+(?:-|--|\w))",
        re.IGNORECASE,
    ),
    "PowerShell-style installer flag": re.compile(
        r"(?<![A-Za-z0-9])-(?:Platform|Visibility|AgentVisibility|Uninstall|AddonSource|AddonTag|Status|Doctor|List|CleanupPending|PromptPlatform|Skills)\b"
    ),
    "Git Bash as Windows default": re.compile(
        r"bash-first path|public Windows install shell|Git Bash is recommended on Windows|Do not paste the bash examples",
        re.IGNORECASE,
    ),
}


def term_findings(text: str) -> list[tuple[int, str]]:
    """(line number, message) for each opaque term and bare layer token on a line that LINE_ALLOWLIST does not match."""
    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for pattern in LINE_ALLOWLIST):
            continue
        for term, pattern in _EXACT_PATTERNS:
            if pattern.search(line):
                findings.append((lineno, f"opaque term `{term}`"))
        for match in BARE_LAYER_RE.finditer(line):
            findings.append((lineno, f"bare layer token `{match.group(0)}`"))
    return findings


def pattern_findings(text: str, patterns: dict[str, re.Pattern[str]]) -> list[tuple[int, str, str]]:
    """(line number, pattern label, stripped line) for each pattern that matches a line;
    a line that two patterns match yields two tuples."""
    return [
        (lineno, label, line.strip())
        for lineno, line in enumerate(text.splitlines(), start=1)
        for label, pattern in patterns.items()
        if pattern.search(line)
    ]
