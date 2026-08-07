"""Secret redaction and strict structured-output validation."""

import pytest
from pydantic import ValidationError

from invoice_agents.models import DecisionKind, FinalDecision
from invoice_agents.observability.audit import redact


def test_recursive_redaction() -> None:
    value = {
        "Authorization": "Bearer abc.secret",
        "nested": {"xai_api_key": "secret-value", "safe": "keep"},
        "message": "Authorization was Bearer token123",
    }
    cleaned = redact(value)
    assert cleaned["Authorization"] == "[REDACTED]"
    assert cleaned["nested"]["xai_api_key"] == "[REDACTED]"
    assert cleaned["nested"]["safe"] == "keep"
    assert "token123" not in cleaned["message"]


def test_invalid_structured_output_is_not_defaulted() -> None:
    with pytest.raises(ValidationError):
        FinalDecision.model_validate(
            {
                "decision": "MAYBE",
                "reasons": [],
                "critic_disposition": DecisionKind.HOLD,
                "payment_eligible": True,
                "unexpected": "field",
            }
        )
    with pytest.raises(ValidationError, match="only APPROVE"):
        FinalDecision(
            decision=DecisionKind.REJECT,
            reasons=["no"],
            critic_disposition=DecisionKind.REJECT,
            payment_eligible=True,
        )
