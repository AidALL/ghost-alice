"""Strict references to accepted original claims; no authority or I/O here.

Only an entire standalone paragraph identical to an accepted claim is covered.
The resolver authenticates work ownership, immutable acceptance and original
dependencies. An envelope never grants authority merely by naming a proof.
"""
from __future__ import annotations

import json
import re

from completion_check_validator import _strip_block_examples, looks_like_evidence_claim

SCHEMA_VERSION = "claim-evidence.v1"
_FIELDS = {"statement", "work_unit_id", "contract_revision", "criterion_id", "proof_digest"}
_MARKER = re.compile(r"^\[/?evidence-report\]$", re.I)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def evaluate_claim_report(text, resolver):
    """Resolve original evidence and return uncovered text for ordinary gates.

    claim_evidence_runtime passes the source-bound original_claim_evidence resolver;
    response control fields cannot supply its result. Absent/example-only envelopes perform no lookup.
    Invalid envelopes preserve all original text and fail closed.
    """
    result = dict(present=False, valid=False, residual_text=text, claims=[], issue=None)
    lines = text.splitlines()
    visible = _strip_block_examples(text).splitlines()
    markers = [i for i, line in enumerate(visible) if _MARKER.fullmatch(line.strip())]
    if not markers:
        return result
    result["present"] = True
    try:
        if (len(markers) != 2 or lines[markers[0]] != "[evidence-report]"
                or lines[markers[1]] != "[/evidence-report]"):
            raise ValueError("One canonical top-level evidence-report pair is required")
        start, end = markers
        raw = "\n".join(lines[start + 1:end])
        if len(raw) > 65536:
            raise ValueError("Claim report is too large")
        payload = json.loads(raw, object_pairs_hook=_object)
        if (not isinstance(payload, dict) or set(payload) != {"schema_version", "claims"}
                or payload["schema_version"] != SCHEMA_VERSION
                or not isinstance(payload["claims"], list) or not 1 <= len(payload["claims"]) <= 32):
            raise ValueError("Invalid claim-evidence.v1 envelope")
        claims = payload["claims"]
        keys, statements = set(), set()
        for claim in claims:
            if (not isinstance(claim, dict) or set(claim) != _FIELDS
                    or type(claim["contract_revision"]) is not int or claim["contract_revision"] < 1
                    or not all(isinstance(claim[k], str) and 0 < len(claim[k]) <= 4096
                               and claim[k] == claim[k].strip() for k in _FIELDS - {"contract_revision"})
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", claim["proof_digest"])
                    or "\n" in claim["statement"] or "[" in claim["statement"]
                    or claim["statement"][0] in "#>*-`"):
                raise ValueError("Invalid qualified claim reference or statement")
            key = tuple(claim[k] for k in ("work_unit_id", "contract_revision", "criterion_id", "proof_digest"))
            if key in keys or claim["statement"] in statements:
                raise ValueError("Duplicate claim reference")
            keys.add(key)
            statements.add(claim["statement"])
        body = "\n".join(lines[:start] + [""] + lines[end + 1:])
        visible_body = _strip_block_examples(body)
        # Authenticate only the exact standalone claim here. Keep every other
        # paragraph, including headings and lists, in residual_text so Core's
        # whole-response review must establish any surrounding or broader scope.
        paragraphs = re.split(r"\n[ \t]*\n", body)
        visible_paragraphs = re.split(r"\n[ \t]*\n", visible_body)
        for statement in statements:
            if paragraphs.count(statement) != 1 or visible_paragraphs.count(statement) != 1:
                raise ValueError("Accepted claim must appear exactly once as a standalone paragraph")
        resolved = resolver(claims)
        rows = resolved.get("claims") if isinstance(resolved, dict) else None
        if (not isinstance(resolved, dict) or resolved.get("status") != "valid"
                or not isinstance(rows, list) or len(rows) != len(claims)):
            raise ValueError("Original accepted evidence is unavailable or invalid")
        for claim, row in zip(claims, rows):
            if (not isinstance(row, dict) or any(row.get(k) != claim[k] for k in _FIELDS)
                    or row.get("validity") != "valid"
                    or not isinstance(row.get("original_claims"), list)
                    or claim["statement"] not in row["original_claims"]
                    or not all(isinstance(row.get(k), str) and row[k].strip() for k in
                               ("original_verified_at", "evidence_reference", "application_reference"))):
                raise ValueError("Claim does not match its qualified accepted original evidence")
        result.update(valid=True, residual_text="\n\n".join(p for p in paragraphs if p not in statements), claims=rows)
    except (ValueError, TypeError, KeyError, OSError, ImportError) as exc:
        result["issue"] = "claim-evidence-invalid: " + str(exc)
    return result


def evaluate_claim_reuse(text, resolver, candidate_provider):
    """Authenticate a response consisting entirely of retained claim paragraphs.

    claim_evidence_runtime supplies candidates from original_claim_evidence's read-only API.
    An incidental match in ordinary prose does not submit an evidence report.
    Eligible responses pass the full resolver before any assertion is covered. New
    completion/partial submissions own their proof and do not inherit old state.
    """
    explicit = evaluate_claim_report(text, resolver)
    if explicit["present"] or looks_like_evidence_claim(text) or not _strip_block_examples(text).strip():
        return explicit
    try:
        candidates = candidate_provider()
        if not isinstance(candidates, dict) or candidates.get("status") != "candidates":
            return explicit
        paragraphs = re.split(r"\n[ \t]*\n", text)
        matched = [row for row in candidates.get("claims", []) if isinstance(row, dict)
                   and row.get("statement") in paragraphs and _FIELDS <= set(row)]
        statements = {row['statement'] for row in matched}
        if not matched or any(p.strip() and p not in statements for p in paragraphs):
            return explicit
        refs = [{key: row[key] for key in _FIELDS} for row in matched]
        envelope = dict(schema_version=SCHEMA_VERSION, claims=refs)
        internal = text + "\n\n[evidence-report]\n" + json.dumps(envelope, ensure_ascii=False) + "\n[/evidence-report]"
        result = evaluate_claim_report(internal, resolver)
        result["automatic"] = True
        if not result["valid"]:
            result["residual_text"] = text
        return result
    except (ValueError, TypeError, KeyError, OSError, ImportError):
        # Discovery failure grants no exemption; ordinary proof remains valid.
        return explicit
