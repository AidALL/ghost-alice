import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_skill_gate_contract import REQUIRED_PATTERNS, run_contract_checks  # noqa: E402


CONTRACT = {
    "version": 1,
    "always_first": ["session-intent-analyzer"],
    "agent_first_after_intake": ["task-router"],
    "user_input_graph": [
        "merge-companion-precheck",
        "session-intent-analyzer",
        "skill-evolution:report-only-terminal-branch",
        "jailbreak-detector",
        "task-router",
        "tool-checkpoint",
    ],
    "intent_consumers": {
        "session-intent-analyzer": ["skill-evolution", "jailbreak-detector"],
        "skill-evolution": ["report-only-terminal-branch"],
        "jailbreak-detector": ["task-router"],
    },
    "conditional_gates": [
        {
            "id": "session-intent-analyzer",
            "trigger": "every user input",
            "position": "after-merge-companion-precheck-before-skill-evolution-and-jailbreak-detector",
        },
        {
            "id": "boundary-contract",
            "trigger": "task-router boundary-contract: required",
            "position": "after-task-router-before-tool-call",
        }
    ],
    "domain_routes": {
        "development": ["using-coding-convention"],
        "bugfix": ["systematic-debugging", "test-driven-development"],
    },
    "before_completion": ["verification-before-completion"],
    "before_commit_push": ["finishing-a-development-branch"],
    "on_task_definition": ["necessity-gate"],
    "routing_surface": {
        "response_mode": ["normal", "clarification-only", "direct-response"],
        "response_order": [
            "causal-answer-first-after-necessary-evidence",
            "imperative-result-first-after-necessary-work",
            "clarification-question-only",
            "resolved-intent-first",
        ],
        "next_required": ["boundary-contract", "skill", "none", "user-input"],
    },
    "terminal_routes": {
        "clarification-only": {
            "owner": "task-router",
            "manual_pending_merge": "defer-until-next-actionable-turn",
            "downstream_work": "none",
            "user_surface": "minimum-decisive-information-only",
            "strict_logging": "active",
        },
        "direct-response": {
            "owner": "task-router",
            "manual_pending_merge": "defer-until-next-actionable-turn",
            "downstream_work": "none",
            "user_surface": "resolved-content-only",
            "strict_logging": "active",
            "ambient_context": "not-user-referent-or-inspection-authority",
            "premise_handling": "explain-before-current-state-validation",
            "evidence_planning": "after-route-classification",
            "local_diagnosis_authority": "explicit-request-or-established-referent",
        },
    },
}


