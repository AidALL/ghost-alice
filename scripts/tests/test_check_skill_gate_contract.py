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
        "task-router",
        "tool-checkpoint",
    ],
    "intent_consumers": {
        "session-intent-analyzer": ["skill-evolution", "task-router"],
        "skill-evolution": ["report-only-terminal-branch"],
    },
    "conditional_gates": [
        {
            "id": "session-intent-analyzer",
            "trigger": "every user input",
            "position": "after-merge-companion-precheck-before-task-router-and-report-only-skill-evolution",
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
    def test_current_boundary_contract_distinguishes_local_plan_from_human_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            body = (REPO_ROOT / "boundary-contract" / "SKILL.md").read_text(encoding="utf-8")
            self._write_file(root, "boundary-contract/SKILL.md", body)
            issues = run_contract_checks(root)
            boundary_issues = [issue for issue in issues if "boundary-contract/SKILL.md" in issue.path]
            self.assertEqual(boundary_issues, [])

    def test_boundary_contract_rejects_missing_authority_and_objective_protection(self) -> None:
        for obligation in ("human-owned", "agent-declared", "objective"):
            with self.subTest(obligation=obligation), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                self._write_valid_fixture(root)
                path = root / "boundary-contract/SKILL.md"
                path.write_text(path.read_text().replace(obligation, "REMOVED"))
                self.assertTrue(any(obligation in issue.message for issue in run_contract_checks(root)))

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
        # Meaning is enforced by terminal_routes machine values, not duplicate prose.
        for mode in ("clarification-only", "direct-response"):
            self.assertEqual(CONTRACT["terminal_routes"][mode]["downstream_work"], "none")
            self.assertEqual(CONTRACT["terminal_routes"][mode]["strict_logging"], "active")
            for path in ("task-router/SKILL.md", "AGENTS.md", "platforms/codex/AGENTS.md", "platforms/claude/CLAUDE.md"):
                self.assertIn(mode, REQUIRED_PATTERNS[path])

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
        self._write_file(root, "skill-catalog/session-gates.json", json.dumps(CONTRACT))
        # Static fixtures provide wiring tokens; real-repository tests check owners.
        for relative, identifiers in REQUIRED_PATTERNS.items():
            self._write_file(root, relative, "\n".join(identifiers) + "\n")
        self._write_file(root, "_shared/reminder_texts.json", json.dumps({
            "tool-checkpoint": "tool-checkpoint: PreToolUse follows common governance rule 9",
        }))
        additions = {
            "AGENTS.md": "### 9. io-trace Transparency Rule\n`intent` `why` `hook-stage: PreToolUse` `contract-ref` `contract-check`\n",
            "task-router/SKILL.md": "## Register the Core projection\nresponse-route.v1 record_response_route\n",
            "coding-convention/using-coding-convention/SKILL.md": "[Instruction Body Reuse](../verification-before-completion/references/verify-or-reuse.md#instruction-body-reuse)\n",
            "coding-convention/verification-before-completion/SKILL.md": "[Register the Core projection](../../task-router/SKILL.md#register-the-core-projection)\n[Apply and deliver](references/autopilot-publication.md#apply-and-deliver)\n",
        }
        for relative, body in additions.items():
            path = root / relative
            path.write_text(path.read_text(encoding="utf-8") + "\n" + body, encoding="utf-8")
        self._write_file(root, "coding-convention/verification-before-completion/references/verify-or-reuse.md",
                         "## Instruction Body Reuse\nskills-reused skills-loaded skill-call\n")
        self._write_file(root, "coding-convention/verification-before-completion/references/autopilot-publication.md",
                         "## Apply and deliver\ndelivery-route repair-target preserve_answer_and_patch_metadata assembled_complete_payload corrected_complete_answer resolve_host_delivery\n")
        claude = root / "platforms/claude/CLAUDE.md"
        claude.write_text(
            "## First-Entry Contract\n\n"
            "1. Reuse the pending-merge precheck.\n"
            "2. Consume the exact session-intent observation.\n"
            "3. Apply task-router to select work.\n"
            "4. Execute selected workflows.\n\n"
            "## Owner references\n" + claude.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def test_current_consumers_use_canonical_owner_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            for relative in ("_shared/reminder_texts.json",
                             "coding-convention/using-coding-convention/SKILL.md",
                             "coding-convention/verification-before-completion/SKILL.md"):
                self._write_file(root, relative, (REPO_ROOT / relative).read_text(encoding="utf-8"))
            self.assertEqual(run_contract_checks(root), [])

    def test_owner_reference_cannot_be_replaced_with_a_broken_anchor(self) -> None:
        cases = (
            ("coding-convention/using-coding-convention/SKILL.md", "#instruction-body-reuse"),
            ("coding-convention/verification-before-completion/SKILL.md", "#register-the-core-projection"),
            ("coding-convention/verification-before-completion/SKILL.md", "#apply-and-deliver"),
        )
        for relative, anchor in cases:
            with self.subTest(anchor=anchor), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                self._write_valid_fixture(root)
                path = root / relative
                path.write_text(path.read_text().replace(anchor, "#missing-owner-section"))
                self.assertTrue(any(issue.path == relative and "owner reference" in issue.message
                                    for issue in run_contract_checks(root)))

    def test_checkpoint_owner_reference_is_required_in_checkpoint_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(root, "_shared/reminder_texts.json", json.dumps({
                "tool-checkpoint": "tool-checkpoint PreToolUse intent why contract-ref contract-check",
                "unrelated-message": "common governance rule 9",
            }))
            self.assertTrue(any(issue.path == "_shared/reminder_texts.json" and "owner reference" in issue.message
                                for issue in run_contract_checks(root)))

    def test_missing_canonical_owner_file_or_section_fails(self) -> None:
        cases = (
            ("AGENTS.md", "### 9. io-trace Transparency Rule"),
            ("task-router/SKILL.md", "## Register the Core projection"),
            ("coding-convention/verification-before-completion/references/verify-or-reuse.md", "## Instruction Body Reuse"),
            ("coding-convention/verification-before-completion/references/autopilot-publication.md", "## Apply and deliver"),
        )
        for relative, heading in cases:
            for mutation in ("file", "section"):
                with self.subTest(owner=relative, mutation=mutation), tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    self._write_valid_fixture(root)
                    path = root / relative
                    if mutation == "file":
                        path.unlink()
                    else:
                        path.write_text(path.read_text().replace(heading, "## Unrelated section"))
                    self.assertTrue(any(issue.path == relative and "owner" in issue.message
                                        for issue in run_contract_checks(root)))

    def test_owner_contract_fields_must_remain_in_the_owned_section(self) -> None:
        cases = (
            ("AGENTS.md", ("why", "contract-ref", "contract-check")),
            ("task-router/SKILL.md", ("record_response_route", "response-route.v1")),
            ("coding-convention/verification-before-completion/references/verify-or-reuse.md", ("skills-reused", "skills-loaded", "skill-call")),
            ("coding-convention/verification-before-completion/references/autopilot-publication.md", ("delivery-route", "repair-target", "preserve_answer_and_patch_metadata", "assembled_complete_payload", "corrected_complete_answer", "resolve_host_delivery")),
        )
        for relative, fields in cases:
            for field in fields:
                with self.subTest(owner=relative, field=field), tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    self._write_valid_fixture(root)
                    path = root / relative
                    # A stray mention outside the owning section cannot satisfy its contract.
                    path.write_text(field + "\n" + path.read_text().replace(field, "REMOVED"))
                    self.assertTrue(any(issue.path == relative and "owner" in issue.message and field in issue.message
                                        for issue in run_contract_checks(root)))

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
            path = root / "task-router/SKILL.md"
            path.write_text(path.read_text().replace("evidence-selection-and-stop-gate", "REMOVED"))
            self.assertTrue(any("evidence-selection-and-stop-gate" in issue.message for issue in run_contract_checks(root)))

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
            path = root / "coding-convention/using-coding-convention/SKILL.md"
            path.write_text(path.read_text().replace("Instruction Body Reuse", "REMOVED"))
            self.assertTrue(any("Instruction Body Reuse" in issue.message for issue in run_contract_checks(root)))

    def test_missing_prose_no_hard_wrap_contract_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            path = root / "coding-convention/using-coding-convention/SKILL.md"
            path.write_text(path.read_text().replace("one physical source line", "REMOVED"))
            self.assertTrue(any("one physical source line" in issue.message for issue in run_contract_checks(root)))

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
            path = root / "docs/policies/session-gate-matrix.md"
            path.write_text(path.read_text().replace("skill-evolution", "REMOVED"))
            self.assertTrue(any("skill-evolution" in issue.message for issue in run_contract_checks(root)))

    def test_runtime_hook_graph_requires_hook_and_manual_pending_merge_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            path = root / "skill-catalog/session-gates.json"
            contract = json.loads(path.read_text())
            contract["user_input_graph"].remove("merge-companion-precheck")
            path.write_text(json.dumps(contract))
            self.assertTrue(any("merge-companion-precheck" in issue.message for issue in run_contract_checks(root)))

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
                    "use a fresh or valid retained instruction body\n"
                    "The pending-merge precheck is a pre-routing and session-start layer that completes before the user-input governance graph begins\n"
                    "explicit user defer or skip\n"
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

    def test_retired_classifier_and_nonterminal_evolution_are_rejected(self):
        for mutation in ('detector', 'evolution'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                self._write_valid_fixture(root)
                path = root / "skill-catalog/session-gates.json"
                contract = json.loads(path.read_text())
                if mutation == 'detector':
                    contract['intent_consumers']['session-intent-analyzer'].append('jailbreak-detector')
                else:
                    contract['intent_consumers']['skill-evolution'].append('task-router')
                path.write_text(json.dumps(contract))
                messages = '\n'.join(issue.message for issue in run_contract_checks(root))
                self.assertIn('retired standalone classifier' if mutation == 'detector' else 'report-only-terminal-branch', messages)

    def test_direct_intake_contract_rejects_a_renamed_intermediate_stage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            path = root / "skill-catalog/session-gates.json"
            contract = json.loads(path.read_text())
            contract['user_input_graph'].insert(2, 'input-policy-review')
            contract['intent_consumers']['session-intent-analyzer'].append('input-policy-review')
            path.write_text(json.dumps(contract))
            messages = '\n'.join(issue.message for issue in run_contract_checks(root))
            self.assertIn('direct intake ownership', messages)

    def test_direct_intake_entrypoint_fields_reject_intermediates_duplicates_and_types(self):
        cases = (
            ('always_first', ['policy-review', 'session-intent-analyzer']),
            ('always_first', ['session-intent-analyzer', 'policy-review']),
            ('always_first', ['session-intent-analyzer', 'session-intent-analyzer']),
            ('always_first', 'session-intent-analyzer'),
            ('always_first', None),
            ('agent_first_after_intake', ['review-input', 'task-router']),
            ('agent_first_after_intake', ['task-router', 'review-input']),
            ('agent_first_after_intake', ['task-router', 'task-router']),
            ('agent_first_after_intake', 'task-router'),
            ('agent_first_after_intake', {'task-router': True}),
            ('agent_first_after_intake', None),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                self._write_valid_fixture(root)
                path = root / 'skill-catalog/session-gates.json'
                contract = json.loads(path.read_text())
                contract[field] = value
                path.write_text(json.dumps(contract))
                messages = '\n'.join(issue.message for issue in run_contract_checks(root))
                self.assertIn(f'{field} direct intake ownership', messages)

    def test_direct_intake_graph_fields_reject_wrong_shapes_and_order(self):
        graph = CONTRACT['user_input_graph']
        cases = (
            ('user_input_graph', graph[:3] + ['task-router', 'task-router', 'tool-checkpoint']),
            ('user_input_graph', [graph[0], 'task-router', graph[2], 'session-intent-analyzer', graph[4]]),
            ('user_input_graph', None),
            ('user_input_graph', 'task-router'),
            ('user_input_graph', graph[:3] + [['task-router'], graph[4]]),
            ('intent_consumers', None),
            ('intent_consumers', {**CONTRACT['intent_consumers'], 'review-input': ['task-router']}),
            ('intent_consumers', {'session-intent-analyzer': ['task-router', 'task-router'], 'skill-evolution': ['report-only-terminal-branch']}),
            ('intent_consumers', {'session-intent-analyzer': {'task-router': True, 'skill-evolution': True}, 'skill-evolution': ['report-only-terminal-branch']}),
            ('intent_consumers', {'session-intent-analyzer': None, 'skill-evolution': ['report-only-terminal-branch']}),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                self._write_valid_fixture(root)
                path = root / 'skill-catalog/session-gates.json'
                contract = json.loads(path.read_text())
                contract[field] = value
                path.write_text(json.dumps(contract))
                errors = [issue for issue in run_contract_checks(root) if issue.severity == 'ERROR']
                self.assertTrue(errors, (field, value))

    def test_claude_first_turn_rejects_block_condition_even_with_correct_later_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            path = root / "platforms/claude/CLAUDE.md"
            path.write_text(path.read_text().replace("3. Apply task-router to select work.", "3. If no block exists, apply task-router."))
            self.assertTrue(any("Claude first-turn must route directly after exact intake" in issue.message for issue in run_contract_checks(root)))

    def test_real_claude_first_turn_routes_directly_after_exact_intake(self) -> None:
        from check_skill_gate_contract import _check_claude_first_turn_contract
        self.assertEqual(_check_claude_first_turn_contract(REPO_ROOT), [])

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
        text = (REPO_ROOT / "skill-evolution/SKILL.md").read_text()
        procedure = self._extract_between(text, "## Analyze and dispose", "## Conduct feedback")
        self.assertLess(procedure.index("observed behavior"), procedure.index("quality_summary"))
        self.assertIn("report-only branch does not dispatch implementation", procedure)
        self.assertIn("defer/reject disposition grants no action", procedure)
        self.assertIn("Watch/reject triage candidates still receive this disposition", procedure)
        payload = json.loads(self._extract_between(text, "```json", "```"))
        disposition = payload["dispositions"][0]
        self.assertEqual(set(disposition), {"candidate_id", "disposition", "owner", "action", "rationale"})
        self.assertEqual((disposition["disposition"], disposition["action"], disposition["owner"]), ("defer", "none", "skill-evolution"))

    def test_skill_evolution_distinguishes_pressure_testing_from_blind_installed_behavior_evaluation(self) -> None:
        text = (REPO_ROOT / "skill-evolution/SKILL.md").read_text()
        section = self._extract_between(text, "## Blind installed-behavior evaluation", "## Warnings")
        owner_path = "docs/policies/live-smoke-regression.md"
        for fragment in (owner_path, "evaluator-confirmed behavioral failure",
                         "report-only conduct-feedback candidate", "harness or transport failure"):
            self.assertIn(fragment, section)
        self.assertIn("must not auto-evolve, auto-promote, auto-create skills", text)
        owner = (REPO_ROOT / owner_path).read_text()
        layers = self._extract_between(owner, "| Layer | Purpose |", "## Smoke Record")
        for fragment in ("Skill pressure RED/GREEN", "never installed-behavior release evidence",
                         "fresh session", "evaluator purpose, rubric, expected answer, pass criteria, prior output, and experiment label stay private"):
            self.assertIn(fragment, layers)
        record = self._extract_between(owner, "## Blind Behavior Record", "## Failure Triage")
        for fragment in ("malformed evaluator output", "fail closed and emit no conduct-feedback candidate",
                         "Only an evaluator-confirmed behavioral failure emits exactly one canonical report-only candidate",
                         "_shared/blind_behavior.py", "_shared/fresh_agent_session.py"):
            self.assertIn(fragment, record)

    def test_task_router_preserves_primary_request_axis(self) -> None:
        text = (REPO_ROOT / "task-router/SKILL.md").read_text()
        for fragment in ("- primary-request:", "- causal-axis:", "- response-order:", "Preserve a causal axis only", "Route classification precedes evidence planning", "imperative requests", "result after necessary work"):
            self.assertIn(fragment, text)
        self.assertIn("authority-and-renewal", text)

    def test_purpose_and_evidence_selection_contract_has_owner_and_consumers(self) -> None:
        boundary = (REPO_ROOT / "boundary-contract/SKILL.md").read_text()
        proof = (REPO_ROOT / "coding-convention/verification-before-completion/SKILL.md").read_text()
        merge = (REPO_ROOT / "merge-companion/SKILL.md").read_text()
        self.assertIn("terminal objective separately", boundary)
        self.assertIn("Every criterion describes a requested outcome", boundary)
        self.assertIn("## Evidence selection and stop gate", proof)
        self.assertIn("semantic", proof)
        self.assertIn("identity", proof)
        self.assertIn("Hash/provenance evidence establishes identity, drift and merge safety", merge)
        self.assertIn("current contract and behavior evidence determine which semantics to retain", merge)

    def test_korean_routing_docs_preserve_primary_request_and_terminal_objective(self) -> None:
        for relative in ("docs/ko/policies/session-gate-matrix.md",):
            text = (REPO_ROOT / relative).read_text()
            # Korean aid points to the English canonical owner instead of duplicating rules.
            self.assertIn("task-router/SKILL.md", text)
            self.assertIn("boundary-contract/SKILL.md", text)
            self.assertIn("verification-before-completion", text)

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
