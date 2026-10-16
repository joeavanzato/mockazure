from uuid import uuid4

import httpx
from typer.testing import CliRunner

from mockazure.admin.app import create_app
from mockazure.cli import app as cli
from mockazure.endpoints import EndpointProfile
from tests.integration.identity_support import identity_client


async def test_advertised_profiles_keep_one_issuer(database, settings, tmp_path):
    profile = EndpointProfile(
        login="https://login.mockazure.test",
        graph="https://graph.mockazure.test",
        exchange="https://outlook.mockazure.test",
        arm="https://management.mockazure.test",
        browser_login="https://localhost:8443",
    )
    configured = settings.model_copy(
        update={
            "service_endpoints": profile,
            "client_profiles": {"host": settings.endpoints, "container": profile},
            "allowed_hosts": (*settings.allowed_hosts, "login.mockazure.test"),
        }
    )
    async with identity_client(database, configured) as (client, tenant, client_id, *_):
        host = (await client.get(f"/{tenant}/v2.0/.well-known/openid-configuration")).json()
        assert host["token_endpoint"].startswith("https://localhost:8443/")
        assert host["issuer"] == profile.issuer(tenant)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(configured, database)),
            base_url="https://login.mockazure.test",
        ) as internal:
            result = await internal.get(
                f"/{tenant}/v2.0/.well-known/openid-configuration",
                headers={"X-Forwarded-Host": "evil.example"},
            )
            assert result.json()["token_endpoint"].startswith(profile.login)
            assert result.json()["issuer"] == host["issuer"]
        path = tmp_path / "config.json"
        path.write_text(
            configured.model_dump_json().replace(
                "**********", settings.database_url.get_secret_value()
            )
        )
        output = tmp_path / "client.json"
        response = CliRunner().invoke(
            cli,
            [
                "client-config",
                "--tenant",
                tenant,
                "--client",
                client_id,
                "--profile",
                "host",
                "--output",
                str(output),
                "--config",
                str(path),
            ],
        )
        assert response.exit_code == 0, response.stdout
        import json

        data = json.loads(output.read_text())
        assert data["issuer"] == profile.issuer(tenant)
        assert data["endpoints"]["login"] == "https://localhost:8443"
        assert "database_url" not in data and "secret" not in data
        assert data["tenant_id"] == tenant


def test_client_config_requires_valid_profile(tmp_path):
    result = CliRunner().invoke(
        cli,
        [
            "client-config",
            "--tenant",
            str(uuid4()),
            "--client",
            str(uuid4()),
            "--profile",
            "unknown",
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
