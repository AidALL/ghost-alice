"""Tests for cross-session conduct_feedback aggregation.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import tempfile
import unittest


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
            {"id": "a", "corrective_rule": "widen scope", "source": "user-explicit",
             "status": "open", "updated_at": "2026-06-01T00:00:00Z"},
        ])
        self._write("claude", "s2", [
            {"id": "a", "corrective_rule": "widen scope", "source": "inferred",
             "status": "open", "updated_at": "2026-06-05T00:00:00Z"},
        ])
        self._write("claude", "s3", [
            {"id": "b", "corrective_rule": "read do not grep", "source": "user-explicit",
             "status": "open", "updated_at": "2026-05-01T00:00:00Z"},
        ])
        self._write("claude", "s4", [
            {"id": "c", "corrective_rule": "x", "source": "user-explicit",
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
        self.assertEqual(top["sources"], ["inferred", "user-explicit"])
        self.assertEqual(result["recommendations"][1]["id"], "b")
        # no fabricated composite weight: the model judges priority from the
        # objective signals, the code does not bake a magic score
        self.assertNotIn("priority_score", top)

    def test_empty_root_returns_no_recommendations(self) -> None:
        result = self.mod.aggregate(self.root, now="2026-06-05T00:00:00Z")
        self.assertEqual(result["recommendation_count"], 0)
        self.assertEqual(result["recommendations"], [])

    def test_session_local_occurrence_count_affects_ranking(self) -> None:
        self._write("codex", "s1", [
            {
                "id": "completioncheck-before-skill-call",
                "summary": "load the verification skill before completion-check",
                "source": "user-explicit",
                "status": "open",
                "occurrence_count": 6,
                "updated_at": "2026-06-01T00:00:00Z",
            },
        ])
        self._write("codex", "s2", [
            {
                "id": "newer-but-single",
                "summary": "newer one-off correction",
                "source": "user-explicit",
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
