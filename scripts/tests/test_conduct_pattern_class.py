"""Stable conduct pattern classes let skill-evolution see recurrence across sessions.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py"
AGGREGATOR_PATH = REPO_ROOT / "skill-evolution" / "scripts" / "aggregate_recommendations.py"
SCHEMA_DOC = REPO_ROOT / "session-intent-analyzer" / "references" / "ledger-schema.md"
INSTALL_HOOKS = REPO_ROOT / "_shared" / "install_hooks.py"
TIMESTAMP = "2026-09-28T00:00:00Z"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses and type lookups resolve through sys.modules
    spec.loader.exec_module(module)
    return module


LEDGER = _load(LEDGER_PATH, "session_intent_ledger_under_test")
AGGREGATOR = _load(AGGREGATOR_PATH, "aggregate_recommendations_under_test")


def _entry(entry_id: str, pattern_class: str | None = None) -> dict:
    entry = {"id": entry_id, "summary": f"{entry_id} summary", "corrective_rule": f"{entry_id} rule",
             "source": "user-explicit", "status": "open", "occurrence_count": 1, "updated_at": TIMESTAMP}
    if pattern_class is not None:
        entry["pattern_class"] = pattern_class
    return entry


class PatternClassNormalizationTests(unittest.TestCase):
    def test_known_pattern_class_is_kept(self) -> None:
        normalized = LEDGER.normalize_conduct_feedback(_entry("repeat-status", "restatement-loop"), TIMESTAMP)
        self.assertEqual(normalized["pattern_class"], "restatement-loop")

    def test_unknown_pattern_class_is_rejected_with_the_allowed_values(self) -> None:
        with self.assertRaises(ValueError) as caught:
            LEDGER.normalize_conduct_feedback(_entry("odd", "made-up-class"), TIMESTAMP)
        message = str(caught.exception)
        self.assertIn("'made-up-class'", message)
        for pattern_class in LEDGER.CONDUCT_PATTERN_CLASSES:
            self.assertIn(f"'{pattern_class}'", message)

    def test_missing_pattern_class_stays_unclassified(self) -> None:
        self.assertEqual(LEDGER.normalize_conduct_feedback(_entry("plain"), TIMESTAMP)["pattern_class"], "")

    def test_unknown_source_is_rejected_with_the_allowed_values(self) -> None:
        with self.assertRaises(ValueError) as caught:
            LEDGER.normalize_conduct_feedback({**_entry("typo"), "source": "infered"}, TIMESTAMP)
        for value in ("'infered'", "'inferred'", "'user-explicit'"):
            self.assertIn(value, str(caught.exception))

    def test_both_unknown_values_are_reported_in_one_error(self) -> None:
        with self.assertRaises(ValueError) as caught:
            LEDGER.normalize_conduct_feedback({**_entry("both", "made-up-class"), "source": "infered"}, TIMESTAMP)
        self.assertIn("'infered'", str(caught.exception))
        self.assertIn("'made-up-class'", str(caught.exception))

    def test_stored_entries_are_not_revalidated(self) -> None:
        stored = {**_entry("legacy-odd"), "source": "legacy-value", "pattern_class": "retired-class"}
        merged = LEDGER.merge_conduct_feedback([stored], [_entry("fresh", "restatement-loop")], TIMESTAMP)
        self.assertEqual([entry["id"] for entry in merged], ["legacy-odd", "fresh"])

    def test_missing_source_keeps_the_compatible_default(self) -> None:
        entry = _entry("legacy")
        del entry["source"]
        self.assertEqual(LEDGER.normalize_conduct_feedback(entry, TIMESTAMP)["source"], "user-explicit")

    def test_update_classifies_an_existing_entry_and_later_updates_keep_it(self) -> None:
        merged = LEDGER.merge_conduct_feedback([_entry("repeat-status")],
                                               [{"id": "repeat-status", "pattern_class": "restatement-loop"}], TIMESTAMP)
        merged = LEDGER.merge_conduct_feedback(merged, [{"id": "repeat-status", "status": "encoded"}], TIMESTAMP)
        self.assertEqual(merged[0]["pattern_class"], "restatement-loop")

    def test_vocabulary_names_the_observed_failure_families(self) -> None:
        self.assertTrue({"redundant-verification", "missed-reverification", "wrong-copy-evidence", "restatement-loop",
                         "under-delivery", "premise-mismatch-without-stop"}.issubset(LEDGER.CONDUCT_PATTERN_CLASSES))

    def test_schema_reference_lists_every_class(self) -> None:
        text = SCHEMA_DOC.read_text(encoding="utf-8")
        for pattern_class in LEDGER.CONDUCT_PATTERN_CLASSES:
            with self.subTest(pattern_class=pattern_class):
                self.assertIn(f"`{pattern_class}`", text)


class CaptureReminderTests(unittest.TestCase):
    def test_capture_reminder_asks_for_pattern_class(self) -> None:
        self.assertIn("(id, failure_pattern, corrective_rule, pattern_class, source=user-explicit, status=open)",
                      INSTALL_HOOKS.read_text(encoding="utf-8"))


class CliRejectionTests(unittest.TestCase):
    def test_rejected_delta_writes_nothing_and_lists_the_allowed_values(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        env = {**os.environ, "CODEX_THREAD_ID": "", "GHOST_ALICE_SESSION_ID": ""}
        base = [sys.executable, str(LEDGER_PATH), "--root", temporary.name, "--platform", "claude",
                "--session-id", "cli-rejection"]

        def run(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run([*base, *args], capture_output=True, text=True, encoding="utf-8", env=env, check=False)

        self.assertEqual(run("--input", "first input").returncode, 0)
        before = json.loads(run("--read-state").stdout)
        delta = {"current_goal": "must not be written", "conduct_feedback": [{**_entry("typo"), "source": "infered"}]}
        result = run("--expected-input-event-id", before["latest_input_event_id"], "--delta-json", json.dumps(delta))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'user-explicit'", result.stderr)
        after = json.loads(run("--read-state").stdout)
        self.assertEqual((after["ledger_revision"], after.get("current_goal")),
                         (before["ledger_revision"], before.get("current_goal")))


class AggregatorGroupingTests(unittest.TestCase):
    def _root(self, sessions: dict[tuple[str, str], list[dict]]) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for (platform, session), entries in sessions.items():
            directory = root / platform / session
            directory.mkdir(parents=True)
            (directory / "intent-state.json").write_text(json.dumps({
                "platform": platform, "session_id": session, "conduct_feedback": entries}), encoding="utf-8")
        return root

    def test_different_ids_in_one_class_count_as_cross_session_recurrence(self) -> None:
        root = self._root({("claude", "s1"): [_entry("repeated-status", "restatement-loop")],
                           ("codex", "s2"): [_entry("parroted-examples", "restatement-loop")]})
        report = AGGREGATOR.aggregate(root, now=TIMESTAMP)
        self.assertEqual(report["recommendation_count"], 1)
        record = report["recommendations"][0]
        self.assertEqual((record["id"], record["session_count"], record["occurrence_count"]), ("restatement-loop", 2, 2))
        self.assertEqual(record["member_ids"], ["parroted-examples", "repeated-status"])

    def test_unclassified_entries_keep_id_grouping(self) -> None:
        root = self._root({("claude", "s1"): [_entry("a")], ("codex", "s2"): [_entry("b")]})
        report = AGGREGATOR.aggregate(root, now=TIMESTAMP)
        self.assertEqual(sorted(r["id"] for r in report["recommendations"]), ["a", "b"])
        self.assertTrue(all(r["session_count"] == 1 for r in report["recommendations"]))


if __name__ == "__main__":
    unittest.main()
