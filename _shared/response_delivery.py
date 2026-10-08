"""Core-owned recovery delivery selection and guidance (no host I/O).

``host_context.delivery_surface`` is an observed host capability: one of
visible_transcript, final_only, unknown. Entry-point names alone are not proof
that an earlier answer is visible. ``prior_answer_ref`` is a host reference or
digest; the business body belongs to the host, never the intent ledger.
``claims_changed`` and ``metadata_changed`` are assessed deltas, not permissions.
The selector returns a delivery enum and neither rewrites nor verifies content.
"""

from __future__ import annotations

DELIVERY_ROUTES = frozenset({"preserve_answer_and_patch_metadata", "corrected_complete_answer",
                             "assembled_complete_payload"})


def select_recovery_delivery(host_context, prior_answer_ref, claims_changed, metadata_changed):
    """Choose a payload shape without confusing metadata repair with new claims."""
    if claims_changed is True:
        return "corrected_complete_answer"
    host_context = host_context if isinstance(host_context, dict) else {}
    if (host_context.get("delivery_surface") == "visible_transcript"
            and isinstance(prior_answer_ref, str) and prior_answer_ref.strip()
            and claims_changed is False):
        return "preserve_answer_and_patch_metadata"
    return "assembled_complete_payload"


def recovery_delivery_guidance(delivery_route):
    """Single Core source for recovery instructions used by host adapters."""
    if delivery_route not in DELIVERY_ROUTES:
        raise ValueError("unknown recovery delivery route")
    if delivery_route == "preserve_answer_and_patch_metadata":
        shape = (
            "Your answer above stays visible to the user. Do not repeat or rewrite it. "
            "Reply with only the missing or corrected control blocks: [completion-check] followed by [io-trace]. "
            "Keep the answer's claims unchanged. "
        )
    elif delivery_route == "corrected_complete_answer":
        shape = (
            "Deliver a corrected complete answer with the user's requested payload and the genuine claim correction. "
            "Preserve every still-supported conclusion and withdraw unsupported completion claims truthfully. "
        )
    else:
        shape = (
            "Retry by assembling a complete standalone final answer from the original requested answer payload "
            "and corrected metadata. Begin with the user's requested answer, not with the verification process. "
            "Do not output a correction-only note. Do not refer to a previous answer with phrases such as "
            "above, earlier, already provided, or previously. "
        )
    return shape + (
        "Required control block format overrides any requested line-count limit. "
        "Write [completion-check] and [io-trace] as canonical multi-line blocks, not inline one-line summaries. "
        "Preserve the substantive answer when the defect is only control-field formatting; "
        "change only the invalid control fields, not the business calculations or conclusions. "
        "Retain original evidence times and proof references without rerunning unchanged business checks. "
        "Do not invent evidence or turn a failed/unverified result into a pass. "
        "If the required evidence is unavailable, deliver a corrected complete answer that withdraws unsupported "
        "completion claims and report the unverified or failed state without a finalized [completion-check]."
    )
