"""Regression tests for session-bound mechanical check reuse."""
from pathlib import Path
from contextlib import closing
import sys
import tempfile
import unittest
import json
import os
import subprocess
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))


class SessionCheckReuseTest(unittest.TestCase):
    def test_instruction_retry_returns_the_latest_stable_body(self):
        from session_check_cache import load_instruction
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "SKILL.md"
            skill.write_text("old body")
            read_text = Path.read_text
            calls = []
            def read_then_mutate(path, *args, **kwargs):
                text = read_text(path, *args, **kwargs)
                calls.append(text)
                if len(calls) == 1:
                    skill.write_text("new body")
                return text
            with mock.patch.object(Path, "read_text", read_then_mutate):
                result = load_instruction(root, "codex", "a", skill, body_retained=False)
            self.assertEqual(result["body"], "new body")

    def test_continuously_changing_target_returns_no_result_or_clean_receipt(self):
        import session_check_cache as cache
        import pending_merge_precheck_hook as hook
        import io
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "manifest.json"
            target.write_text("0")
            def check():
                target.write_text(target.read_text() + "1")
                return {"undecided_count": 0}
            with self.assertRaises(cache.UnstableCheckTarget):
                cache.observe_check(root, "codex", "a", "merge", target, check)
            output = io.StringIO()
            with mock.patch.object(hook, "observe_check", side_effect=cache.UnstableCheckTarget("keep changing")), \
                 mock.patch.object(sys, "argv", ["hook", "--root", str(root), "--platform", "codex", "--format", "json"]), \
                 mock.patch.object(hook, "_read_hook_input", return_value={}), \
                 mock.patch.object(sys, "stdout", output):
                self.assertEqual(hook.main(), 0)
            # The check did not finish: the hook says so and leaves the host's flow alone.
            body = json.loads(output.getvalue())
            self.assertNotIn("decision", body)
            self.assertIs(body.get("continue"), True)
            self.assertIn("did not finish", body.get("systemMessage", ""))
            self.assertNotIn("clean", body.get("systemMessage", ""))
            self.assertNotIn("ghostAlicePendingMergeCheck", output.getvalue())

    def test_unstable_check_informs_the_user_on_both_hosts_and_formats(self):
        import session_check_cache as cache
        import pending_merge_precheck_hook as hook
        import agent_visibility_policy
        import hook_profile_gate
        import io
        absolute = "/private/home/.ghost-alice/pending-merges/codex/manifest.json"
        for platform in ("claude", "codex"):
            for output_format in ("json", "text"):
                with self.subTest(platform=platform, output_format=output_format), tempfile.TemporaryDirectory() as directory:
                    stdout, stderr = io.StringIO(), io.StringIO()
                    error = cache.UnstableCheckTarget(f"Check target changed repeatedly: {absolute}; recheck before continuing")
                    with mock.patch.object(hook, "observe_check", side_effect=error), \
                         mock.patch.object(sys, "argv", ["hook", "--root", directory, "--platform", platform, "--format", output_format]), \
                         mock.patch.object(hook, "_read_hook_input", return_value={}), \
                         mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
                        self.assertEqual(hook.main(), 0)
                    if output_format == "json":
                        body = json.loads(stdout.getvalue())
                        self.assertIn("systemMessage", body)
                        notice = body["systemMessage"]
                    else:
                        notice = stdout.getvalue()
                    # SessionStart shows systemMessage to the user on both hosts; the notice names the manifest the
                    # way the other precheck lines do and never echoes the account's absolute path.
                    self.assertIn(f"~/.ghost-alice/pending-merges/{platform}/manifest.json", notice)
                    self.assertIn("did not finish", notice)
                    self.assertNotIn(absolute, stdout.getvalue() + stderr.getvalue())
                    self.assertEqual(stderr.getvalue(), "")
                    if output_format == "json":
                        declared, host_stdout = hook_profile_gate._take_surface_declaration(stdout.getvalue())
                        context = hook_profile_gate._visibility_context(
                            "session-start", host_stdout, "", 0, env={"HOME": directory, "GHOST_ALICE_PLATFORM": platform},
                            declared=declared)
                        for profile in ("strict", "dynamic", "minimal"):
                            decision = agent_visibility_policy.decide(
                                profile=profile, hook_id="session-start", event="SessionStart", stdout=host_stdout,
                                stderr="", exit_code=0, context=context)
                            self.assertEqual(decision["visible_decision"], "force_show", profile)

    def test_every_check_outcome_informs_without_flow_control(self):
        # Core informs and stops: no outcome of a parsed check carries a host control field or a blocking exit.
        import session_check_cache as cache
        import pending_merge_precheck_hook as hook
        import io
        control_fields = {"decision", "reason", "stopReason", "permissionDecision"}
        for platform in ("claude", "codex"):
            for output_format in ("json", "text"):
                for outcome in ("clean", "pending", "reused", "unstable"):
                    with self.subTest(platform=platform, output_format=output_format, outcome=outcome), \
                         tempfile.TemporaryDirectory() as directory:
                        manifest = Path(directory) / ".ghost-alice/pending-merges" / platform / "manifest.json"
                        manifest.parent.mkdir(parents=True)
                        manifest.write_text(json.dumps({"entries": [] if outcome == "clean" else [{"decided": False}]}))
                        argv = ["hook", "--root", str(Path(directory) / "ledger"), "--platform", platform, "--format", output_format]
                        environment = {key: value for key, value in os.environ.items()
                                       if not key.startswith(("GHOST_ALICE_", "CODEX_"))}
                        environment["HOME"] = directory
                        def run():
                            stdout = io.StringIO()
                            unstable = cache.UnstableCheckTarget("keep changing")
                            with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(sys, "argv", argv), \
                                 mock.patch.object(hook, "_read_hook_input", return_value={"session_id": "session-outcomes"}), \
                                 mock.patch.object(hook, "observe_check",
                                                   side_effect=unstable if outcome == "unstable" else hook.observe_check), \
                                 mock.patch.object(sys, "stdout", stdout):
                                return hook.main(), stdout.getvalue()
                        if outcome == "reused":
                            run()
                        code, output = run()
                        self.assertEqual(code, 0)
                        if output_format == "json":
                            body = json.loads(output)
                            self.assertIs(body.get("continue"), True)
                            self.assertFalse(control_fields & set(body))
                            if outcome != "unstable":
                                status = body["ghostAlicePendingMergeCheck"]["status"]
                                self.assertEqual(status, "reused" if outcome == "reused" else "checked")
                        else:
                            self.assertTrue(output.strip())
                            self.assertNotIn('"decision"', output)
                            if outcome == "reused":
                                self.assertIn("session check reused", output)

    def test_mutation_during_check_retries_and_never_returns_the_old_value(self):
        from session_check_cache import observe_check
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "manifest.json"
            target.write_text("clean")
            calls = []
            def check():
                value = target.read_text()
                calls.append(value)
                if len(calls) == 1:
                    target.write_text("pending")
                return {"observed": value}
            result = observe_check(root, "codex", "a", "merge", target, check)
            self.assertEqual(result["value"], {"observed": "pending"})
            self.assertEqual(calls, ["clean", "pending"])

    def test_invalid_check_time_requires_real_instruction_read(self):
        from session_check_cache import load_instruction
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "SKILL.md"
            skill.write_text("rules")
            load_instruction(root, "codex", "a", skill, body_retained=False)
            for invalid in (None, "", "invalid", "2026-10-01T00:00:00"):
                with closing(sqlite3.connect(root / "session-checks.sqlite3")) as connection:
                    connection.execute("UPDATE checks SET checked_at=?", (invalid,))
                    connection.commit()
                result = load_instruction(root, "codex", "a", skill, body_retained=True)
                self.assertEqual(result["body"], "rules")
                self.assertEqual(result["status"], "checked")

    def test_change_during_cache_lookup_cannot_return_stale_result(self):
        import session_check_cache as cache
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "manifest.json"
            target.write_text("old")
            checker = lambda: {"observed": target.read_text()}
            cache.observe_check(root, "codex", "a", "merge", target, checker)
            stamp = cache._stamp
            calls = []
            def mutate_after_first_stamp(path):
                value = stamp(path)
                calls.append(value)
                if len(calls) == 1:
                    target.write_text("changed during lookup")
                return value
            with mock.patch.object(cache, "_stamp", side_effect=mutate_after_first_stamp):
                result = cache.observe_check(root, "codex", "a", "merge", target, checker)
            self.assertNotEqual(result["status"], "reused")
            self.assertEqual(result["value"], {"observed": "changed during lookup"})

    def test_pending_hook_reuses_same_session_and_notices_manifest_change(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            manifest = home / ".ghost-alice/pending-merges/codex/manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"entries": [{"decided": False}]}))
            env = dict(os.environ)
            env.update({"HOME": directory, "CODEX_THREAD_ID": "session-test", "GHOST_ALICE_SESSION_ID": "session-test"})
            command = [sys.executable, str(Path(__file__).with_name("pending_merge_precheck_hook.py")), "--root", str(home / "ledger"), "--platform", "codex", "--format", "json"]
            def run():
                return json.loads(subprocess.run(command, input='{}', text=True, capture_output=True, env=env, check=True).stdout)
            first, second = run(), run()
            for body in (first, second):
                # Session start reports the check on the user's screen and adds nothing to the model context.
                self.assertNotIn("hookSpecificOutput", body)
                self.assertNotIn("LONG WORKFLOW", json.dumps(body))
            checked = first["ghostAlicePendingMergeCheck"]
            self.assertEqual((checked["platform"], checked["undecided_count"], checked["status"]), ("codex", 1, "checked"))
            reused = second["ghostAlicePendingMergeCheck"]
            self.assertEqual((reused["status"], reused["checked_at"]), ("reused", checked["checked_at"]))
            self.assertNotIn("Surface merge-companion first", second["systemMessage"])
            import sqlite3
            with closing(sqlite3.connect(home / "ledger/session-checks.sqlite3")) as connection:
                connection.execute("UPDATE checks SET value=? WHERE key='pending-merges'", (json.dumps({"wrong-shape": True}),))
                connection.commit()
            recovered = run()
            self.assertEqual(recovered["ghostAlicePendingMergeCheck"]["status"], "checked")
            self.assertEqual(recovered["ghostAlicePendingMergeCheck"]["undecided_count"], 1)
            manifest.write_text(json.dumps({"entries": [{"decided": True}]}))
            changed = run()
            self.assertEqual(changed["ghostAlicePendingMergeCheck"]["status"], "checked")
            self.assertEqual(changed["ghostAlicePendingMergeCheck"]["undecided_count"], 0)

    def test_next_session_start_reports_a_manifest_change(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            manifest = home / ".ghost-alice/pending-merges/codex/manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"entries": []}))
            env = dict(os.environ)
            env.update({"HOME": directory, "CODEX_THREAD_ID": "session-new", "GHOST_ALICE_SESSION_ID": "session-new"})
            base = [sys.executable, str(Path(__file__).with_name("pending_merge_precheck_hook.py")), "--root", str(home / "ledger"), "--platform", "codex", "--format", "json"]
            def run():
                return json.loads(subprocess.run(base, input='{}', text=True, capture_output=True, env=env, check=True).stdout)
            run()
            # Another installer run backs up a modified skill during the session.
            manifest.write_text(json.dumps({"entries": [{"decided": False}]}))
            # merge-companion re-checks the manifest before work changes an installed skill; the next
            # session start reports the change.
            compacted = run()
            self.assertNotIn("hookSpecificOutput", compacted)
            checked = compacted["ghostAlicePendingMergeCheck"]
            self.assertEqual((checked["undecided_count"], checked["status"]), (1, "checked"))
            self.assertIn("1 undecided", compacted["systemMessage"])

    def test_removed_input_check_options_end_without_a_check(self):
        # A 0.5.1 input registration still passes the removed options; the installer removes that registration.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            manifest = home / ".ghost-alice/pending-merges/codex/manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"entries": [{"decided": False}]}))
            env = dict(os.environ)
            env.update({"HOME": directory, "CODEX_THREAD_ID": "session-old", "GHOST_ALICE_SESSION_ID": "session-old"})
            command = [sys.executable, str(Path(__file__).with_name("pending_merge_precheck_hook.py")),
                       "--platform", "codex", "--hook", "prompt-check", "--context", "prompt_submit",
                       "--format", "json", "--internal-b64", "eA==", "--root", str(home / "ledger")]
            result = subprocess.run(command, input='{}', text=True, capture_output=True, env=env)
            self.assertEqual((result.returncode, result.stdout), (2, ""))
            self.assertFalse((home / "ledger/session-checks.sqlite3").exists())

    def test_pending_check_stays_out_of_model_context(self):
        import pending_merge_precheck_hook as hook
        for count in (0, 2):
            result = dict(status='checked', checked_at='2026-10-12T21:00:00+00:00',
                          value=dict(undecided_count=count))
            body = json.loads(hook._json_payload('codex', result))
            # The user sees the screen notice; merge-companion re-checks before work changes a skill.
            self.assertNotIn('hookSpecificOutput', body)
            self.assertEqual(body['ghostAlicePendingMergeCheck']['undecided_count'], count)
            self.assertIn('systemMessage', body)

    def test_same_session_reuses_and_mutation_or_new_session_rechecks(self):
        from session_check_cache import observe_check
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "manifest.json"
            target.write_text("old")
            calls = []
            def check():
                calls.append(target.read_text())
                return {"observed": calls[-1]}
            def observe(session):
                return observe_check(root, "codex", session, "pending-merges", target, check)
            first = observe("session-a")
            second = observe("session-a")
            self.assertEqual([first["status"], second["status"]], ["checked", "reused"])
            self.assertEqual(first["checked_at"], second["checked_at"])
            self.assertEqual(calls, ["old"])
            target.write_text("new")
            self.assertEqual(observe("session-a")["value"], {"observed": "new"})
            self.assertEqual(observe("session-b")["status"], "checked")
            self.assertEqual(calls, ["old", "new", "new"])

    def test_missing_target_creation_and_corrupt_store_cannot_hide_change(self):
        from session_check_cache import observe_check
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "manifest.json"
            def check():
                return {"exists": target.exists()}
            self.assertFalse(observe_check(root, "codex", "a", "merge", target, check)["value"]["exists"])
            target.write_text("pending")
            self.assertTrue(observe_check(root, "codex", "a", "merge", target, check)["value"]["exists"])
            (root / "session-checks.sqlite3").write_bytes(b"broken")
            self.assertEqual(observe_check(root, "codex", "a", "merge", target, check)["status"], "uncached")

    def test_instruction_cache_never_substitutes_metadata_for_lost_body(self):
        from session_check_cache import load_instruction
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "SKILL.md"
            skill.write_text("instruction v1")
            first = load_instruction(root, "codex", "a", skill, body_retained=False)
            self.assertEqual(first["body"], "instruction v1")
            reused = load_instruction(root, "codex", "a", skill, body_retained=True)
            self.assertEqual(reused["status"], "reused")
            self.assertNotIn("body", reused)
            lost = load_instruction(root, "codex", "a", skill, body_retained=False)
            self.assertEqual(lost["body"], "instruction v1")
            skill.write_text("instruction v2")
            changed = load_instruction(root, "codex", "a", skill, body_retained=True)
            self.assertEqual(changed["body"], "instruction v2")
            self.assertEqual(load_instruction(root, "codex", "b", skill, body_retained=True)["status"], "checked")
            self.assertNotIn(b"instruction v2", (root / "session-checks.sqlite3").read_bytes())

    def test_unknown_session_never_creates_shared_cache(self):
        from session_check_cache import observe_check
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = observe_check(root, "codex", "unknown", "check", root / "absent", lambda: {"done": True})
            self.assertEqual(result["status"], "uncached")
            self.assertFalse((root / "session-checks.sqlite3").exists())


class DeliveryRecordTest(unittest.TestCase):
    def test_delivery_follows_the_content_of_one_session(self):
        from session_check_cache import delivered, record_delivery
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(record_delivery(root, "codex", "a", "receipt", "content v1", create=True))
            self.assertTrue(delivered(root, "codex", "a", "receipt", "content v1"))
            for other in (("codex", "a", "receipt", "content v2"), ("codex", "b", "receipt", "content v1"),
                          ("claude", "a", "receipt", "content v1"), ("codex", "a", "other", "content v1")):
                self.assertFalse(delivered(root, *other))
            self.assertTrue(record_delivery(root, "codex", "a", "receipt", "content v2", create=False))
            self.assertTrue(delivered(root, "codex", "a", "receipt", "content v2"))
            self.assertFalse(delivered(root, "codex", "a", "receipt", "content v1"))
            # The store keeps a digest of the delivered content, never the content.
            self.assertNotIn(b"content v2", (root / "session-checks.sqlite3").read_bytes())

    def test_delivery_record_without_create_creates_neither_store_nor_record(self):
        from session_check_cache import delivered, record_delivery
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            self.assertFalse(record_delivery(root, "codex", "a", "receipt", "content", create=False))
            self.assertFalse(delivered(root, "codex", "a", "receipt", "content"))
            self.assertFalse(record_delivery(root, "codex", "unknown", "receipt", "content", create=True))
            self.assertFalse(root.exists())
            self.assertTrue(record_delivery(root, "codex", "a", "receipt", "content", create=True))
            # Only a session's own start creates its record; an existing store does not change that.
            self.assertFalse(record_delivery(root, "codex", "b", "receipt", "content", create=False))
            self.assertFalse(delivered(root, "codex", "b", "receipt", "content"))
            (root / "session-checks.sqlite3").write_bytes(b"broken")
            for create in (False, True):
                self.assertFalse(record_delivery(root, "codex", "a", "receipt", "content", create=create))
            self.assertFalse(delivered(root, "codex", "a", "receipt", "content"))

    def test_store_path_symlink_loop_reads_and_records_nothing(self):
        from session_check_cache import delivered, record_delivery
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "session-checks.sqlite3"
            try:
                store.symlink_to(store)
            except OSError as error:
                self.skipTest(f"symbolic links unavailable: {error}")
            self.assertFalse(delivered(root, "codex", "a", "receipt", "content"))
            for create in (False, True):
                self.assertFalse(record_delivery(root, "codex", "a", "receipt", "content", create=create))
