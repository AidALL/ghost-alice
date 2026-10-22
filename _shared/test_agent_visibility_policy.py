#!/usr/bin/env python3
"""Tests for Ghost-ALICE agent visibility policy decisions."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_visibility_policy


class TestAgentVisibilityPolicy(unittest.TestCase):
    def test_retired_context_and_phrases_do_not_change_the_decision(self):
        # The runner is decide's only caller and sets none of these keys; no hook prints these phrases.
        contexts = ({"external_tool_claim": True}, {"ambiguous_intent": True}, {"intent_changed": True},
                    {"high_recovery_cost": True}, {"source_selection": True}, {"evidence_quality": True},
                    {"focus_boundary_crossing": True}, {"noop_audit": True}, {"routine_clean_pass": True},
                    {"external_side_effect": True}, {"secret_boundary": True}, {"security_boundary": True},
                    {"destructive": True}, {"unresolved_completion": True}, {"duplicate": True},
                    {"signal": "routine-clean-pass"}, {"decision": "deny"},
                    {"routing_surface": {"forced_visibility": "yes", "focus_layer": "meta"}})
        phrases = ("decision: deny", "tool checkpoint required", "Ledger module present but failed to load",
                   "Ledger write failed", "verification failed", "unresolved completion")
        cases = [("io-trace", "", context) for context in contexts]
        cases += [("io-trace", phrase, {}) for phrase in phrases]
        cases.append(("completion-reminder", "[completion-check]", {}))
        for hook_id, stdout, context in cases:
            with self.subTest(hook_id=hook_id, stdout=stdout, context=context):
                decision = agent_visibility_policy.decide(profile="minimal", hook_id=hook_id, event="PostToolUse",
                                                          stdout=stdout, stderr="", exit_code=0, context=context)
                self.assertEqual(decision, {"visible_decision": "show", "reason": "minimal-default-visible"})

    def _decide(self, profile: str, **fields) -> dict:
        values = dict(hook_id="session-start", event="SessionStart", stdout="", stderr="", exit_code=0, context={})
        values.update(fields)
        return agent_visibility_policy.decide(profile=profile, **values)

    def test_strict_profile_shows_declared_routine(self):
        decision = self._decide("strict", stdout="merge-companion-precheck: clean (hook-verified) at session start.",
                                context={"declared_surface": "routine"})

        self.assertEqual(decision, {"visible_decision": "show", "reason": "strict-profile"})

    def test_unknown_profile_falls_back_to_strict(self):
        decision = self._decide("quiet", stdout="routine notice", context={"declared_surface": "routine"})

        self.assertEqual(decision, {"visible_decision": "show", "reason": "strict-profile"})

    def test_failed_hook_output_is_forced(self):
        decision = self._decide("dynamic", hook_id="completion", event="Stop", stderr="verification failed",
                                exit_code=1, context=None)

        self.assertEqual(decision, {"visible_decision": "force_show", "reason": "forced-nonzero-exit"})

    def test_dynamic_hides_declared_routine(self):
        decision = self._decide("dynamic", stdout="merge-companion-precheck: clean (hook-verified) at session start.",
                                context={"declared_surface": "routine"})

        self.assertEqual(decision, {"visible_decision": "hide", "reason": "declared-routine"})

    def test_pending_merge_state_is_forced(self):
        decision = self._decide("dynamic", context={"pending_merge_undecided": True})

        self.assertEqual(decision, {"visible_decision": "force_show", "reason": "forced-pending-merge"})

    def test_block_output_is_forced(self):
        decision = self._decide("minimal", hook_id="completion", event="Stop",
                                stdout='{"decision": "block", "reason": "Continue the approved work."}')

        self.assertEqual(decision, {"visible_decision": "force_show", "reason": "forced-verification"})

    def test_completion_assessment_issue_is_forced(self):
        decision = self._decide("minimal", hook_id="completion", event="Stop", stdout=(
            '{"continue": true, "response_assessment": {"body_issue": "requires a [completion-check]"}}'))

        self.assertEqual(decision, {"visible_decision": "force_show", "reason": "forced-verification"})

    def test_visible_decision_values_are_canonical(self):
        for profile in ("strict", "dynamic", "minimal", "quiet"):
            decision = self._decide(profile, hook_id="io-trace", event="PostToolUse", stdout="routine")

            self.assertIn(decision["visible_decision"], agent_visibility_policy.VISIBLE_DECISIONS)
            self.assertIsInstance(decision["reason"], str)
            self.assertTrue(decision["reason"])


if __name__ == "__main__":
    unittest.main()
