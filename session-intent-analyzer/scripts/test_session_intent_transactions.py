"""Identity, optimistic input binding and atomic SQLite state and event commits.

Dependencies: Python 3.11+ standard library only.
"""
import importlib.util
import json
import os
import sqlite3
import stat
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

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_a_state_write_keeps_the_mode_of_the_file_it_replaces(self):
        path = self.root / "state.json"
        self.ledger.write_json(path, {"revision": 1})
        os.chmod(path, 0o640)
        self.ledger.write_json(path, {"revision": 2})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"revision": 2})

    @unittest.skipIf(os.name == "nt", "Windows cannot flush a folder")
    def test_a_state_write_also_flushes_its_folder_entry(self):
        # A ledger state file survives a power loss under its new name, not only its new contents.
        flushed = []
        real_fsync = os.fsync

        def observe(descriptor):
            flushed.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
            return real_fsync(descriptor)

        with patch("os.fsync", side_effect=observe):
            self.ledger.write_json(self.root / "state.json", {"revision": 1})
        self.assertIn(True, flushed)

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
        return [x["event_id"] for x in rows if x["event"] == "input-observed"][-1]

    def criterion(self):
        return self.state()["acceptance_criteria"][0]

    def setup_criterion(self):
        self.write(intent_delta={"acceptance_criteria": [
            {"id": "report", "summary": "Validate revision one", "source": "user-said"}]})

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
        self.write(raw_input="first request")
        before = self.state()
        with self.assertRaisesRegex(ValueError, "expected.input"):
            self.write(intent_delta={"current_goal": "unbound"})
        self.assertEqual(self.state(), before)

    def test_stale_input_is_rejected_after_new_intake(self):
        self.write(raw_input="first request")
        old_id = self.observed_id()
        self.write(raw_input="revised request")
        before = self.state()
        with self.assertRaisesRegex(ValueError, "stale"):
            self.write(intent_delta={"current_goal": "old goal"}, expected_input_event_id=old_id)
        self.assertEqual(self.state(), before)
        self.write(intent_delta={"current_goal": "new goal"}, expected_input_event_id=self.observed_id())
        self.assertEqual(self.state()["current_goal"], "new goal")

    def test_a_stored_field_the_ledger_does_not_define_never_blocks_later_writes(self):
        self.write(raw_input="first request")
        state = self.state()
        state["undefined_field"] = {"value": 1}
        self.write_database_state(state)
        self.write(intent_delta={"current_goal": "next goal"}, expected_input_event_id=self.observed_id())
        self.assertEqual(self.state()["current_goal"], "next goal")
        self.assertNotIn("undefined_field", self.ledger.consumer_snapshot(self.state()))

    def test_a_criterion_field_the_ledger_does_not_define_never_blocks_later_writes(self):
        self.write(intent_delta={"acceptance_criteria": [
            {"id": "report", "summary": "Validate revision one", "source": "user-said"},
            {"id": "other", "summary": "Keep the other row", "source": "user-said"}]})
        state = self.state()
        for row in state["acceptance_criteria"]:
            row.update(status="met", met_completion_check_digest="a" * 64, met_at="2026-10-01T12:00:00Z")
        self.write_database_state(state)
        self.write(intent_delta={"current_goal": "next goal"})
        self.assertEqual(self.state()["current_goal"], "next goal")
        self.write(intent_delta={"acceptance_criteria": [dict(self.criterion(), summary="Validate revision two")]})
        self.assertEqual(self.state()["acceptance_criteria"], [
            {"id": "report", "summary": "Validate revision two", "source": "user-said", "selectable": True},
            {"id": "other", "summary": "Keep the other row", "source": "user-said", "selectable": True}])

    def test_a_write_checks_the_stored_identity(self):
        self.write(raw_input="request")
        state = self.state()
        anchor = self.observed_id()
        state["platform"] = "claude"
        self.write_database_state(state)
        with self.assertRaisesRegex(ValueError, "identity"):
            self.write(intent_delta={"current_goal": "other goal"}, expected_input_event_id=anchor)

    def test_a_ledger_outside_the_owned_root_is_refused(self):
        owned = self.root / "owned"
        owned.mkdir()
        with patch.dict(os.environ, {"GHOST_ALICE_OWNED_ROOT": str(owned)}), \
                self.assertRaisesRegex(ValueError, "escape"):
            self.write(intent_delta={"current_goal": "outside"})
        self.assertFalse(self.ledger.storage_database_path(self.root).exists())

    def test_first_writes_from_parallel_threads_share_one_loaded_helper(self):
        self.write(intent_delta={"current_goal": "existing root"})
        script = (
            "import importlib.util, sys, threading\n"
            "spec = importlib.util.spec_from_file_location('ledger_under_test', sys.argv[1])\n"
            "ledger = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(ledger)\n"
            "errors = []\n"
            "def write(index):\n"
            "    try:\n"
            "        ledger.record_turn(root=ledger.Path(sys.argv[2]), platform='codex', session_id=f'thread-{index}')\n"
            "    except Exception as error:\n"
            "        errors.append(repr(error))\n"
            "threads = [threading.Thread(target=write, args=(index,)) for index in range(8)]\n"
            "for thread in threads:\n"
            "    thread.start()\n"
            "for thread in threads:\n"
            "    thread.join()\n"
            "sys.exit('\\n'.join(errors) or 0)\n")
        result = subprocess.run([sys.executable, "-B", "-c", script, str(MODULE), str(self.root)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_expected_revision_rejects_a_lost_snapshot(self):
        self.write()
        self.write(intent_delta={"constraints": ["keep"]})
        with self.assertRaisesRegex(ValueError, "revision"):
            self.write(intent_delta={"constraints": ["stale"]}, expected_revision=1)
        self.assertEqual(self.state()["constraints"], ["keep"])

    def test_concurrent_subprocess_updates_lose_no_write(self):
        # A list update carries the complete current list, so each writer extends the list it read inside the
        # transaction. A writer that read a stale state would rebuild the list without the others' values.
        self.write()
        worker = '''import importlib.util, pathlib, sys, time
spec=importlib.util.spec_from_file_location("ledger", sys.argv[1])
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
apply=m.apply_delta
def slow(state, delta):
    time.sleep(0.08)
    return apply(state, {"constraints": state.get("constraints", []) + delta["constraints"]})
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

    def test_semantic_history_records_corrections_as_digests_without_raw_fields(self):
        self.write(raw_input="RAW_USER_PROMPT", intent_delta={"current_goal": "Inspect configuration"})
        anchor = self.observed_id()
        self.write(expected_input_event_id=anchor, intent_delta={
            "current_goal": "Repair the diagnosed configuration",
            "latest_scope": {"allowed": ["configuration"], "prohibited": ["publish"], "raw_prompt": "RAW_SCOPE"},
            "decisions": [{"id": "repair", "summary": "Repair authorized", "tool_output": "RAW_TOOL_OUTPUT"}],
        })
        rows = self.events()
        changes = rows[-1]["semantic_changes"]
        digest = self.ledger.json_digest
        # History keeps digests of the allowlisted projection.
        self.assertEqual(set(changes), {"current_goal", "latest_scope", "decisions"})
        self.assertEqual(changes["current_goal"], {"before_digest": digest("Inspect configuration"),
                                                   "after_digest": digest("Repair the diagnosed configuration")})
        self.assertEqual(changes["latest_scope"]["after_digest"],
                         digest({"allowed": ["configuration"], "prohibited": ["publish"]}))
        self.assertEqual(changes["decisions"]["after_digest"],
                         digest([{"id": "repair", "summary": "Repair authorized", "superseded": False}]))
        self.assertEqual(rows[-1]["input_event_id"], anchor)
        self.assertEqual(rows[-1]["ledger_revision"], 2)
        for raw in ("RAW_USER_PROMPT", "RAW_SCOPE", "RAW_TOOL_OUTPUT"):
            self.assertNotIn(raw, json.dumps(self.events()))

    def test_failed_event_insert_rolls_back_state_and_retry_does_not_duplicate_feedback(self):
        self.write()
        before = self.state(), self.events()
        delta = {"conduct_feedback": [{"id": "scope", "summary": "Preserve the approved scope", "source": "user-said"}]}
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

    def test_legacy_valid_header_migrates_input_anchor_with_cas(self):
        self.write(raw_input="legacy input")
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

    def test_one_legacy_import_reader_serves_the_writer_and_the_import_check(self):
        self.write(raw_input="legacy input")
        legacy = self.state()
        original_events = self.events()
        self.root = self.root / "legacy-import"
        self.identity["root"] = self.root
        self.paths = self.ledger.session_paths(**self.identity)
        legacy["pending_audit_event"] = dict(original_events[-1], platform="claude")
        self.ledger.write_json(self.paths["state"], legacy)
        self.paths["events"].write_text("".join(json.dumps(row) + "\n" for row in original_events))
        state, events, mismatch = self.ledger._legacy_import(self.paths, "codex", "session-a")
        self.assertEqual(mismatch, "pending ledger audit is invalid")
        self.assertEqual(events, original_events)
        self.assertNotIn("pending_audit_event", state)
        with self.assertRaises(ValueError) as caught:
            with self.ledger.storage_transaction(self.root) as connection:
                self.ledger._load_state_for_update(connection, self.paths, "codex", "session-a")
        self.assertEqual(str(caught.exception), "pending ledger audit is invalid")
        legacy["pending_audit_event"] = dict(original_events[-1], ledger_revision=legacy["ledger_revision"],
                                             mutation_id="pending-mutation")
        self.ledger.write_json(self.paths["state"], legacy)
        state, events, mismatch = self.ledger._legacy_import(self.paths, "codex", "session-a")
        self.assertIsNone(mismatch)
        self.assertEqual(events[-1]["mutation_id"], "pending-mutation")
        self.assertEqual(len(events), len(original_events) + 1)

    def test_an_import_that_also_attested_an_unread_file_keeps_its_event_count(self):
        self.write(raw_input="legacy input")
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
        self.write(expected_input_event_id=anchor, intent_delta={"current_goal": "bound"})
        with self.ledger.storage_transaction(self.root) as connection:
            state = self.ledger._stored_state(connection, "codex", "session-a")
            count = self.ledger._imported_event_count(connection, self.root, state)
            # The import record also carries the digest of a file the ledger does not read.
            (raw,) = connection.execute("SELECT sources_json FROM legacy_migrations WHERE platform=? AND session_id=?",
                                        ("codex", "session-a")).fetchone()
            sources = dict(json.loads(raw), unread_file="0" * 64)
            connection.execute("UPDATE legacy_migrations SET sources_json=? WHERE platform=? AND session_id=?",
                               (json.dumps(sources, sort_keys=True), "codex", "session-a"))
            self.assertEqual(self.ledger._imported_event_count(connection, self.root, state), count)
        self.assertEqual(count, len(original_events))

    def test_an_import_that_attested_only_an_unread_file_counts_no_imported_events(self):
        self.write(raw_input="current input")
        self.assertFalse(self.paths["state"].exists() or self.paths["events"].exists())
        with self.ledger.storage_transaction(self.root) as connection:
            state = self.ledger._stored_state(connection, "codex", "session-a")
            connection.execute("INSERT INTO legacy_migrations VALUES(?,?,?,?)",
                               ("codex", "session-a", json.dumps({"unread_file": "0" * 64}), self.ledger.utc_now()))
            self.assertEqual(self.ledger._imported_event_count(connection, self.root, state), 0)
            # A state or events file that appears after the import leaves the count unknown.
            self.paths["events"].parent.mkdir(parents=True, exist_ok=True)
            self.paths["events"].write_text("")
            self.assertIsNone(self.ledger._imported_event_count(connection, self.root, state))

    def test_delayed_subprocess_cannot_apply_old_input_after_new_intake(self):
        self.write(raw_input="first input")
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
            self.write(raw_input="replacement input")
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

    def test_attached_raw_input_cannot_bypass_semantic_cas(self):
        self.write(raw_input="current request")
        with self.assertRaisesRegex(ValueError, "expected.input"):
            self.write(raw_input="replayed request", intent_delta={"current_goal": "unbound"})
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
        self.write(raw_input="active request")
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
        self.write(raw_input="original input")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        observation = self.ledger.build_input_observation(platform="codex", session_id="session-a", raw_input="x")
        for count in (True, False, 1.0, -1, "1", None, 2, 2 ** 53):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.write(raw_input="x", observation=dict(observation, input_char_count=count))
            self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_invalid_observation_does_not_even_import_pending_legacy_audit(self):
        pending = self.ledger.default_state("codex", "session-a")
        pending["ledger_revision"] = 1
        pending["pending_audit_event"] = {"platform": "codex", "session_id": "session-a", "ledger_revision": 1,
                                          "mutation_id": "pending-legacy", "event": "intent-updated"}
        self.ledger.write_json(self.paths["state"], pending)
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        observation = self.ledger.build_input_observation(platform="codex", session_id="session-a", raw_input="x")
        with self.assertRaises(ValueError):
            self.write(raw_input="x", observation=dict(observation, input_char_count=-1))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertFalse(self.ledger.storage_database_path(self.root).exists())

    def test_persisted_anchor_number_schema_rejects_unsafe_values(self):
        self.write(raw_input="original input")
        valid = self.state()
        for key in ("latest_input_char_count", "ledger_revision"):
            for value in (True, False, -1, 1.5, "1", None, 2 ** 53):
                with self.subTest(key=key, value=value):
                    self.write_database_state(dict(valid, **{key: value}))
                    before = self.database_dump()
                    with self.assertRaises(ValueError):
                        self.ledger.read_session_state(**self.identity, recover_audit=False)
                    with self.assertRaises(ValueError):
                        self.write(raw_input="replacement")
                    self.assertEqual(before, self.database_dump())

    def test_partial_database_anchor_cannot_be_treated_as_legacy(self):
        self.write(raw_input="original input")
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
