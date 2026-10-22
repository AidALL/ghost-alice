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

# Check protocol identifiers and canonical owner links, not repeated narrative wording.
# These static checks establish document wiring; runtime tests establish behavior.
REQUIRED_PATTERNS = {'docs/policies/session-gate-matrix.md': ['skill-catalog/session-gates.json',
                                          'session-intent-analyzer',
                                          'task-router',
                                          'skill-evolution',
                                          'tool-checkpoint',
                                          'boundary-contract',
                                          'verification-before-completion',
                                          'routing-surface',
                                          'clarification-only',
                                          'direct-response'],
 'AGENTS.md': ['installed common contract', 'current-session receipt', 'platforms/common-governance.md'],
 'platforms/codex/AGENTS.md': ['## Codex tools and installation',
                               'Metadata alone is not execution',
                               'current hook receipt'],
 'platforms/claude/CLAUDE.md': ['## Claude tools and installation',
                                'visible `Skill` tool',
                                'current hook receipt'],
 '_shared/installer/source.py': ['session-gates.json'],
 '_shared/installer/hosts.py': ['"platforms" / "codex" / "AGENTS.md"',
                                'hook_definitions.CORE_HOOK_IDS'],
 '_shared/global_rule_blocks.py': ['CODEX_SPEC',
                                   'render_codex_bootstrap',
                                   'apply_codex_bootstrap',
                                   'codex-merge'],
 'README.md': ['skill-catalog/session-gates.json', 'python scripts/check_skill_gate_contract.py'],
 'scripts/validate_skills.py': ['run_contract_checks', 'gate-contract'],
 'task-router/SKILL.md': ['session-intent-analyzer',
                          'using-coding-convention',
                          'boundary-contract',
                          'response-route',
                          'systematic-debugging',
                          'Work Selection section',
                          'verification-before-completion',
                          'references/routing-details.md',
                          'ledger-schema.md#core-response-projection'],
 'boundary-contract/SKILL.md': ['name: boundary-contract',
                                '## Authority and renewal',
                                '[boundary-contract]',
                                'objective',
                                'explicit-non-goals',
                                'allowed-surface',
                                'prohibited-surface',
                                'locked-decisions',
                                'acceptance-criteria',
                                'open-questions',
                                'test-purpose',
                                'stop-conditions',
                                'next-allowed-actions',
                                'human-owned',
                                'agent-declared'],
 '_shared/install_hooks.py': ['TOOL_CHECKPOINT_MARKER',
                              'TOOL_CHECKPOINT_INTERNAL',
                              'pre_tool_use',
                              'tool-checkpoint',
                              'task_router_reminder_hook.py'],
 '_shared/ghost-alice-hook.mjs': ['PreToolUse', 'readSessionIntentSnapshot', 'taskRouterReleaseMessage'],
 '_shared/reminder_texts.json': ['tool-checkpoint'],
 'coding-convention/using-coding-convention/SKILL.md': ['verification-before-completion',
                                                        'boundary-contract',
                                                        'systematic-debugging',
                                                        'Instruction Body Reuse',
                                                        'one physical source line'],
 'coding-convention/verification-before-completion/SKILL.md': ['acceptance-criteria',
                                                               'claim-evidence-map',
                                                               'unverified',
                                                               '[completion-check]',
                                                               '[partial-check]',
                                                               'source and time',
                                                               'incident-accounting-and-continuation'],
 'session-intent-analyzer/references/ledger-schema.md': ['acceptance_criteria',
                                                         'Core Response Projection',
                                                         'record_response_route',
                                                         'direct-response',
                                                         'clarification-only'],
 'session-intent-analyzer/scripts/session_intent_ledger.py': ['acceptance_criteria',
                                                              'consumer_snapshot',
                                                              'record_response_route'],
 '.github/workflows/skill-gate-contract.yml': ['python scripts/check_skill_gate_contract.py',
                                               'python scripts/run_installer_compat_tests.py --group '
                                               'skill-gate-contract'],
 'platforms/common-governance.md': ['## Operating Model',
                                    '## Session Intent',
                                    '## Work Selection',
                                    '`user-said: `',
                                    '`interpreted-as: `',
                                    '`[focus: ',
                                    'session-intent-analyzer',
                                    'task-router',
                                    'verification-before-completion',
                                    'merge-companion']}


@dataclass(frozen=True)
class OwnerSection:
    consumer: str
    reference: str
    owner: str
    heading: str
    identifiers: tuple[str, ...]
    message_key: str | None = None


