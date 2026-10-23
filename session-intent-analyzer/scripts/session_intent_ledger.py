#!/usr/bin/env python3
"""Maintain a session-local semantic intent ledger.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def assert_mutation_path(path, *, env=None):
    """Expose Core's single location guard without breaking normal skill copies."""
    source=os.environ if env is None else env
    if not source.get("GHOST_ALICE_OWNED_ROOT"):
        return Path(path)
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"_shared"))
    from install_destination import assert_mutation_path as guard
    return guard(path,env=source)


_assert_location_write = assert_mutation_path


SCHEMA_VERSION = "session-intent-ledger.v1"
CURRENT_SESSION_SCHEMA = "session-intent-current.v1"
SESSION_INTENT_ROOT_ENV = "GHOST_ALICE_SESSION_INTENT_ROOT"
LEGACY_DEFAULT_ROOT = Path("~/.ghost-alice/session-intent")
TEXT_FIELDS = ("current_goal", "user_intent_summary", "routing_summary")
LIST_FIELDS = ("constraints", "non_goals", "open_questions", "risk_flags")
ACCEPTANCE_CRITERIA_SOURCES = {"user-said", "interpreted-as", "previous-tool", "system-doc"}
ACCEPTANCE_CRITERIA_STATUSES = {"unmet", "met"}
ACCEPTANCE_CRITERIA_ADMITTED_SOURCES = {"user-said", "previous-tool", "system-doc"}
# Stored labels are never rewritten; readers map a legacy label to its current name.
LEGACY_SOURCE_ALIASES = {"user-explicit": "user-said", "inferred": "interpreted-as"}
COMPLETION_CHECK_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
SECURITY_DECISIONS = {"allow", "block"}
SECURITY_REASON_MAX = 240
SECURITY_RISK_FLAG_MAX = 12
CONDUCT_FEEDBACK_SOURCES = {"user-said", "interpreted-as"}
CONDUCT_FEEDBACK_STATUS = {"open", "encoded"}
# Stable failure families let differently named corrections recur visibly across sessions.
CONDUCT_PATTERN_CLASSES = (
    "redundant-verification",
    "missed-reverification",
    "wrong-copy-evidence",
    "unsupported-claim",
    "restatement-loop",
    "unrequested-content",
    "under-delivery",
    "unnecessary-reapproval",
    "scope-widening",
    "scope-narrowing",
    "premise-mismatch-without-stop",
    "governance-record-error",
    "domain-rule",
)
SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.=-]+")
# JSON numbers must retain their exact value in the JavaScript consumers.
MAX_SHARED_INTEGER = (1 << 53) - 1
INPUT_ANCHOR_FIELDS = ("ledger_revision", "latest_input_event_id", "latest_input_digest", "latest_input_char_count")


def is_ghost_alice_repo_root(path: Path) -> bool:
    return (
        (path / "install.sh").exists()
        and (path / "skill-catalog").is_dir()
        and (path / "session-intent-analyzer").is_dir()
    )


def discover_repo_root(cwd: Path | None = None) -> Path | None:
    start = Path(cwd) if cwd is not None else Path.cwd()
    start = start.expanduser().resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if is_ghost_alice_repo_root(candidate):
            return candidate
    script_root = Path(__file__).resolve()
    for candidate in script_root.parents:
        if is_ghost_alice_repo_root(candidate):
            return candidate
    return None


