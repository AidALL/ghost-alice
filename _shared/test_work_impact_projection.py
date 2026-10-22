#!/usr/bin/env python3
"""Tests for the Ghost-ALICE work-impact projector.

Materialization levels (the projector chooses a user-visible level and a
compatibility model hint):
  model_surface: omitted | full
  user_surface:  hidden | focused | full | forced
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work_impact_projection


class TestWorkImpactProjection(unittest.TestCase):
    def test_retired_classes_and_kinds_fail_closed(self):
        # Nothing classifies a hook value as focused, audit-only, routing or debug any more.
        for exposure_class, value_kind in (("focused", "routine"), ("audit-only", "routine"),
                                           ("essential", "routing"), ("routine", "debug")):
            with self.subTest(exposure_class=exposure_class, value_kind=value_kind):
                self.assertEqual(work_impact_projection.project_surfaces(
                    exposure_class=exposure_class, value_kind=value_kind, profile="minimal"), ("full", "full"))

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

    def test_essential_keeps_model_full_user_dials_down_on_minimal(self):
        ud, md = work_impact_projection.project_surfaces(
            exposure_class="essential",
            value_kind="routine",
            profile="dynamic",
            verification_failed=False,
        )
        um, mm = work_impact_projection.project_surfaces(
            exposure_class="essential",
            value_kind="routine",
            profile="minimal",
            verification_failed=False,
        )
        self.assertEqual((ud, md), ("full", "full"))
        self.assertEqual((um, mm), ("focused", "full"))

    def test_make_item_routine_clean_pass_matches_spec_example(self):
        item = work_impact_projection.make_item(
            source_hook="session-start",
            value_key="session-start",
            value_kind="routine",
            exposure_class="routine",
            value="merge-companion-precheck: clean",
            strict_log_ref="strict-log#1",
            profile="dynamic",
        )
        self.assertEqual(item["user_surface"], "hidden")
        self.assertEqual(item["model_surface"], "omitted")
        self.assertEqual(item["work_impact"], "routine-noise")
        self.assertEqual(item["strict_log_ref"], "strict-log#1")
        self.assertEqual(item["value"], "merge-companion-precheck: clean")

    def test_make_item_pending_merge_matches_spec_example(self):
        item = work_impact_projection.make_item(
            source_hook="session-start",
            value_key="pending-merge",
            value_kind="risk",
            exposure_class="forced",
            value="undecided_count=1",
            strict_log_ref="strict-log#2",
            profile="minimal",
        )
        self.assertEqual(item["user_surface"], "forced")
        self.assertEqual(item["model_surface"], "full")
        self.assertEqual(item["work_impact"], "interrupts-work")


if __name__ == "__main__":
    unittest.main()
