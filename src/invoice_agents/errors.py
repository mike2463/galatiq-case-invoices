"""Stable error taxonomy used at every application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    """Categories retained in results and audit events without collapsing causes."""

    CONFIGURATION = "CONFIGURATION"
    AUTHENTICATION = "AUTHENTICATION"
    PROVIDER = "PROVIDER"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    DATABASE = "DATABASE"
    SOURCE = "SOURCE"
    PARSE = "PARSE"
    TOOL = "TOOL"
    SCHEMA = "SCHEMA"
    ORCHESTRATION = "ORCHESTRATION"
    PAYMENT = "PAYMENT"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class InvoiceAgentsError(Exception):
    """Expected application error with audit-safe context."""

    category: ErrorCategory
    message: str
    case_id: str | None = None
    stop_reason: str | None = None
    provider_request_id: str | None = None
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.category}: {self.message}"


class DatabaseVerificationError(InvoiceAgentsError):
    """Raised when a required database fails signature, schema, or integrity checks."""


class SourceEvidenceError(InvoiceAgentsError):
    """Raised when source evidence cannot be read without inventing content."""


class PaymentExecutionError(InvoiceAgentsError):
    """Raised when the mock payment tool cannot record a trustworthy result."""
