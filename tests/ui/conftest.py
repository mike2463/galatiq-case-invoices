"""Web-console fixtures: ephemeral migrated DBs, isolated cwd, in-process app."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from invoice_agents.config import Settings
from invoice_agents.ui.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "invoices"


@pytest.fixture(autouse=True)
def _reset_sse_app_status() -> Iterator[None]:
    """sse_starlette's module-level AppStatus lazily binds its exit Event to the
    first event loop that serves SSE; each test here runs the app on a fresh
    loop, so a stale Event raises "bound to a different event loop"."""

    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit = False
    AppStatus.should_exit_event = None


@pytest.fixture
def ui_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated cwd with its own data/invoices corpus subset; artifacts stay here."""

    invoice_dir = tmp_path / "data" / "invoices"
    invoice_dir.mkdir(parents=True)
    for name in ("invoice_1001.txt", "invoice_1002.txt"):
        shutil.copy(DATA_DIR / name, invoice_dir / name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def app(settings: Settings, ui_workdir: Path) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
