"""Contract tests for the shared verify-or-reuse decision used by Claude and Codex.

Dependencies: Python 3.11+ standard library only. The hook-path tests also use
Node.js when it is available and skip otherwise.
"""

from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "coding-convention" / "verification-before-completion"
MODULE_PATH = SKILL_DIR / "scripts" / "verify_or_reuse.py"
REFERENCE_PATH = SKILL_DIR / "references" / "verify-or-reuse.md"
HOOK_DISPATCHER = REPO_ROOT / "_shared" / "ghost-alice-hook.mjs"
RULE_BLOCKS = REPO_ROOT / "_shared" / "global_rule_blocks.py"

FLOW = (
    "claim -> claim-time -> target-copy -> retained-evidence -> last-known-author -> "
    "actual-mutation-authority -> observed-mutation-event -> evidence-still-valid -> "
    "decision-impact -> verify-or-reuse"
)
VERIFY_OR_REUSE_PRECHECK = "Before re-reading an artifact to support a claim, apply verify-or-reuse: reuse retained evidence unless an observed trigger exists."
CHANGE_DEFINITION = "A change is an observed mutation event, not a possibility, a storage location, or a new user message."
TOOL_CHECKPOINT_FIELD_HOOK = "Add verify-or-reuse when a call re-reads an artifact to support a claim, with why naming the observed trigger."

# The reported incident: the agent wrote a Markdown file in a chat sandbox, the
# user could change it only by instructing the agent, no edit instruction or
# other write followed, and the user asked whether content was already included.
INCIDENT = {
    "claim_time": "authored-at",
    "claim_copy": "chat-sandbox:plan.md",
    "retained_evidence": {"present": True, "copy": "chat-sandbox:plan.md", "origin": "authored"},
    "last_known_author": "current-agent",
    "mutation_authority": "agent-mediated",
    "authority_basis": "interaction-contract",
    "events_since_evidence": ["user-message"],
}
SEMANTIC_KEYS = (
    "actual_mutation_authority",
    "observed_mutation_events",
    "evidence_still_valid",
    "decision_impact",
    "verdict",
    "trigger",
    "claim_scope",
    "tool_call_required",
    "check_copy",
)


