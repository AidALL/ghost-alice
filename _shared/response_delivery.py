"""Core-owned recovery delivery selection and guidance (no host I/O).

``host_context.delivery_surface`` is the host delivery capability: one of
visible_transcript, final_only, unknown. The native observer uses explicit host
context or the adapter mapping for its known entrypoint. A usable answer or
retained host reference is also required for a controls-only correction.
The business body belongs to the host, never the intent ledger.
``claims_changed`` and ``metadata_changed`` are assessed deltas, not permissions.
The selector returns a delivery enum and neither rewrites nor verifies content.
"""

from __future__ import annotations

DELIVERY_ROUTES = frozenset({"preserve_answer_and_patch_metadata", "corrected_complete_answer",
                             "assembled_complete_payload", "resolve_host_delivery"})


def select_recovery_delivery(host_context, prior_answer_ref, claims_changed, metadata_changed):
    """Choose a payload shape without confusing metadata repair with new claims."""
    if claims_changed is True:
        return "corrected_complete_answer"
    host_context = host_context if isinstance(host_context, dict) else {}
    if (host_context.get("delivery_surface") == "visible_transcript"
            and isinstance(prior_answer_ref, str) and prior_answer_ref.strip()
            and claims_changed is False):
        return "preserve_answer_and_patch_metadata"
    if (host_context.get("delivery_surface") not in {"visible_transcript", "final_only"}
            and isinstance(prior_answer_ref, str) and prior_answer_ref.strip()
            and claims_changed is False):
        return "resolve_host_delivery"
    return "assembled_complete_payload"


def recovery_delivery_guidance(delivery_route):
    """Describe answer inclusion only; the diagnostic owns any required operation."""
    shapes = {
        "preserve_answer_and_patch_metadata": "The prior answer is visible. The current-session assistant returns only the correction required by the diagnostic, without repeating that answer.",
        "corrected_complete_answer": "The current-session assistant submits the complete answer with the changed claim and preserves the other supported claims.",
        "assembled_complete_payload": "The current-session assistant includes the retained answer once with any correction required by the diagnostic in one final response.",
        "resolve_host_delivery": (
            "The current-session assistant determines answer visibility from supplied conversation and host context. "
            "The current-session assistant must not run tools to discover answer visibility. "
            "If the answer is already visible, the current-session assistant returns only the correction required by the diagnostic. "
            "If the host displays only the final response, the current-session assistant includes the retained answer once. "
            "If visibility is unknown, the current-session assistant returns the correction without repeating the answer."),
    }
    if delivery_route not in DELIVERY_ROUTES:
        raise ValueError("unknown recovery delivery route")
    return f"delivery-route: {delivery_route}\n" + shapes[delivery_route]


def runtime_record_repair_guidance():
    """Identify the owning procedure for a diagnosed publication record defect."""
    return (
        "For a recording defect that prevents an authorized dependent action, the current-session assistant follows "
        "verification-before-completion/references/autopilot-publication.md, section Recording-only failures, "
        "with the diagnostic's record identifiers and original evidence times and proof references. "
        "Core validation code authenticates the completion review; a recording defect does not request another semantic review."
    )


def _usable_business_payload(text):
    if not isinstance(text,str) or not text.strip():
        return False
    # A repair that contains only control metadata cannot identify the earlier
    # substantive answer. The host may instead supply its retained answer ref.
    if any(marker in text for marker in ('[completion-check]','[partial-check]','[io-trace]')):
        scalar_fields={'criterion','claim','evidence','verdict','source','summary','verification-before-completion',
            'skill-call','scope','acceptance-criteria','claim-evidence-map','unverified','files-read','files-written',
            'files-searched','commands-run','web-accessed','skills-loaded','skills-reused','subagents'}
        for line in text.splitlines():
            value=line.strip()
            if not value or value in {'[completion-check]','[partial-check]','[io-trace]','- none'} or value.startswith('```'):
                continue
            if line[:1].isspace() or value.removeprefix('- ').split(':',1)[0] in scalar_fields:
                continue
            return True
        return False
    return True


def observe_recovery_delivery(input_data, *, platform, entrypoint='', final_text='', repair_target='completion_metadata'):
    """Interpret the owning host observation once, never infer from child env identity.

    Callers supply actual Stop input or a retained host observation, not intent
    ledger material. References/digests identify host-held answers without
    copying their bodies into the ledger. Unknown entrypoints retain unknown
    visibility; a known delivery capability does not supply a missing answer.
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
    # A second malformed metadata-only reply is not a lost business answer.
    # Keep validating its fields without making the current-session assistant replay the body.
    if (changed is False and host.get('delivery_surface') != 'final_only'
            and isinstance(final_text, str) and any(marker in final_text for marker in ('[completion-check]', '[partial-check]'))
            and not _usable_business_payload(final_text) and not reference):
        route = 'resolve_host_delivery'
    guidance = f'repair-target: {repair_target}\n' + recovery_delivery_guidance(route)
    if changed is False and repair_target in {'response_route', 'response_review', 'runtime_records'}:
        guidance += ' This internal record operation requires no new completion controls.'
    if repair_target == 'source_status':
        guidance += ' Delivery selection does not establish source validity or claim meaning.'
    return dict(delivery_route=route, host_context=host, prior_answer_ref=reference,
                claims_changed=changed, guidance=guidance)
