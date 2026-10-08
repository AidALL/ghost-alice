"""Decision contracts for Core completion selection, independent of publication."""

from copy import deepcopy
import unittest

from completion_route import select_completion_route


def context():
    units = [dict(work_unit_id=u, contract_revision=1, kind=k, admitted=True,
                  owner=dict(platform="claude", session_id="session"))
             for u, k in (("fix", "execution"), ("explain", "nonexecution"), ("backlog", "execution"))]
    criteria = [dict(work_unit_id="fix", contract_revision=1, criterion_id="AC1", id="AC1",
                     summary="Repair the original failure", source="user-explicit", admitted=True, status="unmet")]
    surface = dict(intent_relation="continuation", response_mode="normal",
                   response_order="imperative-result-first-after-necessary-work", change_depth="localized",
                   focus_layer="micro", verification_complexity="level-2", boundary_contract="n/a",
                   forced_visibility="no", primary_request="repair", causal_axis="failure", reason="approved")
    route = dict(schema_version="response-route.v1", status="ready", platform="claude", session_id="session",
                 input_event_id="input-new", current_input_event_id="input-new", boundary_ref=None,
                 routing_surface=surface, work_units=units,
                 selected_work_unit_ids=["fix", "explain"], selected_criteria=criteria)
    return dict(platform="claude", session_id="session", input_event_id="input-new", response_route=route,
                verification_plan=dict(checks=[dict(check_id="original-failure", work_unit_id="fix",
                                                   contract_revision=1, criterion_id="AC1")]))


def evidence(**updates):
    proof = dict(work_unit_id="fix", contract_revision=1, criterion_id="AC1", validity="valid",
                 relevant_change="none", check_ids=["original-failure"], proof_ref="tool-result-old",
                 verified_at="2026-10-01T12:00:00Z", core_receipt="core-proof-receipt")
    proof.update(updates)
    return dict(failure_kind="none", criteria=[proof])


