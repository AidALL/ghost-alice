"""The history reader resolves only the original session, event and frozen definitions."""
import json
import sqlite3
import unittest

from source_reference import json_digest


class RegistrationHistoryTests(unittest.TestCase):
    def setUp(self):
        import ledger_registrations
        import ledger_criteria
        self.history = ledger_registrations
        self.criteria = ledger_criteria
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("""CREATE TABLE sessions (
            platform TEXT, session_id TEXT, state_json TEXT, PRIMARY KEY (platform, session_id))""")
        self.db.execute("""CREATE TABLE intent_events (
            platform TEXT, session_id TEXT, sequence INTEGER, mutation_id TEXT, event_json TEXT,
            PRIMARY KEY (platform, session_id, sequence))""")
        self.criteria.create_schema(self.db)
        self.history.initialize(self.db)

    def populate(self, platform, text):
        owner = dict(platform=platform, session_id="s")
        self.db.execute("INSERT INTO sessions VALUES (?, 's', '{}')", (platform,))
        units = [dict(work_unit_id=name, contract_revision=1, kind="execution",
                      owner=owner, selectable=True) for name in ("first", "second")]
        criteria = [dict(work_unit_id=name, contract_revision=1, id="AC1", summary=text + name,
                         source="user-said", selectable=True) for name in ("first", "second")]
        registry = [dict(unit, criteria=[{k: row[k] for k in ("id", "summary", "source")}])
                    for unit, row in zip(units, criteria)]
        self.criteria.write_current(self.db, platform, "s", criteria)
        self.criteria.write_registry(self.db, platform, "s", registry)
        return dict(schema_version="response-route.v2", status="ready", **owner,
                    routing_surface=dict(response_mode="normal", boundary_contract="required"),
                    input_event_id="I", boundary_ref="boundary", work_units=units,
                    selected_work_unit_ids=["second", "first"],
                    selected_criteria=[dict(row, criterion_id="AC1") for row in reversed(criteria)])

    def add_event(self, platform, sequence, event):
        self.db.execute("INSERT INTO intent_events VALUES (?, 's', ?, NULL, ?)",
                        (platform, sequence, json.dumps(event)))

    def register(self, route, sequence):
        event = self.history.registration_event(route, "registration-time")
        self.add_event(route["platform"], sequence, event)
        self.history.write_selection(self.db, sequence, route)
        return event

    def test_sequence_keeps_scoped_frozen_values_and_independent_orderings(self):
        first = self.populate("codex", "original ")
        first["declared_remaining_work"] = dict(schema_version="declared-remaining-work.v1", items=[], control="active")
        event = self.register(first, 3)
        second = dict(first, selected_work_unit_ids=["first"], selected_criteria=[])
        self.register(second, 4)
        other = self.populate("claude", "another ")
        self.register(other, 3)
        for route, sequence in ((first, 3), (second, 4), (other, 3)):
            history = self.history.read_registration(self.db, route["platform"], "s", sequence)
            self.assertEqual(history["registration_sequence"], sequence)
            self.assertNotIn("status", history)
            self.assertNotIn("current_input_event_id", history)
            self.assertEqual(history["digest_verification"], "verified")
            for key in route.keys() - {"status"}:
                self.assertEqual(history[key], route[key])
            self.assertEqual(self.history.ready_projection(self.db, route["platform"], "s", sequence), route)
        for field in ("selected_criteria", "selected_work_unit_ids", "work_units"):
            self.assertNotIn(field, event)
        self.assertNotIn("original ", json.dumps(event))
        public = self.history.public_event(self.db, "codex", "s", 3, event)
        self.assertNotIn("registration_format", public)
        self.assertNotIn("declared_remaining_work", public)
        self.assertEqual(public["selected_work_unit_ids"], ["second", "first"])
        self.assertEqual(public["selected_criteria"], [
            dict(work_unit_id=name, contract_revision=1, criterion_id="AC1")
            for name in ("second", "first")])

    def test_empty_registration_missing_event_and_other_event_are_distinct(self):
        route = dict(self.populate("codex", "original "), work_units=[], selected_work_unit_ids=[],
                     selected_criteria=[])
        self.register(route, 4)
        self.add_event("codex", 5, dict(event="input-observed"))
        self.assertEqual(self.history.read_registration(self.db, "codex", "s", 4)["work_units"], [])
        for sequence, code in ((7, "registration-not-found"), (5, "not-registration")):
            with self.subTest(sequence=sequence), self.assertRaises(ValueError) as caught:
                self.history.read_registration(self.db, "codex", "s", sequence)
            self.assertEqual(caught.exception.code, code)

    def test_reader_detects_lost_refs_and_digest_mismatch(self):
        route = self.populate("codex", "original ")
        self.register(route, 3)
        self.db.commit()
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.execute("DELETE FROM revision_criteria WHERE work_unit_id='first'")
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.history.read_registration(self.db, "codex", "s", 3)
        self.db.rollback()
        self.db.execute("DELETE FROM route_criteria WHERE work_unit_id='first'")
        with self.assertRaisesRegex(ValueError, "digest mismatch") as caught:
            self.history.read_registration(self.db, "codex", "s", 3)
        self.assertEqual(caught.exception.code, "route-schema")

    def test_unselected_unit_retains_its_original_selectability_and_revision(self):
        route = self.populate("codex", "original ")
        route["work_units"][0]["selectable"] = False
        route["selected_work_unit_ids"] = ["second"]
        route["selected_criteria"] = []
        self.register(route, 2)
        self.assertEqual(self.history.read_registration(self.db, "codex", "s", 2)["work_units"], route["work_units"])

    def test_history_queries_seek_event_choices_and_definitions_by_scope(self):
        route = self.populate("codex", "original ")
        self.register(route, 3)
        statements = []
        self.db.set_trace_callback(statements.append)
        self.history.read_registration(self.db, "codex", "s", 3)
        self.db.set_trace_callback(None)
        operations = [row[3] for sql in statements for row in self.db.execute("EXPLAIN QUERY PLAN " + sql)]
        self.assertFalse(any("SCAN " in step for step in operations), operations)
        self.assertEqual(sum("SEARCH " in step for step in operations), 6, operations)

    def test_foreign_keys_and_uniqueness_reject_unbound_or_repeated_choices(self):
        route = self.populate("codex", "original ")
        self.register(route, 3)
        values = ("codex", "s", 3, "first", 1, "AC1", 2)
        cases = [values, (*values[:5], "missing", 2), (*values[:4], 2, "AC1", 2),
                 ("claude", *values[1:]), (*values[:2], 99, *values[3:])]
        for row in cases:
            with self.subTest(row=row), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute("INSERT INTO route_criteria VALUES (?, ?, ?, ?, ?, ?, ?)", row)

    def test_legacy_files_can_authenticate_latest_snapshot_with_either_digest_format(self):
        route = self.populate("codex", "original ")
        native = self.register(route, 3)
        portable = self.history.public_event(self.db, "codex", "s", 3, native)
        for sequence, digest in ((4, native["response_route_digest"]), (5, json_digest(route))):
            self.add_event("codex", sequence, dict(portable, response_route_digest=digest))
        self.db.execute("UPDATE sessions SET state_json=?", (json.dumps(dict(response_route=dict(route, status="stale"))),))
        for sequence in (4, 5):
            history = self.history.read_registration(self.db, "codex", "s", sequence)
            self.assertEqual(history["digest_verification"], "verified")
            self.assertEqual(history["selected_criteria"], route["selected_criteria"])

    def test_legacy_partial_identifies_only_provable_revisions_and_values(self):
        route = self.populate("codex", "old ")
        event = dict(event="response-route-recorded", platform="codex", session_id="s",
                     input_event_id="I", response_route_digest=json_digest(route),
                     selected_work_unit_ids=["second", "first"], selected_criteria=[
                         dict(work_unit_id="first", contract_revision=1, criterion_id="AC1")])
        self.add_event("codex", 3, event)
        units = [dict(unit, criteria=[dict(id="AC1", summary="old " + unit["work_unit_id"],
                                          source="user-said")]) for unit in route["work_units"]]
        result = self.history.read_registration(self.db, "codex", "s", 3, legacy_units=lambda: units)
        self.assertEqual(result["digest_verification"], "unavailable")
        self.assertEqual(result["selected_criteria"][0]["summary"], "old first")
        self.assertIsNone(result["work_units"][0]["contract_revision"])
        self.assertEqual(result["work_units"][1]["contract_revision"], 1)
        self.assertNotIn("status", result)


if __name__ == "__main__":
    unittest.main()
