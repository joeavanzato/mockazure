from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update

from tests.integration.graph_support import graph_client, headers_for, registered_app


async def test_security_collection_shapes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, {"Application.Read.All"})
        result = await client.get("/v1.0/applications", headers=headers)
        assert result.status_code == 200, result.text
        rows = result.json()["value"]
        row = next(x for x in rows if x["appId"] == str(app))
        assert UUID(row["id"]) != app
        assert row["displayName"] == "Collector"
        assert "secretText" not in result.text and "verifier" not in result.text
        single = await client.get(f"/v1.0/applications/{row['id']}", headers=headers)
        assert single.status_code == 200 and single.json()["appId"] == str(app)
        alternate = await client.get(f"/v1.0/applications(appId='{app}')", headers=headers)
        assert alternate.status_code == 200 and alternate.json()["id"] == row["id"]
        service = await client.get("/v1.0/servicePrincipals", headers=headers)
        assert service.status_code == 200, service.text
        graph = next(
            x
            for x in service.json()["value"]
            if x["appId"] == "00000003-0000-0000-c000-000000000000"
        )
        assert any(
            r["id"] == "df021288-bdef-4463-88db-98f22de89214" and r["value"] == "User.Read.All"
            for r in graph["appRoles"]
        )
        assert any(x["appId"] == str(app) for x in service.json()["value"])
        denied = await client.get("/v1.0/devices", headers=headers)
        assert denied.status_code == 403
        device_headers = await headers_for(
            database, settings, tenant, app, None, {"Device.Read.All"}
        )
        devices = await client.get("/v1.0/devices", headers=device_headers)
        assert devices.status_code == 200 and devices.json()["value"] == []
        from mockazure.security.models import SecurityObject

        device_id = uuid4()
        async with database.sessions.begin() as session:
            session.add(
                SecurityObject(
                    tenant_id=tenant,
                    id=device_id,
                    kind="device",
                    properties={"displayName": "Workstation", "accountEnabled": True},
                )
            )
        devices = await client.get("/v1.0/devices", headers=device_headers)
        assert devices.json()["value"][0]["id"] == str(device_id)


async def test_security_snapshot_retains_filtered_ordered_values(database, settings):
    from mockazure.identity.models import Application

    async with graph_client(database, settings) as (client, tenant, app, user):
        for name in ["Alpha", "Bravo", "Charlie"]:
            response = await client.post(
                f"/_mock/tenants/{tenant}/identity/applications",
                json={"display_name": name, "client_mode": "public"},
            )
            assert response.status_code == 201
        headers = await headers_for(database, settings, tenant, app, None, {"Application.Read.All"})
        headers["ConsistencyLevel"] = "eventual"
        response = await client.get(
            "/v1.0/applications",
            headers=headers,
            params={
                "$filter": "displayName ne 'Collector'",
                "$select": "id,displayName",
                "$orderby": "displayName",
                "$top": "1",
                "$count": "true",
            },
        )
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["@odata.count"] == 3
        assert first["value"][0]["displayName"] == "Alpha"
        async with database.sessions.begin() as session:
            await session.execute(
                update(Application)
                .where(Application.tenant_id == tenant, Application.display_name == "Bravo")
                .values(display_name="Zulu")
            )
        values = first["value"]
        while "@odata.nextLink" in first:
            response = await client.get(first["@odata.nextLink"], headers=headers)
            assert response.status_code == 200, response.text
            first = response.json()
            values.extend(first["value"])
        assert [x["displayName"] for x in values] == ["Alpha", "Bravo", "Charlie"]
        assert len({x["id"] for x in values}) == 3
        assert all(set(x) == {"id", "displayName"} for x in values)


async def test_security_cross_tenant_cursor_and_reference(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        other_app = await registered_app(client, tenant)
        headers = await headers_for(database, settings, tenant, app, None, {"Application.Read.All"})
        response = await client.get("/v1.0/applications?$top=1", headers=headers)
        assert response.status_code == 200, response.text
        link = response.json()["@odata.nextLink"]
        other = await headers_for(
            database, settings, tenant, other_app, None, {"Application.Read.All"}
        )
        assert (await client.get(link, headers=other)).status_code == 400
        assert (await client.get(link + "&$select=id", headers=headers)).status_code == 400
        foreign = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "foreign",
                    "domain": "foreign.test",
                    "display_name": "Foreign",
                },
            )
        ).json()["id"]
        foreign_app = await registered_app(client, foreign)
        foreign_headers = await headers_for(
            database, settings, foreign, foreign_app, None, {"Application.Read.All"}
        )
        assert (await client.get(link, headers=foreign_headers)).status_code == 400
        object_id = response.json()["value"][0]["id"]
        assert (
            await client.get(f"/v1.0/applications/{object_id}", headers=foreign_headers)
        ).status_code == 404
        from mockazure.security.models import SecuritySnapshot

        async with database.sessions.begin() as session:
            await session.execute(
                update(SecuritySnapshot)
                .where(SecuritySnapshot.tenant_id == tenant)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert (await client.get(link, headers=headers)).status_code == 400
        for query in [
            "$select=verifier",
            "$expand=secret",
            "$filter=displayName eq 4",
            "$search=hello",
            "$top=-1",
            "$count=true&$count=false",
        ]:
            result = await client.get("/v1.0/applications?" + query, headers=headers)
            assert result.status_code == 400, result.text


async def test_legacy_application_upgrade_preserves_oauth(database, settings):
    from alembic import command
    from sqlalchemy import create_engine

    from mockazure.db.migrations import configuration, migrate
    from mockazure.identity.models import Application, Credential
    from tests.integration.identity_support import identity_client

    async with identity_client(database, settings) as (client, tenant, app, secret, *_):
        async with database.sessions() as session:
            original = await session.scalar(
                select(Credential.verifier).where(Credential.id == UUID(secret["id"]))
            )
        engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
        try:
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "016")
            migrate(settings.database_url.get_secret_value())
        finally:
            engine.dispose()
        async with database.sessions() as session:
            row = await session.get(Application, UUID(app))
            assert row.object_id != row.client_id
            object_id = row.object_id
            assert (
                await session.scalar(
                    select(Credential.verifier).where(Credential.id == UUID(secret["id"]))
                )
                == original
            )
        migrate(settings.database_url.get_secret_value())
        async with database.sessions() as session:
            assert (await session.get(Application, UUID(app))).object_id == object_id
        token = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app,
                "client_secret": secret["secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        assert token.status_code == 200, token.text
