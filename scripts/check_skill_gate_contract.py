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
    "routing_surface",
    "terminal_routes",
}

REQUIRED_PATTERNS = {
    "docs/policies/session-gate-matrix.md": [
        "skill-catalog/session-gates.json",
        "task-router",
        "using-coding-convention",
        "boundary-contract",
        "session-intent-analyzer",
        "session-intent-analyzer intake is first",
        "boundary-contract: required",
        "English canonical narrative + English control surface",
        "[gate-state]",
        "[completion-check]",
        "acceptance-criteria",
        "claim-evidence-map",
        "unverified",
        "skill-call:",
        "Required gate skills are not complete unless a fresh or valid retained instruction body was used",
        "Read or validly reuse the relevant instruction body before marking a required gate done",
        "Runtime Hook Graph Contract",
        "Hook pending-merge precheck is a pre-routing/session-start layer",
        "manual fallback still completes before actionable downstream work",
        "intent-state.json is update-plus-accumulate state",
        "does not feed task-router",
        "user-explicit defer/skip may continue",
        "PreToolUse/BeforeTool checkpoint",
        "hook-stage: PreToolUse",
        "meaning: tool-call retry checkpoint, not user-input intake",
        "session-intent-analyzer intake is first",
        "Runtime hooks surface one full `[tool-checkpoint]` per session input lineage",
        "keep checking later tool calls silently",
        "first poll in the input lineage",
        "Dynamic Focus Contract",
        "mismatch location",
        "verification burden",
        "micro, meso, macro, and meta",
        "scope reopen point",
        "Routing Surface Contract",
        "routing-surface",
        "primary-request",
        "causal-axis",
        "response-mode",
        "response-order",
        "clarification-only",
        "direct-response",
        "resolved-intent-first",
        "Ambient working directory",
        "Do not validate or rebut that premise",
        "Route classification precedes evidence planning",
        "Intake and routing still run internally",
        "minimum decisive information",
        "terminal objective outranks",
        "Do not let a means replace, narrow, or expand",
        "task-router owns the reusable work judgment",
        "session-intent-analyzer records semantic facts and accumulated decisions",
        "governance surface policy consumes routing-surface",
        "token reduction is a secondary consequence, not a success metric",
        "Session-intent intake feeds task-router directly",
        "Native tool permission stays with its host",
    ],
    "AGENTS.md": [
        "skill-catalog/session-gates.json",
        "docs/policies/session-gate-matrix.md",
        "boundary-contract",
        "session-intent-analyzer",
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
        "use a fresh or valid retained instruction body",
        "Hook pending-merge precheck is a pre-routing/session-start layer",
        "manual fallback still completes before actionable downstream work",
        "explicit user defer or skip",
        "tool-call identity",
        "payload content",
        "hook-stage: PreToolUse",
        "meaning: tool-call retry checkpoint, not user-input intake",
        "The focus scope is not fixed and does not expand in only one direction",
        "the mismatch location and the verification burden",
        "micro, meso, macro, and meta",
        "task-router emits a reusable routing-surface",
        "clarification-only",
        "direct-response",
        "Ambient working directory",
        "Do not validate or rebut that premise",
        "Route classification precedes evidence planning",
        "Do not emit `[gate-state]`, `[tool-checkpoint]`, or `[io-trace]`",
        "Session-intent intake feeds task-router directly",
        "Native tool permission stays with its host",
    ],
    "platforms/codex/AGENTS.md": [
        "Session Gate Contract",
        "boundary-contract",
        "session-intent-analyzer",
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
        "Read or validly reuse the skill's instruction body before marking a required gate as complete",
        "Hook pending-merge precheck is a pre-routing/session-start layer",
        "manual fallback still completes before actionable downstream work",
        "explicit user defer or skip",
        "hook-stage: PreToolUse",
        "meaning: tool-call retry checkpoint, not user-input intake",
        "the same process, session, or tool-call id",
        "tool-call identity",
        "payload content",
        "The focus scope is not fixed and does not expand in only one direction",
        "the mismatch location and the verification burden",
        "micro, meso, macro, and meta",
        "task-router emits a reusable routing-surface",
        "clarification-only",
        "direct-response",
        "minimum decisive information",
        "Ambient working directory",
        "Do not validate or rebut that premise",
        "Route classification precedes evidence planning",
        "Session-intent intake feeds task-router directly",
        "Native tool permission stays with its host",
    ],
    "platforms/claude/CLAUDE.md": [
        "clarification-only",
        "direct-response",
        "minimum decisive information",
        "Ambient working directory",
        "Do not validate or rebut that premise",
        "Route classification precedes evidence planning",
        "Intake and routing still run internally",
        "Do not emit `[gate-state]`, `[tool-checkpoint]`, or `[io-trace]`",
    ],
    "install.ps1": [
        "session-gates.json",
        "platforms\\codex\\AGENTS.md",
        "pending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace",
    ],
    "install.sh": [
        "session-gates.json",
        "platforms/codex/AGENTS.md",
        "pending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace",
    ],
    "_shared/global_rule_blocks.py": [
        "CODEX_SPEC",
        "render_codex_bootstrap",
        "apply_codex_bootstrap",
        "codex-merge",
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
        "routing decision",
        "raw user intent inference",
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
        "response-mode",
        "clarification-only",
        "direct-response",
        "resolved-intent-first",
        "minimum decisive information",
        "Ambient working directory",
        "Do not validate or rebut that premise",
        "Route classification precedes evidence planning",
        "change-depth",
        "focus-layer",
        "verification-complexity",
        "forced-visibility",
        "accepted-continuation requires recorded acceptance",
        "unknown routing-surface values fail closed",
        "user corrects the agent for changing, narrowing, widening, relabeling, or",
        "substituting the requested objective, scope, selection criterion",
        "Session-intent intake feeds task-router directly",
        "not intake, raw intent inference, credentials, or native tool permission",
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
        "Renew an agent-declared implementation contract before using an additional surface already inside the user authorization envelope",
        "A changed user-owned boundary or committed objective requires the exact human decision",
        "Do not silently narrow the requested outcome or infer new authority from a wider focus",
    ],
    "_shared/install_hooks.py": [
        "TOOL_CHECKPOINT_MARKER",
        "TOOL_CHECKPOINT_INTERNAL",
        "pre_tool_use",
        "tool-checkpoint",
        "do not run an extra shell manifest check",
        "hook-enforced retry point",
        "task_router_reminder_hook.py",
        "atomic meaning units",
        "focus-layer",
        "scope-reopen",
        "tool-call retry checkpoint, not user-input intake",
        "intent",
        "run task-router after exact session-intent preflight",
    ],
    "_shared/ghost-alice-hook.mjs": [
        "PreToolUse",
        "readSessionIntentSnapshot",
        "taskRouterReleaseMessage",
    ],
    "_shared/reminder_texts.json": [
        "hook-enforced retry point",
        "Surface a visible [tool-checkpoint] block once per user-input tool batch",
        "same session input lineage",
        "continuing to check every tool call",
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
        "finished work, verified results, or a decision that materially changes the user's next action",
        "clarification-only",
        "direct-response",
        "no-work terminal route",
        "exits before a development turn begins",
        "Prose paragraphs stay on one physical source line unless a semantic, syntax, Markdown-structural, or protocol boundary requires a newline.",
        "Scope changes already belong to boundary-contract",
        "Stop and renew or confirm before changing the committed scope",
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
    "session-intent-analyzer/scripts/session_intent_ledger.py": [
        "acceptance_criteria",
        "consumer_snapshot",
    ],
    ".github/workflows/skill-gate-contract.yml": [
        "python scripts/check_skill_gate_contract.py",
        "python scripts/run_installer_compat_tests.py --group skill-gate-contract",
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
            r"compact\s+(?:tool-checkpoint|`\[tool-checkpoint\]`|\[tool-checkpoint\])",
            "compact runtime tool-checkpoint wording is forbidden",
        ),
        (
            "risk," + r"\s+and\s+" + "recovery",
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
            r"compact\s+(?:tool-checkpoint|`\[tool-checkpoint\]`|\[tool-checkpoint\])",
            "compact runtime tool-checkpoint wording is forbidden",
        ),
        (
            "risk," + r"\s+and\s+" + "recovery",
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


def _check_claude_first_turn_contract(root: Path) -> list[ContractIssue]:
    path = root / 'platforms/claude/CLAUDE.md'
    if not path.is_file():
        return []  # The required-file check reports this separately.
    section = re.search(r'^## Claude First-Turn Contract\s*\n(.*?)(?=^## |\Z)',
                        _read_text(path), flags=re.MULTILINE | re.DOTALL)
    steps = re.findall(r'^(\d+)\. (.+)$', section.group(1), flags=re.MULTILINE) if section else []
    numbers = [int(number) for number, _ in steps]
    actions = [action for _, action in steps]
    intake = [index for index, action in enumerate(actions)
              if action.startswith('Connect the `session-intent-analyzer` intake')]
    router = [index for index, action in enumerate(actions) if '`task-router`' in action and 'run' in action.lower()]
    direct = (len(intake) == len(router) == 1 and router[0] == intake[0] + 1
              and re.fullmatch(r'Run `task-router` directly after exact current `session-intent-analyzer` intake\.',
                               actions[router[0]]) is not None)
    if not direct or numbers != list(range(1, len(steps) + 1)):
        return [ContractIssue('ERROR', 'platforms/claude/CLAUDE.md',
                              'Claude first-turn must route directly after exact intake with consecutive steps')]
    return []


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
    if always_first != ["session-intent-analyzer"]:
        issues.append(ContractIssue("ERROR", str(contract_path),
                                    "always_first direct intake ownership requires exactly [session-intent-analyzer]"))

    agent_first_after_intake = contract.get("agent_first_after_intake", [])
    if agent_first_after_intake != ["task-router"]:
        issues.append(ContractIssue("ERROR", str(contract_path),
                                    "agent_first_after_intake direct intake ownership requires exactly [task-router]"))

    user_input_graph = contract.get("user_input_graph", [])
    if not isinstance(user_input_graph, list) or not all(isinstance(item, str) for item in user_input_graph):
        issues.append(ContractIssue('ERROR', str(contract_path),
                                    'user_input_graph direct intake ownership requires a list of string nodes'))
        user_input_graph = []
    allowed_nodes = {'merge-companion-precheck', 'session-intent-analyzer',
                     'skill-evolution:report-only-terminal-branch', 'task-router', 'tool-checkpoint'}
    if len(user_input_graph) != len(allowed_nodes) or set(user_input_graph) != allowed_nodes:
        issues.append(ContractIssue('ERROR', str(contract_path),
                                    'direct intake ownership forbids intermediate input policy stages'))
    expected_order = [
        "merge-companion-precheck",
        "session-intent-analyzer",
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
    if not isinstance(intent_consumers, dict) or set(intent_consumers) != {'session-intent-analyzer', 'skill-evolution'}:
        issues.append(ContractIssue('ERROR', str(contract_path),
                                    'intent_consumers direct intake ownership permits only intake and report-only evolution owners'))
        if not isinstance(intent_consumers, dict):
            intent_consumers = {}
    session_consumers = intent_consumers.get("session-intent-analyzer", [])
    if not isinstance(session_consumers, list) or not all(isinstance(item, str) for item in session_consumers):
        issues.append(ContractIssue('ERROR', str(contract_path),
                                    'intent_consumers.session-intent-analyzer direct intake ownership requires a list of string consumers'))
        session_consumers = []
    if len(session_consumers) != 2 or set(session_consumers) != {'task-router', 'skill-evolution'}:
        issues.append(ContractIssue('ERROR', str(contract_path),
                                    'direct intake ownership requires only task-router and report-only skill-evolution consumers'))
    for required_consumer in ("skill-evolution", "task-router"):
        if required_consumer not in session_consumers:
            issues.append(
                ContractIssue(
                    "ERROR",
                    str(contract_path),
                    f"intent_consumers.session-intent-analyzer must include {required_consumer}",
                )
            )
    if intent_consumers.get("skill-evolution") != ["report-only-terminal-branch"]:
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "intent_consumers.skill-evolution must be report-only-terminal-branch",
            )
        )
    if ("jailbreak-detector" in graph_positions or "jailbreak-detector" in intent_consumers
            or "jailbreak-detector" in session_consumers):
        issues.append(
            ContractIssue(
                "ERROR",
                str(contract_path),
                "retired standalone classifier must not participate in the active graph",
            )
        )

    routing_surface = contract.get("routing_surface", {})
    if not isinstance(routing_surface, dict):
        routing_surface = {}
    required_routing_values = {
        "response_mode": ("normal", "clarification-only", "direct-response"),
        "response_order": ("clarification-question-only", "resolved-intent-first"),
        "next_required": ("user-input",),
    }
    for field, required_values in required_routing_values.items():
        values = routing_surface.get(field, [])
        for required_value in required_values:
            if required_value not in values:
                issues.append(
                    ContractIssue(
                        "ERROR",
                        str(contract_path),
                        f"routing_surface.{field} must include {required_value}",
                    )
                )

    terminal_routes = contract.get("terminal_routes", {})
    clarification_route = terminal_routes.get("clarification-only", {}) if isinstance(terminal_routes, dict) else {}
    clarification_contract = {
        "owner": "task-router",
        "manual_pending_merge": "defer-until-next-actionable-turn",
        "downstream_work": "none",
        "user_surface": "minimum-decisive-information-only",
        "strict_logging": "active",
    }
    if not isinstance(clarification_route, dict):
        clarification_route = {}
    for field, expected in clarification_contract.items():
        if clarification_route.get(field) != expected:
            issues.append(
                ContractIssue(
                    "ERROR",
                    str(contract_path),
                    f"clarification-only terminal route must set {field}={expected}",
                )
            )

    direct_route = terminal_routes.get("direct-response", {}) if isinstance(terminal_routes, dict) else {}
    direct_contract = {
        "owner": "task-router",
        "manual_pending_merge": "defer-until-next-actionable-turn",
        "downstream_work": "none",
        "user_surface": "resolved-content-only",
        "strict_logging": "active",
        "ambient_context": "not-user-referent-or-inspection-authority",
        "premise_handling": "explain-before-current-state-validation",
        "evidence_planning": "after-route-classification",
        "local_diagnosis_authority": "explicit-request-or-established-referent",
    }
    if not isinstance(direct_route, dict):
        direct_route = {}
    for field, expected in direct_contract.items():
        if direct_route.get(field) != expected:
            issues.append(
                ContractIssue(
                    "ERROR",
                    str(contract_path),
                    f"direct-response terminal route must set {field}={expected}",
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
            # Validate the combined installer entrypoint and installer_lib modules, including their governance strings.
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
    issues.extend(_check_claude_first_turn_contract(root))
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
