"""Ghost-ALICE shared secrets loader for Python.

All skills and scripts retrieve login information such as API keys, tokens,
passwords, and email credentials through the same path. Once a value is
registered, later calls retrieve it automatically instead of prompting each time.

Location: ~/.ghost-alice/secrets.env (mode 600)
Format: KEY=value (.env style, optional quotes)

Usage:

    # 1. Import the module
    import sys, os
    sys.path.insert(0, os.path.join("<project root>", "_shared", "secrets"))
    from secrets_loader import get, get_or_prompt, set as set_secret

    # 2. Look up from env/file only (None when missing)
    key = get("CONTEXT7_API_KEY")
    if key is None:
        raise RuntimeError("CONTEXT7_API_KEY is not registered")

    # 3. Look up env/file/prompt in order
    key = get_or_prompt("CONTEXT7_API_KEY", label="Context7 API key")

    # 4. Store directly
    set_secret("MY_KEY", "value")

Lookup order for every function:
    1) Already exported environment variable
    2) ~/.ghost-alice/secrets.env
    3) prompt (interactive environments only; get_or_prompt only)
"""

# Keep the filename as load.py so the module name does not collide with the
# stdlib `secrets` module; callers distinguish it by import path.

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path
from typing import Optional

__all__ = ["get", "set", "unset", "get_or_prompt", "list_keys", "SECRETS_FILE"]


def _secrets_path() -> Path:
    return Path(os.environ.get("GHOST_ALICE_SECRETS_FILE", str(Path.home() / ".ghost-alice" / "secrets.env")))


SECRETS_FILE = _secrets_path()

_HEADER = (
    "# Ghost-ALICE secrets. Plaintext KEY=value format.\n"
    "# Keep mode 600. Never commit this file to git.\n"
    "# One key per line. Quotes are optional (KEY=value and KEY=\"value\" are both allowed).\n"
)

_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _ensure_file() -> Path:
    path = _secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    if not path.exists():
        path.write_text(_HEADER, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _strip_quotes(val: str) -> str:
    val = val.rstrip("\r\n")
    if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
        return val[1:-1]
    return val


def _read_file() -> dict[str, str]:
    path = _secrets_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        m = _KEY_RE.match(line)
        if m:
            out[m.group(1)] = _strip_quotes(m.group(2))
    return out


def _write_file(values: dict[str, str]) -> None:
    path = _ensure_file()
    lines = [_HEADER]
    for key, val in values.items():
        lines.append(f"{key}={val}\n")
    path.write_text("".join(lines), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def get(key: str) -> Optional[str]:
    """Look up env var first, then file. Return None when missing."""
    val = os.environ.get(key)
    if val:
        return val
    return _read_file().get(key)


def set(key: str, value: str) -> None:  # noqa: A001. Intentional shadow
    """Store in the file. Existing keys are overwritten."""
    values = _read_file()
    values[key] = value
    _write_file(values)


def unset(key: str) -> None:
    """Remove from the file. Missing keys are ignored."""
    values = _read_file()
    if key in values:
        del values[key]
        _write_file(values)


def get_or_prompt(key: str, label: Optional[str] = None) -> Optional[str]:
    """Prefer env/file, then prompt with a save option. Return None for non-TTY or empty input."""
    val = get(key)
    if val is not None:
        return val

    label = label or key
    if not sys.stdin.isatty():
        sys.stderr.write(f"[WARN] {label} is not registered; skipping prompt in non-TTY mode.\n")
        return None

    sys.stderr.write(f"[INFO] {label} is not registered.\n")
    sys.stderr.write("Enter a value, or press Enter to skip: ")
    sys.stderr.flush()
    val = input().strip()
    if not val:
        return None

    sys.stderr.write(f"[INFO] Save to {_secrets_path()}? [Y/n] ")
    sys.stderr.flush()
    save_choice = input().strip().lower()
    if save_choice not in ("n", "no"):
        set(key, val)
        sys.stderr.write(f"[OK] {key} saved\n")

    return val


def _mask(val: str) -> str:
    n = len(val)
    if n <= 4:
        return "****"
    return f"{val[:2]}****{val[-2:]}  ({n} chars)"


def list_keys() -> None:
    """Print registered keys with masked values to stdout."""
    path = _secrets_path()
    if not path.exists():
        sys.stderr.write(f"[INFO] secrets file does not exist ({path}).\n")
        return
    values = _read_file()
    print(f"Registered secrets ({path}):\n")
    for key, val in values.items():
        print(f"  {key:<30} {_mask(val)}")


if __name__ == "__main__":
    # Simple CLI: python load.py [list|get KEY|set KEY VALUE|unset KEY]
    args = sys.argv[1:]
    if not args or args[0] == "list":
        list_keys()
    elif args[0] == "get" and len(args) == 2:
        v = get(args[1])
        if v is None:
            sys.exit(1)
        print(v)
    elif args[0] == "set" and len(args) == 3:
        set(args[1], args[2])
        print(f"[OK] {args[1]} saved")
    elif args[0] == "unset" and len(args) == 2:
        unset(args[1])
        print(f"[OK] {args[1]} removed")
    else:
        sys.stderr.write("Usage: python load.py [list | get KEY | set KEY VALUE | unset KEY]\n")
        sys.exit(2)
