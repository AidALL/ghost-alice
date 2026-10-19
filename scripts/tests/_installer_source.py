import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def installer_bash_source() -> str:
    """Return the text of install.sh, the thin bash entry point.

    The shell holds only the bash guard, the UTF-8 locale setup and the Python
    runtime detection; `_shared/installer/` owns every other installer step.
    """
    return (REPO_ROOT / "install.sh").read_text(encoding="utf-8")


def installer_ps1_source() -> str:
    """Return the text of install.ps1, the thin PowerShell entry point, without its UTF-8 BOM.

    The shell holds only the PowerShell 7.6 guard, the UTF-8 console setup, the
    Python runtime detection and the mapping of the earlier PowerShell parameter
    names; `_shared/installer/` owns every other installer step.
    """
    return (REPO_ROOT / "install.ps1").read_text(encoding="utf-8-sig")


def powershell_function(text: str, name: str) -> str:
    """The text of `function <name> { ... }` in a PowerShell source, found by brace depth."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(rf"function {re.escape(name)}\b", line)), None)
    if start is None:
        raise AssertionError(f"function not found: {name}")
    depth = 0
    for end in range(start, len(lines)):
        depth += lines[end].count("{") - lines[end].count("}")
        if depth == 0:
            return "".join(lines[start:end + 1])
    raise AssertionError(f"function does not end: {name}")