def _load_module():
    if not MODULE_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location("verify_or_reuse_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VOR = _load_module()


def facts(**overrides):
    data = copy.deepcopy(INCIDENT)
    for key, value in overrides.items():
        data[key] = value
    return data


class _ModuleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if VOR is None:
            self.fail(f"verify-or-reuse decision module is missing: {MODULE_PATH.relative_to(REPO_ROOT)}")


class IncidentAndReuseTests(_ModuleTestCase):
    def test_t1_agent_authored_unchanged_artifact_reuses_retained_evidence(self) -> None:
        result = VOR.decide(INCIDENT)
        self.assertEqual(result["verdict"], "reuse")
        self.assertFalse(result["tool_call_required"])
        self.assertIsNone(result["check_copy"])
        self.assertEqual(result["last_known_author"], "current-agent")
        self.assertEqual(result["actual_mutation_authority"], "agent-mediated")
        self.assertEqual(result["observed_mutation_events"], [])
        self.assertTrue(result["evidence_still_valid"])
        self.assertEqual(result["decision_impact"], "none")
        self.assertEqual(result["claim_scope"], "authored-at")

    def test_t1_new_user_message_is_recorded_as_ignored_signal(self) -> None:
        result = VOR.decide(INCIDENT)
        self.assertIn({"signal": "user-message", "reason": "not-a-mutation-event"}, result["ignored_signals"])

    def test_t1_reread_in_trace_is_redundant_verification_on_both_platforms(self) -> None:
        traces = {
            "claude": [{"tool": "Read", "copy": "chat-sandbox:plan.md"}],
            "codex": [{"tool": "shell", "copy": "chat-sandbox:plan.md"}],
        }
        for platform, calls in traces.items():
            with self.subTest(platform=platform):
                audit = VOR.audit_trace(platform, INCIDENT, calls)
                self.assertEqual([item["finding"] for item in audit["findings"]], ["redundant-verification"])
                self.assertEqual(VOR.audit_trace(platform, INCIDENT, [])["findings"], [])

    def test_t1_fetch_shell_and_connector_rereads_are_all_redundant(self) -> None:
        calls = [
            {"tool": "WebFetch", "copy": "chat-sandbox:plan.md"},
            {"tool": "Bash", "copy": "chat-sandbox:plan.md"},
            {"tool": "mcp__Google_Drive__read_file_content", "copy": "chat-sandbox:plan.md"},
        ]
        audit = VOR.audit_trace("claude", INCIDENT, calls)
        self.assertEqual([item["finding"] for item in audit["findings"]], ["redundant-verification"] * 3)

    def test_unrelated_calls_are_outside_the_audit(self) -> None:
        audit = VOR.audit_trace("claude", INCIDENT, [{"tool": "Read", "copy": "repo:README.md"}])
        self.assertEqual(audit["findings"], [])


class MutationEventTests(_ModuleTestCase):
    def test_t2_agent_mediated_successful_write_invalidates_prior_evidence(self) -> None:
        result = VOR.decide(facts(claim_time="current", events_since_evidence=["user-message", "agent-write-succeeded"]))
        self.assertEqual(result["verdict"], "verify")
        self.assertEqual(result["trigger"], "agent-write-succeeded")
        self.assertFalse(result["evidence_still_valid"])
        self.assertTrue(result["tool_call_required"])
        self.assertEqual(result["check_copy"], "chat-sandbox:plan.md")
        self.assertEqual(result["decision_impact"], "material")

    def test_failed_write_is_not_a_mutation_event(self) -> None:
        result = VOR.decide(facts(claim_time="current", events_since_evidence=["agent-write-failed"]))
        self.assertEqual(result["observed_mutation_events"], [])
        self.assertEqual(result["verdict"], "reuse")
        self.assertEqual(result["claim_scope"], "current")

    def test_unexecuted_edit_instruction_is_not_a_mutation_event(self) -> None:
        result = VOR.decide(facts(claim_time="current", events_since_evidence=["edit-instruction-unexecuted"]))
        self.assertEqual(result["observed_mutation_events"], [])
        self.assertEqual(result["verdict"], "reuse")

    def test_repeated_user_messages_alone_keep_reuse(self) -> None:
        result = VOR.decide(facts(claim_time="current", events_since_evidence=["user-message", "user-message"]))
        self.assertEqual(result["observed_mutation_events"], [])
        self.assertEqual(result["verdict"], "reuse")

    def test_unknown_event_type_is_rejected_instead_of_silently_counted(self) -> None:
        with self.assertRaises(ValueError):
            VOR.decide(facts(events_since_evidence=["maybe-edited"]))

    def test_t4_observed_external_revision_requires_authoritative_recheck(self) -> None:
        for event in ("external-revision", "sync-revision", "other-agent-write", "user-provided-changed-artifact"):
            with self.subTest(event=event):
                result = VOR.decide(facts(claim_time="current", events_since_evidence=[event]))
                self.assertEqual(result["verdict"], "verify")
                self.assertEqual(result["trigger"], event)
                self.assertEqual(result["check_copy"], "chat-sandbox:plan.md")

    def test_t4_last_writer_other_than_agent_counts_as_observed_change(self) -> None:
        result = VOR.decide(facts(claim_time="current", last_known_author="user", events_since_evidence=[]))
        self.assertEqual(result["verdict"], "verify")
        self.assertEqual(result["trigger"], "last-writer-changed")

    def test_explicit_recheck_request_triggers_one_check(self) -> None:
        result = VOR.decide(facts(explicit_recheck_request=True))
        self.assertEqual(result["verdict"], "verify")
        self.assertEqual(result["trigger"], "explicit-recheck-request")


class AuthorityTests(_ModuleTestCase):
    def test_t3_storage_location_does_not_open_a_change_path(self) -> None:
        result = VOR.decide(facts(
            claim_time="current",
            claim_copy="drive:doc-123",
            retained_evidence={"present": True, "copy": "drive:doc-123", "origin": "authored"},
            mutation_authority="externally-writable",
            authority_basis="storage-location",
            hypothetical_changes=["collaborator-may-have-edited", "sync-may-have-run"],
        ))
        self.assertEqual(result["actual_mutation_authority"], "unknown")
        self.assertIn("authority-not-evidenced", result["reasons"])
        self.assertEqual(result["verdict"], "reuse")
        self.assertEqual(result["claim_scope"], "last-known")
        self.assertFalse(result["tool_call_required"])
        self.assertIn({"signal": "collaborator-may-have-edited", "reason": "hypothetical-not-a-trigger"}, result["ignored_signals"])

    def test_t3_drive_copy_with_agent_mediated_contract_stays_reusable(self) -> None:
        result = VOR.decide(facts(
            claim_time="current",
            claim_copy="drive:doc-123",
            retained_evidence={"present": True, "copy": "drive:doc-123", "origin": "authored"},
        ))
        self.assertEqual(result["verdict"], "reuse")
        self.assertEqual(result["claim_scope"], "current")

    def test_evidenced_externally_writable_current_claim_requires_check(self) -> None:
        result = VOR.decide(facts(claim_time="current", mutation_authority="externally-writable", authority_basis="sharing-state"))
        self.assertEqual(result["verdict"], "verify")
        self.assertEqual(result["trigger"], "externally-writable-current-state")

    def test_unknown_authority_current_claim_reuses_as_last_known_state(self) -> None:
        result = VOR.decide(facts(claim_time="current", mutation_authority="unknown", authority_basis="none"))
        self.assertEqual(result["verdict"], "reuse")
        self.assertEqual(result["claim_scope"], "last-known")
        self.assertFalse(result["evidence_still_valid"])
        self.assertFalse(result["tool_call_required"])

    def test_unevidenced_closed_authority_is_not_trusted_as_current(self) -> None:
        result = VOR.decide(facts(claim_time="current", mutation_authority="closed", authority_basis="assumption"))
        self.assertEqual(result["actual_mutation_authority"], "unknown")
        self.assertEqual(result["claim_scope"], "last-known")


class QuestionTimeAndCopyTests(_ModuleTestCase):
    def test_t5_authored_at_question_uses_chronology_not_current_file(self) -> None:
        result = VOR.decide(facts(events_since_evidence=["user-message", "external-revision"]))
        self.assertEqual(result["verdict"], "reuse")
        self.assertEqual(result["claim_scope"], "authored-at")
        self.assertFalse(result["tool_call_required"])

    def test_t5_same_facts_as_current_state_question_require_check(self) -> None:
        result = VOR.decide(facts(claim_time="current", events_since_evidence=["user-message", "external-revision"]))
        self.assertEqual(result["verdict"], "verify")

    def test_planned_check_on_a_different_copy_is_wrong_copy(self) -> None:
        result = VOR.decide(facts(
            claim_time="current",
            claim_copy="drive:doc-123",
            retained_evidence={"present": True, "copy": "drive:doc-123", "origin": "authored"},
            events_since_evidence=["external-revision"],
            planned_check_copy="local:docs/plan.md",
        ))
        self.assertEqual(result["verdict"], "wrong-copy")
        self.assertFalse(result["tool_call_required"])
        self.assertEqual(result["redirect"], {"copy": "drive:doc-123", "verdict": "verify"})

    def test_evidence_about_a_different_copy_does_not_cover_the_claim(self) -> None:
        result = VOR.decide(facts(
            claim_time="current",
            claim_copy="drive:doc-123",
            retained_evidence={"present": True, "copy": "local:docs/plan.md", "origin": "authored"},
        ))
        self.assertEqual(result["verdict"], "verify")
        self.assertEqual(result["trigger"], "evidence-covers-different-copy")
        self.assertEqual(result["check_copy"], "drive:doc-123")

    def test_trace_inspecting_a_related_copy_is_wrong_copy_inspection(self) -> None:
        data = facts(claim_copy="drive:doc-123", related_copies=["local:docs/plan.md"],
                     retained_evidence={"present": True, "copy": "drive:doc-123", "origin": "authored"})
        audit = VOR.audit_trace("claude", data, [{"tool": "Read", "copy": "local:docs/plan.md"}])
        self.assertEqual([item["finding"] for item in audit["findings"]], ["wrong-copy-inspection"])


class LostEvidenceTests(_ModuleTestCase):
    def test_t6_lost_retained_evidence_allows_one_minimal_read(self) -> None:
        data = facts(retained_evidence={"present": False, "copy": None, "origin": "authored"})
        result = VOR.decide(data)
        self.assertEqual(result["verdict"], "verify")
        self.assertEqual(result["trigger"], "retained-evidence-lost")
        audit_once = VOR.audit_trace("claude", data, [{"tool": "Read", "copy": "chat-sandbox:plan.md"}])
        self.assertEqual(audit_once["findings"], [])
        audit_twice = VOR.audit_trace("claude", data, [
            {"tool": "Read", "copy": "chat-sandbox:plan.md"},
            {"tool": "Read", "copy": "chat-sandbox:plan.md"},
        ])
        self.assertEqual([item["finding"] for item in audit_twice["findings"]], ["duplicate-verification"])

    def test_t6_lost_evidence_without_access_is_unverified_not_pretended(self) -> None:
        result = VOR.decide(facts(retained_evidence={"present": False, "copy": None, "origin": "authored"}, artifact_accessible=False))
        self.assertEqual(result["verdict"], "unverified")
        self.assertFalse(result["tool_call_required"])

    def test_t6_lost_authored_evidence_after_mutation_cannot_be_recovered_from_current_copy(self) -> None:
        result = VOR.decide(facts(
            retained_evidence={"present": False, "copy": None, "origin": "authored"},
            events_since_evidence=["external-revision"],
        ))
        self.assertEqual(result["verdict"], "unverified")
        self.assertIn("current-copy-is-not-authored-state", result["reasons"])


class FlawPropagationTests(_ModuleTestCase):
    BASE = {
        "flaw_confirmed": True,
        "deliverable_origin": "agent-provided",
        "request_mode": "deliverable",
        "fix_within_scope": True,
        "fix_safe": True,
    }

    def test_t7_confirmed_flaw_requires_corrected_complete_deliverable(self) -> None:
        decision = VOR.flaw_propagation(self.BASE)
        self.assertEqual(decision["required_response"], "corrected-full-deliverable")
        self.assertFalse(decision["reapproval_required"])
        for response in ("explanation-only", "previous-version", "partial-patch"):
            with self.subTest(response=response):
                self.assertEqual(VOR.classify_response(decision, response), "confirmed-flaw-not-propagated")
        self.assertIsNone(VOR.classify_response(decision, "corrected-full-deliverable"))
        self.assertEqual(VOR.classify_response(decision, "confirmation-question"), "unnecessary-reapproval")

    def test_diagnosis_only_request_ends_with_explanation(self) -> None:
        decision = VOR.flaw_propagation({**self.BASE, "request_mode": "diagnosis-only"})
        self.assertEqual(decision["required_response"], "diagnosis-only")
        self.assertIsNone(VOR.classify_response(decision, "explanation-only"))

    def test_out_of_scope_fix_needs_confirmation(self) -> None:
        decision = VOR.flaw_propagation({**self.BASE, "fix_within_scope": False})
        self.assertEqual(decision["required_response"], "confirm-before-fix")
        self.assertTrue(decision["reapproval_required"])


class PlatformParityTests(_ModuleTestCase):
    PLANNED = {
        "claude": {"tool": "Read"},
        "codex": {"tool": "shell"},
    }

    def _fixtures(self):
        return {
            "incident": facts(),
            "agent-write": facts(claim_time="current", events_since_evidence=["agent-write-succeeded"]),
            "storage-only": facts(claim_time="current", mutation_authority="externally-writable", authority_basis="storage-location"),
            "external-revision": facts(claim_time="current", events_since_evidence=["external-revision"]),
            "authored-at-after-revision": facts(events_since_evidence=["external-revision"]),
            "lost-evidence": facts(retained_evidence={"present": False, "copy": None, "origin": "authored"}),
            "wrong-copy": facts(claim_time="current", related_copies=["local:plan.md"]),
        }

    def test_t8_claude_and_codex_adapters_produce_identical_semantic_verdicts(self) -> None:
        for name, data in self._fixtures().items():
            planned_copy = "local:plan.md" if name == "wrong-copy" else data["claim_copy"]
            results = {}
            for platform, call in self.PLANNED.items():
                results[platform] = VOR.evaluate(platform, data, {**call, "copy": planned_copy})
                self.assertIn(results[platform]["planned_call_class"], VOR.INSPECTION_CLASSES)
            with self.subTest(fixture=name):
                claude = {key: results["claude"][key] for key in SEMANTIC_KEYS}
                codex = {key: results["codex"][key] for key in SEMANTIC_KEYS}
                self.assertEqual(claude, codex)

    def test_t8_flaw_propagation_requirement_is_platform_neutral(self) -> None:
        base = FlawPropagationTests.BASE
        self.assertEqual(VOR.flaw_propagation(base), VOR.flaw_propagation(dict(base)))
        self.assertNotIn("platform", inspect.signature(VOR.flaw_propagation).parameters)

    def test_decision_function_has_no_platform_input(self) -> None:
        self.assertEqual(list(inspect.signature(VOR.decide).parameters), ["facts"])
        self.assertEqual(set(VOR.PLATFORM_TOOL_CLASSES), {"claude", "codex"})

    def test_adapter_maps_only_tool_names(self) -> None:
        self.assertEqual(VOR.tool_class("claude", {"tool": "Read"}), "file-read")
        self.assertEqual(VOR.tool_class("codex", {"tool": "shell"}), "shell")
        self.assertEqual(VOR.tool_class("codex", {"tool": "apply_patch"}), "write")
        self.assertEqual(VOR.tool_class("codex", {"class": "connector"}), "connector")
        self.assertEqual(VOR.tool_class("claude", {"tool": "UnlistedTool"}), "other")


# Each fixture encodes, with neutral names, a correction recorded in real session ledgers and
# the verdict that correction demanded. The private ledgers themselves never enter the repository.
REAL_CASE_FIXTURES = {
    "user-confirmed-unchanged-state": (facts(claim_time="current", mutation_authority="externally-writable",
                                             authority_basis="permission", writer_confirmed_unchanged=True), "reuse"),
    "user-edits-saved-file-directly": (facts(claim_time="current", mutation_authority="externally-writable",
                                             authority_basis="interaction-contract"), "verify"),
    "user-supplied-edited-cells": (facts(claim_time="current", events_since_evidence=["user-provided-changed-artifact"]), "verify"),
    "explicit-availability-recheck": (facts(claim_time="current", explicit_recheck_request=True), "verify"),
    "stale-extract-versus-latest-deck": (facts(claim_time="current", claim_copy="deck:latest",
                                               retained_evidence={"present": True, "copy": "registry:2024-extract", "origin": "inspected"}), "verify"),
    "substitute-renderer-preview": (facts(claim_time="current", claim_copy="pptx:rendered",
                                          retained_evidence={"present": True, "copy": "pptx:rendered", "origin": "authored"},
                                          events_since_evidence=["agent-write-succeeded"], planned_check_copy="libreoffice:preview"), "wrong-copy"),
    "disk-write-versus-editor-buffer": (facts(claim_time="current", claim_copy="editor:buffer",
                                              retained_evidence={"present": True, "copy": "disk:file", "origin": "authored"}), "verify"),
    "settled-decision-rechecked": (facts(claim_time="current", mutation_authority="closed"), "reuse"),
}


class RealCaseReplayTests(_ModuleTestCase):
    def test_real_case_replays_match_the_verdict_each_correction_demanded(self) -> None:
        for name, (data, expected) in REAL_CASE_FIXTURES.items():
            with self.subTest(case=name):
                self.assertEqual(VOR.decide(data)["verdict"], expected)

    def test_writer_confirmation_does_not_override_an_observed_event(self) -> None:
        result = VOR.decide(facts(claim_time="current", mutation_authority="externally-writable", authority_basis="permission",
                                  writer_confirmed_unchanged=True, events_since_evidence=["external-revision"]))
        self.assertEqual(result["verdict"], "verify")


class AuthoritativeCopyTests(_ModuleTestCase):
    def test_user_designated_copy_wins(self) -> None:
        result = VOR.authoritative_copy([
            {"copy": "deck:v7", "basis": "dated-version", "version": "2026-09-20"},
            {"copy": "deck:user-pick", "basis": "user-designated"},
        ])
        self.assertEqual((result["status"], result["copy"]), ("selected", "deck:user-pick"))

    def test_newest_dated_version_beats_a_folder_named_latest(self) -> None:
        result = VOR.authoritative_copy([
            {"copy": "assets/latest/product.png", "basis": "folder-name"},
            {"copy": "assets/2026-09-20/product.png", "basis": "dated-version", "version": "2026-09-20"},
            {"copy": "assets/2026-08-01/product.png", "basis": "dated-version", "version": "2026-08-01"},
        ])
        self.assertEqual(result["copy"], "assets/2026-09-20/product.png")
        self.assertIn("assets/latest/product.png", result["ignored"])

    def test_names_and_familiarity_alone_leave_the_copy_unresolved(self) -> None:
        result = VOR.authoritative_copy([
            {"copy": "assets/latest/product.png", "basis": "folder-name"},
            {"copy": "assets/used-last-time.png", "basis": "familiarity"},
        ])
        self.assertEqual((result["status"], result["copy"]), ("unresolved", None))

    def test_tied_evidence_is_unresolved(self) -> None:
        result = VOR.authoritative_copy([
            {"copy": "a", "basis": "dated-version", "version": "2026-09-20"},
            {"copy": "b", "basis": "content-compared", "version": "2026-09-20"},
        ])
        self.assertEqual(result["status"], "unresolved")


class RestatementAuditTests(_ModuleTestCase):
    def test_sentence_already_delivered_is_flagged_in_the_later_message(self) -> None:
        audit = VOR.audit_restatement([
            "The archive is a collection kit, not ledger data. Upload the exported archive.",
            "Confirmed facts: the archive is a collection kit, not ledger data. Nothing else changed.",
        ])
        self.assertEqual([(f["message"], f["first_seen"]) for f in audit["findings"]], [(1, 0)])

    def test_new_content_and_fenced_control_blocks_are_not_flagged(self) -> None:
        block = "```text\n[gate-state]\n- task-router: done (routing recorded for this turn)\n```\n"
        audit = VOR.audit_restatement([block + "Result A is ready for review now.", block + "Result B replaces the old draft."])
        self.assertEqual(audit["findings"], [])

    def test_short_acknowledgements_are_ignored(self) -> None:
        self.assertEqual(VOR.audit_restatement(["알겠습니다.", "알겠습니다."])["findings"], [])


class CommandLineTests(_ModuleTestCase):
    def _run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", str(MODULE_PATH), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )

    def test_cli_decides_without_claude_skill_dir(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != "CLAUDE_SKILL_DIR"}
        result = self._run("decide", "--facts-json", json.dumps(INCIDENT), env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["verdict"], "reuse")
        self.assertEqual(payload["schema_version"], VOR.SCHEMA_VERSION)

    def test_cli_audit_reports_redundant_reread(self) -> None:
        result = self._run(
            "audit", "--platform", "codex", "--facts-json", json.dumps(INCIDENT),
            "--calls-json", json.dumps([{"tool": "shell", "copy": "chat-sandbox:plan.md"}]),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["findings"][0]["finding"], "redundant-verification")

    def test_cli_rejects_invalid_facts(self) -> None:
        result = self._run("decide", "--facts-json", json.dumps({**INCIDENT, "mutation_authority": "maybe"}))
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutation_authority", result.stderr)


class ReferenceContractTests(_ModuleTestCase):
    def test_reference_states_the_flow_and_every_enum_value(self) -> None:
        text = " ".join(REFERENCE_PATH.read_text(encoding="utf-8").split())
        self.assertIn(FLOW, text)
        vocabulary = (
            *VOR.CLAIM_TIMES, *VOR.AUTHORS, *VOR.AUTHORITIES, *VOR.MUTATION_EVENTS,
            *VOR.NON_MUTATION_EVENTS, *VOR.VERDICTS, *VOR.TRIGGERS, *VOR.CLAIM_SCOPES, *VOR.COPY_BASES,
        )
        for value in vocabulary:
            with self.subTest(value=value):
                self.assertIn(f"`{value}`", text)

    def test_skill_points_to_reference_and_script_without_claude_only_paths(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/verify-or-reuse.md", skill)
        self.assertIn("scripts/verify_or_reuse.py", skill)
        for path in (SKILL_DIR / "SKILL.md", REFERENCE_PATH):
            with self.subTest(path=path.name):
                self.assertNotIn("CLAUDE_SKILL_DIR", path.read_text(encoding="utf-8"))


class RuntimeSurfaceTests(unittest.TestCase):
    def _run_pretool(self, platform: str) -> dict:
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node is required to execute the hook dispatcher")
        with tempfile.TemporaryDirectory() as temp_home:
            env = {key: value for key, value in os.environ.items() if not key.startswith("GHOST_ALICE_")}
            env.update({"HOME": temp_home, "USERPROFILE": temp_home})
            result = subprocess.run(
                [node, str(HOOK_DISPATCHER), "--platform", platform, "--event", "PreToolUse", "--hook", "tool-checkpoint"],
                input=json.dumps({"session_id": f"s-{platform}-verify-or-reuse", "hook_event_name": "PreToolUse",
                                  "tool_name": "Read", "tool_input": {"file_path": "plan.md"}}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_codex_pretool_checkpoint_reminder_carries_verify_or_reuse_field(self) -> None:
        payload = self._run_pretool("codex")
        context = payload.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn(TOOL_CHECKPOINT_FIELD_HOOK, context)
        self.assertNotIn("permissionDecision", payload.get("hookSpecificOutput", {}))

    def test_claude_pretool_allow_carries_no_context_so_rule_blocks_carry_the_contract(self) -> None:
        self.assertEqual(self._run_pretool("claude"), {})
        port = " ".join((REPO_ROOT / "platforms" / "claude" / "CLAUDE.md").read_text(encoding="utf-8").split())
        self.assertIn(VERIFY_OR_REUSE_PRECHECK, port)

    def _merge(self, command: str, source: Path, dest: Path) -> str:
        result = subprocess.run(
            [sys.executable, "-B", str(RULE_BLOCKS), command, "--source", str(source), "--dest", str(dest)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("updated:"), result.stdout)
        return " ".join(dest.read_text(encoding="utf-8").split())

    def test_installer_rule_block_merge_carries_contract_for_both_platforms(self) -> None:
        spec = importlib.util.spec_from_file_location("global_rule_blocks_under_test", RULE_BLOCKS)
        blocks = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = blocks  # dataclasses resolve their defining module through sys.modules
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(blocks)
        targets = {
            "claude-merge": (REPO_ROOT / "platforms" / "claude" / "CLAUDE.md", blocks.CLAUDE_SPEC),
            "codex-merge": (REPO_ROOT / "platforms" / "codex" / "AGENTS.md", blocks.CODEX_SPEC),
        }
        for command, (source, block_spec) in targets.items():
            with self.subTest(command=command, case="fresh-install"), tempfile.TemporaryDirectory() as temp_dir:
                merged = self._merge(command, source, Path(temp_dir) / "rules.md")
                self.assertIn(VERIFY_OR_REUSE_PRECHECK, merged)
                self.assertIn(CHANGE_DEFINITION, merged)
            with self.subTest(command=command, case="refresh-managed-block"), tempfile.TemporaryDirectory() as temp_dir:
                dest = Path(temp_dir) / "rules.md"
                dest.write_text(
                    f"User-owned note stays.\n{block_spec.begin}\nstale managed text\n{block_spec.end}\n",
                    encoding="utf-8",
                )
                merged = self._merge(command, source, dest)
                self.assertIn("User-owned note stays.", merged)
                self.assertNotIn("stale managed text", merged)
                self.assertIn(VERIFY_OR_REUSE_PRECHECK, merged)
                self.assertIn(CHANGE_DEFINITION, merged)


class InstalledCopyTests(unittest.TestCase):
    """Isolated-install parity; set the variables to installer outputs under a temporary HOME."""

    FILES = ("SKILL.md", "references/verify-or-reuse.md", "scripts/verify_or_reuse.py")

    def _check_installed_skill(self, skills_dir: Path) -> None:
        installed = skills_dir / "verification-before-completion"
        for rel in self.FILES:
            with self.subTest(file=rel):
                self.assertEqual((installed / rel).read_bytes(), (SKILL_DIR / rel).read_bytes())
        env = {key: value for key, value in os.environ.items() if key != "CLAUDE_SKILL_DIR"}
        result = subprocess.run(
            [sys.executable, "-B", str(installed / "scripts" / "verify_or_reuse.py"), "decide", "--facts-json", json.dumps(INCIDENT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["verdict"], "reuse")

    def test_installed_codex_copy_matches_repository_ssot(self) -> None:
        skills_dir = os.environ.get("GHOST_ALICE_INSTALLED_SKILLS_DIR")
        if not skills_dir:
            self.skipTest("set GHOST_ALICE_INSTALLED_SKILLS_DIR to an isolated Codex install")
        skills_path = Path(skills_dir)
        self._check_installed_skill(skills_path)
        agents = skills_path.parents[1] / ".codex" / "AGENTS.md"
        if agents.is_file():
            self.assertIn(VERIFY_OR_REUSE_PRECHECK, " ".join(agents.read_text(encoding="utf-8").split()))

    def test_installed_claude_copy_matches_repository_ssot(self) -> None:
        claude_dir = os.environ.get("GHOST_ALICE_INSTALLED_CLAUDE_DIR")
        if not claude_dir:
            self.skipTest("set GHOST_ALICE_INSTALLED_CLAUDE_DIR to an isolated Claude install")
        claude_path = Path(claude_dir)
        self._check_installed_skill(claude_path / "skills")
        self.assertIn(VERIFY_OR_REUSE_PRECHECK, " ".join((claude_path / "CLAUDE.md").read_text(encoding="utf-8").split()))


if __name__ == "__main__":
    unittest.main()
