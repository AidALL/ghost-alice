"""Identity, optimistic input binding and atomic SQLite state and event commits.

Dependencies: Python 3.11+ standard library only.
"""
import importlib.util
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


MODULE = Path(__file__).with_name("session_intent_ledger.py")


def load_module():
    spec = importlib.util.spec_from_file_location("transaction_ledger", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = load_module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = dict(root=self.root, platform="codex", session_id="session-a")
        self.paths = self.ledger.session_paths(**self.identity)

    def write(self, **kwargs):
        return self.ledger.record_turn(**self.identity, **kwargs)

    def state(self):
        return self.ledger.read_session_state(**self.identity)

    def events(self):
        return self.ledger.read_session_events(**self.identity)

    def write_database_state(self, state, *, sync_revision=False):
        # Deliberately corrupt persisted payload for validation tests, without
        # asking the production writer to admit malformed state.
        with self.ledger.storage_transaction(self.root) as connection:
            if sync_revision:
                connection.execute("UPDATE sessions SET revision=?, state_json=? WHERE platform=? AND session_id=?",
                                   (state["ledger_revision"], json.dumps(state), "codex", "session-a"))
            else:
                connection.execute("UPDATE sessions SET state_json=? WHERE platform=? AND session_id=?",
                                   (json.dumps(state), "codex", "session-a"))

    def database_dump(self):
        connection = sqlite3.connect(self.ledger.storage_database_path(self.root).as_uri() + "?mode=ro", uri=True)
        try:
            return connection.execute("PRAGMA user_version").fetchone(), tuple(connection.iterdump())
        finally:
            connection.close()

    def database_payload(self):
        with sqlite3.connect(self.ledger.storage_database_path(self.root)) as connection:
            return connection.execute("SELECT revision,input_event_id,state_json FROM sessions WHERE platform=? AND session_id=?",
                                      ("codex", "session-a")).fetchone()

    def fail_trigger(self, name, clause):
        with self.ledger.storage_transaction(self.root) as connection:
            connection.execute(f"CREATE TRIGGER {name} {clause} BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END")

    def remove_trigger(self, name):
        with self.ledger.storage_transaction(self.root) as connection:
            connection.execute(f"DROP TRIGGER {name}")

    def observed_id(self):
        rows = self.events()
        return [x["event_id"] for x in rows if x["event"] == "user-input-observed"][-1]

    def criterion(self):
        return self.state()["acceptance_criteria"][0]

    def setup_criterion(self):
        self.write(intent_delta={"acceptance_criteria": [
            {"id": "report", "summary": "Validate revision one", "source": "user-explicit"}]})

    def mark(self, **kwargs):
        return self.ledger.mark_acceptance_criterion_met(
            **self.identity, criterion_id="report", completion_check_digest="a" * 64, **kwargs)

    def test_persisted_identity_mismatch_cannot_be_overwritten(self):
        for key, wrong in [("session_id", "other-session"), ("platform", "claude")]:
            with self.subTest(key=key):
                self.paths["dir"].mkdir(parents=True, exist_ok=True)
                bad = self.ledger.default_state("codex", "session-a")
                bad[key] = wrong
                self.ledger.write_json(self.paths["state"], bad)
                before = self.paths["state"].read_bytes()
                with self.assertRaisesRegex(ValueError, "identity"):
                    self.write(intent_delta={"current_goal": "must not replace"})
                self.assertEqual(self.paths["state"].read_bytes(), before)
                self.assertFalse(self.paths["events"].exists())

    def test_corrupt_and_headerless_state_cannot_be_reset_by_writer(self):
        self.paths["dir"].mkdir(parents=True)
        for value in ["{partial", "[]", '{"current_goal":"old"}']:
            with self.subTest(value=value):
                self.paths["state"].write_text(value)
                with self.assertRaises(ValueError):
                    self.write(intent_delta={"current_goal": "must not replace"})
                self.assertEqual(self.paths["state"].read_text(), value)

    def test_normalized_session_collision_is_rejected(self):
        self.write(intent_delta={"current_goal": "legitimate"})
        before = self.state()
        for unsafe in ["session/a", " session-a", ".session-a", "session-a" + "x" * 130]:
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                self.ledger.record_turn(root=self.root, platform="codex", session_id=unsafe)
        self.assertEqual(self.state(), before)

    def test_active_input_requires_explicit_expected_input(self):
        self.write(raw_user_input="first request")
        before = self.state()
        with self.assertRaisesRegex(ValueError, "expected.input"):
            self.write(intent_delta={"current_goal": "unbound"})
        self.assertEqual(self.state(), before)

    def test_stale_input_is_rejected_after_new_intake(self):
        self.write(raw_user_input="first request")
        old_id = self.observed_id()
        self.write(raw_user_input="revised request")
        before = self.state()
        with self.assertRaisesRegex(ValueError, "stale"):
            self.write(intent_delta={"current_goal": "old goal"}, expected_input_event_id=old_id)
        self.assertEqual(self.state(), before)
        self.write(intent_delta={"current_goal": "new goal"}, expected_input_event_id=self.observed_id())
        self.assertEqual(self.state()["current_goal"], "new goal")

    def test_expected_revision_rejects_a_lost_snapshot(self):
        self.write()
        self.write(intent_delta={"constraints": ["keep"]})
        with self.assertRaisesRegex(ValueError, "revision"):
            self.write(intent_delta={"constraints": ["stale"]}, expected_revision=1)
        self.assertEqual(self.state()["constraints"], ["keep"])

    def test_completion_requires_bound_criterion(self):
        self.setup_criterion()
        with self.assertRaisesRegex(ValueError, "expected.criterion"):
            self.mark()
        self.assertEqual(self.criterion()["status"], "unmet")

    def test_completion_snapshot_requires_boolean_admission_not_integer_alias(self):
        self.setup_criterion()
        with self.assertRaisesRegex(ValueError, "criterion"):
            self.mark(expected_criterion=dict(self.criterion(), admitted=1))

    def test_old_completion_cannot_close_revised_criterion(self):
        self.setup_criterion()
        approved = self.criterion()
        self.write(intent_delta={"acceptance_criteria": [dict(approved, summary="Validate revision two")]})
        with self.assertRaisesRegex(ValueError, "criterion"):
            self.mark(expected_criterion=approved)
        self.assertEqual(self.criterion()["status"], "unmet")

    def test_unadmitted_criterion_cannot_be_marked(self):
        self.setup_criterion()
        self.write(intent_delta={"acceptance_criteria": [dict(self.criterion(), admitted=False)]})
        with self.assertRaisesRegex(ValueError, "admitted"):
            self.mark(expected_criterion=self.criterion())

    def test_concurrent_subprocess_updates_retain_all_fields(self):
        self.write()
        worker = '''import importlib.util, pathlib, sys, time
spec=importlib.util.spec_from_file_location("ledger", sys.argv[1])
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
apply=m.apply_delta
def slow(state, delta):
    time.sleep(0.08)
    return apply(state, delta)
m.apply_delta=slow
root=pathlib.Path(sys.argv[2])
(root / ("ready-" + sys.argv[3])).touch()
while not (root / "start").exists(): time.sleep(0.005)
m.record_turn(root=root, platform="codex", session_id="session-a", intent_delta={"constraints":[sys.argv[3]]})
'''
        processes = [subprocess.Popen([sys.executable, "-B", "-c", worker, str(MODULE), str(self.root), str(i)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for i in range(8)]
        try:
            deadline = time.monotonic() + 15
            while len(list(self.root.glob("ready-*"))) != len(processes):
                if time.monotonic() > deadline:
                    self.fail("workers did not reach start barrier")
                time.sleep(0.01)
            (self.root / "start").touch()
            for process in processes:
                out, err = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, out + err)
            self.assertEqual(set(self.state()["constraints"]), set(map(str, range(8))))
            self.assertEqual(self.state()["ledger_revision"], 9)
            self.assertEqual(len(self.events()), 9)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_atomic_reader_never_sees_partial_state(self):
        self.write()
        failures, reads = [], []
        finished = threading.Event()
        def reader():
            while not finished.is_set():
                try:
                    value = self.state()
                    reads.append(value["session_id"])
                except Exception as exc:
                    failures.append(type(exc).__name__)
        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for i in range(12):
                self.write(intent_delta={"current_goal": str(i) + "x" * 500000})
        finally:
            finished.set()
            thread.join(timeout=5)
        self.assertTrue(reads)
        self.assertEqual(failures, [])

    def test_semantic_history_preserves_corrections_without_raw_extra_fields(self):
        self.write(raw_user_input="RAW_USER_PROMPT", intent_delta={"current_goal": "Inspect configuration"})
        anchor = self.observed_id()
        self.write(expected_input_event_id=anchor, intent_delta={
            "current_goal": "Repair the diagnosed configuration",
            "latest_scope": {"allowed": ["configuration"], "prohibited": ["publish"], "raw_prompt": "RAW_SCOPE"},
            "decisions": [{"id": "repair", "summary": "Repair authorized", "tool_output": "RAW_TOOL_OUTPUT"}],
            "model_security_decision": {"decision": "allow", "reason": "RAW_REASON", "input_event_id": anchor},
            "consumer_hints": {"raw": ["RAW_HINT"]},
        })
        rows = self.events()
        changes = rows[-1]["semantic_changes"]
        self.assertEqual(changes["current_goal"], {"before": "Inspect configuration", "after": "Repair the diagnosed configuration"})
        self.assertEqual(changes["latest_scope"]["after"], {"allowed": ["configuration"], "prohibited": ["publish"]})
        self.assertEqual(changes["decisions"]["after"][0]["summary"], "Repair authorized")
        self.assertEqual(rows[-1]["input_event_id"], anchor)
        self.assertEqual(rows[-1]["ledger_revision"], 2)
        for raw in ("RAW_USER_PROMPT", "RAW_SCOPE", "RAW_TOOL_OUTPUT", "RAW_REASON", "RAW_HINT"):
            self.assertNotIn(raw, json.dumps(self.events()))

    def test_failed_event_insert_rolls_back_state_and_retry_does_not_duplicate_feedback(self):
        self.write()
        before = self.state(), self.events()
        delta = {"conduct_feedback": [{"id": "scope", "summary": "Preserve the approved scope", "source": "user-explicit"}]}
        self.fail_trigger("fail_event", "BEFORE INSERT ON intent_events")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected storage failure"):
            self.write(intent_delta=delta, expected_revision=1)
        self.assertEqual((self.state(), self.events()), before)
        self.remove_trigger("fail_event")
        self.write(intent_delta=delta, expected_revision=1)
        self.assertEqual(self.state()["ledger_revision"], 2)
        self.assertEqual(self.state()["conduct_feedback"][0]["occurrence_count"], 1)
        self.assertEqual(len(self.events()), 2)
        with self.assertRaisesRegex(ValueError, "revision"):
            self.write(intent_delta=delta, expected_revision=1)
        self.assertEqual(self.state()["conduct_feedback"][0]["occurrence_count"], 1)
        self.assertEqual(len(self.events()), 2)

    def test_failure_after_event_insert_rolls_back_and_retry_keeps_one_event(self):
        self.write()
        before = self.state(), self.events()
        self.fail_trigger("fail_after_event", "AFTER INSERT ON intent_events")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected storage failure"):
            self.write(intent_delta={"constraints": ["preserved"]})
        self.assertEqual((self.state(), self.events()), before)
        self.remove_trigger("fail_after_event")
        self.write(expected_revision=1, intent_delta={"constraints": ["preserved"]})
        self.assertEqual(self.state()["constraints"], ["preserved"])
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.ledger.read_session_state(**self.identity), self.state())
        self.assertEqual(len(self.events()), 2)

    def test_failed_state_update_retains_previous_state_and_audit(self):
        self.write()
        before = self.state(), self.events()
        self.fail_trigger("fail_state", "BEFORE UPDATE ON sessions")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected storage failure"):
            self.write(intent_delta={"current_goal": "not committed"})
        self.assertEqual((self.state(), self.events()), before)

    def test_read_only_snapshot_never_creates_runtime_files(self):
        self.assertEqual(self.ledger.read_session_state(**self.identity, recover_audit=False).get("ledger_revision", 0), 0)
        self.assertEqual(list(self.root.rglob("*")), [])

    def test_mark_uses_input_cas_and_exact_persisted_identity(self):
        self.setup_criterion()
        approved = self.criterion()
        self.write(raw_user_input="old request")
        old = self.observed_id()
        self.write(raw_user_input="new request")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.mark(expected_input_event_id=old, expected_criterion=approved)
        self.assertEqual(self.criterion()["status"], "unmet")
        state = self.state()
        anchor = self.observed_id()
        state["platform"] = "claude"
        self.write_database_state(state)
        with self.assertRaisesRegex(ValueError, "identity"):
            self.mark(expected_input_event_id=anchor, expected_criterion=approved)

    def test_security_record_cannot_launder_an_old_input_using_fresh_cas(self):
        self.write(raw_user_input="old input")
        old = self.observed_id()
        self.write(raw_user_input="new input")
        with self.assertRaisesRegex(ValueError, "security decision input"):
            self.write(expected_input_event_id=self.observed_id(), intent_delta={
                "model_security_decision": {"decision": "block", "input_event_id": old}})

    def test_legacy_valid_header_migrates_input_anchor_with_cas(self):
        self.write(raw_user_input="legacy input")
        anchor = self.observed_id()
        legacy = self.state()
        original_events = self.events()
        self.root = self.root / "legacy-import"
        self.identity["root"] = self.root
        self.paths = self.ledger.session_paths(**self.identity)
        for key in ("ledger_revision", "latest_input_event_id", "latest_input_digest", "latest_input_char_count"):
            legacy.pop(key)
        self.ledger.write_json(self.paths["state"], legacy)
        self.paths["events"].write_text("".join(json.dumps(row) + "\n" for row in original_events))
        with self.assertRaisesRegex(ValueError, "expected.input"):
            self.write(intent_delta={"current_goal": "unbound"})
        self.write(expected_input_event_id=anchor, intent_delta={"current_goal": "bound"})
        self.assertEqual(self.state()["latest_input_event_id"], anchor)
        self.assertEqual(self.state()["ledger_revision"], 1)

    def test_delayed_subprocess_cannot_apply_old_input_after_new_intake(self):
        self.write(raw_user_input="first input")
        old = self.observed_id()
        worker = '''import importlib.util, pathlib, sys, time
spec=importlib.util.spec_from_file_location("ledger", sys.argv[1])
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
root=pathlib.Path(sys.argv[2]); (root / "ready").touch()
while not (root / "release").exists(): time.sleep(0.005)
try:
    m.record_turn(root=root, platform="codex", session_id="session-a", expected_input_event_id=sys.argv[3], intent_delta={"current_goal":"STALE"})
except ValueError as error:
    print(str(error)); sys.exit(0)
sys.exit(7)
'''
        process = subprocess.Popen([sys.executable, "-B", "-c", worker, str(MODULE), str(self.root), old],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not (self.root / "ready").exists():
                if time.monotonic() > deadline:
                    self.fail("worker did not capture its old-input operation")
                time.sleep(0.01)
            self.write(raw_user_input="replacement input")
            (self.root / "release").touch()
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, err)
            self.assertIn("stale input", out)
            self.assertEqual(self.state()["current_goal"], "")
            self.assertEqual(self.state()["ledger_revision"], 2)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_record_and_completion_share_the_same_lock(self):
        self.setup_criterion()
        approved = self.criterion()
        entered = threading.Event()
        errors = []
        real_apply = self.ledger.apply_delta
        def slow_apply(state, delta):
            entered.set()
            time.sleep(0.1)
            return real_apply(state, delta)
        def update():
            try:
                self.write(intent_delta={"constraints": ["concurrent constraint"]})
            except Exception as exc:
                errors.append(exc)
        with patch.object(self.ledger, "apply_delta", side_effect=slow_apply):
            thread = threading.Thread(target=update)
            thread.start()
            self.assertTrue(entered.wait(timeout=5))
            self.mark(expected_criterion=approved)
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.state()["constraints"], ["concurrent constraint"])
        self.assertEqual(self.criterion()["status"], "met")

    def test_attached_raw_input_cannot_bypass_semantic_cas(self):
        self.write(raw_user_input="current request")
        with self.assertRaisesRegex(ValueError, "expected.input"):
            self.write(raw_user_input="replayed request", intent_delta={"current_goal": "unbound"})
        self.assertEqual(self.state()["ledger_revision"], 1)

    def test_legacy_strict_reads_do_not_invent_changing_timestamps(self):
        self.ledger.write_json(self.paths["state"], {
            "schema_version": self.ledger.SCHEMA_VERSION, "platform": "codex", "session_id": "session-a"})
        with patch.object(self.ledger, "utc_now", return_value="2026-01-01T00:00:00Z"):
            first = self.ledger.read_session_state(**self.identity, recover_audit=False)
        with patch.object(self.ledger, "utc_now", return_value="2026-01-01T00:00:01Z"):
            second = self.ledger.read_session_state(**self.identity, recover_audit=False)
        self.assertEqual(first, second)
        self.assertEqual(first.get("created_at", ""), "")
        self.assertEqual(first.get("updated_at", ""), "")

    def test_discovery_failure_rolls_back_state_event_and_discovery_together(self):
        self.write()
        before = self.state(), self.events(), self.ledger.read_current_session_pointer(self.root, "codex")
        self.fail_trigger("fail_discovery", "BEFORE UPDATE ON session_discovery")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected storage failure"):
            self.write(expected_revision=1, intent_delta={"current_goal": "committed"})
        self.assertEqual((self.state(), self.events(), self.ledger.read_current_session_pointer(self.root, "codex")), before)
        self.remove_trigger("fail_discovery")
        self.write(expected_revision=1, intent_delta={"current_goal": "committed"})
        self.assertEqual(self.state()["current_goal"], "committed")
        self.assertEqual(self.state()["ledger_revision"], 2)
        self.assertEqual(len(self.events()), 2)

    def test_consumer_snapshot_exposes_persisted_cas_coordinates(self):
        self.write(raw_user_input="active request")
        snapshot = self.ledger.consumer_snapshot(self.paths["state"])
        self.assertEqual(snapshot["platform"], "codex")
        self.assertEqual(snapshot["session_id"], "session-a")
        self.assertEqual(snapshot["ledger_revision"], 1)
        self.assertEqual(snapshot["latest_input_event_id"], self.observed_id())
        self.write(expected_input_event_id=snapshot["latest_input_event_id"],
                   expected_revision=snapshot["ledger_revision"], intent_delta={"current_goal": "bound"})

    def test_snapshot_missing_legacy_metadata_is_unknown_not_bootstrap_anchor(self):
        self.ledger.write_json(self.paths["state"], {"current_goal": "legacy readable goal"})
        with self.assertRaises(ValueError):
            self.ledger.read_session_state(**self.identity)
        snapshot = self.ledger.consumer_snapshot(json.loads(self.paths["state"].read_text()))
        self.assertEqual(snapshot["current_goal"], "legacy readable goal")
        for key in ("platform", "session_id", "ledger_revision", "latest_input_event_id"):
            self.assertIsNone(snapshot[key])
        self.assertEqual(snapshot["constraints"], [])

    def test_custom_observation_count_requires_exact_nonnegative_integer(self):
        self.write(raw_user_input="original input")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        observation = self.ledger.build_input_observation(platform="codex", session_id="session-a", raw_user_input="x")
        for count in (True, False, 1.0, -1, "1", None, 2, 2 ** 53):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.write(raw_user_input="x", observation=dict(observation, input_char_count=count))
            self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_invalid_observation_does_not_even_import_pending_legacy_audit(self):
        pending = self.ledger.default_state("codex", "session-a")
        pending["ledger_revision"] = 1
        pending["pending_audit_event"] = {"platform": "codex", "session_id": "session-a", "ledger_revision": 1,
                                          "mutation_id": "pending-legacy", "event": "intent-updated"}
        self.ledger.write_json(self.paths["state"], pending)
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        observation = self.ledger.build_input_observation(platform="codex", session_id="session-a", raw_user_input="x")
        with self.assertRaises(ValueError):
            self.write(raw_user_input="x", observation=dict(observation, input_char_count=-1))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertFalse(self.ledger.storage_database_path(self.root).exists())

    def test_persisted_anchor_number_schema_rejects_unsafe_values(self):
        self.write(raw_user_input="original input")
        valid = self.state()
        for key in ("latest_input_char_count", "ledger_revision"):
            for value in (True, False, -1, 1.5, "1", None, 2 ** 53):
                with self.subTest(key=key, value=value):
                    self.write_database_state(dict(valid, **{key: value}))
                    before = self.database_dump()
                    with self.assertRaises(ValueError):
                        self.ledger.read_session_state(**self.identity, recover_audit=False)
                    with self.assertRaises(ValueError):
                        self.write(raw_user_input="replacement")
                    self.assertEqual(before, self.database_dump())

    def test_partial_database_anchor_cannot_be_treated_as_legacy(self):
        self.write(raw_user_input="original input")
        valid = self.state()
        for key in ("latest_input_char_count", "latest_input_event_id", "latest_input_digest", "ledger_revision"):
            with self.subTest(key=key):
                partial = dict(valid)
                partial.pop(key)
                self.write_database_state(partial)
                before = self.database_payload()
                with self.assertRaises(ValueError):
                    self.ledger.read_session_state(**self.identity, recover_audit=False)
                self.assertEqual(self.database_payload(), before)

    def test_partial_new_legacy_anchor_cannot_be_migrated_as_legacy(self):
        valid = self.ledger.default_state("codex", "session-a")
        for key in ("latest_input_char_count", "latest_input_event_id", "latest_input_digest", "ledger_revision"):
            with self.subTest(key=key):
                partial = dict(valid)
                partial.pop(key)
                self.ledger.write_json(self.paths["state"], partial)
                before = self.paths["state"].read_bytes()
                with self.assertRaises(ValueError):
                    self.ledger.migrate_session(**self.identity)
                self.assertEqual(self.paths["state"].read_bytes(), before)
                with sqlite3.connect(self.ledger.storage_database_path(self.root)) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_legacy_anchor_number_schema_rejects_unsafe_values(self):
        valid = self.ledger.default_state("codex", "session-a")
        for key in ("latest_input_char_count", "ledger_revision"):
            for value in (True, False, -1, 1.5, "1", None, 2 ** 53):
                with self.subTest(key=key, value=value):
                    self.ledger.write_json(self.paths["state"], dict(valid, **{key: value}))
                    before = self.paths["state"].read_bytes()
                    with self.assertRaises(ValueError):
                        self.ledger.migrate_session(**self.identity)
                    self.assertEqual(self.paths["state"].read_bytes(), before)
                    with sqlite3.connect(self.ledger.storage_database_path(self.root)) as connection:
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_revision_cannot_increment_outside_shared_json_integer_range(self):
        self.write()
        state = self.state()
        state["ledger_revision"] = 2 ** 53 - 1
        self.write_database_state(state, sync_revision=True)
        self.assertEqual(self.ledger.read_session_state(**self.identity, recover_audit=False)["ledger_revision"], 2 ** 53 - 1)
        before = self.database_dump()
        with self.assertRaisesRegex(ValueError, "revision"):
            self.write(expected_revision=2 ** 53 - 1, intent_delta={"current_goal": "overflow"})
        self.assertEqual(before, self.database_dump())


if __name__ == "__main__":
    unittest.main(verbosity=2)
