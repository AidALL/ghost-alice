#!/usr/bin/env python3
"""
check_skill_gate_contract.py. Static session gate contract validator.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from markdown_structure import atx_headings

@dataclass
class ContractIssue:
    severity: str
    path: str
    message: str


# Check protocol identifiers and canonical owner links, not repeated narrative wording.
# These static checks establish document wiring; runtime tests establish behavior.
REQUIRED_PATTERNS = {'docs/policies/session-gate-matrix.md': ['session-intent-analyzer',
                                          'task-router',
                                          'skill-evolution',
                                          'tool-checkpoint',
                                          'boundary-contract',
                                          'verification-before-completion',
                                          'routing-surface'],
 'AGENTS.md': ['installed common contract', 'current session metadata', 'platforms/common-governance.md'],
 'platforms/codex/AGENTS.md': ['## Codex tools and installation',
                               'Metadata alone is not execution'],
 'platforms/claude/CLAUDE.md': ['## Claude tools and installation',
                                'visible `Skill` tool'],
 '_shared/installer/hosts.py': ['"platforms" / "codex" / "AGENTS.md"',
                                'hook_definitions.CORE_HOOK_IDS'],
 '_shared/global_rule_blocks.py': ['CODEX_SPEC',
                                   'render_codex_bootstrap',
                                   'apply_codex_bootstrap',
                                   'codex-merge'],
 'README.md': ['python scripts/check_skill_gate_contract.py'],
 'scripts/validate_skills.py': ['run_contract_checks', 'gate-contract'],
 'task-router/SKILL.md': ['session-intent-analyzer',
                          'using-coding-convention',
                          'boundary-contract',
                          'response-route',
                          'systematic-debugging',
                          'Work Selection section',
                          'verification-before-completion',
                          'references/routing-details.md',
                          '../session-intent-analyzer/references/ledger-schema.md#core-response-projection'],
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
                                'stop-conditions',
                                'next-allowed-actions',
                                'a boundary the human user set',
                                'self-declared'],
 '_shared/install_hooks.py': ['TOOL_CHECKPOINT_MARKER',
                              'TOOL_CHECKPOINT_INTERNAL',
                              'pre_tool_use',
                              'tool-checkpoint'],
 '_shared/ghost-alice-hook.mjs': ['PreToolUse'],
 '_shared/reminder_texts.json': ['tool-checkpoint'],
 'coding-convention/using-coding-convention/SKILL.md': ['verification-before-completion',
                                                        'boundary-contract',
                                                        'systematic-debugging',
                                                        'Instruction Body Reuse'],
 'coding-convention/verification-before-completion/SKILL.md': ['acceptance-criteria',
                                                               'claim-evidence-map',
                                                               'unverified',
                                                               '[completion-check]',
                                                               '[partial-check]',
                                                               'source and time',
                                                               'incident-accounting-and-continuation'],
 'session-intent-analyzer/references/ledger-schema.md': ['acceptance_criteria',
                                                         'Core Response Projection',
                                                         'record_response_route'],
 'session-intent-analyzer/scripts/session_intent_ledger.py': ['acceptance_criteria',
                                                              'consumer_snapshot',
                                                              'record_response_route'],
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
        "Instruction Body Reuse", ("session_check_cache.py", "--body-retained"),
    ),
    OwnerSection(
        "coding-convention/verification-before-completion/SKILL.md",
        "session-intent-analyzer's `references/ledger-schema.md`, section Core Response Projection",
        "session-intent-analyzer/references/ledger-schema.md", "Core Response Projection",
        ("response-route.v2", "record_response_route"),
    ),
    OwnerSection(
        "coding-convention/verification-before-completion/SKILL.md",
        "references/response-review.md#deliver",
        "coding-convention/verification-before-completion/references/response-review.md",
        "Deliver",
        ("recording-only error", "Do not invoke Stop"),
    ),
)

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
    def scan_error(error: OSError) -> None:
        if not isinstance(error, PermissionError):
            raise error

    for directory, dirs, files in os.walk(root, onerror=scan_error):
        parent = Path(directory)
        # Excluded trees can contain running tests and cleanup; never descend into them.
        dirs[:] = [name for name in dirs if not _is_excluded_contract_path(parent / name, root)]
        for name in files:
            path = parent / name
            if not path.is_file() or _is_excluded_contract_path(path, root):
                continue
            if path.suffix.lower() not in SCAN_EXCLUDED_SUFFIXES:
                yield path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _owner_section(text: str, heading: str) -> str | None:
    headings = iter(atx_headings(text))
    for owner in headings:
        if owner.title == heading:
            end = next((section.start for section in headings if section.level <= owner.level), len(text))
            return text[owner.end:end]
    return None


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
            or rel.as_posix() in ("AGENTS.md", "_shared/reminder_texts.json"))


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
            or not all(_owner_section(common_text, heading) is not None for heading in ("Session Intent", "Work Selection"))):
        return [ContractIssue(
            "ERROR", "platforms/claude/CLAUDE.md",
            "Claude bootstrap must reference one common governance contract with Session Intent and Work Selection sections",
        )]
    return []


def run_contract_checks(root: Path) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
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
