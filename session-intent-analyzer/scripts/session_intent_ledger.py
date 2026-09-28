#!/usr/bin/env python3
"""Maintain a session-local semantic intent ledger.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
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


SCHEMA_VERSION = "session-intent-ledger.v1"
CURRENT_SESSION_SCHEMA = "session-intent-current.v1"
SESSION_INTENT_ROOT_ENV = "GHOST_ALICE_SESSION_INTENT_ROOT"
LEGACY_DEFAULT_ROOT = Path("~/.ghost-alice/session-intent")
TEXT_FIELDS = ("current_goal", "user_intent_summary")
LIST_FIELDS = ("constraints", "non_goals", "open_questions", "risk_flags")
ACCEPTANCE_CRITERIA_SOURCES = {"user-explicit", "inferred", "previous-tool", "system-doc"}
ACCEPTANCE_CRITERIA_STATUSES = {"unmet", "met"}
ACCEPTANCE_CRITERIA_ADMITTED_SOURCES = {"user-explicit", "previous-tool", "system-doc"}
COMPLETION_CHECK_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
SECURITY_DECISIONS = {"allow", "block"}
SECURITY_REASON_MAX = 240
SECURITY_RISK_FLAG_MAX = 12
CONDUCT_FEEDBACK_SOURCES = {"user-explicit", "inferred"}
CONDUCT_FEEDBACK_STATUS = {"open", "encoded"}
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


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    # Called under the session lock. Atomic replacement also protects audit
    # readers from a partial last row if a process exits during the write.
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if previous and not previous.endswith("\n"):
        previous += "\n"
    _atomic_write(path, previous + json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def validate_identity(platform: str, session_id: str) -> None:
    """Mutation identities are exact, never sanitized aliases of another ID."""
    for value in (platform, session_id):
        if not isinstance(value, str) or not value or safe_component(value, "") != value:
            raise ValueError("invalid ledger identity: use the exact canonical platform and session ID from the hook receipt")


@contextmanager
def _session_lock(paths: dict[str, Path], timeout: float = 10.0):
    """Serialize all cooperating writers on POSIX and Windows, including threads."""
    paths["dir"].mkdir(parents=True, exist_ok=True)
    with (paths["dir"] / ".intent-state.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("ledger session is busy; retry with a fresh receipt") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            release()


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
    # A read of legacy state must not fabricate a new creation/update instant
    # on every call. Unknown historical timestamps stay explicitly unknown.
    for key in ("created_at", "updated_at"):
        if key not in data:
            state[key] = ""
    if not has_anchor:
        # One-time compatibility for a valid v1 header. The JSON state owns
        # the anchor after the next commit; the audit is then a projection.
        try:
            rows = paths["events"].read_text(encoding="utf-8").splitlines() if paths["events"].exists() else []
            for line in rows:
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
            raise ValueError("legacy ledger audit is corrupt; inspect it before writing") from None
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
        raise ValueError("authoritative SQLite database is missing; restore it, do not revive legacy JSON")


def _check_database(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != STORAGE_SCHEMA_VERSION:
        raise ValueError("unsupported Ghost state database schema; no legacy fallback is permitted")


def _initialize_database(connection: sqlite3.Connection) -> None:
    # Individual statements preserve the caller's transaction; executescript
    # would implicitly commit and break Core/Autopilot atomicity.
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
    """One durable unit of work, including bound Autopilot mutations.

    Lock waiting limits contention, not the lifetime of an autonomous task.
    Connections are operation-local and must be passed explicitly to nested
    writers; this also works when adapters load the module under another name.
    """
    database = storage_database_path(root)
    _require_database_if_initialized(root)
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
            # This routing marker records cutover, never semantic state. It
            # precedes the first semantic commit so a lost DB cannot silently
            # resurrect pre-migration JSON. Restore the DB to recover.
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
    for line in paths["events"].read_text(encoding="utf-8").splitlines():
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
    # Catch a legacy writer changing the files during import; legacy writers
    # must be stopped at cutover. The originals remain a recovery source.
    after = {key: hashlib.sha256(paths[key].read_bytes()).hexdigest()
             for key in ("state", "events", "security") if paths[key].exists()}
    if after != sources:
        raise ValueError("legacy source changed during migration; retry after stopping the legacy writer")
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
    """Read authority without creating files. Legacy recovery is an explicit import."""
    validate_identity(platform, session_id)
    if recover_audit:
        return migrate_session(root=root, platform=platform, session_id=session_id, transaction=transaction)
    with _read_connection(root, transaction) as connection:
        state = _stored_state(connection, platform, session_id)
        if state is not None:
            return state
        paths = session_paths(root, platform, session_id)
        # Preserve absent legacy metadata, rather than inventing proof of a
        # current intake. Migration normalizes only after validating its audit.
        if not paths["state"].exists():
            _load_legacy_state(paths, platform, session_id)
            return {}
        raw = json.loads(paths["state"].read_text(encoding="utf-8"))
        _load_legacy_state(paths, platform, session_id, data=raw)
        # Pending legacy projection does not invalidate its committed current
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
    for key in ("constraints", "non_goals", "open_questions"):
        projection[key] = objects(state.get(key), ("id", "summary", "source", "status"))
    projection["acceptance_criteria"] = objects(state.get("acceptance_criteria"),
        ("id", "summary", "source", "admitted", "status", "met_completion_check_digest"))
    projection["decisions"] = objects(state.get("decisions"),
        ("id", "summary", "source", "superseded", "superseded_by"))
    projection["conduct_feedback"] = objects(state.get("conduct_feedback"),
        ("id", "summary", "failure_pattern", "corrective_rule", "source", "status", "occurrence_count"))
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
    return {key: {"before": before[key], "after": value}
            for key, value in after.items() if before[key] != value}


def merge_unique(existing: Any, incoming: Any) -> list[Any]:
    """Merge list-field entries without destroying their shape.

    Plain strings are kept as strings (deduped by value). Structured entries
    (dicts, e.g. {"id", "summary"}) are kept as objects and deduped by their
    "id" when present, else by a normalized JSON form. This only preserves the
    shape supplied by the caller; it does not impose or validate a schema.
    First occurrence wins, matching the prior string-dedup behavior.
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


