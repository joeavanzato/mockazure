import os
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from mockazure.admin.app import create_app
from mockazure.bootstrap import initialize_instance
from mockazure.db.migrations import migrate


async def test_complete_host_authority_validation(database, settings):
    settings.token_file.write_text("A" * 43)
    transport = httpx.ASGITransport(app=create_app(settings, database), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://localhost:8443") as client:
        for host in (
            "localhost:garbage",
            "localhost:8443@other.invalid",
            "localhost:0",
            "localhost:65536",
            "localhost:8443:80",
        ):
            response = await client.get("/_mock/health", headers={"Host": host})
            assert response.status_code == 400, host
        response = await client.get(
            "/_mock/health", headers=[("Host", "localhost"), ("Host", "other.invalid")]
        )
        assert response.status_code == 400
        assert (
            await client.get("/_mock/health", headers={"Host": "LOCALHOST:8443"})
        ).status_code == 200


async def test_slash_requests_never_reflect_alternate_port(database, settings):
    settings.token_file.write_text("A" * 43)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, database)),
        base_url="https://localhost:8443",
    ) as client:
        response = await client.get(
            "/_mock/tenants/",
            headers={"Authorization": "Bearer " + "A" * 43, "Host": "localhost:9999"},
        )
        assert response.status_code == 404
        assert "location" not in response.headers


@pytest.mark.parametrize("field,value", [("display_name", "A\x00B"), ("licences", ["A\x00B"])])
async def test_nul_rejected_by_create_and_import(database, settings, tenant_payload, field, value):
    settings.token_file.write_text("A" * 43)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, database)),
        base_url="https://localhost:8443",
        headers={"Authorization": "Bearer " + "A" * 43},
    ) as client:
        invalid = {**tenant_payload, field: value}
        assert (await client.post("/_mock/tenants", json=invalid)).status_code == 422
        snapshot = {
            "schema_version": 1,
            "source_tenant_id": str(uuid4()),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "tenant": invalid,
        }
        assert (
            await client.post("/_mock/tenants/import", json={"snapshot": snapshot})
        ).status_code == 422
        assert (await client.get("/_mock/tenants")).json()["items"] == []


async def test_unknown_schema_revision_has_sanitized_cli_failure(database, settings, tmp_path):
    async with database.sessions.begin() as session:
        await session.execute(text("UPDATE alembic_version SET version_num='unknown-revision'"))
    with pytest.raises(ValueError, match="migration failed"):
        migrate(settings.database_url.get_secret_value())
    files = initialize_instance(
        tmp_path / "instance",
        database_url=settings.database_url.get_secret_value(),
        advertised_host="localhost",
        port=8443,
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("MOCKAZURE_")
    }
    result = subprocess.run(
        [sys.executable, "-m", "mockazure", "db", "upgrade", "--config", str(files.config_file)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=15,
    )
    assert result.returncode != 0
    assert "Migration failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert settings.database_url.get_secret_value() not in result.stderr
