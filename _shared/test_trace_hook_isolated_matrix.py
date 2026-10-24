"""Isolated production-hook matrix for retained proof, diagnostic assessment and native envelopes.

Runs real subprocess hooks and installer-generated commands in a disposable
source/home tree. This proves supplied-payload behavior, not external host event
firing or model compliance. Set GHOST_ALICE_TRACE_MATRIX_SOURCE_ROOT to an
immutable source checkout to compare a baseline without changing installed files.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_completion_skill_evidence as skill_fixture
import completion_check_validator as proof_validator
from pathlib import Path


PLATFORMS = ("claude", "codex")
PROFILES = ("strict", "dynamic", "minimal")
HOSTS = ("cli", "claude-desktop", "claude-vscode", "remote_desktop", "remote_mobile",
         "sdk-cli", "sdk-ts", "sdk-py", "some-future-host", None)
BUSINESS = "Operating time: 82 hours 40 minutes."
DIRECT_KEYS = ("last_assistant_message", "lastAssistantMessage", "prompt_response", "promptResponse")


def prompt(text="Synthetic matrix request.", **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def skill():
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": "verification-before-completion"}}]}}


def reply(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def valid_text(*, verdict="pass"):
    criteria = "- acceptance-criteria:\n  - calculation: recompute time [source: user-said]\n"
    reference = "    criterion: calculation\n"
    return (f"{BUSINESS}\n\n[completion-check]\n{criteria}"
            f"- claim-evidence-map:\n  - claim: operating time was checked\n{reference}"
            f"    evidence: synthetic calculation subprocess exited {0 if verdict == 'pass' else 1}\n"
            f"    verdict: {verdict}\n- unverified:\n  - none\n")


class TraceHookIsolatedMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = tempfile.TemporaryDirectory(prefix="trace-hook-matrix-source-")
        cls.addClassCleanup(cls.sandbox.cleanup)
        cls.source = Path(cls.sandbox.name) / "source"
        selected = os.environ.get("GHOST_ALICE_TRACE_MATRIX_SOURCE_ROOT")
        origin = Path(selected).resolve() if selected else Path(__file__).resolve().parents[1]
        for name in ("_shared", "session-intent-analyzer", "skill-catalog"):
            shutil.copytree(origin / name, cls.source / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "test_*.py"))
        cls.shared = cls.source / "_shared"
        cls.ledger = cls.source / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py"
        cls.ledger_root = cls.source / ".tmp" / "session-intent"
        cls.node = shutil.which("node") or shutil.which("node.exe")
        cls.shell = shutil.which("sh") if os.name != "nt" else None

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(prefix="trace-hook-matrix-home-")
        self.addCleanup(self.case.cleanup)
        self.home = Path(self.case.name)
        self.counter = 0
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("GHOST_ALICE_", "CODEX_", "CLAUDE_", "NODE_REPL_"))}
        self.env.update({"HOME": str(self.home), "USERPROFILE": str(self.home),
                         "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                         "CODEX_HOME": str(self.home / ".codex"),
                         "GHOST_ALICE_SESSION_INTENT_ROOT": str(self.ledger_root),
                         "GHOST_ALICE_GOVERNANCE_EVENTS": str(self.home / "governance-events.jsonl"),
                         "GHOST_ALICE_PYTHON": sys.executable,
                         "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
        for name in ("TEMP", "TMP", "TMPDIR"):
            self.env[name] = str(self.home)
        if self.node:
            self.env["GHOST_ALICE_NODE"] = str(Path(self.node).resolve())

    def run_process(self, argv, payload=None, *, env=None):
        result = subprocess.run(argv, input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                                cwd=self.home, env=env or self.env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30, check=False)
        self.assertEqual(result.returncode, 0, msg=f"{argv}\n{result.stdout}\n{result.stderr}")
        return result

    def stop(self, platform, text=None, *, entries=None, profile="dynamic", host=None,
             direct_key="last_assistant_message", transcript_key="transcript_path", extra=None, purpose=None):
        env = {**self.env, "GHOST_ALICE_AGENT_VISIBILITY": profile}
        if host is not None:
            env["CLAUDE_CODE_ENTRYPOINT"] = host
        self.counter += 1
        payload = dict(extra or {})
        # These repair cases exercise an explicitly usable visible prior answer.
        # Final-only/unknown assembly and absent usable prior are covered in test_stop_retry_surface.
        payload.setdefault("host_context",{"delivery_surface":"visible_transcript"})
        payload.setdefault("prior_answer_ref","fixture:usable-business-answer")
        if text is not None:
            payload[direct_key] = text
        if entries is None:
            entries = [prompt(), skill(), reply(text or "")]
        transcript = self.home / f"synthetic-{self.counter}.jsonl"
        transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
        payload[transcript_key] = str(transcript)
        from claude_stop_verification_hook import _assistant_text_this_turn
        selected = text or _assistant_text_this_turn(entries)
        if selected:
            self.bind_review(platform, selected, env, purpose=purpose)
            payload['session_id'] = 'fixture-stop'
        return json.loads(self.run_process([sys.executable, "-B", str(self.shared / "claude_stop_verification_hook.py"),
                                           "--platform", platform], payload, env=env).stdout)

    def assert_allow(self, payload):
        self.assertTrue(payload.get("continue"), msg=payload)
        self.assertNotIn("decision", payload)

    def assert_unaccepted(self, payload):
        self.assert_allow(payload)
        observation = payload.get("response_assessment", {})
        self.assertEqual(observation.get("status"), "unaccepted")
        self.assertTrue(observation.get("body_issue"))
        self.assertNotIn("systemMessage", payload)

    def assert_diagnostic(self, payload):
        self.assert_unaccepted(payload)
        self.assertEqual(payload['response_assessment']['repair_target'], 'completion_metadata')
        self.assertNotIn('reason', payload)

    def bind_review(self, platform, text, env, *, purpose=None, session='fixture-stop', ledger_root=None):
        """Authenticate synthetic protocol observations; submit defects separately."""
        root = self.home / ('review-' + str(self.counter))
        root.mkdir(exist_ok=True)
        marked = '[completion-check]' in text.lower() or '[partial-check]' in text.lower()
        reviewed = text if marked and proof_validator.validate_completion_text(
            text, require_completion_check=True) is None else valid_text()
        review_root = ledger_root or root / 'ledger'
        skill_fixture.StopHookSkillEvidenceTests._review_format_fixture(root, platform, reviewed, env,
            session_id=session, ledger_root=review_root, core_root=self.source,
            input_text='Synthetic matrix request.')
        if marked and purpose != 'completion':
            return
        import test_response_route as fixtures
        import completion_assessment as review
        coords = dict(root=review_root, platform=platform, session_id=session)
        state = fixtures.ledger.read_session_state(**coords)
        route = fixtures.ResponseRouteTests().candidate()
        if purpose == 'completion':
            route['work_units'][0]['owner'] = dict(platform=platform, session_id=session)
            route['selected_criteria'][0]['criterion_id'] = 'calculation'
            fixtures.ledger.record_response_route(**coords, expected_input_event_id=state['latest_input_event_id'],
                candidate=route, boundary_ref='synthetic-protocol')
            return
        route.update(work_units=[], selected_work_unit_ids=[], selected_criteria=[])
        fixtures.ledger.record_response_route(**coords, expected_input_event_id=state['latest_input_event_id'],
            candidate=route, boundary_ref='synthetic-protocol')
        observation = root / 'explanation.txt'
        observation.write_text(text)
        candidate = review.prepare_response_review(env, response_text=text,
            source_paths=[str(observation)], claim_statements=[text])
        source_ids = [candidate['sources'][0]['source_id']]
        review.record_response_review(env, candidate['candidate_reference'], dict(
            claims=[dict(binding_id=b['binding_id'], source_ids=source_ids, verdict='supported',
                rationale='This synthetic observation isolates delivery protocol from semantic review.')
                for b in candidate['bindings']],
            response=dict(binding_id=candidate['response_binding']['binding_id'], source_ids=source_ids,
                verdict='supported', rationale='The complete synthetic explanation matches its declared purpose.')))


    @contextlib.contextmanager
    def installed(self, platform, profile):
        if not self.node or not self.shell:
            self.skipTest("Installed command matrix needs existing Node and a native POSIX shell; no runtime is installed.")
        home = self.home / f"installed-{platform}-{profile}"
        for name in (".claude", ".codex"):
            (home / name).mkdir(parents=True, exist_ok=True)
        # The public installer installs skills before hooks. The relocated Node
        # dispatcher resolves its ledger reader through these installed paths.
        for name in (".agents", ".claude"):
            shutil.copytree(self.source / "session-intent-analyzer" / "scripts",
                            home / name / "skills" / "session-intent-analyzer" / "scripts")
        env = {**self.env, "HOME": str(home), "USERPROFILE": str(home),
               "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CODEX_HOME": str(home / ".codex")}
        self.run_process([sys.executable, "-B", str(self.shared / "install_hooks.py"),
                          "--platform", platform, "--visibility", profile,
                          "--hook-shared-dir", str(self.shared)], env=env)
        config = home / (".claude/settings.json" if platform == "claude" else ".codex/hooks.json")
        hooks = json.loads(config.read_text(encoding="utf-8"))["hooks"]
        yield home, env, hooks

    def installed_hook(self, hooks, event, hook_id, payload, env):
        commands = [hook["command"] for group in hooks[event] for hook in group["hooks"]
                    if f"[hook-runner:{hook_id}]" in hook.get("command", "")]
        self.assertEqual(len(commands), 1, msg=(event, hook_id))
        return self.run_process([self.shell, "-c", commands[0]], payload, env=env)

    def strict_rows(self, home, platform, session):
        path = home / ".ghost-alice" / "session-logs" / platform / session / "strict-hook-output.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def assert_audit_surface(self, result, profile):
        # Wrapper projection is separate from the silent
        # child audit hook whose original stdout is kept in the strict log.
        self.assertEqual(result.stdout, "io-trace observed\n" if profile == "minimal" else "")

    def observe(self, platform, session):
        self.run_process([sys.executable, "-B", str(self.ledger), "--root", str(self.ledger_root),
                                   "--platform", platform, "--session-id", session,
                                   "--input", "Synthetic isolated security case."])
        state = self.run_process([sys.executable, "-B", str(self.ledger), "--root", str(self.ledger_root),
                                  "--platform", platform, "--session-id", session, "--snapshot"])
        return json.loads(state.stdout)["latest_input_event_id"]

    def test_equivalent_field_and_line_serializations(self):
        for platform, form in itertools.product(PLATFORMS, ("lf", "crlf", "none-inline", "semicolon", "indented", "upper-header")):
            text = valid_text()
            if form == "crlf":
                text = text.replace("\n", "\r\n")
            elif form == "none-inline":
                text = text.replace("- unverified:\n  - none", "- unverified: none")
            elif form == "semicolon":
                text = text.replace("\n    criterion:", "; criterion:").replace("\n    evidence:", "; evidence:").replace("\n    verdict:", "; verdict:")
            elif form == "indented":
                text = text.replace("[completion-check]", "  [completion-check]")
            elif form == "upper-header":
                text = text.replace("[completion-check]", "[COMPLETION-CHECK]")
            with self.subTest(platform=platform, form=form):
                self.assert_allow(self.stop(platform, text))

    def test_answers_that_claim_no_success_pass_without_screen_trace(self):
        answers = ("Here is why the hook requests that evidence.",
                   "What missing input determines the next action?", "A bounded general explanation.",
                   "I have not verified that the tests pass. Verification remains unresolved.",
                   "The unsupported completion claim is withdrawn; calculation remains unverified.")
        for platform, profile, text in itertools.product(PLATFORMS, PROFILES, answers):
            with self.subTest(platform=platform, profile=profile, text=text):
                self.assert_allow(self.stop(platform, text, entries=[prompt(), reply(text)], profile=profile))
        for platform, profile in itertools.product(PLATFORMS, PROFILES):
            self.assert_allow(self.stop(platform, valid_text(verdict="fail"), profile=profile))


    def test_empty_and_non_object_stop_payloads_are_protocol_noops(self):
        for platform, payload in itertools.product(PLATFORMS, ({}, [], "not an object", {"last_assistant_message": ""})):
            with self.subTest(platform=platform, payload=payload):
                result = self.run_process([sys.executable, "-B", str(self.shared / "claude_stop_verification_hook.py"),
                                           "--platform", platform], payload)
                self.assert_allow(json.loads(result.stdout))

    def test_core_evidence_negatives_are_unaccepted(self):
        changes = (
            ("claim: operating time was checked", "claim:"),
            ("    evidence: synthetic calculation subprocess exited 0\n", ""),
            ("    verdict: pass\n", ""), ("verdict: pass", "verdict: pass."),
            ("verdict: pass", "verdict: 'pass'"), ("verdict: pass", "verdict: unverified"),
            ("criterion: calculation", "criterion: missing"),
            ("  - none", "  - none; pending calculation"),
            ("    verdict: pass", "    verdict: fail\n    verdict: pass"),
            ("- unverified:\n  - none", "- unverified:\n  - none\n- unverified: pending"),
            ("- unverified:", "  - invalid: lacks explicit claim\n- unverified:"),
        )
        for platform, profile, (old, new) in itertools.product(PLATFORMS, PROFILES, changes):
            with self.subTest(platform=platform, profile=profile, change=new):
                self.assert_unaccepted(self.stop(platform, valid_text().replace(old, new), profile=profile))
        for profile in PROFILES:
            self.assert_allow(self.stop("claude", valid_text(), entries=[prompt(), reply(valid_text())], profile=profile))

    def test_payload_alias_and_transcript_precedence(self):
        for platform, key, path_key, direct_valid in itertools.product(PLATFORMS, DIRECT_KEYS,
                                                                       ("transcript_path", "transcriptPath"), (False, True)):
            good = valid_text()
            bad = good.replace("    evidence: synthetic calculation subprocess exited 0\n", "")
            direct, recorded = (good, bad) if direct_valid else (bad, good)
            with self.subTest(platform=platform, key=key, path_key=path_key, direct_valid=direct_valid):
                result = self.stop(platform, direct, entries=[prompt(), skill(), reply(recorded)],
                                   direct_key=key, transcript_key=path_key)
                expected_valid = direct_valid
                (self.assert_allow if expected_valid else self.assert_unaccepted)(result)
        for platform, key in itertools.product(PLATFORMS, DIRECT_KEYS):
            good = valid_text()
            self.assert_allow(self.stop(platform, "", direct_key=key, entries=[prompt(), skill(), reply(good)]))
        bad = valid_text().replace("    evidence: synthetic calculation subprocess exited 0\n", "")
        self.assert_unaccepted(self.stop("codex", bad, direct_key="lastAssistantMessage",
                                    extra={"last_assistant_message": bad, "prompt_response": valid_text()}))

    def test_last_reply_only_after_meta_tool_results_and_ask_answers(self):
        boundaries = (prompt("A real subsequent question."),
                      prompt([{ "type": "text", "text": "A real subsequent question."}]),
                      prompt("Stop hook feedback: [completion-gate] previous defect", isMeta=True),
                      prompt("Other meta feedback", isMeta=True),
                      prompt([{"type": "tool_result", "tool_use_id": "synthetic-read", "content": "Read result"}]),
                      prompt([{"type": "tool_result", "tool_use_id": "synthetic-ask", "content": "Option A"}]),
                      prompt([{"type": "text", "text": "tool explanation"}, {"type": "tool_result", "content": "result"}]))
        for platform, boundary, final_valid in itertools.product(PLATFORMS, boundaries, (False, True)):
            good = valid_text()
            bad = good.replace("    evidence: synthetic calculation subprocess exited 0\n", "")
            before, final = (bad, good) if final_valid else (good, bad)
            entries = [prompt(), skill(), reply(before), boundary, skill(), reply(final)]
            with self.subTest(platform=platform, boundary=boundary, final_valid=final_valid):
                payload = self.stop(platform, final, entries=entries)
                if final_valid:
                    self.assert_allow(payload)
                else:
                    self.assert_unaccepted(payload)

    def test_claude_review_is_independent_of_skill_call_display(self):
        good = valid_text()
        for boundary in (prompt('Real new input'), prompt('meta', isMeta=True),
                         prompt([{'type':'tool_result', 'content':'answer'}])):
            with self.subTest(boundary=boundary):
                self.assert_allow(self.stop('claude', None,
                    entries=[prompt(), skill(), reply(good), boundary, reply(good)]))


    def test_no_current_reply_after_real_input_cannot_revalidate_previous_answer(self):
        boundaries = ((True, prompt("A real new user input.")),
                      (True, prompt([{"type": "text", "text": "A real new user input."}])),
                      (False, prompt("Meta feedback", isMeta=True)),
                      (False, prompt([{"type": "tool_result", "tool_use_id": "read", "content": "tool result"}])),
                      (False, prompt([{"type": "tool_result", "tool_use_id": "ask", "content": "Option A"}])))
        for platform, previous_valid, (new_turn, boundary) in itertools.product(PLATFORMS, (False, True), boundaries):
            previous = valid_text()
            if not previous_valid:
                previous = previous.replace("    evidence: synthetic calculation subprocess exited 0\n", "")
            with self.subTest(platform=platform, previous_valid=previous_valid, new_turn=new_turn, boundary=boundary):
                payload = self.stop(platform, None, entries=[prompt(), skill(), reply(previous), boundary])
                self.assert_allow(payload)
                self.assertNotIn("response_assessment", payload)

    def test_recovery_never_forces_business_replay_for_any_configured_host(self):
        for platform, profile, host, defect in itertools.product(PLATFORMS, PROFILES, HOSTS,
                                                                ("missing-evidence", "bad-verdict")):
            text = valid_text()
            if defect == "missing-evidence":
                text = text.replace("    evidence: synthetic calculation subprocess exited 0\n", "")
            else:
                text = text.replace("verdict: pass", "verdict: pass.")
            with self.subTest(platform=platform, profile=profile, host=host, defect=defect):
                self.assert_diagnostic(self.stop(platform, text, profile=profile, host=host, purpose="completion"))

    def test_repeated_metadata_defect_does_not_request_answer_retry(self):
        for platform, profile in itertools.product(PLATFORMS, PROFILES):
            bad = valid_text().replace("    evidence: synthetic calculation subprocess exited 0\n", "")
            for hook_active in (False, True):
                with self.subTest(platform=platform, profile=profile, hook_active=hook_active):
                    result = self.stop(platform, bad, profile=profile, extra={"stop_hook_active": hook_active})
                    self.assert_diagnostic(result)
                    self.assertFalse('retry' in json.dumps(result).lower())
            self.assert_allow(self.stop(platform, valid_text(), profile=profile))

    def test_installed_lifecycle_profile_matrix_keeps_audit_and_control_protocol(self):
        for platform, profile in itertools.product(PLATFORMS, PROFILES):
            with self.subTest(platform=platform, profile=profile), self.installed(platform, profile) as (home, env, hooks):
                self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"})
                session = f"lifecycle-{platform}-{profile}"
                transcript = home / 'lifecycle-input.jsonl'
                transcript.write_text('')
                payload = {"session_id": session, "prompt": "Synthetic matrix request.",
                           "prompt_id": "lifecycle-prompt", "transcript_path": str(transcript),
                           "hook_event_name": "SessionStart"}
                json.loads(self.installed_hook(hooks, "SessionStart", "session-start", payload, env).stdout)
                payload["hook_event_name"] = "UserPromptSubmit"
                json.loads(self.installed_hook(hooks, "UserPromptSubmit", "session-intent", payload, env).stdout)
                if platform == 'claude':
                    transcript.write_text(json.dumps(dict(type='user', sessionId=session,
                        promptId='lifecycle-prompt', origin=dict(kind='human'), promptSource='sdk',
                        turnOrigin='human', message=dict(role='user', content=payload['prompt']))) + '\n')
                payload.update({"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "/synthetic/result.txt"}})
                for _ in range(2):
                    result = json.loads(self.installed_hook(hooks, "PreToolUse", "tool-checkpoint", payload, env).stdout)
                    self.assertNotEqual(result.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
                payload["hook_event_name"] = "PostToolUse"
                self.assert_audit_surface(self.installed_hook(hooks, "PostToolUse", "io-trace", payload, env), profile)
                good = valid_text()
                transcript = home / "synthetic-final.jsonl"
                transcript.write_text("".join(json.dumps(e) + "\n" for e in [prompt(), skill(), reply(good)]), encoding="utf-8")
                self.bind_review(platform, good, env, session=session, ledger_root=self.ledger_root)
                payload.update({"hook_event_name": "Stop", "last_assistant_message": good, "transcript_path": str(transcript)})
                self.assert_allow(json.loads(self.installed_hook(hooks, "Stop", "completion", payload, env).stdout))
                rows = self.strict_rows(home, platform, session)
                self.assertEqual(len(rows), 6)
                self.assertEqual({row["event"] for row in rows}, set(hooks))
                for row in rows:
                    self.assertEqual(row["agent_visibility_profile"], profile)
                    self.assertRegex(row["payload_digest"], r"^sha256:[0-9a-f]{64}$")
                    self.assertNotIn("stdin", row)
                    self.assertEqual(row["observed_duration_source"], "hook-runner")
                    self.assertIn("stdout", row)
                    self.assertIn("surface_item", row)
                audit = [json.loads(line) for line in (home / ".ghost-alice/io-trace.jsonl").read_text().splitlines()]
                self.assertEqual(audit[-1]["path"], "/synthetic/result.txt")
                self.assertEqual(audit[-1]["platform"], platform)
                self.assertEqual(audit[-1]["session"], session)

    def test_installed_completion_matrix_after_new_input(self):
        for platform, profile in itertools.product(PLATFORMS, PROFILES):
            with self.installed(platform, profile) as (home, env, hooks):
                session = f"completion-{platform}-{profile}"
                good = valid_text()
                bad = good.replace("    evidence: synthetic calculation subprocess exited 0\n", "")
                scenarios = ((True, good, [prompt(), skill(), reply(good)]),
                             (False, bad, [prompt(), skill(), reply(bad)]),
                             (True, "", [prompt(), skill(), reply(bad), prompt("Real new user input.")]))
                for index, (allowed, direct, entries) in enumerate(scenarios):
                    with self.subTest(platform=platform, profile=profile, scenario=index):
                        transcript = home / f"synthetic-completion-{index}.jsonl"
                        transcript.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
                        payload = {"session_id": session, "hook_event_name": "Stop", "last_assistant_message": direct,
                                   "transcript_path": str(transcript)}
                        if index == 0:
                            self.counter += 1
                            self.bind_review(platform, good, env, session=session, ledger_root=self.ledger_root)
                        output = self.installed_hook(hooks, "Stop", "completion", payload, env)
                        self.assert_allow(json.loads(output.stdout))
                rows = self.strict_rows(home, platform, session)
                self.assertEqual(len(rows), len(scenarios))
                self.assertTrue(all(row["hook_id"] == "completion" and row["event"] == "Stop" for row in rows))
                diagnostic = json.loads(rows[1]["stdout"])["response_assessment"]
                self.assertEqual(diagnostic["status"], "unaccepted")
                self.assertTrue(diagnostic["body_issue"])

    def test_installed_checkpoint_retains_block_data_without_widening_current_admission(self):
        for platform, profile in itertools.product(PLATFORMS, PROFILES):
            with self.subTest(platform=platform, profile=profile), self.installed(platform, profile) as (home, env, hooks):
                session = f"security-{platform}-{profile}"
                event_id = self.observe(platform, session)
                decision = {"decision": "block", "risk_flags": ["instruction-hierarchy-override"],
                            "reason": "synthetic current security block", "input_event_id": event_id}
                self.run_process([sys.executable, "-B", str(self.ledger), "--root", str(self.ledger_root),
                                  "--platform", platform, "--session-id", session,
                                  "--expected-input-event-id", event_id,
                                  "--delta-json", json.dumps({"model_security_decision": decision})])
                payload = {"session_id": session, "hook_event_name": "PreToolUse", "tool_name": "Read"}
                # Retained model-security data is a storage fact after removal
                # of the standalone classifier, never native permission authority.
                state_command = [sys.executable, "-B", str(self.ledger), "--root", str(self.ledger_root),
                                 "--platform", platform, "--session-id", session, "--snapshot"]
                before = json.loads(self.run_process(state_command).stdout)
                observed = self.installed_hook(hooks, "PreToolUse", "tool-checkpoint", payload, env)
                protocol = json.loads(observed.stdout)
                self.assertNotIn("permissionDecision", protocol.get("hookSpecificOutput", {}))
                self.assertEqual(json.loads(self.run_process(state_command).stdout), before)
                self.assertEqual(before["model_security_decision"]["decision"], "block")
                self.assertEqual(before["model_security_decision"]["input_event_id"], event_id)
                row = self.strict_rows(home, platform, session)[-1]
                self.assertEqual(row["event"], "PreToolUse")
                self.assertEqual(row["hook_id"], "tool-checkpoint")
                self.assertIn("surface_item", row)
                self.assertIn("observed_duration_source", row)
                # The strict row retains the child protocol independently of visibility.
                self.assertNotIn("permissionDecision", json.loads(row["stdout"]).get("hookSpecificOutput", {}))
                # Current work admission remains a separate owning Core check.
                # A checkpoint or broader focus cannot admit the prohibited unit.
                criterion = dict(id="protected-resource", summary="Protected target requires admission",
                                 source="user-said", admitted=False, status="unmet",
                                 work_unit_id="protected-unit", contract_revision=1)
                self.run_process([sys.executable, "-B", str(self.ledger), "--root", str(self.ledger_root),
                    "--platform", platform, "--session-id", session, "--expected-input-event-id", event_id,
                    "--delta-json", json.dumps({"constraints":["Do not use the protected target without admission."],
                                                "acceptance_criteria":[criterion]})])
                protected = json.loads(self.run_process(state_command).stdout)
                candidate = dict(schema_version="response-route.v1", routing_surface={
                    "response_mode":"normal", "boundary_contract":"required",
                    "reason":"Focus cannot grant resource authority"},
                    work_units=[dict(work_unit_id="protected-unit", contract_revision=1, kind="execution", admitted=True,
                                     owner=dict(platform=platform, session_id=session))],
                    selected_work_unit_ids=["protected-unit"],
                    selected_criteria=[dict(work_unit_id="protected-unit", contract_revision=1, criterion_id="protected-resource")])
                rejected = subprocess.run([sys.executable, "-B", str(self.shared / "response_route.py"),
                    "--root", str(self.ledger_root), "--platform", platform, "--session-id", session,
                    "--expected-input-event-id", event_id, "--boundary-ref", "protected-boundary",
                    "--candidate-json", json.dumps(candidate)], cwd=self.home, env=env, text=True, capture_output=True,
                    timeout=30, check=False)
                self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
                diagnostic = json.loads(rejected.stderr)
                self.assertEqual(diagnostic["error"], "route-schema")
                self.assertIn("admitted owned criterion", diagnostic["message"])
                self.assertEqual(json.loads(self.run_process(state_command).stdout), protected)
                self.assertIsNone(protected["response_route"])
                # New input changes lineage without relabeling retained history.
                self.observe(platform, session)
                current = json.loads(self.run_process(state_command).stdout)
                self.assertNotEqual(current["latest_input_event_id"], event_id)
                self.assertEqual(current["model_security_decision"], protected["model_security_decision"])
                self.installed_hook(hooks, "PreToolUse", "tool-checkpoint", payload, env)
                self.assertEqual(len(self.strict_rows(home, platform, session)), 2)

    def test_installed_posttool_audit_tool_forms_across_profiles(self):
        forms = [(name, {"file_path": "/synthetic/자료.txt"}) for name in ("Read", "Edit", "Write")]
        forms += [(name, {"path": "/synthetic", "pattern": "needle"}) for name in ("Grep", "Glob")]
        forms += [("Bash", {"command": "cat /synthetic/file.txt"}),
                  ("Agent", {"description": "Synthetic independent task"}),
                  ("Skill", {"skill": "verification-before-completion"}),
                  ("WebFetch", {"url": "https://example.invalid/synthetic"}),
                  ("WebSearch", {"query": "synthetic query"}),
                  ("exec_command", {"cmd": "cat /synthetic/file.txt"}),
                  ("future-tool", {"path": "/synthetic/future.txt"})]
        for platform, profile in itertools.product(PLATFORMS, PROFILES):
            with self.installed(platform, profile) as (home, env, hooks):
                session = f"audit-{platform}-{profile}"
                for tool, tool_input in forms:
                    with self.subTest(platform=platform, profile=profile, tool=tool):
                        payload = {"session_id": session, "platform": "foreign", "hook_event_name": "PostToolUse",
                                   "tool_name": tool, "tool_input": tool_input}
                        self.assert_audit_surface(self.installed_hook(hooks, "PostToolUse", "io-trace", payload, env), profile)
                rows = self.strict_rows(home, platform, session)
                audit = [json.loads(line) for line in (home / ".ghost-alice/io-trace.jsonl").read_text().splitlines()]
                self.assertEqual(len(rows), len(forms))
                self.assertEqual(len(audit), len(forms))
                self.assertEqual([row["tool"] for row in audit], [form[0] for form in forms])
                self.assertTrue(all(row["platform"] == platform and row["session"] == session for row in audit))
                self.assertEqual(audit[0]["path"], "/synthetic/자료.txt")
                self.assertEqual(audit[5]["pattern"], "cat /synthetic/file.txt")
                self.assertTrue(all(row["hook_id"] == "io-trace" and row["stdout"] == "" for row in rows))


if __name__ == "__main__":
    unittest.main()
