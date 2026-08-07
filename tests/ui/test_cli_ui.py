"""The ui command initializes databases by default and stays loopback-only."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from invoice_agents.cli import app

runner = CliRunner()


def test_ui_refuses_non_loopback_host_without_flag() -> None:
    result = runner.invoke(app, ["ui", "--host", "0.0.0.0"])
    assert result.exit_code == 1
    assert "allow-remote-i-understand" in result.output
    assert "no authentication" in result.output


def test_ui_help_documents_loopback_default() -> None:
    result = runner.invoke(app, ["ui", "--help"])
    assert result.exit_code == 0
    assert "loopback" in result.output.lower()
    assert "init-db" in result.output


def test_ui_initializes_databases_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    result = runner.invoke(app, ["ui"])
    assert result.exit_code == 0
    assert (tmp_path / "inventory.db").is_file()
    assert (tmp_path / "workflow.db").is_file()
    assert "inventory database ready" in result.output
    assert "workflow database ready" in result.output

    again = runner.invoke(app, ["ui"])
    assert again.exit_code == 0
    assert "already migrated" in again.output


def test_ui_no_init_db_skips_database_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    result = runner.invoke(app, ["ui", "--no-init-db"])
    assert result.exit_code == 0
    assert not (tmp_path / "inventory.db").exists()
    assert not (tmp_path / "workflow.db").exists()
