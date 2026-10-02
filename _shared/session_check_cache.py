#!/usr/bin/env python3
"""Persist reusable mechanical checks, scoped to an exact platform/session.

Dependencies: Python 3.11+ standard library. This is not an intent ledger or a
security-decision cache. Values must be small check metadata, never prompts,
instruction bodies, credentials or tool transcripts.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable


class UnstableCheckTarget(OSError):
    """The checked target kept changing; no stable result can be returned."""


def _valid_check_time(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _stamp(target: Path) -> list[str] | None:
    try:
        stat = target.stat()
    except FileNotFoundError:
        return ["missing"]
    except OSError:
        return None
    # Strings retain integer nanoseconds and inode identity across JSON readers.
    return [str(value) for value in (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)]


def _connect(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "session-checks.sqlite3", timeout=2)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS checks (platform TEXT, session TEXT, key TEXT, target TEXT, stamp TEXT, value TEXT, checked_at TEXT, PRIMARY KEY(platform,session,key))")
        connection.execute("CREATE TABLE IF NOT EXISTS check_events (platform TEXT, session TEXT, key TEXT, status TEXT, occurred_at TEXT)")
        connection.commit()
    except sqlite3.Error:
        connection.close()
        raise
    return connection


def observe_check(root: Path, platform: str, session_id: str, key: str,
                  target: Path, checker: Callable[[], dict], *, force: bool = False,
                  validate: Callable[[dict], bool] | None = None) -> dict:
    """Reuse only this session's result for this unchanged target.

    Every call records checked/reused in the cache audit. A missing identity,
    unreadable target stamp, corrupt/unavailable store, or target changing while
    checked bypasses persistence and performs the real check.
    """
    target = target.resolve()
    before = _stamp(target)
    checked_at = datetime.now(timezone.utc).isoformat()
    connection = None
    if platform and session_id and session_id != "unknown" and before is not None:
        try:
            connection = _connect(root)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT target,stamp,value,checked_at FROM checks WHERE platform=? AND session=? AND key=?", (platform, session_id, key)).fetchone()
            if row and not force and row[0] == str(target) and _valid_check_time(row[3]) and json.loads(row[1]) == before and _stamp(target) == before:
                value = json.loads(row[2])
                if isinstance(value, dict) and (validate is None or validate(value)):
                    connection.execute("INSERT INTO check_events VALUES (?,?,?,?,?)", (platform, session_id, key, "reused", checked_at))
                    connection.commit()
                    connection.close()
                    return {"status": "reused", "value": value, "checked_at": row[3]}
        except (sqlite3.Error, OSError, ValueError, TypeError):
            if connection is not None:
                connection.close()
            connection = None
    try:
        for _attempt in range(3):
            value = checker()
            after = _stamp(target)
            if before == after:
                break
            before = after
        else:
            raise UnstableCheckTarget(f"Check target changed repeatedly: {target}; recheck before continuing")
        checked_at = datetime.now(timezone.utc).isoformat()
        result = {"status": "uncached", "value": value, "checked_at": checked_at}
        if connection is not None and after is not None:
            try:
                connection.execute("INSERT OR REPLACE INTO checks VALUES (?,?,?,?,?,?,?)", (platform, session_id, key, str(target), json.dumps(after), json.dumps(value), checked_at))
                connection.execute("INSERT INTO check_events VALUES (?,?,?,?,?)", (platform, session_id, key, "checked", checked_at))
                connection.commit()
                result["status"] = "checked"
            except (sqlite3.Error, OSError):
                pass
        return result
    finally:
        if connection is not None:
            connection.close()


def load_instruction(root: Path, platform: str, session_id: str, path: Path,
                     *, body_retained: bool) -> dict:
    body = []
    def read_body() -> dict:
        body.append(path.read_text(encoding="utf-8"))
        return {"path": str(path.resolve()), "kind": "instruction"}
    metadata = {"path": str(path.resolve()), "kind": "instruction"}
    result = observe_check(root, platform, session_id, "instruction:" + str(path.resolve()), path, read_body,
                           force=not body_retained, validate=lambda value: value == metadata)
    if body:
        result["body"] = body[-1]
    return result


def instruction_delivery(root: Path, platform: str, session_id: str,
                         key: str, source: Path, message: str) -> bool:
    """True once per unchanged instruction source and session; no text stored."""
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
    result = observe_check(root, platform, session_id, key + ":" + digest, source,
                           lambda: {"delivered": True}, validate=lambda value: value == {"delivered": True})
    return result["status"] != "reused"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--instruction-path", type=Path)
    parser.add_argument("--body-retained", action="store_true")
    parser.add_argument("--read-records", action="store_true")
    args = parser.parse_args()
    native = os.environ.get("CODEX_THREAD_ID") if args.platform == "codex" else None
    bound = native or os.environ.get("GHOST_ALICE_SESSION_ID")
    if bound and bound != args.session_id:
        parser.error("session id conflicts with the current host binding")
    if args.read_records:
        with closing(_connect(args.root)) as connection:
            rows = connection.execute("SELECT key,target,value,checked_at FROM checks WHERE platform=? AND session=?", (args.platform, args.session_id)).fetchall()
        print(json.dumps([{"key": row[0], "target": row[1], "value": json.loads(row[2]), "checked_at": row[3]} for row in rows]))
    elif args.instruction_path:
        print(json.dumps(load_instruction(args.root, args.platform, args.session_id, args.instruction_path, body_retained=args.body_retained)))
    else:
        parser.error("choose --instruction-path or --read-records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