def default_root(
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    source_env = os.environ if env is None else env
    configured = source_env.get(SESSION_INTENT_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    repo = discover_repo_root(cwd)
    if repo is not None:
        return repo / ".tmp" / "session-intent"
    return LEGACY_DEFAULT_ROOT.expanduser()


DEFAULT_ROOT = default_root()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_component(value: str | None, fallback: str = "unknown") -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    cleaned = SAFE_COMPONENT.sub("-", text).strip(".-")
    return cleaned[:120] or fallback


def input_digest(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return input_digest(encoded)


def build_input_observation(*, platform: str, session_id: str, raw_user_input: str | None) -> dict[str, Any]:
    observed_at = utc_now()
    observation: dict[str, Any] = {
        "observed_at": observed_at,
        "input_event_id": "",
        "input_digest": "",
        "input_char_count": 0,
    }
    if raw_user_input is None:
        return observation
    digest = input_digest(raw_user_input)
    observation["input_digest"] = digest
    observation["input_char_count"] = len(raw_user_input)
    observation["input_event_id"] = json_digest({
        "event": "user-input-observed",
        "platform": platform,
        "session_id": session_id,
        "input_digest": digest,
        "observed_at": observed_at,
        "event_nonce": uuid.uuid4().hex,
    })
    return observation


def session_paths(root: Path, platform: str, session_id: str) -> dict[str, Path]:
    session_dir = root.expanduser() / safe_component(platform) / safe_component(session_id)
    return {
        "dir": session_dir,
        "state": session_dir / "intent-state.json",
        "events": session_dir / "intent-events.jsonl",
        "security": session_dir / "security-events.jsonl",
    }


def current_session_pointer_path(root: Path, platform: str) -> Path:
    return root.expanduser() / safe_component(platform) / "current-session.json"


def write_current_session_pointer(root: Path, platform: str, session_id: str) -> Path:
    paths = session_paths(root, platform, session_id)
    pointer = current_session_pointer_path(root, platform)
    payload = {
        "schema_version": CURRENT_SESSION_SCHEMA,
        "platform": platform,
        "session_id": safe_component(session_id),
        "state_path": str(paths["state"]),
        "updated_at": utc_now(),
    }
    write_json(pointer, payload)
    return pointer


def read_current_session_pointer(root: Path, platform: str, *, transaction: sqlite3.Connection | None = None) -> str:
    with _read_connection(root, transaction) as connection:
        if connection is not None:
            row = connection.execute("SELECT session_id FROM session_discovery WHERE platform=?", (platform,)).fetchone()
            if row:
                return row[0]
    path = current_session_pointer_path(root, platform)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("schema_version") != CURRENT_SESSION_SCHEMA:
        return ""
    return safe_component(str(data.get("session_id") or ""), "")


def resolve_session_id(
    *,
    root: Path = DEFAULT_ROOT,
    platform: str,
    explicit: str | None = None,
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    for_write: bool = False,
) -> str:
    payload = payload or {}
    env = env or {}
    for candidate in (
        explicit,
        payload.get("session_id"),
        payload.get("sessionId"),
        payload.get("conversation_id"),
        payload.get("thread_id"),
        env.get("CODEX_THREAD_ID") if safe_component(platform) == "codex" else None,
        env.get("GHOST_ALICE_SESSION_ID"),
        None if for_write else read_current_session_pointer(root, platform),
    ):
        if for_write and candidate not in (None, ""):
            validate_identity(platform, candidate)
            return candidate
        value = safe_component(str(candidate or ""), "")
        if value:
            return value
    if for_write:
        raise ValueError("session identity is required for intake; a shared pointer cannot select a write destination")
    return "unknown"


def default_state(platform: str, session_id: str) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "session_id": session_id,
        "ledger_revision": 0,
        "latest_input_event_id": "",
        "canonical_input_event_id": "",
        "current_context_input_event_id": "",
        "pending_native_inputs": [],
        "native_input_cursors": {},
        "native_input_waiting": {},
        "native_input_notified_causes": [],
        "latest_input_digest": "",
        "latest_input_char_count": 0,
        "created_at": now,
        "updated_at": now,
        "current_goal": "",
        "user_intent_summary": "",
        "constraints": [],
        "non_goals": [],
        "open_questions": [],
        "acceptance_criteria": [],
        "work_units": [],
        "response_route": None,
        "decisions": [],
        "conduct_feedback": [],
        "risk_flags": [],
        "consumer_hints": {},
        "model_security_decision": None,
        "intake_status": "pending",
        "last_intake_source": "",
        "last_semantic_delta_status": "not-provided",
        "semantic_delta_policy": "agent-updates-when-intent-materially-changes",
    }


def load_state(path: Path, platform: str = "unknown", session_id: str = "unknown") -> dict[str, Any]:
    if path.name == "intent-state.json" and storage_is_initialized(path.parents[2]):
        return read_session_state(root=path.parents[2], platform=path.parent.parent.name, session_id=path.parent.name)
    if not path.exists():
        return default_state(platform, session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state(platform, session_id)
    if not isinstance(data, dict):
        return default_state(platform, session_id)
    state = default_state(
        str(data.get("platform") or platform),
        str(data.get("session_id") or session_id),
    )
    state.update(data)
    state["schema_version"] = SCHEMA_VERSION
    return state


def _atomic_write(path: Path, text: str) -> None:
    """Replace one file; readers see either complete version, never a prefix."""
    _assert_location_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def validate_identity(platform: str, session_id: str) -> None:
    """Mutation identities are exact, never sanitized aliases of another ID."""
    for value in (platform, session_id):
        if not isinstance(value, str) or not value or safe_component(value, "") != value:
            raise ValueError("invalid ledger identity: use the exact canonical platform and session ID from the hook receipt")


def _load_legacy_state(paths: dict[str, Path], platform: str, session_id: str, *, data=None) -> dict[str, Any]:
    if not paths["state"].exists():
        if paths["events"].exists():
            raise ValueError("ledger state is missing but audit exists; inspect and restore the state before writing")
        return default_state(platform, session_id)
    if data is None:
        try:
            data = json.loads(paths["state"].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError):
            raise ValueError("ledger state is corrupt; inspect and restore it before writing") from None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("ledger state header is missing or unsupported; inspect it before writing")
    if data.get("platform") != platform or data.get("session_id") != session_id:
        raise ValueError("persisted ledger identity mismatch; no mutation was applied")
    revision = data.get("ledger_revision", 0)
    if type(revision) is not int or not 0 <= revision <= MAX_SHARED_INTEGER:
        raise ValueError("ledger revision is invalid; inspect it before writing")
    has_anchor = any(key in data for key in INPUT_ANCHOR_FIELDS)
    if has_anchor and not all(key in data for key in INPUT_ANCHOR_FIELDS):
        raise ValueError("ledger input anchor is incomplete; inspect it before writing")
    state = default_state(platform, session_id)
    state.update(data)
    # Reading imported state must not fabricate a creation/update instant
    # on every call. Unknown historical timestamps stay explicitly unknown.
    for key in ("created_at", "updated_at"):
        if key not in data:
            state[key] = ""
    if not has_anchor:
        # For imported v1 state without an anchor, recover it from the validated
        # audit before committing the session to SQLite.
        try:
            rows = (_shared_module('text_lines').split_lines(paths["events"].read_text(encoding="utf-8"))
                    if paths["events"].exists() else [])
            for line in rows:
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("platform") != platform or event.get("session_id") != session_id:
                    raise ValueError("legacy ledger audit identity mismatch")
                if event.get("event") == "user-input-observed":
                    if not event.get("event_id") or not event.get("input_digest"):
                        raise ValueError("legacy input lineage is incomplete")
                    state["latest_input_event_id"] = event["event_id"]
                    state["latest_input_digest"] = event["input_digest"]
                    state["latest_input_char_count"] = event.get("input_char_count", 0)
        except (json.JSONDecodeError, UnicodeError):
            raise ValueError("source ledger audit is corrupt; inspect it before writing") from None
    for key in ("latest_input_event_id", "latest_input_digest"):
        if not isinstance(state.get(key), str):
            raise ValueError("ledger input lineage is invalid")
    if bool(state["latest_input_event_id"]) != bool(state["latest_input_digest"]):
        raise ValueError("ledger input lineage is incomplete")
    count = state.get("latest_input_char_count")
    if type(count) is not int or not 0 <= count <= MAX_SHARED_INTEGER:
        raise ValueError("ledger input character count is invalid; inspect it before writing")
    return state


def _check_expected(state: dict[str, Any], expected_input_event_id: str | None,
                    expected_revision: int | None, *, new_input: bool = False) -> None:
    if _pending_native_inputs(state):
        raise ValueError("native input origin is pending; reconcile native intake before writing")
    if state.get('current_context_input_event_id'):
        raise ValueError("current SDK context input supplies no human mutation authority")
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision != state["ledger_revision"]
    ):
        raise ValueError("stale ledger revision; read the current state and re-evaluate before writing")
    active_input = state["latest_input_event_id"]
    if active_input and expected_input_event_id is None and not new_input:
        raise ValueError("expected-input-event-id is required; use the input_event_id from the receipt used for this decision")
    if expected_input_event_id is not None and expected_input_event_id != active_input:
        raise ValueError("stale input event; re-evaluate against the current user input before writing")


STORAGE_SCHEMA_VERSION = 1


def storage_database_path(root: Path) -> Path:
    return Path(root).expanduser().resolve() / "ghost-state.sqlite3"


def storage_is_initialized(root: Path) -> bool:
    database = storage_database_path(root)
    return database.exists() or (database.parent / ".sqlite-authority.json").exists()


def _require_database_if_initialized(root: Path) -> None:
    database = storage_database_path(root)
    if (database.parent / ".sqlite-authority.json").exists() and not database.is_file():
        raise ValueError("authoritative SQLite database is missing; restore it, do not use JSON exports as authority")


def _check_database(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != STORAGE_SCHEMA_VERSION:
        raise ValueError("unsupported Ghost state database schema; no JSON fallback is permitted")


def _initialize_database(connection: sqlite3.Connection) -> None:
    # Individual statements preserve the caller's transaction; executescript
    # would implicitly commit and break Core atomicity.
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, STORAGE_SCHEMA_VERSION):
        raise ValueError("unsupported Ghost state database schema")
    connection.execute("""CREATE TABLE IF NOT EXISTS sessions (
        platform TEXT NOT NULL, session_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 0), input_event_id TEXT NOT NULL,
        state_json TEXT NOT NULL, PRIMARY KEY(platform, session_id))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS intent_events (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, sequence INTEGER NOT NULL,
        mutation_id TEXT, event_json TEXT NOT NULL,
        PRIMARY KEY(platform, session_id, sequence),
        FOREIGN KEY(platform, session_id) REFERENCES sessions(platform, session_id))""")
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS intent_mutations
        ON intent_events(platform, session_id, mutation_id) WHERE mutation_id IS NOT NULL""")
    connection.execute("""CREATE TABLE IF NOT EXISTS canonical_intakes (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, input_event_id TEXT NOT NULL,
        input_digest TEXT NOT NULL, input_char_count INTEGER NOT NULL CHECK(input_char_count >= 0),
        event_sequence INTEGER NOT NULL CHECK(event_sequence > 0), PRIMARY KEY(platform, session_id),
        FOREIGN KEY(platform, session_id, event_sequence)
        REFERENCES intent_events(platform, session_id, sequence))""")
    connection.execute('''CREATE TABLE IF NOT EXISTS core_context_intakes (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, input_event_id TEXT NOT NULL,
        payload_json TEXT NOT NULL, staged_sequence INTEGER NOT NULL, resolved_sequence INTEGER NOT NULL,
        PRIMARY KEY(platform,session_id,input_event_id),
        FOREIGN KEY(platform,session_id,staged_sequence) REFERENCES intent_events(platform,session_id,sequence),
        FOREIGN KEY(platform,session_id,resolved_sequence) REFERENCES intent_events(platform,session_id,sequence))''')
    for operation in ('UPDATE', 'DELETE'):
        connection.execute(f'''CREATE TRIGGER IF NOT EXISTS immutable_context_intake_{operation.lower()}
            BEFORE {operation} ON core_context_intakes
            BEGIN SELECT RAISE(ABORT, 'immutable Core context intake'); END''')
    connection.execute("""CREATE TABLE IF NOT EXISTS legacy_migrations (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, sources_json TEXT NOT NULL,
        imported_at TEXT NOT NULL, PRIMARY KEY(platform, session_id),
        FOREIGN KEY(platform, session_id) REFERENCES sessions(platform, session_id))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS session_discovery (
        platform TEXT PRIMARY KEY, session_id TEXT NOT NULL,
        FOREIGN KEY(platform, session_id) REFERENCES sessions(platform, session_id))""")
    connection.execute(f"PRAGMA user_version = {STORAGE_SCHEMA_VERSION}")


def _enable_wal(connection: sqlite3.Connection) -> None:
    # Concurrent first opens may return SQLITE_BUSY while changing journal
    # mode without invoking SQLite's busy handler. Retry only this setup step;
    # no semantic transaction has begun and no user mutation is replayed.
    deadline = time.monotonic() + 10.0
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


@contextmanager
def storage_transaction(root: Path):
    """One durable unit of work.

    Lock waiting limits contention, not the lifetime of an autonomous task.
    Connections are operation-local and must be passed explicitly to nested
    writers; this also works when adapters load the module under another name.
    """
    database = storage_database_path(root)
    _require_database_if_initialized(root)
    _assert_location_write(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=10, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        _enable_wal(connection)
        connection.execute("PRAGMA synchronous = FULL")
        # Schema creation is independent of session import. A failed import
        # leaves an empty, usable database instead of a half-imported session.
        connection.execute("BEGIN IMMEDIATE")
        _initialize_database(connection)
        connection.commit()
        marker = database.parent / ".sqlite-authority.json"
        if not marker.exists():
            # The authority marker prevents fallback to JSON if the database is lost.
            # Write it before the first semantic commit; recovery restores the database.
            write_json(marker, {"schema_version": "ghost-sqlite-authority.v1", "database": database.name})
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
    finally:
        connection.close()


def _validate_transaction(root: Path, transaction: sqlite3.Connection) -> None:
    databases = transaction.execute("PRAGMA database_list").fetchall()
    main = next((row[2] for row in databases if row[1] == "main"), "")
    if not main or Path(main).resolve() != storage_database_path(root):
        raise ValueError("transaction database does not match the requested ledger root")
    if not transaction.in_transaction:
        raise ValueError("an active storage transaction is required")
    _check_database(transaction)


@contextmanager
def _transaction(root: Path, transaction: sqlite3.Connection | None = None):
    if transaction is not None:
        _validate_transaction(root, transaction)
        # A failed nested operation cannot leave half its changes in an outer
        # transaction whose caller catches the exception and continues.
        savepoint = "ghost_" + uuid.uuid4().hex
        transaction.execute(f"SAVEPOINT {savepoint}")
        try:
            yield transaction
        except BaseException:
            transaction.execute(f"ROLLBACK TO {savepoint}")
            raise
        finally:
            transaction.execute(f"RELEASE {savepoint}")
    else:
        with storage_transaction(root) as connection:
            yield connection


@contextmanager
def _read_connection(root: Path, transaction: sqlite3.Connection | None = None):
    if transaction is not None:
        _validate_transaction(root, transaction)
        yield transaction
        return
    database = storage_database_path(root)
    _require_database_if_initialized(root)
    if not database.exists():
        yield None
        return
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10, isolation_level=None)
    try:
        _check_database(connection)
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def _stored_state(connection, platform: str, session_id: str):
    if connection is None:
        return None
    row = connection.execute("SELECT revision,input_event_id,state_json FROM sessions WHERE platform=? AND session_id=?",
                             (platform, session_id)).fetchone()
    if row is None:
        return None
    state = json.loads(row[2])
    if (not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION
            or state.get("platform") != platform or state.get("session_id") != session_id
            or type(state.get("ledger_revision")) is not int or state["ledger_revision"] != row[0]
            or state.get("latest_input_event_id") != row[1]):
        raise ValueError("database session identity or revision is inconsistent")
    if not 0 <= state["ledger_revision"] <= MAX_SHARED_INTEGER:
        raise ValueError("ledger revision is invalid")
    if not all(key in state for key in INPUT_ANCHOR_FIELDS):
        raise ValueError("ledger input anchor is incomplete")
    for key in ("latest_input_event_id", "latest_input_digest"):
        if not isinstance(state[key], str):
            raise ValueError("ledger input lineage is invalid")
    if bool(state["latest_input_event_id"]) != bool(state["latest_input_digest"]):
        raise ValueError("ledger input lineage is incomplete")
    count = state["latest_input_char_count"]
    if type(count) is not int or not 0 <= count <= MAX_SHARED_INTEGER:
        raise ValueError("ledger input character count is invalid")
    return state


def _legacy_events(paths: dict[str, Path], platform: str, session_id: str) -> list[dict[str, Any]]:
    if not paths["events"].exists():
        return []
    result = []
    for line in _shared_module('text_lines').split_lines(paths["events"].read_text(encoding="utf-8")):
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict) or event.get("platform") != platform or event.get("session_id") != session_id:
            raise ValueError("legacy ledger audit identity mismatch")
        result.append(event)
    return result


def _save_state(connection, state):
    connection.execute("""INSERT INTO sessions VALUES(?,?,?,?,?)
        ON CONFLICT(platform,session_id) DO UPDATE SET revision=excluded.revision,
        input_event_id=excluded.input_event_id,state_json=excluded.state_json""",
        (state["platform"], state["session_id"], state["ledger_revision"], state["latest_input_event_id"],
         json.dumps(state, ensure_ascii=False, sort_keys=True)))


def _append_event(connection, platform, session_id, event):
    connection.execute("""INSERT INTO intent_events(platform,session_id,sequence,mutation_id,event_json)
        VALUES(?,?,(SELECT COALESCE(MAX(sequence),0)+1 FROM intent_events WHERE platform=? AND session_id=?),?,?)""",
        (platform, session_id, platform, session_id, event.get("mutation_id") or None,
         json.dumps(event, ensure_ascii=False, sort_keys=True)))


def _load_state_for_update(connection, paths, platform, session_id):
    state = _stored_state(connection, platform, session_id)
    if state is not None:
        return state
    sources = {key: hashlib.sha256(paths[key].read_bytes()).hexdigest()
               for key in ("state", "events", "security") if paths[key].exists()}
    state = _load_legacy_state(paths, platform, session_id)
    # Portable state may carry a copied intake stamp or ready route. Only a
    # fresh intake committed here can establish canonical current authority.
    state["canonical_input_event_id"] = ""
    if isinstance(state.get("response_route"), dict):
        state["response_route"]["status"] = "stale"
    events = _legacy_events(paths, platform, session_id)
    pending = state.pop("pending_audit_event", None)
    if pending is not None:
        if (not isinstance(pending, dict) or pending.get("platform") != platform
                or pending.get("session_id") != session_id or not pending.get("mutation_id")
                or pending.get("ledger_revision") != state["ledger_revision"]):
            raise ValueError("pending ledger audit is invalid")
        matches = [row for row in events if row.get("mutation_id") == pending["mutation_id"]]
        if matches and matches != [pending]:
            raise ValueError("ledger audit conflicts with committed state")
        if not matches:
            events.append(pending)
    # Detect source-file mutations during import; source writers
    # must be stopped during import. The originals remain a recovery source.
    after = {key: hashlib.sha256(paths[key].read_bytes()).hexdigest()
             for key in ("state", "events", "security") if paths[key].exists()}
    if after != sources:
        raise ValueError("JSON source changed during import; retry after stopping its writer")
    _save_state(connection, state)
    for event in events:
        _append_event(connection, platform, session_id, event)
    if sources:
        connection.execute("INSERT INTO legacy_migrations VALUES(?,?,?,?)",
                           (platform, session_id, json.dumps(sources, sort_keys=True), utc_now()))
    return state


def migrate_session(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
                    transaction: sqlite3.Connection | None = None) -> dict[str, Any]:
    validate_identity(platform, session_id)
    with _transaction(root, transaction) as connection:
        return _load_state_for_update(connection, session_paths(root, platform, session_id), platform, session_id)


def _commit(connection, state: dict[str, Any], event: dict[str, Any]) -> None:
    if state["ledger_revision"] >= MAX_SHARED_INTEGER:
        raise ValueError("ledger revision has reached the shared JSON integer limit; no mutation was applied")
    state["ledger_revision"] += 1
    event.update(ledger_revision=state["ledger_revision"], mutation_id=uuid.uuid4().hex)
    _save_state(connection, state)
    _append_event(connection, state["platform"], state["session_id"], event)
    if state["session_id"] != "unknown":
        connection.execute("""INSERT INTO session_discovery VALUES(?,?)
            ON CONFLICT(platform) DO UPDATE SET session_id=excluded.session_id""", (state["platform"], state["session_id"]))


def read_session_state(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
                       recover_audit: bool = False, transaction: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Read authority without creating files. JSON recovery requires explicit import."""
    validate_identity(platform, session_id)
    if recover_audit:
        return migrate_session(root=root, platform=platform, session_id=session_id, transaction=transaction)
    with _read_connection(root, transaction) as connection:
        state = _stored_state(connection, platform, session_id)
        if state is not None:
            return state
        paths = session_paths(root, platform, session_id)
        # Preserve absent input metadata rather than inventing proof of a
        # current intake. Normalize only after validating its audit.
        if not paths["state"].exists():
            _load_legacy_state(paths, platform, session_id)
            return {}
        raw = json.loads(paths["state"].read_text(encoding="utf-8"))
        _load_legacy_state(paths, platform, session_id, data=raw)
        # A pending import projection does not invalidate its committed current
        # block. Preserve the pending marker; import reconciles it atomically.
        return raw


def read_session_events(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
                        transaction: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    validate_identity(platform, session_id)
    with _read_connection(root, transaction) as connection:
        if _stored_state(connection, platform, session_id) is not None:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT event_json FROM intent_events WHERE platform=? AND session_id=? ORDER BY sequence",
                (platform, session_id))]
        return _legacy_events(session_paths(root, platform, session_id), platform, session_id)


def list_session_states(root: Path) -> list[dict[str, Any]]:
    identities = set()
    with _read_connection(root) as connection:
        if connection is not None:
            identities.update(connection.execute("SELECT platform,session_id FROM sessions"))
    identities.update((p.parent.parent.name, p.parent.name) for p in Path(root).glob("*/*/intent-state.json"))
    return [read_session_state(root=root, platform=p, session_id=s) for p, s in sorted(identities)]


def export_session(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str, destination: Path) -> dict[str, Path]:
    """Explicit portable projection. Never export over the authority/import root."""
    destination = Path(destination).expanduser().resolve()
    if destination == Path(root).expanduser().resolve():
        raise ValueError("export destination must differ from authority root")
    with _read_connection(root) as connection:
        state = read_session_state(root=root, platform=platform, session_id=session_id, transaction=connection)
        events = read_session_events(root=root, platform=platform, session_id=session_id, transaction=connection)
    paths = session_paths(destination, platform, session_id)
    write_json(paths["state"], state)
    _atomic_write(paths["events"], "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in events))
    return paths


def _semantic_projection(state: dict[str, Any]) -> dict[str, Any]:
    """History of approved semantic fields, never the arbitrary input delta.

    Nested objects need an allowlist too: decisions and scope permit accessory
    fields in current state, but those are not copied into historical events.
    Callers remain responsible for supplying compressed summaries rather than
    embedding raw content in the summary itself; code cannot infer provenance.
    """
    def objects(value, keys):
        if not isinstance(value, list):
            return []
        return [({key: deepcopy(item[key]) for key in keys if key in item
                  and isinstance(item[key], (str, bool, int))} if isinstance(item, dict) else item)
                for item in value if isinstance(item, (dict, str))]
    projection = {key: state.get(key, "") for key in TEXT_FIELDS}
    projection["routing_keywords"] = list(state.get("routing_keywords", []))
    for key in ("constraints", "non_goals", "open_questions"):
        projection[key] = objects(state.get(key), ("id", "summary", "source", "status"))
    projection["acceptance_criteria"] = objects(state.get("acceptance_criteria"),
        ("id", "summary", "source", "admitted", "status", "met_completion_check_digest", "work_unit_id", "contract_revision"))
    projection["decisions"] = objects(state.get("decisions"),
        ("id", "summary", "source", "superseded", "superseded_by"))
    projection["conduct_feedback"] = objects(state.get("conduct_feedback"),
        ("id", "summary", "failure_pattern", "pattern_class", "corrective_rule", "source", "status", "occurrence_count"))
    scope = state.get("latest_scope")
    projection["latest_scope"] = {}
    if isinstance(scope, dict):
        for key in ("allowed", "prohibited", "non_goals", "constraints", "stop_conditions"):
            if isinstance(scope.get(key), list):
                projection["latest_scope"][key] = objects(scope[key], ("id", "summary"))
            elif isinstance(scope.get(key), str):
                projection["latest_scope"][key] = scope[key]
    return projection


def _semantic_changes(before: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    after = _semantic_projection(state)
    return {key: {"before_digest": json_digest(before[key]), "after_digest": json_digest(value)}
            for key, value in after.items() if before[key] != value}


def merge_unique(existing: Any, incoming: Any) -> list[Any]:
    """Merge list-field entries without destroying their shape.

    Plain strings are kept as strings (deduped by value). Structured entries
    (dicts, e.g. {"id", "summary"}) are kept as objects and deduped by their
    "id" when present, else by a normalized JSON form. This only preserves the
    shape supplied by the caller; it does not impose or validate a schema.
    First occurrence wins.
    """
    values: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for source in (existing, incoming):
        if source is None:
            continue
        if isinstance(source, (str, dict)):
            iterable: list[Any] = [source]
        elif isinstance(source, list):
            iterable = source
        else:
            iterable = [source]
        for value in iterable:
            if isinstance(value, dict):
                entry_id = value.get("id")
                if isinstance(entry_id, str) and entry_id.strip():
                    key = ("id", entry_id.strip())
                else:
                    key = ("obj", json.dumps(value, sort_keys=True, ensure_ascii=False))
            else:
                text = str(value).strip()
                if not text:
                    continue
                key = ("str", text)
                value = text
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
    return values


def merge_consumer_hints(existing: Any, incoming: Any) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    if isinstance(existing, dict):
        for key, value in existing.items():
            merged[str(key)] = merge_unique([], value)
    if isinstance(incoming, dict):
        for key, value in incoming.items():
            merged[str(key)] = merge_unique(merged.get(str(key), []), value)
    return merged


def normalize_decision(raw: Any, timestamp: str) -> dict[str, Any] | None:
    if isinstance(raw, str):
        decision_id = safe_component(raw.lower(), "decision")
        summary = raw.strip()
        payload: dict[str, Any] = {"id": decision_id, "summary": summary}
    elif isinstance(raw, dict):
        payload = dict(raw)
        if not payload.get("id"):
            payload["id"] = safe_component(str(payload.get("summary") or "decision").lower(), "decision")
    else:
        return None
    payload["id"] = safe_component(str(payload["id"]), "decision")
    payload.setdefault("summary", "")
    payload.setdefault("created_at", timestamp)
    payload["updated_at"] = timestamp
    payload.setdefault("superseded", False)
    return payload


def canonical_source(value: Any, default: str) -> str:
    """Return the current name of a source label; a legacy label maps to its current name."""
    source = str(value or default).strip()
    return LEGACY_SOURCE_ALIASES.get(source, source)


def normalize_acceptance_criterion(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        summary = raw.strip()
        if not summary:
            return None
        return {
            "id": safe_component(summary.lower(), "criterion"),
            "summary": summary,
            "source": "interpreted-as",
            "status": "unmet",
            "admitted": False,
        }
    if not isinstance(raw, dict):
        return None
    summary = str(raw.get("summary") or raw.get("text") or "").strip()
    criterion_id = safe_component(str(raw.get("id") or summary.lower()), "criterion")
    if not summary:
        return None
    source = str(raw.get("source") or "interpreted-as").strip()
    if canonical_source(source, "interpreted-as") not in ACCEPTANCE_CRITERIA_SOURCES:
        source = "interpreted-as"
    status = str(raw.get("status") or "").strip().lower()
    if status not in ACCEPTANCE_CRITERIA_STATUSES:
        status = "unmet"
    admitted = raw.get("admitted")
    if not isinstance(admitted, bool):
        admitted = canonical_source(source, "interpreted-as") in ACCEPTANCE_CRITERIA_ADMITTED_SOURCES
    criterion = {
        "id": criterion_id,
        "summary": summary,
        "source": source,
        "status": status,
        "admitted": admitted,
    }
    if "work_unit_id" in raw or "contract_revision" in raw:
        unit = raw.get("work_unit_id")
        revision = raw.get("contract_revision")
        if (not isinstance(unit, str) or not unit or safe_component(unit, "") != unit
                or len(unit) > 120 or type(revision) is not int or not 1 <= revision <= MAX_SHARED_INTEGER):
            raise ValueError("criterion ownership requires an exact work_unit_id and positive integer contract_revision")
        criterion.update(work_unit_id=unit, contract_revision=revision)
    return criterion


def acceptance_criterion_key(criterion: dict[str, Any]) -> tuple[Any, Any, str]:
    """An ownerless ID never aliases an explicitly owned local ID."""
    return criterion.get("work_unit_id"), criterion.get("contract_revision"), criterion["id"]


def merge_acceptance_criteria(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    merged: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []

    def store(criterion_id: tuple[Any, Any, str], criterion: dict[str, Any]) -> None:
        if criterion_id not in merged:
            order.append(criterion_id)
        merged[criterion_id] = criterion

    # Existing persisted criteria are trusted, including a "met" status set by mark_acceptance_criterion_met (the sole legitimate "met" writer).
    existing_items = existing if isinstance(existing, list) else [existing]
    for item in existing_items:
        criterion = normalize_acceptance_criterion(item)
        if criterion is None:
            continue
        if isinstance(item, dict):
            stored_status = str(item.get("status") or "").strip().lower()
            if stored_status in ACCEPTANCE_CRITERIA_STATUSES:
                criterion["status"] = stored_status
            digest = item.get("met_completion_check_digest")
            if isinstance(digest, str) and digest:
                criterion["met_completion_check_digest"] = digest
            met_at = item.get("met_at")
            if isinstance(met_at, str) and met_at:
                criterion["met_at"] = met_at
        store(acceptance_criterion_key(criterion), criterion)

    # Incoming raw deltas may not assert "met": the status only transitions through mark_acceptance_criterion_met. Admission may still be promoted.
    incoming_items = incoming if isinstance(incoming, list) else [incoming]
    for item in incoming_items:
        criterion = normalize_acceptance_criterion(item)
        if criterion is None:
            continue
        criterion_id = acceptance_criterion_key(criterion)
        prior = merged.get(criterion_id)
        if prior is not None:
            incoming_admitted = item.get("admitted") if isinstance(item, dict) else None
            if not isinstance(incoming_admitted, bool):
                # No explicit boolean admission: admission is sticky and can be upgraded by a newly contract-bound source, but a present-but-invalid field (e.g. null) must never silently drop it.
                criterion["admitted"] = bool(prior["admitted"]) or bool(criterion["admitted"])
            # An ID identifies the requirement, not a perpetual completion proof.
            # Code cannot certify that changed wording has unchanged meaning.
            # Reopen only the revised/withdrawn condition; historical proof stays
            # in the append-only events, and unchanged criteria retain evidence.
            same_condition = criterion["summary"] == prior["summary"]
            if same_condition and criterion["admitted"]:
                criterion["status"] = prior["status"]
                if "met_completion_check_digest" in prior:
                    criterion["met_completion_check_digest"] = prior["met_completion_check_digest"]
                if "met_at" in prior:
                    criterion["met_at"] = prior["met_at"]
            else:
                criterion["status"] = "unmet"
        else:
            criterion["status"] = "unmet"
        store(criterion_id, criterion)

    return [merged[criterion_id] for criterion_id in order]


# Import/audit shape only; current consumers do not derive permission.
def normalize_security_decision(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    decision = str(raw.get("decision", "")).strip().lower()
    if decision not in SECURITY_DECISIONS:
        return None
    flags: list[str] = []
    raw_flags = raw.get("risk_flags")
    if isinstance(raw_flags, list):
        for value in raw_flags:
            text = safe_component(str(value).lower(), "")
            if text and text not in flags:
                flags.append(text)
            if len(flags) >= SECURITY_RISK_FLAG_MAX:
                break
    decision_out: dict[str, Any] = {
        "decision": decision,
        "risk_flags": flags,
        "reason": str(raw.get("reason", "")).strip()[:SECURITY_REASON_MAX],
    }
    for key in ("input_event_id", "input_digest", "recorded_at"):
        value = str(raw.get(key, "")).strip()
        if value:
            decision_out[key] = value
    return decision_out


def normalize_conduct_feedback(raw: Any, timestamp: str) -> dict[str, Any] | None:
    if isinstance(raw, str):
        rule = raw.strip()
        if not rule:
            return None
        payload: dict[str, Any] = {
            "id": rule.lower(),
            "summary": rule,
            "corrective_rule": rule,
        }
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        return None
    summary = str(payload.get("summary") or "").strip()
    rule = str(payload.get("corrective_rule") or summary).strip()
    pattern = str(payload.get("failure_pattern") or "").strip()
    entry_id = str(payload.get("id") or rule or pattern or summary).strip()
    if not entry_id:
        return None
    source = str(payload.get("source") or "user-said").strip()
    pattern_class = str(payload.get("pattern_class") or "").strip()
    invalid = []
    if canonical_source(source, "user-said") not in CONDUCT_FEEDBACK_SOURCES:
        invalid.append(f"source {source!r} is not one of {sorted(CONDUCT_FEEDBACK_SOURCES)}")
    if pattern_class and pattern_class not in CONDUCT_PATTERN_CLASSES:
        invalid.append(f"pattern_class {pattern_class!r} is not one of {list(CONDUCT_PATTERN_CLASSES)}")
    if invalid:
        raise ValueError(f"conduct_feedback {entry_id!r}: " + "; ".join(invalid))
    status = str(payload.get("status") or "open").strip()
    if status not in CONDUCT_FEEDBACK_STATUS:
        status = "open"
    return {
        "id": safe_component(entry_id, "conduct"),
        "summary": summary or rule or pattern,
        "failure_pattern": pattern,
        "pattern_class": pattern_class,
        "corrective_rule": rule,
        "source": source,
        "status": status,
        "occurrence_count": positive_int(payload.get("occurrence_count"), 1),
        "updated_at": timestamp,
    }


def positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return parsed


def conduct_feedback_marks_occurrence(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return True
    if "occurrence_count" in raw:
        return True
    for field in ("summary", "failure_pattern", "corrective_rule"):
        if str(raw.get(field) or "").strip():
            return True
    return False


def merge_conduct_feedback(existing: Any, incoming: Any, timestamp: str) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in existing if isinstance(existing, list) else []:
        if isinstance(item, dict) and item.get("id"):
            key = safe_component(str(item["id"]), "conduct")
            by_id[key] = dict(item)
            by_id[key]["occurrence_count"] = positive_int(by_id[key].get("occurrence_count"), 1)
            if key not in order:
                order.append(key)
    for raw in incoming if isinstance(incoming, list) else [incoming]:
        normalized = normalize_conduct_feedback(raw, timestamp)
        if normalized is None:
            continue
        key = normalized["id"]
        if key in by_id:
            current = by_id[key]
            if normalized["summary"]:
                current["summary"] = normalized["summary"]
            if normalized["failure_pattern"]:
                current["failure_pattern"] = normalized["failure_pattern"]
            if normalized["pattern_class"]:
                current["pattern_class"] = normalized["pattern_class"]
            if normalized["corrective_rule"]:
                current["corrective_rule"] = normalized["corrective_rule"]
            if isinstance(raw, dict) and "source" in raw:
                current["source"] = normalized["source"]
            if isinstance(raw, dict) and "status" in raw:
                current["status"] = normalized["status"]
            if conduct_feedback_marks_occurrence(raw):
                current["occurrence_count"] = positive_int(
                    current.get("occurrence_count"), 1
                ) + positive_int(normalized.get("occurrence_count"), 1)
            current["updated_at"] = timestamp
        else:
            normalized["created_at"] = timestamp
            by_id[key] = normalized
            order.append(key)
    return [by_id[key] for key in order]


def apply_delta(state: dict[str, Any], delta: dict[str, Any] | None) -> dict[str, Any]:
    if not delta:
        state["updated_at"] = utc_now()
        return state

    now = utc_now()
    for field in TEXT_FIELDS:
        value = delta.get(field)
        if isinstance(value, str) and value.strip():
            state[field] = value.strip()

    for field in LIST_FIELDS:
        if field in delta:
            if not isinstance(delta[field], list):
                raise ValueError(f"{field} must be a complete current list")
            state[field] = merge_unique([], delta[field])
    if "routing_keywords" in delta:
        words = delta["routing_keywords"]
        if not isinstance(words, list) or any(not isinstance(w, str) or not w.strip() for w in words):
            raise ValueError("routing_keywords must contain nonempty strings")
        state["routing_keywords"] = list(dict.fromkeys(w.strip() for w in words))

    if "acceptance_criteria" in delta:
        state["acceptance_criteria"] = merge_acceptance_criteria(
            state.get("acceptance_criteria", []),
            delta.get("acceptance_criteria"),
        )

    if "consumer_hints" in delta:
        state["consumer_hints"] = merge_consumer_hints({}, delta.get("consumer_hints"))

    if "conduct_feedback" in delta:
        state["conduct_feedback"] = merge_conduct_feedback(
            state.get("conduct_feedback", []), delta.get("conduct_feedback"), now
        )
    state["conduct_feedback"] = [item for item in state.get("conduct_feedback", [])
                                 if item.get("status") != "encoded"]

    if "model_security_decision" in delta:
        normalized = normalize_security_decision(delta.get("model_security_decision"))
        if normalized is not None:
            normalized.setdefault("recorded_at", now)
            state["model_security_decision"] = normalized

    if isinstance(delta.get("latest_scope"), dict):
        state["latest_scope"] = delta["latest_scope"]

    existing_decisions: list[dict[str, Any]] = [
        item for item in state.get("decisions", []) if isinstance(item, dict)
    ]
    decision_by_id = {str(item.get("id")): item for item in existing_decisions if item.get("id")}
    incoming_decisions = [
        normalized for item in delta.get("decisions", [])
        if (normalized := normalize_decision(item, now)) is not None
    ] if isinstance(delta.get("decisions"), list) else []

    replacement_id = incoming_decisions[0]["id"] if incoming_decisions else ""
    supersedes = delta.get("supersedes", [])
    if isinstance(supersedes, str):
        supersedes = [supersedes]
    if isinstance(supersedes, list):
        for old_id in supersedes:
            old_key = str(old_id)
            if old_key in decision_by_id:
                decision_by_id[old_key]["superseded"] = True
                decision_by_id[old_key]["superseded_at"] = now
                if replacement_id:
                    decision_by_id[old_key]["superseded_by"] = replacement_id

    for decision in incoming_decisions:
        current = decision_by_id.get(decision["id"], {})
        current.update(decision)
        current.setdefault("superseded", False)
        decision_by_id[decision["id"]] = current

    if decision_by_id:
        state["decisions"] = list(decision_by_id.values())

    state["updated_at"] = now
    return state


def _commit_turn(connection, state, observation, *, has_input, intent_delta, source, native_human=False):
    """Single owner of semantic updates and canonical input promotion."""
    platform, session_id = state['platform'], state['session_id']
    if intent_delta is not None and not isinstance(intent_delta, dict):
        raise ValueError("intent_delta must be an object")
    if has_input:
        if state.get('current_context_input_event_id') and not native_human:
            raise ValueError('Only positively reconciled native human input clears SDK context authority')
        if native_human:
            state['current_context_input_event_id'] = ''
        route = state.get("response_route")
        if isinstance(route, dict) and route.get("input_event_id") != observation["input_event_id"]:
            # Reopen routing/authorization binding, never the business proof.
            route["status"] = "stale"
        state["latest_input_event_id"] = observation["input_event_id"]
        state["canonical_input_event_id"] = observation["input_event_id"]
        state["latest_input_digest"] = observation["input_digest"]
        state["latest_input_char_count"] = observation["input_char_count"]
    decision = (intent_delta or {}).get("model_security_decision")
    if isinstance(decision, dict) and state["latest_input_event_id"]:
        if decision.get("input_event_id") != state["latest_input_event_id"]:
            raise ValueError("stale or missing security decision input event; re-evaluate the current input")
        if decision.get("input_digest") not in (None, "", state["latest_input_digest"]):
            raise ValueError("security decision input digest mismatch")
    before = _semantic_projection(state)
    feedback_ids = {item["id"] for item in state.get("conduct_feedback", []) if isinstance(item, dict) and item.get("id")}
    state = apply_delta(state, intent_delta)
    retired_feedback = sorted(feedback_ids - {item["id"] for item in state.get("conduct_feedback", []) if item.get("id")})
    if "routing_keywords" in (intent_delta or {}):
        state["keywords_input_event_id"] = state.get("latest_input_event_id", "")
    state["intake_status"] = "pending-native-origin" if _pending_native_inputs(state) else "observed"
    state["last_intake_source"] = source
    state["last_semantic_delta_status"] = "recorded" if intent_delta else "not-provided"
    state["semantic_delta_policy"] = "agent-updates-when-intent-materially-changes"
    event: dict[str, Any] = {
        "ts": observation["observed_at"],
        "event": "user-input-observed" if has_input else "intent-updated",
        "platform": platform,
        "session_id": session_id,
        "source": source,
        "intent_delta_status": "recorded" if intent_delta else "not-provided",
        "input_event_id": state["latest_input_event_id"],
        "semantic_changes": _semantic_changes(before, state),
    }
    if retired_feedback:
        event["retired_feedback_ids"] = retired_feedback
    if has_input:
        event["event_id"] = observation["input_event_id"]
        event["input_digest"] = observation["input_digest"]
        event["input_char_count"] = observation["input_char_count"]
    if intent_delta:
        event["intent_delta_digest"] = json_digest(intent_delta)
        event["delta_keys"] = sorted(str(key) for key in intent_delta.keys())
    _commit(connection, state, event)
    if has_input:
        _persist_canonical_intake(connection, state, native=True)
    return state


def record_turn(
    *,
    root: Path = DEFAULT_ROOT,
    platform: str,
    session_id: str,
    raw_user_input: str | None = None,
    intent_delta: dict[str, Any] | None = None,
    source: str = "agent",
    observation: dict[str, Any] | None = None,
    expected_input_event_id: str | None = None,
    expected_revision: int | None = None,
    transaction: sqlite3.Connection | None = None,
) -> dict[str, Path]:
    validate_identity(platform, session_id)
    if observation is None:
        observation = build_input_observation(
            platform=platform,
            session_id=session_id,
            raw_user_input=raw_user_input,
        )
    if not isinstance(observation, dict) or not isinstance(observation.get("observed_at"), str) or not observation["observed_at"]:
        raise ValueError("input observation requires an observation timestamp")
    if raw_user_input is not None:
        if (not isinstance(observation.get("input_event_id"), str) or not observation["input_event_id"]
                or observation.get("input_digest") != input_digest(raw_user_input)
                or type(observation.get("input_char_count")) is not int
                or not 0 <= observation["input_char_count"] <= MAX_SHARED_INTEGER
                or observation["input_char_count"] != len(raw_user_input)):
            raise ValueError("input observation does not match this intake")
    paths = session_paths(root, platform, session_id)
    with _transaction(root, transaction) as connection:
        state = _load_state_for_update(connection, paths, platform, session_id)
        _check_expected(state, expected_input_event_id, expected_revision,
                        new_input=raw_user_input is not None and not intent_delta)
        _commit_turn(connection, state, observation, has_input=raw_user_input is not None,
                     intent_delta=intent_delta, source=source)
    return paths


def _shared_module(name):
    """Load the sibling Core helper; installation preserves this shared layout."""
    path = Path(__file__).resolve().parents[2] / "_shared" / (name + ".py")
    retained = sys.modules.get(name)
    if retained is not None and Path(retained.__file__).resolve() == path:
        return retained
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Core {name} helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _response_route_module():
    return _shared_module('response_route')


def _pending_native_inputs(state):
    pending = state.get('pending_native_inputs', [])
    if not isinstance(pending, list):
        raise ValueError('pending native input state is invalid')
    for observation in pending:
        if (not isinstance(observation, dict)
                or observation.get('platform') != state['platform']
                or observation.get('session_id') != state['session_id']
                or not isinstance(observation.get('transport_observation_id'), str)
                or not re.fullmatch(r'[0-9a-f]{32}', observation['transport_observation_id'])
                or not isinstance(observation.get('input_digest'), str)
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', observation['input_digest'])
                or type(observation.get('input_char_count')) is not int
                or not 0 <= observation['input_char_count'] <= MAX_SHARED_INTEGER):
            raise ValueError('pending native input identity is invalid')
    return pending


def stage_native_input(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
                       raw_input: str, prompt_id: str | None, transcript_path: str | None,
                       transaction: sqlite3.Connection | None = None) -> dict[str, Path]:
    """Record transport reception without assigning user authority or a route."""
    validate_identity(platform, session_id)
    if platform != 'claude' or session_id == 'unknown' or not isinstance(raw_input, str):
        raise ValueError('native transport requires an exact Claude session and input')
    native = _shared_module('native_transcript')
    observation = dict(transport_observation_id=uuid.uuid4().hex, platform=platform, session_id=session_id,
        observed_at=utc_now(), input_digest=input_digest(raw_input), input_char_count=len(raw_input),
        native_prompt_id=prompt_id if isinstance(prompt_id, str) and prompt_id else None,
        transcript_boundary=native.capture_append_boundary(transcript_path))
    paths = session_paths(root, platform, session_id)
    with _transaction(root, transaction) as connection:
        state = _load_state_for_update(connection, paths, platform, session_id)
        pending = _pending_native_inputs(state)
        state['pending_native_inputs'] = [*pending, observation]
        state['intake_status'] = 'pending-native-origin'
        _commit(connection, state, dict(ts=observation['observed_at'], event='native-input-staged',
            platform=platform, session_id=session_id, source='hook', **{
                key: observation[key] for key in ('transport_observation_id', 'input_digest', 'input_char_count')}))
    return paths


NOTIFIED_CAUSE_LIMIT = 32
# Non-human context actors from the native reader. Only 'sdk' rows also become
# a frozen content original for context selection.
CONTEXT_ACTORS = ('sdk', 'context')


def _native_input_cursors(state):
    cursors = state.get('native_input_cursors', {})
    if (not isinstance(cursors, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0
                                             for k, v in cursors.items())):
        raise ValueError('native transcript cursors are invalid')
    return cursors


def _native_input_waiting(state):
    waiting = state.get('native_input_waiting', {})
    return waiting if isinstance(waiting, dict) else {}


def _wait_cause(located):
    """Failure key from the reader's common values: the waiting reason, and an unknown actor's marker shape."""
    return 'unknown-actor:' + located['shape'] if located['status'] == 'located' else located['status']


def _later_located_human(native, queued, session_id, cursors, cache):
    """First later staged input whose own native row is located and human; nothing is written."""
    probe = dict(cursors)
    for observation in queued:
        located = native.locate_staged_input(observation, session_id, probe, cache=cache)
        if located['status'] != 'located':
            continue
        if located['actor'] == 'human':
            return observation
        probe[located['cursor_key']] = located['end_offset']
    return None


def _leave_native_input(state, observation, located):
    """Remove the head input; a located row also advances its transcript cursor."""
    state['pending_native_inputs'] = _pending_native_inputs(state)[1:]
    if located['status'] == 'located':
        state['native_input_cursors'] = dict(_native_input_cursors(state),
                                             **{located['cursor_key']: located['end_offset']})
    if _native_input_waiting(state).get('transport_observation_id') == observation['transport_observation_id']:
        state['native_input_waiting'] = {}


def _record_native_wait(connection, state, observation, cause):
    """One failure record per blocking input and cause; the hook wrapper owns any notice."""
    waiting = _native_input_waiting(state)
    if (waiting.get('transport_observation_id'), waiting.get('cause')) == (observation['transport_observation_id'], cause):
        return
    state['native_input_waiting'] = dict(transport_observation_id=observation['transport_observation_id'],
                                         cause=cause, since=utc_now())
    state['intake_status'] = 'pending-native-origin'
    _commit(connection, state, dict(ts=state['native_input_waiting']['since'], event='native-input-waiting',
        platform=state['platform'], session_id=state['session_id'],
        transport_observation_id=observation['transport_observation_id'], cause=cause,
        pending_count=len(_pending_native_inputs(state)), input_event_id=state['latest_input_event_id']))


def reconcile_native_inputs(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
                            transaction: sqlite3.Connection | None = None,
                            record_wait: bool = False) -> dict[str, Any]:
    """Resolve committed native origin in queue order, before native consumers run.

    Public reads remain pure. Each located row resolves by its actor. An unknown
    or unlocated input never blocks a later located human input, which
    supersedes it without promoting it. Without a later human input the queue
    waits and dependent writes stay refused. Notification resolution preserves
    the exact preceding human route. Each transcript is read once per call.

    The lifecycle hook wrapper passes record_wait: PreToolUse and Stop run after
    the host appends the turn's input, so a wait there is recorded once per
    blocking input and cause. A generation-boundary call can precede the append.
    """
    validate_identity(platform, session_id)
    current = read_session_state(root=root, platform=platform, session_id=session_id, transaction=transaction)
    if platform != 'claude' or not current or not _pending_native_inputs(current):
        return current
    native = _shared_module('native_transcript')
    paths = session_paths(root, platform, session_id)
    with _transaction(root, transaction) as connection:
        state = _load_state_for_update(connection, paths, platform, session_id)
        cache = native.TranscriptCache()
        while pending := _pending_native_inputs(state):
            observation = pending[0]
            cursors = _native_input_cursors(state)
            located = native.locate_staged_input(observation, session_id, cursors, cache=cache)
            actor = located['actor'] if located['status'] == 'located' else None
            if actor is None:
                probe = dict(cursors)
                if located['status'] == 'located':
                    probe[located['cursor_key']] = located['end_offset']
                later = _later_located_human(native, pending[1:], session_id, probe, cache)
                cause = _wait_cause(located)
                if later is None:
                    if record_wait:
                        _record_native_wait(connection, state, observation, cause)
                    break
                _leave_native_input(state, observation, located)
                event = dict(ts=utc_now(), event='native-input-superseded', platform=platform,
                    session_id=session_id, transport_observation_id=observation['transport_observation_id'],
                    superseded_by_transport_observation_id=later['transport_observation_id'],
                    actor='unknown', cause=cause,
                    reason='Later positively bound human input controls current authority; earlier actor is not promoted.',
                    input_event_id=state['latest_input_event_id'])
                if located['status'] == 'located':
                    event['native_row'] = {key: located[key] for key in ('cursor_key', 'start_offset', 'end_offset')}
                _commit(connection, state, event)
                continue
            _leave_native_input(state, observation, located)
            if actor == 'human':
                promoted = dict(observed_at=observation['observed_at'],
                    input_event_id=json_digest(dict(event='user-input-observed', platform=platform,
                        session_id=session_id, transport_observation_id=observation['transport_observation_id'])),
                    input_digest=observation['input_digest'], input_char_count=observation['input_char_count'])
                state = _commit_turn(connection, state, promoted, has_input=True, intent_delta=None, source='hook', native_human=True)
            context_event = None
            if actor in CONTEXT_ACTORS:
                context_event = json_digest(dict(event='context-input-resolved', platform=platform,
                    session_id=session_id, transport_observation_id=observation['transport_observation_id']))
                state['current_context_input_event_id'] = context_event
                if isinstance(state.get('response_route'),dict):
                    state['response_route']['status'] = 'stale'
            state['intake_status'] = ('pending-native-origin' if state['pending_native_inputs'] else
                                      'context-only' if state.get('current_context_input_event_id') else
                                      'observed' if state['latest_input_event_id'] else 'pending')
            event = dict(ts=utc_now(), event='native-input-resolved', platform=platform,
                session_id=session_id, transport_observation_id=observation['transport_observation_id'],
                actor=actor, native_row={key: located[key] for key in
                    ('cursor_key', 'start_offset', 'end_offset')}, input_event_id=state['latest_input_event_id'])
            if context_event:
                event['context_input_event_id'] = context_event
            _commit(connection, state, event)
            if actor == 'sdk':
                staged = connection.execute('''SELECT sequence FROM intent_events WHERE platform=? AND session_id=?
                    AND json_extract(event_json,'$.event')='native-input-staged'
                    AND json_extract(event_json,'$.transport_observation_id')=?''',
                    (platform,session_id,observation['transport_observation_id'])).fetchall()
                if len(staged) != 1:
                    raise ValueError('Context intake requires one original staged transport')
                sequence = connection.execute('SELECT MAX(sequence) FROM intent_events WHERE platform=? AND session_id=?',
                    (platform,session_id)).fetchone()[0]
                payload = dict(schema_version='context-input.v1', intent_root=str(Path(root).resolve()),
                    platform=platform,session_id=session_id,input_event_id=context_event,actor='sdk',
                    observation=observation,native_row=event['native_row'],native_source=located['native_source'],
                    native_file_identity=located['native_file_identity'])
                connection.execute('INSERT INTO core_context_intakes VALUES (?,?,?,?,?,?)',
                    (platform,session_id,context_event,json.dumps(payload,ensure_ascii=False,sort_keys=True),staged[0][0],sequence))
        return state


def _due_native_notice(state):
    """(wait, pending, notified causes) when the current wait's cause was not yet shown in this session."""
    if state is None:
        return None
    waiting, pending = _native_input_waiting(state), _pending_native_inputs(state)
    if not waiting or not pending or pending[0]['transport_observation_id'] != waiting.get('transport_observation_id'):
        return None
    notified = state.get('native_input_notified_causes', [])
    notified = [cause for cause in notified if isinstance(cause, str)] if isinstance(notified, list) else []
    return None if waiting.get('cause') in notified else (waiting, pending, notified)


def claim_native_input_notice(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
                              transaction: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """Claim the current wait's notice once per session and cause.

    Only a caller that displays the notice claims it; reconcile_native_inputs
    records every wait whoever calls it. A read settles the usual case, no wait
    or a cause already shown, without the write lock; the write transaction
    checks again because another hook may have claimed it in between. The
    result holds host-neutral values.
    """
    validate_identity(platform, session_id)
    if transaction is None and not storage_is_initialized(root):
        return None
    with _read_connection(root, transaction) as connection:
        if connection is None or _due_native_notice(_stored_state(connection, platform, session_id)) is None:
            return None
    with _transaction(root, transaction) as connection:
        state = _stored_state(connection, platform, session_id)
        due = _due_native_notice(state)
        if due is None:
            return None
        waiting, pending, notified = due
        state['native_input_notified_causes'] = [*notified, waiting['cause']][-NOTIFIED_CAUSE_LIMIT:]
        _commit(connection, state, dict(ts=utc_now(), event='native-input-notice', platform=platform,
            session_id=session_id, transport_observation_id=waiting['transport_observation_id'],
            cause=waiting['cause'], input_event_id=state['latest_input_event_id']))
        return dict(cause=waiting['cause'], since=waiting.get('since', ''), pending_count=len(pending))


def record_native_intake_failure(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
                                 error: BaseException, transaction: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Record one failed native intake step for this session; the hook that caught it continues.

    The session keeps one failure entry that counts every occurrence with its
    first and last times. A native-intake-failed event marks the session's
    first failure and each change of error type; a repeat of the same type
    updates the entry without an event or a revision step.
    """
    validate_identity(platform, session_id)
    now = utc_now()
    name = type(error).__name__
    detail = str(error)[:200].encode('ascii', 'backslashreplace').decode('ascii')
    paths = session_paths(root, platform, session_id)
    with _transaction(root, transaction) as connection:
        state = _load_state_for_update(connection, paths, platform, session_id)
        previous = state.get('native_intake_failure')
        previous = previous if isinstance(previous, dict) else {}
        count = previous['count'] if type(previous.get('count')) is int and previous['count'] > 0 else 0
        first = previous.get('first_at') if count and isinstance(previous.get('first_at'), str) else now
        state['native_intake_failure'] = dict(error=name, detail=detail, count=count + 1, first_at=first, last_at=now)
        if count and previous.get('error') == name:
            # A persisting cause fails every PreToolUse and Stop; the count carries that, an event per repeat would not.
            _save_state(connection, state)
        else:
            _commit(connection, state, dict(ts=now, event='native-intake-failed', platform=platform,
                session_id=session_id, error=name, detail=detail, input_event_id=state['latest_input_event_id']))
        return state['native_intake_failure']


def _session_states(root: Path, platform: str | None):
    """(platform, session_id, state) of every stored session, read-only."""
    with _read_connection(root) as connection:
        if connection is None:
            return []
        query = 'SELECT platform, session_id, state_json FROM sessions'
        rows = (connection.execute(query + ' WHERE platform=?', (platform,)) if platform
                else connection.execute(query)).fetchall()
    return [(owner, session, state) for owner, session, raw in rows
            if isinstance(state := json.loads(raw), dict)]


def list_native_input_waits(root: Path, platform: str | None = None) -> list[dict[str, Any]]:
    """Sessions whose native inputs wait for a human input, read from their failure records."""
    waits = []
    for owner, session, state in _session_states(root, platform):
        waiting, pending = state.get('native_input_waiting'), state.get('pending_native_inputs')
        if isinstance(waiting, dict) and isinstance(waiting.get('cause'), str) and isinstance(pending, list) and pending:
            waits.append(dict(platform=owner, session_id=session, cause=waiting['cause'],
                              since=waiting.get('since', ''), pending_count=len(pending)))
    return sorted(waits, key=lambda wait: (str(wait['since']), wait['session_id']))


def list_native_intake_failures(root: Path, platform: str | None = None) -> list[dict[str, Any]]:
    """Sessions with a recorded native intake failure, the latest first; a later success keeps the record."""
    failures = []
    for owner, session, state in _session_states(root, platform):
        failure = state.get('native_intake_failure')
        if isinstance(failure, dict) and isinstance(failure.get('error'), str):
            failures.append(dict(platform=owner, session_id=session, error=failure['error'],
                                 detail=failure.get('detail', ''), count=failure.get('count', 1),
                                 first_at=failure.get('first_at', ''), last_at=failure.get('last_at', '')))
    return sorted(failures, key=lambda failure: (str(failure['last_at']), failure['session_id']), reverse=True)


def _context_input_binding(connection, root, state, event_id):
    """Authenticate a context-only original at its frozen row, never later matches."""
    def require(condition):
        if not condition:
            raise ValueError('Original SDK context intake is unavailable or unverified')
    require(connection is not None and connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_context_intakes'").fetchone())
    owner = (state['platform'],state['session_id'])
    row = connection.execute('''SELECT payload_json,staged_sequence,resolved_sequence FROM core_context_intakes
        WHERE platform=? AND session_id=? AND input_event_id=?''', (*owner,event_id)).fetchone()
    require(row is not None)
    payload = json.loads(row[0])
    require(isinstance(payload,dict) and set(payload) == {'schema_version','intent_root','platform','session_id',
        'input_event_id','actor','observation','native_row','native_source','native_file_identity'}
        and payload['schema_version']=='context-input.v1' and payload['intent_root']==str(Path(root).resolve())
        and (payload['platform'],payload['session_id'])==owner and payload['actor']=='sdk'
        and payload['input_event_id']==event_id and owner[0]=='claude')
    observation, locator, original, identity = (payload[key] for key in
        ('observation','native_row','native_source','native_file_identity'))
    require(isinstance(observation,dict) and observation.get('platform')==owner[0]
        and observation.get('session_id')==owner[1] and event_id==json_digest(dict(event='context-input-resolved',
            platform=owner[0],session_id=owner[1],transport_observation_id=observation.get('transport_observation_id'))))
    imported = _imported_event_count(connection,Path(root),state)
    require(imported is not None and row[1] > imported and row[2] > row[1])
    audits = []
    for sequence in row[1:]:
        raw = connection.execute('SELECT event_json FROM intent_events WHERE platform=? AND session_id=? AND sequence=?',
            (*owner,sequence)).fetchone()
        require(raw is not None); audits.append(json.loads(raw[0]))
    staged, resolved = audits
    require(staged.get('event')=='native-input-staged' and staged.get('ts')==observation.get('observed_at')
        and all(staged.get(key)==observation.get(key) for key in
            ('platform','session_id','transport_observation_id','input_digest','input_char_count'))
        and resolved.get('event')=='native-input-resolved' and resolved.get('actor')=='sdk'
        and resolved.get('context_input_event_id')==event_id and resolved.get('native_row')==locator
        and all(resolved.get(key)==observation.get(key) for key in ('platform','session_id','transport_observation_id')))
    require(isinstance(original,dict) and set(original)=={'storage','locator','digest','byte_length','appendable','source_id'}
        and original.get('storage')=='source-reference.v1' and original.get('appendable') is True
        and isinstance(locator,dict) and set(locator)=={'cursor_key','start_offset','end_offset'}
        and type(locator['start_offset']) is int and type(locator['end_offset']) is int
        and 0 <= locator['start_offset'] < locator['end_offset']==original['byte_length']
        and isinstance(identity,dict) and set(identity)=={'device','inode'})
    source_api = _shared_module('source_reference')
    source_api._validate(original)
    with Path(original['locator']).open('rb') as stream:
        actual = os.fstat(stream.fileno()); raw = stream.read(original['byte_length'])
    require((actual.st_dev,actual.st_ino)==(identity['device'],identity['inode'])
        and len(raw)==original['byte_length'] and 'sha256:'+hashlib.sha256(raw).hexdigest()==original['digest']
        and locator['cursor_key']==hashlib.sha256(json.dumps([original['locator'],actual.st_dev,actual.st_ino]).encode()).hexdigest())
    boundary = observation.get('transcript_boundary')
    require(isinstance(boundary,dict) and boundary.get('path')==original['locator']
        and type(boundary.get('offset')) is int and 0 <= boundary['offset'] <= locator['start_offset']
        and hashlib.sha256(raw[:boundary['offset']]).hexdigest()==boundary.get('prefix_digest')
        and (boundary.get('inode') is None or (boundary.get('device'),boundary['inode'])==(actual.st_dev,actual.st_ino)))
    native_line = raw[locator['start_offset']:locator['end_offset']]
    require([line for _, _, line in _shared_module('text_lines').committed_byte_lines(native_line)]==[native_line])
    entry = json.loads(native_line)
    native = _shared_module('native_transcript')
    require(isinstance(entry,dict) and entry.get('sessionId')==owner[1] and native.native_input_actor(entry)=='sdk'
        and entry.get('promptId') in (None,observation.get('native_prompt_id'))
        and native.matches_input_text(entry['message'].get('content'),observation))
    adapter = _shared_module('claude_context_adapter')
    message = adapter._normalize_message('user',entry['message']['content'])
    return dict(platform=owner[0],session_id=owner[1],input_event_id=event_id,
        input_digest=observation['input_digest'],input_char_count=observation['input_char_count'],context_only=True,
        native_transcript_path=original['locator'],native_input_record=_shared_module('text_lines').line_number(raw,locator['start_offset']),
        native_input_digest='sha256:'+hashlib.sha256(json.dumps(message,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def _imported_event_count(connection: sqlite3.Connection, root: Path, state: dict[str, Any]) -> int | None:
    """Recover only an attested import prefix, never current compatibility state."""
    coordinates = (state["platform"], state["session_id"])
    row = connection.execute("SELECT sources_json FROM legacy_migrations WHERE platform=? AND session_id=?",
                             coordinates).fetchone()
    if row is None:
        return 0
    sources = json.loads(row[0])
    if not isinstance(sources, dict) or not sources or not set(sources) <= {"state", "events", "security"}:
        return None
    paths = session_paths(root, *coordinates)
    try:
        if {key for key in ("state", "events", "security") if paths[key].exists()} != set(sources):
            return None
        if any(not paths[key].is_file() or hashlib.sha256(paths[key].read_bytes()).hexdigest() != digest
               for key, digest in sources.items()):
            return None
        imported = _legacy_events(paths, *coordinates)
        original = _load_legacy_state(paths, *coordinates)
        pending = original.get("pending_audit_event")
        if pending is not None:
            if (not isinstance(pending, dict) or pending.get("platform") != coordinates[0]
                    or pending.get("session_id") != coordinates[1] or not pending.get("mutation_id")
                    or pending.get("ledger_revision") != original["ledger_revision"]):
                return None
            matches = [e for e in imported if e.get("mutation_id") == pending["mutation_id"]]
            if matches and matches != [pending]:
                return None
            if not matches:
                imported.append(pending)
        prefix = [json.loads(row[0]) for row in connection.execute(
            "SELECT event_json FROM intent_events WHERE platform=? AND session_id=? ORDER BY sequence LIMIT ?",
            (*coordinates, len(imported)))]
        if prefix != imported or any(hashlib.sha256(paths[key].read_bytes()).hexdigest() != digest
                                     for key, digest in sources.items()):
            return None
        return len(imported)
    except (OSError, ValueError):
        return None


def _observation_matches(event, platform, session_id, event_id, digest, count) -> bool:
    return (isinstance(event, dict) and event.get("event") == "user-input-observed"
            and event.get("platform") == platform and event.get("session_id") == session_id
            and event.get("event_id") == event_id and event.get("input_event_id") == event_id
            and event.get("input_digest") == digest and type(event.get("input_char_count")) is int
            and event["input_char_count"] == count)


def _current_observation_sequence(connection: sqlite3.Connection, state: dict[str, Any]) -> int | None:
    matches = []
    for sequence, raw in connection.execute(
            "SELECT sequence,event_json FROM intent_events WHERE platform=? AND session_id=?",
            (state["platform"], state["session_id"])):
        event = json.loads(raw)
        if isinstance(event, dict) and event.get("event_id") == state["latest_input_event_id"]:
            if not _observation_matches(event, state["platform"], state["session_id"],
                    state["latest_input_event_id"], state["latest_input_digest"], state["latest_input_char_count"]):
                return None
            matches.append(sequence)
    return matches[0] if len(matches) == 1 else None


def _canonical_intake_record(connection: sqlite3.Connection, state: dict[str, Any]):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_intakes'").fetchone():
        return None
    return connection.execute("""SELECT input_event_id,input_digest,input_char_count,event_sequence
        FROM canonical_intakes WHERE platform=? AND session_id=?""",
        (state["platform"], state["session_id"])).fetchone()


def _trusted_current_intake_sequence(connection: sqlite3.Connection, state: dict[str, Any]) -> int | None:
    record = _canonical_intake_record(connection, state)
    if record is None or tuple(record[:3]) != (state["latest_input_event_id"], state["latest_input_digest"],
                                             state["latest_input_char_count"]):
        return None
    event = connection.execute("SELECT event_json FROM intent_events WHERE platform=? AND session_id=? AND sequence=?",
                               (state["platform"], state["session_id"], record[3])).fetchone()
    if event is None or not _observation_matches(json.loads(event[0]), state["platform"], state["session_id"], *record[:3]):
        return None
    return record[3]


def _canonical_input_bound(connection: sqlite3.Connection, root: Path, state: dict[str, Any]) -> bool:
    """Only a protected SQLite record or verified native audit origin binds intake."""
    if _pending_native_inputs(state) or state.get('current_context_input_event_id') or not state.get("latest_input_event_id"):
        return False
    record = _canonical_intake_record(connection, state)
    if record is not None and record[0] == state["latest_input_event_id"]:
        return _trusted_current_intake_sequence(connection, state) is not None
    sequence = _current_observation_sequence(connection, state)
    if sequence is None:
        return False
    if record is not None:
        prior = connection.execute("SELECT event_json FROM intent_events WHERE platform=? AND session_id=? AND sequence=?",
                                   (state["platform"], state["session_id"], record[3])).fetchone()
        return (prior is not None and sequence > record[3]
                and _observation_matches(json.loads(prior[0]), state["platform"], state["session_id"], *record[:3]))
    imported_count = _imported_event_count(connection, root, state)
    return imported_count is not None and sequence > imported_count


def _persist_canonical_intake(connection: sqlite3.Connection, state: dict[str, Any], *, native=False) -> None:
    # New intake still checks ID uniqueness across the audit; a trusted current
    # record already establishes that proof for repeated route consumers.
    sequence = None if native else _trusted_current_intake_sequence(connection, state)
    if sequence is None:
        sequence = _current_observation_sequence(connection, state)
    if sequence is None:
        raise ValueError("canonical intake must match exactly one authoritative observation")
    connection.execute("""INSERT INTO canonical_intakes VALUES(?,?,?,?,?,?)
        ON CONFLICT(platform,session_id) DO UPDATE SET input_event_id=excluded.input_event_id,
        input_digest=excluded.input_digest,input_char_count=excluded.input_char_count,
        event_sequence=excluded.event_sequence""", (state["platform"], state["session_id"],
        state["latest_input_event_id"], state["latest_input_digest"], state["latest_input_char_count"], sequence))


def bind_legacy_acceptance_criteria(
    *, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
    work_unit_id: str, contract_revision: int, expected_input_event_id: str,
    expected_revision: int, expected_criteria: list[dict[str, Any]],
    transaction: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Core binds existing admitted rows to their frozen unit and returns state.

    The caller supplies the current input, revision and complete row snapshots.
    Core preserves criterion meaning, admission, status and proof; binding does
    not accept evidence or establish a response route.
    """
    routes = _response_route_module()
    validate_identity(platform, session_id)
    routes._unit_key(dict(work_unit_id=work_unit_id, contract_revision=contract_revision))
    if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_SHARED_INTEGER:
        raise routes.RouteBindingError("ownership binding requires an exact integer ledger revision")
    if not isinstance(expected_criteria, list) or not expected_criteria:
        raise routes.RouteBindingError("ownership binding requires complete existing criterion snapshots")
    def exact(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    with _transaction(root, transaction) as connection:
        state = _stored_state(connection, platform, session_id)
        if state is None or not expected_input_event_id or not _canonical_input_bound(connection, root, state):
            raise routes.RouteBindingError("ownership binding requires current canonical SQLite intake provenance")
        _check_expected(state, expected_input_event_id, expected_revision)
        registry = state.get("work_units")
        if not isinstance(registry, list) or any(not isinstance(unit, dict) for unit in registry):
            raise routes.RouteBindingError("ownership binding requires an existing work unit registry")
        keys = [routes._unit_key(unit) for unit in registry]
        key = (work_unit_id, contract_revision)
        if keys.count(key) != 1 or contract_revision != max((r for u, r in keys if u == work_unit_id), default=0):
            raise routes.RouteBindingError("ownership binding requires a unique latest registered unit revision")
        unit = registry[keys.index(key)]
        if (unit.get("owner") != {"platform": platform, "session_id": session_id}
                or unit.get("kind") != "execution" or unit.get("admitted") is not True):
            raise routes.RouteBindingError("ownership binding requires the current admitted execution owner")
        definitions = unit.get("criteria")
        if not isinstance(definitions, list) or not definitions:
            raise routes.RouteBindingError("ownership binding requires frozen criterion definitions")
        for row in definitions:
            routes._object(row, {"id", "summary", "source"}, "frozen criterion definition")
            routes._identifier(row["id"], "criterion id")
        definition_ids = [row["id"] for row in definitions]
        if len(set(definition_ids)) != len(definition_ids):
            raise routes.RouteBindingError("frozen criterion definitions are ambiguous")
        rows = state.get("acceptance_criteria")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise routes.RouteBindingError("stored criterion rows are invalid")
        selected = []
        for snapshot in expected_criteria:
            if (not isinstance(snapshot, dict) or "work_unit_id" in snapshot or "contract_revision" in snapshot
                    or snapshot.get("admitted") is not True):
                raise routes.RouteBindingError("ownership binding requires admitted ownerless row snapshots")
            matches = [row for row in rows if row.get("id") == snapshot.get("id")
                       and "work_unit_id" not in row and "contract_revision" not in row]
            if len(matches) != 1 or exact(matches[0]) != exact(snapshot):
                raise routes.RouteBindingError("existing criterion snapshot changed or is ambiguous")
            if any(row.get("id") == snapshot.get("id") and
                   (row.get("work_unit_id"), row.get("contract_revision")) == key for row in rows):
                raise routes.RouteBindingError("qualified criterion already exists; ownership cannot be overwritten")
            routes._criterion(dict(matches[0], work_unit_id=work_unit_id, contract_revision=contract_revision))
            selected.append(matches[0])
        projected = [{k: row[k] for k in ("id", "summary", "source")} for row in selected]
        if exact(sorted(projected, key=lambda row: row["id"])) != exact(sorted(definitions, key=lambda row: row["id"])):
            raise routes.RouteBindingError("binding snapshots must match exactly the frozen criterion definitions")
        for row in selected:
            row.update(work_unit_id=work_unit_id, contract_revision=contract_revision)
        state["updated_at"] = utc_now()
        _commit(connection, state, dict(ts=state["updated_at"], event="acceptance-criteria-ownership-bound",
            platform=platform, session_id=session_id, input_event_id=expected_input_event_id,
            work_unit_id=work_unit_id, contract_revision=contract_revision,
            criterion_ids=sorted(definition_ids)))
        return deepcopy(state)


def record_response_route(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
                          expected_input_event_id: str, candidate: dict[str, Any],
                          boundary_ref: str | None,
                          transaction: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Official atomic projection writer. Core validates before committing."""
    routes = _response_route_module()
    try:
        validate_identity(platform, session_id)
    except ValueError as exc:
        raise routes.RouteBindingError(str(exc)) from exc
    with _transaction(root, transaction) as connection:
        state = _stored_state(connection, platform, session_id)
        if state is None or not expected_input_event_id or not _canonical_input_bound(connection, root, state):
            raise routes.RouteBindingError("ready route requires canonical SQLite intake provenance; restore missing import-boundary evidence, never fabricate a new input")
        try:
            _check_expected(state, expected_input_event_id, None)
        except ValueError as exc:
            raise routes.RouteBindingError(str(exc)) from exc
        state["canonical_input_event_id"] = state["latest_input_event_id"]
        _persist_canonical_intake(connection, state)
        projection, units = routes.validate_response_route(state, candidate, boundary_ref)
        projection = routes.authenticate_continuation_feedback(projection, root, connection)
        state["response_route"] = projection
        state["work_units"] = units
        state["updated_at"] = utc_now()
        _commit(connection, state, {
            "ts": state["updated_at"], "event": "response-route-recorded",
            "platform": platform, "session_id": session_id,
            "input_event_id": expected_input_event_id,
            "response_route_digest": json_digest(projection),
            "review_scope": {
                "route": deepcopy(projection),
                "canonical_criteria": [deepcopy(row) for row in state["acceptance_criteria"]
                    if any((row.get("work_unit_id"), row.get("contract_revision"), row["id"]) ==
                        (selected["work_unit_id"], selected["contract_revision"], selected["id"])
                        for selected in projection["selected_criteria"])],
                "relevant_context": {key: deepcopy(state.get(key, {} if key == "latest_scope" else []))
                    for key in ("constraints", "non_goals", "decisions", "latest_scope")},
                "input_binding": {"platform": platform, "session_id": session_id,
                    "input_event_id": expected_input_event_id,
                    "input_digest": state["latest_input_digest"],
                    "input_char_count": state["latest_input_char_count"]},
            },
            "selected_work_unit_ids": projection["selected_work_unit_ids"],
            "selected_criteria": [{k: c[k] for k in ("work_unit_id", "contract_revision", "criterion_id")}
                                  for c in projection["selected_criteria"]],
        })
        return deepcopy(projection)


def read_response_route(*, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
                        expected_input_event_id: str | None = None,
                        transaction: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """Read route and input anchor from one authoritative snapshot."""
    routes = _response_route_module()
    try:
        validate_identity(platform, session_id)
    except ValueError as exc:
        raise routes.RouteBindingError(str(exc)) from exc
    with _read_connection(root, transaction) as connection:
        state = _stored_state(connection, platform, session_id)
        if state is not None:
            state["canonical_input_event_id"] = (state["latest_input_event_id"]
                if _canonical_input_bound(connection, root, state) else "")
        if state is None:
            return None
        projection = routes.project_stored_response_route(state, expected_input_event_id)
        return None if projection is None else routes.refresh_continuation_feedback(projection, root, connection)


def mark_acceptance_criterion_met(
    *,
    root: Path = DEFAULT_ROOT,
    platform: str,
    session_id: str,
    criterion_id: str,
    completion_check_digest: str,
    expected_input_event_id: str | None = None,
    expected_criterion: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    transaction: sqlite3.Connection | None = None,
) -> dict[str, Path]:
    """Flip an acceptance criterion to "met". The sole writer of "met".

    The caller supplies the accepted full or partial proof digest and exact
    input/criterion snapshot. This writer checks binding and digest syntax;
    it does not review source meaning or infer fulfillment from work execution.
    A caller applies only the criteria supported by Core's assessment.
    Raw semantic deltas cannot assert "met".
    """
    digest = str(completion_check_digest or "").strip().lower()
    if not COMPLETION_CHECK_DIGEST_PATTERN.fullmatch(digest):
        raise ValueError(
            "mark_acceptance_criterion_met requires a sha256 completion-check digest"
        )
    validate_identity(platform, session_id)
    if not isinstance(expected_criterion, dict):
        raise ValueError("expected_criterion is required from the approved completion snapshot")
    target_id = str(criterion_id)
    paths = session_paths(root, platform, session_id)
    with _transaction(root, transaction) as connection:
        state = _load_state_for_update(connection, paths, platform, session_id)
        _check_expected(state, expected_input_event_id, expected_revision)
        criteria = state.get("acceptance_criteria")
        matches = [c for c in criteria if isinstance(c, dict) and c.get("id") == target_id
                   and (c.get("work_unit_id"), c.get("contract_revision")) ==
                       (expected_criterion.get("work_unit_id"), expected_criterion.get("contract_revision"))] if isinstance(criteria, list) else []
        if len(matches) != 1:
            raise ValueError("acceptance criterion is missing or ambiguous")
        criterion = matches[0]
        if criterion.get("admitted") is not True:
            raise ValueError("acceptance criterion is not admitted")
        if "work_unit_id" in criterion:
            state["canonical_input_event_id"] = (state["latest_input_event_id"]
                if _canonical_input_bound(connection, root, state) else "")
            route = _response_route_module().project_stored_response_route(state)
            if (route is None or route["status"] != "ready" or not any(
                    (c["work_unit_id"], c["contract_revision"], c["criterion_id"]) ==
                    acceptance_criterion_key(criterion) for c in route["selected_criteria"])):
                raise ValueError("owned criterion must be selected by a ready current response route")
        for key in ("id", "summary", "source", "admitted", *(
                ("work_unit_id", "contract_revision") if "work_unit_id" in criterion else ())):
            if (key not in expected_criterion or expected_criterion[key] != criterion.get(key)
                    or type(expected_criterion[key]) is not type(criterion.get(key))):
                raise ValueError("stale expected criterion; obtain evidence for the current approved condition")
        before = _semantic_projection(state)
        now = utc_now()
        criterion["status"] = "met"
        criterion["met_completion_check_digest"] = digest
        criterion["met_at"] = now
        state["updated_at"] = now
        if "work_unit_id" in criterion:
            _persist_canonical_intake(connection, state)
        _commit(connection, state, {
            "ts": now,
            "event": "acceptance-criterion-met",
            "platform": platform,
            "session_id": session_id,
            "criterion_id": target_id,
            "work_unit_id": criterion.get("work_unit_id"),
            "contract_revision": criterion.get("contract_revision"),
            "completion_check_digest": digest,
            "input_event_id": state["latest_input_event_id"],
            "semantic_changes": _semantic_changes(before, state),
        })
    return paths


def invalidate_acceptance_criterion(
    *, root: Path = DEFAULT_ROOT, platform: str, session_id: str,
    criterion_id: str, expected_input_event_id: str,
    expected_criterion: dict[str, Any], expected_completion_check_digest: str,
    invalidation_reason: str, transaction: sqlite3.Connection | None = None,
) -> dict[str, Path]:
    """Reopen observed invalid evidence without changing its admitted meaning.

    Dependency callers supply observed changes. This writer checks exact current
    native selection, immutable definition and the currently met proof (CAS).
    It cannot assert pass. Duplicate invalidation of that same proof/input is a
    no-op only when the last proof event for this qualified criterion is the
    matching invalidation. Newer proof is never withdrawn by an old request.
    """
    validate_identity(platform, session_id)
    if (not isinstance(expected_input_event_id, str) or not expected_input_event_id
            or not isinstance(expected_criterion, dict)
            or not isinstance(expected_completion_check_digest, str)
            or not COMPLETION_CHECK_DIGEST_PATTERN.fullmatch(expected_completion_check_digest)
            or invalidation_reason != "dependency-mismatch"):
        raise ValueError("invalidation requires current input, definition, proof digest and an allowed evidence change")
    digest = expected_completion_check_digest.removeprefix("sha256:").lower()
    paths = session_paths(root, platform, session_id)
    with _transaction(root, transaction) as connection:
        state = _load_state_for_update(connection, paths, platform, session_id)
        _check_expected(state, expected_input_event_id, None)
        key = (expected_criterion.get("work_unit_id"), expected_criterion.get("contract_revision"), criterion_id)
        if (not isinstance(key[0], str) or not key[0] or type(key[1]) is not int or key[1] < 1):
            raise ValueError("invalidation requires an exact owned qualified criterion")
        matches = [c for c in state.get("acceptance_criteria", [])
                   if isinstance(c, dict) and acceptance_criterion_key(c) == key]
        if len(matches) != 1 or matches[0].get("admitted") is not True:
            raise ValueError("invalidation criterion is missing, ambiguous or not admitted")
        criterion = matches[0]
        for field in ("id", "summary", "source", "admitted", "work_unit_id", "contract_revision"):
            if (field not in expected_criterion or expected_criterion[field] != criterion.get(field)
                    or type(expected_criterion[field]) is not type(criterion.get(field))):
                raise ValueError("stale expected criterion; no invalidation was applied")
        state["canonical_input_event_id"] = (state["latest_input_event_id"]
            if _canonical_input_bound(connection, root, state) else "")
        route = _response_route_module().project_stored_response_route(state)
        if (route is None or route["status"] != "ready" or not any(
                (c["work_unit_id"], c["contract_revision"], c["criterion_id"]) == key
                for c in route["selected_criteria"]) or not any(
                u["work_unit_id"] == key[0] and u["contract_revision"] == key[1]
                and u["owner"] == {"platform": platform, "session_id": session_id}
                for u in route["work_units"])):
            raise ValueError("invalidation requires the ready current native owner selection")
        if criterion.get("status") != "met":
            events = read_session_events(root=root, platform=platform, session_id=session_id, transaction=connection)
            latest = next((e for e in reversed(events) if
                (e.get("work_unit_id"), e.get("contract_revision"), e.get("criterion_id")) == key
                and e.get("event") in {"acceptance-criterion-met", "acceptance-criterion-evidence-invalidated"}), None)
            if (latest is not None and latest["event"] == "acceptance-criterion-evidence-invalidated"
                    and latest.get("input_event_id") == expected_input_event_id
                    and latest.get("prior_completion_check_digest", "").removeprefix("sha256:") == digest):
                return paths
            raise ValueError("criterion is not currently met by the expected proof")
        prior_digest = criterion.get("met_completion_check_digest", "")
        if prior_digest.removeprefix("sha256:") != digest:
            raise ValueError("newer or different met proof; no invalidation was applied")
        before = _semantic_projection(state)
        prior_time = criterion.get("met_at")
        criterion["status"] = "unmet"
        criterion.pop("met_completion_check_digest", None)
        criterion.pop("met_at", None)
        now = utc_now()
        state["updated_at"] = now
        _persist_canonical_intake(connection, state)
        _commit(connection, state, {
            "ts": now, "event": "acceptance-criterion-evidence-invalidated",
            "platform": platform, "session_id": session_id, "criterion_id": criterion_id,
            "work_unit_id": key[0], "contract_revision": key[1], "input_event_id": expected_input_event_id,
            "invalidation_reason": invalidation_reason, "criterion_source": criterion["source"],
            "prior_completion_check_digest": prior_digest, "prior_met_at": prior_time,
            "semantic_changes": _semantic_changes(before, state),
        })
    return paths


def consumer_snapshot(state_or_path: dict[str, Any] | Path) -> dict[str, Any]:
    if isinstance(state_or_path, Path):
        # A single read preserves both semantics and their actual metadata.
        # Missing input metadata must not become a fabricated CAS anchor.
        if state_or_path.name == "intent-state.json":
            state = read_session_state(root=state_or_path.parents[2], platform=state_or_path.parent.parent.name,
                                       session_id=state_or_path.parent.name)
        else:
            try:
                state = json.loads(state_or_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
        if not isinstance(state, dict):
            state = {}
    else:
        state = state_or_path
    decisions = [item for item in state.get("decisions", []) if isinstance(item, dict)]
    active_decisions = [item for item in decisions if not item.get("superseded")]
    latest_scope = state.get("latest_scope")
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": state.get("platform"),
        "session_id": state.get("session_id"),
        **{key: state[key] for key in ("ledger_revision", "latest_input_event_id",
            "latest_input_digest", "latest_input_char_count") if key in state},
        "canonical_input_event_id": state.get("canonical_input_event_id"),
        "current_context_input_event_id": state.get('current_context_input_event_id',''),
        "intake_status": state.get("intake_status"),
        "pending_native_input_count": len(_pending_native_inputs(state)),
        "current_goal": state.get("current_goal", ""),
        "user_intent_summary": state.get("user_intent_summary", ""),
        "constraints": list(state.get("constraints", [])),
        "non_goals": list(state.get("non_goals", [])),
        "open_questions": list(state.get("open_questions", [])),
        "acceptance_criteria": list(state.get("acceptance_criteria", [])),
        "work_units": deepcopy(state.get("work_units", [])),
        "response_route": deepcopy(state.get("response_route")),
        "decision_count": len(active_decisions),
        "active_decisions": deepcopy(active_decisions),
        "latest_scope": deepcopy(latest_scope) if isinstance(latest_scope, dict) else {},
        "risk_flags": list(state.get("risk_flags", [])),
        "consumer_hints": dict(state.get("consumer_hints", {})),
        "conduct_feedback": list(state.get("conduct_feedback", [])),
        "model_security_decision": state.get("model_security_decision"),
        "intake_status": state.get("intake_status", "pending"),
        "last_semantic_delta_status": state.get("last_semantic_delta_status", "not-provided"),
        "semantic_delta_policy": state.get(
            "semantic_delta_policy",
            "agent-updates-when-intent-materially-changes",
        ),
    }


def intent_reference(state):
    """Small routing input; detailed state remains available by exact pointer."""
    return {
        "schema_version": "session-intent-reference.v1",
        "input_event_id": state.get("latest_input_event_id", ""),
        "ledger_revision": state.get("ledger_revision", 0),
        "keywords": list(state.get("routing_keywords", [])),
        "summary": state.get("routing_summary", ""),
        "keywords_input_event_id": state.get("keywords_input_event_id", ""),
        "references": {
            "intent": "/user_intent_summary", "goal": "/current_goal",
            "boundary": "/latest_scope", "constraints": "/constraints",
            "decisions": "/decisions", "route": "/response_route",
        },
    }


def state_detail(state, pointer):
    """Resolve one RFC 6901 pointer, retaining the input/revision it came from."""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("A non-root JSON pointer is required")
    value = state
    for part in pointer[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise ValueError("Invalid JSON pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(value, list):
                if not key.isdigit() or str(int(key)) != key:
                    raise ValueError("Invalid list index")
                value = value[int(key)]
            elif isinstance(value, dict):
                value = value[key]
            else:
                raise ValueError("Pointer does not address a stored item")
        except (KeyError, IndexError) as exc:
            raise ValueError("State detail is unavailable") from exc
    return dict(input_event_id=state.get("latest_input_event_id", ""),
                ledger_revision=state.get("ledger_revision", 0), pointer=pointer, value=deepcopy(value))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update or read a session intent ledger.")
    parser.add_argument("--root", default=str(default_root()), help="ledger root")
    parser.add_argument("--platform", default="codex", help="agent platform")
    parser.add_argument("--session-id", default="", help="session identifier; required for writes without a bound host session")
    parser.add_argument("--input", default=None, help="raw input to hash only; never persisted")
    parser.add_argument("--delta-json", default=None, help="intent delta JSON")
    parser.add_argument("--expected-input-event-id", default=None,
                        help="input_event_id from the receipt used for this decision; required after intake")
    parser.add_argument("--expected-revision", type=int, default=None,
                        help="optional ledger_revision of the snapshot used for this update")
    parser.add_argument("--snapshot", action="store_true", help="emit consumer snapshot")
    parser.add_argument("--read-state", action="store_true", help="read intent references, or one detail with --pointer")
    parser.add_argument("--read-intent", action="store_true", help="read routing keywords and detail references only")
    parser.add_argument("--pointer", help="select one JSON pointer from --read-state")
    parser.add_argument("--read-events", action="store_true", help="read ordered authoritative audit events")
    parser.add_argument("--migrate", action="store_true", help="atomically import the explicitly selected JSON session")
    parser.add_argument("--export-to", help="write a portable projection to a separate root")
    parser.add_argument("--list-native-waits", action="store_true",
                        help="list this platform's sessions whose native input waits, from their failure records")
    parser.add_argument("--list-native-failures", action="store_true",
                        help="list this platform's sessions with a recorded native intake failure")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    delta = None
    if args.delta_json:
        delta = json.loads(args.delta_json)
        if not isinstance(delta, dict):
            raise SystemExit("--delta-json must decode to an object")
    root = Path(args.root)
    if args.list_native_waits or args.list_native_failures:
        if (args.input is not None or delta is not None or args.migrate or args.session_id or args.snapshot
                or args.read_intent or args.read_state or args.read_events or args.export_to
                or (args.list_native_waits and args.list_native_failures)):
            raise SystemExit("a native intake listing is one root-wide read; it takes no session, other read or mutation flag")
        listing = list_native_input_waits if args.list_native_waits else list_native_intake_failures
        print(json.dumps(listing(root, args.platform), ensure_ascii=False))
        return 0
    read_only = (args.snapshot or args.read_intent or args.read_state or args.read_events or args.export_to) and args.input is None and delta is None and not args.migrate
    if args.pointer and not args.read_state:
        raise SystemExit("--pointer requires --read-state")
    if (args.read_intent or args.read_state or args.read_events or args.export_to) and not read_only:
        raise SystemExit("read/export flags cannot be combined with mutations")
    # A platform-wide pointer can move while two sessions are active. Native
    # identity binds CLI mutations; an explicit historical snapshot remains a
    # supported read-only operation. Keep the library's explicit-session API
    # independent of the environment for adapters and offline processing.
    bound_session = ""
    if args.platform == "codex":
        bound_session = os.environ.get("CODEX_THREAD_ID", "")
    if not bound_session:
        bound_session = os.environ.get("GHOST_ALICE_SESSION_ID", "")
    explicit_session = args.session_id
    if not read_only:
        try:
            if bound_session:
                validate_identity(args.platform, bound_session)
            if explicit_session:
                validate_identity(args.platform, explicit_session)
        except ValueError as exc:
            raise SystemExit(str(exc) + ". No ledger was written.") from None
        if bound_session and explicit_session and explicit_session != bound_session:
            raise SystemExit(
                "session identity mismatch: --session-id does not match the bound host session; "
                "use the session ID in the current hook receipt. No ledger was written. "
                "Do not unset or replace host identity to bypass this check."
            )
        if not bound_session and not explicit_session:
            raise SystemExit(
                "--session-id is required for writes without a bound host session. "
                "Use the current hook receipt or an explicitly selected standalone session; "
                "current-session.json is a shared discovery hint, not a write identity. "
                "No ledger was written."
            )
    session_id = resolve_session_id(
        root=root,
        platform=args.platform,
        explicit=explicit_session or bound_session,
        for_write=not read_only,
    )
    if read_only:
        try:
            coordinates = dict(root=root, platform=args.platform, session_id=session_id)
            if args.export_to:
                result = {key: str(value) for key, value in export_session(**coordinates, destination=Path(args.export_to)).items()}
            elif args.read_events:
                result = read_session_events(**coordinates)
            else:
                result = read_session_state(**coordinates)
                if args.expected_input_event_id and result.get("latest_input_event_id") != args.expected_input_event_id:
                    raise ValueError("State input changed; read the current intent reference")
                if args.expected_revision is not None and result.get("ledger_revision") != args.expected_revision:
                    raise ValueError("State revision changed; read the current intent reference")
                if args.read_intent:
                    result = intent_reference(result)
                elif args.pointer:
                    result = state_detail(result, args.pointer)
                elif args.snapshot:
                    result = consumer_snapshot(result)
                else:
                    result = intent_reference(result)
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise SystemExit(str(exc)) from None
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.migrate:
        try:
            state = migrate_session(root=root, platform=args.platform, session_id=session_id)
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise SystemExit(str(exc)) from None
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    try:
        paths = record_turn(
            root=root,
            platform=args.platform,
            session_id=session_id,
            raw_user_input=args.input,
            intent_delta=delta,
            source="cli",
            expected_input_event_id=args.expected_input_event_id,
            expected_revision=args.expected_revision,
        )
    except (ValueError, TimeoutError, RuntimeError, sqlite3.Error) as exc:
        raise SystemExit(str(exc)) from None
    if args.snapshot:
        print(json.dumps(consumer_snapshot(paths["state"]), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps({key: str(value) for key, value in paths.items()}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