# Callers name the owning procedure; only that section owns its detailed fields.
OWNER_SECTIONS = (
    OwnerSection(
        "_shared/reminder_texts.json", "io-trace Transparency Rule", "platforms/common-governance.md",
        "io-trace Transparency Rule",
        ("intent", "why", "stage: before-tool", "contract-ref", "contract-check"),
        message_key="tool-checkpoint",
    ),
    OwnerSection(
        "coding-convention/using-coding-convention/SKILL.md",
        "../verification-before-completion/references/verify-or-reuse.md#instruction-body-reuse",
        "coding-convention/verification-before-completion/references/verify-or-reuse.md",
        "Instruction Body Reuse", ("skills-reused", "skills-loaded", "skill-call"),
    ),
    OwnerSection(
        "coding-convention/verification-before-completion/SKILL.md",
        "session-intent-analyzer's `references/ledger-schema.md`, section Core Response Projection",
        "session-intent-analyzer/references/ledger-schema.md", "Core Response Projection",
        ("response-route.v1", "record_response_route"),
    ),
    OwnerSection(
        "coding-convention/verification-before-completion/SKILL.md",
        "references/autopilot-publication.md#apply-and-deliver",
        "coding-convention/verification-before-completion/references/autopilot-publication.md",
        "Apply and deliver",
        ("continue: true", "response_assessment.body_issue", "strict audit", "does not hold or replace desktop output"),
    ),
)

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

# Removed contract wording must not return to prompt or policy text.
REMOVED_CONTRACT_TOKENS = (
    "First" + "-Entry Contract",
    "hook" + "-stage",
    "retry" + " checkpoint",
    "printed io" + "-trace is optional",
    "Printed `[io" + "-trace]` is optional",
    "counts against" + " the user's tool limit",
    "or count includes" + " governance calls",
    "tool-count limit" + " includes",
    "[gate" + "-state]",
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
            r"run merge-companion before normal work|merge-companion before other work|before task-router or session-intent semantic work",
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


def _owner_section(text: str, heading: str) -> str | None:
    match = re.search(r"(?m)^(#{1,6})[ \t]+" + re.escape(heading) + r"[ \t]*$", text)
    if match is None:
        return None
    body = text[match.end():]
    end = re.search(r"(?m)^#{1," + str(len(match[1])) + r"}[ \t]+", body)
    return body[:end.start()] if end else body


def _check_owner_sections(root: Path) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for contract in OWNER_SECTIONS:
        consumer_path = root / contract.consumer
        if consumer_path.is_file():
            consumer = _read_text(consumer_path)
            reference = contract.reference
            if contract.message_key is not None:
                try:
                    messages = json.loads(consumer)
                    consumer = messages.get(contract.message_key) if isinstance(messages, dict) else None
                except json.JSONDecodeError:
                    consumer = None
                reference = contract.reference
            if not isinstance(consumer, str) or reference not in consumer:
                issues.append(ContractIssue("ERROR", contract.consumer,
                                            f"missing owner reference: {contract.reference}"))
        owner_path = root / contract.owner
        if not owner_path.is_file():
            issues.append(ContractIssue("ERROR", contract.owner, "required owner file missing"))
            continue
        body = _owner_section(_read_text(owner_path), contract.heading)
        if body is None:
            issues.append(ContractIssue("ERROR", contract.owner,
                                        f"required owner section missing: {contract.heading}"))
            continue
        for identifier in contract.identifiers:
            if identifier not in body:
                issues.append(ContractIssue("ERROR", contract.owner,
                                            f"owner section {contract.heading} missing required identifier: {identifier}"))
    return issues


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


def _is_prompt_or_policy_text(rel: Path) -> bool:
    parts = rel.parts
    return (parts[:1] == ("platforms",) or parts[:2] == ("docs", "policies") or parts[:3] == ("docs", "ko", "policies")
            or rel.name == "SKILL.md" or (len(parts) > 1 and parts[-2] == "references" and rel.suffix == ".md")
            or rel.as_posix() == "_shared/reminder_texts.json")


def _check_removed_contract_wording(root: Path) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for path in _iter_contract_text_files(root):
        rel = path.relative_to(root)
        if not _is_prompt_or_policy_text(rel):
            continue
        try:
            text = _read_text(path)
        except UnicodeDecodeError:
            continue
        for token in REMOVED_CONTRACT_TOKENS:
            if token in text:
                issues.append(ContractIssue("ERROR", str(rel), f"removed contract wording is forbidden: {token}"))
    return issues


def _check_claude_first_turn_contract(root: Path) -> list[ContractIssue]:
    path = root / "platforms/claude/CLAUDE.md"
    if not path.is_file():
        return []  # The required-file check reports this separately.
    common = root / "platforms/common-governance.md"
    common_text = common.read_text(encoding="utf-8") if common.is_file() else ""
    if (path.read_text(encoding="utf-8").count("{{COMMON_GOVERNANCE}}") != 1
            or not all(_owner_section(common_text, heading) for heading in ("Session Intent", "Work Selection"))):
        return [ContractIssue(
            "ERROR", "platforms/claude/CLAUDE.md",
            "Claude bootstrap must reference one common governance contract with Session Intent and Work Selection sections",
        )]
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
        "owner": "session-intent",
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

    issues.extend(_check_owner_sections(root))
    issues.extend(_check_gate_state_order(root))
    issues.extend(_check_claude_first_turn_contract(root))
    issues.extend(_check_legacy_tool_checkpoint_names(root))
    issues.extend(_check_removed_contract_wording(root))

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
