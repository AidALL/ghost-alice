"""Decision contracts for Core completion selection."""

from copy import deepcopy
import unittest

from completion_route import select_completion_route


def context():
    units = [dict(work_unit_id=u, contract_revision=1, kind=k, admitted=True,
                  owner=dict(platform="claude", session_id="session"))
             for u, k in (("fix", "execution"), ("explain", "nonexecution"), ("backlog", "execution"))]
    criteria = [dict(work_unit_id="fix", contract_revision=1, criterion_id="AC1", id="AC1",
                     summary="Repair the original failure", source="user-said", admitted=True, status="unmet")]
    surface = dict(response_mode="normal", boundary_contract="n/a", reason="approved")
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


class PartialEvidenceRouteTests(unittest.TestCase):
    def test_partial_evidence_is_not_whole_completion(self):
        ctx = context()
        text = '''[partial-check]
- scope: partial
- acceptance-criteria:
  - AC1: Repair the original failure [source: user-said]
  - waiting: Unresolved condition [source: user-said]
- claim-evidence-map:
  - claim: The targeted regression passed.
    criterion: AC1
    evidence: retained original test result
    verdict: pass
- unverified:
  - waiting: Pending required input
'''
        text = 'The targeted tests passed. A required condition remains unresolved.\n' + text
        result = select_completion_route(ctx, dict(text=text, phase='final'), evidence())
        self.assertFalse(result['required_completion_check'])
        self.assertTrue(result.get('required_partial_check'))
        self.assertEqual(result['selected_criteria'], ctx['response_route']['selected_criteria'])


