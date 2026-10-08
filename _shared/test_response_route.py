"""Core response projection tests. Dependencies: Python 3.11+ standard library."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "session-intent-analyzer" / "scripts"))
import session_intent_ledger as ledger


def load_routes():
    path = ROOT / "_shared" / "response_route.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("response_route", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["response_route"] = module
    spec.loader.exec_module(module)
    return module


class ResponseRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "ledger"
        self.api = load_routes()
        self.coords = dict(root=self.root, platform="codex", session_id="current")

    def intake(self, text="implement first"):
        ledger.record_turn(**self.coords, raw_user_input=text, source="hook")
        return ledger.read_session_state(**self.coords)["latest_input_event_id"]

    def criterion(self, unit="first", revision=1, admitted=True, summary=None):
        return dict(id="AC1", summary=summary or f"verify {unit}", source="user-explicit",
                    admitted=admitted, work_unit_id=unit, contract_revision=revision)

    def candidate(self, units=("first",), selected=None):
        return {
            "schema_version": "response-route.v1",
            "routing_surface": {
                "intent_relation": "continuation", "primary_request": "implement the contract",
                "causal_axis": "n/a", "response_mode": "normal",
                "response_order": "imperative-result-first-after-necessary-work",
                "change_depth": "structural", "focus_layer": "meso",
                "verification_complexity": "level-2", "boundary_contract": "required",
                "forced_visibility": "no", "reason": "current authorized implementation",
            },
            "work_units": [dict(work_unit_id=u, contract_revision=1, kind="execution", admitted=True,
                                owner={"platform": "codex", "session_id": "current"}) for u in units],
            "selected_work_unit_ids": list(units),
            "selected_criteria": [dict(work_unit_id=u, contract_revision=1, criterion_id="AC1")
                                  for u in (units if selected is None else selected)],
        }

    def setup_owned(self):
        event = self.intake()
        ledger.record_turn(**self.coords, expected_input_event_id=event, intent_delta={
            "acceptance_criteria": [self.criterion("first"), self.criterion("second"),
                dict(id="old", summary="historical unbound", source="user-explicit")]})
        return event

    def record(self, candidate=None, event=None, **extra):
        self.assertIsNotNone(self.api, "Core must supply the response route API")
        return self.api.record_response_route(**self.coords, expected_input_event_id=event or self.event,
                                             candidate=candidate or self.candidate(), boundary_ref="boundary-1", **extra)

    def test_absent_projection_does_not_create_storage_or_block_intake(self):
        self.assertIsNotNone(self.api, "Core must supply the response route API")
        self.assertIsNone(self.api.read_response_route(**self.coords))
        self.assertFalse(self.root.exists())
        self.intake()
        self.assertIsNone(self.api.read_response_route(**self.coords))

    def test_projection_selects_owned_current_criteria_not_historical_union(self):
        self.event = self.setup_owned()
        route = self.record()
        self.assertEqual(route["status"], "ready")
        self.assertEqual([c["work_unit_id"] for c in route["selected_criteria"]], ["first"])
        self.assertEqual(route["input_event_id"], self.event)
        self.assertEqual(ledger.read_session_state(**self.coords)["response_route"], route)
        self.assertFalse(ledger.session_paths(self.root, "codex", "current")["state"].exists())

    def test_new_input_stales_binding_and_preserves_verified_evidence_and_unit(self):
        self.event = self.setup_owned()
        self.record()
        criterion = ledger.read_session_state(**self.coords)["acceptance_criteria"][0]
        ledger.mark_acceptance_criterion_met(**self.coords, criterion_id="AC1", expected_criterion=criterion,
            expected_input_event_id=self.event, completion_check_digest="a" * 64)
        next_event = self.intake("status; continue the approved work")
        route = self.api.read_response_route(**self.coords)
        self.assertEqual(route["status"], "stale")
        self.assertEqual(route["current_input_event_id"], next_event)
        self.assertEqual(route["input_event_id"], self.event)
        state = ledger.read_session_state(**self.coords)
        self.assertEqual(state["acceptance_criteria"][0]["met_completion_check_digest"], "a" * 64)
        rebound = self.record(event=next_event)
        self.assertEqual(rebound["work_units"][0]["work_unit_id"], "first")
        self.assertEqual(rebound["selected_criteria"][0]["status"], "met")

    def test_stale_writer_is_rejected_without_mutation(self):
        self.event = self.setup_owned()
        self.intake("new current objective")
        before = ledger.read_session_state(**self.coords)
        with self.assertRaises(self.api.RouteBindingError):
            self.record()
        self.assertEqual(ledger.read_session_state(**self.coords), before)

    def test_unknown_legacy_owner_is_not_guessed(self):
        self.event = self.setup_owned()
        candidate = self.candidate()
        candidate["selected_criteria"][0]["criterion_id"] = "old"
        with self.assertRaises(self.api.RouteSchemaError):
            self.record(candidate)

    def test_invalid_schema_and_admission_are_fail_closed(self):
        self.event = self.setup_owned()
        cases = []
        candidate = self.candidate(); candidate["routing_surface"]["response_mode"] = "chat"; cases.append(candidate)
        candidate = self.candidate(); candidate["work_units"][0]["owner"]["session_id"] = "other"; cases.append(candidate)
        candidate = self.candidate(); candidate["work_units"][0]["admitted"] = False; cases.append(candidate)
        candidate = self.candidate(); candidate["selected_criteria"] *= 2; cases.append(candidate)
        candidate = self.candidate(); candidate["selected_work_unit_ids"] *= 2; cases.append(candidate)
        candidate = self.candidate(); candidate["selected_work_unit_ids"] = [{}]; cases.append(candidate)
        candidate = self.candidate(); candidate["selected_criteria"] = [[]]; cases.append(candidate)
        candidate = self.candidate(); candidate["answer_body"] = "business answer must not persist"; cases.append(candidate)
        candidate = self.candidate(); candidate["work_units"][0]["contract_revision"] = True; cases.append(candidate)
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(self.api.RouteSchemaError):
                    self.record(candidate)
        ledger.record_turn(**self.coords, expected_input_event_id=self.event,
            intent_delta={"acceptance_criteria": [self.criterion(admitted=False)]})
        with self.assertRaises(self.api.RouteSchemaError):
            self.record()

    def test_same_revision_cannot_relabel_unit_or_changed_criterion(self):
        self.event = self.setup_owned()
        self.record()
        candidate = self.candidate(); candidate["work_units"][0]["kind"] = "nonexecution"
        with self.assertRaises(self.api.RouteSchemaError):
            self.record(candidate)
        ledger.record_turn(**self.coords, expected_input_event_id=self.event,
            intent_delta={"acceptance_criteria": [self.criterion(summary="changed meaning")]})
        self.assertEqual(self.api.read_response_route(**self.coords)["status"], "stale")
        with self.assertRaises(self.api.RouteSchemaError):
            self.record()
        ledger.record_turn(**self.coords, expected_input_event_id=self.event,
            intent_delta={"acceptance_criteria": [self.criterion(revision=2, summary="changed meaning")]})
        candidate = self.candidate()
        candidate["work_units"][0]["contract_revision"] = 2
        candidate["selected_criteria"][0]["contract_revision"] = 2
        self.assertEqual(self.record(candidate)["work_units"][0]["contract_revision"], 2)

    def test_empty_selection_is_current_reply_not_a_closure_exemption(self):
        self.event = self.setup_owned()
        candidate = self.candidate(units=())
        route = self.record(candidate)
        self.assertEqual(route["selected_criteria"], [])
        self.assertEqual(len(ledger.read_session_state(**self.coords)["acceptance_criteria"]), 3)

    def test_shared_transaction_reads_and_rollback_are_atomic(self):
        self.event = self.setup_owned()
        before = ledger.read_session_state(**self.coords)
        with self.assertRaisesRegex(RuntimeError, "outer rollback"):
            with ledger.storage_transaction(self.root) as tx:
                self.record(transaction=tx)
                self.assertEqual(self.api.read_response_route(**self.coords, transaction=tx)["status"], "ready")
                raise RuntimeError("outer rollback")
        self.assertEqual(ledger.read_session_state(**self.coords), before)

    def test_owned_met_writer_rejects_another_units_local_id(self):
        self.event = self.setup_owned()
        self.record()
        second = ledger.read_session_state(**self.coords)["acceptance_criteria"][1]
        with self.assertRaisesRegex(ValueError, "selected.*current.*route"):
            ledger.mark_acceptance_criterion_met(**self.coords, criterion_id="AC1", expected_criterion=second,
                expected_input_event_id=self.event, completion_check_digest="c" * 64)
        self.assertEqual(ledger.read_session_state(**self.coords)["acceptance_criteria"][1]["status"], "unmet")

    def test_compatibility_only_ready_export_cannot_authorize_a_route(self):
        self.event = self.setup_owned()
        self.record()
        copied = Path(self.tmp.name) / "compatibility-only"
        state = ledger.read_session_state(**self.coords)
        path = ledger.session_paths(copied, "codex", "current")["state"]
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(state))
        coords = dict(root=copied, platform="codex", session_id="current")
        self.assertIsNone(self.api.read_response_route(**coords))
        self.assertFalse(ledger.storage_database_path(copied).exists())
        with self.assertRaises(self.api.RouteBindingError):
            self.api.record_response_route(**coords, expected_input_event_id=self.event,
                candidate=self.candidate(), boundary_ref="boundary-1")
        self.assertIsNone(self.api.read_response_route(**coords))

    def test_legacy_import_requires_canonical_intake_before_route_registration(self):
        self.event = self.setup_owned()
        self.record()
        copied = Path(self.tmp.name) / "legacy-import"
        path = ledger.session_paths(copied, "codex", "current")["state"]
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(ledger.read_session_state(**self.coords)))
        coords = dict(root=copied, platform="codex", session_id="current")
        ledger.migrate_session(**coords)
        with self.assertRaises(self.api.RouteBindingError):
            self.api.record_response_route(**coords, expected_input_event_id=self.event,
                candidate=self.candidate(), boundary_ref="boundary-1")
        self.assertEqual(self.api.read_response_route(**coords)["status"], "stale")
        ledger.record_turn(**coords, raw_user_input="continue approved work", source="hook")
        fresh = ledger.read_session_state(**coords)["latest_input_event_id"]
        self.assertEqual(self.api.record_response_route(**coords, expected_input_event_id=fresh,
            candidate=self.candidate(), boundary_ref="boundary-1")["status"], "ready")

    def test_existing_canonical_sqlite_receipt_upgrades_without_new_observation(self):
        self.event = self.setup_owned()
        self.record()
        with ledger.storage_transaction(self.root) as tx:
            state = ledger.read_session_state(**self.coords, transaction=tx)
            state.pop("canonical_input_event_id")  # Genuine pre-upgrade SQLite shape.
            tx.execute("UPDATE sessions SET state_json=? WHERE platform=? AND session_id=?",
                       (json.dumps(state), "codex", "current"))
        before = ledger.read_session_events(**self.coords)
        self.assertEqual(self.api.read_response_route(**self.coords)["status"], "ready")
        self.assertEqual(self.record()["status"], "ready")
        after = ledger.read_session_events(**self.coords)
        self.assertEqual([e for e in after if e["event"] == "user-input-observed"],
                         [e for e in before if e["event"] == "user-input-observed"])
        self.assertEqual(ledger.read_session_state(**self.coords)["canonical_input_event_id"], self.event)

    def test_older_native_intake_after_import_upgrades_from_attested_event_boundary(self):
        self.event = self.setup_owned()
        self.record()
        copied = Path(self.tmp.name) / "older-import"
        paths = ledger.session_paths(copied, "codex", "current")
        paths["state"].parent.mkdir(parents=True)
        paths["state"].write_text(json.dumps(ledger.read_session_state(**self.coords)))
        paths["events"].write_text("".join(json.dumps(e) + "\n" for e in ledger.read_session_events(**self.coords)))
        coords = dict(root=copied, platform="codex", session_id="current")
        ledger.migrate_session(**coords)
        ledger.record_turn(**coords, raw_user_input="real older canonical input", source="hook")
        fresh = ledger.read_session_state(**coords)["latest_input_event_id"]
        self.api.record_response_route(**coords, expected_input_event_id=fresh,
            candidate=self.candidate(), boundary_ref="boundary-1")
        with ledger.storage_transaction(copied) as tx:
            state = ledger.read_session_state(**coords, transaction=tx)
            state.pop("canonical_input_event_id")
            tx.execute("UPDATE sessions SET state_json=? WHERE platform=? AND session_id=?",
                       (json.dumps(state), "codex", "current"))
        before = ledger.read_session_events(**coords)
        self.assertEqual(self.api.read_response_route(**coords)["status"], "ready")
        self.assertEqual(self.api.record_response_route(**coords, expected_input_event_id=fresh,
            candidate=self.candidate(), boundary_ref="boundary-1")["status"], "ready")
        self.assertEqual([e for e in ledger.read_session_events(**coords) if e["event"] == "user-input-observed"],
                         [e for e in before if e["event"] == "user-input-observed"])

    def test_ownerless_met_snapshot_only_matches_ownerless_local_id(self):
        self.event = self.setup_owned()
        ledger.record_turn(**self.coords, expected_input_event_id=self.event, intent_delta={
            "acceptance_criteria": [{"id": "AC1", "summary": "legacy AC1", "source": "user-explicit"}]})
        state = ledger.read_session_state(**self.coords)
        legacy = next(c for c in state["acceptance_criteria"] if c["id"] == "AC1" and "work_unit_id" not in c)
        try:
            ledger.mark_acceptance_criterion_met(**self.coords, criterion_id="AC1", expected_criterion=legacy,
                expected_input_event_id=self.event, completion_check_digest="d" * 64)
        except ValueError as exc:
            self.fail(f"ownerless snapshot must not collide with owned local IDs: {exc}")
        state = ledger.read_session_state(**self.coords)
        self.assertEqual([c["status"] for c in state["acceptance_criteria"] if c["id"] == "AC1"],
                         ["unmet", "unmet", "met"])

    def test_official_ledger_snapshot_exposes_detached_projection(self):
        self.event = self.setup_owned()
        self.record()
        state = ledger.read_session_state(**self.coords)
        snapshot = ledger.consumer_snapshot(state)
        snapshot["response_route"]["status"] = "changed by consumer"
        self.assertEqual(state["response_route"]["status"], "ready")
        self.assertTrue(any(e["event"] == "response-route-recorded" for e in ledger.read_session_events(**self.coords)))

    def test_cli_round_trip_uses_receipt_and_native_identity(self):
        self.event = self.setup_owned()
        script = ROOT / "_shared" / "response_route.py"
        command = [sys.executable, str(script), "--root", str(self.root), "--platform", "codex",
                   "--session-id", "current"]
        import os
        env = dict(os.environ, CODEX_THREAD_ID="current", GHOST_ALICE_SESSION_ID="")
        result = subprocess.run(command + ["--expected-input-event-id", self.event, "--boundary-ref", "boundary-1",
            "--candidate-json", json.dumps(self.candidate())], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        read = subprocess.run(command + ["--read"], capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(read.stdout)["status"], "ready")
        env["CODEX_THREAD_ID"] = "other"
        result = subprocess.run(command + ["--expected-input-event-id", self.event, "--candidate-json", "{}"],
                                capture_output=True, text=True, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["error"], "route-binding")

    def test_copied_skill_and_shared_helper_use_the_same_authority(self):
        self.event = self.setup_owned()
        install = Path(self.tmp.name) / "isolated-skills"
        (install / "session-intent-analyzer" / "scripts").mkdir(parents=True)
        (install / "_shared").mkdir()
        shutil.copy2(ROOT / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py",
                     install / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py")
        shutil.copy2(ROOT / "_shared" / "response_route.py", install / "_shared" / "response_route.py")
        script = install / "_shared" / "response_route.py"
        import os
        env = dict(os.environ, CODEX_THREAD_ID="current", GHOST_ALICE_SESSION_ID="")
        result = subprocess.run([sys.executable, str(script), "--root", str(self.root),
            "--platform", "codex", "--session-id", "current", "--expected-input-event-id", self.event,
            "--boundary-ref", "boundary-1", "--candidate-json", json.dumps(self.candidate())],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.api.read_response_route(**self.coords)["status"], "ready")

    def test_real_intake_hook_stales_route_without_erasing_evidence(self):
        self.event = self.setup_owned()
        self.record()
        criterion = ledger.read_session_state(**self.coords)["acceptance_criteria"][0]
        ledger.mark_acceptance_criterion_met(**self.coords, criterion_id="AC1", expected_criterion=criterion,
            expected_input_event_id=self.event, completion_check_digest="b" * 64)
        result = subprocess.run([sys.executable, str(ROOT / "_shared" / "session_intent_analyzer_hook.py"),
            "--root", str(self.root), "--platform", "codex", "--format", "json"],
            input=json.dumps({"session_id": "current", "prompt": "continue with the current task"}),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("session-intent-receipt", result.stdout)
        self.assertEqual(self.api.read_response_route(**self.coords)["status"], "stale")
        self.assertEqual(ledger.read_session_state(**self.coords)["acceptance_criteria"][0]
                         ["met_completion_check_digest"], "b" * 64)

    def test_read_rejects_wrong_input_receipt_and_unknown_stored_enum(self):
        self.event = self.setup_owned()
        self.record()
        with self.assertRaises(self.api.RouteBindingError):
            self.api.read_response_route(**self.coords, expected_input_event_id="old-event")
        with ledger.storage_transaction(self.root) as tx:
            state = ledger.read_session_state(**self.coords, transaction=tx)
            state["response_route"]["routing_surface"]["response_mode"] = "chat"
            tx.execute("UPDATE sessions SET state_json=? WHERE platform=? AND session_id=?",
                       (json.dumps(state), "codex", "current"))
        with self.assertRaises(self.api.RouteSchemaError):
            self.api.read_response_route(**self.coords)


if __name__ == "__main__":
    unittest.main()