class SkillGateContractTest(unittest.TestCase):
    def test_missing_clarification_only_machine_route_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            contract_path = root / "skill-catalog" / "session-gates.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract.pop("terminal_routes")
            contract_path.write_text(json.dumps(contract), encoding="utf-8")

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("clarification-only terminal route", messages)

    def test_missing_direct_response_machine_route_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            contract_path = root / "skill-catalog" / "session-gates.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["terminal_routes"].pop("direct-response")
            contract_path.write_text(json.dumps(contract), encoding="utf-8")

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("direct-response terminal route", messages)

    def test_direct_response_machine_route_rejects_ambient_context_as_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            contract_path = root / "skill-catalog" / "session-gates.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["terminal_routes"]["direct-response"].pop("ambient_context")
            contract_path.write_text(json.dumps(contract), encoding="utf-8")

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("ambient_context=not-user-referent-or-inspection-authority", messages)

    def test_direct_response_machine_route_explains_before_validating_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            contract_path = root / "skill-catalog" / "session-gates.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["terminal_routes"]["direct-response"].pop("premise_handling")
            contract_path.write_text(json.dumps(contract), encoding="utf-8")

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("premise_handling=explain-before-current-state-validation", messages)

    def test_direct_response_machine_route_classifies_before_evidence_planning(self) -> None:
        expected_messages = {
            "evidence_planning": "evidence_planning=after-route-classification",
            "local_diagnosis_authority": "local_diagnosis_authority=explicit-request-or-established-referent",
        }
        for field, expected_message in expected_messages.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                self._write_valid_fixture(root)
                contract_path = root / "skill-catalog" / "session-gates.json"
                contract = json.loads(contract_path.read_text(encoding="utf-8"))
                contract["terminal_routes"]["direct-response"].pop(field)
                contract_path.write_text(json.dumps(contract), encoding="utf-8")

                issues = run_contract_checks(root)
                messages = "\n".join(issue.message for issue in issues)
                self.assertIn(expected_message, messages)

    def test_no_work_terminal_routes_are_enforced_by_checker(self) -> None:
        required_by_surface = {
            "task-router/SKILL.md": [
                "response-mode",
                "clarification-only",
                "minimum decisive information",
                "Ambient working directory",
                "Do not validate or rebut that premise",
                "Route classification precedes evidence planning",
            ],
            "AGENTS.md": ["clarification-only", "Ambient working directory", "Do not validate or rebut that premise", "Route classification precedes evidence planning", "Do not emit `[gate-state]`, `[tool-checkpoint]`, or `[io-trace]`"],
            "platforms/codex/AGENTS.md": ["clarification-only", "minimum decisive information", "Ambient working directory", "Do not validate or rebut that premise", "Route classification precedes evidence planning"],
            "platforms/claude/CLAUDE.md": ["clarification-only", "minimum decisive information", "Ambient working directory", "Do not validate or rebut that premise", "Route classification precedes evidence planning"],
            "docs/policies/session-gate-matrix.md": ["clarification-only", "Intake and routing still run internally"],
            "coding-convention/using-coding-convention/SKILL.md": [
                "clarification-only",
                "exits before a development turn begins",
            ],
        }
        for surface in required_by_surface:
            required_by_surface[surface].append("direct-response")
        for surface, fragments in required_by_surface.items():
            for fragment in fragments:
                with self.subTest(surface=surface, fragment=fragment):
                    self.assertIn(fragment, REQUIRED_PATTERNS[surface])

    def _extract_between(self, text: str, start_marker: str, end_marker: str) -> str:
        start = text.find(start_marker)
        self.assertGreaterEqual(start, 0, f"missing start marker: {start_marker}")
        content_start = start + len(start_marker)
        end = text.find(end_marker, content_start)
        self.assertGreaterEqual(end, 0, f"missing end marker after {start_marker}: {end_marker}")
        return text[content_start:end]

    def _assert_fragment_tables(self, sections, required_by_section, forbidden_fragments) -> None:
        for section_name, fragments in required_by_section.items():
            for fragment in fragments:
                with self.subTest(section=section_name, required=fragment):
                    self.assertIn(fragment, sections[section_name])
        for section_name, fragment in forbidden_fragments:
            with self.subTest(section=section_name, forbidden=fragment):
                self.assertNotIn(fragment, sections[section_name])

    def _write_file(self, root: Path, rel_path: str, content: str) -> None:
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_valid_fixture(self, root: Path) -> None:
        self._write_file(
            root,
            "skill-catalog/session-gates.json",
            json.dumps(CONTRACT, ensure_ascii=False, indent=2),
        )
        self._write_file(
            root,
            "docs/policies/session-gate-matrix.md",
            "skill-catalog/session-gates.json\ntask-router\nusing-coding-convention\nboundary-contract\nsession-intent-analyzer\nsession-intent-analyzer intake is first\nsession-intent-analyzer fans out to `skill-evolution` (report-only terminal branch) and `jailbreak-detector`\n`task-router` is a consumer of `session-intent-analyzer` and `jailbreak-detector/downstream-gates.json` context\ntask-router is released after session-intent preflight when no current-lineage block gate exists\nabsent `downstream-gates.json` is silent allow\natomic meaning decomposition\nboundary-contract: required\nEnglish canonical narrative + English control surface\n[gate-state]\n[completion-check]\nacceptance-criteria\nclaim-evidence-map\nunverified\nskill-call:\nRequired gate skills are not complete unless the actual `SKILL.md` file was read\nRead the relevant `SKILL.md` before marking a required gate done\nRuntime Hook Graph Contract\nHook pending-merge precheck is a pre-routing/session-start layer\nmanual fallback still completes before actionable downstream work\nintent-state.json is update-plus-accumulate state\ndownstream-gates.json\nskill-evolution is report-only and self-terminating\ndoes not feed task-router\nonly current-lineage block decisions are carried to `downstream-gates.json`\nuser-explicit defer/skip may continue\nDeterministic hard-block rules are narrow regression guards\nGradual multi-turn jailbreak resistance depends on session-intent summary quality\nPreToolUse/BeforeTool checkpoint\nhook-stage: PreToolUse\nmeaning: tool-call retry checkpoint, not user-input intake\nRuntime hooks surface one full `[tool-checkpoint]` per session input lineage\nkeep checking later tool calls silently\nnew user input, current-lineage block/deny, mismatch\nfirst poll in the input lineage\nDynamic Focus Contract\nmismatch location\nverification burden\nmicro, meso, macro, and meta\nscope reopen point\nRouting Surface Contract\nrouting-surface\nprimary-request\ncausal-axis\nresponse-mode\nresponse-order\nclarification-only\ndirect-response\nresolved-intent-first\nIntake and routing still run internally\nminimum decisive information\nThe user's terminal objective outranks investigative means.\nDo not let a means replace, narrow, or expand the terminal objective.\ntask-router owns the reusable work judgment\nsession-intent-analyzer records semantic facts and accumulated decisions\ngovernance surface policy consumes routing-surface\ntoken reduction is a secondary consequence, not a success metric\n",
        )
        self._write_file(
            root,
            "AGENTS.md",
            "skill-catalog/session-gates.json\ndocs/policies/session-gate-matrix.md\nboundary-contract\nsession-intent-analyzer\nsession-intent-analyzer fans out to skill-evolution(report-only terminal branch) and jailbreak-detector\n`task-router` is a consumer of `session-intent-analyzer` and `jailbreak-detector/downstream-gates.json` context\ntask-router reminder hook withholds task-router until session-intent preflight exists and jailbreak-detector has had the first chance to record a current-lineage block\nabsent `downstream-gates.json` is silent allow\natomic meaning decomposition\nboundary-contract: required\nhook-verified\nEnglish canonical narrative + English control surface\n[gate-state]\n[completion-check]\nacceptance-criteria\nclaim-evidence-map\nunverified\nskill-call:\nA required gate skill is not satisfied by a metadata-only match\nmust read the `SKILL.md`\nHook pending-merge precheck is a pre-routing/session-start layer\nmanual fallback still completes before actionable downstream work\nexplicit user defer or skip\ndownstream-gates.json\nhook-stage: PreToolUse\nmeaning: tool-call retry checkpoint, not user-input intake\ncurrent-lineage block gate\nsilent allow\ntool-call identity\npayload content\noutside the decision body\nThe focus scope is not fixed and does not expand in only one direction\nthe mismatch location and the verification burden\nmicro, meso, macro, and meta\ntask-router emits a reusable routing-surface\nclarification-only\ndirect-response\nDo not emit `[gate-state]`, `[tool-checkpoint]`, or `[io-trace]`\n",
        )
        self._write_file(
            root,
            "platforms/codex/AGENTS.md",
            "Session Gate Contract\nboundary-contract\nsession-intent-analyzer\n`session-intent-analyzer` branches to the `skill-evolution` report-only branch and to `jailbreak-detector`\ntask-router is a consumer of the session-intent and jailbreak gate context\nThe task-router reminder hook releases task-router when a session-intent preflight exists and there is no current-lineage block gate\nThe absence of `downstream-gates.json` is treated as a silent allow meaning there is no current-lineage block\natomic meaning decomposition\nboundary-contract: required\nhook-verified\nEnglish canonical narrative + English control surface\n[gate-state]\n[completion-check]\nacceptance-criteria\nclaim-evidence-map\nunverified\nskill-call:\nAfter the session-intent-analyzer intake and the jailbreak-detector downstream gate, read `~/.agents/skills/task-router/SKILL.md`\nA required gate skill is not satisfied by a metadata-only match\nAlways read the skill's `SKILL.md` before marking a required gate as complete\nHook pending-merge precheck is a pre-routing/session-start layer\nmanual fallback still completes before actionable downstream work\nexplicit user defer or skip\ndownstream-gates.json\nhook-stage: PreToolUse\nmeaning: tool-call retry checkpoint, not user-input intake\nthe same process, session, or tool-call id\nnew user input, current-lineage block/deny, mismatch\ncurrent-lineage block gate\nsilent allow\ntool-call identity\npayload content\noutside the decision body\nThe focus scope is not fixed and does not expand in only one direction\nthe mismatch location and the verification burden\nmicro, meso, macro, and meta\ntask-router emits a reusable routing-surface\nclarification-only\ndirect-response\nminimum decisive information\n",
        )
        self._write_file(
            root,
            "platforms/claude/CLAUDE.md",
            "clarification-only\ndirect-response\nminimum decisive information\nIntake and routing still run internally\nDo not emit `[gate-state]`, `[tool-checkpoint]`, or `[io-trace]`\n",
        )
        self._write_file(
            root,
            "install.ps1",
            "session-gates.json\nplatforms\\codex\\AGENTS.md\npending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace\n",
        )
        self._write_file(
            root,
            "install.sh",
            "session-gates.json\nplatforms/codex/AGENTS.md\npending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace\n",
        )
        self._write_file(
            root,
            "_shared/global_rule_blocks.py",
            "CODEX_SPEC\nrender_codex_bootstrap\napply_codex_bootstrap\ncodex-merge\n",
        )
        self._write_file(
            root,
            "README.md",
            "skill-catalog/session-gates.json\npython scripts/check_skill_gate_contract.py\n",
        )
        self._write_file(
            root,
            "scripts/validate_skills.py",
            "run_contract_checks\ngate-contract\n",
        )
        self._write_file(
            root,
            "task-router/SKILL.md",
            "English canonical narrative + English control surface\n[gate-state]\nrequest-routing gate\nrequest decomposition, work placement, skill routing\nconsumer of session-intent-analyzer and\nrouting decision\nraw user intent inference\nafter session-intent-analyzer intake and\nsilent allow\ncurrent-lineage block gate\natomic meaning\nnot a tool permission owner\nnot a tool-checkpoint owner\nhook-stage: PreToolUse\nmeaning: tool-call retry checkpoint, not user-input intake\nusing-coding-convention\nboundary-contract\nboundary-reason\nnext-required\nhook-verified clean\nuser-explicit defer/skip may continue\npending merge remains undecided when deferred\nskill-call:\nrouting-surface\nintent-relation\nresponse-mode\nclarification-only\ndirect-response\nresolved-intent-first\nminimum decisive information\nchange-depth\nfocus-layer\nverification-complexity\nforced-visibility\naccepted-continuation requires recorded acceptance\nunknown routing-surface values fail closed\nuser corrects the agent for changing, narrowing, widening, relabeling, or\nsubstituting the requested objective, scope, selection criterion\n",
        )
        self._write_file(
            root,
            "boundary-contract/SKILL.md",
            "---\nname: boundary-contract\ndescription: Use after task-router routes boundary-contract: required.\n---\n\nKeep field names, enum values, literal tokens, gate schemas, and allowed/forbidden control values in English.\n[boundary-contract]\n- objective: <work-objective>\nphase\nobjective\nexplicit-non-goals\nallowed-surface\nprohibited-surface\nlocked-decisions\nacceptance-criteria\nopen-questions\ntest-purpose\nstop-conditions\nnext-allowed-actions\nScope changes need renegotiation in both directions\nNever shrink or grow the committed scope silently\n",
        )
        self._write_file(
            root,
            "_shared/install_hooks.py",
            "TOOL_CHECKPOINT_MARKER\nTOOL_CHECKPOINT_INTERNAL\npre_tool_use\ntool-checkpoint\ndo not run an extra shell manifest check\nhook-enforced retry point\ntask_router_reminder_hook.py\ntask-router waits until session-intent preflight exists\nAbsent downstream-gates.json means silent allow\natomic meaning units\nfocus-layer\nscope-reopen\nhook-stage: PreToolUse\ntool-call retry checkpoint, not user-input intake\njailbreak-detector downstream gate\ntask-router consumes session-intent and jailbreak gate context\nSurface a visible [tool-checkpoint] block once per user-input tool batch, carrying intent and why\nsuppress duplicate user-facing checkpoint text for the same session input lineage while continuing to check every tool call\nAdd procedure when it changes the next work decision\nAdd contract-ref and contract-check when a boundary-contract is active\nAdd localized-human-note, rejected-alternatives, unverified-premises, and failure-mode-if-wrong only when a side effect, forced signal, mismatch, or meaningful user decision point makes those fields useful\ncontract-ref\ncontract-check\nlocalized-human-note\nrejected-alternatives\nunverified-premises\nfailure-mode-if-wrong\nrecovery-action\n",
        )
        self._write_file(
            root,
            "_shared/ghost-alice-hook.mjs",
            'case "tool-checkpoint":\ndo not run an extra shell manifest check\nPreToolUse\npermissionDecision\nhook-enforced retry point\ntask-router waits until session-intent preflight exists\nAbsent downstream-gates.json means silent allow\natomic meaning decomposition\nfocus-layer\nscope-reopen\nhook-stage: PreToolUse/BeforeTool\ntool-call retry checkpoint, not user-input intake\njailbreak-detector downstream gate\ntask-router consumes session-intent and jailbreak gate context\nSurface a visible [tool-checkpoint] block once per user-input tool batch, carrying intent and why\nsuppress duplicate user-facing checkpoint text for the same session input lineage while continuing to check every tool call\nAdd procedure when it changes the next work decision\nAdd contract-ref and contract-check when a boundary-contract is active\nAdd localized-human-note, rejected-alternatives, unverified-premises, and failure-mode-if-wrong only when a side effect, forced signal, mismatch, or meaningful user decision point makes those fields useful\ncontract-ref\ncontract-check\nlocalized-human-note\nrejected-alternatives\nunverified-premises\nfailure-mode-if-wrong\nrecovery-action\nacceptance-criteria\nclaim-evidence-map\nunverified\ndownstream-gates.json\n',
        )
        self._write_file(
            root,
            "_shared/reminder_texts.json",
            "hook-enforced retry point\nSurface a visible [tool-checkpoint] block once per user-input tool batch\nsame session input lineage\ncontinuing to check every tool call\nhook-stage: PreToolUse\ntool-call retry checkpoint, not user-input intake\nintent\nwhy\nprocedure\ncontract-ref\ncontract-check\nlocalized-human-note\nrejected-alternatives\nunverified-premises\nfailure-mode-if-wrong\nrecovery-action\nfocus-layer\nscope-reopen\nnever skip the gate\nread-only\nboundary-contract\n",
        )
        self._write_file(
            root,
            "coding-convention/using-coding-convention/SKILL.md",
            "quality-maintenance device confirmed through repeated user work\nuser intent, work scope, and verification quality\ndo not bypass it from the agent's judgment alone\nleave a short reason for the skip\n[gate-state]\n[completion-check]\nacceptance-criteria\nclaim-evidence-map\nunverified\nfinished work, verified results, or a decision that materially changes the user's next action\nclarification-only\ndirect-response\nno-work terminal route\nexits before a development turn begins\nProse paragraphs stay on one physical source line unless a semantic, syntax, Markdown-structural, or protocol boundary requires a newline.\nskill-call:\nScope changes already belong to boundary-contract\nStop and renew or confirm before changing the committed scope\n",
        )
        self._write_file(
            root,
            "coding-convention/verification-before-completion/SKILL.md",
            "No acceptance-criteria means no completed verification-before-completion\nclaim-evidence-map\nunverified\n",
        )
        self._write_file(
            root,
            "session-intent-analyzer/references/ledger-schema.md",
            "acceptance_criteria\nverifiable completion criterion\nintent-state.json is update-plus-accumulate state\n",
        )
        self._write_file(
            root,
            "jailbreak-detector/SKILL.md",
            "Deterministic hard-block rules are narrow regression guards\nGradual multi-turn jailbreak resistance depends on session-intent summary quality\n",
        )
        self._write_file(
            root,
            "session-intent-analyzer/scripts/session_intent_ledger.py",
            "acceptance_criteria\nconsumer_snapshot\n",
        )
        self._write_file(
            root,
            ".github/workflows/skill-gate-contract.yml",
            "python scripts/check_skill_gate_contract.py\npython scripts/run_installer_compat_tests.py --group skill-gate-contract\n",
        )
        ambient_rule = "\nAmbient working directory, opened project, and available tools are not user-provided referents or inspection authority. Route classification precedes evidence planning. Do not validate or rebut that premise before explaining.\n"
        for relative_path in (
            "docs/policies/session-gate-matrix.md",
            "AGENTS.md",
            "platforms/codex/AGENTS.md",
            "platforms/claude/CLAUDE.md",
            "task-router/SKILL.md",
        ):
            fixture_path = root / relative_path
            fixture_path.write_text(fixture_path.read_text(encoding="utf-8") + ambient_rule, encoding="utf-8")

    def test_missing_task_router_reference_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            agents_path = root / "AGENTS.md"
            agents_path.write_text("docs/policies/session-gate-matrix.md\n", encoding="utf-8")

            issues = run_contract_checks(root)
            self.assertTrue(any("AGENTS.md" in issue.path for issue in issues))

    def test_missing_boundary_contract_skill_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            (root / "boundary-contract" / "SKILL.md").unlink()

            issues = run_contract_checks(root)
            self.assertTrue(any("boundary-contract/SKILL.md" in issue.path for issue in issues))

    def test_stale_tool_checkpoint_field_names_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "task-router/SKILL.md",
                "[tool-checkpoint]\nalternatives-considered\ninherited-premises\nrisk-if-wrong\n",
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("forbidden pattern", messages)

    def test_task_router_over_scope_contract_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            task_router_path = root / "task-router" / "SKILL.md"
            task_router_path.write_text(
                task_router_path.read_text(encoding="utf-8")
                + "task-router is the single entrypoint that decides the existence reason for every tool call.\n"
                + "task-router manages every new tool action.\n",
                encoding="utf-8",
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("task-router over-scope wording is forbidden", messages)
            self.assertNotIn("missing required pattern", messages)

    def test_missing_task_router_routing_surface_contract_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            task_router_path = root / "task-router" / "SKILL.md"
            task_router_path.write_text(
                task_router_path.read_text(encoding="utf-8").replace("routing-surface", "route-surface"),
                encoding="utf-8",
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("routing-surface", messages)

    def test_missing_routing_surface_owner_policy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            matrix_path = root / "docs" / "policies" / "session-gate-matrix.md"
            matrix_path.write_text(
                matrix_path.read_text(encoding="utf-8").replace(
                    "task-router owns the reusable work judgment\n",
                    "",
                ),
                encoding="utf-8",
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("task-router owns the reusable work judgment", messages)

    def test_legacy_tool_checkpoint_name_fails(self) -> None:
        legacy_name = "action" + "-gate"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            matrix_path = root / "docs" / "policies" / "session-gate-matrix.md"
            matrix_path.write_text(
                matrix_path.read_text(encoding="utf-8") + f"\n{legacy_name}\n",
                encoding="utf-8",
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("legacy tool checkpoint name is forbidden", messages)

    def test_quality_rationale_missing_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "coding-convention/using-coding-convention/SKILL.md",
                "[gate-state]\n[completion-check]\nacceptance-criteria\nclaim-evidence-map\nunverified\nfinished work, verified results, or a decision that materially changes the user's next action\nskill-call:\n",
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("quality-maintenance device confirmed through repeated user work", messages)

    def test_missing_prose_no_hard_wrap_contract_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            using_path = root / "coding-convention" / "using-coding-convention" / "SKILL.md"
            phrase = "Prose paragraphs stay on one physical source line unless a semantic, syntax, Markdown-structural, or protocol boundary requires a newline."
            using_path.write_text(using_path.read_text(encoding="utf-8").replace(phrase, ""), encoding="utf-8")

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn(phrase, messages)

    def test_coercive_skip_wording_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            using_path = root / "coding-convention" / "using-coding-convention" / "SKILL.md"
            using_path.write_text(
                using_path.read_text(encoding="utf-8")
                + "\nThis is not negotiable. No rationalization can escape it.\n",
                encoding="utf-8",
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("coercive skip wording is forbidden", messages)

    def test_ignored_local_worktree_dirs_are_not_contract_surfaces(self) -> None:
        legacy_name = "action" + "-gate"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(root, ".worktrees/stale-checkout/AGENTS.md", legacy_name)
            self._write_file(root, ".claude/worktrees/stale-checkout/AGENTS.md", legacy_name)

            issues = run_contract_checks(root)
            messages = "\n".join(f"{issue.path}: {issue.message}" for issue in issues)
            self.assertNotIn("stale-checkout", messages)
            self.assertEqual([issue for issue in issues if issue.severity == "ERROR"], [])

    def test_claude_commands_remain_contract_surfaces(self) -> None:
        legacy_name = "action" + "-gate"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(root, ".claude/commands/legacy.md", legacy_name)

            issues = run_contract_checks(root)
            messages = "\n".join(f"{issue.path}: {issue.message}" for issue in issues).replace("\\", "/")
            self.assertIn(".claude/commands/legacy.md", messages)
            self.assertIn("legacy tool checkpoint name is forbidden", messages)

    def test_pending_merge_hard_block_wording_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "_shared/pending_merge_precheck_hook.py",
                "Activate merge-companion before normal work.\n",
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("pending-merge hard-block wording is forbidden", messages)

    def test_completion_contract_rejects_surrounding_evidence_as_direct_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "coding-convention/verification-before-completion/SKILL.md",
                (
                    "tests pass means complete\n"
                    "No acceptance-criteria means no completed verification-before-completion\n"
                    "claim-evidence-map\n"
                    "unverified\n"
                ),
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("surrounding evidence", messages)

    def test_compact_runtime_tool_checkpoint_contract_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "_shared/install_hooks.py",
                (
                    "TOOL_CHECKPOINT_MARKER\n"
                    "TOOL_CHECKPOINT_INTERNAL\n"
                    "pre_tool_use\n"
                    "tool-checkpoint\n"
                    "do not run an extra shell manifest check\n"
                    "hook-enforced retry point\n"
                    "compact " + "tool-checkpoint\n"
                    "Surface a visible [tool-checkpoint] block with "
                    + "risk, and " + "recovery\n"
                ),
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("compact runtime tool-checkpoint wording", messages)

    def test_runtime_hook_graph_gateway_contract_fails_when_roles_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "docs/policies/session-gate-matrix.md",
                (
                    "skill-catalog/session-gates.json\n"
                    "task-router\n"
                    "using-coding-convention\n"
                    "boundary-contract\n"
                    "session-intent-analyzer\n"
                    "boundary-contract: required\n"
                    "English canonical narrative + English control surface\n"
                    "[gate-state]\n"
                    "[completion-check]\n"
                    "acceptance-criteria\n"
                    "claim-evidence-map\n"
                    "unverified\n"
                    "skill-call:\n"
                    "Required gate skills are not complete unless the actual `SKILL.md` file was read\n"
                    "Read the relevant `SKILL.md` before marking a required gate done\n"
                    "Runtime Hook Graph Contract\n"
                    "downstream-gates.json\n"
                    "session-intent-analyzer intake is first\n"
                    "session-intent-analyzer → task-router\n"
                    "Runtime hooks surface one full `[tool-checkpoint]` per session input lineage\n"
                    "keep checking later tool calls silently\n"
                    "new user input, current-lineage block/deny, mismatch\n"
                    "first poll in the input lineage\n"
                ),
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("skill-evolution is report-only and self-terminating", messages)
            self.assertIn("only current-lineage block decisions are carried to `downstream-gates.json`", messages)

    def test_runtime_hook_graph_requires_hook_and_manual_pending_merge_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "docs/policies/session-gate-matrix.md",
                (
                    "skill-catalog/session-gates.json\n"
                    "task-router\n"
                    "using-coding-convention\n"
                    "boundary-contract\n"
                    "session-intent-analyzer\n"
                    "boundary-contract: required\n"
                    "English canonical narrative + English control surface\n"
                    "[gate-state]\n"
                    "[completion-check]\n"
                    "acceptance-criteria\n"
                    "claim-evidence-map\n"
                    "unverified\n"
                    "skill-call:\n"
                    "Required gate skills are not complete unless the actual `SKILL.md` file was read\n"
                    "Read the relevant `SKILL.md` before marking a required gate done\n"
                    "Runtime Hook Graph Contract\n"
                    "downstream-gates.json\n"
                    "skill-evolution is report-only and self-terminating\n"
                    "only current-lineage block decisions are carried to `downstream-gates.json`\n"
                    "session-intent-analyzer intake is first\n"
                    "session-intent-analyzer → task-router\n"
                    "Runtime hooks surface one full `[tool-checkpoint]` per session input lineage\n"
                    "keep checking later tool calls silently\n"
                    "new user input, current-lineage block/deny, mismatch\n"
                    "first poll in the input lineage\n"
                ),
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("Hook pending-merge precheck is a pre-routing/session-start layer", messages)
            self.assertIn("manual fallback still completes before actionable downstream work", messages)

    def test_gate_state_order_contract_rejects_task_router_before_session_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "AGENTS.md",
                (
                    "skill-catalog/session-gates.json\n"
                    "docs/policies/session-gate-matrix.md\n"
                    "boundary-contract\n"
                    "session-intent-analyzer\n"
                    "session-intent-analyzer → task-router\n"
                    "boundary-contract: required\n"
                    "hook-verified\n"
                    "English canonical narrative + English control surface\n"
                    "[gate-state]\n"
                    "- merge-companion-precheck: clean\n"
                    "- task-router: done\n"
                    "- session-intent-analyzer: done | hook-observed | pending\n"
                    "- using-coding-convention: done | n/a\n"
                    "- boundary-contract: required | done | n/a\n"
                    "- skill-call:\n"
                    "- next-required: <skill-name|none>\n"
                    "[completion-check]\n"
                    "acceptance-criteria\n"
                    "claim-evidence-map\n"
                    "unverified\n"
                    "A required gate skill is not satisfied by a metadata-only match\n"
                    "must read the `SKILL.md`\n"
                    "The pending-merge precheck is a pre-routing and session-start layer that completes before the user-input governance graph begins\n"
                    "explicit user defer or skip\n"
                    "downstream-gates.json\n"
                ),
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("gate-state must list session-intent-analyzer before task-router", messages)

    def test_gate_state_order_contract_rejects_task_router_without_session_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "task-router/SKILL.md",
                (
                    "English canonical narrative + English control surface\n"
                    "[gate-state]\n"
                    "- task-router: done\n"
                    "- using-coding-convention: done | n/a\n"
                    "- skill-call: task-router (this turn); using-coding-convention (this turn) | n/a\n"
                    "- next-required: <skill-name|none>\n"
                    "intent-routing gate\n"
                    "work intent analysis and skill routing\n"
                    "after session-intent-analyzer intake\n"
                    "not a tool permission owner\n"
                    "not a tool-checkpoint owner\n"
                    "using-coding-convention\n"
                    "boundary-contract\n"
                    "boundary-reason\n"
                    "next-required\n"
                    "hook-verified clean\n"
                    "user-explicit defer/skip may continue\n"
                    "pending merge remains undecided when deferred\n"
                    "skill-call:\n"
                ),
            )

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)
            self.assertIn("gate-state with task-router must include session-intent-analyzer", messages)

    def test_detector_scope_contract_requires_long_horizon_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(
                root,
                "docs/policies/session-gate-matrix.md",
                (
                    "skill-catalog/session-gates.json\n"
                    "task-router\n"
                    "using-coding-convention\n"
                    "boundary-contract\n"
                    "session-intent-analyzer\n"
                    "boundary-contract: required\n"
                    "English canonical narrative + English control surface\n"
                    "[gate-state]\n"
                    "[completion-check]\n"
                    "acceptance-criteria\n"
                    "claim-evidence-map\n"
                    "unverified\n"
                    "skill-call:\n"
                    "Required gate skills are not complete unless the actual `SKILL.md` file was read\n"
                    "Read the relevant `SKILL.md` before marking a required gate done\n"
                    "Runtime Hook Graph Contract\n"
                    "Pending-merge precheck runs before the user-input governance graph begins\n"
                    "downstream-gates.json\n"
                    "skill-evolution is report-only and self-terminating\n"
                    "only current-lineage block decisions are carried to `downstream-gates.json`\n"
                    "session-intent-analyzer intake is first\n"
                    "session-intent-analyzer → task-router\n"
                    "Runtime hooks surface one full `[tool-checkpoint]` per session input lineage\n"
                    "keep checking later tool calls silently\n"
                    "new user input, current-lineage block/deny, mismatch\n"
                    "first poll in the input lineage\n"
                ),
            )
            self._write_file(
                root,
                "session-intent-analyzer/references/ledger-schema.md",
                "acceptance_criteria\nverifiable completion criterion\n",
            )
            self._write_file(root, "jailbreak-detector/SKILL.md", "")

            issues = run_contract_checks(root)
            messages = "\n".join(issue.message for issue in issues)

            self.assertIn("Deterministic hard-block rules are narrow regression guards", messages)
            self.assertIn("Gradual multi-turn jailbreak resistance depends on session-intent summary quality", messages)
            self.assertIn("intent-state.json is update-plus-accumulate state", messages)

    def test_valid_contract_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)

            issues = run_contract_checks(root)
            self.assertEqual(issues, [])

    def test_real_repo_run_contract_checks_passes(self) -> None:
        """run_contract_checks must report zero ERROR findings for the real repo.

        This safety check prevents fixture-only tests from giving false
        confidence. It passes only when the real docs/SSOT match the checker
        required phrases.
        """
        issues = run_contract_checks(REPO_ROOT)
        errors = [issue for issue in issues if issue.severity == "ERROR"]
        self.assertEqual(
            errors,
            [],
            f"real repo contract check has {len(errors)} ERROR(s): "
            + "\n".join(f"{e.path}: {e.message}" for e in errors[:10]),
        )

    def test_skill_evolution_requires_behavior_first_selection_and_closed_disposition(self) -> None:
        skill_text = (REPO_ROOT / "skill-evolution" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        sections = {
            "skill": skill_text,
            "conduct": self._extract_between(skill_text, "## Conduct Feedback", "## Procedure"),
            "procedure": self._extract_between(skill_text, "## Procedure", "## Output Format"),
            "output": self._extract_between(skill_text, "## Output Format", "## Quality Decisions"),
            "quality": self._extract_between(skill_text, "## Quality Decisions", "## Warnings"),
        }
        normalized = {name: " ".join(text.split()) for name, text in sections.items()}
        required_by_section = {
            "conduct": [
                "Every candidate first receives a behavior-first assessment of its observed behavior or failure mode and contract impact, then a closed disposition.",
                "Only a disposition that proposes follow-up work routes to necessity-gate.",
                "likely preventing gate skill",
            ],
            "procedure": [
                "Quality is triage evidence, never eligibility or approval authority.",
                "`instincts[*].decision` and quality are analyzer triage data only.",
                "`absorb`, `project-local`, `session-only`, `reject`, or `defer`",
                "Items triaged as `watch` or `reject` still receive a closed disposition.",
                "A closed disposition does not make a candidate an implementation candidate.",
                "`dispositions[*].disposition` is a report-only classification and routing recommendation.",
                "necessity-gate remains the execution authority",
                "`owner` is the accountable contract or skill identifier",
                "A `defer` or `reject` disposition authorizes no action.",
                "Analyzer `decision` does not authorize a handoff.",
            ],
        }
        forbidden_fragments = [
            ("skill", "Route each candidate through necessity-gate"),
            ("procedure", "Read `quality_summary` first"),
            ("procedure", "Treat only `quality=review` candidates as possible follow-up work"),
            ("quality", "route through necessity-gate"),
            ("procedure", "as a debugging handoff rather than"),
            ("procedure", "sole action authority"),
        ]
        self._assert_fragment_tables(normalized, required_by_section, forbidden_fragments)

        behavior_position = normalized["procedure"].find("observed behavior or failure mode")
        impact_position = normalized["procedure"].find("contract impact")
        quality_position = normalized["procedure"].find("quality triage")
        self.assertGreaterEqual(behavior_position, 0)
        self.assertGreater(impact_position, behavior_position)
        self.assertGreater(quality_position, impact_position)

        output_payload = json.loads(self._extract_between(sections["output"], "```json", "```"))
        disposition = output_payload["dispositions"][0]
        self.assertEqual(
            set(disposition),
            {"candidate_id", "disposition", "owner", "action", "rationale"},
        )
        self.assertEqual(disposition["disposition"], "defer")
        self.assertEqual(disposition["action"], "none")
        self.assertRegex(disposition["owner"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertEqual(disposition["owner"], "skill-evolution")

    def test_skill_evolution_distinguishes_pressure_testing_from_blind_installed_behavior_evaluation(self) -> None:
        skill_text = (REPO_ROOT / "skill-evolution" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(skill_text.split())

        required = [
            "Skill pressure RED/GREEN tests whether the written methodology changes behavior under disclosed pressure; it is not installed-behavior release evidence.",
            "Post-install blind behavior evaluation uses a held-out case in a fresh subject session.",
            "The subject sees only the authentic prompt and ordinary runtime context.",
            "purpose, rubric, expected answer, pass criteria, prior output, and experiment label remain evaluator-private",
            "Only an evaluator-confirmed behavioral failure emits exactly one report-only conduct-feedback candidate; it never auto-edits a skill or writes recommendation state.",
            "Harness, transport, provenance, timeout, empty-output, or malformed-evaluator failures emit no conduct-feedback candidate.",
            "Detailed blind-evaluation transport, provenance, and persistence rules are owned by the source-tree live-smoke policy and blind-behavior runtime.",
        ]
        for fragment in required:
            with self.subTest(required=fragment):
                self.assertIn(fragment, normalized)

        for duplicated_detail in (
            "Held-out and fresh-session provenance must be validated from the case bundle and canonical install manifest",
            "Persist only the case id/hash",
            "../docs/policies/live-smoke-regression.md",
            "../_shared/blind_behavior.py",
        ):
            self.assertNotIn(duplicated_detail, normalized)

        pressure_position = normalized.find("Skill pressure RED/GREEN")
        blind_position = normalized.find("Post-install blind behavior evaluation")
        self.assertGreaterEqual(pressure_position, 0)
        self.assertGreater(blind_position, pressure_position)

    def test_task_router_preserves_primary_request_axis(self) -> None:
        skill_text = (REPO_ROOT / "task-router" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        sections = {
            "skill": " ".join(skill_text.split()),
            "surface": " ".join(self._extract_between(
                skill_text, "```text\n[routing-surface]", "```"
            ).split()),
            "rules": " ".join(self._extract_between(
                skill_text, "Rules:", "### Sufficient Change Principle"
            ).split()),
        }
        required_by_section = {
            "surface": [
                "- primary-request:", "- causal-axis:", "- response-mode:", "- response-order:",
                "<cause or relationship | n/a>",
                "causal-answer-first-after-necessary-evidence",
                "imperative-result-first-after-necessary-work",
                "clarification-question-only",
            ],
            "rules": [
                "`primary-request` preserves the user's primary question or imperative instruction.",
                "For a causal question, preserve its causal axis",
                "For a non-causal imperative request, set `causal-axis: n/a` and use `response-order: imperative-result-first-after-necessary-work`.",
                "Do not invent causality.",
                "The user's terminal objective outranks investigative means.",
                "Treat investigation, provenance reconstruction, artifact preservation, and worktree inspection as means unless the user explicitly requests one as a deliverable.",
                "Do not let a means replace, narrow, or expand the terminal objective.",
            ],
            "skill": ["At pre-tool routing, do not fabricate or require an unsupported answer."],
        }
        forbidden_fragments = [
            ("surface", "- primary-question:"),
            ("skill", "Answer the primary causal axis before elaborating, narrowing, or broadening scope."),
            ("skill", "must answer immediately"),
            ("skill", "must answer before evidence"),
            ("skill", "requires an answer before evidence"),
            ("skill", "must answer before tool"),
        ]
        self._assert_fragment_tables(sections, required_by_section, forbidden_fragments)

    def test_purpose_and_evidence_selection_contract_has_owner_and_consumers(self) -> None:
        paths = {
            "boundary": REPO_ROOT / "boundary-contract" / "SKILL.md",
            "verification": (
                REPO_ROOT
                / "coding-convention"
                / "verification-before-completion"
                / "SKILL.md"
            ),
            "merge": REPO_ROOT / "merge-companion" / "SKILL.md",
        }
        sections = {
            name: " ".join(path.read_text(encoding="utf-8").split())
            for name, path in paths.items()
        }
        required_by_section = {
            "boundary": [
                "Record the terminal objective separately from implementation and investigation means.",
                "Acceptance criteria describe the requested outcome, not incidental methods, examples, artifact counts, or verification mechanics unless the user made them requirements.",
            ],
            "verification": [
                "Current accessible behavior or content is the default direct evidence for semantic claims.",
                "Hash or provenance evidence is appropriate when the criterion is artifact identity, integrity, drift, merge safety, or reproducibility.",
                "Do not use hash equality, byte identity, cache history, or repository lineage as a proxy for current semantic behavior.",
            ],
            "merge": [
                "Use hashes and provenance only to establish artifact identity, drift, and merge safety.",
                "Choose semantic behavior by current contract and behavior evidence, not by recency, authorship, or hash equality.",
            ],
        }
        forbidden_fragments = [
            ("verification", "Always hash the original"),
            ("verification", "Always reconstruct provenance"),
            ("merge", "newest file wins"),
            ("merge", "matching hash proves semantic correctness"),
        ]
        self._assert_fragment_tables(sections, required_by_section, forbidden_fragments)

    def test_korean_routing_docs_preserve_primary_request_and_terminal_objective(self) -> None:
        required = [
            "## Routing Surface Contract",
            "primary-request",
            "causal-axis",
            "response-order",
            "The user's terminal objective outranks investigative means.",
            "Do not let a means replace, narrow, or expand the terminal objective.",
        ]
        for rel_path in ("docs/ko/policies/session-gate-matrix.md",):
            text = " ".join(
                (REPO_ROOT / rel_path).read_text(encoding="utf-8").split()
            )
            for fragment in required:
                with self.subTest(path=rel_path, fragment=fragment):
                    self.assertIn(fragment, text)

    def test_real_repo_contract_surfaces_do_not_reintroduce_tool_shape_policy(self) -> None:
        old_phrase = "deterministic " + "inspection"
        old_phrase_title = "Deterministic " + "inspection"
        scanned_paths = [
            "docs/policies/session-gate-matrix.md",
            "task-router/SKILL.md",
            "install.sh",
            "install.ps1",
            "scripts/tests/test_global_rule_blocks.py",
            "_shared/test_install_hooks.py",
        ]

        hits: list[str] = []
        for relative_path in scanned_paths:
            text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            if old_phrase in text or old_phrase_title in text:
                hits.append(relative_path)

        self.assertEqual(hits, [], f"old tool-shape policy phrase remains in: {hits}")


if __name__ == "__main__":
    unittest.main()