class CompletionRouteTests(unittest.TestCase):
    def test_minimal_surface_preserves_owned_verification_and_original_proof(self):
        ctx = context()
        ctx["response_route"]["routing_surface"] = dict(response_mode="normal", boundary_contract="n/a")
        result = self.select(ctx, proof={})
        self.assertEqual(result["route_binding"], "current")
        self.assertEqual(result["evidence_route"], "fresh_affected")
        self.assertEqual(result["unit_decisions"][0]["criteria"][0]["check_ids"], ["original-failure"])
        retained = self.select(ctx)
        self.assertEqual(retained["evidence_route"], "reuse")
        proof = retained["unit_decisions"][0]["criteria"][0]
        self.assertEqual((proof["proof_ref"], proof["verified_at"]), ("tool-result-old", "2026-10-01T12:00:00Z"))

    def test_removed_or_malformed_surface_fields_cannot_authorize_completion(self):
        for field, value in (("verification_complexity", "level-2"), ("focus_layer", "micro"), ("reason", " ")):
            with self.subTest(field=field):
                ctx = context(); ctx["response_route"]["routing_surface"][field] = value
                result = self.select(ctx)
                self.assertEqual(result["route_binding"], "invalid")
                self.assertTrue(result["required_completion_check"])
                self.assertEqual(result["selected_criteria"], [])

    def test_prior_result_explanation_preserves_proof(self):
        ctx = context()
        before = deepcopy(ctx)
        proof = evidence()
        result = self.select(ctx, dict(text="기존 검증 기록에 따르면 로컬 설치 수정을 완료했습니다.",
                                       phase="final"), proof)
        self.assertFalse(result["required_completion_check"])
        self.assertEqual(result["unit_decisions"][0]["criteria"][0]["proof_ref"], "tool-result-old")
        self.assertEqual(result["unit_decisions"][0]["criteria"][0]["verified_at"], "2026-10-01T12:00:00Z")
        self.assertEqual(ctx, before)

    def test_nonclosing_final_preserves_work(self):
        ctx = context()
        for text in ("Here is why the pending change needs approval.", "Please choose the proposed direction."):
            with self.subTest(text=text):
                result = self.select(ctx, claims=dict(text=text, phase="final"), proof={})
                self.assertFalse(result["required_completion_check"])
                self.assertIn("No completion submission", result["reason"])
                self.assertEqual(result["selected_criteria"], context()["response_route"]["selected_criteria"])

    def test_text_without_a_control_block_never_excuses_actual_closure(self):
        ctx = context()
        for phase in ("before_tool", "final"):
            with self.subTest(phase=phase):
                result = self.select(ctx, dict(text="", phase=phase), {})
                self.assertFalse(result["required_completion_check"])
                self.assertNotIn("response_purpose", result)
                self.assertEqual(result["selected_work_unit_ids"], ["fix", "explain"])
        result = self.select(ctx, proof={})
        self.assertTrue(result["required_completion_check"])
        self.assertEqual(result["evidence_route"], "fresh_affected")

    def test_execution_selection_does_not_infer_completion_from_prose(self):
        for text in ("Saved requested report", "I saved the requested report."):
            result = self.select(claims=dict(text=text, phase="final"), proof={})
            self.assertFalse(result["required_completion_check"])
            self.assertIn("No completion submission", result["reason"])

    def select(self, ctx=None, claims=None, proof=None):
        return select_completion_route(ctx if ctx is not None else context(),
            claims if claims is not None else dict(text="[completion-check]", phase="final"),
            proof if proof is not None else evidence())

    def test_ordinary_chat_needs_intent_judgment_without_external_operation(self):
        result = self.select({}, dict(text="Hello.", phase="final"), {})
        self.assertEqual(result.get("evidence_route"), "no_external_check")

    def test_route_selects_evidence_only_from_the_three_core_inputs(self):
        for result in (self.select(), self.select({}, dict(text="Hello.", phase="final"), {}),
                       self.select(claims=dict(text="", phase="before_tool"), proof={})):
            for field in ("publication_route", "report_only", "unbound_report_text", "claim_report_issue"):
                self.assertNotIn(field, result)
        with self.assertRaises(TypeError):
            select_completion_route(context(), dict(text="", phase="final"), {}, {})
        # A caller-supplied claim report is not a Core input; the delivered text stays the claim.
        reported = self.select(claims=dict(text="[completion-check]", phase="final",
                                           claim_report=dict(present=True, valid=True, residual_text="")))
        self.assertTrue(reported["required_completion_check"])

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

    def test_recording_mismatch_reopens_affected_cause(self):
        proof = evidence(relevant_change="changed"); proof["failure_kind"] = "recording"
        self.assertEqual(self.select(proof=proof).get("evidence_route"), "reopen_cause")

    def test_authority_integrity_contract_or_unknown_reopen_cause(self):
        for failure in ("authority", "integrity", "contract", "unknown"):
            with self.subTest(failure=failure):
                proof = evidence(); proof["failure_kind"] = failure
                self.assertEqual(self.select(proof=proof).get("evidence_route"), "reopen_cause")
        self.assertEqual(self.select(proof=evidence(relevant_change="unknown")).get("evidence_route"), "reopen_cause")

    def test_owner_check_is_not_downgraded_by_short_explanation(self):
        ctx = context()
        result = self.select(ctx, dict(text="Yes.", phase="final", consequential=True), dict(criteria=[]))
        self.assertEqual(result.get("evidence_route"), "fresh_affected")
        self.assertEqual(result["unit_decisions"][0]["criteria"][0]["check_ids"], ["original-failure"])

    def test_nonexecution_label_cannot_excuse_actual_unsupported_closure(self):
        ctx = context(); ctx["response_route"]["selected_work_unit_ids"] = ["explain"]
        ctx["response_route"]["selected_criteria"] = []
        result = self.select(ctx, dict(text="[completion-check]", phase="final"), {})
        self.assertTrue(result.get("required_completion_check"))
        self.assertEqual(result.get("evidence_route"), "owner_verification")

    def test_quoted_examples_do_not_create_execution_criteria(self):
        ctx = context(); ctx["response_route"]["selected_work_unit_ids"] = ["explain"]
        ctx["response_route"]["selected_criteria"] = []
        result = self.select(ctx, dict(text='Example: "The task is complete."\n```\n[completion-check]\n```', phase="final"), {})
        self.assertFalse(result.get("required_completion_check"))
        self.assertEqual(result.get("evidence_route"), "no_external_check")
        self.assertEqual(result["selected_criteria"], [])

    def test_long_fence_indented_and_lazy_quote_examples_do_not_require_closure(self):
        for text in ("````text\n```\n[completion-check]\nThe task is complete.\n```\n````\n",
                     "    [completion-check]\n    The task is complete.\n",
                     "> Quoted example:\nThe task is complete.\n[completion-check]\n"):
            with self.subTest(text=text):
                result = self.select({}, dict(text=text, phase="final"), {})
                self.assertFalse(result.get("required_completion_check"))
                self.assertEqual(result.get("evidence_route"), "no_external_check")

    def test_long_example_does_not_hide_real_closure_after_it(self):
        text = "````text\n```\n[completion-check]\n```\n````\n\n[completion-check]"
        result = self.select({}, dict(text=text, phase="final"), {})
        self.assertTrue(result.get("required_completion_check"))
        self.assertEqual(result.get("evidence_route"), "owner_verification")

    def test_balanced_explicit_multiline_quote_does_not_create_closure(self):
        for opening, closing in (("“", "”"), ('"', '"')):
            text = "A sample response is:\n" + opening + "The task is complete.\n[completion-check]\n- claim-evidence-map:\n" + closing
            with self.subTest(text=text):
                result = self.select({}, dict(text=text, phase="final"), {})
                self.assertFalse(result.get("required_completion_check"))
                self.assertEqual(result.get("evidence_route"), "no_external_check")

    def test_inline_quote_boundaries_cannot_hide_actual_control(self):
        text = 'The command returned "OK."\n\n[completion-check]\n\n"[boundary-contract]"'
        result = self.select({}, dict(text=text, phase="final"), {})
        self.assertTrue(result.get("required_completion_check"))
        self.assertEqual(result.get("evidence_route"), "owner_verification")

    def test_quoted_list_lazy_continuation_does_not_create_closure(self):
        for prefix in ("> - ", "> 1. ", "> 1. > "):
            text = prefix + "Example output:\nThe task is complete.\n[completion-check]\n"
            with self.subTest(text=text):
                result = self.select({}, dict(text=text, phase="final"), {})
                self.assertFalse(result.get("required_completion_check"))
                self.assertEqual(result.get("evidence_route"), "no_external_check")

    def test_closed_nested_container_fence_does_not_create_closure_for_lazy_quote(self):
        prefix = "> - > ```\n>   > code\n>   > ```\n>\n> A quoted paragraph.\n"
        for tail in ("The task is complete.\n[completion-check]\n", "> The task is complete.\n> [completion-check]\n"):
            with self.subTest(tail=tail):
                result = self.select({}, dict(text=prefix + tail, phase="final"), {})
                self.assertFalse(result.get("required_completion_check"))
                self.assertEqual(result.get("evidence_route"), "no_external_check")

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

    def test_new_input_waits_for_route(self):
        for missing in (False, True):
            ctx = context()
            ctx['input_event_id'] = 'next-input'
            if missing:
                ctx['response_route'] = None
            else:
                ctx['response_route'].update(status='stale', current_input_event_id='next-input')
            with self.subTest(missing=missing):
                waiting = self.select(ctx, claims=dict(text='', phase='before_tool'))
                self.assertEqual(waiting['route_binding'], 'missing' if missing else 'stale')
                self.assertEqual(waiting['selected_criteria'], [])
                self.assertIn('awaits route registration', waiting['reason'])
        ctx = context()
        ctx['response_route']['session_id'] = 'foreign-session'
        invalid = self.select(ctx, claims=dict(text='', phase='before_tool'))
        self.assertEqual(invalid['route_binding'], 'stale')
        self.assertIn('route repair', invalid['reason'])

    def test_missing_or_stale_binding_requires_route_repair_without_excusing_final_claim(self):
        for binding in (None, "stale", "wrong-session", "wrong-input"):
            ctx = context()
            if binding is None: ctx["response_route"] = None
            elif binding == "stale": ctx["response_route"]["status"] = "stale"
            elif binding == "wrong-session": ctx["response_route"]["session_id"] = "other"
            else: ctx["response_route"]["input_event_id"] = "old-input"
            with self.subTest(binding=binding):
                result = self.select(ctx)
                self.assertNotEqual(result["route_binding"], "current")
                self.assertTrue(result["required_completion_check"])
                self.assertIn("authorized continuation", result["reason"])

    def test_closure_wording_without_a_control_block_is_not_a_submission(self):
        for text in ("The fix is complete.", "I reviewed the requested files."):
            with self.subTest(text=text):
                ctx = context()
                result = self.select(ctx, claims=dict(text=text, phase="final"), proof={})
                self.assertEqual(result["route_binding"], "current")
                self.assertFalse(result["required_completion_check"])
                self.assertIn("No completion submission", result["reason"])
                self.assertEqual(result["selected_criteria"][0]["status"], "unmet")

    def test_terminal_response_rejects_inconsistent_selected_execution(self):
        for mode in ("direct-response", "clarification-only"):
            for phase in ("final", "before_tool"):
                with self.subTest(mode=mode, phase=phase):
                    ctx = context()
                    ctx["response_route"]["routing_surface"].update(response_mode=mode)
                    result = self.select(ctx, dict(text="The fix is complete.\n[completion-check]", phase=phase))
                    self.assertEqual(result["route_binding"], "invalid")
                    self.assertEqual(result["selected_criteria"], [])
                    self.assertTrue(result["required_completion_check"])
                    self.assertEqual(result["evidence_route"], "owner_verification")

    def test_terminal_explanation_does_not_replay_unselected_execution(self):
        ctx = context()
        ctx["response_route"]["routing_surface"].update(response_mode="direct-response")
        ctx["response_route"]["selected_work_unit_ids"] = ["explain"]
        ctx["response_route"]["selected_criteria"] = []
        result = self.select(ctx, dict(text="The earlier path output identifies the working directory.", phase="final"))
        self.assertEqual(result["route_binding"], "current")
        self.assertEqual(result["evidence_route"], "no_external_check")
        self.assertFalse(result["required_completion_check"])

    def test_before_tool_without_claim_requires_no_completion_check(self):
        result = self.select(claims=dict(text="", phase="before_tool"), proof={})
        self.assertFalse(result["required_completion_check"])
        self.assertEqual(result["selected_work_unit_ids"], ["fix", "explain"])
        self.assertEqual(result["evidence_route"], "fresh_affected")

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

    def test_malformed_evidence_container_or_member_selects_no_business_rerun(self):
        for raw in ({}, [None]):
            with self.subTest(raw=raw):
                result = self.select(proof=dict(criteria=raw))
                self.assertEqual(result.get("evidence_route"), "reopen_cause")
                decision = result["unit_decisions"][0]["criteria"][0]
                self.assertEqual(decision["check_ids"], [])
                self.assertEqual(decision["required_check_ids"], ["original-failure"])

    def test_genuine_evidence_absence_still_selects_owner_required_check(self):
        for proof in ({}, dict(criteria=[])):
            with self.subTest(proof=proof):
                result = self.select(proof=proof)
                self.assertEqual(result.get("evidence_route"), "fresh_affected")
                self.assertEqual(result["unit_decisions"][0]["criteria"][0]["check_ids"], ["original-failure"])

    def test_incomplete_selected_owner_record_retains_valid_proof_without_rerun(self):
        for malformed in (None, dict(work_unit_id="fix", contract_revision=1)):
            proof = evidence(); proof["criteria"].append(malformed)
            with self.subTest(malformed=malformed):
                result = self.select(proof=proof)
                self.assertEqual(result.get("evidence_route"), "reopen_cause")
                decision = result["unit_decisions"][0]["criteria"][0]
                self.assertEqual(decision["check_ids"], [])
                self.assertEqual(decision["proof_ref"], "tool-result-old")
                self.assertEqual(decision["verified_at"], "2026-10-01T12:00:00Z")

    def test_malformed_owned_record_reopens_only_its_unit(self):
        ctx = context()
        ctx["response_route"]["selected_work_unit_ids"].append("backlog")
        ctx["response_route"]["selected_criteria"].append(dict(ctx["response_route"]["selected_criteria"][0], work_unit_id="backlog"))
        ctx["verification_plan"]["checks"].append(dict(check_id="backlog-check", work_unit_id="backlog", contract_revision=1, criterion_id="AC1"))
        proof = evidence(); proof["criteria"].extend([dict(proof["criteria"][0], work_unit_id="backlog", check_ids=["backlog-check"], proof_ref="backlog-result"),
                                                    dict(work_unit_id="fix", contract_revision=1)])
        result = self.select(ctx, proof=proof)
        first, _, last = result["unit_decisions"]
        self.assertEqual(first["evidence_route"], "reopen_cause")
        self.assertEqual(first["criteria"][0]["check_ids"], [])
        self.assertEqual(first["criteria"][0]["proof_ref"], "tool-result-old")
        self.assertEqual(last["evidence_route"], "reuse")
        self.assertEqual(last["criteria"][0]["proof_ref"], "backlog-result")

    def test_unrelated_historical_records_do_not_supply_or_conflict_with_current_proof(self):
        proof = evidence(); proof["criteria"].extend([dict(proof["criteria"][0], contract_revision=2, proof_ref="other-revision"),
                                                    dict(proof["criteria"][0], work_unit_id="backlog", proof_ref="other-unit")])
        result = self.select(proof=proof)
        self.assertEqual(result.get("evidence_route"), "reuse")
        self.assertEqual(result["unit_decisions"][0]["criteria"][0]["proof_ref"], "tool-result-old")
        proof["criteria"].pop(0)
        result = self.select(proof=proof)
        self.assertEqual(result.get("evidence_route"), "fresh_affected")
        self.assertNotIn("proof_ref", result["unit_decisions"][0]["criteria"][0])

    def test_pure_selector_does_not_mutate_any_input(self):
        inputs = [context(), dict(text="The fix is complete.", phase="final"), evidence()]
        before = deepcopy(inputs)
        select_completion_route(*inputs)
        self.assertEqual(inputs, before)


if __name__ == "__main__":
    unittest.main()
