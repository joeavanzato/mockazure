from types import SimpleNamespace

from typer.testing import CliRunner

from mockazure.cli import app
from mockazure.config import Settings


def test_maintenance_cli_bounds_and_route(monkeypatch):
    from mockazure.commands import maintenance

    calls = []

    class Client:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))

    monkeypatch.setattr(maintenance, "execute", lambda config, action: action(Client()))
    tenant = "2cd50efb-9d14-430b-82aa-98fef2b7cbc1"
    runner = CliRunner()
    result = runner.invoke(app, ["maintenance", "cleanup", "--tenant-id", tenant])
    assert result.exit_code == 0, result.output
    assert calls == [
        ("POST", f"/_mock/tenants/{tenant}/maintenance/cleanup", {"json": {"max_rows": 500}})
    ]
    for bound in ("0", "1001"):
        result = runner.invoke(
            app, ["maintenance", "cleanup", "--tenant-id", tenant, "--max-rows", bound]
        )
        assert result.exit_code != 0
    assert len(calls) == 1


async def test_maintenance_capability_is_explicit(tmp_path):
    from mockazure.admin.routes import capabilities

    value = await capabilities(
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    settings=Settings(data_dir=tmp_path, database_url="postgresql://localhost/unit")
                )
            )
        )
    )
    assert "maintenance.cleanup" in value["admin_operations"]
    assert not any("cleanup is deferred" in text for text in value["limitations"])
