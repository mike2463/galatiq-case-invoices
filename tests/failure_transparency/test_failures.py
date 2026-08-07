"""Failures remain FAILED/ERROR and never synthesize approval or payment."""

import asyncio
from pathlib import Path

import httpx
import openai
import pytest

from invoice_agents.config import Settings
from invoice_agents.models import CaseStatus, ToolStatus
from invoice_agents.orchestration import _error_record, process_invoice
from invoice_agents.tools.comparison import InventoryReader


def test_missing_key_fails_before_case_or_model(
    invoice_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Preflight failures now write artifacts/results JSON (G11); keep it in tmp.
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        xai_api_key=None,
        inventory_db=tmp_path / "missing-inventory.db",
        workflow_db=tmp_path / "missing-workflow.db",
    )
    result = asyncio.run(process_invoice(invoice_dir / "invoice_1001.txt", settings))
    assert result.status is CaseStatus.FAILED
    assert result.stop_reason == "PROVIDER_PREFLIGHT_FAILED"
    assert result.final_decision is None
    assert result.payment is None


def test_missing_sqlite_lookup_is_error_not_not_found(tmp_path: Path) -> None:
    result = InventoryReader(tmp_path / "missing.db").lookup_inventory_exact("NeverFound")
    assert result.status is ToolStatus.ERROR
    assert result.status is not ToolStatus.NOT_FOUND


def test_provider_error_categories_and_request_ids_remain_distinct() -> None:
    request = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
    unauthorized_response = httpx.Response(
        401, request=request, headers={"x-request-id": "req-auth-sentinel"}
    )
    auth = _error_record(
        openai.AuthenticationError("invalid key", response=unauthorized_response, body=None)
    )
    assert auth.category == "AUTHENTICATION"
    assert auth.stop_reason == "PROVIDER_AUTHENTICATION_FAILED"
    assert auth.provider_request_id == "req-auth-sentinel"

    rate_response = httpx.Response(429, request=request, headers={"x-request-id": "req-rate"})
    rate = _error_record(openai.RateLimitError("exhausted", response=rate_response, body=None))
    assert rate.category == "RATE_LIMIT"
    assert rate.stop_reason == "PROVIDER_RATE_LIMIT_EXHAUSTED"
    assert rate.provider_request_id == "req-rate"

    timeout = _error_record(openai.APITimeoutError(request))
    assert timeout.category == "TIMEOUT"
    assert timeout.stop_reason == "PROVIDER_TIMEOUT"

    network = _error_record(openai.APIConnectionError(request=request))
    assert network.category == "PROVIDER"
    assert network.stop_reason == "PROVIDER_REQUEST_FAILED"
