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
            "Reply with only the missing or corrected control blocks: [completion-check]. Printed [io-trace] is optional; strict audit remains required. "
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
        "Write [completion-check] as a canonical multi-line block, not an inline one-line summary. Printed [io-trace] is optional; strict audit remains required. "
        "Preserve the substantive answer when the defect is only control-field formatting; "
        "change only the invalid control fields, not the business calculations or conclusions. "
        "Retain original evidence times and proof references without rerunning unchanged business checks. "
        "Do not invent evidence or turn a failed/unverified result into a pass. "
        "If the required evidence is unavailable, deliver a corrected complete answer that withdraws unsupported "
        "completion claims and report the unverified or failed state without a finalized [completion-check]."
    )


def _usable_business_payload(text):
    if not isinstance(text,str) or not text.strip():
        return False
    # A repair that contains only control metadata cannot identify the earlier
    # substantive answer. The host may instead supply its retained answer ref.
    if any(marker in text for marker in ('[completion-check]','[io-trace]')):
        scalar_fields={'criterion','claim','evidence','verdict','source','summary','verification-before-completion',
            'skill-call','acceptance-criteria','claim-evidence-map','unverified','files-read','files-written',
            'files-searched','commands-run','web-accessed','skills-loaded','skills-reused','subagents'}
        for line in text.splitlines():
            value=line.strip()
            if not value or value in {'[completion-check]','[io-trace]','- none'} or value.startswith('```'):
                continue
            if line[:1].isspace() or value.removeprefix('- ').split(':',1)[0] in scalar_fields:
                continue
            return True
        return False
    return True


def observe_recovery_delivery(input_data, *, platform, entrypoint='', final_text=''):
    """Interpret the owning host observation once, never infer from child env identity.

    Callers supply actual Stop input or a retained host observation, not intent
    ledger material. References/digests identify host-held answers without
    copying their bodies into the ledger. Entry-point alone proves nothing.
    """
    import hashlib
    data = input_data if isinstance(input_data, dict) else {}
    host = data.get('host_context')
    if not isinstance(host, dict) or 'delivery_surface' not in host:
        scratchpad = data.get('scratchpad_dir')
        native_visible = entrypoint == 'cli' and isinstance(scratchpad, str) and bool(scratchpad.strip())
        visible = platform == 'claude' and (entrypoint in {'claude-desktop', 'claude-vscode', 'remote_desktop', 'remote_mobile'} or native_visible)
        final_only = platform == 'claude' and entrypoint in {'sdk-cli', 'sdk-ts', 'sdk-py'}
        host = dict(delivery_surface='visible_transcript' if visible else 'final_only' if final_only else 'unknown')
    reference = data.get('prior_answer_ref')
    if not isinstance(reference, str) or not reference.strip():
        reference = 'sha256:' + hashlib.sha256(final_text.encode()).hexdigest() if _usable_business_payload(final_text) else None
    changed = data.get('claims_changed', False)
    route = select_recovery_delivery(host, reference, changed, True)
    return dict(delivery_route=route, host_context=host, prior_answer_ref=reference,
                claims_changed=changed, guidance=recovery_delivery_guidance(route))
