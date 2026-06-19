"""TDD for Phase P6: durable autopilot approved-run work-item state.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_autopilot_state_p6.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import autopilot_state as aps  # noqa: E402


def _item(item_id: str, *, status: str = "ready", depends_on: list[str] | None = None) -> dict:
    return {
        "id": item_id,
        "status": status,
        "focus_layer": "meso",
        "depends_on": depends_on or [],
        "prompt": f"Do {item_id}",
        "acceptance_criteria": [f"{item_id}-ac"],
        "allowed_surface": ["_shared/..."],
        "completion": {
            "state": "not_started",
            "verdict": None,
            "evidence": [],
            "completion_check_digest": None,
            "reopen_target": None,
        },
        "attempt": 0,
    }


def _write_run(run_dir: Path, items: list[dict], *, decision: dict | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "approved-run.json").write_text(
        json.dumps({
            "schema_version": "autopilot-run.v1",
            "run_id": "run-1",
            "approved": True,
            "status": "running",
            "budget": {"remaining_steps": 3},
        }),
        encoding="utf-8",
    )
    aps.write_work_items(run_dir / "tasks.jsonl", items)
    if decision is not None:
        (run_dir / "consistency-decision.json").write_text(json.dumps(decision), encoding="utf-8")


def _write_run_config(
    run_dir: Path,
    *,
    approved: bool = True,
    status: str = "running",
    remaining_steps: int = 3,
) -> None:
    (run_dir / "approved-run.json").write_text(
        json.dumps({
            "schema_version": "autopilot-run.v1",
            "run_id": "run-1",
            "approved": approved,
            "status": status,
            "budget": {"remaining_steps": remaining_steps},
        }),
        encoding="utf-8",
    )


class AutopilotStateTest(unittest.TestCase):
    def test_tasks_jsonl_preserves_completed_items_and_derives_ready_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.jsonl"
            items = [_item("a", status="completed"), _item("b", depends_on=["a"])]
            aps.write_work_items(path, items)

            loaded = aps.read_work_items(path)
            ready = aps.derive_ready_queue(loaded)

        self.assertEqual([item["id"] for item in loaded], ["a", "b"])
        self.assertEqual(ready, ["b"])

    def test_continue_next_requires_completion_check_evidence(self):
        items = [_item("a", status="running")]

        with self.assertRaises(aps.AutopilotStateError):
            aps.apply_consistency_decision(items, "a", "continue_next")

        updated = aps.apply_consistency_decision(
            items,
            "a",
            "continue_next",
            completion_check_digest="sha256:abc",
            verdict="pass",
            evidence=["pytest _shared/test_autopilot_state_p6.py"],
        )

        self.assertEqual(updated[0]["status"], "completed")
        self.assertEqual(updated[0]["completion"]["state"], "completed")
        self.assertEqual(updated[0]["completion"]["completion_check_digest"], "sha256:abc")

    def test_consistency_decision_rejects_non_running_targets(self):
        cases = [
            (
                "ready",
                "continue_next",
                {
                    "completion_check_digest": "sha256:abc",
                    "verdict": "pass",
                    "evidence": ["pytest _shared/test_autopilot_state_p6.py"],
                },
            ),
            ("completed", "retry_same_unit", {}),
            ("not_applicable", "reopen_macro", {}),
        ]

        for status, decision, kwargs in cases:
            with self.subTest(status=status, decision=decision):
                items = [_item("a", status=status)]

                with self.assertRaises(aps.AutopilotStateError) as ctx:
                    aps.apply_consistency_decision(items, "a", decision, **kwargs)

                message = str(ctx.exception).lower()
                self.assertIn("consistency decision state transition", message)
                self.assertIn("requires running work item", message)
                self.assertEqual(items[0]["status"], status)

    def test_retry_same_unit_keeps_item_and_increments_attempt(self):
        items = [_item("a", status="running")]

        updated = aps.apply_consistency_decision(items, "a", "retry_same_unit")

        self.assertEqual([item["id"] for item in updated], ["a"])
        self.assertEqual(updated[0]["status"], "ready")
        self.assertEqual(updated[0]["attempt"], 1)
        self.assertEqual(updated[0]["completion"]["state"], "retry")

    def test_reopen_decision_marks_item_without_popping_it(self):
        items = [_item("a", status="running")]

        updated = aps.apply_consistency_decision(items, "a", "reopen_macro")

        self.assertEqual([item["id"] for item in updated], ["a"])
        self.assertEqual(updated[0]["status"], "reopened")
        self.assertEqual(updated[0]["completion"]["state"], "reopened")
        self.assertEqual(updated[0]["completion"]["reopen_target"], "macro")

    def test_replan_rewrites_open_items_but_preserves_terminal_history(self):
        items = [
            _item("done", status="completed"),
            _item("skip", status="not_applicable"),
            _item("old-ready", status="ready"),
            _item("old-blocked", status="blocked"),
        ]
        replacement = [_item("new-first"), _item("new-second", depends_on=["new-first"])]

        updated = aps.rewrite_open_work_items(items, replacement)

        self.assertEqual([item["id"] for item in updated], ["done", "skip", "new-first", "new-second"])
        self.assertEqual(aps.derive_ready_queue(updated), ["new-first"])

    def test_replan_allows_new_open_items_to_depend_on_preserved_terminal_history(self):
        items = [_item("done", status="completed"), _item("old-ready")]
        replacement = [_item("new-ready", depends_on=["done"])]

        updated = aps.rewrite_open_work_items(items, replacement)

        self.assertEqual([item["id"] for item in updated], ["done", "new-ready"])
        self.assertEqual(aps.derive_ready_queue(updated), ["new-ready"])

    def test_adapter_payload_without_run_dir_is_noop(self):
        self.assertEqual(aps.adapter_payload_from_env({}), {"continue": True, "systemMessage": ""})

    def test_unapproved_stopped_or_exhausted_run_is_noop_without_mutating_tasks(self):
        cases = [
            {"approved": False, "status": "running", "remaining_steps": 3},
            {"approved": True, "status": "stopped", "remaining_steps": 3},
            {"approved": True, "status": "running", "remaining_steps": 0},
        ]

        for run_config in cases:
            with self.subTest(run_config=run_config), tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                _write_run(run_dir, [_item("next")])
                before = (run_dir / "tasks.jsonl").read_text(encoding="utf-8")
                _write_run_config(run_dir, **run_config)

                payload = aps.advance_approved_run(run_dir)
                after = (run_dir / "tasks.jsonl").read_text(encoding="utf-8")
                events_exists = (run_dir / "events.jsonl").exists()

            self.assertEqual(payload, {"continue": True, "systemMessage": ""})
            self.assertEqual(after, before)
            self.assertFalse(events_exists)

    def test_no_ready_item_records_event_and_returns_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_run(run_dir, [_item("waiting", depends_on=["done"]), _item("done", status="running")])

            payload = aps.advance_approved_run(run_dir)
            items = aps.read_work_items(run_dir / "tasks.jsonl")
            events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(payload, {"continue": True, "systemMessage": ""})
        self.assertEqual([item["status"] for item in items], ["ready", "running"])
        self.assertEqual(events[0]["event"], "no_ready_item")
        self.assertEqual(events[0]["run_id"], "run-1")

    def test_approved_run_emits_next_ready_item_and_records_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_run(run_dir, [_item("next")])

            payload = aps.advance_approved_run(run_dir)
            items = aps.read_work_items(run_dir / "tasks.jsonl")
            events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertTrue(payload["continue"])
        self.assertIn("work-item: next", payload["systemMessage"])
        self.assertIn("Do next", payload["systemMessage"])
        self.assertEqual(items[0]["status"], "running")
        self.assertEqual(events[0]["event"], "continue_next_item")
        self.assertEqual(events[0]["work_item_id"], "next")

    def test_consistency_decision_completes_running_item_before_selecting_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_run(
                run_dir,
                [_item("current", status="running"), _item("next", depends_on=["current"])],
                decision={
                    "decision_id": "d1",
                    "work_item_id": "current",
                    "decision": "continue_next",
                    "completion_check_digest": "sha256:abc",
                    "verdict": "pass",
                    "evidence": ["pytest _shared/test_autopilot_state_p6.py"],
                },
            )

            payload = aps.advance_approved_run(run_dir)
            items = aps.read_work_items(run_dir / "tasks.jsonl")
            decision_removed = not (run_dir / "consistency-decision.json").exists()
            applied_decision_exists = (run_dir / "consistency-decision.applied.json").is_file()

        self.assertEqual(items[0]["status"], "completed")
        self.assertEqual(items[1]["status"], "running")
        self.assertTrue(decision_removed)
        self.assertTrue(applied_decision_exists)
        self.assertIn("work-item: next", payload["systemMessage"])

    def test_retry_decision_requeues_same_item_without_popping_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_run(
                run_dir,
                [_item("current", status="running")],
                decision={
                    "decision_id": "d-retry",
                    "work_item_id": "current",
                    "decision": "retry_same_unit",
                },
            )

            payload = aps.advance_approved_run(run_dir)
            items = aps.read_work_items(run_dir / "tasks.jsonl")

        self.assertEqual([item["id"] for item in items], ["current"])
        self.assertEqual(items[0]["status"], "running")
        self.assertEqual(items[0]["attempt"], 1)
        self.assertIn("work-item: current", payload["systemMessage"])

    def test_consistency_decision_rejects_non_array_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_run(
                run_dir,
                [_item("current", status="running")],
                decision={
                    "decision_id": "d-bad",
                    "work_item_id": "current",
                    "decision": "continue_next",
                    "completion_check_digest": "sha256:abc",
                    "verdict": "pass",
                    "evidence": "pytest _shared/test_autopilot_state_p6.py",
                },
            )

            with self.assertRaises(aps.AutopilotStateError):
                aps.advance_approved_run(run_dir)

            items = aps.read_work_items(run_dir / "tasks.jsonl")

        self.assertEqual(items[0]["status"], "running")


if __name__ == "__main__":
    unittest.main()
