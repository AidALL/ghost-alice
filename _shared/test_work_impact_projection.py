#!/usr/bin/env python3
"""Tests for the Ghost-ALICE work-impact projector.

Materialization levels (the projector chooses a user-visible level and a
compatibility model hint):
  model_surface: omitted | marker | digest | focused | full
  user_surface:  hidden | compact | focused | full | forced
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work_impact_projection


class TestWorkImpactProjection(unittest.TestCase):
    def test_forced_class_is_forced_user_full_model_on_every_profile(self):
        for profile in ("strict", "dynamic", "minimal"):
            u, m = work_impact_projection.project_surfaces(
                exposure_class="forced",
                value_kind="risk",
                profile=profile,
                verification_failed=False,
            )
            self.assertEqual((u, m), ("forced", "full"))

    def test_gate_kind_is_forced_full_even_when_routine_classed(self):
        u, m = work_impact_projection.project_surfaces(
            exposure_class="routine",
            value_kind="gate",
            profile="minimal",
            verification_failed=False,
        )
        self.assertEqual((u, m), ("forced", "full"))

    def test_failed_verification_is_forced_full(self):
        u, m = work_impact_projection.project_surfaces(
            exposure_class="routine",
            value_kind="verification",
            profile="minimal",
            verification_failed=True,
        )
        self.assertEqual((u, m), ("forced", "full"))

    def test_unknown_class_fails_closed_to_full(self):
        u, m = work_impact_projection.project_surfaces(
            exposure_class="weird",
            value_kind="routine",
            profile="dynamic",
            verification_failed=False,
        )
        self.assertEqual((u, m), ("full", "full"))

    def test_unknown_profile_fails_closed_to_full(self):
        u, m = work_impact_projection.project_surfaces(
            exposure_class="routine",
            value_kind="routine",
            profile="quiet",
            verification_failed=False,
        )
        self.assertEqual((u, m), ("full", "full"))

    def test_strict_materializes_user_full_but_keeps_routine_model_omitted(self):
        u, m = work_impact_projection.project_surfaces(
            exposure_class="routine",
            value_kind="routine",
            profile="strict",
            verification_failed=False,
        )
        self.assertEqual((u, m), ("full", "omitted"))

    def test_routine_is_user_hidden_and_model_omitted_off_strict(self):
        ud, md = work_impact_projection.project_surfaces(
            exposure_class="routine",
            value_kind="routine",
            profile="dynamic",
            verification_failed=False,
        )
        um, mm = work_impact_projection.project_surfaces(
            exposure_class="routine",
            value_kind="routine",
            profile="minimal",
            verification_failed=False,
        )
        self.assertEqual((ud, md), ("hidden", "omitted"))
        self.assertEqual((um, mm), ("hidden", "omitted"))

    def test_focused_class_dials_user_down_but_keeps_work_impact_digest(self):
        ud, md = work_impact_projection.project_surfaces(
            exposure_class="focused",
            value_kind="routing",
            profile="dynamic",
            verification_failed=False,
        )
        um, mm = work_impact_projection.project_surfaces(
            exposure_class="focused",
            value_kind="routing",
            profile="minimal",
            verification_failed=False,
        )
        self.assertEqual((ud, md), ("focused", "digest"))
        self.assertEqual((um, mm), ("compact", "digest"))
        self.assertNotEqual(ud, um)
        self.assertEqual(md, mm)

    def test_essential_keeps_model_full_user_dials_down_on_minimal(self):
        ud, md = work_impact_projection.project_surfaces(
            exposure_class="essential",
            value_kind="gate" if False else "routing",
            profile="dynamic",
            verification_failed=False,
        )
        um, mm = work_impact_projection.project_surfaces(
            exposure_class="essential",
            value_kind="routing",
            profile="minimal",
            verification_failed=False,
        )
        self.assertEqual((ud, md), ("full", "full"))
        self.assertEqual((um, mm), ("focused", "full"))

    def test_audit_only_is_hidden_omitted_off_strict(self):
        for profile in ("dynamic", "minimal"):
            u, m = work_impact_projection.project_surfaces(
                exposure_class="audit-only",
                value_kind="debug",
                profile=profile,
                verification_failed=False,
            )
            self.assertEqual((u, m), ("hidden", "omitted"))

    def test_make_item_routine_clean_pass_matches_spec_example(self):
        item = work_impact_projection.make_item(
            source_hook="prompt",
            value_key="session-intent-preflight",
            value_kind="routine",
            exposure_class="routine",
            value="session-intent-preflight=observed",
            strict_log_ref="strict-log#1",
            profile="dynamic",
        )
        self.assertEqual(item["user_surface"], "hidden")
        self.assertEqual(item["model_surface"], "omitted")
        self.assertEqual(item["work_impact"], "routine-noise")
        self.assertEqual(item["strict_log_ref"], "strict-log#1")
        self.assertEqual(item["value"], "session-intent-preflight=observed")

    def test_make_item_downstream_block_matches_spec_example(self):
        item = work_impact_projection.make_item(
            source_hook="tool-checkpoint",
            value_key="downstream-block",
            value_kind="risk",
            exposure_class="forced",
            value="current-lineage block, decision=block, reason_ref=...",
            strict_log_ref="strict-log#2",
            profile="minimal",
        )
        self.assertEqual(item["user_surface"], "forced")
        self.assertEqual(item["model_surface"], "full")
        self.assertEqual(item["work_impact"], "interrupts-work")


if __name__ == "__main__":
    unittest.main()