def normalize_acceptance_criterion(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        summary = raw.strip()
        if not summary:
            return None
        return {
            "id": safe_component(summary.lower(), "criterion"),
            "summary": summary,
            "source": "inferred",
            "status": "unmet",
            "admitted": False,
        }
    if not isinstance(raw, dict):
        return None
    summary = str(raw.get("summary") or raw.get("text") or "").strip()
    criterion_id = safe_component(str(raw.get("id") or summary.lower()), "criterion")
    if not summary:
        return None
    source = str(raw.get("source") or "inferred").strip()
    if source not in ACCEPTANCE_CRITERIA_SOURCES:
        source = "inferred"
    status = str(raw.get("status") or "").strip().lower()
    if status not in ACCEPTANCE_CRITERIA_STATUSES:
        status = "unmet"
    admitted = raw.get("admitted")
    if not isinstance(admitted, bool):
        admitted = source in ACCEPTANCE_CRITERIA_ADMITTED_SOURCES
    return {
        "id": criterion_id,
        "summary": summary,
        "source": source,
        "status": status,
        "admitted": admitted,
    }


def merge_acceptance_criteria(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def store(criterion_id: str, criterion: dict[str, Any]) -> None:
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
        store(criterion["id"], criterion)

    # Incoming raw deltas may not assert "met": the status only transitions through mark_acceptance_criterion_met. Admission may still be promoted.
    incoming_items = incoming if isinstance(incoming, list) else [incoming]
    for item in incoming_items:
        criterion = normalize_acceptance_criterion(item)
        if criterion is None:
            continue
        criterion_id = criterion["id"]
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
    source = str(payload.get("source") or "user-explicit").strip()
    if source not in CONDUCT_FEEDBACK_SOURCES:
        source = "user-explicit"
    status = str(payload.get("status") or "open").strip()
    if status not in CONDUCT_FEEDBACK_STATUS:
        status = "open"
    return {
        "id": safe_component(entry_id, "conduct"),
        "summary": summary or rule or pattern,
        "failure_pattern": pattern,
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
            state[field] = merge_unique(state.get(field, []), delta.get(field))

    if "acceptance_criteria" in delta:
        state["acceptance_criteria"] = merge_acceptance_criteria(
            state.get("acceptance_criteria", []),
            delta.get("acceptance_criteria"),
        )

    if "consumer_hints" in delta:
        state["consumer_hints"] = merge_consumer_hints(state.get("consumer_hints", {}), delta.get("consumer_hints"))

    if "conduct_feedback" in delta:
        state["conduct_feedback"] = merge_conduct_feedback(
            state.get("conduct_feedback", []), delta.get("conduct_feedback"), now
        )

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
        if intent_delta is not None and not isinstance(intent_delta, dict):
            raise ValueError("intent_delta must be an object")
        if raw_user_input is not None:
            state["latest_input_event_id"] = observation["input_event_id"]
            state["latest_input_digest"] = observation["input_digest"]
            state["latest_input_char_count"] = observation["input_char_count"]
        decision = (intent_delta or {}).get("model_security_decision")
        if isinstance(decision, dict) and state["latest_input_event_id"]:
            if decision.get("input_event_id") != state["latest_input_event_id"]:
                raise ValueError("stale or missing security decision input event; re-evaluate the current input")
            if decision.get("input_digest") not in (None, "", state["latest_input_digest"]):
                raise ValueError("security decision input digest mismatch")
        before = _semantic_projection(state)
        state = apply_delta(state, intent_delta)
        state["intake_status"] = "observed"
        state["last_intake_source"] = source
        state["last_semantic_delta_status"] = "recorded" if intent_delta else "not-provided"
        state["semantic_delta_policy"] = "agent-updates-when-intent-materially-changes"
        event: dict[str, Any] = {
            "ts": observation["observed_at"],
            "event": "user-input-observed" if raw_user_input is not None else "intent-updated",
            "platform": platform,
            "session_id": session_id,
            "source": source,
            "intent_delta_status": "recorded" if intent_delta else "not-provided",
            "input_event_id": state["latest_input_event_id"],
            "semantic_changes": _semantic_changes(before, state),
        }
        if raw_user_input is not None:
            event["event_id"] = observation["input_event_id"]
            event["input_digest"] = observation["input_digest"]
            event["input_char_count"] = observation["input_char_count"]
        if intent_delta:
            event["intent_delta_digest"] = json_digest(intent_delta)
            event["delta_keys"] = sorted(str(key) for key in intent_delta.keys())
        _commit(connection, state, event)
    return paths


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

    The caller supplies its verified completion-check digest and the exact
    input/criterion snapshot that proof concerns. This function validates
    binding and digest syntax, not the underlying test evidence. Deltas cannot assert
    "met"; only this writer can, so an autopilot run terminates a criterion
    only after a passing completion-check, not merely because work happened.
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
        matches = [c for c in criteria if isinstance(c, dict) and c.get("id") == target_id] if isinstance(criteria, list) else []
        if len(matches) != 1:
            raise ValueError("acceptance criterion is missing or ambiguous")
        criterion = matches[0]
        if criterion.get("admitted") is not True:
            raise ValueError("acceptance criterion is not admitted")
        for key in ("id", "summary", "source", "admitted"):
            if (key not in expected_criterion or expected_criterion[key] != criterion.get(key)
                    or type(expected_criterion[key]) is not type(criterion.get(key))):
                raise ValueError("stale expected criterion; obtain evidence for the current approved condition")
        before = _semantic_projection(state)
        now = utc_now()
        criterion["status"] = "met"
        criterion["met_completion_check_digest"] = digest
        criterion["met_at"] = now
        state["updated_at"] = now
        _commit(connection, state, {
            "ts": now,
            "event": "acceptance-criterion-met",
            "platform": platform,
            "session_id": session_id,
            "criterion_id": target_id,
            "completion_check_digest": digest,
            "input_event_id": state["latest_input_event_id"],
            "semantic_changes": _semantic_changes(before, state),
        })
    return paths


def consumer_snapshot(state_or_path: dict[str, Any] | Path) -> dict[str, Any]:
    if isinstance(state_or_path, Path):
        # A single read preserves both semantics and their actual metadata.
        # Missing legacy metadata must not become a fabricated CAS anchor.
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
        "ledger_revision": state.get("ledger_revision"),
        "latest_input_event_id": state.get("latest_input_event_id"),
        "current_goal": state.get("current_goal", ""),
        "user_intent_summary": state.get("user_intent_summary", ""),
        "constraints": list(state.get("constraints", [])),
        "non_goals": list(state.get("non_goals", [])),
        "open_questions": list(state.get("open_questions", [])),
        "acceptance_criteria": list(state.get("acceptance_criteria", [])),
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
    parser.add_argument("--read-state", action="store_true", help="read exact authoritative state without mutation")
    parser.add_argument("--read-events", action="store_true", help="read ordered authoritative audit events")
    parser.add_argument("--migrate", action="store_true", help="atomically import the explicitly selected legacy session")
    parser.add_argument("--export-to", help="write a portable projection to a separate root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    delta = None
    if args.delta_json:
        delta = json.loads(args.delta_json)
        if not isinstance(delta, dict):
            raise SystemExit("--delta-json must decode to an object")
    root = Path(args.root)
    read_only = (args.snapshot or args.read_state or args.read_events or args.export_to) and args.input is None and delta is None and not args.migrate
    if (args.read_state or args.read_events or args.export_to) and not read_only:
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
                if args.snapshot:
                    result = consumer_snapshot(result)
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
