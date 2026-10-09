"""Core host recovery preserves business payload and changes only what failed."""

import unittest

from response_delivery import select_recovery_delivery, recovery_delivery_guidance


class RecoveryDeliveryTests(unittest.TestCase):
    def test_shared_observer_requires_usable_host_answer(self):
        from response_delivery import observe_recovery_delivery
        self.assertEqual(observe_recovery_delivery({'scratchpad_dir':'/host'}, platform='claude', entrypoint='cli', final_text='Saved result')['delivery_route'], 'preserve_answer_and_patch_metadata')
        self.assertEqual(observe_recovery_delivery({'scratchpad_dir':'/host'}, platform='claude', entrypoint='cli', final_text='')['delivery_route'], 'assembled_complete_payload')
        self.assertEqual(observe_recovery_delivery({}, platform='codex', entrypoint='Codex Desktop', final_text='Saved result')['delivery_route'], 'assembled_complete_payload')

    def test_correction_only_metadata_is_not_the_original_business_answer(self):
        from response_delivery import observe_recovery_delivery
        payload=dict(host_context=dict(delivery_surface='visible_transcript'))
        text='[completion-check]\n- unverified: none\n[io-trace]\n- files-read: [/report]'
        self.assertEqual(observe_recovery_delivery(payload,platform='claude',final_text=text)['delivery_route'],'assembled_complete_payload')
        payload['prior_answer_ref']='retained-host-answer'
        self.assertEqual(observe_recovery_delivery(payload,platform='claude',final_text=text)['delivery_route'],'preserve_answer_and_patch_metadata')

    def test_visible_bullet_business_answer_is_usable(self):
        from response_delivery import observe_recovery_delivery
        answer='- Saved the report and preserved the backup.\n[completion-check]\n- unverified: none\n[io-trace]\n- files-written: [/report]'
        result=observe_recovery_delivery(dict(host_context=dict(delivery_surface='visible_transcript')),platform='codex',final_text=answer)
        self.assertEqual(result['delivery_route'],'preserve_answer_and_patch_metadata')

    def test_known_visible_unchanged_answer_only_patches_metadata(self):
        result = select_recovery_delivery(dict(delivery_surface="visible_transcript"), "host-answer-ref", False, True)
        self.assertEqual(result, "preserve_answer_and_patch_metadata")

    def test_final_only_or_unknown_assembles_complete_payload(self):
        for host in (dict(delivery_surface="final_only"), dict(delivery_surface="unknown"), {}, dict(entrypoint="cli")):
            with self.subTest(host=host):
                self.assertEqual(select_recovery_delivery(host, "answer-digest", False, True), "assembled_complete_payload")

    def test_substantive_correction_returns_corrected_complete_answer(self):
        for surface in ("visible_transcript", "final_only", "unknown"):
            with self.subTest(surface=surface):
                self.assertEqual(select_recovery_delivery(dict(delivery_surface=surface), "answer-ref", True, True), "corrected_complete_answer")

    def test_absent_answer_ref_or_unknown_claim_change_is_conservative(self):
        host = dict(delivery_surface="visible_transcript")
        self.assertEqual(select_recovery_delivery(host, None, False, True), "assembled_complete_payload")
        self.assertEqual(select_recovery_delivery(host, "answer-ref", None, True), "assembled_complete_payload")

    def test_no_metadata_delta_does_not_force_business_rewrite(self):
        self.assertEqual(select_recovery_delivery(dict(delivery_surface="visible_transcript"), "answer-ref", False, False), "preserve_answer_and_patch_metadata")

    def test_guidance_keeps_original_evidence_and_truthful_withdrawals(self):
        for route in ("preserve_answer_and_patch_metadata", "assembled_complete_payload", "corrected_complete_answer"):
            with self.subTest(route=route):
                guidance = recovery_delivery_guidance(route)
                self.assertIn("Do not invent evidence", guidance)
                self.assertIn("original evidence times", guidance)
                self.assertIn("without rerunning unchanged business checks", guidance)
                self.assertIn("corrected complete answer", guidance)


if __name__ == "__main__":
    unittest.main()
