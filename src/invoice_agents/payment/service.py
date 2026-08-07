"""Auditable local payment simulator.

The idempotency identity intentionally excludes source format, hash, amount, and
revision: representations and revisions of the same vendor invoice must not produce
two payments without a separately reviewed adjustment design.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from invoice_agents.db.core import connect_database
from invoice_agents.db.store import WorkflowStore
from invoice_agents.models import (
    DecisionKind,
    ExtractedInvoice,
    HumanDecisionKind,
    Money,
    PaymentResult,
    PaymentStatus,
)


def payment_idempotency_key(invoice: ExtractedInvoice) -> str:
    """Build a stable identity across duplicate formats and revisions."""

    material = "|".join(
        [
            (invoice.vendor.normalized_value or "").casefold().strip(),
            (invoice.invoice_number.normalized_value or "").casefold().strip(),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _from_row(
    row: sqlite3.Row,
    *,
    duplicate: bool = False,
    attempted_case_id: str | None = None,
) -> PaymentResult:
    stored_status = PaymentStatus(str(row["status"]))
    status = (
        PaymentStatus.DUPLICATE
        if duplicate and stored_status is PaymentStatus.PAID
        else stored_status
    )
    return PaymentResult(
        payment_id=str(row["payment_id"]),
        case_id=attempted_case_id or str(row["case_id"]),
        idempotency_key=str(row["idempotency_key"]),
        status=status,
        vendor=str(row["vendor"]),
        amount=Money(amount=Decimal(str(row["amount"])), currency=str(row["currency"])),
        processed_at=datetime.fromisoformat(str(row["created_at"])),
        duplicate_of=str(row["payment_id"]) if duplicate else None,
        error=str(row["error"]) if row["error"] is not None else None,
    )


def mock_payment(
    case_id: str,
    invoice: ExtractedInvoice,
    store: WorkflowStore,
    workflow_db: Path,
    *,
    inject_failure: bool = False,
) -> PaymentResult:
    """Pay an eligible approved case exactly once, or return the prior transaction."""

    key = payment_idempotency_key(invoice)
    decision = store.load_final_decision(case_id)
    if (
        decision is None
        or decision.decision is not DecisionKind.APPROVE
        or not decision.payment_eligible
    ):
        return PaymentResult(
            payment_id=None,
            case_id=case_id,
            idempotency_key=key,
            status=PaymentStatus.NOT_ELIGIBLE,
            vendor=invoice.vendor.normalized_value,
            amount=None,
            processed_at=None,
            error="case lacks an APPROVE decision with payment_eligible=true",
        )
    review = store.load_case_review(case_id)
    if review is not None:
        human = review.human_decision
        if (
            review.status != "RESOLVED"
            or human is None
            or human.decision
            not in {
                HumanDecisionKind.APPROVE,
                HumanDecisionKind.ESTABLISH_MAPPING,
                HumanDecisionKind.SUPERSEDE_REVISION,
            }
        ):
            return PaymentResult(
                payment_id=None,
                case_id=case_id,
                idempotency_key=key,
                status=PaymentStatus.NOT_ELIGIBLE,
                vendor=invoice.vendor.normalized_value,
                amount=None,
                processed_at=None,
                error="human review is unresolved or does not authorize approval",
            )
    vendor = invoice.vendor.normalized_value
    currency = invoice.currency.normalized_value
    amount = invoice.declared_total
    if not vendor or not currency or amount is None or amount <= 0:
        return PaymentResult(
            payment_id=None,
            case_id=case_id,
            idempotency_key=key,
            status=PaymentStatus.NOT_ELIGIBLE,
            vendor=vendor,
            amount=None,
            processed_at=None,
            error="payment requires a vendor, currency, and positive declared total",
        )
    with connect_database(workflow_db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT * FROM payments WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if prior is not None:
            connection.rollback()
            return _from_row(prior, duplicate=True, attempted_case_id=case_id)
        payment_id = f"pay_{uuid4().hex}"
        created_at = datetime.now(UTC)
        status = PaymentStatus.FAILED if inject_failure else PaymentStatus.PAID
        error = "injected mock-payment failure" if inject_failure else None
        connection.execute(
            "INSERT INTO payments("
            "payment_id, case_id, idempotency_key, vendor, amount, currency, status, error, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payment_id,
                case_id,
                key,
                vendor,
                str(amount),
                currency,
                status,
                error,
                created_at.isoformat(),
            ),
        )
        connection.commit()
    return PaymentResult(
        payment_id=payment_id,
        case_id=case_id,
        idempotency_key=key,
        status=status,
        vendor=vendor,
        amount=Money(amount=amount, currency=currency),
        processed_at=created_at,
        error=error,
    )
