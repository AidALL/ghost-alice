"""Claim references cannot expand or replace accepted original evidence."""
import json
import unittest
from unittest.mock import patch

from claim_evidence import evaluate_claim_report, evaluate_claim_reuse
from completion_route import select_completion_route


CLAIM = dict(statement="이번 Core/AP 수정의 관련 검사가 통과했습니다.", work_unit_id="install",
             contract_revision=1, criterion_id="checks", proof_digest="sha256:" + "a" * 64)


def report(claims=None, body=None):
    claims = [CLAIM] if claims is None else claims
    body = CLAIM["statement"] if body is None else body
    return body + '\n\n[evidence-report]\n' + json.dumps(dict(schema_version="claim-evidence.v1", claims=claims), ensure_ascii=False) + '\n[/evidence-report]'


def accepted(claims):
    return dict(status="valid", claims=[dict(c, validity="valid", original_claims=[CLAIM["statement"]],
        original_verified_at="2026-10-10T10:42:22Z", evidence_reference="original-evidence",
        application_reference="accepted-application") for c in claims])


class ClaimEvidenceTests(unittest.TestCase):
    def test_prompt_view_is_small_and_contains_no_model_linkage_protocol(self):
        from claim_evidence_runtime import retained_results_hint
        rows = [dict(CLAIM, statement=CLAIM["statement"] + str(i), original_verified_at="2026-10-10T10:42:22Z") for i in range(20)]
        with patch("claim_evidence_runtime.retained_claim_candidates", return_value=dict(status="candidates", claims=rows)):
            hint = retained_results_hint("codex", {"session_id": "synthetic"}, {})
        self.assertLess(len(hint), 3000)
        self.assertNotIn("proof_digest", hint)
        self.assertNotIn("work_unit_id", hint)
        self.assertEqual(hint.count('"statement"'), 4)

    def test_native_reuse_needs_no_response_protocol(self):
        candidates = lambda: dict(status="candidates", claims=[dict(CLAIM, validity="candidate")])
        value = evaluate_claim_reuse(CLAIM["statement"], accepted, candidates)
        self.assertTrue(value["valid"], value)
        self.assertTrue(value["automatic"])
        self.assertFalse(value["residual_text"].strip())

    def test_automatic_reuse_never_expands_or_hides_a_new_claim(self):
        candidates = lambda: dict(status="candidates", claims=[dict(CLAIM, validity="candidate")])
        value = evaluate_claim_reuse("All " + CLAIM["statement"], accepted, candidates)
        self.assertFalse(value["valid"])
        value = evaluate_claim_reuse(CLAIM["statement"] + "\n\nThe work is complete.", accepted, candidates)
        self.assertIn("The work is complete.", value["residual_text"])

    def test_automatic_discovery_is_lazy_and_ambiguous_claims_fail_closed(self):
        def forbidden():
            self.fail("Nonclaim must not query retained evidence")
        self.assertFalse(evaluate_claim_reuse("```text\nThe tests passed.\n```", accepted, forbidden)["present"])
        candidates = lambda: dict(status="candidates", claims=[CLAIM, dict(CLAIM, work_unit_id="another")])
        value = evaluate_claim_reuse(CLAIM["statement"], accepted, candidates)
        self.assertFalse(value["valid"])

    def test_accepted_claim_linking_does_not_depend_on_completion_vocabulary(self):
        claim = dict(CLAIM, statement="Saved report matches input")
        def resolved(rows):
            result = accepted(rows)
            result["claims"][0]["original_claims"] = [claim["statement"]]
            return result
        value = evaluate_claim_reuse(claim["statement"], resolved,
            lambda: dict(status="candidates", claims=[claim]))
        self.assertTrue(value["valid"], value)

    def test_original_claim_not_temporal_heuristic(self):
        value = evaluate_claim_report(report(), accepted)
        self.assertTrue(value["valid"], value)
        self.assertFalse(value["residual_text"].strip())
        self.assertEqual(value["claims"][0]["original_verified_at"], "2026-10-10T10:42:22Z")

    def test_exact_paragraph_and_original_claim_required(self):
        for body in ["All " + CLAIM["statement"], CLAIM["statement"] + " Everything is done.",
                     CLAIM["statement"] + "\n\n" + CLAIM["statement"],
                     "- " + CLAIM["statement"]]:
            with self.subTest(body=body):
                self.assertFalse(evaluate_claim_report(report(body=body), accepted)["valid"])
        expanded = dict(CLAIM, statement="All work is complete.")
        self.assertFalse(evaluate_claim_report(report([expanded], expanded["statement"]), accepted)["valid"])

    def test_extra_assertion_remains_guarded(self):
        text = report(body=CLAIM["statement"] + "\n\n앞서 확보한 기록 기준으로 배포 검사가 통과했습니다.")
        value = evaluate_claim_report(text, accepted)
        selected = select_completion_route({}, dict(text=text, phase="final", claim_report=value), {}, {"enabled": True})
        self.assertFalse(selected["required_completion_check"])
        self.assertTrue(selected["unbound_report_text"])
        self.assertFalse(selected["report_only"])
        self.assertEqual(selected["publication_route"], "none")

    def test_report_only_with_missing_route_does_not_publish(self):
        text = report()
        value = evaluate_claim_report(text, accepted)
        selected = select_completion_route({}, dict(text=text, phase="final", claim_report=value), {}, {"enabled": True})
        self.assertTrue(selected["report_only"])
        self.assertFalse(selected["required_completion_check"])
        self.assertEqual(selected["publication_route"], "none")

    def test_plain_surrounding_scope_cannot_widen_original_claim(self):
        for prefix in ["These results cover every repository in the company.",
                       "The following result covers the entire platform.", "A neutral explanation."]:
            text = report(body=prefix + "\n\n" + CLAIM["statement"])
            value = evaluate_claim_report(text, accepted)
            selected = select_completion_route({}, dict(text=text, phase="final", claim_report=value), {}, {"enabled": True})
            self.assertFalse(selected["report_only"])
            self.assertFalse(selected["required_completion_check"])
            self.assertTrue(selected["unbound_report_text"])

    def test_surrounding_structure_is_reviewed_not_rejected_as_a_bad_reference(self):
        candidates = lambda: dict(status="candidates", claims=[CLAIM])
        for prefix in ["1. 비교 범위\n\n- Core와 AP의 변경을 설명합니다.",
                       "# All projects", "All projects:"]:
            with self.subTest(prefix=prefix):
                text = prefix + "\n\n" + CLAIM["statement"]
                value = evaluate_claim_reuse(text, accepted, candidates)
                self.assertFalse(value["present"])
                self.assertEqual(value["residual_text"], text)
                self.assertEqual(value["claims"], [])
                # Explicit references still authenticate only the original claim;
                # surrounding scope needs the separate whole-response review.
                value = evaluate_claim_report(report(body=text), accepted)
                self.assertTrue(value["valid"], value)
                self.assertEqual(value["residual_text"].strip(), prefix)
                self.assertEqual(value["claims"][0]["original_verified_at"], "2026-10-10T10:42:22Z")
                selected = select_completion_route({}, dict(text=text, phase="final", claim_report=value), {}, {"enabled": True})
                self.assertFalse(selected["report_only"])
                self.assertFalse(selected["required_completion_check"])
                self.assertTrue(selected["unbound_report_text"])
                self.assertEqual(selected["publication_route"], "none")
                self.assertIsNone(selected["claim_report_issue"])

    def test_invalid_and_unavailable_fail_closed(self):
        for resolve in [lambda _: {"status": "unavailable", "claims": []},
                        lambda _: {"status": "valid", "claims": []},
                        lambda cs: dict(status="valid", claims=[dict(accepted(cs)["claims"][0], proof_digest="sha256:" + "b" * 64)])]:
            with self.subTest(resolve=resolve):
                value = evaluate_claim_report(report(), resolve)
                self.assertFalse(value["valid"])
                selected = select_completion_route({}, dict(text=report(), phase="final", claim_report=value), {}, {"enabled": True})
                self.assertEqual(selected["publication_route"], "blocked")
                self.assertTrue(selected["claim_report_issue"])

    def test_protocol_examples_do_not_invoke_resolver(self):
        def forbidden(_):
            self.fail("Example must not access authority")
        for text in ['```text\n' + report() + '\n```', '\n'.join('> ' + line for line in report().splitlines()), 'Ordinary explanation.']:
            self.assertFalse(evaluate_claim_report(text, forbidden)["present"])

    def test_malformed_and_duplicate_protocol_fail_closed(self):
        malformed = [report() + '\n\n' + report(), report().replace('[/evidence-report]', ''),
                     report([dict(CLAIM, retained=True)]), report([CLAIM, CLAIM]), report([]),
                     report().replace('"schema_version":', '"schema_version":"other","schema_version":')]
        for text in malformed:
            with self.subTest(text=text):
                self.assertFalse(evaluate_claim_report(text, accepted)["valid"])

    def test_explicit_controls_cannot_be_hidden(self):
        text = report(body=CLAIM["statement"] + '\n\n[completion-check]\n- unverified: none')
        value = evaluate_claim_report(text, accepted)
        selected = select_completion_route({}, dict(text=text, phase="final", claim_report=value), {}, {"enabled": True})
        self.assertTrue(selected["required_completion_check"])
        self.assertFalse(selected["report_only"])


if __name__ == "__main__":
    unittest.main()
