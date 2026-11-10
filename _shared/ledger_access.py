"""Load the session-intent ledger once, check the coordinates a caller supplies for it, and name them as an environment.

Dependencies: Python 3.11+ standard library.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys

LEDGER_PATH = Path(__file__).resolve().parents[1] / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py"
MODULE_NAME = "session_intent_ledger"
PLATFORMS = frozenset({"codex", "claude"})


class LedgerUnavailable(ImportError):
    """The ledger module is absent from this installation."""

    def __init__(self, path):
        super().__init__(f"The session-intent ledger API is unavailable: {path}", name=MODULE_NAME, path=str(path))


class CoordinateError(ValueError):
    """The supplied coordinates do not name an exact ledger root and native owner."""

    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason


def _module_name(path):
    """The plain name while it is free or already holds this file; a per-path name beside another ledger."""
    retained = sys.modules.get(MODULE_NAME)
    if retained is None or (getattr(retained, "__file__", None)
                            and Path(retained.__file__).resolve() == path):
        return MODULE_NAME
    return MODULE_NAME + "_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]


def load_ledger(path=LEDGER_PATH):
    """The ledger module at `path`, loaded once per process; absent raises LedgerUnavailable, broken raises its own error."""
    path = Path(path).resolve()
    name = _module_name(path)
    retained = sys.modules.get(name)
    if retained is not None and getattr(retained, "__file__", None) and Path(retained.__file__).resolve() == path:
        return retained
    if not path.is_file():
        raise LedgerUnavailable(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise LedgerUnavailable(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def coordinates(source):
    """(ledger, resolved root, owner) for an exact root and native owner; CoordinateError names what is missing."""
    root = source.get("GHOST_ALICE_SESSION_INTENT_ROOT")
    owner = {key: source.get("GHOST_ALICE_" + key.upper()) for key in ("platform", "session_id")}
    if not (isinstance(root, str) and Path(root).is_absolute()):
        raise CoordinateError("ledger-root-unavailable", "Exact ledger root is required")
    if owner["platform"] not in PLATFORMS or not isinstance(owner["session_id"], str) or not owner["session_id"]:
        raise CoordinateError("owner-mismatch", "Exact native owner is required")
    if owner["platform"] == "codex" and source.get("CODEX_THREAD_ID") not in (None, "", owner["session_id"]):
        raise CoordinateError("owner-mismatch", "Native owner conflicts with the host")
    core = load_ledger()
    core.validate_identity(**owner)
    return core, Path(root).resolve(), owner


def source_environment(platform, session_id, *, root=None, native_dir=None, environment=None):
    """The environment-shaped mapping Core APIs read their coordinates from, over a copy of `environment` when given."""
    source = dict(environment) if environment is not None else {}
    source.update(GHOST_ALICE_PLATFORM=platform, GHOST_ALICE_SESSION_ID=session_id)
    if root is not None:
        source["GHOST_ALICE_SESSION_INTENT_ROOT"] = str(root)
    if native_dir is not None:
        source["GHOST_ALICE_CODEX_DIR" if platform == "codex" else "GHOST_ALICE_CLAUDE_DIR"] = str(native_dir)
    return source
