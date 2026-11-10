#!/usr/bin/env python3
"""Core's Stop notice: one message per Stop, its text only when something needs attention, and its file."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_completion_skill_evidence as evidence
from test_completion_skill_evidence import assert_stop_observation

HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")
NOTICE_FILE = "GHOST_ALICE_STOP_NOTICE_FILE"
# The test writes source labels as the response model's origin claims, not as sender authentication.
COMPLETE = """Result: the report is ready.

[completion-check]
- acceptance-criteria:
  - c1: the report is generated [source: user-said]
  - c2: the report is checked [source: user-said]
- claim-evidence-map:
  - claim: the report was generated
    criterion: c1
    evidence: report.txt exists
    verdict: pass
  - claim: the report was checked
    criterion: c2
    evidence: check.py exited 0
    verdict: pass
- unverified:
  - none
- evidence: report.txt and the check.py output
"""
PARTIAL = """Result: the report is generated; its check waits for the checker.

[partial-check]
- scope: partial
- acceptance-criteria:
  - c1: the report is generated [source: user-said]
  - c2: the report is checked [source: user-said]
- claim-evidence-map:
  - claim: the report was generated
    criterion: c1
    evidence: report.txt exists
    verdict: pass
- unverified:
  - c2: the checker is not installed
- evidence: report.txt
"""


def declared(control="active", criterion="c2"):
    return {"schema_version": "declared-remaining-work.v1", "control": control, "items": [{
        "criterion_refs": [{"work_unit_id": "first", "contract_revision": 1, "criterion_id": criterion}],
        "reason": "the summary still needs the reviewer's numbers",
        "next_information": "Ask the reviewer for the final numbers.",
        "prerequisite": "missing", "source_refs": []}]}


class StopNoticeCase(unittest.TestCase):
    def stop(self, text, root, *, ledger=True, notice_file=None, new_input=None, **fixture):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("GHOST_ALICE_") and key not in {"CODEX_THREAD_ID", "CLAUDE_CODE_ENTRYPOINT"}}
        if ledger:
            evidence.StopHookSkillEvidenceTests._review_format_fixture(root, "codex", text, env, **fixture)
        if new_input is not None:
            # Another input is recorded through the intake hook path after the route was registered, so that route is stale.
            import test_response_route as fixtures
            fixtures.ledger.record_turn(root=root / "ledger", platform="codex", session_id="fixture-stop",
                                        raw_input=new_input, recording_path="hook")
        if notice_file is not None:
            env[NOTICE_FILE] = str(notice_file)
        result = subprocess.run([sys.executable, "-B", str(HOOK), "--platform", "codex"],
            input=json.dumps({"session_id": "fixture-stop", "last_assistant_message": text}),
            text=True, capture_output=True, check=True, env=env)
        return json.loads(result.stdout)

    def run_stop(self, text, **options):
        with tempfile.TemporaryDirectory() as temporary:
            return self.stop(text, Path(temporary), **options)

    def lines(self, payload):
        return payload["systemMessage"].splitlines()


class StopNoticeMessageTests(StopNoticeCase):
    def test_native_session_reads_every_host_session_key_and_no_environment(self):
        import stop_notice
        self.assertEqual(stop_notice.native_session({"conversation_id": "c-1"}), "c-1")
        self.assertEqual(stop_notice.native_session({"threadId": "t-1"}), "t-1")
        self.assertEqual(stop_notice.native_session({"session_id": " "}), "")
        self.assertEqual(stop_notice.native_session({}), "")

    def test_supported_completion_with_nothing_declared_is_quiet(self):
        payload = self.run_stop(COMPLETE)
        assert_stop_observation(self, payload)
        message = payload["stop_notice"]
        self.assertEqual(message["state"], "quiet")
        self.assertEqual(message["text"], "")
        self.assertEqual(message["control"], "completion-check")
        self.assertEqual(message["claims"], {"reviewed": 2, "supported": 2})
        self.assertEqual(message["remaining_work"], {"declared": False})
        self.assertEqual(message["body_digest"], "sha256:" + hashlib.sha256(COMPLETE.encode()).hexdigest())

    def test_unsupported_claim_is_named_with_its_criterion(self):
        payload = self.run_stop(COMPLETE, claim_verdicts=["supported", "unsupported"])
        message = payload["stop_notice"]
        self.assertEqual(message["state"], "notice")
        self.assertEqual(message["unsupported_claims"], [{"criteria": ["c2"], "claim": "the report was checked"}])
        self.assertEqual(self.lines(payload), [
            "Ghost-ALICE Stop notice: the recorded review does not support 1 of 2 claims.",
            "- Not supported (criterion c2): the report was checked",
            "- Remaining work: unknown; the response model made no declaration for this input",
        ])

    def test_partial_completion_lists_unresolved_criteria_as_progress(self):
        payload = self.run_stop(PARTIAL)
        assert_stop_observation(self, payload)
        message = payload["stop_notice"]
        self.assertEqual(message["unresolved_criteria"], [{"criterion": "c2", "reason": "the checker is not installed"}])
        self.assertEqual(message["remaining_work"], {"declared": False})
        self.assertEqual(self.lines(payload), [
            "Ghost-ALICE Stop notice: partial completion with 1 unresolved criterion.",
            "- Unresolved (criterion c2): the checker is not installed",
            "- Remaining work: unknown; the response model made no declaration for this input",
        ])

    def test_declared_remaining_work_is_listed_as_declared(self):
        payload = self.run_stop(COMPLETE, declared_remaining_work=declared())
        assert_stop_observation(self, payload)
        work = payload["stop_notice"]["remaining_work"]
        self.assertEqual((work["declared"], work["control"]), (True, "active"))
        self.assertEqual(work["items"], [{"criteria": ["c2"], "reason": "the summary still needs the reviewer's numbers",
            "next_information": "Ask the reviewer for the final numbers.", "prerequisite": "missing"}])
        self.assertEqual(self.lines(payload), [
            "Ghost-ALICE Stop notice: 1 remaining work item the response model declared.",
            "- Remaining work the response model declared (criterion c2): the summary still needs the reviewer's numbers",
            "  Next needed information: Ask the reviewer for the final numbers.",
            "  Prerequisite: not available yet",
        ])

    def test_work_the_user_stopped_is_marked_stopped(self):
        payload = self.run_stop(COMPLETE, declared_remaining_work=declared("model-reported-human-stop"))
        self.assertEqual(payload["stop_notice"]["remaining_work"]["control"], "model-reported-human-stop")
        self.assertEqual(self.lines(payload)[1:], [
            "- Remaining work the response model declared, the response model reports the human user's stop instruction (criterion c2): the summary still needs the reviewer's numbers",
            "  Next needed information: Ask the reviewer for the final numbers.",
        ])

    def test_a_row_on_hold_names_why(self):
        import stop_notice
        work = dict(declared=True, control="active", items=[dict(criteria=["c2"], reason="the numbers come from the report",
            next_information="Check the report again.", prerequisite="ready", source_issue="source-changed")])
        message = dict(claims=None, unsupported_claims=[], issues=[], unresolved_criteria=[], remaining_work=work)
        self.assertEqual(stop_notice.render(message).splitlines(), [
            "Ghost-ALICE Stop notice: 1 remaining work item the response model declared.",
            "- Remaining work the response model declared (criterion c2): the numbers come from the report",
            "  Next needed information: Check the report again.",
            "  On hold: its original source changed",
        ])

    def test_a_row_the_route_holds_carries_its_source_issue(self):
        import stop_notice
        route = dict(status="ready", declared_remaining_work=declared(), declared_remaining_work_issues=[
            dict(pointer="/declared_remaining_work/items/0", issue="source-changed")])
        work = stop_notice._remaining_work(dict(response_route=route, completion_route=dict(route_binding="current")))
        self.assertEqual([item.get("source_issue") for item in work["items"]], ["source-changed"])

    def test_legacy_issue_coordinates_map_once_to_the_correct_declared_row(self):
        import copy
        import stop_notice
        from response_route import RouteSchemaError
        current = declared(criterion='c1')
        current['items'] += declared(criterion='c2')['items']
        legacy = copy.deepcopy(current)
        legacy['schema_version'] = 'continuation-feedback.v1'
        legacy['required_unprocessed'] = legacy.pop('items')
        route = dict(status='ready', declared_remaining_work=current, continuation_feedback=legacy,
            declared_remaining_work_issues=[dict(pointer='/declared_remaining_work/items/1', issue='source-changed')],
            continuation_feedback_issues=[dict(pointer='/continuation_feedback/required_unprocessed/1', issue='source-changed')])
        context = dict(response_route=route, completion_route=dict(route_binding='current'))
        work = stop_notice._remaining_work(context)
        self.assertEqual([row.get('source_issue') for row in work['items']], [None, 'source-changed'])
        self.assertEqual([row['criteria'] for row in work['items']], [['c1'], ['c2']])
        route['continuation_feedback_issues'][0]['issue'] = 'source-unavailable'
        with self.assertRaises(RouteSchemaError):
            stop_notice._remaining_work(context)

    def test_an_empty_declaration_says_none_remains(self):
        import stop_notice
        message = dict(claims=None, unsupported_claims=[], issues=[], remaining_work=dict(declared=True, control="active", items=[]),
                       unresolved_criteria=[dict(criterion="c2", reason="the checker is not installed")])
        self.assertEqual(stop_notice.render(message).splitlines()[-1], "- Remaining work: none, as the response model declared")

    def test_selected_criteria_alone_are_not_remaining_work(self):
        # The route selects both criteria; only a declaration names remaining work.
        payload = self.run_stop(COMPLETE)
        self.assertEqual(payload["stop_notice"]["remaining_work"], {"declared": False})
        self.assertNotIn("systemMessage", payload)

    def test_completion_claim_without_a_route_is_an_issue(self):
        payload = self.run_stop(COMPLETE, ledger=False)
        assert_stop_observation(self, payload, issue="route-unavailable", repair_target="response_route")
        self.assertIn("- Issue: no current response route is registered for this answer",
                      self.lines(payload))

    def test_unsupported_whole_answer_is_an_issue(self):
        payload = self.run_stop(COMPLETE, review_verdict="unsupported")
        assert_stop_observation(self, payload, issue="response-unsupported")
        self.assertIn("- Issue: the recorded review does not support this answer", self.lines(payload))

    def test_a_claim_naming_two_criteria_counts_once(self):
        two = COMPLETE.replace("  - claim: the report was checked\n    criterion: c2",
                               "  - claim: the report was checked\n    criterion: c1, c2")
        payload = self.run_stop(two, claim_verdicts=["supported", "unsupported", "unsupported"])
        message = payload["stop_notice"]
        self.assertEqual(message["claims"], {"reviewed": 2, "supported": 1})
        self.assertEqual(message["unsupported_claims"], [{"criteria": ["c1", "c2"], "claim": "the report was checked"}])
        self.assertIn("- Not supported (criteria c1, c2): the report was checked", self.lines(payload))

    def test_a_completion_block_without_a_recorded_review_names_the_missing_review(self):
        payload = self.run_stop(COMPLETE, record_review=False)
        assert_stop_observation(self, payload, issue="assessment-required")
        self.assertIn("- Issue: no recorded review covers the claims in the completion block", self.lines(payload))

    def test_an_unsupported_whole_answer_still_names_its_unsupported_claims(self):
        payload = self.run_stop(COMPLETE, review_verdict="unsupported", claim_verdicts=["supported", "unsupported"])
        message = payload["stop_notice"]
        self.assertEqual(message["claims"], {"reviewed": 2, "supported": 1})
        self.assertEqual(message["unsupported_claims"], [{"criteria": ["c2"], "claim": "the report was checked"}])

    def test_a_declaration_in_a_route_for_an_earlier_input_is_not_shown(self):
        payload = self.run_stop("", record_review=False, declared_remaining_work=declared(criterion="c1"),
                                new_input="A new request.")
        self.assertEqual((payload["stop_notice"]["state"], payload["stop_notice"]["remaining_work"]), ("quiet", {"declared": False}))
        self.assertNotIn("systemMessage", payload)

    def test_issue_lines_name_the_problem_without_the_diagnostic(self):
        import stop_notice
        issues = [dict(code="completion-format", repair_target="completion_metadata",
                       detail="Write verdict: pass or verdict: fail on its own line."),
                  dict(code="route-unavailable", repair_target="response_route",
                       detail="route-unavailable: A recorded review binds this input but not this exact answer. The response model follows it."),
                  dict(code="owner-mismatch", repair_target="completion_metadata", detail="owner-mismatch: Exact native owner is required")]
        message = dict(claims=None, unsupported_claims=[], issues=issues, unresolved_criteria=[], remaining_work=dict(declared=False))
        self.assertEqual(stop_notice.render(message).splitlines(), [
            "Ghost-ALICE Stop notice: 3 issues found while checking this answer.",
            "- Issue: the completion block is missing or malformed",
            "- Issue: the response model's recorded response review covers this input but not this exact answer",
            "- Issue: Core code could not check this answer (owner-mismatch)",
            "- Remaining work: unknown; the response model made no declaration for this input",
        ])

    def test_empty_answer_with_nothing_declared_is_quiet(self):
        payload = self.run_stop("", ledger=False)
        self.assertEqual(payload["stop_notice"]["state"], "quiet")
        self.assertNotIn("systemMessage", payload)

    def test_an_empty_answer_still_shows_the_declared_remaining_work(self):
        # An empty answer has no claims or block to compare; the declaration in the current route still stands.
        payload = self.run_stop("", record_review=False, declared_remaining_work=declared(criterion="c1"))
        message = payload["stop_notice"]
        self.assertEqual((message["state"], message["claims"], message["issues"]), ("notice", None, []))
        self.assertIsNotNone(message["input_event_id"])
        self.assertEqual(self.lines(payload), [
            "Ghost-ALICE Stop notice: 1 remaining work item the response model declared.",
            "- Remaining work the response model declared (criterion c1): the summary still needs the reviewer's numbers",
            "  Next needed information: Ask the reviewer for the final numbers.",
            "  Prerequisite: not available yet",
        ])


class StopNoticeFileTests(StopNoticeCase):
    def test_file_holds_the_message_the_script_returns(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notice = root / "session" / "stop-notice.json"
            payload = self.stop(COMPLETE, root, claim_verdicts=["supported", "unsupported"], notice_file=notice)
            self.assertEqual(json.loads(notice.read_text(encoding="utf-8")), payload["stop_notice"])

    def test_the_same_stop_reuses_the_recorded_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notice = root / "stop-notice.json"
            first = self.stop(COMPLETE, root, notice_file=notice)
            written = notice.stat().st_mtime_ns
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("GHOST_ALICE_") and key not in {"CODEX_THREAD_ID", "CLAUDE_CODE_ENTRYPOINT"}}
            env.update(GHOST_ALICE_CORE_ROOT=str(HOOK.parent.parent), GHOST_ALICE_SESSION_INTENT_ROOT=str(root / "ledger"),
                       GHOST_ALICE_PLATFORM="codex", GHOST_ALICE_SESSION_ID="fixture-stop")
            env[NOTICE_FILE] = str(notice)
            again = json.loads(subprocess.run([sys.executable, "-B", str(HOOK), "--platform", "codex"],
                input=json.dumps({"session_id": "fixture-stop", "last_assistant_message": COMPLETE}),
                text=True, capture_output=True, check=True, env=env).stdout)
            self.assertEqual(notice.stat().st_mtime_ns, written)
            self.assertEqual(again["stop_notice"], first["stop_notice"])

    def test_another_answer_replaces_the_recorded_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notice = root / "stop-notice.json"
            self.stop(COMPLETE, root, notice_file=notice)
            payload = self.stop("A different answer.", root / "second", ledger=False, notice_file=notice)
            recorded = json.loads(notice.read_text(encoding="utf-8"))
            self.assertEqual(recorded["body_digest"], payload["stop_notice"]["body_digest"])
            self.assertEqual(recorded["body_digest"], "sha256:" + hashlib.sha256(b"A different answer.").hexdigest())

    def test_a_changed_message_for_the_same_answer_replaces_the_recorded_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notice = root / "stop-notice.json"
            stale = dict(self.stop(COMPLETE, root, notice_file=notice)["stop_notice"], state="notice", text="older")
            notice.write_text(json.dumps(stale), encoding="utf-8")
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("GHOST_ALICE_") and key not in {"CODEX_THREAD_ID", "CLAUDE_CODE_ENTRYPOINT"}}
            env.update(GHOST_ALICE_CORE_ROOT=str(HOOK.parent.parent), GHOST_ALICE_SESSION_INTENT_ROOT=str(root / "ledger"),
                       GHOST_ALICE_PLATFORM="codex", GHOST_ALICE_SESSION_ID="fixture-stop")
            env[NOTICE_FILE] = str(notice)
            again = json.loads(subprocess.run([sys.executable, "-B", str(HOOK), "--platform", "codex"],
                input=json.dumps({"session_id": "fixture-stop", "last_assistant_message": COMPLETE}),
                text=True, capture_output=True, check=True, env=env).stdout)
            self.assertEqual(again["stop_notice"]["state"], "quiet")
            self.assertEqual(json.loads(notice.read_text(encoding="utf-8")), again["stop_notice"])

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_a_recorded_message_keeps_the_mode_of_the_file_it_replaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notice = root / "stop-notice.json"
            self.stop(COMPLETE, root, notice_file=notice)
            os.chmod(notice, 0o640)
            payload = self.stop("A different answer.", root / "second", ledger=False, notice_file=notice)
            self.assertEqual(stat.S_IMODE(notice.stat().st_mode), 0o640)
            self.assertEqual(json.loads(notice.read_text(encoding="utf-8")), payload["stop_notice"])

    def test_a_failed_replacement_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "session"
            (folder / "stop-notice.json").mkdir(parents=True)
            payload = self.stop(COMPLETE, root, notice_file=folder / "stop-notice.json")
            self.assertTrue(payload["stop_notice"]["file_error"])
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ["stop-notice.json"])

    def test_a_file_that_cannot_be_written_keeps_the_notice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blocker = root / "not-a-folder"
            blocker.write_text("x", encoding="utf-8")
            payload = self.stop(COMPLETE, root, claim_verdicts=["supported", "unsupported"],
                                notice_file=blocker / "stop-notice.json")
            self.assertEqual(payload["systemMessage"], payload["stop_notice"]["text"])
            self.assertTrue(payload["stop_notice"]["file_error"])


if __name__ == "__main__":
    unittest.main()
