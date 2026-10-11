"""Core host recovery preserves business payload and changes only what failed."""

import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
import json

from response_delivery import select_recovery_delivery, recovery_delivery_guidance


class RecoveryDeliveryTests(unittest.TestCase):
    def test_mixed_response_defect_does_not_request_manual_original_claim_envelope(self):
        import claude_stop_verification_hook as hook
        issue = 'claim-evidence-unbound: Surrounding prose needs owning evidence.'
        output = StringIO()
        with patch.object(hook, '_read_hook_input', return_value={'session_id': 'test', 'last_assistant_message': 'Report with original proof and new claims.'}), patch.object(hook, 'assess_native_completion', return_value={'body_issue': issue, 'has_evidence_marker': False}), patch('sys.argv', ['hook', '--platform', 'codex']), redirect_stdout(output):
            hook.main()
        value = json.loads(output.getvalue())
        self.assertEqual(value['decision'], 'block')
        self.assertIn(issue, value['reason'])
        self.assertNotIn('[evidence-report]', value['reason'])
        self.assertIn('delivery-route: resolve_host_delivery', value['reason'])

    def test_ap_record_repair_does_not_reteach_core_proof_format(self):
        from response_delivery import runtime_record_repair_guidance
        text = runtime_record_repair_guidance()
        self.assertIn('Core validation code authenticates the completion review', text)
        self.assertIn('original evidence times', text)
        self.assertNotIn('scope: partial', text)
        self.assertNotIn('claim-evidence-map', text)

    def test_delivery_guidance_does_not_control_prerequisite_repair(self):
        text = recovery_delivery_guidance('resolve_host_delivery')
        self.assertNotIn('without tools', text)
        self.assertNotIn('Replace the entire invalid control block', text)
        self.assertIn('answer visibility', text)
        self.assertNotIn('follows task-router', text)
        self.assertNotIn('follows verification-before-completion', text)

    def test_unknown_delivery_does_not_unconditionally_replay_visible_answer(self):
        from response_delivery import observe_recovery_delivery
        result = observe_recovery_delivery({}, platform='codex', final_text='The local repair is complete.')
        self.assertEqual(result['delivery_route'], 'resolve_host_delivery')
        self.assertIn('If the answer is already visible', result['guidance'])
        self.assertNotIn('Do not output a correction-only note', result['guidance'])

    def test_visible_delivery_leaves_claim_judgment_to_diagnostic(self):
        guidance = recovery_delivery_guidance('preserve_answer_and_patch_metadata')
        self.assertNotIn('deliver a corrected complete answer', guidance)
        self.assertIn('correction required by the diagnostic', guidance)
        self.assertNotIn('unsupported claim', guidance)

    def test_shared_observer_requires_usable_host_answer(self):
        from response_delivery import observe_recovery_delivery
        self.assertEqual(observe_recovery_delivery({'scratchpad_dir':'/host'}, platform='claude', entrypoint='cli', final_text='Saved result')['delivery_route'], 'preserve_answer_and_patch_metadata')
        self.assertEqual(observe_recovery_delivery({'scratchpad_dir':'/host'}, platform='claude', entrypoint='cli', final_text='')['delivery_route'], 'assembled_complete_payload')
        self.assertEqual(observe_recovery_delivery({}, platform='codex', entrypoint='Codex Desktop', final_text='Saved result')['delivery_route'], 'resolve_host_delivery')

    def test_correction_only_metadata_is_not_the_original_business_answer(self):
        from response_delivery import observe_recovery_delivery
        payload=dict(host_context=dict(delivery_surface='visible_transcript'))
        text='[completion-check]\n- unverified: none\n[io-trace]\n- files-read: [/report]'
        self.assertEqual(observe_recovery_delivery(payload,platform='claude',final_text=text)['delivery_route'],'resolve_host_delivery')
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

    def test_final_only_assembles_and_unknown_defers_to_supplied_host_context(self):
        self.assertEqual(select_recovery_delivery(dict(delivery_surface="final_only"), "answer-digest", False, True), "assembled_complete_payload")
        for host in (dict(delivery_surface="unknown"), {}, dict(entrypoint="cli")):
            with self.subTest(host=host):
                self.assertEqual(select_recovery_delivery(host, "answer-digest", False, True), "resolve_host_delivery")

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

    def test_delivery_does_not_repeat_proof_or_recording_procedures(self):
        for route in ("preserve_answer_and_patch_metadata", "assembled_complete_payload", "corrected_complete_answer", "resolve_host_delivery"):
            with self.subTest(route=route):
                guidance = recovery_delivery_guidance(route)
                self.assertNotIn('verification-before-completion', guidance)
                self.assertNotIn('task-router', guidance)
                self.assertNotIn('[completion-check]', guidance)


if __name__ == "__main__":
    unittest.main()
