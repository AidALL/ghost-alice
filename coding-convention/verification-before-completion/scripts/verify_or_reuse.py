#!/usr/bin/env python3
"""Shared verify-or-reuse decision contract for Claude and Codex.

Dependencies: Python 3.11+ standard library only. The module is pure: it does
not read artifacts, hooks, ledgers, databases, or the network. Callers pass
semantic facts they already hold, and the result says whether retained
evidence answers the claim or one minimal check of the authoritative copy is
needed. Platform adapters only map tool names to neutral tool classes.

Usage:
    python3 scripts/verify_or_reuse.py decide --facts-json '<facts>'
    python3 scripts/verify_or_reuse.py audit --platform codex --facts-json '<facts>' --calls-json '<calls>'
    python3 scripts/verify_or_reuse.py flaw --facts-json '<facts>' [--response explanation-only]

Resolve this script relative to the loaded skill directory; it does not rely
on any platform-specific skill directory variable.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "verify-or-reuse.v1"

CLAIM_TIMES = ("authored-at", "current")
AUTHORS = ("current-agent", "user", "other-agent", "external-process", "unknown")
AUTHORITIES = ("closed", "agent-mediated", "externally-writable", "unknown")
AUTHORITY_BASES = (
    "interaction-contract",
    "permission",
    "sharing-state",
    "observed-event",
    "storage-location",
    "assumption",
    "none",
)
EVIDENCED_AUTHORITY_BASES = ("interaction-contract", "permission", "sharing-state", "observed-event")
EVIDENCE_ORIGINS = ("authored", "inspected")
MUTATION_EVENTS = (
    "agent-write-succeeded",
    "external-revision",
    "sync-revision",
    "other-agent-write",
    "user-provided-changed-artifact",
)
NON_MUTATION_EVENTS = (
    "user-message",
    "edit-instruction-unexecuted",
    "agent-write-failed",
    "agent-read",
)
DERIVED_EVENTS = ("last-writer-changed",)
VERDICTS = ("reuse", "verify", "wrong-copy", "unverified")
TRIGGERS = (
    "explicit-recheck-request",
    *MUTATION_EVENTS,
    *DERIVED_EVENTS,
    "externally-writable-current-state",
    "retained-evidence-lost",
    "evidence-covers-different-copy",
)
CLAIM_SCOPES = ("authored-at", "current", "last-known", "n/a")
COPY_BASES = ("user-designated", "dated-version", "content-compared", "folder-name", "familiarity", "none")
VERSIONED_COPY_BASES = ("dated-version", "content-compared")

TOOL_CLASSES = ("file-read", "file-search", "shell", "fetch", "connector", "write", "other")
INSPECTION_CLASSES = frozenset({"file-read", "file-search", "shell", "fetch", "connector"})
PLATFORM_TOOL_CLASSES: dict[str, dict[str, str]] = {
    "claude": {
        "Read": "file-read",
        "Grep": "file-search",
        "Glob": "file-search",
        "Bash": "shell",
        "WebFetch": "fetch",
        "Write": "write",
        "Edit": "write",
        "NotebookEdit": "write",
    },
    "codex": {
        "shell": "shell",
        "apply_patch": "write",
    },
}
# Claude Code exposes connector tools as mcp__<server>__<tool>. Codex connector
# calls use an explicit neutral "class" until a payload name is pinned.
CONNECTOR_PREFIXES = {"claude": "mcp__"}
AUDIT_FINDINGS = (
    "redundant-verification",
    "duplicate-verification",
    "wrong-copy-inspection",
    "non-evidential-inspection",
)

DELIVERABLE_ORIGINS = ("agent-provided", "user-provided", "third-party")
REQUEST_MODES = ("deliverable", "diagnosis-only")
REQUIRED_RESPONSES = ("none", "diagnosis-only", "corrected-full-deliverable", "confirm-before-fix")
RESPONSE_KINDS = (
    "corrected-full-deliverable",
    "explanation-only",
    "previous-version",
    "partial-patch",
    "confirmation-question",
)
FAILURE_CLASSES = ("confirmed-flaw-not-propagated", "unnecessary-reapproval", "unconfirmed-out-of-scope-change")

_FACT_KEYS = {
    "claim_time",
    "claim_copy",
    "retained_evidence",
    "last_known_author",
    "mutation_authority",
    "authority_basis",
    "events_since_evidence",
    "hypothetical_changes",
    "artifact_accessible",
    "explicit_recheck_request",
    "planned_check_copy",
    "related_copies",
    "writer_confirmed_unchanged",
}
_FLAW_KEYS = {"flaw_confirmed", "deliverable_origin", "request_mode", "fix_within_scope", "fix_safe"}


def _choice(data: Mapping[str, Any], key: str, allowed: Sequence[str], default: str | None = None) -> str:
    value = data.get(key, default)
    if value not in allowed:
        raise ValueError(f"{key}: {value!r} is not one of {', '.join(allowed)}")
    return value


def _flag(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key}: expected true or false")
    return value


def _text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key}: expected a non-empty string")
    return value


def _strings(data: Mapping[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{key}: expected a list of non-empty strings")
    return list(value)


def _normalize_facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(facts, Mapping):
        raise ValueError("facts: expected an object")
    unknown = sorted(set(facts) - _FACT_KEYS)
    if unknown:
        raise ValueError(f"facts: unknown field {unknown[0]}")
    evidence = facts.get("retained_evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("retained_evidence: expected an object with present, copy, and origin")
    present = _flag(evidence, "present", False)
    evidence_copy = evidence.get("copy")
    if present:
        evidence_copy = _text(evidence_copy, "retained_evidence.copy")
    elif evidence_copy is not None:
        evidence_copy = _text(evidence_copy, "retained_evidence.copy")
    events = _strings(facts, "events_since_evidence")
    for event in events:
        if event not in MUTATION_EVENTS and event not in NON_MUTATION_EVENTS:
            raise ValueError(
                f"events_since_evidence: unknown event type {event!r}; record possibilities in hypothetical_changes"
            )
    planned = facts.get("planned_check_copy")
    return {
        "claim_time": _choice(facts, "claim_time", CLAIM_TIMES),
        "claim_copy": _text(facts.get("claim_copy"), "claim_copy"),
        "evidence_present": present,
        "evidence_copy": evidence_copy,
        "evidence_origin": _choice(evidence, "origin", EVIDENCE_ORIGINS, "inspected"),
        "last_known_author": _choice(facts, "last_known_author", AUTHORS, "unknown"),
        "mutation_authority": _choice(facts, "mutation_authority", AUTHORITIES, "unknown"),
        "authority_basis": _choice(facts, "authority_basis", AUTHORITY_BASES, "none"),
        "events": events,
        "hypothetical_changes": _strings(facts, "hypothetical_changes"),
        "artifact_accessible": _flag(facts, "artifact_accessible", True),
        "explicit_recheck_request": _flag(facts, "explicit_recheck_request", False),
        "planned_check_copy": None if planned is None else _text(planned, "planned_check_copy"),
        "related_copies": _strings(facts, "related_copies"),
        "writer_confirmed_unchanged": _flag(facts, "writer_confirmed_unchanged", False),
    }


def _unique(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: list[dict[str, str]] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def decide(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Return the verify-or-reuse verdict for one claim about one artifact copy."""
    f = _normalize_facts(facts)
    reasons: list[str] = []

    authority = f["mutation_authority"]
    if authority != "unknown" and f["authority_basis"] not in EVIDENCED_AUTHORITY_BASES:
        authority = "unknown"
        reasons.append("authority-not-evidenced")

    mutations = [event for event in f["events"] if event in MUTATION_EVENTS]
    ignored = [{"signal": event, "reason": "not-a-mutation-event"} for event in f["events"] if event in NON_MUTATION_EVENTS]
    ignored += [{"signal": item, "reason": "hypothetical-not-a-trigger"} for item in f["hypothetical_changes"]]
    if (
        not mutations
        and f["evidence_origin"] == "authored"
        and f["last_known_author"] not in ("current-agent", "unknown")
    ):
        mutations = ["last-writer-changed"]

    covers_claim = f["evidence_present"] and f["evidence_copy"] == f["claim_copy"]
    evidence_gap = "retained-evidence-lost" if not f["evidence_present"] else "evidence-covers-different-copy"
    trigger: str | None = None
    verdict = "reuse"

    if f["explicit_recheck_request"]:
        verdict, trigger = "verify", "explicit-recheck-request"
    elif f["claim_time"] == "authored-at":
        if not covers_claim:
            if mutations:
                verdict = "unverified"
                reasons.append("current-copy-is-not-authored-state")
            else:
                verdict, trigger = "verify", evidence_gap
    elif mutations:
        verdict, trigger = "verify", mutations[0]
    elif authority == "externally-writable" and not f["writer_confirmed_unchanged"]:
        verdict, trigger = "verify", "externally-writable-current-state"
    elif not covers_claim:
        verdict, trigger = "verify", evidence_gap

    if verdict == "verify" and not f["artifact_accessible"]:
        verdict = "unverified"
        reasons.append("artifact-not-accessible")

    # The party holding an open change path can close it for this claim by confirming nothing
    # relevant changed; an observed mutation event still wins over that confirmation.
    confirmed_closed = authority in ("closed", "agent-mediated") or f["writer_confirmed_unchanged"]
    if f["writer_confirmed_unchanged"] and not mutations:
        reasons.append("writer-confirmed-unchanged")
    # Later mutations change the current copy, not the state a claim about the authored version refers to.
    evidence_still_valid = covers_claim and (
        f["claim_time"] == "authored-at" or (not mutations and confirmed_closed)
    )
    if verdict == "reuse":
        if f["claim_time"] == "authored-at":
            claim_scope = "authored-at"
        elif confirmed_closed:
            claim_scope = "current"
        else:
            claim_scope = "last-known"
            reasons.append("last-known-scope")
    else:
        claim_scope = "n/a"

    redirect = None
    if f["planned_check_copy"] is not None and f["planned_check_copy"] != f["claim_copy"]:
        redirect = {"copy": f["claim_copy"], "verdict": verdict}
        verdict = "wrong-copy"
        reasons.append("planned-check-targets-different-copy")

    return {
        "schema_version": SCHEMA_VERSION,
        "claim_time": f["claim_time"],
        "target_copy": f["claim_copy"],
        "last_known_author": f["last_known_author"],
        "actual_mutation_authority": authority,
        "observed_mutation_events": mutations,
        "ignored_signals": _unique(ignored),
        "evidence_still_valid": evidence_still_valid,
        "decision_impact": "material" if verdict == "verify" else "none",
        "verdict": verdict,
        "trigger": trigger,
        "claim_scope": claim_scope,
        "tool_call_required": verdict == "verify",
        "check_copy": f["claim_copy"] if verdict == "verify" else None,
        "redirect": redirect,
        "reasons": reasons,
    }


