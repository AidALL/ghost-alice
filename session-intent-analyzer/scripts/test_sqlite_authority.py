"""Behavioral contracts for the authoritative store, independent of exports.

Dependencies: Python standard library only.
"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("sqlite_ledger_under_test", Path(__file__).with_name("session_intent_ledger.py"))
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


class SQLiteAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = dict(root=self.root, platform="codex", session_id="session-a")

    def seed(self):
        ledger.record_turn(**self.args, raw_user_input="first request")
        return ledger.read_session_state(**self.args)

    def test_first_wal_transition_retries_busy_before_any_mutation(self):
        connect = sqlite3.connect
        attempts = []

        class ContendedConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "PRAGMA journal_mode = WAL":
                    attempts.append(sql)
                    if len(attempts) < 3:
                        failure = sqlite3.OperationalError("database is locked")
                        failure.sqlite_errorcode = sqlite3.SQLITE_BUSY
                        raise failure
                return super().execute(sql, *args)

        def contended(*args, **kwargs):
            return connect(*args, **kwargs, factory=ContendedConnection)

        with mock.patch.object(ledger.sqlite3, "connect", side_effect=contended):
            state = self.seed()
        self.assertEqual(len(attempts), 3)
        self.assertEqual(state["ledger_revision"], 1)
        self.assertEqual(len(ledger.read_session_events(**self.args)), 1)

    def test_wal_initialization_does_not_retry_non_contention_errors(self):
        connect = sqlite3.connect
        attempts = []

        class BrokenConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "PRAGMA journal_mode = WAL":
                    attempts.append(sql)
                    failure = sqlite3.OperationalError("read-only database")
                    failure.sqlite_errorcode = sqlite3.SQLITE_READONLY
                    raise failure
                return super().execute(sql, *args)

        def broken(*args, **kwargs):
            return connect(*args, **kwargs, factory=BrokenConnection)

        with mock.patch.object(ledger.sqlite3, "connect", side_effect=broken):
            with self.assertRaisesRegex(sqlite3.OperationalError, "read-only"):
                self.seed()
        self.assertEqual(len(attempts), 1)

    def test_database_is_authority_when_export_is_missing_or_poisoned(self):
        original = self.seed()
        self.assertTrue((self.root / "ghost-state.sqlite3").is_file(), "mutations must create SQLite authority")
        path = ledger.session_paths(**self.args)["state"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**original, "current_goal": "poisoned export"}))
        self.assertEqual(ledger.read_session_state(**self.args), original)
        path.unlink()
        self.assertEqual(ledger.read_session_state(**self.args), original)

    def test_outer_transaction_rolls_back_state_and_event(self):
        self.assertTrue(hasattr(ledger, "storage_transaction"), "shared transactional API required")
        old = self.seed()
        events = ledger.read_session_events(**self.args)
        with self.assertRaisesRegex(RuntimeError, "injected interruption"):
            with ledger.storage_transaction(self.root) as transaction:
                ledger.record_turn(**self.args, intent_delta={"current_goal": "uncommitted"},
                    expected_input_event_id=old["latest_input_event_id"], transaction=transaction)
                raise RuntimeError("injected interruption")
        self.assertEqual(ledger.read_session_state(**self.args), old)
        self.assertEqual(ledger.read_session_events(**self.args), events)

    def test_wrong_database_transaction_cannot_cross_session_root(self):
        self.assertTrue(hasattr(ledger, "storage_transaction"), "shared transactional API required")
        self.seed()
        other = self.root / "other"
        with ledger.storage_transaction(other) as transaction:
            with self.assertRaisesRegex(ValueError, "root|database"):
                ledger.record_turn(**self.args, raw_user_input="wrong root", transaction=transaction)

    def test_migration_preserves_source_and_history_and_is_idempotent(self):
        self.assertTrue(hasattr(ledger, "migrate_session"), "legacy import API required")
        paths = ledger.session_paths(**self.args)
        paths["dir"].mkdir(parents=True)
        state = ledger.default_state("codex", "session-a")
        state.update(current_goal="legacy goal", ledger_revision=1)
        event = dict(event="intent-updated", platform="codex", session_id="session-a", ledger_revision=1, mutation_id="old-mutation")
        paths["state"].write_text(json.dumps(state))
        paths["events"].write_text(json.dumps(event) + "\n")
        originals = {key: paths[key].read_bytes() for key in ("state", "events")}
        self.assertEqual(ledger.migrate_session(**self.args), state)
        self.assertEqual(ledger.migrate_session(**self.args), state)
        self.assertEqual(ledger.read_session_events(**self.args), [event])
        self.assertEqual({key: paths[key].read_bytes() for key in originals}, originals)

    def test_corrupt_database_never_revives_legacy_export(self):
        self.seed()
        database = self.root / "ghost-state.sqlite3"
        # Close per-call connections before replacing the test database.
        database.write_bytes(b"not a SQLite database")
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            ledger.read_session_state(**self.args)

    def test_deleted_database_cannot_silently_reimport_stale_export(self):
        original = self.seed()
        path = ledger.session_paths(**self.args)["state"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**original, "current_goal": "stale export"}))
        (self.root / "ghost-state.sqlite3").unlink()
        with self.assertRaisesRegex(ValueError, "missing|restore"):
            ledger.read_session_state(**self.args)
        with self.assertRaisesRegex(ValueError, "missing|restore"):
            ledger.record_turn(**self.args, raw_user_input="must not reset history")

    def test_invalid_migration_does_not_create_partial_session(self):
        self.assertTrue(hasattr(ledger, "migrate_session"), "legacy import API required")
        paths = ledger.session_paths(**self.args)
        paths["dir"].mkdir(parents=True)
        paths["state"].write_text(json.dumps(ledger.default_state("codex", "session-a")))
        paths["events"].write_text(json.dumps(dict(platform="claude", session_id="other")))
        with self.assertRaisesRegex(ValueError, "identity"):
            ledger.migrate_session(**self.args)
        with sqlite3.connect(self.root / "ghost-state.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)

    def test_reporting_and_security_consumers_read_without_exports(self):
        old = self.seed()
        ledger.record_turn(**self.args, expected_input_event_id=old["latest_input_event_id"], intent_delta={
            "current_goal": "current DB goal", "conduct_feedback": [{"id": "feedback", "source": "user-explicit",
            "corrective_rule": "Respect the revised condition", "status": "open"}]})
        root = Path(__file__).resolve().parents[2]
        def load(relative):
            entry = importlib.util.spec_from_file_location("consumer_" + Path(relative).stem, root / relative)
            module = importlib.util.module_from_spec(entry)
            sys.modules[entry.name] = module
            entry.loader.exec_module(module)
            return module
        path = ledger.session_paths(**self.args)["state"]
        security = load("jailbreak-detector/scripts/evaluate_intent_risk.py")
        analysis = load("skill-evolution/scripts/analyze_io_trace.py")
        aggregate = load("skill-evolution/scripts/aggregate_recommendations.py")
        self.assertEqual(security.load_state(path).get("current_goal"), "current DB goal")
        self.assertEqual(analysis.load_intent_context(path)["current_goal"], "current DB goal")
        report = aggregate.aggregate(self.root)
        self.assertEqual(report["session_files"], 1)
        self.assertEqual(report["recommendations"][0]["id"], "feedback")


if __name__ == "__main__":
    unittest.main()
