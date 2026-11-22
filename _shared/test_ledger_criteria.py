"""Relational storage of the current and frozen criterion lists."""
import json
import sqlite3
import unittest

import ledger_criteria as criteria
import response_route


class CriteriaStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("CREATE TABLE sessions(platform, session_id, PRIMARY KEY(platform,session_id))")
        self.owner = ("codex", "a")
        self.db.execute("INSERT INTO sessions VALUES (?,?)", self.owner)
        criteria.create_schema(self.db)
        self.owned = dict(id="AC1", summary="Original result", source="user-explicit",
                          selectable=True, work_unit_id="X", contract_revision=2)
        self.definition = {k: self.owned[k] for k in ("id", "summary", "source")}
        self.unit = dict(work_unit_id="X", contract_revision=2, kind="execution", admitted=True,
                         owner=dict(platform="codex", session_id="a"), criteria=[self.definition])

    def write(self, rows, units):
        with self.db:
            criteria.write_current(self.db, *self.owner, rows)
            criteria.write_registry(self.db, *self.owner, units)

    def current(self, **filters):
        return criteria.read_current(self.db, *self.owner, **filters)

    def test_current_and_frozen_values_share_the_original_row_after_current_changes(self):
        original = self.owned
        self.write([original], [self.unit])
        self.write([dict(original, summary="Revised", selectable=False)], [self.unit])
        self.assertEqual(self.current(), [dict(original, summary="Revised", selectable=False)])
        self.assertEqual(criteria.read_registry(self.db, *self.owner), [self.unit])
        self.assertEqual(self.db.execute("SELECT value_no,criterion_text FROM criterion_values ORDER BY value_no"
                                         ).fetchall(), [(1, "Original result"), (2, "Revised")])
        self.assertEqual(self.db.execute("SELECT value_no FROM revision_criteria").fetchone(), (1,))
        self.write([original], [self.unit])
        self.assertEqual(self.db.execute("SELECT value_no FROM current_criteria").fetchone(), (1,))
        before = self.db.total_changes
        self.write([original], [self.unit])
        self.assertEqual(self.db.total_changes, before)
        with self.assertRaises(ValueError):
            self.write([original], [dict(self.unit, criteria=[dict(self.definition, summary="Invalid rewrite")])])
        self.assertEqual(criteria.read_registry(self.db, *self.owner), [self.unit])

    def test_binding_reuses_position_and_preserves_fields_aliases_and_local_id_collisions(self):
        legacy = dict(self.definition, admitted=True, status="met", met_at="then", nested={"raw": 1})
        other = dict(self.owned, work_unit_id="Y")
        rows = [other, legacy, dict(legacy, id="tail", admitted=None), dict(self.definition, id="absent"),
                dict(legacy, id="both", selectable=True), dict(legacy, id="zero", admitted=0)]
        self.write(rows, [self.unit])
        self.assertEqual(json.dumps(self.current(), sort_keys=True), json.dumps(rows, sort_keys=True))
        rows[1] = dict(legacy, work_unit_id="X", contract_revision=2)
        self.write(rows, [self.unit])
        self.assertEqual(self.current(), rows)
        self.assertEqual(self.db.execute(
            "SELECT position, criterion_text, origin_kind, value_no, legacy_fields_json "
            "FROM current_criteria WHERE work_unit_id='X'"
        ).fetchone()[:4], (1, None, None, 1))
        stored = json.loads(self.db.execute(
            "SELECT legacy_fields_json FROM current_criteria WHERE work_unit_id='X'"
        ).fetchone()[0])
        self.assertEqual(stored, dict(status="met", met_at="then", nested={"raw": 1}))
        for invalid in ([*rows, dict(rows[1])], [legacy, dict(legacy)], [dict(legacy, selectable=False)],
                        [dict(self.owned, contract_revision=None)], [dict(self.owned, source="unknown")]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.write(invalid, [self.unit])
            self.assertEqual(self.current(), rows)

    def test_filtered_reads_keep_global_order_and_use_scoped_criterion_and_eligibility_indexes(self):
        rows = [dict(self.owned, work_unit_id="other"), self.owned,
                dict(self.owned, id="AC2", selectable=False), dict(self.owned, work_unit_id="withdrawn", selectable=False)]
        self.write(rows, [])
        other_owner = ("claude", "a")
        self.db.execute("INSERT INTO sessions VALUES (?,?)", other_owner)
        criteria.write_current(self.db, *other_owner, [dict(rows[-1], selectable=True)])
        sql = []
        self.db.set_trace_callback(sql.append)
        self.assertEqual(self.current(criterion_keys=[("withdrawn", 2, "AC1"), ("X", 2, "AC1")]),
                         [rows[1], rows[-1]])
        self.assertEqual(self.current(criterion_keys=[]), [])
        self.assertEqual(criteria.selectable_execution_units(self.db, *self.owner,
                         [("X", 2), ("withdrawn", 2), ("other", 3)]), {("X", 2)})
        self.db.set_trace_callback(None)
        reads = [query for query in sql if "current_criteria" in query]
        self.assertTrue(reads)
        for query in reads:
            plan = [row[-1] for row in self.db.execute("EXPLAIN QUERY PLAN " + query)]
            current_reads = [row for row in plan if row.startswith(("SEARCH c ", "SEARCH current_criteria "))]
            self.assertEqual(len(current_reads), 1, plan)
            self.assertIn("work_unit_id=? AND contract_revision=?", current_reads[0])
            if "LIMIT 1" in query:
                self.assertIn("current_criteria_selectable_owner", current_reads[0])

    def test_unbound_legacy_identifier_is_read_as_written(self):
        row = dict(id="legacy condition", summary="A legacy condition", source="user-explicit", admitted="yes")
        self.write([row], [])
        self.assertEqual(self.current(), [row])

    def test_import_preserves_malformed_owned_flags_and_route_validation_still_rejects_them(self):
        body = {k: v for k, v in self.owned.items() if k != "selectable"}
        missing, raw = dict(body, id="missing"), dict(body, id="raw", admitted=1)
        rows = [self.owned, missing, raw]
        with self.db:
            criteria.write_current(self.db, *self.owner, rows, imported=True)
        self.assertEqual(json.dumps(self.current(), sort_keys=True), json.dumps(rows, sort_keys=True))
        self.assertEqual(criteria.selectable_execution_units(self.db, *self.owner, [("X", 2)]), {("X", 2)})
        unit = {k: v for k, v in self.unit.items() if k != "criteria"}
        candidate = dict(schema_version="response-route.v2",
                         routing_surface=dict(response_mode="normal", boundary_contract="n/a"),
                         work_units=[unit], selected_work_unit_ids=["X"],
                         selected_criteria=[dict(work_unit_id="X", contract_revision=2, criterion_id="AC1")])
        for invalid, error in [(missing, "fields are incomplete"), (raw, "flag must be boolean")]:
            current = self.current(criterion_keys=[("X", 2, "AC1"), ("X", 2, invalid["id"])])
            state = dict(platform="codex", session_id="a", latest_input_event_id="input", acceptance_criteria=current)
            with self.subTest(invalid=invalid), self.assertRaisesRegex(response_route.RouteSchemaError, error):
                response_route.validate_response_route(state, candidate, None)
        self.write([dict(self.owned, selectable=False), missing, raw], [])
        self.assertEqual(criteria.selectable_execution_units(self.db, *self.owner, [("X", 2)]), set())
        with self.assertRaisesRegex(response_route.RouteSchemaError, "flag must be boolean"):
            self.write([dict(self.owned, selectable="invalid"), missing, raw], [])

    def test_later_write_handles_an_absent_current_row_between_remaining_positions(self):
        rows = [dict(self.owned, id=cid) for cid in ("AC1", "AC2", "AC3")]
        self.write(rows, [])
        self.db.execute("DELETE FROM current_criteria WHERE criterion_id='AC2'")
        self.assertEqual(self.current(), [rows[0], rows[2]])
        remaining = [rows[0], dict(rows[2], selectable=False), dict(self.owned, id="AC4")]
        self.write(remaining, [])
        self.assertEqual(self.current(), remaining)

    def test_frozen_references_reject_values_from_another_session(self):
        self.write([self.owned], [self.unit])
        self.db.execute("INSERT INTO sessions VALUES ('claude','a')")
        other_unit = dict(self.unit, owner=dict(platform="claude", session_id="a"), criteria=[])
        criteria.write_registry(self.db, "claude", "a", [other_unit])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("""INSERT INTO revision_criteria
                (platform,session_id,work_unit_id,contract_revision,criterion_id,value_no,position)
                VALUES ('claude','a','X',2,'AC1',1,0)""")

    def test_registry_preserves_order_missing_definitions_and_empty_definitions(self):
        incomplete = {k: v for k, v in self.unit.items() if k != "criteria"}
        units = [incomplete, dict(self.unit, work_unit_id="Y", criteria=[]),
                 dict(self.unit, work_unit_id="Z", criteria=[dict(self.definition, id="Z"), self.definition])]
        self.write([], units)
        self.assertEqual(criteria.read_registry(self.db, *self.owner), units)
        units[1]["admitted"] = False
        self.write([], units)
        self.assertEqual(criteria.read_registry(self.db, *self.owner), units)
        with self.assertRaises(ValueError):
            self.write([], [dict(self.unit, owner=dict(platform="codex", session_id="b"))])


if __name__ == "__main__":
    unittest.main()
