import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from mockazure.cli import app


def test_identity_diagnostic_is_classified_and_redacted(monkeypatch):

    monkeypatch.setattr(
        "mockazure.config.load_settings",
        lambda config: (_ for _ in ()).throw(ValueError("secret:secret@localhost")),
    )
    result = CliRunner().invoke(app, ["identity", "initialize"])
    assert result.exit_code == 1
    assert "secret" not in result.output
    value = json.loads(result.output)
    assert value == {"component": "identity", "phase": "configuration", "category": "invalid_state"}


def test_worker_processing_failure_is_not_called_startup(monkeypatch):
    from mockazure.commands import jobs

    monkeypatch.setattr(jobs, "load_settings", lambda config: None)
    monkeypatch.setattr(jobs, "create_database", lambda settings: object())
    monkeypatch.setattr(jobs, "schema_is_current", AsyncMock(return_value=True))
    monkeypatch.setattr(jobs, "close_database", AsyncMock())
    monkeypatch.setattr(jobs, "serve_worker", AsyncMock(side_effect=ValueError("private-key")))
    result = CliRunner().invoke(app, ["worker"])
    assert result.exit_code == 2
    assert "private-key" not in result.output
    assert json.loads(result.output) == {
        "component": "worker",
        "phase": "processing",
        "category": "invalid_state",
    }


def test_worker_database_error_exposes_only_sqlstate(monkeypatch):
    from mockazure.commands import jobs

    error = OperationalError(
        "SELECT secret", {"password": "secret"}, SimpleNamespace(sqlstate="08006")
    )
    monkeypatch.setattr(jobs, "load_settings", lambda config: None)
    monkeypatch.setattr(jobs, "create_database", lambda settings: object())
    check = AsyncMock(side_effect=error)
    monkeypatch.setattr(jobs, "schema_is_current", check)
    monkeypatch.setattr(jobs, "close_database", AsyncMock())
    result = CliRunner().invoke(app, ["worker"])
    assert result.exit_code == 2
    assert "secret" not in result.output
    assert json.loads(result.output) == {
        "component": "worker",
        "phase": "schema",
        "category": "database",
        "sqlstate": "08006",
    }
    assert check.call_args.kwargs == {"raise_errors": True}