def authoritative_copy(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pick the copy a current-state claim must use; folder names and familiarity are not evidence.

    A user designation wins. Otherwise the newest sortable version (ISO date or zero-padded
    number) backed by dates or compared content wins. Ties or no evidence leave it unresolved.
    """
    designated: list[str] = []
    versioned: list[tuple[str, str, str]] = []
    ignored: list[str] = []
    for candidate in candidates:
        copy_id = _text(candidate.get("copy"), "copy")
        basis = _choice(candidate, "basis", COPY_BASES, "none")
        if basis == "user-designated":
            designated.append(copy_id)
        elif basis in VERSIONED_COPY_BASES and candidate.get("version"):
            versioned.append((str(candidate["version"]), copy_id, basis))
        else:
            ignored.append(copy_id)
    if len(designated) == 1:
        return {"status": "selected", "copy": designated[0], "basis": "user-designated", "ignored": ignored}
    if designated:
        return {"status": "unresolved", "copy": None, "basis": None, "reason": "multiple-user-designations", "ignored": ignored}
    if versioned:
        versioned.sort()
        version, copy_id, basis = versioned[-1]
        if len(versioned) > 1 and versioned[-2][0] == version:
            return {"status": "unresolved", "copy": None, "basis": None, "reason": "tied-version-evidence", "ignored": ignored}
        return {"status": "selected", "copy": copy_id, "basis": basis, "ignored": ignored}
    return {"status": "unresolved", "copy": None, "basis": None, "reason": "no-version-evidence", "ignored": ignored}


_FENCED_BLOCK = re.compile(r"```.*?```", re.S)
_CLAUSE_BREAK = re.compile(r"(?<=[.!?。:;])\s+|\n+")
_MARKUP = re.compile(r"[`*_#>|]+")


def _statements(message: str, min_chars: int) -> list[str]:
    statements = []
    for clause in _CLAUSE_BREAK.split(_FENCED_BLOCK.sub(" ", message)):
        text = " ".join(_MARKUP.sub(" ", clause).split()).lower().strip(" -.!?。:;")
        if len(text) >= min_chars:
            statements.append(text)
    return statements


def audit_restatement(messages: Sequence[str], min_chars: int = 20) -> dict[str, Any]:
    """Flag statements that an earlier message already delivered; fenced control blocks are skipped."""
    first_seen: dict[str, int] = {}
    findings: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        for statement in _statements(str(message), min_chars):
            earlier = first_seen.setdefault(statement, index)
            if earlier != index:
                findings.append({"message": index, "first_seen": earlier, "statement": statement})
    return {"schema_version": SCHEMA_VERSION, "messages": len(messages), "findings": findings}


def tool_class(platform: str, call: Mapping[str, Any]) -> str:
    """Map a platform tool call to a neutral tool class; this is the only adapter duty."""
    if platform not in PLATFORM_TOOL_CLASSES:
        raise ValueError(f"platform: {platform!r} is not one of {', '.join(PLATFORM_TOOL_CLASSES)}")
    if "class" in call:
        return _choice(call, "class", TOOL_CLASSES)
    name = str(call.get("tool", ""))
    mapped = PLATFORM_TOOL_CLASSES[platform].get(name)
    if mapped:
        return mapped
    prefix = CONNECTOR_PREFIXES.get(platform)
    if prefix and name.startswith(prefix):
        return "connector"
    return "other"


def evaluate(platform: str, facts: Mapping[str, Any], planned_call: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Decide for a platform-shaped planned call without letting the platform change semantics."""
    data = copy.deepcopy(dict(facts))
    planned_class = None
    if planned_call is not None:
        planned_class = tool_class(platform, planned_call)
        if planned_class in INSPECTION_CLASSES and planned_call.get("copy"):
            data["planned_check_copy"] = planned_call["copy"]
    result = decide(data)
    result["planned_call_class"] = planned_class
    return result


def audit_trace(platform: str, facts: Mapping[str, Any], calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Classify inspection calls in a finished turn against the verify-or-reuse verdict."""
    data = {key: value for key, value in dict(facts).items() if key != "planned_check_copy"}
    decision = decide(data)
    claim_copy = decision["target_copy"]
    related = set(data.get("related_copies", []))
    evidence_copy = data["retained_evidence"].get("copy")
    if evidence_copy and evidence_copy != claim_copy:
        related.add(evidence_copy)
    related.discard(claim_copy)

    findings: list[dict[str, Any]] = []
    inspections_since_write = 0
    for index, call in enumerate(calls):
        call_class = tool_class(platform, call)
        call_copy = call.get("copy")
        if call_class == "write" and call_copy == claim_copy:
            inspections_since_write = 0
            continue
        if call_class not in INSPECTION_CLASSES:
            continue
        finding = None
        if call_copy in related:
            finding = "wrong-copy-inspection"
        elif call_copy == claim_copy:
            if decision["verdict"] == "reuse":
                finding = "redundant-verification"
            elif decision["verdict"] == "unverified":
                finding = "non-evidential-inspection"
            else:
                inspections_since_write += 1
                if inspections_since_write > 1:
                    finding = "duplicate-verification"
        if finding:
            findings.append({"index": index, "tool": call.get("tool", call.get("class")), "class": call_class,
                             "copy": call_copy, "finding": finding})
    return {"schema_version": SCHEMA_VERSION, "verdict": decision["verdict"], "findings": findings}


def flaw_propagation(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Return the response a confirmed flaw in a provided deliverable requires."""
    if not isinstance(facts, Mapping):
        raise ValueError("facts: expected an object")
    unknown = sorted(set(facts) - _FLAW_KEYS)
    if unknown:
        raise ValueError(f"facts: unknown field {unknown[0]}")
    confirmed = _flag(facts, "flaw_confirmed", False)
    origin = _choice(facts, "deliverable_origin", DELIVERABLE_ORIGINS, "agent-provided")
    mode = _choice(facts, "request_mode", REQUEST_MODES, "deliverable")
    in_scope = _flag(facts, "fix_within_scope", True)
    safe = _flag(facts, "fix_safe", True)
    if not confirmed:
        required, reapproval = "none", False
    elif mode == "diagnosis-only":
        required, reapproval = "diagnosis-only", False
    elif in_scope and safe:
        required, reapproval = "corrected-full-deliverable", False
    else:
        required, reapproval = "confirm-before-fix", True
    return {
        "schema_version": SCHEMA_VERSION,
        "flaw_confirmed": confirmed,
        "deliverable_origin": origin,
        "required_response": required,
        "reapproval_required": reapproval,
    }


def classify_response(decision: Mapping[str, Any], response_kind: str) -> str | None:
    """Name the failure class of a response, or return None when it satisfies the requirement."""
    if response_kind not in RESPONSE_KINDS:
        raise ValueError(f"response: {response_kind!r} is not one of {', '.join(RESPONSE_KINDS)}")
    required = decision.get("required_response")
    if required == "corrected-full-deliverable":
        if response_kind == "corrected-full-deliverable":
            return None
        if response_kind == "confirmation-question":
            return "unnecessary-reapproval"
        return "confirmed-flaw-not-propagated"
    if required == "confirm-before-fix" and response_kind == "corrected-full-deliverable":
        return "unconfirmed-out-of-scope-change"
    return None


def _json_argument(inline: str | None, file_path: str | None, label: str) -> Any:
    if file_path:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    if inline is None:
        raise ValueError(f"{label}: provide --{label}-json or --{label}-file")
    return json.loads(inline)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Decide verify-or-reuse from already-known semantic facts.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("decide", "audit", "flaw"):
        command = commands.add_parser(name)
        command.add_argument("--facts-json")
        command.add_argument("--facts-file")
        if name == "audit":
            command.add_argument("--platform", required=True, choices=sorted(PLATFORM_TOOL_CLASSES))
            command.add_argument("--calls-json")
            command.add_argument("--calls-file")
        if name == "flaw":
            command.add_argument("--response", choices=RESPONSE_KINDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        facts = _json_argument(args.facts_json, args.facts_file, "facts")
        if args.command == "decide":
            result = decide(facts)
        elif args.command == "audit":
            calls = _json_argument(args.calls_json, args.calls_file, "calls")
            if not isinstance(calls, list):
                raise ValueError("calls: expected a list")
            result = audit_trace(args.platform, facts, calls)
        else:
            result = flaw_propagation(facts)
            if args.response:
                result["response_failure"] = classify_response(result, args.response)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"verify_or_reuse: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
