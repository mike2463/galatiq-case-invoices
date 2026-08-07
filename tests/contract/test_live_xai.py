"""Paid compatibility contracts; skipped status is visibly distinct from pass."""

import os

import pytest

from invoice_agents.compatibility import run_live_contracts
from invoice_agents.config import Settings


@pytest.mark.live
@pytest.mark.asyncio
async def test_autogen_xai_live_contracts() -> None:
    if os.getenv("RUN_LIVE_XAI") != "1":
        pytest.skip("live xAI contracts NOT RUN; set RUN_LIVE_XAI=1")
    checks = await run_live_contracts(Settings())
    assert checks
    assert all(check.passed for check in checks), [check.model_dump() for check in checks]