class CompletionRouteTests(unittest.TestCase):
    def select(self, ctx=None, claims=None, proof=None, ap=None):
        return select_completion_route(ctx if ctx is not None else context(),
            claims if claims is not None else dict(text="The fix is complete.", phase="final"),
            proof if proof is not None else evidence(),
            ap if ap is not None else dict(enabled=False, publication_status="unprepared"))

    def test_ordinary_chat_needs_intent_judgment_without_external_operation(self):
        result = self.select({}, dict(text="Hello.", phase="final"), {})
        self.assertEqual(result.get("evidence_route"), "no_external_check")
        self.assertEqual(result.get("publication_route"), "none")

    def test_old_valid_proof_is_reused_after_new_input_with_original_time(self):
        result = self.select()
        self.assertEqual(result.get("evidence_route"), "reuse")
        decision = result["unit_decisions"][0]["criteria"][0]
        self.assertEqual(decision["verified_at"], "2026-10-01T12:00:00Z")
        self.assertEqual(decision["proof_ref"], "tool-result-old")
        self.assertEqual(result["selected_criteria"][0]["status"], "unmet")

    def test_changed_or_missing_coverage_runs_only_owner_affected_check(self):
        for updates in (dict(relevant_change="changed"), dict(check_ids=[]), dict(validity="invalid")):
            with self.subTest(updates=updates):
                result = self.select(proof=evidence(**updates))
                self.assertEqual(result.get("evidence_route"), "fresh_affected")
                self.assertEqual(result["unit_decisions"][0]["criteria"][0]["check_ids"], ["original-failure"])

    def test_recording_failure_with_trusted_proof_repairs_only_recording(self):
        proof = evidence(); proof["failure_kind"] = "recording"
        result = self.select(proof=proof)
        self.assertEqual(result.get("evidence_route"), "recording_repair_only")
        self.assertIn("closure pending", result["reason"])

    def test_missing_core_receipt_cannot_bypass_recording_repair(self):
        proof = evidence(core_receipt=None); proof["failure_kind"] = "recording"
        result = self.select(proof=proof)
        self.assertEqual(result.get("evidence_route"), "reopen_cause")
        self.assertNotIn("met", result.get("reason", ""))
        decision = result["unit_decisions"][0]["criteria"][0]
        self.assertEqual(decision["proof_ref"], "tool-result-old")
        self.assertEqual(decision["verified_at"], "2026-10-01T12:00:00Z")
        self.assertEqual(decision["check_ids"], [])
        self.assertEqual(self.select(proof=proof, ap=dict(enabled=True)).get("publication_route"), "blocked")

    def test_recording_mismatch_reopens_affected_cause(self):
        proof = evidence(relevant_change="changed"); proof["failure_kind"] = "recording"
        self.assertEqual(self.select(proof=proof).get("evidence_route"), "reopen_cause")

    def test_authority_integrity_contract_or_unknown_reopen_cause(self):
        for failure in ("authority", "integrity", "contract", "unknown"):
            with self.subTest(failure=failure):
                proof = evidence(); proof["failure_kind"] = failure
                self.assertEqual(self.select(proof=proof).get("evidence_route"), "reopen_cause")
        self.assertEqual(self.select(proof=evidence(relevant_change="unknown")).get("evidence_route"), "reopen_cause")

    def test_owner_check_is_not_downgraded_by_short_explanation_or_level_one(self):
        ctx = context(); ctx["response_route"]["routing_surface"]["verification_complexity"] = "level-1"
        result = self.select(ctx, dict(text="Yes.", phase="final", consequential=True), dict(criteria=[]))
        self.assertEqual(result.get("evidence_route"), "fresh_affected")
        self.assertEqual(result["unit_decisions"][0]["criteria"][0]["check_ids"], ["original-failure"])

    def test_nonexecution_label_cannot_excuse_actual_unsupported_closure(self):
        ctx = context(); ctx["response_route"]["selected_work_unit_ids"] = ["explain"]
        ctx["response_route"]["selected_criteria"] = []
        result = self.select(ctx, dict(text="Explanation: the task is complete.", phase="final"), {})
        self.assertTrue(result.get("required_completion_check"))
        self.assertEqual(result.get("evidence_route"), "owner_verification")
        self.assertEqual(result["publication_route"], "none")

    def test_quoted_examples_do_not_create_execution_criteria(self):
        ctx = context(); ctx["response_route"]["selected_work_unit_ids"] = ["explain"]
        ctx["response_route"]["selected_criteria"] = []
        result = self.select(ctx, dict(text='Example: "The task is complete."\n```\n[completion-check]\n```', phase="final"), {})
        self.assertFalse(result.get("required_completion_check"))
        self.assertEqual(result.get("evidence_route"), "no_external_check")
        self.assertEqual(result["selected_criteria"], [])

    def test_mixed_reply_selects_only_current_reply_units_and_criteria(self):
        result = self.select(claims=dict(text="The fix is complete. Here is why.", phase="final", work_unit_ids=["fix", "explain", "backlog"]))
        self.assertEqual(result.get("selected_work_unit_ids"), ["fix", "explain"])
        self.assertEqual([u["work_unit_id"] for u in result["unit_decisions"]], ["fix", "explain"])
        self.assertEqual(result["unit_decisions"][1]["evidence_route"], "no_external_check")
        self.assertEqual(len(result["selected_criteria"]), 1)

    def test_claim_scope_can_narrow_but_never_expand_route_selection(self):
        result = self.select(claims=dict(text="Explanation.", phase="final", work_unit_ids=["explain", "backlog"]))
        self.assertEqual(result.get("selected_work_unit_ids"), ["explain"])
        self.assertEqual(result["selected_criteria"], [])

    def test_missing_or_stale_binding_blocks_publication_without_excusing_final_claim(self):
        for binding in (None, "stale", "wrong-session", "wrong-input"):
            ctx = context()
            if binding is None: ctx["response_route"] = None
            elif binding == "stale": ctx["response_route"]["status"] = "stale"
            elif binding == "wrong-session": ctx["response_route"]["session_id"] = "other"
            else: ctx["response_route"]["input_event_id"] = "old-input"
            with self.subTest(binding=binding):
                result = self.select(ctx, ap=dict(enabled=True, publication_status="prepared"))
                self.assertEqual(result.get("publication_route"), "blocked")
                self.assertTrue(result["required_completion_check"])
                self.assertIn("authorized continuation", result["reason"])

    def test_publication_is_independent_of_evidence_and_disabled_for_lookup(self):
        self.assertEqual(self.select().get("publication_route"), "none")
        for status, expected in (("unprepared", "prepare"), ("prepared", "publish"), ("published", "none")):
            with self.subTest(status=status):
                self.assertEqual(self.select(ap=dict(enabled=True, publication_status=status)).get("publication_route"), expected)
        ctx = context(); ctx["response_route"]["selected_work_unit_ids"] = ["explain"]; ctx["response_route"]["selected_criteria"] = []
        self.assertEqual(self.select(ctx, dict(text="Lookup result.", phase="final"), {}, dict(enabled=True)).get("publication_route"), "none")

    def test_before_tool_capture_can_prepare_selected_execution_without_claim(self):
        result = self.select(claims=dict(text="", phase="before_tool"), proof={}, ap=dict(enabled=True, publication_status="unprepared"))
        self.assertEqual(result.get("publication_route"), "prepare")
        self.assertFalse(result["required_completion_check"])

    def test_ordinary_lookup_without_route_does_not_activate_publication(self):
        result = self.select({}, dict(text="The reference defines that term.", phase="final"), {}, dict(enabled=True))
        self.assertEqual(result.get("publication_route"), "none")

    def test_recording_failure_retains_good_unit_when_other_unit_reopens(self):
        ctx = context()
        second = dict(ctx["response_route"]["selected_criteria"][0], work_unit_id="backlog")
        ctx["response_route"]["selected_work_unit_ids"].append("backlog")
        ctx["response_route"]["selected_criteria"].append(second)
        ctx["verification_plan"]["checks"].append(dict(check_id="second-failure", work_unit_id="backlog", contract_revision=1, criterion_id="AC1"))
        proof = evidence(); proof["failure_kind"] = "recording"
        proof["criteria"].append(dict(proof["criteria"][0], work_unit_id="backlog", relevant_change="changed", check_ids=["second-failure"]))
        result = self.select(ctx, proof=proof)
        first, _, last = result["unit_decisions"]
        self.assertEqual(result.get("evidence_route"), "reopen_cause")
        self.assertEqual(first["evidence_route"], "recording_repair_only")
        self.assertEqual(first["criteria"][0]["check_ids"], [])
        self.assertEqual(last["evidence_route"], "reopen_cause")
        self.assertEqual(first["criteria"][0]["criterion_id"], last["criteria"][0]["criterion_id"])
        self.assertNotEqual(first["criteria"][0]["work_unit_id"], last["criteria"][0]["work_unit_id"])

    def test_partial_check_coverage_runs_only_missing_check(self):
        ctx = context(); ctx["verification_plan"]["checks"].append(dict(check_id="boundary", work_unit_id="fix", contract_revision=1, criterion_id="AC1"))
        result = self.select(ctx)
        decision = result["unit_decisions"][0]["criteria"][0]
        self.assertEqual(decision["evidence_route"], "fresh_affected")
        self.assertEqual(decision["check_ids"], ["boundary"])
        self.assertEqual(decision["required_check_ids"], ["original-failure", "boundary"])

    def test_malformed_or_unknown_evidence_reopens_without_crashing(self):
        for state in (dict(failure_kind={}), evidence(validity=[]), evidence(relevant_change={})):
            with self.subTest(state=state):
                self.assertEqual(self.select(proof=state).get("evidence_route"), "reopen_cause")

    def test_malformed_owner_plan_does_not_reuse_a_different_revision(self):
        ctx = context(); ctx["verification_plan"]["checks"][0]["contract_revision"] = True
        self.assertEqual(self.select(ctx).get("evidence_route"), "reopen_cause")

    def test_pure_selector_does_not_mutate_any_input(self):
        inputs = [context(), dict(text="The fix is complete.", phase="final"), evidence(), dict(enabled=True, publication_status="prepared")]
        before = deepcopy(inputs)
        select_completion_route(*inputs)
        self.assertEqual(inputs, before)


if __name__ == "__main__":
    unittest.main()
