"""Bind retained claim references to the native session and Core resolver functions."""
from __future__ import annotations

import json
import os

from claim_evidence import evaluate_claim_report, evaluate_claim_reuse


def _invoke(source, function, *args):
    import original_claim_evidence
    return getattr(original_claim_evidence, function)(source, *args)


def native_claim_source(platform, hook_input, source=None):
    current = dict(os.environ if source is None else source)
    native_session = next((hook_input.get(k) for k in ("session_id", "sessionId", "thread_id", "threadId")
                           if isinstance(hook_input.get(k), str) and hook_input[k].strip()), None)
    if not native_session:
        raise ValueError("Native claim-report session identity is missing")
    current.update(GHOST_ALICE_PLATFORM=platform, GHOST_ALICE_SESSION_ID=native_session)
    return current


def evaluate_runtime_claim_report(text, platform, hook_input, source=None, *, automatic=True):
    def resolve(claims):
        current = native_claim_source(platform, hook_input, source)
        return _invoke(current, "resolve_retained_claims", claims)
    if not automatic:
        return evaluate_claim_report(text, resolve)
    return evaluate_claim_reuse(text, resolve,
        lambda: _invoke(native_claim_source(platform, hook_input, source), "list_retained_claim_candidates"))


def retained_claim_candidates(platform, hook_input, source=None):
    """Return advisory candidates; evaluate_runtime_claim_report authenticates later claims at Stop."""
    try:
        current = native_claim_source(platform, hook_input, source)
        return _invoke(current, "list_retained_claim_candidates")
    except (ValueError, OSError, ImportError, AttributeError):
        return {"status": "unavailable", "claims": []}
