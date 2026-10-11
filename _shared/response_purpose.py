"""Shared vocabulary for response intent, independent of durable work status."""

RESPONSE_PURPOSES = frozenset({"execution", "explanation", "approval-wait", "partial", "completion"})
NONCLOSING_PURPOSES = frozenset({"explanation", "approval-wait", "partial"})


def response_purpose(route):
    """An omitted response purpose defaults to execution in response-route.v1."""
    purpose = route.get("response_purpose", "execution")
    if not isinstance(purpose, str) or purpose not in RESPONSE_PURPOSES:
        raise ValueError("unknown response_purpose")
    return purpose
