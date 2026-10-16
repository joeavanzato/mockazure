from contextlib import aclosing
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mockazure.admin.app import create_app

pytestmark = pytest.mark.asyncio


async def setup_client(database, settings, tenant_payload):
    settings.token_file.write_text("A" * 43)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, database)),
        base_url="https://localhost:8443",
        headers={"Authorization": "Bearer " + "A" * 43},
    )
    response = await client.post("/_mock/tenants", json=tenant_payload)
    assert response.status_code == 201
    return client, response.json()["id"]


async def test_identity_admin_lifecycle_and_secret_boundaries(database, settings, tenant_payload):
    client, tenant = await setup_client(database, settings, tenant_payload)
    async with aclosing(client):
        base = f"/_mock/tenants/{tenant}/identity"
        user = {
            "upn": "analyst@contoso.test",
            "display_name": "Analyst",
            "password": "local-password-123",
        }
        assert (
            await client.post(base + "/users", json=user, headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        created = await client.post(base + "/users", json=user)
        assert created.status_code == 201, created.text
        user_id = created.json()["id"]
        assert "password" not in created.text and "digest" not in created.text
        assert (
            await client.post(base + "/users", json={**user, "upn": "ANALYST@contoso.test"})
        ).status_code == 409
        assert (
            await client.post(base + "/users", json={**user, "upn": "analyst@unowned.test"})
        ).status_code == 422
        app = await client.post(
            base + "/applications",
            json={
                "display_name": "Collector",
                "client_mode": "confidential",
                "required_permissions": [
                    {
                        "resource": "graph",
                        "kind": "application",
                        "permissions": ["Organization.Read.All"],
                    }
                ],
            },
        )
        assert app.status_code == 201, app.text
        app_id = app.json()["client_id"]
        secret = await client.post(
            base + f"/applications/{app_id}/credentials", json={"kind": "secret"}
        )
        assert secret.status_code == 201, secret.text
        assert len(secret.json()["secret"]) >= 43
        grant_id = str(uuid4())
        grant = {
            "client_id": app_id,
            "resource": "graph",
            "kind": "application",
            "permissions": ["Organization.Read.All"],
        }
        assert (await client.put(base + f"/grants/{grant_id}", json=grant)).status_code == 200
        assert (
            await client.put(
                base + f"/grants/{uuid4()}", json={**grant, "permissions": ["User.Read"]}
            )
        ).status_code == 422
        listing = await client.get(base + "/applications")
        assert listing.status_code == 200
        assert secret.json()["secret"] not in listing.text
        assert "verifier" not in listing.text
        assert (await client.get(base + "/users?limit=501")).status_code == 422
        assert (await client.patch(base + f"/users/{user_id}", json={"enabled": False})).json()[
            "enabled"
        ] is False
        assert (
            await client.delete(base + f"/credentials/{secret.json()['id']}")
        ).status_code == 200
        async with database.sessions() as session:
            stored = (
                await session.execute(
                    text("SELECT verifier, revoked_at FROM identity_credentials WHERE id=:id"),
                    {"id": UUID(secret.json()["id"])},
                )
            ).one()
            assert secret.json()["secret"] not in stored.verifier
            assert stored.revoked_at is not None
        assert (await client.delete(base + f"/grants/{grant_id}")).status_code == 200


async def test_identity_relations_enforce_tenant_ownership(database, settings, tenant_payload):
    client, tenant = await setup_client(database, settings, tenant_payload)
    async with aclosing(client):
        other = (
            await client.post(
                "/_mock/tenants", json={**tenant_payload, "slug": "other", "domain": "other.test"}
            )
        ).json()["id"]
        base = f"/_mock/tenants/{tenant}/identity"
        app = (
            await client.post(
                base + "/applications",
                json={
                    "display_name": "Local",
                    "client_mode": "public",
                    "required_permissions": [
                        {"resource": "graph", "kind": "delegated", "permissions": ["User.Read"]}
                    ],
                },
            )
        ).json()
        foreign_user = (
            await client.post(
                f"/_mock/tenants/{other}/identity/users",
                json={
                    "upn": "reader@other.test",
                    "display_name": "Other",
                    "password": "local-password-456",
                },
            )
        ).json()
        response = await client.put(
            base + f"/grants/{uuid4()}",
            json={
                "client_id": app["client_id"],
                "resource": "graph",
                "kind": "delegated",
                "user_id": foreign_user["id"],
                "permissions": ["User.Read"],
            },
        )
        assert response.status_code == 404
        # Direct SQL proves the invariant is in PostgreSQL, not just HTTP validation.
        with pytest.raises(IntegrityError):
            async with database.sessions.begin() as session:
                await session.execute(
                    text("""
                    INSERT INTO identity_grants
                    (id,tenant_id,client_id,resource,kind,permissions,user_id)
                    VALUES (:id,:tenant,:client,'graph','delegated','["User.Read"]'::jsonb,:user)
                """),
                    {
                        "id": uuid4(),
                        "tenant": UUID(tenant),
                        "client": UUID(app["client_id"]),
                        "user": UUID(foreign_user["id"]),
                    },
                )
