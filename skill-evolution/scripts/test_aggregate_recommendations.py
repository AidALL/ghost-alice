"""Tests for cross-session conduct_feedback aggregation.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
SCRIPT = SCRIPT_DIR / "aggregate_recommendations.py"


def load_module():
    spec = importlib.util.spec_from_file_location("aggregate_recommendations", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AggregateRecommendationsTests(unittest.TestCase):
    # The fixtures assign source labels as the response model would; user-said is a claim about origin.
    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="agg-rec-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.mod = load_module()

    def _write(self, platform: str, session: str, conduct: list[dict]) -> None:
        path = self.root / platform / session
        path.mkdir(parents=True, exist_ok=True)
        (path / "intent-state.json").write_text(
            json.dumps({"platform": platform, "session_id": session, "conduct_feedback": conduct}),
            encoding="utf-8",
        )

    def test_recurrence_and_recency_ranked(self) -> None:
        self._write("claude", "s1", [
            {"id": "a", "corrective_rule": "widen scope", "source": "user-said",
             "status": "open", "updated_at": "2026-06-01T00:00:00Z"},
        ])
        self._write("claude", "s2", [
            {"id": "a", "corrective_rule": "widen scope", "source": "interpreted-as",
             "status": "open", "updated_at": "2026-06-05T00:00:00Z"},
        ])
        self._write("claude", "s3", [
            {"id": "b", "corrective_rule": "read do not grep", "source": "user-said",
             "status": "open", "updated_at": "2026-05-01T00:00:00Z"},
        ])
        self._write("claude", "s4", [
            {"id": "c", "corrective_rule": "x", "source": "user-said",
             "status": "encoded", "updated_at": "2026-06-05T00:00:00Z"},
        ])
        result = self.mod.aggregate(self.root, now="2026-06-05T00:00:00Z")
        ids = [r["id"] for r in result["recommendations"]]
        self.assertNotIn("c", ids)
        self.assertEqual(ids[0], "a")
        top = result["recommendations"][0]
        self.assertEqual(top["session_count"], 2)
        self.assertEqual(top["occurrence_count"], 2)
        self.assertEqual(top["last_seen"], "2026-06-05T00:00:00Z")
        self.assertEqual(top["sources"], ["interpreted-as", "user-said"])
        self.assertEqual(top["suggested_next_step"], "necessity-gate")
        self.assertNotIn("decision", top)
        self.assertEqual(result["recommendations"][1]["id"], "b")
        # The response model judges priority from the recurrence and recency signals; the script supplies no composite score.
        self.assertNotIn("priority_score", top)

    def test_root_discovery_and_time_parsing_and_ledger_loading_come_from_their_owners(self) -> None:
        with mock.patch.dict(os.environ, {"GHOST_ALICE_SESSION_INTENT_ROOT": str(self.root)}):
            self.assertEqual(self.mod.discover_root(), self.root)
        self.assertEqual(self.mod.parse_timestamp.__module__, "timestamps")
        self.assertIsNone(self.mod.parse_timestamp("2026-06-05T00:00:00"))
        self.assertEqual(self.mod.load_ledger.__module__, "ledger_access")
        self.assertFalse(hasattr(self.mod, "parse_ts"))
        self.assertFalse(hasattr(self.mod, "_ledger_module"))

    def test_empty_root_returns_no_recommendations(self) -> None:
        result = self.mod.aggregate(self.root, now="2026-06-05T00:00:00Z")
        self.assertEqual(result["recommendation_count"], 0)
        self.assertEqual(result["recommendations"], [])

    def test_saved_recommendations_render_without_rewriting_or_conflicting_aliases(self) -> None:
        self._write("codex", "s1", [{"id": "a", "corrective_rule": "Read the selected source"}])
        report = self.mod.aggregate(self.root)
        item = report["recommendations"][0]
        item["decision"] = item.pop("suggested_next_step")
        original = json.dumps(report, sort_keys=True)
        self.assertIn("-> necessity-gate", self.mod.render_text(report))
        self.assertEqual(json.dumps(report, sort_keys=True), original)
        item["suggested_next_step"] = "necessity-gate"
        self.assertIn("-> necessity-gate", self.mod.render_text(report))
        item["suggested_next_step"] = "observe-more"
        with self.assertRaisesRegex(ValueError, "conflicting.*suggested_next_step"):
            self.mod.render_text(report)

    def test_legacy_missing_sources_stay_unknown_in_reports(self) -> None:
        entries = [{"id": "missing", "corrective_rule": "Recorded correction"}]
        entries.extend({"id": str(index), "source": source, "corrective_rule": "Recorded correction"}
                       for index, source in enumerate((None, "", " ", "user-explicit", "inferred", "legacy-value")))
        self._write("codex", "legacy", entries)
        path = self.root / "codex" / "legacy" / "intent-state.json"
        original = path.read_bytes()
        reports = {item["id"]: item for item in self.mod.aggregate(self.root)["recommendations"]}
        for entry_id in ("missing", "0", "1", "2"):
            self.assertEqual(reports[entry_id]["sources"], ["unknown"])
        self.assertEqual(reports["3"]["sources"], ["user-said"])
        self.assertEqual(reports["4"]["sources"], ["interpreted-as"])
        self.assertEqual(reports["5"]["sources"], ["legacy-value"])
        self.assertEqual(path.read_bytes(), original)

    def test_session_local_occurrence_count_affects_ranking(self) -> None:
        self._write("codex", "s1", [
            {
                "id": "completioncheck-before-skill-call",
                "summary": "load the verification skill before completion-check",
                "source": "user-said",
                "status": "open",
                "occurrence_count": 6,
                "updated_at": "2026-06-01T00:00:00Z",
            },
        ])
        self._write("codex", "s2", [
            {
                "id": "newer-but-single",
                "summary": "newer one-off correction",
                "source": "user-said",
                "status": "open",
                "occurrence_count": 1,
                "updated_at": "2026-06-05T00:00:00Z",
            },
        ])

        result = self.mod.aggregate(self.root, now="2026-06-05T00:00:00Z")
        top = result["recommendations"][0]
        self.assertEqual(top["id"], "completioncheck-before-skill-call")
        self.assertEqual(top["occurrence_count"], 6)
        self.assertEqual(top["summary"], "load the verification skill before completion-check")


if __name__ == "__main__":
    unittest.main()
