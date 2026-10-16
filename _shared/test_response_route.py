"""Core response projection tests. Dependencies: Python 3.11+ standard library."""

from __future__ import annotations

import importlib.util
import json
import os
from copy import deepcopy
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
    def test_minimal_surface_round_trips_without_invented_judgments(self):
        self.event = self.setup_owned()
        for mode, units, boundary in (("direct-response", (), "n/a"), ("normal", ("first",), "required")):
            with self.subTest(mode=mode):
                candidate = self.candidate(units=units)
                candidate["routing_surface"] = dict(response_mode=mode, boundary_contract=boundary)
                candidate["response_purpose"] = "explanation" if not units else "execution"
                self.record(candidate)
                route = self.api.read_response_route(**self.coords)
                self.assertEqual(route["routing_surface"], candidate["routing_surface"])
                self.assertEqual(route["selected_work_unit_ids"], list(units))
                self.assertEqual([c["work_unit_id"] for c in route["selected_criteria"]], list(units))

    def test_minimal_surface_keeps_controls_and_validates_supplied_judgments(self):
        self.event = self.setup_owned()
        minimal = dict(response_mode="normal", boundary_contract="required")
        invalid = [dict(minimal, focus_layer="wide"), dict(minimal, causal_axis=None),
                   dict(minimal, verification_complexity=[]), dict(minimal, reason=" "),
                   dict(minimal, unexpected="value"), {"response_mode": "normal"},
                   {"boundary_contract": "required"}, dict(minimal, response_mode="direct-response",
                    response_order="imperative-result-first-after-necessary-work")]
        before = ledger.read_session_state(**self.coords)
        for surface in invalid:
            with self.subTest(surface=surface):
                candidate = self.candidate(); candidate["routing_surface"] = surface
                with self.assertRaises(self.api.RouteSchemaError):
                    self.record(candidate)
                self.assertEqual(ledger.read_session_state(**self.coords), before)
        candidate = self.candidate(); candidate["routing_surface"] = minimal
        with self.assertRaisesRegex(self.api.RouteSchemaError, "boundary_ref"):
            self.api.validate_response_route(before, candidate, None)

    def test_runtime_cli_uses_the_explicit_platform_installation(self):
        self.event = self.setup_owned()
        runtime = Path(self.tmp.name) / "runtime" / "_shared"
        runtime.mkdir(parents=True)
        installed = Path(os.environ.get("GHOST_ALICE_TEST_SHARED_ROOT", ROOT / "_shared"))
        for name in ("response_route.py", "response_purpose.py"):
            shutil.copy2(installed / name, runtime / name)
        ghost = Path(self.tmp.name) / "ghost"
        manifest = ghost / "install-state" / "codex.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps(dict(platform="codex", source_root=str(ROOT))))
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("GHOST_ALICE_") and k not in ("CODEX_THREAD_ID", "PYTHONPATH")}
        env.update(GHOST_ALICE_GHOST_ROOT=str(ghost), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", str(runtime / "response_route.py"),
            "--root", str(self.root), "--platform", "codex", "--session-id", "current",
            "--expected-input-event-id", self.event, "--boundary-ref", "test-boundary",
            "--candidate-json", json.dumps(self.candidate())], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ready")
        self.assertEqual(self.api.read_response_route(**self.coords)["input_event_id"], self.event)

    def test_purpose_changes_without_rewriting_work_contract_or_proof(self):
        before = self.legacy_registered()
        self.bind_legacy(before)
        self.record()
        before = ledger.read_session_state(**self.coords)
        for purpose in ("explanation", "approval-wait", "partial", "completion", "execution"):
            with self.subTest(purpose=purpose):
                candidate = self.candidate(); candidate["response_purpose"] = purpose
                self.record(candidate)
                route = self.api.read_response_route(**self.coords)
                self.assertEqual(route["response_purpose"], purpose)
                after = ledger.read_session_state(**self.coords)
                self.assertEqual(after["work_units"], before["work_units"])
                self.assertEqual(after["acceptance_criteria"], before["acceptance_criteria"])
                self.assertEqual(route["selected_work_unit_ids"], ["first"])

    def test_legacy_route_defaults_to_execution_and_unknown_purpose_is_rejected(self):
        self.event = self.setup_owned()
        self.record()
        self.assertEqual(self.api.read_response_route(**self.coords)["response_purpose"], "execution")
        before = ledger.read_session_state(**self.coords)
        candidate = self.candidate(); candidate["response_purpose"] = "skip-checks"
        with self.assertRaises(self.api.RouteSchemaError):
            self.record(candidate)
        self.assertEqual(ledger.read_session_state(**self.coords), before)

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
        return dict(id="AC1", summary=summary or f"verify {unit}", source="user-said",
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
                dict(id="old", summary="historical unbound", source="user-said")]})
        return event

    def record(self, candidate=None, event=None, **extra):
        self.assertIsNotNone(self.api, "Core must supply the response route API")
        return self.api.record_response_route(**self.coords, expected_input_event_id=event or self.event,
                                             candidate=candidate or self.candidate(), boundary_ref="boundary-1", **extra)

    def legacy_registered(self, *, duplicate=False, foreign=False, superseded=False, collision=False, native=True):
        row = dict(id="AC1", summary="verify first", source="user-explicit", admitted=True,
                   status="met", met_completion_check_digest="a" * 64, met_at="2026-10-01T12:00:00Z")
        state = ledger.default_state("codex", "current")
        state["acceptance_criteria"] = [row, dict(id="extra", summary="Unselected condition",
            source="user-explicit", admitted=False, status="unmet")]
        if duplicate:
            state["acceptance_criteria"].append(deepcopy(row))
        if collision:
            state["acceptance_criteria"].append(dict(row, work_unit_id="first", contract_revision=1))
        unit = dict(self.candidate()["work_units"][0], criteria=[{k: row[k] for k in ("id", "summary", "source")}])
        if foreign:
            unit["owner"]["session_id"] = "other"
        state["work_units"] = [unit]
        if superseded:
            state["work_units"].append(dict(deepcopy(unit), contract_revision=2))
        if not native:
            state.update(latest_input_event_id="copied-input", latest_input_digest="sha256:" + "b" * 64,
                         latest_input_char_count=7, canonical_input_event_id="copied-input")
        path = ledger.session_paths(self.root, "codex", "current")["state"]
        ledger.write_json(path, state)
        ledger.migrate_session(**self.coords)
        self.event = self.intake("Continue the existing registered condition") if native else "copied-input"
        return ledger.read_session_state(**self.coords)

    def bind_legacy(self, before, **changes):
        self.assertTrue(callable(getattr(ledger, "bind_legacy_acceptance_criteria", None)),
                        "Core needs an explicit in-place ownership binding API")
        args = dict(**self.coords, work_unit_id="first", contract_revision=1,
            expected_input_event_id=self.event, expected_revision=before["ledger_revision"],
            expected_criteria=[deepcopy(before["acceptance_criteria"][0])])
        args.update(changes)
        return ledger.bind_legacy_acceptance_criteria(**args)

    def test_binding_preserves_rows_proof_and_admission_before_current_route(self):
        before = self.legacy_registered()
        with self.assertRaisesRegex(self.api.RouteSchemaError, "criterion definition"):
            self.record()
        with ledger.storage_transaction(self.root) as connection:
            self.bind_legacy(before, transaction=connection)
            self.record(transaction=connection)
        after = ledger.read_session_state(**self.coords)
        self.assertEqual(len(after["acceptance_criteria"]), len(before["acceptance_criteria"]))
        bound = deepcopy(after["acceptance_criteria"][0])
        self.assertEqual(bound.pop("work_unit_id"), "first")
        self.assertEqual(bound.pop("contract_revision"), 1)
        self.assertEqual(bound, before["acceptance_criteria"][0])
        self.assertEqual(after["acceptance_criteria"][1:], before["acceptance_criteria"][1:])
        self.assertEqual(after["work_units"], before["work_units"])
        self.assertEqual(self.api.read_response_route(**self.coords)["status"], "ready")
        events = ledger.read_session_events(**self.coords)
        event = next(e for e in events if e["event"] == "acceptance-criteria-ownership-bound")
        self.assertEqual(event["criterion_ids"], ["AC1"])
        self.assertNotIn("proof", event)

    def test_stored_legacy_source_survives_a_later_criterion_write(self):
        before = self.legacy_registered()
        with ledger.storage_transaction(self.root) as connection:
            self.bind_legacy(before, transaction=connection)
            self.record(transaction=connection)
        ledger.record_turn(**self.coords, expected_input_event_id=self.event, intent_delta={
            "acceptance_criteria": [dict(id="AC9", summary="added after the upgrade", source="user-said")]})
        after = ledger.read_session_state(**self.coords)
        self.assertEqual(after["acceptance_criteria"][0]["source"], "user-explicit")
        self.record()
        self.assertEqual(self.api.read_response_route(**self.coords)["status"], "ready")

    def test_binding_rejects_ambiguity_foreign_revision_and_stale_full_snapshot(self):
        before = self.legacy_registered()
        cases = [dict(expected_input_event_id="old"), dict(expected_revision=before["ledger_revision"] + 1),
                 dict(expected_revision=None), dict(expected_revision=True),
                 dict(expected_criteria=[dict(before["acceptance_criteria"][0], met_at="changed")]),
                 dict(expected_criteria=[dict(before["acceptance_criteria"][0], admitted=False)]),
                 dict(contract_revision=2)]
        for change in cases:
            events = ledger.read_session_events(**self.coords)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.bind_legacy(before, **change)
            self.assertEqual(ledger.read_session_state(**self.coords), before)
            self.assertEqual(ledger.read_session_events(**self.coords), events)
        for fixture in (dict(duplicate=True), dict(foreign=True), dict(superseded=True), dict(collision=True)):
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp) / "ledger"; self.coords["root"] = self.root
                before = self.legacy_registered(**fixture)
                events = ledger.read_session_events(**self.coords)
                with self.assertRaises(ValueError): self.bind_legacy(before)
                self.assertEqual(ledger.read_session_state(**self.coords), before)
                self.assertEqual(ledger.read_session_events(**self.coords), events)

    def test_binding_and_invalid_route_roll_back_as_one_transaction(self):
        before = self.legacy_registered(); events = ledger.read_session_events(**self.coords)
        candidate = self.candidate(); candidate["selected_criteria"][0]["criterion_id"] = "extra"
        with self.assertRaises(self.api.RouteSchemaError):
            with ledger.storage_transaction(self.root) as connection:
                self.bind_legacy(before, transaction=connection)
                self.record(candidate, transaction=connection)
        self.assertEqual(ledger.read_session_state(**self.coords), before)
        self.assertEqual(ledger.read_session_events(**self.coords), events)

    def test_binding_does_not_trust_a_copied_input_header(self):
        before = self.legacy_registered(native=False)
        events = ledger.read_session_events(**self.coords)
        with self.assertRaisesRegex(self.api.RouteBindingError, "canonical"):
            self.bind_legacy(before)
        self.assertEqual(ledger.read_session_state(**self.coords), before)
        self.assertEqual(ledger.read_session_events(**self.coords), events)

    def test_cli_binds_existing_conditions_and_returns_ready_route_atomically(self):
        before = self.legacy_registered()
        binding = dict(work_unit_id="first", contract_revision=1,
                       expected_criteria=[before["acceptance_criteria"][0]])
        args = [sys.executable, str(ROOT / "_shared/response_route.py"), "--root", str(self.root),
            "--platform", "codex", "--session-id", "current", "--expected-input-event-id", self.event,
            "--expected-revision", str(before["ledger_revision"]), "--boundary-ref", "boundary-1",
            "--bind-legacy-criteria-json", json.dumps(binding), "--candidate-json", json.dumps(self.candidate())]
        result = subprocess.run(args, text=True, capture_output=True,
            env={"PATH": str(Path(sys.executable).parent), "CODEX_THREAD_ID": "current"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ready")
        self.assertEqual(len(ledger.read_session_state(**self.coords)["acceptance_criteria"]), 2)
        state = ledger.read_session_state(**self.coords); events = ledger.read_session_events(**self.coords)
        for changes in (("--read",), ("foreign-native",)):
            with self.subTest(changes=changes):
                invocation = args[:]
                env = {"PATH": str(Path(sys.executable).parent), "CODEX_THREAD_ID": "current"}
                if changes == ("--read",):
                    index = invocation.index("--candidate-json")
                    invocation[index:index + 2] = ["--read"]
                else:
                    env["CODEX_THREAD_ID"] = "foreign"
                result = subprocess.run(invocation, text=True, capture_output=True, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(ledger.read_session_state(**self.coords), state)
                self.assertEqual(ledger.read_session_events(**self.coords), events)

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

    def test_registration_rejects_malformed_owned_criteria_with_typed_errors(self):
        self.event = self.setup_owned()
        baseline = ledger.read_session_state(**self.coords)
        variants = [None, 1]
        missing_summary = deepcopy(baseline["acceptance_criteria"])
        missing_summary[0].pop("summary")
        variants.append(missing_summary)
        invalid_status = deepcopy(baseline["acceptance_criteria"])
        invalid_status[0]["status"] = "invalid"
        variants.append(invalid_status)
        for criteria in variants:
            with self.subTest(criteria=criteria):
                bad_state = deepcopy(baseline)
                bad_state["acceptance_criteria"] = criteria
                with ledger.storage_transaction(self.root) as tx:
                    tx.execute("UPDATE sessions SET state_json=? WHERE platform=? AND session_id=?",
                               (json.dumps(bad_state), "codex", "current"))
                events = ledger.read_session_events(**self.coords)
                try:
                    self.record()
                except self.api.RouteSchemaError:
                    pass
                except Exception as exc:
                    self.fail(f"typed RouteSchemaError required, got {type(exc).__name__}: {exc}")
                else:
                    self.fail("malformed criteria committed an unreadable ready projection")
                self.assertEqual(ledger.read_session_state(**self.coords), bad_state)
                self.assertEqual(ledger.read_session_events(**self.coords), events)

    def test_reader_uses_the_same_owned_criterion_schema(self):
        self.event = self.setup_owned()
        self.record()
        baseline = ledger.read_session_state(**self.coords)
        variants = [None, 1]
        missing_summary = deepcopy(baseline["acceptance_criteria"])
        missing_summary[0].pop("summary")
        variants.append(missing_summary)
        invalid_status = deepcopy(baseline["acceptance_criteria"])
        invalid_status[0]["status"] = "invalid"
        variants.append(invalid_status)
        for criteria in variants:
            with self.subTest(criteria=criteria):
                bad_state = deepcopy(baseline)
                bad_state["acceptance_criteria"] = criteria
                with ledger.storage_transaction(self.root) as tx:
                    tx.execute("UPDATE sessions SET state_json=? WHERE platform=? AND session_id=?",
                               (json.dumps(bad_state), "codex", "current"))
                try:
                    self.api.read_response_route(**self.coords)
                except self.api.RouteSchemaError:
                    pass
                except Exception as exc:
                    self.fail(f"typed RouteSchemaError required, got {type(exc).__name__}: {exc}")
                else:
                    self.fail("reader accepted malformed owned criterion state")

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

    def test_terminal_route_rejects_selected_execution_without_mutating_ledger(self):
        self.event = self.setup_owned()
        before = ledger.read_session_state(**self.coords)
        for mode, order in (("direct-response", "resolved-intent-first"),
                            ("clarification-only", "clarification-question-only")):
            with self.subTest(mode=mode):
                candidate = self.candidate()
                candidate["routing_surface"].update(response_mode=mode, response_order=order)
                with self.assertRaisesRegex(self.api.RouteSchemaError, "terminal.*execution"):
                    self.record(candidate)
                self.assertEqual(ledger.read_session_state(**self.coords), before)

    def test_terminal_route_may_retain_unselected_execution_backlog(self):
        self.event = self.setup_owned()
        candidate = self.candidate()
        candidate["routing_surface"].update(response_mode="direct-response", response_order="resolved-intent-first")
        candidate["selected_work_unit_ids"] = []
        candidate["selected_criteria"] = []
        route = self.record(candidate)
        self.assertEqual(route["status"], "ready")
        self.assertEqual(route["selected_work_unit_ids"], [])
        self.assertEqual(len(ledger.read_session_state(**self.coords)["acceptance_criteria"]), 3)

    def test_reader_rejects_terminal_projection_with_selected_execution(self):
        self.event = self.setup_owned()
        self.record()
        state = ledger.read_session_state(**self.coords)
        state["response_route"]["routing_surface"].update(
            response_mode="direct-response", response_order="resolved-intent-first")
        with self.assertRaisesRegex(self.api.RouteSchemaError, "terminal.*execution"):
            self.api.project_stored_response_route(state)

    def test_explanation_or_stop_clears_current_execution_without_reopening_proof(self):
        from completion_route import select_completion_route
        self.event = self.setup_owned()
        self.record()
        criterion = ledger.read_session_state(**self.coords)["acceptance_criteria"][0]
        ledger.mark_acceptance_criterion_met(**self.coords, criterion_id="AC1", expected_criterion=criterion,
            expected_input_event_id=self.event, completion_check_digest="a" * 64)
        preserved = deepcopy(ledger.read_session_state(**self.coords)["acceptance_criteria"])
        for request in ("Explain the pwd result already returned; do not run it again.",
                        "Stop execution. Only explain the existing result."):
            with self.subTest(request=request):
                event = self.intake(request)
                self.assertEqual(self.api.read_response_route(**self.coords)["status"], "stale")
                candidate = self.candidate(units=())
                candidate["routing_surface"].update(response_mode="direct-response", response_order="resolved-intent-first")
                self.record(candidate, event=event)
                route = self.api.read_response_route(**self.coords)
                result = select_completion_route(
                    dict(platform="codex", session_id="current", input_event_id=event, response_route=route),
                    dict(text="The returned path names the working directory.", phase="final"),
                    {}, dict(enabled=True, publication_status="prepared"))
                self.assertEqual(result["publication_route"], "none")
                self.assertEqual(result["evidence_route"], "no_external_check")
                self.assertEqual(result["selected_criteria"], [])
                self.assertEqual(ledger.read_session_state(**self.coords)["acceptance_criteria"], preserved)

    def test_readonly_file_lookup_is_normal_execution_with_owner_verification(self):
        from completion_route import select_completion_route
        self.event = self.intake("Find the file in the requested directory.")
        ledger.record_turn(**self.coords, expected_input_event_id=self.event, intent_delta={
            "acceptance_criteria": [self.criterion(summary="Identify the requested file from the directory lookup")]})
        candidate = self.candidate()
        candidate["routing_surface"].update(primary_request="find the requested file", reason="authorized directory lookup")
        self.record(candidate)
        route = self.api.read_response_route(**self.coords)
        key = dict(work_unit_id="first", contract_revision=1, criterion_id="AC1")
        result = select_completion_route(
            dict(platform="codex", session_id="current", input_event_id=self.event, response_route=route,
                 verification_plan=dict(checks=[dict(key, check_id="directory-lookup")])),
            dict(text="", phase="before_tool"), {}, dict(enabled=True, publication_status="unprepared"))
        self.assertEqual(result["publication_route"], "prepare")
        self.assertEqual(result["evidence_route"], "fresh_affected")
        self.assertEqual(result["unit_decisions"][0]["criteria"][0]["check_ids"], ["directory-lookup"])

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
            state.pop("canonical_input_event_id")  # SQLite fixture with no response projection tables.
            tx.execute("DROP TABLE canonical_intakes")
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
            tx.execute("DROP TABLE canonical_intakes")
            tx.execute("UPDATE sessions SET state_json=? WHERE platform=? AND session_id=?",
                       (json.dumps(state), "codex", "current"))
        before = ledger.read_session_events(**coords)
        self.assertEqual(self.api.read_response_route(**coords)["status"], "ready")
        self.assertEqual(self.api.record_response_route(**coords, expected_input_event_id=fresh,
            candidate=self.candidate(), boundary_ref="boundary-1")["status"], "ready")
        self.assertEqual([e for e in ledger.read_session_events(**coords) if e["event"] == "user-input-observed"],
                         [e for e in before if e["event"] == "user-input-observed"])

    def test_older_import_preserved_json_stamp_cannot_authorize_current_route(self):
        self.event = self.setup_owned()
        self.record()
        copied = Path(self.tmp.name) / "older-preserved-stamp"
        paths = ledger.session_paths(copied, "codex", "current")
        paths["state"].parent.mkdir(parents=True)
        exported = ledger.read_session_state(**self.coords)
        paths["state"].write_text(json.dumps(exported))
        paths["events"].write_text("".join(json.dumps(e) + "\n" for e in ledger.read_session_events(**self.coords)))
        coords = dict(root=copied, platform="codex", session_id="current")
        ledger.migrate_session(**coords)
        # Imported accessory JSON fields remain available for inspection.
        # Restore that exact persisted shape after importing its actual audit.
        with ledger.storage_transaction(copied) as tx:
            imported = ledger.read_session_state(**coords, transaction=tx)
            imported["canonical_input_event_id"] = exported["canonical_input_event_id"]
            imported["response_route"] = exported["response_route"]
            tx.execute("UPDATE sessions SET state_json=? WHERE platform=? AND session_id=?",
                       (json.dumps(imported), "codex", "current"))
        self.assertEqual(self.api.read_response_route(**coords)["status"], "stale")
        with self.assertRaises(self.api.RouteBindingError):
            self.api.record_response_route(**coords, expected_input_event_id=self.event,
                candidate=self.candidate(), boundary_ref="boundary-1")

    def test_trusted_intake_record_must_match_current_digest_count_and_sequence(self):
        self.event = self.setup_owned()
        self.record()
        with ledger.storage_transaction(self.root) as tx:
            row = tx.execute("SELECT input_event_id,input_digest,input_char_count,event_sequence FROM canonical_intakes WHERE platform=? AND session_id=?",
                             ("codex", "current")).fetchone()
            self.assertEqual(row[:3], (self.event, ledger.read_session_state(**self.coords, transaction=tx)["latest_input_digest"],
                                      ledger.read_session_state(**self.coords, transaction=tx)["latest_input_char_count"]))
        for column, invalid in (("input_digest", "sha256:" + "f" * 64),
                                ("input_char_count", row[2] + 1), ("event_sequence", row[3] + 1)):
            with self.subTest(column=column):
                with ledger.storage_transaction(self.root) as tx:
                    tx.execute(f"UPDATE canonical_intakes SET {column}=? WHERE platform=? AND session_id=?",
                               (invalid, "codex", "current"))
                self.assertEqual(self.api.read_response_route(**self.coords)["status"], "stale")
                with self.assertRaises(self.api.RouteBindingError):
                    self.record()
                with ledger.storage_transaction(self.root) as tx:
                    tx.execute("UPDATE canonical_intakes SET input_event_id=?,input_digest=?,input_char_count=?,event_sequence=? WHERE platform=? AND session_id=?",
                               (*row, "codex", "current"))

    def test_native_intake_record_and_event_roll_back_together(self):
        self.event = self.setup_owned()
        before = ledger.read_session_state(**self.coords)
        with self.assertRaisesRegex(RuntimeError, "rollback native intake"):
            with ledger.storage_transaction(self.root) as tx:
                ledger.record_turn(**self.coords, raw_user_input="new input to roll back", transaction=tx)
                row = tx.execute("SELECT input_event_id FROM canonical_intakes WHERE platform=? AND session_id=?",
                                 ("codex", "current")).fetchone()
                self.assertNotEqual(row[0], self.event)
                raise RuntimeError("rollback native intake")
        self.assertEqual(ledger.read_session_state(**self.coords), before)
        with ledger.storage_transaction(self.root) as tx:
            row = tx.execute("SELECT input_event_id FROM canonical_intakes WHERE platform=? AND session_id=?",
                             ("codex", "current")).fetchone()
            self.assertEqual(row[0], self.event)

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
        shutil.copy2(ROOT / "_shared" / "response_purpose.py", install / "_shared" / "response_purpose.py")
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


class RouteContextUpdateTests(unittest.TestCase):
    def setUp(self):
        import test_codex_context_updates as fixtures
        self.updates = fixtures.CodexContextUpdateTests()
        self.updates.setUp()
        self.addCleanup(self.updates.doCleanups)
        self.route = self.updates.fixture.fixture

    def publish(self, choices, **extra):
        return self.route.record(context_selection=choices, context_source=self.updates.fixture.source, **extra)

    def test_required_route_can_update_second_source_without_restoring_first(self):
        result = self.publish([self.updates.second], context_selection_mode='update')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['context_disposition']['selection_mode'], 'update')
        self.updates.assert_both_omitted(self.updates.select())

    def test_route_targeted_keep_restores_only_named_original(self):
        self.publish([self.updates.second], context_selection_mode='update')
        self.publish([dict(self.updates.first, disposition='keep')], context_selection_mode='update')
        result = self.updates.select()
        self.assertEqual(result.request['input'][2], self.updates.fixture.output)
        self.assertEqual(result.request['input'][-1]['output'], '')

    def test_route_empty_update_retains_choices_and_default_empty_refresh_clears(self):
        self.publish([self.updates.second], context_selection_mode='update')
        self.publish([], context_selection_mode='update')
        self.updates.assert_both_omitted(self.updates.select())
        self.publish([])
        self.assertEqual(self.updates.select().request, self.updates.fixture.request)

    def test_default_route_selection_still_refreshes_the_complete_selection(self):
        self.publish([self.updates.second])
        result = self.updates.select()
        self.assertEqual(result.request['input'][2], self.updates.fixture.output)
        self.assertEqual(result.request['input'][-1]['output'], '')

    def test_invalid_retained_origin_rolls_back_required_route_and_content_update(self):
        import context_lifecycle as lifecycle
        self.updates.corrupt_retained_file_reference(lambda original: original.update(storage='unclassified-inline',
            text=self.updates.fixture.log.read_text()), select_second=True)
        before = ledger.read_session_state(**self.route.coords)
        disposition_before = self.updates.current_payload()
        candidate = self.route.candidate()
        candidate['routing_surface']['reason'] = 'new route must roll back with rejected content update'
        with self.assertRaises(lifecycle.ContextSelectionRejected):
            self.publish([self.updates.second], candidate=candidate, context_selection_mode='update')
        self.assertEqual(ledger.read_session_state(**self.route.coords), before)
        self.assertEqual(self.updates.current_payload(), disposition_before)

    def test_unknown_mode_or_update_without_selection_rejects_before_route_write(self):
        before = ledger.read_session_state(**self.route.coords)
        for mode in ('unsupported', None, False, []):
            with self.subTest(mode=mode), self.assertRaises(self.route.api.RouteSchemaError):
                self.route.record(context_selection_mode=mode)
            self.assertEqual(ledger.read_session_state(**self.route.coords), before)
        with self.assertRaises(self.route.api.RouteBindingError):
            self.route.record(context_selection_mode='update')
        self.assertEqual(ledger.read_session_state(**self.route.coords), before)

    def test_claude_source_local_mode_rejects_before_any_route_write(self):
        before = ledger.read_session_state(**self.route.coords)
        with self.assertRaises(self.route.api.RouteBindingError):
            self.route.api.record_response_route(root=self.route.root, platform='claude', session_id='current',
                expected_input_event_id=self.route.event, candidate=self.route.candidate(), boundary_ref='boundary-1',
                context_selection=[], context_selection_mode='update')
        self.assertEqual(ledger.read_session_state(**self.route.coords), before)

    def test_existing_route_cli_combines_current_work_and_source_local_update(self):
        from contextlib import redirect_stdout
        import io
        from unittest.mock import patch
        output = io.StringIO()
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'current',
            'GHOST_ALICE_CODEX_DIR': str(self.updates.fixture.home)}), redirect_stdout(output):
            status = self.route.api.main(['--root', str(self.route.root), '--platform', 'codex',
                '--session-id', 'current', '--expected-input-event-id', self.route.event,
                '--boundary-ref', 'boundary-1', '--candidate-json', json.dumps(self.route.candidate()),
                '--context-selection-mode', 'update', '--context-selection-json', json.dumps([self.updates.second])])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())['context_disposition']['selection_mode'], 'update')
        self.updates.assert_both_omitted(self.updates.select())

    def test_read_cli_cannot_apply_source_local_selection_mode(self):
        from contextlib import redirect_stderr
        import io
        errors = io.StringIO()
        before = ledger.read_session_state(**self.route.coords)
        with redirect_stderr(errors):
            status = self.route.api.main(['--root', str(self.route.root), '--platform', 'codex',
                '--session-id', 'current', '--read', '--context-selection-mode', 'update'])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(errors.getvalue())['error'], 'route-binding')
        self.assertEqual(ledger.read_session_state(**self.route.coords), before)


if __name__ == "__main__":
    unittest.main()
