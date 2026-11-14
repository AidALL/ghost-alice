import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_skill_gate_contract import REQUIRED_PATTERNS, run_contract_checks  # noqa: E402


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

    def test_missing_required_patterns_identify_the_file_and_pattern(self) -> None:
        cases = (
            ("boundary-contract/SKILL.md", "a boundary the human user set"),
            ("boundary-contract/SKILL.md", "self-declared"),
            ("boundary-contract/SKILL.md", "objective"),
            ("AGENTS.md", "installed common contract"),
            ("task-router/SKILL.md", "response-route"),
            ("task-router/SKILL.md", "references/routing-details.md"),
            ("coding-convention/using-coding-convention/SKILL.md", "Instruction Body Reuse"),
            ("task-router/SKILL.md", "../session-intent-analyzer/references/ledger-schema.md#core-response-projection"),
        )
        for relative, pattern in cases:
            with self.subTest(path=relative, pattern=pattern), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                self._write_valid_fixture(root)
                path = root / relative
                path.write_text(path.read_text(encoding="utf-8").replace(pattern, "REMOVED"), encoding="utf-8")
                issues = {(issue.path, issue.message) for issue in run_contract_checks(root)}
                self.assertIn((relative, f"missing required pattern: {pattern}"), issues)

    def _extract_between(self, text: str, start_marker: str, end_marker: str) -> str:
        start = text.find(start_marker)
        self.assertGreaterEqual(start, 0, f"missing start marker: {start_marker}")
        content_start = start + len(start_marker)
        end = text.find(end_marker, content_start)
        self.assertGreaterEqual(end, 0, f"missing end marker after {start_marker}: {end_marker}")
        return text[content_start:end]

    def _write_file(self, root: Path, rel_path: str, content: str) -> None:
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_valid_fixture(self, root: Path) -> None:
        # Static fixtures provide wiring tokens; real-repository tests check owners.
        for relative, identifiers in REQUIRED_PATTERNS.items():
            self._write_file(root, relative, "\n".join(identifiers) + "\n")
        self._write_file(root, "_shared/reminder_texts.json", json.dumps({
            "tool-checkpoint": "tool-checkpoint: follows the common contract's io-trace Transparency Rule",
        }))
        additions = {
            "platforms/common-governance.md": "## Session Intent\n`user-said: ` `interpreted-as: `\n## Work Selection\n`task-router` decides between a direct answer and work.\n## io-trace Transparency Rule\n`intent` `why` `stage: before-tool` `contract-ref` `contract-check`\n",
            "session-intent-analyzer/references/ledger-schema.md": "## Core Response Projection\nresponse-route.v2 record_response_route\n",
            "coding-convention/using-coding-convention/SKILL.md": "[Instruction Body Reuse](../verification-before-completion/references/verify-or-reuse.md#instruction-body-reuse)\n",
            "coding-convention/verification-before-completion/SKILL.md": "session-intent-analyzer's `references/ledger-schema.md`, section Core Response Projection\n[Deliver](references/response-review.md#deliver)\n",
        }
        for relative, body in additions.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text((path.read_text(encoding="utf-8") if path.exists() else "") + "\n" + body, encoding="utf-8")
        self._write_file(root, "coding-convention/verification-before-completion/references/verify-or-reuse.md",
                         "## Instruction Body Reuse\nsession_check_cache.py --body-retained\n")
        self._write_file(root, "coding-convention/verification-before-completion/references/response-review.md",
                         "## Deliver\nrecording-only error Do not invoke Stop\n")
        claude = root / "platforms/claude/CLAUDE.md"
        claude.write_text(
            "{{COMMON_GOVERNANCE}}\n\n"
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
            ("coding-convention/verification-before-completion/SKILL.md", "Core Response Projection"),
            ("coding-convention/verification-before-completion/SKILL.md", "#deliver"),
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
                "tool-checkpoint": "tool-checkpoint intent why contract-ref contract-check",
                "unrelated-message": "io-trace Transparency Rule",
            }))
            self.assertTrue(any(issue.path == "_shared/reminder_texts.json" and "owner reference" in issue.message
                                for issue in run_contract_checks(root)))

    def test_missing_canonical_owner_file_or_section_fails(self) -> None:
        cases = (
            ("platforms/common-governance.md", "## io-trace Transparency Rule"),
            ("session-intent-analyzer/references/ledger-schema.md", "## Core Response Projection"),
            ("coding-convention/verification-before-completion/references/verify-or-reuse.md", "## Instruction Body Reuse"),
            ("coding-convention/verification-before-completion/references/response-review.md", "## Deliver"),
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

    def test_real_owner_keeps_normative_fields_inside_fenced_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            self._write_file(root, 'session-intent-analyzer/references/ledger-schema.md',
                             'acceptance_criteria\n## Core Response Projection ##\n~~~example\n## Not a section\n'
                             'record_response_route response-route.v2\n~~~\n')
            self.assertEqual(run_contract_checks(root), [])

    def test_owner_contract_fields_must_remain_in_the_owned_section(self) -> None:
        cases = (
            ("platforms/common-governance.md", ("why", "contract-ref", "contract-check")),
            ("session-intent-analyzer/references/ledger-schema.md", ("record_response_route", "response-route.v2")),
            ("coding-convention/verification-before-completion/references/verify-or-reuse.md", ("session_check_cache.py", "--body-retained")),
            ("coding-convention/verification-before-completion/references/response-review.md", ("recording-only error", "Do not invoke Stop")),
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

    def test_excluded_subtrees_are_not_descended_during_contract_scan(self) -> None:
        legacy_name = "action" + "-gate"
        for relative in (".tmp", ".worktrees", ".claude/worktrees", "docs/node_modules"):
            with self.subTest(excluded=relative), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                self._write_valid_fixture(root)
                self._write_file(root, ".claude/commands/retained.md", legacy_name)
                excluded = root / relative
                self._write_file(root, relative + "/running-test/AGENTS.md", legacy_name)
                scandir = os.scandir

                def cleanup_before_descent(path):
                    if not isinstance(path, int) and Path(path) == excluded:
                        shutil.rmtree(excluded)
                    return scandir(path)

                with mock.patch.object(os, "scandir", side_effect=cleanup_before_descent):
                    issues = run_contract_checks(root)
                legacy_issues = [issue for issue in issues if "legacy tool checkpoint name" in issue.message]
                self.assertEqual([issue.path.replace("\\", "/") for issue in legacy_issues],
                                 [".claude/commands/retained.md"])
                self.assertTrue(excluded.is_dir(), "Excluded directories must be pruned before descent")

    def test_unavailable_retained_subtree_cannot_become_a_clean_contract_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            retained = root / ".claude/commands"
            self._write_file(root, ".claude/commands/retained.md", "action" + "-gate")
            scandir = os.scandir

            def cleanup_before_descent(path):
                if not isinstance(path, int) and Path(path) == retained:
                    shutil.rmtree(retained)
                return scandir(path)

            with mock.patch.object(os, "scandir", side_effect=cleanup_before_descent):
                with self.assertRaises(FileNotFoundError):
                    run_contract_checks(root)

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

    def test_instruction_reuse_names_no_retired_printed_fields(self) -> None:
        # The printed skill-call, skills-loaded and skills-reused fields are retired.
        text = (
            REPO_ROOT / "coding-convention/verification-before-completion/references/verify-or-reuse.md"
        ).read_text(encoding="utf-8")
        section = text.split("## Instruction Body Reuse", 1)[1].split("\n## ", 1)[0]
        for field in ("skill-call", "skills-loaded", "skills-reused"):
            self.assertNotIn(field, section)

    def test_claude_bootstrap_rejects_missing_common_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            path = root / "platforms/claude/CLAUDE.md"
            path.write_text(path.read_text().replace("{{COMMON_GOVERNANCE}}", "Apply task-router after intake."))
            self.assertTrue(any("must reference one common governance" in issue.message for issue in run_contract_checks(root)))

    def test_claude_bootstrap_rejects_missing_entry_sections(self) -> None:
        from check_skill_gate_contract import _check_claude_first_turn_contract
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            (root / "platforms/common-governance.md").write_text("## Operating Model\nSelect relevant work.\n")
            self.assertTrue(_check_claude_first_turn_contract(root))

    def test_removed_contract_wording_cannot_return(self) -> None:
        from check_skill_gate_contract import REMOVED_CONTRACT_TOKENS
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_fixture(root)
            path = root / "platforms/common-governance.md"
            original = path.read_text(encoding="utf-8")
            for token in REMOVED_CONTRACT_TOKENS:
                with self.subTest(token=token):
                    path.write_text(original + "\n" + token + "\n", encoding="utf-8")
                    self.assertTrue(any("removed contract wording" in issue.message for issue in run_contract_checks(root)))
            path.write_text(original, encoding="utf-8")
            self.assertFalse(any("removed contract wording" in issue.message for issue in run_contract_checks(root)))

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
        self.assertIn("report-only workflow does not start implementation", procedure)
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

    def test_route_surface_carries_only_controls_and_reason(self) -> None:
        router = (REPO_ROOT / "task-router/SKILL.md").read_text()
        schema = (REPO_ROOT / "session-intent-analyzer/references/ledger-schema.md").read_text()
        self.assertIn('Do not replace a repair that the human user requested with a report', router)
        for field in ('response_mode', 'boundary_contract', '"reason"'):
            self.assertIn(field, schema)
        for field in ('primary_request', 'causal_axis', 'response_order', 'change_depth', 'focus_layer',
                      'verification_complexity', 'intent_relation', 'forced_visibility', 'verification depth'):
            self.assertNotIn(field, schema)
            self.assertNotIn(field, router)

    def test_purpose_and_evidence_selection_contract_has_owner_and_consumers(self) -> None:
        boundary = (REPO_ROOT / "boundary-contract/SKILL.md").read_text()
        proof = (REPO_ROOT / "coding-convention/verification-before-completion/SKILL.md").read_text()
        merge = (REPO_ROOT / "merge-companion/SKILL.md").read_text()
        self.assertIn("terminal objective separately", boundary)
        self.assertIn("Every acceptance criterion describes a requested outcome", boundary)
        self.assertIn("## Evidence selection and stop gate", proof)
        self.assertIn("semantic", proof)
        self.assertIn("identity", proof)
        self.assertIn("Hash and provenance evidence establishes identity, drift and merge safety", merge)
        self.assertIn("evidence from the current contract and behavior supports the response model's proposal of which meaning to keep", merge)

    def test_korean_session_gate_matrix_points_to_the_english_owners(self) -> None:
        for relative in ("docs/ko/policies/session-gate-matrix.md",):
            text = (REPO_ROOT / relative).read_text()
            # Korean aid points to the English canonical owner instead of duplicating rules.
            self.assertIn("task-router/SKILL.md", text)
            self.assertIn("boundary-contract/SKILL.md", text)
            self.assertIn("verification-before-completion", text)

if __name__ == "__main__":
    unittest.main()
