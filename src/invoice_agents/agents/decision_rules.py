"""Pure final-decision rules; every violation raises and nothing defaults to approval."""

from __future__ import annotations

from decimal import Decimal

from invoice_agents.errors import ErrorCategory, InvoiceAgentsError
from invoice_agents.models import (
    Critique,
    DecisionKind,
    HumanDecisionKind,
    InventoryStatus,
    ReviewRequest,
    RiskAssessment,
)

AUTHORIZING_HUMAN_DECISIONS = frozenset(
    {
        HumanDecisionKind.APPROVE,
        HumanDecisionKind.ESTABLISH_MAPPING,
        HumanDecisionKind.SUPERSEDE_REVISION,
    }
)

# Evidence an authorizing decision does not implicitly clear: payment against
# unavailable/unknown stock or a conflicting declared total always needs its own ruling.
BLOCKING_INVENTORY_STATUSES = frozenset(
    {
        InventoryStatus.EXCEEDS_STOCK,
        InventoryStatus.OUT_OF_STOCK,
        InventoryStatus.UNKNOWN,
        InventoryStatus.INVALID_QUANTITY,
    }
)


def blocking_evidence(risk: RiskAssessment) -> list[str]:
    """List evidence that still blocks APPROVE regardless of an authorizing decision."""

    blockers = [
        f"inventory {item.status}: {item.raw_items} requested={item.requested_quantity} "
        f"stock={item.available_stock}"
        for item in risk.inventory
        if item.status in BLOCKING_INVENTORY_STATUSES
    ]
    total_delta = risk.financial.total_delta
    if total_delta is not None and total_delta != Decimal("0"):
        blockers.append(f"declared/calculated total delta is {total_delta}")
    return blockers


def assert_new_review_cycle_permitted(
    latest: ReviewRequest | None, case_id: str | None = None
) -> None:
    """Refuse to open a review cycle over a final (non-authorizing) human ruling.

    REJECT and REQUEST_CORRECTION already rule on every listed blocker; the only
    lawful continuation is the forced final decision, so re-escalation is refused
    loudly instead of looping the queue. Authorizing decisions with remaining
    blocking evidence stay eligible for a further cycle (remediation §3.5).
    """

    if latest is None or latest.status != "RESOLVED" or latest.human_decision is None:
        return
    decision = latest.human_decision.decision
    if decision not in AUTHORIZING_HUMAN_DECISIONS:
        raise InvoiceAgentsError(
            ErrorCategory.TOOL,
            f"human decision {decision} on review {latest.review_id} is final; submit the "
            "matching final decision instead of requesting another review",
            case_id=case_id,
            stop_reason="HUMAN_DECISION_MUST_BE_OBEYED",
        )


def validate_final_decision(
    selected: DecisionKind,
    payment_eligible: bool,
    risk: RiskAssessment,
    critique: Critique,
    review: ReviewRequest | None,
    case_id: str | None = None,
) -> None:
    """Raise for every rule violation; returning means the decision may be persisted.

    Rules, in order:
    1. Policy/ambiguity triggers require a resolved human review.
    2. A recorded human decision constrains the agent: REJECT forces REJECT,
       REQUEST_CORRECTION forces HOLD, and an authorizing decision permits APPROVE -
       or HOLD when blocking evidence the decision did not address remains. It never
       forces APPROVE against remaining blocking evidence, and REJECT after an
       authorizing decision stays a conflict.
    3. APPROVE additionally requires the independent critic's APPROVE or a resolved
       authorizing human decision; an unresolved disagreement stops the case.
    4. APPROVE and payment eligibility must agree exactly.
    """

    if risk.policy_review_reasons and (review is None or review.status != "RESOLVED"):
        raise InvoiceAgentsError(
            ErrorCategory.TOOL,
            "policy/ambiguity triggers require resolved human review before final decision",
            case_id=case_id,
            stop_reason="HUMAN_REVIEW_UNRESOLVED",
        )
    human = review.human_decision if review else None
    if human is not None:
        if human.decision in AUTHORIZING_HUMAN_DECISIONS:
            remaining = blocking_evidence(risk)
            hold_permitted = selected is DecisionKind.HOLD and remaining
            if selected is not DecisionKind.APPROVE and not hold_permitted:
                raise InvoiceAgentsError(
                    ErrorCategory.TOOL,
                    "agent final decision conflicts with authorizing human decision",
                    case_id=case_id,
                    stop_reason="HUMAN_AGENT_DECISION_CONFLICT",
                )
        if human.decision is HumanDecisionKind.REJECT and selected is not DecisionKind.REJECT:
            raise InvoiceAgentsError(
                ErrorCategory.TOOL,
                "agent final decision conflicts with human rejection",
                case_id=case_id,
                stop_reason="HUMAN_AGENT_DECISION_CONFLICT",
            )
        if (
            human.decision is HumanDecisionKind.REQUEST_CORRECTION
            and selected is not DecisionKind.HOLD
        ):
            raise InvoiceAgentsError(
                ErrorCategory.TOOL,
                "request-correction human decision requires HOLD",
                case_id=case_id,
                stop_reason="HUMAN_AGENT_DECISION_CONFLICT",
            )
    if selected is DecisionKind.APPROVE and critique.recommended_disposition is not (
        DecisionKind.APPROVE
    ):
        authorized = human is not None and human.decision in AUTHORIZING_HUMAN_DECISIONS
        if not authorized:
            raise InvoiceAgentsError(
                ErrorCategory.TOOL,
                f"critic recommends {critique.recommended_disposition} and no human decision "
                "authorizes APPROVE; the disagreement requires human review",
                case_id=case_id,
                stop_reason="CRITIC_DISAGREEMENT_UNRESOLVED",
            )
    if selected is DecisionKind.APPROVE and not payment_eligible:
        raise InvoiceAgentsError(
            ErrorCategory.SCHEMA,
            "APPROVE must set payment_eligible=true",
            case_id=case_id,
            stop_reason="FINAL_DECISION_INVALID",
        )
    if selected is not DecisionKind.APPROVE and payment_eligible:
        raise InvoiceAgentsError(
            ErrorCategory.SCHEMA,
            "non-APPROVE decision cannot be payment eligible",
            case_id=case_id,
            stop_reason="FINAL_DECISION_INVALID",
        )
