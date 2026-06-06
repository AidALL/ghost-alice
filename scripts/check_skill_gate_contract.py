#!/usr/bin/env python3
"""
check_skill_gate_contract.py. Static session gate contract validator.

Checks whether the project-wide gate contract is reflected consistently in the
SSOT, installed surfaces, and runtime skill documents.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ContractIssue:
    severity: str
    path: str
    message: str


REQUIRED_CONTRACT_KEYS = {
    "version",
    "always_first",
    "agent_first_after_intake",
    "user_input_graph",
    "intent_consumers",
    "conditional_gates",
    "domain_routes",
    "before_completion",
    "before_commit_push",
    "on_task_definition",
}

REQUIRED_PATTERNS = {
    "docs/policies/session-gate-matrix.md": [
        "skill-catalog/session-gates.json",
        "task-router",
        "using-coding-convention",
        "boundary-contract",
        "session-intent-analyzer",
        "session-intent-analyzer intake is first",
        "session-intent-analyzer fans out to `skill-evolution` (report-only terminal branch) and `jailbreak-detector`",
        "`task-router` is a consumer of `session-intent-analyzer` and `jailbreak-detector/downstream-gates.json` context",
        "task-router is released after session-intent preflight when no current-lineage block gate exists",
        "absent `downstream-gates.json` is silent allow",
        "atomic meaning decomposition",
        "boundary-contract: required",
        "English canonical narrative + English control surface",
        "[gate-state]",
        "[completion-check]",
        "acceptance-criteria",
        "claim-evidence-map",
        "unverified",
        "skill-call:",
        "Required gate skills are not complete unless the actual `SKILL.md` file was read",
        "Read the relevant `SKILL.md` before marking a required gate done",
        "Runtime Hook Graph Contract",
        "Pending-merge precheck runs before the user-input governance graph begins",
        "intent-state.json is update-plus-accumulate state",
        "downstream-gates.json",
        "skill-evolution is report-only and self-terminating",
        "does not feed task-router",
        "only current-lineage block decisions are carried to `downstream-gates.json`",
        "user-explicit defer/skip may continue",
        "Deterministic hard-block rules are narrow regression guards",
        "Gradual multi-turn jailbreak resistance depends on session-intent summary quality",
        "PreToolUse/BeforeTool checkpoint",
        "hook-stage: PreToolUse",
        "meaning: tool-call retry checkpoint, not user-input intake",
        "session-intent-analyzer intake is first",
        "same process/session/tool-call id",
        "New command, input, timeout, interruption, or ref changes",
        "Simple polling of the same ref is not a duty to repeat output forever",
        "Dynamic Focus Contract",
        "mismatch location",
        "verification burden",
        "micro, meso, macro, and meta",
        "scope reopen point",
        "Routing Surface Contract",
        "routing-surface",
        "task-router owns the reusable work judgment",
        "session-intent-analyzer records semantic facts and accumulated decisions",
        "governance surface policy consumes routing-surface",
        "token reduction is a secondary consequence, not a success metric",
    ],
    "AGENTS.md": [
        "skill-catalog/session-gates.json",
        "docs/policies/session-gate-matrix.md",
        "boundary-contract",
        "session-intent-analyzer",
        "session-intent-analyzer fans out to skill-evolution(report-only terminal branch) and jailbreak-detector",
        "`task-router` is a consumer of `session-intent-analyzer` and `jailbreak-detector/downstream-gates.json` context",
        "task-router reminder hook withholds task-router until session-intent preflight exists and jailbreak-detector has had the first chance to record a current-lineage block",
        "absent `downstream-gates.json` is silent allow",
        "atomic meaning decomposition",
        "boundary-contract: required",
        "hook-verified",
        "English canonical narrative + English control surface",
        "[gate-state]",
        "[completion-check]",
        "acceptance-criteria",
        "claim-evidence-map",
        "unverified",
        "skill-call:",
        "A required gate skill is not satisfied by a metadata-only match",
        "must read the `SKILL.md`",
        "The pending-merge precheck is a pre-routing and session-start layer that completes before the user-input governance graph begins",
        "explicit user defer or skip",
        "downstream-gates.json",
        "current-lineage block gate",
        "silent allow",
        "tool-call identity",
        "payload content",
        "outside the decision body",
        "hook-stage: PreToolUse",
        "meaning: tool-call retry checkpoint, not user-input intake",
        "The focus scope is not fixed and does not expand in only one direction",
        "the mismatch location and the verification burden",
        "micro, meso, macro, and meta",
        "task-router emits a reusable routing-surface",
    ],
    "platforms/codex/AGENTS.md": [
        "Session Gate Contract",
        "boundary-contract",
        "session-intent-analyzer",
        "`session-intent-analyzer` branches to the `skill-evolution` report-only branch and to `jailbreak-detector`",
        "task-router is a consumer of the session-intent and jailbreak gate context",
        "The task-router reminder hook releases task-router when a session-intent preflight exists and there is no current-lineage block gate",
        "The absence of `downstream-gates.json` is treated as a silent allow meaning there is no current-lineage block",
        "atomic meaning decomposition",
        "boundary-contract: required",
        "hook-verified",
        "English canonical narrative + English control surface",
        "[gate-state]",
        "[completion-check]",
        "acceptance-criteria",
        "claim-evidence-map",
        "unverified",
        "skill-call:",
        "After the session-intent-analyzer intake and the jailbreak-detector downstream gate, read `~/.agents/skills/task-router/SKILL.md`",
        "A required gate skill is not satisfied by a metadata-only match",
        "Always read the skill's `SKILL.md` before marking a required gate as complete",
        "The pending-merge precheck is a pre-routing and session-start layer that completes before the user-input governance graph begins",
        "explicit user defer or skip",
        "downstream-gates.json",
        "hook-stage: PreToolUse",
        "meaning: tool-call retry checkpoint, not user-input intake",
        "the same process, session, or tool-call id",
        "A new command, input, timeout, interruption, or ref switch",
        "current-lineage block gate",
        "silent allow",
        "tool-call identity",
        "payload content",
        "outside the decision body",
        "The focus scope is not fixed and does not expand in only one direction",
        "the mismatch location and the verification burden",
        "micro, meso, macro, and meta",
        "task-router emits a reusable routing-surface",
    ],
    "install.ps1": [
        "session-gates.json",
        "boundary-contract: required",
        "English canonical narrative + English control surface",
        "pending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace",
        "pending-merge precheck",
        "explicit user defer/skip",
        "downstream-gates.json",
    ],
    "install.sh": [
        "session-gates.json",
        "boundary-contract: required",
        "English canonical narrative + English control surface",
        "pending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace",
        "pending-merge precheck",
        "explicit user defer/skip",
        "downstream-gates.json",
    ],
    "_shared/global_rule_blocks.py": [
        "boundary-contract: required",
        "English canonical narrative + English control surface",
        "[tool-checkpoint]",
        "pending-merge precheck",
        "explicit user defer/skip",
        "downstream-gates.json",
        "same process/session/tool-call id",
        "Switching to a new command, input, timeout, interruption, or ref",
        "current-lineage block gate",
        "silent allow",
        "tool-call identity",
        "payload content",
        "outside the decision body",
    ],
    "README.md": [
        "skill-catalog/session-gates.json",
        "python scripts/check_skill_gate_contract.py",
    ],
    "scripts/validate_skills.py": [
        "run_contract_checks",
        "gate-contract",
    ],
    "task-router/SKILL.md": [
        "English canonical narrative + English control surface",
        "[gate-state]",
        "request-routing gate",
        "request decomposition, work placement, skill routing",
        "consumer of session-intent-analyzer and",
        "routing decision",
        "raw user intent inference",
        "after session-intent-analyzer intake and",
        "silent allow",
        "current-lineage block gate",
        "atomic meaning",
        "not a tool permission owner",
        "not a tool-checkpoint owner",
        "hook-stage: PreToolUse",
        "meaning: tool-call retry checkpoint, not user-input intake",
        "using-coding-convention",
        "boundary-contract",
        "boundary-reason",
        "next-required",
        "hook-verified clean",
        "user-explicit defer/skip may continue",
        "pending merge remains undecided when deferred",
        "skill-call:",
        "routing-surface",
        "intent-relation",
        "change-depth",
        "focus-layer",
        "verification-complexity",
        "forced-visibility",
        "accepted-continuation requires recorded acceptance",
        "unknown routing-surface values fail closed",
    ],
    "boundary-contract/SKILL.md": [
        "name: boundary-contract",
        "Keep field names",
        "[boundary-contract]",
        "- objective: <work-objective>",
        "phase",
        "objective",
        "explicit-non-goals",
        "allowed-surface",
        "prohibited-surface",
        "locked-decisions",
        "acceptance-criteria",
        "open-questions",
        "test-purpose",
        "stop-conditions",
        "next-allowed-actions",
    ],
    "_shared/install_hooks.py": [
        "TOOL_CHECKPOINT_MARKER",
        "TOOL_CHECKPOINT_INTERNAL",
        "pre_tool_use",
        "tool-checkpoint",
        "do not run an extra shell manifest check",
        "hook-enforced retry point",
        "task_router_reminder_hook.py",
        "task-router waits until session-intent preflight exists",
        "Absent downstream-gates.json means silent allow",
        "atomic meaning units",
        "focus-layer",
        "scope-reopen",
        "tool-call retry checkpoint, not user-input intake",
        "jailbreak-detector downstream gate",
        "task-router consumes session-intent and jailbreak gate context",
        "intent",
    ],
    "_shared/ghost-alice-hook.mjs": [
        "PreToolUse",
        "permissionDecision",
        "downstream-gates.json",
    ],
    "_shared/reminder_texts.json": [
        "hook-enforced retry point",
        "Emit a visible [tool-checkpoint] block",
        "hook-stage: PreToolUse",
        "tool-call retry checkpoint, not user-input intake",
        "intent",
        "why",
        "procedure",
        "contract-ref",
        "contract-check",
        "localized-human-note",
        "rejected-alternatives",
        "unverified-premises",
        "failure-mode-if-wrong",
        "recovery-action",
        "focus-layer",
        "scope-reopen",
        "never skip the gate",
        "read-only",
        "boundary-contract",
    ],
    "coding-convention/using-coding-convention/SKILL.md": [
        "[gate-state]",
        "[completion-check]",
        "acceptance-criteria",
        "claim-evidence-map",
        "unverified",
        "skill-call:",
        "quality-maintenance device confirmed through repeated user work",
        "user intent, work scope, and verification quality",
        "do not bypass it from the agent's judgment alone",
        "leave a short reason for the skip",
        "Recommendations, option selection, status judgments",
    ],
    "coding-convention/verification-before-completion/SKILL.md": [
        "No acceptance-criteria means no completed verification-before-completion",
        "claim-evidence-map",
        "unverified",
    ],
    "session-intent-analyzer/references/ledger-schema.md": [
        "acceptance_criteria",
        "verifiable completion criterion",
        "intent-state.json is update-plus-accumulate state",
    ],
    "jailbreak-detector/SKILL.md": [
        "Deterministic hard-block rules are narrow regression guards",
        "Gradual multi-turn jailbreak resistance depends on session-intent summary quality",
    ],
    "session-intent-analyzer/scripts/session_intent_ledger.py": [
        "acceptance_criteria",
        "consumer_snapshot",
    ],
    ".github/workflows/skill-gate-contract.yml": [
        "python scripts/check_skill_gate_contract.py",
        "python -m unittest scripts.tests.test_check_skill_gate_contract",
        "scripts.tests.test_validate_entrypoints",
    ],
}

GATE_STATE_ORDER_PATHS = [
    "AGENTS.md",
    "platforms/codex/AGENTS.md",
    "docs/policies/session-gate-matrix.md",
    "task-router/SKILL.md",
    "coding-convention/using-coding-convention/SKILL.md",
]

LEGACY_TOOL_CHECKPOINT_TOKENS = (
    "action" + "-gate",
    "ACTION" + "_GATE",
    "action" + "_gate",
    "Action" + " gate",
    "Action" + "-Gate",
    "[action" + "-gate]",
)

SCAN_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".tmp",
    "__pycache__",
    "node_modules",
}

SCAN_EXCLUDED_PATH_PREFIXES = (
    (".worktrees",),
    (".claude", "worktrees"),
)

SCAN_EXCLUDED_SUFFIXES = {
    ".bak",
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".log",
    ".png",
    ".pyc",
    ".sqlite",
    ".webp",
    ".zip",
}

FORBIDDEN_PATTERNS = {
    "task-router/SKILL.md": [
        (
            r"figure\s+out\s+the\s+work\s+intent|what\s+the\s+user\s+really\s+wants|judge\s+only\s+the\s+following\s+items\s+from\s+the\s+current\s+user\s+input|request\s+analysis|work\s+intent\s+analysis",
            "task-router raw intent judgment wording is forbidden",
        ),
        (r"alternatives-considered", "forbidden pattern present: alternatives-considered"),
        (r"inherited-premises", "forbidden pattern present: inherited-premises"),
        (r"risk-if-wrong", "forbidden pattern present: risk-if-wrong"),
        (
            r"existence\s+reason\s+for\s+every\s+tool\s+call|single\s+entrypoint|every\s+new\s+tool\s+action|not\s+negotiable",
            "task-router over-scope wording is forbidden",
        ),
        (
            r"after\s+the\s+collaborative\s+merge\s+procedure\s+is\s+complete|after\s+merge-companion\s+is\s+complete",
            "pending-merge hard-block wording is forbidden",
        ),
    ],
    "AGENTS.md": [
        (
            r"after\s+the\s+collaborative\s+merge\s+procedure\s+is\s+complete|after\s+merge-companion\s+is\s+complete",
            "pending-merge hard-block wording is forbidden",
        ),
    ],
    "platforms/codex/AGENTS.md": [
        (
            r"if\s+an\s+undecided\s+entry\s+is\s+reported,\s+call\s+merge-companion\s+immediately",
            "pending-merge hard-block wording is forbidden",
        ),
    ],
    "_shared/install_hooks.py": [
        (
            r"run merge-companion before normal work|before task-router or session-intent semantic work",
            "pending-merge hard-block wording is forbidden",
        ),
        (
            r"compact tool-checkpoint|compact\s+`\[tool-checkpoint\]`|compact\s+\[tool-checkpoint\]",
            "compact runtime tool-checkpoint wording is forbidden",
        ),
        (
            r"risk,\s+and\s+recovery",
            "compact runtime tool-checkpoint wording is forbidden",
        ),
    ],
    "_shared/pending_merge_precheck_hook.py": [
        (
            r"Activate merge-companion before normal work|before task-router and session-intent semantic work",
            "pending-merge hard-block wording is forbidden",
        ),
    ],
    "_shared/ghost-alice-hook.mjs": [
        (
            r"Activate merge-companion before normal work|before task-router or session-intent semantic work",
            "pending-merge hard-block wording is forbidden",
        ),
        (
            r"compact tool-checkpoint|compact\s+`\[tool-checkpoint\]`|compact\s+\[tool-checkpoint\]",
            "compact runtime tool-checkpoint wording is forbidden",
        ),
        (
            r"risk,\s+and\s+recovery",
            "compact runtime tool-checkpoint wording is forbidden",
        ),
    ],
    "coding-convention/verification-before-completion/SKILL.md": [
        (
            r"(?<![A-Za-z0-9_])tests?\s+pass\s+means\s+complete(?![A-Za-z0-9_])",
            "surrounding evidence cannot be treated as direct proof: tests pass means complete",
        ),
        (
            r"(?<![A-Za-z0-9_])lint\s+pass\s+means\s+complete(?![A-Za-z0-9_])",
            "surrounding evidence cannot be treated as direct proof: lint pass means complete",
        ),
        (
            r"(?<![A-Za-z0-9_])hook\s+installed\s+means\s+verified(?![A-Za-z0-9_])",
            "surrounding evidence cannot be treated as direct proof: hook installed means verified",
        ),
    ],
    "coding-convention/using-coding-convention/SKILL.md": [
        (
            r"not\s+negotiable|No\s+rationalization\s+can\s+escape\s+it|\"the\s+skill\s+is\s+excessive\"\s*\|\s*simple\s+work\s+becomes\s+complex\.\s*Use\s+it\.",
            "coercive skip wording is forbidden",
        ),
    ],
}


def _is_excluded_contract_path(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part in SCAN_EXCLUDED_DIRS for part in rel_parts):
        return True
    return any(
        rel_parts[: len(prefix)] == prefix
        for prefix in SCAN_EXCLUDED_PATH_PREFIXES
    )


def _iter_contract_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded_contract_path(path, root):
            continue
        if path.suffix.lower() in SCAN_EXCLUDED_SUFFIXES:
            continue
        yield path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _gate_state_blocks(text: str) -> list[list[str]]:
    lines = text.splitlines()
    blocks: list[list[str]] = []
    for index, line in enumerate(lines):
        if line.strip() != "[gate-state]":
            continue
        block: list[str] = []
        for body_line in lines[index + 1 :]:
            stripped = body_line.strip()
            if stripped.startswith("- "):
                block.append(stripped)
                continue
            if not stripped:
                continue
            break
        if block:
            blocks.append(block)
    return blocks


def _gate_state_positions(block: list[str]) -> dict[str, int]:
    positions: dict[str, int] = {}
    for index, line in enumerate(block):
        match = re.match(r"-\s+([a-z0-9-]+)\s*:", line)
        if match:
            positions.setdefault(match.group(1), index)
    return positions


def _check_gate_state_order(root: Path) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for rel_path in GATE_STATE_ORDER_PATHS:
        path = root / rel_path
        if not path.is_file():
            continue
        for block in _gate_state_blocks(_read_text(path)):
            positions = _gate_state_positions(block)
            if "task-router" not in positions:
                continue
            if "session-intent-analyzer" not in positions:
                issues.append(
                    ContractIssue(
                        "ERROR",
                        rel_path,
                        "gate-state with task-router must include session-intent-analyzer",
                    )
                )
                continue
            if positions["session-intent-analyzer"] > positions["task-router"]:
                issues.append(
                    ContractIssue(
                        "ERROR",
                        rel_path,
                        "gate-state must list session-intent-analyzer before task-router",
                    )
                )
            if (
                "merge-companion-precheck" in positions
                and positions["merge-companion-precheck"] > positions["session-intent-analyzer"]
            ):
                issues.append(
                    ContractIssue(
                        "ERROR",
                        rel_path,
                        "gate-state must list merge-companion-precheck before session-intent-analyzer",
                    )
                )
    return issues


def _check_legacy_tool_checkpoint_names(root: Path) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for path in _iter_contract_text_files(root):
        try:
            text = _read_text(path)
        except UnicodeDecodeError:
            continue
        for token in LEGACY_TOOL_CHECKPOINT_TOKENS:
            if token in text:
                issues.append(
                    ContractIssue(
                        "ERROR",
                        str(path.relative_to(root)),
                        "legacy tool checkpoint name is forbidden",
                    )
                )
                break
    return issues


def run_contract_checks(root: Path) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    contract_path = root / "skill-catalog" / "session-gates.json"
    if not contract_path.is_file():
        return [ContractIssue("ERROR", str(contract_path), "session gate contract file missing")]

    contract = json.loads(_read_text(contract_path))
    missing_keys = REQUIRED_CONTRACT_KEYS - set(contract.keys())
    for key in sorted(missing_keys):
        issues.append(ContractIssue("ERROR", str(contract_path), f"missing contract key: {key}"))

    always_first = contract.get("always_first", [])
    if "session-intent-analyzer" not in always_first:
        issues.append(ContractIssue("ERROR", str(contract_path), "always_first must include session-intent-analyzer"))
    if "task-router" in always_first:
        issues.append(ContractIssue("ERROR", str(contract_path), "always_first must not put task-router before session-intent-analyzer"))

    agent_first_after_intake = contract.get("agent_first_after_intake", [])
    if "task-router" not in agent_first_after_intake:
        issues.append(ContractIssue("ERROR", str(contract_path), "agent_first_after_intake must include task-router"))

    user_input_graph = contract.get("user_input_graph", [])
    expected_order = [
        "merge-companion-precheck",
        "session-intent-analyzer",
        "jailbreak-detector",
        "task-router",
        "tool-checkpoint",
    ]
    graph_positions = {
        item.split(":", 1)[0]: index
        for index, item in enumerate(user_input_graph)
        if isinstance(item, str)
    }
    for item in expected_order:
        if item not in graph_positions:
            issues.append(ContractIssue("ERROR", str(contract_path), f"user_input_graph must include {item}"))
    for earlier, later in zip(expected_order, expected_order[1:]):
        if (
            earlier in graph_positions
            and later in graph_positions
            and graph_positions[earlier] > graph_positions[later]
        ):
            issues.append(
                ContractIssue(
                    "ERROR",
                    str(contract_path),
                    f"user_input_graph must order {earlier} before {later}",
                )
            )
    if "skill-evolution:report-only-terminal-branch" not in user_input_graph:
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "user_input_graph must mark skill-evolution as report-only-terminal-branch",
            )
        )

    intent_consumers = contract.get("intent_consumers", {})
    session_consumers = intent_consumers.get("session-intent-analyzer", [])
    for required_consumer in ("skill-evolution", "jailbreak-detector"):
        if required_consumer not in session_consumers:
            issues.append(
                ContractIssue(
                    "ERROR",
                    str(contract_path),
                    f"intent_consumers.session-intent-analyzer must include {required_consumer}",
                )
            )
    if "report-only-terminal-branch" not in intent_consumers.get("skill-evolution", []):
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "intent_consumers.skill-evolution must be report-only-terminal-branch",
            )
        )
    if "task-router" not in intent_consumers.get("jailbreak-detector", []):
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "intent_consumers.jailbreak-detector must feed task-router",
            )
        )

    conditional_gates = contract.get("conditional_gates", [])
    if not any(gate.get("id") == "boundary-contract" for gate in conditional_gates if isinstance(gate, dict)):
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "conditional_gates must include boundary-contract",
            )
        )

    domain_routes = contract.get("domain_routes", {})
    if "using-coding-convention" not in domain_routes.get("development", []):
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "development domain route must include using-coding-convention",
            )
        )

    bugfix_route = domain_routes.get("bugfix", [])
    for required in ("systematic-debugging", "test-driven-development"):
        if required not in bugfix_route:
            issues.append(
                ContractIssue(
                    "ERROR",
                    str(contract_path),
                    f"bugfix domain route must include {required}",
                )
            )

    if "verification-before-completion" not in contract.get("before_completion", []):
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "before_completion must include verification-before-completion",
            )
        )

    if "finishing-a-development-branch" not in contract.get("before_commit_push", []):
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "before_commit_push must include finishing-a-development-branch",
            )
        )

    if "necessity-gate" not in contract.get("on_task_definition", []):
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "on_task_definition must include necessity-gate",
            )
        )

    for rel_path, patterns in REQUIRED_PATTERNS.items():
        path = root / rel_path
        if not path.is_file():
            issues.append(ContractIssue("ERROR", rel_path, "required file missing"))
            continue
        text = _read_text(path)
        if rel_path in ("install.sh", "install.ps1"):
            # install.sh / install.ps1 are modularized: function definitions (and the
            # governance contract strings they carry, e.g. the codex bootstrap block)
            # now live in installer_lib/*.sh and installer_lib/*.ps1. Validate the
            # combined installer source so required patterns are found regardless of
            # which module now holds them.
            ext = ".sh" if rel_path == "install.sh" else ".ps1"
            lib_dir = root / "installer_lib"
            if lib_dir.is_dir():
                for lib in sorted(lib_dir.glob(f"*{ext}")):
                    text += "\n" + _read_text(lib)
        for pattern in patterns:
            if pattern not in text:
                issues.append(ContractIssue("ERROR", rel_path, f"missing required pattern: {pattern}"))

    for rel_path, patterns in FORBIDDEN_PATTERNS.items():
        path = root / rel_path
        if not path.is_file():
            continue
        text = _read_text(path)
        for pattern, message in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
                issues.append(ContractIssue("ERROR", rel_path, message))

    issues.extend(_check_gate_state_order(root))
    issues.extend(_check_legacy_tool_checkpoint_names(root))

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the repository session gate contract")
    parser.add_argument("--root", default=".", help="Repository root path")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    issues = run_contract_checks(root)
    if issues:
        for issue in issues:
            print(f"[{issue.severity}] {issue.path}: {issue.message}")
        return 2

    print("Session gate contract verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
