"""Checks against a user's private legacy ledger export; skipped unless its paths are supplied.

Dependencies: Python 3.11+ standard library only. Set GHOST_ALICE_LEDGER_CORPUS to an
extracted export root and GHOST_ALICE_LEDGER_CASE_MAP to a local JSON file shaped as
{"fixtures": {real_id: fixture_name}, "pattern_classes": {real_id: pattern_class}}.
Neither the corpus nor the map belongs in the repository.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = os.environ.get("GHOST_ALICE_LEDGER_CORPUS")
CASE_MAP = os.environ.get("GHOST_ALICE_LEDGER_CASE_MAP")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class RealLedgerCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        if not CORPUS:
            self.skipTest("set GHOST_ALICE_LEDGER_CORPUS to an extracted legacy ledger export")
        self.states = sorted(Path(CORPUS).rglob("intent-state.json"))

    def _case_map(self) -> dict:
        if not CASE_MAP:
            self.skipTest("set GHOST_ALICE_LEDGER_CASE_MAP to a local case map")
        return json.loads(Path(CASE_MAP).read_text(encoding="utf-8"))

    def _ids(self) -> set[str]:
        ids = set()
        for path in self.states:
            for entry in json.loads(path.read_text(encoding="utf-8-sig")).get("conduct_feedback") or []:
                if isinstance(entry, dict) and entry.get("id"):
                    ids.add(str(entry["id"]))
        return ids

    def test_every_legacy_state_parses_for_consumers(self) -> None:
        self.assertTrue(self.states)
        for path in self.states:
            with self.subTest(path=path.parent.parent.name):
                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8-sig")), dict)

    def test_replay_fixtures_are_grounded_in_recorded_corrections(self) -> None:
        fixtures = _load(REPO_ROOT / "scripts" / "tests" / "test_verify_or_reuse.py", "verify_or_reuse_fixtures").REAL_CASE_FIXTURES
        ids = self._ids()
        mapping = self._case_map()["fixtures"]
        self.assertTrue(set(fixtures) <= set(mapping.values()))
        for real_id, fixture in mapping.items():
            with self.subTest(real_id=real_id):
                self.assertIn(real_id, ids)
                self.assertIn(fixture, fixtures)

    def test_pattern_classes_reveal_recurrence_that_id_grouping_hides(self) -> None:
        aggregator = _load(REPO_ROOT / "skill-evolution" / "scripts" / "aggregate_recommendations.py", "corpus_aggregator")
        classes = self._case_map()["pattern_classes"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, path in enumerate(self.states):
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                for entry in data.get("conduct_feedback") or []:
                    if isinstance(entry, dict) and entry.get("id") in classes:
                        entry["pattern_class"] = classes[entry["id"]]
                target = root / "corpus" / f"s{index:04d}"
                target.mkdir(parents=True)
                (target / "intent-state.json").write_text(json.dumps(data), encoding="utf-8")
            report = aggregator.aggregate(root, now="2026-09-28T00:00:00Z")
        by_class = [r for r in report["recommendations"] if r.get("member_ids")]
        self.assertTrue(by_class)
        self.assertGreaterEqual(max(r["session_count"] for r in by_class), 2)


if __name__ == "__main__":
    unittest.main()
