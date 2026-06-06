#!/usr/bin/env python3
"""Tests for Ghost-ALICE agent visibility policy decisions."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_visibility_policy


class TestAgentVisibilityPolicy(unittest.TestCase):
    def test_strict_profile_emits_routine_clean_message(self):
        decision = agent_visibility_policy.decide(
            profile="strict",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="No pending warning from this hook means merge-companion-precheck is clean.",
            stderr="",
            exit_code=0,
            context={"signal": "routine-clean-pass"},
        )

        self.assertEqual(decision["visible_decision"], "show")
        self.assertEqual(decision["reason"], "strict-profile")

    def test_unknown_profile_falls_back_to_strict(self):
        decision = agent_visibility_policy.decide(
            profile="quiet",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="routine reminder",
            stderr="",
            exit_code=0,
            context={"signal": "routine-clean-pass"},
        )

        self.assertEqual(decision["visible_decision"], "show")
        self.assertEqual(decision["reason"], "strict-profile")

    def test_action_denial_is_forced_under_minimal(self):
        decision = agent_visibility_policy.decide(
            profile="minimal",
            hook_id="tool-checkpoint",
            event="BeforeTool",
            stdout='{"decision":"deny","reason":"[tool-checkpoint] required"}',
            stderr="",
            exit_code=0,
            context={"decision": "deny"},
        )

        self.assertEqual(decision["visible_decision"], "force_show")
        self.assertEqual(decision["reason"], "forced-action-denial")

    def test_failed_hook_output_is_forced(self):
        decision = agent_visibility_policy.decide(
            profile="dynamic",
            hook_id="completion",
            event="Stop",
            stdout="",
            stderr="verification failed",
            exit_code=1,
            context=None,
        )

        self.assertEqual(decision["visible_decision"], "force_show")
        self.assertEqual(decision["reason"], "forced-nonzero-exit")

    def test_dynamic_hides_routine_clean_pass(self):
        decision = agent_visibility_policy.decide(
            profile="dynamic",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="routine clean pass already persisted",
            stderr="",
            exit_code=0,
            context={"signal": "routine-clean-pass"},
        )

        self.assertEqual(decision["visible_decision"], "hide")
        self.assertEqual(decision["reason"], "routine-clean-pass")

    def test_project_user_surface_omits_routine_model_hint(self):
        user_surface, model_surface = agent_visibility_policy.project_user_surface(
            exposure_class="routine",
            value_kind="routine",
            profile="dynamic",
        )

        self.assertEqual((user_surface, model_surface), ("hidden", "omitted"))
        decision = agent_visibility_policy.decide(
            profile="dynamic",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="routine clean pass already persisted",
            stderr="",
            exit_code=0,
            context={"signal": "routine-clean-pass"},
        )
        self.assertEqual(decision["visible_decision"], "hide")

    def test_dynamic_routing_surface_level_two_overrides_routine_hide(self):
        decision = agent_visibility_policy.decide(
            profile="dynamic",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="routine clean pass already persisted",
            stderr="",
            exit_code=0,
            context={
                "signal": "routine-clean-pass",
                "routing_surface": {
                    "intent_relation": "changed",
                    "change_depth": "localized",
                    "focus_layer": "meso",
                    "verification_complexity": "level-2",
                    "boundary_contract": "n/a",
                    "forced_visibility": "no",
                },
            },
        )

        self.assertEqual(decision["visible_decision"], "show")
        self.assertEqual(decision["reason"], "routing-surface-focused")

    def test_dynamic_routing_surface_ambiguous_fails_closed(self):
        decision = agent_visibility_policy.decide(
            profile="dynamic",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="routine clean pass already persisted",
            stderr="",
            exit_code=0,
            context={
                "signal": "routine-clean-pass",
                "routing_surface": {
                    "intent_relation": "ambiguous",
                    "change_depth": "minimal",
                    "focus_layer": "micro",
                    "verification_complexity": "level-1",
                    "boundary_contract": "n/a",
                    "forced_visibility": "no",
                },
            },
        )

        self.assertEqual(decision["visible_decision"], "show")
        self.assertEqual(decision["reason"], "routing-surface-fail-closed")

    def test_dynamic_routing_surface_unknown_value_fails_closed(self):
        decision = agent_visibility_policy.decide(
            profile="dynamic",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="routine clean pass already persisted",
            stderr="",
            exit_code=0,
            context={
                "signal": "routine-clean-pass",
                "routing_surface": {
                    "intent_relation": "accepted-continuation",
                    "accepted_continuation_grounded": True,
                    "change_depth": "tiny",
                    "focus_layer": "micro",
                    "verification_complexity": "level-1",
                    "boundary_contract": "n/a",
                    "forced_visibility": "no",
                },
            },
        )

        self.assertEqual(decision["visible_decision"], "show")
        self.assertEqual(decision["reason"], "routing-surface-fail-closed")

    def test_dynamic_ungrounded_accepted_continuation_fails_closed(self):
        decision = agent_visibility_policy.decide(
            profile="dynamic",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="routine clean pass already persisted",
            stderr="",
            exit_code=0,
            context={
                "signal": "routine-clean-pass",
                "routing_surface": {
                    "intent_relation": "accepted-continuation",
                    "change_depth": "minimal",
                    "focus_layer": "micro",
                    "verification_complexity": "level-1",
                    "boundary_contract": "n/a",
                    "forced_visibility": "no",
                },
            },
        )

        self.assertEqual(decision["visible_decision"], "show")
        self.assertEqual(decision["reason"], "routing-surface-fail-closed")

    def test_routing_surface_forced_visibility_overrides_minimal(self):
        decision = agent_visibility_policy.decide(
            profile="minimal",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="routine clean pass already persisted",
            stderr="",
            exit_code=0,
            context={
                "signal": "routine-clean-pass",
                "routing_surface": {
                    "intent_relation": "continuation",
                    "change_depth": "minimal",
                    "focus_layer": "micro",
                    "verification_complexity": "level-1",
                    "boundary_contract": "n/a",
                    "forced_visibility": "yes",
                },
            },
        )

        self.assertEqual(decision["visible_decision"], "force_show")
        self.assertEqual(decision["reason"], "forced-routing-surface")

    def test_mechanical_pending_merge_overrides_routing_surface_no(self):
        # invariant A union: the model's routing-surface judges no forced
        # visibility, but a hook-mechanical structured-state flag is present.
        # The mechanical signal must still force the surface open.
        decision = agent_visibility_policy.decide(
            profile="dynamic",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="",
            stderr="",
            exit_code=0,
            context={
                "pending_merge_undecided": True,
                "routing_surface": {
                    "intent_relation": "accepted-continuation",
                    "change_depth": "minimal",
                    "focus_layer": "micro",
                    "verification_complexity": "level-1",
                    "boundary_contract": "n/a",
                    "forced_visibility": "no",
                    "accepted_continuation_grounded": True,
                },
            },
        )

        self.assertEqual(decision["visible_decision"], "force_show")
        self.assertEqual(decision["reason"], "forced-pending-merge")

    def test_mechanical_security_boundary_forces_show(self):
        # invariant A union: a current-lineage downstream block surfaces as the
        # security_boundary structured-state flag and must force the surface,
        # even when the model-side routing-surface says forced visibility is no.
        decision = agent_visibility_policy.decide(
            profile="minimal",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="routine clean pass already persisted",
            stderr="",
            exit_code=0,
            context={
                "signal": "routine-clean-pass",
                "security_boundary": True,
                "routing_surface": {
                    "intent_relation": "accepted-continuation",
                    "change_depth": "minimal",
                    "focus_layer": "micro",
                    "verification_complexity": "level-1",
                    "boundary_contract": "n/a",
                    "forced_visibility": "no",
                    "accepted_continuation_grounded": True,
                },
            },
        )

        self.assertEqual(decision["visible_decision"], "force_show")
        self.assertEqual(decision["reason"], "forced-security-boundary")

    def test_dynamic_shows_user_relevant_context(self):
        decision = agent_visibility_policy.decide(
            profile="dynamic",
            hook_id="web-search-first",
            event="UserPromptSubmit",
            stdout="external tool behavior claim requires current community evidence",
            stderr="",
            exit_code=0,
            context={"external_tool_claim": True},
        )

        self.assertEqual(decision["visible_decision"], "show")
        self.assertEqual(decision["reason"], "user-relevant-external-tool-claim")

    def test_minimal_hides_duplicate_reminder(self):
        decision = agent_visibility_policy.decide(
            profile="minimal",
            hook_id="hook-reminder",
            event="UserPromptSubmit",
            stdout="duplicate reminder with no state change",
            stderr="",
            exit_code=0,
            context={"duplicate": True},
        )

        self.assertEqual(decision["visible_decision"], "hide")
        self.assertEqual(decision["reason"], "duplicate-reminder")

    def test_visible_decision_values_are_canonical(self):
        for profile in ("strict", "dynamic", "minimal", "quiet"):
            decision = agent_visibility_policy.decide(
                profile=profile,
                hook_id="hook-reminder",
                event="UserPromptSubmit",
                stdout="routine",
                stderr="",
                exit_code=0,
                context={},
            )

            self.assertIn(decision["visible_decision"], {"show", "hide", "force_show"})
            self.assertIsInstance(decision["reason"], str)
            self.assertTrue(decision["reason"])


if __name__ == "__main__":
    unittest.main()
